"""Unit tests for EventRecord / Provenance."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance


def test_provenance_extends_hint_with_writer_id() -> None:
    p = Provenance(
        actor_type=ActorType.CAPSULE_ASSEMBLER.value,
        actor_id="m03-assembler",
        writer_id="m01-writer",
    )
    assert p.writer_id == "m01-writer"
    assert p.actor_id == "m03-assembler"


def test_provenance_writer_id_optional() -> None:
    p = Provenance(actor_type=ActorType.NATURE.value, actor_id="nature-1")
    assert p.writer_id is None


def test_event_record_path_a_all_set(make_record, provenance, supersede_no, subject_primary_task) -> None:
    rec = make_record(
        sequence_number=10,
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        local_intent_id="i-1",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=0,
    )
    assert rec.transaction_id == "tx-1"
    assert rec.batch_position == 0


def test_event_record_path_b_all_none(make_record, provenance, supersede_no, subject_primary_task) -> None:
    rec = make_record(
        sequence_number=11,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        local_intent_id="i-2",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
    )
    assert rec.transaction_id is None
    assert rec.batch_id is None
    assert rec.batch_position is None


@pytest.mark.invariant
def test_event_record_mixed_path_rejected(make_record, provenance, supersede_no, subject_primary_task) -> None:
    """M-0.1a Patch 1 §1.4 invariant: tx/batch/position 必须要么全 set 要么全 None."""
    with pytest.raises(ValidationError, match=r"all be set \(path A\) or all None \(path B\)"):
        make_record(
            sequence_number=12,
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            local_intent_id="i-3",
            provenance=provenance,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            transaction_id="tx-1",
            batch_id=None,  # missing
            batch_position=0,
        )


def test_event_record_event_id_pattern_required(provenance, supersede_no, subject_primary_task) -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        EventRecord(
            event_id="not-prefixed",
            sequence_number=1,
            timestamp=datetime(2026, 5, 21, tzinfo=UTC),
            transaction_id=None,
            batch_id=None,
            batch_position=None,
            record_hash="h",
            local_intent_id="i-1",
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={},
            provenance=provenance,
            base_classification=BaseClassification.DISCARDABLE_NOISE,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            schema_version="1.0.0",
        )


def test_event_record_sequence_number_non_negative(provenance, supersede_no, subject_primary_task) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        EventRecord(
            event_id="evt-abc",
            sequence_number=-1,
            timestamp=datetime(2026, 5, 21, tzinfo=UTC),
            transaction_id=None,
            batch_id=None,
            batch_position=None,
            record_hash="h",
            local_intent_id="i-1",
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={},
            provenance=provenance,
            base_classification=BaseClassification.DISCARDABLE_NOISE,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            schema_version="1.0.0",
        )


def test_event_record_json_round_trip(
    make_record,
    provenance,
    supersede_no,
    subject_primary_task: Subject,
) -> None:
    rec = make_record(
        sequence_number=99,
        event_type=EventType.OBLIGATION_CAPTURED,
        event_category=EventCategory.OBLIGATION,
        local_intent_id="i-99",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
        transaction_id="tx-99",
        batch_id="b-99",
        batch_position=0,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    raw = rec.model_dump_json()
    rebuilt = EventRecord.model_validate_json(raw)
    assert rebuilt == rec


@pytest.mark.invariant
def test_event_record_frozen(make_record, provenance, supersede_no, subject_primary_task: Subject) -> None:
    """Event sourcing invariant: EventRecord is immutable (append-only)."""
    rec = make_record(
        sequence_number=1,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        local_intent_id="i-frozen",
        provenance=provenance,
        supersede=supersede_no,
        subjects=[subject_primary_task],
    )
    with pytest.raises(ValidationError, match="frozen"):
        rec.sequence_number = 2


def test_event_record_reuses_supersede_invariant() -> None:
    """EventRecord reuses Supersede from event_intent.py — invariant enforced at Supersede ctor."""
    with pytest.raises(ValidationError, match="novelty_type"):
        Supersede(
            is_supersede=True,
            superseded_event_id="evt-old",
            novelty="error corrected",
            novelty_type=None,
        )
