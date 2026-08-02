"""Spec-faithful enumerations for v3 event log + obligation system + escalation lifecycle.

Step 1.a of Phase E.1 schema-first sequence. No runtime logic — only StrEnum classes.

# spec source (canonical, frozen at Phase D):
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.1 EventBase (actor_type 7 / base_classification 3 / novelty_type 4)       (L60..L100)
#     §2.2 10 EventCategory + base_classification 默认                              (L102..L118)
#     §2.3.1 StateTransitionFields (target_entity_type 9, transition_type 5)       (L121..L135)
#     §2.3.2 SemanticJudgmentFields (judgment_type 11, evidence source_type 5)     (L137..L149)
#     §2.3.3 SnapshotFields (snapshot_type 3)                                      (L151..L159)
#     §2.3.4 ConsolidationFields (digest_type 3)                                   (L161..L170)
#     §2.3.5 CapsuleFields (scene_type 9)                                          (L172..L184)
#     §2.3.6 EnvelopeFields (active_obligations_declared.status 3)                 (L186..L200)
#     §2.3.7 CommitFields (verdict 2, rejection_type 7)                            (L202..L213)
#     §2.3.8 FindingFields (severity 4, lifecycle_state 4, detection_method 3)     (L215..L226)
#     §2.3.9 DetectionRuleFields (rule_lifecycle_state 6)                          (L228..L237)
#     §2.3.10 ObligationFields (obligation_lifecycle_state 6, event-level)         (L239..L252)
#     §3.11 event 类型总览表 — 59 base event_type                                  (L1009..L1025)
#
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §2.1 Obligation canonical schema
#       (nature 5 / severity 3 / scope_type 5 / attached_to entity_type 5 /
#        capture_source 6 / checker pattern_type 3)                                (L53..L121)
#     §3.1 三态 canonical lifecycle (active/superseded/retired)                    (L172..L191)
#
#   05-l2-maintenance/M-2.3-candidate-curation-lifecycle-detailed-design.md
#     §3.1 HistoricalPatternSurfaceCandidate (grouping_dimension 5)                (L157..L210)
#     §3.2 EscalationTriaged (triage_category 8, expected_nature_action 5)         (L221..L270)
#     §3.3 EscalationResolved (resolution 7, no "other" fallback)                  (L279..L317)
#     §3.4 EscalationLearningCaptured (learning_type 4, judgment_type=esc_learning)(L325..L363)
#     §3.5 EscalationDecisionMade
#       (decided_state 4, decision_made_by 2, judgment_type=esc_decision)          (L372..L414)
#
#   07-process-handoffs/PHASE-D-REVERSE-CONTRIBUTION-LOG.md
#     §1.2 EventType Phase D 新增 (event_type registry +8)                          (L44..L58)
#     §1.3 target_entity_type Phase D 新增 4                                       (L60..L70)
#     §1.4 finding_kind 累积 9                                                     (L72..L80)
#     §2.2 escalation_lifecycle 4 态                                               (L99..L130)
#
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#     §11.x M-0.5a Patch D / Patch 3: LockReleased registered into M-0.1
#       state_transition class                                                      (L1219..L1391)
#
# ─── E.1.1 收束的 spec conflict canonical decisions ───
#   详见 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md
#
#   Conflict 1 (resolved): EscalationDecisionMade name 已在 M-0.1 §3.11 base 59 之内。
#     Phase D Patch M-2.3-C 补全其 schema (M-2.3 §3.5)。
#     "补全 schema" ≠ "新增 name"。EventType 67 成员 =
#       59 base (含 EscalationDecisionMade name) + 7 Phase D real-new +
#       1 M-0.5a Patch D LockReleased.
#
#   Conflict 2 (resolved): ActorType closed enum 9 个 canonical 值
#     (7 from M-0.1 §2.1 + daemon + agent_session per E.1.1 narrow patch).
#     daemon / agent_session 是 v3 运行期真实出现的新角色 — provenance 语义边界
#     强制实施 (daemon → daemon_name; agent_session → session_id+skill_id;
#     agent_fork → parent_session_id+fork_name)。Provenance/ProvenanceHint
#     pydantic model_validator 强制。
#
#   Conflict 3 (resolved): DetectionRuleLifecycleState 按
#     canonical_emit (5) / deprecated_alias (1) / reserved_not_emitted (1) 三类组织.
#     canonical_emit: proposed / shadow / active_warning / enforced / retired
#     deprecated_alias: warning → normalize → active_warning (parser 接受, emitter 不输出)
#     reserved_not_emitted: revised (registry 保留, 当前状态机不 emit)
#     lifecycle module (M-2.3 §4.1) wins semantic meaning, M-0.1 §2.3.9 同步.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class EventCategory(StrEnum):
    """10 event categories per M-0.1 §2.2."""

    STATE_TRANSITION = "state_transition"
    SEMANTIC_JUDGMENT = "semantic_judgment"
    SNAPSHOT = "snapshot"
    CONSOLIDATION = "consolidation"
    CAPSULE = "capsule"
    ENVELOPE = "envelope"
    COMMIT = "commit"
    FINDING = "finding"
    DETECTION_RULE = "detection_rule"
    OBLIGATION = "obligation"


@unique
class BaseClassification(StrEnum):
    """Three-tier event classification per M-0.1 §2.1.

    immutable_truth: Nature judgment / red-line obligation / concept definition — never droppable.
    abstractable_process: task execution log / resolver verdict / commit history — digestable on archive.
    discardable_noise: ephemeral debug / heartbeat — droppable on archive.
    """

    IMMUTABLE_TRUTH = "immutable_truth"
    ABSTRACTABLE_PROCESS = "abstractable_process"
    DISCARDABLE_NOISE = "discardable_noise"


@unique
class ActorType(StrEnum):
    """Provenance actor_type — 9 canonical values per E.1.1 LEDGER Conflict 2.

    M-0.1 §2.1 base 7 + E.1.1 narrow patch adds daemon + agent_session.

    Provenance 语义边界 (enforced by ProvenanceHint model_validator in event_intent.py):
      daemon         → daemon_name required (e.g. "m21.cascade_daemon")
      agent_session  → session_id + skill_id required
      agent_fork     → parent_session_id + fork_name required
      其他 6 → actor_id 必填 (无额外约束)
    """

    AGENT_FORK = "agent_fork"
    COMMIT_GATE = "commit_gate"
    SNAPSHOT_MODULE = "snapshot_module"
    CAPSULE_ASSEMBLER = "capsule_assembler"
    RESOLVER = "resolver"
    SYSTEM = "system"
    NATURE = "nature"
    DAEMON = "daemon"  # E.1.1 narrow patch — provenance must include daemon_name
    AGENT_SESSION = "agent_session"  # E.1.1 narrow patch — provenance must include session_id+skill_id


@unique
class SupersedeNoveltyType(StrEnum):
    """EventBase.supersede.novelty_type per M-0.1 §2.1 (4 types).

    E.1.1 Conflict 5 (resolved): renamed from `NoveltyType` to distinguish from
    `PatchNoveltyType` (envelope-level, 5 values). See SPEC-CONFLICT-RESOLUTION-LEDGER.md.

    SupersedeNoveltyType is **event-level** — "为什么这个 event supersede 旧 event"
    (supersede 链元数据).
    """

    NEW_EVIDENCE = "new_evidence"
    CORRECTED_ERROR = "corrected_error"
    SCOPE_CHANGE = "scope_change"
    NATURE_OVERRIDE = "nature_override"


# Backward-compat alias — to be removed after E.2.
NoveltyType = SupersedeNoveltyType


@unique
class EventType(StrEnum):
    """v3 event_type registry.

    Total 71 members =
      59 base (M-0.1 §3.11, 含 EscalationDecisionMade name) +
      7 real-new Phase D event_types +
      1 M-0.5a Patch D LockReleased registered into state_transition +
      3 RUN-003 GOAL mode (GoalSessionStarted / Terminated / GoalEscalationRaised) +
      1 RUN-017C Patch Q-interview-brief-canonical-emit-1 (InterviewBriefPublished).
    (This historical breakdown drifted as later events were registered without refreshing
    the tally; the live len(EventType) is the asserted truth in
    tests/unit/test_registry_consistency.py. T-DEC-1 adds TaskNodeClosed — the
    done-elsewhere closure terminal, done-elsewhere-task-closure@v1.)
    """

    # ─── state_transition (21 base) — M-0.1 §3.1 ───
    CONCEPT_CREATED = "ConceptCreated"
    CONCEPT_EDGE_ADDED = "ConceptEdgeAdded"
    CONCEPT_EDGE_REMOVED = "ConceptEdgeRemoved"
    CONCEPT_STATE_TRANSITION = "ConceptStateTransition"
    CONCEPT_GRAPH_PROPOSAL = "ConceptGraphProposal"
    TASK_NODE_CREATED = "TaskNodeCreated"
    TASK_DEPENDENCY_EDGE_ADDED = "TaskDependencyEdgeAdded"
    # T-FIX-B6-01 (PLAN-seam#1): task 图纠错原语。错向/假依赖边一旦 emit 永久污染 plan;
    # 撤边事件让 planner 撤一条边而无需整盘换 plan_id。event-sourced 撤边不物理删 ADDED。
    TASK_DEPENDENCY_EDGE_REMOVED = "TaskDependencyEdgeRemoved"
    # done-elsewhere-task-closure@v1 (T-DEC-1): task 生命周期一等终态原语 (与 TaskNodeCreated /
    # TaskDependencyEdgeRemoved 并列的 task 图原语)。把一个已在别处做完的冻结计划任务诚实标记为
    # 终态 closed —— 不是 TaskRunOutcome 的某个值, 也不是 abort (abort=没做完会重派; closure=已在
    # 别处做完, 终态, 永不重派)。零-diff 的 done-elsewhere 任务无法诚实产 TaskRunCompleted(success)
    # (success 需本 run 真 commit), 这正是 done-elsewhere 重派死循环的根因。
    TASK_NODE_CLOSED = "TaskNodeClosed"
    # owner-gate-clearance@v1 (autopilot-owner-presence-removal): owner 经合法机制 (显式 CLI +
    # 过 commit gate) 解除某 task 的 owner-gate 的 canonical 事件。是 owner 的显式受控决定的持久化,
    # 【不】是"自动放行开关"(autopilot 不能自 emit 绕自己的红线; 本事件由 owner/主会话经 CLI 发)。
    # 编排器派发层 (_task_owner_gate) + task_graph 投影读它 → 已解 gate 的 task 可进 ready-set 被派。
    TASK_NODE_OWNER_GATE_CLEARED = "TaskNodeOwnerGateCleared"
    TASK_READ_SET_CLAIMED = "TaskReadSetClaimed"
    TASK_WRITE_SET_CLAIMED = "TaskWriteSetClaimed"
    TASK_MODEL_TIER_ASSIGNED = "TaskModelTierAssigned"
    TASK_PACKAGE_PUBLISHED = "TaskPackagePublished"
    TASK_RUN_COMPLETED = "TaskRunCompleted"
    INFORMATION_NEED_CREATED = "InformationNeedCreated"
    INFORMATION_NEED_STATUS_CHANGED = "InformationNeedStatusChanged"
    AT_REFERENCE_ADDED = "AtReferenceAdded"
    AT_REFERENCE_REMOVED = "AtReferenceRemoved"
    # M-1.2 §4.3 失效响应辅助 event (T-L1-13, Patch S) — 引用目标 supersede 时按 policy emit。
    AT_REFERENCE_AUTO_UPDATED = "AtReferenceAutoUpdated"  # auto_accept_latest 策略
    AT_REFERENCE_TARGET_SUPERSEDED = "AtReferenceTargetSuperseded"  # pin_to_snapshot / explicit_decision_required
    PATCH_PROPOSED = "PatchProposed"
    FIX_PROPOSED = "FixProposed"
    FIX_COMPLETED = "FixCompleted"
    CONSUMER_LIST_PUBLISHED = "ConsumerListPublished"
    ENGINEERING_CONSENSUS_FREEZED = "EngineeringConsensusFreezed"

    # ─── state_transition Phase D 新增 6 ───
    INVALIDATION_CASCADE = "InvalidationCascade"  # Patch M-2.1-A
    DAEMON_RUN_COMPLETED = "DaemonRunCompleted"  # Patch M-2.1-A
    # T-L3kc-04 (波1): orchestrator spawn retry 耗尽审计 — 行为早真, 此前 emit 走 NodeTouched
    # 假名, 注册真 EventType 后 emit canonical (M-3.1 §7.6)。
    ORCHESTRATOR_DISPATCH_FAILED = "OrchestratorDispatchFailed"
    # T-RMD-S3-REAPER (根治 f-sub-atomic-claim-no-reaper): exec spawn 原子认领 (.claim) 的 reaper
    # 回收一个泄漏/过期 claim 时 emit。崩溃泄漏的 claim 无 reaper = 永久饿死; 回收留痕让被饿死 task
    # 可重派 + 上看板。daemon-internal self-observation, 走 path-B (write_direct), 同
    # ORCHESTRATOR_DISPATCH_FAILED / PlanSessionLockReaped 范式。新事件类型 = 新增能力客观签名。
    EXEC_CLAIM_REAPED = "ExecClaimReaped"
    # 哨兵 A3 空转源 (reconcile-cycle-count-emission@v1): 每轮 level-triggered reconcile pass 收尾
    # 发布五计数 (watermark_before/after / dispatched_count / active_session_count / action_count),
    # 供哨兵 A3 空转探测 (detect_a3_reconcile: 水位涨但 0 真派发 0 活会话 = daemon 吃自己心跳空转)。
    # daemon-internal self-observation, 同 ORCHESTRATOR_DISPATCH_FAILED — 走 path-B (write_direct),
    # 非被审计的域 patch。dispatched_count 只计真前进派发 (execution spawn + replan + dead-letter
    # triage), 排除 daemon 吃自己 DaemonRunCompleted 心跳 (INV-SENT-A3-NO-HEARTBEAT)。
    RECONCILE_CYCLE_PUBLISHED = "ReconcileCyclePublished"
    # T-RMD-S5-OBSERVER (根治 f-turnon-sentinel-blind-silent-swallow, critical): 哨兵 A1-A8 安全四不变量
    # 观测 pass 一轮的 liveness/failure 自观测件 —— 把"睁眼了"(pass 真跑过) 与"抓到了"(FindingCreated)
    # 拆成两个独立信号。SentinelPassCompleted = 每轮无条件 emit 的 liveness (扫了几条候选/emit 几条 finding,
    # 即便全 0); SentinelPassFailed = 哨兵 pass 整轮崩时 emit (不静默吞, 让失明可见, 对齐维护 daemon scan 崩
    # 走 _failed_daemon_iteration emit DaemonRunCompleted(failed) 的范式)。daemon-internal self-observation,
    # 走 path-B (write_direct), 非被审计的域 patch (同 ReconcileCyclePublished / DaemonRunCompleted)。
    # 不在 FORWARD_CHAIN_REGISTRY → 非前进链触发燃料 (不自喂心跳); 非 dispatch → 不计入 A3 dispatched_count。
    SENTINEL_PASS_COMPLETED = "SentinelPassCompleted"  # noqa: S105 — 事件名, "Pass"=哨兵巡检轮非密码
    SENTINEL_PASS_FAILED = "SentinelPassFailed"  # noqa: S105 — 事件名, "Pass"=哨兵巡检轮非密码
    # T-L3kc-01 (波1): towow init 的"项目已初始化"标记 — 此前 emit 裸 NodeTouched(noise) 假名,
    # 注册真 EventType 后 emit canonical 非噪声标记 (M-3.1 §3.3 step3)。
    PROJECT_INITIALIZED = "ProjectInitialized"
    OBLIGATION_RETIRE_CANDIDATE = "ObligationRetireCandidate"  # Patch M-2.2-A
    HISTORICAL_PATTERN_SURFACE_CANDIDATE = "HistoricalPatternSurfaceCandidate"  # Patch M-2.3-A
    ESCALATION_TRIAGED = "EscalationTriaged"  # Patch M-2.3-A
    ESCALATION_RESOLVED = "EscalationResolved"  # Patch M-2.3-A
    # M-1.6 §3.6 / §10.2 Patch X — fix 修不动升级给 Nature 产品决策 (F-09b 产品语言)。
    # ≠ GoalEscalationRaised (后者是 goal session pause/resume 机制); 二者正交 (M-1.6 §3.6 注)。
    ESCALATION_RAISED = "EscalationRaised"  # M-1.6 §3.6 Patch X (RUN-036 T-L1-70)

    # ─── state_transition M-0.5a Patch D / Patch 3 ───
    LOCK_RELEASED = "LockReleased"  # Patch M-0.5a-3: ownership lifecycle, registered to M-0.1

    # ─── state_transition K2b-REG (50-graph-protocol §TG.4) — 图协议三类新节点的物化事件 ───
    # 血缘真兑现 (parent ~0% → 真): spawn 真发生那刻 emit SessionSpawned (独立事件 = 结构性强制,
    # 不是给现有事件补可选 parent 字段)。LockAcquired 让锁的获取也留痕上图 (LockReleased 已存在,
    # 但其 payload 是 task/run/entities 形, 无 lock_id — 见 K2b-REG 交回的 lock-release 缺口)。
    # node_reducers.reduce_node_events 消费这两类 → session_graph.json / lock_graph.json。
    SESSION_SPAWNED = "SessionSpawned"  # K2b-REG: 父 spawn 子, after_state 带 parent_session_id 连 spawned 边
    LOCK_ACQUIRED = "LockAcquired"  # K2b-REG: 锁获取留痕, after_state 带 lock_id/session_id/resource 连 held-by 边

    # ─── state_transition DOGFOOD-RUN-003 P-12 GOAL mode ───
    GOAL_SESSION_STARTED = "GoalSessionStarted"  # RUN-003 / P-12: agent autonomous run a stretch
    GOAL_SESSION_TERMINATED = "GoalSessionTerminated"  # RUN-003 / P-12: GO ends (completion or escalation)
    GOAL_ESCALATION_RAISED = "GoalEscalationRaised"  # RUN-003 / C-07: bg session pauses, awaits owner NJ
    ESCALATION_ANSWER_APPLIED = "EscalationAnswerApplied"  # R08: owner 答复真送回等待会话并被消费的硬闭环留痕

    # ─── state_transition RUN-017C Patch Q-interview-brief-canonical-emit-1 (M-1.1 §5.4) ───
    # M-1.1 brief publish 终点 — canonical InterviewBriefPublished event. 在 RUN-017C 之前
    # CLI 用 NATURE_JUDGMENT_CAPTURED stub-rewrap 假装发 brief (payload.kind). 现真注册
    # 让 M-1.1 brief 不再装样子. 详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch Q.
    INTERVIEW_BRIEF_PUBLISHED = "InterviewBriefPublished"

    # ─── state_transition RUN-027 块4 (角色走正规通道) — L1 session-start checkpoints ───
    # M-1.1 §5.1 / M-1.2 §13.12 / M-1.3 §13: 三个 L1 session-start 事件 spec 早已定义, 但
    # enum 缺类型 → CLI 一直用 NATURE_JUDGMENT_CAPTURED stub-rewrap 成 NodeTouched (Path B
    # 后门) 装样子。块4 现真注册 + 走 Path A commit gate (spec R3: L1 skill 不拿 Path B 权限),
    # 跟 INTERVIEW_BRIEF_PUBLISHED 同款 "现真注册" 修法。payload 保留 kind 字段 (canonical
    # payload schema 本身含 kind), 向后兼容既有 _find_by_kind finder。
    INTERVIEW_SESSION_STARTED = "InterviewSessionStarted"
    ENGINEERING_CONSENSUS_SESSION_STARTED = "EngineeringConsensusSessionStarted"
    PLANNER_SESSION_STARTED = "PlannerSessionStarted"

    # ─── state_transition RUN-018-F11 sub-skill invocation audit (M-1.1 §7) ───
    # F11 fix (DOGFOOD-RUN-002 dogfood review surface): agent 决定不调 sub-skill 时该
    # emit 留痕 (sub_skill_id + decision_rationale + triggering_condition_evaluation).
    # 未来回头审, 知道是真没触发条件 还是 agent 忘了. 详 F11 closure_contract.
    SUB_SKILL_INVOCATION_SKIPPED = "SubSkillInvocationSkipped"

    # ─── state_transition RUN-018-F7 plan freeze fail-closed 终态门 (M-1.3 §3.8) ───
    # F7 (review 根因 fix): plan CLI 加独立 freeze 命令 + 5 项 blocking_check 评估 +
    # atomic emit PlanFreezed; reject freeze if 任一 fail. 修了 F7 → 大半 走捷径 (F8/F9/
    # F10) 自动 enforce. 详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER (留 task #106) +
    # closure_contract F7.
    PLAN_FREEZED = "PlanFreezed"

    # ─── state_transition T-L1-31 (M-1.3 §3.7 + §14.1.b) planning uncertainty ───
    # 计划期不确定性 (red_line 阻塞 freeze / advisory 记录)。target_entity_type=concept (plan as
    # concept, 同 PlanFreezed 约定)。整机制此前 0 代码。
    PLANNING_UNCERTAINTY = "PlanningUncertainty"

    # ─── state_transition R08-T1 plan zombie-lock reap (vitality-gated) ───
    # 死 planner 会话留下的 zombie plan 锁卡死后续 `plan start` (串行单飞门)。
    # `towow plan reap-stale-session` 用 vitality 机器裁决该会话死活, 仅 dead 才释放锁并
    # emit 本留痕 (vitality 证据 + provenance)。唯一生产者 = 该 CLI。
    PLAN_SESSION_LOCK_REAPED = "PlanSessionLockReaped"

    # ─── state_transition RUN-018-F5 concept state machine defined (M-1.2 §3.3) ───
    # F5 fix: 共识棒每 concept 应有 state machine 定义. 之前 0 emit (CLI 无 --state-machine
    # 参数). 加 EventType + CLI option + sequential emit (v3 initial; atomic envelope 留 future).
    CONCEPT_STATE_MACHINE_DEFINED = "ConceptStateMachineDefined"

    # ─── state_transition RUN-038 加固(2) (T-L1-38/T-L1-39, debt-0337d1ae14f7) — M-1.4 §3.2/§3.3 ───
    # 执行侧 mismatch 检出 (T-L1-38) / 处理决定 (T-L1-39) 的 canonical 源。之前 no_unhandled_mismatch
    # 门逻辑真 fail-closed, 但生产无人 emit MismatchDetected → 永远扫空集 return True (vacuous,
    # RUN-032 §7 裁定3: 尺子逻辑对但没接进生产路径)。现真注册 EventType + payload + work mismatch /
    # work mismatch-resolve CLI emit, 让门在真实执行路径 fire。target_entity_type=mismatch。
    # (v2.1 cleanup / T-L1-46: MismatchForkSpawned + MismatchVerdict event 已删 — mismatch-judge
    #  fork 已删, 功能并入 MismatchResolutionDecided / AdvisorVerdictDelivered。)
    MISMATCH_DETECTED = "MismatchDetected"
    MISMATCH_RESOLUTION_DECIDED = "MismatchResolutionDecided"

    # ─── state_transition RUN-038 波2 (T-L1-40) — M-1.4 §3.5 advisor-consult 协议 ───
    # executor 在执行中遇到需独立 OPUS 决策者裁决的不确定 (mismatch / self-check 疑虑 / 实现选择 /
    # done_criteria 模糊 / verdict evidence scope breach) 时, 经 advisor-consult fork 走的事件链起点。
    # AdvisorConsultRequested (T-L1-40) → advisor fork 给 AdvisorVerdictDelivered (本条, T-L1-49)。
    # AdvisorVerdictDelivered v2.1 独立为通用 advisor verdict event (不再跟已删 MismatchVerdict 共用
    # schema), 必带 evidence_scope_summary (v2.1 evidence scope binding)。
    # target_entity_type=advisor_consult (T-L1-48 波1 Narrow Patch Y 已注册)。
    ADVISOR_CONSULT_REQUESTED = "AdvisorConsultRequested"
    ADVISOR_VERDICT_DELIVERED = "AdvisorVerdictDelivered"

    # ─── state_transition RUN-038 波3 (T-L1-42 / T-L1-71) — RePlanTriggered ───
    # F-14 re-plan 衔接的真事件源。M-1.3 §14.2 是 schema 定稿 owner (target_entity_type=plan);
    # M-1.4 (§3.8, T-L1-42) + M-1.6 (§3.4, T-L1-71) + M-2.1 都是产生方。此前 grep 全 src 零
    # RePlan, 多份 spec (M-1.4 §9 L754 / M-1.6 §3.4 注) 谎称 "M-0.1 已注册" — 实际无任何东西能
    # emit 真 canonical RePlanTriggered (0 历史真事件, 核于 RUN-038 波3)。波3 现真注册 EventType +
    # payload + trigger 枚举 (并入三份 spec 的 trigger_source 场景), 走 Path A commit gate。
    # payload-shape 统一裁定见 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 12。
    RE_PLAN_TRIGGERED = "RePlanTriggered"

    # ─── semantic_judgment (11 base) — M-0.1 §3.2 + §3.11 总览 ───
    # (v2.1 cleanup / T-L1-46: 原 12 base 含 MismatchVerdict event; M-1.4 §3.4 v2.1 明令删除——
    #  mismatch-judge fork 已删, MismatchVerdict 失去 owner, 功能合并进 MismatchResolutionDecided
    #  (executor 自决) / AdvisorVerdictDelivered (经 advisor)。删 EventType + payload + JudgmentType
    #  值 + registry。0 历史真 MismatchVerdict 事件 (核于 RUN-038 波2) → 删不破坏重放。
    #  详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 10。)
    NATURE_JUDGMENT_CAPTURED = "NatureJudgmentCaptured"
    SEMANTIC_UPGRADE_DECLARATION = "SemanticUpgradeDeclaration"
    RESOLVER_DECISION_MADE = "ResolverDecisionMade"
    OBLIGATION_SCOPE_JUDGED = "ObligationScopeJudged"
    RISK_SURFACE_ASSIGNED = "RiskSurfaceAssigned"
    IMPACT_ASSESSMENT_MADE = "ImpactAssessmentMade"
    CONSISTENCY_JUDGMENT_MADE = "ConsistencyJudgmentMade"
    CRITICAL_PATH_IDENTIFIED = "CriticalPathIdentified"
    AUDIT_TRIGGERED = "AuditTriggered"
    AUDIT_VERDICT_RECEIVED = "AuditVerdictReceived"
    ESCALATION_DECISION_MADE = "EscalationDecisionMade"  # name in §3.11 base; schema 补全 by M-2.3 §3.5

    # ─── semantic_judgment Phase D 新增 1 ───
    ESCALATION_LEARNING_CAPTURED = "EscalationLearningCaptured"  # Patch M-2.3-A

    # ─── semantic_judgment T-FIX-B5-03 (CONSTITUTION-unknown#3) — 语义冲突检测留痕 ───
    # RUN-080 漏洞2 (SPEC-PATCH-CONCURRENCY-RUN080.md §2): 两并发会话对同一 obligation 给相反 scope
    # 判定 (一个 in_scope, 一个 out_of_scope) 此前系统完全看不见 (detect_semantic_conflicts 是纯函数,
    # 全仓零生产调用点)。本事件给检测产物一个 canonical 落地处: scan 入口 (daemon_run_once.
    # scan_semantic_conflicts_and_emit) 读 ObligationScopeJudged → detect → 对每条冲突 emit 一条
    # SemanticConflictDetected (path-B 自观测件, like DriftDetected / DaemonRunCompleted), orchestrator
    # route → main-inbound 通知 owner (零 spawn)。
    # 🔴 检测 ≠ 自动仲裁: 仲裁 verdict (谁赢 / commit-gate reject / resolver 裁决) SKIP, 挂 deferral 债
    #    debt-run080-semantic-arbitration。本事件只 surface 冲突供后续仲裁层消费, 不替任一方下结论 /
    #    不阻断提交 (RUN-080 owner 授权)。
    SEMANTIC_CONFLICT_DETECTED = "SemanticConflictDetected"

    # ─── semantic_judgment RUN-029 第0波 ③ — system self-debt tracking ───
    # New first-class debt lifecycle: every stub / deferral / blocked dependency the system
    # leaves behind is recorded as a trackable debt entry (vs the silent 假done it replaces).
    # The debt_registry projection derives the "honest done-ledger" (phase-status 升级形态).
    DEBT_REGISTERED = "DebtRegistered"
    DEBT_RESOLVED = "DebtResolved"

    # ─── semantic_judgment T-JLM-01 — judgment-case@v1 富化持久化 ───
    # 判例 JudgmentCase 是对一条 NatureJudgmentCaptured 原始事件的结构化富化包装 (本体独立于事件,
    # 经 source_event_id 引用它, 不改 L0 NatureJudgmentCaptured schema)。本事件是判例的 canonical
    # 落地形态 (draft 骨架 / enriched 富化本)。纠正处理不新造机制 —— 判例被 Nature 后续反应修正时,
    # 新版本走【标准 envelope 通用 supersede】(EventBase.supersede.is_supersede=true +
    # superseded_event_id 指回旧事件 + novelty/novelty_type), 非 concept 专属仲裁机制。conforming
    # producer (`towow judgment-case` CLI + l1.judgment_case) 唯一生产者 + emit 即 conforming →
    # ENFORCED (fail-closed, 不进 EXEMPT), 同 SemanticConflictDetected / DebtRegistered 范式。
    JUDGMENT_CASE_ENRICHED = "JudgmentCaseEnriched"

    # ─── semantic_judgment T-JLM-03 — preference-as-test-harness@v1 回归验证门 ───
    # 判例检索机制 (judgment-retrieval-protocol@v1) 正式投用前必须通过的语义正确性回归门:
    # 从历史判例留出 holdout (不进检索候选池), 把每条留出判例的 scenario 还原成预测任务喂给检索机制,
    # 跟真实 quote/meaning 的方向对答案。本事件是一次回归运行的结论载荷 (score/pass_threshold/
    # gate_result_state) —— 未来任何索引/嵌入设施类 task 的立项前置检查读它 (冻结不变量
    # preference-as-test-gate-blocks-index-infra)。唯一生产者 = l1.judgment_regression_harness +
    # `towow judgment-regression run` CLI, emit 即 conforming → ENFORCED (fail-closed, 不进 EXEMPT),
    # 同 JudgmentCaseEnriched 范式。
    JUDGMENT_REGRESSION_EVALUATED = "JudgmentRegressionEvaluated"

    # ─── semantic_judgment T-TRACK-01 — capability status real projection ───
    # 治 finding-phase-status-not-a-projection: 603 能力的"做完没"做成 event-sourced
    # projection 而非 markdown 快照 + 人对账。CapabilityBaselineIngested 一次性把
    # panorama 基线 event 化 (带 provenance) seed 全部节点; CapabilityStatusAdvanced
    # 每次任务落地/债结清 emit, flip 单条 current_classification。capability_status
    # projection 从这两类事件派生 → 刷盘时不再读 markdown。
    CAPABILITY_BASELINE_INGESTED = "CapabilityBaselineIngested"
    CAPABILITY_STATUS_ADVANCED = "CapabilityStatusAdvanced"

    # ─── snapshot (2) — M-0.1 §3.3 ───
    SNAPSHOT_CREATED = "SnapshotCreated"
    SNAPSHOT_SUPERSEDED = "SnapshotSuperseded"

    # ─── run wrapper RUN-052 (M-3.1 §10) — maintenance run lifecycle (F-18) ───
    # Previously maintenance commands (consolidate/snapshot/gc) each emitted a DAEMON_RUN_COMPLETED
    # "假事件" with no unified wrapper, and RunDigestPublished was NOT registered (the investment-doc
    # claim "M-0.7 已注册" was wrong — corrected in RUN-052). These are the real §10 run-wrapper
    # lifecycle events: RunStarted → command → RunDigestPublished (success) / RunFailed (failure).
    # Flat §10.2 payload shape (no after_state wrapper), conforming producer → PAYLOAD_VALIDATION_ENFORCED.
    RUN_STARTED = "RunStarted"
    RUN_DIGEST_PUBLISHED = "RunDigestPublished"
    RUN_FAILED = "RunFailed"

    # ─── consolidation (4) — M-0.1 §3.4 ───
    CROSS_RUN_CONSOLIDATION_COMMITTED = "CrossRunConsolidationCommitted"
    ARCHIVE_SEGMENT_MOVED = "ArchiveSegmentMoved"
    DIGEST_SUPERSEDED = "DigestSuperseded"
    RETENTION_POLICY_CHANGED = "RetentionPolicyChanged"

    # ─── capsule (1) — M-0.1 §3.5 ───
    CAPSULE_COMPILED = "CapsuleCompiled"

    # ─── capsule RUN-031 T-L3kc-06 (M-3.1 §8.3 fail-closed) ───
    # skill-start aborted because a required knowledge file is missing — path B fail-closed
    # signal. Replaces the NodeTouched stub-rewrap (kind="CapsuleInjectionFailed") it superseded.
    CAPSULE_INJECTION_FAILED = "CapsuleInjectionFailed"

    # ─── capsule 装配 abort (T-L0-16, 波1) — M-0.3 §2.8/§2.10 ───
    # M-0.1 写入失败时 capsule 装配中止 (不交付无留痕半截 capsule)。emit 此事件留痕 (失败 stage +
    # 已写 partial event ids), pipeline 抛受控 PipelineError。留痕 best-effort: 存储全死时吞掉留痕,
    # 但 abort 抛错保证不破。
    CAPSULE_ASSEMBLY_FAILED = "CapsuleAssemblyFailed"

    # ─── envelope (2) — M-0.1 §3.6 ───
    TRANSACTION_ENVELOPE_SUBMITTED = "TransactionEnvelopeSubmitted"
    NODE_TOUCHED = "NodeTouched"

    # ─── commit (3) — M-0.1 §3.7 ───
    COMMIT_ACCEPTED = "CommitAccepted"
    COMMIT_REJECTED = "CommitRejected"
    DRIFT_DETECTED = "DriftDetected"

    # ─── commit RUN-031 T-L3kc-03 (M-3.1 §5.3 V-01 owner-guard) ───
    # write-boundary guard observation (path B, like DriftDetected): a non-owner skill tried to
    # write a file outside its task worktree write_set. Previously only an exception class existed
    # (shell/worktree.py) — 门没接、event 没注册 (fake-done). Now a canonical, audit-visible event.
    OWNER_GUARD_VIOLATION = "OwnerGuardViolation"

    # ─── commit T-FIX-B4-05 (INT-content#1 / PARALLEL-locks#2) — admin-bypass 受控产生路径 ───
    # PreToolUse 物理门 (B4-02 采访入口门 / B4-03 V-01 门 / B4-06 账本删除门) 的唯一出口旗标
    # .towow/locks/admin-bypass.flag 的 canonical provenance (path B guard observation, like
    # OwnerGuardViolation)。旗标只能经 `towow guard admin-bypass --reason ... --owner-confirm`
    # CLI 产生 (--owner-confirm = Class A owner 物理动作), agent 自己 touch 旗标绕门 = 禁止。
    # Granted 先 emit 再写旗标 (emit 失败则旗标不落地 — 不允许"门开着但 canonical 无痕");
    # Revoked 先删旗标再 emit (emit 失败时门已关 — fail-closed 方向)。
    GUARD_ADMIN_BYPASS_GRANTED = "GuardAdminBypassGranted"
    GUARD_ADMIN_BYPASS_REVOKED = "GuardAdminBypassRevoked"

    # ─── commit owner-confirm@v1 (组件6 — 红线 enforcement + 不可伪造 owner 授权) ───
    # owner 对【一个具体不可逆动作的挑战】(red_line_class + task_id/finding_id 或 action_fingerprint
    # + nonce) 做的 Ed25519 detached 签名的 canonical 载体。与 GuardAdminBypass 同族 path-B guard
    # observation, 但【硬于】admin-bypass: admin-bypass 的 --owner-confirm 只是布尔 flag (任何 CLI 调用
    # 方都能传, 物理没验任何东西); 本事件带的是钉死公钥验得过的签名 —— agent emit 得出事件、造不出有效
    # 签名 (除非拿到 agent 信封外的私钥)。消费方两处共用同一验签核: retire 闭合门 (closure_evidence_
    # check._owner_confirm_present) + 4 红线物理门。唯一受控产生路径 = `towow owner-confirm grant`
    # (先验签再 emit, 验不过不留痕, fail-closed 方向同 admin-bypass)。
    OWNER_CONFIRMATION_GRANTED = "OwnerConfirmationGranted"

    # ─── commit A6 (哨兵正经源 / irreversible-action-blocked-audit-event@v1) — path-B guard observation ───
    # 物理闸 (PhysicalGate, layer ④) DENY 一个高危不可逆动作 (生产部署/对外发布/资金/删数据/公开承诺)
    # 时, best-effort emit 本事件上看板 (DENY 是 fail-closed 主动作; emit 只观测、绝不回退放行)。
    # per-instance: payload 带 command_or_payload + write_set, 同 risk_class 不同命令 → 不同事件
    # (OwnerGuardViolation / GuardAdminBypass 同族 path-B guard observation)。
    IRREVERSIBLE_ACTION_BLOCKED = "IrreversibleActionBlocked"

    # ─── finding (5) — M-0.1 §3.8 + f-escalation-task-oriented ACCEPTED ───
    FINDING_CREATED = "FindingCreated"
    FINDING_VERIFIED = "FindingVerified"
    FINDING_DISPUTED = "FindingDisputed"
    FINDING_RESOLVED = "FindingResolved"
    # f-escalation-task-oriented-not-reversibility-framework: 改不了/不可变的 finding (如账本
    # 553 历史重号) 上报一次 → owner accept → ACCEPTED → 哨兵每轮先查它, 永不再报 (不刷屏)。
    FINDING_ACCEPTED = "FindingAccepted"
    # f-stale-closure-contract-permanently-unclosable: closure_contract 此前**写一次即不可变**
    # (唯一写入点 = FindingCreated), 判据锚定的位置被后续**合法**重构架空后, 该 finding 物理上
    # 永久不可闭合 (fix→review 每轮空转)。本事件 = 有权座位 (M-1.5 review) 修订陈旧合约的唯一
    # 通道: 走 supersede 链 + M-0.5 NoveltyCheck, 闭合门读**生效合约** (末条 amend, 非首条
    # FindingCreated)。**不是** lifecycle transition —— amend 不改 finding 的 lifecycle_state
    # (一条 verified finding 修订合约后仍是 verified), 故 reducer 只换合约不动状态。
    FINDING_CLOSURE_CONTRACT_AMENDED = "FindingClosureContractAmended"

    # ─── review_plan (1) — M-1.5 §3.1 Patch Z (RUN-035 T-L1-50) ───
    # design-time review mode 产出: review_plan 是 author-time / fix-after mode 的稳定共同前提
    # (dimensions + VoI criteria + historical_failures_feed)。0 历史真事件, M-1.5 唯一生产者
    # → 直接注册 enforced (非 exempt), 同 T-L1-05 Patch R 范式。
    REVIEW_PLAN_CREATED = "ReviewPlanCreated"

    # ─── review proof-of-work (1) — T-RMD-S2-REVFIX (finding f-glob-review-fix-no-proof-of-work / M15-F1) ───
    # author-time review driver 每跑一个方法论维度 fork 就 emit 一条本事件 = "这个维度真被独立验过了"
    # 的 canonical 痕迹。修 M15-F1: 此前维度 fork 跑完 0 finding 时 verdict 门把空 findings 折叠成
    # passed 却零账本痕 ("patch 干净" 与 "维度根本没跑" 不可区分)。本事件让 0-finding 也留下
    # proof-of-work (dimension + fork_spawned + fork_completed + finding_count), 抓假的最后一道 (独立
    # 复核) 自己证明得了真跑过。无 reducer 消费 (proof-of-work 只活在 EventLog.all_records(), 不进投影)。
    REVIEW_DIMENSION_EXERCISED = "ReviewDimensionExercised"

    # ─── detection_rule (4) — M-0.1 §3.9 ───
    DETECTION_RULE_PROPOSED = "DetectionRuleProposed"
    DETECTION_RULE_SHADOWING = "DetectionRuleShadowing"
    DETECTION_RULE_PROMOTED = "DetectionRulePromoted"
    DETECTION_RULE_RETIRED = "DetectionRuleRetired"

    # ─── obligation (6) — M-0.1 §3.10 ───
    OBLIGATION_CAPTURED = "ObligationCaptured"
    OBLIGATION_ACTIVATED = "ObligationActivated"
    OBLIGATION_CHECKED = "ObligationChecked"
    OBLIGATION_VIOLATED = "ObligationViolated"
    OBLIGATION_EVOLVED = "ObligationEvolved"
    OBLIGATION_RETIRED = "ObligationRetired"

    # ─── state_transition RUN-058 (M-3.4 §3.3) — validation matrix run record ───
    # M-3.4 验证大盘的留痕事件: validation runner 真驱动一个验证场景 (按 spec input/trigger
    # 跑真实代码路径) → 收集 events → 比对 expected_event_sequence → 裁 outcome → emit 本事件。
    # 此前 EPIC-10 只有静态注册表 (validation.py), 无 runner 无此事件 (空跑道, RUN-058 baseline)。
    # base_classification=immutable_truth (§3.3 "validation 留痕永久")。validation runner 是唯一
    # 生产者 + emit 即 conforming → PAYLOAD_VALIDATION_ENFORCED (fail-closed, 同 T-L1-37 范式)。
    # Path B 留痕 (validation runner 自观察, like DebtRegistered / DriftDetected — 不走 commit gate:
    # 验证结果是 runner 对系统跑出来的观察, 不是要被审计的域 patch)。
    VALIDATION_SCENARIO_RUN = "ValidationScenarioRun"

    # ─── state_transition M-3.3 §11.1 (RUN-066) — 散落形态迁移工具留痕 4 事件 ───
    # M-3.3 是一次性迁移工具 (v2.x 散落规则/决策/状态/记忆 → v3 概念图+obligation)。这 4 个
    # event_type 是迁移 run 的留痕链 (§13.1 "MigrationRunStarted → 多 MigrationStepRecorded →
    # MigrationRunCompleted"): MigrationStepRecorded 进 apply-batch 的 commit gate envelope
    # (跟 ConceptCreated/ObligationCaptured 同 batch); MigrationRunStarted/BatchSubmitted/
    # RunCompleted 是 path-B 留痕观察 (run lifecycle, 不是被审计的域 patch — 同 RunStarted 范式)。
    # base_classification=immutable_truth (§5.2/§5.3 "migration 留痕永久保留")。narrow patch §14.1。
    MIGRATION_RUN_STARTED = "MigrationRunStarted"
    MIGRATION_STEP_RECORDED = "MigrationStepRecorded"
    MIGRATION_BATCH_SUBMITTED = "MigrationBatchSubmitted"
    MIGRATION_RUN_COMPLETED = "MigrationRunCompleted"


@unique
class TargetEntityType(StrEnum):
    """target_entity_type per M-0.1 §2.3.1 + M-1.6 Patch X (escalation) + Phase D 4.

    Total 16 = 9 base (M-0.1 §2.3.1) + 1 (M-1.6 Patch X escalation) + 4 Phase D +
    1 RUN-017C Patch Q-interview-brief-canonical-emit-1 (interview_brief) +
    1 RUN-018-F11 sub_skill_decision (SubSkillInvocationSkipped target).
    """

    CONCEPT = "concept"
    CONCEPT_EDGE = "concept_edge"
    TASK = "task"
    TASK_EDGE = "task_edge"
    AT_REFERENCE = "at_reference"
    INFORMATION_NEED = "information_need"
    PATCH = "patch"
    CONSUMER_LIST = "consumer_list"
    ENGINEERING_CONSENSUS = "engineering_consensus"
    ESCALATION = "escalation"  # M-1.6 v2.1.1 Patch X — registered pre-Phase D
    # ─── Phase D 新增 4 ───
    INVALIDATION_CASCADE = "invalidation_cascade"  # Patch M-2.1-B
    DAEMON_RUN = "daemon_run"  # Patch M-2.1-B
    OBLIGATION_RETIRE_CANDIDATE = "obligation_retire_candidate"  # Patch M-2.2-B
    HISTORICAL_PATTERN_CANDIDATE = "historical_pattern_candidate"  # Patch M-2.3-B
    # ─── RUN-017C Patch Q-interview-brief-canonical-emit-1 (M-1.1 §5.4) ───
    INTERVIEW_BRIEF = "interview_brief"  # InterviewBriefPublished target entity
    # ─── RUN-018-F11 sub-skill decision audit ───
    SUB_SKILL_DECISION = "sub_skill_decision"  # SubSkillInvocationSkipped target entity
    # ─── M-1.5 §3.1 Patch Z (RUN-035 T-L1-50) ───
    REVIEW_PLAN = "review_plan"  # ReviewPlanCreated target_entity_type
    # ─── M-1.4 §3.2/§3.3 (RUN-038 加固(2) T-L1-38/T-L1-39) ───
    MISMATCH = "mismatch"  # MismatchDetected / MismatchResolutionDecided target_entity_type
    ADVISOR_CONSULT = "advisor_consult"  # T-L1-48 (波1) Narrow Patch Y 补齐 3/3 — advisor 事件 target
    # ─── M-3.4 §3.3 (RUN-058) — validation scenario run target ───
    VALIDATION_SCENARIO = "validation_scenario"  # ValidationScenarioRun target_entity_type
    # ─── M-3.3 §11.3 / §14.1 (RUN-066) — 迁移留痕 target_entity_type ───
    MIGRATION_RUN = "migration_run"  # MigrationRunStarted / BatchSubmitted / RunCompleted target
    MIGRATION_STEP = "migration_step"  # MigrationStepRecorded target
    # ─── K2b-REG (50-graph-protocol §TG.4) — 图协议三类新节点的 target_entity_type ───
    SESSION = "session"  # SessionSpawned target — 会话血缘节点
    LOCK = "lock"  # LockAcquired target — 锁节点 (并发可见)


@unique
class TransitionType(StrEnum):
    """StateTransitionFields.transition_type per M-0.1 §2.3.1 (5 values)."""

    CREATED = "created"
    MODIFIED = "modified"
    REMOVED = "removed"
    SUPERSEDED = "superseded"
    FROZEN = "frozen"


@unique
class ScanMethod(StrEnum):
    """M-1.2 §3.6 ConsumerListPublished.scan_method — 区分写入方 (M-1.2 共识扫 vs M-2.1 CIA 扫).

    M-1.2 工程共识建立时初次扫消费方 (INITIAL_SCAN); M-2.1 变更影响识别 skill 推送的持续更新
    (UPDATE_FROM_CHANGE_IMPACT)。两者共用 ConsumerListPublished event_type (不新增
    ConsumerListUpdated 避免膨胀), 靠 scan_method + actor_id 区分写入方。详 §3.6。
    """

    INITIAL_SCAN = "initial_scan"  # M-1.2 工程共识建立时初次扫描 (自产)
    UPDATE_FROM_CHANGE_IMPACT = "update_from_change_impact"  # M-2.1 变更影响识别 skill 推送的更新


@unique
class JudgmentType(StrEnum):
    """SemanticJudgmentFields.judgment_type per M-0.1 §2.3.2 + M-2.3 §3.4/§3.5 extensions."""

    NATURE_JUDGMENT = "nature_judgment"
    RESOLVER_DECISION = "resolver_decision"
    OBLIGATION_SCOPE = "obligation_scope"
    RISK_SURFACE = "risk_surface"
    IMPACT_ASSESSMENT = "impact_assessment"
    CONSISTENCY = "consistency"
    # T-L1-46: mismatch_verdict judgment-type 已删 — MismatchVerdict event (M-1.4 §3.4 v2.1) 删除后
    # 该 judgment_type 值失去 producing event。M-1.4 §3.4 supersede M-0.1 §2.3.2 该项。详 LEDGER Conflict 10。
    CRITICAL_PATH = "critical_path"
    AUDIT_TRIGGER = "audit_trigger"
    AUDIT_VERDICT = "audit_verdict"
    SEMANTIC_UPGRADE = "semantic_upgrade"
    # M-2.3 §3.4 / §3.5 引用 (judgment_type 字段值)
    ESCALATION_LEARNING = "escalation_learning"  # M-2.3 §3.4
    ESCALATION_DECISION = "escalation_decision"  # M-2.3 §3.5


# ════════════════════════════════════════════════════════════════════════════════
#  JudgmentCase 富化字段枚举 — judgment-case@v1 (T-JLM-01)
#
#  判例 JudgmentCase 是对一条 NatureJudgmentCaptured 原始事件的结构化富化包装 (本体独立于事件、经
#  source_event_id 引用它)。下面三个枚举钉死 judgment-case@v1 概念定义里三个 enum 字段的取值形状:
#  weight_tier / stability / enrichment_status。语义见 judgment-case@v1 concept 定义。
# ════════════════════════════════════════════════════════════════════════════════


@unique
class WeightTier(StrEnum):
    """judgment-case@v1.weight_tier — 判例权重五级 (纠正类天然最高, 直接暴露评价函数)。"""

    CASUAL_MENTION = "casual_mention"
    ACTIVE_PREFERENCE = "active_preference"
    CORRECTION = "correction"
    REAL_DECISION = "real_decision"
    RECURRING_PATTERN = "recurring_pattern"


@unique
class Stability(StrEnum):
    """judgment-case v1.stability — 判例稳定性三级 (缺省 temporary_tactic, 需反复印证才升级)。"""

    TEMPORARY_TACTIC = "temporary_tactic"
    PHASE_PREFERENCE = "phase_preference"
    LONG_TERM_PRINCIPLE = "long_term_principle"


@unique
class EnrichmentStatus(StrEnum):
    """judgment-case@v1.enrichment_status — 富化状态。

    draft   : 仅有 source_event_id + scenario 的最小骨架 (weight_tier/applicable/affordance 未定);
    enriched: 五个行李字段 (weight_tier/applicable_conditions/non_applicable_conditions/
              counter_example_case_ids/affordance) 全部有值 (stability 有缺省, 骨架期即在)。
    """

    DRAFT = "draft"
    ENRICHED = "enriched"


@unique
class EvidenceSourceType(StrEnum):
    """SemanticJudgmentFields.evidence[].source_type per M-0.1 §2.3.2 (5 sources)."""

    EVENT = "event"
    PROJECTION = "projection"
    CONCEPT = "concept"
    FILE = "file"
    NATURE_QUOTE = "nature_quote"


@unique
class ValidationOutcome(StrEnum):
    """M-3.4 §3.3 ValidationScenarioRun.outcome — 5 终态.

    passed                          : 跑过 + 全部 invariant 验证 ✓
    failed_invariant_violation      : 跑过但某 invariant 违反 (硬失败, 暴露真问题)
    failed_with_correction_signal   : 跑过但失败, 且失败被 explicit captured 为修正信号
    aborted_external                : 被外部因素中止 (依赖的 daemon stub / live skill session 不可自动驱动)
    aborted_setup_error             : 场景 setup 阶段就出错, 未真正跑到 trigger
    """

    PASSED = "passed"
    FAILED_INVARIANT_VIOLATION = "failed_invariant_violation"
    FAILED_WITH_CORRECTION_SIGNAL = "failed_with_correction_signal"
    ABORTED_EXTERNAL = "aborted_external"
    ABORTED_SETUP_ERROR = "aborted_setup_error"


@unique
class SnapshotType(StrEnum):
    """SnapshotFields.snapshot_type per M-0.1 §2.3.3 (3 types)."""

    ROUTINE = "routine"
    MILESTONE = "milestone"
    CONSOLIDATION_ANCHOR = "consolidation_anchor"


@unique
class DigestType(StrEnum):
    """ConsolidationFields.digest_type per M-0.1 §2.3.4 (3 types)."""

    SUMMARY = "summary"
    AGGREGATION = "aggregation"
    ARCHIVE_POINTER = "archive_pointer"


@unique
class LookbackType(StrEnum):
    """CrossRunConsolidationCommitted.lookback_window.lookback_type per M-0.7 §2.3 (3 types).

    How the consolidation run chose its固结 window:
      run_generations — last N run generations (parameters e.g. {n_runs: 50})
      seq_range       — an explicit [start_seq, end_seq] sequence range
      manual          — Nature / operator hand-picked window
    """

    RUN_GENERATIONS = "run_generations"
    SEQ_RANGE = "seq_range"
    MANUAL = "manual"


@unique
class ProvenanceRefRelevance(StrEnum):
    """CrossRunConsolidationCommitted.digest.provenance_refs[].relevance per M-0.7 §2.3 (3 levels).

    How relevant a referenced source event is to the digest (O-06 Invariant 2 provenance):
      primary    — central source the digest summarizes
      supporting — corroborating source
      mentioned  — peripherally referenced
    """

    PRIMARY = "primary"
    SUPPORTING = "supporting"
    MENTIONED = "mentioned"


@unique
class ArchiveDestination(StrEnum):
    """ArchiveSegmentMoved.archive_destination per M-0.7 §2.4 (2 destinations).

    cold      — 移到 .towow/events/cold/archive-NNNNNN.jsonl.gz (provenance_preserved=true)
    discarded — 第三层 discardable_noise 直接删除 (§4.3 tombstone, provenance_preserved=false)
    The cold path itself lives in segment_path_after, NOT in archive_destination.
    """

    COLD = "cold"
    DISCARDED = "discarded"


@unique
class SnapshotSupersedeNoveltyType(StrEnum):
    """SnapshotSuperseded.novelty.novelty_type per M-0.7 §2.2 (3 types).

    Distinct from SupersedeNoveltyType (event-level supersede metadata, 4 values) and
    DigestSupersedeNoveltyType (digest amend, 4 values). These 3 describe WHY a snapshot's
    content is wrong (the §2.2 semantic boundary: SnapshotSuperseded is only for a content
    error, never for "create a new snapshot"):
      corrected_error    — reconstructability 抽样验证失败 (§6.5)
      missing_projection — snapshot 漏了某 projection
      hash_mismatch      — bundle_hash 跟实际 dump 不一致
    """

    CORRECTED_ERROR = "corrected_error"
    MISSING_PROJECTION = "missing_projection"
    HASH_MISMATCH = "hash_mismatch"


@unique
class DigestSupersedeNoveltyType(StrEnum):
    """DigestSuperseded.novelty.novelty_type per M-0.7 §2.5 (4 types).

    Distinct from SnapshotSupersedeNoveltyType (snapshot content error, 3 values). These 4
    describe WHY a digest is being amended (§2.5 supersede chain, append-only correction):
      corrected_error      — 旧 digest 信息错误
      new_evidence_emerged — 新证据出现需要重新 digest
      coverage_extension   — 旧 digest 覆盖范围不够
      provenance_repair    — 旧 digest provenance 链断了
    """

    CORRECTED_ERROR = "corrected_error"
    NEW_EVIDENCE_EMERGED = "new_evidence_emerged"
    COVERAGE_EXTENSION = "coverage_extension"
    PROVENANCE_REPAIR = "provenance_repair"


@unique
class SceneType(StrEnum):
    """CapsuleFields.scene_type per M-0.1 §2.3.5 (9) + M-0.7 §10.3 Patch K (consolidation, 10th)."""

    INTERVIEW = "interview"
    ENGINEERING_CONSENSUS = "engineering_consensus"
    PLANNING = "planning"
    EXECUTION = "execution"
    MISMATCH = "mismatch"
    REVIEW = "review"
    FIX = "fix"
    MAINTENANCE = "maintenance"
    AUDIT = "audit"
    # ─── M-0.7 §10.3 Patch K — 第 10 个场景 (consolidation run capsule 模板, §6.2) ───
    # consolidation run 走 runtime protocol (O-06 §3.5), 需要专门的 capsule scene_type:
    # lookback window / F-18 digests / 三个 compaction invariant 声明。区别于 maintenance
    # (巡检 / propose / audit, §8.4): consolidation 是实际跑 consolidation run 接受 lookback
    # window 产 digest+snapshot+archive。M-0.3a Patch 10 记录反向 narrow patch。
    CONSOLIDATION = "consolidation"


@unique
class CommitVerdict(StrEnum):
    """CommitFields.verdict per M-0.1 §2.3.7."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


@unique
class RejectionType(StrEnum):
    """CommitFields.rejection_type per M-0.1 §2.3.7 (7) + M-0.7 §10.3 Patch N (8th)."""

    VERSION_CONFLICT = "version_conflict"
    WRITE_CONFLICT = "write_conflict"
    OBLIGATION_VIOLATION = "obligation_violation"
    NOVELTY_MISSING = "novelty_missing"
    CLAIM_BOUNDARY_EXCEEDED = "claim_boundary_exceeded"
    DRIFT_DETECTED = "drift_detected"
    AUDIT_FAILED = "audit_failed"
    # ─── M-0.7 §10.3 Patch N — consolidation envelope verify 失败的拒绝路径 (§6.6) ───
    # M-0.5 ConsolidationInvariantCheck (Patch M) 调 M-0.7.verify_consolidation_invariants;
    # 任一 compaction invariant (reconstructability / provenance / correctability) 失败时
    # gate reject 用这个 rejection_type, evidence 带 invariant_results。
    CONSOLIDATION_INVARIANT_VIOLATED = "consolidation_invariant_violated"


@unique
class FindingSeverity(StrEnum):
    """FindingFields.severity per M-0.1 §2.3.8 (4 levels) + M-1.5 §3.2 purple (RUN-035 T-L1-51).

    Distinct from ObligationSeverity (red_line / normal / hint).

    purple = preexisting / adjacent-code latent bug (PR 没引入但 review 发现的存量隐患)，
    区别于 PR 引入的 critical/major/minor (M-1.5 §3.2)。
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    OBSERVATION = "observation"
    PURPLE = "purple"  # M-1.5 §3.2 — preexisting / adjacent code latent bug


@unique
class FindingLifecycleState(StrEnum):
    """FindingFields.lifecycle_state — M-0.1 §2.3.8 (4 states) + ACCEPTED (5th).

    ACCEPTED 源 = f-escalation-task-oriented-not-reversibility-framework (owner 系统级原则):
    改不了/不可变的 finding (如账本 553 历史重号, 史料不可变) 上报一次后由 owner accept →
    标 ACCEPTED → 哨兵每轮先查 accepted 集跳过, 永不再报 (不刷屏淹没真新问题)。这是有意偏离
    旧 spec "4 states" 的设计扩展 (FindingAccepted 事件驱动)。
    """

    CREATED = "created"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


@unique
class FindingDetectionMethod(StrEnum):
    """FindingFields.detection_method per M-0.1 §2.3.8 (3 methods)."""

    MANUAL_REVIEW = "manual_review"
    AUTOMATED_RULE = "automated_rule"
    AD_HOC = "ad_hoc"


@unique
class FindingKind(StrEnum):
    """FindingCreated.finding_kind enum, accumulated 9 values.

    5 from M-1.6 v2.1.1 Patch D + 1 from Patch M-2.1-C + 3 from Patch M-2.2-C.
    """

    # ─── M-1.6 v2.1.1 Patch D (5) ───
    CLOSURE_CONTRACT_DEFECT = "closure_contract_defect"
    CONCEPT_ISSUE = "concept_issue"
    OBLIGATION_ISSUE = "obligation_issue"
    REVIEW_PLAN_ISSUE = "review_plan_issue"
    ADJACENT_CODE_ISSUE = "adjacent_code_issue"
    # ─── Phase D Patch M-2.1-C (1) ───
    CROSS_PROJECTION_INCONSISTENCY = "cross_projection_inconsistency"
    # ─── Phase D Patch M-2.2-C (3) ───
    ROUTING_STUCK = "routing_stuck"
    CROSS_TASK_FIX_COLLISION = "cross_task_fix_collision"
    ANOMALY = "anomaly"
    # ─── R07 治理环路阶段2/3 专属 (plan-r07-govloop, 不劫持 anomaly) ───
    EFFICIENCY_REGRESSION = "efficiency_regression"  # 效率越界 (探路过长/回读暴增/真失误率高)
    SYSTEM_GOVERNANCE_DEFECT = "system_governance_defect"  # 探针/治理机制自身缺陷 (verify-the-observer 抓)
    # ─── 退役终态 (TaskClosureReason.RETIRED 的证据 finding) ───
    # 断言某冻结 task 的 GOAL 前提为假; 须 subjects 锚定被退役 task + 经 verify-step verified 才能
    # 支撑 reason=retired 的关闭 (closure-evidence-verification-gate@v1 retired 分支)。不是代码 bug。
    PREMISE_FALSE = "premise_false"


# ════════════════════════════════════════════════════════════════════════════════
#  M-1.5 review enums (RUN-035 0-E-4 — review skill 尺子真化)
# ════════════════════════════════════════════════════════════════════════════════


@unique
class ReviewMode(StrEnum):
    """M-1.5 §7 三 review mode — trigger contract 据上游事件选 mode."""

    DESIGN_TIME = "design_time"      # §7.1 — EngineeringConsensusFreezed 触发, 产 review_plan
    AUTHOR_TIME = "author_time"      # §7.2 — TaskRunCompleted(success) 触发, 跑方法论 fork 找 finding
    FIX_AFTER = "fix_after"          # §7.3 — FixCompleted 触发, bounded closure verification


@unique
class ReviewPlanDimension(StrEnum):
    """M-1.5 §3.1 ReviewPlanCreated.dimensions[].dimension_id (3 方法论维度)."""

    METHOD_EXECUTION_PATH = "method-execution-path"
    METHOD_CONSISTENCY = "method-consistency"
    METHOD_RED_TEAM = "method-red-team"


@unique
class FindingReviewDimension(StrEnum):
    """M-1.5 §3.2 FindingCreated.review_dimension (5 — 3 方法论 + verify-step + meta-review).

    比 ReviewPlanDimension 多 verify-step / meta-review: 这两个 fork 也产 finding
    (verify-step 产 disputed/inconclusive; meta-review 产 review_plan 缺陷 finding),
    但不出现在 review_plan.dimensions 里。
    """

    METHOD_EXECUTION_PATH = "method-execution-path"
    METHOD_CONSISTENCY = "method-consistency"
    METHOD_RED_TEAM = "method-red-team"
    VERIFY_STEP = "verify-step"
    META_REVIEW = "meta-review"


@unique
class ReviewTriggerReason(StrEnum):
    """M-1.5 §3.1 dimensions[].trigger_reason — 该维度为何被触发."""

    AUTO_DETECTION = "auto_detection"
    AUTHOR_SELF_REPORTED = "author_self_reported"
    SCHEMA_ENFORCED = "schema_enforced"


@unique
class ReviewConsolidationType(StrEnum):
    """M-1.5 §3.1 batch_info.consolidation_type — 分批冻结对接 M-1.2 §11.3."""

    BATCH_SLICE = "batch_slice"
    FINAL_CONSOLIDATION = "final_consolidation"


@unique
class FindingVerificationState(StrEnum):
    """M-1.5 §3.3 FindingVerified.verification_state (三态 — RUN-035 T-L1-52).

    现 base 版只有 false_positive_eliminated: bool 二态; 三态是 Anthropic <1% FP 秘诀:
    verified / rejected_false_positive / unverified_inconclusive (不确定时保留给 author)。
    """

    VERIFIED = "verified"
    REJECTED_FALSE_POSITIVE = "rejected_false_positive"
    UNVERIFIED_INCONCLUSIVE = "unverified_inconclusive"


@unique
class ReviewClosureState(StrEnum):
    """M-1.5 §3.5/§3.5a closure_verification.closure_state (RUN-035 T-L1-53).

    Review Closure Cycle 状态机的终止判据 — bounded regression test 模式:
      closed                       = 终态 (criteria 满足 + ripple 同步 + 0 forbidden_residuals)
      fix_insufficient             = reopen (criteria 未满足)
      ripple_incomplete            = reopen (scope bounded 到 ripple_targets, 不全文 re-review)
      new_unrelated_finding_logged = 终态 (新 finding 进 backlog 不阻塞当前 closure)
    """

    CLOSED = "closed"
    FIX_INSUFFICIENT = "fix_insufficient"
    RIPPLE_INCOMPLETE = "ripple_incomplete"
    NEW_UNRELATED_FINDING_LOGGED = "new_unrelated_finding_logged"


@unique
class FalsificationResult(StrEnum):
    """M-1.5 §3.2 FindingCreated.falsification_evidence.result — 尝试 disprove patch 的结果."""

    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    UNABLE_TO_DISPROVE = "unable_to_disprove"


@unique
class SuggestedFixLayer(StrEnum):
    """M-1.5 §3.2 FindingCreated.suggested_fix_layer.primary — 告诉 author 改哪一层."""

    CODE = "code"
    SQL = "sql"
    HOOK = "hook"
    CONFIG = "config"
    CONCEPT = "concept"
    OBLIGATION = "obligation"
    SPEC = "spec"


@unique
class FindingSourceType(StrEnum):
    """M-1.5 §3.2 FindingCreated.source.type — finding 的来源类型.

    model_review 是 review fork 产的 finding (review 语境判别器): T-L1-51 条件强制锚点。
    """

    MODEL_REVIEW = "model_review"
    PHYSICAL_HOOK = "physical_hook"
    HISTORICAL_PATTERN = "historical_pattern"
    NATURE_LEARNING = "nature_learning"


@unique
class ClosureVerificationMethod(StrEnum):
    """M-1.5 §3.2 closure_contract.closure_criteria[].verification_method (7 — 可机器验).

    git_diff (第 7 个, finding-machine-check-encoding-gap-1780563112 / Ledger Conflict 19):
    "本任务 commit 集改了哪些文件" 类不变量 (INV 形态, 如 '文件 X 零改动') 的天然机器复核 —
    门对任务真实 commit 集跑 git name-only 拿改动清单, verification_pattern 对清单逐路径
    re.search 计数须 == expected_occurrences。T-SL-A1 实撞前这类 done_criterion 在引擎上
    根本无法编码, assemble 只能把命令文本塞进 grep search_scope (发布过门、复算永久拒)。
    """

    GREP = "grep"
    SCHEMA_CHECK = "schema_check"
    PROJECTION_CHECK = "projection_check"
    MANUAL_REASONING = "manual_reasoning"  # 最弱, 应少用
    TEST = "test"
    REPLAY = "replay"
    GIT_DIFF = "git_diff"  # 本任务 commit 集改动文件清单比对 (需 caller 注入 commit 上下文)
    # live-target-execution-evidence@v1 新原语 (物理基石): 门读 committed canonical 账本、对某类真实
    # domain 事件计数须 ≥ min_occurrences。**真·新方法, 不是 grep 的扩展** —— grep 结构性排除 .towow*
    # (l1/closure_verification._EXCLUDE_DIRS), 物理上进不了账本; LEDGER_EVENT 读的对象是账本而非仓库树。
    # 独立不可伪造: 读的是已提交账本, 被审者用文字伪造不了 (与 grep/test/git_diff 同质)。
    # machine_check 形态: {event_kind, payload_predicate?, min_occurrences≥1, scope_binding}。
    LEDGER_EVENT = "ledger_event"


@unique
class LedgerEventScope(StrEnum):
    """live-target-execution-evidence@v1 · LEDGER_EVENT machine_check.scope_binding — 计数范围锚定。

    门对 committed 账本按 event_kind 计数时的作用域过滤 (advisor 承重指点: this_goal 绝不能退化成
    global, 否则 goal A 上月合法冻结的 1 条会让 goal B 的空声称 false-accept)。结构锚定:
      - this_run:  provenance.run_id == 本次 run
      - this_task: provenance.task_id == 本 task
      - this_goal: 事件的 provenance.task_id ∈ 本 goal 所属 plan 的全 task 集 (经 plan→task 结构解析);
                   解析不出 → fail-closed 拒, 永不静默扩成 global
      - global:    不过滤 (显式全域声称; 仅当合约明确要求全域时用, 非 goal observable 默认)
    """

    THIS_RUN = "this_run"
    THIS_TASK = "this_task"
    THIS_GOAL = "this_goal"
    GLOBAL = "global"


# 门能从 worktree 独立机器复算的 verification_method (subprocess grep/test/git 拿真结果, 或读 committed
# 账本计数 —— 被审者用文字伪造不了)。**单一真值源** (RUN-044): M-1.5 closure_verification (review
# fix-after 闭合环) + M-1.3 planner publish gate + M-1.4 execution 自检 共用同一集合, 防三处各自硬编码
# 导致漂移。其余 (schema_check / projection_check / replay / manual_reasoning) 本质不可门独立机器复算。
# git_diff 需 caller 注入"本任务 commit 集"上下文 (M-1.4 work complete 已接线; 未接线的 caller 对
# git_diff criterion fail-closed 明拒, 不静默降级 — 见 l1/closure_verification)。
# ledger_event (live-target-execution-evidence@v1): 门读 committed 账本计数, 需 caller 注入账本 records
# provider + scope 上下文; 未注入的 caller 对 ledger_event criterion fail-closed 明拒 (同 git_diff 待遇)。
GATE_RECOMPUTABLE_VERIFICATION_METHODS: frozenset[ClosureVerificationMethod] = frozenset(
    {
        ClosureVerificationMethod.GREP,
        ClosureVerificationMethod.TEST,
        ClosureVerificationMethod.GIT_DIFF,
        ClosureVerificationMethod.LEDGER_EVENT,
    },
)


@unique
class OccurrenceComparator(StrEnum):
    """M-1.5 closure_criterion.occurrence_comparator — grep/git_diff 复算的比较语义 (2 — EQ 缺省).

    finding f-ohr3-done-criteria-exact-match-vs-existence-intent 根因修复: GREP/GIT_DIFF 复算原先
    只有 `occ == expected_occurrences` 一种语义 (精确相等) — 当合约意图其实是"存在性"(expected_result
    写"至少 N 处", 如 T-OHR3 dc-1 一个决策分类词在把它当分类枚举的 rubric 真身里天然多次出现) 时,
    精确相等在**不破坏产物本职**的前提下物理无法满足 (真实 occurrence 数会随文档措辞自然漂移, 见
    T-REFLOW-SENTINEL-DEPLOY 因文档编辑导致 grep 计数漂移而 abort 的同型事故)。

    - EQ (缺省, 向后兼容): occ == expected_occurrences — 精确计数不变量 (如 "残留必须恰好 0 处" /
      "旧符号必须恰好清零"), 原有全部存量判据的语义。
    - GTE: occ >= expected_occurrences — 存在性不变量 (如 "至少 1 处引用 / 至少出现过"), expected_occurrences
      在此语义下是下限而非精确目标。

    字段缺省 None → 复算时按 EQ 处理 (closure_verification._occurrence_comparator), 存量已发布
    ClosureCriterion/DoneCriterion.machine_check (无此字段) 重放语义完全不变 —— 只有显式声明
    GTE 的新合约才启用存在性比较, 不改变任何既有判据的通过/拒绝结果。
    """

    EQ = "eq"
    GTE = "gte"


@unique
class ClosureResidualCheckMethod(StrEnum):
    """M-1.5 §3.2 closure_contract.forbidden_residuals[].check_method (3)."""

    GREP = "grep"
    SCHEMA_CHECK = "schema_check"
    MANUAL_REASONING = "manual_reasoning"


@unique
class ClosureRippleSyncStatus(StrEnum):
    """M-1.5 §3.5 closure_verification.ripple_results[].sync_status (3)."""

    SYNCED = "synced"
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"


@unique
class DetectionRuleLifecycleState(StrEnum):
    """DetectionRule lifecycle state — E.1.1 Conflict 3 canonical 分类.

    Per LEDGER Conflict 3 (canonical decision: M-2.3 §4.1 lifecycle semantics wins):

      canonical_emit     (5): proposed / shadow / active_warning / enforced / retired
      deprecated_alias   (1): warning   → normalize → active_warning
      reserved_not_emitted(1): revised   (registry 保留，当前状态机不 emit)

    Parser 接受 deprecated_alias (cold replay 兼容)，emitter 只输出 canonical_emit 5 个值。
    revised 仅保留 in registry，emit 时抛错。

    Use helpers `parse_detection_rule_state()` / `emit_detection_rule_state()` /
    `is_canonical_emit()` / `is_reserved()` instead of accessing values directly when
    you need parse/emit edge enforcement.
    """

    # ─── canonical_emit (5) — M-2.3 §4.1 ─────────────────────────────────────
    PROPOSED = "proposed"
    SHADOW = "shadow"
    ACTIVE_WARNING = "active_warning"
    ENFORCED = "enforced"
    RETIRED = "retired"
    # ─── deprecated_alias (1) — M-0.1 §2.3.9 legacy; parser only ─────────────
    WARNING = "warning"  # deprecated alias of active_warning; emit normalizes to active_warning
    # ─── reserved_not_emitted (1) — M-0.1 §2.3.9 legacy; never emit ──────────
    REVISED = "revised"  # reserved for future versioning lifecycle, current FSM does not emit


# Canonical category sets — used by parse/emit helpers and registry consistency tests.
_DETECTION_RULE_CANONICAL_EMIT: frozenset[DetectionRuleLifecycleState] = frozenset(
    {
        DetectionRuleLifecycleState.PROPOSED,
        DetectionRuleLifecycleState.SHADOW,
        DetectionRuleLifecycleState.ACTIVE_WARNING,
        DetectionRuleLifecycleState.ENFORCED,
        DetectionRuleLifecycleState.RETIRED,
    },
)
_DETECTION_RULE_DEPRECATED_ALIAS: dict[str, DetectionRuleLifecycleState] = {
    DetectionRuleLifecycleState.WARNING.value: DetectionRuleLifecycleState.ACTIVE_WARNING,
}
_DETECTION_RULE_RESERVED_NOT_EMITTED: frozenset[DetectionRuleLifecycleState] = frozenset(
    {DetectionRuleLifecycleState.REVISED},
)


def parse_detection_rule_state(s: str) -> DetectionRuleLifecycleState:
    """Parser path — accepts canonical_emit + deprecated_alias.

    deprecated_alias normalizes to the corresponding canonical value (e.g. "warning"
    → ACTIVE_WARNING). reserved_not_emitted values are rejected on parse — they should
    never appear in normal event log writes.
    """
    # Try direct enum lookup first (covers canonical_emit + deprecated_alias + reserved).
    try:
        value = DetectionRuleLifecycleState(s)
    except ValueError as exc:
        raise ValueError(f"unknown detection rule lifecycle state: {s!r}") from exc
    # Normalize deprecated_alias.
    if value.value in _DETECTION_RULE_DEPRECATED_ALIAS:
        return _DETECTION_RULE_DEPRECATED_ALIAS[value.value]
    if value in _DETECTION_RULE_RESERVED_NOT_EMITTED:
        raise ValueError(
            f"{value.value!r} is reserved_not_emitted — current FSM does not emit this state",
        )
    return value


def emit_detection_rule_state(value: DetectionRuleLifecycleState) -> str:
    """Emitter path — only canonical_emit values allowed (5)."""
    if value not in _DETECTION_RULE_CANONICAL_EMIT:
        raise ValueError(
            f"{value.value!r} is not in canonical_emit set "
            f"({sorted(v.value for v in _DETECTION_RULE_CANONICAL_EMIT)}); cannot emit",
        )
    return value.value


def is_canonical_emit_detection_rule_state(value: DetectionRuleLifecycleState) -> bool:
    """True iff value is in canonical_emit (5 values)."""
    return value in _DETECTION_RULE_CANONICAL_EMIT


def is_reserved_detection_rule_state(value: DetectionRuleLifecycleState) -> bool:
    """True iff value is in reserved_not_emitted (revised)."""
    return value in _DETECTION_RULE_RESERVED_NOT_EMITTED


@unique
class ObligationFieldsLifecycleState(StrEnum):
    """M-0.1 §2.3.10 ObligationFields.obligation_lifecycle_state (6 event-level states).

    Distinct dimension from ObligationCanonicalState — these 6 describe the lifecycle
    event type itself (Captured / Activated / Checked / Violated / Evolved / Retired).
    M-0.6 §3.1 真正的 canonical state machine 是 3 态 (active/superseded/retired) —
    scoped events (ObligationScopeJudged / Activated / Checked / Violated) 不改 canonical.
    """

    CAPTURED = "captured"
    ACTIVATED = "activated"
    CHECKED = "checked"
    VIOLATED = "violated"
    EVOLVED = "evolved"
    RETIRED = "retired"


@unique
class ObligationCanonicalState(StrEnum):
    """M-0.6 §3.1 三态 canonical lifecycle (active / superseded / retired).

    canonical_state 跟 ObligationFieldsLifecycleState 是两个独立维度——
    canonical 回答"obligation 是否仍是有效条文"，event lifecycle 是历史 event 类型记录.
    M-0.6 Narrow Patch E 将此字段加入 obligation_lifecycle projection state.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


@unique
class ObligationNature(StrEnum):
    """Obligation.nature per M-0.6 §2.1 (5 natures)."""

    INVARIANT = "invariant"
    CONTRACT = "contract"
    INTENT_ATTRIBUTION = "intent_attribution"
    TASK_CONSTRAINT = "task_constraint"
    TOOL_SPECIFIC = "tool_specific"


@unique
class ObligationSeverity(StrEnum):
    """Obligation.severity per M-0.6 §2.1 (3 levels).

    Distinct from FindingSeverity (critical / major / minor / observation).
    """

    RED_LINE = "red_line"
    NORMAL = "normal"
    HINT = "hint"


@unique
class DebtSeverity(StrEnum):
    """DebtRegistered.severity — RUN-029 第0波 ③ (system self-debt tracking).

    blocking      — the capability MUST NOT be marked done while this is open (mirrors how
                    red_line obligations hard-block). Used for genuine correctness/coverage
                    gaps that make a "done" claim a lie.
    normal        — a real gap that should be paid off, but the capability can ship with it
                    visibly on the books (debt is tracked, not hidden → not 假done).
    informational — a noted simplification / future-proofing hook; no payoff obligation.

    Whether `blocking` actually hard-blocks done at the gate is the owner-decided invariant
    (RUN-029 escalation); the enum reserves the three tiers regardless.
    """

    BLOCKING = "blocking"
    NORMAL = "normal"
    INFORMATIONAL = "informational"


@unique
class DebtType(StrEnum):
    """DebtRegistered.debt_type — what kind of incompleteness this entry records."""

    STUB = "stub"  # placeholder / mock standing in for real behavior
    DEFERRAL = "deferral"  # consciously postponed work ("以后再做")
    PARTIAL_IMPLEMENTATION = "partial_implementation"  # real but incomplete coverage
    SPEC_CONFLICT = "spec_conflict"  # two specs disagree; resolution pending
    DEPENDENCY_BLOCKED = "dependency_blocked"  # blocked on another capability/wave
    # activation-debt@v1 — '建成未接线' 缺口的债类型: 某闭合体声称完成但生产路径尚无有机流量
    # 证明其真被接线时登记, 带 expiry/gap_type/root_cause_key (见 DebtRegisteredPayload)。
    ACTIVATION = "activation"


@unique
class ActivationGapType(StrEnum):
    """activation-gap-type-taxonomy@v1 — '建成未接线'(built≠activated) 缺口的七型分类 A-G。

    来自 44-skill 诊断战役 + fork 激活侦察收割的 174 条实例分型框架; 是 activation-debt 的
    gap_type 字段合法取值。@concept:activation-acceptance-gate 覆盖全七型。
    """

    A_EXPRESSION_SURFACE_MISSING = "A"  # CLI/表达面缺失: 代码建成但命令不存在/报错/缺参
    B_GATE_NOT_ENFORCED = "B"  # 门禁不强制: gate 写成 '在场才查/(如有)', 绕过零成本
    C_DEPLOY_SURFACE_DRIFT = "C"  # 部署面漂移: src/harness/.claude/root 三面不同步
    D_AUTO_CHANNEL_DEADEND = "D"  # 自动通道断头: daemon/orchestrator spawn/触发链没接
    E_CONSUMER_UNAWARE = "E"  # 消费端不知情: 基建在但 skill 文本/派发模板仍教旧法
    F_GUARANTEE_IDLE = "F"  # 保证空转: 已建成已接线但有机生产流量上从未真被行使 (只演习/demo/livefire)
    G_SEMANTIC_DRIFT = "G"  # 语义漂移/口径混用: 事件类型/字段多语境共用无 emitter 边界, 下游计数失真


@unique
class DebtState(StrEnum):
    """Debt lifecycle state (debt_registry projection)."""

    OPEN = "open"
    RESOLVED = "resolved"
    # activation-debt@v1 — 真空期(闭合→首次有机流量)默认挂的状态, 不算失败(owner 定 2026-07-07);
    # 到期仍无有机流量才升级为 expired_escalated (升级判据/生产者是下游 activation-acceptance-gate
    # 任务的范围, 本 task 只把它作合法取值预留, 不实现到期比对)。
    AWAITING_ORGANIC_TRAFFIC = "awaiting_organic_traffic"
    EXPIRED_ESCALATED = "expired_escalated"


@unique
class CapabilityClassification(StrEnum):
    """T-TRACK-01 — the 6 panorama buckets a capability can be classified into.

    Mirrors CAPABILITY-PANORAMA.md / gen_status_603.py BUCKETS exactly so the
    capability_status projection is a faithful event-sourced form of the 603-capability board.
    """

    DONE_REAL = "done-real"  # 真做完 — read code, confirms spec
    FAKE_DONE = "fake-done"  # 危险假done — project thinks done, actually a shell
    UNPLANNED = "unplanned"  # 漏规划 — spec wrote it, never scheduled
    LEGIT_FUTURE = "legit-future"  # 合法未来 — scheduled, not yet reached / debt-tracked
    NEVER_SPECD = "never-specd"  # 真空白 — spec never designed it
    UNCERTAIN = "uncertain"  # 待核


@unique
class EnforcementLevel(StrEnum):
    """RUN-096 — a capability's enforcement layer, ORTHOGONAL to CapabilityClassification.

    The board headline (`done-real` count) means "建好 + 单测过". That is honest about being
    *built*, but it lets a reader assume "做完 = 达到 spec 效果" — which is exactly the disease
    this whole system exists to catch (说做完其实没在真路径生效). enforcement_level is the second
    axis that makes the board tell the truth about itself:

      built     — 建好 + 单测过 (the existing meaning of done-real; the default / unmarked state).
      enforced  — 效果在真路径物理成立: there is an agent-unbypassable physical gate AND real-path
                  evidence (TDD before/after probe + committed audit/provenance artifact), per the
                  M-3.4 反退路判别尺 "成了" column. enforced is NOT a higher classification — a
                  capability stays done-real; enforced just records that its effect was proven to
                  hold, not merely that code+tests exist.

    Orthogonal: it does NOT replace current_classification. Unmarked nodes are treated as built.
    Only marked via CapabilityStatusAdvanced.enforcement_level (evidence-backed), never inferred.
    """

    BUILT = "built"  # 建好 + 单测过 (= 现有 done-real 含义; 默认/未标 = built)
    ENFORCED = "enforced"  # 效果在真路径物理成立 (agent 绕不过的物理 gate + 真路径实证)


@unique
class ObligationScopeType(StrEnum):
    """Obligation.scope_type per M-0.6 §2.1 (5 types)."""

    GLOBAL = "global"
    ALL_PATCHES = "all_patches"
    SPECIFIC_ARTIFACT = "specific_artifact"
    CONCEPT_NEIGHBORHOOD = "concept_neighborhood"
    SEMANTIC_DEPENDENT = "semantic_dependent"


@unique
class ObligationAttachedToEntityType(StrEnum):
    """Obligation.attached_to[].entity_type per M-0.6 §2.1 (5 entities)."""

    CONCEPT = "concept"
    CONCEPT_EDGE = "concept_edge"
    TASK = "task"
    ARTIFACT = "artifact"
    RUN = "run"


@unique
class ObligationCaptureSource(StrEnum):
    """Obligation.capture_provenance.capture_source per M-0.6 §2.1 (6 sources).

    system_bootstrap is special: capture_source==system_bootstrap obligations cannot be
    retired (M-0.6 §5.1 protocol invariant immutability rule — bootstrap 4 条 retire
    必抛 ProtocolInvariantImmutabilityError).
    """

    SYSTEM_BOOTSTRAP = "system_bootstrap"
    USER_CORRECTION = "user_correction"
    NATURE_JUDGMENT = "nature_judgment"
    REVIEW_FINDING_UPGRADE = "review_finding_upgrade"
    ENGINEERING_CONSENSUS = "engineering_consensus"
    OTHER = "other"


@unique
class CheckerPatternType(StrEnum):
    """Obligation.checker_metadata.pattern_type per M-0.6 §2.1 (3 types).

    Source: M-0.5a Patch 3 reverse contribution (checker_metadata 字段族).
    """

    REGEX = "regex"
    AST = "ast"
    TEXT_MATCH = "text_match"


@unique
class ObligationDeclaredStatus(StrEnum):
    """Envelope.active_obligations_declared[].status per M-0.1 §2.3.6 (3 states)."""

    MAINTAINED = "maintained"
    VIOLATED = "violated"
    NOT_APPLICABLE = "not_applicable"


@unique
class ObligationCheckMethod(StrEnum):
    """ObligationChecked.check_method per M-0.6 §4.2.3 (3 methods).

    How a maintained / not_applicable obligation was verified at the commit gate:
      mechanical_pattern  — M-0.5 §3.4.2 physical forbidden_pattern matched clean
      audit_verdict       — M-0.5 triggered an audit fork; verdict=accept
      self_declared_only  — agent declared maintained with no mechanical / audit verification
    Distinct from ObligationDeclaredStatus (the agent's declared status, not how it was checked).
    """

    MECHANICAL_PATTERN = "mechanical_pattern"
    AUDIT_VERDICT = "audit_verdict"
    SELF_DECLARED_ONLY = "self_declared_only"


@unique
class ObligationCheckOutcome(StrEnum):
    """ObligationChecked.check_outcome per M-0.6 §4.2.3 (2 outcomes).

    A passed check is either maintained (physical / semantic verification ok) or
    not_applicable (agent declared n/a and it was accepted). violated is NOT an outcome
    here — violations go through ObligationViolated in the rejected batch (§4.2.4), so the
    Checked event carries only the two passing outcomes.
    """

    MAINTAINED = "maintained"
    NOT_APPLICABLE = "not_applicable"


@unique
class GroupingDimension(StrEnum):
    """HistoricalPatternSurfaceCandidate.grouping_dimension per M-2.3 §3.1 (5 values).

    机械化分组维度——不是 LLM 命名的 pattern。invariant: 不抽象 lesson / 不算 confidence.
    """

    SAME_RISK_SURFACE = "same_risk_surface"
    SAME_FILE_PATH = "same_file_path"
    SAME_ROOT_CAUSE_SIGNATURE = "same_root_cause_signature"
    SAME_DETECTION_METHOD = "same_detection_method"
    SAME_CONSUMER_RELATIONSHIP = "same_consumer_relationship"


@unique
class TriageCategory(StrEnum):
    """EscalationTriaged.triage_category per M-2.3 §3.2 (8 categories, 含 other)."""

    TECHNICAL_BLOCKER = "technical_blocker"
    CONSENSUS_CONFLICT = "consensus_conflict"
    OBLIGATION_EVOLUTION_PROPOSAL = "obligation_evolution_proposal"
    RESOURCE_CONSTRAINT = "resource_constraint"
    STRATEGIC_DECISION = "strategic_decision"
    CROSS_TASK_COLLISION = "cross_task_collision"
    DETECTION_RULE_PROMOTION_REQUEST = "detection_rule_promotion_request"
    OTHER = "other"


@unique
class ExpectedNatureAction(StrEnum):
    """EscalationTriaged.expected_nature_action per M-2.3 §3.2 (5 hints, optional).

    Hint only — Nature 决策时不受约束.
    """

    ACCEPT_WORKAROUND = "accept_workaround"
    REJECT_AND_RETRY = "reject_and_retry"
    ESCALATE_TO_STRATEGIC = "escalate_to_strategic"
    DEFER = "defer"
    REVIEW_EVIDENCE = "review_evidence"


@unique
class EscalationResolution(StrEnum):
    """EscalationResolved.resolution per M-2.3 §3.3 (7 resolutions).

    Invariant: 拒绝 "other" 兜底 — M-2.3 必须完整覆盖.
    """

    NATURE_ACCEPTED_WORKAROUND = "nature_accepted_workaround"
    NATURE_REJECTED_RETRY_TRIGGERED = "nature_rejected_retry_triggered"
    OBLIGATION_EVOLVED = "obligation_evolved"
    SUPERSEDED_CONCEPT_RESOLVED = "superseded_concept_resolved"
    ESCALATED_FURTHER = "escalated_further"
    ABANDONED = "abandoned"
    DEFERRED = "deferred"


@unique
class LearningType(StrEnum):
    """EscalationLearningCaptured.learning_type per M-2.3 §3.4 (4 types).

    Invariant: learning_type=no_learning ↔ seed_for_event_id is None.
    """

    DETECTION_RULE_CANDIDATE = "detection_rule_candidate"
    HISTORICAL_PATTERN_SEED = "historical_pattern_seed"
    OBLIGATION_EVOLUTION_SEED = "obligation_evolution_seed"
    NO_LEARNING = "no_learning"


@unique
class DecidedState(StrEnum):
    """EscalationDecisionMade.decided_state per M-2.3 §3.5 (4 outcomes)."""

    RESOLVED = "resolved"
    REOPENED = "reopened"
    ESCALATED_FURTHER = "escalated_further"
    DEFERRED = "deferred"


@unique
class DecisionMadeBy(StrEnum):
    """EscalationDecisionMade.decision_made_by per M-2.3 §3.5.

    Invariant: decision_made_by=nature ↔ confidence is None (Nature 判断不打分);
               decision_made_by=agent_self_resolved ↔ confidence required.
    """

    NATURE = "nature"
    AGENT_SELF_RESOLVED = "agent_self_resolved"


@unique
class EscalationLifecycleState(StrEnum):
    """escalation_lifecycle projection state per PHASE-D §2.2 + M-2.3 §6.1 (4 states).

    Patch M-2.3-E adds escalation_lifecycle projection — reducer consumes
    EscalationRaised / EscalationTriaged / EscalationResolved / EscalationLearningCaptured /
    EscalationDecisionMade.
    """

    RAISED = "raised"
    TRIAGED = "triaged"
    RESOLVED = "resolved"
    POST_RESOLVED_LEARNING_CAPTURED = "post_resolved_learning_captured"


@unique
class SubjectEntityType(StrEnum):
    """EventIntent.subjects[].entity_type per M-0.1a Patch 1 §1.2 (13 entity types).

    Distinct from TargetEntityType (StateTransitionFields.target_entity_type, 14 types).
    Subjects[] is M-0.1a Patch 3 unified indexing field — "this event acts on whom",
    while target_entity_type only applies to state_transition class.
    """

    CONCEPT = "concept"
    CONCEPT_EDGE = "concept_edge"
    TASK = "task"
    TASK_EDGE = "task_edge"
    OBLIGATION = "obligation"
    FILE = "file"
    FINDING = "finding"
    REVIEW_PLAN = "review_plan"  # M-1.5 §3.1 Patch Z (RUN-035 T-L1-50)
    PATCH = "patch"
    RULE = "rule"
    SNAPSHOT = "snapshot"
    CAPSULE = "capsule"
    AT_REFERENCE = "at_reference"
    INFORMATION_NEED = "information_need"
    MISMATCH = "mismatch"  # M-1.4 §3.2/§3.3 (RUN-038 加固(2)): mismatch subject entity
    ADVISOR_CONSULT = "advisor_consult"  # M-1.4 §3.5 (RUN-038 波2 T-L1-40): advisor consult subject


@unique
class SubjectRole(StrEnum):
    """EventIntent.subjects[].role per M-0.1a Patch 1 §1.2 (7 roles)."""

    PRIMARY = "primary"
    AFFECTED = "affected"
    EVIDENCE = "evidence"
    PARENT = "parent"
    CHILD = "child"
    SUPERSEDED = "superseded"
    RELATED = "related"


@unique
class BatchStatus(StrEnum):
    """TransactionBatch.status per M-0.1a Patch 1 §1.4 (2 outcomes).

    accepted batch: [envelope, ...domain events, CommitAccepted sentinel]
    rejected batch: [envelope, CommitRejected] — no domain events.
    """

    COMMITTED = "committed"
    REJECTED = "rejected"


@unique
class PatchType(StrEnum):
    """Envelope.patches[].patch_type per M-0.4 §5 (5 types) + M-0.6 Narrow Patch I (+1).

    M-0.4 §5: code_diff / concept_event / task_event / doc_change / config_change.
    M-0.6 Narrow Patch I: + obligation_event (6th type).
    M-0.7 §10.3 Patch L: + consolidation_event (7th type).
    """

    CODE_DIFF = "code_diff"
    CONCEPT_EVENT = "concept_event"
    TASK_EVENT = "task_event"
    DOC_CHANGE = "doc_change"
    CONFIG_CHANGE = "config_change"
    OBLIGATION_EVENT = "obligation_event"  # Patch M-0.6-I
    # ─── M-0.7 §10.3 Patch L — consolidation envelope patches[].patch_type (§6.3) ───
    # consolidation envelope 的 patches 不属 code_diff / concept_event / … — 它是
    # system-generated event batch (CrossRunConsolidationCommitted + SnapshotCreated +
    # ArchiveSegmentMoved* + DigestSuperseded?)。M-0.5 ConsolidationInvariantCheck (Patch M)
    # 据 patch_type==consolidation_event 决定是否调 verify (M-0.1 §10.3 Patch O system-generated
    # event 区分)。
    CONSOLIDATION_EVENT = "consolidation_event"  # Patch M-0.7-L


# ════════════════════════════════════════════════════════════════════════════════
#  state_transition payload enums — used by Step 1.c payloads/state_transition.py
# ════════════════════════════════════════════════════════════════════════════════


@unique
class ConceptCreatedStage(StrEnum):
    """ConceptCreated.after_state.created_by_stage per M-0.1 §3.1 (3 stages)."""

    INTERVIEW = "interview"
    ENGINEERING_CONSENSUS = "engineering_consensus"
    PLANNING = "planning"


@unique
class ConceptEdgeType(StrEnum):
    """ConceptEdgeAdded.after_state.edge_type per M-0.1 §3.1 (6 types)."""

    DEPENDENCY = "dependency"
    CONTRACT = "contract"
    DATA_SCHEMA = "data_schema"
    STATE_TRANSITION = "state_transition"
    REFERENCE = "reference"
    CONTAINMENT = "containment"


@unique
class ConceptEdgeDirection(StrEnum):
    """ConceptEdgeAdded.after_state.direction per M-0.1 §3.1."""

    UNIDIRECTIONAL = "unidirectional"
    BIDIRECTIONAL = "bidirectional"


@unique
class TaskType(StrEnum):
    """TaskNodeCreated.after_state.task_type per M-0.1 §3.1 (6 types)."""

    CODE_CHANGE = "code_change"
    DESIGN_CHANGE = "design_change"
    DOC_CHANGE = "doc_change"
    REVIEW = "review"
    FIX = "fix"
    MAINTAIN = "maintain"


@unique
class TaskPhase(StrEnum):
    """T-LND-09 (task-phase-stage@v1 / INV-B2-4): TaskNodeCreated.after_state.phase —— 使"谁先于谁"
    的阶段依赖可被表达和机器校验 (现仅 review_required bool 无 phase, 阶段先后无处表达)。是
    design-time-review-plan-precedes-implementation (T-LND-10) 依赖边的载体。"""

    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"


@unique
class TaskDependencyType(StrEnum):
    """TaskDependencyEdgeAdded.after_state.dependency_type — M-1.3 §3.2 6 types (Patch W, T-L1-24).

    base 3 (M-0.1 §3.1) + Patch W 3 (semantic/review/state_machine_transition_dependency)。
    """

    DATA_DEPENDENCY = "data_dependency"
    ORDERING = "ordering"
    RESOURCE_CONFLICT = "resource_conflict"
    # T-L1-24 (波1) Patch W — M-1.3 §3.2 L117 补 3 种语义依赖
    SEMANTIC_DEPENDENCY = "semantic_dependency"
    REVIEW_DEPENDENCY = "review_dependency"
    STATE_MACHINE_TRANSITION_DEPENDENCY = "state_machine_transition_dependency"


@unique
class TaskDependencyStrength(StrEnum):
    """TaskDependencyEdgeAdded.after_state.strength — M-1.3 §3.2 L119 (T-L1-24).

    hard = 必须等; medium = 可先做但要 re-check (并行风险)。
    """

    HARD = "hard"
    MEDIUM = "medium"


@unique
class LockingPolicy(StrEnum):
    """TaskNodeCreated.after_state.concept_refs[].locking_policy — M-1.3 §3.1 (T-L1-23).

    pin_to_snapshot = 钉死到某 snapshot (lock_event_id 必填); auto_accept_latest = 自动接最新;
    explicit_decision_required = 概念变更须显式决策。
    """

    PIN_TO_SNAPSHOT = "pin_to_snapshot"
    AUTO_ACCEPT_LATEST = "auto_accept_latest"
    EXPLICIT_DECISION_REQUIRED = "explicit_decision_required"


@unique
class RetentionAction(StrEnum):
    """RetentionPolicyChanged.after_state.new_rules[].retention_action — M-0.7 §2.6 (T-L0-36, 4 态)。

    §4 三层矩阵默认: immutable_truth → archive_to_cold (保留原件, 禁 digest, **永不 discard**);
    abstractable_process → digest_then_archive; discardable_noise → discard。owner-decided
    (RUN-038): 事实源永不自动删 (= 默认按 §4 矩阵, immutable_truth 不映射到 discard)。
    """

    KEEP_HOT_FOREVER = "keep_hot_forever"
    ARCHIVE_TO_COLD_AFTER_GC_UNREACHABLE = "archive_to_cold_after_gc_unreachable"
    DIGEST_THEN_ARCHIVE = "digest_then_archive"
    DISCARD_AFTER_CONSUMERS_ADVANCE = "discard_after_consumers_advance"


@unique
class ArchiveAction(StrEnum):
    """determine_archive_action 的物理动作 — M-0.7 §3.5 / §4.1 三层动作矩阵。

    归档动作由 base_classification 决定 (区别于 RetentionAction: 后者是 policy 可配的 retention
    分级; 这里是 GC 双层过滤通过后, 真要执行的物理 archive 动作):
      immutable_truth      → MOVE_TO_COLD    (原件入 cold, **禁 digest 替代**, §4.2 硬约束)
      abstractable_process → DIGEST_AND_COLD (产 digest + 原件入 cold, 保留 provenance)
      discardable_noise    → DISCARD         (直接删除, 不入 cold; §4.3 留痕 / §4.4 mixed 保守)
    """

    MOVE_TO_COLD = "move_to_cold"
    DIGEST_AND_COLD = "digest_and_cold"
    DISCARD = "discard"


@unique
class TaskModelTier(StrEnum):
    """TaskModelTierAssigned.after_state.model_tier per M-0.1 §3.1."""

    OPUS = "opus"
    SONNET = "sonnet"


@unique
class TaskRunOutcome(StrEnum):
    """TaskRunCompleted.after_state.outcome per M-1.4 §3.6 (5 outcomes, RUN-038 波2 T-L1-36).

    M-1.4 §3.6 (execution producer 详设, 权威) 用 5 枚举: success + 4 种可区分 abort
    (replan / unrecoverable / external / advisor_decision)。supersede M-0.1 §3.1 的 4 枚举
    placeholder (success/failure/partial/escalated) — 那 4 个 abort 路径混一, 区分不出 replan
    vs advisor vs external。详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 11。
    """

    SUCCESS = "success"
    ABORTED_FOR_REPLAN = "aborted_for_replan"
    ABORTED_UNRECOVERABLE = "aborted_unrecoverable"
    ABORTED_EXTERNAL = "aborted_external"
    ABORTED_ADVISOR_DECISION = "aborted_advisor_decision"


@unique
class TaskClosureReason(StrEnum):
    """TaskNodeClosed.after_state.reason (done-elsewhere-task-closure@v1, T-DEC-1).

    首值 done_elsewhere — 被派的 task 其实已在别处做完, 诚实标记终态 closed (区别 abort: abort=
    没做完, 会被重派; closure=已在别处做完, 终态, 永不重派)。可扩展: 未来其它"非 success 但应
    终态化"的关闭语义按需加成员 — 这是一个开放枚举, done_elsewhere 是奠基首值。

    retired — GOAL 前提为假、无 delivering envelope 的诚实终态 (task 不该再做, 因为它建立在一个
    已被证伪的前提上)。证据契约与 done_elsewhere 不同: 不要 delivering commit / 不要 audit verdict,
    而是 superseded_by 指向一条 finding_kind=premise_false 的 FindingCreated (subjects 锚定被退役
    task), 且该 finding 经独立 verify-step 证伪确认前提确为假。见 closure-evidence-verification-gate@v1
    的 retired 分支。
    """

    DONE_ELSEWHERE = "done_elsewhere"
    RETIRED = "retired"


@unique
class TaskClosureRefType(StrEnum):
    """TaskNodeClosed.after_state.superseded_by.ref_type — 取代被关闭 task 的交付物落在哪种引用上。

    commit = 被取代的 commit sha; finding = 承接它的 finding id。closure-evidence-verification-gate@v1
    (T-DEC-2) 在接受 TaskNodeClosed 前核验该 ref 真实可解析, 且其指向的产物独立覆盖被关闭 task 的
    done_criteria —— 结构层 (ref 非空) 必要但不充分, 防 done-elsewhere 关闭沦为新的"假装做完"后门。
    """

    COMMIT = "commit"
    FINDING = "finding"


# ── M-1.4 §3.2/§3.3 mismatch enums (RUN-038 加固(2) T-L1-38/T-L1-39) ──────────────────


@unique
class MismatchDetectionTime(StrEnum):
    """MismatchDetected.detection_time per M-1.4 §3.2 — mismatch 在哪个执行阶段被检出."""

    STARTING = "starting"
    MID_EXECUTION = "mid_execution"
    PRE_SUBMIT = "pre_submit"
    POST_COMMIT_REJECT = "post_commit_reject"


@unique
class MismatchAffectedAspect(StrEnum):
    """MismatchDetected.affected_aspect per M-1.4 §3.2.

    大类参考 — 不是严格 enum, executor 选最接近的或 `other` (§3.2 注: unknown 时用 other)。
    """

    READ_SET_ENTITY = "read_set_entity"
    WRITE_SET_LOCK = "write_set_lock"
    CONCEPT_REF_PIN = "concept_ref_pin"
    DONE_CRITERIA_VS_BRIEF = "done_criteria_vs_brief"
    OBLIGATION_STATE = "obligation_state"
    ACTUAL_VS_DECLARED_SET = "actual_vs_declared_set"
    CAPSULE_INFO = "capsule_info"
    OTHER = "other"


@unique
class MismatchSeverity(StrEnum):
    """MismatchDetected.severity_judgment per M-1.4 §3.2 — executor 自判, 不查表.

    critical 仅给 plan 基础假设破坏的场景 (pin stale / done_criteria 跟 brief 矛盾等)。
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@unique
class MismatchResolutionAction(StrEnum):
    """MismatchResolutionDecided.resolution_action per M-1.4 §3.3 (v2.1 改名+合并, 5 类)."""

    SELF_HEAL = "self_heal"  # executor 自解
    FINDING_HANDOFF = "finding_handoff"  # 产 FindingCreated 让其他 skill 接
    ADVISOR_GUIDED = "advisor_guided"  # 经 advisor verdict 决策
    REPLAN_TRIGGERED = "replan_triggered"  # 触发 RePlanTriggered
    ABORT_TASK = "abort_task"  # 放弃 task


@unique
class MismatchResolutionOutcome(StrEnum):
    """MismatchResolutionDecided.outcome per M-1.4 §3.3 — task 继续还是 abort."""

    CONTINUED = "continued"
    ABORTED = "aborted"


@unique
class AdvisorConsultTrigger(StrEnum):
    """AdvisorConsultRequested.triggered_by per M-1.4 §3.5 (RUN-038 波2 T-L1-40).

    executor 在何种情况下发起 advisor 咨询。verdict_evidence_scope_breach 是 v2.1 新增 —
    之前 verdict 的 evidence scope 被新证据 breach 时重新 consult。
    """

    MISMATCH = "mismatch"
    SELF_CHECK_DOUBT = "self_check_doubt"
    MID_EXECUTION_JUDGMENT = "mid_execution_judgment"
    DONE_CRITERIA_AMBIGUITY = "done_criteria_ambiguity"
    IMPLEMENTATION_CHOICE = "implementation_choice"
    VERDICT_EVIDENCE_SCOPE_BREACH = "verdict_evidence_scope_breach"


@unique
class AdvisorVerdictDecision(StrEnum):
    """AdvisorVerdictDelivered.decision per M-1.4 §3.5 (RUN-038 波2 T-L1-49).

    advisor 决策者 (独立 OPUS fork) 给的裁决路径。custom_action 时必带 specific_steps。
    """

    USE_PATH_SELF_HEAL = "use_path_self_heal"
    USE_PATH_CONSULT_AGAIN = "use_path_consult_again"
    USE_PATH_TRIGGER_REPLAN = "use_path_trigger_replan"
    USE_PATH_ABORT_TASK = "use_path_abort_task"
    CUSTOM_ACTION = "custom_action"


@unique
class AdvisorVerdictConfidence(StrEnum):
    """AdvisorVerdictDelivered.confidence per M-1.4 §3.5 (3 levels)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@unique
class RePlanTriggerSource(StrEnum):
    """RePlanTriggered.trigger_source — 统一并入三份 spec 的触发场景 (RUN-038 波3 T-L1-42/T-L1-71).

    M-1.3 §14.2 是 schema 定稿 owner, 给 6 个通用触发源 (investigation/nature/concept/cross-plan/
    execution-failure/resource)。M-1.4 §3.8 (T-L1-42) 加 4 个 execution-侧 hardcoded critical 场景
    (pin stale / done_criteria 矛盾 / advisor verdict=use_path_trigger_replan / task 边界本身错)。
    M-1.6 §3.4 (T-L1-71) 加 fix_scope_violation (fix 修复越过 task scope)。

    payload-shape 统一裁定见 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 12。
    """

    # M-1.3 §14.2 通用 6
    INVESTIGATION_COMPLETED = "investigation_completed"  # investigation task 产出改变前提
    NATURE_JUDGMENT_CHANGED = "nature_judgment_changed"  # Nature 新原话推翻前提
    CONCEPT_SUPERSEDED = "concept_superseded"  # 引用的 concept 被 supersede (M-1.2 / M-2.1)
    CROSS_PLAN_CONFLICT_EMERGED = "cross_plan_conflict_emerged"  # 其他 plan 冲突浮现
    EXECUTION_FAILURE_PATTERN = "execution_failure_pattern"  # M-1.4 反复失败
    RESOURCE_CONSTRAINT_CHANGED = "resource_constraint_changed"  # 资源/配额变化
    # M-1.4 §3.8 execution-侧 hardcoded critical 4 (T-L1-42)
    PIN_STALE = "pin_stale"  # pin_to_snapshot 的 @ 引用 stale (critical)
    DONE_CRITERIA_CONFLICT = "done_criteria_conflict"  # done_criteria 跟 brief 矛盾 (critical)
    ADVISOR_VERDICT_REPLAN = "advisor_verdict_replan"  # advisor verdict=use_path_trigger_replan
    TASK_BOUNDARY_ERROR = "task_boundary_error"  # execution-self-check 发现 task 边界本身错
    # M-1.6 §3.4 fix-侧 (T-L1-71)
    FIX_SCOPE_VIOLATION = "fix_scope_violation"  # fix 修复越过 task scope


@unique
class RePlanSuggestedAction(StrEnum):
    """RePlanTriggered.suggested_action per M-1.3 §14.2 — 给 F-14 的 re-plan 范围建议."""

    PARTIAL_REPLAN = "partial_replan"  # 部分 task 重拆 (F-14 默认)
    FULL_REPLAN = "full_replan"  # 整个 plan 重做 (罕见, 需 Nature 确认)
    ABORT_PLAN = "abort_plan"  # 放弃这个 plan


@unique
class InformationNeedPriority(StrEnum):
    """InformationNeedCreated.after_state.priority per M-0.1 §3.1 (3 levels).

    SUPERSEDED for the canonical InformationNeed payload by M-1.1 §10.2 Patch R
    (SPEC-CONFLICT-LEDGER Patch R). M-1.1 §3.1/§5.2 拆 information_need 成 source/status/
    red_line 三正交维, 不再用 priority/source_stage/6-混 status. 本 enum 保留仅供历史/
    向后兼容, canonical InformationNeedCreatedPayload 已不再使用 (T-L1-05/RUN-032).
    """

    CRITICAL = "critical"
    IMPORTANT = "important"
    NICE_TO_HAVE = "nice_to_have"


@unique
class InformationNeedSourceStage(StrEnum):
    """InformationNeedCreated.after_state.source_stage per M-0.1 §3.1.

    SUPERSEDED by Patch R (见 InformationNeedPriority docstring). 保留仅历史兼容.
    """

    INTERVIEW = "interview"
    ENGINEERING_CONSENSUS = "engineering_consensus"


@unique
class InformationNeedStatus(StrEnum):
    """M-0.1 §3.1 早期定义把"来源分类 / 生命周期状态 / 红线"混在一个 6-value status enum 里.

    SUPERSEDED by M-1.1 §10.2 Patch R — M-1.1 §3.1/§5.2 拆成三正交维 (InformationNeedSource /
    InformationNeedLifecycleStatus / red_line bool). 本 6-value enum 保留仅历史兼容, canonical
    payload 已不用 (T-L1-05/RUN-032). 详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch R.
    """

    REACHABLE = "reachable"
    NATURE_UNIQUE = "nature_unique"
    NATURE_CONFIRM = "nature_confirm"
    DEFER = "defer"
    RED_LINE = "red_line"
    RESOLVED = "resolved"


# ── M-1.1 §10.2 Patch R canonical InformationNeed 三正交维 (T-L1-05/RUN-032) ──────────
#  M-1.1 §3.1/§5.2/§10.2 Patch R: information_need 实体 schema 拆成 source / status / red_line
#  三独立正交维 (不再混在一个 enum 里, 见上 InformationNeedStatus SUPERSEDED docstring).
#  L0 schema 层不能 import L1 (towow.l1.interview.*), 故在此定义 canonical enum; 字面值跟
#  L1 §7.2 InfoNeedSource / InfoNeedStatus (info_map_build.py) 完全一致 (wire-interop),
#  L1 侧是 skill-facing proposal 枚举, 本侧是 canonical event payload 枚举.


@unique
class InformationNeedSource(StrEnum):
    """M-1.1 §5.2/§3.1 InformationNeedCreated.source — 来源分类 (Patch R 第一正交维)."""

    AI_REACHABLE = "ai_reachable"  # AI 自己能查 (不要问 Nature)
    NATURE_UNIQUE = "nature_unique"  # 只有 Nature 知道 (必须问)
    NATURE_CONFIRM = "nature_confirm"  # AI 能提假设让 Nature 确认
    DEFER = "defer"  # 现在不用定


@unique
class InformationNeedLifecycleStatus(StrEnum):
    """M-1.1 §3.1/§5.2 InformationNeed status — 生命周期状态 (Patch R 第二正交维).

    superseded 是终态. 合法转移: pending→resolved / pending→superseded / resolved→superseded.
    """

    PENDING = "pending"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


@unique
class InformationNeedResolutionMethod(StrEnum):
    """M-1.1 §5.2 InformationNeedStatusChanged.resolution_method — new_status=resolved 时填."""

    NATURE_ANSWERED = "nature_answered"
    AI_RESEARCH = "ai_research"
    AI_ASSUMPTION_CONFIRMED = "ai_assumption_confirmed"


@unique
class AtReferenceType(StrEnum):
    """AtReferenceAdded.after_state.reference_type per M-0.1 §3.1 (3 types)."""

    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    OVERRIDE = "override"


@unique
class PatchChangeType(StrEnum):
    """PatchProposed.after_state.change_type per M-0.1 §3.1 (4 changes)."""

    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"


@unique
class FixOutcome(StrEnum):
    """FixCompleted.after_state.outcome per M-0.1 §3.1 (3 outcomes)."""

    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    NEEDS_FURTHER_REVIEW = "needs_further_review"


@unique
class FeasibilityCheckSummary(StrEnum):
    """FixCompleted.after_state.feasibility_check_summary per M-1.6 §3.2 (T-L1-68).

    fast_path_passed  = v2.1.1 Patch C 简单 finding 走 fast-path feasibility
    all_checks_passed = 复杂 finding 详细 feasibility check 全通过
    """

    FAST_PATH_PASSED = "fast_path_passed"
    ALL_CHECKS_PASSED = "all_checks_passed"


# ─── Phase D state_transition payload enums ─────────────────────────────────────


@unique
class CascadeAffectedEntityType(StrEnum):
    """InvalidationCascade.after_state.affected_entities[].entity_type per M-2.1 §3.1 (6).

    Patch M-2.1-A — broader than SubjectEntityType because cascade can affect
    review_plan (M-2.x maintenance daemon abstraction, no event-level entity yet).
    """

    CONCEPT = "concept"
    TASK = "task"
    AT_REFERENCE = "at_reference"
    OBLIGATION = "obligation"
    REVIEW_PLAN = "review_plan"
    FINDING = "finding"


@unique
class CascadeAffectedViaSlice(StrEnum):
    """InvalidationCascade.after_state.affected_entities[].affected_via_slice per M-2.1 §3.1."""

    FORWARD = "forward"
    BACKWARD = "backward"
    CONTRACT = "contract"
    DATA_SCHEMA = "data_schema"
    STATE_MACHINE = "state_machine"


@unique
class CascadeResponseExpectation(StrEnum):
    """InvalidationCascade.after_state.response_expectation[].suggested_response per M-2.1 §3.1.

    Hint only — downstream skills self-decide (F-04d SAGA style).
    """

    ACKNOWLEDGE = "acknowledge"
    REVERIFY = "reverify"
    SUPERSEDE = "supersede"
    RETRACT = "retract"
    IGNORE = "ignore"


@unique
class DaemonName(StrEnum):
    """DaemonRunCompleted.after_state.daemon_name per M-2.1 §3.2 (7 named daemons).

    Patch M-2.1-A — shared across M-2.1 / M-2.2 / M-2.3 daemons.
    """

    CONCEPT_GRAPH_HEALTH = "concept_graph_health"  # M-2.1
    CROSS_PROJECTION_CHECK = "cross_projection_check"  # M-2.1
    OBLIGATION_LIFECYCLE_SCAN = "obligation_lifecycle_scan"  # M-2.2
    AUTHORITY_ROUTING_SUPERVISION = "authority_routing_supervision"  # M-2.2
    CROSS_TASK_FIX_COORDINATION = "cross_task_fix_coordination"  # M-2.2
    DETECTION_RULE_LIFECYCLE = "detection_rule_lifecycle"  # M-2.3
    HISTORICAL_FAILURE_CONSOLIDATION = "historical_failure_consolidation"  # M-2.3
    SEMANTIC_CONFLICT_SCAN = "semantic_conflict_scan"  # T-LC-05
    TRANSCRIPT_EFFICIENCY = "transcript_efficiency"  # R07 治理环路观测层 (plan-r07-govloop)
    EFFICIENCY_BOUNDARY = "efficiency_boundary"  # R07 治理环路阶段2 越界检测 (surface-only)
    VERIFY_OBSERVER = "verify_observer"  # R07 治理环路阶段3 验观测抽查 (OFF-by-default, surface-only)
    MAILBOX_MATERIALIZE = "mailbox_materialize"  # T-PCH-A3 mailbox 物化 (非 M-2.3 historical_failure_consolidation)


@unique
class DaemonOutcome(StrEnum):
    """DaemonRunCompleted.after_state.outcome per M-2.1 §3.2."""

    COMPLETED = "completed"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


@unique
class ObligationRetireCandidateReason(StrEnum):
    """ObligationRetireCandidate.after_state.candidate_reason per M-2.2 §3.1 (4 reasons).

    Patch M-2.2-A — surface candidates to Nature; M-2.2 does not retire.
    """

    TASK_COMPLETED_LONG_AGO = "task_completed_long_ago"
    PARENT_OBLIGATION_RETIRED = "parent_obligation_retired"
    NATURE_JUDGMENT_PROPOSED = "nature_judgment_proposed"
    CONSOLIDATION_EMERGED = "consolidation_emerged"


@unique
class CrossTaskFixCollisionType(StrEnum):
    """cross_task_fix_collision finding payload.collision_type per M-2.2 §7.2 (3 types).

    同 entity 被多个不同 task 的 fix 触碰 → collision; collision_type 描述碰撞形态。
    判别用 CIA forward_slice 证据 (RUN-073 库层): 同组 fix 都改同一 entity → same_entity_modified;
    若 CIA slice 显示一 fix 改的 entity 是另一 fix entity 的上/下游 → upstream_downstream_conflict。
    bidirectional_contract_conflict 需双向契约破坏证据 (更强判别, 保守留 future refinement)。
    """

    SAME_ENTITY_MODIFIED = "same_entity_modified"
    UPSTREAM_DOWNSTREAM_CONFLICT = "upstream_downstream_conflict"
    BIDIRECTIONAL_CONTRACT_CONFLICT = "bidirectional_contract_conflict"


@unique
class CrossTaskFixSuggestedResolution(StrEnum):
    """cross_task_fix_collision finding payload.suggested_resolution per M-2.2 §7.2 (3 values).

    M-2.2 不替 Nature/M-1.6 决策 — suggested_resolution 只是建议, finding 路由给 M-1.6 (§7.4)。
    默认偏保守 (nature_review_required), 除非 CIA 证据明确指向无影响 (no_action_needed)。
    """

    NATURE_REVIEW_REQUIRED = "nature_review_required"
    CASCADE_INVALIDATION_LIKELY = "cascade_invalidation_likely"
    NO_ACTION_NEEDED = "no_action_needed"


# ════════════════════════════════════════════════════════════════════════════════
#  semantic_judgment payload enums — Step 1.c category 2 (M-0.1 §3.2)
# ════════════════════════════════════════════════════════════════════════════════


@unique
class SemanticUpgradeChangeType(StrEnum):
    """SemanticUpgradeDeclaration.after_state.semantic_changes[].change_type per M-0.1 §3.2."""

    NEW_CONCEPT = "new_concept"
    NEW_EDGE = "new_edge"
    EDGE_MODIFIED = "edge_modified"
    CONCEPT_REFINED = "concept_refined"
    REFERENCE_ADDED = "reference_added"


@unique
class ObligationScopeJudgment(StrEnum):
    """ObligationScopeJudged.after_state.judgment per M-0.1 §3.2."""

    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    UNCERTAIN = "uncertain"


@unique
class ImpactType(StrEnum):
    """ImpactAssessmentMade.after_state.affected_entities[].impact_type per M-0.1 §3.2."""

    DIRECT = "direct"
    TRANSITIVE = "transitive"
    POTENTIAL = "potential"


@unique
class ConsistencyVerdictValue(StrEnum):
    """ConsistencyJudgmentMade.after_state.verdict per M-0.1 §3.2."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    PARTIALLY_CONSISTENT = "partially_consistent"


# T-L1-46: MismatchVerdictValue + MismatchRecommendedAction 已删 — 仅为 MismatchVerdict event
# 的 after_state 字段服务; 该 event (M-1.4 §3.4 v2.1) 删除后两枚举无 owner。详 LEDGER Conflict 10。


@unique
class AuditTriggerReason(StrEnum):
    """AuditTriggered.after_state.trigger_reason per M-0.1 §3.2 (4 reasons)."""

    SEMANTIC_CHECK_NEEDED = "semantic_check_needed"
    HIGH_RISK_PATCH = "high_risk_patch"
    OBLIGATION_VIOLATION_SUSPECTED = "obligation_violation_suspected"
    RANDOM_SAMPLE = "random_sample"
    # closure-scoped 审计 (towow audit-closure): 审 "artifact X 是否兑现被关 task Y 的 done_criteria",
    # emit 的 AuditTriggered 锚定被关 task Y (envelope-scoped 审计锚在 envelope 自己的 task)。
    CLOSURE_VERIFICATION = "closure_verification"


@unique
class AuditVerdictValue(StrEnum):
    """AuditVerdictReceived.after_state.verdict per M-0.1 §3.2."""

    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    CONDITIONAL_PASS = "conditional_pass"  # noqa: S105


# ════════════════════════════════════════════════════════════════════════════════
#  envelope / commit / finding / detection_rule / obligation enums — Step 1.c
# ════════════════════════════════════════════════════════════════════════════════


@unique
class NodeTouchedEntityType(StrEnum):
    """NodeTouched.target_entity_type per M-0.1 §3.6 (4 entity kinds touched by forks)."""

    CONCEPT = "concept"
    TASK = "task"
    FILE = "file"
    ARTIFACT = "artifact"


@unique
class NodeTouchedTouchType(StrEnum):
    """NodeTouched.touch_type per M-0.1 §3.6."""

    READ = "read"
    WRITE = "write"
    REFERENCE = "reference"


@unique
class FindingResolution(StrEnum):
    """FindingResolved.resolution per M-0.1 §3.8 (4) + M-1.5 §3.5 unresolved_risk (RUN-035 T-L1-53).

    unresolved_risk = 留作 known risk (reviewer 既不修也不撤回, 显式登记为未解风险)。
    """

    CONFIRMED_AND_FIXED = "confirmed_and_fixed"
    CONFIRMED_AND_ACCEPTED = "confirmed_and_accepted"
    RETRACTED = "retracted"
    ESCALATED = "escalated"
    UNRESOLVED_RISK = "unresolved_risk"  # M-1.5 §3.5 — 第 5 态, 留作 known risk


@unique
class DetectionRuleProposedSeverity(StrEnum):
    """DetectionRuleProposed.proposed_severity per M-0.1 §3.9."""

    WARNING = "warning"
    ENFORCED = "enforced"


@unique
class ObligationActivationSource(StrEnum):
    """ObligationActivated.activation_source per M-0.1 §3.10 (3 sources)."""

    MECHANICAL_FILTER = "mechanical_filter"
    RESOLVER_JUDGMENT = "resolver_judgment"
    FALLBACK_CONSERVATIVE = "fallback_conservative"


@unique
class ObligationViolatedRecommendedAction(StrEnum):
    """ObligationViolated.recommended_action per M-0.1 §3.10."""

    FIX = "fix"
    ESCALATE = "escalate"
    ACCEPT_WITH_JUSTIFICATION = "accept_with_justification"


@unique
class ObligationActiveSetRole(StrEnum):
    """ObligationActivated.active_set_role per M-0.6 §4.2.2 (RUN-068 件B).

    Mirrors the obligation's severity → decides placement in the capsule's active set
    (red_line obligations are placed最 prominently). 3 roles per §4.2.2.
    """

    RED_LINE = "red_line"
    NORMAL = "normal"
    HINT = "hint"


@unique
class ObligationMaterializedInto(StrEnum):
    """ObligationActivated.materialized_into per M-0.6 §4.2.2 (RUN-068 件B).

    Where the activation edge materialized the obligation. `capsule` is the common case
    (the obligation enters the capsule active set); `envelope_requirement` is the §4.2.2
    reserved extension slot (a future obligation that goes straight into envelope requirements).
    """

    CAPSULE = "capsule"
    ENVELOPE_REQUIREMENT = "envelope_requirement"


@unique
class ObligationViolatedDetectedBy(StrEnum):
    """ObligationViolated.detected_by per M-0.6 §4.2.4 (RUN-068 件B — 3 detection sources).

    m05_mechanical_pattern = §3.4.2 物理 forbidden_pattern 命中违反 (RUN-068 件C 物理匹配器);
    m05_audit_verdict = §5 audit verdict=reject + confirmed_violation; m05_self_reported =
    agent 自己声明 violated。M-0.5a Patch 1: ObligationViolated 只在 confirmed_violation 时产。
    """

    M05_MECHANICAL_PATTERN = "m05_mechanical_pattern"
    M05_AUDIT_VERDICT = "m05_audit_verdict"
    M05_SELF_REPORTED = "m05_self_reported"


@unique
class ObligationScopeJudgmentSource(StrEnum):
    """ObligationScopeJudged.judgment_source per M-0.6 §4.2.1 (RUN-068 件B — 3 sources).

    Same 3 sources as ObligationActivationSource (a scope verdict and the activation it may
    drive share provenance), kept a distinct enum to match the §4.2.1 vs §4.2.2 spec split:
    mechanical_filter (M-0.3 §2.3 机械过滤直接判) / resolver_judgment (resolver verdict) /
    fallback_conservative (resolver 不可用, conservative 包含).
    """

    MECHANICAL_FILTER = "mechanical_filter"
    RESOLVER_JUDGMENT = "resolver_judgment"
    FALLBACK_CONSERVATIVE = "fallback_conservative"


@unique
class VerdictEvidenceRefType(StrEnum):
    """ResolverVerdict.judgments[].evidence[].ref_type per M-0.6 §6.3."""

    EVENT_ID = "event_id"
    CONCEPT_NODE = "concept_node"
    AT_REFERENCE = "at_reference"


@unique
class ResolverOutputFormat(StrEnum):
    """ResolverInput.resolver_config.output_format per M-0.6 §6.2."""

    STRICT_JSON = "strict_json"


# ════════════════════════════════════════════════════════════════════════════════
#  envelope (M-0.4) enums — Step 1.e
#
#  E.1.1 Conflict 5 (resolved): patch-level PatchNoveltyType (5 values) and
#  event-level SupersedeNoveltyType (4 values) are distinct semantic dimensions.
#  When patch.is_supersede=true AND corresponding event also supersede, PatchEntry
#  requires PatchSupersedeBridge with explicit mapping_reason — no silent automatic
#  mapping. See SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 5.
# ════════════════════════════════════════════════════════════════════════════════


@unique
class EnvelopeReadSetEntityType(StrEnum):
    """TransactionEnvelope.read_set[].entity_type per M-0.4 §2 (6 values)."""

    CONCEPT = "concept"
    CONCEPT_EDGE = "concept_edge"
    TASK = "task"
    FILE = "file"
    OBLIGATION = "obligation"
    AT_REFERENCE = "at_reference"


@unique
class EnvelopeWriteSetEntityType(StrEnum):
    """TransactionEnvelope.write_set[].entity_type per M-0.4 §2 (7 values — read_set + patch)."""

    CONCEPT = "concept"
    CONCEPT_EDGE = "concept_edge"
    TASK = "task"
    FILE = "file"
    OBLIGATION = "obligation"
    AT_REFERENCE = "at_reference"
    PATCH = "patch"


@unique
class EnvelopeWriteChangeType(StrEnum):
    """TransactionEnvelope.write_set[].change_type per M-0.4 §2."""

    CREATED = "created"
    MODIFIED = "modified"
    REMOVED = "removed"


@unique
class EnvelopeClaimType(StrEnum):
    """TransactionEnvelope.claims[].claim_type per M-0.4 §2."""

    READ_CLAIM = "read_claim"
    WRITE_CLAIM = "write_claim"


@unique
class PatchNoveltyType(StrEnum):
    """TransactionEnvelope.patches[].novelty_type per M-0.4 §2 (5 values).

    E.1.1 Conflict 5 (resolved): renamed from `EnvelopeNoveltyType` to distinguish from
    `SupersedeNoveltyType` (event-level, 4 values). See SPEC-CONFLICT-RESOLUTION-LEDGER.md.

    PatchNoveltyType is **patch-level** — "agent 声明这个 patch 为什么有新意"
    (给 commit gate 校验). Different domain from SupersedeNoveltyType.

    When patch.is_supersede=true AND corresponding domain event supersede.is_supersede=true,
    PatchEntry.supersede_bridge must explicitly map patch_novelty_type → supersede_novelty_type
    with mapping_reason. No silent automatic mapping.
    """

    NEW_CONSTRAINT = "new_constraint"
    NEW_EVIDENCE = "new_evidence"
    NEW_RESOLUTION = "new_resolution"
    SCOPE_CHANGE = "scope_change"
    NATURE_OVERRIDE = "nature_override"


# Backward-compat alias — to be removed after E.2.
EnvelopeNoveltyType = PatchNoveltyType


@unique
class EnvelopeUncertaintySeverity(StrEnum):
    """TransactionEnvelope.uncertainties[].severity per M-0.4 §2."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@unique
class EnvelopeUncertaintySuggestedAction(StrEnum):
    """TransactionEnvelope.uncertainties[].suggested_action per M-0.4 §2."""

    REVIEW_RECOMMENDED = "review_recommended"
    NATURE_INPUT_NEEDED = "nature_input_needed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"


@unique
class PlanningUncertaintySeverity(StrEnum):
    """PlanningUncertainty.severity per M-1.3 §3.7 (M-1.3a 审查补).

    red_line = 阻塞 plan freeze (未解决前 fail-closed); advisory = 记录但不阻塞。
    """

    RED_LINE = "red_line"
    ADVISORY = "advisory"


@unique
class PlanningUncertaintyResolutionAction(StrEnum):
    """PlanningUncertainty.resolution_action per M-1.3 §3.7."""

    ASK_NATURE = "ask_nature"                    # 需要 Nature 决策
    RETURN_TO_INTERVIEW = "return_to_interview"  # 回 M-1.1 追问
    INVESTIGATION_TASK = "investigation_task"    # 产 investigation task 先调研
    DEFER_TO_EXECUTION = "defer_to_execution"    # 执行时才能确定


# ════════════════════════════════════════════════════════════════════════════════
#  capsule (M-0.3) enums — Step 1.f
# ════════════════════════════════════════════════════════════════════════════════


@unique
class CapsuleSection(StrEnum):
    """Capsule 8 sections per M-0.3 §7.2 priority order (high → low truncation order).

    Priority semantic (truncation order): RED_LINE_OBLIGATIONS (never) >
    TASK (never) > MUST_RETURN (never) > MUST_NOT_DO > TASK_BRIEFING >
    KNOWN_FACTS > FILES_TO_READ > TOOL_CONTEXT.
    """

    RED_LINE_OBLIGATIONS = "RED_LINE_OBLIGATIONS"
    TASK = "TASK"
    MUST_RETURN = "MUST_RETURN"
    MUST_NOT_DO = "MUST_NOT_DO"
    TASK_BRIEFING = "TASK_BRIEFING"
    KNOWN_FACTS = "KNOWN_FACTS"
    FILES_TO_READ = "FILES_TO_READ"
    TOOL_CONTEXT = "TOOL_CONTEXT"
    # T-L0-13 (波1) M-0.3 §2.6 — low_confidence obligation 落软提醒区 (最低优先, 不强断言仅提示)
    HIDDEN_REMINDER = "HIDDEN_REMINDER"


@unique
class ProjectionName(StrEnum):
    """Named projections per M-0.2 §3 (10 core) + Phase D escalation_lifecycle + UI views.

    Core 10 (M-0.2 §3 graph-state projections):
      concept_graph / task_graph / obligation_lifecycle_state / ownership /
      at_reference_graph / consumer_relation / finding_lifecycle /
      detection_rule_lifecycle / escalation_lifecycle (Patch M-2.3-E) / commit_history
    UI view projections (6, deferred to E.5):
      review_inbox / dashboard / capsule_inspector / resolver_trend /
      obligation_heatmap / drift_stream
    """

    # ─── 10 core graph-state projections (M-0.2 §3) ───
    CONCEPT_GRAPH = "concept_graph"
    TASK_GRAPH = "task_graph"
    OBLIGATION_LIFECYCLE_STATE = "obligation_lifecycle_state"
    OWNERSHIP = "ownership"
    AT_REFERENCE_GRAPH = "at_reference_graph"
    CONSUMER_RELATION = "consumer_relation"
    FINDING_LIFECYCLE = "finding_lifecycle"
    DETECTION_RULE_LIFECYCLE = "detection_rule_lifecycle"
    ESCALATION_LIFECYCLE = "escalation_lifecycle"  # Patch M-2.3-E
    COMMIT_HISTORY = "commit_history"
    # M-1.2 §10.2 Patch T — per-plan 批次冻结状态 (T-L1-15, Patch T 第 3 个 reducer)。
    ENGINEERING_CONSENSUS_INDEX = "engineering_consensus_index"
    # ─── 6 UI view projections (M-0.2 §3, stub in E.1) ───
    REVIEW_INBOX = "review_inbox"
    DASHBOARD = "dashboard"
    CAPSULE_INSPECTOR = "capsule_inspector"
    RESOLVER_TREND = "resolver_trend"
    OBLIGATION_HEATMAP = "obligation_heatmap"
    DRIFT_STREAM = "drift_stream"


@unique
class MaintenanceMode(StrEnum):
    """maintenance scene_type caller_params.maintenance_mode per Patch M-2.2-D.

    Distinguishes M-0.7 maintenance modes (consolidate / snapshot / gc / audit-consolidation).
    """

    CONSOLIDATE = "consolidate"
    SNAPSHOT = "snapshot"
    GC = "gc"
    AUDIT_CONSOLIDATION = "audit-consolidation"


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.3 散落形态迁移工具 enums (RUN-066) — §3.1 / §3.2 / §5.1
# ════════════════════════════════════════════════════════════════════════════════


@unique
class MigrationSourceType(StrEnum):
    """M-3.3 §3.1/§3.2 catalog source_type — 5 类散落源 (枚举值定死)."""

    CLAUDE_RULES = "claude_rules"
    ADR = "adr"
    TOWOW_STATE = "towow_state"
    MEMORY = "memory"
    KNOWLEDGE_PACK = "knowledge_pack"


@unique
class MigrationPrimaryIntent(StrEnum):
    """M-3.3 §3.2 analysis.primary_intent — AI 分析判别的源主意图 (5)."""

    DECLARATIVE_RULE = "declarative_rule"  # "X 必须 Y" — 倾向 obligation
    DESIGN_DECISION = "design_decision"  # "我们选 X 是因为 Y" — 倾向 concept
    HISTORICAL_STATE = "historical_state"  # "当前状态是 X" — 倾向 backfill event
    BEHAVIORAL_NORM = "behavioral_norm"  # "agent 应该 X" — 倾向 obligation
    KNOWLEDGE_DEFINITION = "knowledge_definition"  # "X 是 Y" — 倾向 concept


@unique
class MigrationEntityType(StrEnum):
    """M-3.3 §3.2 analysis.identified_entities[].entity_type — AI 识别的实体类型 (4)."""

    CONCEPT = "concept"
    OBLIGATION = "obligation"
    STATE_MACHINE = "state_machine"
    DETECTION_RULE = "detection_rule"


@unique
class MigrationDecision(StrEnum):
    """M-3.3 §3.2 catalog source.decision — per-source owner review 后的迁移决定 (5)."""

    MIGRATE = "migrate"  # 走 batch 迁移流程
    SKIP_OBSOLETE = "skip_obsolete"  # v2.x 已废弃不适用 v3
    SKIP_NOT_APPLICABLE = "skip_not_applicable"  # 内容不属于 v3 概念框架
    DEFER = "defer"  # 需要 v3 框架先扩展 (常含 design gap)
    MANUAL_HANDLE = "manual_handle"  # 需要 Nature 单独决策


@unique
class MigrationDecidedBy(StrEnum):
    """M-3.3 §3.2 catalog source.decided_by — 谁做的决定 (2).

    v3 初版只允许 nature (Inv5 红线: AI 高 confidence 不自动决定/不自动 commit)。
    ai_high_confidence_auto 在 schema 保留 (spec §3.2 枚举), 但 v3 初版 owner-review 路径
    不产此值 — invariants.assert_owner_review_not_bypassed 物理拦截。
    """

    NATURE = "nature"
    AI_HIGH_CONFIDENCE_AUTO = "ai_high_confidence_auto"


@unique
class MigrationStepOutcome(StrEnum):
    """M-3.3 §5.2 MigrationStepRecorded.outcome — 单 source step 的终态 (5)."""

    MIGRATED_SUCCESS = "migrated_success"  # 翻译 + commit gate accept
    REJECTED_BY_COMMIT_GATE = "rejected_by_commit_gate"
    REJECTED_BY_NATURE = "rejected_by_nature"  # Nature reject batch
    DEFERRED = "deferred"
    SKIPPED = "skipped"


@unique
class MigrationTranslatorType(StrEnum):
    """M-3.3 §5.1 origin_metadata.translation.translator_type (2)."""

    AI_ASSISTED = "ai_assisted"
    MANUAL_ONLY = "manual_only"


__all__ = [
    "ActivationGapType",
    "ActorType",
    "AdvisorConsultTrigger",
    "AdvisorVerdictConfidence",
    "AdvisorVerdictDecision",
    "AtReferenceType",
    "AuditTriggerReason",
    "AuditVerdictValue",
    "BaseClassification",
    "BatchStatus",
    "CapabilityClassification",
    "CapsuleSection",
    "CascadeAffectedEntityType",
    "CascadeAffectedViaSlice",
    "CascadeResponseExpectation",
    "CheckerPatternType",
    "CommitVerdict",
    "ConceptCreatedStage",
    "ConceptEdgeDirection",
    "ConceptEdgeType",
    "ConsistencyVerdictValue",
    "CrossTaskFixCollisionType",
    "CrossTaskFixSuggestedResolution",
    "DaemonName",
    "DaemonOutcome",
    "DebtSeverity",
    "DebtState",
    "DebtType",
    "DecidedState",
    "DecisionMadeBy",
    "DetectionRuleLifecycleState",
    "DetectionRuleProposedSeverity",
    "DigestType",
    "EnforcementLevel",
    "EnvelopeClaimType",
    "EnvelopeNoveltyType",  # backward-compat alias of PatchNoveltyType
    "EnvelopeReadSetEntityType",
    "EnvelopeUncertaintySeverity",
    "EnvelopeUncertaintySuggestedAction",
    "EnvelopeWriteChangeType",
    "EnvelopeWriteSetEntityType",
    "EscalationLifecycleState",
    "EscalationResolution",
    "EventCategory",
    "EventType",
    "EvidenceSourceType",
    "ExpectedNatureAction",
    "FindingDetectionMethod",
    "FindingKind",
    "FindingLifecycleState",
    "FindingResolution",
    "FindingSeverity",
    "FixOutcome",
    "GroupingDimension",
    "ImpactType",
    "InformationNeedLifecycleStatus",
    "InformationNeedPriority",
    "InformationNeedResolutionMethod",
    "InformationNeedSource",
    "InformationNeedSourceStage",
    "InformationNeedStatus",
    "JudgmentType",
    "LearningType",
    "MaintenanceMode",
    "MigrationDecidedBy",
    "MigrationDecision",
    "MigrationEntityType",
    "MigrationPrimaryIntent",
    "MigrationSourceType",
    "MigrationStepOutcome",
    "MigrationTranslatorType",
    "MismatchAffectedAspect",
    "MismatchDetectionTime",
    "MismatchResolutionAction",
    "MismatchResolutionOutcome",
    "MismatchSeverity",
    "NodeTouchedEntityType",
    "NodeTouchedTouchType",
    "NoveltyType",
    "ObligationActivationSource",
    "ObligationActiveSetRole",
    "ObligationAttachedToEntityType",
    "ObligationCanonicalState",
    "ObligationCaptureSource",
    "ObligationCheckMethod",
    "ObligationCheckOutcome",
    "ObligationDeclaredStatus",
    "ObligationFieldsLifecycleState",
    "ObligationMaterializedInto",
    "ObligationNature",
    "ObligationRetireCandidateReason",
    "ObligationScopeJudgment",
    "ObligationScopeJudgmentSource",
    "ObligationScopeType",
    "ObligationSeverity",
    "ObligationViolatedDetectedBy",
    "ObligationViolatedRecommendedAction",
    "PatchChangeType",
    "PatchNoveltyType",
    "PatchType",
    "PlanningUncertaintyResolutionAction",
    "PlanningUncertaintySeverity",
    "ProjectionName",
    "RePlanSuggestedAction",
    "RePlanTriggerSource",
    "RejectionType",
    "ResolverOutputFormat",
    "ScanMethod",
    "SceneType",
    "SemanticUpgradeChangeType",
    "SnapshotType",
    "SubjectEntityType",
    "SubjectRole",
    "SupersedeNoveltyType",
    "TargetEntityType",
    "TaskClosureReason",
    "TaskClosureRefType",
    "TaskDependencyStrength",
    "TaskDependencyType",
    "TaskModelTier",
    "TaskRunOutcome",
    "TaskType",
    "TransitionType",
    "TriageCategory",
    "VerdictEvidenceRefType",
    "emit_detection_rule_state",
    "is_canonical_emit_detection_rule_state",
    "is_reserved_detection_rule_state",
    "parse_detection_rule_state",
]
