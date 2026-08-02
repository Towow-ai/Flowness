"""闭合门结构性 fail-closed 守卫的钉死测试 (anti-fake-done, closure-evidence-verification-gate@v1)。

concept goal_completion_condition 2 要求每条 fail-closed 拒绝分支都有测试覆盖。本文件补两条此前零覆盖的
结构守卫 (finding f-closure-gate-rejection-path-not-affirmative-untested):

  - closure_task_id_missing: TaskNodeClosed.after_state 缺 task_id → 无法核验关闭, 拒 (顶层守卫,
    先于任何 reason 分派)。
  - closure_closed_by_missing: retired 分支 superseded_by 为可解析 finding 但缺 closed_by →
    独立性无对照锚, 拒。

直接测 check_closure_evidence_verification 纯函数 (构造 EventIntent + 空 EventIndex), 精确断 failure_reason。
"""

from __future__ import annotations

import uuid
from typing import Any

from towow.l0.commit_gate.closure_evidence_check import check_closure_evidence_verification
from towow.l0.event_log.index import EventIndex
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

_TASK = "T-GUARD-X"
_PLAN = "plan-guard"
_CLOSER = "sess-closer-guard"


def _closed_intent(after: dict[str, Any]) -> EventIntent:
    """把任意 after_state 包成一条扁平 TaskNodeClosed intent (subjects 用 task_id 兜底占位)。"""
    task_id = after.get("task_id")
    subj_id = task_id if isinstance(task_id, str) and task_id else "T-PLACEHOLDER"
    return EventIntent(
        local_intent_id=f"tnc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_NODE_CLOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": "task",
            "transition_type": "modified",
            "after_state": after,
        },
        provenance_hint=Provenance(
            actor_type="agent_session", actor_id="m13-planner",
            skill_id="M-1.3", session_id=_CLOSER,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=subj_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _empty_index() -> tuple[list[EventRecord], EventIndex]:
    return [], EventIndex([])


def test_task_id_missing_rejected() -> None:
    """after_state 无 task_id → closure_task_id_missing (顶层守卫, 先于 reason 分派)。"""
    records, index = _empty_index()
    after = {
        "plan_id": _PLAN,
        "reason": "done_elsewhere",
        "superseded_by": {"ref_type": "finding", "ref_id": "f-whatever"},
        "verification_verdict_ref": "evt-whatever",
        "closed_by": _CLOSER,
    }
    res = check_closure_evidence_verification([_closed_intent(after)], index, records)
    assert res.passed is False
    assert res.applicable is True
    assert res.failure_reason == "closure_task_id_missing", res.failure_reason


def test_task_id_empty_string_rejected() -> None:
    """task_id="" (空串) 同样被顶层守卫拒 (isinstance str 但 falsy)。"""
    records, index = _empty_index()
    after = {
        "task_id": "",
        "plan_id": _PLAN,
        "reason": "done_elsewhere",
        "superseded_by": {"ref_type": "finding", "ref_id": "f-whatever"},
    }
    res = check_closure_evidence_verification([_closed_intent(after)], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_task_id_missing", res.failure_reason


def test_retired_missing_closed_by_rejected() -> None:
    """retired 分支: superseded_by 为可解析形态的 finding, 但 after_state 缺 closed_by →
    独立性无对照锚 closure_closed_by_missing (该守卫在 superseded_by 结构检查通过后、
    premise-false 证据链核验之前触发, 故不需 seed 真 finding)。"""
    records, index = _empty_index()
    after = {
        "task_id": _TASK,
        "plan_id": _PLAN,
        "reason": "retired",
        "superseded_by": {"ref_type": "finding", "ref_id": "f-premise-false-x"},
        # 无 closed_by
    }
    res = check_closure_evidence_verification([_closed_intent(after)], index, records)
    assert res.passed is False
    assert res.applicable is True
    assert res.failure_reason == "closure_closed_by_missing", res.failure_reason


def test_retired_empty_closed_by_rejected() -> None:
    """closed_by="" (空串) 同样被 retired 守卫拒。"""
    records, index = _empty_index()
    after = {
        "task_id": _TASK,
        "plan_id": _PLAN,
        "reason": "retired",
        "superseded_by": {"ref_type": "finding", "ref_id": "f-premise-false-x"},
        "closed_by": "",
    }
    res = check_closure_evidence_verification([_closed_intent(after)], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_closed_by_missing", res.failure_reason
