"""RUN-015: Per-skill condition_text 模板系统 -- F-11 自动 spawn 用.

# spec source:
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md §7.4 dispatch table
#   02-meta-and-requirements/SYSTEM-REQUIREMENTS.md §四 P-12 (condition <=4000 字符 +
#     designer-style + 只 reference v3 已有 entity, 不重写 brief)
#   docs/dogfood-runs/DOGFOOD-RUN-005-launch/bucket-A-fix-launch.md (人工 reference 模板)
#
# Why this module:
#   Original orchestrator._generate_minimal_condition_text 是 minimal placeholder --
#   "你被 dispatch 到 X 处理 event Y". bg agent 没足够 context 真做事. RUN-005 那几个
#   bucket-launch.md 是人工写的高质量 prompt; 本模块系统化 -- 按 finding_kind 自动选
#   skill 路径 + 注入 finding event payload + 完成判据 + escalation 协议.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l2.orchestrator import DispatchDecision


@dataclass(frozen=True)
class SkillTarget:
    """Skill spawn target derived from finding_kind."""

    skill_id: str           # 形如 "M-1.5" / "M-1.6" / "M-1.2"
    skill_role: str         # 形如 "review" / "fix" / "engineering-consensus"
    skill_md_relpath: str   # 相对 harness/ 的 SKILL.md 路径


# M-3.1 §7.4 trigger contract + RUN-015 skill mapping
SKILL_REGISTRY: dict[str, SkillTarget] = {
    "obligation_issue": SkillTarget(
        "M-1.6", "fix", ".claude/skills/fix/SKILL.md",
    ),
    "adjacent_code_issue": SkillTarget(
        "M-1.6", "fix", ".claude/skills/fix/SKILL.md",
    ),
    "closure_contract_defect": SkillTarget(
        "M-1.6", "fix", ".claude/skills/fix/SKILL.md",
    ),
    "cross_task_fix_collision": SkillTarget(
        "M-1.6", "fix", ".claude/skills/fix/SKILL.md",
    ),
    "concept_issue": SkillTarget(
        "M-1.2", "engineering-consensus",
        ".claude/skills/engineering-consensus/SKILL.md",
    ),
    "review_plan_issue": SkillTarget(
        # T4 (E.4 closure) fix: init deploys src/towow/skills/review/ → .claude/skills/review/;
        # 旧值 m15-review 是不存在的别名 → 自动 spawn 的审查会话 load 不到人格。
        "M-1.5", "review", ".claude/skills/review/SKILL.md",
    ),
}


# f-c2-dispatch-command-text-source-divergence (LEDGER Conflict 32 双例实证: evt-82a4784e.../
# evt-20daa3a4...): orchestrator._route_event 对 concept_issue finding 的原始路由
# (SKILL_REGISTRY["concept_issue"] → engineering-consensus) 可能被
# _fix_layer_contradicts_consensus_seat 纠偏成 dispatch_to="fix" (suggested_fix_layer 指
# code 类座位矛盾)。worktree 命名 (_fix_worktree_id) 与 GoalSessionStarted.spawned_role 都
# 直接读纠偏后的 decision.dispatch_to, 但 generate_condition_text 过去只按 finding_kind 独立
# 重查 SKILL_REGISTRY, 看不到这次纠偏 —— 结果两条本该同源的产出 (工位名 vs 命令文本) 各用各的
# 推导, 派出 fix__ 前缀工位却塞进 /engineering-consensus 命令文本。按角色反查表, 供
# generate_condition_text 把 decision.dispatch_to 当最终真相, 覆盖按 finding_kind 查到的座位。
_ROLE_TARGETS: dict[str, SkillTarget] = {target.skill_role: target for target in SKILL_REGISTRY.values()}


# E.5 forward-chain registry: 上游"阶段完工事件 EventType" → 下一棒 skill.
# 区别于 SKILL_REGISTRY（按 finding_kind 派活的回环边）—— 这是 forward 方向的自动推进:
# 上一步干完 → 自动起下一步。
# spec: docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch E5-forward-chain-trigger-contract-1
#       + M-3.1 §7.4 trigger contract（本轮补 consensus→planning / plan→execution 两条主干边）
FORWARD_CHAIN_REGISTRY: dict[str, SkillTarget] = {
    # T-FIX-B5-01: 采访→共识第一跳。brief 发布 → 自动起工程共识接力 (对齐 spec "采访后
    # Nature 不需工程判断"=全自动)。InterviewBriefPublished 不在 REVIEW_TRIGGER_CONTRACT,
    # 故 resolve_review_mode 返 None, 只产前进边、不多派 review fan-out。顺分布式自愈哲学:
    # 仅 registry 加一条边, orchestrator 既有通用 forward-chain 分支自然生效, 不建中心总控。
    "InterviewBriefPublished": SkillTarget(
        "M-1.2", "engineering-consensus",
        ".claude/skills/engineering-consensus/SKILL.md",
    ),
    "EngineeringConsensusFreezed": SkillTarget(
        "M-1.3", "planning", ".claude/skills/planning/SKILL.md",
    ),
    "PlanFreezed": SkillTarget(
        "M-1.4", "execution", ".claude/skills/execution/SKILL.md",
    ),
    # 仅 outcome=success 且 source task_type!=review 时由 orchestrator 路由
    # (抑制 review-of-review; 见 orchestrator.py suppress_review_of_review)
    "TaskRunCompleted": SkillTarget(
        "M-1.5", "review", ".claude/skills/review/SKILL.md",
    ),
    "FixCompleted": SkillTarget(
        "M-1.5", "review", ".claude/skills/review/SKILL.md",
    ),
}


# M-1.5 §7.0 Orchestrator Trigger Contract (RUN-035 T-L1-54): 上游事件 → review mode.
# 独立于 FORWARD_CHAIN_REGISTRY（主干前进链, 一事件一下一棒）—— review 是正交旁路触发
# (同一事件额外触发评审, 不是前进链的下一棒)。advisor 钦点不污染 forward-chain value。
#   EngineeringConsensusFreezed → design_time  (额外触发, 主干 fwd 仍 → planning)
#   TaskRunCompleted(success)   → author_time   (fwd 本就 → review, 这里给它定 mode)
#   FixCompleted                → fix_after      (同上)
# RUN-039 debt-37cf41: 补齐 spec §7.0 剩 2 条触发边 (内容感知, 见 resolve_review_mode):
#   ReviewPlanCreated(v2 supersede) → author_time rerun  (plan 改好 → 用新 plan 重跑作者评审)
#   FindingCreated(meta-review critical) → design_time v2 reroute  (meta-review 否决 plan → 重产 v2)
# (M-1.5 §7.0 supersede 协议: meta-review 发现 review_plan v1 缺陷 → orchestrator 重调 design-time
#  产 ReviewPlanCreated v2(superseded_event_id=v1))。
REVIEW_TRIGGER_CONTRACT: dict[str, str] = {
    "EngineeringConsensusFreezed": "design_time",
    "TaskRunCompleted": "author_time",
    "FixCompleted": "fix_after",
}

_REVIEW_SKILL_TARGET = SkillTarget("M-1.5", "review", ".claude/skills/review/SKILL.md")


# 座位→模板表 (f-dispatch-letter-ignores-decision-seat): finding 驱动派发的信件模板必须表达
# decision.dispatch_to (路由权威) —— 所有路由纠偏 (如 orchestrator 的
# _fix_layer_contradicts_consensus_seat 座位矛盾门) 都落在 decision 层, FB-3 backlog 重放也只
# 重放 decision、不重路由。旧逻辑 generate_condition_text 选模板只查 finding_kind → SKILL_REGISTRY,
# 完全不看 decision.dispatch_to: 纠偏把 concept_issue+fix_layer=code 的 finding 路由到 fix 席后,
# 信仍按 finding_kind 拼成 /engineering-consensus 模板 —— 被派会话开局收到自相矛盾的指令
# (元数据说 fix、信让它冻概念), 只能走误派发退场, 白烧一个会话 (活体实证:
# evt-57319bf… f-inv7-late-lease-403-recycle-path-untested, 2026-07-17)。
# 此表只列可被 finding 驱动 spawn 的 skill 座位; dispatch_to 是 surface 决策
# ("Nature dashboard" / "main-inbound" / "no-route") 时查不到 → 沿用 finding_kind 兜底, 行为不变。
_TARGET_BY_SKILL_ROLE: dict[str, SkillTarget] = {
    "fix": SkillTarget("M-1.6", "fix", ".claude/skills/fix/SKILL.md"),
    "review": SkillTarget("M-1.5", "review", ".claude/skills/review/SKILL.md"),
    "engineering-consensus": SkillTarget(
        "M-1.2", "engineering-consensus",
        ".claude/skills/engineering-consensus/SKILL.md",
    ),
}


# 产出回收闭环 (BRIEF-product-recovery-loop-fix-2026-06-26 fix #1): 派发出去的会话最常见的
# 丢结果方式 = 把"我做完了/产出是 X"写进对话回复 (它以为信息发给 owner 了), 但对话信息系统
# 【不回收】= 丢失。系统所有确定的共识只认 canonical 事件 (协议回收)。每个派发 prompt 的收尾
# 段都显式钉这条 (不是只在散文里提一句 GoalSessionTerminated), 让被派 agent 完成后主动走协议
# 回收 + 终止, 不把结果停在对话里。
_RECOVERY_PROTOCOL_BLOCK = (
    "完成的定义: 结论走协议回收落成 canonical 事件 (TaskRunCompleted / 阶段完工事件等), "
    "再 emit GoalSessionTerminated 收口——只写在对话里的结果系统不消费。\n"
)


# ── GOAL 目标循环 (T-RMD-GOAL, owner 2026-07-17 方向B: 重新落回 92c3644dd 的 /goal 前缀) ──
# autopilot bg 派发的 prompt 最前拼一行 `/goal <可机判完成条件>` = 激活 Claude Code 内置目标循环:
# 每轮快模型自评本会话开头 /goal 行那条完成条件是否达成, 未达成自动续跑, 达成或 /goal clear 才停。
# 完成条件锚到本派发该产出的 canonical 闭合事件 (TaskRunCompleted / PlanFreezed /
# EngineeringConsensusFreezed / FixCompleted / review verdict) = 真"完工"可观测信号, 非过程描述。
# is_autonomous_goal_session 据首条 user 文本 lstrip().startswith("/goal") 判自主会话 → 跳过
# owner-facing confirmation-loop guard (此前 prompt 以 /{skill_role} 开头 = 该判据一直假)。
_GOAL_LOOP_NOTE = (
    "本会话由开头 `/goal <完成条件>` 目标循环驱动: 条件达成即停; 判定无法达成时先 emit "
    "GoalEscalationRaised / GoalSessionTerminated 落终态, 再 `/goal clear` 停循环。\n"
)


def _goal_completion_condition(
    *,
    role: str,
    decision: DispatchDecision,
    finding_id: str | None = None,
) -> str:
    """一行可机判完成条件 (用作 `/goal` 的 directive — 目标循环每轮自评是否达成)。

    锚到本派发该产出的 canonical 闭合事件 (真"完工"的可观测信号), 不是过程描述 —— 与会话该走的
    收口事件 (PlanFreezed / TaskRunCompleted / FixCompleted / EngineeringConsensusFreezed /
    review verdict) 对齐, 故循环自然在 towow 阶段真完工时停 (与会话终态 emit 收口同源)。

    必须单行 (换行在 `_goal_directive` 里被折叠, 否则会污染 directive 的评判键)。
    """
    review_mode = getattr(decision, "review_mode", None)
    task_id = getattr(decision, "task_id", None)
    if review_mode:
        if task_id:
            return (
                f"本次 {review_mode} review 完工: REVIEW task {task_id} 的 TaskRunCompleted "
                f"已 emit 且过 M-1.5 verdict 门 (审查结论落账)"
            )
        return f"本次 {review_mode} review 完工 (forward-chain, 无 REVIEW task): " + (
            _REVIEW_MODE_GOAL_ANCHOR.get(review_mode, _FORWARD_CHAIN_REVIEW_GOAL_ANCHOR_FALLBACK)
        )
    if role == "execution":
        tid = task_id or "(交接上下文里指派的那个 task)"
        return f"指派的 task {tid} 完工: TaskRunCompleted(outcome=success) 已 emit"
    if role == "planning":
        return "本计划冻结: PlanFreezed 已 emit (task 图 + 依赖 + package 全 publish)"
    if role == "engineering-consensus":
        if finding_id:
            return (
                f"concept_issue finding {finding_id} 处理完: 相关 ConceptCreated/Edge + "
                f"FixCompleted/FindingResolved 已 emit"
            )
        return "本 brief 的工程共识冻结: EngineeringConsensusFreezed 已 emit"
    if role == "fix":
        fid = finding_id or "(交接的那条 finding)"
        return f"finding {fid} 修复完工: FixCompleted 已 emit (引 closure_contract)"
    if role == "review":
        # 此分支只在 review_mode 为空时可达 (有 mode 的在上面早返回) = finding_kind 驱动的
        # review_plan_issue 派活, mode 事前不知道 → 诚实给两条真实锚点的析取, 不假装知道是哪个。
        fid = finding_id or "(交接的那条 finding)"
        return (
            f"finding {fid} 审查完工: 该产的 Finding 生命周期事件已 emit "
            f"(fix-after 是 FindingResolved; author-time 是 FindingCreated + FindingVerified)"
        )
    return (
        f"本次 {decision.dispatch_to} 派发完工: 其 canonical 完成事件已 emit + "
        f"GoalSessionTerminated 收口"
    )


def _goal_directive(condition: str) -> str:
    """把一行完成条件包成 `/goal` directive 放 prompt 最前 — 激活 Claude Code 内置目标循环。

    放最前一行是两个契约的硬要求:
    1. `claude --bg "/goal ..."` slash 命令从 prompt 起始解析 (激活内置目标循环);
    2. Stop-detect-confirmation-loop.is_autonomous_goal_session 据首条 user 文本
       lstrip().startswith("/goal") 判自主会话 → 跳过 owner-facing confirmation-loop guard。

    condition 折叠成单行 (首句要干净, 别让换行把它和后文糊在一起污染评判)。
    """
    oneline = " ".join(condition.split())
    return f"/goal {oneline}\n\n"


def _goal_termination_instruction(goal_session_id: str | None) -> str:
    """f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: 收尾指令文本。

    2026-07-17 事故 (esc-057ef69e4722) 根因: 旧文案只说"release .towow/locks/goal_session.lock"
    ——那是被最后 spawn 的邻居覆盖的共享单指针, 从不告诉 agent 自己的 goal_session_id, 逼 agent 靠
    读那把共享锁猜身份, 猜中邻居就把邻居当自己、误终止 + 误删邻居的锁。

    根治: 子会话进程 env 已被注入 TOWOW_SELF_GID (spawn 时预生成, 见 claude_bg_helper.
    _provision_spawn_env) —— `towow goal terminate` 现在默认直接读这个 env, 不用 agent 传参也不
    读共享锁。goal_session_id 非 None 时额外把值写进 prompt 文本, 供 agent 自我认知/debug 用
    (非权威来源, 权威来源是 env; 若 agent 手误传了 --goal-session-id 且与 env 不一致, CLI 会
    fail-closed 拒绝, 不会被覆盖成别的会话)。
    """
    self_id_line = (
        f"你自己的 goal_session_id = {goal_session_id} (已随子会话 env 注入, "
        f"`goal terminate` 会自动认领, 无需你手传 --goal-session-id)。\n"
        if goal_session_id
        else ""
    )
    return (
        f"{self_id_line}"
        f"具体: emit GoalSessionTerminated reason=completion 收口 (身份来自你进程自己的 "
        f"TOWOW_SELF_GID env, 不是共享锁——不要读/依赖 `.towow/locks/goal_session.lock`, "
        f"那把锁会被并发起的邻居会话覆盖, 靠它猜自己是谁 2026-07-17 实锤误终止过活邻居)。"
        f"你的阶段完工事件会自动触发下一棒，不用你手动起会话。\n"
    )


def _is_review_plan_supersede(payload: dict[str, object]) -> bool:
    """ReviewPlanCreated 是否一个 v2 supersede (带非空 superseded_event_id)。"""
    after = payload.get("after_state")
    src = after if isinstance(after, dict) else payload
    sup = src.get("superseded_event_id")
    return isinstance(sup, str) and bool(sup.strip())


def _is_meta_review_critical_finding(payload: dict[str, object]) -> bool:
    """FindingCreated 是否一条 meta-review 对 review_plan 的 critical 否决 (→ design_time v2 reroute)。"""
    after = payload.get("after_state")
    src = after if isinstance(after, dict) else payload
    dim = src.get("review_dimension") or src.get("review_plan_dimension_ref")
    severity = str(src.get("severity", "")).lower()
    return str(dim) == "meta-review" and severity == "critical"


def resolve_review_mode(
    trigger_event_type: str,
    payload: dict[str, object] | None = None,
) -> str | None:
    """M-1.5 §7.0: 上游事件 → review mode (None = 不触发 review)。

    3 条基础边按 event_type 映射; RUN-039 debt-37cf41 补的 2 条边内容感知 (看 payload):
      - ReviewPlanCreated 且 v2 supersede → author_time (用改好的 plan 重跑作者评审)
      - FindingCreated 且 meta-review critical → design_time (meta-review 否决 plan → 重产 v2)
    """
    base = REVIEW_TRIGGER_CONTRACT.get(trigger_event_type)
    if base is not None:
        return base
    if payload is None:
        return None
    if trigger_event_type == "ReviewPlanCreated" and _is_review_plan_supersede(payload):
        return "author_time"
    if trigger_event_type == "FindingCreated" and _is_meta_review_critical_finding(payload):
        return "design_time"
    return None

# 每类前进链派活的"目标 + 钩一句它已有的判断"(handoff skill 核心: 钩判断、不重教手册)。
# 持久人格(方法/为什么/失败模式)住各自 skill, 由派发信开头的 `/<role>` slash 装上; 这里只点
# 目标 + 钩那一句它本就知道的判断, 把路留给它。
_ROLE_GOAL_HOOK = {
    "fix": "把这条被发现的问题修干净——修一个问题，不制造下一个。",
    "review": (
        "把这次的产物跟它该满足的合约对照，找出真问题——你知道什么算放行、什么必须打回，照它来。"
    ),
    "engineering-consensus": (
        "把这批要做的事的共同前提定下来、冻成后续都得遵守的概念——你知道什么时候算对齐了。"
    ),
    "planning": (
        "把冻结的共识拆成一张能并行执行的 task 图——你知道好计划长什么样：可执行、依赖清楚、失败看得见。"
    ),
    "execution": (
        "把指派给你的这一个 task 做完、留下可验证的完成信号——你知道做到什么才算这步成了。"
    ),
}


# 每类的短打卡 (必守的关键动作, 说成"做到什么")。运行态命令 (它无从猜起的具体) 保留并给准;
# 方法/为什么住 skill, 不在这里重教。fix 走独立精简模板 (_generate_fix_condition_text), 此 fix
# entry 仅为旧 forward-chain 兼容保留, 当前 fix 派活不读它。
_COMPLETION_CRITERIA = {
    "fix": (
        "1. `fix start`（拿 session_id，之后 fix 子命令都带 `--session-id <它>`；它还会打印这次碰到的\n"
        "   概念定义文件路径，开工前读它）→ emit FixProposed，引上闭合合约\n"
        "2. 按合约修（代码 + 测试，ruff、mypy、pytest 全绿）\n"
        "3. `fix complete`（独立检查者按合约复算）→ emit FixCompleted（临时闭合，不标 finding 关闭，那由复查裁）"
    ),
    "review": (
        "1. `review start`（拿 session_id，之后 review 子命令都带 `--session-id <它>`；它还会打印被审\n"
        "   单元碰的概念定义文件路径，开工前读它）→ emit ReviewSessionStarted\n"
        "2. 按你这次的 mode（author-time / design-time / fix-after）找 finding 或验闭合\n"
        "3. 给出结论 + 依据：落成 Finding 生命周期事件（author-time → `finding-create` +\n"
        "   `finding-verify` 产 FindingCreated + FindingVerified；fix-after → `finding-resolve`\n"
        "   产 FindingResolved）→ `review conclude`。forward-chain review 没有独立的审查完成\n"
        "   事件，conclude 只清 session lock；只有 REVIEW-typed task（start 带 --task-id）的\n"
        "   conclude 才 emit TaskRunCompleted 过 verdict 门"
    ),
    "engineering-consensus": (
        "1. 先读上游的 brief（按上方 brief_id）——那是你要变成概念的真输入；`consensus start` 会打印 brief\n"
        "   的种子概念定义文件路径，开工前读它（拿 session_id，之后子命令都带 `--session-id <它>`）\n"
        "2. 把概念定下来：emit ConceptCreated / ConceptEdgeAdded；标出谁会消费它（ConsumerListPublished）\n"
        "3. 冻结：emit EngineeringConsensusFreezed（产新 plan_id）——触发下游 planning 自动接力"
    ),
    "planning": (
        "1. 先读上游冻结的共识（按上方 plan_id）——那是你要照着拆 task 的真输入；`plan start` 会打印本批\n"
        "   冻结概念的定义文件路径，开工前读它（拿 session_id，之后 plan 子命令都带 `--session-id <它>`）\n"
        "2. 拆 task 图：每个 task 标清它读/写哪些概念（read-set / write-set）；加依赖边、模型分层、\n"
        "   关键路径；发布 task 包\n"
        "3. 冻结：emit PlanFreezed——触发下游 execution 自动接力"
    ),
    "execution": (
        "1. 先 load 你 task 的 TaskPackagePublished——真简报: 目标、`done_criteria`（success 门照它\n"
        "   机器复算，按它精确命名你的测试）、read/write-set 都在里面\n"
        "2. `work start` 被指派的那个 task（只做这一个），带 `--touched-node <概念id>`（可重复，按 task\n"
        "   包 read-set）；之后 work 子命令带 `--session-id`；start 打印的概念定义文件开工前读\n"
        "3. 实施（代码 + 测试，ruff、mypy --strict、pytest 全绿）\n"
        "4. `work complete --outcome success --touched-node <真碰的概念id>`（必填，缺则门拒）→\n"
        "   emit TaskRunCompleted，自动触发下一棒"
    ),
}


# forward-chain review (review_mode 有值但没有 REVIEW task) 的 /goal 完成条件锚点, 按 mode 分。
# 与下面 _REVIEW_MODE_COMPLETION 同一组 key、同一件事的两面 (那张表说"要跑哪些命令", 这张表说
# "跑完什么才算完工") —— 改一张必须回头看另一张, 别让两张按 mode 分的表悄悄漂移。
#
# f-dispatch-template-names-nonexistent-reviewconcluded-event-forces-forwardchain-review-goal-
# unsatisfiable: 这里曾写死两个 EventType 枚举里根本不存在的 Review 完成事件名 (全账本历史各出现
# 0 次)。concept review-completion-is-finding-lifecycle@v1 明定 forward-chain review 没有独立的
# 审查完成事件: `review conclude` 只清 session lock (只有 REVIEW-typed task 的 conclude 才 emit
# TaskRunCompleted), 完工体现为 Finding 生命周期事件 (design_time 例外, 它产 ReviewPlanCreated)。
# 锚一个永不发生的事件, 后果不是文案不准: 会话干完全部实活、conclude 并落成 FindingResolved 之后
# Stop 门仍判条件未达成, 此时最省事的出路就是回头给 review start 补 --task-id 去凑一个
# TaskRunCompleted —— 而那正是 M-1.5 review skill 点名禁止、且实锤差点在 replan 后的已终态 task
# 上冲突 conclude 的动作。即锚点写错会持续把每个 forward-chain review 会话往落错账的方向推。
_REVIEW_MODE_GOAL_ANCHOR: dict[str, str] = {
    "design_time": "ReviewPlanCreated 已 emit (`review plan-create`) 且 review 已 conclude",
    "author_time": (
        "各维度找到的 finding 都已落成 FindingCreated + FindingVerified (verify-step 独立三态), "
        "一条都没找到则零条落账, 且 review 已 conclude"
    ),
    "fix_after": (
        "被验 finding 的 FindingResolved 已 emit (闭合门按 closure_contract 复算过) 且 review 已 conclude"
    ),
}

# 未知 mode 的兜底: 不知道该走哪条路时, 给全部三个真实存在的 Finding 生命周期事件名, 让会话自己
# 按实际 mode 挑 —— 宁可条件宽一点, 也不锚一个不存在的事件把 Stop 门变成永不满足。
_FORWARD_CHAIN_REVIEW_GOAL_ANCHOR_FALLBACK = (
    "该 mode 该产的 Finding 生命周期事件 (FindingCreated / FindingVerified / FindingResolved) "
    "已 emit 且 review 已 conclude"
)


# 三种 review mode 的短打卡。关键运行命令 (它无从猜起、漏了就开不了工的具体) 全保留并给准:
# `--mode` (不带 review 不知道跑哪个模式)、`--trigger-event-id` (不带 start 会拒)、交接给的
# `--task-id` (REVIEW task 据它过验收门才落账)、`--session-id` (并发下认得出是你)。方法/为什么住
# review skill, 由 `/review` slash 装上, 这里不重教。
# (完工锚点在上面 _REVIEW_MODE_GOAL_ANCHOR, 同一组 key —— 这里改动了跑法, 回头看那张表。)
_REVIEW_MODE_COMPLETION: dict[str, str] = {
    "design_time": (
        "1. `towow review start --mode design_time --trigger-event-id <现场信息里的 trigger event_id>`；\n"
        "   交接给了 `--task-id` 就原样照拼；之后子命令带 `--session-id`；start 打印的概念定义开工前读\n"
        "2. 让 review-plan-creator 读冻结的共识出 review_plan，meta-review 审一遍\n"
        "3. `towow review plan-create --plan-file <json>` → `towow review conclude`"
    ),
    "author_time": (
        "1. `towow review start --mode author_time --trigger-event-id <现场信息里的 trigger event_id>`；\n"
        "   交接给了 `--task-id` 就原样照拼；之后子命令带 `--session-id`；start 打印的概念定义开工前读\n"
        "2. 读关联 review_plan，按维度找 finding；每条经 verify-step 独立验（三态）\n"
        "3. `towow review finding-create` + `finding-verify` → `conclude`（有未解决的 verified-FAIL 则门拒）"
    ),
    "fix_after": (
        "1. `towow review start --mode fix_after --trigger-event-id <现场信息里的 trigger event_id>`；\n"
        "   交接给了 `--task-id` 就原样照拼；之后子命令带 `--session-id`；start 打印的概念定义开工前读\n"
        "2. 读关联 finding（含闭合合约）+ fix 补丁；verify-step 做有界闭合复验\n"
        "3. `towow review finding-resolve --closure-file <json>`；闭合不够 → reopen 新 finding；conclude"
    ),
}


# E.5: 上游阶段完工事件 → 从其 payload 抽取交接给下一棒的上下文键
# (兼容 stub-rewrap 顶层 + canonical after_state 两种 payload 形状)
def _payload_field(payload: dict[str, object], key: str) -> str | None:
    """从上游事件 payload 读一个字段, canonical after_state 优先、回退 stub-rewrap 顶层。

    抽出来给 _extract_handoff_context (仅供阅读的交接信息段) 与
    _consensus_completion_with_anchor (T-BOOT-C3: 真正写进要执行命令的显式锚) 共用同一条取值
    路径 —— 两处各写一遍容易在 payload 形状上悄悄漂移(一处读对一处读漏)。
    """
    after = payload.get("after_state")
    after_state = after if isinstance(after, dict) else {}
    val = after_state.get(key)
    if val is None:
        val = payload.get(key)
    return str(val) if val is not None else None


def _extract_handoff_context(trigger_event_type: str, payload: dict[str, object]) -> str:
    """从上游完工事件 payload 抽取下一棒需要的交接上下文(plan_id / task_id / outcome 等)。

    这是"自动生成下一棒该干嘛"的关键: 下一棒不是空降, 而是带着上游产出的引用开工。
    """

    def _get(key: str) -> str | None:
        return _payload_field(payload, key)

    lines: list[str] = []
    # T-FIX-B5-01: 采访→共识第一跳, brief 的语义 id (brief_id, 形如 brief-xxx) 进交接,
    # 让工程共识下一棒带 brief 引用开工 (M-1.2 / view_engine 按 brief_id 反查 brief, 非 plan_id —
    # 采访阶段还没有 plan_id, 它是共识 freeze 才产的)。brief 的 event_id 另经
    # decision.trigger_event_id 进 prompt 的接力触发段。
    brief_id = _get("brief_id")
    if brief_id:
        lines.append(f"- 上游 brief_id: {brief_id}")
    plan_id = _get("plan_id")
    if plan_id:
        lines.append(f"- 上游 plan_id: {plan_id}")
    task_id = _get("task_id")
    if task_id:
        lines.append(f"- 上游 task_id: {task_id}")
    run_id = _get("run_id")
    if run_id:
        lines.append(f"- 上游 run_id: {run_id}")
    outcome = _get("outcome")
    if outcome:
        lines.append(f"- 上游 outcome: {outcome}")
    finding_id = _get("finding_id")
    if finding_id:
        lines.append(f"- 关联 finding_id: {finding_id}")
    if not lines:
        lines.append("- (上游事件无结构化交接键; 读 trigger 完整 payload 自行提取)")
    return "\n".join(lines)


def _knowledge_capsule_ref_line(skill_role: str, main_project_dir: str | None) -> str:
    """T-FIX-B6-07 (USABILITY-walk#1/#2): 该 skill 已聚合的 knowledge capsule 路径行,
    显式列进参考路径段让被派 agent 必去 Read。

    knowledge 此前被切散成多个文件; capsule 把它们聚合成一份
    `.towow/capsules/<role>-knowledge.md`(M-0.3 capsule-assembly 产物), 指它优于指 8 个
    分散文件。隔离工位(main_project_dir 给定)时 rooted 在 {main_project_dir}/.towow/...
    (工位 cwd 下相对路径会指错, 同 log_hint 逻辑), 否则用 harness/.towow/...(主对话路径)。
    """
    base = f"{main_project_dir}/.towow/capsules" if main_project_dir else "harness/.towow/capsules"
    return (
        f"- 该 skill 的 knowledge(操作判断料: execution-playbook / git-safety / "
        f"code-quality 等, 已聚合成一份): `{base}/{skill_role}-knowledge.md` "
        f"— 开工前先 Read 它, 别照本 prompt 推断 knowledge 在哪(隔离工位记得带 "
        f"--project-dir 才 Read 得到)"
    )


def _consensus_completion_with_anchor(brief_id: str) -> str:
    """T-BOOT-C3: 工程共识接力打卡第1步 —— 显式钉 `--source-brief-id`, 不留隐式『取最新』缺口。

    根因: `towow consensus start` 早就支持 `--source-brief-id`(R08-T1, 为并行采访场景加的
    显式选 brief 出口), 但 InterviewBriefPublished→engineering-consensus 前进链的打卡文案
    (_COMPLETION_CRITERIA["engineering-consensus"]) 一直只写字面命令 `consensus start`(不带
    flag)——brief_id 虽已进交接上下文段(供阅读), 却没进*真被执行的命令*。省略该 flag 时
    `consensus start` 静默 fallback 到『全局最新 InterviewBriefPublished』; C-1 落地的复工补派
    回捞积压 brief 后, 并发/补派场景下『最新』未必是触发这次接力的那条 —— 接力会话可能锚到
    别人排队中的 brief 上、按错的需求建共识。这里把触发本次接力的具体 brief_id 直接写进要执行
    的命令, 并用 fail-closed 措辞("必带、别自己推断") 关掉猜的空间——不造平行字段, 只是把
    payload 里已有的锚字段真接上消费端。
    """
    return (
        f"1. `towow consensus start --source-brief-id {brief_id}`（必带、别自己推断或让它取默认"
        "最新——并发/补派场景下『最新』未必是触发你这次接力的这条 brief；漏带 = 静默锚到别的 brief "
        "上、为别人的需求建共识）。它会打印 brief 的种子概念定义文件路径，开工前读它（拿 "
        "session_id，之后子命令都带 `--session-id <它>`）\n"
        "2. 把概念定下来：emit ConceptCreated / ConceptEdgeAdded；标出谁会消费它（ConsumerListPublished）\n"
        "3. 冻结：emit EngineeringConsensusFreezed（产新 plan_id）——触发下游 planning 自动接力"
    )


def generate_forward_chain_condition_text(
    decision: DispatchDecision,
    trigger_payload: dict[str, object] | None = None,
    main_head_commit: str | None = None,
    main_project_dir: str | None = None,
    worktree: Path | None = None,
    goal_session_id: str | None = None,
) -> str:
    """E.5: 为前进链 spawn 构造 condition_text(P-12 <=4000 字 + designer-style)。

    与 generate_condition_text(finding 驱动)不同 —— 前进链由上游"阶段完工事件"驱动,
    没有 finding。按 trigger_event_type 从 FORWARD_CHAIN_REGISTRY 取下一棒 skill,
    并从 trigger 事件 payload 自动抽取交接上下文(plan_id 等)注入 prompt。

    Returns:
        condition_text <=4000 chars; trigger_event_type 不在前进链表 → minimal fallback。

    M-1.5 §7.0 (RUN-035 T-L1-54): decision.review_mode 非空 → 这是 review 触发边 (design_time /
    author_time / fix_after), 用 review target + mode-specific completion (prompt 真带
    `towow review start --mode <mode>`, 不靠 FORWARD_CHAIN_REGISTRY[trigger] 误取 planning)。

    worktree (f-bgrv2-workcomplete-recompute-worktree-incompat): 本次 spawn 的工位路径。execution
    fan-out 在隔离工位 (worktree != main_project_dir) 时, prompt 注入 `work complete --repo-dir
    <worktree>/harness` 指引 —— done_criteria 机器复算默认在 main 跑、看不到工位分支未 promote 的新
    代码/测试 = false-negative 阻塞; --repo-dir 让复算在工位 harness 跑 (事件仍经 --project-dir 回流主账本)。
    """
    review_mode = getattr(decision, "review_mode", None)
    task_id = getattr(decision, "task_id", None)
    if review_mode:
        target = _REVIEW_SKILL_TARGET
        completion = _REVIEW_MODE_COMPLETION.get(review_mode, _COMPLETION_CRITERIA.get("review", ""))
    elif decision.dispatch_to == "execution":
        # T3 (PLAN-FIX F-07): execution fan-out decision — target 固定 execution。不靠 trigger 查
        # FORWARD_CHAIN, 因为 TaskRunCompleted 的 trigger 在表里映射到 review (会取错 target)。
        target = SkillTarget("M-1.4", "execution", ".claude/skills/execution/SKILL.md")
        completion = _COMPLETION_CRITERIA.get("execution", "")
    else:
        fwd = FORWARD_CHAIN_REGISTRY.get(decision.trigger_event_type)
        if fwd is None:
            return _build_minimal_fallback(decision)
        target = fwd
        completion = _COMPLETION_CRITERIA.get(target.skill_role, "")
        # T-BOOT-C3 (接力显式锚定): InterviewBriefPublished → engineering-consensus 这一跳,
        # 把触发本次接力的 brief_id 直接写进打卡第1步要执行的命令 (--source-brief-id), 不留
        # `consensus start` 裸调用隐式 fallback 到『取最新』的锚错缺口。brief_id 缺失(理论上不该
        # 发生 —— canonical InterviewBriefPublished payload 该字段必填, 见
        # schemas/payloads/interview_brief.py)才保留通用文案, fail-open 到"至少能开工"而非硬拒。
        if (
            target.skill_role == "engineering-consensus"
            and decision.trigger_event_type == "InterviewBriefPublished"
        ):
            _anchor_brief_id = _payload_field(trigger_payload or {}, "brief_id")
            if _anchor_brief_id:
                completion = _consensus_completion_with_anchor(_anchor_brief_id)

    handoff = _extract_handoff_context(decision.trigger_event_type, trigger_payload or {})
    log_hint = (
        f"{main_project_dir}/.towow/events.log {main_project_dir}/.towow/events/hot/*.jsonl"
        if main_project_dir
        else "harness/.towow/events.log"
    )
    # f-bgrv2-workcomplete-recompute-worktree-incompat: execution fan-out 在隔离工位
    # (worktree != main_project_dir) 时, 注入 `work complete --repo-dir <worktree>/harness` 指引。
    # done_criteria 机器复算 (pytest/grep test_selector) 默认在 --project-dir(=main) 跑, 但工位分支
    # 新写的代码/测试未 promote, 在 main 复算不到 = false-negative 阻塞 success 门。--repo-dir 让复算
    # 在工位 harness 跑 (事件仍经 --project-dir 回流主账本)。漏带 = 退回原 fail-closed 阻塞, 非误放行。
    repo_dir_note = ""
    if (
        decision.dispatch_to == "execution"
        and worktree is not None
        and main_project_dir is not None
        and worktree.resolve() != Path(main_project_dir).resolve()
    ):
        _wt_repo_dir = worktree.resolve() / Path(main_project_dir).name
        repo_dir_note = (
            f"- 隔离工位 `work complete` 加 `--repo-dir {_wt_repo_dir}`——机器复算须看你工位分支"
            f"的新代码（默认在 main 跑会误判没做完）；事件仍经 --project-dir 回主账本\n"
        )
    if task_id:
        if review_mode:
            # T-FIX-B2-04 (REVIEW-verdict#2): review-mode 决策带 task_id = 它要完成的 REVIEW-typed
            # task (fix_after 时 = orchestrator 溯源锚定的原 REVIEW task)。明示这是 review start
            # 必带的 `--task-id` —— conclude 据此 emit TaskRunCompleted 过 verdict 门, REVIEW task
            # 才落账。不带则 conclude 不发完成事件、verdict 门不触发、REVIEW task 永不完成。
            # T-FIX-B3-04 (CONSTITUTION-unknown#1 子命题 c): 把 `--dispatched-as-review-task` 结构化
            # 嵌进拼好的命令 —— 这是交给 spawn 的 review agent 的 fail-closed 信号。即便 agent 复制
            # 时漏带 --task-id, 该 flag 触发 review start fail-closed (非零退出), 而非静默 no-op
            # 让 verdict 门吞掉审查。REVIEW-typed (review_mode + task_id 同现) 才置位; forward-chain
            # review (review_mode 有但无 task_id) 不进此分支, 命令不带此 flag, 不会被误拦。
            handoff = (
                f"- 【你这次 review 要完成的 REVIEW task_id】: {task_id} "
                f"→ `towow review start --mode {review_mode} --dispatched-as-review-task "
                f"--task-id {task_id} --session-id <你的>` (此 --task-id 必带; conclude 据此 emit "
                f"TaskRunCompleted 过 verdict 门, REVIEW task 才落账; --dispatched-as-review-task "
                f"是 fail-closed 信号, 漏带 --task-id 会被 review start 拒, 不静默放行)\n{handoff}"
            )
        else:
            # T3: 指派的具体 task_id 放交接上下文最前 (execution 只做这个, 不自选)
            handoff = f"- 【你被指派执行的 task_id】: {task_id} (只做这一个, 不要自选别的 task)\n{handoff}"

    head_block = ""
    if main_head_commit:
        head_block = (
            f"\n# main HEAD at spawn: {main_head_commit}\n"
            f"# 你 worktree base 不一致请 `git fetch && git rebase {main_head_commit}` 再继续.\n"
        )

    goal_hook = _ROLE_GOAL_HOOK.get(
        target.skill_role, "接上一步的产出，把这一棒往前推。"
    )

    return (
        _goal_directive(_goal_completion_condition(role=target.skill_role, decision=decision))
        + f"/{target.skill_role}\n\n"
        f"{goal_hook}{head_block}\n\n"
        f"## 你接的活（上一步刚完工，轮到你）\n"
        f"{handoff}\n\n"
        f"## 现场信息（开工要用的）\n"
        f"- trigger event_id: {decision.trigger_event_id}"
        f"（接力锚点；上一步完整产出 → 跑 "
        f"`grep '{decision.trigger_event_id}' {log_hint}` 调出来）\n"
        + (
            f"- 所有 towow 命令带 `--project-dir={main_project_dir}`（你可能在隔离工位，事件要回主账本）\n"
            if main_project_dir
            else "- bg agent 在隔离 worktree 时所有 towow 命令带 `--project-dir=<主仓>/harness`\n"
        )
        + repo_dir_note
        + "\n## 打卡（必守）\n"
        f"{completion}\n\n"
        f"## 收尾\n"
        f"{_RECOVERY_PROTOCOL_BLOCK}"
        f"{_goal_termination_instruction(goal_session_id)}"
        f"{_GOAL_LOOP_NOTE}"
        f"只有 owner 能拍的事（产品方向 / 风险取舍 / 范围边界）→ emit GoalEscalationRaised "
        f"等回话；真不可达 → GoalSessionTerminated reason=unreachable。\n\n"
        f"## 硬约束\n"
        f"- 提交前 ruff + mypy --strict + pytest 全绿\n"
        f"- 与 v3 spec 冲突 → 记 SPEC-CONFLICT-RESOLUTION-LEDGER，不静默改\n"
        f"- 共享树上不用 git 回滚类命令（stash / reset --hard / restore / checkout 还原路径）；"
        f"要干净基线用 `git worktree add <临时目录> HEAD`\n"
    )


def _truncate(text: str, max_chars: int) -> str:
    """Cut text to max_chars at sentence/clause boundary if possible."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    for sep in ["\n\n", "\n", ". ", "; ", ", ", " "]:
        idx = cut.rfind(sep)
        if idx >= max_chars * 0.7:  # don't cut too aggressively
            return cut[: idx + len(sep)].rstrip() + "…"
    return cut + "…"


# fix 派发信的精简预算: 描述配额(1000) + 现场信息脚手架 的总长上限。远低于 P-12 的 4000 字
# 上限 (旧模板 ~3500)。owner: "有字数要求、但不一定死磕某个具体数" —— 这是 lean 目标、由 eval
# 校准, 不是铁律; 超预算 = 高信号被低信号噪音稀释的警报, 该砍而非该放宽。
# T-RMD-GOAL: /goal directive (锚闭合事件的一行完成条件) + 目标循环逃生阀 是 owner 指示的合法
# 新增高信号内容 (非噪音), 长 finding 下把地板抬高 ~150 字, 故 budget 1600 → 1750 (仍远低于 4000)。
FIX_DISPATCH_CHAR_BUDGET = 1750


def _generate_fix_condition_text(
    decision: DispatchDecision,
    *,
    finding_id: str,
    severity: str,
    description: str,
    main_head_commit: str | None,
    main_project_dir: str | None,
    worktree: Path | None,
    log_hint: str,
) -> str:
    """fix 派发信 — 新标准 (`/fix` slash 注入持久人格)。

    设计依据 (Anthropic 一手, 2025): "right altitude"/Goldilocks (给目标+判断标准,
    不把步骤写死) · "smallest set of high-signal tokens" (砍掉低信号噪音) · "brilliant
    but new employee" + golden rule (去掉收信方 ground 不住的内部代号, 否则诱发编造) ·
    "tell what to do not what not to do" (正向措辞)。

    关键结构改变: "修干净该怎么想"= 持久人格 (打卡点 / 闭合纪律 / 硬约束) 住在 `/fix`
    命令 (`.claude/glue/commands/fix.md`, slash 自动注入), **不在这封一次性派发信里重抄**
    (旧模板把 SKILL.md 已讲透的东西又抄一遍且抄漏抄乱)。本信只装这次的运行态:
    哪条 finding + 现场信息 (基线 / project-dir / 隔离工位 repo-dir)。从 ~3500 字降到几百字。
    """
    # 高信号摘要内联 (auto-route, 不让它"自己去 grep"); 全文 payload 留一条 grep 兜底。
    # 描述配额 1000: 够一条 finding 把"问题是什么"讲透, 又把派发信总长压在精简预算内
    # (FIX_DISPATCH_CHAR_BUDGET); 超出部分由 grep 完整 payload 兜底, 不在信里塞全文。
    desc_clipped = _truncate(description, 1000)
    site: list[str] = [
        f"- 你的真判据 = 这条 finding 的闭合合约（怎样算闭合、要同步哪些连带处、哪些残留清零）: "
        f"跑 `grep '{decision.trigger_event_id}' {log_hint}` 把完整 payload 调出来、照合约修",
    ]
    if main_head_commit:
        site.append(
            f"- 这批代码基线 commit: {main_head_commit} "
            f"(你的工位 base 应 ≥ 它; 落后就 `git fetch && git rebase {main_head_commit}` 再开工)"
        )
    if main_project_dir:
        site.append(
            f"- 所有 towow 命令带 `--project-dir={main_project_dir}` "
            f"(你可能在隔离工位, 事件要回主账本)"
        )
    if (
        worktree is not None
        and main_project_dir is not None
        and worktree.resolve() != Path(main_project_dir).resolve()
    ):
        _wt_repo_dir = worktree.resolve() / Path(main_project_dir).name
        site.append(
            f"- 隔离工位执行 `fix complete` 须加 `--repo-dir {_wt_repo_dir}` — 闭合的机器复算"
            f"要看你工位分支新写的代码/测试, 否则在 main 上复算不到、误判没修"
        )
    return (
        _goal_directive(
            _goal_completion_condition(role="fix", decision=decision, finding_id=finding_id)
        )
        + "/fix\n\n"
        f"**问题**: {desc_clipped}\n"
        f"**严重度**: {severity} · **记录 id (finding_id)**: {finding_id}\n\n"
        "**现场信息 (开工要用的)**:\n"
        + "\n".join(site)
        + "\n本会话由开头 `/goal` 目标循环驱动: 修完 (FixCompleted 已 emit) 即达成; 无法达成先 "
        "emit GoalEscalationRaised + GoalSessionTerminated 再 `/goal clear` 停循环, 别空转。\n"
    )


def generate_condition_text(
    decision: DispatchDecision,
    finding_payload: dict[str, object] | None = None,
    main_head_commit: str | None = None,
    main_project_dir: str | None = None,
    worktree: Path | None = None,
    goal_session_id: str | None = None,
) -> str:
    """Build full condition_text for F-11 spawn (P-12 <=4000 字符 + designer-style).

    Returns:
        condition_text string <=4000 chars; if finding_payload is None or finding_kind
        unknown, falls back to minimal generic prompt (compatible with original behavior).

    Args:
        decision: DispatchDecision from orchestrator
        finding_payload: stub_original_payload from FindingCreated event (含
            finding_id / finding_kind / severity / description / spec_source 等)
        main_head_commit: optional main HEAD hash to inject baseline guard
        worktree: f-fixcomplete-closure-recompute-worktree-incompat — 本次 spawn 的工位路径。
            fix 派活 (dispatch_to=="fix") 在隔离工位 (worktree != main_project_dir) 时, prompt 注入
            `fix complete --repo-dir <worktree>/harness` 指引 —— review finding 的 closure 机器复算默认在
            main 跑、看不到工位分支未 promote 的新建测试/代码 = false-negative 阻塞 resolved 门;
            --repo-dir 让复算在工位 harness 跑 (事件仍经 --project-dir 回流主账本)。镜像 work complete 侧
            (f-bgrv2-workcomplete-recompute-worktree-incompat / generate_forward_chain_condition_text)。
    """
    # Find target by mapping from finding_payload's finding_kind.
    target: SkillTarget | None = None
    finding_id = "unknown"
    description = "(no finding payload provided)"
    severity = "unknown"
    if finding_payload is not None:
        # f-dispatch-letter-ignores-decision-seat: decision.dispatch_to 是路由权威 (纠偏门都住
        # decision 层), 信必须跟座位一致 —— 座位表命中就用它选模板; finding_kind 只在
        # dispatch_to 不是可 spawn skill 座位时兜底 (详见 _TARGET_BY_SKILL_ROLE 注释)。
        target = _TARGET_BY_SKILL_ROLE.get(decision.dispatch_to)
        if target is None:
            kind_str = finding_payload.get("finding_kind")
            if isinstance(kind_str, str) and kind_str in SKILL_REGISTRY:
                target = SKILL_REGISTRY[kind_str]
        fid = finding_payload.get("finding_id")
        if isinstance(fid, str):
            finding_id = fid
        desc = finding_payload.get("description")
        if isinstance(desc, str):
            description = desc
        sev = finding_payload.get("severity")
        if isinstance(sev, str):
            severity = sev

    if target is not None:
        # f-c2-dispatch-command-text-source-divergence: decision.dispatch_to 已经过
        # _fix_layer_contradicts_consensus_seat 座位纠偏, 是 worktree 命名/spawned_role 都信的
        # 终态路由; 让它覆盖上面按 finding_kind 独立查到的座位, 两条产出重新同源。dispatch_to 若
        # 不是已知的 prompt-generating 角色 (fix/review/engineering-consensus) 时 _ROLE_TARGETS
        # 查无, 保留原 target 不变 (不影响 execution/no-route 等不经此函数的路径)。
        target = _ROLE_TARGETS.get(decision.dispatch_to, target)

    if target is None:
        # Fallback: minimal generic prompt (same as orchestrator pre-RUN-015 behavior)
        return _build_minimal_fallback(decision)

    log_hint = (
        f"{main_project_dir}/.towow/events.log {main_project_dir}/.towow/events/hot/*.jsonl"
        if main_project_dir
        else "harness/.towow/events.log"
    )
    # fix 派活走新标准模板 (`/fix` slash 注入持久人格); review/consensus 暂留旧模板,
    # 待按同标准逐个重写。设计依据见 _generate_fix_condition_text docstring。
    if target.skill_role == "fix":
        return _generate_fix_condition_text(
            decision,
            finding_id=finding_id,
            severity=severity,
            description=description,
            main_head_commit=main_head_commit,
            main_project_dir=main_project_dir,
            worktree=worktree,
            log_hint=log_hint,
        )

    # finding 驱动的 review/consensus 派活 — 新标准 (`/role` slash 装人格)。与 fix 同一个魂:
    # 目标在前 + 这条 finding(具体的活) + 现场信息 + 短打卡; 方法/人格住各自 skill。
    # (fix 已在上面早分支走 _generate_fix_condition_text; 此路径只到 review/consensus。)
    desc_clipped = _truncate(description, 1000)
    completion = _COMPLETION_CRITERIA[target.skill_role]
    head_block = ""
    if main_head_commit:
        head_block = (
            f"\n# main HEAD at spawn: {main_head_commit}\n"
            f"# 你 worktree base 不一致请 `git fetch && git rebase {main_head_commit}` 再继续.\n"
        )
    goal_hook = _ROLE_GOAL_HOOK.get(target.skill_role, "处理下面这条 finding。")

    return (
        _goal_directive(
            _goal_completion_condition(
                role=target.skill_role, decision=decision, finding_id=finding_id,
            )
        )
        + f"/{target.skill_role}\n\n"
        f"{goal_hook}{head_block}\n\n"
        f"## 你要处理的 finding\n"
        f"- 问题: {desc_clipped}\n"
        f"- 严重度: {severity} · 记录 id (finding_id): {finding_id}\n\n"
        f"## 现场信息（开工要用的）\n"
        f"- 完整原始记录: 跑 `grep '{decision.trigger_event_id}' {log_hint}` 调出完整 payload\n"
        + (
            f"- 所有 towow 命令带 `--project-dir={main_project_dir}`（你可能在隔离工位，事件要回主账本）\n"
            if main_project_dir
            else "- bg agent 在隔离 worktree 时所有 towow 命令带 `--project-dir=<主仓>/harness`\n"
        )
        + "\n## 打卡（必守）\n"
        f"{completion}\n\n"
        f"## 收尾\n"
        f"{_RECOVERY_PROTOCOL_BLOCK}"
        f"{_goal_termination_instruction(goal_session_id)}"
        f"{_GOAL_LOOP_NOTE}"
        f"只有 owner 能拍的事 → emit GoalEscalationRaised 等回话；真不可达 → "
        f"GoalSessionTerminated reason=unreachable。\n\n"
        f"## 硬约束\n"
        f"- 只处理这一条 finding\n"
        f"- 提交前 ruff + mypy --strict + pytest 全绿\n"
        f"- 与 v3 spec 冲突 → 记 SPEC-CONFLICT-RESOLUTION-LEDGER，不静默改\n"
        f"- 共享树上不用 git 回滚类命令；要干净基线用 `git worktree add <临时目录> HEAD`\n"
    )


def _build_minimal_fallback(decision: DispatchDecision) -> str:
    """最小兜底 prompt — finding_payload 缺失 / finding_kind / trigger 未知时用。

    T-RMD-GOAL: 本兜底路径同样是 autopilot real-spawn 的 bg 派发信 → 首行拼 `/goal <完成条件>`,
    与 forward-chain / finding-driven / fix 三路径一致 (激活内置目标循环 + 让 Stop hook 的
    is_autonomous_goal_session 判据为真, 跳过 owner-facing confirmation-loop guard)。原
    92c3644dd 即覆盖此路径。
    """
    return (
        _goal_directive(_goal_completion_condition(role=decision.dispatch_to, decision=decision))
        + f"你被自动 dispatch 到 {decision.dispatch_to} 角色，处理一个 "
        f"{decision.trigger_event_type} 触发。\n\n"
        f"## 现场信息\n"
        f"- trigger event_id: {decision.trigger_event_id}（跑 `grep '{decision.trigger_event_id}' "
        f"harness/.towow/events.log harness/.towow/events/hot/*.jsonl` 调出完整 context；"
        f"按你的角色装上对应 skill 人格再开工）\n\n"
        f"## 收尾\n"
        f"{_RECOVERY_PROTOCOL_BLOCK}"
        f"具体: emit 完成/产出事件 + emit GoalSessionTerminated reason=completion。"
        f"卡住 emit GoalEscalationRaised。\n"
        f"{_GOAL_LOOP_NOTE}"
    )


__all__ = [
    "FIX_DISPATCH_CHAR_BUDGET",
    "FORWARD_CHAIN_REGISTRY",
    "REVIEW_TRIGGER_CONTRACT",
    "SKILL_REGISTRY",
    "SkillTarget",
    "generate_condition_text",
    "generate_forward_chain_condition_text",
    "resolve_review_mode",
]
