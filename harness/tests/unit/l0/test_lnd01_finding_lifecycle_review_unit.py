"""T-LND-01 (INV-B1-1 finding-no-evaporate): finding_lifecycle 投影携带 review_unit_id +
target 折叠链接, 并把 "每条 FindingCreated 必在投影有当前状态条目直到 terminal" 钉成可查询断言.

危机2 (finding 蒸发) 的真根不是 "没有投影" —— RUN-035 的 _reduce_finding_lifecycle 已物化四态.
真缺口是: 投影条目缺 review_unit_id (下游 derived-review-verdict 折叠的分组键) + target, 导致
finding 虽在日志+投影里, 却对 verdict 完成决策不可见 = 实质蒸发. 本测试钉住:
  1. reducer 把 FindingCreated.review_unit_id / target 物化进条目 (RED: 现 reducer 不读这两字段)
  2. ProjectionStore.findings_for_review_unit(uid) 按 review_unit_id 分组返回 (RED: 无此接口)
  3. INV-B1-1: 任一 FindingCreated event_id 在投影必有对应条目且 lifecycle_state 合法 (回归保护)

源: concept finding-lifecycle-projection@v1 / review-unit@v1; brief-e3cbdbe8 INV-B1-1.
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

_VALID_LIFECYCLE = {"created", "verified", "disputed", "resolved"}


def _record(
    event_type: EventType,
    payload: dict[str, object],
    seq: int,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> EventRecord:
    if session_id or run_id:
        prov = Provenance(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m15-review-session",
            session_id=session_id,
            run_id=run_id,
            skill_id="M-1.5",
        )
    else:
        prov = Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test")
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.FINDING,
        payload=payload,
        provenance=prov,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id="f", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _node(store: ProjectionStore, fid: str) -> dict:
    return next(f for f in store.read("finding_lifecycle")["findings"] if f["finding_id"] == fid)


def test_finding_lifecycle_carries_review_unit_id_and_target(tmp_path: Path) -> None:
    """FindingCreated 带 review_unit_id + target → reducer 物化进条目 (折叠分组键不丢)."""
    store = ProjectionStore(tmp_path / "graph")
    fid = "F-RU-1"
    target = {"artifact": "src/towow/x.py", "location": "src/towow/x.py:42"}
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {
                "finding_id": fid,
                "severity": "major",
                "lifecycle_state": "created",
                "description": "leak",
                "detection_method": "model_review",
                "review_unit_id": "RU-abc",
                "target": target,
            },
            1,
        )
    )
    node = _node(store, fid)
    assert node["review_unit_id"] == "RU-abc"
    assert node["target"] == target


def test_findings_for_review_unit_groups_by_unit(tmp_path: Path) -> None:
    """ProjectionStore.findings_for_review_unit(uid) 返回该 review-unit 的全部 finding (verdict 折叠输入)."""
    store = ProjectionStore(tmp_path / "graph")
    for i, ru in enumerate(["RU-1", "RU-1", "RU-2"], start=1):
        store._apply(
            _record(
                EventType.FINDING_CREATED,
                {
                    "finding_id": f"F-{i}",
                    "severity": "minor",
                    "lifecycle_state": "created",
                    "description": "d",
                    "detection_method": "model_review",
                    "review_unit_id": ru,
                },
                i,
            )
        )
    ru1 = {f["finding_id"] for f in store.findings_for_review_unit("RU-1")}
    ru2 = {f["finding_id"] for f in store.findings_for_review_unit("RU-2")}
    assert ru1 == {"F-1", "F-2"}
    assert ru2 == {"F-3"}
    assert store.findings_for_review_unit("RU-missing") == []


def test_inv_b1_1_every_finding_created_has_projection_entry(tmp_path: Path) -> None:
    """INV-B1-1: 任一 FindingCreated 在 finding_lifecycle 必有条目且 lifecycle_state 合法 (不蒸发)."""
    store = ProjectionStore(tmp_path / "graph")
    created_ids: list[str] = []
    for i in range(5):
        fid = f"F-EV-{i}"
        store._apply(
            _record(
                EventType.FINDING_CREATED,
                {
                    "finding_id": fid,
                    "severity": "major",
                    "lifecycle_state": "created",
                    "description": "d",
                    "detection_method": "ad_hoc",
                },
                i + 1,
            )
        )
        created_ids.append(fid)
    # 部分推进到后续态 (条目仍必须在, 直到 terminal 后也保留终态条目)
    store._apply(
        _record(EventType.FINDING_VERIFIED, {"finding_id": "F-EV-0", "verification_state": "confirmed_real"}, 10)
    )
    store._apply(_record(EventType.FINDING_RESOLVED, {"finding_id": "F-EV-1", "resolution": "fixed"}, 11))

    findings = {f["finding_id"]: f for f in store.read("finding_lifecycle")["findings"]}
    for fid in created_ids:
        assert fid in findings, f"INV-B1-1 违反: FindingCreated {fid} 在投影蒸发"
        assert findings[fid]["lifecycle_state"] in _VALID_LIFECYCLE


def test_review_unit_id_falls_back_to_session_id_for_review_finding(tmp_path: Path) -> None:
    """无显式 review_unit_id 的 model_review finding → reducer 回退 provenance.session_id (poka-yoke)."""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {
                "finding_id": "F-SESS",
                "severity": "major",
                "lifecycle_state": "created",
                "description": "d",
                "detection_method": "manual_review",
                "source": {"type": "model_review", "agent_id": "m15.review"},
            },
            1,
            session_id="sess-review-99",
        )
    )
    assert _node(store, "F-SESS")["review_unit_id"] == "sess-review-99"


def test_non_review_finding_does_not_borrow_session_as_review_unit(tmp_path: Path) -> None:
    """系统/execution finding (无 model_review source) 不把 provenance.session_id 误当 review_unit_id."""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {
                "finding_id": "F-SYS",
                "severity": "minor",
                "lifecycle_state": "created",
                "description": "system self-check",
                "detection_method": "ad_hoc",
            },
            1,
            session_id="sess-exec-77",
        )
    )
    assert _node(store, "F-SYS")["review_unit_id"] is None


def test_out_of_order_created_does_not_clobber_review_unit_id(tmp_path: Path) -> None:
    """洞A 回归: VERIFIED 先到定了 review_unit_id, 后到的无键 CREATED 不得覆盖成 None."""
    store = ProjectionStore(tmp_path / "graph")
    # VERIFIED 先到 (model_review, 带 session) → 节点 review_unit_id = session
    store._apply(
        _record(
            EventType.FINDING_VERIFIED,
            {"finding_id": "F-OOO", "verification_state": "confirmed_real",
             "source": {"type": "model_review", "agent_id": "m15.review"}},
            1,
            session_id="sess-review-OOO",
        )
    )
    assert _node(store, "F-OOO")["review_unit_id"] == "sess-review-OOO"
    # CREATED 后到, 本条既无显式 review_unit_id 又无 source → 派生 None, 但不得覆盖已有值
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {"finding_id": "F-OOO", "severity": "major", "lifecycle_state": "created",
             "description": "d", "detection_method": "ad_hoc"},
            2,
        )
    )
    assert _node(store, "F-OOO")["review_unit_id"] == "sess-review-OOO"


def test_findings_for_review_unit_rejects_none_key(tmp_path: Path) -> None:
    """洞B 回归: review_unit_id=None 不把系统/execution 侧 None 键 finding 一锅端."""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {"finding_id": "F-NONE", "severity": "minor", "lifecycle_state": "created",
             "description": "d", "detection_method": "ad_hoc"},
            1,
        )
    )
    assert _node(store, "F-NONE")["review_unit_id"] is None
    assert store.findings_for_review_unit(None) == []  # type: ignore[arg-type]
    assert store.findings_for_review_unit("") == []


# ── T-FIX-B2-03: 跨会话 resolve 不腐蚀原 review_unit_id ────────────────────────


def test_cross_session_resolve_does_not_clobber_origin_review_unit_id(tmp_path: Path) -> None:
    """T-FIX-B2-03: FindingCreated 在 review 会话 A 定 review_unit_id=A; 独立 fix_after 会话 B
    (prov.session_id=B) 的 FindingResolved 经 reducer 后, 节点 review_unit_id 必须仍是 A
    (resolve 不覆盖已定分组键, 跨会话不漂移)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.FINDING_CREATED,
            {"finding_id": "F-XS", "severity": "major", "lifecycle_state": "created",
             "description": "d", "detection_method": "manual_review",
             "source": {"type": "model_review", "agent_id": "m15.review"},
             "review_unit_id": "sess-review-A"},
            1,
            session_id="sess-review-A",
        )
    )
    # 跨会话 resolve (prov B), 但 T-FIX-B2-03 修后 payload 显式带 review_unit_id=A。
    store._apply(
        _record(
            EventType.FINDING_RESOLVED,
            {"finding_id": "F-XS", "resolution": "confirmed_and_accepted",
             "review_unit_id": "sess-review-A"},
            2,
            session_id="sess-review-B",
        )
    )
    node = _node(store, "F-XS")
    assert node["lifecycle_state"] == "resolved"
    assert node["review_unit_id"] == "sess-review-A", "跨会话 resolve 不得把分组键漂成 B"


def test_out_of_order_resolve_reads_explicit_review_unit_id(tmp_path: Path) -> None:
    """乱序保护: FindingResolved 先到 (节点尚不存在) 且 payload 显式带 review_unit_id=A →
    reducer 据显式字段建节点 review_unit_id=A (prov.session_id=B 不被误当分组键)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _record(
            EventType.FINDING_RESOLVED,
            {"finding_id": "F-OOR", "resolution": "confirmed_and_accepted",
             "source": {"type": "model_review", "agent_id": "m15.review"},
             "review_unit_id": "sess-review-A"},
            1,
            session_id="sess-review-B",
        )
    )
    assert _node(store, "F-OOR")["review_unit_id"] == "sess-review-A"
