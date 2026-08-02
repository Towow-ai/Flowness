"""M-0.1 §7.1/§7.2/§11.2 — physical segment rotation + shared raw-line access.

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   §7.1 文件布局 (events/hot/events-NNNNNN.jsonl)
#   §7.2 文件格式 (JSONL; segment ≈ 10,000 events 或 10MB, 先到为准)
#   §11.2 segment 切分参数 (运维参数)

Logical model: the event log is ONE continuous ordered stream. Physically it is split
across segment files for size management. The read order is fixed and total:

    [legacy events.log (base/oldest, if present)] + sorted(events/hot/events-NNNNNN.jsonl by N)

Why the legacy ``events.log`` is the base segment: the whole codebase (44+ call sites) opens
``EventLog(towow_dir / "events.log")`` and ~10 raw readers scan that exact path. Treating the
pre-existing single file as the oldest segment means rotation is purely additive — no migration,
no path change, and an un-rotated repo behaves byte-identically to today (single file).

This module is the single physical-layout authority. Both EventLog and the out-of-band raw
readers/writers (escalation poller, the two blocking-check "rulers", CLI scans, bg poller) route
through ``iter_raw_event_lines`` / ``append_raw_event_line`` / ``ordered_segment_paths`` so a
post-rotation reader never silently reads only the stale base segment (which would break
escalation polling + the rulers — the whole point of migrating them).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# events-NNNNNN.jsonl — zero-padded 6-digit segment number (§7.1).
_HOT_SEGMENT_RE = re.compile(r"^events-(\d{6})\.jsonl$")
_HOT_SEGMENT_FMT = "events-{:06d}.jsonl"


def _resolve_log_path(towow_or_log: Path) -> Path:
    """Accept either a .towow dir or a concrete events.log path; return the base log path.

    Raw readers historically take ``towow_dir`` (escalation/rulers) OR a concrete
    ``events.log`` path (CLI helpers). Normalize both to the base segment path so the
    segment layout is computed identically regardless of which the caller passed.
    """
    if towow_or_log.name == "events.log":
        return towow_or_log
    return towow_or_log / "events.log"


def hot_dir_for(base_log_path: Path) -> Path:
    """Return the events/hot/ directory that holds rotated segments for this log."""
    return base_log_path.parent / "events" / "hot"


def _segment_number(path: Path) -> int | None:
    m = _HOT_SEGMENT_RE.match(path.name)
    return int(m.group(1)) if m else None


def segment_number_of(path: Path) -> int | None:
    """The numeric segment id of a rotated hot segment path (events-NNNNNN.jsonl), else None.

    Public read primitive — M-0.7 cold-storage archival keys an archive file to its hot segment by
    this number (archive-NNNNNN.jsonl.gz ↔ events-NNNNNN.jsonl, the §7.1 1:1 mapping). The legacy
    unnumbered base ``events.log`` returns None (it is not an archivable rotated segment).
    """
    return _segment_number(path)


def ordered_hot_segments(base_log_path: Path) -> list[Path]:
    """Rotated hot segments in ascending segment-number order (excludes the base log)."""
    hot = hot_dir_for(base_log_path)
    if not hot.is_dir():
        return []
    numbered: list[tuple[int, Path]] = []
    for p in hot.iterdir():
        n = _segment_number(p)
        if n is not None and p.is_file():
            numbered.append((n, p))
    numbered.sort(key=lambda t: t[0])
    return [p for _, p in numbered]


def ordered_segment_paths(towow_or_log: Path) -> list[Path]:
    """Full read order: [legacy events.log if it exists] + sorted hot segments.

    This is the total physical ordering of the single logical stream. Every reader that wants
    the complete log iterates these files in this order.
    """
    base = _resolve_log_path(towow_or_log)
    paths: list[Path] = []
    if base.exists():
        paths.append(base)
    paths.extend(ordered_hot_segments(base))
    return paths


def next_segment_path(base_log_path: Path) -> Path:
    """Path of the segment that follows the current active one (highest existing N + 1).

    Numbering starts at 1 (events-000001.jsonl) for the first rotated segment; the legacy
    ``events.log`` is the unnumbered base. If hot segments already exist, the next number is
    max(existing) + 1.
    """
    existing = ordered_hot_segments(base_log_path)
    if not existing:
        next_n = 1
    else:
        last_n = _segment_number(existing[-1])
        next_n = (last_n if last_n is not None else 0) + 1
    return hot_dir_for(base_log_path) / _HOT_SEGMENT_FMT.format(next_n)


def active_segment_path(base_log_path: Path) -> Path:
    """The segment writes currently target = the last segment in read order.

    If no hot segment exists yet, the active segment is the legacy base ``events.log`` (so a
    never-rotated log keeps writing to the same single file as today). Otherwise it is the
    highest-numbered hot segment.
    """
    hot = ordered_hot_segments(base_log_path)
    if hot:
        return hot[-1]
    return base_log_path


def iter_raw_event_lines(towow_or_log: Path) -> Iterator[str]:
    """Yield every non-empty event line across all segments in read order.

    The shared raw-access primitive for out-of-band readers (escalation poller, the two
    blocking-check rulers, CLI raw scans). Yields stripped non-empty lines so callers can
    ``json.loads`` each directly — identical semantics to the old single-file ``for line in f``
    loop, but transparently spanning rotated segments.

    Accepts either a ``.towow`` directory or a concrete ``events.log`` path.

    TOCTOU robustness (finding f-orchestrator-crashes-on-hot-segment-rotation-race): a hot segment
    listed by ordered_segment_paths can be unlinked between listing and open — M-0.7 GC cold-move
    finalize unlinks the hot file (its bytes already in cold) AFTER the listing snapshot. The open
    then raises FileNotFoundError, which previously propagated out of this generator and crashed every
    consumer (the orchestrator poll loop's all_records() → silent autopilot death). Skipping the
    vanished segment is exactly steady-state: ordered_hot_segments only lists files still present in
    hot/, so a fresh listing wouldn't include a cold-moved segment either — its events live in cold
    and are reachable via get_event / get_events_in_range (the raw line stream was never cold-complete,
    and is not the API responsible for cold completeness). Only FileNotFoundError is swallowed; a real
    IO error (permission/disk) still propagates — 不静默吞真相.
    """
    for seg in ordered_segment_paths(towow_or_log):
        try:
            with seg.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        yield stripped
        except FileNotFoundError:
            continue


def append_raw_event_line(towow_or_log: Path, line: str) -> None:
    """Append one raw pre-serialized event line to the active segment (no seq assignment).

    For the bg worktree poller's best-effort NodeTouched write (path-B-shaped JSON with no
    sequence_number). After rotation this must land in the active segment, not the stale base
    ``events.log`` — otherwise the main session reads it only if it still scans the base file.
    """
    target = active_segment_path(_resolve_log_path(towow_or_log))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(line if line.endswith("\n") else line + "\n")


__all__ = [
    "active_segment_path",
    "append_raw_event_line",
    "hot_dir_for",
    "iter_raw_event_lines",
    "next_segment_path",
    "ordered_hot_segments",
    "ordered_segment_paths",
    "segment_number_of",
]
