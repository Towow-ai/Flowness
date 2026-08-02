"""T-FIX-B6-01 (PLAN-seam#1) — task 依赖边纠错原语.

PLAN-seam#1: task 图只有加边无删/纠正。一条错向/假依赖边一旦 emit 永久污染 plan,
补反向边成环被 no_circular 拒, 唯一退路整盘换 plan_id 重做。

本 task 新增 TaskDependencyEdgeRemoved 事件 (schema + payload + registry) + projection
reducer 消费撤边 (event-sourced, append-only, 不物理删 ADDED), 让 planner 能撤一条错向边
而无需整盘换 plan_id。

闭环 (真验): 加错向 A→B → 撤 A→B → 补正确 B→A → no_circular 不再拒。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection import ProjectionStore
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
from towow.schemas.payloads.plan_freezed import check_no_circular_dependency
from towow.schemas.payloads.registry import PAYLOAD_REGISTRY, conforms

if TYPE_CHECKING:
    from pathlib import Path


def _rec(seq: int, event_type: EventType, payload: dict, entity_id: str) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{seq}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK_EDGE, entity_id=entity_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _added_payload(src: str, dst: str) -> dict:
    return {
        "target_entity_type": "task_edge",
        "transition_type": "created",
        "after_state": {
            "source_task_id": src,
            "target_task_id": dst,
            "dependency_type": "ordering",
            "evidence": f"{src} writes X before {dst}",
            "strength": "hard",
        },
    }


def _removed_payload(src: str, dst: str) -> dict:
    return {
        "target_entity_type": "task_edge",
        "transition_type": "removed",
        "before_state": {
            "source_task_id": src,
            "target_task_id": dst,
            "dependency_type": "ordering",
        },
        "after_state": {"status": "removed", "removal_reason": "wrong direction; re-add reversed"},
    }


# ─── (1) schema + registry 存在 ──────────────────────────────────────────────


def test_enum_has_dependency_edge_removed() -> None:
    assert EventType.TASK_DEPENDENCY_EDGE_REMOVED.value == "TaskDependencyEdgeRemoved"


def test_registry_maps_removed_payload() -> None:
    assert EventType.TASK_DEPENDENCY_EDGE_REMOVED in PAYLOAD_REGISTRY
    # 走生产校验路径 (conforms = model_validate strict=False, enum 收 string)。
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_REMOVED, _removed_payload("A", "B")) is None
    # 缺 before_state → 被拒
    bad = _removed_payload("A", "B")
    del bad["before_state"]
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_REMOVED, bad) is not None
    # 缺 removal_reason → 被拒
    bad2 = _removed_payload("A", "B")
    del bad2["after_state"]["removal_reason"]
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_REMOVED, bad2) is not None
    # 校验后取出字段
    model = PAYLOAD_REGISTRY[EventType.TASK_DEPENDENCY_EDGE_REMOVED]
    obj = model.model_validate(_removed_payload("A", "B"), strict=False)
    assert obj.before_state.source_task_id == "A"
    assert obj.before_state.target_task_id == "B"


# ─── (2) reducer 消费撤边: active edge 消失, 但事件流两条都在 ───────────────────


def test_reducer_marks_edge_inactive_on_remove(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_rec(1, EventType.TASK_DEPENDENCY_EDGE_ADDED, _added_payload("A", "B"), "A->B"))
    edges = store.read("task_graph")["edges"]
    assert any(e["source_task_id"] == "A" and e["target_task_id"] == "B" for e in edges)
    # ADDED 边活
    added_edge = next(e for e in edges if e["source_task_id"] == "A" and e["target_task_id"] == "B")
    assert added_edge.get("is_active") is True

    store._apply(_rec(2, EventType.TASK_DEPENDENCY_EDGE_REMOVED, _removed_payload("A", "B"), "A->B"))
    edges = store.read("task_graph")["edges"]
    # 撤边非物理删: edge dict 还在, 但 is_active=False
    removed_edge = next(e for e in edges if e["source_task_id"] == "A" and e["target_task_id"] == "B")
    assert removed_edge.get("is_active") is False
    # active edges 不含 A->B
    active = [e for e in edges if e.get("is_active") is not False]
    assert not any(e["source_task_id"] == "A" and e["target_task_id"] == "B" for e in active)


def test_get_neighborhood_skips_removed_edge(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    from towow.l0.event_log.event_log import EventLog

    log = EventLog(tmp_path / "events.log")
    # 建两节点 A, B
    for tid in ("A", "B"):
        store._apply(
            _rec(
                int(tid == "B") + 1,
                EventType.TASK_NODE_CREATED,
                {"after_state": {"task_id": tid, "task_type": "implementation", "description": "d"}},
                tid,
            ),
        )
    store._apply(_rec(3, EventType.TASK_DEPENDENCY_EDGE_ADDED, _added_payload("A", "B"), "A->B"))
    # 撤边前: A 的 1-hop 邻域含 B
    nb = store.get_neighborhood(log, "task_graph", "A", 1)
    assert any(n["task_id"] == "B" for n in nb["nodes"])

    store._apply(_rec(4, EventType.TASK_DEPENDENCY_EDGE_REMOVED, _removed_payload("A", "B"), "A->B"))
    nb = store.get_neighborhood(log, "task_graph", "A", 1)
    # 撤边后: A 邻域不再含 B (inactive 边不可走)
    assert not any(n["task_id"] == "B" for n in nb["nodes"])
    assert not any(e["source_task_id"] == "A" and e["target_task_id"] == "B" for e in nb["edges"])


# ─── (3) 闭环: 加错向 → 撤 → 补正确不再成环被 no_circular 拒 ───────────────────


def _write_event(events_log: Path, seq: int, event_type: str, payload: dict) -> None:
    rec = {
        "event_id": f"evt-{uuid.uuid4().hex}",
        "sequence_number": seq,
        "event_type": event_type,
        "payload": payload,
    }
    with events_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_no_circular_honors_edge_removal(tmp_path: Path) -> None:
    """加错向 A→B 后撤掉, 补正确 B→A, no_circular 必须放行 (不把撤掉的边算进环)。

    现实场景: planner 已 dep-add(A→B), 但方向反了。要补 B→A 才对。若 no_circular
    还把 A→B 算进去, A→B + B→A 构成 2-cycle → 冻结被拒 → 唯一退路整盘换 plan_id。
    撤边原语让 A→B inactive 后, 只剩 B→A, 无环。
    """
    log = tmp_path / "events.log"
    plan_id = "plan-xyz"
    # 两个 plan task
    _write_event(log, 1, "TaskNodeCreated", {"after_state": {"task_id": "A", "plan_id": plan_id}})
    _write_event(log, 2, "TaskNodeCreated", {"after_state": {"task_id": "B", "plan_id": plan_id}})
    # 错向 A→B
    _write_event(log, 3, "TaskDependencyEdgeAdded", _added_payload("A", "B"))
    # 撤掉 A→B
    _write_event(log, 4, "TaskDependencyEdgeRemoved", _removed_payload("A", "B"))
    # 补正确 B→A
    _write_event(log, 5, "TaskDependencyEdgeAdded", _added_payload("B", "A"))

    ok, msg = check_no_circular_dependency(tmp_path, plan_id)
    assert ok, f"no_circular should pass after removal but got: {msg}"


def test_no_circular_still_catches_real_cycle(tmp_path: Path) -> None:
    """回归: 没撤边时, A→B + B→A 仍被判成环 (撤边逻辑不能让真环漏网)。"""
    log = tmp_path / "events.log"
    plan_id = "plan-real-cycle"
    _write_event(log, 1, "TaskNodeCreated", {"after_state": {"task_id": "A", "plan_id": plan_id}})
    _write_event(log, 2, "TaskNodeCreated", {"after_state": {"task_id": "B", "plan_id": plan_id}})
    _write_event(log, 3, "TaskDependencyEdgeAdded", _added_payload("A", "B"))
    _write_event(log, 4, "TaskDependencyEdgeAdded", _added_payload("B", "A"))

    ok, _msg = check_no_circular_dependency(tmp_path, plan_id)
    assert not ok
