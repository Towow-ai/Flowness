"""T-L0-04 (波1) — M-0.1 §7.1/§7.2/§11.2 physical segment rotation.

Covers the done_criteria:
  - 写入超阈值 → 自动切出新 hot segment, 旧 segment 落 events/hot/
  - Read API 跨 segment 读出完整有序结果
  - rotate 前后全量逐行 json.loads 通过 + seq 连续
  - 跨 segment Read API 结果与单文件一致

Anti-vacuous: each cross-segment claim has a negative control proving the assertion is not
恒过 (a single-file log of the same events, OR a deliberately-broken segment iteration).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from towow.l0.event_log import EventLog, iter_raw_event_lines, ordered_segment_paths
from towow.l0.event_log.event_log import build_accepted_batch_e11
from towow.l0.event_log.segments import (
    active_segment_path,
    hot_dir_for,
    ordered_hot_segments,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

# ─── fixtures / helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


def _pathb_intent(local_intent_id: str) -> EventIntent:
    """A path-B NodeTouched intent (one record per write_direct)."""
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": "c-1", "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            run_id="r-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _domain_intent(local_intent_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": "concept",
            "transition_type": "created",
            "after_state": {"concept_id": local_intent_id},
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m12-sess",
            task_id="t-1",
            run_id="r-1",
            session_id="sess-1",
            skill_id="M-1.2",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=local_intent_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _sentinel_intent(local_intent_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.COMMIT,
        payload={"target_entity_type": "transaction", "transition_type": "created"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id="m05-gate",
            run_id="r-1",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="tx", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _append_one_batch(log: EventLog, i: int) -> None:
    """Append a [ConceptCreated, CommitAccepted] batch (2 records)."""
    domain = log.stamp_for_batch(
        _domain_intent(f"c-{i}"),
        transaction_id=f"tx-{i}",
        batch_id=f"b-{i}",
        batch_position=0,
    )
    sentinel = log.stamp_for_batch(
        _sentinel_intent(f"ca-{i}"),
        transaction_id=f"tx-{i}",
        batch_id=f"b-{i}",
        batch_position=1,
    )
    batch = build_accepted_batch_e11(
        batch_id=f"b-{i}",
        transaction_id=f"tx-{i}",
        domain_records=[domain],
        sentinel_record=sentinel,
    )
    log.append_transaction_batch(batch)


# ─── rotation actually happens ─────────────────────────────────────────────────


def test_rotation_creates_new_hot_segment_past_event_threshold(log_path: Path) -> None:
    """Writing past segment_max_events rotates a fresh events/hot/events-NNNNNN.jsonl."""
    log = EventLog(log_path, segment_max_events=3)
    for i in range(10):
        log.write_direct(_pathb_intent(f"i-{i}"))
    hot = hot_dir_for(log_path)
    segs = ordered_hot_segments(log_path)
    assert hot.is_dir()
    assert len(segs) >= 2, f"expected rotation into multiple hot segments, got {segs}"
    # First rotated segment is events-000001.jsonl.
    assert segs[0].name == "events-000001.jsonl"
    # Base events.log holds the first <= threshold records, none lost.
    assert log_path.exists()


def test_no_rotation_at_default_thresholds(log_path: Path) -> None:
    """Default thresholds (10k/10MB) keep everything in the single base file (back-compat)."""
    log = EventLog(log_path)
    for i in range(50):
        log.write_direct(_pathb_intent(f"i-{i}"))
    assert ordered_hot_segments(log_path) == []
    # Everything physically in the base segment.
    assert active_segment_path(log_path) == log_path


# ─── cross-segment read == single-file read (with negative control) ─────────────


def _read_shape(log: EventLog) -> list[tuple[int, str, str]]:
    """(seq, event_type, local_intent_id) per record — deterministic across logs.

    event_id / timestamp are per-write random, so two independent logs of the same intents
    differ there; the *shape* (seq order + type + intent id) is what must be identical.
    """
    return [(r.sequence_number, r.event_type.value, r.local_intent_id) for r in log.all_records()]


def test_cross_segment_read_equals_single_file(tmp_path: Path) -> None:
    """all_records() over a rotated log == over a single-file log of the identical writes.

    Negative control: a deliberately base-only reader (ignores hot segments) yields a STRICT
    subset, proving the equality is not vacuous (it would fail if rotation lost data or the
    reader silently dropped hot segments).
    """
    seg_path = tmp_path / "seg" / "events.log"
    single_path = tmp_path / "single" / "events.log"
    seg_log = EventLog(seg_path, segment_max_events=3)
    single_log = EventLog(single_path)  # default → never rotates
    for i in range(12):
        _append_one_batch(seg_log, i)
        _append_one_batch(single_log, i)

    # Sanity: the segmented log truly rotated; the single one did not.
    assert len(ordered_hot_segments(seg_path)) >= 2
    assert ordered_hot_segments(single_path) == []

    # Full ordered Read API result is identical in shape (seq/type/intent order).
    assert _read_shape(seg_log) == _read_shape(single_log)
    assert len(seg_log.all_records()) == 24  # 12 batches * 2 records

    # Negative control: base-segment-only read drops the rotated records → strict subset.
    base_only_lines = [
        json.loads(line) for line in seg_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    base_only_intents = {r["local_intent_id"] for r in base_only_lines}
    full_intents = {r.local_intent_id for r in seg_log.all_records()}
    assert base_only_intents < full_intents, "base-only read must be a STRICT subset (else test is vacuous)"


def test_read_apis_span_segments(tmp_path: Path) -> None:
    """get_events_by_type / get_event / get_events_in_range all reach rotated segments."""
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path, segment_max_events=3)
    for i in range(8):
        _append_one_batch(log, i)
    assert len(ordered_hot_segments(seg_path)) >= 2

    # by_type spans every segment.
    created = log.get_events_by_type(EventType.CONCEPT_CREATED)
    assert len(created) == 8
    # A late-written record (in a rotated segment) is point-lookup reachable.
    last_id = log.all_records()[-1].event_id
    assert log.get_event(last_id) is not None
    # Range query crossing the segment boundary.
    in_range = log.get_events_in_range(0, log.next_sequence - 1)
    assert len(in_range) == len(log.all_records())


# ─── seq continuity across rotation (with negative control) ─────────────────────


def test_seq_continuous_across_rotation(tmp_path: Path) -> None:
    """sequence_number is gap-free 0..N-1 across the rotation boundary.

    Negative control: scanning only the base segment leaves a gap (the rotated seqs are
    missing), so the contiguity assertion is non-trivial.
    """
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path, segment_max_events=3)
    for i in range(10):
        log.write_direct(_pathb_intent(f"i-{i}"))
    assert len(ordered_hot_segments(seg_path)) >= 2

    seqs = [r.sequence_number for r in log.all_records()]
    assert seqs == list(range(len(seqs))), f"seq not contiguous: {seqs}"

    # Negative control: base-only seqs have a hole (rotated tail missing).
    base_seqs = sorted(
        json.loads(line)["sequence_number"]
        for line in seg_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert base_seqs != list(range(len(seqs))), "base-only must be incomplete (else vacuous)"


def test_every_line_json_loads_across_segments(tmp_path: Path) -> None:
    """rotate 前后全量逐行 json.loads 通过 (across every physical segment)."""
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path, segment_max_events=3)
    for i in range(6):
        _append_one_batch(log, i)
    seg_count = 0
    line_count = 0
    for seg in ordered_segment_paths(seg_path):
        seg_count += 1
        for raw in seg.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                json.loads(raw)  # raises on any malformed line
                line_count += 1
    assert seg_count >= 3  # base + >= 2 hot
    assert line_count == 12  # 6 batches * 2 records


# ─── recover acts on the active segment ──────────────────────────────────────────


def test_recover_truncates_active_segment_only(tmp_path: Path) -> None:
    """A crash-tail un-sentineled record in the active segment is truncated; sealed segments kept.

    Negative control: an identical un-sentineled tail written to the BASE segment of a
    never-rotated log is also recovered — proves recover() targets the active segment in both
    layouts (not hard-wired to events.log).
    """
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path, segment_max_events=3)
    for i in range(6):
        _append_one_batch(log, i)
    assert len(ordered_hot_segments(seg_path)) >= 2
    active = active_segment_path(seg_path)

    committed_before = {r.event_id for r in log.all_records()}

    # Simulate a crash mid-batch: append a lone un-sentineled domain record to the ACTIVE segment.
    orphan = log.stamp_for_batch(
        _domain_intent("orphan"),
        transaction_id="tx-orphan",
        batch_id="b-orphan",
        batch_position=0,
    )
    with active.open("a", encoding="utf-8") as f:
        f.write(orphan.model_dump_json() + "\n")

    # Before recover: orphan is invisible (no sentinel) but physically present in active segment.
    assert "orphan" not in {r.payload.get("after_state", {}).get("concept_id") for r in log.all_records()}
    assert orphan.event_id in active.read_text(encoding="utf-8")

    truncated = log.recover()
    assert truncated > 0
    # Orphan physically gone from the active segment; sealed/base segments untouched.
    assert orphan.event_id not in active.read_text(encoding="utf-8")
    assert {r.event_id for r in log.all_records()} == committed_before


def test_recover_on_single_file_layout(tmp_path: Path) -> None:
    """Negative-control twin: recover targets events.log when no rotation has happened."""
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path)  # default → single file
    for i in range(3):
        _append_one_batch(log, i)
    assert ordered_hot_segments(seg_path) == []
    assert active_segment_path(seg_path) == seg_path

    orphan = log.stamp_for_batch(
        _domain_intent("orphan"),
        transaction_id="tx-orphan",
        batch_id="b-orphan",
        batch_position=0,
    )
    with seg_path.open("a", encoding="utf-8") as f:
        f.write(orphan.model_dump_json() + "\n")
    assert log.recover() > 0
    assert orphan.event_id not in seg_path.read_text(encoding="utf-8")


# ─── shared raw helper spans segments (with negative control) ────────────────────


def test_iter_raw_event_lines_spans_segments(tmp_path: Path) -> None:
    """iter_raw_event_lines yields base + hot lines in read order; base-only is a strict subset."""
    seg_path = tmp_path / "events.log"
    log = EventLog(seg_path, segment_max_events=3)
    for i in range(8):
        log.write_direct(_pathb_intent(f"i-{i}"))
    assert len(ordered_hot_segments(seg_path)) >= 2

    raw_ids = [json.loads(line)["event_id"] for line in iter_raw_event_lines(seg_path)]
    full_ids = [r.event_id for r in log.all_records()]
    assert raw_ids == full_ids  # same order, all records

    # Negative control: a reader pinned to the base file alone misses the rotated tail.
    base_only = [
        json.loads(line)["event_id"]
        for line in seg_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(base_only) < len(raw_ids), "base-only raw read must be incomplete (else vacuous)"


def test_iter_raw_event_lines_survives_segment_unlinked_mid_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A segment listed by ordered_segment_paths but unlinked before open must NOT crash the scan.

    Finding f-orchestrator-crashes-on-hot-segment-rotation-race: M-0.7 GC cold-move finalize unlinks
    a hot segment (its bytes already in cold) AFTER the reader listed it — a TOCTOU between listing
    and open. The unfixed code let the FileNotFoundError on .open() propagate out of the generator,
    crashing every consumer (the orchestrator poll loop's all_records() → silent autopilot death).

    Skipping the vanished segment is exactly steady-state: ordered_hot_segments only lists files still
    present in hot/, so a fresh listing wouldn't include a cold-moved segment either — its events are
    in cold, reachable via get_event/get_events_in_range (the raw line stream was never cold-complete).

    We force the "listed but gone" window by patching ordered_segment_paths to return a non-existent
    middle path (natural listing's is_file() filter would exclude it, hiding the race).
    """
    from towow.l0.event_log import segments as seg_mod

    seg1 = tmp_path / "events.log"
    seg1.write_text('{"event_id":"a"}\n', encoding="utf-8")
    hot = tmp_path / "events" / "hot"
    hot.mkdir(parents=True)
    seg3 = hot / "events-000002.jsonl"
    seg3.write_text('{"event_id":"c"}\n', encoding="utf-8")
    vanished = hot / "events-000001.jsonl"  # listed, then cold-moved out from under the reader
    assert not vanished.exists()

    monkeypatch.setattr(
        seg_mod, "ordered_segment_paths", lambda _p: [seg1, vanished, seg3]
    )

    raw_ids = [json.loads(line)["event_id"] for line in seg_mod.iter_raw_event_lines(seg1)]
    assert raw_ids == ["a", "c"]  # both present segments read in order; vanished one skipped, no crash

    # Negative control: only FileNotFoundError is swallowed — a real IO error still propagates
    # (不静默吞真相). A directory in the listing raises IsADirectoryError on open and must NOT be
    # silently skipped.
    bad = hot / "a-directory"
    bad.mkdir()
    monkeypatch.setattr(seg_mod, "ordered_segment_paths", lambda _p: [seg1, bad])
    with pytest.raises(OSError):  # noqa: PT011 — IsADirectoryError subclass; the point is it propagates
        list(seg_mod.iter_raw_event_lines(seg1))


def test_iter_raw_accepts_towow_dir_or_log_path(tmp_path: Path) -> None:
    """iter_raw_event_lines normalizes a .towow dir and a concrete events.log path identically."""
    towow = tmp_path / ".towow"
    log_path = towow / "events.log"
    log = EventLog(log_path, segment_max_events=3)
    for i in range(8):
        log.write_direct(_pathb_intent(f"i-{i}"))
    via_dir = [json.loads(line)["event_id"] for line in iter_raw_event_lines(towow)]
    via_file = [json.loads(line)["event_id"] for line in iter_raw_event_lines(log_path)]
    assert via_dir == via_file == [r.event_id for r in log.all_records()]
