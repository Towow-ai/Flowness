"""live-target-execution-evidence@v1 · goal 收口门 goal.live_target_reconciled (承重).

钉住 §5 拒绝路径 (伪造只有沙盒证据的完成声明必须被真实拒掉):
  - test 4 (旗舰): goal completion_condition 含 observable、账本 0 条证据 → 拒 completion。
  - test 5 (撤边逃逸): task 图达终态但 observable 事件仍 0 → 门仍拒 (证明门看账本证据不看 task 图)。
  - test 6 (零误伤): 无 observable 的 goal → not_applicable 放行。
  - passed 路径: 账本有 ≥N 条落在本 goal task 集内的真实事件 → 放行。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from towow.l0.commit_gate.live_target_reconcile_check import (
    check_live_target_reconciled,
    reconcile_goal_live_targets,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

_GSID = "gs-goal-1"
_BRIEF_ID = "brief-reflow"
_PLAN = "plan-reflow"
_EXEC_TASK = "T-REFLOW-BATCH-EXECUTE"
_FROZEN_KIND = "StrandedBatchManifestFrozen"


def _rec(
    event_type: EventType,
    payload: dict,
    seq: int,
    *,
    task_id: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test", task_id=task_id),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _ledger_evidence(event_kind: str = _FROZEN_KIND, min_occ: int = 1) -> dict:
    return {
        "condition": f"账本存在 ≥{min_occ} 条 {event_kind}",
        "verification_method": "ledger_event",
        "expected_result": "clean freeze occurred",
        "event_kind": event_kind,
        "min_occurrences": min_occ,
        "scope_binding": "this_goal",
    }


def _observable(obs_id: str = "obs-freeze") -> dict:
    return {
        "observable_id": obs_id,
        "description": "清单被冻结并执行",
        "evidence": _ledger_evidence(),
    }


def _goal_started(seq: int) -> EventRecord:
    return _rec(
        EventType.GOAL_SESSION_STARTED,
        {"after_state": {"goal_session_id": _GSID, "brief_event_id": _BRIEF_ID}},
        seq,
    )


def _brief(seq: int, *, observables: list[dict] | None = None) -> EventRecord:
    return _rec(
        EventType.INTERVIEW_BRIEF_PUBLISHED,
        {
            "brief_id": _BRIEF_ID,
            "goal_completion_condition": "清单被冻结并执行",
            "live_target_observables": observables if observables is not None else [],
        },
        seq,
    )


def _consensus(seq: int) -> EventRecord:
    return _rec(
        EventType.ENGINEERING_CONSENSUS_FREEZED,
        {"after_state": {"plan_id": _PLAN, "brief_event_id": _BRIEF_ID}},
        seq,
    )


def _task_node(task_id: str, seq: int) -> EventRecord:
    return _rec(
        EventType.TASK_NODE_CREATED,
        {"after_state": {"task_id": task_id, "task_type": "code_change", "plan_id": _PLAN}},
        seq,
    )


def _frozen_event(seq: int, *, task_id: str | None = None) -> EventRecord:
    return _rec(
        EventType.NODE_TOUCHED,
        {"kind": _FROZEN_KIND, "stub_original_payload": {"kind": _FROZEN_KIND, "batch_id": "b1"}},
        seq,
        task_id=task_id,
    )


def _base_goal_records(*, with_observable: bool) -> list[EventRecord]:
    obs = [_observable()] if with_observable else []
    return [
        _goal_started(1),
        _brief(2, observables=obs),
        _consensus(3),
        _task_node(_EXEC_TASK, 4),
    ]


# ── §5 test 4 (旗舰): observable 在场、账本 0 条冻结事件 → 拒 ──


def test_flagship_zero_evidence_rejected() -> None:
    records = _base_goal_records(with_observable=True)  # 无任何 StrandedBatchManifestFrozen
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.applicable is True
    assert result.passed is False
    assert result.failure_reason == "goal_live_target_unsatisfied"


# ── §5 test 5 (撤边逃逸): task 达终态但 observable 事件仍 0 → 仍拒 ──


def test_edge_removal_escape_still_rejected() -> None:
    """撤除执行 task 的边让 task 图达终态, 但 observable 事件仍 0 → 门仍拒 (看账本不看 task 图)。"""
    records = _base_goal_records(with_observable=True)
    # 模拟"task 图达终态": 加一条 EXECUTE task 的 TaskRunCompleted success (撤边后 sibling 代表终态)
    records.append(_rec(
        EventType.TASK_RUN_COMPLETED,
        {"after_state": {"task_id": _EXEC_TASK, "outcome": "success"}},
        5,
    ))
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.passed is False  # task 达终态骗不过门 — 账本仍 0 条 StrandedBatchManifestFrozen


# ── passed 路径: 账本有真实事件且落在本 goal task 集内 → 放行 ──


def test_real_attributed_event_satisfies_observable() -> None:
    """正向闭环 (debt-82d4effd33a0 approach a 修后): 真实处置事件带 task 归属 (freeze_manifest
    的 attribution_task_id → provenance.task_id ∈ 本 goal task 集) → this_goal scope 计入 → 放行。

    这是 emitting 侧带 provenance 后的真实形态 (见 test_stranded_batch_manifest.
    test_freeze_carries_attribution_provenance 证 freeze_manifest 真 emit task_id), 不再是合成。
    """
    records = _base_goal_records(with_observable=True)
    records.append(_frozen_event(5, task_id=_EXEC_TASK))  # freeze_manifest(attribution_task_id=...) 的产物形态
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.applicable is True
    assert result.passed is True


def test_unattributed_event_does_not_satisfy_this_goal() -> None:
    """设计正确性 (非 trap): 冻结事件不带 task 归属 (旧行为 / 调用方没传 attribution) → this_goal
    scope (task_id ∈ 本 goal task 集) 不匹配 → 不计入 → 拒。

    这不是缺陷而是 this_goal 的语义要求: 一条无法归属到本 goal 的事件, 不能替本 goal 的完成背书
    (否则退化成 global, 别 goal 的旧同类事件会 false-accept)。真处置要收口 → 调用方必须带 attribution
    (approach a)。fail-closed 安全方向。"""
    records = _base_goal_records(with_observable=True)
    records.append(_frozen_event(5, task_id=None))  # 无归属: 不能算作本 goal 的证据
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.passed is False


def test_real_evidence_other_goal_task_still_rejected() -> None:
    """真实冻结事件但 provenance.task_id 不在本 goal task 集 → this_goal scope 不计 → 拒 (false-accept 防线)。"""
    records = _base_goal_records(with_observable=True)
    records.append(_frozen_event(5, task_id="T-SOME-OTHER-GOAL-TASK"))
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.passed is False


# ── §5 test 6 (零误伤): 无 observable 的 goal → not_applicable 放行 ──


def test_no_observable_zero_false_positive() -> None:
    records = _base_goal_records(with_observable=False)
    result = reconcile_goal_live_targets(_GSID, records)
    assert result.applicable is False
    assert result.passed is True


def test_goal_without_brief_link_not_applicable() -> None:
    """无法定位 brief 的 goal → not_applicable (零误伤, 门不冒充能验它)。"""
    started = _rec(
        EventType.GOAL_SESSION_STARTED,
        {"after_state": {"goal_session_id": _GSID, "brief_event_id": "brief-missing"}},
        1,
    )
    result = reconcile_goal_live_targets(_GSID, [started])
    assert result.applicable is False
    assert result.passed is True


# ── check_live_target_reconciled (commit gate intent 入口) ──


def _terminate_intent(reason: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.GOAL_SESSION_TERMINATED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": {"goal_session_id": _GSID, "termination_reason": reason}},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="orch"),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=_GSID, role=SubjectRole.PRIMARY)],
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        schema_version="1.0.0",
    )


def _completion_intent() -> EventIntent:
    return _terminate_intent("completion")


def test_gate_check_completion_zero_evidence_rejected() -> None:
    intents = [_completion_intent()]
    committed = _base_goal_records(with_observable=True)
    result = check_live_target_reconciled(intents, committed)
    assert result.passed is False
    assert result.failure_reason == "goal_live_target_unsatisfied"


def test_gate_check_completion_with_evidence_passes() -> None:
    intents = [_completion_intent()]
    committed = _base_goal_records(with_observable=True)
    committed.append(_frozen_event(5, task_id=_EXEC_TASK))
    result = check_live_target_reconciled(intents, committed)
    assert result.passed is True


def test_gate_check_non_completion_not_applicable() -> None:
    """escalation 收口不是"做成真实处理"的声称 → 本门 not_applicable 放行。"""
    intent = _terminate_intent("escalation")
    committed = _base_goal_records(with_observable=True)  # 有 observable 但账本 0 证据
    result = check_live_target_reconciled([intent], committed)
    assert result.applicable is False  # escalation 不走本门
    assert result.passed is True
