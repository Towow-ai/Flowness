"""波1 T-L0-34: lifecycle_state 诚实名 last_observed_lifecycle_event_type (M-0.6 Patch E §3.4).

源表 done_criteria: reducer 不再用 lifecycle_state 混写 canonical/scoped; scoped 事件(activated/
checked/violated)只更新 last_observed_lifecycle_event_type, canonical 状态在 canonical_state。
spec Patch E: 兼容保留 lifecycle_state 旧名, 加 last_observed_lifecycle_event_type 同步 (非破坏改名)。

现状(改前): obligation 节点有 lifecycle_state(存 activated/checked 等 event 类型) + canonical_state
(3 态), 但无 last_observed_lifecycle_event_type 诚实名 → 字段名 lifecycle_state 误导(像状态实为事件类型)。
本组证: 加 last_observed_lifecycle_event_type 同步 lifecycle_state(兼容保留); scoped 事件只动它+
lifecycle_state, 不动 canonical_state(active 不变); canonical 转移(retired)才动 canonical_state。
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


def _obl_event(event_type: EventType, obligation_id: str, seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.OBLIGATION,
        payload={"obligation_id": obligation_id, "scope_rule": "r", "definition": "d"},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.OBLIGATION, entity_id=obligation_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _node(store: ProjectionStore, obligation_id: str) -> dict:
    proj = store.read("obligation_lifecycle_state") or {"obligations": []}
    return next(n for n in proj["obligations"] if n["obligation_id"] == obligation_id)


def test_captured_syncs_last_observed(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_obl_event(EventType.OBLIGATION_CAPTURED, "obl-1", 1))
    n = _node(store, "obl-1")
    assert n["lifecycle_state"] == "captured"
    assert n["last_observed_lifecycle_event_type"] == "captured"  # 诚实名同步
    assert n["canonical_state"] == "active"


def test_scoped_events_update_last_observed_not_canonical(tmp_path: Path) -> None:
    """activated/checked 是 scoped 事件 → 更新 last_observed(+lifecycle_state 兼容), canonical 不动。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_obl_event(EventType.OBLIGATION_CAPTURED, "obl-1", 1))
    store._apply(_obl_event(EventType.OBLIGATION_ACTIVATED, "obl-1", 2))
    n = _node(store, "obl-1")
    assert n["last_observed_lifecycle_event_type"] == "activated"
    assert n["lifecycle_state"] == "activated"  # 兼容保留, 同步
    assert n["canonical_state"] == "active", "scoped 事件不动 canonical (不混淆)"

    store._apply(_obl_event(EventType.OBLIGATION_CHECKED, "obl-1", 3))
    n = _node(store, "obl-1")
    assert n["last_observed_lifecycle_event_type"] == "checked"
    assert n["canonical_state"] == "active", "checked 仍不动 canonical"


def test_canonical_transition_updates_canonical_state(tmp_path: Path) -> None:
    """retired 是 canonical 转移 → canonical_state 变 retired (last_observed 也记 retired)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_obl_event(EventType.OBLIGATION_CAPTURED, "obl-1", 1))
    store._apply(_obl_event(EventType.OBLIGATION_RETIRED, "obl-1", 2))
    n = _node(store, "obl-1")
    assert n["last_observed_lifecycle_event_type"] == "retired"
    assert n["canonical_state"] == "retired"
