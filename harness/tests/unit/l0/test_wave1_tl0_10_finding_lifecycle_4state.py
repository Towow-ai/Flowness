"""波1 T-L0-10 / T-L1-64: finding_lifecycle reducer 真物化 finding 四态 (M-0.2 §3.10 / M-1.5).

活对账器发现: finding_lifecycle reducer 已由 RUN-035 (T-L1-52/53) 建好且真物化全四态 ——
T-L0-10 (M-0.2 §3.10 reducer) 与 T-L1-64 (M-1.5 finding lifecycle projection) 两条波1任务实由
此 reducer 覆盖。本测试钉住四态生命周期归约 (回归保护 + 验收证据), 防后续退化。

源表 done_criteria (T-L0-10): finding_lifecycle projection 真物化 finding 四态, review_inbox 可消费。
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
        event_category=EventCategory.FINDING,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id="f", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _node(store: ProjectionStore, fid: str) -> dict:
    return next(f for f in store.read("finding_lifecycle")["findings"] if f["finding_id"] == fid)


def test_finding_lifecycle_materializes_four_states(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    fid = "F-1"
    store._apply(_record(EventType.FINDING_CREATED, {
        "finding_id": fid, "severity": "major", "lifecycle_state": "created",
        "description": "race", "detection_method": "ad_hoc",
    }, 1))
    assert _node(store, fid)["lifecycle_state"] == "created"

    store._apply(_record(EventType.FINDING_VERIFIED, {"finding_id": fid, "verification_state": "confirmed_real"}, 2))
    assert _node(store, fid)["lifecycle_state"] == "verified"
    assert _node(store, fid)["verification_state"] == "confirmed_real"

    store._apply(_record(EventType.FINDING_DISPUTED, {"finding_id": fid}, 3))
    assert _node(store, fid)["lifecycle_state"] == "disputed"

    store._apply(_record(EventType.FINDING_RESOLVED, {"finding_id": fid, "resolution": "fixed"}, 4))
    assert _node(store, fid)["lifecycle_state"] == "resolved"
    assert _node(store, fid)["resolution"] == "fixed"


def test_finding_lifecycle_idempotent_rebuild(tmp_path: Path) -> None:
    events = [
        _record(EventType.FINDING_CREATED, {
            "finding_id": "F-2", "severity": "minor", "lifecycle_state": "created",
            "description": "d", "detection_method": "ad_hoc",
        }, 1),
        _record(EventType.FINDING_VERIFIED, {"finding_id": "F-2", "verification_state": "confirmed_real"}, 2),
    ]
    s1 = ProjectionStore(tmp_path / "g1")
    for e in events:
        s1._apply(e)
    s2 = ProjectionStore(tmp_path / "g2")
    for e in [*events, *events]:
        s2._apply(e)
    assert s1.read("finding_lifecycle") == s2.read("finding_lifecycle")
