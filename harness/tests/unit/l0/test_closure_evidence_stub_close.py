"""f-stub-rewrap-close-bypasses-closure-evidence-gate-1 (critical, anti-fake-done) 门侧半:
closure-evidence-verification-gate 对 stub-rewrap NodeTouched(kind=TaskNodeClosed) 闭合施加与真
TaskNodeClosed **同一**三重门 —— 不因 event_type=NodeTouched 就跳过 (否则 stub 闭合逃过验证)。

修前: _closed_after_states 用原始 intent.event_type 过滤 → NodeTouched 信封被跳过 → 门 no-op
(applicable=False, passed=True), stub 闭合无需任何独立 verdict 即"过门"。
修后: _closed_after_states 也 unwrap stub 闭合 → 门对其施加 superseded_by/verdict/独立性 三重核验;
悬空 verdict/superseded 的 stub 闭合被 fail-closed 拒。

镜像 test_lnd04_review_verdict_gate 的 EventIntent/EventIndex 构造范式。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from towow.l0.commit_gate.closure_evidence_check import (
    _closed_after_states,
    check_closure_evidence_verification,
)
from towow.l0.event_log.index import EventIndex
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance


def _prov() -> Provenance:
    return Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test")


def _finding_created_rec(fid: str, seq: int) -> EventRecord:
    """一个真实的 FindingCreated (subjects 锚 finding=fid), 让 superseded_by(finding) 可解析。"""
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload={"finding_id": fid, "severity": "major"},
        provenance=_prov(),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=fid, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _stub_close_intent(
    task_id: str,
    *,
    superseded_finding: str,
    verdict_ref: str,
    closed_by: str = "owner-decision-final-tail",
) -> EventIntent:
    """stub-rewrap 闭合 intent: NodeTouched 信封 + kind=TaskNodeClosed + 内层 stub_original_payload。"""
    return EventIntent(
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "kind": "TaskNodeClosed",
            "target_entity_id": "stub-TaskNodeClosed",
            "stub_original_payload": {
                "after_state": {
                    "task_id": task_id,
                    "plan_id": "plan-autopilot-turnon-remediation",
                    "reason": "done_elsewhere",
                    "superseded_by": {"ref_type": "finding", "ref_id": superseded_finding},
                    "verification_verdict_ref": verdict_ref,
                    "closed_by": closed_by,
                }
            },
        },
        provenance_hint=_prov(),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="cli-NodeTouched", role=SubjectRole.PRIMARY)
        ],
        schema_version="1.0.0",
    )


def test_closed_after_states_now_surfaces_stub_close() -> None:
    """修的核心: _closed_after_states 不再跳过 stub 闭合, 而是 unwrap 出它的 after_state 交门核验。"""
    intent = _stub_close_intent(
        "T-RMD-S6-SINGLELANE", superseded_finding="f-x", verdict_ref="evt-owner-decision-s6-seqf"
    )
    surfaced = _closed_after_states([intent])
    assert len(surfaced) == 1, "stub 闭合必须被 unwrap 出来交门 (修前会被跳过 → 空列表 → 门 no-op)"
    assert surfaced[0]["task_id"] == "T-RMD-S6-SINGLELANE"


def test_stub_close_dangling_verdict_rejected() -> None:
    """superseded_by 可解析、但 verdict_ref 悬空的 stub 闭合 → 门 fail-closed 拒 (verdict 不可解析)。

    正是 autopilot 点火 7 任务的实证形态: verification_verdict_ref=evt-owner-decision-* 不存在。
    """
    fid = "f-sub-forward-chain-never-closes"
    records = [_finding_created_rec(fid, seq=1)]
    index = EventIndex(records)
    intent = _stub_close_intent(
        "T-RMD-S6-SINGLELANE", superseded_finding=fid, verdict_ref="evt-owner-decision-s6-seqf"
    )
    res = check_closure_evidence_verification([intent], index, records)
    assert res.passed is False, "悬空 verdict 的 stub 闭合绝不能过门 (anti-fake-done)"
    assert res.applicable is True, "门必须认到这是一个闭合 (不再因 NodeTouched 跳过)"
    assert res.failure_reason == "closure_verdict_unresolvable"


def test_stub_close_dangling_superseded_rejected() -> None:
    """superseded_by 指向不存在 finding 的 stub 闭合 → 门在结构层就拒 (superseded 不可解析)。"""
    index = EventIndex([])
    intent = _stub_close_intent(
        "T-RMD-GOAL", superseded_finding="f-does-not-exist", verdict_ref="evt-owner-decision-goal"
    )
    res = check_closure_evidence_verification([intent], index, [])
    assert res.passed is False
    assert res.applicable is True
    assert res.failure_reason == "closure_superseded_by_unresolvable"


def _flat_commit_close_intent(task_id: str, *, commit_sha: str) -> EventIntent:
    """扁平 TaskNodeClosed intent, superseded_by=commit(commit_sha), 用于复现工位撞的 commit-ref 拒绝。"""
    return EventIntent(
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_NODE_CLOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "after_state": {
                "task_id": task_id,
                "reason": "done_elsewhere",
                "superseded_by": {"ref_type": "commit", "ref_id": commit_sha},
                "verification_verdict_ref": "evt-some-verdict",
                "closed_by": "closer-session",
            }
        },
        provenance_hint=_prov(),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)
        ],
        schema_version="1.0.0",
    )


def test_unresolvable_commit_ref_rejection_is_actionable() -> None:
    """f-closure-gate-canonical-sha-only-blocks-worktrees-20260718: commit-ref 不可解析时, 语义不变
    (仍 fail-closed 拒), 但拒绝证据须【可操作】—— 带 remediation 指出 canonical SHA / finding ref-type
    两条自助改法。正是 S2-M12/S4-DEADLETTER 等工位拿 git/工位 SHA 收口时撞的那一句。
    """
    index = EventIndex([])
    intent = _flat_commit_close_intent("T-RMD-S4-DEADLETTER", commit_sha="6bb8d219ffdeadbeef")
    res = check_closure_evidence_verification([intent], index, [])
    # 语义未变: 非 canonical 的 git/工位 SHA 仍被拒 (anti-fake-done 不放水)。
    assert res.passed is False
    assert res.applicable is True
    assert res.failure_reason == "closure_superseded_by_unresolvable"
    # 新增价值: 拒绝可操作 —— remediation 在场且点出 finding ref-type 这条自助逃生路。
    assert "remediation" in res.failure_evidence
    assert "finding" in res.failure_evidence["remediation"]
    assert "canonical" in res.failure_evidence["reason"]
