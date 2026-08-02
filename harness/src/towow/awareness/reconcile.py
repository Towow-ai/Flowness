"""C-2 · level-triggered 对账循环核心 (层③, 设计 20-layer23 §5.1)。

owner 第④点 "自动旋转队列、不手动推" 的核心: 反复读"期望态 vs 实际态"的 diff → 驱动幂等动作,
**漏事件下一轮从当前状态照样收敛**(k8s 控制器模型: edge-triggered 是优化, level-triggered 是兜底)。

## 本质 (为什么 level 比 edge 对)
edge-triggered (现 _route_event switch) 的响应种类 = 列的 case 数; 现实事件种类 > 它 (Ashby 律必漏)
→ RePlanTriggered 死胡同 (producer 在、consumer 没) = 漏事件的活样本, 落账即失踪、下游永久卡。
level-triggered 把"靠捕获每个事件"换成"反复对比当前期望vs实际", 漏一个下轮收敛。

## 纯函数 + 注册表 (新对账项加一个 Reconciler 不改循环, 呼应 per-form adapter)
本模块只算动作 (纯, 不 emit/不 spawn), 易测低风险; orchestrator 拿动作去经原子认领(K5)+幂等戳落地
= 后续 l2 接线步 (run_polling_loop 每 RECONCILE_INTERVAL 跑一次 reconcile_all)。

## 幂等 (level-triggered 硬配套)
动作从当前 state 算, 反复跑同 state → 同动作; observed 必含"已派但未完成"(不只"已完成"), 否则风暴。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .exception_taxonomy import ACTION, FailureClass


@dataclass(frozen=True)
class ReconcileAction:
    kind: str       # dispatch / replan / escalate / retire / redispatch / revive / wait / circuit_break
    target: str     # task_id / plan_id / entry_id / gsid
    reason: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ForwardChainGap:
    """finding fnd-forward-chain-silent-skip-head-published-brief: 一条命中 E.5 FORWARD_CHAIN_REGISTRY
    边、却从未产出可查 DispatchDecision 的触发事件 (InterviewBriefPublished→consensus /
    EngineeringConsensusFreezed→planning / TaskRunCompleted|FixCompleted→review 等)。水位线快进/
    daemon 重启窗口可以越过一个从未被 _route_event 扫过的事件, edge-triggered 路由对此无能为力——
    只有反复比对"已发布触发事件 vs 已有 DispatchDecision/死信"的 diff 才能兜住 (与 Replan/DeadLetter
    同一 level-triggered 范式)。

    routable=True  → 今天重新路由仍能产出 decision (dispatch_to/review_mode/task_id 是重建非 exec
                      backlog marker 所需的最小信息), 走既有 backlog re-scan 补派。
    routable=False → 路由本身抛异常 (payload 畸形等结构性故障), 不可重派 → 落死信箱等 owner 分诊
                      (route_error 记诊断用异常摘要)。
    """

    trigger_event_id: str
    trigger_event_type: str
    routable: bool
    dispatch_to: str | None = None
    review_mode: str | None = None
    task_id: str | None = None
    route_error: str | None = None


@dataclass(frozen=True)
class ObservedState:
    """对账的当前真实状态快照 (从 canonical 投影读, 纯输入)。desired 与 observed 都在此。"""

    # ReadySet (自动派发队列): 期望 = ready 的都已派/在跑; 实际 = 已认领戳或有活会话的 task。
    ready_tasks: tuple[str, ...] = ()
    dispatched_or_active: frozenset[str] = frozenset()
    # Replan (冲突/边界错返工): 期望 = 每个 RePlanTriggered 都有对应 re-decompose; 实际 = 已 re-decompose。
    replan_triggered: tuple[tuple[str, str], ...] = ()  # (replan_event_id, plan_id)
    redecomposed: frozenset[str] = frozenset()           # 已有 re-decompose 的 replan_event_id
    # DeadLetter (耗尽分诊): 期望 = 每个非终态 entry 被分诊到终态; 实际 = entry 的失败类。
    dead_letter_pending: tuple[tuple[str, FailureClass], ...] = ()  # (entry_id, failure_class)
    # Escalation (owner 待决可见性): 期望 = 每个 raised escalation 已 answered/aged **或已 surface 到
    # owner (main-inbound)**; 实际 = 已被 NJ answer (escalation_subscribe responds_to 契约) 或边触发
    # _route_event 已 surface 过。escalations_unsurfaced = raised 但既未 answered 又没任何 main-inbound
    # surface 的 escalation event_id (edge route 漏的, 由本兜底层补 surface — 治"死电路换层复发")。
    escalations_unsurfaced: tuple[str, ...] = ()  # escalation event_id 列表
    # ForwardChain (C-1: 静默丢活防治): 期望 = 每条命中 FORWARD_CHAIN_REGISTRY 的触发事件都已产出
    # DispatchDecision (backlog marker 已补) 或已落死信; 实际 = 尚未覆盖的缺口 (见 ForwardChainGap)。
    forward_chain_pending: tuple[ForwardChainGap, ...] = ()


class Reconciler(Protocol):
    name: str
    def reconcile(self, state: ObservedState) -> list[ReconcileAction]: ...


class ReadySetReconciler:
    """owner 第④点核心: ready 但未派/无活会话 → 派。level-triggered = 漏派也下轮补 (收编 backlog re-scan)。"""

    name = "ready_set"

    def reconcile(self, state: ObservedState) -> list[ReconcileAction]:
        return [
            ReconcileAction("dispatch", t, "ready 但未认领/无活会话")
            for t in state.ready_tasks
            if t not in state.dispatched_or_active
        ]


class ReplanReconciler:
    """RePlanTriggered 死胡同的 level 兜底: 有 RePlan 无对应 re-decompose → 派 planning 重拆 (her 冲突返工)。"""

    name = "replan"

    def reconcile(self, state: ObservedState) -> list[ReconcileAction]:
        return [
            ReconcileAction("replan", plan_id, f"RePlanTriggered {eid} 无对应 re-decompose",
                            {"replan_event_id": eid})
            for (eid, plan_id) in state.replan_triggered
            if eid not in state.redecomposed
        ]


class DeadLetterReconciler:
    """耗尽分诊: 每个非终态 entry 按 K6 异常分类法 → 规定动作 (不静默丢; transient→redispatch/
    exhausted→triage/waiting→escalate/unknown→wait 保守)。复用 exception_taxonomy.ACTION。"""

    name = "dead_letter"

    def reconcile(self, state: ObservedState) -> list[ReconcileAction]:
        out: list[ReconcileAction] = []
        for entry_id, fclass in state.dead_letter_pending:
            kind = ACTION.get(fclass, "wait")  # fail-safe: 不认的类 → 保守 wait, 绝不静默丢
            out.append(ReconcileAction(kind, entry_id, f"dead-letter 分诊: {fclass.value}"))
        return out


class EscalationReconciler:
    """owner 监督命根的 level 兜底: raised 但既没 owner answer 又没任何 main-inbound surface 的
    escalation → 补一条 main-inbound surface (再让 owner 看见)。

    治的病 (f-sub-deadletter-escalate-dead-circuit / rc-two-escalation-worlds): escalation 靠
    edge-triggered _route_event 一次性 surface, 漏一个 (watermark 已 march 过 / 崩在 route 前) 就
    静默丢 = owner 永远看不到。level-triggered: 反复对比 desired(每条 escalation 已 answered/aged
    或已 surface) vs observed(NJ answer + 已 surface), 漏的下一轮补 surface。

    幂等不刷屏: observed 已含"已 surface 过的" (build_observed_state 据真 main-inbound dispatch 算),
    故已 surface 的不再进 escalations_unsurfaced → 同 state 反复跑只补 surface 一次 (与 ReplanReconciler
    "observed 必含已派但未完成"同一幂等纪律)。"""

    name = "escalation"

    def reconcile(self, state: ObservedState) -> list[ReconcileAction]:
        return [
            ReconcileAction(
                "escalation_surface", esc_id, "raised escalation 未 answer 且未 surface 到 owner",
            )
            for esc_id in state.escalations_unsurfaced
        ]


class ForwardChainReconciler:
    """finding fnd-forward-chain-silent-skip-head-published-brief 的 level 兜底: forward-chain
    trigger 事件命中 FORWARD_CHAIN_REGISTRY 边却从未产出可查的 DispatchDecision (水位线快进/daemon
    重启窗口越过) → 补一条前进链派发 (routable) 或落死信等分诊 (not routable), 不再无声丢活。
    execution 目标已有独立于水位线的 ready-set backlog re-scan 自愈, 不在此覆盖 (build_observed_state
    已滤掉)。"""

    name = "forward_chain"

    def reconcile(self, state: ObservedState) -> list[ReconcileAction]:
        out: list[ReconcileAction] = []
        for gap in state.forward_chain_pending:
            if gap.routable:
                out.append(ReconcileAction(
                    "forward_chain_dispatch", gap.trigger_event_id,
                    f"forward-chain {gap.trigger_event_type}→{gap.dispatch_to} 从未产出 "
                    "DispatchDecision (静默丢活防治, 补写非 exec backlog marker)",
                    {
                        "trigger_event_type": gap.trigger_event_type,
                        "dispatch_to": gap.dispatch_to,
                        "review_mode": gap.review_mode,
                        "task_id": gap.task_id,
                    },
                ))
            else:
                out.append(ReconcileAction(
                    "forward_chain_deadletter", gap.trigger_event_id,
                    f"forward-chain {gap.trigger_event_type} 路由本身失败 "
                    f"({gap.route_error}) — 结构性, 落死信箱等分诊",
                    {"trigger_event_type": gap.trigger_event_type},
                ))
        return out


RECONCILERS: list[Reconciler] = [
    ReadySetReconciler(),
    ReplanReconciler(),
    DeadLetterReconciler(),
    EscalationReconciler(),
    ForwardChainReconciler(),
]


def reconcile_all(
    state: ObservedState, reconcilers: list[Reconciler] | None = None,
) -> list[ReconcileAction]:
    """跑所有 reconciler 汇总动作。orchestrator 拿去经 K5 原子认领+幂等戳落地 (重复扫到只认领一次)。"""
    rs = RECONCILERS if reconcilers is None else reconcilers
    return [a for r in rs for a in r.reconcile(state)]
