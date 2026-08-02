"""Shared fixtures for schemas unit tests — build valid sample instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    BatchStatus,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.transaction_batch import TransactionBatch


@pytest.fixture
def provenance_hint() -> ProvenanceHint:
    return ProvenanceHint(
        actor_type=ActorType.AGENT_FORK.value,
        actor_id="fork-001",
        session_id="sess-001",
        task_id="task-001",
        run_id="run-001",
        causation_event_id=None,
        correlation_id=None,
        # E.1.1 Conflict 2: agent_fork requires parent_session_id + fork_name
        parent_session_id="sess-001",
        fork_name="fork-001",
    )


@pytest.fixture
def provenance() -> Provenance:
    return Provenance(
        actor_type=ActorType.COMMIT_GATE.value,
        actor_id="m05-commit-gate",
        session_id=None,
        task_id="task-001",
        run_id="run-001",
        causation_event_id=None,
        correlation_id=None,
        writer_id="m01-writer",
    )


@pytest.fixture
def supersede_no() -> Supersede:
    return Supersede(is_supersede=False, superseded_event_id=None, novelty=None, novelty_type=None)


@pytest.fixture
def subject_primary_task() -> Subject:
    return Subject(entity_type=SubjectEntityType.TASK, entity_id="task-001", role=SubjectRole.PRIMARY)


@pytest.fixture
def event_intent_envelope(
    provenance_hint: ProvenanceHint,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> EventIntent:
    return EventIntent(
        local_intent_id="intent-env-001",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={"envelope_id": "env-001", "patches": []},
        provenance_hint=provenance_hint,
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        schema_version="1.0.0",
    )


def _make_record(
    *,
    sequence_number: int,
    event_type: EventType,
    event_category: EventCategory,
    local_intent_id: str,
    provenance: Provenance,
    supersede: Supersede,
    subjects: list[Subject],
    transaction_id: str | None = None,
    batch_id: str | None = None,
    batch_position: int | None = None,
    base_classification: BaseClassification = BaseClassification.ABSTRACTABLE_PROCESS,
    payload: dict[str, object] | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{local_intent_id}",
        sequence_number=sequence_number,
        timestamp=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC) + timedelta(microseconds=sequence_number),
        transaction_id=transaction_id,
        batch_id=batch_id,
        batch_position=batch_position,
        record_hash=f"hash-{sequence_number:08d}",
        local_intent_id=local_intent_id,
        event_type=event_type,
        event_category=event_category,
        payload=payload if payload is not None else {},
        provenance=provenance,
        base_classification=base_classification,
        supersede=supersede,
        subjects=subjects,
        schema_version="1.0.0",
    )


@pytest.fixture
def make_record():
    """Factory that returns valid EventRecord instances with overridable fields."""
    return _make_record


@pytest.fixture
def accepted_batch(
    provenance: Provenance,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> TransactionBatch:
    """E.1.1-canonical accepted batch: [domain, CommitAccepted]. Envelope on path B (not in batch)."""
    tx = "tx-001"
    bid = "batch-001"
    domain = _make_record(
        sequence_number=101,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="concept-001",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
    )
    sentinel = _make_record(
        sequence_number=102,
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="commit-accepted-001",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    return TransactionBatch(
        batch_id=bid,
        transaction_id=tx,
        status=BatchStatus.COMMITTED,
        events=[domain, sentinel],
    )


@pytest.fixture
def rejected_batch(
    provenance: Provenance,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> TransactionBatch:
    """E.1.1-canonical rejected batch: [CommitRejected]. Envelope on path B (not in batch)."""
    tx = "tx-002"
    bid = "batch-002"
    sentinel = _make_record(
        sequence_number=201,
        event_type=EventType.COMMIT_REJECTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="commit-rejected-001",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    return TransactionBatch(
        batch_id=bid,
        transaction_id=tx,
        status=BatchStatus.REJECTED,
        events=[sentinel],
    )
