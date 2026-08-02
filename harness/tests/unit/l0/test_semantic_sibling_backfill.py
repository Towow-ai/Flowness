"""RUN-038 L0增强 M-0.4 §7.5 — semantic-upgrade fields go via a sibling event, not inlined.

Cap4 builder unit: backfill_semantic_sibling_refs resolves a SemanticUpgradeDeclaration's
sibling_event_id placeholder (`local:envelope`) to the real envelope event_id — the same backfill
shape as T-L0-27 patch detail_refs (the envelope event_id is only known after its path-B write).
M-0.4 §7.5: the semantic interpretation is a first-class sibling event co-submitted in the SAME
accepted batch, never an inlined envelope field.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from towow.l0.envelope.builder import backfill_semantic_sibling_refs
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

_PLACEHOLDER = "local:envelope"


def _record(
    seq: int,
    event_type: EventType,
    payload: dict[str, object],
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 5, 31, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=seq,
        record_hash=f"h-{seq}",
        local_intent_id=f"li-{seq}",
        event_type=event_type,
        event_category=(
            EventCategory.SEMANTIC_JUDGMENT
            if event_type is EventType.SEMANTIC_UPGRADE_DECLARATION
            else EventCategory.STATE_TRANSITION
        ),
        payload=payload,
        provenance=Provenance(actor_type="system", actor_id="s", writer_id="w"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _sud_payload(sibling: str) -> dict[str, object]:
    return {
        "judgment_type": "semantic_upgrade",
        "after_state": {
            "sibling_event_id": sibling,
            "semantic_changes": [
                {"change_type": "new_concept", "affected_entity_id": "c-1", "relationship_to_graph": "adds c-1"},
            ],
        },
        "confidence": None,
    }


def test_sibling_placeholder_backfilled_to_envelope_event_id() -> None:
    """The SemanticUpgradeDeclaration's placeholder sibling_event_id is rewritten to the real
    envelope event_id; non-semantic records pass through untouched."""
    records = [
        _record(1, EventType.CONCEPT_CREATED, {"concept_id": "c-1"}),
        _record(2, EventType.SEMANTIC_UPGRADE_DECLARATION, _sud_payload(_PLACEHOLDER)),
    ]
    out = backfill_semantic_sibling_refs(records, envelope_event_id="evt-envelope-real")
    assert out[0].payload == {"concept_id": "c-1"}  # untouched
    assert out[1].payload["after_state"]["sibling_event_id"] == "evt-envelope-real"


def test_real_sibling_ref_passed_through_untouched() -> None:
    """A sibling_event_id that is already a real ref (no placeholder) is preserved verbatim."""
    records = [_record(2, EventType.SEMANTIC_UPGRADE_DECLARATION, _sud_payload("evt-already-set"))]
    out = backfill_semantic_sibling_refs(records, envelope_event_id="evt-envelope-real")
    assert out[0].payload["after_state"]["sibling_event_id"] == "evt-already-set"


def test_no_semantic_record_is_noop() -> None:
    """No SemanticUpgradeDeclaration in the batch → backfill is a no-op (returns records as-is)."""
    records = [_record(1, EventType.CONCEPT_CREATED, {"concept_id": "c-1"})]
    out = backfill_semantic_sibling_refs(records, envelope_event_id="evt-x")
    assert out[0].payload == {"concept_id": "c-1"}


def test_original_record_not_mutated() -> None:
    """Backfill returns new records — the caller's input records are not mutated in place."""
    rec = _record(2, EventType.SEMANTIC_UPGRADE_DECLARATION, _sud_payload(_PLACEHOLDER))
    backfill_semantic_sibling_refs([rec], envelope_event_id="evt-envelope-real")
    assert rec.payload["after_state"]["sibling_event_id"] == _PLACEHOLDER  # original intact
