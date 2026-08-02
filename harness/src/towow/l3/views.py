"""M-3.2 UI projection — 30 v1.0 views + 3 Phase D views (Patch M-2.3-G).

# spec source:
#   06-l3-engineering-shell/M-3.2-ui-projection-detailed-design.md §4.1-§4.11
#   07-process-handoffs/PHASE-D-REVERSE-CONTRIBUTION-LOG.md §7 Patch M-2.3-G
#
# E.5 scaffold: each view is a `ViewDefinition` with view_id, data_source projection
# name(s), and short rendering description. Real markdown rendering belongs to runtime
# (`towow view render <view_id>`) — E.5 provides registry + lookups; render lands in E.6.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ViewDefinition:
    """One UI view definition per M-3.2 §4 + §5.

    `data_sources`: projection names this view reads (M-3.2 §2 invariant — view never
    reads raw event log except via projection_lookup).
    `render_kind`: markdown / mermaid / json / cross-projection-joined
    `phase`: v1.0 or phase_d (Patch M-2.3-G additions)
    """

    view_id: str
    file_path: str  # relative under .towow/views/
    data_sources: list[str]
    render_kind: str
    description: str
    phase: str = "v1.0"


# ════════════════════════════════════════════════════════════════════════════════
#  30 v1.0 views (M-3.2 §4.1-§4.11)
# ════════════════════════════════════════════════════════════════════════════════


_V1_VIEWS: list[ViewDefinition] = [
    # 4.1 dashboard
    ViewDefinition("dashboard", "dashboard.md", ["all_projections"], "markdown",
        "global system dashboard — task progress / finding state / obligation / commits / alerts"),
    # 4.2 task views
    ViewDefinition("task_list", "tasks/task_list.md", ["task_graph"], "markdown",
        "task_id / state / done_criteria / assignee_skill / latest_event"),
    ViewDefinition("task_graph", "tasks/task_graph.md", ["task_graph"], "mermaid",
        "task dependency graph (mermaid TD)"),
    ViewDefinition("critical_path", "tasks/critical_path.md", ["task_graph"], "markdown",
        "critical path highlighted + duration estimate"),
    # 4.3 finding / fix views
    ViewDefinition("finding_inbox", "findings/finding_inbox.md", ["finding_lifecycle"], "markdown",
        "cross-session finding inbox sorted by severity"),
    ViewDefinition("finding_lifecycle", "findings/finding_lifecycle.md", ["finding_lifecycle"], "markdown",
        "finding full state machine timeline"),
    ViewDefinition("fix_history", "findings/fix_history.md", ["finding_lifecycle"], "markdown",
        "multi-round fix dashboard — fix_attempt_no trend + novelty"),
    # 4.4 concept / reference views
    ViewDefinition("concept_graph", "concepts/concept_graph.md", ["concept_graph"], "mermaid",
        "concept graph visualization + state machine"),
    ViewDefinition("at_reference_inspector", "concepts/at_reference_inspector.md",
        ["at_reference_graph"], "markdown", "field-level @ reference graph + stale refs"),
    ViewDefinition("consumer_list", "concepts/consumer_list.md",
        ["at_reference_graph", "concept_graph"], "markdown", "concept consumer list per concept_id"),
    ViewDefinition("stale_references", "concepts/stale_references.md",
        ["at_reference_graph"], "markdown", "@ ref pin pointing to superseded/retired concepts"),
    # 4.5 obligation views
    ViewDefinition("obligation_dashboard", "obligations/obligation_dashboard.md",
        ["obligation_lifecycle_state"], "markdown",
        "obligation overview — id / canonical_state / severity / scope_type"),
    ViewDefinition("resolver_decision_inspector", "obligations/resolver_decision_inspector.md",
        ["obligation_lifecycle_state"], "markdown",
        "hit/miss/uncertain distribution by (obligation_id, task_type)"),
    ViewDefinition("activation_heatmap", "obligations/activation_heatmap.md",
        ["obligation_lifecycle_state"], "markdown",
        "obligation × task activation heatmap"),
    # 4.6 interview views
    ViewDefinition("info_need_graph", "interviews/info_need_graph.md",
        ["information_need_graph"], "markdown",
        "information need graph per session with red-line highlight"),
    ViewDefinition("interview_progress", "interviews/interview_progress.md",
        ["information_need_graph"], "markdown",
        "interview progress: total / resolved / red_line_pending / nature_unique_pending"),
    ViewDefinition("brief_inspector", "interviews/brief_inspector.md",
        ["information_need_graph"], "markdown",
        "brief content + supersede history + version diff"),
    ViewDefinition("nature_judgment_timeline", "interviews/nature_judgment_timeline.md",
        ["information_need_graph"], "markdown",
        "NatureJudgmentCaptured timeline + cluster detection"),
    # 4.7 commit / maintenance views
    ViewDefinition("commit_history", "commits/commit_history.md",
        ["commit_history"], "markdown",
        "commit_sha / verdict / checks_passed / rejection_reasons / audit_chain"),
    # T-FU-09 可观测 — commit gate 每道 check 的运行状况, 聚焦 live-target-execution-evidence@v1
    # goal 收口门: 区分"门在跑但从没真评估过 observable (潜伏)" vs "真评估 N 次/拒 M 次"。
    ViewDefinition("gate_check_firing", "commits/gate_check_firing.md",
        ["gate_check_firing"], "markdown",
        "commit gate check 触发计数 — live-target 门潜伏态 (vacuous/real firing) + 全 check_type result 分布"),
    # M-0.7 §13.8 UI 数据源 4 视图 (snapshot / consolidation / retention / gc) —— 读 event log
    # (CrossRunConsolidationCommitted / SnapshotCreated) + snapshot-module 状态文件, 非 projection。
    ViewDefinition("consolidation_history", "runs/consolidation_history.md",
        ["events"], "markdown",
        "CrossRunConsolidationCommitted timeline (M-0.7 §13.8)"),
    ViewDefinition("snapshot_inspector", "runs/snapshot_inspector.md",
        ["events"], "markdown",
        "snapshot bundle_hash + per_projection_state_hashes (M-0.7 §13.8)"),
    ViewDefinition("retention_health", "runs/retention_health.md",
        ["events"], "markdown",
        "retention_watermark trend + consumer cursors + hot segment 统计 (M-0.7 §13.8)"),
    ViewDefinition("gc_diagnostic", "runs/gc_diagnostic.md",
        ["events"], "markdown",
        "GC run 历史 + root composition + unreachable event stats (M-0.7 §13.8)"),
    # 4.8 mismatch / advisor views
    ViewDefinition("mismatch_inbox", "mismatches/mismatch_inbox.md",
        ["finding_lifecycle"], "markdown",
        "mismatch handling history filtered by task_id / type"),
    ViewDefinition("advisor_consult_log", "mismatches/advisor_consult_log.md",
        ["finding_lifecycle"], "markdown",
        "advisor consult history per AdvisorConsultRequested / VerdictDelivered"),
    # 4.9 planning views
    ViewDefinition("planning_uncertainty_inbox", "plans/planning_uncertainty_inbox.md",
        ["task_graph"], "markdown", "PlanningUncertainty events list"),
    ViewDefinition("cross_plan_dashboard", "plans/cross_plan_dashboard.md",
        ["task_graph"], "markdown", "cross-plan state aggregation"),
    # 4.10 escalation view
    ViewDefinition("escalation_inbox", "escalations/escalation_inbox.md",
        ["escalation_lifecycle"], "markdown",
        "F-09b product-language only — nature_facing_summary / options"),
    # R08 TI-2 — owner 专属收件箱: 可见提示 + 已 prep、blocking 排前、可点进 session
    # data_sources 声明真实读源 (修 f-sub-owner-inbox-waited-seconds-zero 的 under-declare 半):
    # _query_owner_inbox→_query_escalation_inbox 读 escalation_lifecycle projection + raw event log
    # (EscalationRaised / GoalEscalationRaised / NatureJudgmentCaptured — 产品语言字段 + canonical
    # 落账时间只在事件 after_state/ev.timestamp, 不在 projection)。"events" 是 raw event log 的既有
    # 声明形态 (同 consolidation_history 等), render 的 provenance 对它特判、不产悬空 projection 链接。
    ViewDefinition("owner_inbox", "escalations/owner_inbox.md",
        ["escalation_lifecycle", "events"], "markdown",
        "R08 owner 收件箱 — 可见提示 headline + 已 prep 排序收件箱 + 点进 session 句柄"),
    # 4.11 cross-projection joined views
    ViewDefinition("finding_fix_task_joined", "cross/finding_fix_task_joined.md",
        ["finding_lifecycle", "task_graph", "commit_history"], "markdown",
        "finding × fix × task joined view"),
    ViewDefinition("provenance_trace", "cross/provenance_trace.md",
        ["all_projections", "events"], "markdown",
        "full provenance trace: artifact → fix → finding → review_plan → task → brief → NatureJudgment"),
]


# ════════════════════════════════════════════════════════════════════════════════
#  M-0.2 §3.12-§3.17 UI 视图型 — M-3.2 §4 未单列的 3 个 (其余 3 个 review_inbox /
#  dashboard / obligation_heatmap 已对齐 M-3.2 名 finding_inbox / dashboard /
#  activation_heatmap, 不重复登记)。M-0.2 §7.4: UI 视图型从 event log 派生。
# ════════════════════════════════════════════════════════════════════════════════


_M02_UI_VIEWS: list[ViewDefinition] = [
    ViewDefinition("capsule_inspector", "capsule/capsule_inspector.md",
        ["events"], "markdown",
        "M-0.2 §3.14: 每个 task 的 capsule assembly 结果 — "
        "candidates / hit / miss / fallback / placement (消费 ResolverDecisionMade)"),
    # F-CIR-capsule-session-view-consumer-unbuilt: capsule_inspector 是 per-task (读
    # ResolverDecisionMade), 不消费 CapsuleCompiled.provenance.session_id (源头 fix
    # F-CIR-capsule-fork-not-session 已盖该字段, 但 grep l2/l3 = 0 消费方)。本视图补上 per-session
    # 消费方: 按会话单元展示该单元胶囊归属 (注入的概念/义务/红线 真 ID, 非阶段均值/计数兜底)。
    ViewDefinition("capsule_session_attribution", "capsule/capsule_session_attribution.md",
        ["events"], "markdown",
        "per-session 胶囊归属 — 消费 CapsuleCompiled.provenance.session_id, 按会话单元展示该单元"
        "注入的概念 (neighborhood_concept_ids) / 义务+红线 (经 covered_subjects→task→"
        "ResolverDecisionMade.final_active_set + stage_6_placement 关联) 的真实 ID"),
    ViewDefinition("resolver_trend", "capsule/resolver_trend.md",
        ["events"], "markdown",
        "M-0.2 §3.15: ResolverDecisionMade 聚合 — 调用频率 / cache 命中率 / "
        "fallback 率 / 平均 confidence / 平均耗时"),
    ViewDefinition("drift_stream", "drift/drift_stream.md",
        ["events"], "markdown",
        "M-0.2 §3.17: DriftDetected 列表 (时间倒序) + 涉及节点 + drift_type"),
]


# ════════════════════════════════════════════════════════════════════════════════
#  3 Phase D views (Patch M-2.3-G per PHASE-D §7)
# ════════════════════════════════════════════════════════════════════════════════


_PHASE_D_VIEWS: list[ViewDefinition] = [
    ViewDefinition(
        "detection_rules_lifecycle_inspector",
        "detection_rules/lifecycle_inspector.md",
        ["detection_rule_lifecycle"],
        "markdown",
        "Patch M-2.3-G: detection rule lifecycle states — proposed/shadow/active_warning/enforced/retired",
        phase="phase_d",
    ),
    ViewDefinition(
        "patterns_surface_candidates",
        "patterns/surface_candidates.md",
        ["events", "dedup_state"],
        "markdown",
        "Patch M-2.3-G: HistoricalPatternSurfaceCandidate events + dedup state",
        phase="phase_d",
    ),
    ViewDefinition(
        "escalations_lifecycle_inspector",
        "escalations/lifecycle_inspector.md",
        ["escalation_lifecycle"],
        "markdown",
        "Patch M-2.3-G + M-2.3-E: escalation_lifecycle projection inspector",
        phase="phase_d",
    ),
]


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.4 validation matrix view (RUN-076) — 读 ValidationScenarioRun 事件渲验证 outcome
# ════════════════════════════════════════════════════════════════════════════════


_M34_VIEWS: list[ViewDefinition] = [
    ViewDefinition(
        "validation_report",
        "runs/validation_report.md",
        ["events"],
        "markdown",
        "M-3.4 §3.3 验证大盘 — per-scenario 最新 outcome (passed/failed/aborted_external) + outcome 分布 "
        "+ 真 fail 场景的 correction_signal + daemon-gated/aborted 明账 skip (读 ValidationScenarioRun)",
    ),
]


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.2 §9 提示性 view (RUN-086) — 4 类 candidate 只 surface, 不替 Nature 决策 (宪法 4)
# ════════════════════════════════════════════════════════════════════════════════


_RUN086_VIEWS: list[ViewDefinition] = [
    ViewDefinition(
        "candidate_alerts",
        "alerts/candidate_alerts.md",
        ["all_projections", "events"],
        "markdown",
        "M-3.2 §9 提示性 view — 4 类 candidate (obligation upgrade / concept supersede / "
        "escalation / knowledge pack drift) 只 surface 识别, 由 Nature 显式触发 action, view 不替决策",
    ),
]


VIEW_REGISTRY: dict[str, ViewDefinition] = {
    v.view_id: v for v in (_V1_VIEWS + _M02_UI_VIEWS + _PHASE_D_VIEWS + _M34_VIEWS + _RUN086_VIEWS)
}


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.2 §5.2 Entity Link Format (RUN-086) — entity→view anchor / event→commit_history 反向链
# ════════════════════════════════════════════════════════════════════════════════
#
# 纯函数 (无 I/O) —— 给定 entity_type + entity_id, 算出指向对应 view anchor 的可点击 markdown
# 链接。跨 view 用相对路径 (从 from_view 的目录出发); event_id 反向追溯到 commit_history。
# anchor 用确定性 slug (lower + 非 word 字符折成 -), 同 entity_id → 同 anchor (git-diff friendly)。


# entity 类型 → 对应 view 文件 (相对 .towow/views/)。链接的 anchor 落在该 view 内。
ENTITY_VIEW_TARGET: dict[str, str] = {
    "finding": "findings/finding_lifecycle.md",
    "fix": "findings/fix_history.md",
    "task": "tasks/task_list.md",
    "concept": "concepts/concept_graph.md",
    "obligation": "obligations/obligation_dashboard.md",
    "escalation": "escalations/escalation_inbox.md",
    "brief": "interviews/brief_inspector.md",
    "nature_judgment": "interviews/nature_judgment_timeline.md",
    "info_need": "interviews/info_need_graph.md",
    "commit": "commits/commit_history.md",
}

# event_id 反向追溯统一落 commit_history view (§5.2)。
_EVENT_VIEW_TARGET = "commits/commit_history.md"


def _anchor_slug(raw: str) -> str:
    """entity_id → markdown anchor slug (lower + 非 word 字符折成单个 -, 去首尾 -)。

    `\\w` 在 Python 3 默认 unicode-aware → CJK 保留。确定性: 同输入恒同输出。
    """
    s = str(raw).strip().lower()
    s = re.sub(r"[^\w]+", "-", s)
    return s.strip("-")


def _relative_to(target: str, from_view: str | None) -> str:
    """target view 路径相对 from_view 所在目录的相对路径; from_view=None → 直接 views-root 相对。"""
    if from_view is None:
        return target
    from_dir = posixpath.dirname(from_view)
    if not from_dir:
        return target
    return posixpath.relpath(target, start=from_dir)


def entity_link(
    entity_type: str,
    entity_id: str,
    *,
    from_view: str | None = None,
    label: str | None = None,
) -> str:
    """§5.2 entity → 对应 view anchor 的 markdown 链接。unknown entity_type → fail-closed raise。"""
    if entity_type not in ENTITY_VIEW_TARGET:
        raise KeyError(f"unknown entity_type for link: {entity_type!r}")
    rel = _relative_to(ENTITY_VIEW_TARGET[entity_type], from_view)
    text = label if label is not None else str(entity_id)
    return f"[{text}]({rel}#{_anchor_slug(entity_id)})"


def event_link(event_id: str, *, from_view: str | None = None, label: str | None = None) -> str:
    """§5.2 event_id → commits/commit_history.md#event-{id} 反向追溯链接。"""
    rel = _relative_to(_EVENT_VIEW_TARGET, from_view)
    text = label if label is not None else str(event_id)
    return f"[{text}]({rel}#event-{event_id})"


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.2 §7.2 Invalidation 规则表 (RUN-086) — 8 类事件 → 失效 view 集 (纯映射, 不依赖 daemon)
# ════════════════════════════════════════════════════════════════════════════════
#
# 纯数据 + 查表函数。M-3.2 §7.1 auto-refresh (commit gate accept 时自动按此表 invalidate) 已接进
# 生产 commit gate 的 accept 路径 (l0/commit_gate/view_refresh.auto_refresh_views_after_accept):
# 一个 batch 真 accept 后, 按 batch 里的 event_type 把受影响 view 标 stale (mark stale; next status
# 自动 re-render —— spec §7.1 字面 re-render 是 lazy 的)。本表是该机制的规则本体, 同时也供 on-demand /
# batch 显式调用 (ViewEngine.invalidate_views_for_event)。失效是纯只读副产物, 不 emit / 不改 canonical。


_TASKS_VIEWS = frozenset({"task_list", "task_graph", "critical_path"})
_FINDINGS_VIEWS = frozenset({"finding_inbox", "finding_lifecycle", "fix_history"})
_CONCEPTS_VIEWS = frozenset(
    {"concept_graph", "at_reference_inspector", "consumer_list", "stale_references"})
_OBLIGATIONS_VIEWS = frozenset(
    {"obligation_dashboard", "resolver_decision_inspector", "activation_heatmap"})
_COMMITS_VIEWS = frozenset({"commit_history"})
_ESCALATIONS_VIEWS = frozenset({"escalation_inbox", "escalations_lifecycle_inspector"})
_CROSS_VIEWS = frozenset({"finding_fix_task_joined", "provenance_trace"})
_DASHBOARD = frozenset({"dashboard"})


# §7.2 表 8 行 → 展开成 12 个具体 canonical event_type (Finding* / Obligation* 各 1 行多 type)。
INVALIDATION_RULES: dict[str, frozenset[str]] = {
    "TaskRunCompleted": _TASKS_VIEWS | _DASHBOARD | _CROSS_VIEWS,
    "FindingCreated": _FINDINGS_VIEWS | _DASHBOARD | _CROSS_VIEWS,
    "FindingVerified": _FINDINGS_VIEWS | _DASHBOARD | _CROSS_VIEWS,
    "FindingResolved": _FINDINGS_VIEWS | _DASHBOARD | _CROSS_VIEWS,
    "FixCompleted": frozenset({"fix_history"}) | _CROSS_VIEWS | _ESCALATIONS_VIEWS,
    "ConceptCreated": _CONCEPTS_VIEWS | _DASHBOARD,
    "ObligationCaptured": _OBLIGATIONS_VIEWS | _DASHBOARD,
    "ObligationActivated": _OBLIGATIONS_VIEWS | _DASHBOARD,
    "ObligationEvolved": _OBLIGATIONS_VIEWS | _DASHBOARD,
    "CommitAccepted": _COMMITS_VIEWS | _DASHBOARD,
    "NatureJudgmentCaptured": frozenset({"nature_judgment_timeline"}) | _DASHBOARD,
    "EscalationRaised": _ESCALATIONS_VIEWS | _DASHBOARD,
}


def views_invalidated_by(event_type: str) -> frozenset[str]:
    """§7.2 — 给定 canonical event_type, 返回它会让哪些 view 数据失效。未登记事件 → 空集。"""
    return INVALIDATION_RULES.get(event_type, frozenset())


# ════════════════════════════════════════════════════════════════════════════════
#  M-3.2 §10.1 PHASE-C 7 维度 UI hook (RUN-086) — 为 M-3.4 验证提供 view 锚点
# ════════════════════════════════════════════════════════════════════════════════
#
# 12 个代表场景 (跨 7 维度) → 支撑它们的 view 集。M-3.4 验证矩阵据此知道每个场景该看哪些 view。
# 引用的 view 全部 ∈ VIEW_REGISTRY (锚点非悬空) —— 由测试机械校验。


PHASE_C_VIEW_HOOKS: dict[str, dict[str, object]] = {
    "1.1": {"dimension_no": 1, "dimension": "业务工作流", "scenario": "采访→共识→计划",
            "views": ("dashboard", "info_need_graph", "concept_graph", "task_graph",
                      "finding_fix_task_joined")},
    "1.2": {"dimension_no": 1, "dimension": "业务工作流", "scenario": "执行→review→fix",
            "views": ("finding_inbox", "fix_history", "finding_fix_task_joined")},
    "2.1": {"dimension_no": 2, "dimension": "失败模式", "scenario": "多轮 fix novelty gate",
            "views": ("fix_history",)},
    "2.2": {"dimension_no": 2, "dimension": "失败模式", "scenario": "knowledge pack drift",
            "views": ("dashboard", "drift_stream")},
    "3.1": {"dimension_no": 3, "dimension": "系统自我修正", "scenario": "NatureJudgment 升级",
            "views": ("nature_judgment_timeline", "obligation_dashboard")},
    "3.2": {"dimension_no": 3, "dimension": "系统自我修正", "scenario": "Bootstrap obligation evolution",
            "views": ("obligation_dashboard", "escalation_inbox")},
    "4.1": {"dimension_no": 4, "dimension": "运维进程", "scenario": "Daemon 运行",
            "views": ("consolidation_history", "snapshot_inspector", "retention_health",
                      "gc_diagnostic")},
    "5.1": {"dimension_no": 5, "dimension": "可观测性", "scenario": "Provenance 全栈追溯",
            "views": ("provenance_trace",)},
    "5.2": {"dimension_no": 5, "dimension": "可观测性", "scenario": "Cold archive rebuild",
            "views": ("dashboard",)},
    "6.1": {"dimension_no": 6, "dimension": "Bootstrap", "scenario": "towow init 链路",
            "views": ("dashboard",)},
    "7.1": {"dimension_no": 7, "dimension": "Cross-cutting", "scenario": "NoveltyCheck cross-cutting",
            "views": ("commit_history",)},
    "7.2": {"dimension_no": 7, "dimension": "Cross-cutting", "scenario": "finding_kind authority routing",
            "views": ("finding_inbox",)},
}


def phase_c_view_hooks() -> dict[str, dict[str, object]]:
    """§10.1 PHASE-C 维度 → view 锚点全表 (M-3.4 验证矩阵消费)。"""
    return PHASE_C_VIEW_HOOKS


def views_for_phase_c_scenario(scenario_id: str) -> tuple[str, ...]:
    """某 PHASE-C 代表场景 (如 '5.1') 支撑它的 view 集; 未知场景 → 空 tuple。"""
    hook = PHASE_C_VIEW_HOOKS.get(scenario_id)
    if hook is None:
        return ()
    return tuple(hook["views"])  # type: ignore[arg-type]


def list_views(phase: str | None = None) -> list[ViewDefinition]:
    """List all views, optionally filtered by phase ('v1.0' or 'phase_d')."""
    if phase is None:
        return list(VIEW_REGISTRY.values())
    return [v for v in VIEW_REGISTRY.values() if v.phase == phase]


__all__ = [
    "ENTITY_VIEW_TARGET",
    "INVALIDATION_RULES",
    "PHASE_C_VIEW_HOOKS",
    "VIEW_REGISTRY",
    "ViewDefinition",
    "entity_link",
    "event_link",
    "list_views",
    "phase_c_view_hooks",
    "views_for_phase_c_scenario",
    "views_invalidated_by",
]
