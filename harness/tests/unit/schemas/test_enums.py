"""Unit tests for towow.schemas.enums.

Covers:
- 每个 enum 类的成员数 (spec-anchored counts)
- 67 EventType 全部成员可枚举
- Phase D 真 new event_type / target_entity_type / finding_kind 全部存在
- 13 Phase D + M-0.5a patch grep 锚点全部在源文件中 (# Patch M-X.Y-Z)
- Spec 内部矛盾标记 (Conflict 1/2/3) 用注释存在以备 surface
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from towow.schemas import enums

if TYPE_CHECKING:
    from enum import StrEnum
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    CheckerPatternType,
    CommitVerdict,
    DecidedState,
    DecisionMadeBy,
    DetectionRuleLifecycleState,
    DigestType,
    EscalationLifecycleState,
    EscalationResolution,
    EventCategory,
    EventType,
    EvidenceSourceType,
    ExpectedNatureAction,
    FindingDetectionMethod,
    FindingKind,
    FindingLifecycleState,
    FindingSeverity,
    GroupingDimension,
    JudgmentType,
    LearningType,
    NoveltyType,
    ObligationAttachedToEntityType,
    ObligationCanonicalState,
    ObligationCaptureSource,
    ObligationDeclaredStatus,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    RejectionType,
    SceneType,
    SnapshotType,
    TargetEntityType,
    TransitionType,
    TriageCategory,
)

# ─── 成员数（spec-anchored） ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("enum_cls", "expected_count", "spec_source"),
    [
        (EventCategory, 10, "M-0.1 §2.2"),
        (BaseClassification, 3, "M-0.1 §2.1"),
        (ActorType, 9, "M-0.1 §2.1 (7) + M-2.x extensions (2)"),
        (NoveltyType, 4, "M-0.1 §2.1"),
        (
            EventType,
            124,
            "f-stale-closure-contract-permanently-unclosable 1 (FindingClosureContractAmended — "
            "陈旧 closure_contract 的修订通道: 合约此前写一次即不可变, 判据锚定位置被合法重构架空后该 "
            "finding 物理上永久不可闭合; `towow review finding-contract-amend` 唯一生产者, 走 supersede "
            "+ NoveltyCheck, finding_lifecycle reducer 消费但**不**改 lifecycle_state — amend 非状态迁移) + "
            "T-JLM-03 1 (JudgmentRegressionEvaluated — preference-as-test-harness@v1 回归验证门结论) + "
            "T-JLM-01 1 (JudgmentCaseEnriched — judgment-case@v1 富化持久化) + "
            "owner-confirm@v1 1 (OwnerConfirmationGranted) + owner-gate-clearance@v1 1 "
            "(TaskNodeOwnerGateCleared) + "
            "f-escalation-task-oriented 1 (FindingAccepted — 改不了/不可变 finding 如账本 553 被 owner "
            "接受为基线 → ACCEPTED, 哨兵永不再报; owner path-B 直写, finding_lifecycle reducer 消费) + "
            "哨兵 A6 正经源 1 (IrreversibleActionBlocked — irreversible-action-blocked-audit-event@v1, "
            "PhysicalGate DENY 高危不可逆动作 best-effort emit; check_and_emit 唯一生产者) + "
            "哨兵 A3 空转源 1 (ReconcileCyclePublished — reconcile-cycle-count-emission@v1, 每轮 reconcile "
            "pass 收尾发布五计数; reconcile_loop.run_reconcile_pass 唯一生产者; detect_a3_reconcile 消费) + "
            "K2b-REG (50-graph-protocol §TG.4) 2 (SessionSpawned / LockAcquired — 图协议会话血缘 + 锁"
            "节点物化事件; node_reducers 消费产 session_graph / lock_graph) + "
            "R08-T1 1 (PlanSessionLockReaped — 死 planner 会话 zombie plan 锁被 vitality 裁决后回收的"
            "留痕, `towow plan reap-stale-session` 唯一生产者; vitality 仅 dead 才释放锁) + "
            "R08 1 (EscalationAnswerApplied — owner 答复送回等待会话被消费的硬闭环留痕) + "
            "T-FIX-B4-05 (INT-content#1/PARALLEL-locks#2) 2 (GuardAdminBypassGranted / "
            "GuardAdminBypassRevoked — admin-bypass 旗标受控产生/撤销留痕) + "
            "T-FIX-B5-03 (CONSTITUTION-unknown#3) 1 (SemanticConflictDetected — 语义冲突检测留痕) + "
            "T-FIX-B6-01 (PLAN-seam#1) 1 (TaskDependencyEdgeRemoved — task 图纠错原语撤边) + "
            "RUN-066 M-3.3 §11.1 4 (MigrationRunStarted / MigrationStepRecorded / "
            "MigrationBatchSubmitted / MigrationRunCompleted — 散落形态迁移工具留痕链) + "
            "RUN-058 EPIC-10 1 (ValidationScenarioRun — M-3.4 §3.3 验证大盘留痕) + "
            "M-0.1 §3.11 base 59 + Phase D real-new 7 + M-0.5a-D 1 + RUN-003 GOAL 3 "
            "(GoalSessionStarted / GoalSessionTerminated / GoalEscalationRaised) + "
            "RUN-017C Patch Q 1 (InterviewBriefPublished) + RUN-027 块4 L1 session-start 3 "
            "(InterviewSessionStarted / EngineeringConsensusSessionStarted / PlannerSessionStarted) + "
            "RUN-029 第0波 ③ self-debt 2 (DebtRegistered / DebtResolved) + "
            "RUN-031 T-L3kc-03 OwnerGuardViolation 1 + T-L3kc-06 CapsuleInjectionFailed 1 + "
            "RUN-035 T-L1-50 ReviewPlanCreated 1 + "
            "RUN-036 T-L1-70 EscalationRaised 1 (M-1.6 §3.6 Patch X) + "
            "RUN-038 加固(2) 2 (MismatchDetected / MismatchResolutionDecided, M-1.4 §3.2/§3.3) + "
            "T-TRACK-01 2 (CapabilityBaselineIngested / CapabilityStatusAdvanced — 能力状态真 projection) + "
            "T-L3kc-04 1 (OrchestratorDispatchFailed canonical 化, 波1) + "
            "T-L3kc-01 1 (ProjectInitialized canonical 化, 波1) + "
            "T-L0-16 1 (CapsuleAssemblyFailed — capsule 装配 abort 留痕, 波1) "
            "− RUN-038 波2 T-L1-46 1 (MismatchVerdict 删, M-1.4 §3.4 v2.1, LEDGER Conflict 10) "
            "+ RUN-038 波2 T-L1-40 1 (AdvisorConsultRequested, M-1.4 §3.5) "
            "+ RUN-038 波2 T-L1-49 1 (AdvisorVerdictDelivered, M-1.4 §3.5) "
            "+ RUN-038 波2 T-L1-31 1 (PlanningUncertainty, M-1.3 §3.7) "
            "+ RUN-038 波3 T-L1-13 2 (AtReferenceAutoUpdated / AtReferenceTargetSuperseded, "
            "M-1.2 §4.3 Patch S 失效响应辅助 event) "
            "+ RUN-038 波3 T-L1-42/T-L1-71 1 (RePlanTriggered, M-1.3 §14.2 schema owner / "
            "M-1.4 §3.8 + M-1.6 §3.4 producers, LEDGER Conflict 12) "
            "+ RUN-052 (M-3.1 §10) 3 (RunStarted / RunDigestPublished / RunFailed — run wrapper "
            "lifecycle, F-18, 取代 DAEMON_RUN_COMPLETED 假事件) "
            "+ T-RMD-S3-REAPER 1 (ExecClaimReaped — exec 原子认领 reaper 回收泄漏/过期 .claim 的留痕, "
            "根治 f-sub-atomic-claim-no-reaper; reap_stale_exec_claims 唯一生产者, path-B 直写) "
            "+ T-RMD-S5-OBSERVER 2 (SentinelPassCompleted / SentinelPassFailed — 哨兵 A1-A8 pass "
            "liveness/failure 自观测, 崩可见不静默吞 + 睁眼了与抓到了拆两信号; run_sentinel_pass_safe "
            "唯一生产者, path-B 直写; 根治 f-turnon-sentinel-blind-silent-swallow) "
            "+ 1 (并发兄弟会话 ReviewDimensionExercised — 本批为 import-coherence 共提其 enum/registry/payload)",
        ),
        (
            TargetEntityType,
            24,
            "M-0.1 §2.3.1 (9) + M-1.6 Patch X (1) + Phase D (4) + "
            "RUN-017C Patch Q (1, interview_brief) + RUN-035 T-L1-50 Patch Z (1, review_plan) + "
            "RUN-038 加固(2) (1, mismatch — M-1.4 §3.2/§3.3) + "
            "T-L1-48 (1, advisor_consult — Narrow Patch Y 补齐 3/3, 波1) + "
            "RUN-058 EPIC-10 (1, validation_scenario — M-3.4 §3.3) + "
            "RUN-066 M-3.3 §11.3 (2, migration_run / migration_step — 散落形态迁移工具留痕 target) + "
            "K2b-REG (50-graph-protocol §TG.4) (2, session / lock — 图协议新节点 target)",
        ),
        (TransitionType, 5, "M-0.1 §2.3.1"),
        (JudgmentType, 12, "M-0.1 §2.3.2 (11) − T-L1-46 mismatch_verdict 删 (10) + M-2.3 §3.4/§3.5 (2)"),
        (EvidenceSourceType, 5, "M-0.1 §2.3.2"),
        (SnapshotType, 3, "M-0.1 §2.3.3"),
        (DigestType, 3, "M-0.1 §2.3.4"),
        (enums.ArchiveDestination, 2, "M-0.7 §2.4 (cold / discarded)"),
        (enums.LookbackType, 3, "M-0.7 §2.3 (run_generations / seq_range / manual)"),
        (enums.ProvenanceRefRelevance, 3, "M-0.7 §2.3 (primary / supporting / mentioned)"),
        (enums.SnapshotSupersedeNoveltyType, 3, "M-0.7 §2.2 (snapshot content-error novelty)"),
        (enums.DigestSupersedeNoveltyType, 4, "M-0.7 §2.5 (digest amend novelty)"),
        (SceneType, 10, "M-0.1 §2.3.5 (9) + M-0.7 §10.3 Patch K (consolidation)"),
        (CommitVerdict, 2, "M-0.1 §2.3.7"),
        (RejectionType, 8, "M-0.1 §2.3.7 (7) + M-0.7 §10.3 Patch N (consolidation_invariant_violated)"),
        (FindingSeverity, 5, "M-0.1 §2.3.8 (4) + M-1.5 §3.2 purple (RUN-035 T-L1-51)"),
        (FindingLifecycleState, 5, "M-0.1 §2.3.8 (4) + f-escalation ACCEPTED (5th, 不可变 finding 基线终态)"),
        (FindingDetectionMethod, 3, "M-0.1 §2.3.8"),
        (FindingKind, 12, "M-1.6 Patch D (5) + M-2.1-C (1) + M-2.2-C (3) + R07 govloop (2) + premise_false (1)"),
        (DetectionRuleLifecycleState, 7, "M-0.1 §2.3.9 (6) ∪ M-2.3 §4.1 (5) — union pending patch"),
        (ObligationFieldsLifecycleState, 6, "M-0.1 §2.3.10"),
        (ObligationCanonicalState, 3, "M-0.6 §3.1"),
        (ObligationNature, 5, "M-0.6 §2.1"),
        (ObligationSeverity, 3, "M-0.6 §2.1"),
        (ObligationScopeType, 5, "M-0.6 §2.1"),
        (ObligationAttachedToEntityType, 5, "M-0.6 §2.1"),
        (ObligationCaptureSource, 6, "M-0.6 §2.1"),
        (CheckerPatternType, 3, "M-0.6 §2.1 (M-0.5a Patch 3)"),
        (ObligationDeclaredStatus, 3, "M-0.1 §2.3.6"),
        (GroupingDimension, 5, "M-2.3 §3.1"),
        (TriageCategory, 8, "M-2.3 §3.2"),
        (ExpectedNatureAction, 5, "M-2.3 §3.2"),
        (EscalationResolution, 7, "M-2.3 §3.3 (no 'other' fallback)"),
        (LearningType, 4, "M-2.3 §3.4"),
        (DecidedState, 4, "M-2.3 §3.5"),
        (DecisionMadeBy, 2, "M-2.3 §3.5"),
        (EscalationLifecycleState, 4, "PHASE-D §2.2"),
        (
            enums.SubjectEntityType,
            16,
            "M-0.1a Patch 1 §1.2 (13) + RUN-035 T-L1-50 review_plan (1) + "
            "RUN-038 加固(2) mismatch (1, M-1.4 §3.2/§3.3) + "
            "RUN-038 波2 T-L1-40 advisor_consult (1, M-1.4 §3.5)",
        ),
        (enums.SubjectRole, 7, "M-0.1a Patch 1 §1.2"),
        (enums.BatchStatus, 2, "M-0.1a Patch 1 §1.4"),
        (enums.PatchType, 7, "M-0.4 §5 (5) + M-0.6 Narrow Patch I (1) + M-0.7 §10.3 Patch L (consolidation_event)"),
    ],
)
def test_enum_member_count(enum_cls: type[StrEnum], expected_count: int, spec_source: str) -> None:
    """Each enum class has exactly the spec-specified number of members."""
    actual = len(list(enum_cls))
    assert actual == expected_count, (
        f"{enum_cls.__name__} expected {expected_count} members per {spec_source}, "
        f"got {actual}: {list(enum_cls)}"
    )


# ─── EventType 全枚举 + 关键成员 ────────────────────────────────────────────


def test_event_type_is_at_least_67() -> None:
    """Plan §0 / §3.1.a contract: EventType ≥ 67 (RUN-003 added 3 GOAL events → 70)."""
    assert len(list(EventType)) >= 67


def test_event_type_members_enumerable() -> None:
    """All 80 EventType members are individually accessible via __members__.

    67 base (§3.11 59 + Phase D 7 + M-0.5a-D 1) + RUN-003 GOAL mode 3 +
    RUN-017C Patch Q 1 (InterviewBriefPublished) + … + RUN-031 T-L3kc-03 OwnerGuardViolation 1.
    """
    members = EventType.__members__
    # T-L1-46 −MismatchVerdict; T-L1-40 +AdvisorConsultRequested; T-L1-49 +AdvisorVerdictDelivered
    # T-L1-31 +PlanningUncertainty; T-L1-42/T-L1-71 +RePlanTriggered; RUN-058 +ValidationScenarioRun
    # RUN-066 +4 Migration* (MigrationRunStarted/StepRecorded/BatchSubmitted/RunCompleted)
    # T-FIX-B6-01 +TaskDependencyEdgeRemoved (PLAN-seam#1 task 图纠错原语撤边)
    # T-FIX-B5-03 +SemanticConflictDetected (CONSTITUTION-unknown#3 语义冲突检测留痕)
    # T-FIX-B4-05 +GuardAdminBypassGranted/GuardAdminBypassRevoked (admin-bypass 旗标受控产生路径)
    # R08 +EscalationAnswerApplied (owner 答复送回等待会话被消费的硬闭环留痕)
    # R08-T1 +PlanSessionLockReaped (死 planner zombie plan 锁 vitality-gated reap 留痕)
    # K2b-REG +SessionSpawned/+LockAcquired (50-graph-protocol §TG.4 图协议新节点物化事件)
    # f-escalation-task-oriented +FindingAccepted (改不了/不可变 finding 接受为基线, 哨兵永不再报)
    # 哨兵 A6 +IrreversibleActionBlocked (irreversible-action-blocked-audit-event@v1, PhysicalGate DENY emit)
    # 哨兵 A3 +ReconcileCyclePublished (reconcile-cycle-count-emission@v1 reconcile pass 五计数发布)
    # T-RMD-S3-REAPER +ExecClaimReaped (exec 原子认领 reaper 回收泄漏/过期 .claim 留痕)
    # T-RMD-S5-OBSERVER +SentinelPassCompleted/+SentinelPassFailed (哨兵 pass liveness/failure 自观测,
    #   崩可见不静默吞 + 睁眼了与抓到了拆两信号, 根治 f-turnon-sentinel-blind-silent-swallow)
    # (并发兄弟会话 +ReviewDimensionExercised — 本批为 import-coherence 共提其 enum/registry/payload)
    # 组件6 +OwnerConfirmationGranted (owner-confirm@v1 — 不可伪造 owner 授权载体, 带 Ed25519 detached
    #   签名; retire 闭合门 + 4 红线物理门共用钉死公钥验签核; `towow owner-confirm grant` 唯一生产者)
    # owner-gate-clearance@v1 +TaskNodeOwnerGateCleared (autopilot-owner-presence-removal, owner
    #   2026-07-01 决策 — owner 经 CLI 显式解除某 task owner-gate; `towow plan owner-gate-clear` 生产)
    # T-JLM-01 +JudgmentCaseEnriched (judgment-case@v1 富化持久化, l1.judgment_case + CLI 唯一生产者)
    # T-JLM-03 +JudgmentRegressionEvaluated (preference-as-test-harness@v1 回归验证门结论,
    #   l1.judgment_regression_harness + `towow judgment-regression run` CLI 唯一生产者)
    # f-stale-closure-contract-permanently-unclosable +FindingClosureContractAmended (陈旧
    #   closure_contract 的修订通道 — 闭合门此前恒读首条 FindingCreated 的合约, 合约事实上写一次即不可变,
    #   判据锚定位置被后续**合法**重构架空后该 finding 在任何 resolution 口径下都被硬拦 = 永久不可闭合;
    #   `towow review finding-contract-amend` 唯一生产者 (review 座位 — 修复者不得改自己的判据), 走
    #   supersede 链 + M-0.5 NoveltyCheck; 闭合门三处改读末条 amend 的**生效**合约。
    #   ⚠ 非 lifecycle transition: reducer 只登记"合约被修订过", 不动 lifecycle_state)
    assert len(members) == 124
    # spot-check across categories
    assert EventType.CONCEPT_CREATED.value == "ConceptCreated"
    assert EventType.NATURE_JUDGMENT_CAPTURED.value == "NatureJudgmentCaptured"
    assert EventType.CAPSULE_COMPILED.value == "CapsuleCompiled"
    assert EventType.OBLIGATION_CAPTURED.value == "ObligationCaptured"
    # RUN-003 GOAL mode additions
    assert EventType.GOAL_SESSION_STARTED.value == "GoalSessionStarted"
    assert EventType.GOAL_SESSION_TERMINATED.value == "GoalSessionTerminated"
    assert EventType.GOAL_ESCALATION_RAISED.value == "GoalEscalationRaised"
    # RUN-017C Patch Q addition
    assert EventType.INTERVIEW_BRIEF_PUBLISHED.value == "InterviewBriefPublished"


@pytest.mark.patch
def test_phase_d_event_types_present() -> None:
    """Phase D §1.2 — 7 真正新增 event_type 名字 (EscalationDecisionMade name 已在 base §3.11)."""
    phase_d_real_new = {
        # state_transition (6)
        "InvalidationCascade",  # Patch M-2.1-A
        "DaemonRunCompleted",  # Patch M-2.1-A
        "ObligationRetireCandidate",  # Patch M-2.2-A
        "HistoricalPatternSurfaceCandidate",  # Patch M-2.3-A
        "EscalationTriaged",  # Patch M-2.3-A
        "EscalationResolved",  # Patch M-2.3-A
        # semantic_judgment (1)
        "EscalationLearningCaptured",  # Patch M-2.3-A
    }
    all_values = {et.value for et in EventType}
    missing = phase_d_real_new - all_values
    assert not missing, f"Phase D event_types missing from EventType enum: {missing}"


@pytest.mark.patch
def test_escalation_decision_made_present_as_base_name() -> None:
    """M-0.1 §3.11 总览表语义判断类 12 已含 EscalationDecisionMade name (schema 补全 by M-2.3 §3.5)."""
    assert EventType.ESCALATION_DECISION_MADE.value == "EscalationDecisionMade"


@pytest.mark.patch
def test_m05a_patch_d_lock_released_present() -> None:
    """M-0.5a Patch D/Patch 3: LockReleased registered into M-0.1 state_transition."""
    assert EventType.LOCK_RELEASED.value == "LockReleased"


@pytest.mark.patch
def test_phase_d_target_entity_types_present() -> None:
    """Phase D §1.3 — 4 新增 target_entity_type."""
    phase_d = {
        "invalidation_cascade",  # Patch M-2.1-B
        "daemon_run",  # Patch M-2.1-B
        "obligation_retire_candidate",  # Patch M-2.2-B
        "historical_pattern_candidate",  # Patch M-2.3-B
    }
    all_values = {t.value for t in TargetEntityType}
    missing = phase_d - all_values
    assert not missing, f"Phase D target_entity_types missing: {missing}"
    # escalation 由 M-1.6 Patch X 注册 — 应在 enum 中
    assert "escalation" in all_values
    # T-L1-48 (波1) Narrow Patch Y 补齐第 3/3: advisor_consult target_entity_type
    assert "advisor_consult" in all_values


@pytest.mark.patch
def test_finding_kind_accumulated_12() -> None:
    """finding_kind 累积 12 values: 5 from M-1.6 + 1 M-2.1-C + 3 M-2.2-C + 2 R07 治理环路
    + 1 退役证据 (premise_false, closure-evidence-verification-gate retired 分支)。"""
    expected = {
        # M-1.6 v2.1.1 Patch D (5)
        "closure_contract_defect",
        "concept_issue",
        "obligation_issue",
        "review_plan_issue",
        "adjacent_code_issue",
        # M-2.1-C (1)
        "cross_projection_inconsistency",
        # M-2.2-C (3)
        "routing_stuck",
        "cross_task_fix_collision",
        "anomaly",
        # R07 治理环路 (2) — T-GL-07 专属 finding_kind (不劫持 anomaly)
        "efficiency_regression",
        "system_governance_defect",
        # 退役证据 (1) — TaskClosureReason.RETIRED 的 superseded_by finding_kind (前提为假, 非代码 bug)
        "premise_false",
    }
    actual = {f.value for f in FindingKind}
    assert actual == expected


# ─── Patch grep 锚点存在性 ──────────────────────────────────────────────────


_ENUMS_PATH = Path(enums.__file__)
_ENUMS_SOURCE = _ENUMS_PATH.read_text(encoding="utf-8")


@pytest.mark.patch
@pytest.mark.parametrize(
    "anchor",
    [
        "Patch M-2.1-A",
        "Patch M-2.1-B",
        "Patch M-2.1-C",
        "Patch M-2.2-A",
        "Patch M-2.2-B",
        "Patch M-2.2-C",
        "Patch M-2.3-A",
        "Patch M-2.3-B",
        "Patch M-0.5a-3",
    ],
)
def test_patch_anchor_present_in_enums_source(anchor: str) -> None:
    """每个 Phase D + M-0.5a patch 锚点在 enums.py 源文件中可被 grep 到."""
    assert anchor in _ENUMS_SOURCE, f"Patch anchor missing in enums.py: {anchor}"


@pytest.mark.patch
def test_phase_d_patch_anchor_count_via_regex() -> None:
    """Plan §6.1 contract: enums.py 含 Phase D narrow patch 锚点 (≥ 8 个 distinct M-2.x anchors).

    Phase D patches landing in enums.py: M-2.1-A/B/C, M-2.2-A/B/C, M-2.3-A/B — 8 distinct.
    """
    matches = re.findall(r"Patch M-2\.[123]-[A-Z]\b", _ENUMS_SOURCE)
    distinct = set(matches)
    assert len(distinct) >= 8, f"Phase D distinct patch anchors in enums.py: {distinct}"


# ─── E.1.1 canonical decision markers (Conflict 1-7 全部 resolved) ────────────────


@pytest.mark.patch
def test_actor_type_extensions_canonical_per_e11() -> None:
    """E.1.1 Conflict 2 resolved: ActorType closed-enum 9 values, daemon+agent_session canonical.

    Source must reference E.1.1 narrow patch (not 'pending') and define provenance boundary.
    """
    assert "E.1.1 narrow patch" in _ENUMS_SOURCE
    assert ActorType.DAEMON.value == "daemon"
    assert ActorType.AGENT_SESSION.value == "agent_session"
    assert len(list(ActorType)) == 9


@pytest.mark.patch
def test_detection_rule_state_canonical_categories_e11() -> None:
    """E.1.1 Conflict 3 resolved: DetectionRuleLifecycleState按 canonical_emit (5) /
    deprecated_alias (1) / reserved_not_emitted (1) 三类组织.
    """
    from towow.schemas.enums import (
        emit_detection_rule_state,
        is_canonical_emit_detection_rule_state,
        is_reserved_detection_rule_state,
        parse_detection_rule_state,
    )

    # canonical_emit (5)
    canonical = [
        DetectionRuleLifecycleState.PROPOSED,
        DetectionRuleLifecycleState.SHADOW,
        DetectionRuleLifecycleState.ACTIVE_WARNING,
        DetectionRuleLifecycleState.ENFORCED,
        DetectionRuleLifecycleState.RETIRED,
    ]
    for v in canonical:
        assert is_canonical_emit_detection_rule_state(v) is True
        assert emit_detection_rule_state(v) == v.value

    # deprecated_alias normalizes
    assert parse_detection_rule_state("warning") is DetectionRuleLifecycleState.ACTIVE_WARNING

    # reserved_not_emitted (revised) — parse rejected
    with pytest.raises(ValueError, match="reserved_not_emitted"):
        parse_detection_rule_state("revised")
    # emit rejected
    with pytest.raises(ValueError, match="not in canonical_emit"):
        emit_detection_rule_state(DetectionRuleLifecycleState.REVISED)
    assert is_reserved_detection_rule_state(DetectionRuleLifecycleState.REVISED) is True

    # parse canonical pass-through
    assert (
        parse_detection_rule_state("active_warning")
        is DetectionRuleLifecycleState.ACTIVE_WARNING
    )
    assert parse_detection_rule_state("enforced") is DetectionRuleLifecycleState.ENFORCED

    # unknown value rejected
    with pytest.raises(ValueError, match="unknown detection rule"):
        parse_detection_rule_state("not_a_real_state")


# ─── Spec source 注释存在 ────────────────────────────────────────────────────


def test_enums_file_has_spec_source_header() -> None:
    """Plan §9 contract: schema 文件首部 # spec source 注释作为字段溯源."""
    assert "spec source" in _ENUMS_SOURCE
    assert "M-0.1-event-log-detailed-design.md" in _ENUMS_SOURCE
    assert "M-0.6-obligation-system-detailed-design.md" in _ENUMS_SOURCE
    assert "M-2.3-candidate-curation-lifecycle-detailed-design.md" in _ENUMS_SOURCE
    assert "PHASE-D-REVERSE-CONTRIBUTION-LOG.md" in _ENUMS_SOURCE


def test_enums_file_references_e11_ledger() -> None:
    """E.1.1 收束: enums.py 顶部应引用 SPEC-CONFLICT-RESOLUTION-LEDGER."""
    assert "SPEC-CONFLICT-RESOLUTION-LEDGER" in _ENUMS_SOURCE


# ─── StrEnum behavior basics ─────────────────────────────────────────────────


def test_event_type_str_round_trip() -> None:
    """StrEnum: round-trip via value string."""
    assert EventType("ConceptCreated") is EventType.CONCEPT_CREATED
    assert EventType("LockReleased") is EventType.LOCK_RELEASED


def test_unknown_event_type_raises() -> None:
    """StrEnum: unknown value raises ValueError (pydantic 校验依赖)."""
    with pytest.raises(ValueError, match="DoesNotExist"):
        EventType("DoesNotExist")


def test_obligation_canonical_vs_event_lifecycle_state_independent() -> None:
    """M-0.6 §3.1: canonical 3 态 跟 M-0.1 §2.3.10 event lifecycle 6 态是两个独立维度."""
    canonical = {s.value for s in ObligationCanonicalState}
    event_level = {s.value for s in ObligationFieldsLifecycleState}
    assert canonical == {"active", "superseded", "retired"}
    assert event_level == {"captured", "activated", "checked", "violated", "evolved", "retired"}
    # 仅 "retired" 是两者共享的字面值
    assert canonical & event_level == {"retired"}


def test_finding_severity_distinct_from_obligation_severity() -> None:
    """FindingFields.severity 跟 Obligation.severity 是不同 enum (M-0.1 §2.3.8 vs M-0.6 §2.1)."""
    finding = {s.value for s in FindingSeverity}
    obligation = {s.value for s in ObligationSeverity}
    # purple = M-1.5 §3.2 preexisting / adjacent latent bug (RUN-035 T-L1-51)
    assert finding == {"critical", "major", "minor", "observation", "purple"}
    assert obligation == {"red_line", "normal", "hint"}
    assert finding & obligation == set()


def test_judgment_type_includes_m23_extensions() -> None:
    """M-2.3 §3.4 / §3.5 引用 judgment_type=escalation_learning / escalation_decision."""
    values = {j.value for j in JudgmentType}
    assert "escalation_learning" in values
    assert "escalation_decision" in values


# ─── M-0.7 §10.3 Patch K / L / N — consolidation enum extensions ─────────────


def test_patch_k_scene_type_consolidation_registered() -> None:
    """M-0.7 §10.3 Patch K — SceneType 第 10 个值 consolidation (§6.2 capsule template)."""
    # registered as a real enum member (not a dead string)
    assert SceneType.CONSOLIDATION.value == "consolidation"
    # round-trips through StrEnum lookup — recognized, not just declared
    assert SceneType("consolidation") is SceneType.CONSOLIDATION
    # NEGATIVE: a non-registered scene string is not silently accepted
    with pytest.raises(ValueError, match=r"not a valid SceneType|consolidation_typo"):
        SceneType("consolidation_typo")


def test_patch_l_patch_type_consolidation_event_registered() -> None:
    """M-0.7 §10.3 Patch L — PatchType 第 7 个值 consolidation_event (§6.3 envelope patches)."""
    assert enums.PatchType.CONSOLIDATION_EVENT.value == "consolidation_event"
    assert enums.PatchType("consolidation_event") is enums.PatchType.CONSOLIDATION_EVENT
    with pytest.raises(ValueError, match=r"not a valid PatchType|consolidation_evt"):
        enums.PatchType("consolidation_evt")


def test_patch_n_rejection_type_consolidation_invariant_violated_registered() -> None:
    """M-0.7 §10.3 Patch N — RejectionType 加 consolidation_invariant_violated (§6.6 拒绝路径)."""
    assert RejectionType.CONSOLIDATION_INVARIANT_VIOLATED.value == "consolidation_invariant_violated"
    assert (
        RejectionType("consolidation_invariant_violated")
        is RejectionType.CONSOLIDATION_INVARIANT_VIOLATED
    )
    with pytest.raises(ValueError, match=r"not a valid RejectionType|consolidation_bad"):
        RejectionType("consolidation_bad")
