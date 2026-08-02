"""live-target-execution-evidence@v1 · goal 收口门 goal.live_target_reconciled (承重).

概念动机 (生产实锤 plan-reflow-systemic-repair / T-REFLOW-BATCH-MECH · finding
f-reflow-condition4-batch-execute-never-ran): goal 的 completion_condition 明写"清单被冻结并执行",
却在全 hot 账本 StrandedBatchManifestFrozen 计数=0 的情况下, 靠"撤除 EXECUTE→REVIEW 边 + 单条
sibling success" 被宣布 GoalSessionTerminated(reason=completion) —— 全程无一门要求出示一条真实生产事件。

本门是承重那道 (旗舰病例真正的洞在 goal 层): 任何把 goal 推向 GoalSessionTerminated(completion) 的
路径先过本门, 门对该 goal 的 brief.live_target_observables 逐条复算 against committed 账本 (共用唯一
真值源 gate_ledger_event)。任一未满足 → 拒 completion。

**不看 task 图, 只看 observable 的真实账本证据** —— 撤掉执行 task 不会让 StrandedBatchManifestFrozen
从 0 变 ≥1, 故"撤边宣布全 task 达终态"的逃逸被拒。

单一真值源: 本模块的 reconcile 核心被 commit gate (check_live_target_reconciled) + orchestrator FB-1
即时回收 (代发 completion 前先对账) 共用, 防两处判据漂移。

scope 锚定 (advisor #1 承重): observable 的 evidence 是 scope=this_goal 的 LEDGER_EVENT (brief schema
LiveTargetObservable model_validator 已强制)。this_goal → 本 goal 所属 plan(s) 的全 task 集: 事件
provenance.task_id ∈ 该集才计入 —— 绝不退化 global (否则别 goal 的旧同类事件会让本 goal 空声称
false-accept)。goal→brief→consensus→plan→task 链任一环解析失败 → gate_ledger_event fail-closed 拒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l1.closure_verification import LedgerScopeContext, gate_ledger_event
from towow.schemas.enums import ClosureVerificationMethod, LedgerEventScope
from towow.schemas.payloads.finding import ClosureCriterion

if TYPE_CHECKING:
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

_GOAL_SESSION_TERMINATED = "GoalSessionTerminated"
_GOAL_SESSION_STARTED = "GoalSessionStarted"
_SESSION_SPAWNED = "SessionSpawned"
_INTERVIEW_BRIEF_PUBLISHED = "InterviewBriefPublished"
_ENGINEERING_CONSENSUS_FREEZED = "EngineeringConsensusFreezed"
_TASK_NODE_CREATED = "TaskNodeCreated"
_NODE_TOUCHED = "NodeTouched"
_COMPLETION = "completion"


@dataclass(frozen=True)
class LiveTargetReconcileResult:
    """goal.live_target_reconciled 检查结果 (镜像 ReviewVerdictResult 形态)。"""

    passed: bool
    applicable: bool = True
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)


def _rec_type(rec: EventRecord) -> str:
    return rec.event_type.value


def _rec_payload(rec: EventRecord) -> dict[str, object]:
    return rec.payload if isinstance(rec.payload, dict) else {}


def _after(payload: dict[str, object]) -> dict[str, object]:
    a = payload.get("after_state")
    return a if isinstance(a, dict) else {}


def _after_or_top(payload: dict[str, object], key: str) -> object | None:
    """after_state.key 优先, 回退顶层 (兼容 canonical / stub-rewrap 两形态)。"""
    a = _after(payload)
    if key in a:
        return a[key]
    return payload.get(key)


def _eff_kind_payload(rec: EventRecord) -> tuple[str, dict[str, object]]:
    """还原 stub-rewrap NodeTouched → (effective_kind, effective_payload) (镜像 l1 _ledger_effective_kind_payload).

    orchestrator 自动派下一棒经 _build_orch_nodetouched 落的 GoalSessionStarted / GoalSessionTerminated /
    TaskNodeCreated 都是 NodeTouched stub (event_type=NodeTouched, payload.kind=真 kind, 真字段在
    payload.stub_original_payload) —— 裸按 rec.event_type.value 匹配永远 miss (debt-4d61bbb20311 坐实的
    第二重恒空放行根因: 除了 GoalSessionStarted 不带 brief_event_id, 它连"是 GoalSessionStarted"都认不出)。
    故本模块所有账本解析先 unwrap。不 import l2.orchestrator (层反转: 本模块属 l0), inline 同款逻辑。
    """
    et_val = _rec_type(rec)
    payload = _rec_payload(rec)
    if et_val == _NODE_TOUCHED:
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    return et_val, payload


def _iter_kind(committed: list[EventRecord], kind: str) -> list[tuple[EventRecord, dict[str, object]]]:
    """committed 中 effective_kind == kind 的 (rec, effective_payload) 列表 (含 stub-unwrap)。"""
    out: list[tuple[EventRecord, dict[str, object]]] = []
    for rec in committed:
        eff_kind, payload = _eff_kind_payload(rec)
        if eff_kind == kind:
            out.append((rec, payload))
    return out


def _resolve_plan_id_for_goal(committed: list[EventRecord], gsid: str) -> str | None:
    """gsid → 该 goal 所属 plan_id (承重 anchor)。三条解析路径, 任一命中即返回 (durable 优先):

    1. GoalSessionStarted.task_id (T-TWU-G Option A: emit 时直存的 durable anchor) → TaskNodeCreated.plan_id
    2. SessionSpawned(session_id==gsid).task_id (execution fan-out 与 GoalSessionStarted 同时 co-emit 的
       血缘事件, 天然带 task_id) → TaskNodeCreated.plan_id —— 自动派发路径无 brief_event_id 时的 durable 兜底
    3. GoalSessionStarted.brief_event_id (CLI goal spawn 显式传的路径) → EngineeringConsensusFreezed.plan_id

    全不命中 → None (调用侧零误伤放行: 无法把 goal 结构锚到 plan = 本门不冒充能验它)。
    """
    brief_ref: str | None = None
    task_id: str | None = None
    for _rec, payload in _iter_kind(committed, _GOAL_SESSION_STARTED):
        if _after_or_top(payload, "goal_session_id") != gsid:
            continue
        beid = _after_or_top(payload, "brief_event_id")
        if isinstance(beid, str) and beid:
            brief_ref = beid  # 最后一条覆盖 (retry/更正取最新)
        tid = _after_or_top(payload, "task_id")
        if isinstance(tid, str) and tid:
            task_id = tid

    if task_id is None:
        for _rec, payload in _iter_kind(committed, _SESSION_SPAWNED):
            if _after_or_top(payload, "session_id") != gsid:
                continue
            tid = _after_or_top(payload, "task_id")
            if isinstance(tid, str) and tid:
                task_id = tid

    if task_id is not None:
        pid = _plan_id_of_task(committed, task_id)
        if pid:
            return pid
    if brief_ref:
        pid = _plan_id_of_consensus_brief(committed, brief_ref)
        if pid:
            return pid
    return None


def _plan_id_of_task(committed: list[EventRecord], task_id: str) -> str | None:
    for _rec, payload in _iter_kind(committed, _TASK_NODE_CREATED):
        if _after_or_top(payload, "task_id") == task_id:
            pid = _after_or_top(payload, "plan_id")
            if isinstance(pid, str) and pid:
                return pid
    return None


def _plan_id_of_consensus_brief(committed: list[EventRecord], brief_ref: str) -> str | None:
    found: str | None = None
    for _rec, payload in _iter_kind(committed, _ENGINEERING_CONSENSUS_FREEZED):
        if _after_or_top(payload, "brief_event_id") == brief_ref:
            pid = _after_or_top(payload, "plan_id")
            if isinstance(pid, str) and pid:
                found = pid
    return found


def _consensus_brief_ref_for_plan(committed: list[EventRecord], plan_id: str) -> str | None:
    """plan_id → EngineeringConsensusFreezed.brief_event_id (冻结时捕获的 brief, 可能是 amend 前旧 brief)。"""
    found: str | None = None
    for _rec, payload in _iter_kind(committed, _ENGINEERING_CONSENSUS_FREEZED):
        if _after_or_top(payload, "plan_id") == plan_id:
            beid = _after_or_top(payload, "brief_event_id")
            if isinstance(beid, str) and beid:
                found = beid
    return found


def _active_brief_observables(committed: list[EventRecord], brief_ref: str) -> list[dict[str, object]]:
    """brief_ref (consensus 冻结的 brief, 可能已被 amend supersede) → 跟随 supersede 前向链到 tip active
    brief → 其 live_target_observables (raw dicts)。

    承重 (debt-ac9e6941ff00 根因): import-brief-amend 补入的 observable 挂在 *superseding tip* brief 上
    (audit_trace_refs.superseded_brief_event_id 指回被取代的旧 brief), 而 consensus 冻结的是 amend 前旧
    brief —— 精确匹配旧 brief 只会取到 0 个 observable → 空放行。前向跟随 supersede 链取 tip.observables
    才让门看见 amend 后的 obs。

    id 双形态容忍 (debt-ac9e6941ff00 备注): consensus.brief_event_id 常是 brief_id 形态 ('brief-xxxx'),
    supersede 链用 event_id 形态 → _brief_record 按 brief_id 或 event_id 双匹配, 链跳按 event_id 匹配。
    """
    rec_payload = _brief_record(committed, brief_ref)
    if rec_payload is None:
        return []
    rec, payload = rec_payload
    seen: set[str] = {rec.event_id}
    while True:
        nxt = _superseder_of(committed, rec.event_id)
        if nxt is None or nxt[0].event_id in seen:
            break
        rec, payload = nxt
        seen.add(rec.event_id)
    obs = payload.get("live_target_observables")
    if isinstance(obs, list):
        return [o for o in obs if isinstance(o, dict)]
    return []


def _resolve_observables(committed: list[EventRecord], brief_ref: str) -> list[dict[str, object]]:
    """brief_ref (brief_id 或 event_id) → 该 brief **自身**的 live_target_observables (不跟随 supersede)。

    直读某一具体 brief 的声明 (发布路径闭环测试用: 验 CLI 发布的 observable 真落进 canonical payload、
    门 loader 读得到)。跟随 supersede 前向链取 tip observable 的是 _active_brief_observables (reconcile 用)。
    """
    rec_payload = _brief_record(committed, brief_ref)
    if rec_payload is None:
        return []
    obs = rec_payload[1].get("live_target_observables")
    if isinstance(obs, list):
        return [o for o in obs if isinstance(o, dict)]
    return []


def _brief_record(
    committed: list[EventRecord], brief_ref: str,
) -> tuple[EventRecord, dict[str, object]] | None:
    """brief_ref (brief_id 或 event_id) → (InterviewBriefPublished rec, effective_payload) (dual-form)。"""
    for rec, payload in _iter_kind(committed, _INTERVIEW_BRIEF_PUBLISHED):
        bid = payload.get("brief_id")
        if str(bid) == brief_ref or rec.event_id == brief_ref:
            return rec, payload
    return None


def _superseder_of(
    committed: list[EventRecord], brief_event_id: str,
) -> tuple[EventRecord, dict[str, object]] | None:
    """找 audit_trace_refs.superseded_brief_event_id == brief_event_id 的 brief (supersede 前向一跳)。"""
    for rec, payload in _iter_kind(committed, _INTERVIEW_BRIEF_PUBLISHED):
        atr = payload.get("audit_trace_refs")
        if isinstance(atr, dict) and atr.get("superseded_brief_event_id") == brief_event_id:
            return rec, payload
    return None


def _plan_task_ids(committed: list[EventRecord], plan_id: str) -> frozenset[str]:
    """plan_id → TaskNodeCreated(plan_id) 的全 task_id 集 (this_goal scope 锚定, 直接锚 plan 不锚 brief)。

    承重 (advisor #1 陷阱): scope 若锚在 tip active brief 上会 miss —— consensus 冻结的 brief_event_id 是
    旧 brief, 没有 EngineeringConsensusFreezed 以 tip brief 为 key, 故 goal→plan→task 必须以 plan_id 直接
    锚 TaskNodeCreated, 与 observable 取自 tip brief 解耦 (否则合法完成会被 scope=None false-reject)。
    """
    task_ids: set[str] = set()
    for _rec, payload in _iter_kind(committed, _TASK_NODE_CREATED):
        if _after_or_top(payload, "plan_id") == plan_id:
            tid = _after_or_top(payload, "task_id")
            if isinstance(tid, str) and tid:
                task_ids.add(tid)
    return frozenset(task_ids)


def reconcile_goal_live_targets(
    gsid: str,
    committed: list[EventRecord],
) -> LiveTargetReconcileResult:
    """对一个正在收口的 goal 复算其 brief.live_target_observables against committed 账本 (承重核心)。

    applicable=False (放行): 该 goal 无法锚到 plan / 该 plan 无 (active tip) observable brief (零误伤 —
    不含真实处理断言的 goal 不受本门约束)。applicable=True 且任一 observable 未满足 (账本证据不足 / scope
    不可锚定) → passed=False (拒 completion)。全满足 → passed=True。

    goal→plan anchor (debt-4d61bbb20311 根治): 自动派发 goal 的 GoalSessionStarted 是 NodeTouched stub 且
    不带 brief_event_id → 旧 _resolve_brief_id_for_goal 双重认不出恒空放行。改经 stub-unwrap + 三路
    anchor (GoalSessionStarted.task_id / SessionSpawned.task_id / brief_event_id) 定位 plan_id。
    observable 取自 plan consensus 冻结 brief 的 supersede 前向 tip (debt-ac9e6941ff00 根治: amend 后的
    obs 挂在 superseding tip 上)。scope=this_goal 直接锚 plan_id → TaskNodeCreated, 与 observable brief
    解耦 (advisor 陷阱: 锚 tip brief 会 scope=None false-reject 合法完成)。共用 gate_ledger_event 唯一真值源。
    """
    plan_id = _resolve_plan_id_for_goal(committed, gsid)
    if not plan_id:
        # 无法把 goal 结构锚到 plan → 无从取 observable。这类 goal 不受本门约束 (零误伤): 无 durable
        # anchor (无 task_id / 无 SessionSpawned / 无 CLI brief_event_id) 的 goal 本门不冒充能验它。
        return LiveTargetReconcileResult(passed=True, applicable=False)
    frozen_brief_ref = _consensus_brief_ref_for_plan(committed, plan_id)
    observables = _active_brief_observables(committed, frozen_brief_ref) if frozen_brief_ref else []
    if not observables:
        # 该 plan 无 (active tip) observable brief → 不含真实处理断言, 零误伤放行。
        return LiveTargetReconcileResult(passed=True, applicable=False)

    # scope 锚 plan_id 直取 TaskNodeCreated (不锚 tip brief — 见 _plan_task_ids 陷阱注)。空集 → None
    # 交 gate_ledger_event fail-closed (scope 不可锚 = 拒), 不静默退化 global。
    goal_task_ids = _plan_task_ids(committed, plan_id) or None
    scope_ctx = LedgerScopeContext(goal_task_ids=goal_task_ids)

    unsatisfied: list[str] = []
    details: list[str] = []
    for obs in observables:
        obs_id = str(obs.get("observable_id", "<no-id>"))
        evidence = obs.get("evidence")
        if not isinstance(evidence, dict):
            unsatisfied.append(obs_id)
            details.append(f"{obs_id}: evidence 缺失/非结构化 (fail-closed)")
            continue
        try:
            # strict=False: evidence 从 committed 账本读回是 JSON dict (enum 是字符串), 须 coerce
            # (同 l1/closure_verification 读被审者 JSON 用 non-strict input config 的道理)。
            crit = ClosureCriterion.model_validate(evidence, strict=False)
        except (ValueError, TypeError) as exc:
            unsatisfied.append(obs_id)
            details.append(f"{obs_id}: evidence 非法 ({exc}) — fail-closed")
            continue
        if crit.verification_method is not ClosureVerificationMethod.LEDGER_EVENT:
            unsatisfied.append(obs_id)
            details.append(f"{obs_id}: evidence 非 ledger_event (goal 证据须门读账本), fail-closed")
            continue
        if not crit.event_kind or crit.min_occurrences is None or crit.scope_binding is None:
            unsatisfied.append(obs_id)
            details.append(f"{obs_id}: ledger_event 缺 event_kind/min_occurrences/scope_binding, fail-closed")
            continue
        scope_binding = crit.scope_binding if crit.scope_binding is not None else LedgerEventScope.THIS_GOAL
        passed, detail, _count = gate_ledger_event(
            event_kind=crit.event_kind,
            payload_predicate=crit.payload_predicate,
            min_occurrences=crit.min_occurrences,
            scope_binding=scope_binding,
            records=list(committed),
            scope=scope_ctx,
        )
        if not passed:  # None (scope 不可锚定) 或 False (证据不足) 都拒
            unsatisfied.append(obs_id)
            details.append(f"{obs_id}: {detail}")

    if unsatisfied:
        return LiveTargetReconcileResult(
            passed=False,
            applicable=True,
            failure_reason="goal_live_target_unsatisfied",
            failure_evidence={
                "goal_session_id": gsid,
                "unsatisfied_observables": ", ".join(unsatisfied),
                "detail": " | ".join(details)[:600],
                "reason": (
                    "goal completion_condition 含 live_target_observable, 但账本证据未满足 —— 拒 "
                    "reason=completion (降级 blocked_live_target_unsatisfied 触发 RePlan; 门看账本证据不看 "
                    "task 图, 撤边逃逸无效)"
                ),
            },
        )
    return LiveTargetReconcileResult(passed=True, applicable=True)


def check_live_target_reconciled(
    domain_intents: list[EventIntent],
    committed_records: list[EventRecord],
) -> LiveTargetReconcileResult:
    """commit gate check: 本批任一 GoalSessionTerminated(reason=completion) 都须过 goal 层账本对账。

    非 completion 收口 (escalation/unreachable/external/restart_frozen) 不走本门 (它们不是"做成了真实
    处理"的声称)。本批无 completion 收口 → applicable=False 放行。
    """
    for intent in domain_intents:
        if intent.event_type.value != _GOAL_SESSION_TERMINATED:
            continue
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        if _after_or_top(payload, "termination_reason") != _COMPLETION:
            continue
        gsid = _after_or_top(payload, "goal_session_id")
        if not isinstance(gsid, str) or not gsid:
            # completion 收口却无 goal_session_id → 无法对账, fail-closed 拒。
            return LiveTargetReconcileResult(
                passed=False,
                applicable=True,
                failure_reason="goal_completion_no_gsid",
                failure_evidence={
                    "reason": "GoalSessionTerminated(completion) 无 goal_session_id — 无法 live-target 对账",
                },
            )
        result = reconcile_goal_live_targets(gsid, committed_records)
        if not result.passed:
            return result
    return LiveTargetReconcileResult(passed=True, applicable=False)


__all__ = [
    "LiveTargetReconcileResult",
    "check_live_target_reconciled",
    "reconcile_goal_live_targets",
]
