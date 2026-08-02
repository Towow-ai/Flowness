"""T-L0-04 (波1) — migrated raw accessors must read/write ACROSS segments, not just the base.

This is the contract verification for the migration step: each raw reader (escalation poll =
命脉, the two execution/plan "尺子", CLI raw scans) is fed an event that lives ONLY in a rotated
hot segment, and must still find it. Every assertion carries a negative control: the same event
read via a base-segment-only scan is NOT found, proving the test would catch a regression that
silently reads only events.log after rotation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from towow.l0.event_log.segments import (
    active_segment_path,
    append_raw_event_line,
    hot_dir_for,
    next_segment_path,
)
from towow.l2.escalation_subscribe import _scan_for_match
from towow.schemas.payloads.consensus_freeze_checks import (  # T-L0-04 独立审补迁的第三把尺子
    _read_events as _consensus_read_events,
)
from towow.schemas.payloads.plan_freezed import _read_events as _plan_read_events
from towow.schemas.payloads.task_run_completed_checks import (
    _read_events as _exec_read_events,
)


def _force_rotated_segment(towow: Path) -> Path:
    """Create a base events.log + one rotated hot segment; return the (empty) hot segment path.

    Writers append to the *active* segment = the highest-numbered hot file once one exists, so
    after this the active segment is the rotated file and the base events.log is sealed behind it.
    """
    (towow / "events").mkdir(parents=True, exist_ok=True)
    base = towow / "events.log"
    base.write_text("", encoding="utf-8")  # base exists but empty
    hot = hot_dir_for(base)
    hot.mkdir(parents=True, exist_ok=True)
    seg = next_segment_path(base)  # events-000001.jsonl
    seg.write_text("", encoding="utf-8")
    assert active_segment_path(base) == seg
    return seg


def _write_line_to(seg: Path, record: Mapping[str, object]) -> None:
    with seg.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─── escalation poller (命脉) reads across segments ──────────────────────────────


def test_escalation_scan_finds_nj_in_rotated_segment(tmp_path: Path) -> None:
    """The owner-response NJ lands in a rotated hot segment — _scan_for_match must find it."""
    towow = tmp_path / ".towow"
    seg = _force_rotated_segment(towow)
    nj = {
        "event_id": "evt-nj-rotated",
        "event_type": "NatureJudgmentCaptured",
        "payload": {
            "after_state": {
                "responds_to_escalation_event_id": "evt-esc-target",
                "judgment_id": "j-1",
                "quote": "approved",
                "meaning": "go ahead",
                "scope": "goal_escalation",
                "is_value_judgment": True,
            },
        },
    }
    _write_line_to(seg, nj)

    match = _scan_for_match(towow / "events.log", "evt-esc-target")
    assert match is not None
    assert match.quote == "approved"

    # Negative control: a base-segment-only scan (the pre-migration behavior) misses it.
    base_only = [
        json.loads(line)
        for line in (towow / "events.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert base_only == [], "base events.log is empty — pre-migration reader would NEVER match"


# ─── execution / plan "尺子" read across segments ────────────────────────────────


@pytest.mark.parametrize(
    "read_events", [_exec_read_events, _plan_read_events, _consensus_read_events],
)
def test_ruler_read_events_spans_rotated_segment(tmp_path: Path, read_events) -> None:
    """All three blocking-check rulers' _read_events must surface events from rotated segments.

    T-L0-04 独立审: consensus_freeze_checks (M-1.2 消费方完整性 freeze 门) 是漏迁的第三把尺子 —
    切段后只读 base 会把 hot 段的 ConsumerListPublished 当漏报 → 错误阻断合法 freeze。已补迁 + 此参数化覆盖。

    A gate that judges done_criteria / mismatch / coverage against only the base segment after
    rotation would silently pass/fail on stale data — the whole reason these were migrated.
    """
    towow = tmp_path / ".towow"
    seg = _force_rotated_segment(towow)
    rec = {
        "event_id": "evt-ruler-rotated",
        "event_type": "PatchProposed",
        "payload": {"after_state": {"task_id": "t-9"}},
        "provenance": {"run_id": "r-9", "session_id": "s-9"},
    }
    _write_line_to(seg, rec)

    events = read_events(towow / "events.log")
    ids = {e.get("event_id") for e in events}
    assert "evt-ruler-rotated" in ids, "ruler must see the rotated-segment event"

    # Negative control: base-only read returns nothing (base is empty post-rotation).
    base_lines = [
        line for line in (towow / "events.log").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert base_lines == [], "base events.log empty — pre-migration ruler would miss the event"


# ─── bg poller raw WRITE goes to the active segment ──────────────────────────────


def test_append_raw_event_line_targets_active_segment(tmp_path: Path) -> None:
    """The bg poller's best-effort raw NodeTouched must land in the active (rotated) segment.

    Pre-migration it appended straight to events.log; after rotation that base file is sealed
    behind hot segments, so the raw line belongs at the live write head (the active segment).
    """
    towow = tmp_path / ".towow"
    seg = _force_rotated_segment(towow)
    line = json.dumps({"event_id": "evt-poller", "event_type": "NodeTouched"}, ensure_ascii=False)

    append_raw_event_line(towow, line)

    # Landed in the active (rotated) segment, NOT the sealed base file.
    assert "evt-poller" in seg.read_text(encoding="utf-8")
    assert "evt-poller" not in (towow / "events.log").read_text(encoding="utf-8")


def test_append_raw_event_line_no_rotation_targets_base(tmp_path: Path) -> None:
    """Back-compat: with no rotation, the active segment IS events.log (writes go there)."""
    towow = tmp_path / ".towow"
    (towow / "events").mkdir(parents=True, exist_ok=True)
    base = towow / "events.log"
    base.write_text("", encoding="utf-8")
    assert active_segment_path(base) == base  # no hot segments → base is active

    append_raw_event_line(towow, json.dumps({"event_id": "evt-base"}, ensure_ascii=False))
    assert "evt-base" in base.read_text(encoding="utf-8")
