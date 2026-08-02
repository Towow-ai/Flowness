"""RUN-044 LB1 — execution 自检对 done_criteria 真机器复算 (薄适配器, 复用 M-1.5 内核).

owner 诊断 (debt-832a233907a4): spec 设计了"门自己跑 grep/test、机器算 != agent 自报即拒"的硬机制
(l1/closure_verification.verify_closure_against_contract), 但只装进 M-1.5 review 的 fix-after 闭合环,
**没装进 execution 第一次提交的自检** —— execution 的 check_done_criteria_satisfied 只验
--done-evidence 非空 (纯语义自报)。导致 M-1.3 形态 (cross-plan write_set 交集冲突检测仅文档/SKILL
有、代码 0 enforce) 在 execution 关漏网。

本模块是**薄适配器** (禁另造平行逻辑 — 复算引擎 100% 复用 M-1.5 verify_closure_against_contract):
把带 gate-recomputable machine_check 的 done_criteria 翻译成 M-1.5 的 ClosureContract/ClosureResult,
门用合约自带 pattern 真起 subprocess grep/pytest, 拿真结果跟 agent 自报 (self_reported_done) 比对,
不一致 → fail-closed。这是 closure_verification 模块所述"门的通过判定里必须有一部分是门自己在
worktree 真跑出来的、被审者用任何文字都伪造不了的结果"原则向 execution 第一次提交的延伸。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from towow.l0.event_log.segments import iter_raw_event_lines
from towow.l1.closure_verification import (
    ClosureResult,
    CriterionResult,
    GitDiffRunner,
    GrepRunner,
    PytestRunner,
    gate_git_diff_name_only,
    verify_closure_against_contract,
)
from towow.schemas.enums import (
    GATE_RECOMPUTABLE_VERIFICATION_METHODS,
    ClosureVerificationMethod,
)
from towow.schemas.payloads.finding import ClosureContract, ClosureCriterion
from towow.schemas.payloads.task_package import DoneCriterion, TaskPackageContent
from towow.schemas.payloads.task_run_completed_checks import (
    ExecutionBlockingCheck,
    execution_evidence,
)

_CHECK_ID = "execution.done_criteria_satisfied"


def load_task_done_criteria(towow_dir: Path, task_id: str) -> list[DoneCriterion]:
    """从 events.log 取该 task 最新 TaskPackagePublished 的 package_content.done_criteria.

    用于 execution `work complete` 拿结构化 done_criteria 做机器复算 (work start 只传字符串列表)。
    无 TaskPackagePublished (task 未发布 package / stub / 直接起的 run) → 返回空 → caller 回退
    evidence-only check。多个 publish (re-publish) → 取读序最后一个 (= 最新)。strict=False:
    package_content 是 JSON 派生 (enum 是字符串), 与 publish 路径同向 coercion。
    """
    out: list[DoneCriterion] = []
    for line in iter_raw_event_lines(towow_dir):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or str(rec.get("event_type", "")) != "TaskPackagePublished":
            continue
        payload = rec.get("payload", {})
        after = payload.get("after_state", {}) if isinstance(payload, dict) else {}
        if not isinstance(after, dict) or str(after.get("task_id", "")) != task_id:
            continue
        content_dict = after.get("package_content", {})
        if not isinstance(content_dict, dict):
            continue
        try:
            content = TaskPackageContent.model_validate(content_dict, strict=False)
        except ValidationError:
            continue  # 历史/异形 package 内容 → 跳过, 不让 work complete 崩
        out = list(content.done_criteria)  # 覆盖: 取最新 publish
    return out


def collect_run_commit_shas(towow_dir: Path, run_id: str) -> list[str]:
    """收集该 run 全部 PatchProposed 的 worktree_commit_sha (本任务真实 commit 集, 升序去重).

    与 cli `_derive_run_actual_write_set` 同一事实源 (PatchProposed 是 git-verified 的 commit 记录,
    机器事实非 agent 自报)。无 commit (非代码 task / 未提交) → 空列表 — git_diff runner 视为
    诚实空改动清单 (honesty contract 同 derive_actual_write_set_from_commit)。
    """
    shas: list[str] = []
    seen: set[str] = set()
    for line in iter_raw_event_lines(towow_dir):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or str(rec.get("event_type", "")) != "PatchProposed":
            continue
        prov = rec.get("provenance", {})
        payload = rec.get("payload", {})
        after = payload.get("after_state", {}) if isinstance(payload, dict) else {}
        rec_run = (prov.get("run_id") if isinstance(prov, dict) else None) or (
            after.get("run_id") if isinstance(after, dict) else None
        )
        if str(rec_run or "") != run_id:
            continue
        sha = after.get("worktree_commit_sha") if isinstance(after, dict) else None
        if isinstance(sha, str) and sha.strip() and sha not in seen:
            seen.add(sha)
            shas.append(sha)
    return shas


def build_run_git_diff_runner(towow_dir: Path, run_id: str, repo_dir: Path) -> GitDiffRunner:
    """把"本 run 的真实 commit 集"封进 GitDiffRunner 闭包, 喂 closure_verification 内核.

    finding-machine-check-encoding-gap-1780563112 缺口 (a) 的接线端: 合约在 assemble 时不可能知道
    未来 commit sha (T-SL-A1 把占位符命令文本塞进 search_scope 的根源), commit 集是 run 上下文 —
    由本 builder 在 work complete 时从 run 的 PatchProposed 真 commit 派生, 门再自己跑 git 拿
    改动清单 (机器事实, 自报伪造不了)。
    """
    def _run() -> tuple[list[str] | None, str]:
        shas = collect_run_commit_shas(towow_dir, run_id)
        return gate_git_diff_name_only(repo_dir, shas)
    return _run


# 执行侧 pre-commit 自检能在 worktree 真复算的方法 —— 排除 LEDGER_EVENT。
# live-target-execution-evidence@v1: LEDGER_EVENT 读的是 **committed canonical 账本**, 而本自检跑在
# envelope 提交 **之前** 的 worktree —— 本 task 该 emit 的 live-target 事件此刻还在待提交 envelope 里、
# 尚未进 committed 账本, 自检读不到, 若纳入必永久 fail-closed (把 live_target task 变成做不完的埋雷)。
# LEDGER_EVENT 的复算归 **提交门** (goal.live_target_reconciled / execution.live_target_evidence_real),
# 它们在 commit 时对 committed 账本 + 本批 domain intents 复算。故自检子集 = GATE_RECOMPUTABLE − LEDGER_EVENT。
_WORKTREE_RECOMPUTABLE_METHODS = GATE_RECOMPUTABLE_VERIFICATION_METHODS - {
    ClosureVerificationMethod.LEDGER_EVENT,
}


def _gate_recomputable_machine_checks(
    done_criteria: list[DoneCriterion],
) -> list[tuple[str, ClosureCriterion]]:
    """筛出带 worktree-可复算 machine_check (verification_method ∈ {grep,test,git_diff}) 的 criterion。

    只有这些门能自己 subprocess 复算、被审者文字伪造不了 (其余 schema_check/projection_check/
    replay/manual_reasoning 本质不可门独立复算; ledger_event 需 committed 账本, 归提交门不归自检)。
    返回 (criterion_id, machine_check) 列表。
    """
    out: list[tuple[str, ClosureCriterion]] = []
    for dc in done_criteria:
        mc = dc.machine_check
        if mc is not None and mc.verification_method in _WORKTREE_RECOMPUTABLE_METHODS:
            out.append((dc.criterion_id, mc))
    return out


def recompute_done_criteria_check(
    *,
    done_criteria: list[DoneCriterion],
    repo_dir: Path,
    extra_bases: tuple[Path, ...] = (),
    self_reported_done: bool,
    grep_runner: GrepRunner | None = None,
    pytest_runner: PytestRunner | None = None,
    git_diff_runner: GitDiffRunner | None = None,
) -> ExecutionBlockingCheck | None:
    """对带 gate-recomputable machine_check 的 done_criteria 真机器复算, 产 done_criteria_satisfied check.

    流程 (全程复用 M-1.5 verify_closure_against_contract, 不另造复算逻辑):
      1. 筛出 machine_check.verification_method ∈ {grep,test,git_diff} 的 criterion。
      2. 无任何这类 criterion → 返回 None (caller 回退 evidence-only check_done_criteria_satisfied,
         向后兼容: 历史 / 纯具名机械方法 task 不被本路径强制)。
      3. 每条 machine_check 即 ClosureContract.closure_criteria; agent 的整体"已做/未做"声明
         (self_reported_done) 当每条 CriterionResult.self_reported_passed, 喂内核。
      4. 门复算 (occ 比较 expected, 语义按 occurrence_comparator eq/gte / pytest exit 0 /
         改动清单命中数比较 expected) != self_reported_done
         → "抓撒谎拒" → status=failed; 门复算无法执行 (合约缺 pattern / grep 错误 / git_diff 未注入
         上下文) → fail-closed failed; 全过 → passed。

    git_diff_runner: 本任务 commit 集上下文 (work complete 用 build_run_git_diff_runner 注入);
    含 git_diff machine_check 而 runner 未注入 → 该条 fail-closed (内核明拒, 非静默放过)。

    extra_bases (finding-gw1-machine-check-path-defect 缺口 a): repo_dir 之外的 fallback base
    (caller 算好 git 仓库根传进)。让 search_scope/test_selector 解析到按中立性解耦在 harness/ 外的包
    (feishu_gateway 等); default () = 旧行为。透传给内核 verify_closure_against_contract。

    返回 ExecutionBlockingCheck(check_id="execution.done_criteria_satisfied", status, evidence)
    或 None (无可机器复算 criterion)。
    """
    recomputable = _gate_recomputable_machine_checks(done_criteria)
    if not recomputable:
        return None

    contract = ClosureContract(closure_criteria=[mc for _, mc in recomputable])
    result = ClosureResult(
        criteria_results=[
            CriterionResult(
                criterion=mc.condition,
                self_reported_passed=self_reported_done,
                evidence=f"execution self-report done={self_reported_done} (criterion {cid})",
            )
            for cid, mc in recomputable
        ],
    )
    outcome = verify_closure_against_contract(
        contract=contract,
        result=result,
        repo_dir=repo_dir,
        extra_bases=extra_bases,
        grep_runner=grep_runner,
        pytest_runner=pytest_runner,
        git_diff_runner=git_diff_runner,
    )

    if outcome.strong_gate_passed:
        passed_items = "; ".join(v.detail for v in outcome.verdicts if v.kind == "criterion")
        return ExecutionBlockingCheck(
            check_id=_CHECK_ID,
            status="passed",
            evidence=execution_evidence(
                _CHECK_ID,
                f"{len(recomputable)} done_criterion 机器复算通过 (门自跑 grep/test, 与自报一致): "
                f"{passed_items[:400]}",
            ),
        )

    failed = "; ".join(outcome.reasons) or "机器复算未通过"
    return ExecutionBlockingCheck(
        check_id=_CHECK_ID,
        status="failed",
        evidence=execution_evidence(
            _CHECK_ID,
            "done_criteria 机器复算 fail-closed (门自跑结果 != agent 自报 / 复算无法执行): "
            f"{failed[:400]}",
        ),
    )


_LIVEFIRE_CHECK_ID = "execution.livefire_recomputed"


def _livefire_machine_checks(
    done_criteria: list[DoneCriterion],
) -> list[tuple[str, ClosureCriterion]]:
    """筛出 test 型 (live-fire) machine_check 的 criterion (verification_method == TEST)。

    live-fire-machine-check-contract@v1 层2: 一个被判为新增能力的 task, 其 done_criterion 的
    machine_check 必须 **test 型** (test_selector 指向用 EventLog.all_records() 读真账本断 live 事件
    存在 + provenance 的集成测试) 才算"锚到真跑过"。grep/git_diff 型只是代码层契约 (旧路径移除 /
    本任务真改某文件), closure_verification 的全域 re-grep 排除 .git/.towow, 扫不到 canonical 账本里
    的 live 事件签名 —— 故"接 live 真跑过"类判据的复算载体只能是 test 型。本函数只挑 test 型: 它们
    才是 LB1 subprocess 要真复算、被审者文字伪造不了的 live-fire 牙齿。
    """
    out: list[tuple[str, ClosureCriterion]] = []
    for dc in done_criteria:
        mc = dc.machine_check
        if mc is not None and mc.verification_method == ClosureVerificationMethod.TEST:
            out.append((dc.criterion_id, mc))
    return out


def recompute_livefire_check(
    *,
    done_criteria: list[DoneCriterion],
    repo_dir: Path,
    extra_bases: tuple[Path, ...] = (),
    self_reported_done: bool,
    grep_runner: GrepRunner | None = None,
    pytest_runner: PytestRunner | None = None,
    git_diff_runner: GitDiffRunner | None = None,
) -> ExecutionBlockingCheck:
    """T-RMD-FIX3 (修3) producer — 产 execution.livefire_recomputed.

    对 done_criteria 里的 **test 型 (live-fire) machine_check** 真起 LB1 subprocess (pytest) 复算
    (全程复用 M-1.5 verify_closure_against_contract, 不另造复算逻辑)。机器复算 != agent 自报 →
    fail-closed; 全过 → passed。

    与 recompute_done_criteria_check **关键不同: 永不返回 None** —— work-complete CLI 把本 check
    **无条件** append 进 envelope.self_check.blocking_checks (镜像 execution.sis_declared 总是注入),
    这样 completeness 总闸 (修3) 对每个 execution.* closure require 它 present+passed —— 绕 work-
    complete CLI 直 emit 缺它的新增能力 closure 被 M-0.5 物理拒 (绕 CLI 也拦, 不可旁路总闸)。
      - 无 test 型 machine_check (非新增能力 live-fire 类; 纯重构/文档用 grep|git_diff 型即可) →
        vacuous passed (present 即可, 镜像 sis 对 abort 的 vacuous present)。
      - 有 test 型 → verify_closure_against_contract 真起 pytest subprocess 复算 test_selector,
        agent 用任何文字伪造不了 test subprocess 的真账本断言 (live-fire-machine-check-contract@v1 门牙齿)。

    诚实边界 (per-task-vs-production-real-boundary@v1): 证的是 code-path-real (进程内集成测试), 不是
    production-real —— 本 check 不越权声称"生产已发生"(那由 master 段⑥读 live 生产账本 + owner 在场)。
    """
    livefire = _livefire_machine_checks(done_criteria)
    if not livefire:
        return ExecutionBlockingCheck(
            check_id=_LIVEFIRE_CHECK_ID,
            status="passed",
            evidence=execution_evidence(
                _LIVEFIRE_CHECK_ID,
                "本 task done_criteria 无 test 型 live-fire machine_check (非新增能力 live-fire 类) — "
                "vacuous passed (present 即可, 镜像 sis 对 abort 的 vacuous)",
            ),
        )

    contract = ClosureContract(closure_criteria=[mc for _, mc in livefire])
    result = ClosureResult(
        criteria_results=[
            CriterionResult(
                criterion=mc.condition,
                self_reported_passed=self_reported_done,
                evidence=f"execution self-report done={self_reported_done} (livefire criterion {cid})",
            )
            for cid, mc in livefire
        ],
    )
    outcome = verify_closure_against_contract(
        contract=contract,
        result=result,
        repo_dir=repo_dir,
        extra_bases=extra_bases,
        grep_runner=grep_runner,
        pytest_runner=pytest_runner,
        git_diff_runner=git_diff_runner,
    )

    if outcome.strong_gate_passed:
        passed_items = "; ".join(v.detail for v in outcome.verdicts if v.kind == "criterion")
        return ExecutionBlockingCheck(
            check_id=_LIVEFIRE_CHECK_ID,
            status="passed",
            evidence=execution_evidence(
                _LIVEFIRE_CHECK_ID,
                f"{len(livefire)} test 型 live-fire machine_check 经 LB1 subprocess 复算通过 "
                f"(门自跑 pytest 读真账本断 live 签名, 与自报一致): {passed_items[:300]}",
            ),
        )

    failed = "; ".join(outcome.reasons) or "live-fire 机器复算未通过"
    return ExecutionBlockingCheck(
        check_id=_LIVEFIRE_CHECK_ID,
        status="failed",
        evidence=execution_evidence(
            _LIVEFIRE_CHECK_ID,
            "live-fire machine_check 复算 fail-closed (门自跑 pytest != agent 自报 / 复算无法执行): "
            f"{failed[:300]}",
        ),
    )


__all__ = [
    "build_run_git_diff_runner",
    "collect_run_commit_shas",
    "load_task_done_criteria",
    "recompute_done_criteria_check",
    "recompute_livefire_check",
]
