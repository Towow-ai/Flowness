"""M-1.2 §7.1 消费方初次自动扫描 (T-L1-17, 波1).

# spec source: 04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §7.1

M-1.2 工程共识建立 / 每批冻结前, 对每个 concept 自动扫出消费方清单 (而非人工 --consumers 手填)。
spec §7.1 三扫描源:
  (1) 当前 plan 下任务的 read_set / write_set / call_set；
  (2) 既有代码库静态扫描 (grep / AST)；
  (3) 既有 ConceptEdgeAdded 标了引用关系的下游。

本实现 (v3 初版可确定性两源):
  - 源(1): task_graph projection 的任务 read_set/write_set 含本 concept_id → data_consumer(read/write)。
    call_set: task_graph 无此字段 (只 read_set/write_set), 故不产 behavior_consumer(call) — 待 call_set
    事件落地后补 (诚实边界)。
  - 源(3): concept_graph 指向本 concept 的"消费型"边 (dependency/reference/data_schema/contract) →
    边源 concept 是 consumer。(实际 ConceptEdgeType 6 枚举无 spec 伪代码的 consumes/references_field,
    映射到这 4 个有"使用"语义的类型。)
  - 源(2) 代码 AST 扫描: spec §7.1 标"如适用" + §11.4 "v3 初版 M-1.2 手动" → 此处不做, 留 M-2.1
    持续更新 / 后续 (诚实边界, 不假装)。

plan_id: task_graph 节点当前无 plan_id 字段 → 扫全部任务 (v3 初版单 plan 假设); 多 plan 后按 plan 过滤。
输出 consumer dict 用当前 producer 真形 {consumer_type, consumer_id, usage} (与现有 consumer-publish
CLI + consumer_relation reducer 一致; 与 spec §3.1 ConsumerRef {entity_type/entity_id/consumption_type}
字段名分歧是既有, 走 T-L0-09 alignment, 本任务不动)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l0.projection.projection import ProjectionStore

# concept_graph 边里有"使用/消费"语义的 edge_type (ConceptEdgeType 6 枚举的子集)。
# state_transition / containment 非消费关系, 不计入。
_CONSUMING_EDGE_TYPES = {"dependency", "reference", "data_schema", "contract"}


def initial_scan_consumers(
    concept_id: str,
    projection_store: ProjectionStore,
    *,
    plan_id: str | None = None,  # v3 初版单 plan, task 节点无 plan_id; 预留多 plan 过滤 (暂未用)
) -> list[dict[str, str]]:
    """M-1.2 §7.1 — 自动扫出 concept_id 的消费方清单 (替代人工手填)。

    返回 consumer dict 列表 {consumer_type, consumer_id, usage}, 去重 (按 type+id+usage)。
    """
    consumers: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(consumer_type: str, consumer_id: str, usage: str) -> None:
        if not consumer_id:
            return
        key = (consumer_type, consumer_id, usage)
        if key in seen:
            return
        seen.add(key)
        consumers.append(
            {"consumer_type": consumer_type, "consumer_id": consumer_id, "usage": usage},
        )

    # 源(1): task read_set / write_set
    task_graph = projection_store.read("task_graph") or {}
    nodes = task_graph.get("nodes", [])
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            task_id = str(node.get("task_id", ""))
            for entry in node.get("read_set", []) if isinstance(node.get("read_set"), list) else []:
                if isinstance(entry, dict) and entry.get("entity_id") == concept_id:
                    _add("data_consumer", task_id, "read")
            for entry in node.get("write_set", []) if isinstance(node.get("write_set"), list) else []:
                if isinstance(entry, dict) and entry.get("entity_id") == concept_id:
                    _add("data_consumer", task_id, "write")

    # 源(3): concept_graph 指向本 concept 的消费型边
    concept_graph = projection_store.read("concept_graph") or {}
    edges = concept_graph.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            if edge.get("is_active") is False:
                continue
            if (
                edge.get("target_concept_id") == concept_id
                and edge.get("edge_type") in _CONSUMING_EDGE_TYPES
            ):
                _add(
                    "data_consumer",
                    str(edge.get("source_concept_id", "")),
                    f"edge:{edge.get('edge_type')}",
                )

    return consumers


__all__ = ["initial_scan_consumers"]
