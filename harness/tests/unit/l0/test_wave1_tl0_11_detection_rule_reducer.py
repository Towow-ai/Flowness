"""波1 T-L0-11: detection_rule_lifecycle reducer (M-0.2 §3.11).

源表 done_criteria: 有 DetectionRule* 事件后 catchup 出 detection_rule projection (现 reducer stub:
声明 projection 文件但无 _apply 分支 → DetectionRule* 事件发了没人读, 假完整)。

现状(改前): projection.py 有 detection_rule_lifecycle 文件声明 + schema, 但 _apply 无分支 → 永不物化。
本组证: 喂合成 DetectionRule* 事件 (proposed→shadow→promoted→retired) 经 _apply → projection 真
物化, lifecycle_state 随事件迁移, rule_definition 留存, 同 rule_id upsert (幂等), 多 rule 独立。

注: done_criteria 的"无数据可验"指端到端 (E.6 detection-rule 子系统未建无真事件源); reducer 本体 +
合成事件单测可做 — 这是 reducer 正确性的可验部分, 真事件源到位后行为一致 (reducer 幂等)。
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


def _dr(event_type: EventType, rule_id: str, state: str, seq: int, **extra: object) -> EventRecord:
    """合成 DetectionRule* 事件 (flat payload: rule_id + rule_lifecycle_state + 额外字段)。"""
    payload: dict[str, object] = {"rule_id": rule_id, "rule_lifecycle_state": state, **extra}
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test-m23"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=rule_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _rules(store: ProjectionStore) -> dict[str, dict]:
    proj = store.read("detection_rule_lifecycle") or {"rules": []}
    return {r["rule_id"]: r for r in proj.get("rules", [])}


def test_detection_rule_lifecycle_materializes_and_transitions(tmp_path: Path) -> None:
    """proposed→shadow→promoted(enforced)→retired 全程物化; lifecycle_state 迁移; rule_definition 留存。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_dr(EventType.DETECTION_RULE_PROPOSED, "rule-X", "proposed", 1, rule_definition="若 X 则告警"))
    store._apply(_dr(EventType.DETECTION_RULE_SHADOWING, "rule-X", "shadow", 2, shadow_start_event_offset=2))
    store._apply(_dr(EventType.DETECTION_RULE_PROMOTED, "rule-X", "enforced", 3, promotion_reason="FP 低"))

    rules = _rules(store)
    assert "rule-X" in rules, "DetectionRule* 事件后 projection 真物化 (改前 stub 永不物化)"
    rx = rules["rule-X"]
    assert rx["lifecycle_state"] == "enforced", "lifecycle_state 随事件迁移到最新"
    assert rx["rule_definition"] == "若 X 则告警", "rule_definition 创建时留存"
    assert rx["created_at_event_id"]  # 创建留痕

    store._apply(_dr(EventType.DETECTION_RULE_RETIRED, "rule-X", "retired", 4, retirement_reason="无命中"))
    rules = _rules(store)
    assert rules["rule-X"]["lifecycle_state"] == "retired"
    assert len(rules) == 1, "同 rule_id upsert 非追加 (无重复)"


def test_multiple_rules_independent(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_dr(EventType.DETECTION_RULE_PROPOSED, "rule-A", "proposed", 1, rule_definition="dA"))
    store._apply(_dr(EventType.DETECTION_RULE_PROPOSED, "rule-B", "proposed", 2, rule_definition="dB"))
    store._apply(_dr(EventType.DETECTION_RULE_RETIRED, "rule-A", "retired", 3, retirement_reason="x"))

    rules = _rules(store)
    assert rules["rule-A"]["lifecycle_state"] == "retired"
    assert rules["rule-B"]["lifecycle_state"] == "proposed", "rule-B 不受 rule-A 退役影响"


def test_idempotent_rebuild(tmp_path: Path) -> None:
    """重放同事件序 → state 一致 (幂等, full rebuild 可重现)。"""
    store = ProjectionStore(tmp_path / "graph")
    evs = [
        _dr(EventType.DETECTION_RULE_PROPOSED, "rule-X", "proposed", 1, rule_definition="d"),
        _dr(EventType.DETECTION_RULE_PROMOTED, "rule-X", "enforced", 2, promotion_reason="r"),
    ]
    for e in evs:
        store._apply(e)
    for e in evs:  # 重放
        store._apply(e)
    rules = _rules(store)
    assert len(rules) == 1
    assert rules["rule-X"]["lifecycle_state"] == "enforced"
