"""DebtRegistered / DebtResolved emit + query (RUN-029 第0波 ③).

# spec source: RUN-029 brief-1e96066a — "每处 stub/deferral 产生即 emit 可追踪债条目"
#
# DebtRegistered stays a path-B write (immediately audit-visible, like DriftDetected) — register
# / re-register is not a closure claim, no gate needed. DebtResolved (T-REFLOW-DEBT,
# functional-equivalence-closure-criterion@v1) no longer is: resolving a debt IS a closure claim,
# so it now goes through EventLog.write_debt_resolved — a producer-only gated write requiring a
# fail-closed evidence chain (evidence_type != NS1) + an independently-verified verdict
# (actor != closer/fixer). The debt_registry projection (M-0.2) reduces both event types into the
# honest done-ledger.
#
# The owner-decided invariant (RUN-029 escalation) — whether an OPEN blocking-severity debt
# hard-blocks marking its capability done — is enforced at the M-0.5 gate (see commit_gate),
# NOT here. This module only records + queries debt; query helpers expose the open/blocking
# set the gate (and `towow status`) consume.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    DebtSeverity,
    DebtType,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.l0.event_log.index import EventIndex
    from towow.l0.projection import ProjectionStore
    from towow.l1.functional_equivalence_evidence_tier import EvidenceReasonCode
    from towow.schemas.event_record import EventRecord


def register_debt(
    event_log: EventLog,
    *,
    debt_type: DebtType,
    severity: DebtSeverity,
    title: str,
    description: str,
    against_capability: str,
    resolution_criteria: str,
    depends_on: list[str] | None = None,
    debt_id: str | None = None,
    registered_by_task: str | None = None,
    registered_by_event_id: str | None = None,
    actor_id: str = "m05_debt_registry",
) -> EventRecord:
    """Emit a DebtRegistered event (path B). Returns the written record.

    The subject points at `against_capability` (as a concept ref) so the event entity index
    links capability → its outstanding debt — the lookup the gate uses to decide whether a
    done-claiming commit against that capability is honest.
    """
    did = debt_id or f"debt-{uuid.uuid4().hex[:12]}"
    intent = EventIntent(
        local_intent_id=f"dr-{uuid.uuid4().hex[:12]}",
        event_type=EventType.DEBT_REGISTERED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "debt_id": did,
            "debt_type": debt_type.value,
            "severity": severity.value,
            "title": title,
            "description": description,
            "against_capability": against_capability,
            "depends_on": depends_on or [],
            "resolution_criteria": resolution_criteria,
            "registered_by_task": registered_by_task,
            "registered_by_event_id": registered_by_event_id,
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
                entity_id=against_capability,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)


def resolve_debt(
    event_log: EventLog,
    *,
    debt_id: str,
    resolution_evidence: str,
    against_capability: str,
    evidence_type: EvidenceReasonCode | None = None,
    evidence_ref_type: str | None = None,
    evidence_ref_id: str | None = None,
    verification_verdict_ref: str | None = None,
    agent_verdict_ref: str | None = None,
    resolved_by_session_id: str | None = None,
    resolved_by_event_id: str | None = None,
    actor_id: str = "m05_debt_registry",
) -> EventRecord:
    """Emit a DebtResolved event through the T-REFLOW-DEBT gate — the named debt is only
    genuinely paid off when its closure carries an independently-verified functional-equivalence
    evidence chain (functional-equivalence-closure-criterion@v1); merge-base ancestry alone is
    never sufficient.

    The evidence_type/evidence_ref_*/verification_verdict_ref/resolved_by_session_id parameters
    are structurally optional here (kept type-compatible with any not-yet-migrated caller — the
    CLI wiring for these is deliberately out of this task's write_set, coordinated separately at
    reflow time) but EventLog.write_debt_resolved rejects (raises) any resolve missing them —
    there is no silent bypass: an un-migrated caller fails loudly at write time, never succeeds
    quietly on bare merge-base evidence.
    """
    from towow.l0.event_log.index import EventIndex

    intent = EventIntent(
        local_intent_id=f"drs-{uuid.uuid4().hex[:12]}",
        event_type=EventType.DEBT_RESOLVED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "debt_id": debt_id,
            "resolution_evidence": resolution_evidence,
            "evidence_type": evidence_type.value if evidence_type is not None else None,
            "evidence_ref_type": evidence_ref_type,
            "evidence_ref_id": evidence_ref_id,
            "verification_verdict_ref": verification_verdict_ref,
            "agent_verdict_ref": agent_verdict_ref,
            "resolved_by_event_id": resolved_by_event_id,
        },
        provenance_hint=(
            ProvenanceHint(
                actor_type=ActorType.AGENT_SESSION.value,
                actor_id=actor_id,
                session_id=resolved_by_session_id,
                skill_id="debt-registry",
            )
            if resolved_by_session_id is not None
            else ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id=actor_id)
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=against_capability,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    index = EventIndex(event_log.all_records())
    return event_log.write_debt_resolved(intent, index)


def open_blocking_debt_ids_from_index(index: EventIndex, against_capability: str) -> list[str]:
    """Open blocking-severity debt ids against a capability, computed from the event index.

    The M-0.5 gate has the event index (not a ProjectionStore) at check time — this derives
    the same open-blocking set the gate consumes to enforce Nature's RUN-029 invariant
    (a capability with open blocking debt cannot be claimed done). DebtRegistered /
    DebtResolved both carry the capability as their primary concept subject, so a single
    entity-index lookup returns the full register/resolve history for it.
    """
    registered_blocking: dict[str, bool] = {}
    resolved: set[str] = set()
    for rec in index.lookup_entity(SubjectEntityType.CONCEPT.value, against_capability):
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        did = payload.get("debt_id")
        if not isinstance(did, str) or not did:
            continue
        if rec.event_type is EventType.DEBT_REGISTERED:
            registered_blocking[did] = payload.get("severity") == DebtSeverity.BLOCKING.value
        elif rec.event_type is EventType.DEBT_RESOLVED:
            resolved.add(did)
    return [did for did, is_blocking in registered_blocking.items() if is_blocking and did not in resolved]


def open_debts_against(store: ProjectionStore, against_capability: str) -> list[dict[str, object]]:
    """Open debt nodes registered against the given capability (from debt_registry)."""
    registry = store.read("debt_registry") or {"debts": []}
    debts = registry.get("debts", [])
    if not isinstance(debts, list):
        return []
    return [
        d
        for d in debts
        if isinstance(d, dict)
        and d.get("against_capability") == against_capability
        and d.get("state") == "open"
    ]


def open_blocking_debts(store: ProjectionStore, against_capability: str) -> list[dict[str, object]]:
    """Open debts against the capability whose severity is `blocking` — the set that, per the
    owner-decided invariant, makes a done-claim on that capability dishonest."""
    return [
        d
        for d in open_debts_against(store, against_capability)
        if d.get("severity") == DebtSeverity.BLOCKING.value
    ]


__all__ = [
    "open_blocking_debt_ids_from_index",
    "open_blocking_debts",
    "open_debts_against",
    "register_debt",
    "resolve_debt",
]
