"""M-0.4 Envelope engine — task-facing public assembly API over the L0 primitives.

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md
#   §2 (L31..L116)  full TransactionEnvelope schema
#   §3.3 (L144..L169) read_set / write_set / claims derivation rules
#   §5 (L230..L295)  per-task-type patches patterns (5 + obligation/consolidation)
#   §6.1 (L301..L338) Envelope → TransactionEnvelopeSubmitted EventIntent shape
#   §6.2 (L340..L342) envelope 不独立持久化 — read-back goes through the M-0.1 Read API
#
# Honest scope note (stale-premise reconciliation — the same premise already documented for
# the sibling task by ledger finding f-m04-delivered-by-c8a50d79-livefire): plan-m04-envelope-
# payoff was framed on "l0/envelope/__init__.py 空壳", but the derivation / boundary-check /
# read-back CORE already shipped (builder.py / checks.py / reader.py via RUN-027/031/038/091/094).
# This module does NOT re-implement that core. It is the task-facing PUBLIC ASSEMBLY API the
# frozen consensus named (concepts m04-envelope-build-from-model / m04-readset-writeset-
# derivation / m04-claims-boundary-check / m04-envelope-roundtrip-readback /
# m04-patches-by-task-type): the two genuinely-missing spec functions get real
# implementations here (build_envelope_intent §6.1 / patches_to_domain_intent_specs §5), and
# the already-implemented derivations are exposed through thin adapters that DELEGATE to the
# L0 primitives (no logic duplication). Where a task-facing signature diverges from the
# richer primitive, the adapter maps onto it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.envelope.builder import (
    derive_actual_write_set_from_commit,
)
from towow.l0.envelope.builder import (
    derive_read_set as _builder_derive_read_set,
)
from towow.l0.envelope.builder import (
    derive_write_set as _builder_derive_write_set,
)
from towow.l0.envelope.checks import check_claims_boundary as _check_claims_boundary_payload
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EnvelopeReadSetEntityType,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    EventCategory,
    EventType,
    PatchType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.envelope import ReadEntry, TransactionEnvelope, WriteEntry
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.schemas.envelope import PatchEntry
    from towow.schemas.event_record import EventRecord


class EnvelopeEngineError(Exception):
    """Raised when an engine operation cannot honour its contract (e.g. wrong event type)."""


# ════════════════════════════════════════════════════════════════════════════════
#  (1) build_envelope_intent — canonical TransactionEnvelope → submit EventIntent (§6.1)
# ════════════════════════════════════════════════════════════════════════════════
#  This is the genuinely-missing piece the CLI submit-path hand-rolls in ~10 places
#  (payload={envelope_id, capsule_compiled_event_id:'evt-stub-...'} 后门). §6.1 fixes the
#  exact EventIntent shape; the only reconciliation with the (stricter) ProvenanceHint schema
#  is that actor_type=agent_fork REQUIRES parent_session_id + fork_name — fields the envelope
#  itself does not carry — so they are passed in by the submit wrapper that knows the fork
#  identity (the §6.1 yaml sketch predates that provenance-boundary invariant, E.1.1 Conflict 2).


def build_envelope_intent(
    envelope: TransactionEnvelope,
    *,
    parent_session_id: str,
    fork_name: str,
    causation_event_id: str | None = None,
    correlation_id: str | None = None,
) -> EventIntent:
    """M-0.4 §6.1 — serialize a canonical TransactionEnvelope into its submit EventIntent.

    Produces the exact §6.1 shape: event_type=TransactionEnvelopeSubmitted,
    event_category=envelope, payload = the full envelope, provenance actor_type=agent_fork /
    actor_id=envelope.fork_id / task_id / run_id, base_classification=abstractable_process,
    subjects = one `affected` per write_set entity + the task as `primary`.

    parent_session_id / fork_name are required by the ProvenanceHint agent_fork boundary
    (E.1.1 Conflict 2) and supplied by the submit wrapper (they are not envelope fields).
    """
    subjects: list[Subject] = []
    seen: set[tuple[str, str, SubjectRole]] = set()

    def _add(entity_type: SubjectEntityType, entity_id: str, role: SubjectRole) -> None:
        key = (entity_type.value, entity_id, role)
        if key not in seen:
            seen.add(key)
            subjects.append(Subject(entity_type=entity_type, entity_id=entity_id, role=role))

    # §6.1: each write_set entity is an `affected` subject (write-set entity universe is a
    # string-subset of the subject universe — every value maps cleanly).
    for entry in envelope.write_set:
        _add(SubjectEntityType(entry.entity_type.value), entry.entity_id, SubjectRole.AFFECTED)
    # §6.1: the task itself is the `primary` subject.
    _add(SubjectEntityType.TASK, envelope.task_id, SubjectRole.PRIMARY)

    return EventIntent(
        local_intent_id=envelope.envelope_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload=envelope.model_dump(mode="json"),
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=envelope.fork_id,
            task_id=envelope.task_id,
            run_id=envelope.run_id,
            parent_session_id=parent_session_id,
            fork_name=fork_name,
            causation_event_id=causation_event_id,
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version=envelope.schema_version,
    )


# ════════════════════════════════════════════════════════════════════════════════
#  (2) derive_write_set — union of emitted-event writes + git file writes (§3.3)
# ════════════════════════════════════════════════════════════════════════════════
#  §3.3 write_set = git diff (code) ∪ emitted domain events. builder.derive_write_set already
#  handles the event half and derive_actual_write_set_from_commit the (real, change-typed) git
#  half; this task-facing signature unifies both into one call. When the caller only has a bare
#  list of changed paths (no commit to name-status), each becomes a FILE entry at the supplied
#  default change_type — for precise A/M/D detection use derive_actual_write_set_from_commit.

_CHANGE_STRENGTH = {
    EnvelopeWriteChangeType.CREATED: 0,
    EnvelopeWriteChangeType.MODIFIED: 1,
    EnvelopeWriteChangeType.REMOVED: 2,
}


def derive_write_set(
    domain_intents: list[EventIntent],
    git_changed_files: list[str] | None = None,
    *,
    git_change_type: EnvelopeWriteChangeType = EnvelopeWriteChangeType.MODIFIED,
) -> list[WriteEntry]:
    """M-0.4 §3.3 — derive write_set from emitted domain events ∪ git-changed files.

    Delegates the event half to builder.derive_write_set (net-effect dedup by entity). Each
    git_changed_files path becomes a FILE WriteEntry (change_type=git_change_type). When one
    entity appears on both sides the strongest change_type wins (removed > modified > created),
    matching the primitive's net-effect rule.
    """
    entries = list(_builder_derive_write_set(domain_intents))
    best: dict[tuple[EnvelopeWriteSetEntityType, str], int] = {
        (e.entity_type, e.entity_id): i for i, e in enumerate(entries)
    }
    for raw in git_changed_files or []:
        path = raw.strip()
        if not path:
            continue
        key = (EnvelopeWriteSetEntityType.FILE, path)
        if key not in best:
            best[key] = len(entries)
            entries.append(
                WriteEntry(
                    entity_type=EnvelopeWriteSetEntityType.FILE,
                    entity_id=path,
                    change_type=git_change_type,
                ),
            )
        else:
            existing = entries[best[key]]
            if _CHANGE_STRENGTH[git_change_type] > _CHANGE_STRENGTH[existing.change_type]:
                entries[best[key]] = existing.model_copy(update={"change_type": git_change_type})
    return entries


# ════════════════════════════════════════════════════════════════════════════════
#  (3) derive_read_set — capsule files + touched nodes + fallback neighborhood (§3.3)
# ════════════════════════════════════════════════════════════════════════════════
#  §3.3 read_set = capsule files_to_read ∪ actually-touched nodes ∪ (fallback) projection
#  neighborhood as the upper bound. Delegates to builder.derive_read_set (which guarantees the
#  §2 non-empty floor). touched_nodes / fallback_neighborhood are concept ids.


def derive_read_set(
    capsule_files_to_read: list[str] | None,
    touched_nodes: list[str] | None,
    fallback_neighborhood: list[str] | None,
    *,
    task_id: str = "task:unknown",
    version: str = "evt-no-capsule",
) -> list[ReadEntry]:
    """M-0.4 §3.3 — derive read_set from capsule files, touched concept nodes, and the
    fallback projection neighborhood (read-set upper bound when no real read-trace exists).

    Thin adapter over builder.derive_read_set: touched_nodes become explicitly-declared CONCEPT
    entries (kept verbatim), fallback_neighborhood is the neighborhood upper bound that only
    ADDS concepts not already named. ``version`` stamps every entry (the capsule event id when
    available). Guaranteed non-empty (§2 invariant) via the primitive's task-package floor.
    """
    declared = [
        ReadEntry(entity_type=EnvelopeReadSetEntityType.CONCEPT, entity_id=cid, version=version)
        for cid in (touched_nodes or [])
        if cid
    ]
    return _builder_derive_read_set(
        task_id=task_id,
        capsule_compiled_event_id=version,
        capsule_files=capsule_files_to_read,
        declared_read_set=declared or None,
        neighborhood_concept_ids=fallback_neighborhood,
    )


# ════════════════════════════════════════════════════════════════════════════════
#  (4) check_claims_boundary — envelope vs Planner claims → violations (§3.3 / M-0.5 §3.6)
# ════════════════════════════════════════════════════════════════════════════════
#  Task-facing pure function: TransactionEnvelope in → list[BoundaryViolation] out (empty when
#  compliant OR when claims are absent — Planner optional, §3.3 skip). Delegates the set-ops to
#  checks.check_claims_boundary (the M-0.5 semantic source) and reshapes its result.


@dataclass(frozen=True)
class BoundaryViolation:
    """One claims-boundary breach: an entity in read/write_set not covered by any claim."""

    entity_type: str
    entity_id: str
    violation_type: str  # read_set_exceeds_claims | write_set_exceeds_claims
    detail: str


def check_claims_boundary(envelope: TransactionEnvelope | dict[str, object]) -> list[BoundaryViolation]:
    """M-0.4 §3.3 / M-0.5 §3.6 — claims-boundary check over an envelope.

    Accepts a canonical TransactionEnvelope (or its payload dict). Returns the list of
    BoundaryViolation for every read_set entity not covered by a read/write claim and every
    write_set entity not covered by a write claim. Empty list = compliant, OR claims absent
    (Planner pre-declared nothing → boundary check skipped, §3.3).
    """
    payload = (
        envelope.model_dump(mode="json")
        if isinstance(envelope, TransactionEnvelope)
        else envelope
    )
    result = _check_claims_boundary_payload(payload)
    if result.passed or result.claims_absent:
        return []
    reason = result.failure_reason or "claims_boundary_exceeded"
    return [
        BoundaryViolation(
            entity_type=etype,
            entity_id=eid,
            violation_type=reason,
            detail=f"{etype}:{eid} not covered by Planner {reason}",
        )
        for (etype, eid) in result.exceeded
    ]


# ════════════════════════════════════════════════════════════════════════════════
#  (5) read_envelope_from_event — reconstruct a TransactionEnvelope from a record (§6.2)
# ════════════════════════════════════════════════════════════════════════════════
#  §6.2 envelope 不独立持久化 — it is one TransactionEnvelopeSubmitted event. reader.read_envelope
#  looks an id up in the EventLog first; this record-in variant reconstructs directly from an
#  EventRecord the caller already holds (the same canonical-schema validation).


def read_envelope_from_event(record: EventRecord) -> TransactionEnvelope:
    """M-0.4 §6.2 — reconstruct a canonical TransactionEnvelope from an event record.

    Raises EnvelopeEngineError if the record is not a TransactionEnvelopeSubmitted event or its
    payload does not satisfy the TransactionEnvelope schema (a boundary-less stub is not a real
    envelope). round-trip: build_envelope_intent(env).payload → this → an equal envelope.
    """
    if record.event_type is not EventType.TRANSACTION_ENVELOPE_SUBMITTED:
        raise EnvelopeEngineError(
            f"event {record.event_id} is {record.event_type.value}, not an envelope",
        )
    try:
        # strict=False: stored payload is JSON (enums/datetime as strings) — coerce back.
        return TransactionEnvelope.model_validate(record.payload, strict=False)
    except Exception as exc:  # pydantic ValidationError et al.
        raise EnvelopeEngineError(
            f"event {record.event_id} payload is not a canonical TransactionEnvelope: {exc}",
        ) from exc


# ════════════════════════════════════════════════════════════════════════════════
#  (6) patches_to_domain_intent_specs — route patches by patch_type (§5 / §4.3)
# ════════════════════════════════════════════════════════════════════════════════
#  §4.3: concept/task/obligation/consolidation_event patches map to domain events co-submitted
#  in the accepted batch (detail_ref → evt:<id>); code_diff/doc_change/config_change are git
#  facts (detail_ref → git:<hash>), producing NO extra domain event. This routes each patch so
#  the submit wrapper knows which patches require domain events alongside the envelope.

_GIT_PATCH_TYPES = frozenset({PatchType.CODE_DIFF, PatchType.DOC_CHANGE, PatchType.CONFIG_CHANGE})
_EVENT_PATCH_TYPES = frozenset(
    {
        PatchType.CONCEPT_EVENT,
        PatchType.TASK_EVENT,
        PatchType.OBLIGATION_EVENT,
        PatchType.CONSOLIDATION_EVENT,
    },
)


@dataclass(frozen=True)
class DomainIntentSpec:
    """Routing of one patch: whether it needs a co-submitted domain event, and its refs.

    produces_domain_event=True → the batch must carry the domain events named by
    domain_event_refs (evt:<id>). produces_domain_event=False → git-only fact (git_refs).
    """

    patch_id: str
    patch_type: PatchType
    target: str
    summary: str
    produces_domain_event: bool
    domain_event_refs: list[str] = field(default_factory=list)
    git_refs: list[str] = field(default_factory=list)
    is_supersede: bool = False
    superseded_entity_ref: str | None = None


def _parse_detail_refs(detail_ref: str | None, prefix: str) -> list[str]:
    """Collect the bare ids of comma-separated ``<prefix><id>`` tokens (drop the prefix)."""
    if not detail_ref:
        return []
    out: list[str] = []
    for token in (t.strip() for t in detail_ref.split(",")):
        if token.startswith(prefix):
            body = token[len(prefix) :]
            if body:
                out.append(body)
    return out


def patches_to_domain_intent_specs(patches: list[PatchEntry]) -> list[DomainIntentSpec]:
    """M-0.4 §5 / §4.3 — route each patch by patch_type into a domain-intent spec.

    event-typed patches (concept/task/obligation/consolidation) declare the domain events the
    batch co-submits (from evt:<id> detail_ref); git-typed patches (code_diff/doc_change/
    config_change) are git-only facts (git:<hash> detail_ref) that produce no extra event.
    """
    specs: list[DomainIntentSpec] = []
    for patch in patches:
        produces = patch.patch_type in _EVENT_PATCH_TYPES
        specs.append(
            DomainIntentSpec(
                patch_id=patch.patch_id,
                patch_type=patch.patch_type,
                target=patch.target,
                summary=patch.summary,
                produces_domain_event=produces,
                domain_event_refs=_parse_detail_refs(patch.detail_ref, "evt:") if produces else [],
                git_refs=(
                    _parse_detail_refs(patch.detail_ref, "git:")
                    if patch.patch_type in _GIT_PATCH_TYPES
                    else []
                ),
                is_supersede=patch.is_supersede,
                superseded_entity_ref=patch.superseded_entity_ref,
            ),
        )
    return specs


__all__ = [
    "BoundaryViolation",
    "DomainIntentSpec",
    "EnvelopeEngineError",
    "build_envelope_intent",
    "check_claims_boundary",
    "derive_actual_write_set_from_commit",
    "derive_read_set",
    "derive_write_set",
    "patches_to_domain_intent_specs",
    "read_envelope_from_event",
]
