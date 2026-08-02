"""M-0.7 §3.3/§3.4 GC reachability — semantic protection layer (deferred-gap payoff, RUN-038).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §3.1 double-filter: archivable ⟺ seq ≤ retention_watermark AND GC-unreachable
#     §3.3 GC roots (7 categories) — the precise "live entry points"
#     §3.4 gc_reachable(event_id) — BFS from roots over reference edges
#     §3.5 determine_archive_action — three-layer verdict by base_classification
#
# Why (gc.py docstring deferred note + retention_watermark §3.2 already built): the ordering
# filter (archive_seq_bound) only answers "has every sequential consumer advanced past it". The
# *semantic* filter answers "is this event still referenced by a live object" — and these are
# **independent** (§3.1). Dropping an event that passed the ordering filter but is still
# GC-reachable is a data-loss bug. This module supplies the second (semantic) filter so the
# double-filter is real, not just the ordering half.
#
# Edge model (§3.4 "supersede / provenance / source_event_refs"): the v3 EventRecord carries two
# concrete reference edges that connect an event to other events:
#   - provenance.causation_event_id  (the upstream event that caused this one)
#   - supersede.superseded_event_id  (the older event this one replaces)
# We traverse BOTH from every root. We also treat any event *referenced as* superseded_event_id
# as itself a root (§3.3 category 7 — supersede chain must stay traceable end-to-end).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from towow.schemas.enums import (
    ArchiveAction,
    BaseClassification,
    EventType,
    ObligationCanonicalState,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.event_log.lease import RetentionLeaseStore
    from towow.l0.projection.projection import ProjectionStore
    from towow.schemas.event_record import EventRecord

# §3.3 category defaults — N tunables.
DEFAULT_SNAPSHOT_WINDOW = 5  # 类别 1: 最近 N 个 SnapshotCreated (§4.2 historical retention)
DEFAULT_NATURE_WINDOW = 100  # 类别 5: 最近 N 个 NatureJudgmentCaptured (learning window)
DEFAULT_RETENTION_POLICY_WINDOW = 1  # 类别 4: 最近 N 条 RetentionPolicyChanged (最新策略链)

# projection state fields that hold a live event_id reference (§3.3 category 2).
_PROJECTION_REF_FIELDS = (
    "last_state_change_event_id",
    "last_change_event_id",
)


def _recent_by_type(
    event_log: EventLog,
    event_type: EventType,
    window: int,
) -> list[EventRecord]:
    """The most recent ``window`` records of ``event_type`` (by sequence_number, newest first)."""
    recs = event_log.get_events_by_type(event_type)
    recs.sort(key=lambda r: r.sequence_number, reverse=True)
    return recs[:window]


def _snapshot_anchor_roots(event_log: EventLog, window: int) -> tuple[set[str], int | None]:
    """§3.3 类别 1 — recent N SnapshotCreated events + the "everything after the oldest" floor.

    Returns (snapshot_event_ids, post_snapshot_floor). post_snapshot_floor is the minimum
    event_offset among the retained snapshots: every committed event with
    sequence_number > floor is reachable ("snapshot.event_offset+1 之后整段必保"). None when no
    snapshot exists.
    """
    snaps = _recent_by_type(event_log, EventType.SNAPSHOT_CREATED, window)
    ids = {s.event_id for s in snaps}
    offsets = [
        off
        for s in snaps
        if isinstance(off := s.payload.get("event_offset"), int)
    ]
    floor = min(offsets) if offsets else None
    return ids, floor


def _projection_reference_roots(projection_store: ProjectionStore) -> set[str]:
    """§3.3 类别 2 — event_ids referenced by current projection state.

    Walks every materialized projection and harvests any ``last_state_change_event_id`` /
    ``last_change_event_id`` it carries (the per-entity "where is this object's latest truth"
    pointer). ownership.entities[].lock_holder_run_id is a run_id (not an event_id) so it is not a
    reference edge here — it is covered by the active-object category via the events that set it.
    """
    refs: set[str] = set()
    bundle = projection_store.get_projection_bundle(_all_projection_names(projection_store))
    for state in bundle.values():
        if isinstance(state, dict):
            _harvest_event_ids(state, refs)
    return refs


def _all_projection_names(projection_store: ProjectionStore) -> list[str]:
    from towow.l0.projection.projection import _PROJECTION_FILES

    del projection_store  # names are static; param kept for call-site symmetry / future scoping
    return list(_PROJECTION_FILES)


def _harvest_event_ids(node: object, out: set[str]) -> None:
    """Recursively collect every value under a _PROJECTION_REF_FIELDS key that looks like evt-*."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key in _PROJECTION_REF_FIELDS and isinstance(val, str) and val.startswith("evt-"):
                out.add(val)
            else:
                _harvest_event_ids(val, out)
    elif isinstance(node, list):
        for item in node:
            _harvest_event_ids(item, out)


def _active_object_roots(event_log: EventLog, projection_store: ProjectionStore) -> set[str]:
    """§3.3 类别 3 — events backing live business objects (active obligations / unclosed findings /
    in-flight envelopes).

    An obligation whose canonical_state is "active" keeps its ObligationCaptured (and latest
    state-change) event reachable; a finding that is not yet in a terminal state keeps its
    backing event reachable. We read the projections (the materialized "what is live now") and
    map each live object back to the event_id it points at.

    A TransactionEnvelopeSubmitted whose commit has not yet landed is also a live business object —
    §3.3 names it explicitly ("in-flight envelope（M-0.5 abandoned scan 还未处理的）"). It is harvested
    from the log (not a projection) by _in_flight_envelope_roots, because an envelope is written
    path-B BEFORE its commit batch, so no projection points at it while it is in flight.
    """
    refs: set[str] = set()
    obl = projection_store.read("obligation_lifecycle_state")
    if isinstance(obl, dict):
        for node in _as_node_list(obl.get("obligations")):
            if node.get("canonical_state") == ObligationCanonicalState.ACTIVE.value:
                _add_if_event_id(node.get("last_state_change_event_id"), refs)
    finding = projection_store.read("finding_lifecycle")
    if isinstance(finding, dict):
        for node in _as_node_list(finding.get("findings")):
            if not _is_terminal_finding(node):
                _add_if_event_id(node.get("last_state_change_event_id"), refs)
    refs |= _in_flight_envelope_roots(event_log)
    return refs


def _in_flight_envelope_roots(event_log: EventLog) -> set[str]:
    """§3.3 类别 3 — in-flight envelopes (audit 0.7-b: the in-flight-envelope data-loss path).

    An envelope is in flight from the moment its TransactionEnvelopeSubmitted is written (path B,
    BEFORE the commit batch — M-0.5 §6) until a CommitAccepted / CommitRejected resolves it. The
    in-flight predicate here is exactly the one M-0.5 §7.3 scan_abandoned uses (CommitGate.
    _collect_envelope_outcomes): an envelope is resolved iff its event_id appears as the
    ``envelope_event_id`` of some CommitAccepted / CommitRejected.

    Why this must be a GC root: post_snapshot_floor = snapshot cutoff_seq = the committed head at
    snapshot time, and the floor only protects seq > cutoff. An envelope emitted *before* that head
    but still in flight sits at seq ≤ cutoff — below the floor — and, having no committed outcome,
    is referenced by no projection state. Without this root it would be GC-unreachable, so once a
    snapshot advances the retention watermark past it, it could be archived out from under the still-
    pending commit gate (the in-flight envelope the gate is about to accept / scan_abandoned is about
    to recover) — the data-loss path the audit flagged.
    """
    resolved: set[str] = set()
    for outcome_type in (EventType.COMMIT_ACCEPTED, EventType.COMMIT_REJECTED):
        for rec in event_log.get_events_by_type(outcome_type):
            eid = rec.payload.get("envelope_event_id")
            if isinstance(eid, str):
                resolved.add(eid)
    return {
        env.event_id
        for env in event_log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED)
        if env.event_id not in resolved
    }


def _is_terminal_finding(node: dict[str, object]) -> bool:
    """A finding is "closed" when its lifecycle state is a terminal one (resolved/dismissed)."""
    state = node.get("lifecycle_state") or node.get("state")
    return isinstance(state, str) and state in {"resolved", "dismissed", "closed", "fixed"}


def _protocol_level_anchor_roots(
    event_log: EventLog,
    policy_window: int,
) -> set[str]:
    """§3.3 类别 4 — bootstrap protocol-level invariants (永远在根集) + recent RetentionPolicyChanged.

    Bootstrap obligations carry payload.capture_source == "system_bootstrap" (M-0.6 §5.2); their
    ObligationCaptured events are protocol core and never collectable. Plus the most recent
    RetentionPolicyChanged events (the active-policy reference chain).
    """
    refs: set[str] = set()
    for rec in event_log.get_events_by_type(EventType.OBLIGATION_CAPTURED):
        if rec.payload.get("capture_source") == "system_bootstrap":
            refs.add(rec.event_id)
    for rec in _recent_by_type(event_log, EventType.RETENTION_POLICY_CHANGED, policy_window):
        refs.add(rec.event_id)
    return refs


def _nature_anchor_roots(event_log: EventLog, window: int) -> set[str]:
    """§3.3 类别 5 — recent N NatureJudgmentCaptured (Nature learning window, 不可丢)."""
    return {r.event_id for r in _recent_by_type(event_log, EventType.NATURE_JUDGMENT_CAPTURED, window)}


def _lease_protection_roots(
    lease_store: RetentionLeaseStore,
    event_log: EventLog,
) -> set[str]:
    """§3.3 类别 6 — every active retention lease's protected events.

    Three protection shapes (lease.py): event_ids (capsule_reconstruction) protect those events
    directly; min_seq (active_task) protects every event with seq ≥ min_seq; input_hashes
    (resolver_cache) protect the ResolverDecisionMade event found by that hash. All resolve to
    concrete event_ids that become roots — this is the semantic-protection home of input_hashes
    leases (which §3.2 explicitly keeps OUT of the ordering watermark).
    """
    refs: set[str] = set()
    seq_to_id: dict[int, str] | None = None
    for lease in lease_store.active_leases():
        if lease.event_ids:
            refs.update(lease.event_ids)
        if lease.min_seq is not None:
            if seq_to_id is None:
                seq_to_id = {r.sequence_number: r.event_id for r in event_log.all_records()}
            refs.update(eid for seq, eid in seq_to_id.items() if seq >= lease.min_seq)
        if lease.input_hashes:
            refs.update(_resolve_input_hash_events(lease.input_hashes, event_log))
    return refs


def _resolve_input_hash_events(input_hashes: Iterable[str], event_log: EventLog) -> set[str]:
    """Map each lease input_hash to the ResolverDecisionMade event_id carrying it (if present)."""
    wanted = set(input_hashes)
    found: set[str] = set()
    for rec in event_log.get_events_by_type(EventType.RESOLVER_DECISION_MADE):
        h = rec.payload.get("input_hash")
        if isinstance(h, str) and h in wanted:
            found.add(rec.event_id)
    return found


def _supersede_chain_roots(event_log: EventLog) -> set[str]:
    """§3.3 类别 7 — any event referenced AS superseded_event_id keeps the supersede chain whole.

    If event B supersedes event A, A must stay reachable so the chain B→A is traceable. (B itself
    is reachable through other roots; the rule here is specifically about the *referenced* old A.)
    """
    refs: set[str] = set()
    for rec in event_log.all_records():
        sid = rec.supersede.superseded_event_id
        if sid is not None:
            refs.add(sid)
    return refs


def _as_node_list(value: object) -> list[dict[str, object]]:
    return [n for n in value if isinstance(n, dict)] if isinstance(value, list) else []


def _add_if_event_id(value: object, out: set[str]) -> None:
    if isinstance(value, str) and value.startswith("evt-"):
        out.add(value)


def compute_gc_roots(
    event_log: EventLog,
    projection_store: ProjectionStore,
    lease_store: RetentionLeaseStore | None = None,
    *,
    snapshot_window: int = DEFAULT_SNAPSHOT_WINDOW,
    nature_window: int = DEFAULT_NATURE_WINDOW,
    policy_window: int = DEFAULT_RETENTION_POLICY_WINDOW,
) -> GcRoots:
    """M-0.7 §3.3 — the GC root set (7 categories) as concrete event_ids + the post-snapshot floor.

    Every root event_id is "live now"; from these, gc_reachable() (§3.4) expands transitively.
    Returns a GcRoots carrying the per-category breakdown (so callers / audits can see *why* an
    event is a root) plus the aggregate set and the snapshot floor.
    """
    snap_ids, post_snapshot_floor = _snapshot_anchor_roots(event_log, snapshot_window)
    return GcRoots(
        snapshot_anchors=snap_ids,
        projection_references=_projection_reference_roots(projection_store),
        active_objects=_active_object_roots(event_log, projection_store),
        protocol_level_anchors=_protocol_level_anchor_roots(event_log, policy_window),
        nature_anchors=_nature_anchor_roots(event_log, nature_window),
        lease_protection=(
            _lease_protection_roots(lease_store, event_log) if lease_store is not None else set()
        ),
        supersede_chains=_supersede_chain_roots(event_log),
        post_snapshot_floor=post_snapshot_floor,
    )


class GcRoots:
    """The 7-category GC root set (§3.3) + the post-snapshot seq floor (§3.3 category 1, 整段必保).

    ``ids`` is the union of all category event_id sets. ``post_snapshot_floor`` (when not None)
    means "every committed event with sequence_number > floor is reachable" — the floor is a
    *seq-range* root that the BFS layers in via gc_reachable().
    """

    __slots__ = (
        "active_objects",
        "lease_protection",
        "nature_anchors",
        "post_snapshot_floor",
        "projection_references",
        "protocol_level_anchors",
        "snapshot_anchors",
        "supersede_chains",
    )

    def __init__(
        self,
        *,
        snapshot_anchors: set[str],
        projection_references: set[str],
        active_objects: set[str],
        protocol_level_anchors: set[str],
        nature_anchors: set[str],
        lease_protection: set[str],
        supersede_chains: set[str],
        post_snapshot_floor: int | None,
    ) -> None:
        self.snapshot_anchors = snapshot_anchors
        self.projection_references = projection_references
        self.active_objects = active_objects
        self.protocol_level_anchors = protocol_level_anchors
        self.nature_anchors = nature_anchors
        self.lease_protection = lease_protection
        self.supersede_chains = supersede_chains
        self.post_snapshot_floor = post_snapshot_floor

    @property
    def ids(self) -> set[str]:
        """Union of all 7 category event_id sets."""
        return (
            self.snapshot_anchors
            | self.projection_references
            | self.active_objects
            | self.protocol_level_anchors
            | self.nature_anchors
            | self.lease_protection
            | self.supersede_chains
        )

    def category_of(self, event_id: str) -> list[str]:
        """Which root categories claim ``event_id`` (for audit / "why is this a root")."""
        named = (
            ("snapshot_anchors", self.snapshot_anchors),
            ("projection_references", self.projection_references),
            ("active_objects", self.active_objects),
            ("protocol_level_anchors", self.protocol_level_anchors),
            ("nature_anchors", self.nature_anchors),
            ("lease_protection", self.lease_protection),
            ("supersede_chains", self.supersede_chains),
        )
        return [name for name, ids in named if event_id in ids]


def _refs_from(rec: EventRecord) -> list[str]:
    """§3.4 get_refs_from — the downstream event_ids ``rec`` references.

    v3 EventRecord reference edges: provenance.causation_event_id (the cause) +
    supersede.superseded_event_id (the replaced event). Both keep the referenced event reachable.
    """
    refs: list[str] = []
    cause = rec.provenance.causation_event_id
    if cause is not None:
        refs.append(cause)
    sup = rec.supersede.superseded_event_id
    if sup is not None:
        refs.append(sup)
    return refs


def compute_gc_reachable(
    event_log: EventLog,
    roots: GcRoots,
) -> set[str]:
    """M-0.7 §3.4 — BFS from the root set over reference edges; return ALL reachable event_ids.

    Reachable = a root, OR referenced (transitively, via causation / supersede edges) from a
    root, OR sitting above the post-snapshot floor (every event after the oldest retained
    snapshot is kept hot per §3.3 category 1). Anything NOT in this set is GC-unreachable and is
    a candidate for collection (subject to the ordering filter, §3.1).

    Full GC (§3.4 v3 初版): each call recomputes the complete reachability closure. v3 data
    volume is small (thousands–tens-of-thousands of events) so this is bounded and simple.
    """
    by_id = {rec.event_id: rec for rec in event_log.all_records()}
    reachable: set[str] = set()
    queue: list[str] = []

    # seed with concrete root event_ids that actually exist in the log
    for rid in roots.ids:
        if rid in by_id and rid not in reachable:
            reachable.add(rid)
            queue.append(rid)
    # seed with the post-snapshot floor range root (整段必保)
    if roots.post_snapshot_floor is not None:
        for rec in by_id.values():
            if rec.sequence_number > roots.post_snapshot_floor and rec.event_id not in reachable:
                reachable.add(rec.event_id)
                queue.append(rec.event_id)

    while queue:
        # every queued id was added only when present in by_id, so the index is total here
        for ref in _refs_from(by_id[queue.pop(0)]):
            if ref not in reachable and ref in by_id:
                reachable.add(ref)
                queue.append(ref)
    return reachable


def is_gc_reachable(event_id: str, reachable: set[str]) -> bool:
    """§3.4 thin predicate — is ``event_id`` in the precomputed reachable closure."""
    return event_id in reachable


def determine_archive_action(base_classification: BaseClassification) -> ArchiveAction:
    """M-0.7 §3.5 / §4.1 — the three-layer archive verdict, keyed by base_classification.

    This is the *physical action* an event takes once the double-filter (§3.1) has cleared it for
    archiving. The three layers are an ordered, total decision over the base_classification enum:

      1. immutable_truth      → MOVE_TO_COLD     (原件入 cold; §4.2 禁 digest 替代)
      2. abstractable_process → DIGEST_AND_COLD  (产 digest + 原件入 cold, 保留 provenance)
      3. discardable_noise    → DISCARD          (直接删除, 不入 cold; §4.3 留痕)

    Distinct from RetentionAction (policy-configurable retention tier): determine_archive_action is
    the fixed §4.1 matrix that says *how* to archive once archiving is decided. The match is
    exhaustive over the 3-member BaseClassification enum — no fall-through default that could
    silently mis-route a new classification.
    """
    if base_classification is BaseClassification.IMMUTABLE_TRUTH:
        return ArchiveAction.MOVE_TO_COLD  # layer 1: never digested, original to cold
    if base_classification is BaseClassification.ABSTRACTABLE_PROCESS:
        return ArchiveAction.DIGEST_AND_COLD  # layer 2: digest allowed, original still to cold
    if base_classification is BaseClassification.DISCARDABLE_NOISE:
        return ArchiveAction.DISCARD  # layer 3: dropped (tombstone, §4.3)
    # exhaustive over the 3-member enum; assert_never makes a future 4th classification a
    # mypy error here (compile-time fail-closed) AND raises at runtime if one slips through.
    assert_never(base_classification)


__all__ = [
    "DEFAULT_NATURE_WINDOW",
    "DEFAULT_RETENTION_POLICY_WINDOW",
    "DEFAULT_SNAPSHOT_WINDOW",
    "GcRoots",
    "compute_gc_reachable",
    "compute_gc_roots",
    "determine_archive_action",
    "is_gc_reachable",
]
