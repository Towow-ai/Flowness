"""M-0.5 §3.3 DriftCheck — FreshnessDrift made real (RUN-030 第0波 ① 剩余).

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#   §3.3.1 ScopeDriftCheck — agent touched nodes outside the capsule scope
#   §3.3.2 FreshnessDriftCheck — the world changed behind the capsule's back
#   §3.7 order — DriftCheck runs after WriteConflict, before ClaimsBoundary (physical
#         short-circuit: a drift reject terminates the remaining pipeline)
#
# What is REAL here vs honest gap:
#   - FreshnessDrift (§3.3.2)  → REAL. The predicate `invalidates_capsule_context` is a pure
#     enum-lookup + index scan over committed events after as_of_projection_seq, with NO
#     capsule content needed. The relevant-subject set (spec `capsule_relevant_subjects` =
#     `touched_nodes ∪ read_set_neighborhood ∪ obligation_dependency_neighborhood`) is built by
#     freshness_relevant_subjects(): the envelope's own read_set ∪ write_set entity refs PLUS the
#     capsule's `touched_nodes` (RUN-038 L0增强 — CapsuleCompiled.payload.neighborhood_concept_ids,
#     the SAME field §3.3.1 ScopeDrift reads). Folding in touched_nodes closed a fail-open hole: an
#     invalidating event on a capsule-NEIGHBORHOOD concept the agent had access to but did not list
#     in read_set was previously MISSED (false-accept of a stale commit). The remaining two spec
#     components (read_set_neighborhood / obligation_dependency_neighborhood) need capsule INNER
#     content CapsuleCompiled does not store (M-0.1 §3.5 summary-only) → still 已登记 capsule-content
#     debt (debt-31bf949b5803, depends_on wave1-capsule-content-accessible). The set is a SUBSET of
#     the full spec set, so it can still MISS but never falsely include — documented, not silently
#     claimed complete.
#   - ScopeDrift (§3.3.1)      → REAL (RUN-038 L0增强, resolves debt-5ae5362a16b9). The capsule
#     scope is now available: M-0.3 stamps the hops-expanded neighborhood it assembled the capsule
#     over onto CapsuleCompiled.payload.neighborhood_concept_ids — that field IS the spec's
#     capsule.touched_nodes (§3.3.1 L186: "已经是模板 hops 扩展后的结果（§2.2 neighborhood
#     expansion）", §9.2 L1007 lists touched_nodes as a CapsuleCompiled.payload read). check_scope_drift
#     reads it and computes the CONCEPT-dimension set difference. Honest limit: capsule.touched_nodes
#     is concept-only by construction (a concept-graph neighborhood), so the check scopes to
#     concept-typed read_set/write_set entities; non-concept entities (task/obligation/...) are not
#     part of the capsule's concept scope and are out of this check's purview. A capsule ref that
#     does not resolve to a real CapsuleCompiled carrying the field → skip+notice (honest degradation,
#     same as FreshnessDrift's no-baseline skip) — never a vacuous reject on a stub capsule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from towow.schemas.enums import EventType

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex

# §3.3.2 default INVALIDATING_EVENT_TYPES (allowlist, extension slot). An event of one of
# these types, written after the capsule's as_of_projection_seq and touching (primary/affected
# role) an entity in the relevant-subject set, changes a semantic premise the capsule relied
# on → the capsule is stale. The list is deliberately curated to avoid false positives
# (§11.3): commit *consequences* (ObligationChecked/Violated) and process markers
# (CapsuleCompiled / EnvelopeSubmitted / NodeTouched / ObligationActivated) are NOT here.
INVALIDATING_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.OBLIGATION_CAPTURED,
        EventType.OBLIGATION_EVOLVED,
        EventType.OBLIGATION_RETIRED,
        EventType.CONCEPT_CREATED,
        EventType.CONCEPT_EDGE_ADDED,
        EventType.CONCEPT_EDGE_REMOVED,
        EventType.CONCEPT_STATE_TRANSITION,
        # ConceptGraphProposal invalidates only once its CommitAccepted has landed — committed
        # path-A events are only index-visible after their sentinel, so scanning committed
        # records satisfies that condition implicitly (an un-accepted proposal is not in the index).
        EventType.CONCEPT_GRAPH_PROPOSAL,
        EventType.AT_REFERENCE_ADDED,
        EventType.AT_REFERENCE_REMOVED,
        EventType.SEMANTIC_UPGRADE_DECLARATION,
        # NatureJudgmentCaptured invalidates only when it touches a relevant subject — the
        # intersection scan below already enforces that (we only look at events touching the
        # relevant-subject set), so it can sit in the allowlist unconditionally.
        EventType.NATURE_JUDGMENT_CAPTURED,
    }
)

# §3.3.2 — only primary/affected subjects count as "this event changed the entity".
_WRITING_ROLES = frozenset({"primary", "affected"})


@dataclass(frozen=True)
class FreshnessDriftResult:
    """Outcome of FreshnessDriftCheck (M-0.5 §3.3.2).

    drift_nodes are the relevant subjects that had ≥1 invalidating event (the nodes whose
    semantic premise changed); invalidating_event_ids are the events that caused it. Both
    feed the DriftDetected payload (drift_nodes) + the CommitRejected evidence
    (invalidating_event_ids).
    """

    passed: bool
    skipped: bool = False
    notice: str | None = None
    invalidating_event_ids: tuple[str, ...] = ()
    drift_nodes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScopeDriftResult:
    """Outcome of ScopeDriftCheck (M-0.5 §3.3.1).

    drift_nodes are the CONCEPT entities the envelope touched that fall OUTSIDE the capsule's
    declared neighborhood (capsule.touched_nodes). They feed the DriftDetected payload
    (drift_type=scope) + the CommitRejected evidence. capsule_scope is the resolved
    neighborhood (for original_touched_nodes留痕).
    """

    passed: bool
    skipped: bool = False
    notice: str | None = None
    drift_nodes: tuple[tuple[str, str], ...] = ()
    capsule_scope: tuple[str, ...] = ()


def _capsule_neighborhood_concept_ids(
    index: EventIndex,
    capsule_compiled_event_id: str,
) -> frozenset[str] | None:
    """Resolve the capsule's scope = CapsuleCompiled.payload.neighborhood_concept_ids.

    Returns None when the ref does not resolve to a real CapsuleCompiled OR that event predates
    the neighborhood_concept_ids field (backward-compat stub / pre-Cap5 capsule) — the caller
    skips (honest degradation), never vacuously rejects. An empty-but-present field is a real
    empty scope (returns frozenset()), distinct from None (field absent).
    """
    rec = index.lookup_event_id(capsule_compiled_event_id)
    if rec is None or rec.event_type is not EventType.CAPSULE_COMPILED:
        return None
    raw = rec.payload.get("neighborhood_concept_ids")
    if not isinstance(raw, list):
        return None
    return frozenset(cid for cid in raw if isinstance(cid, str) and cid)


def check_scope_drift(
    index: EventIndex,
    capsule_compiled_event_id: str,
    actual_touched: set[tuple[str, str]],
) -> ScopeDriftResult:
    """M-0.5 §3.3.1 — did the agent touch CONCEPT nodes outside the capsule's declared scope?

    Physical set difference (§3.8): the spec's capsule_scope = capsule.touched_nodes, now read
    from CapsuleCompiled.payload.neighborhood_concept_ids (M-0.3 §2.2 hops-expanded neighborhood,
    §9.2 L1007). M-0.5 does NOT re-expand (§3.3.1 L187). Since capsule.touched_nodes is concept-only
    (a concept-graph neighborhood), we compare only the concept-typed subset of actual_touched
    (read_set ∪ write_set) against it; non-concept entities are not in the capsule's concept scope
    and are not judged here. drift = touched_concepts - capsule_scope; non-empty → ScopeDrift.

    Skip+notice (not reject) when the capsule ref does not resolve / carries no scope field —
    the same honest degradation as FreshnessDrift's no-baseline skip. A boundary-less envelope
    (no concept touches at all) trivially has empty drift → passes (nothing escaped).
    """
    capsule_scope = _capsule_neighborhood_concept_ids(index, capsule_compiled_event_id)
    if capsule_scope is None:
        return ScopeDriftResult(
            passed=True,
            skipped=True,
            notice="scope_drift_skipped: no capsule scope — capsule ref does not resolve to a "
            "CapsuleCompiled carrying neighborhood_concept_ids (stub / pre-Cap5 capsule)",
        )
    touched_concepts = {eid for etype, eid in actual_touched if etype == "concept"}
    drift = sorted(touched_concepts - capsule_scope)
    if drift:
        return ScopeDriftResult(
            passed=False,
            drift_nodes=tuple(("concept", eid) for eid in drift),
            capsule_scope=tuple(sorted(capsule_scope)),
        )
    return ScopeDriftResult(passed=True, capsule_scope=tuple(sorted(capsule_scope)))


def scope_touched_from_payload(payload: dict[str, object]) -> set[tuple[str, str]]:
    """The entities ScopeDrift (§3.3.1) judges against the capsule scope: read_set ∪ write_set,
    EXCLUDING write entries whose change_type is `created`.

    Rationale (faithful §3.3.1): drift is "touching nodes outside the scope the capsule gave the
    agent". A `created` write entry mints a NEW node — it is the envelope's legitimate output, not
    a pre-existing node the agent reached outside scope (a brand-new id could never have been in
    any prior neighborhood, so flagging it would reject all node creation). read_set entries and
    modified/removed write entries DO reference pre-existing nodes the agent reached, so they are
    judged. (check_scope_drift further restricts to concept-typed entries, since capsule.touched_nodes
    is a concept-graph neighborhood.)
    """
    touched: set[tuple[str, str]] = set()
    read_entries = payload.get("read_set")
    if isinstance(read_entries, list):
        for entry in read_entries:
            if not isinstance(entry, dict):
                continue
            etype = entry.get("entity_type")
            eid = entry.get("entity_id")
            if isinstance(etype, str) and isinstance(eid, str) and etype and eid:
                touched.add((etype, eid))
    write_entries = payload.get("write_set")
    if isinstance(write_entries, list):
        for entry in write_entries:
            if not isinstance(entry, dict):
                continue
            etype = entry.get("entity_type")
            eid = entry.get("entity_id")
            if not (isinstance(etype, str) and isinstance(eid, str) and etype and eid):
                continue
            if entry.get("change_type") == "created":
                continue  # the envelope's own new output, not an out-of-scope reach
            touched.add((etype, eid))
    return touched


def _relevant_subjects_from_payload(payload: dict[str, object]) -> set[tuple[str, str]]:
    """Build the relevant-subject proxy from envelope read_set ∪ write_set entity refs.

    The spec's `capsule_relevant_subjects` also folds in read_set_neighborhood +
    obligation_dependency_neighborhood, which need capsule content not stored in the log
    (wave1 debt). The envelope's own read_set/write_set is the available proxy.
    """
    subjects: set[tuple[str, str]] = set()
    for key in ("read_set", "write_set"):
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            etype = entry.get("entity_type")
            eid = entry.get("entity_id")
            if isinstance(etype, str) and isinstance(eid, str) and etype and eid:
                subjects.add((etype, eid))
    return subjects


def freshness_relevant_subjects(
    index: EventIndex,
    capsule_compiled_event_id: str,
    payload: dict[str, object],
) -> set[tuple[str, str]]:
    """Build the FreshnessDrift relevant-subject set (M-0.5 §3.3.2 `capsule_relevant_subjects`).

    The spec set is `touched_nodes ∪ read_set_neighborhood ∪ obligation_dependency_neighborhood`.
    What is available now (RUN-038 L0增强):
      - the envelope's own read_set ∪ write_set entity refs (the original proxy), AND
      - `touched_nodes` = CapsuleCompiled.payload.neighborhood_concept_ids — the SAME hops-expanded
        capsule neighborhood §3.3.1 ScopeDrift reads (M-0.3 §2.2 / §9.2). Folding it in closes a
        fail-open hole: an invalidating event landing on a capsule-neighborhood concept the agent
        was given access to but did NOT list in its read_set was previously MISSED (false-accept of
        a stale commit). The neighborhood concepts are concept-typed by construction.
    Still NOT covered (the remaining `read_set_neighborhood` + `obligation_dependency_neighborhood`
    components need capsule inner content not on CapsuleCompiled) → debt-31bf949b5803
    (wave1-capsule-content-accessible). The proxy is therefore a SUBSET of the spec set, never a
    superset — it can still miss, but never falsely include, so no spurious rejects.

    Backward compatible: a stub capsule ref / a CapsuleCompiled with no neighborhood field resolves
    to None and the result is EXACTLY the read_set ∪ write_set proxy (same as before this增强).
    """
    subjects = _relevant_subjects_from_payload(payload)
    neighborhood = _capsule_neighborhood_concept_ids(index, capsule_compiled_event_id)
    if neighborhood is not None:
        subjects |= {("concept", cid) for cid in neighborhood}
    return subjects


def check_freshness_drift(
    index: EventIndex,
    relevant_subjects: set[tuple[str, str]],
    as_of_projection_seq: int,
) -> FreshnessDriftResult:
    """M-0.5 §3.3.2 — did an INVALIDATING event land after the capsule's baseline, touching
    (primary/affected) one of the envelope's relevant subjects?

    Physical: enum-lookup + entity-index scan. For each relevant subject we read only the
    events touching it (index.lookup_entity), then apply the three-condition predicate
    (`invalidates_capsule_context`): seq > as_of_projection_seq, event_type ∈
    INVALIDATING_EVENT_TYPES, entity in primary/affected role. Any hit → reject(freshness_drift)
    carrying the invalidating event ids + drift nodes; the gate produces a DriftDetected event
    (path B, before the rejected batch) per §3.3.2 failure behavior.

    as_of_projection_seq <= 0 means the envelope carries no projection baseline (bootstrap /
    caller not on the submit wrapper) → no freshness window to scan → skip with a notice
    (same honest degradation as WriteConflict §3.2). Empty relevant_subjects (a boundary-less
    stub envelope) → skip with a notice rather than vacuously pass.
    """
    if as_of_projection_seq <= 0:
        return FreshnessDriftResult(
            passed=True,
            skipped=True,
            notice="freshness_drift_skipped: envelope as_of_projection_seq<=0 (no projection "
            "baseline — bootstrap or caller not on submit wrapper); no freshness window",
        )
    if not relevant_subjects:
        return FreshnessDriftResult(
            passed=True,
            skipped=True,
            notice="freshness_drift_skipped: envelope has no read_set/write_set entity refs "
            "(boundary-less stub); no relevant-subject set to scan",
        )

    invalidating_ids: list[str] = []
    drift_nodes: list[tuple[str, str]] = []
    seen_event_ids: set[str] = set()
    for etype, eid in sorted(relevant_subjects):
        node_hit = False
        for rec in index.lookup_entity(etype, eid):
            if rec.sequence_number <= as_of_projection_seq:
                continue
            if rec.event_type not in INVALIDATING_EVENT_TYPES:
                continue
            if not any(
                s.entity_type.value == etype
                and s.entity_id == eid
                and s.role.value in _WRITING_ROLES
                for s in rec.subjects
            ):
                continue
            node_hit = True
            if rec.event_id not in seen_event_ids:
                seen_event_ids.add(rec.event_id)
                invalidating_ids.append(rec.event_id)
        if node_hit:
            drift_nodes.append((etype, eid))

    if invalidating_ids:
        return FreshnessDriftResult(
            passed=False,
            invalidating_event_ids=tuple(invalidating_ids),
            drift_nodes=tuple(drift_nodes),
        )
    return FreshnessDriftResult(passed=True)


__all__ = [
    "INVALIDATING_EVENT_TYPES",
    "FreshnessDriftResult",
    "ScopeDriftResult",
    "check_freshness_drift",
    "check_scope_drift",
    "freshness_relevant_subjects",
    "scope_touched_from_payload",
]
