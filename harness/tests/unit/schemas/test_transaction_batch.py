"""Unit tests for TransactionBatch (E.1.1-canonical per LEDGER Conflict 4/6/7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    BaseClassification,
    BatchStatus,
    EventCategory,
    EventType,
)
from towow.schemas.transaction_batch import TransactionBatch


def test_accepted_batch_valid(accepted_batch: TransactionBatch) -> None:
    """E.1.1: accepted batch = [domain, CommitAccepted] — no envelope in batch."""
    assert accepted_batch.status is BatchStatus.COMMITTED
    assert len(accepted_batch.events) == 2
    assert accepted_batch.events[0].event_type is EventType.CONCEPT_CREATED
    assert accepted_batch.events[-1].event_type is EventType.COMMIT_ACCEPTED


def test_rejected_batch_valid(rejected_batch: TransactionBatch) -> None:
    """E.1.1: rejected batch = [CommitRejected] (minimal) — no envelope in batch."""
    assert rejected_batch.status is BatchStatus.REJECTED
    assert len(rejected_batch.events) == 1
    assert rejected_batch.events[-1].event_type is EventType.COMMIT_REJECTED


@pytest.mark.invariant
def test_batch_rejects_envelope_in_any_position(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """E.1.1 LEDGER Conflict 4/6/7: TransactionEnvelopeSubmitted must NEVER appear in path-A batch."""
    tx, bid = "tx-x", "b-x"
    envelope_in_batch = make_record(
        sequence_number=1,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        local_intent_id="env-bad",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
    )
    sentinel = make_record(
        sequence_number=2,
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="ca",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    with pytest.raises(ValidationError, match="must not appear in path-A batch"):
        TransactionBatch(
            batch_id=bid,
            transaction_id=tx,
            status=BatchStatus.COMMITTED,
            events=[envelope_in_batch, sentinel],
        )


@pytest.mark.invariant
def test_batch_sequence_number_must_strictly_increase(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """Patch 2 §2.1: sequence_number 严格递增."""
    tx, bid = "tx-y", "b-y"
    a = make_record(
        sequence_number=10,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="a",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
    )
    b = make_record(
        sequence_number=10,  # duplicate seq
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="b",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    with pytest.raises(ValidationError, match="strictly increasing"):
        TransactionBatch(
            batch_id=bid,
            transaction_id=tx,
            status=BatchStatus.COMMITTED,
            events=[a, b],
        )


@pytest.mark.invariant
def test_batch_transaction_id_mismatch_rejected(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """Every event's transaction_id must match batch.transaction_id."""
    bid = "b-z"
    domain = make_record(
        sequence_number=1,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="d",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id="tx-A",
        batch_id=bid,
        batch_position=0,
    )
    sentinel = make_record(
        sequence_number=2,
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="ca",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id="tx-B",  # mismatch
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    with pytest.raises(ValidationError, match="differs from batch transaction_id"):
        TransactionBatch(
            batch_id=bid,
            transaction_id="tx-A",
            status=BatchStatus.COMMITTED,
            events=[domain, sentinel],
        )


@pytest.mark.invariant
def test_accepted_batch_sentinel_must_be_commit_accepted(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    tx, bid = "tx-q", "b-q"
    domain = make_record(
        sequence_number=1,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="d",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
    )
    wrong_sentinel = make_record(
        sequence_number=2,
        event_type=EventType.COMMIT_REJECTED,  # wrong sentinel for committed batch
        event_category=EventCategory.COMMIT,
        local_intent_id="cr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    with pytest.raises(ValidationError, match="requires sentinel=CommitAccepted"):
        TransactionBatch(
            batch_id=bid,
            transaction_id=tx,
            status=BatchStatus.COMMITTED,
            events=[domain, wrong_sentinel],
        )


@pytest.mark.invariant
def test_rejected_batch_allows_gate_lifecycle_events(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """M-0.5 §6.4: rejected batch may contain gate-detected lifecycle events
    (ObligationViolated / LockReleased) — but NEVER envelope (E.1.1 Conflict 4/6/7).
    """
    tx, bid = "tx-r", "b-r"
    violation = make_record(
        sequence_number=1,
        event_type=EventType.OBLIGATION_VIOLATED,
        event_category=EventCategory.OBLIGATION,
        local_intent_id="ov",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    lock = make_record(
        sequence_number=2,
        event_type=EventType.LOCK_RELEASED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="lr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
    )
    sentinel = make_record(
        sequence_number=3,
        event_type=EventType.COMMIT_REJECTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="cr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=2,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    batch = TransactionBatch(
        batch_id=bid,
        transaction_id=tx,
        status=BatchStatus.REJECTED,
        events=[violation, lock, sentinel],
    )
    assert len(batch.events) == 3
    assert batch.events[-1].event_type is EventType.COMMIT_REJECTED


def test_recovery_batch_minimal_form(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """E.1.1 Conflict 4/6/7: recovery batch = [LockReleased, CommitRejected] (no envelope)."""
    tx, bid = "tx-rec", "b-rec"
    lock = make_record(
        sequence_number=10,
        event_type=EventType.LOCK_RELEASED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="lr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
    )
    sentinel = make_record(
        sequence_number=11,
        event_type=EventType.COMMIT_REJECTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="cr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=1,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    batch = TransactionBatch(
        batch_id=bid,
        transaction_id=tx,
        status=BatchStatus.REJECTED,
        events=[lock, sentinel],
    )
    assert len(batch.events) == 2


def test_batch_min_length_1_sentinel_only(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """E.1.1: batch with only a sentinel (no domain/lifecycle) is structurally valid."""
    tx, bid = "tx-1", "b-1"
    sentinel = make_record(
        sequence_number=1,
        event_type=EventType.COMMIT_REJECTED,
        event_category=EventCategory.COMMIT,
        local_intent_id="cr",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id=tx,
        batch_id=bid,
        batch_position=0,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    batch = TransactionBatch(
        batch_id=bid,
        transaction_id=tx,
        status=BatchStatus.REJECTED,
        events=[sentinel],
    )
    assert len(batch.events) == 1


def test_batch_min_length_zero_rejected(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task,
) -> None:
    """Empty batch rejected (min_length=1)."""
    with pytest.raises(ValidationError, match="at least 1 item"):
        TransactionBatch(
            batch_id="b-empty",
            transaction_id="tx-empty",
            status=BatchStatus.COMMITTED,
            events=[],
        )


def test_batch_json_round_trip(accepted_batch: TransactionBatch) -> None:
    raw = accepted_batch.model_dump_json()
    rebuilt = TransactionBatch.model_validate_json(raw)
    assert rebuilt == accepted_batch
