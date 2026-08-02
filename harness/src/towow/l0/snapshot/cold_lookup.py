"""M-0.7 §7.3 — Cold Lookup 协议 (M-0.1 get_event 冷回溯实现).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §7.3 Cold Lookup 协议:
#     M-0.1 get_event(event_id) 即使 event 已归档也能返回 (M-0.1 §6.2 承诺). M-0.7 cold_lookup:
#       location = index.get_location(event_id); cold → 解压 archive-{seg}.jsonl.gz 找 event_id.
#   §13.1 反向贡献对 M-0.1: cold_lookup 由 M-0.7 实现, M-0.1 透传调用 (hot miss → 走这里).
#
# 这实现 M-0.1 §6.2 Inv2: consolidate (归档) 后 get_event 从冷存储回溯被归档事件 (debt-edb1529a1bc4).
# 数据安全: archive 是归档时按 hot segment 原始字节 verbatim 落的 (cold_archive.prepare), 解压逐行
# 反序列化回 EventRecord → 与归档前逐字段相同 (record_hash / payload / seq 都不变).
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

from towow.l0.snapshot.cold_storage import cold_archive_path, cold_dir, load_cold_index
from towow.schemas.event_record import EventRecord

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from towow.schemas.enums import EventType


def cold_lookup(towow_dir: Path, event_id: str) -> EventRecord | None:
    """§7.3 — return an archived event from cold storage, or None if not in cold.

    Routes via the cold index (event_id → segment), then decompresses that segment's
    archive-{seg:06d}.jsonl.gz and scans for the matching event_id. The archived bytes are a
    verbatim copy of the original hot segment, so the reconstructed EventRecord is field-identical
    to the pre-archive record (record_hash included). Returns None for an event not routed to cold
    (the caller — M-0.1 get_event — only delegates here on a hot miss).
    """
    index = load_cold_index(towow_dir)
    entry = index.get(event_id)
    if entry is None or entry.get("location") != "cold":
        return None
    seg = entry.get("segment")
    if not isinstance(seg, int) or isinstance(seg, bool):
        return None
    cold_path = cold_archive_path(towow_dir, seg)
    if not cold_path.exists():
        return None
    with gzip.open(cold_path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            # cheap event_id substring prefilter before full pydantic validation
            if f'"{event_id}"' not in line:
                continue
            try:
                rec = EventRecord.model_validate_json(line)
            except ValueError:
                continue
            if rec.event_id == event_id:
                return rec
    return None


def _cold_segments(towow_dir: Path) -> set[int]:
    """The set of segment ids that have events routed to cold (per the cold routing index).

    Used to decide WHICH archives a range query has to touch — and, crucially, to short-circuit:
    an empty set (no segment ever archived — the overwhelmingly common case) means a range query
    skips cold entirely with zero decompression. The index missing/corrupt → empty (fail-safe, same
    as load_cold_index): nothing is then merged from cold, which for a never-compacted log is exactly
    right (hot still holds everything).
    """
    segs: set[int] = set()
    for entry in load_cold_index(towow_dir).values():
        if not isinstance(entry, dict) or entry.get("location") != "cold":
            continue
        seg = entry.get("segment")
        if isinstance(seg, int) and not isinstance(seg, bool):
            segs.add(seg)
    return segs


def has_cold_events(towow_dir: Path) -> bool:
    """True iff any event is routed to cold (a cold archive directory exists with index entries).

    The cheap gate M-0.1 get_events_in_range checks before doing any cold work: a log that never
    ran compaction has no cold/ dir, so this is a single stat + (only if present) a small index read —
    keeping the hot read path (daemon/poller/orchestrator range queries) free of any cold overhead.
    """
    if not cold_dir(towow_dir).is_dir():
        return False
    return bool(_cold_segments(towow_dir))


def cold_records_in_range(towow_dir: Path, start_seq: int, end_seq: int) -> list[EventRecord]:
    """M-0.1 §6.2 Inv2 (range form) — archived events with start_seq ≤ sequence_number ≤ end_seq.

    The batch analog of cold_lookup: get_events_in_range must transparently include events that
    段内 compaction (M-0.7 v3.6+ candidate 乙) moved from hot to cold, exactly as get_event already
    cold-falls-back for a point lookup. Without this, a full rebuild()/recompute_one()/validate_rebuild()
    over a compacted log silently drops every moved event's projection contribution (the "可重建"
    guarantee breaks) with no fail-closed.

    Reads ONLY the cold archives (never re-reads hot — the caller merges with the hot index result),
    decompressing archive-{seg}.jsonl.gz for each cold segment, reconstructing records through the
    SAME committed-visibility + schema gate as a hot read (iter_committed_records_from_lines, applied
    per-archive — an archive is a verbatim sealed segment, so its batches are self-contained), then
    keeping the records with start_seq ≤ seq ≤ end_seq. Returns the matching records (unsorted; the
    caller sorts the merge).

    Cost note: the full-rebuild caller passes start_seq=0 and legitimately needs every cold record, so
    decompression there is inherent (and rebuild is rare — DESIGN §4.4). For an ABOVE-cold range query
    (a daemon/poller asking [watermark, head], where watermark > every archived seq because compaction
    only archives seqs ≤ archive_seq_bound ≤ consumer_watermark), we still skip whole archives cheaply:
    an archive's events are seq-ascending, so once we've passed end_seq within it we stop, and an
    archive whose very first record is already > end_seq is abandoned immediately. This keeps the hot
    read path from materializing cold records it can't use.
    """
    # imported here (not module top) to avoid a snapshot→event_log import cycle at load time.
    from towow.l0.event_log.event_log import iter_committed_records_from_lines

    out: list[EventRecord] = []
    for seg in sorted(_cold_segments(towow_dir)):
        cold_path = cold_archive_path(towow_dir, seg)
        if not cold_path.exists():
            continue
        for rec in iter_committed_records_from_lines(_archive_lines(cold_path)):
            if rec.sequence_number > end_seq:
                break  # archive is seq-ascending — nothing past here is in range (skip the rest)
            if rec.sequence_number >= start_seq:
                out.append(rec)
    return out


def cold_records_of_type(towow_dir: Path, event_type: EventType) -> list[EventRecord]:
    """Archived events of one type — the cold half of ``EventLog.scan_committed_records_of_type``.

    f-fix-complete-gate-ignores-amended-closure-contract: an index-free type scan that read only the
    hot segments would report "no such event" for a type whose records 段内 compaction already moved
    to cold — the same silent blindness the index-free read exists to remove, one layer down. Mirrors
    ``cold_records_in_range``: same verbatim archives, same shared committed-visibility gate
    (``iter_committed_records_from_lines``), with the byte-level type prefilter in front so an archive
    costs one decompression pass and a pydantic parse only on candidate lines.
    """
    # imported here (not module top) to avoid a snapshot→event_log import cycle at load time.
    from towow.l0.event_log.event_log import (
        iter_committed_records_from_lines,
        lines_possibly_of_type,
    )

    out: list[EventRecord] = []
    for seg in sorted(_cold_segments(towow_dir)):
        cold_path = cold_archive_path(towow_dir, seg)
        if not cold_path.exists():
            continue
        for rec in iter_committed_records_from_lines(
            lines_possibly_of_type(_archive_lines(cold_path), event_type.value),
        ):
            if rec.event_type is event_type:
                out.append(rec)
    return out


def _archive_lines(cold_path: Path) -> Iterator[str]:
    """Yield stripped non-empty lines from a gzip cold archive (raw line source for the committed gate)."""
    with gzip.open(cold_path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8").strip()
            if line:
                yield line


__all__ = ["cold_lookup", "cold_records_in_range", "cold_records_of_type", "has_cold_events"]
