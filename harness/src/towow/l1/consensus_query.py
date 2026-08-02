"""M-1.2 §5.1 / §5.5 — ConsensusGraphQuery: 工程共识扫描脚本 / Query API 7 类 (T-L1-16).

# spec source: 03-l1-detailed-designs/M-1.2-engineering-consensus-skill-detailed-design.md
#   §5.1 必须支持的查询类型 (Query API 7 类)
#   §5.2 输出格式 (结构化 dict, 非 markdown)
#   §5.4 错误处理 (ScanScriptError: CONCEPT_NOT_FOUND / REFERENCE_NOT_FOUND ...)
#   §5.5 API 接口暴露 (towow consensus query/list-stale/neighborhood)
#   §4.6 失效检测 (detect_stale_references → Query 4)
# CLI: `towow consensus query/list-stale/neighborhood` (main.py consensus group)

工程共识能正确运作的物理基础 —— M-0.2 projection 之上的查询层 (§5 R2 做最完整)。本层
**只读** 四个 Patch T projection:
  concept_graph              (Query 1 / 5 / 7 概念状态 + 邻域 + 跨 plan)
  at_reference_graph         (Query 2 / 3 / 4 引用 / 是否最新 / 失效)
  consumer_relation          (Query 6 消费方清单; = M-1.2 spec 的 consumer_lists)
  engineering_consensus_index(Query 1 scope / Query 7 plan 归属)

概念→plan 归属来自 engineering_consensus_index.frozen_concept_ids (concept_graph node 自身
不带 plan_id)。@ 引用的 target_concept_name 复用 ConceptCreated.concept_name (§4.2 part 3),
故 list_referers 按 concept 的 name 匹配 typed @ 引用, 同时兼容 T-L0-08 endpoint
(target.entity_id == concept_id) 旧 shape。

§5.2: 所有 query 返回结构化 dict / list[dict], 非 markdown (markdown 是 M-3.1 export 层)。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from towow.l0.projection import ProjectionStore


class ScanScriptError(RuntimeError):
    """M-1.2 §5.4 扫描脚本错误。code 取 §5.4 enum 之一。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# §5.4 错误码 enum。
CONCEPT_NOT_FOUND = "CONCEPT_NOT_FOUND"
REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"


class StalenessSeverity(StrEnum):
    """§4.6 determine_severity — ref 未跟随 target supersede 的失效严重度。

    explicit_decision_required policy 的 stale ref 是 high (人必须决断); pin_to_snapshot
    是 low (有意 pin 旧态, 信息性); 其余 medium。
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _refs(at_reference_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(at_reference_graph, dict):
        return []
    raw = at_reference_graph.get("references")
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def _nodes(concept_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(concept_graph, dict):
        return []
    raw = concept_graph.get("nodes")
    return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []


def _edges(concept_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(concept_graph, dict):
        return []
    raw = concept_graph.get("edges")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


class ConsensusGraphQuery:
    """M-1.2 §5.1 Query API 7 类。构造时拿 ProjectionStore — 每次 query 重读 projection 文件

    (projection 由调用方在 catchup 后传入; query 层不自己推进 cursor — 读最新已物化态)。
    """

    def __init__(self, store: ProjectionStore) -> None:
        self._store = store

    # ── 内部: 读 projection (每次 query 重读, 反映最新物化态) ──────────────────

    def _concept_graph(self) -> dict[str, Any] | None:
        return self._store.read("concept_graph")

    def _at_reference_graph(self) -> dict[str, Any] | None:
        return self._store.read("at_reference_graph")

    def _consumer_relation(self) -> dict[str, Any] | None:
        return self._store.read("consumer_relation")

    def _consensus_index(self) -> dict[str, Any] | None:
        return self._store.read("engineering_consensus_index")

    # ── 内部: concept 当前 last_event_id + plan 归属 helpers ──────────────────

    def _find_node(self, concept_id: str) -> dict[str, Any] | None:
        for n in _nodes(self._concept_graph()):
            if n.get("concept_id") == concept_id:
                return n
        return None

    @staticmethod
    def _node_last_event_id(node: dict[str, Any], supersede_index: dict[str, Any]) -> str:
        """concept 当前 last_event_id: supersede_index 末项 (若被 supersede 过) 否则 created_at。

        is_reference_current (§5.1 Query 3) 比对 ref.locked_event_id 与此值。
        """
        cid = node.get("concept_id")
        chain = supersede_index.get(cid) if isinstance(cid, str) else None
        if isinstance(chain, list) and chain:
            last = chain[-1]
            if isinstance(last, str):
                return last
        created = node.get("created_at_event_id")
        return created if isinstance(created, str) else ""

    def _plans_for_concept(self, concept_id: str) -> list[str]:
        """concept 归属的 plan_id 列表 (engineering_consensus_index.frozen_concept_ids 含它)。"""
        out: list[str] = []
        index = self._consensus_index()
        plans_raw = index.get("plans") if isinstance(index, dict) else None
        if not isinstance(plans_raw, list):
            return out
        for p in plans_raw:
            if not isinstance(p, dict):
                continue
            plan_id = p.get("plan_id")
            if not isinstance(plan_id, str):
                continue
            batches = p.get("batches")
            if not isinstance(batches, list):
                continue
            for b in batches:
                if not isinstance(b, dict):
                    continue
                frozen = b.get("frozen_concept_ids")
                if isinstance(frozen, list) and concept_id in frozen and plan_id not in out:
                    out.append(plan_id)
        return out

    def _ref_targets_concept(self, ref: dict[str, Any], node: dict[str, Any]) -> bool:
        """ref 是否引用 node 这个 concept (两 shape: T-L1-14 target_concept_name == node.name;

        T-L0-08 target.entity_id == concept_id)。
        """
        concept_id = node.get("concept_id")
        name = node.get("name")
        # T-L1-14 typed @ locator shape
        tcn = ref.get("target_concept_name")
        if isinstance(tcn, str) and tcn and tcn == name:
            return True
        # T-L0-08 endpoint shape
        target = ref.get("target")
        return isinstance(target, dict) and target.get("entity_id") == concept_id

    # ── Query 1: 某概念当前状态 ──────────────────────────────────────────────

    def get_concept_current_state(self, concept_id: str) -> dict[str, Any]:
        """§5.1 Query 1 — concept 当前状态: 最新 node + active edges + active saga state +

        所属 plan_id。concept 不存在 → ScanScriptError(CONCEPT_NOT_FOUND)。
        """
        node = self._find_node(concept_id)
        if node is None:
            raise ScanScriptError(CONCEPT_NOT_FOUND, f"concept {concept_id!r} 不在 concept_graph")
        cg = self._concept_graph() or {}
        supersede_index = cg.get("supersede_index")
        supersede_index = supersede_index if isinstance(supersede_index, dict) else {}
        active_edges = [
            e
            for e in _edges(cg)
            if e.get("is_active", True)
            and (e.get("source_concept_id") == concept_id or e.get("target_concept_id") == concept_id)
        ]
        return {
            "concept_id": concept_id,
            "name": node.get("name", ""),
            "definition": node.get("definition", ""),
            "saga_state": node.get("saga_state"),
            "state_machine": node.get("state_machine"),
            "last_event_id": self._node_last_event_id(node, supersede_index),
            "is_superseded": bool(
                isinstance(supersede_index.get(concept_id), list) and supersede_index.get(concept_id),
            ),
            "active_edges": active_edges,
            "plan_ids": self._plans_for_concept(concept_id),
        }

    # ── Query 2: 谁引用了某概念 ──────────────────────────────────────────────

    def list_referers(self, concept_id: str, ref_type_filter: str | None = None) -> list[dict[str, Any]]:
        """§5.1 Query 2 — 所有引用了 concept_id 的 active AtReference。

        ref_type_filter: 按 locator_type / reference_type 过滤 (如只看 "concept")。
        concept 不存在 → CONCEPT_NOT_FOUND (引用必须指向存在的 concept 才可观测)。
        """
        node = self._find_node(concept_id)
        if node is None:
            raise ScanScriptError(CONCEPT_NOT_FOUND, f"concept {concept_id!r} 不在 concept_graph")
        out: list[dict[str, Any]] = []
        for ref in _refs(self._at_reference_graph()):
            if not ref.get("is_active", True):
                continue
            if not self._ref_targets_concept(ref, node):
                continue
            rtype = ref.get("locator_type") or ref.get("reference_type")
            if ref_type_filter is not None and rtype != ref_type_filter:
                continue
            out.append(ref)
        return out

    # ── Query 3: 某引用是否最新 ──────────────────────────────────────────────

    def is_reference_current(self, reference_id: str) -> bool:
        """§5.1 Query 3 — ref 是否锁在 target concept 的当前态。

        失效语义 (§4.6): "target 已 supersede 但 ref lock 还在旧 event"。判定:
        - 显式 is_stale=True (AtReferenceTargetSuperseded reducer 标 target supersede 后未跟随)
          → not current。
        - 否则: target concept 若被 supersede 过, ref 的 locked_event_id 必须 == target 当前
          last_event_id 才算 current; target 未被 supersede 过 (无 supersede 链) → current
          (lock 锚在创建态, target 没动 → 不失效, 即便 default lock 是 add-event 本身)。
        reference 不存在 → REFERENCE_NOT_FOUND。
        """
        ref = next(
            (r for r in _refs(self._at_reference_graph()) if r.get("reference_id") == reference_id),
            None,
        )
        if ref is None:
            raise ScanScriptError(REFERENCE_NOT_FOUND, f"reference {reference_id!r} 不存在")
        if ref.get("is_stale"):
            return False
        node = self._resolve_ref_target_node(ref)
        if node is None:
            # target concept 不在图 → 无法判定最新, 保守视为 not current。
            return False
        cg = self._concept_graph() or {}
        supersede_index = cg.get("supersede_index")
        supersede_index = supersede_index if isinstance(supersede_index, dict) else {}
        concept_id = node.get("concept_id")
        was_superseded = bool(
            isinstance(supersede_index.get(concept_id), list) and supersede_index.get(concept_id),
        )
        if not was_superseded:
            # target 自创建未变迁 → ref 不失效 (§4.6 失效前提是 target supersede)。
            return True
        current = self._node_last_event_id(node, supersede_index)
        locked = ref.get("locked_event_id")
        return bool(locked) and locked == current

    def _resolve_ref_target_node(self, ref: dict[str, Any]) -> dict[str, Any] | None:
        """把 ref 的 target 解析到 concept_graph node (T-L1-14 name / T-L0-08 entity_id 两 shape)。"""
        cg = self._concept_graph() or {}
        target_name = ref.get("target_concept_name")
        node = next((n for n in _nodes(cg) if n.get("name") == target_name), None)
        if node is None:
            target = ref.get("target")
            tid = target.get("entity_id") if isinstance(target, dict) else None
            node = next((n for n in _nodes(cg) if n.get("concept_id") == tid), None)
        return node

    # ── Query 4: 哪些引用因上游变迁需要重新校验 ──────────────────────────────

    def list_stale_references(self, severity_filter: str | None = None) -> list[dict[str, Any]]:
        """§5.1 Query 4 / §4.6 — 扫所有 active ref, 找 target 已 supersede 但 ref 未跟随的。

        失效信号 (§4.6 "target 已 supersede 但 ref lock 还在旧 event"):
        - 显式 is_stale=True (AtReferenceTargetSuperseded reducer 标 target supersede 未跟随), 或
        - target concept **被 supersede 过** (supersede_index 有链) 且 ref locked_event_id !=
          target 当前 last_event_id。
        target 自创建从未变迁的 ref 不算失效 (default lock 锚在创建态 ≠ 失效) — 避免误报。
        severity_filter 按 §4.6 severity 过滤。
        """
        cg = self._concept_graph() or {}
        supersede_index = cg.get("supersede_index")
        supersede_index = supersede_index if isinstance(supersede_index, dict) else {}
        out: list[dict[str, Any]] = []
        for ref in _refs(self._at_reference_graph()):
            if not ref.get("is_active", True):
                continue
            stale = bool(ref.get("is_stale"))
            current_target_event = None
            if not stale:
                node = self._resolve_ref_target_node(ref)
                if node is not None:
                    concept_id = node.get("concept_id")
                    was_superseded = bool(
                        isinstance(supersede_index.get(concept_id), list)
                        and supersede_index.get(concept_id),
                    )
                    if was_superseded:
                        current_target_event = self._node_last_event_id(node, supersede_index)
                        locked = ref.get("locked_event_id")
                        if locked and current_target_event and locked != current_target_event:
                            stale = True
            if not stale:
                continue
            severity = self._severity(ref)
            if severity_filter is not None and severity != severity_filter:
                continue
            out.append(
                {
                    "reference_id": ref.get("reference_id"),
                    "target_concept_name": ref.get("target_concept_name"),
                    "old_lock": ref.get("locked_event_id"),
                    "current_target_event": ref.get("target_superseded_at_event_id")
                    or current_target_event,
                    "severity": severity,
                    "policy": ref.get("on_target_supersede_policy"),
                    "staleness_status": ref.get("staleness_status"),
                },
            )
        return out

    @staticmethod
    def _severity(ref: dict[str, Any]) -> str:
        """§4.6 determine_severity — 按失效响应 policy 定严重度。"""
        policy = ref.get("on_target_supersede_policy")
        if policy == "explicit_decision_required":
            return StalenessSeverity.HIGH.value
        if policy == "pin_to_snapshot":
            return StalenessSeverity.LOW.value
        return StalenessSeverity.MEDIUM.value

    # ── Query 5: 概念图邻域查询 ──────────────────────────────────────────────

    def get_concept_neighborhood(
        self,
        concept_id: str,
        hops: int = 1,
        edge_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """§5.1 Query 5 — 从 concept_id 沿 active edge N-hop 扩展返回子图。

        用于 M-0.3 capsule pipeline §2.2 candidate 召回。concept 不存在 → CONCEPT_NOT_FOUND。
        """
        if self._find_node(concept_id) is None:
            raise ScanScriptError(CONCEPT_NOT_FOUND, f"concept {concept_id!r} 不在 concept_graph")
        cg = self._concept_graph() or {}
        all_edges = [e for e in _edges(cg) if e.get("is_active", True)]
        if edge_types is not None:
            type_set = set(edge_types)
            all_edges = [e for e in all_edges if e.get("edge_type") in type_set]

        reached: set[str] = {concept_id}
        sub_edges: list[dict[str, Any]] = []
        frontier = {concept_id}
        for _ in range(max(0, hops)):
            next_frontier: set[str] = set()
            for e in all_edges:
                src = e.get("source_concept_id")
                tgt = e.get("target_concept_id")
                if src in frontier or tgt in frontier:
                    if e not in sub_edges:
                        sub_edges.append(e)
                    for endpoint in (src, tgt):
                        if isinstance(endpoint, str) and endpoint not in reached:
                            reached.add(endpoint)
                            next_frontier.add(endpoint)
            if not next_frontier:
                break
            frontier = next_frontier

        nodes_by_id = {n.get("concept_id"): n for n in _nodes(cg)}
        sub_nodes = [nodes_by_id[c] for c in reached if c in nodes_by_id]
        return {
            "center_concept_id": concept_id,
            "hops": hops,
            "nodes": sub_nodes,
            "edges": sub_edges,
        }

    # ── Query 6: 消费方清单查询 ──────────────────────────────────────────────

    def get_consumers(self, concept_id: str, consumer_type_filter: str | None = None) -> list[dict[str, Any]]:
        """§5.1 Query 6 — concept 的消费方清单 (consumer_relation projection = spec consumer_lists)。

        consumer_type_filter: data_consumer / behavior_consumer / visibility_consumer 过滤。
        concept 无 consumer_relation 记录 → 返回 [] (尚未扫描, 非错误)。
        """
        rels_raw = (self._consumer_relation() or {}).get("relations")
        rels = [r for r in rels_raw if isinstance(r, dict)] if isinstance(rels_raw, list) else []
        node = next((r for r in rels if r.get("concept_id") == concept_id), None)
        if node is None:
            return []
        consumers_raw = node.get("consumers")
        consumers = (
            [c for c in consumers_raw if isinstance(c, dict)] if isinstance(consumers_raw, list) else []
        )
        if consumer_type_filter is not None:
            consumers = [c for c in consumers if c.get("consumer_type") == consumer_type_filter]
        return consumers

    # ── Query 7: 项目级涌现概念查询 ──────────────────────────────────────────

    def list_cross_plan_concepts(self, min_referer_count: int = 2) -> list[dict[str, Any]]:
        """§5.1 Query 7 — 被 ≥ N 个不同 plan 引用的概念 (涌现的项目级共识)。

        某 plan "引用" 一个 concept = 该 plan 内某 concept (frozen_concept_ids) 通过 @ 引用
        指向它。统计每个 target concept 被多少个**不同** referer plan 引用, ≥ min 的返回。
        """
        # concept_id → 它归属的 plan 集合 (referer 端定位 plan)。
        cg = self._concept_graph() or {}
        nodes = _nodes(cg)
        nodes_by_name = {n.get("name"): n for n in nodes if isinstance(n.get("name"), str)}
        # 每个 target concept_id → 引用它的 plan 集合。
        plans_per_target: dict[str, set[str]] = {}
        for ref in _refs(self._at_reference_graph()):
            if not ref.get("is_active", True):
                continue
            # referer 概念 = source_concept_id; target = target_concept_name → node.concept_id
            src_concept = ref.get("source_concept_id")
            target_node = nodes_by_name.get(ref.get("target_concept_name"))
            if target_node is None:
                target = ref.get("target")
                tid = target.get("entity_id") if isinstance(target, dict) else None
                target_node = next((n for n in nodes if n.get("concept_id") == tid), None)
            if target_node is None:
                continue
            target_id = target_node.get("concept_id")
            if not isinstance(target_id, str) or not isinstance(src_concept, str):
                continue
            referer_plans = self._plans_for_concept(src_concept)
            if not referer_plans:
                continue
            plans_per_target.setdefault(target_id, set()).update(referer_plans)

        nodes_by_id = {n.get("concept_id"): n for n in nodes}
        out: list[dict[str, Any]] = []
        for target_id, plans in plans_per_target.items():
            if len(plans) >= min_referer_count:
                node = nodes_by_id.get(target_id, {})
                out.append(
                    {
                        "concept_id": target_id,
                        "name": node.get("name", ""),
                        "referer_plan_count": len(plans),
                        "referer_plan_ids": sorted(plans),
                    },
                )
        out.sort(key=lambda c: (-int(c["referer_plan_count"]), str(c["concept_id"])))
        return out


# §5.5 — CLI query_type → method 名映射 (towow consensus query <query_type>)。
QUERY_TYPE_METHODS: dict[str, str] = {
    "concept-state": "get_concept_current_state",
    "referers": "list_referers",
    "is-current": "is_reference_current",
    "stale": "list_stale_references",
    "neighborhood": "get_concept_neighborhood",
    "consumers": "get_consumers",
    "cross-plan": "list_cross_plan_concepts",
}
