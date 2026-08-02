"""Batch 0-B reducers — task_graph (T-L0-06) / ownership (T-L0-07) / at_reference_graph (T-L0-08).

External-observable done_criteria (LANDING-PLAN-TASKS):
  T-L0-06: catchup 后 task_graph.json 真物化全部 TaskNode/Dependency/ReadSet/WriteSet/RunCompleted，
           重放幂等 (state_hash 一致)。
  T-L0-07: ownership.json 真物化锁态，门禁 WriteConflict 改读它并在 claim 冲突时 reject
           (含僵尸锁 handoff 释放)。
  T-L0-08: at_reference_graph projection 真物化，分流从 conservative-miss 转真 attached_to 匹配。

Reducers are exercised through ProjectionStore._apply with hand-built EventRecords (the
established reducer-test pattern, cf. test_run023_concept_graph_projection.py). The gate
ownership check is exercised end-to-end through CommitGate.attempt_commit.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.commit_gate.gate import CommitGate
from towow.l0.commit_gate.semantic_checks import check_ownership_conflict
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _record(
    event_type: EventType,
    payload: dict[str, object],
    seq: int,
    *,
    run_id: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test", run_id=run_id),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _hash(d: object) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


# ═══ T-L0-06 task_graph reducer ═══════════════════════════════════════════════


def test_task_graph_materializes_all_event_kinds(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(EventType.TASK_NODE_CREATED, {"after_state": {"task_id": "T1", "task_type": "code_change", "description": "d", "target_artifacts": ["a.py"]}}, 1))
    store._apply(_record(EventType.TASK_NODE_CREATED, {"after_state": {"task_id": "T2", "task_type": "review", "description": "d2"}}, 2))
    store._apply(_record(EventType.TASK_DEPENDENCY_EDGE_ADDED, {"after_state": {"source_task_id": "T1", "target_task_id": "T2", "dependency_type": "ordering"}}, 3))
    store._apply(_record(EventType.TASK_READ_SET_CLAIMED, {"after_state": {"task_id": "T1", "read_set": [{"entity_type": "concept", "entity_id": "c1"}]}}, 4))
    store._apply(_record(EventType.TASK_WRITE_SET_CLAIMED, {"after_state": {"task_id": "T1", "write_set": [{"entity_type": "task", "entity_id": "w1"}]}}, 5))
    store._apply(_record(EventType.TASK_MODEL_TIER_ASSIGNED, {"after_state": {"task_id": "T1", "model_tier": "opus"}}, 6))
    store._apply(_record(EventType.TASK_PACKAGE_PUBLISHED, {"after_state": {"task_id": "T1", "package_hash": "sha256:x", "is_self_contained": True}}, 7))
    store._apply(_record(EventType.TASK_RUN_COMPLETED, {"after_state": {"task_id": "T1", "outcome": "success"}}, 8))

    tg = store.read("task_graph")
    assert tg is not None
    assert len(tg["nodes"]) == 2  # type: ignore[arg-type]
    assert len(tg["edges"]) == 1  # type: ignore[arg-type]
    t1 = next(n for n in tg["nodes"] if n["task_id"] == "T1")  # type: ignore[index,union-attr]
    assert t1["read_set"] == [{"entity_type": "concept", "entity_id": "c1"}]
    assert t1["write_set"] == [{"entity_type": "task", "entity_id": "w1"}]
    assert t1["model_tier"] == "opus"
    assert t1["package_hash"] == "sha256:x"
    assert t1["is_self_contained"] is True
    assert t1["last_run_outcome"] == "success"
    assert t1["status"] == "completed"  # success → completed


def test_task_run_5_outcome_status_mapping(tmp_path: Path) -> None:
    """T-L1-36: M-1.4 §3.6 5-outcome → TaskNode status 映射 (LEDGER Conflict 11).

    aborted_for_replan → in_progress (回 plan); aborted_unrecoverable/external → failed;
    aborted_advisor_decision → escalated (advisor 介入)。旧 4-outcome 值仍 cold-replay normalize。
    """
    cases = {
        "success": "completed",
        "aborted_for_replan": "in_progress",
        "aborted_unrecoverable": "failed",
        "aborted_external": "failed",
        "aborted_advisor_decision": "escalated",
        # deprecated_alias — historical events still replay
        "failure": "failed",
        "partial": "in_progress",
        "escalated": "escalated",
    }
    for i, (outcome, expected_status) in enumerate(cases.items(), start=1):
        store = ProjectionStore(tmp_path / f"graph-{i}")
        store._apply(
            _record(
                EventType.TASK_NODE_CREATED,
                {"after_state": {"task_id": "T1", "task_type": "code_change", "description": "d"}},
                1,
            ),
        )
        store._apply(
            _record(EventType.TASK_RUN_COMPLETED, {"after_state": {"task_id": "T1", "outcome": outcome}}, 2),
        )
        tg = store.read("task_graph")
        assert tg is not None
        node = next(n for n in tg["nodes"] if n["task_id"] == "T1")  # type: ignore[index,union-attr]
        assert node["last_run_outcome"] == outcome
        assert node["status"] == expected_status, f"outcome={outcome} → status {node['status']} != {expected_status}"


def test_task_graph_idempotent_replay(tmp_path: Path) -> None:
    events = [
        _record(EventType.TASK_NODE_CREATED, {"after_state": {"task_id": "T1", "task_type": "code_change", "description": "d"}}, 1),
        _record(EventType.TASK_DEPENDENCY_EDGE_ADDED, {"after_state": {"source_task_id": "T1", "target_task_id": "T2", "dependency_type": "ordering"}}, 2),
    ]
    s1 = ProjectionStore(tmp_path / "g1")
    for e in events:
        s1._apply(e)
    s2 = ProjectionStore(tmp_path / "g2")
    for e in [*events, *events]:  # double-replay
        s2._apply(e)
    assert _hash(s1.read("task_graph")) == _hash(s2.read("task_graph")), "reducer must be idempotent"


def test_task_graph_reducer_output_validates_through_model(tmp_path: Path) -> None:
    """The TaskGraphState model must accept the reducer's own output verbatim.

    Regression: obligation_lifecycle_scan daemon reads task_graph then
    TaskGraphState.model_validate(...) — it crashed with 166 extra_forbidden errors
    because the reducer threads plan_id (M-1.3 §3.1 declared payload field) and
    created_at_event_id (provenance, M-0.2 convention) onto every TaskNode, but the
    model (extra='forbid') declared neither. Reducer output must round-trip through
    the projection model that every consumer validates against.
    """
    from towow.schemas.projection_state import TaskGraphState

    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.TASK_NODE_CREATED,
            {
                "after_state": {
                    "task_id": "T-plan42-001",
                    "task_type": "code_change",
                    "description": "d",
                    "plan_id": "plan-42",  # M-1.3 §3.1 declared field, threaded by reducer
                },
            },
            1,
        ),
    )
    raw = store.read("task_graph")
    assert raw is not None
    node = raw["nodes"][0]  # type: ignore[index]
    assert "plan_id" in node and "created_at_event_id" in node  # reducer emits both

    # This is exactly what _scan_obligation_lifecycle_emit does and is what crashed.
    state = TaskGraphState.model_validate(raw, strict=False)
    assert state.nodes[0].plan_id == "plan-42"
    assert state.nodes[0].created_at_event_id == node["created_at_event_id"]


# ═══ T-L0-08 at_reference_graph reducer ═══════════════════════════════════════


def test_at_reference_graph_materializes_and_deactivates(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.AT_REFERENCE_ADDED,
            {"after_state": {
                "reference_id": "ref-1",
                "source_entity": {"type": "concept", "id": "c1@v1", "field_path": "definition"},
                "target_entity": {"type": "spec_document", "id": "M-0.2", "field_path": "3.5"},
                "reference_type": "semantic",
            }},
            1,
        ),
    )
    arg = store.read("at_reference_graph")
    assert arg is not None
    ref = arg["references"][0]  # type: ignore[index]
    assert ref["reference_id"] == "ref-1"
    assert ref["source"]["entity_type"] == "concept"
    assert ref["source"]["entity_id"] == "c1@v1"
    assert ref["target"]["entity_type"] == "spec_document"  # producer 'type'→entity_type mapped
    assert ref["is_active"] is True

    store._apply(_record(EventType.AT_REFERENCE_REMOVED, {"after_state": {"reference_id": "ref-1"}}, 2))
    ref2 = store.read("at_reference_graph")["references"][0]  # type: ignore[index]
    assert ref2["is_active"] is False, "AtReferenceRemoved marks is_active=false, not delete"


# ═══ T-L0-07 ownership reducer ════════════════════════════════════════════════


def test_ownership_locks_on_write_set_claim(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.TASK_WRITE_SET_CLAIMED,
            {"after_state": {"task_id": "T1", "write_set": [{"entity_type": "concept", "entity_id": "E"}]}},
            1,
            run_id="run-A",
        ),
    )
    own = store.read("ownership")
    assert own is not None
    e = own["entities"][0]  # type: ignore[index]
    assert e["entity_id"] == "E"
    assert e["write_locked"] is True
    assert e["owner_task_id"] == "T1"
    assert e["lock_holder_run_id"] == "run-A"


def test_ownership_lock_released_by_lock_released_event(tmp_path: Path) -> None:
    """Patch 8 zombie-lock handoff: LockReleased frees the entity."""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(EventType.TASK_WRITE_SET_CLAIMED, {"after_state": {"task_id": "T1", "write_set": [{"entity_type": "concept", "entity_id": "E"}]}}, 1, run_id="run-A"))
    store._apply(
        _record(
            EventType.LOCK_RELEASED,
            {"after_state": {"task_id": "T1", "run_id": "run-A", "entities": [{"entity_type": "concept", "entity_id": "E"}], "release_reason": "abandoned"}},
            2,
        ),
    )
    e = store.read("ownership")["entities"][0]  # type: ignore[index]
    assert e["write_locked"] is False
    assert e["lock_holder_run_id"] is None


def test_ownership_released_on_commit_accepted(tmp_path: Path) -> None:
    """CommitAccepted → resolve envelope_event_id → release the envelope's write_set locks."""
    event_log = EventLog(tmp_path / "events.log")
    envelope = event_log.write_direct(
        EventIntent(
            local_intent_id="env-1",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
            payload={"envelope_id": "env-1", "write_set": [{"entity_type": "concept", "entity_id": "E"}]},
            provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="t"),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="E", role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
    )
    store = ProjectionStore(tmp_path / "graph")
    store._reduce_event_log = event_log
    store._apply(_record(EventType.TASK_WRITE_SET_CLAIMED, {"after_state": {"task_id": "T1", "write_set": [{"entity_type": "concept", "entity_id": "E"}]}}, 0, run_id="run-A"))
    store._apply(_record(EventType.COMMIT_ACCEPTED, {"envelope_event_id": envelope.event_id}, 1))
    e = store.read("ownership")["entities"][0]  # type: ignore[index]
    assert e["write_locked"] is False, "CommitAccepted must release the committed envelope's write_set locks"


# ═══ T-L0-07 check_ownership_conflict (gate read path) ════════════════════════


_LOCKED = {"entities": [{"entity_type": "concept", "entity_id": "E", "write_locked": True, "owner_task_id": "T1", "lock_holder_run_id": "run-A"}]}
_WS = [{"entity_type": "concept", "entity_id": "E", "change_type": "modified"}]


def test_ownership_conflict_rejects_cross_run_lock() -> None:
    r = check_ownership_conflict(_LOCKED, _WS, "run-B")
    assert not r.passed
    assert r.failure_reason == "write_conflict"
    assert r.failure_evidence["lock_holder_run_id"] == "run-A"


def test_ownership_conflict_self_run_passes() -> None:
    assert check_ownership_conflict(_LOCKED, _WS, "run-A").passed


def test_ownership_conflict_null_run_lock_does_not_block() -> None:
    """A null-run lock (boundary-less zombie) is not attributable to a live run → no block."""
    zombie = {"entities": [{"entity_type": "concept", "entity_id": "E", "write_locked": True, "owner_task_id": "T1", "lock_holder_run_id": None}]}
    assert check_ownership_conflict(zombie, _WS, "run-B").passed


def test_ownership_conflict_boundaryless_skips() -> None:
    assert check_ownership_conflict(_LOCKED, _WS, None).skipped
    assert check_ownership_conflict(_LOCKED, [], "run-B").skipped


# ═══ T-L0-07 gate end-to-end: claim conflict → reject; LockReleased → unblock ══


def _prov(run_id: str) -> ProvenanceHint:
    return ProvenanceHint(actor_type=ActorType.AGENT_SESSION.value, actor_id="exec", session_id="s", skill_id="M-1.4", run_id=run_id)


def _claim_intent(run_id: str, entity_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"claim-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_WRITE_SET_CLAIMED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"target_entity_type": "task", "transition_type": "modified", "after_state": {"task_id": "T-A", "write_set": [{"entity_type": "concept", "entity_id": entity_id}]}},
        provenance_hint=_prov(run_id),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="T-A", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _envelope_intent(*, run_id: str, write_entity: str | None, as_of: int) -> EventIntent:
    env_id = f"env-{uuid.uuid4().hex[:8]}"
    write_set = [{"entity_type": "concept", "entity_id": write_entity, "change_type": "modified"}] if write_entity else []
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id, "task_id": "T-B", "run_id": run_id, "fork_id": "f",
            "capsule_compiled_event_id": "evt-stub-capsule-x", "as_of_projection_seq": as_of,
            "write_set": write_set, "read_set": [],
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
        },
        provenance_hint=_prov(run_id),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=env_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _last_commit_type(event_log: EventLog) -> EventType:
    for rec in reversed(event_log.all_records()):
        if rec.event_type in {EventType.COMMIT_ACCEPTED, EventType.COMMIT_REJECTED}:
            return rec.event_type
    raise AssertionError("no commit sentinel")


def test_gate_rejects_ownership_claim_conflict_then_lock_release_unblocks(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    event_log = EventLog(towow / "events.log")
    gate = CommitGate(event_log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())

    # run-A claims entity E via a commit whose envelope has NO write_set (so CommitAccepted does
    # not release E) — E stays locked by run-A. (envelope as_of=0 → temporal/ownership skip.)
    gate.attempt_commit(_envelope_intent(run_id="run-A", write_entity=None, as_of=0), [_claim_intent("run-A", "E")])
    assert _last_commit_type(event_log) is EventType.COMMIT_ACCEPTED

    max_seq = event_log.next_sequence - 1
    # run-B tries to write E → ownership check sees E locked by run-A (!= run-B) → reject.
    gate.attempt_commit(_envelope_intent(run_id="run-B", write_entity="E", as_of=max_seq), [_claim_intent("run-B", "Z")])
    assert _last_commit_type(event_log) is EventType.COMMIT_REJECTED, "cross-run lock on E must reject"

    # zombie-lock handoff: release E's lock, then run-B's same commit passes ownership.
    event_log.write_direct(
        EventIntent(
            local_intent_id=f"lr-{uuid.uuid4().hex[:8]}",
            event_type=EventType.NODE_TOUCHED,  # placeholder path-B write to advance the log
            event_category=EventCategory.ENVELOPE,
            payload={"kind": "noop"},
            provenance_hint=_prov("run-A"),
            base_classification=BaseClassification.DISCARDABLE_NOISE,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="x", role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
    )
    # Release via a committed LockReleased (path A through the gate, run-A releasing E).
    lr = EventIntent(
        local_intent_id=f"lrd-{uuid.uuid4().hex[:8]}",
        event_type=EventType.LOCK_RELEASED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"target_entity_type": "task", "transition_type": "modified", "after_state": {"task_id": "T-A", "run_id": "run-A", "entities": [{"entity_type": "concept", "entity_id": "E"}], "release_reason": "abandoned"}},
        provenance_hint=_prov("run-A"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="T-A", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    gate.attempt_commit(_envelope_intent(run_id="run-A", write_entity=None, as_of=0), [lr])
    assert _last_commit_type(event_log) is EventType.COMMIT_ACCEPTED

    max_seq2 = event_log.next_sequence - 1
    gate.attempt_commit(_envelope_intent(run_id="run-B", write_entity="E", as_of=max_seq2), [_claim_intent("run-B", "Z2")])
    assert _last_commit_type(event_log) is EventType.COMMIT_ACCEPTED, "after LockReleased, E is free → run-B passes"


# ═══ ownership projection is never read-and-discarded on the skip preconditions ═══════
#
# check_ownership_conflict's skip branches (semantic_checks.py ~L99-109) — no run_id / empty
# write_set — never touch the ownership_projection argument, so the gate must never pay
# _read_ownership_projection() (per-commit ProjectionStore.catchup) for such a commit. The
# property holds via two layers: (1) §3.0 EnvelopeIntegrity rejects empty write_set BEFORE
# the ownership check is reached (envelope/checks.py ~L106), and (2) gate.py's call-site
# guard only reads the projection when run_id + write_set are non-empty (defensive — fires
# only if the integrity guarantee ever relaxes). These tests pin the property from both sides.


def test_gate_empty_write_set_never_pays_ownership_projection_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    event_log = EventLog(towow / "events.log")
    gate = CommitGate(event_log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())

    def _boom() -> object:
        raise AssertionError(
            "must NOT read ownership projection for an empty-write_set commit — "
            "it is rejected at §3.0 integrity / skipped by the gate's precondition guard",
        )

    monkeypatch.setattr(gate, "_read_ownership_projection", _boom)

    # write_entity=None AND no domain intents → _normalize_envelope_payload's write_set
    # auto-derivation (gate.py ~L1188-1200) has nothing to derive from, so the checked
    # write_set stays genuinely empty. Such a commit is rejected at §3.0 EnvelopeIntegrity
    # (envelope_malformed / field=write_set) — BEFORE the ownership check — hence the
    # projection must never be read (same outcome before and after the call-site guard).
    gate.attempt_commit(
        _envelope_intent(run_id="run-A", write_entity=None, as_of=0),
        [],
    )
    assert _last_commit_type(event_log) is EventType.COMMIT_REJECTED
    rejected = next(
        rec for rec in reversed(event_log.all_records())
        if rec.event_type is EventType.COMMIT_REJECTED
    )
    reasons = rejected.payload.get("rejection_reasons")
    assert isinstance(reasons, list), "expected integrity rejection reasons list"
    assert reasons, "expected at least one rejection reason"
    assert reasons[0].get("failure_reason") == "envelope_malformed"
    assert reasons[0].get("evidence", {}).get("field") == "write_set"


def test_gate_ownership_reads_projection_when_write_set_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    event_log = EventLog(towow / "events.log")
    gate = CommitGate(event_log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())

    calls: list[None] = []
    original = gate._read_ownership_projection

    def _spy() -> dict[str, object] | None:
        calls.append(None)
        return original()

    monkeypatch.setattr(gate, "_read_ownership_projection", _spy)

    gate.attempt_commit(
        _envelope_intent(run_id="run-A", write_entity="E", as_of=0),
        [_claim_intent("run-A", "E")],
    )
    assert _last_commit_type(event_log) is EventType.COMMIT_ACCEPTED
    assert calls, "non-empty write_set + run_id must still read the ownership projection"
