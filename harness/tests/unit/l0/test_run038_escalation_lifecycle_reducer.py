"""RUN-038 L0增强修复: escalation_lifecycle reducer (M-2.3 §6.1/§6.7).

spec ground-truth:
  - M-2.3 §6.7 论证选 B = escalation 独立 projection (≠ finding_lifecycle), 加给 M-0.2 §9 narrow patch。
  - M-2.3 §6.1 5 状态机: raised → triaged → (awaiting_nature 隐式) → resolved →
    post_resolved_learning_captured。
  - 喂入事件: EscalationRaised (M-1.6 §3.6) / EscalationTriaged (§3.2) / EscalationResolved (§3.3) /
    EscalationLearningCaptured (§3.4) / EscalationDecisionMade (§3.5)。
  - ★ 真 schema: EscalationRaisedPayload 所有字段嵌 after_state (EscalationRaisedAfter), 不是 flat。
    真生产者 towow fix escalate (cli/main.py) 也嵌 after_state 发出。Triaged/Resolved 同样
    after_state.lifecycle_state; learning/decision 是 semantic_judgment + after_state。

本组证 (改用真 schema 喂入, 非手搓 flat dict):
  - 喂 EscalationRaisedPayload(...).model_dump() 经 _apply → projection 真物化, 物化节点能被
    canonical EscalationLifecycleEntry.model_validate 通过 (必填 raised_at/raised_by_skill/
    escalation_kind 在位, lifecycle_state 是 enum, 无 forbidden 字段)。
  - lifecycle_state 随事件迁移 (raised→triaged→resolved→post_resolved_learning_captured)。
  - 同 escalation_id upsert (幂等), 多 escalation 独立。

负对照 test_raised_reads_after_state_not_top_level: 若 reducer 读顶层 (旧错) → after_state 嵌套
  的 finding_id/task_id 丢 → related_*_ids 不填 → 本测真挂 (验对合约非验自洽)。

诚实挂债: escalation_kind 是 EscalationLifecycleEntry 必填, 但 producer (towow fix escalate /
  EscalationRaisedAfter schema) 无该字段 → producer gap, reducer 侧占位 "unspecified" 兜底,
  不伪造。本测断言占位值, 待 producer 补 escalation_kind 后改读真值。

注: done_criteria 的端到端 (M-2.3 daemon 真产 triage/resolution 事件) 依赖 daemon — 不在本次
  daemon-independent scope。reducer 本体 + 真 schema 单测可做。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EscalationLifecycleState,
    EscalationResolution,
    EventCategory,
    EventType,
    EvidenceSourceType,
    ExpectedNatureAction,
    LearningType,
    SubjectEntityType,
    SubjectRole,
    TriageCategory,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.payloads.semantic_judgment import (
    EscalationLearningCapturedAfter,
    EscalationLearningCapturedPayload,
)
from towow.schemas.payloads.state_transition import (
    EscalationRaisedAfter,
    EscalationRaisedPayload,
    EscalationResolvedAfter,
    EscalationResolvedPayload,
    EscalationTriagedAfter,
    EscalationTriagedPayload,
    EvidenceItem,
)
from towow.schemas.projection_state import EscalationLifecycleEntry

if TYPE_CHECKING:
    from pathlib import Path


def _ev(
    event_type: EventType,
    category: EventCategory,
    payload: dict[str, object],
    seq: int,
    *,
    skill_id: str | None = None,
) -> EventRecord:
    # 真生产者 towow fix escalate 用 actor_type=agent_session + skill_id=M-1.6; agent_session
    # 要求 session_id + skill_id 同时在位 (ProvenanceHint invariant)。
    if skill_id is not None:
        prov = Provenance(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m16-fix-session",
            session_id=f"sess-{uuid.uuid4().hex[:8]}",
            skill_id=skill_id,
        )
    else:
        prov = Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test-m23")
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=category,
        payload=payload,
        provenance=prov,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            # escalation 不在 SubjectEntityType — 用 task 占位 (subjects 仅作索引, reducer 读 payload)。
            Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _raised(escalation_id: str, seq: int, *, skill_id: str | None = "M-1.6") -> EventRecord:
    """真 schema: EscalationRaisedPayload — 所有字段嵌 after_state (EscalationRaisedAfter)。"""
    payload = EscalationRaisedPayload(
        after_state=EscalationRaisedAfter(
            escalation_id=escalation_id,
            fix_id="fix-1",
            finding_id="find-1",
            task_id="t-1",
            nature_facing_summary="修不动 X 需拍板",
            what_was_tried="试了 A 和 B",
            why_it_did_not_close="两条路都需要产品取舍",
            decision_needed_from_nature="接受 workaround 还是重试",
            engineering_detail_ref="evt-eng-detail-1",
        ),
    ).model_dump()
    return _ev(EventType.ESCALATION_RAISED, EventCategory.STATE_TRANSITION, payload, seq, skill_id=skill_id)


def _triaged(escalation_id: str, seq: int) -> EventRecord:
    """真 schema: EscalationTriagedPayload — lifecycle_state 在 after_state。"""
    payload = EscalationTriagedPayload(
        after_state=EscalationTriagedAfter(
            triage_category=TriageCategory.TECHNICAL_BLOCKER.value,  # schema typed str
            triage_reasoning="多轮 fix 无 novelty",
            expected_nature_action=ExpectedNatureAction.REVIEW_EVIDENCE.value,  # schema typed str|None
            related_finding_ids=["find-1"],
            triage_time_lag_ms=1234,
        ),
    ).model_dump()
    payload["target_entity_id"] = escalation_id  # event-level id, 本仓库落 payload
    return _ev(EventType.ESCALATION_TRIAGED, EventCategory.STATE_TRANSITION, payload, seq)


def _resolved(escalation_id: str, seq: int, nj_event_id: str = "evt-nj-1") -> EventRecord:
    """真 schema: EscalationResolvedPayload — lifecycle_state + NJ 关联在 after_state。"""
    payload = EscalationResolvedPayload(
        after_state=EscalationResolvedAfter(
            resolution=EscalationResolution.NATURE_ACCEPTED_WORKAROUND.value,  # schema typed str
            nature_judgment_event_id=nj_event_id,
            resolution_explanation="Nature 接受当前 workaround",
            raised_to_resolved_duration_ms=99999,
        ),
    ).model_dump()
    payload["target_entity_id"] = escalation_id
    return _ev(EventType.ESCALATION_RESOLVED, EventCategory.STATE_TRANSITION, payload, seq)


def _learning(escalation_id: str, seq: int) -> EventRecord:
    """真 schema: EscalationLearningCapturedPayload — after_state.learning_type 等。"""
    payload = EscalationLearningCapturedPayload(
        after_state=EscalationLearningCapturedAfter(
            learning_type=LearningType.DETECTION_RULE_CANDIDATE,  # enum-typed field
            seed_for_event_id="evt-dr-seed",
            capturing_reasoning="该 escalation 模式可固化成 detection rule",
            nature_already_provided_feedback=False,
        ),
        confidence=0.8,
        evidence=[
            EvidenceItem(source_type=EvidenceSourceType.EVENT, source_id="evt-dr-seed", relevance="seed"),
        ],
    ).model_dump()
    payload["target_entity_id"] = escalation_id
    return _ev(EventType.ESCALATION_LEARNING_CAPTURED, EventCategory.SEMANTIC_JUDGMENT, payload, seq)


def _escs(store: ProjectionStore) -> dict[str, dict]:
    proj = store.read("escalation_lifecycle") or {"escalations": []}
    return {e["escalation_id"]: e for e in proj.get("escalations", [])}


def _entry(node: dict[str, object]) -> EscalationLifecycleEntry:
    """物化节点是 JSON-serializable (enum 落字符串值)。canonical schema 是 strict=True,
    用 strict=False 接受字符串 enum 值 (持久化形态), 但仍 enforce extra=forbid + 必填 + enum 成员。
    """
    return EscalationLifecycleEntry.model_validate(node, strict=False)


def test_raised_node_validates_against_canonical_entry(tmp_path: Path) -> None:
    """喂真 EscalationRaisedPayload → 物化节点能被 canonical EscalationLifecycleEntry 接受。

    改前 (读顶层 flat): 物化节点带 forbidden 字段 (fix_id/nature_facing_summary/... 顶层) +
    缺必填 raised_at/raised_by_skill/escalation_kind + lifecycle_state 裸 str → model_validate 必挂。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_raised("esc-X", 1))

    escs = _escs(store)
    assert "esc-X" in escs, "EscalationRaised 后 projection 真物化"
    ex = escs["esc-X"]
    # ★ 核心断言: 物化节点符合 canonical schema (能 model_validate)。
    entry = _entry(ex)
    assert entry.escalation_id == "esc-X"
    assert entry.lifecycle_state is EscalationLifecycleState.RAISED, "lifecycle_state 是 enum 非裸 str"
    assert entry.raised_at, "raised_at 从 ev.timestamp 派生, 非 None"
    assert entry.raised_by_skill == "M-1.6", "raised_by_skill 从 provenance.skill_id 派生"
    assert entry.escalation_kind, "escalation_kind 必填在位 (producer gap → 占位)"
    # finding_id/task_id 嵌 after_state → related_*_ids (读对 after_state 才有)。
    assert entry.related_finding_ids == ["find-1"], "after_state.finding_id → related_finding_ids"
    assert entry.related_task_ids == ["t-1"], "after_state.task_id → related_task_ids"


def test_raised_reads_after_state_not_top_level(tmp_path: Path) -> None:
    """负对照守门: 真 schema 把 escalation_id/finding_id/task_id 嵌 after_state。

    若 reducer 读顶层 (旧错): after_state 嵌套字段读不到 → related_finding_ids / related_task_ids
    全空 → 本断言挂。这是"验对合约"而非"验自洽"的硬守门。
    """
    store = ProjectionStore(tmp_path / "graph")
    raised = _raised("esc-AF", 1)
    # 确认喂入的 payload 真把字段嵌 after_state (不是 flat) —— 这是真 schema 形状。
    assert "escalation_id" not in raised.payload, "真 schema: escalation_id 不在 payload 顶层"
    assert raised.payload["after_state"]["escalation_id"] == "esc-AF"
    assert raised.payload["after_state"]["finding_id"] == "find-1"

    store._apply(raised)
    ex = _escs(store)["esc-AF"]
    # reducer 必须从 after_state 读到 finding_id/task_id 才能填 related_*; 读顶层会得空。
    assert ex.get("related_finding_ids") == ["find-1"], "读 after_state.finding_id 才有 (读顶层→空→挂)"
    assert ex.get("related_task_ids") == ["t-1"], "读 after_state.task_id 才有 (读顶层→空→挂)"


def test_escalation_lifecycle_materializes_full_state_machine(tmp_path: Path) -> None:
    """raised→triaged→resolved→post_resolved_learning_captured 全程物化, 每步符 canonical schema。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_raised("esc-X", 1))
    ex = _escs(store)["esc-X"]
    assert _entry(ex).lifecycle_state is EscalationLifecycleState.RAISED

    store._apply(_triaged("esc-X", 2))
    entry = _entry(_escs(store)["esc-X"])
    assert entry.lifecycle_state is EscalationLifecycleState.TRIAGED, "EscalationTriaged 迁 triaged"
    assert entry.triage_category == "technical_blocker", "triage 分类留存 (读 after_state)"
    assert entry.triaged_at, "triaged_at 从 ev.timestamp 派生"
    assert entry.related_finding_ids == ["find-1"]

    store._apply(_resolved("esc-X", 3, nj_event_id="evt-nj-77"))
    entry = _entry(_escs(store)["esc-X"])
    assert entry.lifecycle_state is EscalationLifecycleState.RESOLVED
    assert entry.resolution == "nature_accepted_workaround"
    assert entry.nature_judgment_event_id == "evt-nj-77", "§6.8 invariant: resolved 必关联 NJ event"
    assert entry.resolved_at

    store._apply(_learning("esc-X", 4))
    entry = _entry(_escs(store)["esc-X"])
    assert (
        entry.lifecycle_state is EscalationLifecycleState.POST_RESOLVED_LEARNING_CAPTURED
    ), "learning capture 是终态"
    assert entry.learning_type == "detection_rule_candidate"
    assert entry.seed_for_event_id == "evt-dr-seed"
    assert entry.learning_captured_at
    assert len(_escs(store)) == 1, "同 escalation_id upsert 非追加"


def test_decision_made_keeps_node_canonical(tmp_path: Path) -> None:
    """EscalationDecisionMade (semantic_judgment) 不破坏 canonical 节点 (§6.1: lifecycle 不由 decision 转移)。

    canonical EscalationLifecycleEntry 无 decided_state 字段 (extra=forbid) → reducer 不在 entry
    物化 decision 结论, 节点保持 schema-valid。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_raised("esc-D", 1))
    store._apply(
        _ev(
            EventType.ESCALATION_DECISION_MADE,
            EventCategory.SEMANTIC_JUDGMENT,
            {
                "target_entity_id": "esc-D",
                "judgment_type": "escalation_decision",
                "after_state": {
                    "decided_state": "resolved",
                    "decision_summary": "接受 workaround",
                    "decision_made_by": "nature",
                },
                "related_escalation_resolved_event_id": "evt-esc-res-1",
            },
            2,
        ),
    )
    ex = _escs(store)["esc-D"]
    entry = _entry(ex)
    # decision 不驱动 lifecycle_state (§6.1: 仍是 raised, 由 EscalationResolved 才转移)。
    assert entry.lifecycle_state is EscalationLifecycleState.RAISED
    assert "decided_state" not in ex, "canonical entry 无 decided_state — 不污染 (走 escalation_decision projection)"


def test_multiple_escalations_independent(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_raised("esc-A", 1))
    store._apply(_raised("esc-B", 2))
    store._apply(_triaged("esc-A", 3))

    escs = _escs(store)
    assert (
        _entry(escs["esc-A"]).lifecycle_state
        is EscalationLifecycleState.TRIAGED
    )
    assert (
        _entry(escs["esc-B"]).lifecycle_state
        is EscalationLifecycleState.RAISED
    ), "esc-B 不受 esc-A triage 影响"


def test_escalation_idempotent_replay(tmp_path: Path) -> None:
    """重放同事件序 → state 一致 (幂等, full rebuild 可重现)。"""
    store = ProjectionStore(tmp_path / "graph")
    evs = [_raised("esc-X", 1), _triaged("esc-X", 2), _resolved("esc-X", 3)]
    for e in evs:
        store._apply(e)
    for e in evs:  # 重放
        store._apply(e)
    escs = _escs(store)
    assert len(escs) == 1, "幂等: 重放不重复物化"
    assert (
        _entry(escs["esc-X"]).lifecycle_state
        is EscalationLifecycleState.RESOLVED
    )


def test_transition_before_raised_still_materializes(tmp_path: Path) -> None:
    """事件乱序到达 (triaged 先于 raised cold-replay 边界) — reducer 不丢, 建占位节点保持 canonical。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_triaged("esc-late", 1))
    escs = _escs(store)
    assert "esc-late" in escs, "transition 先到也物化 (不依赖 raised 先建节点)"
    entry = _entry(escs["esc-late"])
    assert entry.lifecycle_state is EscalationLifecycleState.TRIAGED
    # 占位节点仍符 canonical (必填 raised_at/raised_by_skill/escalation_kind 兜底在位)。
    assert entry.raised_at
    assert entry.raised_by_skill
    assert entry.escalation_kind
