"""T-TRACK-01 — CapabilityBaselineIngested / CapabilityStatusAdvanced emit (path B).

# spec source: STATUS-603.md §5 "真正的修法" + LANDING-PLAN T-TRACK-01.
#
# These are path-B writes (immediately audit-visible, like DebtRegistered) — the system
# recording its own done-state, not a state change requiring gate approval. The
# capability_status projection (M-0.2) reduces these into the event-sourced 603 board, curing
# finding-phase-status-not-a-projection: the board now moves from events, not hand-reconciliation.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    CapabilityClassification,
    EnforcementLevel,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

_ACTOR = "t_track_01_capability_tracker"


def ingest_capability_baseline(
    event_log: EventLog,
    *,
    capabilities: list[dict[str, object]],
    source_ref: str,
    source_commit: str | None = None,
    actor_id: str = _ACTOR,
) -> EventRecord:
    """Emit ONE CapabilityBaselineIngested event seeding the capability_status projection.

    `capabilities` is the parsed panorama baseline: one dict per row with
    {capability_id, module, name, classification, linked_debts}. The event carries the
    source-file + git-commit provenance so the baseline is auditable as an event, not a
    re-readable markdown snapshot.
    """
    intent = EventIntent(
        local_intent_id=f"capbase-{uuid.uuid4().hex[:12]}",
        event_type=EventType.CAPABILITY_BASELINE_INGESTED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "source_ref": source_ref,
            "source_commit": source_commit,
            "ingested_count": len(capabilities),
            "capabilities": capabilities,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id="capability-board-baseline",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)


def advance_capability(
    event_log: EventLog,
    *,
    capability_id: str,
    to_classification: CapabilityClassification,
    advanced_by: str,
    evidence_ref: str,
    from_classification: CapabilityClassification | None = None,
    enforcement_level: EnforcementLevel | None = None,
    actor_id: str = _ACTOR,
) -> EventRecord:
    """Emit a CapabilityStatusAdvanced event — one capability moved classification.

    Emitted by a wave-task-landing (or a debt resolution) once the task has passed the real
    rulers: `capability_id` flips to `to_classification` in the projection, with `evidence_ref`
    (run id + audit/test reference) recording why the move is real.

    RUN-096 — `enforcement_level` is the orthogonal enforcement axis. Pass ENFORCED (with
    real-path evidence in `evidence_ref`: physical gate + TDD probe + audit/provenance artifact)
    to record that the capability's effect was proven to physically hold (not merely built+tested).
    An advance that omits it (None) does NOT downgrade an already-recorded enforcement_level —
    the projection preserves it. `to_classification` may equal current (a no-op classification
    advance whose purpose is the enforcement annotation).
    """
    intent = EventIntent(
        local_intent_id=f"capadv-{uuid.uuid4().hex[:12]}",
        event_type=EventType.CAPABILITY_STATUS_ADVANCED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "capability_id": capability_id,
            "from_classification": from_classification.value if from_classification else None,
            "to_classification": to_classification.value,
            "advanced_by": advanced_by,
            "evidence_ref": evidence_ref,
            "advanced_by_event_id": None,
            "enforcement_level": enforcement_level.value if enforcement_level else None,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=capability_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)
