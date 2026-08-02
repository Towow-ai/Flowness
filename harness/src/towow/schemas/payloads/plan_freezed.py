"""M-1.3 §3.8 PlanFreezed event payload + 5 项 blocking_check function (F7 fix).

spec source: 04-l1-intelligence/M-1.3-planner-skill-detailed-design.md §3.8
F7 fix scope: plan CLI 加独立 freeze 命令 + 5 项 blocking_check 评估 + atomic emit
PlanFreezed; reject freeze if 任一 fail. 这是 fail-closed 终态门 wire (F7 review 根因 fix).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from towow.l0.projection import ProjectionStore

# enums 是 leaf StrEnum 模块 (无 import 依赖) → plan_freezed 单向 import 无环 (T-LRF-12).
from towow.schemas.enums import ClosureVerificationMethod, EventType

# RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation. audience 是 leaf 模块 (只依赖 pydantic+stdlib)
# → plan_freezed 单向 import 无环。
from towow.schemas.payloads.audience import CheckpointAudienceSeparation

# NOTE: `iter_raw_event_lines` is imported lazily inside _read_events (not at module top).
# Importing it eagerly here pulls in the `towow.l0.event_log` package __init__, which imports
# event_log.py → schemas.payloads.registry → (back to) plan_freezed — a real import cycle that
# only resolved by collection ordering when plan_freezed wasn't the first module loaded. Deferring
# the import to call time breaks the cycle so plan_freezed is importable standalone (T-L1-27).

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ─── BlockingCheck (复用 envelope 同款形态, 但 plan-local for fail-closed gate) ──


class PlanBlockingCheck(BaseModel):
    """Plan freeze blocking check entry (per M-1.3 §3.8 self_check.blocking_checks)."""

    model_config = _STRICT

    check_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "pending"]
    evidence: str = Field(min_length=1)


# ─── PlanFreezedPayload ─────────────────────────────────────────────────────────


class PlanBatchInfo(BaseModel):
    model_config = _STRICT
    batch_number: int = Field(ge=1)
    is_final_batch: bool
    previous_batch_plan_freeze_ids: list[str] = Field(default_factory=list)


class CoverageMatrixEntry(BaseModel):
    """M-1.3 §3.8 coverage_matrix entry — proof that a completion_condition observable is
    covered by ≥1 task's done_criterion (T-L1-30).

    v3-initial honesty boundary: `observable` is currently the brief's whole
    goal_completion_condition (a single free-text string), not one of N structured observables —
    because the M-1.1 brief schema carries goal_completion_condition as a single string, not a
    list. So `covered_by_tasks` is "tasks whose done_criterion evidence_ref.quoted_claim is a real
    substring of that string" (quote-grounding — the task cannot fabricate coverage with arbitrary
    text). Per-observable 100% coverage at sub-string granularity needs structured brief
    observables (debt → T-L1-23 / brief schema extension). See plan_freezed debts.
    """

    model_config = _STRICT

    observable: str = Field(min_length=1)
    covered_by_tasks: list[str] = Field(default_factory=list)
    evidence: str = Field(min_length=1)


class PlanFreezedPayload(BaseModel):
    """M-1.3 §3.8 PlanFreezed canonical payload (F7 fix + T-L1-30 coverage_matrix)."""

    model_config = _STRICT

    kind: Literal["PlanFreezed"] = "PlanFreezed"
    plan_id: str = Field(min_length=1)
    frozen_task_ids: list[str] = Field(default_factory=list)
    batch_info: PlanBatchInfo
    # T-L1-30: §3.8 coverage_matrix — completion_condition coverage proof. Optional/default-empty
    # so pre-T-L1-30 PlanFreezed events replay; the freeze gate (check_coverage_complete) is the
    # fail-closed teeth, and the CLI emits a populated matrix.
    coverage_matrix: list[CoverageMatrixEntry] = Field(default_factory=list)
    self_check_passed: bool
    blocking_checks: list[PlanBlockingCheck]
    # RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation (M-1.3 plan freeze checkpoint)。
    # Optional 向后兼容 (既有 PlanFreezed 事件无此字段仍重放); 一旦提供, schema 层 enforce 受众分离:
    # task DAG / internal task id 不会当 owner-facing plan 直接砸给 Nature (LEDGER 表列 M-1.3 同类风险)。
    audience: CheckpointAudienceSeparation | None = None


# ─── 5 项 blocking_check function (event_log query based) ───────────────────────


def _read_events(events_log: Path) -> list[dict[str, object]]:
    # T-L0-04: read across all physical segments (base events.log + rotated events/hot/*.jsonl),
    # not just the base file — this ruler must see post-rotation events or the freeze gate would
    # silently judge against a stale base segment. iter_raw_event_lines accepts the concrete
    # events.log path and spans segments in read order.
    # Lazy import (see module-top NOTE): avoids the event_log↔registry import cycle.
    from towow.l0.event_log.segments import iter_raw_event_lines

    out: list[dict[str, object]] = []
    for line in iter_raw_event_lines(events_log):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _event_type(e: dict[str, object]) -> str:
    """事件类型 — unwrap stub-rewrap NodeTouched 信封 (B6-b, PARALLEL-EXEC-FIX-DESIGN).

    历史 path-B 事件以 event_type=NodeTouched + payload.kind=<真类型> + stub_original_payload
    落盘 (盘上 6 条 wrapped TaskDependencyEdgeAdded 实证)。直读 event_type 会把这些边整个
    跳过 → 依赖图缺边 → ready-set 把有依赖的 task 当无依赖盲并行。unwrap 范式与
    orchestrator._unwrap_stub_rewrap 一致。
    """
    et = str(e.get("event_type", ""))
    if et == "NodeTouched":
        p = e.get("payload")
        if isinstance(p, dict):
            kind = p.get("kind")
            if isinstance(kind, str) and kind:
                return kind
    return et


def _after_state(e: dict[str, object]) -> dict[str, object]:
    """事件业务字段 — canonical 取 payload.after_state; stub-rewrap 取 stub_original_payload.

    stub 信封的字段在 stub_original_payload【顶层】(after_state 常为 None — 盘上实证, B6-b),
    不能只认 after_state 键。字段名别名 (from_task/to_task) 由 _plan_dep_graph 消费方处理。
    """
    p = e.get("payload", {})
    if not isinstance(p, dict):
        return {}
    if str(e.get("event_type", "")) == "NodeTouched" and isinstance(p.get("kind"), str):
        stub = p.get("stub_original_payload")
        if isinstance(stub, dict):
            inner = stub.get("after_state")
            if isinstance(inner, dict) and inner:
                return inner
            return stub
    ast = p.get("after_state", {})
    if isinstance(ast, dict):
        return ast
    return {}


def closed_task_ids(events: list[dict[str, object]]) -> set[str]:
    """readyset-closure-exclusion-contract@v1 (T-DEC-3) — task_ids of accepted TaskNodeClosed events.

    done-elsewhere-task-closure@v1: a TaskNodeClosed marks a frozen-plan task that was actually
    delivered elsewhere as the first-class terminal state `closed` (永不重派, 不是 abort/success)。
    The event log only ever holds gate-accepted events, and a TaskNodeClosed only lands after
    closure-evidence-verification-gate@v1 (T-DEC-2) confirmed it — so scanning the log here = the
    set of *verified* closures. No re-verification is needed at the dispatch layer.

    These ids fold into the SAME "prerequisite satisfied" set as completed_success_task_ids — at
    the completed-set layer, not the dedup layer (concept point 1/3): closing 止住重派, and being a
    completed-set member makes it immune to exec-stamp clearing / circuit-trip / pending_replan (the
    original bug was a dedup-layer exclusion punched through by aborted_for_replan 清戳).

    This single helper lives in the lower layer (plan_freezed) so BOTH consumers reference ONE
    exclusion (concept point 4 — 非 task-m04 专用 hack): the dispatch path imports it into
    execution_dispatch.compute_ready_tasks, and the freeze-gate resource-conflict checks below call
    it directly (a closed task is no longer a live writer, mirroring success).

    anti-fake-done (f-stub-rewrap-close-bypasses-closure-evidence-gate-1): a close only counts if it
    is a REAL flat TaskNodeClosed — never an unwrapped stub-rewrap NodeTouched(kind=TaskNodeClosed).
    A gate-verified close is ALWAYS emitted as a flat TaskNodeClosed (closure-evidence-verification-
    gate@v1 only lets those land, and it inspects raw intent.event_type). A stub-rewrap close never
    faced that gate; folding it into "satisfied" is the exact fake-done backdoor (7 autopilot turn-on
    tasks were stub-closed with dangling verdict_refs, seq 199376-199668, and this helper counted
    them). Two input shapes reach here:
      - dispatch path (_all_events_as_dicts): stubs are ALREADY unwrapped to event_type=<kind>, so the
        on-disk origin is preserved separately in raw_event_type — a stub close has
        raw_event_type=NodeTouched even though event_type reads TaskNodeClosed.
      - freeze-gate / test path (_read_events / raw dicts): no raw_event_type; event_type IS the raw
        on-disk type (a stub close reads event_type=NodeTouched directly).
    Preferring raw_event_type (falling back to event_type) refuses stub closes in BOTH shapes, while
    _event_type's unwrap stays available for TaskDependencyEdgeAdded etc. (path-B stubs legitimate).
    """
    closed: set[str] = set()
    for e in events:
        # raw_event_type (pre-unwrap origin) preferred; fall back to event_type for raw-dict callers.
        # A stub-rewrap NodeTouched(kind=TaskNodeClosed) bypassed the closure-evidence gate → must NOT
        # fold into satisfied. See docstring / f-stub-rewrap-close-bypasses-closure-evidence-gate-1.
        raw_et = str(e.get("raw_event_type") or e.get("event_type", ""))
        if raw_et != "TaskNodeClosed":
            continue
        tid = str(_after_state(e).get("task_id", ""))
        if tid:
            closed.add(tid)
    return closed


def _before_state(e: dict[str, object]) -> dict[str, object]:
    """事件 before_state 字段 (TaskDependencyEdgeRemoved 的撤边身份在这里)。"""
    p = e.get("payload", {})
    if not isinstance(p, dict):
        return {}
    bst = p.get("before_state", {})
    if isinstance(bst, dict):
        return bst
    return {}


def _active_dependency_edges(events: list[dict[str, object]]) -> set[tuple[str, str, str]]:
    """T-FIX-B6-01 (PLAN-seam#1) — 当前活跃的 task 依赖边集合 (event-sourced)。

    重放 TaskDependencyEdgeAdded → 加边; TaskDependencyEdgeRemoved → 去边 (撤边非物理删,
    但活跃集里移除)。边身份 = (source,target,dependency_type)。re-add 一条曾撤的边复活。
    所有读 task 依赖边、判方向/成环的 freeze 检查都必须经此, 否则被撤的错向边永远算进环 →
    补正确反向边被 no_circular 拒 → 唯一退路整盘换 plan_id (正是本 task 修的 seam)。
    """
    active: set[tuple[str, str, str]] = set()
    for e in events:
        et = _event_type(e)
        if et == "TaskDependencyEdgeAdded":
            a = _after_state(e)
            active.add(
                (
                    str(a.get("source_task_id", "")),
                    str(a.get("target_task_id", "")),
                    str(a.get("dependency_type", "")),
                ),
            )
        elif et == "TaskDependencyEdgeRemoved":
            b = _before_state(e)
            active.discard(
                (
                    str(b.get("source_task_id", "")),
                    str(b.get("target_task_id", "")),
                    str(b.get("dependency_type", "")),
                ),
            )
    return active


def check_all_tasks_self_contained(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """Each TaskNodeCreated for plan_id must have matching TaskReadSetClaimed + TaskWriteSetClaimed."""
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated"
        and _after_state(e).get("plan_id") == plan_id
    ]
    if not plan_tasks:
        return False, f"no TaskNodeCreated for plan_id={plan_id}"

    missing_read: list[str] = []
    missing_write: list[str] = []
    for t in plan_tasks:
        tid = str(_after_state(t).get("task_id", ""))
        has_read = any(
            _event_type(e) == "TaskReadSetClaimed" and _after_state(e).get("task_id") == tid
            for e in events
        )
        has_write = any(
            _event_type(e) == "TaskWriteSetClaimed" and _after_state(e).get("task_id") == tid
            for e in events
        )
        if not has_read:
            missing_read.append(tid)
        if not has_write:
            missing_write.append(tid)
    if missing_read or missing_write:
        return False, f"missing read claim: {missing_read[:5]}; missing write claim: {missing_write[:5]}"
    return True, f"all {len(plan_tasks)} tasks have read+write set claims"


def resolve_plan_brief_completion_condition(
    events: list[dict[str, object]], plan_id: str,
) -> tuple[str | None, str]:
    """Resolve the independent observable source: the brief's goal_completion_condition for plan_id.

    Chain (all from raw events — no projection bridge): plan_id → EngineeringConsensusFreezed
    (after_state.plan_id == plan_id) → after_state.brief_event_id. NOTE: that field actually holds
    the brief's *brief_id* (semantic id), not its event_id (verified empirically RUN-033) — so we
    match the InterviewBriefPublished whose payload.brief_id == it (and, defensively, by event_id).
    Fallback: if the chain is broken but exactly one active (non-superseded) brief exists, use it.

    Returns (completion_condition, how-resolved). completion_condition is None ⇒ unresolvable ⇒
    the coverage gate fails closed (no independent observable source = cannot verify coverage).
    """
    # 1. consensus → brief reference for this plan
    brief_ref: str | None = None
    for e in events:
        if _event_type(e) == "EngineeringConsensusFreezed" and _after_state(e).get("plan_id") == plan_id:
            beid = _after_state(e).get("brief_event_id")
            if beid and str(beid) != "evt-no-brief-ref":
                brief_ref = str(beid)

    def _gcc(ev: dict[str, object]) -> str | None:
        p = ev.get("payload")
        if isinstance(p, dict):
            v = p.get("goal_completion_condition")
            if isinstance(v, str) and v.strip():
                return v
        return None

    briefs = [e for e in events if _event_type(e) == "InterviewBriefPublished"]

    def _resolve_ref_to_gcc(ref: str) -> str | None:
        for e in briefs:
            p = e.get("payload")
            bid = p.get("brief_id") if isinstance(p, dict) else None
            if str(bid) == ref or str(e.get("event_id")) == ref:
                return _gcc(e)
        return None

    # 1.5 (dogfood R07 2026-06-14): AUTHORITATIVE recovery via the plan's FROZEN concepts'
    # source_brief_event_id. The consensus-freeze brief_event_id is auto-captured from the global
    # active brief at freeze time → under concurrent multi-plan work (e.g. 12 parallel lines) it can
    # capture the WRONG brief. Each frozen concept carries an *explicit* source_brief_event_id (set
    # at concept-create), so the plan's true brief = the brief those concepts came from. Prefer this
    # over the auto-captured brief_ref. For correctly-frozen plans the two agree (no behavior change).
    frozen_concept_ids: set[str] = set()
    for e in events:
        if _event_type(e) == "EngineeringConsensusFreezed" and _after_state(e).get("plan_id") == plan_id:
            cids = _after_state(e).get("frozen_concept_ids")
            if isinstance(cids, list):
                frozen_concept_ids.update(str(c) for c in cids)
    if frozen_concept_ids:
        concept_brief_refs: set[str] = set()
        for e in events:
            if _event_type(e) != "ConceptCreated":
                continue
            es = _after_state(e) or (e.get("payload") if isinstance(e.get("payload"), dict) else {})
            if not isinstance(es, dict):  # always dict in practice; narrows the union for mypy --strict
                continue
            cid = es.get("concept_id")
            sbe = es.get("source_brief_event_id")
            if isinstance(cid, str) and cid in frozen_concept_ids and isinstance(sbe, str) and sbe:
                concept_brief_refs.add(sbe)
        # 仅当冻结概念一致指向同一 source_brief 才信 (多源 = 概念混了来源, 不猜)
        if len(concept_brief_refs) == 1:
            ref = next(iter(concept_brief_refs))
            gcc = _resolve_ref_to_gcc(ref)
            if gcc:
                return gcc, f"resolved via frozen-concept source_brief={ref}"

    # 2. match brief by brief_id (the value consensus stores) or event_id (defensive)
    if brief_ref:
        for e in briefs:
            p = e.get("payload")
            bid = p.get("brief_id") if isinstance(p, dict) else None
            if str(bid) == brief_ref or str(e.get("event_id")) == brief_ref:
                gcc = _gcc(e)
                if gcc:
                    return gcc, f"resolved via consensus brief_ref={brief_ref}"

    # 3. fallback: exactly one active (non-superseded) brief
    superseded: set[str] = set()
    for e in briefs:
        p = e.get("payload")
        if isinstance(p, dict) and p.get("supersedes"):
            superseded.add(str(p.get("supersedes")))
    active: list[dict[str, object]] = []
    for e in briefs:
        p = e.get("payload")
        bid = str(p.get("brief_id")) if isinstance(p, dict) else ""
        if str(e.get("event_id")) in superseded or bid in superseded:
            continue
        active.append(e)
    if len(active) == 1:
        gcc = _gcc(active[0])
        if gcc:
            return gcc, "resolved via single-active-brief fallback"

    return None, f"unresolvable (consensus brief_ref={brief_ref!r}, active_briefs={len(active)})"


def _resolve_plan_frozen_invariant_observables(
    events: list[dict[str, object]], plan_id: str,
) -> list[str]:
    """The plan's consensus-frozen invariant descriptions — a SECOND independent observable source.

    A consensus session may freeze a concern *adjacent* to the brief (read from code during M-1.2),
    producing a derived sub-batch plan whose deliverable grounds in that frozen invariant rather than
    in the brief's named observables — the brief never mentioned this concern, so a task serving it
    cannot quote-ground in the brief's goal_completion_condition (empirical: plan-l0-read-consistency
    -consensus, derived from brief-06fd0fa4 to correct daemon-patrol-cost-separation@v1's stale
    premise — its read-consistency regression test grounds in inv-eventlog-cross-process-read-
    consistency, not in the brief's five observables).

    M-1.3 §3.8 already lists ``concept_definition`` as a valid evidence_ref.source_type. The invariant
    description is set at consensus freeze (external to the planning task), so quote-grounding to it is
    NOT circular self-claim — same non-circular teeth as the brief path, just a different independent
    source. Read from EngineeringConsensusFreezed.after_state.invariants[].description for plan_id.
    """
    descriptions: list[str] = []
    for e in events:
        if _event_type(e) != "EngineeringConsensusFreezed":
            continue
        if _after_state(e).get("plan_id") != plan_id:
            continue
        invs = _after_state(e).get("invariants")
        if isinstance(invs, list):
            for inv in invs:
                if isinstance(inv, dict):
                    desc = inv.get("description")
                    if isinstance(desc, str) and desc.strip():
                        descriptions.append(desc)
    return descriptions


def build_coverage_matrix(
    towow_dir: Path, plan_id: str,
) -> tuple[bool, list[CoverageMatrixEntry], str]:
    """T-L1-30: build the §3.8 coverage_matrix and decide planning.coverage_complete (fail-closed).

    Replaces the prior fake-done that only compared counts (packages == tasks, never read the
    brief). Now:
      - every plan task must have a TaskPackagePublished (still required — an unpackaged task = an
        incomplete plan);
      - every published package must carry package_content with done_criteria, and each criterion
        must carry a structurally complete evidence_ref (non-empty source_id + quoted_claim) — this
        kills count-only "coverage";
      - the independent observable (brief.goal_completion_condition) must be covered by ≥1 task
        whose done_criterion evidence_ref (source_type=brief.completion_condition) quote-grounds in
        it (quoted_claim is a real substring) — non-circular: the task cannot fabricate coverage;
      - if the brief is unresolvable, fail closed (no independent observable source).

    Returns (passed, coverage_matrix_entries, evidence).
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    ]
    plan_task_ids = {str(_after_state(t).get("task_id", "")) for t in plan_tasks}
    # readyset-closure-exclusion-contract@v1: a gate-verified closed task (retired /
    # done_elsewhere) is terminal — it will never run via this plan, so it needs no package
    # and cannot make the plan "incomplete" (mirrors the resource-conflict twins above;
    # T-PRW-TEST-DEBUG retired-close实证: 关闭后本门仍按缺包拒 freeze = 契约漏实施).
    closed = closed_task_ids(events)
    packages = [
        e for e in events
        if _event_type(e) == "TaskPackagePublished" and _after_state(e).get("task_id") in plan_task_ids
    ]
    if not packages:
        return False, [], f"no TaskPackagePublished events for plan_id={plan_id}"
    pkg_task_ids = {str(_after_state(p).get("task_id", "")) for p in packages}
    missing_pkg = sorted(plan_task_ids - pkg_task_ids - closed)
    if missing_pkg:
        # §11.2 渐进发布感知 (会话锁施工规划 dogfood 实撞): M-1.3 Phase3 明文"分批/渐进发布——
        # 无依赖先发先跑, 前置完成触发后续", §11.2 发布门物理拒绝"前置未完成的下游包"。旧实现
        # 在 freeze 要求所有 task 都有包 → 对带 hard 依赖链的 plan, 两道门互锁 (下游包发不出,
        # freeze 又因缺包永拒)。修正语义: 缺包仅当任务是 ready-frontier (无未完成 hard 前置) 时
        # 才算 plan 不完整; 有未完成 hard 前置的缺包任务 = progressive-pending, 由 §11.2 门在
        # 前置完成后逐个放行发布。完成判定镜像 §11.2: TaskRunCompleted outcome=success, 只认
        # strength=hard 边 (medium 不阻塞)。
        completed_success = {
            str(_after_state(e).get("task_id", ""))
            for e in events
            if _event_type(e) == "TaskRunCompleted"
            and _after_state(e).get("outcome") == "success"
        } | closed  # closure-exclusion: closed 折入 completed-set (同 dispatch 层契约)
        hard_preds: dict[str, set[str]] = {}
        for e in events:
            if _event_type(e) != "TaskDependencyEdgeAdded":
                continue
            es = _after_state(e)
            src = str(es.get("source_task_id", ""))
            tgt = str(es.get("target_task_id", ""))
            if str(es.get("strength", "hard")) != "hard":
                continue
            if src in plan_task_ids and tgt in plan_task_ids:
                hard_preds.setdefault(tgt, set()).add(src)
        frontier_missing = sorted(
            t for t in missing_pkg
            if not (hard_preds.get(t, set()) - completed_success)
        )
        if frontier_missing:
            return (
                False,
                [],
                f"ready-frontier tasks without TaskPackagePublished: {frontier_missing[:5]} "
                "(无未完成 hard 前置却没打包 = plan 不完整; 其余缺包任务为 §11.2 progressive-pending)",
            )

    # independent observable source(s): the brief's goal_completion_condition AND/OR the plan's
    # consensus-frozen invariant descriptions (the latter covers consensus-derived sub-batches whose
    # deliverable grounds in a frozen invariant the brief never named — see
    # _resolve_plan_frozen_invariant_observables). Both are external to the task ⇒ non-circular.
    gcc, how = resolve_plan_brief_completion_condition(events, plan_id)
    invariant_observables = _resolve_plan_frozen_invariant_observables(events, plan_id)
    if gcc is None and not invariant_observables:
        return (
            False,
            [],
            f"plan→brief {how}, and no frozen-invariant observable either — cannot verify coverage "
            "against any independent observable (fail-closed). plan→brief link is an unschematized "
            "shadow field (debt).",
        )

    # evidence-based coverage (kills count-only) + quote-grounding (kills circular self-claim)
    covering: list[str] = []
    for p in packages:
        ast = _after_state(p)
        tid = str(ast.get("task_id", ""))
        content = ast.get("package_content")
        if not isinstance(content, dict):
            return False, [], f"package {tid} has no package_content (count-only fake-done not allowed)"
        dcs = content.get("done_criteria")
        if not isinstance(dcs, list) or not dcs:
            return False, [], f"package {tid} package_content.done_criteria empty"
        for dc in dcs:
            if not isinstance(dc, dict):
                continue
            er = dc.get("evidence_ref")
            if (
                not isinstance(er, dict)
                or not str(er.get("source_id", "")).strip()
                or not str(er.get("quoted_claim", "")).strip()
            ):
                return False, [], f"package {tid} done_criterion {dc.get('criterion_id')} missing evidence_ref"
            quoted = str(er.get("quoted_claim", ""))
            src_type = er.get("source_type")
            # brief path (unchanged): grounds in the brief's goal_completion_condition.
            # invariant path (M-1.3 §3.8 concept_definition source): grounds in a frozen invariant.
            # Both sources are external to the task ⇒ neither is circular self-claim.
            if (src_type == "brief.completion_condition" and gcc is not None and quoted in gcc) or (
                src_type in ("concept_definition", "invariant")
                and any(quoted in inv for inv in invariant_observables)
            ):
                covering.append(tid)

    covered_by = sorted(set(covering))
    observable_repr = gcc if gcc is not None else f"<{len(invariant_observables)} frozen invariant(s)>"
    entry = CoverageMatrixEntry(
        observable=observable_repr,
        covered_by_tasks=covered_by,
        evidence=(
            f"{how}; quote-grounded done_criteria substrings of the brief completion_condition "
            f"and/or {len(invariant_observables)} frozen-invariant observable(s)"
        ),
    )
    if not covered_by:
        return (
            False,
            [entry],
            "independent observable not covered by any quote-grounded task done_criterion "
            f"(brief observable={gcc!r}, frozen invariants={len(invariant_observables)}) — no "
            "task's evidence_ref.quoted_claim is a substring of the brief completion_condition "
            "(source_type=brief.completion_condition) nor of a frozen invariant "
            "(source_type=concept_definition|invariant)",
        )
    return True, [entry], f"observable covered by {covered_by} ({how})"


def check_coverage_complete(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """T-L1-30 §3.8/§6: real coverage gate (was a count-only fake-done). See build_coverage_matrix."""
    passed, _matrix, evidence = build_coverage_matrix(towow_dir, plan_id)
    return passed, evidence


def check_no_circular_dependency(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """DFS cycle detect on *active* TaskDependencyEdges among plan_id tasks.

    T-FIX-B6-01 (PLAN-seam#1): 只数活跃边 (ADDED 减 REMOVED)。被撤的错向边不再算进环, 让
    '加错向 A→B → 撤 → 补正确 B→A' 闭环不被 no_circular 误拒。
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated"
        and _after_state(e).get("plan_id") == plan_id
    ]
    plan_task_ids = {str(_after_state(t).get("task_id", "")) for t in plan_tasks}
    # Restrict to *active* edges within plan (撤掉的边已从活跃集移除)
    edges = [
        (src, tgt)
        for (src, tgt, _dep_type) in _active_dependency_edges(events)
        if src in plan_task_ids and tgt in plan_task_ids
    ]
    graph: dict[str, list[str]] = {}
    for src, tgt in edges:
        graph.setdefault(src, []).append(tgt)
    GRAY, BLACK = 1, 2
    color: dict[str, int] = {}

    def dfs(node: str) -> tuple[bool, str | None]:
        if color.get(node) == GRAY:
            return True, node
        if color.get(node) == BLACK:
            return False, None
        color[node] = GRAY
        for nb in graph.get(node, []):
            cyc, where = dfs(nb)
            if cyc:
                return True, where
        color[node] = BLACK
        return False, None

    for node in list(graph.keys()):
        if color.get(node) is None:
            cyc, where = dfs(node)
            if cyc:
                return False, f"cycle detected involving task {where}"
    return True, f"no cycles in {len(edges)} edges among {len(plan_task_ids)} tasks"


@dataclass
class CriticalPathResult:
    """T-L1-27 (M-1.3 §3.5) result of computing the critical path from the dep graph.

    primary_path: one longest source→...→target chain (node count is maximal).
    alternative_paths: every *other* equal-length longest chain (≠ primary_path).
    makespan: node count of the longest chain (heuristic = 1 unit per task; §3.5
      estimated_makespan; matches SKILL.md "关键路径长度 = estimated_makespan").
    bottlenecks: tasks with the highest fan-in (most incoming edges) — §3.5 bottleneck_tasks.
    """

    primary_path: list[str]
    alternative_paths: list[list[str]] = field(default_factory=list)
    makespan: int = 0
    bottlenecks: list[str] = field(default_factory=list)


def _plan_dep_graph(
    events: list[dict[str, object]], plan_id: str,
) -> tuple[list[str], dict[str, list[str]], dict[str, int]]:
    """Build the directed dep graph for plan_id.

    Edge source→target = source must precede target (same orientation
    check_no_circular_dependency walks: graph[src] → [tgt]). Only tasks belonging to
    plan_id and edges whose BOTH endpoints are plan tasks count — foreign tasks/edges
    cannot extend a plan's chain (no cross-plan leakage).

    Returns (task_ids, adjacency src→[tgt], fan_in count per task).
    """
    plan_task_ids = [
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    ]
    id_set = set(plan_task_ids)
    adj: dict[str, list[str]] = {t: [] for t in plan_task_ids}
    fan_in: dict[str, int] = dict.fromkeys(plan_task_ids, 0)
    for e in events:
        if _event_type(e) != "TaskDependencyEdgeAdded":
            continue
        es = _after_state(e)
        # B6-b 字段别名: canonical 边用 source_task_id/target_task_id; 历史 stub-rewrap 边
        # (盘上 6 条实证) 用 from_task/to_task。只 unwrap 不别名, 历史边照样读不到 (红队亲查)。
        src = str(es.get("source_task_id") or es.get("from_task") or "")
        tgt = str(es.get("target_task_id") or es.get("to_task") or "")
        if src in id_set and tgt in id_set:
            adj[src].append(tgt)
            fan_in[tgt] += 1
    return plan_task_ids, adj, fan_in


def compute_critical_path(
    events: list[dict[str, object]], plan_id: str,
) -> CriticalPathResult:
    """T-L1-27 (M-1.3 §3.5) — compute the critical path (longest dependency chain) from the
    dep graph, NOT from a human-passed --task list.

    Longest-path-in-a-DAG via memoized DFS: for each node, the longest chain *starting* at it.
    The global maximum over all nodes is the makespan; every distinct chain achieving it is a
    critical path (primary = first by sorted task_id for determinism, the rest are alternatives).

    Pre-condition: the graph must be acyclic. Cycles are caught by the separate
    check_no_circular_dependency freeze gate; here we guard against infinite recursion by
    tracking the on-stack set and treating a back-edge as a non-extending leaf (so a cyclic
    graph still returns *a* result rather than hanging — the freeze gate rejects it anyway).
    """
    task_ids, adj, fan_in = _plan_dep_graph(events, plan_id)
    if not task_ids:
        return CriticalPathResult(primary_path=[], makespan=0)

    # best_paths[node] = list of longest chains starting at node (all equal max length).
    best_paths: dict[str, list[list[str]]] = {}
    on_stack: set[str] = set()

    def longest_from(node: str) -> list[list[str]]:
        if node in best_paths:
            return best_paths[node]
        if node in on_stack:
            # back-edge (cycle) — do not extend; treated as a leaf to stay terminating.
            return [[node]]
        on_stack.add(node)
        succ_chains: list[list[str]] = []
        succ_max = 0
        for nxt in sorted(set(adj.get(node, []))):
            for chain in longest_from(nxt):
                if len(chain) > succ_max:
                    succ_max = len(chain)
                    succ_chains = [chain]
                elif len(chain) == succ_max:
                    succ_chains.append(chain)
        on_stack.discard(node)
        result = [[node]] if not succ_chains else [[node, *c] for c in succ_chains]
        best_paths[node] = result
        return result

    # Collect every longest chain over all start nodes; keep only the global-max-length ones.
    all_max_chains: list[list[str]] = []
    global_max = 0
    for t in sorted(task_ids):
        for chain in longest_from(t):
            if len(chain) > global_max:
                global_max = len(chain)
                all_max_chains = [chain]
            elif len(chain) == global_max:
                all_max_chains.append(chain)

    # Dedupe (a chain may be reachable via multiple start scans only if it has a unique head,
    # but guard anyway), keep stable sorted order for a deterministic primary.
    seen: set[tuple[str, ...]] = set()
    unique_chains: list[list[str]] = []
    for c in sorted(all_max_chains):
        key = tuple(c)
        if key not in seen:
            seen.add(key)
            unique_chains.append(c)

    primary = unique_chains[0]
    alternatives = unique_chains[1:]

    max_fan_in = max(fan_in.values()) if fan_in else 0
    bottlenecks = (
        sorted(t for t, n in fan_in.items() if n == max_fan_in) if max_fan_in > 0 else []
    )

    return CriticalPathResult(
        primary_path=primary,
        alternative_paths=alternatives,
        makespan=global_max,
        bottlenecks=bottlenecks,
    )


def check_critical_path_identified(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """Query CriticalPathIdentified event for plan_id (F9 fix lands this emit)."""
    events = _read_events(towow_dir / "events.log")
    cp = [
        e for e in events
        if _event_type(e) == "CriticalPathIdentified"
        and _after_state(e).get("plan_id") == plan_id
    ]
    if not cp:
        return False, f"no CriticalPathIdentified event for plan_id={plan_id} (F9 fix 后会自动 enforce)"
    return True, f"found {len(cp)} CriticalPathIdentified event(s)"


# T-L1-29: opus matched_opus_factors 里这些因素名 = 不容置疑的高风险驱动 (§10 Opus 因素 +
# §13.5 review_required fill: red-line / 状态机 / invariant 变更 → high-risk → review_required)。
_HIGH_RISK_OPUS_FACTOR_KEYWORDS = ("red_line", "red-line", "state_machine", "invariant")


def _collect_high_risk_tasks(
    events: list[dict[str, object]], plan_task_ids: set[str],
) -> dict[str, str]:
    """T-L1-29: identify high-risk plan tasks from REAL, schema-backed event signals.

    Returns {task_id: why} for every high-risk task. Signals 1 & 2 read the LATEST
    TaskModelTierAssigned per task (latest-wins — tier 是可修正决策, 见正文注释):
      1. opus tier — latest TaskModelTierAssigned.model_tier == "opus" for the task. opus is
         assigned precisely because "做错的代价高" (§10/§13.5: red-line / 状态机 / invariant /
         关键路径), so an opus task is by construction high-risk.
      2. latest opus matched_opus_factors carrying a red_line / state_machine / invariant factor —
         the most precise signal (the actual risk driver named on the assignment).
      3. state_machine_transition_dependency edge endpoint — a task party to a state-machine
         transition dependency touches state-machine change (§10 状态机变更 signal).

    Replaces the prior vacuous heuristic that keyed off `risk_surface` (not a TaskNodeCreatedAfter
    field — never present on a conforming event) and task_type ∈ {config_migration, execution}
    (not valid TaskType enum values — never produced). Both signals were structurally unreachable,
    so the old check's is_high_risk was always False ⇒ 恒 pass 假门 (T-L1-29).
    """
    high_risk: dict[str, str] = {}

    # signals 1 & 2 — model tier assignments for plan tasks. latest-wins per task: tier 是
    # 可修正的 planner 决策 (不是 immutable truth) — planner 先发 opus 占位/初判, 随后带因子
    # 重打降 sonnet, 生效的是最后那条。逐事件聚合会让任何历史 opus 事件【永久】把 task 钉成
    # high-risk (降级路径不存在: 一旦碰过 opus 就再也甩不掉 review 门), 与 §10.6 evidence 门
    # 已采用的 latest-wins (check_model_tier_assignments_have_evidence) / M-0.2 投影对可变状态
    # latest-wins 的通则相矛盾。本会话 R01 dogfood 实撞: 全 9 task 最新档位 sonnet+cross_module
    # (无 high-risk 因素), 但早期占位 opus/state_machine 因子仍把 T-R01-1/2/3/5/6 钉成 high-risk
    # → freeze 永久被拒, re-emit 无法解 (opus 事件不可变)。只取每 task 最新一条 assignment 判风险。
    latest_tier: dict[str, dict[str, object]] = {}
    for e in events:
        if _event_type(e) != "TaskModelTierAssigned":
            continue
        ast = _after_state(e)
        tid = str(ast.get("task_id", ""))
        if tid not in plan_task_ids:
            continue
        latest_tier[tid] = ast  # later events overwrite earlier (file order = commit order)
    for tid, ast in latest_tier.items():
        if str(ast.get("model_tier", "")) == "opus":
            high_risk.setdefault(tid, "opus tier (做错代价高)")
        factors = ast.get("matched_opus_factors")
        if isinstance(factors, list):
            for f in factors:
                name = str(f.get("factor", "")) if isinstance(f, dict) else str(f)
                if any(kw in name for kw in _HIGH_RISK_OPUS_FACTOR_KEYWORDS):
                    high_risk[tid] = f"high-risk opus factor: {name}"
                    break

    # signal 3 — state_machine_transition_dependency edge endpoints
    for e in events:
        if _event_type(e) != "TaskDependencyEdgeAdded":
            continue
        es = _after_state(e)
        if str(es.get("dependency_type", "")) != "state_machine_transition_dependency":
            continue
        for key in ("source_task_id", "target_task_id"):
            tid = str(es.get(key, ""))
            if tid in plan_task_ids:
                high_risk.setdefault(tid, "state_machine_transition_dependency endpoint")

    return high_risk


def check_high_risk_tasks_have_review(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """T-L1-29 (M-1.3 §3.8): high-risk tasks (REAL risk signals, see _collect_high_risk_tasks)
    must declare review_required=True. Fails closed when a high-risk task lacks review.

    Was a 恒 pass 假门: the prior signals (risk_surface field / task_type ∈ {config_migration,
    execution}) are both structurally unreachable, so is_high_risk was always False.
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated"
        and _after_state(e).get("plan_id") == plan_id
    ]
    plan_task_ids = {str(_after_state(t).get("task_id", "")) for t in plan_tasks}
    review_by_task = {
        str(_after_state(t).get("task_id", "")): bool(_after_state(t).get("review_required", False))
        for t in plan_tasks
    }

    high_risk = _collect_high_risk_tasks(events, plan_task_ids)
    high_risk_no_review = sorted(
        tid for tid, _why in high_risk.items() if not review_by_task.get(tid, False)
    )

    if high_risk_no_review:
        detail = ", ".join(f"{t} ({high_risk[t]})" for t in high_risk_no_review[:5])
        return False, f"high-risk tasks without review_required=true: {detail}"
    return True, (
        f"all {len(high_risk)} high-risk tasks have review_required "
        f"(检查 {len(plan_tasks)} tasks)"
    )


def check_no_unresolved_red_line_uncertainty(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """T-L1-31 (M-1.3 §3.7 + §14.1.b): plan freeze is blocked while a red_line PlanningUncertainty
    for this plan is unresolved.

    PlanningUncertainty is a flat payload (fields directly under `payload`, not after_state — same
    shape as InformationNeed*). Resolution = a later PlanningUncertainty with the same
    uncertainty_id and resolved=true. We key by uncertainty_id and take the latest event per id
    (latest-wins) — if its severity is red_line and it is not resolved, freeze fails closed.
    advisory uncertainties never block. Cross-plan uncertainties are ignored.
    """
    events = _read_events(towow_dir / "events.log")
    # latest event per uncertainty_id for this plan_id (raw payload, flat shape)
    latest: dict[str, dict[str, object]] = {}
    for e in events:
        if _event_type(e) != "PlanningUncertainty":
            continue
        p = e.get("payload")
        if not isinstance(p, dict) or p.get("plan_id") != plan_id:
            continue
        uid = str(p.get("uncertainty_id", ""))
        if uid:
            latest[uid] = p  # later events overwrite earlier (file order = append/commit order)

    unresolved_red_line = sorted(
        uid for uid, p in latest.items()
        if str(p.get("severity", "")) == "red_line" and not bool(p.get("resolved", False))
    )
    if unresolved_red_line:
        return False, (
            f"unresolved red_line PlanningUncertainty blocks freeze: {unresolved_red_line[:5]} "
            "(resolve via a later PlanningUncertainty with resolved=true)"
        )
    return True, (
        f"no unresolved red_line uncertainty (checked {len(latest)} uncertainty id(s) for plan)"
    )


def check_no_resource_conflict(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """T-L1-25 (M-1.3 §8 / §3.3): two plan tasks whose write_sets intersect must be serialized.

    Parallel-execution safety: if two tasks in the plan claim a common write_set entity AND no
    TaskDependencyEdgeAdded edge orders them (either direction), they could run concurrently and
    collide — freeze fails closed. An explicit dependency edge (e.g. dependency_type=
    resource_conflict / ordering) declares the serialization and clears the conflict.

    Fixes the prior fake-done: the only write-boundary check was per-envelope claims⊆ (no
    cross-task / cross-plan intersection enforced at freeze).
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    ]
    plan_task_ids = {str(_after_state(t).get("task_id", "")) for t in plan_tasks}

    # readyset-closure-exclusion-contract@v1 (T-DEC-3): a closed task (done-elsewhere) will never
    # write via this plan, so it can never collide — drop it from the conflict comparison, the same
    # way a closed task is no longer a live writer in the cross-plan twin below. Without this a task
    # closed before (re-)freeze would raise a phantom write conflict and wrongly fail-closed.
    closed = closed_task_ids(events)

    # latest write_set per task (entity_type:entity_id keys)
    write_sets: dict[str, set[str]] = {}
    for e in events:
        if _event_type(e) != "TaskWriteSetClaimed":
            continue
        ast = _after_state(e)
        tid = str(ast.get("task_id", ""))
        if tid not in plan_task_ids:
            continue
        ws = ast.get("write_set")
        if isinstance(ws, list):
            write_sets[tid] = {
                f"{w.get('entity_type')}:{w.get('entity_id')}"
                for w in ws
                if isinstance(w, dict)
            }

    # dependency edges within the plan (undirected adjacency — an edge in either direction
    # serializes the pair, so a conflict is only "parallel" when no edge connects them).
    ordered: set[frozenset[str]] = set()
    for e in events:
        if _event_type(e) != "TaskDependencyEdgeAdded":
            continue
        es = _after_state(e)
        src, tgt = str(es.get("source_task_id", "")), str(es.get("target_task_id", ""))
        if src in plan_task_ids and tgt in plan_task_ids:
            ordered.add(frozenset({src, tgt}))

    task_ids = sorted(t for t in write_sets if t not in closed)
    for i, a in enumerate(task_ids):
        for b in task_ids[i + 1:]:
            overlap = write_sets[a] & write_sets[b]
            if overlap and frozenset({a, b}) not in ordered:
                shared = sorted(overlap)[:3]
                return False, f"parallel write conflict: tasks {a} & {b} both write {shared} with no ordering edge"
    return True, f"no parallel write conflicts among {len(task_ids)} tasks with write claims"


def check_no_cross_plan_resource_conflict(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """Layer2 缺口3 / Finding A (M-1.3 §13.4 write_set_overlap): two *different* plans must not both
    freeze with tasks that write a common entity and no cross-plan serialization.

    The intra-plan twin ``check_no_resource_conflict`` only intersects write_sets WITHIN one plan
    (``if tid not in plan_task_ids: continue``) — it explicitly never crosses plan boundaries. So
    two plans could each freeze while their tasks both write the same core file, then run
    concurrently and collide. There was zero machine enforcement of this (实测: orchestrator.py 被
    7 plan / 3 plan 的 10 个 task 并发写, 没有任何门拦"两 plan 写集冲突还各自冻结")。

    This is the deterministic enforcement of the ``cross-plan-check`` skill's ``write_set_overlap``
    (severity=high) conflict type, computed straight from canonical events — it does NOT depend on
    the advisory LLM fork having run (that fork stays advisory; the gate must hold even if it never
    fired). Fail-closed: when freezing plan B, if any write-claiming task in B intersects (on a
    write_set entity) a still-live task in another ALREADY-FROZEN plan, with no active dependency
    edge serializing the two tasks → freeze is rejected.

    Scope (advisor verdict ②a/②b):
    - "other plan" = a plan_id != this plan that already has ≥1 PlanFreezed event. Freeze is the
      commitment/serialization point (PlanFreezed events are totally ordered → "first to freeze
      wins, second to freeze must serialize or wait"). Plans with only TaskWriteSetClaimed but no
      PlanFreezed are still mutable → not yet a commitment → not compared (no false-positive on
      concurrent *planning*). This plan B is not yet frozen at this check, so it is never in the
      comparison set; and ``p != plan_id`` self-excludes a re-freezing plan (``plan amend``).
    - "live" = the other plan's conflicting task is not yet successfully completed (no
      TaskRunCompleted outcome=success). A done task's write is already committed, so B writing the
      same entity afterwards cannot collide concurrently. Fail-closed: any non-success / never-run
      task stays live.
    - Escape hatch (mirrors the intra-plan twin): an active TaskDependencyEdgeAdded between the two
      tasks (either direction, removal-aware via ``_active_dependency_edges``) declares serialization
      and clears the conflict — a cross-plan ordering an executor can actually honor.

    诚实边界: this enforces only ``write_set_overlap`` (1 of cross-plan-check's 6 conflict types).
    The other 5 (state_machine_race / obligation_conflict / concurrent concept write / one-writer /
    shared read) need semantic judgment and remain advisory-only via the (still un-wired) fork.
    """
    events = _read_events(towow_dir / "events.log")

    # task_id -> plan_id (TaskNodeCreated)
    task_plan: dict[str, str] = {}
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        ast = _after_state(e)
        tid, pid = str(ast.get("task_id", "")), str(ast.get("plan_id", ""))
        if tid and pid:
            task_plan[tid] = pid

    # plans that already committed (≥1 PlanFreezed) — the serialization set
    frozen_plans = {
        str(_after_state(e).get("plan_id", ""))
        for e in events
        if _event_type(e) == "PlanFreezed" and _after_state(e).get("plan_id")
    }

    # successfully-completed tasks: write committed → no concurrent collision → excluded (still
    # fail-closed: only outcome=success drops a task; aborted/failed/never-run stay live)
    done_tasks = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskRunCompleted" and _after_state(e).get("outcome") == "success"
    }

    # readyset-closure-exclusion-contract@v1 (T-DEC-3): a closed task (done-elsewhere terminal) is
    # no longer a live writer — its delivery already exists elsewhere, it will never write via this
    # frozen plan. Excluded exactly like a success-completed task above (镜像 success), so a closed
    # task in an already-frozen other plan can never block this plan's freeze.
    closed_tasks = closed_task_ids(events)

    # latest write_set per task (entity_type:entity_id keys)
    write_sets: dict[str, set[str]] = {}
    for e in events:
        if _event_type(e) != "TaskWriteSetClaimed":
            continue
        ast = _after_state(e)
        tid = str(ast.get("task_id", ""))
        ws = ast.get("write_set")
        if isinstance(ws, list):
            write_sets[tid] = {
                f"{w.get('entity_type')}:{w.get('entity_id')}"
                for w in ws
                if isinstance(w, dict)
            }

    this_tasks = sorted(t for t, p in task_plan.items() if p == plan_id and t in write_sets)
    if not this_tasks:
        return True, f"no write-claiming task for plan_id={plan_id} (nothing to cross-check)"

    other_tasks = sorted(
        t for t, p in task_plan.items()
        if p != plan_id and p in frozen_plans and t in write_sets
        and t not in done_tasks and t not in closed_tasks
    )

    # active cross-plan serialization edges as undirected pairs (an edge either direction serializes)
    ordered = {
        frozenset({src, tgt})
        for src, tgt, _dep in _active_dependency_edges(events)
        if src and tgt
    }

    other_live_plans = len({task_plan[t] for t in other_tasks})
    for t_b in this_tasks:
        for t_a in other_tasks:
            overlap = write_sets[t_b] & write_sets[t_a]
            if overlap and frozenset({t_b, t_a}) not in ordered:
                shared = sorted(overlap)[:3]
                return False, (
                    f"cross-plan write conflict: task {t_b} (plan {plan_id}) & task {t_a} "
                    f"(frozen plan {task_plan[t_a]}) both write {shared} with no serializing "
                    "cross-plan dependency edge — add a TaskDependencyEdgeAdded between them "
                    "(either direction) to declare the ordering, or wait for the other plan"
                )
    return True, (
        f"no cross-plan write conflict ({len(this_tasks)} write-claiming task(s) vs "
        f"{other_live_plans} other live frozen plan(s))"
    )


# ─── orchestration helper ───────────────────────────────────────────────────────


def check_all_tasks_have_model_tier(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """F8 fix — 每 task 必有 TaskModelTierAssigned event (or model_tier inline in TaskNodeCreated).

    readyset-closure-exclusion-contract@v1: gate-verified closed task (retired/done_elsewhere)
    永不执行 → 不需要 model_tier, 从检查集排除 (T-PRW-TEST-DEBUG retired-close 实证漏实施)。
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated"
        and _after_state(e).get("plan_id") == plan_id
    ]
    plan_task_ids = {
        str(_after_state(t).get("task_id", "")) for t in plan_tasks
    } - closed_task_ids(events)
    # Either inline (TaskNodeCreated.payload.after_state.model_tier) or separate event
    tier_assigned = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskModelTierAssigned"
        and _after_state(e).get("task_id") in plan_task_ids
    }
    inline_tier = {
        str(_after_state(t).get("task_id", ""))
        for t in plan_tasks
        if _after_state(t).get("model_tier")
    }
    have_tier = tier_assigned | inline_tier
    missing = sorted(plan_task_ids - have_tier)
    if missing:
        return False, f"tasks without model_tier: {missing[:5]}"
    return True, f"all {len(plan_task_ids)} tasks have model_tier"


def check_model_tier_assignments_have_evidence(
    towow_dir: Path, plan_id: str,
) -> tuple[bool, str]:
    """T-L1-26 (M-1.3 §10.6) — every TaskModelTierAssigned for this plan must carry real
    evidence: a non-placeholder assignment_reason AND ≥1 matched factor (opus or sonnet).

    A tier assigned with the default placeholder reason ("planner default tier assignment")
    and no matched factors is the evidence-less default the old CLI shipped — §10.6 要求
    可解释 tier (matched_opus_factors + opus_score + rationale). Such an assignment is rejected
    at freeze ("默认 reason 串被 freeze 拒"). Inline-tier tasks (TaskNodeCreated.model_tier with
    a rationale) are not subject to this separate-event check.
    """
    from towow.schemas.payloads.model_tier_scoring import (
        validate_tier_assignment_evidence,
    )

    events = _read_events(towow_dir / "events.log")
    plan_task_ids = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    }
    # 每任务只验【最新】一次 assignment (事件序最后一条) —— tier 是可修正的决策, 不是
    # immutable truth: planner 先发占位档位、随后补带证据的修正档位, 生效的是修正后那条。
    # 旧实现逐事件验 (任何历史占位事件永久卡死 freeze, 修正路径不存在) —— 本次会话锁
    # 施工规划 dogfood 实撞: 第一轮裸 --tier 后第二轮带因子重打, freeze 仍永久被拒。
    # 修正语义 = "生效 assignment 必须带 §10.6 证据" (latest-wins), 与 M-0.2 投影对
    # 可变状态 latest-wins 的通则一致。占位档位若从未被修正, 仍然被拒 (回归测试钉两向)。
    latest: dict[str, dict[str, object]] = {}
    for e in events:
        if _event_type(e) != "TaskModelTierAssigned":
            continue
        a = _after_state(e)
        tid = str(a.get("task_id", ""))
        if tid not in plan_task_ids:
            continue
        latest[tid] = a
    bad: list[str] = []
    for tid, a in latest.items():
        reason = str(a.get("assignment_reason", ""))
        opus_factors = a.get("matched_opus_factors")
        sonnet_factors = a.get("matched_sonnet_factors")
        opus_names = (
            [str(f.get("factor", "")) for f in opus_factors if isinstance(f, dict)]
            if isinstance(opus_factors, list)
            else []
        )
        sonnet_names = (
            [str(f) for f in sonnet_factors] if isinstance(sonnet_factors, list) else []
        )
        ok, _errors = validate_tier_assignment_evidence(
            reason=reason,
            matched_opus_factors=opus_names,
            matched_sonnet_factors=sonnet_names,
        )
        if not ok:
            bad.append(tid)
    bad.sort()
    if bad:
        return False, f"tier assignments lacking §10.6 evidence (default/placeholder): {bad[:5]}"
    return True, "all separate-event model_tier assignments carry §10.6 evidence"


def _task_concepts(after: dict[str, object]) -> set[str]:
    """Every concept_id a task touches: concept_refs[].concept_id + read/write_set entries
    whose entity_type=="concept" (entity_id).

    M-1.3 §3.1: concept_refs[] is {concept_id, at_reference, locking_policy}; read_set/write_set
    entries are {entity_type, entity_id}. Only entity_type=="concept" entries are concepts (a
    write_set entry with entity_type=="task" is the task's own output node, not a concept).
    """
    out: set[str] = set()
    refs = after.get("concept_refs")
    if isinstance(refs, list):
        for r in refs:
            if isinstance(r, dict) and isinstance(r.get("concept_id"), str) and r["concept_id"]:
                out.add(str(r["concept_id"]))
    for set_key in ("read_set", "write_set"):
        entries = after.get(set_key)
        if isinstance(entries, list):
            for e in entries:
                if (
                    isinstance(e, dict)
                    and e.get("entity_type") == "concept"
                    and isinstance(e.get("entity_id"), str)
                    and e["entity_id"]
                ):
                    out.add(str(e["entity_id"]))
    return out


def _concept_to_touching_tasks(events: list[dict[str, object]], plan_id: str) -> dict[str, set[str]]:
    """Map concept_id → set of plan tasks that TOUCH it (read OR write OR concept_ref).

    M-2.1 §4.3 "下游 task" = a task 涉及 (touching) the downstream concept — a task that merely
    READS a downstream concept is still transitively affected by an upstream change, so read-
    consumers count, not only writers/definers. This is the inverse of _task_concepts and uses the
    SAME touch semantics (concept_refs + read_set + write_set entity_type=="concept"). Built across
    the plan's TaskNodeCreated inline sets + separate TaskReadSetClaimed / TaskWriteSetClaimed claims.
    """
    plan_task_ids = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    }
    out: dict[str, set[str]] = {}
    # TaskNodeCreated: every touched concept (concept_refs + read_set + write_set), via _task_concepts.
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if a.get("plan_id") != plan_id:
            continue
        tid = str(a.get("task_id", ""))
        for concept_id in _task_concepts(a):
            out.setdefault(concept_id, set()).add(tid)
    # Separate read/write claims: a concept in any read_set/write_set claim of a plan task means that
    # task touches it (read consumers included, matching the inline touch semantics above).
    for e in events:
        if _event_type(e) not in ("TaskReadSetClaimed", "TaskWriteSetClaimed"):
            continue
        a = _after_state(e)
        tid = str(a.get("task_id", ""))
        if tid not in plan_task_ids:
            continue
        for set_key in ("read_set", "write_set"):
            entries = a.get(set_key)
            if isinstance(entries, list):
                for w in entries:
                    if (
                        isinstance(w, dict)
                        and w.get("entity_type") == "concept"
                        and isinstance(w.get("entity_id"), str)
                        and w["entity_id"]
                    ):
                        out.setdefault(str(w["entity_id"]), set()).add(tid)
    return out


def cia_dependency_scan(
    plan_tasks: list[dict[str, object]],
    store: ProjectionStore,
    *,
    events: list[dict[str, object]] | None = None,
    plan_id: str = "",
) -> dict[str, object]:
    """M-2.1 §4.1 消费方发现 caller-2 — for every plan task, take the concepts it touches
    (concept_refs[].concept_id + read/write_set entity_type=="concept"), run a real CIA
    forward_slice over each (沿 impact_graph 概念出边 BFS, depth ≤ 5 — the transitive *downstream*
    concepts that DEPEND ON the touched concept, empirically verified: a raw `B --dependency--> X`
    edge normalizes to impact_graph `X→B`, so forward_slice(X) returns B). Map each reached
    downstream concept back to the plan task(s) that TOUCH it (read/write/concept_ref — a read-
    consumer of a downstream concept is transitively affected too, §4.3 "下游 task"=涉及), recording
    every (upstream_task, downstream_task, concept_path, depth) edge it surfaces.

    record-only — "把传递性下游摆给 planner 看", NOT a blocker and NOT auto-建边: surfacing that
    task-A touches concept-X and task-B writes a concept downstream-of-X means B is transitively
    affected by A's plan, which the per-task read/write set intersection (check_no_resource_conflict,
    which only catches *direct* write-set overlap) does NOT see. Whether to actually add a dep edge
    stays the planner's dep-add judgment (建边仍是 planner 判断权). The result is recorded as
    evidence so a downstream review/planner sees, at freeze time, the transitive blast radius.

    Returns the evidence dict (always status=passed at the check level — enforce = "must have run",
    completeness.py REQUIRES the check present on PlanFreezed). Empty result (no concept edges /
    no plan-internal downstream task) is still a successful scan: transitive_edges=[] +
    projection_watermark proves the scan ran and is reproducible (re-run forward_slice at that
    watermark → same answer).

    ``store`` — a ProjectionStore already caught up to the freeze point (reads the materialized
    impact_graph 二阶 projection); ``events`` — the raw event list (TaskNodeCreated / claims) the
    plan↔concept ownership index is built from (run_all_blocking_checks passes the same list it read).
    """
    from towow.l1.cia_query import CIAQueryService

    cia = CIAQueryService(store)
    concept_to_tasks = _concept_to_touching_tasks(events or [], plan_id)
    # Seed watermark from the store so a plan with zero touched concepts still records the real
    # projection position (= store.cursor-1) — the value every forward_slice would return.
    watermark = max(0, store.cursor - 1)

    # upstream_task → its touched concepts (sorted, deterministic).
    scanned: list[dict[str, object]] = []
    # collected edges as (depth, upstream_task, downstream_task, concept_id) → entry for a stable sort
    collected: list[tuple[int, str, str, str, dict[str, object]]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for t in sorted(plan_tasks, key=lambda e: str(_after_state(e).get("task_id", ""))):
        a = _after_state(t)
        upstream_task = str(a.get("task_id", ""))
        concepts = sorted(_task_concepts(a))
        if concepts:
            scanned.append({"task_id": upstream_task, "concepts": concepts})
        for concept_id in concepts:
            result = cia.forward_slice(concept_id)
            watermark = result.projection_watermark
            for hit in result.results:
                if hit.depth < 1:
                    continue
                downstream_concept = hit.entity.entity_id
                # map the downstream concept back to the plan task(s) that write/define it.
                for downstream_task in sorted(concept_to_tasks.get(downstream_concept, set())):
                    if downstream_task == upstream_task:
                        # a task transitively-downstream-of-its-own concept is not an inter-task edge.
                        continue
                    key = (upstream_task, downstream_task, downstream_concept)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    collected.append(
                        (
                            hit.depth,
                            upstream_task,
                            downstream_task,
                            downstream_concept,
                            {
                                "upstream_task": upstream_task,
                                "downstream_task": downstream_task,
                                "via_concept": concept_id,
                                "downstream_concept": downstream_concept,
                                "concept_path": list(hit.path),
                                "depth": hit.depth,
                            },
                        ),
                    )
    collected.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    transitive_edges: list[dict[str, object]] = [entry for *_k, entry in collected]
    return {
        "scanned_tasks": scanned,
        "transitive_edges": transitive_edges,
        "projection_watermark": watermark,
    }


def check_cia_dependency_scan(
    towow_dir: Path, plan_id: str, store: ProjectionStore | None = None,
) -> tuple[bool, str]:
    """M-2.1 §4.1 caller-2 — run cia_dependency_scan over the plan tasks and return its evidence
    as a JSON string (PlanBlockingCheck.evidence is strict str — unlike consensus, which takes a
    dict — so the structured result is json.dumps'd; watermark always present for re-computation).

    status is ALWAYS passed (record-only); the teeth = completeness.py REQUIRES the check present.
    ``store`` (optional) — a ProjectionStore caught up to the freeze point (the CLI passes its
    freeze store); when None, one is built from towow_dir + events.log here so the scan still reads
    the live materialized impact_graph (not a skipped/空跑 scan).
    """
    events = _read_events(towow_dir / "events.log")
    plan_tasks = [
        e for e in events
        if _event_type(e) == "TaskNodeCreated" and _after_state(e).get("plan_id") == plan_id
    ]
    scan_store = store if store is not None else _build_caught_up_store(towow_dir)
    evidence = cia_dependency_scan(plan_tasks, scan_store, events=events, plan_id=plan_id)
    return True, json.dumps(evidence, sort_keys=True, ensure_ascii=False)


def _build_caught_up_store(towow_dir: Path) -> ProjectionStore:
    """Build a ProjectionStore from ``towow_dir`` and catch it up to events.log.

    Used when run_all_blocking_checks / check_cia_dependency_scan is called without a store (e.g.
    the unit tests, plan-import). The `plan freeze` CLI passes its own freeze store instead so it
    isn't caught up twice. Lazy import (see module-top NOTE) avoids the event_log↔registry cycle.
    """
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore

    store = ProjectionStore(towow_dir / "graph")
    store.catchup(EventLog(towow_dir / "events.log"))
    return store


# ─── migration-done-requires-old-path-removal@v1 (T-LRF-12) ──────────────────────
#
# 元规则机械验证 (planning 侧): 对迁移类 task 的 done_criteria 做结构性检查 —— 必须含一条
# old-path-removal 形态判据 (指名旧符号 + grep=0), 缺则拒冻结。接 honesty-enforced-completeness-
# and-debt 完整性门家族 (completeness.py REQUIRED_COMPLETENESS_CHECKS[PLAN_FREEZED] + 主 CLI 把
# 本 check surface 进信封, 使总闸 M-0.5 不可旁路)。
#
# 全 event-sourced 机械信号 (不解析自由文本):
#   · 迁移概念 = 有 ConceptEdgeAdded 边指向 migration invariant 的 concept (instance 自声明受其管辖,
#     盘上实证 execution-lock-migration-closeout@v1 / fork-spawn-bg-subscription-contract@v1 各发一条
#     reference 边指向不变量)。
#   · 迁移类 task = concept_refs 含迁移概念的 task —— concept_refs 是【交付绑定】(本 task 交付该概念);
#     刻意 NOT 用 read_set/write_set: 仅【读】一个迁移概念作参考的 task (如本元机制 task 自己 read 两个
#     具名实例) 不是在交付迁移, 不算迁移类 (用 read_set 会把本 task 自己误判成迁移类)。
#   · old-path-removal 形态判据 = done_criterion.machine_check 为 grep + expected_occurrences==0 +
#     非空 verification_pattern (指名旧符号 → grep 生产侧 0 命中 = 旧路径不可走)。复用 RUN-044
#     DoneCriterion.machine_check (M-1.5 ClosureCriterion), 不另造 schema; review 侧重跑同一 grep。
#   · 读侧防御性兼容段豁免: 本 check 只要求"存在 ≥1 条 old-path-removal 判据", 不扫码、不要求删读侧
#     兼容段 (guard 读 registry∪单指针 并集 fallback) —— 故刻意保留的读侧 fallback 永不被误要求删。

MIGRATION_INVARIANT_CONCEPT_ID = "migration-done-requires-old-path-removal@v1"


def _concept_edge_endpoints(e: dict[str, object]) -> tuple[str, str]:
    """(source_concept, target_concept) of a ConceptEdgeAdded — handles BOTH field-name aliases.

    盘上实证两套字段名并存: source_concept_id/target_concept_id (迁移 reference 边用这套) 与
    from_concept/to_concept (containment 等边用这套)。两套都读 (与 _plan_dep_graph 的 task 边
    别名处理同范式), 否则按单套读会漏掉一半的边。返回 ("","") 表示字段缺失。
    """
    a = _after_state(e)
    src = str(a.get("source_concept_id") or a.get("from_concept") or "")
    tgt = str(a.get("target_concept_id") or a.get("to_concept") or "")
    return src, tgt


def migration_concept_ids(events: list[dict[str, object]]) -> set[str]:
    """Concepts that DECLARE themselves governed by the migration invariant.

    = every concept C with an ACTIVE ConceptEdgeAdded edge C → MIGRATION_INVARIANT_CONCEPT_ID. ACTIVE
    = net edge state: replay ConceptEdgeAdded → add edge (keyed by edge_id); ConceptEdgeRemoved → drop
    that edge_id; a re-added edge revives. This is the SAME net-edge-state semantics as the canonical
    concept_graph projection (ConceptEdgeRemoved → is_active=false, keyed by edge_id; projection.py)
    and as this file's own _active_dependency_edges for task edges — one口径, no divergence.

    Honoring ConceptEdgeRemoved is load-bearing (FND-1, contrast-reference-mistaken-for-governance):
    the design's governance signal is "an edge to the invariant" (instance 自声明受其管辖), but the
    `reference` edge_type is ALSO used for contrast/see-also citations — a concept whose own canonical
    definition disclaims being a migration (e.g. new-capability-task-classifier@v1: "纯新增能力无旧
    路径要退役,不触发 migration 判别"; live-fire-machine-check-contract@v1 cites the invariant only to
    contrast grep-vs-test machine_checks) can still carry such an edge. When that mis-signalling edge is
    withdrawn via ConceptEdgeRemoved (corrected_error supersede), the concept must STOP being classified
    migration. The prior added-only scan ignored removal → diverged from the projection → false-positived
    pure new-enforcement concepts, wrongly demanding old-path-removal criteria of tasks that have no old
    production path to delete (which would force fabricated grep=0 criteria — the very hack this family
    of gates exists to kill).

    Any edge_type is accepted (an instance pointing at the invariant is declaring governance regardless
    of edge_type; fail toward catching a migration). The invariant concept itself is never a migration
    concept (a concept can't be its own migration instance), so a self-edge — should one ever appear —
    is excluded. Empirically the two genuine governed instances each emit a `reference` edge to the
    invariant: execution-lock-migration-closeout@v1 / fork-spawn-bg-subscription-contract@v1.
    """
    # edge_id → source_concept, for ACTIVE edges pointing at the migration invariant only.
    active: dict[str, str] = {}
    for e in events:
        et = _event_type(e)
        if et == "ConceptEdgeAdded":
            a = _after_state(e)
            edge_id = str(a.get("edge_id") or "")
            src, tgt = _concept_edge_endpoints(e)
            if (
                edge_id
                and tgt == MIGRATION_INVARIANT_CONCEPT_ID
                and src
                and src != MIGRATION_INVARIANT_CONCEPT_ID
            ):
                active[edge_id] = src
        elif et == "ConceptEdgeRemoved":
            edge_id = str(_before_state(e).get("edge_id") or "")
            if edge_id:
                active.pop(edge_id, None)
    return set(active.values())


def _task_concept_refs(after: dict[str, object]) -> set[str]:
    """A task's concept_refs[].concept_id — the DELIVERY binding (this task is bound to / delivers
    these concepts). Deliberately NOT read_set/write_set (cf. _task_concepts which is the touch-set):
    a task that merely READS a migration concept for reference (e.g. this very meta-mechanism task
    reads the two named instances) is NOT delivering a migration and must not be judged migration-class.
    Empirical: T-LRF-12.concept_refs = [the invariant]; its read_set = [invariant + both instances] —
    keying on the touch-set would false-positive the mechanism task itself.
    """
    out: set[str] = set()
    refs = after.get("concept_refs")
    if isinstance(refs, list):
        for r in refs:
            if isinstance(r, dict) and isinstance(r.get("concept_id"), str) and r["concept_id"]:
                out.add(str(r["concept_id"]))
    return out


def _latest_published_done_criteria(
    events: list[dict[str, object]], task_id: str,
) -> list[dict[str, object]] | None:
    """The latest TaskPackagePublished's package_content.done_criteria for task_id (structured
    DoneCriterion dicts), or None if the task has no published package.

    latest-wins (a task may be re-packaged). The structured machine_check lives ONLY on the PUBLISHED
    package — the inline TaskNodeCreated.done_criteria are free-text strings without machine_check,
    not mechanically checkable — so the published package is the only source the structural check
    can key on.
    """
    found: list[dict[str, object]] | None = None
    for e in events:
        if _event_type(e) != "TaskPackagePublished":
            continue
        a = _after_state(e)
        if str(a.get("task_id", "")) != task_id:
            continue
        content = a.get("package_content")
        if isinstance(content, dict):
            dcs = content.get("done_criteria")
            found = [dc for dc in dcs if isinstance(dc, dict)] if isinstance(dcs, list) else []
    return found


def is_old_path_removal_criterion(dc: dict[str, object]) -> bool:
    """True iff a done_criterion is an old-path-removal form criterion (brief 指定形态):
    a machine_check of verification_method=grep, expected_occurrences==0, non-empty
    verification_pattern (the named old symbol) = "指名旧符号 + grep 不到生产旧形态调用点".

    expected_occurrences==0 is load-bearing: a grep machine_check expecting >0 asserts the NEW
    symbol's presence, not the OLD symbol's absence — only the ==0 form is an old-path-removal
    assertion. Reuses RUN-044 DoneCriterion.machine_check (ClosureCriterion); the review side
    (review_finding) re-runs this exact grep over production code.
    """
    mc = dc.get("machine_check")
    if not isinstance(mc, dict):
        return False
    pattern = mc.get("verification_pattern")
    return (
        str(mc.get("verification_method", "")) == ClosureVerificationMethod.GREP.value
        and isinstance(pattern, str)
        and bool(pattern.strip())
        and mc.get("expected_occurrences") == 0
    )


def migration_class_task_ids(
    events: list[dict[str, object]], plan_id: str,
) -> dict[str, list[str]]:
    """{task_id: [matched migration concept ids]} for every plan task whose concept_refs include a
    migration concept (see migration_concept_ids / _task_concept_refs). Empty when no migration
    concepts exist or no plan task delivers one."""
    mig = migration_concept_ids(events)
    out: dict[str, list[str]] = {}
    if not mig:
        return out
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if a.get("plan_id") != plan_id:
            continue
        matched = sorted(_task_concept_refs(a) & mig)
        if matched:
            out[str(a.get("task_id", ""))] = matched
    return out


def task_migration_old_path_criteria(
    events: list[dict[str, object]], task_id: str,
) -> list[dict[str, object]]:
    """If task_id is migration-class (concept_refs ∩ migration concepts) AND has a published package,
    return its old-path-removal done_criteria (the grep machine_checks); else []. PUBLIC bridge so the
    review side (review_finding.find_migration_old_path_findings) reuses the SAME 'migration-class' +
    'old-path-removal criterion' definition as this planning gate — one source of truth, no drift.
    """
    mig = migration_concept_ids(events)
    if not mig:
        return []
    refs: set[str] = set()
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if str(a.get("task_id", "")) == task_id:
            refs = _task_concept_refs(a)  # latest-wins
    if not (refs & mig):
        return []
    dcs = _latest_published_done_criteria(events, task_id)
    if not dcs:
        return []
    return [dc for dc in dcs if is_old_path_removal_criterion(dc)]


def check_migration_old_path_removal(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """T-LRF-12 / migration-done-requires-old-path-removal@v1 (planning 侧).

    每个迁移类 plan task (concept_refs 含迁移概念) 且已发布 package 的, 其 done_criteria 必须含 ≥1 条
    old-path-removal 形态判据 (grep machine_check, expected_occurrences=0, 指名旧符号); 缺则 fail
    (拒冻结)。无迁移类 task / 无迁移概念 → pass (绝大多数 plan, 不破坏既有冻结)。

    诚实边界: 只检查已发布 package 的迁移类 task (结构化 machine_check 只在 published package 上)。
    未发包的 progressive-pending 迁移 task 在其 package-publish / 后续冻结时另行约束 (镜像
    coverage_complete 的 progressive-pending 处理)。读侧防御性兼容段豁免: 只要求"存在 ≥1 条移除判据",
    不扫码要求删读侧并集符号 —— 刻意保留的防御性读侧 fallback 永不被误要求删。
    """
    events = _read_events(towow_dir / "events.log")
    migration_tasks = migration_class_task_ids(events, plan_id)
    if not migration_tasks:
        return True, "no migration-class tasks in plan (concept_refs 无指向迁移不变量的概念)"
    offenders: list[str] = []
    checked: list[str] = []
    skipped_unpackaged: list[str] = []
    for tid, matched in sorted(migration_tasks.items()):
        dcs = _latest_published_done_criteria(events, tid)
        if dcs is None:
            skipped_unpackaged.append(tid)
            continue
        if any(is_old_path_removal_criterion(dc) for dc in dcs):
            checked.append(tid)
        else:
            offenders.append(f"{tid} (迁移 of {','.join(matched)})")
    if offenders:
        return False, (
            "迁移类 task 缺 old-path-removal 形态判据 (machine_check grep + expected_occurrences=0 "
            f"指名旧符号): {offenders[:5]} —— 新机制建好而旧生产路径仍可走 = 未完成, 拒冻结 "
            "(接 honesty-enforced-completeness-and-debt 完整性门家族)"
        )
    evidence = f"{len(checked)} 迁移类 task 含 old-path-removal 判据"
    if skipped_unpackaged:
        evidence += f"; {len(skipped_unpackaged)} 未发包 (progressive-pending, 发包时另验)"
    return True, evidence


# ─── 修2 / live-fire-default-three-point-enforcement@v1 第10道门 (新增能力 live-fire) ─────────
#
# new-capability-task-classifier@v1 + live-fire-machine-check-contract@v1 的 planning 侧冻结相位牙齿。
# 镜像上面 migration_old_path_removal 那一套: 把一类 task 挑出来 → 验其 done_criteria 含某形态判据 →
# 缺则 fail-closed。差别只在【怎么挑】与【判据形态】:
#   · 怎么挑 = 三信号判别 (任一命中即'新增能力/接线', fail-closed 偏严 —— 宁可误拦不漏放):
#       ① write_set 命中能力性路径 (daemon/orchestrator/gate/hook/feeder/l2 自修器官) —— 机械,
#         作者改不了"我写了这个文件"的事实 (复用 _collect_high_risk_tasks 读 write_set 的机械范式)。
#       ② done_criterion 引用一个账本此前【零出现】的 EventType —— 对一个【已在 enum 定义、但账本
#         零发射】的休眠类型, "本 task 将让系统真发射它" ≈ 新增能力签名。候选只取真实 EventType 枚举名
#         (非任意 PascalCase) 再核账本零出现, 避免乱命中。
#         ★ 能力边界 (写实, 别把 signal② 当无盲点的"客观签名"过度依赖): 候选集 = {EventType 枚举成员}
#           − 账本已出现 —— 故 signal② 只看得见 freeze 时【已是 enum 成员】的休眠类型。若某 plan 自己的
#           M-1.4 执行才把一个全新 EventType 加进 enums.py (enum 增补落在 M-1.3 freeze 之后), 该类型
#           freeze 时尚非候选 → signal② 对它结构性失明 (define+emit-in-one-plan 窄窗)。这类全新类型不
#           靠 signal② 兜底: 它落给 signal① —— 但仅当其发射器接线写进某能力路径器官才命中 (enums.py
#           本身非能力路径 marker, 单改 enum 不命中①) —— 外加 F-08j 活清单 / 下游 review 作 backstop。
#           结论: 没有任何【单一】信号是新增能力的客观签名; 三信号 OR + fail-closed 偏严 + 活清单才是
#           那张网, signal② 只是其中【针对已定义休眠类型】的一道, 非全覆盖。
#       ③ 自报 task_type 仅作加层不作唯一依据: task_type 永不用来【豁免】—— ①或②命中即判, 不论
#         task_type 自报 doc/refactor/config (signal ③ 的"误标也躲不过"就是这条 OR 逻辑的直接结果;
#         不存在'capability' 这种 task_type 值, 故 task_type 不作正向信号, 只作'不得豁免'约束)。
#   · 判据形态 = live-fire: machine_check test 型 + test_selector 非空 (指向集成测试)。
#
# 诚实边界 (与 live-fire-machine-check-contract@v1 层2 / per-task-vs-production-real-boundary 一致):
# 冻结相位只能机械验【形态】(test 型 + selector 在)。该集成测试是否真用 EventLog.all_records() 读账本
# 断 live 事件存在 + provenance (非空心) —— 由 review 侧 (method-execution-path/audit fork) + 执行侧
# (LB1 execution_done_recompute subprocess) 复验, 不在本冻结门内。本门只到 CLI-side fail-closed
# (run_all_blocking_checks → plan freeze CLI 拒 emit); "绕 CLI 直 emit 也拦"是修3 (completeness.py
# 总闸), 不在本 task scope。

# 能力性路径 markers —— concept scope 边界明确这是【活清单】(不规定枚举, 新增器官目录时同步更新)。
# 子串命中仓库相对路径即算。grounding: src/towow/l2 (自修器官)、l0/commit_gate (总闸)、glue/hooks
# (物理门)、cli_run_feeder (喂料)、execution_dispatch (派发)、completeness.py (完整性总闸)、各 *_gate。
_CAPABILITY_PATH_MARKERS: tuple[str, ...] = (
    "/l2/",            # 自修器官层: daemon / orchestrator / dispatch / escalation / feeder
    "daemon",
    "orchestrator",
    "/commit_gate/",   # M-0.5 总闸
    "_gate",           # novelty_gate / physical_gate / ...
    "/gate",
    "/hooks/",         # PreToolUse / Stop / UserPromptSubmit 物理门
    "feeder",          # cli_run_feeder
    "dispatch",        # execution_dispatch
    "completeness",    # completeness.py 完整性总闸
    "escalation",
    "autopilot",
)


def _capability_path_hit(path: str) -> str | None:
    """The first capability-path marker `path` matches (classifier signal ①), or None.

    子串命中即算 —— "我写了 daemon/gate/feeder 这个文件" 是作者改不了的机械事实。`path` 是仓库相对
    路径 (write_set entity_id, 形如 'harness/src/towow/l2/foo.py')。匹配大小写无关。
    """
    low = path.lower()
    for marker in _CAPABILITY_PATH_MARKERS:
        if marker in low:
            return marker
    return None


def _task_write_set_paths(after: dict[str, object]) -> list[str]:
    """A task's write_set file paths. write_set entries are {entity_type, entity_id}; only
    entity_type=='file' carries a path (entity_type=='concept' is a concept claim, not a file)."""
    out: list[str] = []
    ws = after.get("write_set")
    if isinstance(ws, list):
        for w in ws:
            if isinstance(w, dict) and w.get("entity_type") == "file":
                eid = w.get("entity_id")
                if isinstance(eid, str) and eid:
                    out.append(eid)
    return out


def _ledger_event_types(events: list[dict[str, object]]) -> set[str]:
    """Every event_type that has ALREADY appeared in the ledger (classifier signal ② baseline)."""
    return {_event_type(e) for e in events if _event_type(e)}


def _task_done_criteria_text(events: list[dict[str, object]], task_id: str, after: dict[str, object]) -> str:
    """All done_criteria text for a task (for classifier signal ②'s event-type scan): the inline
    TaskNodeCreated.done_criteria (free-text strings) PLUS the latest published package's structured
    done_criteria (serialized). Scanning both because the inline criteria are where a freshly-planned
    task most naturally names the new event it will produce, before any package is published."""
    parts: list[str] = []
    inline = after.get("done_criteria")
    if isinstance(inline, list):
        for dc in inline:
            parts.append(dc if isinstance(dc, str) else json.dumps(dc, ensure_ascii=False, sort_keys=True))
    pub = _latest_published_done_criteria(events, task_id)
    if pub:
        for dc in pub:
            parts.append(json.dumps(dc, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def _new_event_types_referenced(text: str, candidate_event_types: set[str]) -> set[str]:
    r"""EventType enum names referenced in `text`, restricted to `candidate_event_types`
    (= real EventType names with ZERO prior ledger occurrence). Boundary = ASCII-only lookaround
    (?<![A-Za-z0-9_])…(?![A-Za-z0-9_]), NOT Python's `\b`: `re` counts CJK as word characters, so
    a name pressed directly against Chinese (the dominant done_criteria writing style here, e.g.
    '断言PlanFreezed事件') would have no `\b` boundary and silently miss — a fail-open this
    forward-looking gate must not have ('宁可误拦不漏放'). The ASCII-only boundary still refuses
    substring hits ('PlanFreezedPayload' does not count). Candidate-restriction keeps signal ② from
    firing on arbitrary PascalCase prose — only names the system could actually emit count."""
    return {
        name
        for name in candidate_event_types
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text)
    }


def new_capability_task_ids(events: list[dict[str, object]], plan_id: str) -> dict[str, str]:
    """{task_id: why} for every plan task the new-capability-task-classifier@v1 三信号 flags as
    '新增能力/接线' (任一信号命中即判, fail-closed 偏严). Empty when no plan task hits any signal.

    PUBLIC bridge (mirrors migration's task_migration_old_path_criteria): the review side reuses the
    SAME classifier so 'review 侧验判别没漏放' keys on one source of truth — no drift between the
    planning gate's classification and the review check's.

    latest-wins per task (a task may be re-created/re-scoped); classification reads the latest
    TaskNodeCreated after-state for write_set + inline done_criteria.
    """
    candidates = {e.value for e in EventType} - _ledger_event_types(events)
    latest: dict[str, dict[str, object]] = {}
    for e in events:
        if _event_type(e) != "TaskNodeCreated":
            continue
        a = _after_state(e)
        if a.get("plan_id") != plan_id:
            continue
        tid = str(a.get("task_id", ""))
        if tid:
            latest[tid] = a  # later events overwrite earlier (file order = commit order)
    out: dict[str, str] = {}
    for tid, a in latest.items():
        # signal ① — write_set hits a capability path (most reliable; task_type can't exempt it)
        hit_path = next(
            ((p, m) for p in _task_write_set_paths(a) if (m := _capability_path_hit(p))),
            None,
        )
        if hit_path:
            out[tid] = f"signal①: write_set 命中能力性路径 '{hit_path[0]}' (marker={hit_path[1]})"
            continue
        # signal ② — done_criterion references an already-DEFINED-but-zero-ledger (dormant) event type.
        # Boundary (see §10-gate comment '能力边界'): candidates = enum members − ledger-seen, so signal②
        # is structurally blind to a type THIS plan's own M-1.4 execution adds to enums.py post-freeze
        # (define+emit-in-one-plan); that case falls to signal① (only if the emitter wiring lands in a
        # capability-path organ) + F-08j live-list, NOT to signal②. signal② is one secondary net, not a
        # complete objective signature.
        new_types = _new_event_types_referenced(_task_done_criteria_text(events, tid, a), candidates)
        if new_types:
            out[tid] = f"signal②: done_criterion 引用账本零出现的新事件类型 {sorted(new_types)}"
    return out


def is_livefire_machine_check_criterion(dc: dict[str, object]) -> bool:
    """True iff a done_criterion is a live-fire form criterion — the FREEZE-TIME mechanically-checkable
    shape of live-fire-machine-check-contract@v1 层2: machine_check.verification_method == test
    (∈ GATE_RECOMPUTABLE_VERIFICATION_METHODS) + a non-empty test_selector (pointing at an integration
    test). Mirrors is_old_path_removal_criterion's 'form gate' role.

    冻结相位只验【形态】; 该测试是否真用 EventLog.all_records() 读账本断 live 事件存在 + provenance
    (层2 的语义、非空心) 由 review 侧 (method-execution-path/audit fork) + 执行侧 (LB1
    execution_done_recompute subprocess) 复验 (per-task-vs-production-real-boundary /
    self-applying-freeze-gate-g1-g5 递归层)。
    """
    mc = dc.get("machine_check")
    if not isinstance(mc, dict):
        return False
    selector = mc.get("test_selector")
    return (
        str(mc.get("verification_method", "")) == ClosureVerificationMethod.TEST.value
        and isinstance(selector, str)
        and bool(selector.strip())
    )


def check_new_capability_tasks_have_livefire_machine_check(towow_dir: Path, plan_id: str) -> tuple[bool, str]:
    """修2 / live-fire-default-three-point-enforcement@v1 第10道门 (planning 侧冻结相位牙齿).

    每个被 new-capability-task-classifier@v1 三信号判为'新增能力/接线'的 plan task 且已发布 package 的,
    其 done_criteria 必须含 ≥1 条 live-fire 形态判据 (machine_check test 型 + test_selector 指向集成
    测试, 见 is_livefire_machine_check_criterion); 缺则 fail-closed 拒冻结 (evidence 指名缺的 task)。
    无新增能力 task / 三信号均未命中 → pass (绝大多数 plan, 不破坏既有冻结)。

    诚实边界 (镜像 check_migration_old_path_removal): 只检查【已发布 package】的新增能力 task —— 结构化
    machine_check 只在 published package 上 (inline TaskNodeCreated.done_criteria 是自由文本无 machine_
    check)。未发包的 progressive-pending task 在其 package-publish / 后续冻结时另验 (镜像
    coverage_complete 的 progressive-pending 处理)。本门只到 CLI-side fail-closed; 绕 CLI 直 emit 的
    总闸兜底归修3 (completeness.py)。
    """
    events = _read_events(towow_dir / "events.log")
    nc_tasks = new_capability_task_ids(events, plan_id)
    if not nc_tasks:
        return True, "no new-capability tasks in plan (三信号 write_set/新事件类型 均未命中)"
    offenders: list[str] = []
    checked: list[str] = []
    skipped_unpackaged: list[str] = []
    for tid, why in sorted(nc_tasks.items()):
        dcs = _latest_published_done_criteria(events, tid)
        if dcs is None:
            skipped_unpackaged.append(tid)
            continue
        if any(is_livefire_machine_check_criterion(dc) for dc in dcs):
            checked.append(tid)
        else:
            offenders.append(f"{tid} ({why})")
    if offenders:
        return False, (
            "新增能力 task 缺 live-fire 形态判据 (machine_check test 型 + test_selector 指向集成测试): "
            f"{offenders[:5]} —— 新增能力建好而 done_criterion 没锚到真账本 (test 型读 live 签名) = "
            "做完没接, 拒冻结 (接 live-fire-default-three-point-enforcement 三处咬合)"
        )
    evidence = f"{len(checked)} 新增能力 task 含 live-fire 形态判据"
    if skipped_unpackaged:
        evidence += f"; {len(skipped_unpackaged)} 未发包 (progressive-pending, 发包时另验)"
    return True, evidence


def run_all_blocking_checks(
    towow_dir: Path, plan_id: str, store: ProjectionStore | None = None,
) -> tuple[bool, list[PlanBlockingCheck]]:
    """Run 13 blocking_check for plan_id (5 spec §3.8 + F8 model_tier + T-L1-25 + Layer2-缺口3
    cross-plan write conflict + T-L1-26 + T-L1-31 red_line uncertainty + T-LRF-12
    migration_old_path_removal + 修2 new_capability_livefire_machine_check + M-2.1 §4.1 caller-2
    cia_dependency_scan).

    ``store`` (optional) — a ProjectionStore already caught up to the freeze point, reused by the
    M-2.1 §4.1 caller-2 cia_dependency_scan check (the `plan freeze` CLI passes its freeze store so
    the scan doesn't re-catchup). When None, the scan builds one from ``towow_dir`` + events.log.
    """
    checks = [
        ("planning.all_tasks_self_contained", check_all_tasks_self_contained),
        ("planning.coverage_complete", check_coverage_complete),
        ("planning.no_circular_dependency", check_no_circular_dependency),
        ("planning.critical_path_identified", check_critical_path_identified),
        ("planning.high_risk_tasks_have_review", check_high_risk_tasks_have_review),
        ("planning.all_tasks_have_model_tier", check_all_tasks_have_model_tier),
        ("planning.no_resource_conflict", check_no_resource_conflict),  # T-L1-25
        # Layer2 缺口3 / Finding A — cross-plan twin of no_resource_conflict (CLI-side fail-closed,
        # same enforcement tier as its intra-plan sibling; both block a freeze when write-sets
        # collide without a serializing edge).
        ("planning.no_cross_plan_resource_conflict", check_no_cross_plan_resource_conflict),
        ("planning.model_tier_assignments_have_evidence", check_model_tier_assignments_have_evidence),  # T-L1-26
        ("planning.no_unresolved_red_line_uncertainty", check_no_unresolved_red_line_uncertainty),  # T-L1-31
        ("planning.migration_old_path_removal", check_migration_old_path_removal),  # T-LRF-12
        # 修2 / live-fire-default-three-point-enforcement@v1 第10道门: 被 new-capability-task-
        # classifier 三信号判为新增能力的 task 必须带 live-fire test 型 machine_check done_criterion,
        # 缺则冻结 fail-closed (镜像 migration_old_path_removal 的 class→form→fail-closed 范式)。
        (
            "planning.new_capability_livefire_machine_check",
            check_new_capability_tasks_have_livefire_machine_check,
        ),
    ]
    results: list[PlanBlockingCheck] = []
    all_passed = True
    for check_id, fn in checks:
        passed, evidence = fn(towow_dir, plan_id)
        results.append(PlanBlockingCheck(check_id=check_id, status="passed" if passed else "failed", evidence=evidence))
        if not passed:
            all_passed = False
    # M-2.1 §4.1 消费方发现 caller-2 — record-only transitive downstream scan (status 永远 passed).
    # What makes it enforce: completeness.py REQUIRES planning.cia_dependency_scan present on
    # PlanFreezed, so a freeze that skips the scan (check absent) is rejected at the M-0.5 总闸.
    scan_passed, scan_evidence = check_cia_dependency_scan(towow_dir, plan_id, store=store)
    results.append(
        PlanBlockingCheck(
            check_id="planning.cia_dependency_scan",
            status="passed" if scan_passed else "failed",
            evidence=scan_evidence,
        ),
    )
    if not scan_passed:
        all_passed = False
    return all_passed, results


__all__ = [
    "MIGRATION_INVARIANT_CONCEPT_ID",
    "CoverageMatrixEntry",
    "CriticalPathResult",
    "PlanBatchInfo",
    "PlanBlockingCheck",
    "PlanFreezedPayload",
    "build_coverage_matrix",
    "check_all_tasks_have_model_tier",
    "check_all_tasks_self_contained",
    "check_cia_dependency_scan",
    "check_coverage_complete",
    "check_critical_path_identified",
    "check_high_risk_tasks_have_review",
    "check_migration_old_path_removal",
    "check_model_tier_assignments_have_evidence",
    "check_new_capability_tasks_have_livefire_machine_check",
    "check_no_circular_dependency",
    "check_no_cross_plan_resource_conflict",
    "check_no_resource_conflict",
    "check_no_unresolved_red_line_uncertainty",
    "cia_dependency_scan",
    "compute_critical_path",
    "is_livefire_machine_check_criterion",
    "is_old_path_removal_criterion",
    "migration_class_task_ids",
    "migration_concept_ids",
    "new_capability_task_ids",
    "resolve_plan_brief_completion_condition",
    "run_all_blocking_checks",
    "task_migration_old_path_criteria",
]
