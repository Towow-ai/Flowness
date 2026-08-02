"""K3 · investigate 工具本体 (层①, 骑 K1/K2 图协议 facade)。

实现 contracts.investigate.Investigate。直接服务北极星#1 "investigate 跨域查通": 一个 session
派生哪些子、占哪些锁、提了哪个 escalation、碰了哪些概念 —— 全从统一图一跳拿。

接口先行 (§C5): vitality 态由**注入**的 state_fn 提供 (生产 = per-form adapter.detect_state;
测试 = stub), investigate 不耦合具体判活实现 —— K4 判活内核换实现不动 investigate。
只读、不唤醒、不执行动作 (诊断 ≠ 执行, 审计独立性); 只输出 recommended_action + rationale。
"""

from __future__ import annotations

from collections.abc import Callable

from .contracts.graph_protocol import GraphProtocol, NavStep
from .contracts.investigate import Investigate, InvestigateResult
from .contracts.per_form_adapter import AgentState, Lineage, Vitality

# 态 → 建议动作 (per-form_adapter 契约: parked→复活 / dead→重派 / stuck→escalate / done→收割).
_ACTION = {
    Vitality.PARKED: "revive",
    Vitality.DEAD: "redispatch",
    Vitality.STUCK: "escalate",
    Vitality.DONE: "harvest",
    Vitality.RUNAWAY: "circuit_break",
    Vitality.WORKING: "wait",
    Vitality.LAUNCHING: "wait",
    Vitality.UNKNOWN: "wait",  # 判不准 → 保守等下一轮, 绝不收割
}


class DefaultInvestigate(Investigate):
    """在 GraphProtocol 上组合 investigate 的固定跨域查询 + 注入式判活。"""

    def __init__(self, graph: GraphProtocol, state_fn: Callable[[str], AgentState]):
        self._g = graph
        self._state_fn = state_fn

    def _ids(self, start: str, edge_type: str, *, inbound: bool) -> tuple[str, ...]:
        return tuple(sorted(n.id for n in self._g.navigate(start, [NavStep(edge_type, inbound=inbound)])))

    def _lineage(self, agent_id: str) -> Lineage:
        parents = self._ids(agent_id, "spawned", inbound=True)   # 谁 spawned 我
        children = self._ids(agent_id, "spawned", inbound=False)  # 我 spawned 谁
        siblings = self._ids(agent_id, "sibling", inbound=False)
        # 无显式 sibling 边时, 由父的其它子推导 (排自己)
        if not siblings and parents:
            sib = {s for p in parents for s in self._ids(p, "spawned", inbound=False)} - {agent_id}
            siblings = tuple(sorted(sib))
        return Lineage(parent=parents[0] if parents else None, children=children, siblings=siblings)

    def investigate(self, agent_id: str) -> InvestigateResult:
        st = self._state_fn(agent_id)  # 注入的判活 (只读)
        holds = self._ids(agent_id, "held-by", inbound=True)        # 占的锁 (锁 --held-by--> 会话)
        escalations = self._ids(agent_id, "raised-by", inbound=True)  # 提的 escalation
        touched = self._ids(agent_id, "touched", inbound=False)       # 碰的概念 (若有 touched 边)
        action = _ACTION.get(st.verdict, "wait")
        # parked 持锁: 复活前的强活信号, rationale 点明 (per-form 契约)
        rationale = f"verdict={st.verdict.value} ({st.reason}); holds_locks={len(holds)}"
        return InvestigateResult(
            agent_id=agent_id,
            state=st.verdict,
            last_signal=st.reason,
            work_product_so_far={"touched_concepts": list(touched)},
            lineage=self._lineage(agent_id),
            holds_locks=holds,
            raised_escalations=escalations,
            recommended_action=action,
            rationale=rationale,
        )
