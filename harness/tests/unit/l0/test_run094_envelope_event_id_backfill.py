"""RUN-094 件B (M-0.4 §4.3) — execution→commit backlink: TaskRunCompleted.envelope_event_id.

The envelope event_id is only known after its path-B write_direct, so execution submits a
`local:envelope` placeholder in TaskRunCompleted.after_state.envelope_event_id and the gate
resolves it to the real envelope event_id in the same accepted batch (same shape as
backfill_semantic_sibling_refs / patch detail_ref backfill). RUN-094 baseline: 25/25
TaskRunCompleted carried a hardcoded None — execution→commit was not traceable (audit v2 §2 R4).

Two layers of coverage:
  - unit: backfill_envelope_event_id_refs rewrites the placeholder / passes everything else through.
  - end-to-end: a real commit-gate accept stamps a resolvable, non-null backlink that round-trips
    via EventLog.get_event(envelope_event_id) → the envelope (before=None / after=real).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from towow.l0.commit_gate import CommitGate
from towow.l0.envelope.builder import backfill_envelope_event_id_refs
from towow.l0.event_log import EventLog
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
from towow.schemas.event_record import EventRecord, Provenance

_PLACEHOLDER = "local:envelope"


def _record(seq: int, event_type: EventType, payload: dict[str, object]) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 6, 2, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=seq,
        record_hash=f"h-{seq}",
        local_intent_id=f"li-{seq}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type="system", actor_id="s", writer_id="w"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _trc_payload(envelope_event_id: object) -> dict[str, object]:
    return {
        "target_entity_type": TargetEntityType.TASK.value,
        "transition_type": TransitionType.MODIFIED.value,
        "after_state": {
            "task_id": "task-1",
            "run_id": "run-1",
            "outcome": "success",
            "envelope_event_id": envelope_event_id,
        },
    }


# ── unit: backfill_envelope_event_id_refs ──────────────────────────────────────


def test_placeholder_backfilled_to_envelope_event_id() -> None:
    """TaskRunCompleted placeholder envelope_event_id is rewritten to the real envelope id."""
    records = [
        _record(1, EventType.CONCEPT_CREATED, {"concept_id": "c-1"}),
        _record(2, EventType.TASK_RUN_COMPLETED, _trc_payload(_PLACEHOLDER)),
    ]
    out = backfill_envelope_event_id_refs(records, envelope_event_id="evt-envelope-real")
    assert out[0].payload == {"concept_id": "c-1"}  # untouched (no placeholder)
    assert out[1].payload["after_state"]["envelope_event_id"] == "evt-envelope-real"


def test_real_envelope_ref_passed_through_untouched() -> None:
    """An envelope_event_id already set to a real ref is preserved verbatim."""
    records = [_record(2, EventType.TASK_RUN_COMPLETED, _trc_payload("evt-already-set"))]
    out = backfill_envelope_event_id_refs(records, envelope_event_id="evt-envelope-real")
    assert out[0].payload["after_state"]["envelope_event_id"] == "evt-already-set"


def test_none_envelope_ref_passed_through_untouched() -> None:
    """Legacy None envelope_event_id (pre-RUN-094 events) is not a placeholder → left as None."""
    records = [_record(2, EventType.TASK_RUN_COMPLETED, _trc_payload(None))]
    out = backfill_envelope_event_id_refs(records, envelope_event_id="evt-envelope-real")
    assert out[0].payload["after_state"]["envelope_event_id"] is None


def test_no_after_state_is_noop() -> None:
    """A record with no after_state dict passes through unchanged."""
    records = [_record(1, EventType.CONCEPT_CREATED, {"concept_id": "c-1"})]
    out = backfill_envelope_event_id_refs(records, envelope_event_id="evt-x")
    assert out[0].payload == {"concept_id": "c-1"}


def test_original_record_not_mutated() -> None:
    """Backfill returns new records — the caller's input is not mutated in place."""
    rec = _record(2, EventType.TASK_RUN_COMPLETED, _trc_payload(_PLACEHOLDER))
    backfill_envelope_event_id_refs([rec], envelope_event_id="evt-envelope-real")
    assert rec.payload["after_state"]["envelope_event_id"] == _PLACEHOLDER  # original intact


# ── end-to-end: real commit-gate accept produces a resolvable backlink ──────────


def _envelope_intent(env_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": "sess-run094",
            "capsule_compiled_event_id": "evt-stub-capsule-work",
            "self_check": {"passed": True, "checks_run": ["x"], "blocking_checks": [
                {"check_id": "x", "status": "passed", "evidence": "ok"},
            ]},
            "active_obligations_declared": [],
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m14-execution-session",
            session_id="sess-run094",
            skill_id="M-1.4",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="task-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _trc_intent() -> EventIntent:
    return EventIntent(
        local_intent_id="trc-run094",
        event_type=EventType.TASK_RUN_COMPLETED,
        event_category=EventCategory.STATE_TRANSITION,
        payload=_trc_payload(_PLACEHOLDER),
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m14-execution-session",
            session_id="sess-run094",
            skill_id="M-1.4",
            task_id="task-1",
            run_id="run-1",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="task-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_gate_accept_stamps_resolvable_envelope_backlink(tmp_path: Path) -> None:
    """A real commit-gate accept resolves the placeholder to the envelope's REAL event_id, and
    EventLog.get_event(envelope_event_id) round-trips back to that TransactionEnvelopeSubmitted."""
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    records = gate.attempt_commit(_envelope_intent("env-run094"), [_trc_intent()])
    assert isinstance(records, list)
    envelope_record = records[0]
    assert envelope_record.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED
    trc = next(r for r in records if r.event_type is EventType.TASK_RUN_COMPLETED)

    backlink = trc.payload["after_state"]["envelope_event_id"]
    # before=None (placeholder unset) → after=the real envelope event_id (not the placeholder, not None)
    assert backlink is not None
    assert backlink != _PLACEHOLDER
    assert backlink == envelope_record.event_id
    # resolvable: the backlink id resolves to the very envelope this task run committed under.
    resolved = log.get_event(backlink)
    assert resolved is not None
    assert resolved.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED
    assert resolved.event_id == envelope_record.event_id
