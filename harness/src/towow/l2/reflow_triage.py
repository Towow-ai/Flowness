"""存量搁浅工位自主 triage (never-destroy-stranded-salvage, T-REFLOW-06)。

背景: 昨晚 isolation=on 后整夜开发搁浅, .towow/worktrees 下积压数十个'完工却没回流 main'的隔离工位
(实证 reflow-sentinel dry-run 跑出 20 个超 SLA)。要把它们【自主、零销毁】收口: 真缺的回流上 main、
已在 main / 被取代的有记录地关闭, 绝不自动 rm 任何工位。

分类哲学 (呼应 owner '派 agent 智能判, 别执着算法' + functional-equivalence-closure-criterion@v1):
merge-base --is-ancestor 单独既非必要也非充分 (工位 HEAD 是 main 祖先只证明字面提交在场, 不证明功能
已覆盖), 故分类消费 functional-equivalence-evidence-tier@v1 的 classify() —— 目前唯一可得信号
(merge-base 布尔值) 恒落 not_hard(N3)/non_signal(NS1), 不产出 hard tier, 一律 needs_agent_triage:
'效果经另一个 commit 已在 main'(如 T-BRAIN-01, sha 不同但效果在)、'被新版本取代'、'真缺待合' —— 这些
靠 sha 判不出, 交 agent 智能判: emit 一条带分类提示的 triage FindingCreated → 现有 fix dispatch 起
agent 会话, 由它读工程状态 (超人的那种) 决定 reflow / 收口 / 作废。

零销毁红线: 本模块【绝不】删任何工位目录, 只 emit Finding (经 commit gate) 让 agent 决定+留痕。
昨晚所有改动一律保全, 处置走 agent + canonical 事件, 不存在'自动 rm 工位'这条路。

幂等: 每个工位只 emit 一次 triage Finding (reflow_triage_finding__ marker)。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from towow.l0.event_log import EventLog
from towow.l1.functional_equivalence_evidence_tier import (
    EvidenceItem,
    EvidenceReasonCode,
    EvidenceTier,
    classify,
)
from towow.l2.orchestrator import (
    _fix_commit_reachable_from_main,
)
from towow.l2.reflow_reconcile import (
    _RECOVERY_MARKER_STALE_AFTER_SECONDS,
    StrandedWorktree,
    _bump_reflow_conflict_mint_count,
    _emit_reflow_mint_cap_escalation,
    _recovery_marker_fresh,
    _reflow_mint_cap,
    _reflow_mint_cap_escalated,
    _write_recovery_marker,
    _write_reflow_mint_cap_escalated,
    reflow_conflict_mint_count,
    scan_stranded_worktrees,
)
from towow.schemas.enums import FindingKind

# debt-72c5025798c4 根治 (同 reflow_reconcile._REFLOW_CONFLICT_RULE_ID/_REFLOW_STRANDED_RULE_ID 一带):
# triage 路径独立 rule_id + location, 不再继承 orchestrator._build_recovery_finding_intent 默认的
# §SR continuation marker (self-fulfilling-effect-recovery / done_criterion:live-fire-effect)。
_REFLOW_TRIAGE_RULE_ID = "reflow-triage-recovery"
_REFLOW_TRIAGE_LOCATION = "reflow-worktree:triage-backlog"


@dataclass(frozen=True)
class TriageReport:
    """一轮存量 triage 的结构化结果。"""

    commit_in_main: list[str] = field(default_factory=list)  # 工位 commit 字面已在 main (算法确定)
    needs_agent_triage: list[str] = field(default_factory=list)  # 交 agent 智能判 (effect-in-main/取代/真缺)
    findings_emitted: list[str] = field(default_factory=list)  # emit 的 triage finding event_id

    @property
    def total(self) -> int:
        return len(self.commit_in_main) + len(self.needs_agent_triage)


def _worktree_head_sha(worktree_path: Path) -> str | None:
    """工位当前 HEAD sha (拿不到 → None)。"""
    import subprocess

    res = subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    sha = res.stdout.strip()
    return sha if res.returncode == 0 and sha else None


def classify_stranded(
    repo_dir: Path, sw: StrandedWorktree,
) -> str:
    """分类一个搁浅工位: 'commit_in_main' (evidence-tier 判 hard, 免 agent 实查) /
    'needs_agent_triage' (not_hard/未落地, 必须交 agent 智能判)。

    消费 functional-equivalence-evidence-tier@v1 的 classify(), 不再把 merge-base 布尔值本身当闭合信号
    (functional-equivalence-closure-criterion@v1 (b) 非充分: 即便 --is-ancestor 返回 TRUE, 也只证明
    "工位 HEAD 字面已进 main", 不证明该改动功能上已覆盖/取代 —— 单靠它不足以判"安全可收口")。
    工位 HEAD 是 main 祖先 (TRUE) → N3 (not_hard, 该具体案例被 evidence-tier 明确点名"拿不准");
    否则 (不在 main / 拿不到 sha / 判不出) → NS1 (non_signal, 回流机制必然产物, 不能反证未落地)。
    两者当前都不会产出 hard tier (无 H1/H2/H3 证据源), 故本函数目前恒返回 needs_agent_triage —— 这正是
    修复的意图: 不再用裸 merge-base 布尔值自证"已收口", 一律交 agent 按功能等价重新判断。
    """
    sha = _worktree_head_sha(sw.worktree_path)
    if sha is not None and _fix_commit_reachable_from_main(repo_dir, sha) is True:
        evidence = [EvidenceItem(
            reason_code=EvidenceReasonCode.N3,
            detail=f"工位 HEAD {sha} 是 main 的 merge-base 祖先 (字面提交在场), 但该关系本身对"
            "功能等价闭合非充分判据, 仍需 agent 实查确认是否真覆盖",
        )]
    else:
        evidence = [EvidenceItem(
            reason_code=EvidenceReasonCode.NS1,
            detail="工位 HEAD 非 main 祖先或不可验证 — 回流是 apply-diff 再提交, 原工位提交必然不成为"
            "main 祖先, 此关系是机制必然产物、非信号",
        )]
    tier = classify(evidence)
    if tier is EvidenceTier.HARD:
        return "commit_in_main"
    return "needs_agent_triage"


def triage_stranded_backlog(
    towow_dir: Path,
    event_log: EventLog,
    *,
    now: float | None = None,
    stale_after_seconds: float = _RECOVERY_MARKER_STALE_AFTER_SECONDS,
) -> TriageReport:
    """存量 triage 入口 (一次性清积压, 可 CLI 调): 分类每个搁浅工位 + emit 带分类提示的 triage Finding
    交 agent 自主处置 (reflow 真缺 / 收口已在 main 或被取代)。零销毁。

    幂等 (f-review-reflow-stranded-finding-storm): 与 stranded/conflict 三路共享 recovery marker —
    在飞期间 skip, 陈旧仍未回流则重发; 只 emit 成功后写。分类 (commit_in_main/needs_agent_triage) 始终
    记入 report 反映当前真实分布, 即便幂等 skip 不重发。

    §SR 解耦 (f-review-reflow-sr-deadletter-coupling): effect_task=worktree_id (非真执行 task_id) 让
    §SR 一致 skip (见 reflow_reconcile.emit_stranded_reflow_findings)。rule_id 传 _REFLOW_TRIAGE_
    RULE_ID (debt-72c5025798c4 根治), 不再继承 §SR continuation marker。

    A-2 铸造上限 (小补丁包 a, 参数化复用 conflict 路径同款机制, 三路径共享计数器/键 worktree_id): 达
    上限不再铸新 finding, 改一次性 emit GoalEscalationRaised 升级后静默。分类统计 (commit_in_main/
    needs_agent_triage) 不受影响, 达上限也照记, 反映当前真实分布。

    repo_dir = towow_dir.parent (内层 harness/, merge-base 在含 .git 的树跑由 helper 自处理)。
    """
    from towow.l2.reflow_commit_gate import emit_finding_via_gate
    from towow.l2.orchestrator import _build_recovery_finding_intent

    repo_dir = towow_dir.parent
    now = time.time() if now is None else now
    stranded = scan_stranded_worktrees(towow_dir, event_log)
    commit_in_main: list[str] = []
    needs: list[str] = []
    emitted: list[str] = []
    for sw in stranded:
        cls = classify_stranded(repo_dir, sw)
        if cls == "commit_in_main":
            commit_in_main.append(sw.task_id)
        else:
            needs.append(sw.task_id)
        if _recovery_marker_fresh(
            towow_dir, sw.worktree_id, now=now, stale_after_seconds=stale_after_seconds,
        ):
            continue  # 幂等: recovery finding 仍在 fix 在飞期 (含其他路径已认领同工位)
        mint_count = reflow_conflict_mint_count(towow_dir, sw.worktree_id)
        cap = _reflow_mint_cap()
        if mint_count >= cap:
            if not _reflow_mint_cap_escalated(towow_dir, sw.worktree_id):
                esc_event_id = _emit_reflow_mint_cap_escalation(
                    event_log, worktree_id=sw.worktree_id, mint_count=mint_count, cap=cap,
                )
                if esc_event_id:
                    _write_reflow_mint_cap_escalated(towow_dir, sw.worktree_id, esc_event_id, now=now)
            continue
        finding_id = f"f-reflow-triage-{uuid.uuid4().hex[:12]}"
        hint = (
            "evidence-tier 判 hard: 已有独立可复核的功能等价证据 → 可直接收口 (WorktreeClosed/"
            "done-elsewhere), 免再派 agent 实查"
            if cls == "commit_in_main"
            else "sha 判不出处置 (merge-base 本身非充分/非必要证据) → 请读工程状态智能判: 效果是否经别的 "
            "commit 已在 main / 是否被新版取代 / 是否真缺待合, 据此 reflow 或有记录收口"
        )
        intent = _build_recovery_finding_intent(
            finding_id=finding_id,
            severity="major",
            risk_surface="never-destroy-stranded-salvage",
            description=(
                f"存量搁浅工位 {sw.worktree_id} (task {sw.task_id}, 完工 {sw.completion_event_id}) 待自主 "
                f"triage 收口。分类提示: [{cls}] {hint}。**零销毁红线**: 绝不 rm 工位, 处置走 agent + "
                f"canonical 事件 (reflow 上 main / 有记录关闭)。工位路径 {sw.worktree_path}。"
            ),
            finding_kind=FindingKind.SYSTEM_GOVERNANCE_DEFECT.value,
            effect_task=sw.worktree_id,
            rule_id=_REFLOW_TRIAGE_RULE_ID,
            target_location=_REFLOW_TRIAGE_LOCATION,
        )
        event_id = emit_finding_via_gate(
            event_log, intent, towow_dir, closure="reflow-backlog-triage",
        )
        if event_id:
            _write_recovery_marker(towow_dir, sw.worktree_id, finding_id, now=now)
            _bump_reflow_conflict_mint_count(towow_dir, sw.worktree_id)
            emitted.append(event_id)
    return TriageReport(
        commit_in_main=commit_in_main, needs_agent_triage=needs, findings_emitted=emitted,
    )
