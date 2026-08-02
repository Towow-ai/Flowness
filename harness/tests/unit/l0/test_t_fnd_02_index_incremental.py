"""T-FND-02 (daemon-choke fix) — EventLog index incremental catch-up correctness + no-rebuild proof.

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md §7.2 (索引可重建, 不是事实源)
#   + the daemon-choke task done_criteria: "CPU 不再随账本线性恶化 ... 不再每轮全量重建 42MB 索引".

The always-on orchestrator daemon writes a DaemonRunCompleted every ~5-6s loop. The previous code
nulled the cached EventIndex on EVERY write, so each subsequent read FULLY rebuilt + re-persisted the
whole ledger index — a ~42MB rebuild每轮 that grew linearly with the ledger (the choke). T-FND-02
keeps the index warm and folds in only the records appended to the ACTIVE segment since the last read
(this process's writes + other processes' commits), falling back to a full rebuild only on segment
rotation or a contiguity anomaly.

These tests prove the fix is correct, not just faster:
  - EventIndex.add_records folds records in identically to a fresh full build (unit keystone).
  - the warm (catch-up) index returns the SAME results a full rebuild would, over a mixed
    path-B + path-A stream — catch-up never drops / duplicates / misorders.
  - a warm read after a write does NOT full-scan _iter_committed_records (the choke is gone).
  - cross-process: a SEPARATE writer's commits are picked up on the next read (the coherence the
    old null-on-write gave via rebuild, preserved — and now seen without needing this process to
    write first).
  - segment rotation falls back to a correct full rebuild.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.event_log.index import EventIndex
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    TargetEntityType,
    TransitionType,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


@pytest.fixture
def event_log(log_path: Path) -> EventLog:
    return EventLog(log_path)


def _node_touched(
    *,
    local_intent_id: str,
    entity_id: str = "c-1",
    actor_id: str = "fork-1",
    task_id: str | None = "t-1",
    run_id: str | None = "r-1",
    correlation_id: str | None = None,
) -> EventIntent:
    """A path-B-allowed NodeTouched intent (same shape used by test_run055)."""
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=actor_id,
            task_id=task_id,
            run_id=run_id,
            correlation_id=correlation_id,
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _prov() -> ProvenanceHint:
    return ProvenanceHint(
        actor_type=ActorType.AGENT_SESSION.value,
        actor_id="m14-execution-session",
        session_id="tfnd02-session",
        skill_id="M-1.4",
    )


def _envelope() -> EventIntent:
    env_id = f"env-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "task_id": "tfnd02",
            "run_id": "tfnd02",
            "fork_id": "fork-x",
            "session_id": "tfnd02-session",
            "capsule_compiled_event_id": "evt-stub-capsule-x",
            "as_of_projection_seq": 0,
            "write_set": [{"entity_type": "concept", "entity_id": "c-new", "change_type": "created"}],
            "read_set": [],
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
        },
        provenance_hint=_prov(),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=env_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _concept_domain() -> EventIntent:
    cid = f"c-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=f"cc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": TargetEntityType.CONCEPT.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": {"concept_id": cid, "name": "x", "definition": "d"},
        },
        provenance_hint=_prov(),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=cid, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _assert_indexes_equivalent(a: EventIndex, b: EventIndex) -> None:
    """Assert two EventIndexes are observationally identical across every query dimension.

    The keystone correctness check: an index grown incrementally (catch-up / add_records) must be
    indistinguishable from one built fresh over the same committed records — same record set + order,
    same point lookups, same postings buckets per dimension, same watermark.
    """
    ids_a = [r.event_id for r in a.records()]
    ids_b = [r.event_id for r in b.records()]
    assert ids_a == ids_b, "record set / order diverged"
    assert a.max_sequence == b.max_sequence
    assert a.total_records == b.total_records

    for r in b.records():
        assert a.lookup_event_id(r.event_id) is not None, f"{r.event_id} missing from point lookup"
        assert a.lookup_sequence_range(r.sequence_number, r.sequence_number) == [
            a.lookup_event_id(r.event_id),
        ]
        assert [x.event_id for x in a.lookup_type(r.event_type.value)] == [
            x.event_id for x in b.lookup_type(r.event_type.value)
        ]
        for s in r.subjects:
            assert [x.event_id for x in a.lookup_entity(s.entity_type.value, s.entity_id)] == [
                x.event_id for x in b.lookup_entity(s.entity_type.value, s.entity_id)
            ]
        if r.provenance.task_id is not None:
            assert [x.event_id for x in a.lookup_task(r.provenance.task_id)] == [
                x.event_id for x in b.lookup_task(r.provenance.task_id)
            ]
        if r.provenance.run_id is not None:
            assert [x.event_id for x in a.lookup_run(r.provenance.run_id)] == [
                x.event_id for x in b.lookup_run(r.provenance.run_id)
            ]
        assert [x.event_id for x in a.lookup_actor(r.provenance.actor_id)] == [
            x.event_id for x in b.lookup_actor(r.provenance.actor_id)
        ]
    # full-range query identical (covers ordering end-to-end)
    assert [r.event_id for r in a.lookup_sequence_range(0, 10**9)] == [
        r.event_id for r in b.lookup_sequence_range(0, 10**9)
    ]


def _boom() -> object:
    raise AssertionError(
        "must NOT full-scan _iter_committed_records — a warm read should catch up from the active "
        "segment only (T-FND-02: the per-round full rebuild is the choke this removes)",
    )


@pytest.fixture
def gate_and_log(tmp_path: Path) -> tuple[CommitGate, EventLog]:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    event_log = EventLog(towow / "events.log")
    g = CommitGate(event_log)
    g.bootstrap_commit(bootstrap_protocol_invariants())
    return g, event_log


# ─── keystone: incremental == full build ──────────────────────────────────────────


def test_add_records_equivalent_to_full_build(event_log: EventLog) -> None:
    """EventIndex.add_records folds records in byte-for-byte identically to a fresh full build.

    This is the correctness-by-construction guarantee: catch-up reuses the exact per-record indexing
    body (_index_one) that the bulk build uses, so an index split as build(prefix)+add(rest) must be
    indistinguishable from build(whole).
    """
    for i in range(6):
        event_log.write_direct(
            _node_touched(
                local_intent_id=f"u{i}",
                entity_id=f"c{i % 3}",
                task_id=f"t{i % 2}",
                run_id=f"r{i % 2}",
                actor_id=f"a{i % 2}",
            ),
        )
    recs = event_log.all_records()
    assert len(recs) == 6

    full = EventIndex(recs)
    incremental = EventIndex(recs[:2])
    incremental.add_records(recs[2:])
    _assert_indexes_equivalent(incremental, full)


def test_catch_up_matches_full_rebuild_over_mixed_stream(gate_and_log: tuple[CommitGate, EventLog]) -> None:
    """The warm (catch-up) index == a fresh full rebuild over a mixed path-B + path-A stream.

    Interleaves path-B writes and path-A commits with a read after each (so each read folds the
    delta), then cross-checks the warm index against a brand-new EventLog rebuilding from the same
    event files. Path-A matters: its records only become committed-visible at the sentinel, and the
    catch-up applies the IDENTICAL committed-visibility filter to the active segment.
    """
    g, log = gate_and_log
    log.committed_index()  # initial full build (after the bootstrap commit) — warms + sets baseline

    for i in range(5):
        log.write_direct(_node_touched(local_intent_id=f"nb{i}", entity_id=f"e{i}", task_id=f"t{i % 2}"))
        log.committed_index()  # catch up the path-B write
        g.attempt_commit(_envelope(), [_concept_domain()])
        log.committed_index()  # catch up the path-A batch

    warm = log.committed_index()
    fresh = EventLog(log.log_path)
    full = fresh.committed_index()
    _assert_indexes_equivalent(warm, full)


# ─── the choke is gone: warm read after write does not full-scan ──────────────────


def test_warm_read_after_write_does_not_full_scan(
    event_log: EventLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read after a write catches up from the ACTIVE segment, never re-scanning the whole ledger.

    This is the structural proof that the per-round full rebuild (the choke) is gone: once the index
    is warm, _iter_committed_records (the full-ledger scan) must not be touched on a subsequent
    write→read — poisoning it would raise if the old null-on-write + rebuild path still ran.
    """
    event_log.write_direct(_node_touched(local_intent_id="seed", task_id="t-1"))
    event_log.committed_index()  # one full build → warm + baseline

    monkeypatch.setattr(event_log, "_iter_committed_records", _boom)

    # write again + read: must fold the new record via the active-segment catch-up, not a full scan
    rec = event_log.write_direct(_node_touched(local_intent_id="after", task_id="t-2"))
    idx = event_log.committed_index()
    assert idx.lookup_event_id(rec.event_id) is not None, "catch-up must include the post-warm write"
    assert len(idx.lookup_task("t-2")) == 1
    assert len(idx.lookup_task("t-1")) == 1, "earlier records still present"
    # a no-write read is an O(1) size-gate return (still no full scan)
    assert event_log.get_event(rec.event_id) is not None


# ─── cross-process coherence: external writer picked up by catch-up ───────────────


def test_cross_process_external_write_picked_up_by_catch_up(log_path: Path) -> None:
    """A SEPARATE EventLog's committed write is folded into this one's warm index on the next read.

    The old null-on-write design only saw外部 writes after THIS process's own next write nulled the
    index (forcing a rebuild). The catch-up reads the active segment on every read, so an external
    commit is visible immediately — strictly fresher, and the coherence the daemon relies on
    (it must dispatch other processes' commit-gate events) is preserved.
    """
    a = EventLog(log_path)
    a.write_direct(_node_touched(local_intent_id="a1", task_id="t-1"))
    a.committed_index()  # warm a

    b = EventLog(log_path)  # a different process/instance on the same physical log
    rec = b.write_direct(_node_touched(local_intent_id="b1", task_id="t-2"))

    # a never wrote again, yet its next read catches up b's external commit
    assert a.get_event(rec.event_id) is not None
    assert len(a.get_events_by_task("t-2")) == 1
    assert len(a.get_events_by_task("t-1")) == 1


# ─── rotation safety: fall back to a correct full rebuild ─────────────────────────


def test_rotation_falls_back_to_full_rebuild_and_stays_correct(tmp_path: Path) -> None:
    """Writes crossing a segment rotation stay fully queryable (catch-up falls back to full rebuild).

    When the active segment changes (rotation), records with seq > the index max may live in the
    just-sealed segment the active-only catch-up won't scan — so the catch-up detects the rotation
    and rebuilds from all segments. This asserts no event is lost across multiple rotations and the
    result equals a fresh rebuild.
    """
    path = tmp_path / "events.log"
    log = EventLog(path, segment_max_events=3, segment_max_bytes=10_000_000)
    ids: list[str] = []
    for i in range(9):  # 9 events / 3 per segment ⇒ multiple rotations
        rec = log.write_direct(_node_touched(local_intent_id=f"r{i}", entity_id=f"c{i}", task_id="t-1"))
        ids.append(rec.event_id)
        log.committed_index()  # read each round, some of which cross a rotation boundary

    idx = log.committed_index()
    for eid in ids:
        assert idx.lookup_event_id(eid) is not None, f"{eid} lost across rotation"
    assert len(idx.lookup_task("t-1")) == 9

    fresh = EventLog(path, segment_max_events=3, segment_max_bytes=10_000_000)
    _assert_indexes_equivalent(idx, fresh.committed_index())


# ─── load + catch-up: a startup-loaded index also folds later external writes ─────


def test_loaded_index_then_external_write_caught_up(log_path: Path) -> None:
    """An index loaded from the persisted .idx at startup still catches up later external writes.

    A new process loads a fresh .idx (so __init__ sets the catch-up baseline), then another process
    appends. The loaded-then-warm index must fold that append on its next read — i.e. the baseline
    set at load time is correct.
    """
    writer = EventLog(log_path)
    writer.write_direct(_node_touched(local_intent_id="w1", task_id="t-1"))
    writer.get_event("trigger-persist")  # a read persists the .idx covering w1

    reader = EventLog(log_path)
    assert reader._index is not None, "fresh .idx should load at init (baseline set for catch-up)"

    other = EventLog(log_path)
    rec = other.write_direct(_node_touched(local_intent_id="w2", task_id="t-2"))

    assert reader.get_event(rec.event_id) is not None, "loaded index must catch up the later append"
    assert len(reader.get_events_by_task("t-2")) == 1
