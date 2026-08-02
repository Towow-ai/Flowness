"""C-2 接线 (设计 20-layer23 §5.2): 把 awareness/reconcile.py 的纯函数 reconcile_all 接进
orchestrator polling loop —— level-triggered 对账兜底。

## 为什么需要这一层 (对因为 X)
`awareness/reconcile.py` 是纯函数核 (算动作, 不 emit/不 spawn, 易测低风险)。本模块是它与 live
orchestrator 之间唯一的阻抗匹配处:
  ① 从 canonical 投影/事件读出 ObservedState (desired 与 observed 都从真实状态算)。
  ② 调 reconcile_all 拿 [ReconcileAction]。
  ③ 对每个 action 经【既有】幂等戳落地动作 (绝不新造平行 claim 路径)。

## 忠实增强既有, 不 greenfield (设计 §5 关键洞察)
- **ReadySet 已是既有 `_dispatch_execution_batch` 的 backlog re-scan** (它每轮重算 ready-set + 滤掉
  已派/escalation-blocked/owner-gated/熔断)。故本接线**不重做 ReadySet** —— 它本就是 level-triggered
  的局部实现, 收编即可, 不另起一套派 execution 的路径 (否则两套 dedup 机制对同 task 孪生)。
- 本接线真正补的两个【现状无 consumer】缺口:
    * **Replan** (声称3: RePlanTriggered producer 在、`_route_event` 零 case → 落账即失踪)。
    * **DeadLetter triage** (声称T3.3: start_triage/decide_* 生产零调用, 只有 TTL sweep 兜底丢)。

## 幂等 (level-triggered 硬配套, advisor 点名的坑)
- Replan: observed.redecomposed 必含【已派 planning 但未完成 re-decompose】(用既有 composite 戳
  `(replan_event_id, "planning")` 判), 不只【已完成 re-decompose】。只填已完成 → 每 interval 重派
  一次 planning = 风暴 (设计 §5.4 / reconcile.py docstring 都明令)。
- DeadLetter: 复用死信箱自带的 active-key 幂等 + 5 态机 require_state, 同 entry 反复扫只迁移一次。

## 频率 (设计 §5.2)
run_polling_loop 每 `TOWOW_RECONCILE_INTERVAL_S` 跑一次 (默认显著 > poll_interval, 避免 daemon
吃自己心跳空转 —— autopilot_idle_audit 实证的水位线涨坑)。全量 events 读用既有 EventLog, 不每次重读盘。
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

from towow.awareness.exception_taxonomy import FailureClass
from towow.awareness.reconcile import (
    ForwardChainGap,
    ObservedState,
    ReconcileAction,
    reconcile_all,
)
from towow.l2 import dead_letter_inbox
from towow.schemas.enums import EventType

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from towow.l0.event_log import EventLog


# 默认对账间隔 (秒): 显著大于 poll_interval (5s) — reconcile 是慢周期兜底, 不是快路径。
# 与 phase_stuck sweep 同量级 (分钟级), 避免每 poll 全量扫账本 (吃心跳空转)。
_RECONCILE_INTERVAL_DEFAULT_S = 120.0


def _reconcile_interval_s() -> float:
    """env TOWOW_RECONCILE_INTERVAL_S 覆盖; 非法/缺省回默认。钳到 > poll_interval 量级 (>= 30s)。"""
    raw = os.environ.get("TOWOW_RECONCILE_INTERVAL_S", "").strip()
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _RECONCILE_INTERVAL_DEFAULT_S
    # 下限 30s: 防误配成每轮跑 (吃心跳空转的坑)。
    return max(30.0, val)


def reconcile_disabled() -> bool:
    """env TOWOW_RECONCILE_DISABLED 真值 → 整个 reconcile pass 跳过 (operator kill-switch)。

    用途: ① 运维需要单独验 sweep_aged_out 等其它兜底机制不被 reconcile front-run 时; ② 临时关掉
    level-triggered 兜底 (edge 快路径仍跑) 排查问题。默认 off = reconcile 跑。它【只】关 reconcile,
    不关派发/edge 路由 (那些走 paused 总闸)。
    """
    return os.environ.get("TOWOW_RECONCILE_DISABLED", "").strip().lower() in {"1", "true", "on"}


# 死信箱进入源 → 统一异常分类 (K6 exception_taxonomy)。circuit_tripped (重派预算耗尽) 与
# structural_failure (同输入必败) 都归 EXHAUSTED → triage 动作, 但落地的 retire_reason 在
# _execute_dead_letter_action 内按 entry_reason 精确区分 (circuit→REENTRY_EXHAUSTED /
# structural→DETERMINISTIC, 设计 §T3.3 表, 绝不混淆)。unroutable (无 consumer) → 保守 WAITING
# (escalate; no-route 罕见 = 机器判不了的 novel 情形, 不会 flood owner —— fallback 路由已兜大多数)。
_ENTRY_REASON_TO_FAILURE_CLASS: dict[str, FailureClass] = {
    dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED.value: FailureClass.EXHAUSTED,
    dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE.value: FailureClass.EXHAUSTED,
    dead_letter_inbox.DeadLetterEntryReason.UNROUTABLE.value: FailureClass.WAITING,
}


def _replan_dispatch_to() -> str:
    """RePlan reconciler 派发目标 = planning (partial re-decompose; full_replan 走 main-inbound
    由主 session 确认, 不在此自动重做整 plan — 设计 §5.3 + solution-5 分流)。"""
    return "planning"


# C-1 (finding fnd-forward-chain-silent-skip-head-published-brief): forward-chain 缺口扫描不算前进链
# 缺口的 dispatch_to —— "execution" 已有独立于水位线的 ready-set backlog re-scan 自愈 (见
# orchestrator._dispatch_execution_batch, 每轮从 PlanFreezed plan 重算 ready-set, 不靠本兜底捞回,
# 双补会孪生); main-inbound/Nature dashboard/no-route 是纯通知/无路由目标, 不是"前进链的下一棒"。
_FORWARD_CHAIN_SKIP_TARGETS: frozenset[str] = frozenset(
    {"execution", "main-inbound", "Nature dashboard", "no-route"},
)


def _dispatch_marker_is_fake(
    towow_dir: Path, trigger_event_id: str, dispatch_to: str,
) -> bool:
    """D-2 (fix_after 派发链丢活账本实证 4 种失败模式之 2): 一个通过 is_already_dispatched 存在性
    检查的戳, 内容可能只是"假派发"信号——从未真起过下游会话:

    - `resume_orchestrator` 的 skip-on-resume 戳 (F-026-5): 暂停期发生的事件被整体标
      `skipped_on_resume: true` 跳过, 前提假设是"owner 已在暂停期手动处理"; 但对 fix_after review
      这条 forward-chain 边这个假设不成立 —— FixCompleted 之后仍需要一个从未发生过的下游 verify-step
      + finding-resolve, 光是"fix 本身做没做完"跟"这条前进链走没走完"是两件事。
    - `mock_spawn=True` (干跑/测试态) 下真派发流程仍会走完整个 success 分支、写下
      `spawn_result={"method": "mock", "launched": false}` 的戳 —— 内容诚实自报"没真起会话", 但
      is_already_dispatched 只看存在性, 把它当真派发永久去重。

    只读 (与 _scan_forward_chain_gaps 同一纪律), 不改任何戳。查找优先级 composite (本 dispatch_to
    专属, 内容最新最具体) 先于 legacy bare (event_id 全局, 可能是很久以前另一次判定留下的) —— 这样
    "先前是假戳、后来该 trigger 经本对账/backlog 重派真起了会话"的场景, 一旦真派发把 composite 戳
    覆盖为 launched=true, 下一轮扫描立刻优先读到它、判定不再是假戳, 不会对同一个已经真派出去的
    trigger 反复"发现"缺口重派 (storm 防护; 与 archive 归档场景的活跃目录优先同一顺序心智)。
    """
    from towow.l2.orchestrator import (
        _dispatch_target_slug,
        _dispatched_archive_dir,
        _dispatched_dir,
    )

    ddir = _dispatched_dir(towow_dir)
    adir = _dispatched_archive_dir(towow_dir)
    composite_name = f"{trigger_event_id}__{_dispatch_target_slug(dispatch_to)}"
    for candidate in (ddir / composite_name, adir / composite_name, ddir / trigger_event_id, adir / trigger_event_id):
        if not candidate.exists():
            continue
        try:
            body = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False  # 读不出内容 → 保守当真派发 (宁可漏捞、不误捞重派孪生)
        if not isinstance(body, dict):
            return False
        if body.get("skipped_on_resume") is True:
            return True
        spawn_result = body.get("spawn_result")
        # 找到第一份(最具体/最新)戳: 内容非以上两类假信号 → 视为真派发信号 (含此判定)。
        return isinstance(spawn_result, dict) and spawn_result.get("launched") is False
    return False  # 没找到任何戳内容 (不该发生, 调用方已用 is_already_dispatched 判过存在) — 保守 False


def _scan_forward_chain_gaps(
    towow_dir: Path, event_log: EventLog,
) -> tuple[ForwardChainGap, ...]:
    """扫 E.5 FORWARD_CHAIN_REGISTRY 全部触发类型, 找出从未产出可查 DispatchDecision 的事件
    (finding fnd-forward-chain-silent-skip-head-published-brief 根治), 以及戳内容自报"假派发"
    (skip-on-resume / mock 未真起会话) 的事件 (D-2: fix_after 派发链丢活账本实证追加)。

    复用 orchestrator._route_event (唯一路由真源, 与 resume_orchestrator 的 _pending_spawn_decisions
    同一原语) 对每条候选事件重算"今天路由应该产什么"。异常处理策略与 _pending_spawn_decisions 不同:
    那里 (resume 守卫) 路由异常时保守当"无待决"跳过 (宁可多留在途工作、不误吞); 这里路由异常本身
    就是要捕捉的信号 —— 一个 forward-chain trigger 反复路由失败绝不能被本对账悄悄当"没事", 否则
    只是把"水位线静默丢"换成"对账静默丢", 同一个坑换了个入口 (故显式落 routable=False 供落死信,
    不复用 _pending_spawn_decisions 的 except→[] 吞法)。

    纯读不写 (与 build_observed_state 同一纪律): 只调 _route_event (只读路由计算) + is_already_dispatched
    / _dispatch_marker_is_fake (只读戳查) + _terminal_finding_for_decision (只读投影), 不落任何副作用
    —— 写 backlog marker / 落死信都在 execute 阶段 (run_reconcile_pass)。

    D-2 假戳判定: 戳存在但内容自报"假派发" (见 _dispatch_marker_is_fake) 时, 不盲目当缺口捞回——
    先查 _terminal_finding_for_decision (复用 f-fixafter-dispatch-to-terminal-finding-relay-deadlock
    既有终态锚定, 只对 dispatch_to=fix / review_mode=fix_after 两类锚得到 finding), 命中终态
    (resolved/accepted) 说明下游已经【经另一条权威路径】真正解决 (如 owner 暂停期手动做完、或另一
    fix-after 轮次已收口) —— 保留 F-026-5 的原始意图('暂停期手动做完的别当新任务重放'), 不误捞。
    锚不出 finding (非 fix/fix_after 决策, 或锚链断) → 不特别保护, 当正常缺口捞回 (没有等价终态锚,
    保守偏向"捞回重派"而非"永久信任一个从未真起过会话的戳")。

    幂等 (level-triggered 硬配套, 与 ReplanReconciler "observed 必含已派但未完成" 同一纪律): 已有
    backlog marker (routable) / 已有活跃死信条目 (not routable) 的缺口不重复收进 observed —— 否则
    每 interval 重新"发现"同一个尚未被下一轮派发循环消费掉的缺口, 在 A3 空转哨兵的 dispatched_count
    里被反复计成新的真前进 (虚高), 与该不变量 "只计真前进派发" 的口径冲突。
    """
    from towow.l2.dispatch_templates import FORWARD_CHAIN_REGISTRY
    from towow.l2.orchestrator import (
        OrchestratorDaemon,
        _nonexec_backlog_dir,
        _nonexec_backlog_marker_name,
        _terminal_finding_for_decision,
        is_already_dispatched,
    )

    def _has_backlog_marker(trigger_event_id: str, dispatch_to: str) -> bool:
        return (
            _nonexec_backlog_dir(towow_dir)
            / _nonexec_backlog_marker_name(trigger_event_id, dispatch_to)
        ).exists()

    def _has_active_dead_letter(ref: str) -> bool:
        return any(
            e.source_object_ref == ref
            and e.entry_reason == dead_letter_inbox.DeadLetterEntryReason.UNROUTABLE.value
            for e in dead_letter_inbox.list_entries(towow_dir, include_terminal=False)
        )

    router = OrchestratorDaemon(event_log, last_processed_seq=0)
    gaps: list[ForwardChainGap] = []
    for type_key in FORWARD_CHAIN_REGISTRY:
        try:
            etype = EventType(type_key)
        except ValueError:
            continue  # registry key 没有对应 native EventType (不应发生, 防御跳过不炸对账)
        for rec in event_log.get_events_by_type(etype):
            try:
                decisions = router._route_event(rec)  # 唯一路由真源 (同模块内复用私有方法, 与
                # resume_orchestrator._pending_spawn_decisions 同一惯例)
            except Exception as exc:  # 路由异常本身是要捕捉的信号 (见函数 docstring), 绝不吞
                if _has_active_dead_letter(rec.event_id):
                    continue
                gaps.append(ForwardChainGap(
                    trigger_event_id=rec.event_id, trigger_event_type=type_key,
                    routable=False, route_error=repr(exc)[:300],
                ))
                continue
            for d in decisions:
                if d.dispatch_to in _FORWARD_CHAIN_SKIP_TARGETS:
                    continue
                if is_already_dispatched(towow_dir, d.trigger_event_id, d.dispatch_to):
                    if not _dispatch_marker_is_fake(towow_dir, d.trigger_event_id, d.dispatch_to):
                        continue  # 戳存在且内容是真派发信号 — 不是缺口
                    if _terminal_finding_for_decision(event_log, d) is not None:
                        continue  # 假戳, 但下游已经经另一条权威路径真正解决 — 不误捞 (F-026-5 原意)
                    # 假戳 (skip-on-resume / mock 未真起会话) 且无等价终态证据 — 落入下面当缺口捞回
                if _has_backlog_marker(d.trigger_event_id, d.dispatch_to):
                    continue
                gaps.append(ForwardChainGap(
                    trigger_event_id=d.trigger_event_id, trigger_event_type=d.trigger_event_type,
                    routable=True, dispatch_to=d.dispatch_to,
                    review_mode=d.review_mode, task_id=d.task_id,
                ))
    return tuple(gaps)


# ── ObservedState builder (从 canonical 读真实状态, 纯读不写) ──────────────────────────


def build_observed_state(towow_dir: Path, event_log: EventLog) -> ObservedState:
    """从 canonical 事件 + 派生 marker 建对账输入快照。

    desired 与 observed 都在此算 (reconcile_all 是纯函数, 全部输入靠这一处供)。
    本步供 Replan + DeadLetter + Escalation 三个 reconciler 的状态 (ReadySet 走既有
    _dispatch_execution_batch, 不在此重做 — 见模块 docstring; 故 ready_tasks/dispatched_or_active
    留空, ReadySetReconciler 在此 ObservedState 下自然产 0 动作, 不与既有派发路径孪生)。
    """
    from towow.l2.orchestrator import is_already_dispatched, pending_escalations

    # ── Replan: 期望 = 每个 RePlanTriggered 都有对应 re-decompose; 实际 = 已派 planning 或已完成 ──
    replan_triggered: list[tuple[str, str]] = []
    redecomposed: set[str] = set()
    for rec in event_log.get_events_by_type(EventType.RE_PLAN_TRIGGERED):
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        plan_id = after.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            # plan_id 缺失的 RePlanTriggered = 畸形, 不入对账 (保守: 不盲派一个无目标 planning)。
            continue
        suggested = after.get("suggested_action")
        # full_replan 不自动重做整 plan (需 Nature 确认) — 不进 reconciler 的 desired 集。
        # 缺省/partial_replan 才自动 level-triggered 兜底重拆。
        if suggested == "full_replan":
            continue
        eid = rec.event_id
        replan_triggered.append((eid, plan_id))
        # ★ 幂等关键 (advisor 点名): observed 必含【已派 planning 但未完成】, 否则每 interval 风暴。
        # 复用既有 composite 戳 (replan_event_id, "planning") — 不新造平行 claim 路径。
        if is_already_dispatched(towow_dir, eid, _replan_dispatch_to()):
            redecomposed.add(eid)

    # ── DeadLetter: 期望 = 每个非终态 entry 被分诊到终态; 实际 = entry 的失败类 ──
    dead_letter_pending: list[tuple[str, FailureClass]] = []
    for entry in dead_letter_inbox.list_entries(towow_dir, include_terminal=False):
        # 只对 ENQUEUED 起分诊 (under_triage/escalated 已在途, 不重复领取)。
        if entry.triage_status != dead_letter_inbox.DeadLetterState.ENQUEUED.value:
            continue
        fclass = _ENTRY_REASON_TO_FAILURE_CLASS.get(entry.entry_reason, FailureClass.UNKNOWN)
        dead_letter_pending.append((entry.entry_id, fclass))

    # ── Escalation: 期望 = 每条 raised escalation 已 answered/aged 或已 surface 到 owner; 实际 =
    # 已 NJ-answer (pending_escalations 已滤掉) 或已被 (edge _route_event / 上一轮本兜底) 盖过
    # (esc_event_id, "main-inbound") 复合戳。escalations_unsurfaced = raised 但既没 NJ-answer 又没
    # 任何 main-inbound surface 戳的 = edge route 漏的 → 本兜底层补 surface (治死电路换层复发) ──
    escalations_unsurfaced: list[str] = []
    for esc in pending_escalations(event_log):
        esc_eid = esc.get("escalation_event_id")
        if not isinstance(esc_eid, str) or not esc_eid:
            continue
        # 复用与 edge route 同一个 (esc_event_id, "main-inbound") 复合戳 → 边/兜底互去重, 不刷屏。
        if is_already_dispatched(towow_dir, esc_eid, "main-inbound"):
            continue
        escalations_unsurfaced.append(esc_eid)

    return ObservedState(
        ready_tasks=(),  # ReadySet 走既有 _dispatch_execution_batch, 此处不重做 (防孪生)
        dispatched_or_active=frozenset(),
        replan_triggered=tuple(replan_triggered),
        redecomposed=frozenset(redecomposed),
        dead_letter_pending=tuple(dead_letter_pending),
        escalations_unsurfaced=tuple(escalations_unsurfaced),
        forward_chain_pending=_scan_forward_chain_gaps(towow_dir, event_log),
    )


# ── action 执行 (经既有幂等戳; 纯读核算出动作, 这里才落副作用) ─────────────────────────


def _emit_dead_letter_escalation(
    event_log: EventLog, entry: dead_letter_inbox.DeadLetterEntry,
) -> str:
    """死信箱 escalate 分支产真 GoalEscalationRaised (与 owner-gate escalation 同范式), 返回 event_id。

    治死电路 (f-sub-deadletter-escalate-dead-circuit / M23-F1): 旧 escalate 只调 decide_escalate 翻
    条目态 (标 escalated_to_owner) 却从不产真 escalation 事件 → _route_event 没东西可 route → owner
    永远收不到 ("静默丢"换层复发)。这里产真 GoalEscalationRaised (走 orchestrator 既有
    _build_orch_nodetouched stub-rewrap 范式, 与 emit_owner_gate_escalation 一致), 下一轮 _route_event
    见它 route 到 main-inbound (owner 可见) + escalation_lifecycle reducer 物化进 owner 收件箱投影
    (M23-F2 World-A 统一)。escalation_kind=dead_letter; blocking_scope=session_only (只这一条死信等
    owner, 不全局 pause autopilot — 同 owner-gate 不复发 T-L0-02 一条 escalation 停后台 4 天的病)。
    """
    from towow.l2.orchestrator import _build_orch_nodetouched

    decision_id = f"dead-letter-esc-{entry.entry_id}"
    synthetic_sid = f"dead-letter-{entry.entry_id}"
    payload: dict[str, object] = {
        "kind": "GoalEscalationRaised",
        "decision_id": decision_id,
        "escalation_id": decision_id,
        "goal_session_id": synthetic_sid,
        "owner_question": (
            f"⛔ 死信箱条目 {entry.entry_id} (源 {entry.source_object_type}="
            f"{entry.source_object_ref}, 进入原因={entry.entry_reason}) 机器分诊不了/路由不出, "
            f"需要你拍板怎么处置 (重投 / 退役 / 改派)。"
        ),
        "reason": "dead_letter_escalate",
        "raised_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "awaiting_response": True,
        "blocking_scope": "session_only",
        "escalation_kind": "dead_letter",
        "dead_letter_entry_id": entry.entry_id,
        # 源对象 (task / finding / session ...) — reducer 据 type 路由到 related_task_ids /
        # related_finding_ids, owner 收件箱能反查"这条 escalation 卡的是哪个对象"。
        "dead_letter_source_type": entry.source_object_type,
        "dead_letter_source_ref": entry.source_object_ref,
    }
    intent = _build_orch_nodetouched(
        kind="GoalEscalationRaised",
        decision_id=decision_id,
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def _execute_escalation_action(
    towow_dir: Path, event_log: EventLog, action: ReconcileAction,
) -> bool:
    """owner 监督命根的 level 兜底: raised 但漏 surface 的 escalation → 补一条 main-inbound surface。

    复用与 edge _route_event 同一个 (esc_event_id, "main-inbound") 复合戳 → 边/兜底互相去重, 同一
    escalation 只 surface 一次 (不刷屏)。emit OrchestratorDispatched(main-inbound) = main_inbound_poller
    真消费 → owner 真看见 (不是只翻条目状态的死电路)。纯 surface 可见性, 不在此 re-pause (pause 归
    edge 路径处理过的; 漏 surface 的兜底只补可见, 不二次停后台)。返回 True = 真补了一条 surface。
    """
    from towow.l2.orchestrator import (
        DispatchDecision,
        emit_orchestrator_dispatched,
        is_already_dispatched,
        mark_dispatched,
    )

    esc_event_id = action.target
    # 二次幂等护栏 (build→execute 间隙 edge 可能刚 surface): 戳已在 → 跳过。
    if is_already_dispatched(towow_dir, esc_event_id, "main-inbound"):
        return False
    decision = DispatchDecision(
        trigger_event_id=esc_event_id,
        trigger_event_type=EventType.GOAL_ESCALATION_RAISED.value,
        dispatch_to="main-inbound",
        reason=(
            f"reconcile (level-triggered 兜底): escalation {esc_event_id} raised 但未 answer 且 edge "
            f"_route_event 漏 surface → 补 main-inbound 让 owner 可见 (治死电路换层复发)"
        ),
    )
    # 先盖戳再 emit: 戳是幂等关键 (下轮 observed/edge 见它即跳过); emit 是 owner 真可见的 surface。
    mark_dispatched(towow_dir, esc_event_id, dispatch_to="main-inbound")
    emit_orchestrator_dispatched(event_log, decision)
    return True


def _execute_replan_action(
    towow_dir: Path, event_log: EventLog, action: ReconcileAction,
) -> bool:
    """RePlan 死胡同的 level 兜底: 派一棒 planning 重拆 + 盖 composite 戳 (下轮 observed 即含它 → 不重派)。

    返回 True = 真派了一棒 (盖戳+emit dispatch)。已被 _route_event 边路径派过 (戳在) → reconcile 不会
    走到这 (build_observed_state 已把它算进 redecomposed)。这是 edge (快路径) 漏事件时的兜底。
    """
    from towow.l2.orchestrator import (
        DispatchDecision,
        emit_orchestrator_dispatched,
        is_already_dispatched,
        mark_dispatched,
    )

    replan_event_id = str(action.payload.get("replan_event_id") or "")
    plan_id = action.target
    if not replan_event_id:
        return False
    dispatch_to = _replan_dispatch_to()
    # 二次幂等护栏 (build 到 execute 之间可能被 edge 路径抢派): 戳已在 → 跳过。
    if is_already_dispatched(towow_dir, replan_event_id, dispatch_to):
        return False
    decision = DispatchDecision(
        trigger_event_id=replan_event_id,
        trigger_event_type=EventType.RE_PLAN_TRIGGERED.value,
        dispatch_to=dispatch_to,
        reason=(
            f"reconcile (level-triggered 兜底): RePlanTriggered {replan_event_id} 无对应 re-decompose "
            f"→ 派 planning 重拆 plan={plan_id} (edge _route_event 零 case 的死胡同修法)"
        ),
    )
    # 先盖戳再 emit: 盖戳是幂等关键 (下轮 observed 含它); emit 是显著审计 + 主 session 可见。
    mark_dispatched(towow_dir, replan_event_id, dispatch_to=dispatch_to)
    emit_orchestrator_dispatched(event_log, decision)
    return True


def _execute_dead_letter_action(
    towow_dir: Path, event_log: EventLog, action: ReconcileAction,
) -> bool:
    """死信箱分诊接活 (设计 §T3.3): start_triage → 按失败类 decide_*。

    ★ redispatch 的坑 (advisor 点名): decide_redispatch 的合约是【重派确认成功后才调】。本 reconcile
    不在此真重派 spawn (派 execution 走既有 _dispatch_execution_batch 主路径), 故:
      - exhausted → retire, 但 retire_reason 严格按 entry_reason 区分 (设计 §T3.3 表):
          * circuit_tripped (重派预算耗尽) → REENTRY_EXHAUSTED (= OTP MaxR/MaxT 重启超预算)。
          * structural_failure (同输入必败/启动自检崩) → DETERMINISTIC (= DLQ poison message)。
        ★ 绝不把 circuit_tripped 误标 DETERMINISTIC: 重派耗尽 ≠ "同输入永远必败" (advisor 点名)。
      - waiting (unroutable, 无 consumer) → escalate (产 escalation 等 owner; reflow 归 sibling 线)。
      - unknown/redispatch → ★绝不直接 decide_redispatch (会标 REDISPATCHED 却从没重派 = 新假完成)。
        保守留在 under_triage (start_triage 已迁), 等真重派路径确认成功再 decide_redispatch, 或下轮
        被 sweep_aged_out 兜底。绝不静默丢, 也绝不假装重派。

    返回 True = 真迁了条目态。
    """
    entry_id = action.target
    # action.kind 来自 exception_taxonomy.ACTION[fclass]: exhausted→"triage" / waiting→"escalate"
    # / unknown→"wait"。先统一领取分诊 (enqueued → under_triage)。
    entry = dead_letter_inbox.get_entry(towow_dir, entry_id)
    if entry is None:
        return False
    if entry.triage_status != dead_letter_inbox.DeadLetterState.ENQUEUED.value:
        return False  # 已在途 (并发/上轮已领), 不重复
    dead_letter_inbox.start_triage(
        towow_dir, event_log, entry_id, triage_actor="auto-reconcile",
    )
    # 领取后按 kind 决定终态。
    if action.kind == "escalate":
        # waiting: 等 owner → 先产真 GoalEscalationRaised (治死电路 f-sub-deadletter-escalate-dead-
        # circuit: 不再只翻条目态), 拿 escalation_event_id 关联进条目。下一轮 _route_event 见它 route
        # 到 main-inbound (owner 可见) + escalation_lifecycle reducer 物化进 owner 收件箱 (M23-F2)。
        escalation_event_id = _emit_dead_letter_escalation(event_log, entry)
        dead_letter_inbox.decide_escalate(
            towow_dir, event_log, entry_id, triage_actor="auto-reconcile",
            escalation_event_id=escalation_event_id,
        )
        return True
    if action.kind == "triage":
        # exhausted: retire — retire_reason 严格按 entry_reason 区分 (设计 §T3.3 表), 不混淆。
        # 回头客计数已达上限的条目 decide_retire 内部也会落 retired (must_force_retire 只挡 redispatch)。
        retire_reason = (
            dead_letter_inbox.DeadLetterRetireReason.DETERMINISTIC
            if entry.entry_reason
            == dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE.value
            else dead_letter_inbox.DeadLetterRetireReason.REENTRY_EXHAUSTED
        )
        dead_letter_inbox.decide_retire(
            towow_dir, event_log, entry_id, triage_actor="auto-reconcile",
            retire_reason=retire_reason,
        )
        return True
    # "wait" / "redispatch" / 其它: 保守留在 under_triage (绝不假装重派 / 绝不静默丢)。
    # under_triage 非死胡同 (有 → retired 边), stall 由 sibling stuck-detection / sweep 兜底。
    return True


def _execute_forward_chain_dispatch_action(
    towow_dir: Path, event_log: EventLog, action: ReconcileAction,
) -> bool:
    """forward-chain 缺口 (routable) 的 level 兜底: 补写非 exec backlog marker。

    不在此自己 mark_dispatched/emit —— 那会绕开真派发路径要过的一整套既有守卫 (governor 429 /
    统一活会话守卫 / 终态 finding 闸 / stale-backlog 阶段产出存在性检查), 等于重开一条平行派发
    通道 (正是本模块 docstring "忠实增强既有, 不 greenfield" 明令禁止的)。write_nonexec_backlog_marker
    是 T-FIX-B2-05 既有的"待重派发现"落地点: 派发循环下一轮 (_read_nonexec_backlog_decisions, 独立
    于水位线) 会把它并入同一条非 exec 派发路径重过一遍全部守卫, 真派发/真 spawn 走既有代码, 本函数
    不重新实现。

    返回 True = 真补写了一条 marker。二次幂等护栏: build→execute 间隙可能已被 edge 路径/上一轮
    backlog re-scan 赶上 (is_already_dispatched 戳已在) → 跳过, 不重复补写。D-2: 这条护栏与
    _scan_forward_chain_gaps 必须用同一判据 —— 戳存在但内容自报假派发 (_dispatch_marker_is_fake)
    时不能被这里悄悄拦回去 (那会让 scan 阶段判出的缺口在 execute 阶段前功尽弃, forward_chain_backfilled
    永远是 0), 只有戳内容是真派发才当"已被赶上"。
    """
    from towow.l2.orchestrator import is_already_dispatched, write_nonexec_backlog_marker

    trigger_event_id = action.target
    dispatch_to = action.payload.get("dispatch_to")
    if not isinstance(dispatch_to, str) or not dispatch_to:
        return False
    if is_already_dispatched(towow_dir, trigger_event_id, dispatch_to) and not _dispatch_marker_is_fake(
        towow_dir, trigger_event_id, dispatch_to,
    ):
        return False
    review_mode = action.payload.get("review_mode")
    task_id = action.payload.get("task_id")
    write_nonexec_backlog_marker(
        towow_dir, trigger_event_id, dispatch_to,
        trigger_event_type=str(action.payload.get("trigger_event_type") or ""),
        review_mode=review_mode if isinstance(review_mode, str) else None,
        task_id=task_id if isinstance(task_id, str) else None,
        reason=action.reason,
    )
    return True


def _execute_forward_chain_deadletter_action(
    towow_dir: Path, event_log: EventLog, action: ReconcileAction,
) -> bool:
    """forward-chain 缺口 (not routable, 路由本身结构性失败) 的兜底: 投死信箱
    (entry_reason=unroutable) — 复用既有 DeadLetterReconciler 分诊 (WAITING→escalate 产真
    GoalEscalationRaised, owner 可见, 与 orchestrator 现有"no-route"入箱同一范式)。

    enqueue 自带幂等 (同 source_object_ref+entry_reason 已有活跃条目 → 返回既有, 不重复入箱); 已
    终态的历史条目再次命中会按既有"回头客"语义计入 reentry_count (dead_letter_inbox 自身职责, 不
    在此重复判断 —— 与其它 reconciler 的二次幂等护栏一致, 复用既有幂等而非新造)。
    """
    dead_letter_inbox.enqueue(
        towow_dir, event_log,
        source_object_type="forward_chain_trigger",
        source_object_ref=action.target,
        entry_reason=dead_letter_inbox.DeadLetterEntryReason.UNROUTABLE,
        original_trigger_event_id=action.target,
    )
    return True


def run_reconcile_pass(
    towow_dir: Path,
    event_log: EventLog,
    *,
    watermark_before: int | None = None,
    watermark_after: int | None = None,
    exec_dispatched_count: int = 0,
    active_session_count: int = 0,
) -> dict[str, int]:
    """跑一次 level-triggered 对账: 建 ObservedState → reconcile_all → 经既有幂等戳落地动作。

    纯兜底 (不抢 edge 快路径已派的): replan/dead_letter 动作经既有 composite 戳 / 死信箱 5 态机幂等,
    反复跑同 state → 同结果 (重复扫到同一项只迁移一次)。绝不新造平行 dedup。

    返回各类执行计数 (status/test 可见)。单个 action 失败不崩对账 (一个烂项不拖垮兜底), 但
    ★绝不静默吞 (anti-silent-failure 铁律 — 一个 suppress 早把 layout bug 藏过一次): 失败计入
    counts["action_failed"] + emit 一条可观测审计事件, 不让对账失败消失。

    ## 哨兵 A3 空转源 (reconcile-cycle-count-emission@v1) — INF-003 只观测
    watermark_before/after 非 None 时, 收尾发布一条 ReconcileCyclePublished (本轮活动快照五计数),
    供哨兵 A3 空转探测 (detect_a3_reconcile: 跨 cycles 水位涨但全 0 真派发 0 活会话 = daemon 吃自己
    心跳空转)。这五个数 orchestrator polling loop 在 call site 算好传进来 (watermark load 前后 /
    _dispatch_execution_batch 返回的本轮 exec spawn 数 / active_relay_sessions 数), 本函数补上自己
    算出的 replan/dead_letter 派发数凑成 dispatched_count。

    🔴 dispatched_count 不变量 (INV-SENT-A3-NO-HEARTBEAT): 只计【真前进派发】= exec spawn (orchestrator
    传入) + replan dispatch + dead-letter triage (本函数), 显式排除 daemon 吃自己 DaemonRunCompleted
    心跳 —— 纯吃心跳的轮这三项全 0 → dispatched_count=0, A3 才能识别成空转。

    🔴 INV-SENT-EMIT-ONLY: 发布纯【加在动作落地之后】, 绝不改 reconcile_all 算动作 / 派发判定。
    watermark_before=None (裸调 / 单测) → 不 emit, 函数保持独立可测 (INF-003 只观测)。
    """
    from towow.l2.orchestrator import ensure_orchestrator_layout

    counts_disabled = {
        "replan_dispatched": 0, "dead_letter_triaged": 0, "escalation_surfaced": 0,
        "forward_chain_backfilled": 0, "skipped": 0, "action_failed": 0,
    }
    if reconcile_disabled():
        # operator kill-switch: reconcile 整体跳过 (edge 快路径不受影响)。仍发布 cycle 快照 (action_count=0,
        # dispatched_count 只含 orchestrator 传入的 exec spawn) — A3 空转探测照样要看这轮有没有真前进。
        _maybe_emit_reconcile_cycle(
            event_log,
            watermark_before=watermark_before, watermark_after=watermark_after,
            exec_dispatched_count=exec_dispatched_count,
            reconcile_dispatched_count=0,
            active_session_count=active_session_count, action_count=0,
        )
        return counts_disabled

    # dispatched/ 戳目录 + 死信箱目录幂等建好 (run_polling_loop 已建, 但 run_reconcile_pass 也可被
    # 单独调/测, 自包含建目录防 mark_dispatched 写戳时 FileNotFoundError 被吞)。
    ensure_orchestrator_layout(towow_dir)
    dead_letter_inbox.ensure_dead_letter_layout(towow_dir)
    # f-orchestrator-restart-no-forward-chain-catchup: 周期 forward-chain catch-up —
    # 在 Replan/DeadLetter/Escalation 对账同批次清掉死 session 的非 exec 复合戳 + 写
    # backlog, 让 poll loop 下轮即重派而非等 silent-death-reaper 慢周期。绝不崩对账。
    import contextlib
    with contextlib.suppress(Exception):
        # T-SELFHEAL-STALE-RELAY: 延迟导入避免循环 (orchestrator 模块级 import 本文件)。
        from towow.l2.orchestrator import cached_roster_ids
        run_startup_catchup_pass(towow_dir, event_log, roster_ids_fn=cached_roster_ids)
    state = build_observed_state(towow_dir, event_log)
    actions = reconcile_all(state)
    counts = {
        "replan_dispatched": 0, "dead_letter_triaged": 0, "escalation_surfaced": 0,
        "forward_chain_backfilled": 0, "skipped": 0, "action_failed": 0,
    }
    for action in actions:
        try:
            if action.kind == "replan":
                if _execute_replan_action(towow_dir, event_log, action):
                    counts["replan_dispatched"] += 1
                else:
                    counts["skipped"] += 1
            elif action.kind in ("escalate", "triage", "wait", "redispatch"):
                # dead_letter reconciler 的产出 (kind = ACTION[fclass])。
                if _execute_dead_letter_action(towow_dir, event_log, action):
                    counts["dead_letter_triaged"] += 1
                else:
                    counts["skipped"] += 1
            elif action.kind == "escalation_surface":
                # EscalationReconciler 的产出 — raised 但漏 surface 的 escalation 补 main-inbound 可见。
                if _execute_escalation_action(towow_dir, event_log, action):
                    counts["escalation_surfaced"] += 1
                else:
                    counts["skipped"] += 1
            elif action.kind == "forward_chain_dispatch":
                # ForwardChainReconciler 的产出 (routable): 补写非 exec backlog marker, 下一轮派发
                # 循环走既有非 exec 派发路径真派 (C-1 静默丢活防治)。
                if _execute_forward_chain_dispatch_action(towow_dir, event_log, action):
                    counts["forward_chain_backfilled"] += 1
                else:
                    counts["skipped"] += 1
            elif action.kind == "forward_chain_deadletter":
                # ForwardChainReconciler 的产出 (not routable): 投死信箱等分诊 (C-1 必达或死信)。
                if _execute_forward_chain_deadletter_action(towow_dir, event_log, action):
                    counts["forward_chain_backfilled"] += 1
                else:
                    counts["skipped"] += 1
            elif action.kind == "dispatch":
                # ReadySet — 走既有 _dispatch_execution_batch, 此处 ObservedState 不喂 ready_tasks
                # 故正常不会到这; 真到了 (防御) 也不在此另派 (防双 dedup 孪生)。
                counts["skipped"] += 1
            else:
                counts["skipped"] += 1
        except Exception as exc:
            counts["action_failed"] += 1
            _emit_reconcile_action_failed(event_log, action, exc)

    # 哨兵 A3 空转源: 收尾发布本轮活动快照五计数 (INV-SENT-EMIT-ONLY — 纯加在动作落地之后)。
    # forward_chain_backfilled 计入 dispatched_count (INV-SENT-A3-NO-HEARTBEAT 同口径): 补写 backlog
    # marker / 投死信都是【真前进】, 不是心跳空转 (与 replan_dispatched/dead_letter_triaged 同类)。
    _maybe_emit_reconcile_cycle(
        event_log,
        watermark_before=watermark_before, watermark_after=watermark_after,
        exec_dispatched_count=exec_dispatched_count,
        reconcile_dispatched_count=(
            counts["replan_dispatched"]
            + counts["dead_letter_triaged"]
            + counts["forward_chain_backfilled"]
        ),
        active_session_count=active_session_count, action_count=len(actions),
    )
    return counts


def _maybe_emit_reconcile_cycle(
    event_log: EventLog,
    *,
    watermark_before: int | None,
    watermark_after: int | None,
    exec_dispatched_count: int,
    reconcile_dispatched_count: int,
    active_session_count: int,
    action_count: int,
) -> None:
    """哨兵 A3 空转源 — emit 一条 ReconcileCyclePublished (本轮 reconcile 活动快照五计数)。

    watermark_before/after 任一为 None (裸调 / 单测, 无 orchestrator 上下文) → 不 emit, 保持
    run_reconcile_pass 独立可测 (INF-003 只观测; 发布失败绝不崩对账)。

    🔴 dispatched_count = exec_dispatched_count (orchestrator 本轮 _dispatch_execution_batch 真派的
    工位数) + reconcile_dispatched_count (本函数 replan + dead-letter triage 数)。三源都是【真前进
    派发】, 都不含 daemon 吃自己 DaemonRunCompleted 心跳 (INV-SENT-A3-NO-HEARTBEAT): 纯吃心跳的轮
    exec 批不派 + reconcile 无 replan/triage → dispatched_count=0 → A3 识别空转。
    """
    if watermark_before is None or watermark_after is None:
        return  # 无 orchestrator 上下文 (裸调/单测) — 不发布, 函数保持独立可测

    import contextlib
    import uuid

    from towow.l2.orchestrator import _ORCH_ACTOR_ID, _ORCH_ACTOR_TYPE
    from towow.schemas.enums import (
        BaseClassification,
        EventCategory,
        SubjectEntityType,
        SubjectRole,
    )
    from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

    dispatched_count = exec_dispatched_count + reconcile_dispatched_count
    decision_id = f"reconcile-cycle-{uuid.uuid4().hex[:8]}"
    # 发布失败绝不拖垮对账 (anti-silent-failure: 发布是观测面, 不是对账主路径 — suppress 但只这一层)。
    with contextlib.suppress(Exception):
        intent = EventIntent(
            local_intent_id=f"reconcile-cycle-{uuid.uuid4().hex[:12]}",
            event_type=EventType.RECONCILE_CYCLE_PUBLISHED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={
                "watermark_before": int(watermark_before),
                "watermark_after": int(watermark_after),
                "dispatched_count": int(dispatched_count),
                "active_session_count": int(active_session_count),
                "action_count": int(action_count),
            },
            provenance_hint=ProvenanceHint(
                actor_type=_ORCH_ACTOR_TYPE,
                actor_id=_ORCH_ACTOR_ID,
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.TASK,
                    entity_id=decision_id,
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )
        event_log.write_direct(intent)


def _emit_reconcile_action_failed(
    event_log: EventLog, action: ReconcileAction, exc: Exception,
) -> None:
    """对账动作失败 → emit 可观测审计 (绝不静默吞)。本身失败也不再抛 (最后一道, 真崩了认了)。"""
    import contextlib

    from towow.l2.orchestrator import _build_orch_nodetouched

    with contextlib.suppress(Exception):
        intent = _build_orch_nodetouched(
            kind="ReconcileActionFailed",
            decision_id=f"reconcile-{action.kind}-{action.target}"[:120],
            payload_body={
                "kind": "ReconcileActionFailed",
                "action_kind": action.kind,
                "target": action.target,
                "reason": action.reason,
                "error": repr(exc)[:500],
            },
        )
        event_log.write_direct(intent)


def run_startup_catchup_pass(
    towow_dir: Path,
    event_log: EventLog,
    *,
    grace_period_s: float = 90.0,
    _daemon_state_fn: Callable[[str], str | None] | None = None,
    roster_ids_fn: Callable[[], frozenset[str] | None] | None = None,
) -> dict[str, int]:
    """启动时 + 周期性 forward-chain catch-up: 直接清掉 session 已死的非 exec 复合戳, 让
    backlog re-scan 立即重派被 stranded 的 review/close baton, 不等 silent-death-reaper
    慢周期 (f-orchestrator-restart-no-forward-chain-catchup)。

    与 reconcile_orphaned_sessions 的区别:
      - RUNNING/STALLED/UNKNOWN session: 从 kind 锁的 session_signal 取 proc_alive 信号:
          proc_alive=False (pid 死) → vitality Rule-6 DEAD → 清戳 (T-LRF-11 stale RUNNING 修复)
          proc_alive=True  (pid 活, 仅心跳 stale) → vitality UNKNOWN → 保守 skip (FLP 保护)
          proc_alive=None  (无锁, 无可判进程) → vitality UNKNOWN → 保守 skip (安全边;
          T-SELFHEAL-STALE-RELAY 同款 roster 覆盖补上——无锁场景本没有 kind-lock proc_alive
          第二信号可兜, 官方名单 (roster_ids_fn) 缺席这个 gsid 时把 assess_session_liveness
          的 RUNNING/STALLED 判定纠正成 MISSING, 不再单靠这一个可能滞后的 state.json 字段)
      - 调用时机: 启动时 (run_polling_loop) + 120s 周期 (run_reconcile_pass)

    coverage: pending_session 标记的非 exec 工位 (review/fix/close/...)。exec 工位由
    reap_stale_exec_claims / reap_silently_dead_exec_stamps 处理。无 pending 标记的
    stamp (launched=False) 是已知残留 gap, 不在此覆盖。

    Args:
        grace_period_s: pending marker 多旧才处理, 默认 90s, 保护刚起的 session 不被误清。
        _daemon_state_fn: 测试注入; None = 读真实 ~/.claude/jobs 的 state.json。
        roster_ids_fn: T-SELFHEAL-STALE-RELAY 同款注入点 (session_vitality.roster_session_ids
            / orchestrator.cached_roster_ids)。默认 None = 不查, 逐位保持改动前行为 (本函数
            只清本地 marker/戳, 不 emit GoalSessionTerminated, 不是 owner 报告的空转症状根因
            路径; 补这条只是同款盲点的一致性收口, 不改变函数本身的落地效果范围)。

    Returns dict: cleared_dead / done_cleared / skipped_alive / skipped_unknown / error.
    """
    import contextlib
    import json

    from towow.l2.orchestrator import (
        _pending_sessions_dir,
        _workitem_product_exists,
        clear_nonexec_dispatch_stamp,
        write_nonexec_backlog_marker,
    )
    from towow.l2.session_liveness import SessionLivenessVerdict, assess_session_liveness
    from towow.l2.session_vitality import VitalityVerdict, assess_vitality

    counts: dict[str, int] = {
        "cleared_dead": 0,
        "done_cleared": 0,
        "skipped_alive": 0,
        "skipped_unknown": 0,
        "error": 0,
    }

    try:
        pdir = _pending_sessions_dir(towow_dir)
    except Exception:
        return counts
    if not pdir.is_dir():
        return counts

    # T-SELFHEAL-STALE-RELAY: 本轮只查一次官方名单 (同 orchestrator.reconcile_orphaned_sessions
    # 同规格), 所有候选 gsid 共用同一份快照。roster_ids_fn=None (默认) → 不查, 行为不变。
    if roster_ids_fn is None:
        roster_ids: frozenset[str] | None = None
    else:
        try:
            roster_ids = roster_ids_fn()
        except Exception:
            roster_ids = None

    for pf in sorted(pdir.glob("*.json")):
        gsid = pf.stem
        marker_task_id: str | None = None
        marker_trigger_id: str | None = None
        marker_dispatch_to: str | None = None
        marker_trigger_type: str = ""
        marker_review_mode: str | None = None
        marker_recorded_at: float = 0.0

        with contextlib.suppress(OSError, json.JSONDecodeError):
            body = json.loads(pf.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                tid = body.get("task_id")
                marker_task_id = str(tid) if tid else None
                trig = body.get("trigger_event_id")
                marker_trigger_id = str(trig) if trig else None
                dto = body.get("dispatch_to")
                marker_dispatch_to = str(dto) if dto else None
                marker_trigger_type = str(body.get("trigger_event_type", "") or "")
                rmode = body.get("review_mode")
                marker_review_mode = str(rmode) if rmode else None
                with contextlib.suppress(TypeError, ValueError):
                    marker_recorded_at = float(body.get("recorded_at", 0.0))

        # exec 工位由 reap_stale_exec_claims / reap_silently_dead_exec_stamps 处理
        if not marker_trigger_id or not marker_dispatch_to or marker_dispatch_to == "execution":
            continue

        # 宽限期保护: 最近 grace_period_s 内 spawn 的 session 不清
        # (down-window 遗留 session 必然超龄 >> 90s; 刚起的 session 必然在宽限期内)
        import time as _time
        if grace_period_s > 0 and marker_recorded_at > 0 and _time.time() - marker_recorded_at < grace_period_s:
            counts["skipped_alive"] += 1
            continue

        # planning / consensus: 先查 canonical work-product, 有则已出货不重派
        if marker_dispatch_to in ("engineering-consensus", "planning"):
            with contextlib.suppress(Exception):
                if _workitem_product_exists(event_log, marker_dispatch_to, marker_trigger_id):
                    with contextlib.suppress(OSError):
                        pf.unlink()
                    counts["done_cleared"] += 1
                    continue

        # 先查 kind 特定锁: 非 exec 会话 (review/fix/planning) 在活着时持同名 kind 锁。
        # vitality 只查 "execution" 锁 → 必须在这里自查 kind 锁。
        # session_signal 同时返回 proc_alive (pid 活性正向证据), 用它驱动后续 vitality:
        #   holds_live_lock=True                → skip (会话确凿在运行)
        #   holds_live_lock=False, proc=False   → pid 死 → Rule-6 DEAD → 清戳 (T-LRF-11)
        #   holds_live_lock=False, proc=True    → pid 活, 仅心跳 stale → 保守 skip (FLP 保护)
        #   holds_live_lock=False, proc=None    → 无锁 / 无可判进程 → UNKNOWN → 保守 skip
        kind_proc_alive: bool | None = None
        with contextlib.suppress(Exception):
            from towow.l1.session_lock import SessionLockRegistry
            kind_proc_alive, holds_kind_lock = SessionLockRegistry(
                towow_dir, marker_dispatch_to,
            ).session_signal(gsid)
            if holds_kind_lock:
                counts["skipped_alive"] += 1
                continue

        # 用 liveness 取显式死亡信号, 再结合 kind_proc_alive 构造 process_alive_fn
        process_alive_fn = None
        try:
            live = assess_session_liveness(
                gsid, event_log, daemon_state_fn=_daemon_state_fn, roster_ids=roster_ids,
            )
            if live.verdict is SessionLivenessVerdict.COMPLETED:
                # 会话已自报完工, 只清 marker
                with contextlib.suppress(OSError):
                    pf.unlink()
                counts["done_cleared"] += 1
                continue
            if live.verdict in (
                SessionLivenessVerdict.STOPPED,
                SessionLivenessVerdict.MISSING,
            ):
                # 进程确凿没了 (state=stopped/无 state.json) → Rule-6 DEAD
                process_alive_fn = lambda _g: False  # noqa: E731
            elif kind_proc_alive is not None:
                # RUNNING/STALLED/UNKNOWN + kind 锁提供 pid 活性信号:
                # False (pid 死, 锁 stale) → Rule-6 DEAD → 清戳 (T-LRF-11 stale RUNNING 根修)
                # True  (pid 活, 仅心跳 stale) → UNKNOWN → 保守 skip (FLP 保护: 慢会话不误杀)
                process_alive_fn = lambda _g, _v=kind_proc_alive: _v  # noqa: E731
            # kind_proc_alive=None (无锁) → process_alive_fn=None → vitality UNKNOWN → 保守 skip
            # (已知残留: 无锁死会话靠 silent-death-reaper 兜, 已登 debt)
        except Exception:
            counts["error"] += 1
            continue

        try:
            vit = assess_vitality(
                gsid,
                task_id=marker_task_id,
                event_log=event_log,
                process_alive_fn=process_alive_fn,
                aborted_after_ts=marker_recorded_at if marker_recorded_at > 0 else None,
            )
        except Exception:
            counts["error"] += 1
            continue

        if vit.verdict is VitalityVerdict.DEAD:
            with contextlib.suppress(Exception):
                clear_nonexec_dispatch_stamp(towow_dir, marker_trigger_id, marker_dispatch_to)
                write_nonexec_backlog_marker(
                    towow_dir, marker_trigger_id, marker_dispatch_to,
                    trigger_event_type=marker_trigger_type,
                    review_mode=marker_review_mode,
                    reason=(
                        f"startup-catchup dead {gsid[:20]} "
                        f"(liveness={process_alive_fn is not None}) stamp cleared"
                    ),
                )
                with contextlib.suppress(OSError):
                    pf.unlink()
            counts["cleared_dead"] += 1
        elif vit.verdict is VitalityVerdict.DONE:
            with contextlib.suppress(OSError):
                pf.unlink()
            counts["done_cleared"] += 1
        elif vit.verdict in (
            VitalityVerdict.ALIVE_WORKING,
            VitalityVerdict.PARKED_RESUMABLE,
            VitalityVerdict.STUCK_WAITING,
        ):
            counts["skipped_alive"] += 1
        else:  # UNKNOWN
            counts["skipped_unknown"] += 1

    return counts
