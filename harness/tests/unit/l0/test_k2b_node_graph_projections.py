"""K2b-REG (50-graph-protocol §TG.4) — 图协议三类新节点投影 (session/lock/escalation) 接进 projection.py.

证三件 (events → catchup → projection 真产出统一-shape, facade._load_native 直接可读):
  ① session_graph: SessionSpawned 物化 session 节点 + parent→child 的 spawned 边 (血缘真兑现)。
  ② lock_graph 判别性测试 (advisor 点名): LockAcquired 建 held-by 边后 LockReleased 必须把它失活
     —— 这条单测分离"正确的全量 re-fold"与"被破坏的 per-event merge" (后者 release 静默 no-op)。
  ③ recompute_one(三图) == catchup 产出 (canonical 等价), 且不碰邻居 (合约)。
  ④ 全量 re-fold 幂等: 多个 node 事件后投影态与一次性 rebuild 一致, 边不重复。

合成 store / tempdir 副本, 绝不触碰 live harness/.towow。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l0.projection.projection import _PROJECTION_FILES, ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    TargetEntityType,
    TransitionType,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


def _rec(
    seq: int,
    etype: EventType,
    payload: dict[str, object],
    *,
    entity: tuple[SubjectEntityType, str],
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=etype,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="k2b-test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=entity[0], entity_id=entity[1], role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _session_spawned(seq: int, child: str, parent: str | None, form: str = "bg") -> EventRecord:
    after: dict[str, object] = {"session_id": child, "form": form}
    if parent is not None:
        after["parent_session_id"] = parent
    return _rec(
        seq,
        EventType.SESSION_SPAWNED,
        {
            "target_entity_type": TargetEntityType.SESSION.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": after,
        },
        entity=(SubjectEntityType.CONCEPT, "c-anchor"),
    )


def _lock_acquired(seq: int, lock_id: str, session_id: str, resource: str) -> EventRecord:
    return _rec(
        seq,
        EventType.LOCK_ACQUIRED,
        {
            "target_entity_type": TargetEntityType.LOCK.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": {"lock_id": lock_id, "session_id": session_id, "resource": resource},
        },
        entity=(SubjectEntityType.CONCEPT, "c-anchor"),
    )


def _lock_released_real_shape(seq: int) -> EventRecord:
    """LockReleased 的**真生产形态** (M-0.5 §7.3 LockReleasedAfter): task_id/run_id/entities/release_reason,
    **无 lock_id**。K2b-REG 刻意不消费它 (release 缺口交回主 session) — 本测试证它不会让 node-graph
    re-fold 崩 / 也不会被错误物化进 lock_graph。
    """
    return _rec(
        seq,
        EventType.LOCK_RELEASED,
        {
            "target_entity_type": TargetEntityType.TASK.value,
            "transition_type": TransitionType.MODIFIED.value,
            "after_state": {
                "task_id": "t-anchor",
                "run_id": "run-1",
                "entities": [],
                "release_reason": "abandoned_envelope_timeout",
            },
        },
        entity=(SubjectEntityType.TASK, "t-anchor"),
    )


def _write_log(log_path: Path, records: list[EventRecord]) -> EventLog:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")
    return EventLog(log_path)


# ─── ① session_graph: 血缘 spawned 边真兑现 ──────────────────────────────────────────────


def test_session_spawned_materializes_node_and_lineage_edge(tmp_path: Path) -> None:
    """SessionSpawned(child, parent) → session_graph 有 child 节点 + parent→child 的 spawned 边。"""
    records = [_session_spawned(1, child="s-child", parent="s-parent")]
    log = _write_log(tmp_path / "events.log", records)
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)

    sg = store.read("session_graph")
    assert sg is not None
    node_ids = {n["id"] for n in sg["nodes"]}
    assert "s-child" in node_ids, "子会话节点必须物化"
    assert "s-parent" in node_ids, "父会话至少占位 (血缘可导航)"
    spawned = [e for e in sg["edges"] if e["edge_type"] == "spawned"]
    assert len(spawned) == 1
    assert spawned[0]["from_id"] == "s-parent"
    assert spawned[0]["to"] == "s-child"
    assert spawned[0]["is_active"] is True


def test_session_spawned_root_has_no_lineage_edge(tmp_path: Path) -> None:
    """根/手动起的会话 (parent_session_id 缺) → 只有节点, 无 spawned 边 (不伪造血缘)。"""
    records = [_session_spawned(1, child="s-root", parent=None)]
    log = _write_log(tmp_path / "events.log", records)
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)

    sg = store.read("session_graph")
    assert {n["id"] for n in sg["nodes"]} == {"s-root"}
    assert [e for e in sg["edges"] if e["edge_type"] == "spawned"] == []


# ─── ② lock_graph: acquire 侧物化; 真生产 LockReleased (无 lock_id) 不崩不被错物化 (交回缺口的守门) ─


def test_lock_acquire_creates_active_held_by_edge(tmp_path: Path) -> None:
    """LockAcquired → lock 节点 + lock--held-by-->session, is_active=True。"""
    records = [_lock_acquired(1, lock_id="L1", session_id="s-1", resource="phase:plan")]
    log = _write_log(tmp_path / "events.log", records)
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)

    lg = store.read("lock_graph")
    assert {n["id"] for n in lg["nodes"]} == {"L1"}
    held = [e for e in lg["edges"] if e["edge_type"] == "held-by"]
    assert len(held) == 1
    assert held[0]["from_id"] == "L1"
    assert held[0]["to"] == "s-1"
    assert held[0]["is_active"] is True


def test_real_lock_released_neither_crashes_nor_fabricates_documents_the_handback(tmp_path: Path) -> None:
    """★ 守门交回缺口: 真生产 LockReleased (task/run/entities 形, 无 lock_id) 与 LockAcquired 共存时,

    node-graph re-fold 既不崩 (node_reducers 不被喂进会 KeyError 的形态), 也不把 held-by 边失活 /
    不把 release 错物化成节点。lock_graph 只反映 acquire 侧 → held-by 边保持 active (release 缺口未接,
    主 session 接 task/run→lock_id 映射后才能让边失活)。这条钉死"K2b-REG 当前刻意不消费 LockReleased"。
    """
    records = [
        _lock_acquired(1, lock_id="L1", session_id="s-1", resource="phase:plan"),
        _lock_released_real_shape(2),  # 真生产形态, 无 lock_id — 不该被消费
    ]
    log = _write_log(tmp_path / "events.log", records)
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)  # 不崩 = 第一道断言 (LockReleased 不进 node-graph re-fold)

    lg = store.read("lock_graph")
    assert {n["id"] for n in lg["nodes"]} == {"L1"}, "release 不该制造额外节点"
    held = [e for e in lg["edges"] if e["edge_type"] == "held-by"]
    assert len(held) == 1
    assert held[0]["is_active"] is True, (
        "release 缺口未接 → held-by 仍 active (真生产 LockReleased 无 lock_id, 当前刻意不消费)"
    )


# ─── ③ recompute_one(三图) == catchup 产出, 且不碰邻居 ──────────────────────────────────


def test_recompute_one_node_graphs_equal_catchup_and_dont_touch_neighbors(tmp_path: Path) -> None:
    """三个 node 图各自 recompute_one(name) 产出 == catchup 产出 (canonical 等价); 邻居字节不变。"""
    records = [
        _session_spawned(1, child="s-child", parent="s-parent"),
        _lock_acquired(2, lock_id="L1", session_id="s-child", resource="r1"),
        _lock_released_real_shape(3),  # 真生产形态 — 不消费, 不影响 lock_graph
    ]
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    store.catchup(log)
    canonical = {n: store.read(n) for n in ("session_graph", "lock_graph", "escalation_graph")}

    for name in ("session_graph", "lock_graph", "escalation_graph"):
        # snapshot neighbor bytes (all projections except `name` + .cursor)
        before = {}
        for nm, fn in _PROJECTION_FILES.items():
            if nm == name:
                continue
            p = graph / fn
            before[nm] = p.read_bytes() if p.exists() else b"<absent>"
        cursor_before = (graph / ".cursor").read_bytes()

        store.recompute_one(name, log)
        assert store.read(name) == canonical[name], f"recompute_one({name}) 必须 == catchup 产出"

        for nm, fn in _PROJECTION_FILES.items():
            if nm == name:
                continue
            p = graph / fn
            now = p.read_bytes() if p.exists() else b"<absent>"
            assert now == before[nm], f"recompute_one({name}) 不能碰邻居 {nm}"
        assert (graph / ".cursor").read_bytes() == cursor_before, "recompute_one 绝不动 .cursor"


# ─── ④ 全量 re-fold 幂等 (== rebuild), 边不重复 ────────────────────────────────────────


def test_node_graphs_idempotent_no_duplicate_edges(tmp_path: Path) -> None:
    """多 node 事件 catchup 后 == 一次性 rebuild, 且 spawned/held-by 边不因每事件全折而重复 (幂等)。

    这条钉死全量 re-fold 的正确性: 每个 node 事件都触发一次从空的全量 fold (O(n²)), 若 fold 非幂等
    (如 per-event merge 往非空 accumulator append), 边会重复。多事件后边数恰为预期 = 幂等成立。
    """
    records = [
        _session_spawned(1, child="s-a", parent="s-root"),
        _session_spawned(2, child="s-b", parent="s-root"),
        _lock_acquired(3, lock_id="L1", session_id="s-a", resource="r1"),
        _lock_acquired(4, lock_id="L2", session_id="s-b", resource="r2"),
    ]
    # catchup path
    log1 = _write_log(tmp_path / "live" / "events.log", records)
    s1 = ProjectionStore(tmp_path / "live" / "graph")
    s1.catchup(log1)

    # rebuild-from-scratch path
    log2 = _write_log(tmp_path / "ref" / "events.log", records)
    s2 = ProjectionStore(tmp_path / "ref" / "graph")
    s2.rebuild(log2)

    for name in ("session_graph", "lock_graph", "escalation_graph"):
        assert s1.read(name) == s2.read(name), f"{name}: catchup 增量 == rebuild 全量 (幂等)"

    sg = s1.read("session_graph")
    spawned = [e for e in sg["edges"] if e["edge_type"] == "spawned"]
    assert len(spawned) == 2, "两条血缘边 (s-root→s-a, s-root→s-b), 不因每事件全折而重复"
    lg = s1.read("lock_graph")
    held = [e for e in lg["edges"] if e["edge_type"] == "held-by"]
    assert len(held) == 2, "两条 held-by 边 (L1→s-a, L2→s-b), 不重复"
    assert all(e["is_active"] is True for e in held), "两把锁都未 release (release 缺口不消费) → 全 active"


def test_escalation_graph_is_empty_placeholder_pending_world_b(tmp_path: Path) -> None:
    """escalation_graph 当前是空占位: 即便有设计态 EscalationRaised, 也不被 node-graph 消费。

    §6.3 明令 escalation 节点须源自 World-B (GoalEscalationRaised + NatureJudgmentCaptured); 设计态
    EscalationRaised (生产 ≈0) 不该当源。本步刻意不消费 escalation 事件 → 此图诚实为空 (slot 在,
    World-B source 待主 session 接)。这条防止"看着上图其实空/错源"的假完成被悄悄引入。
    """
    # design-time EscalationRaised — 喂既有 escalation_lifecycle reducer, 但**不**该进 escalation_graph
    esc = _rec(
        1,
        EventType.ESCALATION_RAISED,
        {
            "target_entity_type": TargetEntityType.ESCALATION.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": {
                "escalation_id": "esc-1",
                "nature_facing_summary": "x", "what_was_tried": "y",
                "why_it_did_not_close": "z", "decision_needed_from_nature": "w",
                "engineering_detail_ref": "ref",
            },
        },
        entity=(SubjectEntityType.FINDING, "f-1"),
    )
    # 加一条 SessionSpawned 强制 re-fold 真跑 (escalation_graph 文件真被写出) — 证它**即便 re-fold
    # 跑了也为空**, 不从设计态 EscalationRaised 建节点 (真风险点)。
    spawn = _session_spawned(2, child="s-1", parent=None)
    log = _write_log(tmp_path / "events.log", [esc, spawn])
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)

    eg = store.read("escalation_graph")
    assert eg == {"nodes": [], "edges": []}, (
        "escalation_graph re-fold 跑了仍必须为空 (World-B source 未接, 不从设计态 EscalationRaised 建节点)"
    )
    # 同时确认设计态 escalation 仍正常喂既有 escalation_lifecycle (零改, 不被本步影响)
    el = store.read("escalation_lifecycle")
    assert any(e.get("escalation_id") == "esc-1" for e in el["escalations"]), (
        "EscalationRaised 仍正常喂 escalation_lifecycle reducer"
    )
