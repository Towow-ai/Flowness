"""M-0.1 §5.3.1 write-boundary typed-payload validation registry.

Maps every ``EventType`` to its canonical typed payload schema and provides the
dispatch-validate entry point that both write paths (``write_direct`` /
``append_transaction_batch``) call before a record enters the fact source.

WHY (panorama M-0.1 §5.3.1 fake-done — RUN-031 T-L0-01)
-------------------------------------------------------
``EventRecord.payload`` is an opaque ``dict[str, Any]``. Before this module the write
boundary validated event_id uniqueness + the Path-B allow-list + the EventBase / category
envelope (pydantic on EventRecord) — but never the **type-specific payload shape**. A
``ConceptCreated`` whose payload was actually a ``TaskNode`` shape would enter the log and
silently corrupt every downstream reducer / capsule / gate that reads the fact source.
Spec §5.3.1 requires the write boundary itself enforce "类型特有字段（不符合则拒绝写入）".

GRADUATED ENFORCEMENT (honest, non-fake)
----------------------------------------
``PAYLOAD_REGISTRY`` maps **all** event_types (verify_dep "映射真覆盖全 event_type" — a
missing entry fails this module's import-time assertion). Enforcement, however, is
fail-closed only for the event_types whose **current canonical producer** actually emits a
payload that conforms to the registered schema (``PAYLOAD_VALIDATION_ENFORCED``).

The remaining registered types still have producers that emit raw / stub-rewrap /
spec-summary-mismatched dicts — e.g. ``TransactionEnvelopeSubmitted`` emits the full M-0.4
envelope (not the M-0.1 §3.6 summary this schema models), historical ``NodeTouched`` events
stub-rewrap a foreign event inside ``kind`` / ``stub_original_payload``. Hard-enforcing the
existing schema on those would reject the system's own legitimate writes. Rather than
silently skip-and-pretend-done (the exact 假done this whole phase is fixing), those types are
recorded as ``DebtRegistered`` (debt_type=dependency_blocked) naming the producer-alignment
task that will bring each into conformance (T-L0-24/25 envelope canonical, T-L1-37
PatchProposed real emit, NodeTouched stub-rewrap cleanup, ...). As each producer is fixed,
its event_type graduates into ``PAYLOAD_VALIDATION_ENFORCED``.

VALIDATION MODE
---------------
Non-strict. The payload arrives as a JSON-derived dict, so enums are plain strings; pydantic
``strict=True`` would reject every round-tripped enum (a serialization artifact, not a
defect). ``extra="forbid"`` / required-field / discriminator (Literal) checks still apply —
those are exactly what catch an event_type↔payload mismatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from towow.schemas.enums import EventType
from towow.schemas.payloads import (
    capability,
    capsule,
    commit,
    concept_state_machine,
    consolidation,
    debt,
    detection_rule,
    envelope,
    finding,
    interview_brief,
    interview_session,
    judgment_case,
    migration,
    obligation,
    plan_freezed,
    review_plan,
    semantic_judgment,
    snapshot,
    state_transition,
    sub_skill_invocation,
    validation_scenario_run,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


# ════════════════════════════════════════════════════════════════════════════════
#  PAYLOAD_REGISTRY — event_type → canonical typed payload schema (complete)
# ════════════════════════════════════════════════════════════════════════════════

PAYLOAD_REGISTRY: dict[EventType, type[BaseModel]] = {
    # ─── state_transition ───
    EventType.CONCEPT_CREATED: state_transition.ConceptCreatedPayload,
    EventType.CONCEPT_EDGE_ADDED: state_transition.ConceptEdgeAddedPayload,
    EventType.CONCEPT_EDGE_REMOVED: state_transition.ConceptEdgeRemovedPayload,
    EventType.CONCEPT_STATE_TRANSITION: state_transition.ConceptStateTransitionPayload,
    EventType.CONCEPT_GRAPH_PROPOSAL: state_transition.ConceptGraphProposalPayload,
    EventType.TASK_NODE_CREATED: state_transition.TaskNodeCreatedPayload,
    EventType.TASK_DEPENDENCY_EDGE_ADDED: state_transition.TaskDependencyEdgeAddedPayload,
    EventType.TASK_DEPENDENCY_EDGE_REMOVED: state_transition.TaskDependencyEdgeRemovedPayload,
    # done-elsewhere-task-closure@v1 (T-DEC-1) — 全新事件无 legacy producer, conforming future
    # producer (T-DEC-4 close CLI) emit 即合规 → 留在 ENFORCED (不进 EXEMPT, fail-closed),
    # 同 ReviewPlanCreated / SessionSpawned 范式。
    EventType.TASK_NODE_CLOSED: state_transition.TaskNodeClosedPayload,
    # owner-gate-clearance@v1 (autopilot-owner-presence-removal) — 全新事件无 legacy producer,
    # 唯一生产者 `towow plan owner-gate-clear` CLI emit 即 conforming → 留在 ENFORCED (fail-closed,
    # 不进 EXEMPT), 同 TaskNodeClosed 范式。
    EventType.TASK_NODE_OWNER_GATE_CLEARED: state_transition.TaskNodeOwnerGateClearedPayload,
    EventType.TASK_READ_SET_CLAIMED: state_transition.TaskReadSetClaimedPayload,
    EventType.TASK_WRITE_SET_CLAIMED: state_transition.TaskWriteSetClaimedPayload,
    EventType.TASK_MODEL_TIER_ASSIGNED: state_transition.TaskModelTierAssignedPayload,
    EventType.TASK_PACKAGE_PUBLISHED: state_transition.TaskPackagePublishedPayload,
    EventType.TASK_RUN_COMPLETED: state_transition.TaskRunCompletedPayload,
    EventType.INFORMATION_NEED_CREATED: state_transition.InformationNeedCreatedPayload,
    EventType.INFORMATION_NEED_STATUS_CHANGED: state_transition.InformationNeedStatusChangedPayload,
    EventType.AT_REFERENCE_ADDED: state_transition.AtReferenceAddedPayload,
    EventType.AT_REFERENCE_REMOVED: state_transition.AtReferenceRemovedPayload,
    # M-1.2 §4.3 失效响应辅助 event (T-L1-13) — conforming producer → ENFORCED (不进 EXEMPT)。
    EventType.AT_REFERENCE_AUTO_UPDATED: state_transition.AtReferenceAutoUpdatedPayload,
    EventType.AT_REFERENCE_TARGET_SUPERSEDED: state_transition.AtReferenceTargetSupersededPayload,
    EventType.PATCH_PROPOSED: state_transition.PatchProposedPayload,
    # M-1.4 §3.2/§3.3 (RUN-038 加固(2) T-L1-38/T-L1-39) — enforced (NOT exempt): work mismatch /
    # work mismatch-resolve CLI 是唯一生产者 + 0 历史真事件, 同 T-L1-37 PatchProposed 范式 emit
    # 即 conforming → 留在 PAYLOAD_VALIDATION_ENFORCED (fail-closed)。
    EventType.MISMATCH_DETECTED: state_transition.MismatchDetectedPayload,
    EventType.MISMATCH_RESOLUTION_DECIDED: state_transition.MismatchResolutionDecidedPayload,
    # M-1.4 §3.5 advisor-consult (RUN-038 波2 T-L1-40 / T-L1-49): conforming producer → ENFORCED
    EventType.ADVISOR_CONSULT_REQUESTED: state_transition.AdvisorConsultRequestedPayload,
    EventType.ADVISOR_VERDICT_DELIVERED: state_transition.AdvisorVerdictDeliveredPayload,
    # RePlanTriggered (RUN-038 波3 T-L1-42 M-1.4 产 / T-L1-71 M-1.6 fix 产): M-1.3 §14.2 schema
    # owner; M-1.4 §3.8 + M-1.6 §3.4 是产生方。conforming producer (work replan / fix replan CLI) +
    # 0 历史真事件 → ENFORCED (fail-closed), 同 T-L1-38 MismatchDetected 范式。LEDGER Conflict 12。
    EventType.RE_PLAN_TRIGGERED: state_transition.RePlanTriggeredPayload,
    EventType.FIX_PROPOSED: state_transition.FixProposedPayload,
    EventType.FIX_COMPLETED: state_transition.FixCompletedPayload,
    EventType.CONSUMER_LIST_PUBLISHED: state_transition.ConsumerListPublishedPayload,
    EventType.ENGINEERING_CONSENSUS_FREEZED: state_transition.EngineeringConsensusFreezedPayload,
    EventType.INVALIDATION_CASCADE: state_transition.InvalidationCascadePayload,
    EventType.DAEMON_RUN_COMPLETED: state_transition.DaemonRunCompletedPayload,
    # RUN-052 (M-3.1 §10) run wrapper Run* lifecycle — flat §10.2 payloads, conforming producer
    # (cli.main._run_wrapper) → ENFORCED (fail-closed), same范式 as OrchestratorDispatchFailed.
    EventType.RUN_STARTED: state_transition.RunStartedPayload,
    EventType.RUN_DIGEST_PUBLISHED: state_transition.RunDigestPublishedPayload,
    EventType.RUN_FAILED: state_transition.RunFailedPayload,
    EventType.ORCHESTRATOR_DISPATCH_FAILED: state_transition.OrchestratorDispatchFailedPayload,  # T-L3kc-04
    # T-RMD-S3-REAPER: exec claim reaper 回收留痕; 唯一生产者 (orchestrator) emit 即 conforming →
    # ENFORCED (fail-closed), 不进 EXEMPT (同 OrchestratorDispatchFailed / PlanSessionLockReaped 范式)。
    EventType.EXEC_CLAIM_REAPED: state_transition.ExecClaimReapedPayload,
    EventType.RECONCILE_CYCLE_PUBLISHED: state_transition.ReconcileCyclePublishedPayload,  # 哨兵 A3 空转源
    # T-RMD-S5-OBSERVER: 哨兵 pass liveness/failure 自观测; 唯一生产者 (run_sentinel_pass_safe) emit 即
    # conforming → ENFORCED (fail-closed), 不进 EXEMPT (同 ReconcileCyclePublished / ExecClaimReaped 范式)。
    EventType.SENTINEL_PASS_COMPLETED: state_transition.SentinelPassCompletedPayload,
    EventType.SENTINEL_PASS_FAILED: state_transition.SentinelPassFailedPayload,
    EventType.PROJECT_INITIALIZED: state_transition.ProjectInitializedPayload,  # T-L3kc-01
    EventType.OBLIGATION_RETIRE_CANDIDATE: state_transition.ObligationRetireCandidatePayload,
    EventType.HISTORICAL_PATTERN_SURFACE_CANDIDATE: (
        state_transition.HistoricalPatternSurfaceCandidatePayload
    ),
    EventType.ESCALATION_TRIAGED: state_transition.EscalationTriagedPayload,
    EventType.ESCALATION_RESOLVED: state_transition.EscalationResolvedPayload,
    # M-1.6 §3.6 Patch X (RUN-036 T-L1-70) — fix 修不动升级 Nature; enforced (NOT exempt):
    # M-1.6 唯一生产者 + 0 历史真事件, 同 T-L1-50 ReviewPlanCreated 范式。
    EventType.ESCALATION_RAISED: state_transition.EscalationRaisedPayload,
    EventType.LOCK_RELEASED: state_transition.LockReleasedPayload,
    # K2b-REG (50-graph-protocol §TG.4) — 图协议新节点物化事件; enforced (新事件无 legacy producer,
    # 同 ReviewPlanCreated 范式), 不进 EXEMPT。node_reducers 消费 → session_graph / lock_graph。
    EventType.SESSION_SPAWNED: state_transition.SessionSpawnedPayload,
    EventType.LOCK_ACQUIRED: state_transition.LockAcquiredPayload,
    EventType.GOAL_SESSION_STARTED: state_transition.GoalSessionStartedPayload,
    EventType.GOAL_SESSION_TERMINATED: state_transition.GoalSessionTerminatedPayload,
    EventType.GOAL_ESCALATION_RAISED: state_transition.GoalEscalationRaisedPayload,
    EventType.ESCALATION_ANSWER_APPLIED: state_transition.EscalationAnswerAppliedPayload,
    EventType.INTERVIEW_BRIEF_PUBLISHED: interview_brief.InterviewBriefPublishedPayload,
    EventType.INTERVIEW_SESSION_STARTED: interview_session.InterviewSessionStartedPayload,
    EventType.ENGINEERING_CONSENSUS_SESSION_STARTED: (
        interview_session.EngineeringConsensusSessionStartedPayload
    ),
    EventType.PLANNER_SESSION_STARTED: interview_session.PlannerSessionStartedPayload,
    EventType.SUB_SKILL_INVOCATION_SKIPPED: sub_skill_invocation.SubSkillInvocationSkippedPayload,
    EventType.PLAN_FREEZED: plan_freezed.PlanFreezedPayload,
    EventType.PLANNING_UNCERTAINTY: state_transition.PlanningUncertaintyPayload,  # T-L1-31
    # R08-T1 plan zombie-lock reap: `towow plan reap-stale-session` 唯一生产者 + emit 即
    # conforming → ENFORCED (fail-closed, 不进 EXEMPT), 同 PlanningUncertainty 范式。
    EventType.PLAN_SESSION_LOCK_REAPED: state_transition.PlanSessionLockReapedPayload,
    EventType.CONCEPT_STATE_MACHINE_DEFINED: concept_state_machine.ConceptStateMachineDefinedPayload,
    # ─── semantic_judgment ───
    EventType.NATURE_JUDGMENT_CAPTURED: semantic_judgment.NatureJudgmentCapturedPayload,
    EventType.SEMANTIC_UPGRADE_DECLARATION: semantic_judgment.SemanticUpgradeDeclarationPayload,
    EventType.RESOLVER_DECISION_MADE: semantic_judgment.ResolverDecisionMadePayload,
    EventType.OBLIGATION_SCOPE_JUDGED: semantic_judgment.ObligationScopeJudgedPayload,
    EventType.RISK_SURFACE_ASSIGNED: semantic_judgment.RiskSurfaceAssignedPayload,
    EventType.IMPACT_ASSESSMENT_MADE: semantic_judgment.ImpactAssessmentMadePayload,
    EventType.CONSISTENCY_JUDGMENT_MADE: semantic_judgment.ConsistencyJudgmentMadePayload,
    # T-L1-46: EventType.MISMATCH_VERDICT 注册已删 — event 本身删除 (M-1.4 §3.4 v2.1)。
    EventType.CRITICAL_PATH_IDENTIFIED: semantic_judgment.CriticalPathIdentifiedPayload,
    EventType.AUDIT_TRIGGERED: semantic_judgment.AuditTriggeredPayload,
    EventType.AUDIT_VERDICT_RECEIVED: semantic_judgment.AuditVerdictReceivedPayload,
    EventType.ESCALATION_DECISION_MADE: semantic_judgment.EscalationDecisionMadePayload,
    EventType.ESCALATION_LEARNING_CAPTURED: semantic_judgment.EscalationLearningCapturedPayload,
    # T-FIX-B5-03 (CONSTITUTION-unknown#3) — 语义冲突检测留痕 (scan 入口唯一生产者, conforming →
    # ENFORCED/fail-closed, 不进 EXEMPT; flat semantic_judgment shape, 同 EscalationLearning 范式)。
    EventType.SEMANTIC_CONFLICT_DETECTED: semantic_judgment.SemanticConflictDetectedPayload,
    EventType.DEBT_REGISTERED: debt.DebtRegisteredPayload,
    EventType.DEBT_RESOLVED: debt.DebtResolvedPayload,
    # T-JLM-01 judgment-case@v1 富化持久化 — JudgmentCaseEnriched 的 canonical payload = 一个
    # JudgmentCase (9 字段 strict)。conforming producer (l1.judgment_case + CLI) 唯一生产者 + emit 即
    # conforming → 留在 ENFORCED (fail-closed, 不进 EXEMPT), 同 SemanticConflictDetected 范式。
    EventType.JUDGMENT_CASE_ENRICHED: judgment_case.JudgmentCase,
    # T-JLM-03 preference-as-test-harness@v1 回归验证门 — JudgmentRegressionEvaluated 的 canonical
    # payload = 一次回归运行结论 (score/pass_threshold/gate_result_state)。唯一生产者
    # (l1.judgment_regression_harness + CLI) emit 即 conforming → 留在 ENFORCED (fail-closed,
    # 不进 EXEMPT), 同 JudgmentCaseEnriched 范式。
    EventType.JUDGMENT_REGRESSION_EVALUATED: judgment_case.JudgmentRegressionEvaluated,
    # ─── T-TRACK-01 capability status (semantic_judgment family; producer conforms → ENFORCED) ───
    EventType.CAPABILITY_BASELINE_INGESTED: capability.CapabilityBaselineIngestedPayload,
    EventType.CAPABILITY_STATUS_ADVANCED: capability.CapabilityStatusAdvancedPayload,
    # ─── snapshot ───
    EventType.SNAPSHOT_CREATED: snapshot.SnapshotCreatedPayload,
    EventType.SNAPSHOT_SUPERSEDED: snapshot.SnapshotSupersededPayload,
    # ─── consolidation ───
    EventType.CROSS_RUN_CONSOLIDATION_COMMITTED: (
        consolidation.CrossRunConsolidationCommittedPayload
    ),
    EventType.ARCHIVE_SEGMENT_MOVED: consolidation.ArchiveSegmentMovedPayload,
    EventType.DIGEST_SUPERSEDED: consolidation.DigestSupersededPayload,
    EventType.RETENTION_POLICY_CHANGED: consolidation.RetentionPolicyChangedPayload,
    # ─── capsule ───
    EventType.CAPSULE_COMPILED: capsule.CapsuleCompiledPayload,
    EventType.CAPSULE_INJECTION_FAILED: capsule.CapsuleInjectionFailedPayload,  # T-L3kc-06
    EventType.CAPSULE_ASSEMBLY_FAILED: capsule.CapsuleAssemblyFailedPayload,  # T-L0-16 (波1)
    # ─── envelope ───
    EventType.TRANSACTION_ENVELOPE_SUBMITTED: envelope.TransactionEnvelopeSubmittedPayload,
    EventType.NODE_TOUCHED: envelope.NodeTouchedPayload,
    # ─── commit ───
    EventType.COMMIT_ACCEPTED: commit.CommitAcceptedPayload,
    EventType.COMMIT_REJECTED: commit.CommitRejectedPayload,
    EventType.DRIFT_DETECTED: commit.DriftDetectedPayload,
    EventType.OWNER_GUARD_VIOLATION: commit.OwnerGuardViolationPayload,  # T-L3kc-03
    # T-FIX-B4-05 (INT-content#1/PARALLEL-locks#2) — admin-bypass 旗标受控产生/撤销留痕。
    # `towow guard admin-bypass` CLI 是唯一生产者且 emit 即 conforming → ENFORCED (fail-closed),
    # 不进 EXEMPT (同 OwnerGuardViolation / SemanticConflictDetected 范式)。
    EventType.GUARD_ADMIN_BYPASS_GRANTED: commit.GuardAdminBypassGrantedPayload,
    EventType.GUARD_ADMIN_BYPASS_REVOKED: commit.GuardAdminBypassRevokedPayload,
    # owner-confirm@v1 (组件6) — 不可伪造 owner 授权载体, 带 Ed25519 detached 签名。唯一生产者 =
    # `towow owner-confirm grant` (先验签再 emit) → ENFORCED (同 GuardAdminBypass 范式, 不进 EXEMPT)。
    EventType.OWNER_CONFIRMATION_GRANTED: commit.OwnerConfirmationGrantedPayload,
    # A6 哨兵正经源 (irreversible-action-blocked-audit-event@v1) — PhysicalGate DENY 高危不可逆动作时
    # best-effort emit 上看板。唯一生产者 = DefaultPhysicalGate.check_and_emit (DENY 路径), emit 即
    # conforming → ENFORCED (同 OwnerGuardViolation / GuardAdminBypass 范式, 不进 EXEMPT)。
    EventType.IRREVERSIBLE_ACTION_BLOCKED: commit.IrreversibleActionBlockedPayload,
    # ─── finding ───
    EventType.FINDING_CREATED: finding.FindingCreatedPayload,
    EventType.FINDING_VERIFIED: finding.FindingVerifiedPayload,
    EventType.FINDING_DISPUTED: finding.FindingDisputedPayload,
    EventType.FINDING_RESOLVED: finding.FindingResolvedPayload,
    EventType.FINDING_ACCEPTED: finding.FindingAcceptedPayload,
    # f-stale-closure-contract-permanently-unclosable — 陈旧 closure_contract 的修订通道。唯一生产者 =
    # M-1.5 `review finding-contract-amend` (走 supersede + NoveltyCheck), emit 即 conforming
    # → ENFORCED (不进 EXEMPT, 同 FindingDisputed 范式)。
    EventType.FINDING_CLOSURE_CONTRACT_AMENDED: finding.FindingClosureContractAmendedPayload,
    # ─── review_plan (M-1.5 §3.1 Patch Z, RUN-035 T-L1-50) ───
    EventType.REVIEW_PLAN_CREATED: review_plan.ReviewPlanCreatedPayload,
    # T-RMD-S2-REVFIX (M15-F1) — review 维度 proof-of-work; author-time driver 唯一生产者 + emit 即
    # conforming → ENFORCED (fail-closed, 不进 EXEMPT), 同 ReviewPlanCreated 范式。
    EventType.REVIEW_DIMENSION_EXERCISED: review_plan.ReviewDimensionExercisedPayload,
    # ─── detection_rule ───
    EventType.DETECTION_RULE_PROPOSED: detection_rule.DetectionRuleProposedPayload,
    EventType.DETECTION_RULE_SHADOWING: detection_rule.DetectionRuleShadowingPayload,
    EventType.DETECTION_RULE_PROMOTED: detection_rule.DetectionRulePromotedPayload,
    EventType.DETECTION_RULE_RETIRED: detection_rule.DetectionRuleRetiredPayload,
    # ─── obligation ───
    EventType.OBLIGATION_CAPTURED: obligation.ObligationCapturedPayload,
    EventType.OBLIGATION_ACTIVATED: obligation.ObligationActivatedPayload,
    EventType.OBLIGATION_CHECKED: obligation.ObligationCheckedPayload,
    EventType.OBLIGATION_VIOLATED: obligation.ObligationViolatedPayload,
    EventType.OBLIGATION_EVOLVED: obligation.ObligationEvolvedPayload,
    EventType.OBLIGATION_RETIRED: obligation.ObligationRetiredPayload,
    # ─── validation (M-3.4 §3.3, RUN-058) ───
    # validation runner 是唯一生产者 + emit 即 conforming → 不进 EXEMPT, 留在 ENFORCED (fail-closed)。
    EventType.VALIDATION_SCENARIO_RUN: validation_scenario_run.ValidationScenarioRunPayload,
    # ─── migration (M-3.3 §5.2/§5.3/§11.1, RUN-066) ───
    # 迁移工具是唯一生产者 + emit 即 conforming → 不进 EXEMPT, 留在 ENFORCED (fail-closed)。
    EventType.MIGRATION_RUN_STARTED: migration.MigrationRunStartedPayload,
    EventType.MIGRATION_STEP_RECORDED: migration.MigrationStepRecordedPayload,
    EventType.MIGRATION_BATCH_SUBMITTED: migration.MigrationBatchSubmittedPayload,
    EventType.MIGRATION_RUN_COMPLETED: migration.MigrationRunCompletedPayload,
}


# Import-time completeness invariant (verify_dep "映射真覆盖全 event_type"):
# adding an EventType without a payload schema is a fail-closed condition surfaced at import,
# not a silent gap. test_registry_consistency.py asserts the same so it can never regress.
_MISSING = set(EventType) - set(PAYLOAD_REGISTRY)
if _MISSING:  # pragma: no cover - guards against an unmapped event_type being introduced
    raise RuntimeError(
        "PAYLOAD_REGISTRY is incomplete — every EventType must map to a typed payload schema "
        f"(M-0.1 §5.3.1). Missing: {sorted(et.value for et in _MISSING)}",
    )


# ════════════════════════════════════════════════════════════════════════════════
#  Enforcement boundary — fail-closed by default, exempt only known-broken producers
# ════════════════════════════════════════════════════════════════════════════════
#
# A fact-source integrity ruler should be fail-closed BY DEFAULT — an unknown / untested
# event_type must be VALIDATED, not waved through. So enforcement = every registered type
# MINUS the explicit exempt set below.
#
# PAYLOAD_VALIDATION_EXEMPT are the event_types whose CURRENT canonical producer is measured
# (RUN-031 T-L0-01, full test suite shadow run — see docs/DOGFOOD-RUN-031-FINDINGS.md §T-L0-01)
# to emit at least one payload that does NOT conform to the registered schema. Hard-enforcing
# those would reject the system's own legitimate writes, so they are exempted and their
# producer-alignment work is tracked as DebtRegistered (dependency_blocked) — not silently
# skipped-and-pretended-done. Each exempt entry names the task that will bring the producer
# into conformance, after which it is deleted from this set and graduates to fail-closed.
PAYLOAD_VALIDATION_EXEMPT: frozenset[EventType] = frozenset(
    {
        # ── producer emits a richer/different shape than the registered schema ──
        # M-0.1 §3.6 summary schema vs M-0.4 full envelope producer → T-L0-24/25 (batch 0-C)
        EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        # ObligationCaptured payload lacks capture_source/mechanically_checkable producer fields
        # → T-L0-32 (ObligationCaptured provenance payload fields)
        EventType.OBLIGATION_CAPTURED,
        # ── stub-rewrap: foreign event wrapped via kind/stub_original_payload ──
        # NodeTouched still stub-rewraps some session/checkpoint events → stub-rewrap cleanup
        EventType.NODE_TOUCHED,
        # NatureJudgmentCaptured CLI still carries a `kind` discriminator → stub-rewrap cleanup
        EventType.NATURE_JUDGMENT_CAPTURED,
        # ConceptCreated brief-seed path emits a `kind`-carrying variant → T-L1-02 seed shape fix
        EventType.CONCEPT_CREATED,
        # ── discriminator / required-field mismatch in current producer ──
        # ConceptEdgeAdded some emits set target_entity_type=concept (not concept_edge)
        EventType.CONCEPT_EDGE_ADDED,
        # TaskNodeCreated emits read_set/write_set inline (schema forbids) → payload alignment
        EventType.TASK_NODE_CREATED,
        # EngineeringConsensusFreezed emits target_entity_type=concept → T-L1-09 freeze payload
        EventType.ENGINEERING_CONSENSUS_FREEZED,
        # PlanFreezed emits the §3.8 checks shape, not the registered PlanFreezedPayload top shape
        EventType.PLAN_FREEZED,
        # InterviewBriefPublished CLI emits a `kind`-stub variant alongside the canonical payload
        EventType.INTERVIEW_BRIEF_PUBLISHED,
        # LockReleased gate-internal emit uses target_entity_id='unknown' placeholder
        EventType.LOCK_RELEASED,
        # SnapshotCreated producer carries bundle_hash/extra fields the schema forbids
        EventType.SNAPSHOT_CREATED,
        # DriftDetected gate-internal emit uses a drift_type the schema does not model
        EventType.DRIFT_DETECTED,
        # AuditTriggered one emit path omits after_state wrapper
        EventType.AUDIT_TRIGGERED,
        # CommitAccepted one gate path emits a NodeTouched-shaped sentinel placeholder
        EventType.COMMIT_ACCEPTED,
        # ── prod-only producers (not exercised by the test suite; caught via live-log scan) ──
        # ConsumerListPublished CLI emits target_entity_type=concept (not consumer_list) →
        # T-L0-09 consumer_relation reducer + producer discriminator alignment
        EventType.CONSUMER_LIST_PUBLISHED,
        # AtReferenceAdded CLI emits an after_state without reference_id/source_entity →
        # T-L1-12/T-L0-08 typed @ locator + at_reference payload alignment
        EventType.AT_REFERENCE_ADDED,
        # ConceptStateMachineDefined CLI wraps the canonical payload in a state_transition
        # envelope {target_entity_type, transition_type, after_state: <payload>} (same shape as
        # its concept_graph siblings ConceptCreated / ConceptEdgeAdded), so the top-level dict
        # does not match the bare ConceptStateMachineDefinedPayload top shape. T-L1-10 moves the
        # real fail-closed enforcement to validate_state_machine_syntax at the CLI (§6.1 6 条 +
        # §6.3 可达性) — a stronger gate than top-level shape conformance.
        EventType.CONCEPT_STATE_MACHINE_DEFINED,
    },
)

PAYLOAD_VALIDATION_ENFORCED: frozenset[EventType] = frozenset(EventType) - PAYLOAD_VALIDATION_EXEMPT
"""Fail-closed set = all registered event_types minus PAYLOAD_VALIDATION_EXEMPT (RUN-031 T-L0-01)."""


class PayloadSchemaValidationError(ValueError):
    """Raised at the write boundary when an event payload does not match its event_type schema.

    M-0.1 §5.3.1 — the fact source must not accept a payload whose shape contradicts the
    event_type it claims to be (e.g. a ConceptCreated carrying a TaskNode payload).
    """

    def __init__(self, event_type: EventType, cause: ValidationError | str) -> None:
        self.event_type = event_type
        self.cause = cause
        detail = cause if isinstance(cause, str) else str(cause)
        super().__init__(
            f"payload does not match schema for event_type={event_type.value} "
            f"({PAYLOAD_REGISTRY[event_type].__name__}): {detail}",
        )


def conforms(event_type: EventType, payload: dict[str, Any]) -> ValidationError | None:
    """Return the ValidationError if ``payload`` does not match ``event_type``'s schema, else None.

    Non-strict (payload is a JSON-derived dict — enums arrive as strings). Pure check, no raise.
    """
    schema = PAYLOAD_REGISTRY.get(event_type)
    if schema is None:  # pragma: no cover - registry completeness is asserted at import
        return None
    try:
        schema.model_validate(payload, strict=False)
    except ValidationError as exc:
        return exc
    return None


def validate_event_payload(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    shadow_sink: list[tuple[EventType, ValidationError]] | None = None,
) -> None:
    """Write-boundary dispatch validation (M-0.1 §5.3.1).

    Looks up the typed payload schema for ``event_type`` and validates ``payload`` against it.

    Enforcement is graduated (see module docstring):
      - ``event_type`` in ``PAYLOAD_VALIDATION_ENFORCED`` → raise ``PayloadSchemaValidationError``
        on mismatch (fail-closed; the write is rejected).
      - otherwise → not yet fail-closed (producer-alignment debt is tracked); the mismatch is
        recorded into ``shadow_sink`` when provided (used by the conformance measurement /
        `TOWOW_PAYLOAD_SHADOW` diagnostic) and never blocks the write.

    A conforming payload always passes for every registered type.
    """
    err = conforms(event_type, payload)
    if err is None:
        return
    if event_type in PAYLOAD_VALIDATION_ENFORCED:
        raise PayloadSchemaValidationError(event_type, err)
    if shadow_sink is not None:
        shadow_sink.append((event_type, err))


__all__ = [
    "PAYLOAD_REGISTRY",
    "PAYLOAD_VALIDATION_ENFORCED",
    "PAYLOAD_VALIDATION_EXEMPT",
    "PayloadSchemaValidationError",
    "conforms",
    "validate_event_payload",
]
