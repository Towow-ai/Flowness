"""M-1.4 execution `work complete` 的 5 项 blocking_check (T1 — E.4 closure).

spec source: 04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §3.6 / §5
  (execution self_check.blocking_checks: done_criteria_satisfied / actual_set_recorded /
   obligations_maintained / no_unhandled_mismatch / git_committed)

设计同 plan_freezed.py: 能从 event log / git 证据机械导出的 check 就导出 (不自报),
需要 execution session 判断的 check (done_criteria) 要求 caller 提供非空 evidence,
任一 fail → fail-closed, `work complete` 不 emit TaskRunCompleted。

v3-initial heuristic (跟 plan blocking_check 同等成熟度): 部分 check 是 loose 证据,
真严格化随 M-1.4 fork 真跑 + mismatch/advisor 事件注册后收紧。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Import the leaf segment helper directly (not the event_log package __init__) — segments.py has
# no schema deps, so this avoids an import cycle (event_log.py imports schemas.payloads.registry).
from towow.l0.event_log.segments import iter_raw_event_lines

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# Per-check "method" — 这个 check 用什么手段机器复核的 (保留 method 真痕, 让审计能从耐久账本
# 复核反假闸到底用什么手段验的, 不只看结论)。f-glob-blocking-check-evidence-wiped: 旧 evidence
# 是单 str, 进 envelope builder 时被 isinstance(非 dict) 判抹成 {"check_id": cid} floor → 闸跑没跑、
# 各项什么手段、什么结论在真相源里零可证。统一成 dict[str,str] (= canonical envelope.BlockingCheck.
# evidence 类型) 后 builder 原样保留命名键, method/per-check 真痕落进账本。
_CHECK_METHODS: dict[str, str] = {
    "execution.done_criteria_satisfied": (
        "session-asserted-evidence (machine-recompute via grep/test/git_diff subprocess "
        "when task carries a gate-recomputable machine_check)"
    ),
    "execution.actual_set_recorded": "event-log-scan (PatchProposed) / caller-declared patch_summary",
    "execution.obligations_maintained": (
        "obligation_lifecycle_state projection + event-log-scan (ObligationViolated)"
    ),
    "execution.no_unhandled_mismatch": (
        "event-log-scan (MismatchDetected vs MismatchResolutionDecided by run_id)"
    ),
    "execution.git_committed": "git cat-file -t (commit object verification)",
    "execution.sis_declared": "touched-node declaration (SIS 起始影响集)",
    "execution.deploy_byte_identity": (
        "deploy byte-identity recompute (sha256(render(src)) == sha256(deployed live) for "
        "actual_write_set files hitting src/towow/{skills,glue}; fail-closed on src↔live drift)"
    ),
    "execution.livefire_recomputed": (
        "live-fire test_selector machine-recompute via pytest subprocess (reads canonical "
        "ledger; vacuous when task carries no test-type live-fire machine_check)"
    ),
    "execution.aborted": "abort terminal-state (success 5 门跳过)",
}


def execution_evidence(check_id: str, summary: str) -> dict[str, str]:
    """把一项 check 的 per-check summary 包成 canonical dict[str,str] evidence 形态。

    固定键 {summary, method}: summary = 该 check 的 per-check 真痕 (passed/failed 的具体证据串);
    method = 该 check 用什么手段机器复核的 (_CHECK_METHODS)。统一 ExecutionBlockingCheck.evidence
    与 canonical envelope.BlockingCheck.evidence (dict[str,str]) 的类型契约 — 这样进 envelope
    builder._coerce_self_check 时按 dict 原样保留命名键 (不再因非 dict 被抹成 {"check_id": cid}
    零可证 floor; 根治 f-glob-blocking-check-evidence-wiped)。
    """
    return {"summary": summary, "method": _CHECK_METHODS.get(check_id, "n/a")}


class ExecutionBlockingCheck(BaseModel):
    """One execution self_check.blocking_checks entry (per M-1.4 §5).

    evidence 是 dict[str,str] (统一 canonical envelope.BlockingCheck.evidence 的类型契约,
    f-glob-blocking-check-evidence-wiped 根治): 固定键 summary (per-check 真痕) + method
    (机器复核手段); 经 execution_evidence() 构造。需含非空 summary (旧 str min_length=1 的
    "evidence 必有真内容" 保证, 不退化成空 floor)。
    """

    model_config = _STRICT

    check_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "pending"]
    evidence: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_summary(self) -> Self:
        if not self.evidence.get("summary", "").strip():
            raise ValueError(
                f"ExecutionBlockingCheck check_id={self.check_id} requires non-empty "
                "evidence['summary'] (per-check 真痕不可空 — 防 hollow floor)",
            )
        return self


def _read_events(events_log: Path) -> list[dict[str, object]]:
    # T-L0-04: read across all physical segments (base events.log + rotated events/hot/*.jsonl),
    # not just the base file — these execution gates ("尺子") must see post-rotation events or
    # `work complete` would judge done_criteria / mismatch / obligations against a stale base
    # segment. iter_raw_event_lines accepts the concrete events.log path and spans segments.
    out: list[dict[str, object]] = []
    for line in iter_raw_event_lines(events_log):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _provenance_field(e: dict[str, object], key: str) -> str:
    prov = e.get("provenance", {})
    if isinstance(prov, dict):
        return str(prov.get(key, ""))
    return ""


def _effective_kind(e: dict[str, object]) -> str:
    """Real event_type, unwrapping stub-rewrap NodeTouched (payload.kind)."""
    et = str(e.get("event_type", ""))
    if et == "NodeTouched":
        p = e.get("payload", {})
        if isinstance(p, dict) and isinstance(p.get("kind"), str):
            return str(p["kind"])
    return et


def _session_violated_obligation_ids(
    events: list[dict[str, object]], session_id: str,
) -> dict[str, list[str]]:
    """obligation_id → [ObligationViolated event_id] emitted under this session's provenance.

    Session 关联走 provenance.session_id (与既有 check_obligations_maintained 同一关联键)。
    """
    out: dict[str, list[str]] = {}
    for e in events:
        if _effective_kind(e) != "ObligationViolated":
            continue
        if _provenance_field(e, "session_id") != session_id:
            continue
        payload = e.get("payload", {})
        oid = payload.get("obligation_id") if isinstance(payload, dict) else None
        if isinstance(oid, str) and oid:
            out.setdefault(oid, []).append(str(e.get("event_id", "")))
    return out


def build_active_obligation_declarations(
    towow_dir: Path,
    all_obligations: list[dict[str, object]],
    session_id: str,
) -> tuple[list[dict[str, object]], str]:
    """T-L1-35: per active obligation → declaration {obligation_id, status, justification}.

    真实来源 (非占位/非恒-maintained 假列表):
      - obligation_id 集合从 obligation_lifecycle_state projection 的 canonical_state==active
        节点逐条读 (唯一真实来源; capsule 只存 obligation_count 不存 IDs 是既存 obligation_checks
        债, 不在本任务 scope)。
      - status 逐条由真实信号判: 该 session 有对应 ObligationViolated event → violated; 否则
        maintained。not_applicable 不自动判 (per-task applicability scoping 依赖 obligation
        forbidden_pattern / scope analysis, 既存 obligation_checks 债)。
      - justification 逐条带可核 evidence (canonical_state / severity / 关联的真实 violation
        event_id 或 "no ObligationViolated by session") —— 不是恒定占位串。

    Returns (declarations, per-obligation evidence summary)。declarations 注入 envelope.
    active_obligations_declared (T-L1-35); evidence summary 供 obligations_maintained
    check (T-L1-34) 用。
    """
    events = _read_events(towow_dir / "events.log")
    violated = _session_violated_obligation_ids(events, session_id)
    declarations: list[dict[str, object]] = []
    evidence_parts: list[str] = []
    for ob in all_obligations:
        # all_obligations 来自 obligation_lifecycle_state projection (reducer 恒写 dict 节点)。
        if ob.get("canonical_state") != "active":
            continue
        oid = ob.get("obligation_id")
        if not isinstance(oid, str) or not oid:
            continue
        severity = str(ob.get("severity", "normal"))
        if oid in violated:
            evs = violated[oid]
            justification = (
                f"ObligationViolated {evs} by session {session_id} "
                f"(severity={severity}, canonical_state=active)"
            )
            declarations.append(
                {"obligation_id": oid, "status": "violated", "justification": justification},
            )
            evidence_parts.append(f"{oid}: violated (severity={severity}, events={evs})")
        else:
            justification = (
                f"canonical_state=active, severity={severity}; "
                f"no ObligationViolated by session {session_id} → maintained"
            )
            declarations.append(
                {"obligation_id": oid, "status": "maintained", "justification": justification},
            )
            evidence_parts.append(
                f"{oid}: maintained (severity={severity}, no ObligationViolated by {session_id})",
            )
    if declarations:
        evidence = f"{len(declarations)} active obligation(s) declared — " + "; ".join(evidence_parts)
    else:
        evidence = "no active obligations in obligation_lifecycle_state projection"
    return declarations, evidence


def check_obligations_maintained(
    towow_dir: Path, session_id: str,
    *,
    active_obligations: list[dict[str, object]] | None = None,
    declared: list[dict[str, object]] | None = None,
) -> tuple[bool, str]:
    """Obligation maintenance gate (T-L1-34 收紧).

    向后兼容路径 (active_obligations is None): 旧"本 session 无 ObligationViolated → pass"
    判定 (一个 declare 0 obligation 的 session 平凡通过 — loose)。

    收紧路径 (active_obligations 传入 = obligation_lifecycle_state projection 列表;
    declared = envelope 真提交的 active_obligations_declared):
      ① 一致性 gate (honesty): 该 session 有 ObligationViolated 的 obligation 必须在 declared
         里被标 violated — 不能 emit 了 violation 又在 envelope 谎报 maintained / 漏报。
      ② 提交内 violated gate: declared 里任一 status==violated → fail (self-reported violation,
         与 commit gate check_obligations 一致)。
      ③ red_line coverage gate (NEW): projection 里每个 active red_line obligation 必须出现在
         declared (envelope 不能漏交代红线义务) — 非 tautology, 比较"提交的 declared"vs"projection
         的真实 active red_line 集"两个独立输入。
      ④ 全过 → pass, evidence 逐条枚举 declared 的 status (正向 accounting, 非"无违规"负向占位)。

    诚实限度: 不验 forbidden_pattern 语义匹配 / not_applicable per-task scoping (obligation 无
    pattern 数据 + capsule 不存 injected_obligations — obligation_checks.py 既存债, 非本 check 引入)。
    """
    events = _read_events(towow_dir / "events.log")
    session_violated = _session_violated_obligation_ids(events, session_id)

    if active_obligations is None:
        # 向后兼容 (无 projection 上下文): 旧 loose 判定。
        if session_violated:
            return False, (
                f"{sum(len(v) for v in session_violated.values())} ObligationViolated "
                f"event(s) for session {session_id}"
            )
        return True, f"no ObligationViolated for session {session_id} (no projection coverage check)"

    declared = declared or []
    declared_by_id = {
        d.get("obligation_id"): d for d in declared if isinstance(d, dict)
    }

    # ① 一致性 gate: session-violated obligation 必须在 declared 标 violated。
    misreported = [
        oid for oid in session_violated
        if declared_by_id.get(oid, {}).get("status") != "violated"
    ]
    if misreported:
        return False, (
            f"{len(misreported)} obligation(s) have ObligationViolated by session "
            f"{session_id} but envelope did not declare them violated: {misreported}"
        )

    # ② 提交内 violated gate。
    violated_decls = [
        oid for oid, d in declared_by_id.items() if d.get("status") == "violated"
    ]
    if violated_decls:
        return False, (
            f"{len(violated_decls)} active obligation(s) declared violated by session "
            f"{session_id}: {violated_decls}"
        )

    # ③ red_line coverage gate (NEW): active red_line ⊆ declared。
    missing_red_line = [
        oid for oid in active_red_line_ids(active_obligations)
        if oid not in declared_by_id
    ]
    if missing_red_line:
        return False, (
            f"{len(missing_red_line)} active red_line obligation(s) not declared in "
            f"envelope (coverage gap): {missing_red_line}"
        )

    # ④ 正向 accounting evidence。
    if declared:
        parts = [f"{d.get('obligation_id')}:{d.get('status')}" for d in declared]
        return True, f"{len(declared)} obligation(s) accounted: " + ", ".join(parts)
    return True, "no active obligations in obligation_lifecycle_state projection"


def active_red_line_ids(all_obligations: list[dict[str, object]]) -> list[str]:
    """obligation_id of active (canonical_state) red_line-severity obligations."""
    out: list[str] = []
    for ob in all_obligations:
        # all_obligations 来自 obligation_lifecycle_state projection (reducer 恒写 dict 节点)。
        if ob.get("canonical_state") != "active":
            continue
        if str(ob.get("severity", "")) != "red_line":
            continue
        oid = ob.get("obligation_id")
        if isinstance(oid, str) and oid:
            out.append(oid)
    return out


def check_no_unhandled_mismatch(
    towow_dir: Path, run_id: str,
) -> tuple[bool, str]:
    """No MismatchDetected without a matching MismatchResolutionDecided for this run.

    检查逻辑是真的 (T-L1-34 加注入测试钉住): events.log 里有本 run 的 unresolved
    MismatchDetected → fail-closed (return False), work complete 被拒。`_effective_kind`
    同时识别 canonical MismatchDetected 与 stub-rewrap NodeTouched(kind=MismatchDetected)。

    **生产 emit 源已接 (RUN-038 加固(2), T-L1-38/T-L1-39 — debt-0337d1ae14f7 resolved)**:
    MismatchDetected / MismatchResolutionDecided 已注册 canonical EventType + payload +
    `work mismatch` / `work mismatch-resolve` CLI emit (provenance.run_id 对齐本 run)。本 gate
    现在真实执行路径上 **fire** — 未解 mismatch → work complete fail-closed 拒 (契约测试
    test_run038_mismatch_gate.py 真 emit 经 gate 钉住, 非注入)。空集 → 正确 pass (本 run 确无
    mismatch)。**诚实粒度**: resolution 按 run_id 匹配 (一个本 run 的 MismatchResolutionDecided
    清掉本 run 全部 detected), 非 per-mismatch_id — MVP 出口够用, per-mismatch 收紧是后续。
    """
    events = _read_events(towow_dir / "events.log")
    detected = [e for e in events if _effective_kind(e) == "MismatchDetected"]
    if not detected:
        # 无 mismatch → 正确 pass (生产 emit 源已接: work mismatch CLI, RUN-038 加固(2))。
        return True, "no MismatchDetected events for this run (production emit source live: work mismatch CLI, RUN-038)"
    resolved_run_ids = {
        _provenance_field(e, "run_id")
        for e in events
        if _effective_kind(e) == "MismatchResolutionDecided"
    }
    unhandled = [
        e for e in detected
        if _provenance_field(e, "run_id") == run_id
        and run_id not in resolved_run_ids
    ]
    if unhandled:
        return False, f"{len(unhandled)} unresolved MismatchDetected for run {run_id}"
    return True, f"all mismatches for run {run_id} resolved"


def check_actual_set_recorded(
    towow_dir: Path, run_id: str, *, patch_summary: str | None,
) -> tuple[bool, str]:
    """A PatchProposed event for this run, OR a caller-provided non-empty patch_summary.

    actual_set = 本 run 实际改了什么。证据优先取 event log 里的 PatchProposed;
    无 PatchProposed 时要求 caller 用 --patch-summary 显式声明实际改动 (非空)。
    """
    events = _read_events(towow_dir / "events.log")
    patches = [
        e for e in events
        if _effective_kind(e) == "PatchProposed"
        and _provenance_field(e, "run_id") == run_id
    ]
    if patches:
        return True, f"{len(patches)} PatchProposed event(s) for run {run_id}"
    if patch_summary and patch_summary.strip():
        return True, f"caller-declared actual set: {patch_summary.strip()[:160]}"
    return False, (
        f"no PatchProposed for run {run_id} and no --patch-summary provided "
        "(actual set 未记录)"
    )


def check_git_committed(repo_dir: Path, *, commit_ref: str | None) -> tuple[bool, str]:
    """Caller-provided commit_ref must resolve to a real git commit object.

    git_committed = 本 task 的改动已落进 git。要求 caller 传 --commit-ref, 且该 ref
    经 `git cat-file -t` 验证确为 commit 对象 (不接受不存在的 ref / 自报)。
    """
    if not commit_ref or not commit_ref.strip():
        return False, "no --commit-ref provided (git_committed 无证据)"
    ref = commit_ref.strip()
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "-t", ref],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"git verify failed: {exc!r}"
    obj_type = proc.stdout.strip()
    if proc.returncode != 0 or obj_type != "commit":
        return False, f"ref {ref!r} not a commit object (git cat-file → {obj_type or proc.stderr.strip()!r})"
    return True, f"commit {ref} verified via git cat-file"


def check_done_criteria_satisfied(*, done_evidence: str | None) -> tuple[bool, str]:
    """Execution session must assert done-criteria satisfaction with non-empty evidence.

    done_criteria 是 task-specific 语义判断, 不可纯机械导出; 要求 execution session 用
    --done-evidence 提供具体证据陈述 (非空)。空 = fail-closed (防 hollow 自报)。
    """
    if done_evidence and done_evidence.strip():
        return True, f"session-asserted: {done_evidence.strip()[:200]}"
    return False, "no --done-evidence provided (done criteria 未声明 — 防 hollow 自报)"


def run_all_execution_blocking_checks(
    towow_dir: Path,
    *,
    run_id: str,
    session_id: str,
    repo_dir: Path,
    done_evidence: str | None,
    commit_ref: str | None,
    patch_summary: str | None,
    active_obligations: list[dict[str, object]] | None = None,
    declared_obligations: list[dict[str, object]] | None = None,
    done_criteria_machine_check: ExecutionBlockingCheck | None = None,
    deploy_identity_check: ExecutionBlockingCheck | None = None,
) -> tuple[bool, list[ExecutionBlockingCheck]]:
    """Run 5 execution blocking_check (M-1.4 §5). Returns (all_passed, results).

    success outcome 要求 5 项全 passed。caller 在 abort outcome 下应跳过 (abort 是合法终态,
    不走这 5 项 success 门)。

    active_obligations (obligation_lifecycle_state projection) + declared_obligations
    (envelope 真提交的 active_obligations_declared) 传入 → obligations_maintained 走 T-L1-34
    收紧路径 (一致性 / violated / red_line coverage gate); 不传 → 向后兼容 loose 判定。

    done_criteria_machine_check (RUN-044 LB1): caller (CLI) 用 l1/execution_done_recompute
    对 task 的 done_criteria 真起 subprocess grep/test 复算后产的 done_criteria_satisfied check。
    传入 → **替换** evidence-only 的 check_done_criteria_satisfied (机器复算权威: 自报 != 复算即拒);
    不传 (task 无 gate-recomputable machine_check) → 回退 evidence-only 判定。这一层不 import l1
    (schemas 不依赖 l1), 只接收 caller 预算好的 ExecutionBlockingCheck —— 避免分层倒置。

    deploy_identity_check (T-RMD-S4-BYTE-IDENTITY, f-sub-byte-identity-gate-missing): caller (CLI)
    用 l0/commit_gate/deploy_identity_check.build_deploy_identity_check 对本 run 的 actual_write_set
    比 sha256(render(src))==sha256(live) 后产的 byte-identity check。传入 → append 进 results 且
    factor 进 all_passed (src↔live 字节漂移 → work complete fail-closed); 不传 → 不参与 (向后兼容,
    如 abort 路径)。同 done_criteria_machine_check 范式: schemas 不 import l0.commit_gate (避免
    schemas↔l0.commit_gate import 环), 只接收 caller 预算好的 ExecutionBlockingCheck。
    """
    if done_criteria_machine_check is not None:
        done_check = done_criteria_machine_check
    else:
        passed, evidence = check_done_criteria_satisfied(done_evidence=done_evidence)
        done_check = ExecutionBlockingCheck(
            check_id="execution.done_criteria_satisfied",
            status="passed" if passed else "failed",
            evidence=execution_evidence("execution.done_criteria_satisfied", evidence),
        )

    raw = [
        ("execution.actual_set_recorded",
         check_actual_set_recorded(towow_dir, run_id, patch_summary=patch_summary)),
        ("execution.obligations_maintained",
         check_obligations_maintained(
             towow_dir, session_id,
             active_obligations=active_obligations,
             declared=declared_obligations,
         )),
        ("execution.no_unhandled_mismatch",
         check_no_unhandled_mismatch(towow_dir, run_id)),
        ("execution.git_committed",
         check_git_committed(repo_dir, commit_ref=commit_ref)),
    ]
    results: list[ExecutionBlockingCheck] = [done_check]
    all_passed = done_check.status == "passed"
    for check_id, (passed, evidence) in raw:
        results.append(
            ExecutionBlockingCheck(
                check_id=check_id,
                status="passed" if passed else "failed",
                evidence=execution_evidence(check_id, evidence),
            ),
        )
        if not passed:
            all_passed = False
    if deploy_identity_check is not None:
        results.append(deploy_identity_check)
        if deploy_identity_check.status != "passed":
            all_passed = False
    return all_passed, results


__all__ = [
    "ExecutionBlockingCheck",
    "active_red_line_ids",
    "build_active_obligation_declarations",
    "check_actual_set_recorded",
    "check_done_criteria_satisfied",
    "check_git_committed",
    "check_no_unhandled_mismatch",
    "check_obligations_maintained",
    "execution_evidence",
    "run_all_execution_blocking_checks",
]
