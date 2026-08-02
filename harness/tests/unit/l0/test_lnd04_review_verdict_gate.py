"""T-LND-04 (INV-B1-3 verdict-proof-of-work): commit gate 对 REVIEW task 的 TaskRunCompleted
(success) 要求其 review-unit verdict=passed, 否则物理拒。

法则一: review 任务推进凭证是不可伪造的 verdict, 不是 TaskRunCompleted 本身、更非"测试绿"。
本测试钉住 check_review_verdict_gated_completion 的判定:
  - REVIEW task + verdict=failed → reject (review_verdict_not_passed)
  - REVIEW task + verdict=passed → pass
  - REVIEW task + 零 finding (干净 review) → pass
  - REVIEW task 无 review 会话 session_id → fail-closed (review_verdict_unprovable)
  - 非 REVIEW task (code_change) → skip (本门不管)
  - abort outcome → skip (合法终态)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from towow.l0.commit_gate.review_verdict_check import (
    check_review_verdict_gated_completion,
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

_SID = "sess-review-77"
_TASK = "T-REVIEW-1"


def _prov(session_id: str | None = None) -> Provenance:
    if session_id:
        return Provenance(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m15-review-session",
            session_id=session_id,
            skill_id="M-1.5",
        )
    return Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test")


def _rec(
    event_type: EventType,
    category: EventCategory,
    payload: dict,
    seq: int,
    *,
    subjects: list[Subject] | None = None,
    session_id: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=category,
        payload=payload,
        provenance=_prov(session_id),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects or [Subject(entity_type=SubjectEntityType.FINDING, entity_id="f", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _task_node(task_type: str, task_id: str = _TASK, seq: int = 1) -> EventRecord:
    return _rec(
        EventType.TASK_NODE_CREATED,
        EventCategory.STATE_TRANSITION,
        {"after_state": {"task_id": task_id, "task_type": task_type, "plan_id": "plan-x"}},
        seq,
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
    )


def _finding_created(fid: str, severity: str, seq: int, *, ru: str = _SID) -> EventRecord:
    return _rec(
        EventType.FINDING_CREATED,
        EventCategory.FINDING,
        {
            "finding_id": fid,
            "severity": severity,
            "lifecycle_state": "created",
            "review_unit_id": ru,
            "source": {"type": "model_review", "agent_id": "m15.review"},
        },
        seq,
        session_id=ru,
    )


def _finding_verified(fid: str, vstate: str, seq: int, *, ru: str = _SID) -> EventRecord:
    # FindingVerified schema 不带 review_unit_id —— 靠 provenance.session_id 归组 (真实形态)。
    return _rec(
        EventType.FINDING_VERIFIED,
        EventCategory.FINDING,
        {"finding_id": fid, "verification_state": vstate},
        seq,
        session_id=ru,
    )


def _finding_resolved(fid: str, seq: int, *, ru: str = _SID) -> EventRecord:
    # FindingResolved 同 —— 靠 provenance.session_id 归组 (真实形态)。
    return _rec(
        EventType.FINDING_RESOLVED,
        EventCategory.FINDING,
        {"finding_id": fid, "resolution": "fixed"},
        seq,
        session_id=ru,
    )


def _envelope(session_id: str, task_id: str, seq: int) -> EventRecord:
    """一次 review 会话对某 task 落的 task-scoped TransactionEnvelopeSubmitted (真实形态:
    payload.task_id = 被 review 的 task; 提交会话在 provenance.session_id, **不在** payload.session_id
    —— canonical CLI-emitted review envelope 把会话放 provenance, 生产实证 seq 40147)。这是
    review 会话 → 它申明 review 的 REVIEW task 的唯一 canonical 结构桥 (orchestrator
    _trace_fix_after_origin_review_task_id 也用同一 envelope.task_id 桥)。"""
    return _rec(
        EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        EventCategory.ENVELOPE,
        {"task_id": task_id, "envelope_id": f"env-{uuid.uuid4().hex[:8]}"},
        seq,
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        session_id=session_id,
    )


def _task_run_completed_intent(
    outcome: str, *, task_id: str = _TASK, session_id: str | None = _SID
) -> EventIntent:
    return EventIntent(
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TASK_RUN_COMPLETED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": {"task_id": task_id, "run_id": "run-1", "outcome": outcome}},
        provenance_hint=_prov(session_id),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_review_task_with_failed_verdict_rejected() -> None:
    """REVIEW task 完成但 review-unit 有未解决 verified-FAIL → verdict failed → 拒。"""
    records = [
        _task_node("review", seq=1),
        _finding_created("F1", "major", 2),
        _finding_verified("F1", "verified", 3),  # verified-FAIL 未 resolved → failed
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success")
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is False
    assert res.failure_reason == "review_verdict_not_passed"
    assert res.failure_evidence["verdict"] == "failed"


def test_review_task_with_passed_verdict_accepted() -> None:
    """review-unit 全 finding terminal 无 fail → verdict passed → 放行。"""
    records = [
        _task_node("review", seq=1),
        _finding_created("F1", "major", 2),
        _finding_verified("F1", "verified", 3),  # failed
        _finding_resolved("F1", 4),               # failed→pending
        _finding_resolved("F1", 5),               # re-review closure → passed
    ]
    index = EventIndex(records)
    res = check_review_verdict_gated_completion([_task_run_completed_intent("success")], index, records)
    assert res.passed is True


def test_review_task_clean_zero_findings_accepted() -> None:
    """干净 review (零 finding) → verdict passed → 放行。"""
    records = [_task_node("review", seq=1)]
    index = EventIndex(records)
    res = check_review_verdict_gated_completion([_task_run_completed_intent("success")], index, records)
    assert res.passed is True


def test_review_task_without_session_id_fail_closed() -> None:
    """REVIEW task 完成却无 review 会话 session_id → review-unit 不可定位 → fail-closed 拒。"""
    records = [_task_node("review", seq=1)]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success", session_id=None)
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is False
    assert res.failure_reason == "review_verdict_unprovable"


def test_non_review_task_not_gated() -> None:
    """非 REVIEW task (code_change) 即便没有 verdict 也不被本门挡。"""
    records = [_task_node("code_change", seq=1)]
    index = EventIndex(records)
    res = check_review_verdict_gated_completion([_task_run_completed_intent("success")], index, records)
    assert res.passed is True


def test_abort_outcome_not_gated() -> None:
    """REVIEW task 以 abort 终结 (合法终态) → 不走 verdict 门。"""
    records = [
        _task_node("review", seq=1),
        _finding_created("F1", "major", 2),
        _finding_verified("F1", "verified", 3),  # verdict failed, 但 abort 不受门管
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("aborted_unrecoverable")
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is True


# ─────────────────────────────────────────────────────────────────────────────
# finding-review-verdict-gate-session-fold-false-pass (critical) — 折叠口径修
#
# 旧口径: verdict 只按【完成本任务的会话 session_id】折叠 finding。double-drive 下空会话先
# conclude(0 finding)→passed→REVIEW task false-pass, 同 task 另一申明 review 的会话产的 verified
# 阻塞 finding 因 session_id 不同永不进折叠 → proof-of-work verdict 门在它最该生效的并行场景被旁路。
# 新口径: 按 review-target (REVIEW task_id R) 折叠 —— 凡【canonically 申明 review 过 R】的会话
# (= 对 R 落过 task-scoped TransactionEnvelopeSubmitted 的会话) ∪ 完成会话, 其 finding 全部纳入
# R 的 verdict 折叠。Advisor (OPUS 决策) 钦定: task-scoped envelope.task_id==R + provenance.session_id
# 是唯一可靠的 in-scope A→R canonical 桥 (trigger_event_id 会跨 task 过度合并; finding 无 task 锚)。
# ─────────────────────────────────────────────────────────────────────────────


def test_empty_completing_session_cannot_falsepass_over_co_reviewer_blocking_finding() -> None:
    """红队 (本 finding 核心): 同一 review-target R 被两会话 double-drive。完成本任务的会话 A 空
    (0 finding), 另一**申明 review R** 的会话 B (对 R 落了 task-scoped envelope) 审出 verified 阻塞
    finding 未闭合 → A 的 TaskRunCompleted(success) 必须被拒, R 的 verdict 不能 passed。

    修前 (按 A 的 session_id 折叠): 看不到 B 的 finding → passed (绕过坐实, 本测试修前 FAIL)。
    修后 (按 review-target R 折叠跨会话): B 的 verified-FAIL 纳入 → failed → 拒 (修后 PASS)。
    """
    a, b = _SID, "sess-review-coreviewer-b"
    records = [
        _task_node("review", seq=1),               # R = T-REVIEW-1, review-typed
        _envelope(a, _TASK, seq=2),                # A 申明 review R (task-scoped envelope)
        _envelope(b, _TASK, seq=3),                # B 也申明 review R (第二视角, T-RCL-01 合法并发)
        _finding_created("F-B", "major", 4, ru=b),
        _finding_verified("F-B", "verified", 5, ru=b),  # B 的 verified-FAIL 未 resolved
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success", session_id=a)  # A 空会话完成
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is False
    assert res.failure_reason == "review_verdict_not_passed"
    assert res.failure_evidence["verdict"] == "failed"


def test_co_reviewer_blocking_finding_resolved_then_passes() -> None:
    """正常态不破: 同上双会话, 但 B 的阻塞 finding 经 verify 后真 resolved (terminal) → R 全 finding
    terminal 无未决 FAIL → verdict passed → 放行。证明跨会话折叠不是'有别会话就一律拒', 而是按真实
    finding 生命周期折叠。"""
    a, b = _SID, "sess-review-coreviewer-b"
    records = [
        _task_node("review", seq=1),
        _envelope(a, _TASK, seq=2),
        _envelope(b, _TASK, seq=3),
        _finding_created("F-B", "major", 4, ru=b),
        _finding_verified("F-B", "verified", 5, ru=b),   # failed
        _finding_resolved("F-B", 6, ru=b),               # failed→pending
        _finding_resolved("F-B", 7, ru=b),               # re-review closure → passed
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success", session_id=a)
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is True


def test_recycled_sibling_session_scoped_only_KNOWN_RESIDUAL_passes() -> None:
    """KNOWN RESIDUAL (钉住 part(a) 折叠口径修的边界 —— 不是它该过的, 是它够不到的):

    生产事故 (seq 40330) 的 recycled-sibling 变体: 会话 B 在 conclude 前被单飞锁 churn 回收, 只留下
    session-scoped envelope (payload.task_id="session:<B>"), 从未对 R 落 task-scoped envelope → B 与 R
    无任何 canonical 结构链 (provenance.task_id / causation / task-scoped envelope 全无, 实证)。本
    code 层折叠口径修触及不到 B (绑定只能靠语义串匹配 = 违 law-one '不可伪造结构化凭证', 不做)。

    该变体的闭合属 finding-40415 **part(b)**: orchestrator double-drive 去重 + 单飞锁 (本 patch
    scope 外; finding 自述'二者都要才闭合')。**part(b) 落地后, 本测试应翻转为 assert 拒** —— 它在
    这里钉死 part(a) 单独不足以闭合生产事故的精确变体, 防止把 part(a) 误当全闭合。
    """
    a, b = _SID, "sess-review-recycled-b"
    records = [
        _task_node("review", seq=1),
        _envelope(a, _TASK, seq=2),                      # A 申明 review R
        _envelope(b, f"session:{b}", seq=3),             # B 只 session-scoped (recycled before conclude)
        _finding_created("F-B", "major", 4, ru=b),
        _finding_verified("F-B", "verified", 5, ru=b),
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success", session_id=a)
    res = check_review_verdict_gated_completion([intent], index, records)
    # part(a) 单独够不到无结构链的 B → 现行放行。part(b)(单飞锁) 落地后翻转为 assert 拒。
    assert res.passed is True


def test_session_declaring_multiple_review_tasks_fail_closed() -> None:
    """Q4 over-attribution 守卫 (Advisor 钦定): 若纳入 R 折叠集的某会话**还**对另一 REVIEW task 落了
    task-scoped envelope, 则它的 finding (review_unit_id=session, 无 per-finding task 锚) 无法判归
    哪个 task → 不静默把 R1 的 finding 折进 R2 误拒, 而是 fail-closed (ambiguous)。今生产 0 例, 但
    退役单飞 (T-RCL-01) 后不赌'一会话一任务'假设 —— 把未来隐患变成响亮 fail-closed, 不静默错判。"""
    a = _SID
    records = [
        _task_node("review", task_id=_TASK, seq=1),
        _task_node("review", task_id="T-REVIEW-2", seq=2),
        _envelope(a, _TASK, seq=3),
        _envelope(a, "T-REVIEW-2", seq=4),   # A 同时申明 review 两个 REVIEW task → 归属不可判
    ]
    index = EventIndex(records)
    intent = _task_run_completed_intent("success", session_id=a)
    res = check_review_verdict_gated_completion([intent], index, records)
    assert res.passed is False
    assert res.failure_reason == "ambiguous_review_session_multi_task"
