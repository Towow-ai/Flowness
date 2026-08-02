"""M-1.6 fix `fix complete` 的 5 项 blocking_check (RUN-036 T-L1-69 真验 wire).

spec source: 04-l1-intelligence/M-1.6-fix-skill-detailed-design.md §5 / §6.1
  (fix self_check.blocking_checks: closure_criteria_self_verified / ripple_targets_synced /
   forbidden_residuals_zero / consensus_respected / review_plan_respected)

RUN-036 T-L1-69 之前: 4 项 (ripple/residual/consensus/review_plan) 走 `_loose_or_caller` 恒
return True (假门)。现真验:

- **closure_criteria_self_verified / ripple_targets_synced / forbidden_residuals_zero**: 门用
  closure_verification 内核 (verify_closure_against_contract) **独立复算** — 不信 fixer 自报 passed。
  GREP/TEST criterion 门自己跑; forbidden_residual 用合约自带 pattern 门自己 grep found=0; ripple
  覆盖 + sync_status。任一 fail → resolved 强门拒。含 not_recomputable (manual_reasoning 等) → 降级。
- **consensus_respected / review_plan_respected**: 完整自动检测 (fix patches 含 concept_event
  without supersede / patches file paths vs review_plan dimensions) 需要完整 envelope context
  (patches + concept_graph + review_plan projection), CLI 命令层不全 → 现做 attestation 门 (要求
  fixer 显式非空声明, 声明违反则真 fail, 非恒 True), 完整自动检测挂债 (依赖 fix-self-check fork
  envelope context, T-L1-72)。明账 — 不冒充已自动验证 (§11 尺子第二阶段)。

FixCompleted 是 provisional closure (M-1.6 §1.1): verified closure 权在 M-1.5 的
FindingResolved(closure_state=closed), 故 fix 不 emit FindingResolved。

---

M-2.1 §4.1 消费方发现 caller-4 (模块健康度第四刀; 范本 = caller-1 consensus freeze / caller-2 plan
freeze / caller-3 review plan-create) — `fix complete` 跑 record-only CIA forward_slice 记一次修复
的 cascade blast radius。caller-1/2 在 *建立* 共识/计划时查下游; caller-4 在 *修完* 一处时, 把这处
修改触及的概念的**传递性下游**摆出来 (谁会被这次修复波及) + 记进 evidence。

🔴 caller-4 绝不主动 cascade: 不调 compute_cascade / is_cascade_trigger, 不 emit InvalidationCascade,
不碰 l2/invalidation_cascade.py。那是断点 A 的 cascade daemon 递归扩散内核 (现 stub 不点火), fix 链
调它 = 提前点着断点 A = 违 INF-003。FixCompleted.cascade_scope 硬编码 "provisional" (M-1.6 §1.1 —
verified cascade 权在 M-1.5 FindingResolved)。caller-4 只读 impact_graph 做一次同步 forward_slice
(+ 可选 state_machine_check), record-only, 不写任何 event, 不触发任何递归。

enforce 牙齿 = completeness.py REQUIRES fix.cia_cascade_scan present on FixCompleted —— 三种 outcome
(resolved / partially_resolved / needs_further_review) 全要带它 (scan 无条件跑, 不塞 outcome 分支),
跳过即 M-0.5 总闸物理拒。空概念集 (code-only fix 不动概念图) 仍是合法的成功扫描: scanned_concepts=[]
+ watermark 证明扫了 (可复算)。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.l1.closure_verification import (
    ClosureResult,
    ClosureVerificationOutcome,
    GrepRunner,
    LedgerRunner,
    PytestRunner,
    verify_closure_against_contract,
)
from towow.schemas.payloads.finding import ClosureContract

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class FixBlockingCheck(BaseModel):
    """One fix self_check.blocking_checks entry (per M-1.6 §5)."""

    model_config = _STRICT

    check_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "pending"]
    evidence: str = Field(min_length=1)


class FixCompleteVerdict(BaseModel):
    """fix complete 5 blocking_check 综合判定 (T-L1-69 + T-L1-76)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    all_passed: bool  # resolved 强门: 5 项全 passed
    downgrade_required: bool  # 含 not_recomputable (门不可独立复算) → 不能 resolved, 应降级 partial
    checks: list[FixBlockingCheck] = Field(default_factory=list)
    closure_outcome: ClosureVerificationOutcome | None = None  # contract 真验时的内核结果


def _criteria_check(outcome: ClosureVerificationOutcome) -> FixBlockingCheck:
    crit = [v for v in outcome.verdicts if v.kind == "criterion"]
    failed = [v for v in crit if v.status == "failed"]
    not_recomp = [v for v in crit if v.status == "not_recomputable"]
    if failed:
        return FixBlockingCheck(
            check_id="fix.closure_criteria_self_verified", status="failed",
            evidence="; ".join(v.detail for v in failed)[:400],
        )
    if not_recomp:
        return FixBlockingCheck(
            check_id="fix.closure_criteria_self_verified", status="failed",
            evidence="门不可独立复算的 criterion (禁走 resolved 强门, 降级): "
                     + "; ".join(v.item for v in not_recomp)[:300],
        )
    n = len(crit)
    return FixBlockingCheck(
        check_id="fix.closure_criteria_self_verified", status="passed",
        evidence=f"门复算 {n} 条 closure_criteria 全过 (GREP/TEST 门自己跑, 不信自报)",
    )


def _ripple_check(outcome: ClosureVerificationOutcome) -> FixBlockingCheck:
    rip = [v for v in outcome.verdicts if v.kind == "ripple"]
    failed = [v for v in rip if v.status == "failed"]
    if failed:
        return FixBlockingCheck(
            check_id="fix.ripple_targets_synced", status="failed",
            evidence="; ".join(v.detail for v in failed)[:400],
        )
    return FixBlockingCheck(
        check_id="fix.ripple_targets_synced", status="passed",
        evidence=f"{len(rip)} 个 ripple_target 全 synced/not_applicable(+evidence)",
    )


def _residual_check(outcome: ClosureVerificationOutcome) -> FixBlockingCheck:
    res = [v for v in outcome.verdicts if v.kind == "residual"]
    failed = [v for v in res if v.status == "failed"]
    not_recomp = [v for v in res if v.status == "not_recomputable"]
    if failed:
        return FixBlockingCheck(
            check_id="fix.forbidden_residuals_zero", status="failed",
            evidence="; ".join(v.detail for v in failed)[:400],
        )
    if not_recomp:
        return FixBlockingCheck(
            check_id="fix.forbidden_residuals_zero", status="failed",
            evidence="门不可独立复算的 residual check_method (降级): "
                     + "; ".join(v.item for v in not_recomp)[:300],
        )
    return FixBlockingCheck(
        check_id="fix.forbidden_residuals_zero", status="passed",
        evidence=f"门用合约 pattern 自己 grep, {len(res)} 个 forbidden_residual 全 found=0",
    )


def _attestation_check(check_id: str, attested: bool, evidence: str | None, topic: str) -> FixBlockingCheck:
    """consensus_respected / review_plan_respected — attestation 门 (非恒 True).

    完整自动检测依赖 fork envelope context (T-L1-72) — 现要求 fixer 显式非空 attestation,
    声明违反 (attested=False) 则真 fail。明账: 不冒充已自动验证。
    """
    if not attested:
        return FixBlockingCheck(
            check_id=check_id, status="failed",
            evidence=f"{topic} attestation=False (fixer 声明违反 — 拒)",
        )
    if not (evidence and evidence.strip()):
        return FixBlockingCheck(
            check_id=check_id, status="failed",
            evidence=f"{topic} attested 但缺非空 evidence (防 hollow 自报 — 拒)",
        )
    return FixBlockingCheck(
        check_id=check_id, status="passed",
        evidence=f"attestation (CLI 层, 完整自动检测依赖 fork envelope context T-L1-72): {evidence.strip()[:200]}",
    )


def run_fix_blocking_checks_against_contract(
    *,
    contract: ClosureContract,
    closure_result: ClosureResult,
    repo_dir: Path,
    extra_bases: tuple[Path, ...] = (),
    consensus_attested: bool,
    consensus_evidence: str | None,
    review_plan_attested: bool,
    review_plan_evidence: str | None,
    grep_runner: GrepRunner | None = None,
    pytest_runner: PytestRunner | None = None,
    # f-stale-closure-contract-permanently-unclosable: 不透传 = 任何带 ledger_event 判据的合约在 fix
    # 侧闭合门 fail-closed 明拒 (同 git_diff 未接线), 该 finding 物理不可闭合。
    ledger_runner: LedgerRunner | None = None,
) -> FixCompleteVerdict:
    """T-L1-69 + T-L1-76: 5 fix blocking_check 真验 (review finding 强门, 读合约对合约).

    3 项 (criteria/ripple/residual) 门用 closure_verification 内核独立复算; 2 项 (consensus/
    review_plan) attestation 门。all_passed = 5 项全 passed 且无 not_recomputable。

    extra_bases (finding-gw1-machine-check-path-defect 缺口 a): 透传给内核, 让 fix-after 闭合环也能
    对按中立性解耦在 repo_dir 外的包路径复算 (caller 算 git 仓库根传进; default () = 旧行为)。
    """
    outcome = verify_closure_against_contract(
        contract=contract, result=closure_result, repo_dir=repo_dir, extra_bases=extra_bases,
        grep_runner=grep_runner, pytest_runner=pytest_runner, ledger_runner=ledger_runner,
    )
    checks = [
        _criteria_check(outcome),
        _ripple_check(outcome),
        _residual_check(outcome),
        _attestation_check("fix.consensus_respected", consensus_attested, consensus_evidence, "consensus"),
        _attestation_check("fix.review_plan_respected", review_plan_attested, review_plan_evidence, "review_plan"),
    ]
    all_passed = all(c.status == "passed" for c in checks)
    return FixCompleteVerdict(
        all_passed=all_passed,
        downgrade_required=outcome.downgrade_required,
        checks=checks,
        closure_outcome=outcome,
    )


# ── M-2.1 §4.1 消费方发现 caller-4 — fix complete CIA cascade blast-radius scan ───────────────────


def fix_changed_concepts(
    event_log: EventLog,
    fix_id: str,
    semantic_upgrade: dict[str, object] | None,
) -> tuple[list[str], list[dict[str, str]]]:
    """The concepts a fix touched + the concept state changes it declared.

    源 (both optional, either may be empty):
      - FixProposed(fix_id).after_state.affected_entities — filtered to entity_type=="concept"
        entries' entity_id (M-1.6 §3.1: affected_entities is [{entity_type, entity_id, ...}]; a
        code-only fix's affected_entities has no concept entries → empty).
      - FixCompleted.semantic_upgrade_declaration.affected_concepts (O-14 §3.2; this is the
        FixCompleted being built — passed in as the parsed --semantic-upgrade-file dict, not yet
        emitted, so it's read from the param, not the event log).

    Returns (sorted unique concept_ids, concept_state_changes). concept_state_changes are the
    declaration's concept_state_changes[] = [{concept_id, from_state, to_state}] (used by the
    optional state_machine_check). Both empty for a code-only fix that declares no semantic upgrade.

    NOTE: we read FixProposed via the type index (get_events_by_type), matching the sibling
    _trace_fix_finding_id helper (same provenance source, one fewer fabrication surface than letting
    the fixer self-report the affected concepts).
    """
    from towow.schemas.enums import EventType

    concepts: set[str] = set()
    # FixProposed affected_entities (entity_type=="concept").
    for ev in event_log.get_events_by_type(EventType.FIX_PROPOSED):
        p = ev.payload if isinstance(ev.payload, dict) else {}
        after = p.get("after_state") if isinstance(p.get("after_state"), dict) else p
        if not isinstance(after, dict) or after.get("fix_id") != fix_id:
            continue
        affected = after.get("affected_entities")
        if isinstance(affected, list):
            for ent in affected:
                if (
                    isinstance(ent, dict)
                    and ent.get("entity_type") == "concept"
                    and isinstance(ent.get("entity_id"), str)
                    and ent["entity_id"]
                ):
                    concepts.add(str(ent["entity_id"]))
    # FixCompleted semantic_upgrade_declaration.affected_concepts (the in-flight declaration).
    state_changes: list[dict[str, str]] = []
    if isinstance(semantic_upgrade, dict):
        ac = semantic_upgrade.get("affected_concepts")
        if isinstance(ac, list):
            for c in ac:
                if isinstance(c, str) and c:
                    concepts.add(c)
        csc = semantic_upgrade.get("concept_state_changes")
        if isinstance(csc, list):
            for ch in csc:
                if (
                    isinstance(ch, dict)
                    and isinstance(ch.get("concept_id"), str)
                    and ch["concept_id"]
                    and isinstance(ch.get("to_state"), str)
                    and ch["to_state"]
                ):
                    state_changes.append(
                        {
                            "concept_id": str(ch["concept_id"]),
                            "from_state": str(ch.get("from_state", "")),
                            "to_state": str(ch["to_state"]),
                        },
                    )
    return sorted(concepts), state_changes


def cia_cascade_scan(
    changed_concepts: list[str],
    state_changes: list[dict[str, str]],
    store: ProjectionStore,
) -> dict[str, object]:
    """M-2.1 §4.1 消费方发现 caller-4 — for every concept this fix changed, run a real CIA
    forward_slice (沿 impact_graph 概念出边 BFS, depth ≤ 5) and collect the transitive downstream
    entities (depth ≥ 1) it reaches, minus the changed-concept set itself = the cascade blast radius
    of this fix. Optionally, for each declared (concept, to_state) state change, run a real CIA
    state_machine_check and record the transition legality + reason in the SAME evidence.

    🔴 record-only, NO cascade trigger. status is ALWAYS passed (it's 发现并记录 — surfacing the
    downstream a fix could ripple to, NOT a blocker). This NEVER calls compute_cascade /
    is_cascade_trigger and NEVER emits InvalidationCascade (M-1.6 哲学 = 不主动 cascade; auto-cascade
    is 断点 A's daemon, INF-003-gated). state_machine_check is likewise record-only — an illegal
    transition is recorded (is_valid_transition=False + reason) but does NOT fail the check (fix may
    legitimately propose a transition the projection can't yet validate; M-1.5 owns verified closure).

    Direction (empirically verified, NOT trusted from docstring — see test_fix_completed_checks probe):
    a raw `B --dependency--> X` edge (B depends on X) normalizes to impact_graph `X→B`, so
    forward_slice(X) returns B (X 的下游 = 谁依赖 X). Same forward direction as caller-1/caller-2.

    Empty changed_concepts (a code-only fix that touched no concept) is STILL a successful scan:
    downstream_entities=[] + state_machine_checks=[] + projection_watermark proves the scan ran and
    is reproducible (re-run forward_slice at that watermark → same answer). 诚实记 scanned_concepts=[].

    ``store`` — a ProjectionStore already caught up to the fix-complete point (reads the materialized
    impact_graph 二阶 projection + concept_graph.nodes for state_machine_check). The CLI passes its
    own caught-up store (fix complete had no existing store — built +真 catchup, sibling caller-3).

    Returns evidence as a nested dict (caller-1/3 path — NOT json.dumps'd into one string). NOTE the
    commit-gate envelope stamping coerces each evidence leaf VALUE to a string (canonical
    BlockingCheck.evidence is dict[str,str]) — shared framework behavior; the stringified values
    parse back (ast.literal_eval) for re-computation. watermark always present.
    """
    from towow.l1.cia_query import CIAQueryService

    cia = CIAQueryService(store)
    changed_set = set(changed_concepts)
    scanned = sorted(changed_set)
    seen: set[str] = set()
    # (depth, entity_id) → entry so the sort key stays int/str (not object-typed dict values).
    collected: list[tuple[int, str, dict[str, object]]] = []
    # Seed watermark from the store so an empty fix (zero changed concepts) still records the real
    # projection position (= store.cursor-1) — the value every forward_slice would return.
    watermark = max(0, store.cursor - 1)
    for concept_id in scanned:
        result = cia.forward_slice(concept_id)
        watermark = result.projection_watermark
        for hit in result.results:
            target_id = hit.entity.entity_id
            # depth ≥ 1 by construction (start node never in results); 去掉本 fix 改的 concepts
            # (互相依赖是已知的, 下游 blast radius 指改动集 *外* 受波及 entity).
            if hit.depth < 1 or target_id in changed_set or target_id in seen:
                continue
            seen.add(target_id)
            collected.append(
                (
                    hit.depth,
                    target_id,
                    {
                        "entity_id": target_id,
                        "entity_type": hit.entity.entity_type,
                        "depth": hit.depth,
                        "path": list(hit.path),
                    },
                ),
            )
    collected.sort(key=lambda t: (t[0], t[1]))
    downstream: list[dict[str, object]] = [entry for _depth, _tid, entry in collected]

    # Optional state_machine_check — record-only transition legality for each declared state change.
    sm_checks: list[dict[str, object]] = []
    for ch in state_changes:
        sm = cia.state_machine_check(ch["concept_id"], ch["to_state"])
        watermark = sm.projection_watermark
        sm_checks.append(
            {
                "concept_id": ch["concept_id"],
                "from_state_declared": ch.get("from_state", ""),
                "to_state": ch["to_state"],
                "current_state": sm.current_state,
                "is_valid_transition": sm.is_valid_transition,
                "allowed_transitions": list(sm.allowed_transitions),
                "reason": sm.reason,
            },
        )

    return {
        "scanned_concepts": scanned,
        "downstream_entities": downstream,
        "state_machine_checks": sm_checks,
        "projection_watermark": watermark,
    }


def build_fix_cia_cascade_check(
    event_log: EventLog,
    fix_id: str,
    semantic_upgrade: dict[str, object] | None,
    store: ProjectionStore,
) -> dict[str, object]:
    """Assemble the caller-4 fix.cia_cascade_scan blocking_check (status ALWAYS passed, record-only).

    Source the changed concepts + state changes (fix_changed_concepts), run the cascade scan, and
    wrap the result as a plain blocking_check dict with NESTED-DICT evidence — exactly caller-1/2/3's
    shape (NOT a FixBlockingCheck, whose evidence is a single str the envelope builder would drop:
    `_coerce_self_check` keeps a dict evidence's named keys but coerces a non-dict evidence to {} →
    structural floor {"check_id": ...}, losing the watermark). The nested dict's top-level keys
    (scanned_concepts / downstream_entities / state_machine_checks / projection_watermark) survive the
    commit-gate envelope stamping; each VALUE is str()'d (canonical BlockingCheck.evidence is
    dict[str,str], shared framework behavior — same as caller-1) and parses back with ast.literal_eval
    for re-computation. watermark always present.

    Called UNCONDITIONALLY by `fix complete` (outside the outcome-if) so all three outcomes carry it
    (completeness.py requires it on FixCompleted regardless of outcome).
    """
    changed, state_changes = fix_changed_concepts(event_log, fix_id, semantic_upgrade)
    evidence = cia_cascade_scan(changed, state_changes, store)
    return {
        "check_id": "fix.cia_cascade_scan",
        "status": "passed",
        "evidence": evidence,
    }


__all__ = [
    "FixBlockingCheck",
    "FixCompleteVerdict",
    "build_fix_cia_cascade_check",
    "cia_cascade_scan",
    "fix_changed_concepts",
    "run_fix_blocking_checks_against_contract",
]
