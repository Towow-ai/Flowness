"""T-LND-04 review-verdict-gated-completion@v1 (INV-B1-3): commit gate 对 REVIEW task 的
TaskRunCompleted(success) 加一道 blocking_check —— 其对应 review-unit 的派生 verdict 必须 passed。

法则一 (状态转移必须依赖不可伪造结构化凭证): review 任务的推进凭证是 verdict (proof-of-work),
不是 TaskRunCompleted 本身、更不是"测试绿了"。verdict != passed → commit gate 物理拒 success 落账。
这是危机1状态机修复: 状态锁死不再靠时间先后, 靠证据链 (verdict) 完整性。

接轨: TaskRunCompleted.after_state 无 task_type 字段, gate 据 index.lookup_entity('task',task_id)
反查 TaskNodeCreated.after_state.task_type; verdict 经 compute_review_verdict_over_set 从 committed
finding 事件流折叠 (T-LND-03)。

finding-review-verdict-gate-session-fold-false-pass 修 (折叠口径 = review-target 跨会话):
旧口径只按【完成本任务的会话 session_id】单键折叠 → double-drive 下空会话先 conclude(0 finding)→
passed→REVIEW task false-pass, 同 review-target 另一会话的 verified 阻塞 finding 因 session_id 不同
永不进折叠 (proof-of-work 门在并行场景被旁路, 生产实证 T-MILE-brain-quality)。新口径: 折叠集 =
所有【canonically 申明 review 过同一 REVIEW task R】的会话 = {完成会话} ∪ {对 R 落过 task-scoped
TransactionEnvelopeSubmitted 的会话} —— 桥用 envelope.provenance.session_id + payload.task_id==R
(canonical CLI-emitted review envelope 把会话放 provenance, **不读 payload.session_id**; 与
orchestrator _trace_fix_after_origin_review_task_id 同一 A→R 桥)。任一会话的 verified-未闭合 blocking
finding 都把 R 的 verdict 钉成非 passed (空会话的 pass 盖不过 co-reviewer 的 blocking finding)。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from towow.l0.projection.review_verdict import compute_review_verdict_over_set

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

_TASK_RUN_COMPLETED = "TaskRunCompleted"
_TASK_NODE_CREATED = "TaskNodeCreated"
_TRANSACTION_ENVELOPE_SUBMITTED = "TransactionEnvelopeSubmitted"
_NODE_TOUCHED = "NodeTouched"
_SESSION_SCOPED_PREFIX = "session:"
_REVIEW_TASK_TYPE = "review"
_FINDING_EVENT_TYPES = frozenset(
    # ⚠ FindingClosureContractAmended 刻意不在此集 (f-stale-closure-contract-permanently-unclosable):
    # verdict 折叠只看生命周期事件; 修订闭合合约不改任何 finding 的确认/驳回/闭合结论。
    {"FindingCreated", "FindingVerified", "FindingDisputed", "FindingResolved"}
)


@dataclass(frozen=True)
class ReviewVerdictResult:
    """gate review-verdict 检查结果 (镜像 semantic_checks 的 CheckResult 形态)。"""

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)


def _resolve_task_type(index: EventIndex, task_id: str) -> str | None:
    for rec in index.lookup_entity("task", task_id):
        if rec.event_type.value == _TASK_NODE_CREATED:
            payload = rec.payload if isinstance(rec.payload, dict) else {}
            after = payload.get("after_state")
            after = after if isinstance(after, dict) else {}
            tt = after.get("task_type")
            if isinstance(tt, str):
                return tt
    return None


def _unwrap_stub_rewrap_payload(rec: EventRecord) -> tuple[str, dict[str, Any]]:
    """(effective_event_type, payload), 还原 NodeTouched stub-rewrap (镜像 orchestrator._unwrap_stub_rewrap)。

    canonical review envelope 不走 stub-rewrap (生产实证: 直接 event_type=TransactionEnvelopeSubmitted),
    此 unwrap 仅为防御性兜底 —— 万一某路径把 envelope 包成 NodeTouched(kind=...), 仍能取到真 task_id。
    """
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    if rec.event_type.value == _NODE_TOUCHED:
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    return rec.event_type.value, payload


def _session_to_declared_review_tasks(committed_records: list[EventRecord]) -> dict[str, set[str]]:
    """从 committed envelope 反查 (review 会话 session_id) → 它**申明 review 过**的 task_id 集合。

    桥: TransactionEnvelopeSubmitted.provenance.session_id (= 提交会话) + payload.task_id (= 被 review
    的 task)。**读 provenance.session_id, 不读 payload.session_id** —— canonical CLI-emitted review
    envelope 把会话放 provenance (生产实证 seq 40147: payload.session_id=None / provenance.session_id=
    sess-review-...)。session-scoped envelope (payload.task_id 形如 "session:<sid>") 不是对某 task 的
    review 申明, 跳过 (recycled-before-conclude 的 sibling 只留 session-scoped → 与任何 R 无结构链,
    本 code 层够不到, 属 finding part(b) orchestrator 单飞锁 scope)。
    """
    out: dict[str, set[str]] = defaultdict(set)
    for rec in committed_records:
        etype, payload = _unwrap_stub_rewrap_payload(rec)
        if etype != _TRANSACTION_ENVELOPE_SUBMITTED:
            continue
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id.startswith(_SESSION_SCOPED_PREFIX):
            continue
        prov = getattr(rec, "provenance", None)
        sess = getattr(prov, "session_id", None)
        if isinstance(sess, str) and sess:
            out[sess].add(task_id)
    return out


def _record_to_fold_event(rec: EventRecord) -> dict[str, Any]:
    """EventRecord → compute_review_verdict 的入参形态 (event_type/payload/provenance/seq)。"""
    prov = getattr(rec, "provenance", None)
    return {
        "event_type": rec.event_type.value,
        "payload": rec.payload if isinstance(rec.payload, dict) else {},
        "provenance": {"session_id": getattr(prov, "session_id", None)} if prov is not None else {},
        "sequence_number": rec.sequence_number,
    }


def _intent_to_fold_event(intent: EventIntent) -> dict[str, Any]:
    prov = getattr(intent, "provenance_hint", None)
    return {
        "event_type": intent.event_type.value,
        "payload": intent.payload if isinstance(intent.payload, dict) else {},
        "provenance": {"session_id": getattr(prov, "session_id", None)} if prov is not None else {},
        "sequence_number": 1 << 62,  # 批内 intent 还没定 seq — 放最后折叠 (最新)
    }


def check_review_verdict_gated_completion(
    domain_intents: list[EventIntent],
    index: EventIndex,
    committed_records: list[EventRecord],
) -> ReviewVerdictResult:
    """REVIEW task 的 TaskRunCompleted(success) 合法 ⟺ 其 review-unit 的 verdict=passed。

    非 REVIEW task / abort outcome / 无法解析为 review → 跳过 (本检查只管 REVIEW success 门)。
    REVIEW task 完成但 (a) 无 review 会话 session_id 或 (b) verdict != passed → fail-closed 拒。
    """
    for intent in domain_intents:
        if intent.event_type.value != _TASK_RUN_COMPLETED:
            continue
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        if after.get("outcome") != "success":
            continue  # abort 是合法终态, 不走 verdict 门
        task_id = after.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        if _resolve_task_type(index, task_id) != _REVIEW_TASK_TYPE:
            continue  # 非 REVIEW task — 不归本门管 (INV-B1-4 只约束 REVIEW)

        prov = getattr(intent, "provenance_hint", None)
        review_unit_id = getattr(prov, "session_id", None)
        if not isinstance(review_unit_id, str) or not review_unit_id:
            # REVIEW task 完成却没有 review 会话凭证 → 无法证明 verdict, fail-closed。
            return ReviewVerdictResult(
                passed=False,
                failure_reason="review_verdict_unprovable",
                failure_evidence={
                    "task_id": task_id,
                    "reason": "REVIEW task TaskRunCompleted 无 provenance.session_id (review-unit 不可定位)",
                },
            )

        # ── 折叠口径 = review-target 跨会话 (finding-review-verdict-gate-session-fold-false-pass) ──
        # review_session_set = {完成会话} ∪ {对本 REVIEW task R 落过 task-scoped envelope 的会话}。
        session_to_tasks = _session_to_declared_review_tasks(committed_records)
        review_session_set: set[str] = {review_unit_id}
        for sess, declared in session_to_tasks.items():
            if task_id in declared:
                review_session_set.add(sess)

        # Q4 over-attribution 守卫 (Advisor 钦定): 折叠集内任一会话若**还**申明 review 过别的 REVIEW
        # task, 它的 finding (review_unit_id=session, 无 per-finding task 锚) 归属不可判 → 不静默把别
        # task 的 finding 折进本 task 误拒, 而是 fail-closed。今生产 0 例 (退役单飞前'一会话一任务'),
        # 但 T-RCL-01 退役单飞后不赌该假设 —— 把隐患变响亮 fail-closed。
        for sess in review_session_set:
            other_review_tasks = sorted(
                t
                for t in session_to_tasks.get(sess, set())
                if t != task_id and _resolve_task_type(index, t) == _REVIEW_TASK_TYPE
            )
            if other_review_tasks:
                return ReviewVerdictResult(
                    passed=False,
                    failure_reason="ambiguous_review_session_multi_task",
                    failure_evidence={
                        "task_id": task_id,
                        "session_id": sess,
                        "also_declared_review_tasks": ", ".join(other_review_tasks),
                        "reason": "review 会话同时申明 review 多个 REVIEW task, 其 finding 无 per-finding "
                        "task 锚 → 归属不可判, fail-closed (不静默把别 task 的 finding 折进本 task)",
                    },
                )

        fold_events = [_record_to_fold_event(r) for r in committed_records]
        fold_events += [
            _intent_to_fold_event(i)
            for i in domain_intents
            if i.event_type.value in _FINDING_EVENT_TYPES
        ]
        verdict = compute_review_verdict_over_set(fold_events, frozenset(review_session_set))
        if verdict != "passed":
            return ReviewVerdictResult(
                passed=False,
                failure_reason="review_verdict_not_passed",
                failure_evidence={
                    "task_id": task_id,
                    "review_unit_id": review_unit_id,
                    "review_session_set": ", ".join(sorted(review_session_set)),
                    "verdict": verdict,
                    "reason": "REVIEW task success 落账要求 review-target verdict=passed (法则一: 凭证=verdict "
                    "非测试绿; 折叠按 review-target 跨会话, 空会话 pass 盖不过 co-reviewer 的 blocking finding)",
                },
            )
    return ReviewVerdictResult(passed=True)


__all__ = ["ReviewVerdictResult", "check_review_verdict_gated_completion"]
