"""闭合门 verdict 检查方向反转修复的钉死测试 (anti-fake-done, done_elsewhere 分支)。

修前门方向反了: 要求 verdict.subjects 含被关 task + verdict.provenance.session_id 非空 —— 真
audit-fork verdict (subjects=[CONCEPT commit_gate], session_id=None) 永远过不了, 只有手工伪造
的 task-subject + 假 session id 才能满足。修后: verdict 必须是 sanctioned 独立审计 fork 产
(provenance actor commit_gate/m05_audit_fork/skill=audit) + 可追溯到一条锚定被关 task、且审的
envelope_event_id == superseded_by.ref_id 的真 AuditTriggered。

直接测 check_closure_evidence_verification 纯函数 (构造 EventRecord + EventIndex), 精确断 failure_reason。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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

_TASK = "T-CLOSED-Y"
_PLAN = "plan-x"
_FINDING = "f-supersedes-y"
_CLOSER = "sess-closer"


def _prov(
    *, actor_type: str = "commit_gate", actor_id: str = "m05_audit_fork",
    skill_id: str | None = "audit", session_id: str | None = None,
) -> Provenance:
    return Provenance(
        actor_type=actor_type, actor_id=actor_id, skill_id=skill_id, session_id=session_id,
    )


def _rec(
    *, event_type: EventType, payload: dict, subjects: list[Subject], prov: Provenance, seq: int,
    event_id: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=event_id or f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload=payload,
        provenance=prov,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _finding_created(fid: str = _FINDING, seq: int = 1) -> EventRecord:
    return _rec(
        event_type=EventType.FINDING_CREATED,
        payload={"finding_id": fid, "severity": "major"},
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=fid, role=SubjectRole.PRIMARY)],
        prov=_prov(actor_type="agent_session", actor_id="m15", skill_id="M-1.5", session_id="sess-rev"),
        seq=seq,
    )


def _audit_triggered(
    *, task_id: str = _TASK, envelope_ref: str = _FINDING, seq: int = 2,
    event_id: str = "evt-at-closure",
) -> EventRecord:
    """closure-scoped AuditTriggered: 锚定被关 task + 审的 envelope_event_id = 交付物 ref_id。"""
    return _rec(
        event_type=EventType.AUDIT_TRIGGERED,
        payload={
            "judgment_type": "audit_trigger",
            "after_state": {
                "trigger_reason": "closure_verification",
                "envelope_event_id": envelope_ref,
                "audit_scope": f"closure of {task_id}",
            },
            "confidence": None,
        },
        subjects=[
            Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY),
        ],
        prov=_prov(actor_id="m05_audit_fork"),
        seq=seq,
        event_id=event_id,
    )


def _audit_verdict(
    *, trigger_event_id: str = "evt-at-closure", verdict: str = "pass", seq: int = 3,
    event_id: str = "evt-av-closure", sanctioned: bool = True,
) -> EventRecord:
    """真 audit-fork verdict 形态: subjects=[CONCEPT commit_gate], session_id=None,
    after_state.trigger_event_id → AuditTriggered。sanctioned=False 模拟 35 手写伪造。"""
    if sanctioned:
        prov = _prov(actor_type="commit_gate", actor_id="m05_audit_fork", skill_id="audit")
    else:
        prov = _prov(
            actor_type="agent_session", actor_id="m14-execution-session",
            skill_id="M-1.4", session_id="sess-forger",
        )
    return _rec(
        event_type=EventType.AUDIT_VERDICT_RECEIVED,
        payload={
            "judgment_type": "audit_verdict",
            "after_state": {
                "audit_id": f"audit-{uuid.uuid4().hex[:6]}",
                "trigger_event_id": trigger_event_id,
                "verdict": verdict,
            },
            "confidence": 0.9,
            "evidence": [{"source_type": "event", "source_id": "x", "relevance": "y"}],
        },
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="commit_gate", role=SubjectRole.PRIMARY),
        ],
        prov=prov,
        seq=seq,
        event_id=event_id,
    )


def _closed_intent(
    *, task_id: str = _TASK, ref_id: str = _FINDING, verdict_ref: str = "evt-av-closure",
    closed_by: str = _CLOSER, stub_rewrap: bool = False,
) -> EventIntent:
    after = {
        "task_id": task_id,
        "plan_id": _PLAN,
        "reason": "done_elsewhere",
        "superseded_by": {"ref_type": "finding", "ref_id": ref_id},
        "verification_verdict_ref": verdict_ref,
        "closed_by": closed_by,
    }
    if stub_rewrap:
        event_type = EventType.NODE_TOUCHED
        payload = {
            "kind": "TaskNodeClosed",
            "target_entity_id": "stub-TaskNodeClosed",
            "stub_original_payload": {"after_state": after},
        }
    else:
        event_type = EventType.TASK_NODE_CLOSED
        payload = {
            "target_entity_type": "task",
            "transition_type": "modified",
            "after_state": after,
        }
    return EventIntent(
        local_intent_id=f"tnc-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance_hint=_prov(
            actor_type="agent_session", actor_id="m13-planner", skill_id="M-1.3", session_id=closed_by,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _committed(*recs: EventRecord) -> tuple[list[EventRecord], EventIndex]:
    records = list(recs)
    return records, EventIndex(records)


def test_real_audit_fork_verdict_passes() -> None:
    """真形态 verdict (sanctioned, subjects=[CONCEPT commit_gate], session_id=None) + 锚 task 的
    AuditTriggered(envelope_event_id=ref_id) → 过门 (修前的死角: 真 verdict 曾必然过不了)。"""
    records, index = _committed(_finding_created(), _audit_triggered(), _audit_verdict())
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is True, res.failure_reason
    assert res.applicable is True


def test_handwritten_nonsanctioned_verdict_rejected() -> None:
    """verdict actor 非 m05_audit_fork (模拟 35 手写非 sanctioned verdict) → 拒。"""
    records, index = _committed(
        _finding_created(), _audit_triggered(), _audit_verdict(sanctioned=False),
    )
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_verdict_not_sanctioned_audit_fork"


def test_verdict_untraceable_to_audit_triggered_rejected() -> None:
    """verdict.after_state.trigger_event_id 指向不存在 / 非 AuditTriggered → 不可追溯 拒。"""
    # trigger_event_id 指向一个不存在的 id (未 seed AuditTriggered)
    verdict = _audit_verdict(trigger_event_id="evt-nonexistent-trigger")
    records, index = _committed(_finding_created(), verdict)
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_verdict_no_traceable_audit_triggered"


def test_audit_triggered_anchors_other_task_rejected() -> None:
    """AuditTriggered 的 TASK subject 是别的 task (模拟审 envelope 而非 closure) → 拒。"""
    trig = _audit_triggered(task_id="T-SOME-OTHER-TASK")
    records, index = _committed(_finding_created(), trig, _audit_verdict())
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_audit_triggered_does_not_anchor_task"


def test_audit_triggered_artifact_mismatch_rejected() -> None:
    """AuditTriggered.envelope_event_id != superseded_by.ref_id → 审的不是被声明交付物 拒。"""
    trig = _audit_triggered(envelope_ref="f-some-other-artifact")
    records, index = _committed(_finding_created(), trig, _audit_verdict())
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_audit_triggered_artifact_mismatch"


def test_no_session_id_no_longer_required() -> None:
    """真 verdict provenance.session_id=None 不再被拒 (旧第3步删除的回归钉死)。"""
    # 显式 session_id=None 的 sanctioned verdict
    verdict = _audit_verdict()
    assert verdict.provenance.session_id is None
    records, index = _committed(_finding_created(), _audit_triggered(), verdict)
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is True, res.failure_reason


def test_sanctioned_verdict_but_fail_rejected() -> None:
    """pass-vs-FAIL 方向钉死 (本文件此前唯独漏的方向, finding
    f-closure-gate-rejection-path-not-affirmative-untested): 与 test_real_audit_fork_verdict_passes
    完全同构 —— sanctioned audit-fork verdict + 锚 task 的 AuditTriggered + envelope-match, 唯一差别是
    verdict=fail 而非 pass。affirmative 检查 (closure_evidence_check.py:455-464) 先于 sanctioned/traceable,
    故此路径必须以 closure_verdict_not_affirmative 被拒。历史 bug
    f-closure-gate-verdict-check-inverted-and-write-boundary-forgeable 的复发面: 若 verdict 方向再被反转,
    FAIL 会被误当正向放行, 本测试即断裂。"""
    records, index = _committed(
        _finding_created(), _audit_triggered(), _audit_verdict(verdict="fail"),
    )
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_verdict_not_affirmative", res.failure_reason


def test_finding_verified_not_verified_rejected() -> None:
    """affirmative 的另一路径 (FindingVerified verification_state != verified) 同样被拒。
    verification_verdict_ref 指向一条 verification_state=rejected 的 FindingVerified →
    _verdict_is_affirmative 读顶层 verification_state != 'verified' → closure_verdict_not_affirmative。"""
    finding_verified = _rec(
        event_type=EventType.FINDING_VERIFIED,
        payload={"finding_id": _FINDING, "verification_state": "rejected"},
        subjects=[
            Subject(entity_type=SubjectEntityType.FINDING, entity_id=_FINDING, role=SubjectRole.PRIMARY),
        ],
        prov=_prov(actor_type="agent_session", actor_id="m15", skill_id="verify-step", session_id="sess-v"),
        seq=4,
        event_id="evt-fv-notverified",
    )
    records, index = _committed(_finding_created(), finding_verified)
    res = check_closure_evidence_verification(
        [_closed_intent(verdict_ref="evt-fv-notverified")], index, records,
    )
    assert res.passed is False
    assert res.failure_reason == "closure_verdict_not_affirmative", res.failure_reason


def test_stub_rewrapped_verdict_still_face_same_gate() -> None:
    """NodeTouched(kind=TaskNodeClosed) 包裹的关闭仍走 unwrap + 同四连判 (过门形态)。"""
    records, index = _committed(_finding_created(), _audit_triggered(), _audit_verdict())
    res = check_closure_evidence_verification([_closed_intent(stub_rewrap=True)], index, records)
    assert res.passed is True, res.failure_reason
    # 反面: stub 包裹但 verdict 非 sanctioned → 同样被拒 (门不因 stub 跳过)
    records2, index2 = _committed(
        _finding_created(), _audit_triggered(), _audit_verdict(sanctioned=False),
    )
    res2 = check_closure_evidence_verification([_closed_intent(stub_rewrap=True)], index2, records2)
    assert res2.passed is False
    assert res2.failure_reason == "closure_verdict_not_sanctioned_audit_fork"
