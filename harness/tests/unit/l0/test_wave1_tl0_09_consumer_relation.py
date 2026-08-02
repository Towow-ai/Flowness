"""波1 T-L0-09: consumer_relation reducer (M-0.2 §3.6).

源表 done_criteria: consumer_relation projection 真物化, 反映最新发布的消费方列表
(替换语义非追加)。此前 _PROJECTION_FILES 声明了 consumer_relation 但 _apply 无 reducer 分支
→ 文件从不物化 (stub)。本任务补 reducer, reduce canonical ConsumerListPublished 事件。

诚实边界: reducer 按 ev.event_type is CONSUMER_LIST_PUBLISHED 分发, reduce 当前 producer 路径
emit 的 canonical 事件 (after_state.{concept_id, consumers})。consumers 按 producer 真形存
(consumer_type/consumer_id/usage); 该形与 spec §3.1 ConsumerRef(entity_type/...) 的分歧是
既有 producer-alignment exempt 债, 非本 reducer 任务。少量 producer 修前 legacy stub-rewrap
NodeTouched 形 (event_type=NodeTouched) dispatch 看不到, 不在此 claim。
"""

from __future__ import annotations

import hashlib
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

if TYPE_CHECKING:
    from pathlib import Path


def _record(payload: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.CONSUMER_LIST_PUBLISHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _published(concept_id: str, consumers: list[dict[str, object]], seq: int) -> EventRecord:
    return _record({"after_state": {"concept_id": concept_id, "consumers": consumers}}, seq)


def test_consumer_relation_materializes_and_replaces(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    c1 = [{"consumer_type": "skill", "consumer_id": "M-1.3", "usage": "plans target it"}]
    c1_new = [
        {"consumer_type": "skill", "consumer_id": "M-1.4", "usage": "execution"},
        {"consumer_type": "skill", "consumer_id": "M-1.5", "usage": "review"},
    ]
    c2 = [{"consumer_type": "skill", "consumer_id": "M-1.2", "usage": "consensus"}]
    store._apply(_published("concept-A", c1, 1))
    store._apply(_published("concept-B", c2, 2))
    store._apply(_published("concept-A", c1_new, 3))  # 替换 concept-A 的消费方 (非追加)

    proj = store.read("consumer_relation")
    assert proj is not None
    rels = {r["concept_id"]: r["consumers"] for r in proj["relations"]}
    assert rels["concept-A"] == c1_new, "replace semantics: 最新发布替换, 不追加旧的"
    assert rels["concept-B"] == c2
    assert len(proj["relations"]) == 2


def test_consumer_relation_idempotent_rebuild(tmp_path: Path) -> None:
    events = [
        _published("concept-A", [{"consumer_type": "skill", "consumer_id": "M-1.3", "usage": "u"}], 1),
        _published("concept-A", [{"consumer_type": "skill", "consumer_id": "M-1.4", "usage": "u2"}], 2),
    ]

    def _hash(d: object) -> str:
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()

    s1 = ProjectionStore(tmp_path / "g1")
    for e in events:
        s1._apply(e)
    s2 = ProjectionStore(tmp_path / "g2")
    for e in [*events, *events]:
        s2._apply(e)
    assert _hash(s1.read("consumer_relation")) == _hash(s2.read("consumer_relation"))
