"""K2b-logic · 三类新节点的事件→统一-shape投影 reducer (纯函数, 层①/图协议)。

设计依据: 50-graph-protocol.md §TG.4 新三类节点 / rc-node-type-session-lineage 等 / 血缘真兑现。

## 本质
新节点 (session/lock/escalation) 经 canonical 事件物化成**统一-shape投影** (facade._load_native 直接读)。
本模块是纯 reducer (events→projection dicts), commit-safe; 深核 REGISTRATION (事件入 enums.py +
本 reducer 接入 projection.py + spawn 收口 emit SessionSpawned) 是后续集成步。

## 血缘真兑现 (修 parent ~0%)
SessionSpawned 带 parent_session_id → 直接产一条 spawned 边 (parent→child)。血缘做成**独立事件**
(spawn 不 emit 它就没 session 节点 = 结构性强制), 不是给现有事件补可选字段 (那正是 parent 0% 的根)。

## 事件 (统一最小 schema, 实际入 enums 时各自 Pydantic):
- SessionSpawned   {session_id, parent_session_id?, form, event_id}
- LockAcquired     {lock_id, session_id, resource, event_id}
- LockReleased     {lock_id, event_id}
- EscalationRaised {escalation_id, raised_by_session, blocks_task?, event_id}
- EscalationResolved {escalation_id, event_id}
"""

from __future__ import annotations

from typing import Any


def _node(nid: str, ntype: str, layer: str, payload: dict, ev: str | None) -> dict:
    return {"id": nid, "type": ntype, "layer": layer, "payload": payload,
            "provenance": ev, "is_active": True}


def _edge(frm: str, to: str, etype: str, ev: str | None, *, direction: str = "unidirectional") -> dict:
    return {"from_id": frm, "to": to, "edge_type": etype, "direction": direction,
            "is_active": True, "slice_bearing": False, "provenance": ev}


def reduce_node_events(events: list[dict[str, Any]]) -> dict[str, dict]:
    """折叠节点事件 → {session_graph, lock_graph, escalation_graph} 统一-shape 投影。

    幂等于事件序 (level-triggered 友好); 失活/解决用 is_active 标, 不删边 (可重建历史)。
    """
    sg: dict[str, dict] = {"nodes": {}, "edges": []}
    lg: dict[str, dict] = {"nodes": {}, "edges": []}
    eg: dict[str, dict] = {"nodes": {}, "edges": []}

    for e in events:
        t = e.get("type")
        ev = e.get("event_id")
        if t == "SessionSpawned":
            sid = e["session_id"]
            sg["nodes"][sid] = _node(sid, "session", "session_graph",
                                     {"form": e.get("form")}, ev)
            parent = e.get("parent_session_id")
            if parent:
                # 血缘真兑现: parent --spawned--> child (确保 parent 节点至少占位)
                sg["nodes"].setdefault(parent, _node(parent, "session", "session_graph", {}, None))
                sg["edges"].append(_edge(parent, sid, "spawned", ev))
        elif t == "LockAcquired":
            lid, sid = e["lock_id"], e["session_id"]
            lg["nodes"][lid] = _node(lid, "lock", "lock_graph",
                                     {"resource": e.get("resource")}, ev)
            lg["edges"].append(_edge(lid, sid, "held-by", ev))  # 锁 --held-by--> 会话
        elif t == "LockReleased":
            # 防御: LockReleased 是既有事件(Patch M-0.5a), payload 现无 lock_id → .get 跳过不崩。
            # 完整闭合(给 LockReleasedPayload 加 lock_id 让 held-by 真失活)动共享 schema, 留后续。
            lid = e.get("lock_id")
            if lid:
                for edge in lg["edges"]:
                    if edge["from_id"] == lid and edge["edge_type"] == "held-by":
                        edge["is_active"] = False  # 失活, 不删 (可重建历史)
        elif t == "EscalationRaised":
            eid = e["escalation_id"]
            eg["nodes"][eid] = _node(eid, "escalation", "escalation_graph",
                                     {"lifecycle": "raised"}, ev)
            if e.get("raised_by_session"):
                eg["edges"].append(_edge(eid, e["raised_by_session"], "raised-by", ev))
            if e.get("blocks_task"):
                eg["edges"].append(_edge(eid, e["blocks_task"], "blocks", ev))
        elif t == "EscalationResolved":
            eid = e["escalation_id"]
            if eid in eg["nodes"]:
                eg["nodes"][eid]["payload"]["lifecycle"] = "resolved"

    # nodes dict → list (facade 读 list)
    return {
        "session_graph": {"nodes": list(sg["nodes"].values()), "edges": sg["edges"]},
        "lock_graph": {"nodes": list(lg["nodes"].values()), "edges": lg["edges"]},
        "escalation_graph": {"nodes": list(eg["nodes"].values()), "edges": eg["edges"]},
    }
