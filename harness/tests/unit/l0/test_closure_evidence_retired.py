"""退役 (reason=retired) 终态的闭合门证据契约钉死测试 + reason fail-closed-on-unknown。

retired 证据链: superseded_by 必为 finding, 该 finding 须 (a) finding_kind=premise_false 的
FindingCreated 且 subjects 锚定被退役 task (kind 挡"代码 bug finding 冒充", task-subject 挡"跨 task
复用一个已 verified 的 premise_false finding 去关无关 task"), (b) 有一个独立 (session != closer)
verification_state=verified 的 FindingVerified。直接测 check_closure_evidence_verification 纯函数。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from towow.l0.commit_gate.closure_evidence_check import check_closure_evidence_verification
from towow.l0.event_log.index import EventIndex
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    TaskClosureReason,
    TaskClosureRefType,
)
from towow.schemas.event_intent import EventIntent, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.payloads.state_transition import TaskNodeClosedAfter

_TASK = "T-RETIRE-Y"
_PLAN = "plan-x"
_FINDING = "f-premise-false-y"
_CLOSER = "sess-closer"
_VERIFIER = "sess-independent-verifier"


def _prov(*, actor_type: str = "agent_session", actor_id: str = "m15", session_id: str | None = "sess-x") -> Provenance:
    return Provenance(
        actor_type=actor_type, actor_id=actor_id,
        skill_id="M-1.5" if actor_type == "agent_session" else None,
        session_id=session_id,
    )


def _rec(*, event_type: EventType, payload: dict, subjects: list[Subject], prov: Provenance, seq: int) -> EventRecord:
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
        subjects=subjects,
        schema_version="1.0.0",
    )


def _premise_false_finding(
    *, fid: str = _FINDING, anchor_task: str = _TASK, kind: str = "premise_false", seq: int = 1,
) -> EventRecord:
    subjects = [Subject(entity_type=SubjectEntityType.FINDING, entity_id=fid, role=SubjectRole.PRIMARY)]
    if anchor_task:
        subjects.append(Subject(entity_type=SubjectEntityType.TASK, entity_id=anchor_task, role=SubjectRole.AFFECTED))
    return _rec(
        event_type=EventType.FINDING_CREATED,
        payload={"finding_id": fid, "severity": "major", "finding_kind": kind, "description": "GOAL 前提为假"},
        subjects=subjects,
        prov=_prov(session_id="sess-authoring"),
        seq=seq,
    )


def _finding_verified(
    *, fid: str = _FINDING, state: str = "verified", session_id: str = _VERIFIER, seq: int = 2,
) -> EventRecord:
    return _rec(
        event_type=EventType.FINDING_VERIFIED,
        payload={
            "finding_id": fid,
            "lifecycle_state": "verified",
            "verification_method": "verify-step 独立证伪确认前提为假",
            "false_positive_eliminated": True,
            "verification_state": state,
        },
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=fid, role=SubjectRole.PRIMARY)],
        prov=_prov(session_id=session_id),
        seq=seq,
    )


def _retired_close_intent(
    *, task_id: str = _TASK, ref_type: str = "finding", ref_id: str = _FINDING, closed_by: str = _CLOSER,
    reason: str = "retired",
) -> EventIntent:
    return EventIntent(
        local_intent_id=f"tnc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_NODE_CLOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": "task",
            "transition_type": "modified",
            "after_state": {
                "task_id": task_id,
                "plan_id": _PLAN,
                "reason": reason,
                "superseded_by": {"ref_type": ref_type, "ref_id": ref_id},
                "verification_verdict_ref": None,
                "closed_by": closed_by,
            },
        },
        provenance_hint=_prov(actor_type="agent_session", actor_id="m13", session_id=closed_by),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _committed(*recs: EventRecord) -> tuple[list[EventRecord], EventIndex]:
    records = list(recs)
    return records, EventIndex(records)


def test_gate_rejects_self_service_retired_pending_owner_confirm() -> None:
    """R2 封 (红队实证 + owner 红线): premise_false FindingCreated + FindingVerified + session!=closer
    全是可自造的自报串, 单人三条 CLI 就能把不想做的 task 关成退役。退役一个冻结 task 的前提判定是
    Class A owner 决定 (owner: 只有我说的算, autopilot 撬不开)。owner-confirm 机制 (组件6) 未就位前,
    agent 自助退役一律 fail-closed —— 即使 premise_false + verified 齐全也拒。"""
    records, index = _committed(_premise_false_finding(), _finding_verified())
    res = check_closure_evidence_verification([_retired_close_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_owner_confirm"
    assert res.applicable is True


def test_gate_rejects_retired_cross_task_reuse() -> None:
    """premise_false+verified finding 锚定 task_A, 却拿去关 task_B → 拒 (最关键回归: 挡跨 task 复用)。"""
    records, index = _committed(
        _premise_false_finding(anchor_task="T-SOME-OTHER-A"), _finding_verified(),
    )
    res = check_closure_evidence_verification([_retired_close_intent(task_id="T-RETIRE-B")], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_finding_not_premise_false"


def test_gate_rejects_retired_finding_kind_not_premise_false() -> None:
    records, index = _committed(
        _premise_false_finding(kind="adjacent_code_issue"), _finding_verified(),
    )
    res = check_closure_evidence_verification([_retired_close_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_finding_not_premise_false"


def test_gate_rejects_retired_without_verify_step() -> None:
    records, index = _committed(_premise_false_finding())  # 无 FindingVerified
    res = check_closure_evidence_verification([_retired_close_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_not_verify_confirmed"


def test_gate_rejects_retired_verify_not_verified_state() -> None:
    """FindingVerified 存在但 verification_state != verified (rejected_false_positive) → 拒。"""
    records, index = _committed(
        _premise_false_finding(), _finding_verified(state="rejected_false_positive"),
    )
    res = check_closure_evidence_verification([_retired_close_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_not_verify_confirmed"


def test_gate_rejects_retired_self_verified() -> None:
    """FindingVerified.session == closer → 退役者自证前提为假, 违独立性 → 拒。"""
    records, index = _committed(
        _premise_false_finding(), _finding_verified(session_id=_CLOSER),
    )
    res = check_closure_evidence_verification([_retired_close_intent(closed_by=_CLOSER)], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_retired_self_verified"


def test_gate_rejects_retired_with_commit_ref() -> None:
    """reason=retired 但 superseded_by.ref_type=commit → 门层拒 (retired 只认 finding)。"""
    records, index = _committed(_premise_false_finding(), _finding_verified())
    res = check_closure_evidence_verification(
        [_retired_close_intent(ref_type="commit", ref_id="deadbeef")], index, records,
    )
    assert res.passed is False
    assert res.failure_reason == "closure_retired_requires_finding"


def test_gate_rejects_unknown_reason() -> None:
    """reason='banana' → fail-closed 拒 (anti-fake-done 门 unknown reason 绝不 fall-through)。"""
    records, index = _committed(_premise_false_finding(), _finding_verified())
    res = check_closure_evidence_verification([_retired_close_intent(reason="banana")], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_unknown_reason"


# ── schema TaskNodeClosedAfter reason-evidence 契约 ──


def _after_kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": _TASK, "plan_id": _PLAN, "closed_by": _CLOSER,
        "superseded_by": {"ref_type": TaskClosureRefType.FINDING, "ref_id": _FINDING},
    }
    base.update(over)
    return base


def test_tasknodeclosed_reason_evidence_contract() -> None:
    # retired + verdict=None + superseded_by=finding → 合法
    ok = TaskNodeClosedAfter(**_after_kwargs(reason=TaskClosureReason.RETIRED, verification_verdict_ref=None))
    assert ok.reason is TaskClosureReason.RETIRED
    # done_elsewhere + verdict=None → ValueError
    with pytest.raises(ValidationError, match="verification_verdict_ref"):
        TaskNodeClosedAfter(**_after_kwargs(reason=TaskClosureReason.DONE_ELSEWHERE, verification_verdict_ref=None))
    # retired + superseded_by=commit → ValueError
    with pytest.raises(ValidationError, match="finding"):
        TaskNodeClosedAfter(**_after_kwargs(
            reason=TaskClosureReason.RETIRED, verification_verdict_ref=None,
            superseded_by={"ref_type": TaskClosureRefType.COMMIT, "ref_id": "deadbeef"},
        ))
    # done_elsewhere + verdict 非空 → 合法
    ok2 = TaskNodeClosedAfter(**_after_kwargs(
        reason=TaskClosureReason.DONE_ELSEWHERE, verification_verdict_ref="evt-verdict-x",
    ))
    assert ok2.verification_verdict_ref == "evt-verdict-x"
