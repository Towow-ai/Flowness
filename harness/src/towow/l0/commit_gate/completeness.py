"""M-0.5 gate-side semantic-completeness requirement check (RUN-027 block 3 — F-026-1).

# spec source:
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#     §1.2 M-0.5 是协议执行者, 语义在 L1 各层 — 门**消费** L1 完整性检查结果, 不自己判语义
#     §971 / §1118 — 否决 self_check.passed / uncertainties 当判据 (M-0.4 不变量, 只留痕);
#       本检查用 structured blocking_checks[].{check_id,status} (客观检查结果), 不用 passed 布尔
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §8.3.5 "commit gate accept → 本批冻结" — 门是 freeze 的 enforcement 点
#     §64 / §7 每 concept 必跟 ConsumerListPublished, 否则不 freeze
#   docs/DOGFOOD-RUN-026-FINDINGS.md F-026-1
#
# Why this module (F-026-1, roadmap §1 block-3):
#   门只验"格式齐不齐"、不验"spec 设计的完整性检查跑没跑": 一个 stage-freeze envelope 带
#   **零 blocking_check** 时门无可拒 → 结构合法语义空心的提交干净过关 (RUN-026 实证: consensus
#   freeze 漏消费者扫描却 freeze 成功)。根因不是"门天生验不了语义", 是 spec 已分层设计的完整性
#   检查没 wire 进门 (软 prompt / 可旁路 CLI 守卫)。
#
#   对治: "缺必需检查 ≠ 检查通过"。门按 domain 事件类型知道哪些完整性检查是**必需**的, 要求它们
#   present + status=passed; 缺失或失败一律 fail-closed。这把"总闸"做实在 M-0.5 (不可被某条 emit
#   路径旁路), 而不是停在某个 CLI 命令里。
#
#   block-3 wires the M-1.2 consensus-freeze consumer-scan check (the canonical F-026-1
#   instance). Other stages (M-1.3 PlanFreezed coverage_completeness, M-1.5 review contract)
#   register their required checks when block-4 migrates those stages to populate them — see
#   REQUIRED_COMPLETENESS_CHECKS extension note.
"""

from __future__ import annotations

from dataclasses import dataclass

from towow.schemas.enums import EventType

# domain event_type → required completeness blocking_check_ids (must be present + passed).
# Spec-grounded; extend per stage as block-4 migrates that stage to populate the checks.
REQUIRED_COMPLETENESS_CHECKS: dict[EventType, frozenset[str]] = {
    # M-1.2 §64/§7 — consumer scan completeness before consensus freeze (F-026-1 instance).
    # M-2.1 §4.1 消费方发现 caller-1 — consensus.cia_downstream_scan: freeze MUST run the real CIA
    # forward_slice over each new concept (records the transitive downstream blast radius along the
    # impact_graph). Requiring it present here makes the scan un-bypassable at the M-0.5 总闸 — a
    # freeze that skips it (check absent) is rejected. The scan itself is record-only (status always
    # passed); enforcement = "must have run", same shape as consumer_scan_complete. NOTE it抓的是
    # 沿图边的传递性下游 (N 跳), 与 consumer_scan_complete (扫 task read/write set) 不同, 不冗余.
    # design-maintainability-gate@v1 强制门 (owner 指令"以后所有设计都必须过这一关"的总闸载体):
    # 一个 freeze 了 ≥1 概念的 EngineeringConsensusFreezed 必须携带 PASSED
    # consensus.maintainability_clause_present (freeze 时带 well-formed 可维护性条款)。run_batch_freeze_checks
    # 恒 append 本 check (空冻结 status=passed 免带), 故它恒在信封 → 总闸要求它在场且 passed → 缺条款的
    # 概念冻结 (含绕 CLI 直 emit) 被 M-0.5 物理拒, 不可旁路 (堵 rc-freeze-gate-only-partial-enforced-soft-path-bypass)。
    EventType.ENGINEERING_CONSENSUS_FREEZED: frozenset(
        {
            "consensus.consumer_scan_complete",
            "consensus.cia_downstream_scan",
            "consensus.maintainability_clause_present",
        },
    ),
    # T-L1-65 (M-1.5 §0/§5 评审接回必经路): a FindingCreated must carry its review-contract —
    # the M-1.5 review skill populates review.review_contract_present; a FindingCreated emitted
    # off the review path (no contract) is rejected here, so review is a necessary path (review
    # on the critical path = a system-level ruler), not a bypassable CLI guard. Was a comment-
    # only placeholder before this (fake-done).
    EventType.FINDING_CREATED: frozenset({"review.review_contract_present"}),
    # RUN-038 L0增强 (F-026-1 总闸 extends to the plan stage): M-1.3 §3.8 self_check.blocking_checks
    # MUST carry {check_id: planning.coverage_complete} — the completion_condition 100%-coverage
    # proof ("deliverable 拆全没" is the M-1.3-layer completeness bottom, DOGFOOD-RUN-026 F-026-1).
    # The `plan freeze` CLI computes this check (plan_freezed.check_coverage_complete, T-L1-30 real
    # coverage gate) and populates it into the envelope self_check; a PlanFreezed reaching the gate
    # WITHOUT it (e.g. a direct emit bypassing the CLI) is rejected here — coverage completeness is
    # enforced at M-0.5 (un-bypassable 总闸), not only at the CLI. Was a comment-only extension
    # point before this (the plan-stage F-026-1 hole). Only the coverage-completeness check is the
    # gate's concern; the other §3.8 blocking_checks (self_contained / no_circular_dependency /
    # critical_path / high_risk_review) are structural/quality and stay CLI-side fail-closed.
    # M-2.1 §4.1 消费方发现 caller-2 — planning.cia_dependency_scan: plan freeze MUST run the real CIA
    # forward_slice over each concept its tasks touch (records the transitive downstream tasks via
    # impact_graph 概念边). Requiring it present here makes the scan un-bypassable at the M-0.5 总闸 —
    # a PlanFreezed that skips it (check absent) is rejected. The scan itself is record-only (status
    # always passed); enforcement = "must have run", same shape as coverage_complete. NOTE it抓的是
    # 沿图边的传递性下游 task (N 跳), 与 no_resource_conflict (直接 write_set 交集) 不同, 不冗余.
    # T-LRF-12 (migration-done-requires-old-path-removal@v1) — planning.migration_old_path_removal:
    # 迁移类 task (concept_refs 含迁移概念) 的 done_criteria 必须含一条 old-path-removal 形态判据
    # (grep machine_check, expected_occurrences=0, 指名旧符号), 缺则该 check status=failed → freeze 拒。
    # 注册进总闸后, 一个 PlanFreezed 缺本 check (绕 CLI 直 emit) 也被 M-0.5 拒 — 不可旁路。这就是概念
    # 要求的 "接 honesty-enforced-completeness-and-debt 完整性门家族" (registry 从 plan-freeze 阶段
    # 扩到本 check)。主 plan freeze CLI 把本 check 一并 surface 进信封 (与 coverage/cia_scan 同处理)。
    # C-5 (solution-6(A) / 20-layer23 §声称1 / substrate 层②③): 把 3 道确定性硬约束从 CLI 软路径提进
    # 总闸——绕 CLI 直 emit 一个 PlanFreezed (缺这3道) 被 M-0.5 物理拒。= 并行计划的"提交点冲突物理校验"
    # (owner 要的: 进入执行前物理确认计划无跨计划冲突/无环/无未决红线, 像 git 提交点的冲突检查, 不可旁路)。
    # check_id 精确对齐 CLI run_all_blocking_checks (plan_freezed.py:1540/1548/1550); surface 集
    # (main.py _gate_required_checks 两处) 同步加这3道, 否则合法冻结因 check 不在信封被 fail-closed 拒。
    # 修2 / live-fire-default-three-point-enforcement@v1 第10道门 — planning.new_capability_livefire_
    # machine_check: 被 new-capability-task-classifier 三信号判为新增能力且已发布 package 的 plan task,
    # 其 done_criteria 必须含 ≥1 条 live-fire 形态判据 (machine_check test 型 + test_selector 指向集成
    # 测试)。plan_freezed docstring (check_new_capability_tasks_have_livefire_machine_check) 明写"绕 CLI
    # 直 emit 的总闸兜底归本模块", 但此前只在 CLI-side fail-closed —— 注册进总闸后, 一个 PlanFreezed 缺
    # 本 check (绕 CLI 直 emit) 也被 M-0.5 物理拒, 与其结构孪生 migration_old_path_removal (同为
    # class→form→fail-closed 的 honesty-completeness 家族) 对称、不可旁路。主 plan freeze CLI 把本 check
    # 一并 surface 进信封 (与 coverage/cia_scan/migration 同处理; 两处 _gate_required_checks 同步)。
    EventType.PLAN_FREEZED: frozenset(
        {
            "planning.coverage_complete",
            "planning.cia_dependency_scan",
            "planning.migration_old_path_removal",
            "planning.new_capability_livefire_machine_check",
            "planning.no_circular_dependency",
            "planning.no_cross_plan_resource_conflict",
            "planning.no_unresolved_red_line_uncertainty",
        },
    ),
    # M-2.1 §4.1 消费方发现 caller-3 — review.cia_upstream_scan: review plan-create MUST run the
    # real CIA backward_slice over the THIS-batch frozen concepts (the EngineeringConsensusFreezed
    # being reviewed, resolved from the review session's trigger_event_id), recording the transitive
    # UPSTREAM prerequisite concepts (沿 impact_graph 概念入边, 本批共识依赖谁). Requiring it present
    # here makes the scan un-bypassable at the M-0.5 总闸 — a ReviewPlanCreated that skips it (check
    # absent, e.g. a direct emit bypassing the CLI) is rejected. The scan is record-only (status
    # always passed); enforcement = "must have run", same shape as caller-1/caller-2. review_plan is
    # the first link in the review_plan chain to require a completeness check. NOTE direction is
    # backward (上游前提) — caller-1/2 are forward (下游消费方); the two抓相反方向, not redundant.
    EventType.REVIEW_PLAN_CREATED: frozenset({"review.cia_upstream_scan"}),
    # M-2.1 §4.1 消费方发现 caller-4 — fix.cia_cascade_scan: `fix complete` MUST run the real CIA
    # forward_slice over each concept the fix changed (FixProposed.affected_entities[concept] ∪
    # FixCompleted.semantic_upgrade_declaration.affected_concepts), recording the transitive
    # downstream blast radius (沿 impact_graph 概念边). Requiring it present here makes the scan
    # un-bypassable at the M-0.5 总闸 — a FixCompleted that skips it (check absent) is rejected. The
    # scan is record-only (status always passed) — 🔴 it NEVER triggers a cascade (no compute_cascade,
    # no InvalidationCascade — auto-cascade is 断点 A's daemon, INF-003-gated). All THREE outcomes
    # (resolved / partially_resolved / needs_further_review) must carry it (the CLI appends it
    # unconditionally, outside the outcome-if). Same enforce shape as caller-1/2/3.
    EventType.FIX_COMPLETED: frozenset({"fix.cia_cascade_scan"}),
    # M-3.3 §4.4 (RUN-066): 迁移 batch envelope 必含 5 个 migration-specific blocking_checks。
    # MigrationStepRecorded 进 apply-batch 的 commit gate envelope → 把 5 检查注册成此 event_type
    # 的"必需完整性检查", 让总闸 (M-0.5) 物理强制: 一个 migration batch 缺 confidence_threshold /
    # origin_complete 等任一检查 (或 status≠passed) → gate reject (§13.3 Inv5 修正方向 "confidence
    # threshold 必须 blocking", Inv2 origin 完整)。即使绕过 CLI 直 emit 也拦得住 (不可旁路总闸)。
    EventType.MIGRATION_STEP_RECORDED: frozenset(
        {
            "migration.source_origin_complete",
            "migration.target_concept_consistency",
            "migration.target_obligation_well_formed",
            "migration.translation_confidence_threshold",
            "migration.no_self_supersede_within_batch",
        },
    ),
}


@dataclass(frozen=True)
class CompletenessResult:
    """Outcome of the gate-side completeness-requirement check."""

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] | None = None


def check_completeness_requirements(
    domain_event_types: list[EventType],
    self_check: object,
) -> CompletenessResult:
    """Verify every required completeness check for the batch's domain events is present
    in self_check.blocking_checks AND status=passed.

    "Missing a required check" is the core F-026-1 fix: an absent check is NOT a pass.
    Present-but-failed is also rejected here (defence in depth; the gate's generic
    blocking_check loop catches it too). Uses structured check_id/status only — never the
    self_check.passed boolean (M-0.4 invariant, M-0.5 §971).
    """
    required: set[str] = set()
    for et in domain_event_types:
        required |= REQUIRED_COMPLETENESS_CHECKS.get(et, frozenset())

    # T-R01-1 (c-minimal-change-set-declaration-gate@v1) — SIS 必填下沉到 M-0.5 总闸 (不只 CLI sys.exit):
    # 一个 execution 落地 run 的 TaskRunCompleted (envelope 自带 execution.* blocking_check) 必须声明
    # 起始影响集 SIS (execution.sis_declared present+passed); 缺则拒 —— 关掉"绕过 work-complete CLI
    # 直 emit 一个缺 SIS 的改动落地"旁路 (INV-R01-sis-gate-required: 'commit gate 必填,缺则拒')。
    # ⚠ 条件式 (非静态注册 dict): 只对**带 execution.* check** 的 TaskRunCompleted 触发。故
    #   - review conclude 的 TaskRunCompleted (空 blocking_checks, 不落地概念改动) → 不触发, verdict 门不破;
    #   - fix 的 FixCompleted (fix.* check, 非 execution.*) → 不触发;
    #   - 一个零代码空壳 success (自带 execution.done_criteria/git_committed 等却无 sis_declared) → 拒
    #     (finding-t-r01-4-fake-done 那类假完成同被堵: 它带 execution.* 五项但无 SIS)。
    if EventType.TASK_RUN_COMPLETED in domain_event_types and any(
        cid.startswith("execution.") for cid in _blocking_checks_by_id(self_check)
    ):
        required.add("execution.sis_declared")
        # T-RMD-FIX3 (修3 / live-fire-default-three-point-enforcement@v1 的 execution/commit 相位牙齿):
        # live-fire 默认强制的第三处咬合。带 execution.* 的 closure 必须 present+passed 一条
        # execution.livefire_recomputed —— = "done_criteria 里的 test 型 live-fire machine_check 真经 LB1
        # subprocess (pytest 读真账本断 live 签名) 复算过", 由 work-complete CLI 的
        # execution_done_recompute.recompute_livefire_check 无条件注入 (新增能力 live-fire task → real
        # passed; 无 test 型 machine_check 的非新增能力 closure → vacuous passed, 镜像 sis 对 abort 的
        # vacuous present)。绕 work-complete CLI 直 emit 一个缺它的新增能力 TaskRunCompleted → 本门
        # fail-closed 拒 (EventLog 无该 closure 落地) —— 把 live-fire "被真复算" 从 CLI 软路径下沉到
        # M-0.5 不可旁路总闸 (绕 CLI 也拦)。形态镜像 execution.sis_declared: 同一条件式触发 (非静态
        # 注册 dict), 只对带 execution.* 的 TaskRunCompleted; review conclude (空 blocking_checks) /
        # fix (fix.* check) 不触发。【两者关系】修2 (plan_freezed 第10门) 管"冻结时必须带"; 修3 +
        # execution_done_recompute 管"执行时被真复算"。
        required.add("execution.livefire_recomputed")
        # T-RMD-S4-BYTE-IDENTITY (f-sub-byte-identity-gate-missing) — byte-identity 部署门下沉总闸: 带
        # execution.* 的 closure 必须 present+passed 一条 execution.deploy_byte_identity —— = "本 run 碰过
        # 的 src/towow/{skills,glue} 部署源 sha256(render(src))==sha256(live), 无 src↔live 字节漂移", 由
        # work-complete CLI 的 build_deploy_identity_check 无条件产 (碰部署面 → 真核; 无命中 → vacuous
        # passed), success 路径经 run_all_execution_blocking_checks 恒带、abort 路径注入 vacuous 版。绕
        # work-complete CLI 直 emit 一个缺它的 execution closure → 本门 fail-closed 拒 —— 把 byte-identity
        # "被真核" 从 CLI 软路径下沉到 M-0.5 不可旁路总闸, 与其 execution.* 兄弟 (sis_declared /
        # livefire_recomputed 同一条件式触发) 对称。形态镜像上面两条: 同条件式, 只对带 execution.* 的
        # TaskRunCompleted; review conclude / fix 不触发。
        required.add("execution.deploy_byte_identity")

    if not required:
        return CompletenessResult(passed=True)

    by_id = _blocking_checks_by_id(self_check)
    for check_id in sorted(required):
        check = by_id.get(check_id)
        if check is None:
            return CompletenessResult(
                passed=False,
                failure_reason="completeness_check_missing",
                failure_evidence={"check_id": check_id},
            )
        if check.get("status") != "passed":
            return CompletenessResult(
                passed=False,
                failure_reason="completeness_check_failed",
                failure_evidence={"check_id": check_id, "status": str(check.get("status"))},
            )
    return CompletenessResult(passed=True)


def _blocking_checks_by_id(self_check: object) -> dict[str, dict[str, object]]:
    if not isinstance(self_check, dict):
        return {}
    blocking = self_check.get("blocking_checks")
    if not isinstance(blocking, list):
        return {}
    out: dict[str, dict[str, object]] = {}
    for check in blocking:
        if isinstance(check, dict):
            cid = check.get("check_id")
            if isinstance(cid, str) and cid:
                out[cid] = check
    return out


__all__ = [
    "REQUIRED_COMPLETENESS_CHECKS",
    "CompletenessResult",
    "check_completeness_requirements",
]
