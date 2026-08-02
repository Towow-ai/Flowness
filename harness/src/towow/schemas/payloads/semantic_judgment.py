"""semantic_judgment category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.2 SemanticJudgmentFields (L137..L149)
#     §3.2 (L527..L705) — 12 base semantic_judgment payloads
#   05-l2-maintenance/M-2.3-candidate-curation-lifecycle-detailed-design.md
#     §3.4 EscalationLearningCaptured (L325..L363)             # Patch M-2.3-A
#     §3.5 EscalationDecisionMade (L372..L414)                 # Patch M-2.3-C (schema 补全)
#
# Total: 12 base + 1 Phase D EscalationLearningCaptured = 13 payloads.
# EscalationDecisionMade name 在 base §3.11 (12-th 语义判断) — schema 在 M-2.3 §3.5 补全.
#
# Shared SemanticJudgmentFields per M-0.1 §2.3.2:
#   judgment_type (str literal per payload) / confidence (float?) / evidence: list /
#   uncertainty: str? / fallback_applied: bool? (only resolver_decision)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    AuditTriggerReason,
    AuditVerdictValue,
    ConsistencyVerdictValue,
    DecidedState,
    DecisionMadeBy,
    ImpactType,
    JudgmentType,
    LearningType,
    ObligationScopeJudgment,
    ObligationScopeJudgmentSource,
    SemanticUpgradeChangeType,
    SubjectEntityType,
    TaskType,
)
from towow.schemas.payloads.state_transition import EvidenceItem

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ════════════════════════════════════════════════════════════════════════════════
#  Shared sub-models
# ════════════════════════════════════════════════════════════════════════════════


class TaskScopeRef(BaseModel):
    """ObligationScopeJudged.after_state.task_scope per M-0.1 §3.2."""

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    task_type: TaskType
    target_artifacts: list[str] = Field(default_factory=list)


class AffectedEntityWithImpact(BaseModel):
    """ImpactAssessmentMade.after_state.affected_entities[] per M-0.1 §3.2."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    impact_type: ImpactType


class AuditFinding(BaseModel):
    """AuditVerdictReceived.after_state.findings[] per M-0.1 §3.2."""

    model_config = _STRICT

    description: str
    severity: str = Field(min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  12 base semantic_judgment payloads — M-0.1 §3.2
# ════════════════════════════════════════════════════════════════════════════════


class NatureJudgmentCapturedAfter(BaseModel):
    model_config = _STRICT

    judgment_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    meaning: str
    scope: str
    is_value_judgment: bool
    # T-E4 (RUN-003 prereq): when NJ responds to a GoalEscalationRaised event,
    # this carries that escalation event_id so bg session pull-mode subscribers
    # can resume. None when NJ is not an escalation response.
    responds_to_escalation_event_id: str | None = None


class NatureJudgmentCapturedPayload(BaseModel):
    """Nature 判断 — confidence must be null (Nature 判断不打分 invariant)."""

    model_config = _STRICT

    judgment_type: Literal[JudgmentType.NATURE_JUDGMENT] = JudgmentType.NATURE_JUDGMENT
    after_state: NatureJudgmentCapturedAfter
    confidence: None = None
    evidence: list[EvidenceItem] = Field(min_length=1)
    uncertainty: str | None = None


class SemanticChange(BaseModel):
    """SemanticUpgradeDeclaration.after_state.semantic_changes[] per M-0.1 §3.2."""

    model_config = _STRICT

    change_type: SemanticUpgradeChangeType
    affected_entity_id: str = Field(min_length=1)
    relationship_to_graph: str


class SemanticUpgradeDeclarationAfter(BaseModel):
    model_config = _STRICT

    sibling_event_id: str = Field(min_length=1)
    semantic_changes: list[SemanticChange] = Field(min_length=1)


class SemanticUpgradeDeclarationPayload(BaseModel):
    """O-14 涟漪 — stage skill 自声明语义解释，confidence=null."""

    model_config = _STRICT

    judgment_type: Literal[JudgmentType.SEMANTIC_UPGRADE] = JudgmentType.SEMANTIC_UPGRADE
    after_state: SemanticUpgradeDeclarationAfter
    confidence: None = None


class ResolverPipelineStages(BaseModel):
    """ResolverDecisionMade.after_state.pipeline_stages — complex nested; spec leaves stage_2/3/4/6.

    Step 1.c records spec faithfully via dict typing — sub-stage validation deferred to
    M-0.3 Capsule Assembly pipeline implementation (Step 5).
    """

    model_config = _STRICT

    stage_2_candidate_retrieval: dict[str, object]
    stage_3_mechanical_filtering: dict[str, object]
    stage_4_semantic_judgment: dict[str, object]
    stage_6_placement: list[dict[str, object]]


class ReentryContextRef(BaseModel):
    """M-0.3 §5.2 — re-evaluation context recorded on a reentry ResolverDecisionMade.

    reason explains WHY the re-eval happened (scope drift / new touched node / M-0.5 reject);
    previous_verdict_event_id links to the prior ResolverDecisionMade so the audit trail shows
    the incremental re-judgment (the resolver also receives this as ReentryContext, §5.2 step 3).
    """

    model_config = _STRICT

    reason: str = Field(min_length=1)
    previous_verdict_event_id: str | None = None
    previous_run_id: str | None = None


class ResolverDecisionMadeAfter(BaseModel):
    model_config = _STRICT

    resolver_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    pipeline_stages: ResolverPipelineStages
    final_active_set: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    cache_hit: bool
    # M-0.3 §9.1 / §4.3 — cache_lookup_hash recorded when the stage-4 lookup hit (else null);
    # resolver_runtime_ms is null on a cache hit (resolver not invoked), int ms otherwise.
    cache_lookup_hash: str | None = None
    resolver_runtime_ms: int | None = Field(default=None, ge=0)
    # M-0.3 §5.1/§5.2 — re-evaluation: reentry=true forces a full re-run (cache bypassed) and
    # records reentry_context (why re-eval + previous run/verdict). Defaults preserve the
    # non-reentry shape byte-for-byte.
    reentry: bool = False
    reentry_context: ReentryContextRef | None = None
    # M-0.3 §9.1 (L644) — the committed cutoff (= bundle.as_of_seq) stage 1 read against. The
    # decision event itself records the projection截点 it judged at (independent of CapsuleCompiled
    # §9.2, which M-0.4 Cap3 derives from). Optional / default 0 so pre-existing ResolverDecisionMade
    # events validate unchanged (backward compatible); the real pipeline stamps the read_consistent
    # watermark — the same value its sibling CapsuleCompiled carries (single fresh read, §2.1).
    as_of_projection_seq: int = Field(default=0, ge=0)


class ResolverDecisionMadePayload(BaseModel):
    """M-0.3 capsule assembly activation product — sub-judgment confidences embedded in stages."""

    model_config = _STRICT

    judgment_type: Literal[JudgmentType.RESOLVER_DECISION] = JudgmentType.RESOLVER_DECISION
    after_state: ResolverDecisionMadeAfter
    confidence: None = None
    fallback_applied: bool


class ObligationScopeJudgedAfter(BaseModel):
    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    task_scope: TaskScopeRef
    judgment: ObligationScopeJudgment
    scope_rule_applied: str
    # RUN-068 件B (M-0.6 §4.2.1): judgment_source — every candidate (hit/miss/uncertain) records
    # WHO judged it (mechanical_filter / resolver_judgment / fallback_conservative), so the
    # scope-decision audit chain is uniform (机械的也留痕, 不是只 resolver 才留). Additive + OPTIONAL.
    judgment_source: ObligationScopeJudgmentSource | None = None


class ObligationScopeJudgedPayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.OBLIGATION_SCOPE] = JudgmentType.OBLIGATION_SCOPE
    after_state: ObligationScopeJudgedAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)
    uncertainty: str | None = None


class RiskSurfaceAssignedAfter(BaseModel):
    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    risk_surface: str = Field(min_length=1)
    assignment_reason: str


class RiskSurfaceAssignedPayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.RISK_SURFACE] = JudgmentType.RISK_SURFACE
    after_state: RiskSurfaceAssignedAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


class ImpactAssessmentMadeAfter(BaseModel):
    model_config = _STRICT

    change_event_id: str = Field(min_length=1)
    affected_entities: list[AffectedEntityWithImpact] = Field(min_length=1)
    assessment_summary: str


class ImpactAssessmentMadePayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.IMPACT_ASSESSMENT] = JudgmentType.IMPACT_ASSESSMENT
    after_state: ImpactAssessmentMadeAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


class ConsistencyJudgmentMadeAfter(BaseModel):
    model_config = _STRICT

    subject_event_id: str = Field(min_length=1)
    reference_event_ids: list[str] = Field(min_length=1)
    verdict: ConsistencyVerdictValue
    inconsistency_detail: str | None = None


class ConsistencyJudgmentMadePayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.CONSISTENCY] = JudgmentType.CONSISTENCY
    after_state: ConsistencyJudgmentMadeAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


# T-L1-46: MismatchVerdictAfter + MismatchVerdictPayload 已删 — MismatchVerdict event
# (M-1.4 §3.4 v2.1) 删除后无 owner; 功能并入 MismatchResolutionDecided / AdvisorVerdictDelivered。
# 详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 10。


class AlternativeCriticalPath(BaseModel):
    """M-1.3 §3.5 (M-1.3a 审查补) — one equal-length alternative critical path."""

    model_config = _STRICT

    task_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class CriticalPathIdentifiedAfter(BaseModel):
    model_config = _STRICT

    plan_id: str = Field(min_length=1)
    critical_path_tasks: list[str] = Field(min_length=1)
    bottleneck_tasks: list[str] | None = None
    estimated_duration: str | None = None
    # T-L1-27 (M-1.3 §3.5) — algorithm-computed fields (was: human-passed --task only).
    # alternative_critical_paths: every OTHER equal-length longest chain (M-1.3a 审查补 — 多等长时
    # 全部列出). estimated_makespan: longest-chain node count (启发式, 1 unit/task). Optional/default
    # so pre-T-L1-27 CriticalPathIdentified events still replay.
    alternative_critical_paths: list[AlternativeCriticalPath] = Field(default_factory=list)
    estimated_makespan: int | None = None


class CriticalPathIdentifiedPayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.CRITICAL_PATH] = JudgmentType.CRITICAL_PATH
    after_state: CriticalPathIdentifiedAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


class AuditTriggeredAfter(BaseModel):
    model_config = _STRICT

    trigger_reason: AuditTriggerReason
    envelope_event_id: str = Field(min_length=1)
    audit_scope: str


class AuditTriggeredPayload(BaseModel):
    """Trigger 本身不打 confidence."""

    model_config = _STRICT

    judgment_type: Literal[JudgmentType.AUDIT_TRIGGER] = JudgmentType.AUDIT_TRIGGER
    after_state: AuditTriggeredAfter
    confidence: None = None


class AuditVerdictReceivedAfter(BaseModel):
    model_config = _STRICT

    audit_id: str = Field(min_length=1)
    trigger_event_id: str = Field(min_length=1)
    verdict: AuditVerdictValue
    findings: list[AuditFinding] | None = None
    recommended_action: str | None = None


class AuditVerdictReceivedPayload(BaseModel):
    model_config = _STRICT

    judgment_type: Literal[JudgmentType.AUDIT_VERDICT] = JudgmentType.AUDIT_VERDICT
    after_state: AuditVerdictReceivedAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  EscalationDecisionMade — Patch M-2.3-C (schema 补全 by M-2.3 §3.5)
# ════════════════════════════════════════════════════════════════════════════════


class EscalationDecisionMadeAfter(BaseModel):
    """Patch M-2.3-C EscalationDecisionMade.after_state per M-2.3 §3.5."""

    model_config = _STRICT

    decided_state: DecidedState
    decision_summary: str
    decision_made_by: DecisionMadeBy


class EscalationDecisionMadePayload(BaseModel):  # Patch M-2.3-C
    """Patch M-2.3-C: M-2.3 §3.5 补全 M-0.1 §11.5 留的 placeholder.

    Invariants per M-2.3 §3.5:
      - decision_made_by=nature ↔ confidence is None (Nature 判断不打分)
      - decision_made_by=agent_self_resolved ↔ confidence required (agent must be self-aware)
    """

    model_config = _STRICT

    judgment_type: Literal["escalation_decision"] = "escalation_decision"
    after_state: EscalationDecisionMadeAfter
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    uncertainty: str | None = None
    fallback_applied: bool | None = None
    related_escalation_resolved_event_id: str | None = None


# ════════════════════════════════════════════════════════════════════════════════
#  EscalationLearningCaptured — Patch M-2.3-A (Phase D 新增)
# ════════════════════════════════════════════════════════════════════════════════


class EscalationLearningCapturedAfter(BaseModel):
    """Patch M-2.3-A EscalationLearningCaptured.after_state per M-2.3 §3.4.

    Invariants:
      - learning_type=no_learning ↔ seed_for_event_id is None
    """

    model_config = _STRICT

    captured_at_lifecycle_state: Literal["post_resolved"] = "post_resolved"
    learning_type: LearningType
    seed_for_event_id: str | None = None
    capturing_reasoning: str
    nature_already_provided_feedback: bool
    manual_feedback_event_ids: list[str] | None = None


class EscalationLearningCapturedPayload(BaseModel):  # Patch M-2.3-A
    """Patch M-2.3-A EscalationLearningCaptured payload."""

    model_config = _STRICT

    judgment_type: Literal["escalation_learning"] = "escalation_learning"
    after_state: EscalationLearningCapturedAfter
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  SemanticConflictDetected — T-FIX-B5-03 (CONSTITUTION-unknown#3)
#
#  RUN-080 漏洞2 (SPEC-PATCH-CONCURRENCY-RUN080.md §2): 两并发会话对同一 obligation 给相反 scope
#  判定 (in_scope vs out_of_scope) 的检测留痕。l1/semantic_conflict.detect_semantic_conflicts 是判别
#  库层 (纯函数), 本 payload 是它的 canonical 落地形态。
#
#  🔴 检测 ≠ 仲裁: 本事件只携双方证据迹 (谁/哪条事件/什么判定) 供后续仲裁层消费; 仲裁 verdict
#     (谁赢 / reject / resolver 裁决) SKIP, 挂 deferral 债 debt-run080-semantic-arbitration。故 payload
#     最小化 — 只 obligation_id + sides (各方证据迹), 无 winner / resolution / verdict 字段。
#  judgment_type 用 string Literal "semantic_conflict" (同 EscalationDecision/EscalationLearning 范式,
#  不进 JudgmentType 闭枚举 — 它是检测产物而非打分判断, confidence=None)。
# ════════════════════════════════════════════════════════════════════════════════


class SemanticConflictSide(BaseModel):
    """SemanticConflictDetected.after_state.sides[] — 一条参与冲突的 scope 判定的证据迹。

    镜像 l1/semantic_conflict.JudgmentSide: event_id / session_id / judgment / confidence / actor_id。
    """

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    session_id: str | None = None
    judgment: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    actor_id: str | None = None


class SemanticConflictDetectedAfter(BaseModel):
    """SemanticConflictDetected.after_state — 同一 obligation 被不同 session 给相反判定。

    sides ≥2 (一个冲突至少两方); 仲裁 deferred → 无 winner / resolution 字段 (最小检测留痕)。
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    sides: list[SemanticConflictSide] = Field(min_length=2)


class SemanticConflictDetectedPayload(BaseModel):  # T-FIX-B5-03
    """T-FIX-B5-03 SemanticConflictDetected payload — 检测留痕 (confidence=None, 不裁不打分)。"""

    model_config = _STRICT

    judgment_type: Literal["semantic_conflict"] = "semantic_conflict"
    after_state: SemanticConflictDetectedAfter
    confidence: None = None


__all__ = [
    "AffectedEntityWithImpact",
    "AlternativeCriticalPath",
    "AuditFinding",
    "AuditTriggeredAfter",
    "AuditTriggeredPayload",
    "AuditVerdictReceivedAfter",
    "AuditVerdictReceivedPayload",
    "ConsistencyJudgmentMadeAfter",
    "ConsistencyJudgmentMadePayload",
    "CriticalPathIdentifiedAfter",
    "CriticalPathIdentifiedPayload",
    "EscalationDecisionMadeAfter",
    "EscalationDecisionMadePayload",
    "EscalationLearningCapturedAfter",
    "EscalationLearningCapturedPayload",
    "ImpactAssessmentMadeAfter",
    "ImpactAssessmentMadePayload",
    "NatureJudgmentCapturedAfter",
    "NatureJudgmentCapturedPayload",
    "ObligationScopeJudgedAfter",
    "ObligationScopeJudgedPayload",
    "ResolverDecisionMadeAfter",
    "ResolverDecisionMadePayload",
    "ResolverPipelineStages",
    "RiskSurfaceAssignedAfter",
    "RiskSurfaceAssignedPayload",
    "SemanticChange",
    "SemanticConflictDetectedAfter",
    "SemanticConflictDetectedPayload",
    "SemanticConflictSide",
    "SemanticUpgradeDeclarationAfter",
    "SemanticUpgradeDeclarationPayload",
    "TaskScopeRef",
]
