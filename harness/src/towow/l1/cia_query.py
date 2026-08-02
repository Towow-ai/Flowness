"""M-2.1 §4 — CIAQueryService: 变更影响分析 (CIA) 查询库, 5 类沿边查询.

# spec source: 05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#   §4.1 5 类 sliced 工作 (消费方发现 / 依赖前提核查 / 契约保持 / 存量数据兼容 / 状态机合法性)
#   §4.2 查询接口 (CIAQueryService API + SliceResult schema, L286-302)
#   §4.4 关键 invariant (同步返回 / 不写 event / 含 projection_watermark / max_depth ≤ 5)
# CLI: `towow concept slice` (forward/backward), `towow cascade trace`.

§4.1 决策: 复用 M-0.2 impact_graph projection ——"M-2.1 不重新发明遍历——基于 impact_graph
包装查询接口"。impact_graph 已从 concept_graph.edges + task_graph.edges 派生统一有向边集
(source→target, 含 kind / edge_type / edge_id), is_active=false 概念边在派生时已排除 (M-0.2
§3.7)。本层在该 projection 上做带 edge_type 过滤 + path 记录的 BFS, 富化成 §4.2 的 SliceResult。

§4.1 表把 5 类的边源都写成 "concept_graph.edges where edge_type ∈ {...}" —— 即概念关系边,
故本层只沿 impact_graph 里 kind=="concept" 的边走 (task 依赖边不混入 CIA 概念切片; 概念边
edge_type ∈ M-0.2 §3.1 6 类与 task dependency_type 取值不同, kind 过滤再加一道保险)。

§4.4 invariant 物理保证:
  - 同步返回: 纯函数读 projection, 无 async / future。
  - 不写 event: 本模块只 read, 从不 emit (CIA 查询粒度太细, 不进事件日志)。
  - 含 projection_watermark: 每个结果带查询时 impact_graph 的 watermark (= store.cursor-1),
    下游 caller 可 verify 用的是不是最新版本的 projection。
  - max_depth ≤ 5: 所有切片在传入前 clamp 到 [0, 5], 防图爆炸 (§4.4 / §11 简化点 2)。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from towow.l0.projection import ProjectionStore

# §4.4 max_depth 上限 5 (§11 简化点 2 — 防止图爆炸)。
MAX_DEPTH_CAP = 5

# concept-version 约定 base@vN (f-capability-registry-discovery-lists-superseded fix — 解析当前版本)。
_VERSION_RE = re.compile(r"^(.*)@v(\d+)$")

# §4.2 各 slice 的默认沿走 edge_type (§4.1 表)。
DEFAULT_SLICE_EDGE_TYPES = ("dependency", "reference")
CONTRACT_EDGE_TYPE = "contract"
DATA_SCHEMA_EDGE_TYPE = "data_schema"


@dataclass(frozen=True)
class EntityRef:
    """M-2.1 §4.2 EntityRef — 影响图节点 (concept / task)。"""

    entity_type: str
    entity_id: str

    def as_dict(self) -> dict[str, str]:
        return {"entity_type": self.entity_type, "entity_id": self.entity_id}


@dataclass(frozen=True)
class SliceEntityResult:
    """M-2.1 §4.2 SliceResult.results[] — 一个受影响 entity + 沿哪条路径走到它。

    is_superseded / current_version_id (f-capability-registry-discovery-lists-superseded fix):
    发现面消费方（尤其能力注册表 backward reference slice — 陌生 agent 发现在册能力当前标准的入口）
    据此机械分辨一个被达概念是不是已被 supersede 的旧版本、当前版本是谁。这是**标注不是过滤**——
    slice 仍返回全部有活边的节点（impact 分析要看到指向 superseded 概念的 stale 边, 见 finding
    ripple），只是把已存在于 concept_graph.supersede_index 的 superseded 事实带到消费点, 让发现面
    不必靠"每次 supersede 后有人记得撤旧 reference 边"才不误导。默认 False/None — 未 supersede 或
    非版本化概念不受影响。
    """

    entity: EntityRef
    depth: int
    path: list[str]  # 沿哪些 edge_id 走到的 (从 start 起首条到达路径)
    edge_types_traversed: list[str]
    is_superseded: bool = False
    current_version_id: str | None = None  # 若能按 base@vN 约定解析出当前(未 supersede)版本

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity.as_dict(),
            "depth": self.depth,
            "path": list(self.path),
            "edge_types_traversed": list(self.edge_types_traversed),
            "is_superseded": self.is_superseded,
            "current_version_id": self.current_version_id,
        }


@dataclass(frozen=True)
class SliceQuery:
    """M-2.1 §4.2 SliceResult.query — 这次切片查询的参数 (reproducibility)。"""

    start_entity: EntityRef
    direction: str  # forward | backward | bidirectional
    max_depth: int
    edge_types: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_entity": self.start_entity.as_dict(),
            "direction": self.direction,
            "max_depth": self.max_depth,
            "edge_types": list(self.edge_types),
        }


@dataclass(frozen=True)
class SliceResult:
    """M-2.1 §4.2 SliceResult — forward_slice / backward_slice 返回。

    query_timestamp 省略 (spec 列出但 v3 不依赖 wall-clock 做 reproducibility — projection_watermark
    才是可复现锚点, §4.4)。
    """

    query: SliceQuery
    results: list[SliceEntityResult] = field(default_factory=list)
    projection_watermark: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.as_dict(),
            "results": [r.as_dict() for r in self.results],
            "projection_watermark": self.projection_watermark,
        }


@dataclass(frozen=True)
class ContractRelationResult:
    """M-2.1 §4.1 契约保持 — entity 涉及的契约关系 (沿 contract 边 bidirectional)。"""

    entity: EntityRef
    contract_partners: list[SliceEntityResult] = field(default_factory=list)
    projection_watermark: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity.as_dict(),
            "contract_partners": [p.as_dict() for p in self.contract_partners],
            "projection_watermark": self.projection_watermark,
        }


@dataclass(frozen=True)
class DataSchemaCompatibilityResult:
    """M-2.1 §4.1 存量数据兼容 — schema 演化路径上受影响 entity (沿 data_schema 边 forward)。"""

    entity: EntityRef
    target_schema_version: str
    affected_entities: list[SliceEntityResult] = field(default_factory=list)
    projection_watermark: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity.as_dict(),
            "target_schema_version": self.target_schema_version,
            "affected_entities": [e.as_dict() for e in self.affected_entities],
            "projection_watermark": self.projection_watermark,
        }


@dataclass(frozen=True)
class StateMachineValidityResult:
    """M-2.1 §4.1 状态机合法性 — proposed_state 是不是从当前 saga_state 的合法转移。"""

    entity: EntityRef
    proposed_state: str
    current_state: str | None
    is_valid_transition: bool
    allowed_transitions: list[str]
    reason: str
    projection_watermark: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity.as_dict(),
            "proposed_state": self.proposed_state,
            "current_state": self.current_state,
            "is_valid_transition": self.is_valid_transition,
            "allowed_transitions": list(self.allowed_transitions),
            "reason": self.reason,
            "projection_watermark": self.projection_watermark,
        }


def _coerce_entity(entity: str | EntityRef, default_type: str = "concept") -> EntityRef:
    if isinstance(entity, EntityRef):
        return entity
    return EntityRef(entity_type=default_type, entity_id=entity)


def _clamp_depth(depth: int) -> int:
    """§4.4 — max_depth clamp 到 [0, 5]。"""
    return max(0, min(MAX_DEPTH_CAP, depth))


class CIAQueryService:
    """M-2.1 §4.2 CIA 查询库。构造时拿 ProjectionStore — 每次 query 重读最新物化 projection。

    查询层不自己推进 cursor (catchup 由调用方在查询前做); 只读 impact_graph (+ state_machine_check
    读 concept_graph.nodes)。§4.4: 不写 event。
    """

    def __init__(self, store: ProjectionStore) -> None:
        self._store = store

    # ── 内部: 读 projection + watermark ────────────────────────────────────────

    def _impact_edges(self) -> list[dict[str, Any]]:
        ig = self._store.read("impact_graph") or {}
        raw = ig.get("edges")
        return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []

    def _concept_nodes(self) -> list[dict[str, Any]]:
        cg = self._store.read("concept_graph") or {}
        raw = cg.get("nodes")
        return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []

    def _superseded_ids(self) -> set[str]:
        """concept_ids that have been superseded — machine truth from concept_graph.supersede_index
        (a concept_id with a non-empty supersede chain is superseded). Read once per slice.

        f-capability-registry-discovery-lists-superseded fix: this is the already-materialized
        superseded fact the discovery-consumption point was not reading. Same signal that
        get_concept_current_state().is_superseded exposes per-concept — surfaced into slice results
        so the discovery面 annotates旧版本 without depending on the old reference edge having been
        manually removed.
        """
        cg = self._store.read("concept_graph") or {}
        si = cg.get("supersede_index")
        if not isinstance(si, dict):
            return set()
        return {cid for cid, chain in si.items() if isinstance(cid, str) and chain}

    def _current_version_of(
        self,
        concept_id: str,
        superseded_ids: set[str],
        all_concept_ids: set[str],
    ) -> str | None:
        """Best-effort: for a superseded ``base@vN``, the current (non-superseded) ``base@vM`` with
        the highest M present in the concept graph. None if concept_id isn't ``base@vN`` shaped or no
        non-superseded sibling exists. Uses the documented concept-version convention only — no guess.
        """
        m = _VERSION_RE.match(concept_id)
        if m is None:
            return None
        base = m.group(1)
        best: str | None = None
        best_n = -1
        for cid in all_concept_ids:
            mm = _VERSION_RE.match(cid)
            if mm is None or mm.group(1) != base or cid in superseded_ids:
                continue
            n = int(mm.group(2))
            if n > best_n:
                best_n, best = n, cid
        return best

    def _watermark(self) -> int:
        """§4.4 — 查询时 impact_graph 的 watermark (= 已物化到的最高 seq)。"""
        return max(0, self._store.cursor - 1)

    # ── 核心: 带 edge_type 过滤 + path 记录的 BFS (沿 concept 边) ────────────────

    def _filtered_adjacency(
        self,
        edge_types: set[str],
        *,
        reverse: bool,
    ) -> dict[str, list[tuple[str, str, str, str]]]:
        """从 impact_graph 概念边建邻接表, 只收 edge_type ∈ edge_types 的 kind=="concept" 边。

        每条邻接项 = (邻居 id, 邻居 entity_type, edge_id, edge_type)。
        reverse=False: forward (source→target); reverse=True: backward (target→source)。
        """
        adj: dict[str, list[tuple[str, str, str, str]]] = {}
        for e in self._impact_edges():
            if e.get("kind") != "concept":
                continue
            etype = e.get("edge_type")
            if etype not in edge_types:
                continue
            src = e.get("source")
            tgt = e.get("target")
            if not (isinstance(src, str) and isinstance(tgt, str) and src and tgt):
                continue
            edge_id = e.get("edge_id")
            edge_id = edge_id if isinstance(edge_id, str) and edge_id else f"{src}->{tgt}:{etype}"
            etype_str = str(etype)
            if reverse:
                adj.setdefault(tgt, []).append((src, "concept", edge_id, etype_str))
            else:
                adj.setdefault(src, []).append((tgt, "concept", edge_id, etype_str))
        return adj

    def _bfs(
        self,
        start: str,
        adjacencies: list[dict[str, list[tuple[str, str, str, str]]]],
        depth: int,
    ) -> list[SliceEntityResult]:
        """BFS 沿一个或多个邻接表 (bidirectional = 两个) 走 depth 跳, 记录首达路径。

        start 本身不计入结果; 未知 start → 空 (不抛)。同一节点首次到达即定 depth + path (BFS
        保证最短)。

        f-capability-registry-discovery-lists-superseded fix: 每个结果标注 is_superseded /
        current_version_id (读一次 supersede_index)。标注不过滤 — 返回集不变 (impact 消费方仍见指向
        superseded 概念的 stale 边), 只把已物化的 superseded 事实带到发现消费点。
        """
        superseded_ids = self._superseded_ids()
        all_concept_ids = {
            cid for n in self._concept_nodes() if isinstance(cid := n.get("concept_id"), str)
        }
        visited: set[str] = {start}
        results: list[SliceEntityResult] = []
        # queue 项: (node, depth, path[edge_id], edge_types[])
        queue: deque[tuple[str, int, list[str], list[str]]] = deque([(start, 0, [], [])])
        while queue:
            node, d, path, etypes = queue.popleft()
            if d >= depth:
                continue
            for adj in adjacencies:
                for nbr, nbr_type, edge_id, etype in adj.get(node, []):
                    if nbr in visited:
                        continue
                    visited.add(nbr)
                    new_path = [*path, edge_id]
                    new_etypes = [*etypes, etype]
                    nbr_superseded = nbr in superseded_ids
                    results.append(
                        SliceEntityResult(
                            entity=EntityRef(entity_type=nbr_type, entity_id=nbr),
                            depth=d + 1,
                            path=new_path,
                            edge_types_traversed=new_etypes,
                            is_superseded=nbr_superseded,
                            current_version_id=(
                                self._current_version_of(nbr, superseded_ids, all_concept_ids)
                                if nbr_superseded
                                else None
                            ),
                        ),
                    )
                    queue.append((nbr, d + 1, new_path, new_etypes))
        return results

    # ── §4.1 / §4.2 — 5 类 sliced 工作 ──────────────────────────────────────────

    def forward_slice(
        self,
        start_entity: str | EntityRef,
        max_depth: int = 5,
        edge_types: list[str] | None = None,
    ) -> SliceResult:
        """§4.1 消费方发现 (O-11 主条) — 改了 start_entity, 沿出边 (dependency/reference) 走,
        收集下游受影响 entity。
        """
        ref = _coerce_entity(start_entity)
        etypes = list(edge_types) if edge_types is not None else list(DEFAULT_SLICE_EDGE_TYPES)
        depth = _clamp_depth(max_depth)
        adj = self._filtered_adjacency(set(etypes), reverse=False)
        results = self._bfs(ref.entity_id, [adj], depth)
        return SliceResult(
            query=SliceQuery(start_entity=ref, direction="forward", max_depth=depth, edge_types=etypes),
            results=results,
            projection_watermark=self._watermark(),
        )

    def backward_slice(
        self,
        start_entity: str | EntityRef,
        max_depth: int = 5,
        edge_types: list[str] | None = None,
    ) -> SliceResult:
        """§4.1 依赖前提核查 — start_entity 依赖谁, 沿入边走, 收集上游前提 entity。"""
        ref = _coerce_entity(start_entity)
        etypes = list(edge_types) if edge_types is not None else list(DEFAULT_SLICE_EDGE_TYPES)
        depth = _clamp_depth(max_depth)
        adj = self._filtered_adjacency(set(etypes), reverse=True)
        results = self._bfs(ref.entity_id, [adj], depth)
        return SliceResult(
            query=SliceQuery(start_entity=ref, direction="backward", max_depth=depth, edge_types=etypes),
            results=results,
            projection_watermark=self._watermark(),
        )

    def contract_check(self, entity: str | EntityRef, max_depth: int = 5) -> ContractRelationResult:
        """§4.1 契约保持 — entity 涉及哪些契约关系 (只沿 contract 边, bidirectional)。

        只过滤 edge_type==contract → 结果绝不混入 dependency / reference 边。
        """
        ref = _coerce_entity(entity)
        depth = _clamp_depth(max_depth)
        fwd = self._filtered_adjacency({CONTRACT_EDGE_TYPE}, reverse=False)
        bwd = self._filtered_adjacency({CONTRACT_EDGE_TYPE}, reverse=True)
        partners = self._bfs(ref.entity_id, [fwd, bwd], depth)
        return ContractRelationResult(
            entity=ref,
            contract_partners=partners,
            projection_watermark=self._watermark(),
        )

    def data_schema_check(
        self,
        entity: str | EntityRef,
        target_schema_version: str,
        max_depth: int = 5,
    ) -> DataSchemaCompatibilityResult:
        """§4.1 存量数据兼容 — schema 演化路径上受影响 entity (沿 data_schema 边 forward)。"""
        ref = _coerce_entity(entity)
        depth = _clamp_depth(max_depth)
        adj = self._filtered_adjacency({DATA_SCHEMA_EDGE_TYPE}, reverse=False)
        affected = self._bfs(ref.entity_id, [adj], depth)
        return DataSchemaCompatibilityResult(
            entity=ref,
            target_schema_version=target_schema_version,
            affected_entities=affected,
            projection_watermark=self._watermark(),
        )

    def state_machine_check(
        self,
        entity: str | EntityRef,
        proposed_state: str,
    ) -> StateMachineValidityResult:
        """§4.1 状态机合法性 — proposed_state 是不是从当前 saga_state 的合法转移。

        读 concept_graph.nodes[].state_machine (T-L1-10 物化的校验后状态机) + saga_state 当前态。
        合法转移 = 存在 transition(from_state==current_state, to_state==proposed_state)。
        无 state_machine / 概念不存在 → is_valid_transition=False + reason 说明 (保守, 不抛)。
        """
        ref = _coerce_entity(entity)
        wm = self._watermark()
        node = next((n for n in self._concept_nodes() if n.get("concept_id") == ref.entity_id), None)
        if node is None:
            return StateMachineValidityResult(
                entity=ref,
                proposed_state=proposed_state,
                current_state=None,
                is_valid_transition=False,
                allowed_transitions=[],
                reason=f"concept {ref.entity_id!r} 不在 concept_graph",
                projection_watermark=wm,
            )
        sm = node.get("state_machine")
        current_state = node.get("saga_state")
        current_state = current_state if isinstance(current_state, str) else None
        if not isinstance(sm, dict):
            return StateMachineValidityResult(
                entity=ref,
                proposed_state=proposed_state,
                current_state=current_state,
                is_valid_transition=False,
                allowed_transitions=[],
                reason=f"concept {ref.entity_id!r} 无 state_machine 定义, 无法判定转移合法性",
                projection_watermark=wm,
            )
        transitions_raw = sm.get("transitions")
        transitions = (
            [t for t in transitions_raw if isinstance(t, dict)] if isinstance(transitions_raw, list) else []
        )
        # 当前态: saga_state; 缺失时回落到状态机 initial state (is_initial=True)。
        if current_state is None:
            states_raw = sm.get("states")
            states = [s for s in states_raw if isinstance(s, dict)] if isinstance(states_raw, list) else []
            initial = next((s for s in states if s.get("is_initial")), None)
            if isinstance(initial, dict) and isinstance(initial.get("name"), str):
                current_state = str(initial.get("name"))
        allowed = [
            str(t.get("to_state"))
            for t in transitions
            if t.get("from_state") == current_state and isinstance(t.get("to_state"), str)
        ]
        is_valid = proposed_state in allowed
        if current_state is None:
            reason = "无法确定当前 saga_state (无 saga_state 且状态机无 initial state)"
        elif is_valid:
            reason = f"合法转移: {current_state} → {proposed_state}"
        else:
            reason = f"非法转移: 从 {current_state} 无到 {proposed_state} 的转移 (合法目标: {allowed})"
        return StateMachineValidityResult(
            entity=ref,
            proposed_state=proposed_state,
            current_state=current_state,
            is_valid_transition=is_valid,
            allowed_transitions=allowed,
            reason=reason,
            projection_watermark=wm,
        )
