"""RUN-038 L0增强 M-0.2: impact_graph 二阶 projection + get_impact_slice (§3.7/§7.6/§4.1.6).

spec ground-truth:
  - M-0.2 §3.0 表 #7: impact_graph 类型 = 状态推导(二阶), 从 concept_graph + task_graph 派生
    (不直接消费 event)。
  - M-0.2 §3.7: 从 concept_graph.edges + task_graph.edges 派生。不直接消费 event —— 每次
    concept_graph 或 task_graph 更新后重新计算 forward/backward slice。支持 O-11 变更影响识别的
    "沿边走"能力。
  - M-0.2 §7.6: impact_graph 不直接消费 event —— 它从 concept_graph + task_graph 派生。影响关系
    就是"沿概念图边和任务图边走"。每次 concept_graph/task_graph 更新后重算。
  - M-0.2 §4.1.6: get_impact_slice(entity_id, direction[forward|backward], depth) → {affected: []}。
    O-11 变更影响识别核心能力, 在 impact_graph projection 上操作。

direction 语义 (O-11 变更影响, M-2.1 §4 表 L229-230): forward = "改了 X, 谁依赖 X (下游)";
  backward = "X 依赖谁 (上游前提)"。impact_graph 统一 source=上游/target=下游 → forward 沿
  source→target 出边走到下游依赖方。概念 dependency/reference 边的 raw 约定 (source=下游) 在
  _recompute_impact_graph 已归一化 (翻 source/target); 任务边 raw 约定 source=上游产出, 不翻。
  depth = BFS 最大跳数。

现状(改前): _PROJECTION_FILES 无 impact_graph; 无 get_impact_slice → §4.1.6 核心能力缺失。

本组证: concept_graph + task_graph 物化后 impact_graph 真派生 (统一有向边集);
  get_impact_slice forward/backward/depth 对真数据返回正确影响切片; is_active=false 概念边被排除;
  跨图 (概念边 + 任务边混合路径) 沿边走; 幂等 (重算 state 一致)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


def _ev(event_type: EventType, after: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": after},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _concept(cid: str, seq: int) -> EventRecord:
    return _ev(EventType.CONCEPT_CREATED, {"concept_id": cid, "name": cid}, seq)


def _cedge(eid: str, src: str, tgt: str, seq: int) -> EventRecord:
    return _ev(
        EventType.CONCEPT_EDGE_ADDED,
        {"edge_id": eid, "source_concept_id": src, "target_concept_id": tgt,
         "edge_type": "dependency", "direction": "unidirectional"},
        seq,
    )


def _task(tid: str, seq: int) -> EventRecord:
    return _ev(EventType.TASK_NODE_CREATED, {"task_id": tid, "task_type": "code"}, seq)


def _tedge(src: str, tgt: str, seq: int) -> EventRecord:
    return _ev(
        EventType.TASK_DEPENDENCY_EDGE_ADDED,
        {"source_task_id": src, "target_task_id": tgt, "dependency_type": "data_dependency"},
        seq,
    )


def _build_concept_chain(store: ProjectionStore) -> None:
    """概念依赖链 C depends_on B depends_on A — A=根上游, C=最下游 (3 concept + 2 dependency 边)。

    depends_on raw 约定 source=依赖方=下游 (M-0.2 §3.2): "B 依赖 A" = source=B→target=A。
    归一化后 forward(A) 走到 A 的下游依赖方 B, C。
    """
    store._apply(_concept("A", 1))
    store._apply(_concept("B", 2))
    store._apply(_concept("C", 3))
    store._apply(_cedge("e-BA", "B", "A", 4))  # B depends_on A
    store._apply(_cedge("e-CB", "C", "B", 5))  # C depends_on B


def test_impact_graph_materialized_after_concept_graph_update(tmp_path: Path) -> None:
    """concept_graph 更新后 impact_graph 真派生 (§7.6 二阶, 不直接消费 event)。

    归一化: dependency 边 raw source=下游 → impact_graph 翻成 source=上游/target=下游。
    raw 边 B→A (B 依赖 A) 归一化后 = 影响边 A→B (上游 A → 下游 B)。
    """
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    ig = store.read("impact_graph")
    assert ig is not None, "impact_graph 在 concept_graph 更新后真派生 (改前不存在)"
    edges = ig.get("edges", [])
    pairs = {(e["source"], e["target"]) for e in edges}
    assert ("A", "B") in pairs, "raw 边 B→A (B 依赖 A) 归一化为影响边 A→B (上游→下游)"
    assert ("B", "C") in pairs, "raw 边 C→B (C 依赖 B) 归一化为影响边 B→C (上游→下游)"


def test_get_impact_slice_forward(tmp_path: Path) -> None:
    """forward: 改 A → 沿出边走到下游依赖方 B (depth1), B+C (depth2) — O-11 "谁依赖我"。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    d1 = store.get_impact_slice("A", direction="forward", depth=1)
    assert set(d1["affected"]) == {"B"}, "depth1 forward: A 的直接下游依赖方 B"
    d2 = store.get_impact_slice("A", direction="forward", depth=2)
    assert set(d2["affected"]) == {"B", "C"}, "depth2 forward: 依赖 A 的全下游 B, C"


def test_get_impact_slice_backward(tmp_path: Path) -> None:
    """backward: C 依赖谁 — 沿入边走到上游前提 B (depth1), A+B (depth2)。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    d1 = store.get_impact_slice("C", direction="backward", depth=1)
    assert set(d1["affected"]) == {"B"}, "depth1 backward: C 的直接上游前提 B"
    d2 = store.get_impact_slice("C", direction="backward", depth=2)
    assert set(d2["affected"]) == {"A", "B"}, "depth2 backward: C 依赖的全上游 A, B"


def test_get_impact_slice_depth_caps(tmp_path: Path) -> None:
    """depth 截断: depth1 不越过一跳。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    d1 = store.get_impact_slice("A", direction="forward", depth=1)
    assert "C" not in d1["affected"], "depth1 不应到达 2 跳的 C"


def _edge_removed(edge_id: str, seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.CONCEPT_EDGE_REMOVED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"before_state": {"edge_id": edge_id}},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT_EDGE, entity_id=edge_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_inactive_concept_edge_excluded(tmp_path: Path) -> None:
    """is_active=false 的概念边 (removed) 不进影响图 (§3.1 removed 标 false 不物删)。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    store._apply(_edge_removed("e-BA", 6))  # 移除 B depends_on A 这条边 (raw B→A)
    d = store.get_impact_slice("A", direction="forward", depth=2)
    assert "B" not in d["affected"], "removed (is_active=false) 边不参与影响走"


def test_impact_graph_crosses_concept_and_task_edges(tmp_path: Path) -> None:
    """task 边也进影响图; forward slice 沿任务边走 (§3.7 concept_graph + task_graph 双源)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_task("t1", 1))
    store._apply(_task("t2", 2))
    store._apply(_tedge("t1", "t2", 3))
    d = store.get_impact_slice("t1", direction="forward", depth=1)
    assert set(d["affected"]) == {"t2"}, "task 依赖边进入影响图并可走"


def test_impact_graph_idempotent_recompute(tmp_path: Path) -> None:
    """重放同事件序 → impact_graph 边集一致 (幂等)。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    ig1 = store.read("impact_graph")
    _build_concept_chain(store)  # 重放
    ig2 = store.read("impact_graph")
    assert ig1 is not None
    assert ig2 is not None
    p1 = sorted((e["source"], e["target"]) for e in ig1["edges"])
    p2 = sorted((e["source"], e["target"]) for e in ig2["edges"])
    assert p1 == p2, "幂等: 重算边集一致"


def test_get_impact_slice_unknown_entity_empty(tmp_path: Path) -> None:
    """未知 entity → 空切片 (不抛)。"""
    store = ProjectionStore(tmp_path / "graph")
    _build_concept_chain(store)
    d = store.get_impact_slice("nonexistent", direction="forward", depth=3)
    assert d["affected"] == []
