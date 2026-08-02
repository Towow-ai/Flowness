"""RUN-038 L0增强 M-0.2: concept_evolution read API (§3.7 / §4.1.4 get_supersede_chain 同族).

spec ground-truth (M-0.2 §3.7):
  - concept_evolution (概念演化链) —— 从 supersede chain 推导。每个概念的 supersede 历史 = 沿
    supersede_index 追溯。不需要独立 state 文件 —— 是 concept_graph.supersede_index 的 read API 封装。
  - §3.0 表 #8: concept_evolution 类型 = 状态推导, 从 supersede chain 推导。
  - supersede_index (§3.1): concept_id → [supersede_event_id] 快速查哪些概念被 supersede 过。
    由 ConceptGraphProposal (committed supersede) reducer 维护。

设计: concept_evolution 不是物化 projection 文件 (§3.7 明令"不需要独立 state 文件") —— 是 read API
  封装。get_concept_evolution(concept_id) 读 concept_graph.supersede_index[concept_id] 返回该概念的
  supersede 演化事件链 + 当前 node 态 (从 concept_graph.nodes 取)。

现状(改前): 无 get_concept_evolution —— §3.7 的"沿 supersede_index 追溯"read 能力未封装
  (消费方得自己扒 concept_graph 内部 supersede_index, 破坏 API 边界)。

本组证: ConceptGraphProposal 物化进 supersede_index 后 get_concept_evolution 返回该概念的演化
  事件链 (顺序 = 物化顺序) + 当前 node; 多次 supersede 累积; 未 supersede 概念返回空链 + node;
  未知概念返回空。
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
    SupersedeNoveltyType,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


def _ev(event_type: EventType, after: dict[str, object], seq: int, *, supersedes: str | None = None) -> EventRecord:
    sup = (
        Supersede(
            is_supersede=True,
            superseded_event_id=supersedes,
            novelty="维护 fork 改判",
            novelty_type=SupersedeNoveltyType.SCOPE_CHANGE,
        )
        if supersedes is not None
        else Supersede(is_supersede=False)
    )
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
        supersede=sup,
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _concept(cid: str, seq: int, name: str = "") -> EventRecord:
    return _ev(EventType.CONCEPT_CREATED, {"concept_id": cid, "name": name or cid}, seq)


def _proposal(cid: str, new_def: str, seq: int, supersedes_event: str) -> EventRecord:
    return _ev(
        EventType.CONCEPT_GRAPH_PROPOSAL,
        {"proposed_changes": [{"change_type": "redefine", "entity_type": "concept",
                               "entity_id": cid, "new_value": {"definition": new_def}}]},
        seq,
        supersedes=supersedes_event,
    )


def test_get_concept_evolution_after_supersede(tmp_path: Path) -> None:
    """ConceptGraphProposal 物化进 supersede_index 后 get_concept_evolution 返回演化链 + node。"""
    store = ProjectionStore(tmp_path / "graph")
    c = _concept("c-1", 1, name="支付")
    store._apply(c)
    prop = _proposal("c-1", "支付v2 含退款", 2, supersedes_event=c.event_id)
    store._apply(prop)

    evo = store.get_concept_evolution("c-1")
    assert evo["concept_id"] == "c-1"
    assert prop.event_id in evo["supersede_events"], "supersede 事件进演化链 (沿 supersede_index 追溯)"
    assert evo["node"] is not None, "返回当前 node 态"
    assert evo["node"]["definition"] == "支付v2 含退款", "node 已被 proposal apply (definition 更新)"


def test_get_concept_evolution_accumulates_multiple_supersedes(tmp_path: Path) -> None:
    """同概念多次 supersede → 演化链累积 (顺序 = 物化顺序)。"""
    store = ProjectionStore(tmp_path / "graph")
    c = _concept("c-2", 1)
    store._apply(c)
    p1 = _proposal("c-2", "v2", 2, supersedes_event=c.event_id)
    store._apply(p1)
    p2 = _proposal("c-2", "v3", 3, supersedes_event=p1.event_id)
    store._apply(p2)

    evo = store.get_concept_evolution("c-2")
    assert evo["supersede_events"] == [p1.event_id, p2.event_id], "多次 supersede 按物化顺序累积"


def test_get_concept_evolution_unsuperseded_concept(tmp_path: Path) -> None:
    """未 supersede 的概念: 空演化链 + 有 node。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("c-3", 1, name="未改"))
    evo = store.get_concept_evolution("c-3")
    assert evo["supersede_events"] == [], "未 supersede → 空链"
    assert evo["node"] is not None
    assert evo["node"]["name"] == "未改"


def test_get_concept_evolution_unknown_concept(tmp_path: Path) -> None:
    """未知概念: 空链 + node=None (不抛)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("c-4", 1))
    evo = store.get_concept_evolution("nonexistent")
    assert evo["supersede_events"] == []
    assert evo["node"] is None
