"""闭合合约机器复算锚 — f-closure-gate-verdict-check-inverted-and-write-boundary-forgeable
判据 1/2 (done_elsewhere 分支 verdict provenance 核验)。

历史根因: 闭合门 verdict 检查方向反了 —— 曾要求 verdict.subjects 含被关 task + verdict.session_id
非空。但真 audit-fork verdict (subjects=[CONCEPT commit_gate]、session_id=None) 二者皆无 → 真验证
必被拒, 门只能被"伪造出 task-subject + 假 session"的手写 verdict 满足。修后: 门改由结构保证独立性 ——
verdict 必须是 sanctioned 独立审计 fork 产 (provenance actor commit_gate/m05_audit_fork/skill=audit),
task 锚定改看它可追溯到的 AuditTriggered.subjects (非 verdict.subjects)。

本文件是该 finding closure_contract 的两条 test 判据的机器复算锚 (与 test_closure_evidence_verdict_
direction.py 的更广谱覆盖同源, 此处按合约点名的 selector 名钉死"真 verdict 过门 / 伪造 verdict 被拒"
这对承重方向)。直接测 check_closure_evidence_verification 纯函数。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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

_TASK = "T-CLOSED-Y"
_PLAN = "plan-x"
_FINDING = "f-supersedes-y"
_CLOSER = "sess-closer"
_TRIG_ID = "evt-at-closure"
_VERDICT_ID = "evt-av-closure"


def _prov(
    *, actor_type: str = "commit_gate", actor_id: str = "m05_audit_fork",
    skill_id: str | None = "audit", session_id: str | None = None,
) -> Provenance:
    return Provenance(
        actor_type=actor_type, actor_id=actor_id, skill_id=skill_id, session_id=session_id,
    )


def _rec(
    *, event_type: EventType, payload: dict[str, Any], subjects: list[Subject], prov: Provenance, seq: int,
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


def _audit_triggered(*, task_id: str = _TASK, envelope_ref: str = _FINDING, seq: int = 2) -> EventRecord:
    """closure-scoped AuditTriggered: subjects 锚被关 task + 审的 envelope_event_id = 交付物 ref_id。"""
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
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        prov=_prov(actor_id="m05_audit_fork"),
        seq=seq,
        event_id=_TRIG_ID,
    )


def _audit_verdict(*, sanctioned: bool = True, verdict: str = "pass", seq: int = 3) -> EventRecord:
    """真 audit-fork verdict 形态: subjects=[CONCEPT commit_gate], provenance.session_id=None,
    after_state.trigger_event_id → AuditTriggered。sanctioned=False 模拟手写非 sanctioned 伪造。"""
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
                "trigger_event_id": _TRIG_ID,
                "verdict": verdict,
            },
            "confidence": 0.9,
            "evidence": [{"source_type": "event", "source_id": "x", "relevance": "y"}],
        },
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="commit_gate", role=SubjectRole.PRIMARY)],
        prov=prov,
        seq=seq,
        event_id=_VERDICT_ID,
    )


def _closed_intent() -> EventIntent:
    after = {
        "task_id": _TASK,
        "plan_id": _PLAN,
        "reason": "done_elsewhere",
        "superseded_by": {"ref_type": "finding", "ref_id": _FINDING},
        "verification_verdict_ref": _VERDICT_ID,
        "closed_by": _CLOSER,
    }
    return EventIntent(
        local_intent_id=f"tnc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_NODE_CLOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"target_entity_type": "task", "transition_type": "modified", "after_state": after},
        provenance_hint=_prov(
            actor_type="agent_session", actor_id="m13-planner", skill_id="M-1.3", session_id=_CLOSER,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=_TASK, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _committed(*recs: EventRecord) -> tuple[list[EventRecord], EventIndex]:
    records = list(recs)
    return records, EventIndex(records)


def test_genuine_audit_fork_verdict_accepted() -> None:
    """判据1: 真 audit-fork 形态 verdict (actor_id=m05_audit_fork、subjects=[commit_gate]、
    session_id=None、有可追溯锚被关 task 的 AuditTriggered) 过门 —— 修前的死角 (真 verdict 必被拒)。"""
    records, index = _committed(_finding_created(), _audit_triggered(), _audit_verdict())
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is True, res.failure_reason
    assert res.applicable is True


def test_forged_verdict_rejected() -> None:
    """判据2: 手写非 sanctioned verdict (actor 非 m05_audit_fork, 模拟 35 手写伪造) → fail-closed 拒。"""
    records, index = _committed(_finding_created(), _audit_triggered(), _audit_verdict(sanctioned=False))
    res = check_closure_evidence_verification([_closed_intent()], index, records)
    assert res.passed is False
    assert res.failure_reason == "closure_verdict_not_sanctioned_audit_fork"
