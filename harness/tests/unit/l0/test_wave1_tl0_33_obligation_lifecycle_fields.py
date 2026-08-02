"""波1 T-L0-33: obligation_lifecycle reducer 三分类字段 (Patch E §13.1).

源表 done_criteria: catchup 后 obligation 节点的 last_activation/check/violation_event_id 三字段
各自指向对应类型最新事件 (此前只单一 last_state_change_event_id, 三类无法区分)。
"""

from __future__ import annotations

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

if TYPE_CHECKING:
    from pathlib import Path


def _record(event_type: EventType, payload: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.OBLIGATION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_obligation_three_class_last_event_fields(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    oid = "obl-1"
    store._apply(_record(EventType.OBLIGATION_CAPTURED, {"obligation_id": oid, "attached_to": []}, 1))
    act = _record(EventType.OBLIGATION_ACTIVATED, {"obligation_id": oid}, 2)
    store._apply(act)
    chk1 = _record(EventType.OBLIGATION_CHECKED, {"obligation_id": oid}, 3)
    store._apply(chk1)
    vio = _record(EventType.OBLIGATION_VIOLATED, {"obligation_id": oid}, 4)
    store._apply(vio)
    chk2 = _record(EventType.OBLIGATION_CHECKED, {"obligation_id": oid}, 5)
    store._apply(chk2)

    proj = store.read("obligation_lifecycle_state")
    node = next(n for n in proj["obligations"] if n["obligation_id"] == oid)
    # 三类各指对应类型最新事件 (check 取第二个 chk2, 非第一个)
    assert node["last_activation_event_id"] == act.event_id
    assert node["last_check_event_id"] == chk2.event_id
    assert node["last_violation_event_id"] == vio.event_id
    # 旧聚合字段仍在 (向后兼容), 指向全局最新状态变更 (chk2)
    assert node["last_state_change_event_id"] == chk2.event_id


def test_obligation_three_class_fields_none_before_event(tmp_path: Path) -> None:
    """刚 captured、未发生 Activated/Checked/Violated 时三字段为 None (不乱指)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(EventType.OBLIGATION_CAPTURED, {"obligation_id": "obl-x", "attached_to": []}, 1))
    proj = store.read("obligation_lifecycle_state")
    node = next(n for n in proj["obligations"] if n["obligation_id"] == "obl-x")
    assert node["last_activation_event_id"] is None
    assert node["last_check_event_id"] is None
    assert node["last_violation_event_id"] is None
