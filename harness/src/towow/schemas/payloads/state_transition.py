"""state_transition category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.1 StateTransitionFields (L121..L135)
#     §3.1 (L261..L526) — 21 base event_type payloads
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#     §3.1 InvalidationCascade (L126..L161)                  # Patch M-2.1-A
#     §3.2 DaemonRunCompleted (L163..L198)                   # Patch M-2.1-A
#   05-l2-maintenance/M-2.2-maintenance-daemon-family-detailed-design.md
#     §3.1 ObligationRetireCandidate (L125..L157)            # Patch M-2.2-A
#   05-l2-maintenance/M-2.3-candidate-curation-lifecycle-detailed-design.md
#     §3.1 HistoricalPatternSurfaceCandidate (L157..L210)    # Patch M-2.3-A
#     §3.2 EscalationTriaged (L221..L270)                    # Patch M-2.3-A
#     §3.3 EscalationResolved (L279..L317)                   # Patch M-2.3-A
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#     §7.3 LockReleased event payload (L886..L913)           # Patch M-0.5a-3 / Patch D

# Total: 21 base + 6 Phase D + 1 LockReleased = 28 typed event payload schemas.
# All payloads carry target_entity_type / transition_type discriminators as Literal-typed
# fields (spec §2.3.1) — pydantic strict mode enforces equality with the canonical value.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    AdvisorConsultTrigger,
    AdvisorVerdictConfidence,
    AdvisorVerdictDecision,
    AtReferenceType,
    CascadeAffectedEntityType,
    CascadeAffectedViaSlice,
    CascadeResponseExpectation,
    ClosureResidualCheckMethod,
    ClosureRippleSyncStatus,
    ClosureVerificationMethod,
    ConceptCreatedStage,
    ConceptEdgeDirection,
    ConceptEdgeType,
    DaemonName,
    DaemonOutcome,
    EvidenceSourceType,
    FeasibilityCheckSummary,
    FixOutcome,
    InformationNeedLifecycleStatus,
    InformationNeedResolutionMethod,
    InformationNeedSource,
    LockingPolicy,
    MismatchAffectedAspect,
    MismatchDetectionTime,
    MismatchResolutionAction,
    MismatchResolutionOutcome,
    MismatchSeverity,
    ObligationRetireCandidateReason,
    PatchType,
    PlanningUncertaintyResolutionAction,
    PlanningUncertaintySeverity,
    RePlanSuggestedAction,
    RePlanTriggerSource,
    ScanMethod,
    SubjectEntityType,
    TargetEntityType,
    TaskClosureReason,
    TaskClosureRefType,
    TaskDependencyStrength,
    TaskDependencyType,
    TaskModelTier,
    TaskPhase,
    TaskRunOutcome,
    TaskType,
    TransitionType,
)

# RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation. audience 是 leaf 模块 (只依赖 pydantic+stdlib)
# → state_transition 单向 import 无环。
from towow.schemas.payloads.audience import CheckpointAudienceSeparation

# M-3.3 §14.2 narrow patch — ConceptCreated payload 加可选 origin_metadata。OriginMetadata 住
# leaf 模块 (只依赖 enums) → state_transition 单向 import 无环。
from towow.schemas.payloads.migration import OriginMetadata
from towow.schemas.payloads.task_package import (
    TaskPackageContent,
    self_contained_done_criteria_defects,
)

# Shared model config — every payload + sub-model is strict / frozen / extra forbidden.
_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ════════════════════════════════════════════════════════════════════════════════
#  Shared sub-models — common across state_transition payloads
# ════════════════════════════════════════════════════════════════════════════════


class SemanticAnnotation(BaseModel):
    """StateTransitionFields.semantic_annotation per M-0.1 §2.3.1.

    O-14 涟漪 — every state_transition event carries semantic annotation for
    M-0.2 projection reducers (concept graph edges) + capsule injection.
    """

    model_config = _STRICT

    affected_concept_ids: list[str] | None = None
    affected_edge_ids: list[str] | None = None
    relationship_type: str | None = None
    explanation: str | None = None


class EntityRef(BaseModel):
    """Generic (entity_type, entity_id) ref used in read_set / write_set / consumers / etc."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)


class AtReferenceEndpoint(BaseModel):
    """AtReferenceAdded.source_entity / target_entity per M-0.1 §3.1."""

    model_config = _STRICT

    type: str = Field(min_length=1)
    id: str = Field(min_length=1)
    field_path: str | None = None


class EvidenceItem(BaseModel):
    """evidence[] per ObligationRetireCandidate / SemanticJudgment per M-0.1 §2.3.2."""

    model_config = _STRICT

    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1)
    relevance: str


# ════════════════════════════════════════════════════════════════════════════════
#  21 base state_transition payloads — M-0.1 §3.1
# ════════════════════════════════════════════════════════════════════════════════


class ConceptCreatedAfter(BaseModel):
    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: str
    created_by_stage: ConceptCreatedStage


class ConceptCreatedPayload(BaseModel):
    """ConceptCreated payload — M-0.1 §3.1.

    M-3.3 §14.2 Patch M-3.3-2: 加可选字段 origin_metadata — 迁移工具产的 ConceptCreated 含此
    字段反向追溯到原始 v2.x 文件 (§5.1)。schema 字段扩展 (非 breaking): 既有不含 origin_metadata
    的 ConceptCreated 仍合法 (default None)。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT] = TargetEntityType.CONCEPT
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: ConceptCreatedAfter
    semantic_annotation: SemanticAnnotation | None = None
    origin_metadata: OriginMetadata | None = None  # M-3.3 §14.2 (迁移源留痕, 非迁移产物为 None)


class ConceptEdgeAddedAfter(BaseModel):
    model_config = _STRICT

    edge_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    edge_type: ConceptEdgeType
    direction: ConceptEdgeDirection


class ConceptEdgeAddedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT_EDGE] = TargetEntityType.CONCEPT_EDGE
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: ConceptEdgeAddedAfter
    semantic_annotation: SemanticAnnotation | None = None


class ConceptEdgeRemovedBefore(BaseModel):
    model_config = _STRICT

    edge_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    edge_type: ConceptEdgeType


class ConceptEdgeRemovedAfter(BaseModel):
    model_config = _STRICT

    status: Literal["removed"] = "removed"
    removal_reason: str = Field(min_length=1)


class ConceptEdgeRemovedPayload(BaseModel):
    """ConceptEdgeRemoved — top-level supersede (is_supersede=true) required on EventIntent."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT_EDGE] = TargetEntityType.CONCEPT_EDGE
    transition_type: Literal[TransitionType.REMOVED] = TransitionType.REMOVED
    before_state: ConceptEdgeRemovedBefore
    after_state: ConceptEdgeRemovedAfter
    semantic_annotation: SemanticAnnotation | None = None


class ConceptStateTransitionState(BaseModel):
    """ConceptStateTransition before/after_state — SAGA state machine saga_state is open enum (string).

    Spec §3.1 does not pin saga_state enum members — left as string for forward compat.
    """

    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    saga_state: str = Field(min_length=1)
    # §3.4 — 哪个 event 触发了本次转移 (optional; T-L1-11 CLI --trigger-event-id). Optional
    # 保持向后兼容既有最小 (concept_id+saga_state) 构造调用。
    trigger_event_id: str | None = None


class ConceptStateTransitionPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT] = TargetEntityType.CONCEPT
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    before_state: ConceptStateTransitionState
    after_state: ConceptStateTransitionState
    semantic_annotation: SemanticAnnotation | None = None


class ConceptGraphProposalChange(BaseModel):
    """ConceptGraphProposal.proposed_changes[] per M-0.1 §3.1 — change_type is open."""

    model_config = _STRICT

    change_type: str = Field(min_length=1)
    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    new_value: dict[str, object]


class ConceptGraphProposalAfter(BaseModel):
    model_config = _STRICT

    proposed_changes: list[ConceptGraphProposalChange] = Field(min_length=1)


class ConceptGraphProposalPayload(BaseModel):
    """ConceptGraphProposal — O-14 维护 Fork supersede proposal.

    target_entity_type ∈ {concept, concept_edge}; transition_type=superseded;
    supersede.is_supersede=true required at EventIntent top level.
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT, TargetEntityType.CONCEPT_EDGE]
    transition_type: Literal[TransitionType.SUPERSEDED] = TransitionType.SUPERSEDED
    after_state: ConceptGraphProposalAfter
    semantic_annotation: SemanticAnnotation | None = None


class ConceptRef(BaseModel):
    """TaskNodeCreated.after_state.concept_refs[] — M-1.3 §3.1 (T-L1-23): @ 引用锁定的概念。"""

    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    at_reference: str = Field(min_length=1)  # M-1.2 §4 Typed ID 语法
    locking_policy: LockingPolicy


class TaskNodeCreatedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    # plan_id threads the owning plan onto every task node (spec M-1.3 §3.1 — task_id
    # convention is "task-{plan_id}-{seq:03d}", and plan_id is a declared payload field).
    # RUN-020 F-019-6 fix: was missing → plan freeze blocking_checks filter
    # TaskNodeCreated by after_state.plan_id and matched nothing, making the
    # F7 fail-closed terminal gate structurally impassable. Optional for backward
    # compatibility with pre-RUN-020 events that lack it.
    plan_id: str | None = None
    task_type: TaskType
    parent_task_id: str | None = None
    description: str
    target_artifacts: list[str] = Field(default_factory=list)
    # T-L1-23 (波1) M-1.3 §3.1 — task 富字段 (缩水补全)。schema 默认空 (向后兼容历史 event +
    # 直接构造); plan-task-create CLI 强制 done_criteria/concept_refs 非空 (缺则拒)。
    done_criteria: list[str] = Field(default_factory=list)  # 可验证的完成标准
    concept_refs: list[ConceptRef] = Field(default_factory=list)  # @ 引用锁定的概念
    review_required: bool = False
    # T-LND-09 (task-phase-stage@v1 / INV-B2-4): 阶段 (design|implementation|review), 使阶段先后
    # 可表达 + 机器校验 (review_required bool 无法表达"谁先于谁")。Optional 向后兼容历史 event +
    # 直接构造; 是 T-LND-10 (review_plan 先于 implementation) 依赖边的载体。
    phase: TaskPhase | None = None
    estimated_token_budget: int | None = None
    # ─── fnd-r01-9 (owner-gate dispatch guard): 红线门标记 ──────────────────────────────
    # True = 本 task 触及 owner 的 5 类【不可逆真实世界动作】(上线 / 删生产数据 / 动钱 / 对外发布 /
    # 改公开承诺)。编排器自动派发层据此【物理拦死】(永不自动 spawn) + 升级 owner (GoalEscalationRaised
    # → main-inbound), 永远等 owner 显式决定。这是 CLAUDE.md 硬底线。
    # 🔴 **无【自动】放行开关** —— autopilot 绝不能自 emit 一个开关绕过自己的红线; 任何"运行时按
    # 关键词/severity 自动放行 owner-gate"的机制都禁止添加。但 owner 可经【合法机制显式解除】某 task
    # 的 gate: 一条 owner/主会话经 CLI (`towow plan owner-gate-clear`) 发出、过 commit gate 的
    # TaskNodeOwnerGateCleared 事件 (owner-gate-clearance@v1)。这是 owner 的显式受控决定的持久化,
    # 不是自动开关 —— 区别于 T-GL-09 的 INF-003 (那是治理 finding 自动派修的配置开关)。
    # (autopilot-owner-presence-removal, owner 2026-07-01 决策: 拆"要 owner 在场按键"的运行时判据,
    #  留 owner-gate 分类本身 + 只经 commit gate 的显式解除。owner NJ 响应【不】自动放行 —— 放行须
    #  显式发 TaskNodeOwnerGateCleared。) default False 向后兼容 (旧事件无此字段 = 普通任务, 照常
    #  自动派 —— fail-open: 红线只拦【显式标记】的 task; "该标没标"由 plan 阶段 + 冻结门兜底)。
    requires_owner_gate: bool = False
    # owner_gate_reason: requires_owner_gate=True 时升级给 owner 的问题素材 (这是哪类不可逆动作 /
    # 为什么必须停下问)。plan-task-create 在打标时强制非空。
    owner_gate_reason: str | None = None


class TaskNodeCreatedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: TaskNodeCreatedAfter
    semantic_annotation: SemanticAnnotation | None = None


class TaskDependencyEdgeAddedAfter(BaseModel):
    model_config = _STRICT

    source_task_id: str = Field(min_length=1)
    target_task_id: str = Field(min_length=1)
    dependency_type: TaskDependencyType
    # T-L1-24 (波1) M-1.3 §3.2 L118-119 — 防假依赖膨胀: 每条依赖必须带 evidence (哪个 entity 被
    # 共享读写) + strength (hard 必须等 / medium 可先做但 re-check)。
    evidence: str = Field(min_length=1)
    strength: TaskDependencyStrength


class TaskDependencyEdgeAddedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK_EDGE] = TargetEntityType.TASK_EDGE
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: TaskDependencyEdgeAddedAfter


class TaskDependencyEdgeRemovedBefore(BaseModel):
    """T-FIX-B6-01 — 被撤的边身份。task 依赖边无 edge_id, 用 (source,target,dependency_type)
    三元组定位 (与 reducer / no_circular 的边比较口径一致)。"""

    model_config = _STRICT

    source_task_id: str = Field(min_length=1)
    target_task_id: str = Field(min_length=1)
    dependency_type: TaskDependencyType


class TaskDependencyEdgeRemovedAfter(BaseModel):
    model_config = _STRICT

    status: Literal["removed"] = "removed"
    # 撤边理由 (错向 / 假依赖 / supersede)。superseded_by_edge 可选 — 若撤是为补反向边,
    # 记下补的那条边的标识 (source->target) 供审计追溯, 不强制。
    removal_reason: str = Field(min_length=1)
    superseded_by_edge: str | None = None


class TaskDependencyEdgeRemovedPayload(BaseModel):
    """T-FIX-B6-01 (PLAN-seam#1) — 撤一条 task 依赖边。event-sourced: 不物理删 ADDED 事件
    (append-only 真相源), projection reducer 把对应 active edge 标 is_active=False。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK_EDGE] = TargetEntityType.TASK_EDGE
    transition_type: Literal[TransitionType.REMOVED] = TransitionType.REMOVED
    before_state: TaskDependencyEdgeRemovedBefore
    after_state: TaskDependencyEdgeRemovedAfter


class TaskClosureSupersededBy(BaseModel):
    """TaskNodeClosed.after_state.superseded_by (done-elsewhere-task-closure@v1, T-DEC-1).

    指向真正交付了被关闭 task 的产物: 一个 commit sha (ref_type=commit) 或一个 finding id
    (ref_type=finding)。结构层 (ref 非空) 必要但不充分 —— closure-evidence-verification-gate@v1
    (T-DEC-2) 还要核验 verification_verdict_ref 语义上确认该产物覆盖被关闭 task 的 done_criteria。
    """

    model_config = _STRICT

    ref_type: TaskClosureRefType
    ref_id: str = Field(min_length=1)  # 被取代的 commit sha 或承接它的 finding id


class TaskNodeClosedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    # plan_id: 被关闭 task 所属的冻结计划 — 就绪集排除 (readyset-closure-exclusion-contract@v1)
    # 按 plan 范围派生 closed_task_ids, 故必填。
    plan_id: str = Field(min_length=1)
    reason: TaskClosureReason
    superseded_by: TaskClosureSupersededBy
    # verification_verdict_ref: 指向一个【先于本事件、由独立 actor 产出】的 verify/audit verdict。
    # done_elsewhere 关闭必填 (closure-evidence-verification-gate@v1 据它反查锚 task 的 AuditTriggered
    # + sanctioned audit-fork verdict)。retired 关闭无 delivering verdict — 证据是 premise-false finding
    # 经 verify-step 证伪, 故 verification_verdict_ref 对 retired 可空 (下面 model_validator 条件强制)。
    verification_verdict_ref: str | None = None
    # closed_by: closer 的 session_id — 独立性核验的对照锚 (gate 校验 verdict.actor != closed_by,
    # 关闭者不能自核, 复用 audit / verify-step 的独立性模式)。两种 reason 都必填。
    closed_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _enforce_reason_evidence_contract(self) -> Self:
        """按 reason 分派证据契约 (schema 层前置强制, 真核验在 closure-evidence-verification-gate@v1)。

        done_elsewhere → verification_verdict_ref 必须非空 (独立 verdict gated closure)。
        retired → superseded_by 必为 finding (premise-false FindingCreated), verdict 可空。
        """
        if self.reason is TaskClosureReason.DONE_ELSEWHERE and not (
            self.verification_verdict_ref and self.verification_verdict_ref.strip()
        ):
            raise ValueError(
                "done_elsewhere 关闭必须带 verification_verdict_ref (独立 verdict gated closure)",
            )
        if (
            self.reason is TaskClosureReason.RETIRED
            and self.superseded_by.ref_type is not TaskClosureRefType.FINDING
        ):
            raise ValueError(
                "retired 关闭的 superseded_by 必为 finding (premise-false FindingCreated),"
                " 不要 delivering commit",
            )
        return self


class TaskNodeClosedPayload(BaseModel):
    """done-elsewhere-task-closure@v1 (T-DEC-1) — 把一个其实已在别处做完的冻结计划任务诚实标记为
    终态 closed。它是 task 生命周期的一等终态 (与 TaskNodeCreated / TaskDependencyEdgeRemoved 并列
    的 task 图原语), 不是 TaskRunOutcome 的某个值, 也不是 abort (abort=没做完会重派; closure=已在
    别处做完, 终态, 永不重派)。transition_type=modified: 把该 task 的 status 改成终态 closed
    (task_graph reducer → TaskNode.status='closed')。接受门见 closure-evidence-verification-gate@v1
    (T-DEC-2); 就绪集排除见 readyset-closure-exclusion-contract@v1 (T-DEC-3)。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskNodeClosedAfter


class TaskNodeOwnerGateClearedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    # clearance_reason: owner 授权解除本 task owner-gate 的理由 (这个 task 为什么可以自动派 —— 例如
    # "经复核不触及 5 类不可逆真实世界动作, 是被过度打标的 autopilot 内部任务")。CLI 强制非空,
    # 是 owner 显式受控决定的可追溯证据 (镜像 owner_gate_reason 的非空强制)。
    clearance_reason: str = Field(min_length=1)
    # cleared_by: 解除会话的 session_id (provenance)。owner/主会话经 CLI 发, 非 autopilot 自 emit。
    cleared_by: str = Field(min_length=1)
    # plan_id: 被解 gate 的 task 所属计划 (审计/范围, 可选 —— 派发层按 task_id 判, 不依赖 plan_id)。
    plan_id: str | None = None


class TaskNodeOwnerGateClearedPayload(BaseModel):
    """owner-gate-clearance@v1 — owner 经合法机制解除某 task 的 owner-gate 的一等事件。

    背景 (autopilot-owner-presence-removal, owner 2026-07-01 决策): autopilot 自驱底座的意义是
    不需要 owner 在场按键。owner-gate (fnd-r01-9) 本身保留 —— 它拦【显式标记】触及 5 类不可逆真实
    世界动作的 task; 但"永远等 owner 在场显式决定"这一运行时在场判据被替换为"owner 事先经合法机制
    解除"。本事件就是那条合法机制: 显式 CLI (`towow plan owner-gate-clear`) 发出 + 过 commit gate,
    是 owner 显式受控决定的持久化, 不是 autopilot 可自 emit 的"自动放行开关"。

    读它的两处 (读路径认"已解 gate"): 编排器 _task_owner_gate (派发主门 + 兜底门都经它) 见此事件
    → 该 task 不再被 owner-gate 拦, 可进 ready-set 被派; task_graph 投影 reducer 把 node 的
    requires_owner_gate 翻 False (可见态与派发行为一致)。transition_type=modified: 改的是该 task
    的 owner-gate 态。canonical TaskNodeCreated.requires_owner_gate 原标记不物理改 (append-only 真相源,
    原分类保留), 本事件是叠加的显式解除。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskNodeOwnerGateClearedAfter


class TaskReadSetClaimedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    read_set: list[EntityRef] = Field(default_factory=list)


class TaskReadSetClaimedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskReadSetClaimedAfter
    semantic_annotation: SemanticAnnotation | None = None


class TaskWriteSetClaimedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    write_set: list[EntityRef] = Field(default_factory=list)


class TaskWriteSetClaimedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskWriteSetClaimedAfter
    semantic_annotation: SemanticAnnotation | None = None


class TaskModelTierAssignedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    model_tier: TaskModelTier
    assignment_reason: str
    # T-L1-26 (M-1.3 §10.6 Evidence 要求): 可解释 tier 分配 — opus_score 加权结果 + matched
    # factors + decision_rule. Optional 保向后兼容既有 inline-tier / 无因素 emit; plan freeze
    # §10.6 evidence 门要求 separate-event 分配带这些 (非默认 placeholder reason)。
    opus_score: int | None = None
    matched_opus_factors: list[dict[str, object]] = Field(default_factory=list)
    matched_sonnet_factors: list[str] = Field(default_factory=list)
    decision_rule: str = ""


class TaskModelTierAssignedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskModelTierAssignedAfter


class TaskPackagePublishedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    package_hash: str = Field(min_length=1)
    is_self_contained: bool
    # T-L1-28 (0-E-2): the zero-context self-contained package body (§3.6). Optional at the
    # schema level so pre-T-L1-28 historical TaskPackagePublished events (which carried only
    # task_id/hash/flag) still validate on replay; the publish CLI gate REQUIRES it for new
    # publishes and derives is_self_contained from validate_publication_gate over its content.
    package_content: TaskPackageContent | None = None

    @model_validator(mode="after")
    def _self_contained_requires_backed_package(self) -> Self:
        """finding-r05-planner-package-content-schema-optional — 写边界不弱于 CLI 发布门。

        is_self_contained=true 是一个 **DERIVED 裁定**: 它声称内嵌的验收合约通过了 §11.1 门1
        (done_criteria 非空 + 可机器复算)。在此之前, 强制这条不变量的只有 CLI `plan package-publish`
        里 imperative 的 validate_publication_gate; 写边界 schema 把 package_content 设成 Optional,
        于是任何绕过该 CLI、直接 EventLog.append 的 caller, 只要带 {task_id, package_hash,
        is_self_contained=true}, conforms() 就放过 —— 一个完全无 done_criteria 的空验收包能落账
        (freeze 门 build_coverage_matrix 是 plan-freeze 时才兜底, 不是事件落账瞬间的写边界拦截)。
        O-03 §7 红线 + CLAUDE.md 硬底线 '不绕 gate 直 emit'。

        这里把 §11.1 门1 的 **context-free** 结构地板 (与 CLI 发布门共用同一
        self_contained_done_criteria_defects) 提到写边界: 自包含 package 必带可机器复算的验收合约。
        门里需要 event-log/plan_id 上下文的阈值 (引用存在性 / 渐进发布 / hash 复算等) 仍留在 CLI ——
        写边界只挡掉 schema 自身能判定的那条结构地板, 不复制需要外部上下文的检查。

        不触动 replay: is_self_contained=false (被拒/非自包含 package) 与 is_self_contained 缺失的
        历史事件不受约束; 既有历史事件落盘后从不经写边界/schema 重验 (recover() 与投影都是 dict-based,
        无全量 replay 一致性 harness), 此 validator 只对**新**落账的自包含 package 生效。
        """
        if self.is_self_contained is not True:
            return self
        if self.package_content is None:
            raise ValueError(
                "is_self_contained=true 但缺 package_content (§3.6 zero-context body) — "
                "自包含 package 不能无内容 (finding-r05-planner-package-content-schema-optional)",
            )
        defects = self_contained_done_criteria_defects(self.package_content)
        if defects:
            raise ValueError(
                "is_self_contained=true 但 package_content 不满足 §11.1 门1 验收地板: "
                + "; ".join(defects),
            )
        return self


class TaskPackagePublishedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskPackagePublishedAfter


class TaskRunCompletedAfter(BaseModel):
    model_config = _STRICT

    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    outcome: TaskRunOutcome
    envelope_event_id: str | None = None
    # R01 阶段2 (O-14 涟漪信号 — affected_concept_ids 的真填充): 系统从 SIS (agent 声明的
    # --touched-node 起始影响集) 沿概念图算出的候选影响集 CIS 概念 id 集 (recall 优先, 含 SIS 种子
    # 本身)。这是 R01 诊断"整条价值链压在 affected_concept_ids 这个从没被填的信号上"的填充点 ——
    # agent 声明 SIS, 系统算 ripple, 两半接上。**只读派生** (CISComputer 沿 impact_graph 结构闭包
    # 算, 不 mutate 图、不 emit ConceptEdgeAdded; 共变边回填进 concept_graph 是 owner-gated T-R01-9,
    # 不在此)。下游 AIS (actual_impact_set._declared_concepts) 真从这里读, 算精确/召回。
    # 非 success 落地改动 (abort / 无 SIS 的退化) → None (无涟漪可算)。
    affected_concept_ids: list[str] | None = None
    # B-3 (substrate 4, K5 fencing / Kleppmann): 本会话 spawn 时认领的 fencing token (单调世代号),
    # 从 spawn env TOWOW_FENCING_TOKEN 携带回来。commit gate 资源侧校验 token ≥ 该 task 当前最高 .fence,
    # 拒"被抢锁的旧会话复活后迟到写"(时序脑裂)。**必须 optional/None 默认**: 绝大多数 producer
    # (review-conclude/fix/legacy/非 execution/未经认领的 task) 无 token → None → gate 放行 untouched
    # (None=never-claimed, 不是 stale; 把 None 当 stale 拒 = 拒所有合法写 = 灾难)。
    fencing_token: int | None = None


class TaskRunCompletedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: TaskRunCompletedAfter


class PlanningUncertaintyPayload(BaseModel):
    """T-L1-31 (M-1.3 §3.7 + §14.1.b) — 计划期不确定性 (PlanningUncertainty event).

    flat payload (同 InformationNeed* 形态), target_entity_type=concept (plan as concept, 同
    PlanFreezed 约定 — TargetEntityType 无独立 'plan' 值, PlanFreezed 也用 concept)。

    severity=red_line → 阻塞 plan freeze (check_no_unresolved_red_line_uncertainty fail-closed)
    until 解决; advisory → 记录但不阻塞。解决 = 再 emit 同 uncertainty_id 的 PlanningUncertainty
    且 resolved=true (freeze 门按 uncertainty_id latest-wins)。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT] = TargetEntityType.CONCEPT
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    uncertainty_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    description: str = Field(min_length=1)  # 不确定什么
    impact: str = Field(min_length=1)       # 影响哪些 task
    severity: PlanningUncertaintySeverity
    resolution_action: PlanningUncertaintyResolutionAction
    # 解决标记 — emit 解决时同 uncertainty_id + resolved=true (latest-wins; 见 freeze 门)。
    resolved: bool = False
    blocking_tasks: list[str] = Field(default_factory=list)  # 哪些 task 被这个不确定性阻塞


# ── M-1.1 §5.2/§3.1/§10.2 Patch R canonical InformationNeed payloads (T-L1-05/RUN-032) ──
#  M-1.1 §10.2 Patch R 把 information_need 实体 schema 从 M-0.1 早期 nested 形态
#  (after_state{priority, source_stage} + 6-混 status) refine 成 flat 三正交维形态
#  (source / status / red_line). Patch R 原话: "这不是污染 L0, 反而是把 information_need
#  这个 L1 首次真正使用的实体 schema 定准." 详 SPEC-CONFLICT-RESOLUTION-LEDGER Patch R.
#  M-1.1 是 information_need 唯一生产者 (M-1.2 不 emit; grep 实证); 0 历史事件 → 无迁移代价.
#  字段名取 §5.2 (Event Schema 清单) + §3.1 projection 同名 (source/status/red_line),
#  跟已实现的 §7.2 InformationNeedNode (info_map_build.py) 字段一致; Patch R §10.2 的
#  source_classification/lifecycle_status 是同三维的别名表述 (语义等价).


class InformationNeedCreatedPayload(BaseModel):
    """M-1.1 §5.2 InformationNeedCreated — flat (Patch R). 创建时 status 恒 pending (由
    M-0.2 reducer 默认); 解决/取代走 InformationNeedStatusChanged."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.INFORMATION_NEED] = TargetEntityType.INFORMATION_NEED
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    need_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: InformationNeedSource
    red_line: bool
    red_line_reason: str | None = None
    downstream_impact: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    session_id: str = Field(min_length=1)


class InformationNeedStatusChangedPayload(BaseModel):
    """M-1.1 §5.2 InformationNeedStatusChanged — flat (Patch R). 生命周期状态转移留痕."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.INFORMATION_NEED] = TargetEntityType.INFORMATION_NEED
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    need_id: str = Field(min_length=1)
    old_status: InformationNeedLifecycleStatus
    new_status: InformationNeedLifecycleStatus
    nature_original_quote: str | None = None
    meaning_interpretation: str | None = None
    resolution_method: InformationNeedResolutionMethod | None = None
    session_id: str = Field(min_length=1)


class AtReferenceAddedAfter(BaseModel):
    model_config = _STRICT

    reference_id: str = Field(min_length=1)
    source_entity: AtReferenceEndpoint
    target_entity: AtReferenceEndpoint
    reference_type: AtReferenceType


class AtReferenceAddedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.AT_REFERENCE] = TargetEntityType.AT_REFERENCE
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: AtReferenceAddedAfter


class AtReferenceAutoUpdatedAfter(BaseModel):
    """M-1.2 §4.3 auto_accept_latest — ref 自动跟随到 supersede 后的新 lock event。"""

    model_config = _STRICT

    reference_id: str = Field(min_length=1)
    old_lock_event_id: str = Field(min_length=1)
    new_lock_event_id: str = Field(min_length=1)
    target_concept_name: str = Field(min_length=1)
    # RUN-092 R2 件A §3.5: lock 跟随时携带新/旧锁定状态 hash (record_hash 指纹, 漂移检测)。
    # 可选 — 早期 ref 无 locked_at_state_hash, supersede 事件未必可解析 → None 合法。
    old_locked_at_state_hash: str | None = None
    locked_at_state_hash: str | None = None


class AtReferenceAutoUpdatedPayload(BaseModel):
    """M-1.2 §4.3 — AtReferenceAutoUpdated (auto_accept_latest 策略触发)。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.AT_REFERENCE] = TargetEntityType.AT_REFERENCE
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: AtReferenceAutoUpdatedAfter


class AtReferenceTargetSupersededAfter(BaseModel):
    """M-1.2 §4.3 pin_to_snapshot / explicit_decision_required — 目标 supersede 但 ref 不自动跟随。

    status: ``pinned_to_old`` (pin_to_snapshot — 锁定不变, warning) /
            ``awaiting_decision`` (explicit_decision_required — 默认, 阻塞引用方下次 commit)。
    blocks_commit_for_referer: explicit_decision_required → true (引用方须显式处理后才能 commit)。
    """

    model_config = _STRICT

    reference_id: str = Field(min_length=1)
    old_lock_event_id: str = Field(min_length=1)
    new_target_event_id: str = Field(min_length=1)
    target_concept_name: str = Field(min_length=1)
    status: Literal["pinned_to_old", "awaiting_decision"]
    blocks_commit_for_referer: bool = False
    # RUN-092 R2 件A §3.5: 锁定状态 hash (pin/explicit 不跟随 → 保持旧锁 hash) + 新 target 指纹
    # (供漂移可见)。可选 — 早期 ref 无 hash / supersede 事件未必可解析 → None 合法。
    locked_at_state_hash: str | None = None
    new_target_state_hash: str | None = None


class AtReferenceTargetSupersededPayload(BaseModel):
    """M-1.2 §4.3 — AtReferenceTargetSuperseded (pin_to_snapshot / explicit_decision_required)。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.AT_REFERENCE] = TargetEntityType.AT_REFERENCE
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: AtReferenceTargetSupersededAfter


class AtReferenceRemovedBefore(BaseModel):
    model_config = _STRICT

    reference_id: str = Field(min_length=1)
    source_entity: AtReferenceEndpoint
    target_entity: AtReferenceEndpoint


class AtReferenceRemovedPayload(BaseModel):
    """AtReferenceRemoved — supersede (is_supersede=true) required at EventIntent top level."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.AT_REFERENCE] = TargetEntityType.AT_REFERENCE
    transition_type: Literal[TransitionType.REMOVED] = TransitionType.REMOVED
    before_state: AtReferenceRemovedBefore


class PatchProposedAfter(BaseModel):
    """PatchProposed.after_state per M-1.4 §3.1 (execution producer detailed design).

    Authority note (SPEC-CONFLICT-RESOLUTION-LEDGER — Patch M-1.4-PatchProposed-shape):
    M-0.1 §3.1 carries a v1 file-level placeholder shape (file_path / diff_summary /
    change_type[add|modify|delete|rename]). M-1.4 §3.1 — the v2-reviewed producer design —
    defines the real richer shape (patch_type[6] / diff_hash / worktree_commit_sha /
    integration_commit_sha). M-1.4 §10 Patch Y only registers PatchProposed in M-0.1's
    event_type registry + adds `patch` to target_entity_type; the payload-shape authority is
    §3.1 itself. 0 historical canonical PatchProposed events (4 NodeTouched stub-rewraps only)
    → safe replacement, stays PAYLOAD_VALIDATION_ENFORCED (same pattern as T-L1-05 Patch R).
    """

    model_config = _STRICT

    patch_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    fork_session_id: str = Field(min_length=1)
    patch_type: PatchType
    content_summary: str = Field(min_length=1)  # 简短描述，不是 diff 全文
    affected_entities: list[EntityRef] = Field(default_factory=list)
    diff_hash: str = Field(min_length=1)  # 内容哈希——跟具体 commit 无关，可 portable 引用
    # v2.1 cleanup: worktree local commit (必有) vs integration main commit
    # (由 M-3.1 submit wrapper accept 后回填; M-1.4 提交时 integration_commit_sha=null)
    worktree_commit_sha: str = Field(min_length=1)
    integration_commit_sha: str | None = None


class PatchProposedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.PATCH] = TargetEntityType.PATCH
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: PatchProposedAfter
    semantic_annotation: SemanticAnnotation | None = None


# ── M-1.4 §3.2/§3.3 mismatch (RUN-038 加固(2) T-L1-38/T-L1-39, debt-0337d1ae14f7) ──────


class MismatchEvidenceRef(BaseModel):
    """MismatchDetected.evidence.detection_evidence_refs[] per M-1.4 §3.2."""

    model_config = _STRICT

    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    finding: str = Field(min_length=1)


class MismatchEvidence(BaseModel):
    """MismatchDetected.evidence per M-1.4 §3.2 — declared vs actual + discrepancy + refs."""

    model_config = _STRICT

    declared_state: dict[str, object] = Field(default_factory=dict)  # TaskPackage 期望状态
    actual_state: dict[str, object] = Field(default_factory=dict)  # 实际观察状态
    discrepancy: str = Field(min_length=1)  # 具体差异描述
    detection_evidence_refs: list[MismatchEvidenceRef] = Field(default_factory=list)


class MismatchDetectedAfter(BaseModel):
    """MismatchDetected.after_state per M-1.4 §3.2 (执行侧实际跟期望不符发生时).

    不强制分类法 — executor 用自由文本 mismatch_summary 描述; affected_aspect/
    severity_judgment 是 executor 智能自判 (§3.2 注: unknown 用 other; critical 仅给 plan
    基础假设破坏)。target_entity_type=mismatch (RUN-038 narrow patch 加 M-0.1 entity type)。
    """

    model_config = _STRICT

    mismatch_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    fork_session_id: str = Field(min_length=1)
    detection_time: MismatchDetectionTime
    mismatch_summary: str = Field(min_length=1)
    affected_aspect: MismatchAffectedAspect
    severity_judgment: MismatchSeverity
    evidence: MismatchEvidence


class MismatchDetectedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MISMATCH] = TargetEntityType.MISMATCH
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: MismatchDetectedAfter


class MismatchResolutionDecidedAfter(BaseModel):
    """MismatchResolutionDecided.after_state per M-1.4 §3.3 (v2.1 改名+合并).

    mismatch 处理路径: advisor (advisor_consult_id + advisor_verdict_event_id) 或 executor
    自决 (executor_rationale)。follow-up event (finding_id / replan_event_id) 视 action 而填。
    outcome = task 继续还是 abort。给执行侧 no_unhandled_mismatch 门提供合法出口 (T-L1-39)。
    """

    model_config = _STRICT

    mismatch_id: str = Field(min_length=1)
    resolution_action: MismatchResolutionAction
    advisor_consult_id: str | None = None
    advisor_verdict_event_id: str | None = None
    executor_rationale: str | None = None
    finding_id: str | None = None
    replan_event_id: str | None = None
    outcome: MismatchResolutionOutcome


class MismatchResolutionDecidedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MISMATCH] = TargetEntityType.MISMATCH
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: MismatchResolutionDecidedAfter


# ── M-1.4 §3.5 advisor-consult (RUN-038 波2 T-L1-40 / T-L1-49) ──────────────────


class AdvisorConsultRequestedAfter(BaseModel):
    """AdvisorConsultRequested.after_state per M-1.4 §3.5 (RUN-038 波2 T-L1-40).

    executor 发起 advisor 咨询时记录。可选字段视 triggered_by 而填:
    mismatch_id (triggered_by=mismatch); previous_verdict_event_id / new_evidence_summary
    (triggered_by=verdict_evidence_scope_breach — verdict 的 evidence scope 被新证据 breach)。
    """

    model_config = _STRICT

    consult_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    triggered_by: AdvisorConsultTrigger
    question: str = Field(min_length=1)
    executor_tentative_answer: str
    executor_uncertainty: str
    mismatch_id: str | None = None
    previous_verdict_event_id: str | None = None
    new_evidence_summary: str | None = None


class AdvisorConsultRequestedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ADVISOR_CONSULT] = TargetEntityType.ADVISOR_CONSULT
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: AdvisorConsultRequestedAfter


class AdvisorVerdictDeliveredAfter(BaseModel):
    """AdvisorVerdictDelivered.after_state per M-1.4 §3.5 (RUN-038 波2 T-L1-49).

    advisor 决策者 (独立 OPUS fork) 给的裁决。v2.1 独立化: 不再跟已删 MismatchVerdict 共用 schema。
    rationale + evidence_scope_summary 均为必填 (v2.1 evidence scope binding — executor 实施时
    如遇 evidence_scope 外的新证据, 必须重新 consult)。custom_action 时必带 specific_steps。
    """

    model_config = _STRICT

    consult_id: str = Field(min_length=1)
    advisor_session_id: str = Field(min_length=1)
    decision: AdvisorVerdictDecision
    rationale: str = Field(min_length=1)
    specific_steps: list[str] = Field(default_factory=list)
    confidence: AdvisorVerdictConfidence
    evidence_scope_summary: str = Field(min_length=1)
    superseded_by_event_id: str | None = None

    @model_validator(mode="after")
    def _custom_action_requires_steps(self) -> Self:
        """M-1.4 §3.5: decision=custom_action 时必带 specific_steps (否则 verdict 空心)."""
        if self.decision is AdvisorVerdictDecision.CUSTOM_ACTION and not self.specific_steps:
            msg = "decision=custom_action requires non-empty specific_steps (M-1.4 §3.5)"
            raise ValueError(msg)
        return self


class AdvisorVerdictDeliveredPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ADVISOR_CONSULT] = TargetEntityType.ADVISOR_CONSULT
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: AdvisorVerdictDeliveredAfter


# ── RePlanTriggered (RUN-038 波3 T-L1-42 M-1.4 产 / T-L1-71 M-1.6 fix 产) ────────
# M-1.3 §14.2 是 schema 定稿 owner (target_entity_type=plan); M-1.4 §3.8 + M-1.6 §3.4 是产生方。
# payload-shape 统一裁定见 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 12: 以 M-1.3 §14.2 为
# 权威 base shape, 并入 M-1.6 §3.4 的 task_id / trigger_evidence (fix 越 scope 时填) 为可选扩展。


class RePlanEvidenceRef(BaseModel):
    """RePlanTriggered.evidence_refs[] per M-1.3 §14.2 — re-plan 决策的投资证据."""

    model_config = _STRICT

    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    finding: str = Field(min_length=1)


class RePlanFixScopeEvidence(BaseModel):
    """RePlanTriggered.trigger_evidence per M-1.6 §3.4 — fix 越 scope 时的具体证据 (T-L1-71).

    仅 trigger_source=fix_scope_violation 时填 (model_validator 不硬绑, executor/fix 侧据 source 给)。
    """

    model_config = _STRICT

    fix_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    actual_scope_required: list[str] = Field(default_factory=list)
    declared_scope: list[str] = Field(default_factory=list)
    scope_violation_detail: str = Field(min_length=1)


class RePlanTriggeredAfter(BaseModel):
    """RePlanTriggered.after_state per M-1.3 §14.2 (schema 定稿 owner) + M-1.6 §3.4 fix 扩展.

    M-1.3 §14.2 base: plan_id / trigger_source / trigger_event_id / affected_tasks /
    suggested_action / evidence_refs。M-1.6 §3.4 fix-侧加可选 task_id (越 scope 的具体 task) +
    trigger_evidence (fix scope violation 证据)。

    target_entity_type=concept (plan-as-concept, 同 PlanningUncertainty / PlanFreezed 约定 —
    M-1.3 §14.2 文本写 'plan' 但 TargetEntityType 无 plan 成员, 既有 M-1.3 plan-event 统一用
    concept; 见 LEDGER Conflict 12)。

    trigger_event_id 是投资证据 (M-1.3 §14.2 注 '触发 event 的 id') — 非空, 让 F-14 可回溯触发源。
    """

    model_config = _STRICT

    plan_id: str = Field(min_length=1)
    trigger_source: RePlanTriggerSource
    trigger_event_id: str = Field(min_length=1)
    affected_tasks: list[str] = Field(default_factory=list)
    suggested_action: RePlanSuggestedAction
    evidence_refs: list[RePlanEvidenceRef] = Field(default_factory=list)
    # M-1.6 §3.4 fix-侧可选扩展 (T-L1-71) — fix_scope_violation 时填具体越界 task + 证据。
    task_id: str | None = None
    trigger_evidence: RePlanFixScopeEvidence | None = None


class RePlanTriggeredPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONCEPT] = TargetEntityType.CONCEPT
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: RePlanTriggeredAfter


class FixProposedAfter(BaseModel):
    model_config = _STRICT

    fix_id: str = Field(min_length=1)
    related_finding_id: str = Field(min_length=1)
    patch_id: str = Field(min_length=1)
    fix_approach: str
    # T-L1-67 (波1) M-1.6 §3.1 L145-153 富字段 — fix 产物可追溯 (patch 类型 + 改动 sha +
    # fork worktree commit); integration_commit_sha 由 M-3.1 wrapper accept 后回填 (可选)。
    patch_type: PatchType
    affected_entities: list[EntityRef] = Field(default_factory=list)
    diff_hash: str = Field(min_length=1)
    worktree_commit_sha: str = Field(min_length=1)
    integration_commit_sha: str | None = None


class FixProposedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.PATCH] = TargetEntityType.PATCH
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: FixProposedAfter


# ── T-L1-68 (波2) M-1.6 §3.2 self_verification / semantic_upgrade_declaration 结构化 sub-model ──
# 波1 (d795211) 只把它们放成 dict[str, object] (弱, gate 收任何形状); 波2 结构化让 gate 真校验内层
# shape (criteria/ripple/residual 形状对; 偷塞未知字段拒)。全 Optional → 旧最小 payload 重放安全。


class FixSelfVerificationCriterion(BaseModel):
    """FixCompleted.self_verification.criteria_results[] per M-1.6 §3.2 — 逐条 closure_criteria 自验."""

    model_config = _STRICT

    criterion: str = Field(min_length=1)
    passed: bool
    verification_method: ClosureVerificationMethod
    actual_result: str
    expected_result: str
    evidence_artifact: str  # command output / file path / test report


class FixSelfVerificationRipple(BaseModel):
    """FixCompleted.self_verification.ripple_results[] per M-1.6 §3.2."""

    model_config = _STRICT

    target_artifact: str = Field(min_length=1)
    target_location: str
    sync_status: ClosureRippleSyncStatus
    diff_summary: str = ""
    not_applicable_evidence: str | None = None  # not_applicable 时必填 (model_validator 强制)

    @model_validator(mode="after")
    def _na_needs_evidence(self) -> Self:
        if (
            self.sync_status is ClosureRippleSyncStatus.NOT_APPLICABLE
            and not (self.not_applicable_evidence and self.not_applicable_evidence.strip())
        ):
            msg = "ripple sync_status=not_applicable 须带 not_applicable_evidence (M-1.6 §3.2)"
            raise ValueError(msg)
        return self


class FixSelfVerificationResidual(BaseModel):
    """FixCompleted.self_verification.residual_check_results[] per M-1.6 §3.2 — 应 found_occurrences=0."""

    model_config = _STRICT

    pattern: str = Field(min_length=1)
    found_occurrences: int = Field(ge=0)
    check_method: ClosureResidualCheckMethod
    command_evidence: str  # 实际跑的命令 + output


class FixSelfVerification(BaseModel):
    """FixCompleted.self_verification per M-1.6 §3.2 — 双层 verification 的内层 (M-1.6 self-verified)."""

    model_config = _STRICT

    criteria_results: list[FixSelfVerificationCriterion] = Field(default_factory=list)
    ripple_results: list[FixSelfVerificationRipple] = Field(default_factory=list)
    residual_check_results: list[FixSelfVerificationResidual] = Field(default_factory=list)


class FixConceptStateChange(BaseModel):
    """FixCompleted.semantic_upgrade_declaration.concept_state_changes[] per M-1.6 §3.2 (O-14)."""

    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)


class FixSemanticUpgradeDeclaration(BaseModel):
    """FixCompleted.semantic_upgrade_declaration per M-1.6 §3.2 (O-14) — hint, cascade_scope provisional.

    Patch A: 这些是 hint (FixCompleted 的 cascade_scope 是 provisional); broader cascade (O-11 /
    F-04e / downstream) 权在 FindingResolved(closure_state=closed)。
    """

    model_config = _STRICT

    affected_concepts: list[str] = Field(default_factory=list)
    concept_state_changes: list[FixConceptStateChange] = Field(default_factory=list)
    affected_consumers_hint: list[str] | None = None
    affected_ripple_artifacts: list[str] = Field(default_factory=list)


class FixAttemptNovelty(BaseModel):
    """FixCompleted.fix_attempt_novelty per M-1.6 §3.2 — 多轮 fix 时本轮相对上轮 novelty (T-L1-77)."""

    model_config = _STRICT

    semantic_delta: str = Field(min_length=1)
    new_evidence: str | None = None
    changed_strategy: str | None = None


class FixIndependentSelfCheckAttestation(BaseModel):
    """T-RMD-S2-REVFIX (finding f-glob-review-fix-no-proof-of-work / M16-F3) — 独立 fix-self-check fork
    复核的 canonical 审计痕 (系统自己的 verify-the-verifier 留痕)。

    M16-F3 病灶: 此前 fork attestation 被拼进 5 项 blocking_check 的 evidence 字符串, 而 envelope builder
    的 _coerce_self_check 把非 dict 的 str evidence 砸成 structural floor ({check_id}) → attestation 证据
    被丢, 系统证明不了独立复核真跑过。修法 = attestation 升 FixCompleted.after_state 一等字段 (after_state
    不经 self_check coerce, 整体穿过 commit-gate stamping 存活)。

    fork 模式: mode=fork + independent_fork=True + fork_spawned/fork_model/verdict_passed/checks_attested。
    inline 降级: mode=inline + independent_fork=False + note (诚实记账"独立验跳过", 不冒充独立)。
    """

    model_config = _STRICT

    mode: str = Field(min_length=1)  # "fork" | "inline"
    independent_fork: bool
    fork_spawned: bool
    verdict_passed: bool | None = None
    fork_skill_id: str | None = None
    fork_model: str | None = None
    checks_attested: list[str] = Field(default_factory=list)
    note: str | None = None
    spec: str = Field(min_length=1)


class FixCompletedAfter(BaseModel):
    model_config = _STRICT

    fix_id: str = Field(min_length=1)
    outcome: FixOutcome
    # T-L1-68 M-1.6 §3.2 富字段 — cascade_scope 永远 provisional (verified closure 权在
    # M-1.5 FindingResolved); fix_attempt_no/novelty 给 T-L1-77 多轮 novelty gate; self_verification
    # /semantic_upgrade_declaration(O-14)/feasibility_check_summary 是 hint (M-1.5 verification 用)。
    cascade_scope: Literal["provisional"] = "provisional"  # 永远 provisional (gate 锁死, 防自报 verified)
    # T-RMD-S2-REVFIX (M16-F3): 独立 fix-self-check fork 的 attestation 一等字段 (canonical 审计痕);
    # resolved 才有独立复核 → 非 None, 其余 outcome = None。
    independent_self_check_attestation: FixIndependentSelfCheckAttestation | None = None
    fix_attempt_no: int = Field(default=1, ge=1)
    # 波2 结构化: 接受结构化对象, 也容旧自由字符串 (旧 --fix-attempt-novelty/--feasibility-summary 直传)。
    fix_attempt_novelty: FixAttemptNovelty | str | None = None
    self_verification: FixSelfVerification | None = None
    semantic_upgrade_declaration: FixSemanticUpgradeDeclaration | None = None
    # §3.2 enum feasibility_check_summary; 保留旧自由字符串 feasibility_summary 字段向后兼容。
    feasibility_check_summary: FeasibilityCheckSummary | None = None
    feasibility_summary: str | None = None


class FixCompletedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.PATCH] = TargetEntityType.PATCH
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: FixCompletedAfter


class ConsumerRef(BaseModel):
    """ConsumerListPublished.after_state.consumers[] per M-0.1 §3.1."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    consumption_type: str = Field(min_length=1)


class ConsumerListPublishedAfter(BaseModel):
    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    consumers: list[ConsumerRef] = Field(default_factory=list)
    # M-1.2 §3.6 — 区分写入方: M-1.2 工程共识初次扫 (initial_scan) vs M-2.1 变更影响识别 skill
    # 推送的更新 (update_from_change_impact)。M-1.2 与 M-2.1 共用此 event_type, scan_method +
    # provenance.actor_id 区分写入方 (不新增 ConsumerListUpdated 避免膨胀)。default=initial_scan:
    # 既有 ConsumerListPublished 事件皆 M-1.2 共识初次扫产出, 向后兼容重放 (no break)。
    scan_method: ScanMethod = ScanMethod.INITIAL_SCAN
    # §3.6 scan_source_event_id? — 触发本次 publish 的 event (M-2.1 update 时 = ChangeImpactDetected
    # / InvalidationCascade event_id; initial_scan 时为 None)。
    scan_source_event_id: str | None = None


class ConsumerListPublishedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.CONSUMER_LIST] = TargetEntityType.CONSUMER_LIST
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: ConsumerListPublishedAfter


class EngineeringConsensusFreezedAfter(BaseModel):
    model_config = _STRICT

    plan_id: str = Field(min_length=1)
    concept_graph_snapshot_hash: str = Field(min_length=1)
    freezed_at_event_offset: int = Field(ge=0)


class EngineeringConsensusFreezedPayload(BaseModel):
    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ENGINEERING_CONSENSUS] = (
        TargetEntityType.ENGINEERING_CONSENSUS
    )
    transition_type: Literal[TransitionType.FROZEN] = TransitionType.FROZEN
    after_state: EngineeringConsensusFreezedAfter
    # RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation (M-1.2 consensus freeze checkpoint)。
    # Optional 向后兼容 (既有 EngineeringConsensusFreezed 事件无此字段仍重放); 一旦提供, schema 层
    # enforce 受众分离 (hash 完整性 + style-profile-v2 + owner-facing 无内部术语泄漏)。把 concept
    # graph schema 砸给 Nature 的 owner-facing-render-defect 由此根治。
    audience: CheckpointAudienceSeparation | None = None


# ════════════════════════════════════════════════════════════════════════════════
#  Phase D Patch M-2.1-A — InvalidationCascade + DaemonRunCompleted
# ════════════════════════════════════════════════════════════════════════════════


class CascadeAffectedEntity(BaseModel):
    """InvalidationCascade.after_state.affected_entities[] per M-2.1 §3.1."""

    model_config = _STRICT

    entity_type: CascadeAffectedEntityType
    entity_id: str = Field(min_length=1)
    relationship_path: list[str] = Field(default_factory=list)
    affected_via_slice: CascadeAffectedViaSlice


class CascadeResponseExpectationItem(BaseModel):
    """InvalidationCascade.after_state.response_expectation[] per M-2.1 §3.1."""

    model_config = _STRICT

    entity_type: CascadeAffectedEntityType
    suggested_response: CascadeResponseExpectation


class InvalidationCascadeAfter(BaseModel):
    """Patch M-2.1-A InvalidationCascade.after_state — cascade_depth capped at 5."""

    model_config = _STRICT

    cascade_id: str = Field(min_length=1)
    triggered_by_event_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    cascade_depth: int = Field(ge=0, le=5)
    affected_entities: list[CascadeAffectedEntity] = Field(min_length=1)
    response_expectation: list[CascadeResponseExpectationItem] = Field(default_factory=list)


class InvalidationCascadePayload(BaseModel):  # Patch M-2.1-A
    """Patch M-2.1-A InvalidationCascade payload."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.INVALIDATION_CASCADE] = (
        TargetEntityType.INVALIDATION_CASCADE
    )
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: InvalidationCascadeAfter
    semantic_annotation: SemanticAnnotation | None = None


class DaemonRunCompletedAfter(BaseModel):
    """Patch M-2.1-A DaemonRunCompleted.after_state."""

    model_config = _STRICT

    daemon_run_id: str = Field(min_length=1)
    daemon_name: DaemonName
    scanned_count: int = Field(ge=0)
    findings_produced: int = Field(ge=0)
    cascade_events_produced: int | None = Field(default=None, ge=0)
    run_duration_ms: int = Field(ge=0)
    outcome: DaemonOutcome
    partial_failure_reason: str | None = None


class DaemonRunCompletedPayload(BaseModel):  # Patch M-2.1-A
    """Patch M-2.1-A DaemonRunCompleted payload — shared across L2 daemons."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.DAEMON_RUN] = TargetEntityType.DAEMON_RUN
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: DaemonRunCompletedAfter


class OrchestratorDispatchFailedPayload(BaseModel):  # T-L3kc-04 (波1)
    """M-3.1 §7.6 — orchestrator spawn retry 耗尽后的失败审计事件 (canonical 化, T-L3kc-04)。

    重试行为早已真 (有界 MAX_RETRIES + backoff + 耗尽 emit, l2/orchestrator.py)。此前 emit 走
    NodeTouched 假名 (kind=OrchestratorDispatchFailed)。注册此 payload 后 emit 成真 canonical 事件:
    done_criteria = spawn 连续失败超 MAX_RETRIES 后 events.log 出真 OrchestratorDispatchFailed,
    payload 含失败 decision (trigger/dispatch_to/handler) + retry_count。flat 形 (无 after_state
    wrapper) 因 producer 即 flat — 注册即 conforming → 进 PAYLOAD_VALIDATION_ENFORCED。
    """

    model_config = _STRICT

    decision_id: str = Field(min_length=1)
    trigger_event_id: str = Field(min_length=1)
    trigger_event_type: str = Field(min_length=1)
    dispatch_to: str = Field(min_length=1)
    handler: str = Field(min_length=1)
    final_error: str  # 异常信息原样 — 可能为空串, 不加 min_length 防 producer 自身合法写被拒
    retry_count: int = Field(ge=0)
    manual_orchestrator: bool = False
    task_id: str | None = None  # T3 review finding-2: execution fan-out 的 task 身份 (非 fan-out 决策为 None)


class ExecClaimReapedPayload(BaseModel):  # T-RMD-S3-REAPER (根治 f-sub-atomic-claim-no-reaper)
    """ExecClaimReaped — exec spawn 原子认领 (.claim) reaper 回收一个泄漏/过期 claim 的留痕。

    病根: 原子认领 (claim_exec_spawn O_EXCL) 无 reaper —— 崩溃 (SIGKILL 卡在 claim 与
    finally:release 之间) 泄漏的 .claim 永久存在 → 该 task 永远被认为已认领、永不重派 = 永久饿死
    (live 实证)。reaper (orchestrator 启动预检 + 每轮 backlog re-scan 调 awareness.claim.reap_stale_claims)
    回收一个 claim → emit 本事件: 被饿死 task_id + 回收原因 (stale 心跳超时 / malformed 坏文件) +
    claim 心跳年龄。daemon-internal self-observation, 走 path-B (write_direct), provenance=SYSTEM/
    f11-orchestrator-polling (非交互 — 不是人手 CLI session)。flat 形 (无 after_state wrapper) 因
    producer 即 flat → 注册即 conforming → PAYLOAD_VALIDATION_ENFORCED (同 OrchestratorDispatchFailed /
    ReconcileCyclePublished 范式)。malformed 回收时 claimant 可空串、fencing_token/age_s 为 -1 (读不出)。
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)  # 被回收 claim 的 task (malformed 时回退文件名 slug, 仍非空)
    claimant: str  # 原 claimant; malformed 读不出 → 空串
    fencing_token: int  # 原认领 token; malformed → -1
    age_s: float  # 回收时心跳年龄 (now - ts); malformed → -1.0
    stale_after_s: float = Field(ge=0)  # 本次回收用的心跳超时阈值
    reason: Literal["stale", "malformed"]
    reaped_at: str = Field(min_length=1)  # ISO 8601 回收时刻


class ReconcileCyclePublishedPayload(BaseModel):
    """哨兵 A3 空转源 — reconcile-cycle-count-emission@v1 (每轮 level-triggered reconcile pass 收尾发布).

    orchestrator polling loop 每跑一轮 run_reconcile_pass 收尾, emit 一条本轮活动快照: 五计数供哨兵
    A3 空转探测 (detect_a3_reconcile, src/towow/awareness/detection_rules.py)。判据 = 跨 cycles
    水位涨 (watermark_after>before) 但全 dispatched_count==0 且 active_session_count==0 → 报
    reconcile_idle (daemon 吃自己 DaemonRunCompleted 心跳空转, autopilot_idle_audit 实证的水位线涨坑)。

    现状 DaemonRunCompleted (scanned_count/findings_produced/run_duration_ms/outcome) 归 M-2.x 维护
    daemon 家族 (daemon_base.py), 派生不出这五计数 → 需专属事件。flat 形 (无 after_state wrapper) 因
    producer (l2/reconcile_loop.run_reconcile_pass) 即 flat — 注册即 conforming → PAYLOAD_VALIDATION_ENFORCED。
    daemon-internal self-observation, 走 path-B (write_direct), 非被审计的域 patch (同 OrchestratorDispatchFailed)。

    🔴 dispatched_count 不变量 (INV-SENT-A3-NO-HEARTBEAT): 只计【真前进派发】= execution spawn +
    replan dispatch + dead-letter triage, 显式排除 daemon 吃自己 DaemonRunCompleted 心跳 —— 纯吃心跳的
    轮 dispatched_count 必为 0。这是 A3 空转判据成立的前提 (心跳轮 0 派发才能被识别成空转)。
    """

    model_config = _STRICT

    watermark_before: int = Field(ge=0)
    watermark_after: int = Field(ge=0)
    dispatched_count: int = Field(ge=0)  # 真前进派发数 (exec spawn + replan + dead-letter triage; 不含心跳)
    active_session_count: int = Field(ge=0)
    action_count: int = Field(default=0, ge=0)  # 本轮 reconcile_all 算出的动作数 (reconcile disabled 时 0)


class SentinelPassCompletedPayload(BaseModel):  # T-RMD-S5-OBSERVER (根治 f-turnon-sentinel-blind-silent-swallow)
    """眼睛 liveness (睁眼了) — 哨兵 A1-A8 安全四不变量观测 pass 一轮跑完的留痕。

    病根: 维护 polling loop / frame 里哨兵 pass 崩被 ``contextlib.suppress(Exception)`` / 适配器
    ``except: return "sentinel:error"`` 静默吞 —— 无 event/无 log, 监督层自己失明无人知 (V-02 地基被掏空)。
    根治把哨兵一轮拆两信号: 本事件 = liveness (pass 真跑过, 扫了几条候选 / emit 几条 finding, 即便全 0,
    每轮无条件 emit), 与 readiness (FindingCreated, 稳态≈0) 分开 —— "睁眼了" 与 "抓到了" 互不冒充。

    flat 形 (无 after_state wrapper), producer (m2x_polling.run_sentinel_pass_safe) 即 flat → 注册即
    conforming → PAYLOAD_VALIDATION_ENFORCED (同 ReconcileCyclePublished / ExecClaimReaped 范式)。
    daemon-internal self-observation, 走 path-B (write_direct), 非被审计的域 patch。
    """

    model_config = _STRICT

    findings_emitted: int = Field(ge=0)  # 本轮真 emit 的 FindingCreated 数 (readiness; 稳态≈0)
    candidates_examined: int = Field(ge=0)  # 本轮考察的违例候选数 (去重后待 emit 集大小)
    suppressed: bool = False  # 防洪闸 (拟 emit > cap → 抑制整批只发一条降级告警)
    pass_at: str = Field(min_length=1)  # ISO 8601 pass 收尾时刻


class SentinelPassFailedPayload(BaseModel):  # T-RMD-S5-OBSERVER (根治 f-turnon-sentinel-blind-silent-swallow)
    """眼睛崩可见 (sentinel-blind, critical) — 哨兵 A1-A8 pass 整轮崩时 emit, 不静默吞。

    对齐同文件维护 daemon scan 崩走 ``_failed_daemon_iteration`` emit ``DaemonRunCompleted(outcome=failed)``
    让失败可见的范式 —— 眼睛崩同样要留痕, 否则监督层失明而无人知。**绝不再抛** (loop/frame 韧性硬要求):
    崩 → emit 本事件 → 继续。flat 形, producer 即 flat → PAYLOAD_VALIDATION_ENFORCED, path-B (write_direct)。
    """

    model_config = _STRICT

    reason: str = Field(min_length=1)  # 崩溃摘要 (type + msg, 截断)
    failed_at: str = Field(min_length=1)  # ISO 8601 崩溃时刻


class ProjectInitializedPayload(BaseModel):  # T-L3kc-01 (波1)
    """M-3.1 §3.3 step3 — towow init 的"项目已初始化"标记 (canonical 化, T-L3kc-01)。

    此前 init emit 裸 NodeTouched(DISCARDABLE_NOISE) 假名 (无 kind), 不可被识别为初始化标记。
    注册此 payload 后 init emit 真 ProjectInitialized 非噪声标记: done_criteria = events.log 出真
    event_type=ProjectInitialized, 识别为"项目已初始化"而非 DISCARDABLE_NOISE。
    """

    model_config = _STRICT

    project_root: str = Field(min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  RUN-052 (M-3.1 §10) — run wrapper Run* lifecycle (F-18 RunDigestPublished)
# ════════════════════════════════════════════════════════════════════════════════
#
# Flat payloads matching M-3.1 §10.2 exactly (no after_state wrapper). The run wrapper
# (cli.main._run_wrapper) is the sole producer and emits conforming dicts → these go straight
# into PAYLOAD_VALIDATION_ENFORCED (fail-closed). Replaces the per-command DAEMON_RUN_COMPLETED
# fake events maintenance commands used to emit.


class RunArtifact(BaseModel):
    """RunDigestPublished.artifacts[] per M-3.1 §10.2."""

    model_config = _STRICT

    artifact_id: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    summary: str  # may be empty — don't reject a legit producer write


class RunMetrics(BaseModel):
    """RunDigestPublished.metrics per M-3.1 §10.2 (all optional — run_type determines which apply)."""

    model_config = _STRICT

    events_processed: int = Field(default=0, ge=0)
    cold_archive_size_delta_bytes: int = 0
    snapshots_created: list[str] = Field(default_factory=list)
    obligations_retired: list[str] = Field(default_factory=list)
    consolidation_failures: list[str] = Field(default_factory=list)
    # M-0.7 v3.6+ segment compaction run_type (all optional, default 0 for other run types)
    segments_compacted: int = Field(default=0, ge=0)
    hot_bytes_reclaimed: int = Field(default=0, ge=0)
    index_records_pruned: int = Field(default=0, ge=0)


class RunStartedPayload(BaseModel):
    """RunStarted payload — M-3.1 §10.1 step1 (run wrapper opens a maintenance run)."""

    model_config = _STRICT

    run_id: str = Field(min_length=1)
    run_type: str = Field(min_length=1)  # consolidate | snapshot | gc | audit-consolidation
    started_at: str = Field(min_length=1)  # ISO-8601
    run_args: dict[str, str] = Field(default_factory=dict)


class RunDigestPublishedPayload(BaseModel):
    """F-18 RunDigestPublished payload — M-3.1 §10.2 (the digest the run wrapper publishes)."""

    model_config = _STRICT

    run_id: str = Field(min_length=1)
    run_type: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    outcome: str = Field(min_length=1)  # success | partial | failed
    artifacts: list[RunArtifact] = Field(default_factory=list)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    digest_summary: str  # 人话总结 — may be empty


class RunFailedPayload(BaseModel):
    """RunFailed payload — M-3.1 §10.1 step3 (the maintenance skill/command raised)."""

    model_config = _STRICT

    run_id: str = Field(min_length=1)
    run_type: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    failed_at: str = Field(min_length=1)
    error: str  # exception repr — may be empty in pathological cases


# ════════════════════════════════════════════════════════════════════════════════
#  Phase D Patch M-2.2-A — ObligationRetireCandidate
# ════════════════════════════════════════════════════════════════════════════════


class ObligationRetireCandidateAfter(BaseModel):
    """Patch M-2.2-A ObligationRetireCandidate.after_state."""

    model_config = _STRICT

    candidate_id: str = Field(min_length=1)
    obligation_id: str = Field(min_length=1)
    candidate_reason: ObligationRetireCandidateReason
    evidence: list[EvidenceItem] = Field(default_factory=list)
    retire_completion_path: str = Field(min_length=1)


class ObligationRetireCandidatePayload(BaseModel):  # Patch M-2.2-A
    """Patch M-2.2-A ObligationRetireCandidate payload — surface only, no retire action."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.OBLIGATION_RETIRE_CANDIDATE] = (
        TargetEntityType.OBLIGATION_RETIRE_CANDIDATE
    )
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: ObligationRetireCandidateAfter


# ════════════════════════════════════════════════════════════════════════════════
#  Phase D Patch M-2.3-A — HistoricalPatternSurfaceCandidate / EscalationTriaged /
#                          EscalationResolved (state_transition slice)
# ════════════════════════════════════════════════════════════════════════════════


class RawFindingQuote(BaseModel):
    """HistoricalPatternSurfaceCandidate.raw_quotes[] per M-2.3 §3.1 — verbatim, no rewriting."""

    model_config = _STRICT

    finding_event_id: str = Field(min_length=1)
    description: str
    risk_surface: str
    detection_method: str
    created_at: str  # ISO8601 string


class CiaImpactSummary(BaseModel):
    """HistoricalPatternSurfaceCandidate.cia_impact_summary — mechanically produced."""

    model_config = _STRICT

    affected_concepts: list[str] | None = None
    affected_files: list[str] | None = None
    slice_direction: str | None = None  # spec leaves open


class ManualFeedCrossReference(BaseModel):
    """HistoricalPatternSurfaceCandidate.cross_reference_with_manual_feed per M-2.3 §3.1."""

    model_config = _STRICT

    is_already_in_manual_feed: bool
    related_manual_entry_ids: list[str] | None = None


class HistoricalPatternSurfaceCandidateAfter(BaseModel):
    """Patch M-2.3-A HistoricalPatternSurfaceCandidate.after_state."""

    model_config = _STRICT

    candidate_id: str = Field(min_length=1)
    grouping_dimension: str = Field(min_length=1)  # GroupingDimension enum value
    grouping_key: str = Field(min_length=1)
    finding_event_ids: list[str] = Field(min_length=3)  # ≥ 3 invariant
    frequency: int = Field(ge=3)
    first_observed_at: str
    last_observed_at: str
    span_runs: int = Field(ge=2)  # ≥ 2 invariant (cross-run)
    raw_quotes: list[RawFindingQuote] = Field(min_length=1)
    cia_impact_summary: CiaImpactSummary | None = None
    cross_reference_with_manual_feed: ManualFeedCrossReference


class HistoricalPatternSurfaceCandidatePayload(BaseModel):  # Patch M-2.3-A
    """Patch M-2.3-A HistoricalPatternSurfaceCandidate payload."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.HISTORICAL_PATTERN_CANDIDATE] = (
        TargetEntityType.HISTORICAL_PATTERN_CANDIDATE
    )
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: HistoricalPatternSurfaceCandidateAfter


class EscalationTriagedBefore(BaseModel):
    model_config = _STRICT

    lifecycle_state: Literal["raised"] = "raised"


class EscalationTriagedAfter(BaseModel):
    """Patch M-2.3-A EscalationTriaged.after_state."""

    model_config = _STRICT

    lifecycle_state: Literal["triaged"] = "triaged"
    triage_category: str = Field(min_length=1)  # TriageCategory enum value
    triage_reasoning: str
    expected_nature_action: str | None = None
    expected_nature_action_reasoning: str | None = None
    related_finding_ids: list[str] | None = None
    related_concept_ids: list[str] | None = None
    related_task_ids: list[str] | None = None
    related_obligation_ids: list[str] | None = None
    triage_time_lag_ms: int = Field(ge=0)


class EscalationTriagedPayload(BaseModel):  # Patch M-2.3-A
    """Patch M-2.3-A EscalationTriaged payload."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ESCALATION] = TargetEntityType.ESCALATION
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    before_state: EscalationTriagedBefore = Field(default_factory=EscalationTriagedBefore)
    after_state: EscalationTriagedAfter


class EscalationResolvedBefore(BaseModel):
    model_config = _STRICT

    lifecycle_state: Literal["triaged"] = "triaged"


class EscalationResolvedAfter(BaseModel):
    """Patch M-2.3-A EscalationResolved.after_state."""

    model_config = _STRICT

    lifecycle_state: Literal["resolved"] = "resolved"
    resolution: str = Field(min_length=1)  # EscalationResolution enum value
    nature_judgment_event_id: str = Field(min_length=1)
    resolution_explanation: str
    cascade_events_produced: list[str] | None = None
    raised_to_resolved_duration_ms: int = Field(ge=0)


class EscalationResolvedPayload(BaseModel):  # Patch M-2.3-A
    """Patch M-2.3-A EscalationResolved payload."""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ESCALATION] = TargetEntityType.ESCALATION
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    before_state: EscalationResolvedBefore = Field(default_factory=EscalationResolvedBefore)
    after_state: EscalationResolvedAfter


# ────────────────────────────────────────────────────────────────────────────────
#  M-1.6 §3.6 / §10.2 Patch X — EscalationRaised (RUN-036 T-L1-70)
#  fix 修不动 (feasibility check #5 fail / multi-round no-novelty) 升级给 Nature 产品决策。
#  ≠ GoalEscalationRaised (goal session pause/resume); 正交机制。
# ────────────────────────────────────────────────────────────────────────────────


class EscalationRaisedOption(BaseModel):
    """M-1.6 §3.6 EscalationRaised.after_state.options[] — 产品语言备选项 (F-09b)."""

    model_config = _STRICT

    option: str = Field(min_length=1)  # 产品语言, 不是抽象 tradeoff
    tradeoff: str = Field(min_length=1)  # 用具体场景
    product_impact: str = Field(min_length=1)  # 用户感受


class EscalationRaisedAfter(BaseModel):
    """M-1.6 §3.6 / §10.2 EscalationRaised.after_state (Patch X).

    F-09b 产品语言: nature_facing_summary / what_was_tried / why_it_did_not_close /
    decision_needed_from_nature 禁工程黑话 (无变量名/函数名/行号); 工程 detail 放
    engineering_detail_ref 单独链接, 不污染 Nature 视野。fix_id/finding_id/task_id 可空
    (M-1.6 §3.6 标 string?), 但 escalation_id / 4 个 nature-facing 字段 / engineering_detail_ref 必填。
    """

    model_config = _STRICT

    escalation_id: str = Field(min_length=1)
    fix_id: str | None = None
    finding_id: str | None = None
    task_id: str | None = None
    # === Nature-facing 字段 (F-09b 产品语言 ★) ===
    nature_facing_summary: str = Field(min_length=1)
    what_was_tried: str = Field(min_length=1)
    why_it_did_not_close: str = Field(min_length=1)
    decision_needed_from_nature: str = Field(min_length=1)
    options: list[EscalationRaisedOption] = Field(default_factory=list)
    # === 工程 detail (链接出去, 不污染 Nature 视野) ===
    engineering_detail_ref: str = Field(min_length=1)


class EscalationRaisedPayload(BaseModel):  # M-1.6 §3.6 Patch X
    """M-1.6 §3.6 EscalationRaised — fix 修不动升级给 Nature 产品决策.

    base_classification=immutable_truth (§10.2: Nature 决策相关事实永久保留, 同
    NatureJudgmentCaptured 精神)。M-1.6 唯一生产者, 0 历史真事件 → enforced (不进 EXEMPT, 同
    T-L1-50 ReviewPlanCreated 范式)。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.ESCALATION] = TargetEntityType.ESCALATION
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: EscalationRaisedAfter


# ════════════════════════════════════════════════════════════════════════════════
#  Phase C Patch M-0.5a-3 / Patch D — LockReleased (registered into M-0.1)
# ════════════════════════════════════════════════════════════════════════════════


class LockReleasedAfter(BaseModel):
    """LockReleased.after_state per M-0.5 §7.3 (release_reason values defined in §7.4 table)."""

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    entities: list[EntityRef] = Field(default_factory=list)
    release_reason: str = Field(min_length=1)


class LockReleasedPayload(BaseModel):  # Patch M-0.5a-3
    """Patch M-0.5a-3: LockReleased registered into M-0.1 state_transition class.

    M-0.5 §7.4 lock release strategy table determines whether LockReleased is produced
    per rejection_type (abandoned / obligation_violated / claims_exceeded etc. → produce;
    version_conflict / drift / novelty_missing → skip, agent retains lock for retry).
    """

    model_config = _STRICT

    # Patch M-0.5a-3: target_entity_type not registered in M-0.1 §2.3.1 — use task as
    # the natural lock holder.
    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: LockReleasedAfter


# ════════════════════════════════════════════════════════════════════════════════
#  K2b-REG (50-graph-protocol §TG.4) — SessionSpawned / LockAcquired (图协议新节点物化事件)
# ════════════════════════════════════════════════════════════════════════════════


class SessionSpawnedAfter(BaseModel):
    """SessionSpawned.after_state per 50-graph-protocol §6.1 (血缘真兑现).

    字段是 §6.1 设计字段 (spawned_role/task_id/trigger_event_id) 与 node_reducers.reduce_node_events
    消费键 (session_id/parent_session_id/form) 的**超集** — 同一事件既喂图 reducer 又留全血缘上下文。
    parent_session_id 可空 (根会话/手动起的会话无父); session_id 必填。reducer 据 parent_session_id
    建 parent→child 的 spawned 边 (血缘是 session→session 图关系, 非事件属性 — 故独立事件非补字段)。
    """

    model_config = _STRICT

    session_id: str = Field(min_length=1)  # 被 spawn 出的子会话 (node_reducers 键)
    parent_session_id: str | None = None  # 派生它的父会话 (None = 根/手动); 有则建 spawned 边
    form: str | None = None  # 会话形态 (fork / bg / sub-agent / shell — 呼应层① per-form)
    spawned_role: str | None = None  # §6.1 设计字段: 被派的角色 (executor / reviewer / ...)
    task_id: str | None = None  # §6.1: 为哪个 task spawn
    trigger_event_id: str | None = None  # §6.1: 触发本次 spawn 的事件


class SessionSpawnedPayload(BaseModel):  # K2b-REG §6.1
    """SessionSpawned — 图协议会话血缘节点物化事件 (50-graph-protocol §6.1).

    新事件无 legacy producer → 留在 PAYLOAD_VALIDATION_ENFORCED (fail-closed, 同 ReviewPlanCreated
    范式)。唯一消费方 = node_reducers.reduce_node_events → session_graph.json。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.SESSION] = TargetEntityType.SESSION
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: SessionSpawnedAfter


class LockAcquiredAfter(BaseModel):
    """LockAcquired.after_state per 50-graph-protocol §6.2 (锁获取留痕上图).

    字段对齐 node_reducers.reduce_node_events 消费键: lock_id (锁节点 id) / session_id (持有者) /
    resource (被锁实体)。reducer 据此建 lock 节点 + lock→session 的 held-by 边。
    注: 对应的 LockReleased (已存在) payload 是 task/run/entities 形, 无 lock_id — release 路径
    在 node_reducers 里按 lock_id 失活 held-by 边, 故现有 LockReleased 喂不进 (K2b-REG 交回缺口)。
    """

    model_config = _STRICT

    lock_id: str = Field(min_length=1)  # 锁节点 id (node_reducers 键)
    session_id: str = Field(min_length=1)  # 占这把锁的会话 (held-by 边目标)
    resource: str | None = None  # 被锁的资源/实体 (展示用)


class LockAcquiredPayload(BaseModel):  # K2b-REG §6.2
    """LockAcquired — 图协议锁节点物化事件 (50-graph-protocol §6.2).

    新事件无 legacy producer → 留在 PAYLOAD_VALIDATION_ENFORCED (fail-closed)。唯一消费方 =
    node_reducers.reduce_node_events → lock_graph.json。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.LOCK] = TargetEntityType.LOCK
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: LockAcquiredAfter


# ════════════════════════════════════════════════════════════════════════════════
#  R08-T1 plan zombie-lock reap — PlanSessionLockReaped (vitality-gated lock release)
# ════════════════════════════════════════════════════════════════════════════════


class GraphInvestigationEvidence(BaseModel):
    """GP-01 (f-sub-graph-protocol-readside-dormant) — reap 前 investigate 的跨域图读证据。

    reap 决策前, investigate (统一图协议读侧, awareness.investigate.DefaultInvestigate over
    GraphFacade) 在 lock_graph/session_graph 上一跳查 target 的【跨域】holds_locks + lineage ——
    vitality 裁决只看 plan-registry 的 plan-only 信号, investigate 补的是图上别的域 (execution/goal)
    的活 hold / 活血缘。把它写进 PlanSessionLockReaped 即证【读侧真被 live 调用过】(read side
    live-invoked, 非"建好测过从未接进运行路径"的 dormant 态) —— 这是 GP-01 接线的 canonical 留痕。
    """

    model_config = _STRICT

    recommended_action: str = Field(min_length=1)  # investigate 给的建议动作 (harvest/redispatch/...)
    holds_locks: list[str] = Field(default_factory=list)  # 图上 target 仍持的跨域锁 id (空=无未决 hold)
    lineage_children: list[str] = Field(default_factory=list)  # 图上 target spawned 的子会话
    lineage_siblings: list[str] = Field(default_factory=list)  # 图上 target 的兄弟会话
    rationale: str = Field(min_length=1)  # investigate 的判据 (可被审计直接复核)


class PlanSessionLockReapedAfter(BaseModel):
    """PlanSessionLockReaped.after_state — 死 planner 会话的锁被 vitality 裁决后回收的留痕。

    只有 vitality 裁决 == dead 才会产生此事件: vitality_verdict 用 Literal['dead'] 把这个
    fail-closed 不变量焊进 schema —— 任何声称 reap 了非 dead 会话的事件都不合法 (R11 误杀活
    会话烧钱的根禁忌)。携带 vitality 证据 (canonical 产物 + transcript 新鲜度) 让"有没有误杀
    活会话"日后可机器审计。
    """

    model_config = _STRICT

    reaped_session_id: str = Field(min_length=1)  # 被回收锁的死 plan 会话 id
    plan_id: str | None = None  # 锁继承的 plan_id (若有)
    vitality_verdict: Literal["dead"]  # 唯一合法值 — 只有 dead 才收割
    vitality_reason: str = Field(min_length=1)  # vitality 一句话裁决理由
    has_success_product: bool  # 证据: canonical success 产物 (dead ⟹ False)
    has_partial_product: bool  # 证据: canonical 半成品 (dead ⟹ False)
    last_activity_age_s: float | None = None  # 证据: transcript 最后活动距今秒 (None=无 transcript)
    reaper_session_id: str = Field(min_length=1)  # 执行 reap 的独立会话 (不冒充死会话)
    reaped_at: str = Field(min_length=1)  # ISO 8601
    # GP-01: reap 前 investigate 跨域图读证据 (读侧 live-invoked 留痕)。None = 旧事件 (未接 investigate 前)。
    graph_investigation: GraphInvestigationEvidence | None = None


class PlanSessionLockReapedPayload(BaseModel):
    """PlanSessionLockReaped — `towow plan reap-stale-session` 唯一生产者 (R08-T1)。

    死 planner 会话留下的 zombie plan 锁 (registry per-session 锁 + 双写单指针) 卡死后续
    `plan start` 串行门; 此命令用 vitality 机器裁决该会话死活, 仅 dead 才 python-unlink 释放锁,
    并 emit 本事件留痕 (provenance + vitality 证据)。conforming producer → ENFORCED (fail-closed)。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: PlanSessionLockReapedAfter
    semantic_annotation: SemanticAnnotation | None = None


# ════════════════════════════════════════════════════════════════════════════════
#  DOGFOOD-RUN-003 P-12 GOAL mode — GoalSessionStarted / GoalSessionTerminated /
#                                   GoalEscalationRaised
# ════════════════════════════════════════════════════════════════════════════════


class GoalSessionStartedAfter(BaseModel):
    """GoalSessionStarted.after_state — RUN-003 / P-12."""

    model_config = _STRICT

    goal_session_id: str = Field(min_length=1)
    brief_event_id: str = Field(min_length=1)
    capsule_compiled_event_id: str = Field(min_length=1)
    command_text: str = Field(min_length=1)  # audit trail: actual `claude --bg '...'` command
    started_at: str = Field(min_length=1)  # ISO 8601
    parent_session_id: str = Field(min_length=1)
    condition_text: str = Field(min_length=1, max_length=4000)  # P-12: ≤4000 chars
    worktree_path: str = Field(min_length=1)  # 1:1:1 granularity per C-05
    # task #73: daemon-assigned full session UUID from ~/.claude/jobs/<short>/state.json.
    # None for mock / tmux / state.json read failure paths.
    claude_full_session_id: str | None = None
    # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: claude --bg 真实分配的
    # 进程级 short id, 与 goal_session_id (spawn 前预生成的领域身份, 本 fix 后两者解耦) 分开记录。
    # 判活/attach (escalation_reflow.resolve_goal_session_target 经 orchestrator._resolve_bg_session_id
    # 反查) 要用这个去匹配 `claude agents --json`, 领域身份对它们是无意义的假 id。None = 未解耦路径
    # (execution fan-out 等) 或旧版事件, 反查退化回 goal_session_id 自身 (零回归)。
    bg_session_id: str | None = None


class GoalSessionStartedPayload(BaseModel):
    """GoalSessionStarted payload — RUN-003 / P-12.

    Emitted when F-11 orchestrator spawns a new background session via
    `bash -c "claude --bg '<initial prompt>'"` for an autonomous run. The
    condition_text is the actual condition string passed to `/goal` —
    designer-style, references-only v3 entities per C-03 protocol, ≤4000 chars.
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: GoalSessionStartedAfter
    semantic_annotation: SemanticAnnotation | None = None


class GoalSessionTerminatedAfter(BaseModel):
    """GoalSessionTerminated.after_state — RUN-003 / P-12."""

    model_config = _STRICT

    goal_session_id: str = Field(min_length=1)
    # restart_frozen: escalate 中等 owner 回话的会话被受控重启 (pause stop_windows) 冻断 —
    # 进程已死但 daemon state 仍 blocked, 不即时收割就是占 task 的僵尸
    # (finding-escalation-frozen-session-zombie-not-reaped-1)。pause 主动标记, 不留 blocked 僵尸。
    termination_reason: Literal[
        "completion", "escalation", "unreachable", "external", "restart_frozen",
    ]
    terminated_at: str = Field(min_length=1)  # ISO 8601
    final_status: str = Field(min_length=1)  # human-readable summary
    triggering_event_id: str | None = None  # NJ confirming completion / escalation event_id


class GoalSessionTerminatedPayload(BaseModel):
    """GoalSessionTerminated payload — RUN-003 / P-12.

    Emitted when a GO session ends. termination_reason matches the two
    legitimate exit paths declared in P-12 (completion / escalation) + two
    edge cases (unreachable detected by evaluator / external kill).
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.MODIFIED] = TransitionType.MODIFIED
    after_state: GoalSessionTerminatedAfter
    semantic_annotation: SemanticAnnotation | None = None


class GoalEscalationRaisedAfter(BaseModel):
    """GoalEscalationRaised.after_state — RUN-003 / C-07."""

    model_config = _STRICT

    escalation_id: str = Field(min_length=1)
    goal_session_id: str = Field(min_length=1)
    owner_question: str = Field(min_length=1)  # what the agent is asking Nature
    reason: str = Field(min_length=1)  # why this is Class A
    raised_at: str = Field(min_length=1)  # ISO 8601
    awaiting_response: bool = True  # bg session paused, awaits NJ with responds_to_escalation_event_id
    # T4 (PLAN-FIX): 这条 escalation 该停多大范围。global = 停整个后台 (撞影响全局的 owner 决策);
    # session_only = 只停发起它的那个 session, 其它链继续跑 (边缘/不阻塞主线的 owner-gated 决策,
    # 如 T-L0-02 历史数据改不改 — 它把整个后台停了 4 天正是这病)。默认 global (向后兼容 + 保守:
    # 旧事件无此字段 → 当 global 停, 不漏停该停的)。
    blocking_scope: Literal["global", "session_only"] = "global"
    # R08 TI-3: 升级前做功课管线 (escalation_prep) 的产物 —— 背景/已排除/候选/推荐/取舍, 让
    # escalation 触达 owner 时就带完整上下文包 (owner 一眼能判, 非光秃秃一个问题)。可选 (向后兼容:
    # 旧事件 / 未做功课的无此字段)。结构对齐 escalation_prep.PreparedEscalation 的可序列化形态。
    prepared_context: dict[str, object] | None = None


class GoalEscalationRaisedPayload(BaseModel):
    """GoalEscalationRaised payload — RUN-003 / C-07.

    Emitted when GO agent encounters genuine Class A owner-level decision and
    pauses the background session. Owner responds via main session NJ event
    with `responds_to_escalation_event_id` field set; bg session subscribes
    to its own escalation event and resumes when matching NJ arrives.
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.TASK] = TargetEntityType.TASK
    transition_type: Literal[TransitionType.CREATED] = TransitionType.CREATED
    after_state: GoalEscalationRaisedAfter
    semantic_annotation: SemanticAnnotation | None = None


class EscalationAnswerAppliedPayload(BaseModel):
    """R08 硬闭环留痕 — owner 答复真送回那个等待会话并被消费 (非只落账)。

    由 escalation respond / orchestrator 在把 owner 答复 (NJ) 投递回等待会话后 emit。
    这正是历史 0 次真闭环的修复点: 答复落账 ≠ 完成, 必须真应用到等待会话 + 留此痕。
    """

    model_config = _STRICT

    escalation_event_id: str = Field(min_length=1)   # 它闭合哪条 GoalEscalationRaised
    nj_event_id: str = Field(min_length=1)           # owner 答复的那条 NJ
    applied_to_task_id: str = Field(min_length=1)
    applied_to_session_id: str = Field(min_length=1)  # 答复被送回的等待会话
    answer_text: str = Field(min_length=1)
    resumed: bool = True                              # 被卡链解冻续跑


__all__ = [
    # M-1.4 §3.5 advisor-consult (RUN-038 波2)
    "AdvisorConsultRequestedAfter",
    "AdvisorConsultRequestedPayload",
    "AdvisorVerdictDeliveredAfter",
    "AdvisorVerdictDeliveredPayload",
    # 21 base payloads (M-0.1 §3.1)
    "AtReferenceAddedPayload",
    "AtReferenceAutoUpdatedPayload",
    # Shared sub-models
    "AtReferenceEndpoint",
    "AtReferenceRemovedPayload",
    "AtReferenceTargetSupersededPayload",
    "ConceptCreatedPayload",
    "ConceptEdgeAddedPayload",
    "ConceptEdgeRemovedPayload",
    "ConceptGraphProposalPayload",
    "ConceptStateTransitionPayload",
    "ConsumerListPublishedPayload",
    # Phase D payloads
    "DaemonRunCompletedPayload",
    "EngineeringConsensusFreezedPayload",
    "EntityRef",
    "EscalationAnswerAppliedPayload",
    "EscalationResolvedPayload",
    "EscalationTriagedPayload",
    "EvidenceItem",
    "FixAttemptNovelty",
    "FixCompletedAfter",
    "FixCompletedPayload",
    "FixConceptStateChange",
    "FixProposedPayload",
    "FixSelfVerification",
    "FixSelfVerificationCriterion",
    "FixSelfVerificationResidual",
    "FixSelfVerificationRipple",
    "FixSemanticUpgradeDeclaration",
    # RUN-003 / P-12 GOAL mode payloads
    "GoalEscalationRaisedPayload",
    "GoalSessionStartedPayload",
    "GoalSessionTerminatedPayload",
    "HistoricalPatternSurfaceCandidatePayload",
    "InformationNeedCreatedPayload",
    "InformationNeedStatusChangedPayload",
    "InvalidationCascadePayload",
    "LockAcquiredAfter",
    "LockAcquiredPayload",
    "LockReleasedPayload",
    "MismatchDetectedAfter",
    "MismatchDetectedPayload",
    "MismatchEvidence",
    "MismatchEvidenceRef",
    "MismatchResolutionDecidedAfter",
    "MismatchResolutionDecidedPayload",
    "ObligationRetireCandidatePayload",
    "OrchestratorDispatchFailedPayload",
    "PatchProposedPayload",
    "PlanSessionLockReapedAfter",
    "PlanSessionLockReapedPayload",
    "PlanningUncertaintyPayload",
    "ProjectInitializedPayload",
    # RePlanTriggered (RUN-038 波3 T-L1-42 / T-L1-71)
    "RePlanEvidenceRef",
    "RePlanFixScopeEvidence",
    "RePlanTriggeredAfter",
    "RePlanTriggeredPayload",
    "ReconcileCyclePublishedPayload",
    # RUN-052 (M-3.1 §10) run wrapper Run* lifecycle
    "RunArtifact",
    "RunDigestPublishedPayload",
    "RunFailedPayload",
    "RunMetrics",
    "RunStartedPayload",
    "SemanticAnnotation",
    "SentinelPassCompletedPayload",
    "SentinelPassFailedPayload",
    "SessionSpawnedAfter",
    "SessionSpawnedPayload",
    "TaskDependencyEdgeAddedPayload",
    "TaskDependencyEdgeRemovedPayload",
    "TaskModelTierAssignedPayload",
    "TaskNodeCreatedPayload",
    "TaskPackagePublishedPayload",
    "TaskReadSetClaimedPayload",
    "TaskRunCompletedPayload",
    "TaskWriteSetClaimedPayload",
]
