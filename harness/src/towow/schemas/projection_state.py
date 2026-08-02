"""Projection state schemas — 10 typed graph-state + 6 UI view stubs.

# spec source:
#   03-l0-truth-source/M-0.2-projection-detailed-design.md
#     §3 (L180..L372) — 17 projection overview; 6 full schemas (§3.1-3.6); 5 brief
#       derivations (§3.7-3.11); 6 UI views (§3.12-3.17, schema defined in M-3.2 only)
#   07-process-handoffs/PHASE-D-REVERSE-CONTRIBUTION-LOG.md
#     Patch M-2.3-E: new escalation_lifecycle projection (independent, see M-2.3 §6.7)
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     Narrow Patch E: obligation_lifecycle adds canonical_state field
#
# Coverage:
#   10 typed graph projections (M-0.2 §3.1-3.6 + finding_lifecycle / detection_rule_lifecycle /
#     escalation_lifecycle (Patch M-2.3-E) / commit_history)
#   6 UI view stubs (review_inbox / dashboard / capsule_inspector / resolver_trend /
#     obligation_heatmap / drift_stream) — schema 属 M-3.2; defer concrete model to E.5.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable

from towow.schemas.enums import (
    ConceptEdgeDirection,
    ConceptEdgeType,
    DetectionRuleLifecycleState,
    EscalationLifecycleState,
    FindingDetectionMethod,
    FindingLifecycleState,
    FindingSeverity,
    ObligationCanonicalState,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SubjectEntityType,
    TaskDependencyType,
    TaskPhase,
    TaskRunOutcome,
    TaskType,
)
from towow.schemas.obligation import ScopeHint

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class EntityRef(BaseModel):
    """Generic (entity_type, entity_id) ref used across projections."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. concept_graph — M-0.2 §3.1
# ═══════════════════════════════════════════════════════════════════════════════


class ConceptNode(BaseModel):
    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: str
    saga_state: str | None = None
    created_by_stage: str = Field(min_length=1)
    created_at_event_id: str = Field(min_length=1)
    # seed_origin: True 标记"合法独立的种子来源概念"（采访 brief 种子恒 True，见
    # interview_brief.ConceptSeedCreatedPayload）。reducer (projection.py ConceptCreated 物化) 早已写
    # 该字段；此处补进 strict schema 收口 commit 8527ee0 记录的 concept_graph producer-alignment drift
    # 的 seed_origin 项，并供 M-2.1 §6.1 孤儿判定按 seed_origin 作用域过滤（种子天然不挂 task/@ref，
    # 不应被误报为 orphan）。注：同被 reducer 写的 source_brief_id 属独立债 (T-L0-09)，本批不收口，
    # 仍由 tolerant loader 当 extra 剥离。
    seed_origin: bool = False


class ConceptEdge(BaseModel):
    model_config = _STRICT

    edge_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    edge_type: ConceptEdgeType
    direction: ConceptEdgeDirection
    created_at_event_id: str = Field(min_length=1)
    is_active: bool = True


class ConceptGraphState(BaseModel):
    model_config = _STRICT

    nodes: list[ConceptNode] = Field(default_factory=list)
    edges: list[ConceptEdge] = Field(default_factory=list)
    supersede_index: dict[str, list[str]] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. task_graph — M-0.2 §3.2
# ═══════════════════════════════════════════════════════════════════════════════


class TaskNode(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    task_type: TaskType
    # T-LND-09 (INV-B2-4): 阶段 (design|implementation|review), reducer 从 TaskNodeCreated.after_state
    # 暴露; 使阶段先后可机器校验。Optional 向后兼容历史 event。
    phase: TaskPhase | None = None
    parent_task_id: str | None = None
    # plan_id threads the owning plan onto every task node — declared payload field on
    # TaskNodeCreated.after_state (M-1.3 §3.1, task_id convention "task-{plan_id}-{seq:03d}").
    # The reducer (_reduce_task_graph) writes it on every node; optional for back-compat with
    # pre-RUN-020 events that lack it. Was missing here → TaskGraphState.model_validate(reducer
    # output) crashed extra_forbidden in obligation_lifecycle_scan daemon.
    plan_id: str | None = None
    description: str
    target_artifacts: list[str] = Field(default_factory=list)
    status: str = Field(min_length=1)  # created / planned / in_progress / completed / failed / escalated
    model_tier: str | None = None
    read_set: list[EntityRef] = Field(default_factory=list)
    write_set: list[EntityRef] = Field(default_factory=list)
    package_hash: str | None = None
    is_self_contained: bool | None = None
    last_run_outcome: TaskRunOutcome | None = None
    # provenance — M-0.2 convention (cf. ConceptNode / ObligationLifecycleNode); the reducer
    # stamps ev.event_id of the originating TaskNodeCreated on every node.
    created_at_event_id: str | None = None
    # fnd-r01-9 (owner-gate dispatch guard): 红线门标记 (从 TaskNodeCreated.after_state 透传)。
    # True = 触及 owner 5 类不可逆真实世界动作 → 编排器派发层物理拦死 + 升级 owner, 永不自动 spawn。
    # owner 可经合法机制显式解除 (TaskNodeOwnerGateCleared 事件, owner-gate-clearance@v1) → 本字段翻
    # False (投影 reducer 消费该事件, 与派发层 _task_owner_gate 认"已解"一致)。无【自动】放行开关 ——
    # 解除须 owner 经 CLI + commit gate 显式发, 非 autopilot 自 emit (区别 T-GL-09 INF-003 配置开关)。
    # Optional 向后兼容历史 event (缺 = False = 普通任务)。
    requires_owner_gate: bool = False
    owner_gate_reason: str | None = None


class TaskEdge(BaseModel):
    model_config = _STRICT

    source_task_id: str = Field(min_length=1)
    target_task_id: str = Field(min_length=1)
    dependency_type: TaskDependencyType


class TaskGraphState(BaseModel):
    model_config = _STRICT

    nodes: list[TaskNode] = Field(default_factory=list)
    edges: list[TaskEdge] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  3. obligation_lifecycle — M-0.2 §3.3 + M-0.6 Narrow Patch E (canonical_state)
# ═══════════════════════════════════════════════════════════════════════════════


class ObligationLifecycleNode(BaseModel):
    """obligation_lifecycle projection entry per M-0.2 §3.3 + Narrow Patch E."""

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    lifecycle_state: ObligationFieldsLifecycleState  # event-level (M-0.1 §2.3.10 6 values)
    # T-L0-34 (波1) Patch E §3.4: lifecycle_state 的诚实语义名 — 它存的是"最近观测到的生命周期事件
    # 类型"(activated/checked/violated 是 scoped 事件不是 canonical 状态), 真 canonical 状态在
    # canonical_state。兼容保留 lifecycle_state(下游不改), 加本字段消 canonical/scoped 混淆; 两者同步。
    last_observed_lifecycle_event_type: ObligationFieldsLifecycleState | None = None
    canonical_state: ObligationCanonicalState  # Narrow Patch E: 3-state canonical (M-0.6 §3.1)
    attached_to: list[EntityRef] = Field(default_factory=list)
    scope_rule: str
    scope_type: ObligationScopeType
    scope_hint: ScopeHint | None = None  # M-0.6 §2.1 mechanical-filter assist (concept_anchors)
    severity: ObligationSeverity
    nature: ObligationNature
    definition: str
    created_at_event_id: str = Field(min_length=1)
    last_state_change_event_id: str = Field(min_length=1)
    # T-L0-33 (波1) Patch E §13.1 — 三分类最新事件 id (区分 activation/check/violation, 各指对应
    # 类型最新事件; captured 后未发生该类则 None)。
    last_activation_event_id: str | None = None
    last_check_event_id: str | None = None
    last_violation_event_id: str | None = None
    superseded_by: str | None = None  # if evolved, points to new obligation_id


class ObligationLifecycleStateProjection(BaseModel):
    model_config = _STRICT

    obligations: list[ObligationLifecycleNode] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. ownership — M-0.2 §3.4
# ═══════════════════════════════════════════════════════════════════════════════


class OwnershipEntry(BaseModel):
    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    owner_task_id: str | None = None
    write_locked: bool = False
    lock_holder_run_id: str | None = None


class OwnershipState(BaseModel):
    model_config = _STRICT

    entities: list[OwnershipEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. at_reference_graph — M-0.2 §3.5
# ═══════════════════════════════════════════════════════════════════════════════


class AtReferenceEndpoint(BaseModel):
    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    field_path: str | None = None


class AtReferenceEntry(BaseModel):
    model_config = _STRICT

    reference_id: str = Field(min_length=1)
    source: AtReferenceEndpoint
    target: AtReferenceEndpoint
    reference_type: str = Field(min_length=1)
    is_active: bool = True


class AtReferenceGraphState(BaseModel):
    model_config = _STRICT

    references: list[AtReferenceEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  6. consumer_relation — M-0.2 §3.6
# ═══════════════════════════════════════════════════════════════════════════════


class ConsumerEntry(BaseModel):
    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    consumption_type: str = Field(min_length=1)


class ConsumerRelationEntry(BaseModel):
    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    consumers: list[ConsumerEntry] = Field(default_factory=list)


class ConsumerRelationState(BaseModel):
    model_config = _STRICT

    relations: list[ConsumerRelationEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  7. finding_lifecycle — M-0.2 §3.10 (brief schema, similar to obligation_lifecycle)
# ═══════════════════════════════════════════════════════════════════════════════


class FindingLifecycleEntry(BaseModel):
    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    lifecycle_state: FindingLifecycleState
    severity: FindingSeverity
    risk_surface: str
    detection_method: FindingDetectionMethod
    created_at_event_id: str = Field(min_length=1)
    last_state_change_event_id: str = Field(min_length=1)
    related_patch_event_id: str | None = None
    related_obligation_ids: list[str] = Field(default_factory=list)


class FindingLifecycleProjection(BaseModel):
    model_config = _STRICT

    findings: list[FindingLifecycleEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  8. detection_rule_lifecycle — M-0.2 §3.11
# ═══════════════════════════════════════════════════════════════════════════════


class DetectionRuleEntry(BaseModel):
    model_config = _STRICT

    rule_id: str = Field(min_length=1)
    lifecycle_state: DetectionRuleLifecycleState
    rule_definition: str
    shadow_stats: dict[str, int] = Field(default_factory=dict)
    created_at_event_id: str = Field(min_length=1)
    last_state_change_event_id: str = Field(min_length=1)


class DetectionRuleLifecycleProjection(BaseModel):
    model_config = _STRICT

    rules: list[DetectionRuleEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  9. escalation_lifecycle — Patch M-2.3-E (M-2.3 §6.7)
# ═══════════════════════════════════════════════════════════════════════════════


class EscalationLifecycleEntry(BaseModel):  # Patch M-2.3-E
    """Patch M-2.3-E escalation_lifecycle projection entry per PHASE-D §2.2 + M-2.3 §6.7."""

    model_config = _STRICT

    escalation_id: str = Field(min_length=1)
    lifecycle_state: EscalationLifecycleState
    raised_at: str
    raised_by_skill: str = Field(min_length=1)
    escalation_kind: str = Field(min_length=1)
    # Triage 阶段
    triage_category: str | None = None
    triage_reasoning: str | None = None
    triaged_at: str | None = None
    # Resolution 阶段
    resolution: str | None = None
    nature_judgment_event_id: str | None = None
    resolved_at: str | None = None
    # Learning capture 阶段
    learning_type: str | None = None
    seed_for_event_id: str | None = None
    learning_captured_at: str | None = None
    # 关联 entity
    related_finding_ids: list[str] = Field(default_factory=list)
    related_concept_ids: list[str] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    related_obligation_ids: list[str] = Field(default_factory=list)


class EscalationLifecycleProjection(BaseModel):  # Patch M-2.3-E
    """Patch M-2.3-E new projection — reducer consumes EscalationRaised / Triaged /
    Resolved / LearningCaptured / DecisionMade."""

    model_config = _STRICT

    escalations: list[EscalationLifecycleEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  10. commit_history — plan §3 step 3 listing (not in M-0.2 §3 v1.0 spec; derived from
#       commit class events). Schema is plan-implied; treated as a brief projection.
# ═══════════════════════════════════════════════════════════════════════════════


class CommitHistoryEntry(BaseModel):
    """Single commit entry per plan §3 step 3 (commit_history reducer).

    Spec gap: M-0.2 §3 v1.0 does not enumerate commit_history schema explicitly. Plan
    requires this projection — schema derived from commit-class event payloads
    (CommitAccepted / CommitRejected) at minimum.
    """

    model_config = _STRICT

    commit_event_id: str = Field(min_length=1)
    envelope_event_id: str = Field(min_length=1)
    verdict: str = Field(min_length=1)  # CommitVerdict value
    occurred_at_event_id: str = Field(min_length=1)


class CommitHistoryProjection(BaseModel):
    model_config = _STRICT

    commits: list[CommitHistoryEntry] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  6 UI view stubs — M-0.2 §3.12-3.17, schema defined in M-3.2 (deferred to E.5)
# ═══════════════════════════════════════════════════════════════════════════════


class _UiViewStub(BaseModel):
    """Base placeholder for UI view projections — concrete schema lives in M-3.2.

    Phase E.1 records the projection identity + minimal stub fields so consumers can
    register cursors and the projection store can write empty placeholders. Detailed
    aggregation logic deferred to Phase E.5 alongside UI implementation.
    """

    model_config = _STRICT

    last_updated_event_id: str | None = None
    entry_count: int = Field(default=0, ge=0)


class ReviewInboxState(_UiViewStub):
    """Stub for review_inbox UI projection (M-0.2 §3.12; schema in M-3.2)."""


class DashboardState(_UiViewStub):
    """Stub for dashboard UI projection (M-0.2 §3.13)."""


class CapsuleInspectorState(_UiViewStub):
    """Stub for capsule_inspector UI projection (M-0.2 §3.14)."""


class ResolverTrendState(_UiViewStub):
    """Stub for resolver_trend UI projection (M-0.2 §3.15)."""


class ObligationHeatmapState(_UiViewStub):
    """Stub for obligation_heatmap UI projection (M-0.2 §3.16)."""


class DriftStreamState(_UiViewStub):
    """Stub for drift_stream UI projection (M-0.2 §3.17)."""


# ═══════════════════════════════════════════════════════════════════════════════
#  Generic schema-drift-tolerant projection loader (root-cause fix for the recurring
#  "strict load of a derived projection crashes on legacy enum / model-lag fields")
# ═══════════════════════════════════════════════════════════════════════════════

class ProjectionDriftError(ValueError):
    """Raised by load_projection_tolerant when a projection fails to load for a reason that is
    *not* benign schema drift (legacy enum value / model-lagging extra field).

    Carries the original ValidationError so callers / tests can inspect the genuine problem
    (e.g. a required field truly missing, an empty min_length string, a type mismatch). We
    deliberately do NOT swallow these — surfacing them is how a real reducer/producer bug stays
    visible instead of being masked by over-broad tolerance.
    """

    def __init__(self, projection_model: str, original: ValidationError) -> None:
        self.projection_model = projection_model
        self.original = original
        super().__init__(
            f"{projection_model}: projection load failed on non-drift error(s) "
            f"({len(original.errors())} validation error(s)); not tolerated — see .original",
        )


# pydantic ValidationError `type` tags that this loader treats as *benign schema drift* and
# surgically heals (everything else is fatal → fail-closed):
#   - "enum": a stored value is no longer a member of the current enum (schema EVOLVED — an
#     enum member was renamed/removed; the offending list item is dropped). This is the canonical
#     "old event carried an old enum value" class (e.g. SubjectEntityType dropped 'artifact').
#   - "extra_forbidden": the reducer stamps a field the strict model does not declare (model
#     LAGS the reducer, or a producer-alignment-debt projection stores a richer shape). The typed
#     consumer can't read it anyway, so the extra key is stripped on read. Drop is non-lossy for
#     the typed view.
_DRIFT_ERROR_TYPES = frozenset({"enum", "extra_forbidden"})


def _nearest_enclosing_list_index(loc: tuple[object, ...]) -> int | None:
    """Index into ``loc`` of the nearest enclosing list position (an int), scanning from the leaf.

    A pydantic error loc like ``('nodes', 26, 'write_set', 0, 'entity_type')`` means the bad enum
    sits inside the list item ``('nodes', 26, 'write_set', 0)`` — we drop that whole item, not the
    scalar, because a list element with an unparseable enum can't be partially represented.
    """
    for i in range(len(loc) - 1, -1, -1):
        if isinstance(loc[i], int):
            return i
    return None


def _walk_to_parent(root: object, path: Iterable[object]) -> object | None:
    cur = root
    for key in path:
        if isinstance(cur, Mapping):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and 0 <= key < len(cur):
            cur = cur[key]
        else:
            return None
    return cur


def _heal_drift(data: dict[str, object], err: ValidationError) -> dict[str, object]:
    """Return a copy of ``data`` with benign-drift loci removed (extra keys stripped; list items
    carrying a dead enum value dropped). Caller has already verified every error is a drift type.
    """
    healed = copy.deepcopy(data)
    drop_items_by_parent: defaultdict[tuple[object, ...], set[int]] = defaultdict(set)
    strip_keys: list[tuple[object, ...]] = []
    for er in err.errors():
        loc = er["loc"]
        if er["type"] == "extra_forbidden":
            strip_keys.append(loc)
        else:  # "enum" — drop the nearest enclosing list item
            idx_pos = _nearest_enclosing_list_index(loc)
            if idx_pos is not None:
                drop_items_by_parent[loc[: idx_pos]].add(loc[idx_pos])
    # strip extra keys first (they don't shift list indices)
    for loc in strip_keys:
        parent = _walk_to_parent(healed, loc[:-1])
        if isinstance(parent, dict):
            parent.pop(loc[-1], None)
    # drop dead-enum list items (filter by index so multiple drops in one list compose)
    for parent_path, idxs in drop_items_by_parent.items():
        parent = _walk_to_parent(healed, parent_path)
        if isinstance(parent, list):
            parent[:] = [x for i, x in enumerate(parent) if i not in idxs]
    return healed


def load_projection_tolerant[ProjModel: BaseModel](
    projection_model: type[ProjModel],
    raw: object,
) -> ProjModel:
    """Load a strict projection State model from raw projection JSON, tolerant of *benign schema
    drift* but fail-closed on every other defect — the single root-cause mechanism for the
    recurring "daemon/CLI strict-loads a derived projection and crashes on a legacy value" bug.

    Why this exists (root-cause, not whack-a-mole): projection JSON is folded from the canonical
    event log by reducers. When the schema evolves (an enum member is removed, a payload field is
    added), historical events still carry the old shape, so the materialized projection mixes old
    and new shapes. A strict ``model_validate`` of that projection then aborts the whole
    consumer (a maintenance daemon scan, a CLI command). The fix belongs on the *read* side, at
    every strict load, because:
      • reducer-side normalization would rewrite history → a full rebuild from the log would no
        longer reproduce the stored state, breaking the L0 reconstructability invariant, and would
        discard the producer's genuinely-recorded shape;
      • a versioned migration would have to rewrite the stored projection (mutating live state),
        and is heavyweight for what is fundamentally a read-time concern.

    Tolerance is deliberately narrow (honesty gate — masking a real bug is worse than crashing):
      • dead enum value (``type=="enum"``)         → drop the enclosing list item (e.g. a
        read_set/write_set ref whose entity_type is a removed enum member). The consumer that does
        not read that ref is unaffected; one that does sees the ref is gone, never a wrong value.
      • model-lagging extra field (``extra_forbidden``) → strip the key (the typed consumer cannot
        observe it regardless).
      • ANY other ValidationError (a required field truly missing, an empty min_length string, a
        type error) → re-raised as :class:`ProjectionDriftError` (fail-closed). A genuinely
        incomplete entry that a consumer needs is a real defect and must surface, not be silently
        defaulted/dropped — that would yield wrong analysis (e.g. emptying a non-empty consumer
        list would make a consumed concept look orphaned).

    First attempts a clean ``strict=False`` validate (zero-overhead happy path when the projection
    conforms). Only on failure does it inspect the errors and, *iff every one is a drift type*,
    surgically heal and re-validate. If healing still fails, the residual error is fatal.

    Note: projections whose reducer stores a structurally divergent shape with *missing required*
    fields (a tracked producer-alignment debt, e.g. consumer_relation's consumer_id/entity_id
    naming, or at_reference_graph's typed-@-locator shape) are NOT loadable here — they fail-closed
    by design. Consumers of those read them via field-level ``model_construct`` projection of the
    exact fields they need (see daemon_run_once / cli maintenance scans), which is the honest way
    to read a known-divergent projection without pretending the model matches it.
    """
    data: dict[str, object] = dict(raw) if isinstance(raw, Mapping) else {}
    try:
        return projection_model.model_validate(data, strict=False)
    except ValidationError as first_error:
        if not all(er["type"] in _DRIFT_ERROR_TYPES for er in first_error.errors()):
            raise ProjectionDriftError(projection_model.__name__, first_error) from first_error
        healed = _heal_drift(data, first_error)
        try:
            return projection_model.model_validate(healed, strict=False)
        except ValidationError as residual:
            # healing the drift exposed (or left) a genuine defect → fail-closed, don't swallow.
            raise ProjectionDriftError(projection_model.__name__, residual) from residual


__all__ = [
    "AtReferenceEndpoint",
    "AtReferenceEntry",
    "AtReferenceGraphState",
    "CapsuleInspectorState",
    "CommitHistoryEntry",
    "CommitHistoryProjection",
    "ConceptEdge",
    "ConceptGraphState",
    "ConceptNode",
    "ConsumerEntry",
    "ConsumerRelationEntry",
    "ConsumerRelationState",
    "DashboardState",
    "DetectionRuleEntry",
    "DetectionRuleLifecycleProjection",
    "DriftStreamState",
    "EntityRef",
    "EscalationLifecycleEntry",
    "EscalationLifecycleProjection",
    "FindingLifecycleEntry",
    "FindingLifecycleProjection",
    "ObligationHeatmapState",
    "ObligationLifecycleNode",
    "ObligationLifecycleStateProjection",
    "OwnershipEntry",
    "OwnershipState",
    "ProjectionDriftError",
    "ResolverTrendState",
    "ReviewInboxState",
    "TaskEdge",
    "TaskGraphState",
    "TaskNode",
    "load_projection_tolerant",
]
