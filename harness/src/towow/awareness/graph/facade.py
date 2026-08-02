"""K1 · 图协议 facade (路 B) —— 只读映射现有图投影成统一 Node/Edge + 跨域 navigate。

实现 contracts.graph_protocol.GraphProtocol。concept_graph reducer 零改 (承重墙); facade 只读
其投影 JSON。这是 impact_graph(projection.py:1418-1501,已把 concept+task 边归一) 那套范式的扩展。

真实投影 shape (亲验):
- concept_graph: nodes[{concept_id,name,definition,...}] edges[{source_concept_id,target_concept_id,edge_type,direction,created_at_event_id}]
- task_graph:    nodes[{task_id,task_type,plan_id,description,parent_task_id,...}] edges[{source_task_id,target_task_id,dependency_type,is_active}]
- impact_graph:  edges[{source,target,kind,edge_type,edge_id}] (已统一形态)

路径可注入 (projection_dir) → hermetic 测试喂 fixture, 生产指 (隔离 worktree 或 live) .towow/graph。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts.graph_protocol import Edge, ExternalAnchor, GraphProtocol, NavStep, Node

# CIA 影响传播承重边 (cia_query DEFAULT_SLICE_EDGE_TYPES) —— 标 slice_bearing, 改它在喂 CIA。
SLICE_BEARING_EDGE_TYPES = frozenset({"reference", "dependency"})


def _is_external_anchor(target: str) -> bool:
    """悬空边合法第二形态判据: 指 repo 内 spec 路径 或 url (非图内节点 id 形态)。"""
    return target.startswith(("http://", "https://")) or "/" in target or target.endswith(".md")


class GraphFacade(GraphProtocol):
    """读 7 图投影 (本切片先接 concept/task/impact), 映射成统一节点/边, 支持跨域导航。"""

    def __init__(self, projection_dir: str | Path):
        self._dir = Path(projection_dir)
        self._nodes: dict[str, Node] = {}
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}
        self._load()

    def _read(self, name: str) -> dict:
        p = self._dir / f"{name}.json"
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _add_node(self, node: Node) -> None:
        self._nodes[node.id] = node
        self._out.setdefault(node.id, [])
        self._in.setdefault(node.id, [])

    def _add_edge(self, e: Edge) -> None:
        self._out.setdefault(e.from_id, []).append(e)
        to_id = e.to.ref if isinstance(e.to, ExternalAnchor) else e.to
        self._in.setdefault(to_id, []).append(e)

    def _load(self) -> None:
        # --- concept_graph (承重图, 只读) ---
        cg = self._read("concept_graph")
        for n in cg.get("nodes", []):
            cid = n.get("concept_id")
            if cid:
                self._add_node(Node(id=cid, type="concept", payload=n,
                                    provenance=n.get("created_at_event_id"), layer="concept"))
        # --- task_graph ---
        tg = self._read("task_graph")
        for n in tg.get("nodes", []):
            tid = n.get("task_id")
            if tid:
                self._add_node(Node(id=tid, type="task", payload=n,
                                    provenance=n.get("created_at_event_id"), layer="task"))
        # edges 在节点都加完后再加 (才能判 to 是否图内节点 / external_anchor)
        for e in cg.get("edges", []):
            src, tgt = e.get("source_concept_id"), e.get("target_concept_id")
            if not src or not tgt:
                continue
            et = e.get("edge_type", "reference")
            to: str | ExternalAnchor = (
                ExternalAnchor(ref=tgt, validated=True)
                if tgt not in self._nodes and _is_external_anchor(tgt) else tgt
            )
            self._add_edge(Edge(from_id=src, to=to, edge_type=et,
                                direction=e.get("direction", "unidirectional"),
                                is_active=e.get("is_active", True),
                                slice_bearing=et in SLICE_BEARING_EDGE_TYPES,
                                provenance=e.get("created_at_event_id")))
        for e in tg.get("edges", []):
            src, tgt = e.get("source_task_id"), e.get("target_task_id")
            if not src or not tgt:
                continue
            dt = e.get("dependency_type", "dependency")
            self._add_edge(Edge(from_id=src, to=tgt, edge_type=dt,
                                is_active=e.get("is_active", True),
                                slice_bearing=dt in SLICE_BEARING_EDGE_TYPES))

        # --- 三类新节点 (session/lock/escalation) 原生统一 shape, 不经 legacy 映射 ---
        # 设计 TG.4: 新节点原生用统一模型建, 经 canonical 事件 (SessionSpawned/LockAcquired/
        # EscalationRaised...) 物化成统一-shape 投影; facade 直接读、并进同一索引。
        # 这体现"新图=新 layer+新 type, 不改核心"(开放类型)——未来再加新图也走 _load_native。
        for name in ("session_graph", "lock_graph", "escalation_graph"):
            self._load_native(name)

    def _load_native(self, name: str) -> None:
        """读统一-shape 投影 {nodes:[{id,type,layer,payload,provenance,is_active}],
        edges:[{from_id,to,edge_type,direction,slice_bearing,provenance,is_active}]}, 并进索引。"""
        g = self._read(name)
        for n in g.get("nodes", []):
            nid = n.get("id")
            if nid:
                self._add_node(Node(id=nid, type=n.get("type", "unknown"),
                                    payload=n.get("payload", {}), provenance=n.get("provenance"),
                                    layer=n.get("layer", name), is_active=n.get("is_active", True)))
        for e in g.get("edges", []):
            src, tgt = e.get("from_id"), e.get("to")
            if not src or not tgt:
                continue
            to: str | ExternalAnchor = (
                ExternalAnchor(ref=tgt, validated=True)
                if tgt not in self._nodes and _is_external_anchor(tgt) else tgt
            )
            self._add_edge(Edge(from_id=src, to=to, edge_type=e.get("edge_type", "reference"),
                                direction=e.get("direction", "unidirectional"),
                                is_active=e.get("is_active", True),
                                slice_bearing=e.get("slice_bearing",
                                                    e.get("edge_type") in SLICE_BEARING_EDGE_TYPES),
                                provenance=e.get("provenance")))

    # ---- GraphProtocol 接口 ----
    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def edges_of(self, node_id: str, *, inbound: bool = False) -> list[Edge]:
        return list((self._in if inbound else self._out).get(node_id, []))

    def navigate(self, start_id: str, path: list[NavStep]) -> list[Node]:
        frontier = {start_id}
        for step in path:
            nxt: set[str] = set()
            for nid in frontier:
                for e in self.edges_of(nid, inbound=step.inbound):
                    if not e.is_active or e.edge_type != step.edge_type:
                        continue
                    other = e.from_id if step.inbound else (
                        e.to.ref if isinstance(e.to, ExternalAnchor) else e.to)
                    nxt.add(other)
            frontier = nxt
        return [self._nodes[n] for n in frontier if n in self._nodes]

    def integrity_violations(self) -> list[Edge]:
        bad: list[Edge] = []
        for edges in self._out.values():
            for e in edges:
                if isinstance(e.to, ExternalAnchor):
                    continue  # 合法外部锚点
                if e.to not in self._nodes:
                    bad.append(e)  # 指向非图内节点且非合法外部 = 完整性破
        return bad
