"""T-RMD-S4-DEADLETTER (M23-F2 / rc-unify-escalation-event-source-materialize-projection) —
World-B escalation 源 (GoalEscalationRaised + owner NJ) 收进 World-A 同 schema (escalation_lifecycle)。

治 rc-two-escalation-worlds-no-inbox-materialized: 久卡检测器 surface 的是 GoalEscalationRaised
(生产产量>0, 含卡 ~20 天的 tier0 owner 问题), 而 escalation_lifecycle 投影只认 native EscalationRaised
(生产产量 0) → 真需 owner 的问题躺在没有收件箱的世界里。本组证 reducer 也消费 World-B 源, 物化进
**同一个** escalation_lifecycle 投影 (向后兼容, 不动 native World-A 那套)。

GoalEscalationRaised / NatureJudgmentCaptured 以 stub-rewrap NodeTouched (payload.kind=<Type>,
stub_original_payload={...}) 落账 (orchestrator._build_orch_nodetouched 范式)。reducer 据此解包物化。
读真 EscalationLifecycleEntry.model_validate (fail-closed, 物化节点必符 canonical schema)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
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
from towow.schemas.projection_state import EscalationLifecycleEntry

if TYPE_CHECKING:
    from pathlib import Path


def _node_touched(
    kind: str, stub: dict[str, object], seq: int, *, actor_id: str = "f11-orchestrator-polling",
) -> EventRecord:
    """stub-rewrap NodeTouched (World-B 源生产形态: payload.kind + stub_original_payload)。"""
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": str(stub.get("decision_id") or "wb-esc"),
            "touch_type": "write",
            "kind": kind,
            "stub_original_payload": stub,
        },
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id=actor_id),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _goal_escalation(
    seq: int,
    *,
    escalation_kind: str | None = None,
    owner_gate_task_id: str | None = None,
    dead_letter_entry_id: str | None = None,
    dead_letter_source_type: str | None = None,
    dead_letter_source_ref: str | None = None,
) -> EventRecord:
    stub: dict[str, object] = {
        "kind": "GoalEscalationRaised",
        "decision_id": f"esc-{uuid.uuid4().hex[:8]}",
        "goal_session_id": f"gsid-{uuid.uuid4().hex[:6]}",
        "owner_question": "需要你拍板",
        "blocking_scope": "session_only",
        "awaiting_response": True,
    }
    if escalation_kind is not None:
        stub["escalation_kind"] = escalation_kind
    if owner_gate_task_id is not None:
        stub["owner_gate_task_id"] = owner_gate_task_id
    if dead_letter_entry_id is not None:
        stub["dead_letter_entry_id"] = dead_letter_entry_id
    if dead_letter_source_type is not None:
        stub["dead_letter_source_type"] = dead_letter_source_type
    if dead_letter_source_ref is not None:
        stub["dead_letter_source_ref"] = dead_letter_source_ref
    return _node_touched("GoalEscalationRaised", stub, seq)


def _nj_answer(responds_to: str, seq: int) -> EventRecord:
    return _node_touched(
        "NatureJudgmentCaptured",
        {
            "kind": "NatureJudgmentCaptured",
            "decision_id": f"nj-{uuid.uuid4().hex[:8]}",
            "responds_to_escalation_event_id": responds_to,
            "judgment": "owner 决定",
        },
        seq,
    )


def _escs(store: ProjectionStore) -> dict[str, dict]:
    proj = store.read("escalation_lifecycle") or {"escalations": []}
    return {e["escalation_id"]: e for e in proj.get("escalations", [])}


# ─── World-B 物化 ─────────────────────────────────────────────────────────────────────


def test_goal_escalation_materializes_into_world_a_projection(tmp_path: Path) -> None:
    """★ GoalEscalationRaised (World-B) 物化进 escalation_lifecycle (World-A 同 schema), 键=event_id。"""
    store = ProjectionStore(tmp_path / "graph")
    rec = _goal_escalation(1, escalation_kind="goal")
    store._apply(rec)

    escs = _escs(store)
    assert rec.event_id in escs, "GoalEscalationRaised 必须物化进 escalation_lifecycle (owner 收件箱)"
    node = escs[rec.event_id]
    assert node["lifecycle_state"] == "raised"
    assert node["escalation_kind"] == "goal"
    # 物化节点必符 canonical schema (fail-closed)。
    EscalationLifecycleEntry.model_validate(node, strict=False)


def test_escalation_kind_distinguished_not_placeholder(tmp_path: Path) -> None:
    """★ escalation_kind 按签名精确区分 dead_letter / owner_gate / goal, 不填 placeholder (advisor 点名)。"""
    store = ProjectionStore(tmp_path / "graph")
    dead = _goal_escalation(1, dead_letter_entry_id="dl-1")  # 无显式 kind, 靠签名推 dead_letter
    gate = _goal_escalation(2, owner_gate_task_id="T-OG-1")  # 推 owner_gate
    goal = _goal_escalation(3)  # 无任何签名 → 兜底 goal
    explicit = _goal_escalation(4, escalation_kind="dead_letter", owner_gate_task_id="T-X")  # 显式优先

    for r in (dead, gate, goal, explicit):
        store._apply(r)
    escs = _escs(store)
    assert escs[dead.event_id]["escalation_kind"] == "dead_letter"
    assert escs[gate.event_id]["escalation_kind"] == "owner_gate"
    assert escs[goal.event_id]["escalation_kind"] == "goal"
    assert escs[explicit.event_id]["escalation_kind"] == "dead_letter", "显式 escalation_kind 优先"


def test_dead_letter_source_routed_to_related_ids(tmp_path: Path) -> None:
    """dead-letter 源对象按 type 路由进 related_task_ids / related_finding_ids (owner 反查卡的是谁)。"""
    store = ProjectionStore(tmp_path / "graph")
    task_src = _goal_escalation(
        1, dead_letter_entry_id="dl-t", dead_letter_source_type="task", dead_letter_source_ref="T-99",
    )
    finding_src = _goal_escalation(
        2, dead_letter_entry_id="dl-f", dead_letter_source_type="finding", dead_letter_source_ref="f-99",
    )
    store._apply(task_src)
    store._apply(finding_src)
    escs = _escs(store)
    assert escs[task_src.event_id]["related_task_ids"] == ["T-99"]
    assert escs[finding_src.event_id]["related_finding_ids"] == ["f-99"]


# ─── owner NJ answer 闭环 resolve ─────────────────────────────────────────────────────


def test_nj_answer_resolves_matching_world_b_escalation(tmp_path: Path) -> None:
    """★ owner NJ (responds_to_escalation_event_id) → 对应 World-B escalation 闭环 resolved。"""
    store = ProjectionStore(tmp_path / "graph")
    raised = _goal_escalation(1, escalation_kind="dead_letter")
    store._apply(raised)
    assert _escs(store)[raised.event_id]["lifecycle_state"] == "raised"

    nj = _nj_answer(raised.event_id, 2)
    store._apply(nj)
    node = _escs(store)[raised.event_id]
    assert node["lifecycle_state"] == "resolved", "owner 答复后必须闭环 resolved (不再永久 pending)"
    assert node["nature_judgment_event_id"] == nj.event_id
    assert node.get("resolved_at")


def test_nj_unmatched_does_not_create_phantom(tmp_path: Path) -> None:
    """NJ 指向未物化的 escalation → 不造幽灵条目 (诚实, 不伪造闭环; native World-A 走自己的 resolve)。"""
    store = ProjectionStore(tmp_path / "graph")
    nj = _nj_answer("evt-nonexistent-escalation", 1)
    store._apply(nj)
    assert _escs(store) == {}, "NJ 指向不存在的 escalation 不该凭空造条目"


def test_nj_without_responds_to_is_noop(tmp_path: Path) -> None:
    """NJ 不带 responds_to_escalation_event_id (普通判断, 非 escalation 答复) → reducer 不动。"""
    store = ProjectionStore(tmp_path / "graph")
    raised = _goal_escalation(1, escalation_kind="goal")
    store._apply(raised)
    plain_nj = _node_touched(
        "NatureJudgmentCaptured", {"kind": "NatureJudgmentCaptured", "judgment": "无关判断"}, 2,
    )
    store._apply(plain_nj)
    assert _escs(store)[raised.event_id]["lifecycle_state"] == "raised", "无 responds_to 的 NJ 不该改任何 escalation"


# ─── 幂等 + 从零 catchup + World-A/B 共存 ──────────────────────────────────────────────


def test_world_b_idempotent_replay(tmp_path: Path) -> None:
    """同 GoalEscalationRaised 重放 → 同状态 (幂等, full rebuild 一致)。"""
    store = ProjectionStore(tmp_path / "graph")
    raised = _goal_escalation(1, escalation_kind="dead_letter")
    store._apply(raised)
    store._apply(raised)
    escs = _escs(store)
    assert len(escs) == 1, "同 escalation 重放不该产生重复条目"
    assert escs[raised.event_id]["lifecycle_state"] == "raised"


def test_world_b_via_real_catchup_from_zero(tmp_path: Path) -> None:
    """★ 从零 catchup 真账本 → World-B 源经 _apply 分派被消费 (证 _apply 门真路由, 非只直调 reducer)。

    这是 advisor 点名的 reducer 正确性钉法: temp dir 从零 catchup, 不碰 live 投影。
    """
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    log_path = towow / "events.log"
    raised = _goal_escalation(1, dead_letter_entry_id="dl-c", escalation_kind="dead_letter")
    nj = _nj_answer(raised.event_id, 2)
    log_path.write_text(
        raised.model_dump_json() + "\n" + nj.model_dump_json() + "\n", encoding="utf-8",
    )
    event_log = EventLog(log_path)

    store = ProjectionStore(towow / "graph")
    store.catchup(event_log)
    node = _escs(store)[raised.event_id]
    assert node["lifecycle_state"] == "resolved", "catchup 必须经 _apply 门消费 World-B 源 (raised→resolved)"


def test_recompute_one_replays_world_b(tmp_path: Path) -> None:
    """recompute_one('escalation_lifecycle') 也回放 World-B 源 (与 _apply 双分支门一致)。"""
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    log_path = towow / "events.log"
    raised = _goal_escalation(1, escalation_kind="dead_letter")
    log_path.write_text(raised.model_dump_json() + "\n", encoding="utf-8")
    event_log = EventLog(log_path)

    store = ProjectionStore(towow / "graph")
    store.catchup(event_log)
    assert raised.event_id in _escs(store)
    # 外科式单投影重建 (保留 cursor + 邻居) 仍含 World-B 条目。
    store.recompute_one("escalation_lifecycle", event_log)
    assert raised.event_id in _escs(store), "recompute_one 必须回放 World-B 源"
