"""M-3.2 view render engine — 4 Read APIs + dashboard / provenance_trace real render.

# spec source:
#   06-l3-engineering-shell/M-3.2-ui-projection-detailed-design.md
#     §3.3 Read API 设计 (query_view / render_view 两层分离)
#     §4.1 全局 Dashboard 字段 schema
#     §4.11 provenance_trace (artifact → … → source 反向追溯)
#     §5.1/§5.3 view frontmatter + 反向追溯 metadata
#     §6.1-§6.4 query_view / render_view / invalidate_views / list_views 接口
#     §7.3 render 两不变量 (idempotent + deterministic ordering)
#     §13.2 Invariant 1 (M-3.2 read-only) / Invariant 4 (provenance 不断链)
#
# RUN-047 (EPIC-9 起步): E.5 scaffold (views.py registry) 之后, 这是 E.6 render 落地的第一批 ——
# 只渲 owner 价值最高的两个: dashboard + provenance_trace。其余 ~31 view 仍是空登记 (后续 RUN),
# query/render 对它们 raise ViewRendererNotImplemented (不返假数据冒充渲染 — §4.2 forbidden shortcut)。
#
# read-only 严格 (§13.2 Inv1): 本引擎只走 ProjectionStore.read() (纯文件读, 不 catchup) + EventLog
# 读 API (get_event / get_events_by_entity / get_causation_chain / get_supersede_chain) —— 绝不
# emit 事件、不 catchup 推 projection cursor、不改任何 canonical 状态。render_view 写 .towow/views/
# 下的 derived view 文件是 §2.2 明示产出 (view 是 derived, 非 SoT), 不属"状态改动"。
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from towow.l0.event_log import EventLog
from towow.l0.projection import ProjectionStore
from towow.l0.snapshot import load_gc_state, load_retention_watermark
from towow.l3.views import (
    VIEW_REGISTRY,
    ViewDefinition,
    entity_link,
    event_link,
    views_invalidated_by,
)
from towow.schemas.enums import EscalationLifecycleState, EventType

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

    from towow.schemas.event_record import EventRecord


class ViewNotFoundError(KeyError):
    """view_id 不在 VIEW_REGISTRY (§6.1 ViewNotFoundError)。"""


class ProjectionUnavailableError(RuntimeError):
    """view 依赖的 projection 文件缺失 (§6.1 ProjectionUnavailableError — 提示 rebuild)。"""


class ViewRendererNotImplemented(NotImplementedError):
    """view 已登记但本 RUN 尚无 render 实现 (RUN-047 scope = dashboard + provenance_trace)。

    刻意 raise 而非返回 stub/假数据 —— §4.2 forbidden shortcut "不许注册了视图名当渲染"。
    """


# view_id → 已落 render 的视图 (其余在 registry 但 render 未实装 → raise, 不返假数据)。
#   RUN-047: dashboard + provenance_trace
#   RUN-071 (批L 具体视图): M-0.2 §3.12-§3.17 UI 视图型 (finding_inbox=review_inbox /
#     activation_heatmap=obligation_heatmap / capsule_inspector / resolver_trend /
#     drift_stream) + M-0.7 §13.8 4 视图 (snapshot_inspector / consolidation_history /
#     retention_health / gc_diagnostic)。
_RENDERED_VIEW_IDS = frozenset({
    "dashboard",
    "owner_inbox",  # R08 TI-2
    "provenance_trace",
    # RUN-071 — M-0.2 UI 视图型 (展示 schema M-3.2)
    "finding_inbox",
    "activation_heatmap",
    "capsule_inspector",
    "capsule_session_attribution",  # F-CIR-capsule-session-view-consumer-unbuilt
    "resolver_trend",
    "drift_stream",
    # RUN-071 — M-0.7 §13.8 UI 数据源
    "snapshot_inspector",
    "consolidation_history",
    "retention_health",
    "gc_diagnostic",
    # RUN-076 — M-3.4 验证大盘
    "validation_report",
    # RUN-082 — M-3.2 critical 批 (task views / concept_graph / escalation / obligation / commit)
    "task_list",
    "task_graph",
    "critical_path",
    "concept_graph",
    "escalation_inbox",
    "obligation_dashboard",
    "commit_history",
    "gate_check_firing",  # T-FU-09 可观测 — live-target 门运行状况 (潜伏态观测)
    # RUN-085 — M-3.2 view 扩展剩余 18 视图 (§4.3-§4.11 + M-0.2 UI + Phase D)
    "finding_lifecycle",
    "fix_history",
    "at_reference_inspector",
    "stale_references",
    "consumer_list",
    "resolver_decision_inspector",
    "info_need_graph",
    "interview_progress",
    "brief_inspector",
    "nature_judgment_timeline",
    "mismatch_inbox",
    "advisor_consult_log",
    "planning_uncertainty_inbox",
    "cross_plan_dashboard",
    "finding_fix_task_joined",
    "detection_rules_lifecycle_inspector",
    "patterns_surface_candidates",
    "escalations_lifecycle_inspector",
    # RUN-086 — M-3.2 §9 提示性 view (统一 4 类 candidate alert)
    "candidate_alerts",
})

# severity 排序权重 (critical 最前) — finding_inbox / 其它按 severity 排序复用。
_SEVERITY_ORDER = {"critical": 0, "blocking": 0, "major": 1, "normal": 2, "minor": 3}

_INVALIDATION_FILE = ".invalidation.json"
_VIEW_SCHEMA_VERSION = "v1"


@dataclass(frozen=True)
class ViewDescriptor:
    """§6.4 list_views() 返回项 — view 注册信息 + render 状态。"""

    view_id: str
    file_path: str
    data_sources: list[str]
    render_kind: str
    phase: str
    renderer_implemented: bool
    status: str  # "rendered" | "stale" | "not_rendered"
    rendered_watermark: int | None
    current_watermark: int


class ViewEngine:
    """M-3.2 render engine — read-only over projection + event log (§13.2 Inv1).

    构造只读: ProjectionStore / EventLog 都只用读路径。query_view / render_view 全程不 catchup、
    不 emit、不改 canonical 状态。
    """

    def __init__(self, towow_dir: Path) -> None:
        self._towow = towow_dir
        self._graph_dir = towow_dir / "graph"
        self._views_dir = towow_dir / "views"
        self._store = ProjectionStore(self._graph_dir)
        # EventLog 延迟构造 (M-3.2 §7.1 auto-refresh): invalidate_views / _watermark / list_views
        # 只读 projection cursor 文件, 不碰 event log。把 EventLog 留到真需要它的 render/query 路径
        # (provenance_trace / event-sourced view) 才建 —— 否则 commit gate accept 后的 mark-stale
        # 会多构造一个 EventLog (其 __init__ load .idx → 多一次 EventIndex build, 落进 commit
        # 关键路径, 触动 RUN-038 索引复用不变量)。
        self._log_cache: EventLog | None = None

    @property
    def _log(self) -> EventLog:
        """事件日志 (lazy)。仅 render/query 的 event-sourced 视图用; mark-stale 路径不触发构造。"""
        if self._log_cache is None:
            self._log_cache = EventLog(self._towow / "events.log")
        return self._log_cache

    # ─── read-only projection access (NO catchup — §2.3 allow_stale) ────────────

    def _proj(self, name: str) -> dict[str, Any] | None:
        """纯文件读当前 projection state, 不触发 catchup (read-only)。"""
        return self._store.read(name)

    def _proj_list(self, name: str, key: str) -> list[dict[str, Any]]:
        data = self._proj(name)
        if not isinstance(data, dict):
            return []
        items = data.get(key, [])
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict)]

    def _watermark(self) -> int:
        """当前 projection 水位 (cursor-1) —— view freshness 的版本号 (§5.1 projection_versions)。"""
        return max(0, self._store.cursor - 1)

    # ════════════════════════════════════════════════════════════════════════
    #  §6.4 list_views
    # ════════════════════════════════════════════════════════════════════════

    def list_views(self, phase: str | None = None) -> list[ViewDescriptor]:
        """列出全部 view + render 状态 (fresh|stale|not_rendered)。

        status 计算: 渲染文件不存在 → not_rendered; 存在但 frontmatter 水位 < 当前水位 或被
        invalidate_views 标记 → stale; 否则 rendered (fresh)。deterministic ordering: 按 view_id 排。
        """
        current_wm = self._watermark()
        global_inval_wm, per_view_inval = self._invalidation_state()
        out: list[ViewDescriptor] = []
        for vid in sorted(VIEW_REGISTRY):
            vdef = VIEW_REGISTRY[vid]
            if phase is not None and vdef.phase != phase:
                continue
            rendered_wm = self._rendered_watermark(vdef)
            # §7.2 定向失效: 该 view 的有效失效水位 = max(全局失效, 该 view 被点名失效) —— 没被点名
            # 的 view 不受定向失效影响 (auto-refresh 机制只标受影响 view stale, 不一刀切全部)。
            candidates = [w for w in (global_inval_wm, per_view_inval.get(vid)) if w is not None]
            inval_wm = max(candidates) if candidates else None
            status = self._compute_status(rendered_wm, current_wm, inval_wm)
            out.append(
                ViewDescriptor(
                    view_id=vid,
                    file_path=vdef.file_path,
                    data_sources=list(vdef.data_sources),
                    render_kind=vdef.render_kind,
                    phase=vdef.phase,
                    renderer_implemented=vid in _RENDERED_VIEW_IDS,
                    status=status,
                    rendered_watermark=rendered_wm,
                    current_watermark=current_wm,
                ),
            )
        return out

    @staticmethod
    def _compute_status(
        rendered_wm: int | None,
        current_wm: int,
        inval_wm: int | None,
    ) -> str:
        if rendered_wm is None:
            return "not_rendered"
        if rendered_wm < current_wm:
            return "stale"
        if inval_wm is not None and inval_wm >= rendered_wm:
            return "stale"
        return "rendered"

    def _rendered_file(self, vdef: ViewDefinition) -> Path:
        return self._views_dir / vdef.file_path

    def _rendered_watermark(self, vdef: ViewDefinition) -> int | None:
        """从已渲染文件 frontmatter 读 projection 水位; 文件不存在/无 frontmatter → None。"""
        path = self._rendered_file(vdef)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        marker = "projection_watermark:"
        for idx, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if stripped.startswith(marker):
                try:
                    return int(stripped[len(marker):].strip())
                except ValueError:
                    return None
            # 只看首个 frontmatter 块: 命中收尾的 --- (非首行) 即停
            if idx > 0 and stripped == "---":
                break
        return None

    # ════════════════════════════════════════════════════════════════════════
    #  §6.3 invalidate_views
    # ════════════════════════════════════════════════════════════════════════

    def invalidate_views(
        self,
        reason: str | None = None,
        *,
        view_ids: Collection[str] | None = None,
    ) -> dict[str, Any]:
        """标记 view stale (next render overwrite) —— 不删 view 文件 (§6.3)。

        `view_ids=None` → 全局失效 (旧行为, 全部 view 标 stale)。给定 `view_ids` → 只点名失效那些
        view (§7.2 定向失效, 别的 view 不受影响)。写 .towow/views/.invalidation.json 累积记录: 全局
        水位 + per-view 水位 map。这是 mutation API (job 就是标 stale), 与 render/query 的 read-only
        不变量无关。返回本次写下的 marker (含 scope: "all" 或被点名 view_id 列表)。
        """
        self._views_dir.mkdir(parents=True, exist_ok=True)
        wm = self._watermark()
        state = self._read_invalidation_file()
        per_view: dict[str, int] = dict(state.get("views", {}))
        if view_ids is None:
            scope: Any = "all"
            state["global"] = wm
        else:
            named = sorted(set(view_ids))
            scope = named
            for vid in named:
                per_view[vid] = wm
            state["views"] = per_view
        state["invalidated_at"] = datetime.now(UTC).isoformat()
        state["watermark_at_invalidation"] = wm
        state["reason"] = reason or "manual invalidate_views"
        state["scope"] = scope
        path = self._views_dir / _INVALIDATION_FILE
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "invalidated_at": state["invalidated_at"],
            "watermark_at_invalidation": wm,
            "reason": state["reason"],
            "scope": scope,
        }

    def invalidate_views_for_event(
        self,
        event_type: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """§7.2 — 按失效规则表, 把某 canonical 事件影响到的 view 集标 stale。

        这是 view 三触发里 auto-refresh 的**机制本体** (给定事件→该失效哪些 view)。M-3.2 §7.1: 生产
        commit gate accept 后**自动**调本方法 (l0/commit_gate/view_refresh.auto_refresh_views_after_accept,
        按 accepted batch 的 event_type 调) —— mark stale, next `towow status --view` 自动 re-render。
        同时也供 on-demand / 批量显式调用 (CLI / 维护脚本)。未登记事件 → 空失效集 → no-op (不动任何 view)。
        本方法纯只读副产物: 只写 .towow/views/.invalidation.json (标 stale), 不 emit / 不 catchup / 不改 canonical。
        """
        affected = views_invalidated_by(event_type)
        if not affected:
            return {
                "invalidated_at": datetime.now(UTC).isoformat(),
                "watermark_at_invalidation": self._watermark(),
                "reason": reason or f"no invalidation rule for {event_type}",
                "scope": [],
            }
        return self.invalidate_views(
            reason=reason or f"auto-invalidate for event {event_type}",
            view_ids=affected,
        )

    def _read_invalidation_file(self) -> dict[str, Any]:
        path = self._views_dir / _INVALIDATION_FILE
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _invalidation_state(self) -> tuple[int | None, dict[str, int]]:
        """读累积失效 marker → (全局失效水位 | None, per-view 失效水位 map)。

        兼容旧 marker 格式 (只有 watermark_at_invalidation, 无 global/views 字段) → 视作全局失效。
        """
        data = self._read_invalidation_file()
        if not data:
            return None, {}
        global_wm = data.get("global")
        if not isinstance(global_wm, int):
            # 旧格式 fallback: 有 watermark_at_invalidation 且无 scope/views → 当全局
            legacy = data.get("watermark_at_invalidation")
            if isinstance(legacy, int) and "views" not in data and "global" not in data:
                global_wm = legacy
            else:
                global_wm = None
        raw_views = data.get("views", {})
        per_view = {
            k: v for k, v in raw_views.items() if isinstance(v, int)
        } if isinstance(raw_views, dict) else {}
        return global_wm, per_view

    # ════════════════════════════════════════════════════════════════════════
    #  §6.1 query_view  (structured data — pure read)
    # ════════════════════════════════════════════════════════════════════════

    def query_view(
        self,
        view_id: str,
        filters: dict[str, Any] | None = None,
        projection_at: str | None = None,  # historical view 后续 RUN (当前未用)
    ) -> dict[str, Any]:
        """查询 view 结构化数据 (§6.1)。dashboard / provenance_trace 已实装; 其余 raise。"""
        if view_id not in VIEW_REGISTRY:
            raise ViewNotFoundError(view_id)
        f = filters or {}
        dispatch = {
            "dashboard": self._query_dashboard,
            "provenance_trace": lambda: self._query_provenance_trace(f),
            "finding_inbox": lambda: self._query_finding_inbox(f),
            "activation_heatmap": self._query_activation_heatmap,
            "capsule_inspector": lambda: self._query_capsule_inspector(f),
            "capsule_session_attribution": lambda: self._query_capsule_session_attribution(f),
            "resolver_trend": self._query_resolver_trend,
            "drift_stream": self._query_drift_stream,
            "snapshot_inspector": self._query_snapshot_inspector,
            "consolidation_history": self._query_consolidation_history,
            "retention_health": self._query_retention_health,
            "gc_diagnostic": self._query_gc_diagnostic,
            "validation_report": self._query_validation_report,
            # RUN-082 — M-3.2 critical 批
            "task_list": lambda: self._query_task_list(f),
            "task_graph": self._query_task_graph,
            "critical_path": self._query_critical_path,
            "concept_graph": self._query_concept_graph,
            "escalation_inbox": lambda: self._query_escalation_inbox(f),
            "owner_inbox": self._query_owner_inbox,
            "obligation_dashboard": lambda: self._query_obligation_dashboard(f),
            "commit_history": lambda: self._query_commit_history(f),
            "gate_check_firing": lambda: self._query_gate_check_firing(f),  # T-FU-09 可观测
            # RUN-085 — M-3.2 view 扩展剩余 18 视图
            "finding_lifecycle": lambda: self._query_finding_lifecycle(f),
            "fix_history": lambda: self._query_fix_history(f),
            "at_reference_inspector": lambda: self._query_at_reference_inspector(f),
            "stale_references": self._query_stale_references,
            "consumer_list": lambda: self._query_consumer_list(f),
            "resolver_decision_inspector": lambda: self._query_resolver_decision_inspector(f),
            "info_need_graph": lambda: self._query_info_need_graph(f),
            "interview_progress": lambda: self._query_interview_progress(f),
            "brief_inspector": lambda: self._query_brief_inspector(f),
            "nature_judgment_timeline": self._query_nature_judgment_timeline,
            "mismatch_inbox": lambda: self._query_mismatch_inbox(f),
            "advisor_consult_log": lambda: self._query_advisor_consult_log(f),
            "planning_uncertainty_inbox": lambda: self._query_planning_uncertainty_inbox(f),
            "cross_plan_dashboard": self._query_cross_plan_dashboard,
            "finding_fix_task_joined": lambda: self._query_finding_fix_task_joined(f),
            "detection_rules_lifecycle_inspector":
                lambda: self._query_detection_rules_lifecycle_inspector(f),
            "patterns_surface_candidates": self._query_patterns_surface_candidates,
            "escalations_lifecycle_inspector":
                lambda: self._query_escalations_lifecycle_inspector(f),
            # RUN-086 — M-3.2 §9 提示性 view
            "candidate_alerts": self._query_candidate_alerts,
        }
        if view_id in dispatch:
            return dispatch[view_id]()
        raise ViewRendererNotImplemented(
            f"view {view_id!r} 已登记但 render 未实装 (RUN-047/071 已实装 "
            f"{len(_RENDERED_VIEW_IDS)} 个; 其余视图后续 RUN)",
        )

    # ─── dashboard query (§4.1) ─────────────────────────────────────────────

    def _query_dashboard(self) -> dict[str, Any]:
        """聚合真 projection → 系统状态总览。缺失的 projection 诚实标 materialized=False (不假造)。"""
        sections: dict[str, Any] = {}
        alerts: list[dict[str, Any]] = []

        # capabilities — 最丰富信号 (能力看板)
        caps = self._proj_list("capability_status", "capabilities")
        if self._proj("capability_status") is not None:
            by_cls = dict(Counter(str(c.get("current_classification")) for c in caps))
            # RUN-096 — enforcement_level orthogonal axis: built (建好+单测过) vs enforced (真路径
            # 物理成立)。让 done-real 头条数字诚实——区分'做完'(built) 和'达 spec 效果'(enforced)。
            # 未标 (None/缺) = built/unmarked (现有 done-real 含义)。
            enforced = sum(1 for c in caps if c.get("enforcement_level") == "enforced")
            done_real = [c for c in caps if c.get("current_classification") == "done-real"]
            done_real_enforced = sum(
                1 for c in done_real if c.get("enforcement_level") == "enforced"
            )
            sections["capabilities"] = {
                "materialized": True,
                "total": len(caps),
                "by_classification": dict(sorted(by_cls.items())),
                "by_enforcement": {
                    "enforced": enforced,
                    "built_or_unmarked": len(caps) - enforced,
                },
                "done_real_enforced": done_real_enforced,
                "done_real_built_only": len(done_real) - done_real_enforced,
            }
            fake = [c for c in caps if c.get("current_classification") == "fake-done"]
            if fake:
                alerts.append({
                    "kind": "fake_done_capabilities",
                    "count": len(fake),
                    "detail": "能力声明 done 但被复核判定灌水 (fake-done) — 需重核",
                })
        else:
            sections["capabilities"] = {"materialized": False, "total": 0}

        # commits
        commits = self._proj_list("commit_history", "commits")
        if self._proj("commit_history") is not None:
            by_verdict = dict(Counter(str(c.get("verdict")) for c in commits))
            recent = [
                {"commit_event_id": c.get("commit_event_id"), "verdict": c.get("verdict")}
                for c in commits[-5:]
            ]
            sections["commits"] = {
                "materialized": True,
                "total": len(commits),
                "by_verdict": dict(sorted(by_verdict.items())),
                "recent": recent,
            }
        else:
            sections["commits"] = {"materialized": False, "total": 0}

        # obligations
        obls = self._proj_list("obligation_lifecycle_state", "obligations")
        if self._proj("obligation_lifecycle_state") is not None:
            sections["obligations"] = {
                "materialized": True,
                "total": len(obls),
                "by_canonical_state": dict(sorted(Counter(
                    str(o.get("canonical_state")) for o in obls).items())),
                "by_severity": dict(sorted(Counter(str(o.get("severity")) for o in obls).items())),
            }
        else:
            sections["obligations"] = {"materialized": False, "total": 0}

        # debts — open blocking 进 alert
        debts = self._proj_list("debt_registry", "debts")
        if self._proj("debt_registry") is not None:
            open_blocking = [
                d for d in debts
                if d.get("state") == "open" and d.get("severity") == "blocking"
            ]
            sections["debts"] = {
                "materialized": True,
                "total": len(debts),
                "by_state": dict(sorted(Counter(str(d.get("state")) for d in debts).items())),
                "by_severity": dict(sorted(Counter(str(d.get("severity")) for d in debts).items())),
            }
            if open_blocking:
                alerts.append({
                    "kind": "open_blocking_debts",
                    "count": len(open_blocking),
                    "detail": "; ".join(
                        f"{d.get('debt_id')}: {str(d.get('title', ''))[:50]}"
                        for d in open_blocking[:5]
                    ),
                })
        else:
            sections["debts"] = {"materialized": False, "total": 0}

        # decisions / judgments
        judgments = self._proj_list("decision", "judgments")
        if self._proj("decision") is not None:
            sections["decisions"] = {
                "materialized": True,
                "total": len(judgments),
                "by_validity": dict(sorted(Counter(
                    str(j.get("current_validity")) for j in judgments).items())),
                "by_type": dict(sorted(Counter(str(j.get("judgment_type")) for j in judgments).items())),
            }
        else:
            sections["decisions"] = {"materialized": False, "total": 0}

        # concepts
        concept_data = self._proj("concept_graph")
        if isinstance(concept_data, dict):
            nodes = concept_data.get("nodes", [])
            edges = concept_data.get("edges", [])
            sections["concepts"] = {
                "materialized": True,
                "nodes": len(nodes) if isinstance(nodes, list) else 0,
                "edges": len(edges) if isinstance(edges, list) else 0,
            }
        else:
            sections["concepts"] = {"materialized": False, "nodes": 0, "edges": 0}

        # tasks / findings — 本仓库无对应事件 → projection 未物化 (诚实标, 不假造数字)
        for proj_name, sec_name in (("task_graph", "tasks"), ("finding_lifecycle", "findings")):
            if self._proj(proj_name) is not None:
                items = self._proj_list(proj_name, "nodes" if proj_name == "task_graph" else "findings")
                sections[sec_name] = {"materialized": True, "total": len(items)}
            else:
                sections[sec_name] = {
                    "materialized": False,
                    "total": 0,
                    "note": f"{proj_name} projection 未物化 (无对应事件)",
                }

        # interviews
        info = self._proj("information_need_graph")
        if isinstance(info, dict):
            ses = info.get("sessions", {})
            sections["interviews"] = {
                "materialized": True,
                "sessions": len(ses) if isinstance(ses, dict) else 0,
            }
        else:
            sections["interviews"] = {"materialized": False, "sessions": 0}

        # escalations — owner 在等什么 (T-R08-1: dashboard 之前完全没有 escalation 板块, owner
        # 在主面板看不到"系统在等我几件事"; 这里补上, 复用 escalation_inbox 的 pending 计数,
        # 有人死等就升一条 alert 顶到 owner 眼前)。
        inbox = self._query_escalation_inbox({})
        pending_esc = int(inbox.get("pending_count", 0))
        sections["escalations"] = {
            "materialized": bool(inbox.get("materialized")),
            "pending_count": pending_esc,
            "total": int(inbox.get("total", 0)),
        }
        if pending_esc > 0:
            alerts.append({
                "kind": "escalations_awaiting_owner",
                "count": pending_esc,
                "detail": f"有 {pending_esc} 件升级在等你拍板 — 见 escalation_inbox",
            })

        materialized_sections = [
            k for k, v in sections.items() if isinstance(v, dict) and v.get("materialized")
        ]
        return {
            "view_id": "dashboard",
            "watermark": self._watermark(),
            "sections": sections,
            "alerts": alerts,
            "materialized_sections": sorted(materialized_sections),
            "is_empty": len(materialized_sections) == 0,
        }

    # ─── provenance_trace query (§4.11) ─────────────────────────────────────

    def _query_provenance_trace(self, filters: dict[str, Any]) -> dict[str, Any]:
        """从一个结论 (seed) 沿事件链追回源头 (§4.11 / §13.2 Inv4)。

        seed = event_id (evt-…) 或 entity_id (capability_id / debt_id / judgment evt / concept_id /
        obligation_id)。追溯 = 跨 projection join: 读 seed 的 projection 节点拿 backlink event_id
        (baseline_ingested / advanced_by / registered_by / resolved_by / created_at …) → get_event
        解析 → 沿 causation_event_id (get_causation_chain) + supersede (get_supersede_chain) +
        subject 索引 (get_events_by_entity) 拼链, 按 sequence_number 排 (最早=源, 最晚=结论)。

        causation_event_id 在很多事件为 null (debt-532b9d47a6c9 反向 link 未强制) → 用 projection
        backlink 兜底, 并把缺 causation 的点记进 causation_gaps (owner 决策 surface, 非静默吞)。
        """
        seed = str(filters.get("seed", "")).strip()
        if not seed:
            return {
                "view_id": "provenance_trace",
                "seed": None,
                "resolved": False,
                "error": "filters 缺 seed (event_id 或 entity_id)",
                "chain": [],
                "is_empty": True,
            }

        seed_kind, anchors = self._resolve_seed_anchors(seed)
        links_followed = {
            "projection_backlink": 0,
            "causation": 0,
            "supersede": 0,
            "subject_index": 0,
        }
        collected: dict[str, EventRecord] = {}
        reached_via: dict[str, list[str]] = {}

        def _add(rec: EventRecord, via: str) -> None:
            reached_via.setdefault(rec.event_id, [])
            if via not in reached_via[rec.event_id]:
                reached_via[rec.event_id].append(via)
            collected[rec.event_id] = rec

        # 1) projection backlink anchors
        for anchor in anchors:
            ev = self._log.get_event(anchor["event_id"])
            if ev is None:
                continue
            links_followed["projection_backlink"] += 1
            _add(ev, f"projection_backlink:{anchor['role']}")
            # 2) causation chain upward
            causation_chain = self._log.get_causation_chain(ev.event_id)
            for c in causation_chain[1:]:
                links_followed["causation"] += 1
                _add(c, "causation")
            # 3) supersede chain
            sup = self._log.get_supersede_chain(ev.event_id)
            for s in sup[1:]:
                links_followed["supersede"] += 1
                _add(s, "supersede")

        # 4) subject index — 直接作用于 seed 实体的事件 (entity_id 多形态尝试)
        for entity_type in ("concept", "task", "obligation", "capability", "finding", "debt"):
            for rec in self._log.get_events_by_entity(entity_type, seed):
                links_followed["subject_index"] += 1
                _add(rec, f"subject_index:{entity_type}")

        chain_records = sorted(collected.values(), key=lambda r: r.sequence_number)
        chain = [self._event_hop(r, reached_via.get(r.event_id, [])) for r in chain_records]

        causation_gaps = [
            {"event_id": r.event_id, "event_type": str(r.event_type),
             "note": "causation_event_id 为 null — 该跳无显式因果反向 link (debt-532b9d47a6c9)"}
            for r in chain_records
            if r.provenance.causation_event_id is None
        ]

        return {
            "view_id": "provenance_trace",
            "seed": seed,
            "seed_kind": seed_kind,
            "resolved": len(chain) > 0,
            "anchors": anchors,
            "chain": chain,
            "source_event": chain[0] if chain else None,
            "conclusion_event": chain[-1] if chain else None,
            "links_followed": links_followed,
            "causation_gaps": causation_gaps,
            "is_empty": len(chain) == 0,
        }

    def _resolve_seed_anchors(self, seed: str) -> tuple[str, list[dict[str, str]]]:
        """seed → (seed_kind, anchors[{event_id, role}])。跨 projection 查 backlink。"""
        anchors: list[dict[str, str]] = []

        # event_id 直接作 anchor
        if seed.startswith("evt-"):
            anchors.append({"event_id": seed, "role": "seed_event"})

        # capability — capability_id 形如 "module::name"
        for c in self._proj_list("capability_status", "capabilities"):
            if c.get("capability_id") == seed:
                if c.get("baseline_ingested_by_event_id"):
                    anchors.append({"event_id": str(c["baseline_ingested_by_event_id"]),
                                    "role": "capability_baseline_source"})
                if c.get("advanced_by_event_id"):
                    anchors.append({"event_id": str(c["advanced_by_event_id"]),
                                    "role": "capability_advance_conclusion"})
                return ("capability", anchors)

        # debt — debt_id 形如 "debt-…"
        for d in self._proj_list("debt_registry", "debts"):
            if d.get("debt_id") == seed:
                if d.get("registered_by_event_id"):
                    anchors.append({"event_id": str(d["registered_by_event_id"]),
                                    "role": "debt_registered_source"})
                if d.get("resolved_by_event_id"):
                    anchors.append({"event_id": str(d["resolved_by_event_id"]),
                                    "role": "debt_resolved_conclusion"})
                return ("debt", anchors)

        # judgment — judgment_event_id (evt-) 或 subject 命中
        for j in self._proj_list("decision", "judgments"):
            if j.get("judgment_event_id") == seed:
                anchors.append({"event_id": str(j["judgment_event_id"]), "role": "judgment"})
                if j.get("superseded_by_event_id"):
                    anchors.append({"event_id": str(j["superseded_by_event_id"]),
                                    "role": "judgment_superseded_by"})
                return ("judgment", anchors)

        # commit — commit_event_id (evt-)
        for cm in self._proj_list("commit_history", "commits"):
            if cm.get("commit_event_id") == seed:
                anchors.append({"event_id": str(cm["commit_event_id"]), "role": "commit"})
                if cm.get("envelope_event_id"):
                    anchors.append({"event_id": str(cm["envelope_event_id"]),
                                    "role": "commit_envelope_source"})
                return ("commit", anchors)

        # concept node
        concept_data = self._proj("concept_graph")
        if isinstance(concept_data, dict):
            nodes = concept_data.get("nodes", [])
            if isinstance(nodes, list):
                for n in nodes:
                    if isinstance(n, dict) and n.get("concept_id") == seed:
                        if n.get("created_at_event_id"):
                            anchors.append({"event_id": str(n["created_at_event_id"]),
                                            "role": "concept_created_source"})
                        return ("concept", anchors)

        # obligation
        for o in self._proj_list("obligation_lifecycle_state", "obligations"):
            if o.get("obligation_id") == seed:
                if o.get("created_at_event_id"):
                    anchors.append({"event_id": str(o["created_at_event_id"]),
                                    "role": "obligation_created_source"})
                if o.get("last_state_change_event_id"):
                    anchors.append({"event_id": str(o["last_state_change_event_id"]),
                                    "role": "obligation_last_change"})
                return ("obligation", anchors)

        if anchors:  # 只命中 evt- 直查
            return ("event", anchors)
        return ("unknown", anchors)

    @staticmethod
    def _event_hop(rec: EventRecord, via: list[str]) -> dict[str, Any]:
        """一跳事件的结构化摘要 (provenance chain 项)。"""
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        summary_keys = (
            "capability_id", "from_classification", "to_classification", "advanced_by",
            "debt_id", "title", "verdict", "judgment_type", "evidence_ref", "name",
        )
        payload_summary = {
            k: (str(payload[k])[:80]) for k in summary_keys if k in payload
        }
        return {
            "seq": rec.sequence_number,
            "event_id": rec.event_id,
            "event_type": str(rec.event_type),
            "actor_type": rec.provenance.actor_type,
            "actor_id": rec.provenance.actor_id,
            "timestamp": rec.timestamp.isoformat(),
            "causation_event_id": rec.provenance.causation_event_id,
            "subjects": [
                {"entity_type": str(s.entity_type), "entity_id": s.entity_id[:80]}
                for s in rec.subjects
            ],
            "payload_summary": payload_summary,
            "reached_via": via,
        }

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-071 — M-0.2 §3.12-§3.17 UI 视图型 + M-0.7 §13.8 4 视图 query
    #  全部 pure read: ProjectionStore.read() + EventLog.get_events_by_type() +
    #  snapshot-module 状态文件只读。空数据诚实标 (非假造 — §4.2 forbidden shortcut)。
    # ════════════════════════════════════════════════════════════════════════

    def _events_by_type(self, event_type: EventType) -> list[EventRecord]:
        """按 event_type 读事件 (committed 序), 再按 sequence_number 排稳定 (deterministic)。"""
        evs = self._log.get_events_by_type(event_type)
        return sorted(evs, key=lambda r: r.sequence_number)

    @staticmethod
    def _payload(rec: EventRecord) -> dict[str, Any]:
        return rec.payload if isinstance(rec.payload, dict) else {}

    # ─── finding_inbox (= M-0.2 review_inbox / M-3.2 §4.3) ──────────────────

    def _query_finding_inbox(self, filters: dict[str, Any]) -> dict[str, Any]:
        """跨 session finding inbox — 读 finding_lifecycle projection, 按 severity 排 + 按
        lifecycle_state 分组 (M-3.2 §4.3 / M-0.2 §3.12)。projection 未物化 → 诚实标。"""
        materialized = self._proj("finding_lifecycle") is not None
        findings = self._proj_list("finding_lifecycle", "findings")
        sev_filter = filters.get("severity")
        if sev_filter:
            findings = [f for f in findings if f.get("severity") == sev_filter]
        rows = sorted(
            findings,
            key=lambda f: (
                _SEVERITY_ORDER.get(str(f.get("severity")), 99),
                str(f.get("finding_id")),
            ),
        )
        items = [
            {
                "finding_id": f.get("finding_id"),
                "severity": f.get("severity"),
                "lifecycle_state": f.get("lifecycle_state"),
                "closure_state": f.get("closure_state"),
                "verification_state": f.get("verification_state"),
                "review_dimension": f.get("review_dimension"),
                "detection_method": f.get("detection_method"),
                "is_preexisting": f.get("is_preexisting"),
            }
            for f in rows
        ]
        return {
            "view_id": "finding_inbox",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(items),
            "by_lifecycle_state": dict(sorted(
                Counter(str(f.get("lifecycle_state")) for f in rows).items())),
            "by_severity": dict(sorted(
                Counter(str(f.get("severity")) for f in rows).items())),
            "findings": items,
            "filtered_by_severity": sev_filter,
            "is_empty": len(items) == 0,
        }

    # ─── activation_heatmap (= M-0.2 obligation_heatmap / M-3.2 §4.5) ───────

    def _query_activation_heatmap(self) -> dict[str, Any]:
        """obligation × task 激活矩阵 — 聚合 ObligationActivated events (M-3.2 §4.5 /
        M-0.2 §3.16): 每条 obligation 的 activation 频次 / 从未 active 的 / 频次异常的。"""
        events = self._events_by_type(EventType.OBLIGATION_ACTIVATED)
        cells: Counter[tuple[str, str]] = Counter()
        per_obl: Counter[str] = Counter()
        obl_tasks: dict[str, set[str]] = defaultdict(set)
        for ev in events:
            p = self._payload(ev)
            obl = str(p.get("obligation_id"))
            task = str(p.get("activated_for_task_id"))
            cells[(obl, task)] += 1
            per_obl[obl] += 1
            obl_tasks[obl].add(task)
        matrix = sorted(
            ({"obligation_id": o, "task_id": t, "count": c} for (o, t), c in cells.items()),
            key=lambda d: (-d["count"], d["obligation_id"], d["task_id"]),
        )
        # 从未 active: projection 里登记但无 activation 事件的 obligation。
        obls = self._proj_list("obligation_lifecycle_state", "obligations")
        activated_ids = set(per_obl)
        never_activated = sorted(
            str(o.get("obligation_id")) for o in obls
            if o.get("obligation_id") and str(o.get("obligation_id")) not in activated_ids
        )
        # 频次异常: count > 2× 中位频次 (轻量启发, 标出最频繁的 obligation)。
        counts = sorted(per_obl.values())
        anomalies: list[dict[str, Any]] = []
        if counts:
            median = counts[len(counts) // 2]
            thresh = max(2 * median, median + 2)
            anomalies = sorted(
                ({"obligation_id": o, "count": c} for o, c in per_obl.items() if c > thresh),
                key=lambda d: (-d["count"], d["obligation_id"]),
            )
        return {
            "view_id": "activation_heatmap",
            "watermark": self._watermark(),
            "total_activations": len(events),
            "distinct_obligations": len(per_obl),
            "distinct_tasks": len({t for (_o, t) in cells}),
            "per_obligation_count": dict(sorted(per_obl.items())),
            "matrix": matrix,
            "never_activated": never_activated,
            "frequency_anomalies": anomalies,
            "is_empty": len(events) == 0,
        }

    # ─── capsule_inspector (M-0.2 §3.14, 新增) ──────────────────────────────

    def _query_capsule_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """每个 task 的 capsule assembly 结果 — 读 ResolverDecisionMade.after_state
        (M-0.2 §3.14): candidates / hit / miss / fallback / placement / cache_hit。"""
        events = self._events_by_type(EventType.RESOLVER_DECISION_MADE)
        task_filter = filters.get("task_id")
        decisions: list[dict[str, Any]] = []
        for ev in events:
            p = self._payload(ev)
            after = p.get("after_state", {})
            if not isinstance(after, dict):
                after = {}
            task_id = after.get("task_id")
            if task_filter and task_id != task_filter:
                continue
            stages = after.get("pipeline_stages", {})
            stages = stages if isinstance(stages, dict) else {}
            s2 = stages.get("stage_2_candidate_retrieval", {}) or {}
            s3 = stages.get("stage_3_mechanical_filtering", {}) or {}
            s6 = stages.get("stage_6_placement", [])
            decisions.append({
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
                "task_id": task_id,
                "resolver_id": after.get("resolver_id"),
                "candidate_count": s2.get("candidate_count") if isinstance(s2, dict) else None,
                "mechanical_hit_count": s3.get("mechanical_hit_count") if isinstance(s3, dict) else None,
                "mechanical_miss_count": s3.get("mechanical_miss_count") if isinstance(s3, dict) else None,
                "placement_count": len(s6) if isinstance(s6, list) else 0,
                "cache_hit": after.get("cache_hit"),
                "fallback_applied": p.get("fallback_applied"),
                "final_active_set": after.get("final_active_set", []),
            })
        return {
            "view_id": "capsule_inspector",
            "watermark": self._watermark(),
            "total": len(decisions),
            "distinct_tasks": len({d["task_id"] for d in decisions if d["task_id"]}),
            "decisions": decisions,
            "filtered_by_task_id": task_filter,
            "is_empty": len(decisions) == 0,
        }

    # ─── capsule_session_attribution (F-CIR-capsule-session-view-consumer-unbuilt) ──

    def _task_obligation_attribution(self) -> dict[str, dict[str, list[str]]]:
        """task_id → {"obligations": [...全部 final_active_set...], "red_lines": [...红线子集...]}。

        从 ResolverDecisionMade.after_state 取每个 task 注入的真实义务 ID (final_active_set) +
        经 pipeline_stages.stage_6_placement[].section == RED_LINE_OBLIGATIONS 区分红线子集。
        capsule_inspector 已读 final_active_set 但只计数 (per-task); 这里抽成 per-task ID 表, 供
        per-session 胶囊归属按 covered_subjects→task 关联出真实义务/红线 ID (非 summary 计数兜底)。
        """
        out: dict[str, dict[str, list[str]]] = {}
        for ev in self._events_by_type(EventType.RESOLVER_DECISION_MADE):
            after = self._payload(ev).get("after_state", {})
            if not isinstance(after, dict):
                continue
            task_id = after.get("task_id")
            if not task_id:
                continue
            obls = [o for o in after.get("final_active_set", []) if isinstance(o, str)]
            stages = after.get("pipeline_stages", {})
            placements = stages.get("stage_6_placement", []) if isinstance(stages, dict) else []
            red_lines = [
                p["obligation_id"]
                for p in placements
                if isinstance(p, dict)
                and p.get("section") == "RED_LINE_OBLIGATIONS"
                and isinstance(p.get("obligation_id"), str)
            ]
            # 同一 task 可能多次 resolver 决策 (重判); 末次为准 (events 已按 seq 升序, 后写覆盖)。
            out[task_id] = {"obligations": obls, "red_lines": red_lines}
        return out

    def _query_capsule_session_attribution(self, filters: dict[str, Any]) -> dict[str, Any]:
        """per-session 胶囊归属 — 消费 CapsuleCompiled.provenance.session_id (F-CIR fix)。

        capsule_inspector 是 per-task 资源视角 (读 ResolverDecisionMade), 不读源头 fix
        (F-CIR-capsule-fork-not-session) 已盖的 CapsuleCompiled.provenance.session_id → 大脑无法落到
        '某会话单元注入了哪些义务/红线/概念'。本视图按会话单元聚合 CapsuleCompiled, 展示该单元注入的
        真实 ID (不是阶段均值/summary 计数兜底):

        - 概念: CapsuleCompiled.neighborhood_concept_ids (直接, 真实 concept_id)
        - 义务/红线: CapsuleCompiled.covered_subjects[entity_type=task] → ResolverDecisionMade
          [task].final_active_set (义务真实 ID) + stage_6_placement RED_LINE_OBLIGATIONS (红线子集)

        filters.session_id 选单会话; 缺省返回全部带 session 的会话。只读 events, 无新 projection。
        """
        session_filter = filters.get("session_id")
        task_attr = self._task_obligation_attribution()
        # session_id → 累积
        by_session: dict[str, dict[str, Any]] = {}
        for ev in self._events_by_type(EventType.CAPSULE_COMPILED):
            sid = ev.provenance.session_id
            if not sid:
                # 源头 fix 前的 fork-only 胶囊 (provenance.session_id 恒空) — 无法归属到会话单元, 跳过。
                continue
            if session_filter and sid != session_filter:
                continue
            p = self._payload(ev)
            summary = p.get("capsule_sections_summary", {})
            summary = summary if isinstance(summary, dict) else {}
            concepts = [c for c in p.get("neighborhood_concept_ids", []) if isinstance(c, str)]
            tasks = sorted({
                s["entity_id"]
                for s in p.get("covered_subjects", [])
                if isinstance(s, dict) and s.get("entity_type") == "task"
                and isinstance(s.get("entity_id"), str)
            })
            unit_obls: set[str] = set()
            unit_red: set[str] = set()
            for t in tasks:
                attr = task_attr.get(t)
                if attr:
                    unit_obls.update(attr["obligations"])
                    unit_red.update(attr["red_lines"])
            unit = {
                "capsule_event_id": ev.event_id,
                "seq": ev.sequence_number,
                "scene_type": p.get("scene_type"),
                "target_fork_id": p.get("target_fork_id"),
                "tasks": tasks,
                "concept_ids": concepts,
                "obligation_ids": sorted(unit_obls),
                "red_line_ids": sorted(unit_red),
                # summary 计数 (源头已盖) 作交叉校验 — ID 集与计数应一致量级。
                "summary_obligation_count": summary.get("obligation_count"),
                "summary_red_line_count": summary.get("red_line_count"),
            }
            agg = by_session.setdefault(sid, {
                "session_id": sid,
                "skill_id": ev.provenance.skill_id,
                "capsule_unit_count": 0,
                "units": [],
                "_concepts": set(),
                "_obligations": set(),
                "_red_lines": set(),
            })
            agg["capsule_unit_count"] += 1
            agg["units"].append(unit)
            agg["_concepts"].update(concepts)
            agg["_obligations"].update(unit_obls)
            agg["_red_lines"].update(unit_red)
        sessions: list[dict[str, Any]] = []
        for sid in sorted(by_session):
            agg = by_session[sid]
            sessions.append({
                "session_id": agg["session_id"],
                "skill_id": agg["skill_id"],
                "capsule_unit_count": agg["capsule_unit_count"],
                "distinct_concept_count": len(agg["_concepts"]),
                "distinct_obligation_count": len(agg["_obligations"]),
                "distinct_red_line_count": len(agg["_red_lines"]),
                "concept_ids": sorted(agg["_concepts"]),
                "obligation_ids": sorted(agg["_obligations"]),
                "red_line_ids": sorted(agg["_red_lines"]),
                "units": sorted(agg["units"], key=lambda u: u["seq"]),
            })
        return {
            "view_id": "capsule_session_attribution",
            "watermark": self._watermark(),
            "total_sessions": len(sessions),
            "sessions": sessions,
            "filtered_by_session_id": session_filter,
            "is_empty": len(sessions) == 0,
        }

    # ─── resolver_trend (M-0.2 §3.15, 新增) ─────────────────────────────────

    def _query_resolver_trend(self) -> dict[str, Any]:
        """ResolverDecisionMade 聚合统计 (M-0.2 §3.15): 调用频率 / cache 命中率 /
        fallback 率 / 平均耗时。confidence 在 payload 顶层恒 None (sub-judgment 嵌在
        stages) — 诚实标 confidence_note, 不假造平均值。"""
        events = self._events_by_type(EventType.RESOLVER_DECISION_MADE)
        total = len(events)
        cache_hits = 0
        fallbacks = 0
        runtimes: list[int] = []
        for ev in events:
            p = self._payload(ev)
            after = p.get("after_state", {})
            after = after if isinstance(after, dict) else {}
            if after.get("cache_hit") is True:
                cache_hits += 1
            if p.get("fallback_applied") is True:
                fallbacks += 1
            rt = after.get("resolver_runtime_ms")
            if isinstance(rt, int) and not isinstance(rt, bool):
                runtimes.append(rt)

        def _rate(n: int) -> float | None:
            return round(n / total, 4) if total else None

        return {
            "view_id": "resolver_trend",
            "watermark": self._watermark(),
            "total_calls": total,
            "cache_hit_count": cache_hits,
            "cache_hit_rate": _rate(cache_hits),
            "fallback_count": fallbacks,
            "fallback_rate": _rate(fallbacks),
            "avg_runtime_ms": round(sum(runtimes) / len(runtimes), 2) if runtimes else None,
            "runtime_samples": len(runtimes),
            "confidence_note": "ResolverDecisionMade.confidence 顶层恒 None (子判断 confidence "
                               "嵌在 pipeline_stages.stage_4) — 此处不假造平均 confidence",
            "is_empty": total == 0,
        }

    # ─── drift_stream (M-0.2 §3.17, 新增) ───────────────────────────────────

    def _query_drift_stream(self) -> dict[str, Any]:
        """DriftDetected 事件流 (M-0.2 §3.17): 时间倒序 + 涉及节点 + drift_type。"""
        events = self._events_by_type(EventType.DRIFT_DETECTED)
        items: list[dict[str, Any]] = []
        for ev in events:
            p = self._payload(ev)
            nodes = p.get("drift_nodes", [])
            drift_nodes = [
                {"entity_type": n.get("entity_type"), "entity_id": n.get("entity_id")}
                for n in nodes if isinstance(n, dict)
            ]
            items.append({
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
                "timestamp": ev.timestamp.isoformat(),
                "drift_type": p.get("drift_type"),
                "drift_nodes": drift_nodes,
                "envelope_event_id": p.get("envelope_event_id"),
                "original_assembly_event_id": p.get("original_assembly_event_id"),
            })
        items.sort(key=lambda d: d["seq"], reverse=True)  # 时间倒序 = seq 倒序
        return {
            "view_id": "drift_stream",
            "watermark": self._watermark(),
            "total": len(items),
            "by_drift_type": dict(sorted(
                Counter(str(i["drift_type"]) for i in items).items())),
            "drifts": items,
            "is_empty": len(items) == 0,
        }

    # ─── snapshot_inspector (M-0.7 §13.8) ───────────────────────────────────

    def _query_snapshot_inspector(self) -> dict[str, Any]:
        """SnapshotCreated.payload — bundle_hash + per_projection_state_hashes (M-0.7 §13.8)。"""
        events = self._events_by_type(EventType.SNAPSHOT_CREATED)
        snapshots: list[dict[str, Any]] = []
        for ev in events:
            p = self._payload(ev)
            per_proj = p.get("per_projection", [])
            per_projection = [
                {
                    "projection_name": pp.get("projection_name"),
                    "projection_state_hash": pp.get("projection_state_hash"),
                    "watermark": pp.get("watermark"),
                }
                for pp in per_proj if isinstance(pp, dict)
            ]
            snapshots.append({
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
                "event_offset": p.get("event_offset"),
                "snapshot_type": p.get("snapshot_type"),
                "bundle_hash": p.get("bundle_hash"),
                "covered_projections": p.get("covered_projections", []),
                "snapshot_storage_path": p.get("snapshot_storage_path"),
                "consolidation_event_id": p.get("consolidation_event_id"),
                "per_projection": per_projection,
            })
        return {
            "view_id": "snapshot_inspector",
            "watermark": self._watermark(),
            "total": len(snapshots),
            "latest_offset": snapshots[-1]["event_offset"] if snapshots else None,
            "snapshots": snapshots,
            "is_empty": len(snapshots) == 0,
        }

    # ─── consolidation_history (M-0.7 §13.8) ────────────────────────────────

    def _query_consolidation_history(self) -> dict[str, Any]:
        """CrossRunConsolidationCommitted timeline (M-0.7 §13.8)。"""
        events = self._events_by_type(EventType.CROSS_RUN_CONSOLIDATION_COMMITTED)
        items: list[dict[str, Any]] = []
        for ev in events:
            p = self._payload(ev)
            seg = p.get("segment_range", {})
            seg = seg if isinstance(seg, dict) else {}
            items.append({
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
                "timestamp": ev.timestamp.isoformat(),
                "segment_range": seg,
                "digest_hash": p.get("digest_hash"),
                "digest_type": p.get("digest_type"),
                "original_event_count": p.get("original_event_count"),
                "consolidation_run_id": p.get("consolidation_run_id"),
                "snapshot_event_id": p.get("snapshot_event_id"),
            })
        return {
            "view_id": "consolidation_history",
            "watermark": self._watermark(),
            "total": len(items),
            "total_events_consolidated": sum(
                i["original_event_count"] for i in items
                if isinstance(i["original_event_count"], int)
            ),
            "consolidations": items,
            "is_empty": len(items) == 0,
        }

    # ─── retention_health (M-0.7 §13.8) ─────────────────────────────────────

    def _query_retention_health(self) -> dict[str, Any]:
        """retention_watermark trend + consumer cursors + hot segment 统计 (M-0.7 §13.8)。
        读 snapshot-module/retention-watermark.json (M-0.7 push) + hot 段文件统计 (只读)。"""
        rw = load_retention_watermark(self._towow)
        rw = rw if isinstance(rw, dict) else None
        snaps = self._events_by_type(EventType.SNAPSHOT_CREATED)
        # hot segment 累积统计 (只读文件 stat, 不解析逐事件 — 计文件数 + 字节)。
        hot_dir = self._towow / "events" / "hot"
        hot_files = sorted(hot_dir.glob("*.jsonl")) if hot_dir.exists() else []
        hot_bytes = sum(p.stat().st_size for p in hot_files)
        return {
            "view_id": "retention_health",
            "watermark": self._watermark(),
            "retention_watermark_materialized": rw is not None,
            "retention_watermark": rw.get("retention_watermark") if rw else None,
            "consumer_watermark": rw.get("consumer_watermark") if rw else None,
            "hot_replay_floor": rw.get("hot_replay_floor") if rw else None,
            "pushed_at": rw.get("pushed_at") if rw else None,
            "snapshot_count": len(snaps),
            "latest_snapshot_offset": (
                self._payload(snaps[-1]).get("event_offset") if snaps else None
            ),
            "hot_segment_files": len(hot_files),
            "hot_segment_bytes": hot_bytes,
            "is_empty": rw is None and len(snaps) == 0,
        }

    # ─── gc_diagnostic (M-0.7 §13.8) ────────────────────────────────────────

    def _query_gc_diagnostic(self) -> dict[str, Any]:
        """GC run 历史 + root composition + 不可达统计 (M-0.7 §13.8)。读 snapshot-module/
        gc-state.json (M-0.7 §9 write_gc_state)。语义根集 hash 模块诚实标未落 — 不假装。"""
        gc = load_gc_state(self._towow)
        gc = gc if isinstance(gc, dict) else None
        return {
            "view_id": "gc_diagnostic",
            "watermark": self._watermark(),
            "gc_state_materialized": gc is not None,
            "run_count": gc.get("run_count") if gc else 0,
            "last_gc_at": gc.get("last_gc_at") if gc else None,
            "last_gc_archive_seq_bound": gc.get("last_gc_archive_seq_bound") if gc else None,
            "last_gc_consumer_watermark": gc.get("last_gc_consumer_watermark") if gc else None,
            "last_gc_hot_replay_floor": gc.get("last_gc_hot_replay_floor") if gc else None,
            "gc_run_state_hash": gc.get("gc_run_state_hash") if gc else None,
            "root_set_note": "语义根集 hash (§3.3 supersede chains / projection refs 全集) "
                             "尚未落地 — M-0.7 state.py 模块诚实边界, 此处不假装已含根集 composition",
            "is_empty": gc is None,
        }

    # ─── validation_report (M-3.4 §3.3 验证大盘, RUN-076) ───────────────────

    def _query_validation_report(self) -> dict[str, Any]:
        """M-3.4 验证大盘 — 读 ValidationScenarioRun 留痕, per-scenario 取最新 (最高 seq) outcome。

        outcome 5 态 (§3.3): passed / failed_invariant_violation / failed_with_correction_signal /
        aborted_external (daemon-gated/live-LLM/deferred 明账 skip) / aborted_setup_error。failed 场景
        带 correction_signal (暴露的设计假设)。read-only — 不 catchup / 不 emit。
        """
        events = self._events_by_type(EventType.VALIDATION_SCENARIO_RUN)  # sorted by seq (deterministic)
        total_runs = len(events)
        latest_by_scenario: dict[str, dict[str, Any]] = {}
        for ev in events:
            p = self._payload(ev)
            sid = str(p.get("scenario_id"))
            obs = p.get("observability_data") or {}
            cs = p.get("correction_signal") or None
            failed_invs = [
                {
                    "invariant_name": str(iv.get("invariant_name")),
                    "violation_observation_point": iv.get("violation_observation_point"),
                }
                for iv in (p.get("invariant_results") or [])
                if isinstance(iv, dict) and iv.get("passed") is False
            ]
            # events already in ascending seq order → last write for a scenario wins (current outcome)
            latest_by_scenario[sid] = {
                "scenario_id": sid,
                "scenario_version": p.get("scenario_version"),
                "outcome": str(p.get("outcome")),
                "run_id": p.get("run_id"),
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
                "invariant_count": len(p.get("invariant_results") or []),
                "failed_invariants": failed_invs,
                "correction_signal": (
                    {
                        "exposed_design_assumption": cs.get("exposed_design_assumption"),
                        "correction_direction": cs.get("correction_direction"),
                    }
                    if isinstance(cs, dict)
                    else None
                ),
                "observed_event_count": len(obs.get("expected_events_observed") or []),
                "skip_debt_note": (obs.get("expected_events_missing") or None),
            }
        latest = sorted(latest_by_scenario.values(), key=lambda r: r["scenario_id"])
        by_outcome = dict(sorted(Counter(r["outcome"] for r in latest).items()))
        passed = [r for r in latest if r["outcome"] == "passed"]
        failed = [r for r in latest if str(r["outcome"]).startswith("failed")]
        aborted = [r for r in latest if str(r["outcome"]).startswith("aborted")]
        return {
            "view_id": "validation_report",
            "watermark": self._watermark(),
            "total_runs": total_runs,
            "total_scenarios": len(latest),
            "by_outcome_latest": by_outcome,
            "passed": passed,
            "failed": failed,
            "aborted": aborted,
            "scenarios": latest,
            "is_empty": total_runs == 0,
        }

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-082 — M-3.2 critical 批 query: task_list / task_graph / critical_path /
    #  concept_graph / escalation_inbox / obligation_dashboard / commit_history。
    #  全 pure read: ProjectionStore.read() + EventLog.get_events_by_type()。
    #  空数据诚实标 (未物化 vs 物化空), 非假造/非 raise (§4.2 forbidden shortcut)。
    # ════════════════════════════════════════════════════════════════════════

    # ─── task_list (M-3.2 §4.2) ─────────────────────────────────────────────

    def _query_task_list(self, filters: dict[str, Any]) -> dict[str, Any]:
        """task_graph projection → task_id / status / task_type / model_tier / latest_event。

        spec §4.2 字段名为 state / done_criteria / assignee_skill / latest_event; M-0.2 task_graph
        reducer (T-L0-06) 实际物化 status / task_type / model_tier / created_at_event_id —— 渲真实
        字段, done_criteria/assignee_skill reducer 未存则不假造 (status≈state, task_type≈技能路由,
        created_at_event_id≈latest_event)。
        """
        materialized = self._proj("task_graph") is not None
        nodes = self._proj_list("task_graph", "nodes")
        status_filter = filters.get("status")
        if status_filter:
            nodes = [n for n in nodes if n.get("status") == status_filter]
        rows = sorted(nodes, key=lambda n: str(n.get("task_id")))
        tasks = [
            {
                "task_id": n.get("task_id"),
                "status": n.get("status"),
                "task_type": n.get("task_type"),
                "model_tier": n.get("model_tier"),
                "last_run_outcome": n.get("last_run_outcome"),
                "plan_id": n.get("plan_id"),
                "parent_task_id": n.get("parent_task_id"),
                "latest_event": n.get("created_at_event_id"),
            }
            for n in rows
        ]
        return {
            "view_id": "task_list",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(tasks),
            "by_status": dict(sorted(Counter(str(n.get("status")) for n in rows).items())),
            "by_task_type": dict(sorted(Counter(str(n.get("task_type")) for n in rows).items())),
            "tasks": tasks,
            "filtered_by_status": status_filter,
            "is_empty": len(tasks) == 0,
        }

    # ─── task_graph (M-3.2 §4.2, mermaid) ───────────────────────────────────

    def _query_task_graph(self) -> dict[str, Any]:
        """task_graph projection → mermaid 依赖图数据 (nodes + 有向 edges)。"""
        materialized = self._proj("task_graph") is not None
        nodes = self._proj_list("task_graph", "nodes")
        edges = self._proj_list("task_graph", "edges")
        node_rows = sorted(
            ({"task_id": n.get("task_id"), "status": n.get("status"),
              "task_type": n.get("task_type")} for n in nodes),
            key=lambda d: str(d["task_id"]),
        )
        edge_rows = sorted(
            ({"source": e.get("source_task_id"), "target": e.get("target_task_id"),
              "dependency_type": e.get("dependency_type")} for e in edges),
            key=lambda d: (str(d["source"]), str(d["target"]), str(d["dependency_type"])),
        )
        return {
            "view_id": "task_graph",
            "watermark": self._watermark(),
            "materialized": materialized,
            "node_count": len(node_rows),
            "edge_count": len(edge_rows),
            "by_status": dict(sorted(Counter(str(n.get("status")) for n in nodes).items())),
            "nodes": node_rows,
            "edges": edge_rows,
            "is_empty": len(node_rows) == 0,
        }

    # ─── critical_path (M-3.2 §4.2) ─────────────────────────────────────────

    def _query_critical_path(self) -> dict[str, Any]:
        """task_graph edges → 最长依赖链 (source→target DAG 的最长路径)。

        projection 无 duration/estimate 字段 (M-0.2 task_graph reducer 不物化时长) —— 故 duration
        estimate 诚实标不可用 (不假造时长), critical path = 最长依赖链 (hop 数)。defensive: 环检测
        (visited 栈) 防 reducer 出现非 DAG 时无限递归。
        """
        nodes = self._proj_list("task_graph", "nodes")
        edges = self._proj_list("task_graph", "edges")
        node_ids = [str(n.get("task_id")) for n in nodes if n.get("task_id")]
        adj: dict[str, list[str]] = defaultdict(list)
        for e in edges:
            src, tgt = e.get("source_task_id"), e.get("target_task_id")
            if isinstance(src, str) and isinstance(tgt, str):
                adj[src].append(tgt)
        for src in adj:
            adj[src].sort()  # deterministic tie-break

        memo: dict[str, list[str]] = {}

        def _longest_from(node: str, stack: frozenset[str]) -> list[str]:
            if node in stack:  # cycle guard — stop, don't recurse
                return [node]
            if node in memo:
                return memo[node]
            best: list[str] = []
            for nxt in adj.get(node, []):
                cand = _longest_from(nxt, stack | {node})
                if len(cand) > len(best):
                    best = cand
            result = [node, *best]
            memo[node] = result
            return result

        longest: list[str] = []
        for nid in sorted(node_ids):
            cand = _longest_from(nid, frozenset())
            if len(cand) > len(longest):
                longest = cand
        return {
            "view_id": "critical_path",
            "watermark": self._watermark(),
            "materialized": self._proj("task_graph") is not None,
            "node_count": len(node_ids),
            "edge_count": sum(len(v) for v in adj.values()),
            "longest_path": longest,
            "path_length": len(longest),
            "duration_estimate_available": False,
            "duration_note": "task_graph projection 无时长字段 (M-0.2 reducer 不物化 duration) — "
                             "critical path 按依赖 hop 数, 不假造时长 estimate",
            "is_empty": len(node_ids) == 0,
        }

    # ─── concept_graph (M-3.2 §4.4, mermaid) ────────────────────────────────

    def _query_concept_graph(self) -> dict[str, Any]:
        """concept_graph projection → mermaid 概念图数据 (nodes + 有向 edges + supersede 链)。"""
        materialized = self._proj("concept_graph") is not None
        data = self._proj("concept_graph") or {}
        nodes = data.get("nodes", []) if isinstance(data, dict) else []
        edges = data.get("edges", []) if isinstance(data, dict) else []
        nodes = [n for n in nodes if isinstance(n, dict)]
        edges = [e for e in edges if isinstance(e, dict)]
        node_rows = sorted(
            ({"concept_id": n.get("concept_id"), "name": n.get("name"),
              "created_by_stage": n.get("created_by_stage"),
              "created_at_event_id": n.get("created_at_event_id")} for n in nodes),
            key=lambda d: str(d["concept_id"]),
        )
        edge_rows = sorted(
            ({"edge_id": e.get("edge_id"), "source": e.get("source_concept_id"),
              "target": e.get("target_concept_id"), "edge_type": e.get("edge_type"),
              "is_active": e.get("is_active", True)} for e in edges),
            key=lambda d: (str(d["source"]), str(d["target"]), str(d["edge_id"])),
        )
        return {
            "view_id": "concept_graph",
            "watermark": self._watermark(),
            "materialized": materialized,
            "node_count": len(node_rows),
            "edge_count": len(edge_rows),
            "by_edge_type": dict(sorted(Counter(str(e.get("edge_type")) for e in edges).items())),
            "by_created_by_stage": dict(sorted(
                Counter(str(n.get("created_by_stage")) for n in nodes).items())),
            "nodes": node_rows,
            "edges": edge_rows,
            "is_empty": len(node_rows) == 0,
        }

    # ─── escalation_inbox (M-3.2 §4.10, F-09b 产品语言) ──────────────────────

    _ESCALATION_RESOLVED_STATES = frozenset({"resolved", "post_resolved_learning_captured"})

    def _query_escalation_inbox(self, filters: dict[str, Any]) -> dict[str, Any]:
        """升级队列 (F-09b 产品语言) — 读 EscalationRaised 事件拿 nature_facing_summary / options
        (这些产品语言字段只在事件 after_state, 不在 escalation_lifecycle projection) + join
        escalation_lifecycle projection 拿当前 lifecycle_state。pending (未 resolved) 排前。

        spec §4.10: escalation_inbox 只显产品语言 (无工程黑话) — 工程 detail 经 engineering_detail_ref
        链接出去。dedup by escalation_id (取最高 seq 的 EscalationRaised)。空数据诚实标。
        """
        events = self._events_by_type(EventType.ESCALATION_RAISED)
        life = {
            str(e.get("escalation_id")): e
            for e in self._proj_list("escalation_lifecycle", "escalations")
        }
        by_eid: dict[str, dict[str, Any]] = {}  # dedup by escalation_id, 高 seq 覆盖
        for ev in events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            eid = after.get("escalation_id")
            if not isinstance(eid, str) or not eid:
                continue
            lc = life.get(eid, {})
            state = lc.get("lifecycle_state") or EscalationLifecycleState.RAISED.value
            options = [
                {"option": o.get("option"), "tradeoff": o.get("tradeoff"),
                 "product_impact": o.get("product_impact")}
                for o in (after.get("options") or []) if isinstance(o, dict)
            ]
            by_eid[eid] = {
                "escalation_id": eid,
                "nature_facing_summary": after.get("nature_facing_summary"),
                "decision_needed_from_nature": after.get("decision_needed_from_nature"),
                "what_was_tried": after.get("what_was_tried"),
                "why_it_did_not_close": after.get("why_it_did_not_close"),
                "options": options,
                "engineering_detail_ref": after.get("engineering_detail_ref"),
                "lifecycle_state": str(state),
                "raised_event_id": ev.event_id,
                # canonical 落账时间 (算等待时长的基准; 不用 producer 自报的 after.raised_at 串,
                # 它可能硬编码/格式不一 — 同 orchestrator _escalation_raised_unix 用 rec.timestamp)。
                "raised_ts_canonical": ev.timestamp.isoformat(),
                "related_task_id": after.get("task_id"),
                "related_finding_id": after.get("finding_id"),
            }
        # T-R08-1: goal 升级写的是 GoalEscalationRaised (不同 event_type), 不进上面按
        # ESCALATION_RAISED 的查询 → 历史上 goal 升级永远不出现在 inbox view (类型错配 bug,
        # 那条卡 15 天的 escalation 即因此在正经 inbox 看不到)。这里把 GoalEscalationRaised
        # 也纳入, 映射到产品语言 inbox 字段 (owner_question = 要 owner 判什么; reason = 为什么
        # 是 Class A)。EscalationRaised 已覆盖的同 escalation_id 不重复。
        goal_events = self._events_by_type(EventType.GOAL_ESCALATION_RAISED)
        # goal escalation 不进 escalation_lifecycle projection (它不在 _ESCALATION_LIFECYCLE_EVENTS),
        # 所以拿不到 lifecycle 条目。它的"已答复"判据是: 存在一条 NatureJudgmentCaptured 的
        # responds_to_escalation_event_id 指向该 GoalEscalationRaised 的 event_id (同 orchestrator
        # pending_escalations 契约)。不 join 这个 → 已答复的 goal 升级永远显示 pending、永久报 alert。
        answered_raised_event_ids: set[str] = set()

        def _collect_responds(after: object) -> None:
            if isinstance(after, dict):
                rid = after.get("responds_to_escalation_event_id")
                if isinstance(rid, str) and rid:
                    answered_raised_event_ids.add(rid)

        # 真 NatureJudgmentCaptured (path-A, 如 interview answer)
        for nj in self._events_by_type(EventType.NATURE_JUDGMENT_CAPTURED):
            _collect_responds(self._payload(nj).get("after_state"))
        # stub-rewrite 的 NJ: `escalation respond` CLI 用 _write_stub_event 发 NJ → 存成
        # NodeTouched(kind=NatureJudgmentCaptured, stub_original_payload=...)。get_events_by_type
        # 按 top-level 类型索引查不到它 → 不 unwrap 就漏掉真生产路径的 owner 答复。
        for nt in self._events_by_type(EventType.NODE_TOUCHED):
            ntp = self._payload(nt)
            if ntp.get("kind") == EventType.NATURE_JUDGMENT_CAPTURED.value:
                orig = ntp.get("stub_original_payload")
                _collect_responds(orig.get("after_state") if isinstance(orig, dict) else None)
        for ev in goal_events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            eid = after.get("escalation_id")
            if not isinstance(eid, str) or not eid or eid in by_eid:
                continue
            lc = life.get(eid, {})
            if ev.event_id in answered_raised_event_ids:
                state = EscalationLifecycleState.RESOLVED.value
            else:
                state = lc.get("lifecycle_state") or EscalationLifecycleState.RAISED.value
            by_eid[eid] = {
                "escalation_id": eid,
                "nature_facing_summary": after.get("owner_question"),
                "decision_needed_from_nature": after.get("owner_question"),
                "what_was_tried": None,
                "why_it_did_not_close": after.get("reason"),
                "options": [],
                "engineering_detail_ref": None,
                "lifecycle_state": str(state),
                "raised_event_id": ev.event_id,
                # canonical 落账时间 (算等待时长的基准; goal escalation 同样用 ev.timestamp,
                # 不用 after.raised_at — 那是 producer 自报、测试里硬编码 "2026-06-14T...")。
                "raised_ts_canonical": ev.timestamp.isoformat(),
                "related_task_id": after.get("goal_session_id"),
                "related_finding_id": None,
                "source": "goal_escalation",
                "prepared_context": after.get("prepared_context"),  # R08 TI-3: 做功课产物
            }
        items = list(by_eid.values())
        state_filter = filters.get("lifecycle_state")
        if state_filter:
            items = [i for i in items if i["lifecycle_state"] == state_filter]
        items.sort(key=lambda i: (
            1 if i["lifecycle_state"] in self._ESCALATION_RESOLVED_STATES else 0,
            str(i["escalation_id"]),
        ))
        pending = [i for i in items if i["lifecycle_state"] not in self._ESCALATION_RESOLVED_STATES]
        return {
            "view_id": "escalation_inbox",
            "watermark": self._watermark(),
            "materialized": (len(events) + len(goal_events)) > 0,
            "total": len(items),
            "pending_count": len(pending),
            "by_lifecycle_state": dict(sorted(
                Counter(i["lifecycle_state"] for i in items).items())),
            "escalations": items,
            "filtered_by_lifecycle_state": state_filter,
            "is_empty": len(items) == 0,
        }

    @staticmethod
    def _waited_seconds_since(raised_ts_canonical: object, now: datetime) -> float:
        """从 canonical 落账时间戳算真实等待秒数 (>=0)。

        基准 = 事件的 ev.timestamp (canonical 落账时刻), 不用 producer 自报的 raised_at 串
        (可能硬编码/格式不一) — 与 orchestrator._escalation_raised_unix 同基准, 保证 view 渲染的
        等待时长和 daemon stuck-sweep 算的卡时长一致。解析失败/缺失 → 0.0 兜底 (诚实降级, 不崩 view)。
        """
        if not isinstance(raised_ts_canonical, str) or not raised_ts_canonical:
            return 0.0
        with contextlib.suppress(ValueError):
            raised = datetime.fromisoformat(raised_ts_canonical)
            if raised.tzinfo is None:
                raised = raised.replace(tzinfo=UTC)
            return max(0.0, (now - raised).total_seconds())
        return 0.0

    def _query_owner_inbox(self, now: datetime | None = None) -> dict[str, Any]:
        """R08 TI-2: owner 专属收件箱 view — 把 owner_signal + owner_inbox_view 接进真渲染。

        从 pending escalation 构建: 可见提示 headline (哪个 session 等他) + 已 prep、blocking
        排前、可一步点进 session 的收件箱。这是"在哪看"的真生产入口 (owner 来看时刷新, 不新增
        常驻进程)。prep 摘要在 TI-3(prep 接进 raise)落地后变完整; 现在从 escalation 已有字段拼。
        """
        from towow.l3.owner_inbox_view import InboxEntry, build_inbox
        from towow.l3.owner_signal import SignalUrgency, WaitingItem, build_signal

        inbox = self._query_escalation_inbox({})
        pending = [
            i for i in inbox["escalations"]
            if i["lifecycle_state"] not in self._ESCALATION_RESOLVED_STATES
        ]
        entries: list[InboxEntry] = []
        waiting: list[WaitingItem] = []
        # 来看时刷新: 用当前时刻对 canonical 落账算真实等待时长。now 可注入 (测试/集成时钟一致性,
        # 同 orchestrator.collect_stuck_batons 的 now 参数), 默认当前时刻。
        now = now or datetime.now(UTC)
        for it in pending:
            sid = str(it.get("related_task_id") or it["escalation_id"])
            essence = str(it.get("nature_facing_summary") or it.get("decision_needed_from_nature") or "(待 prep)")
            # R08 TI-3: 优先用 escalation 携带的做功课产物 (prepared_context) 拼完整 prep 摘要;
            # 没带 (旧事件/未做功课) 才退回 escalation 的零散字段。
            pc = it.get("prepared_context")
            if isinstance(pc, dict):
                _bg = pc.get("background")
                _opts = pc.get("options")
                _rec = pc.get("recommendation")
                _ruled = pc.get("ruled_out")
                seg = []
                if _bg:
                    seg.append(f"背景: {_bg}")
                if isinstance(_ruled, list) and _ruled:
                    seg.append("已排除: " + "、".join(str(x) for x in _ruled))
                if isinstance(_opts, list) and _opts:
                    seg.append("候选: " + "/".join(str(x) for x in _opts))
                if _rec:
                    seg.append(f"推荐: {_rec}")
                prep_summary = " | ".join(seg) if seg else "(prepared_context 空)"
            else:
                prep_bits = [b for b in (it.get("what_was_tried"), it.get("why_it_did_not_close")) if b]
                prep_summary = " | ".join(str(b) for b in prep_bits) if prep_bits else "(待 prep — 升级前未带做功课产物)"
            urgency = SignalUrgency.BLOCKING  # 需 owner 的默认 blocking; FYI 细分随 TI-3/4 落地
            eid = str(it["escalation_id"])
            waited_seconds = self._waited_seconds_since(it.get("raised_ts_canonical"), now)
            entries.append(InboxEntry.assemble(
                escalation_id=eid, session_id=sid, essence=essence,
                urgency=urgency, waited_seconds=waited_seconds, prep_summary=prep_summary))
            waiting.append(WaitingItem(session_id=sid, escalation_id=eid, essence=essence,
                                       urgency=urgency, waited_seconds=waited_seconds))
        signal = build_signal(waiting)
        owner_inbox = build_inbox(entries)
        return {
            "view_id": "owner_inbox",
            "watermark": self._watermark(),
            "materialized": bool(inbox["materialized"]),
            "signal_lit": signal.lit,
            "signal_headline": signal.headline,
            "blocking_count": owner_inbox.blocking_count,
            "fyi_count": owner_inbox.fyi_count,
            "entries": [e.model_dump(mode="json") for e in owner_inbox.entries],
            "is_empty": owner_inbox.is_empty,
        }

    # ─── obligation_dashboard (M-3.2 §4.5) ──────────────────────────────────

    def _query_obligation_dashboard(self, filters: dict[str, Any]) -> dict[str, Any]:
        """obligation_lifecycle_state projection → id / canonical_state / severity / scope_type 总览。"""
        materialized = self._proj("obligation_lifecycle_state") is not None
        obls = self._proj_list("obligation_lifecycle_state", "obligations")
        cstate_filter = filters.get("canonical_state")
        sev_filter = filters.get("severity")
        if cstate_filter:
            obls = [o for o in obls if o.get("canonical_state") == cstate_filter]
        if sev_filter:
            obls = [o for o in obls if o.get("severity") == sev_filter]
        rows = sorted(
            obls,
            key=lambda o: (_SEVERITY_ORDER.get(str(o.get("severity")), 99), str(o.get("obligation_id"))),
        )
        items = [
            {
                "obligation_id": o.get("obligation_id"),
                "canonical_state": o.get("canonical_state"),
                "lifecycle_state": o.get("lifecycle_state"),
                "severity": o.get("severity"),
                "scope_type": o.get("scope_type"),
                "nature": o.get("nature"),
            }
            for o in rows
        ]
        return {
            "view_id": "obligation_dashboard",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(items),
            "by_canonical_state": dict(sorted(
                Counter(str(o.get("canonical_state")) for o in rows).items())),
            "by_severity": dict(sorted(Counter(str(o.get("severity")) for o in rows).items())),
            "by_scope_type": dict(sorted(Counter(str(o.get("scope_type")) for o in rows).items())),
            "obligations": items,
            "filtered_by_canonical_state": cstate_filter,
            "filtered_by_severity": sev_filter,
            "is_empty": len(items) == 0,
        }

    # ─── commit_history (M-3.2 §4.7) ────────────────────────────────────────

    _COMMIT_RECENT_LIMIT = 20

    def _query_commit_history(self, filters: dict[str, Any]) -> dict[str, Any]:
        """commit_history projection → verdict 分布 + 最近 commit (尾部 20, projection 顺序)。"""
        materialized = self._proj("commit_history") is not None
        commits = self._proj_list("commit_history", "commits")
        verdict_filter = filters.get("verdict")
        if verdict_filter:
            commits = [c for c in commits if c.get("verdict") == verdict_filter]
        recent = [
            {"commit_event_id": c.get("commit_event_id"), "verdict": c.get("verdict"),
             "envelope_event_id": c.get("envelope_event_id")}
            for c in commits[-self._COMMIT_RECENT_LIMIT:]
        ]
        return {
            "view_id": "commit_history",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(commits),
            "by_verdict": dict(sorted(Counter(str(c.get("verdict")) for c in commits).items())),
            "recent": recent,
            "recent_limit": self._COMMIT_RECENT_LIMIT,
            "filtered_by_verdict": verdict_filter,
            "is_empty": len(commits) == 0,
        }

    # ─── gate_check_firing (T-FU-09 可观测) ──────────────────────────────────

    # live-target 门真拒的两个 failure_reason (承重开火信号)。
    _LIVE_TARGET_CHECK = "live_target_reconciled"
    _LIVE_TARGET_REJECT_REASONS = ("goal_live_target_unsatisfied", "goal_completion_no_gsid")
    # 潜伏原因链到的两条真实债 (against live-target-execution-evidence@v1, 均 open)。
    _LIVE_TARGET_LATENCY_DEBTS = (
        ("debt-b6187ed0d19c", "live-target task 层三门未落地(分类+发布门+完成门) — 发布门未强制 "
         "goal 声明 live_target_observable, 故门收口时永远读到空 observable 集 → not_applicable 放行"),
        ("debt-82d4effd33a0", "SYSTEM-provenance 事件在 this_goal scope 下不可满足 + 硬禁 global "
         "堵死唯一出路 — 即便声明了 observable, SYSTEM 来源证据也无法过 scope 锚定"),
    )

    def _query_gate_check_firing(self, filters: dict[str, Any]) -> dict[str, Any]:
        """gate_check_firing projection → live-target 门潜伏态裁决 + 全 check_type result 分布。"""
        materialized = self._proj("gate_check_firing") is not None
        data = self._proj("gate_check_firing") or {}
        by_check_type = data.get("by_check_type")
        if not isinstance(by_check_type, dict):
            by_check_type = {}
        rejections = data.get("rejections_by_reason")
        if not isinstance(rejections, dict):
            rejections = {}

        lt = by_check_type.get(self._LIVE_TARGET_CHECK)
        lt = lt if isinstance(lt, dict) else {}
        vacuous = int(lt.get("not_applicable", 0))  # 门跑了但无 observable 可评 = 潜伏
        real_pass = int(lt.get("passed", 0))         # 真评估 observable 且全满足
        real_reject = sum(int(rejections.get(r, 0)) for r in self._LIVE_TARGET_REJECT_REASONS)
        real_firing = real_pass + real_reject
        total_firing = vacuous + real_firing
        # 潜伏 = 门已接线在跑, 但从没真评估过任何 observable (real_firing==0)。
        latent = real_firing == 0

        return {
            "view_id": "gate_check_firing",
            "watermark": self._watermark(),
            "materialized": materialized,
            "is_empty": len(by_check_type) == 0 and len(rejections) == 0,
            "live_target": {
                "total_firing": total_firing,
                "vacuous_not_applicable": vacuous,
                "real_pass": real_pass,
                "real_reject": real_reject,
                "real_firing": real_firing,
                "latent": latent,
                "reject_breakdown": {
                    r: int(rejections.get(r, 0)) for r in self._LIVE_TARGET_REJECT_REASONS
                },
            },
            "latency_debts": [
                {"debt_id": d, "why": w} for d, w in self._LIVE_TARGET_LATENCY_DEBTS
            ],
            "by_check_type": {
                k: dict(sorted(v.items()))
                for k, v in sorted(by_check_type.items())
                if isinstance(v, dict)
            },
            "rejections_by_reason": dict(sorted(rejections.items())),
        }

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-085 — M-3.2 view 扩展剩余 (§4.3-§4.11 + M-0.2 UI + Phase D)
    #  18 视图: 复用 RUN-082 框架 (read-only / idempotent / deterministic),
    #  读真 projection/event → 渲真 markdown, 空数据诚实标 is_empty, 不假造。
    # ════════════════════════════════════════════════════════════════════════

    # ─── finding_lifecycle (M-3.2 §4.3) — finding 全状态机时间线 ───────────────

    def _query_finding_lifecycle(self, filters: dict[str, Any]) -> dict[str, Any]:
        """finding_lifecycle projection → 每个 finding 一条状态机时间线 (state + 关键 event_id)。"""
        materialized = self._proj("finding_lifecycle") is not None
        findings = self._proj_list("finding_lifecycle", "findings")
        sev_filter = filters.get("severity")
        state_filter = filters.get("lifecycle_state")
        if sev_filter:
            findings = [f for f in findings if f.get("severity") == sev_filter]
        if state_filter:
            findings = [f for f in findings if f.get("lifecycle_state") == state_filter]
        rows = sorted(
            findings,
            key=lambda f: (_SEVERITY_ORDER.get(str(f.get("severity")), 99), str(f.get("finding_id"))),
        )
        items = [
            {
                "finding_id": f.get("finding_id"),
                "lifecycle_state": f.get("lifecycle_state"),
                "severity": f.get("severity"),
                "verification_state": f.get("verification_state"),
                "resolution": f.get("resolution"),
                "closure_state": f.get("closure_state"),
                "review_dimension": f.get("review_dimension"),
                "detection_method": f.get("detection_method"),
                "is_preexisting": f.get("is_preexisting"),
                "created_at_event_id": f.get("created_at_event_id"),
                "last_state_change_event_id": f.get("last_state_change_event_id"),
            }
            for f in rows
        ]
        return {
            "view_id": "finding_lifecycle",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(items),
            "by_lifecycle_state": dict(sorted(
                Counter(str(f.get("lifecycle_state")) for f in rows).items())),
            "by_severity": dict(sorted(Counter(str(f.get("severity")) for f in rows).items())),
            "findings": items,
            "filtered_by_severity": sev_filter,
            "filtered_by_lifecycle_state": state_filter,
            "is_empty": len(items) == 0,
        }

    # ─── fix_history (M-3.2 §4.3) — 多轮 fix dashboard (FixCompleted by fix_id) ─

    _FIX_ESCALATION_ROUND_THRESHOLD = 4  # ≥4 轮 → EscalationCandidate alert (spec §4.3)

    def _query_fix_history(self, filters: dict[str, Any]) -> dict[str, Any]:
        """FixCompleted 事件 by fix_id → fix_attempt_no 趋势 + novelty + 多轮 alert (§4.3)。

        spec 数据源含 fix_lifecycle projection — 本仓库无独立 fix_lifecycle projection (FixCompleted
        是 path-A 域事件, 未单独物化 fix projection), 故直接读 FixCompleted 事件聚合 (诚实标 source)。
        某 fix_id 进入 ≥4 轮 (max fix_attempt_no) → EscalationCandidate 高亮 (spec alert)。
        """
        events = self._events_by_type(EventType.FIX_COMPLETED)
        fix_filter = filters.get("fix_id")
        by_fix: dict[str, dict[str, Any]] = {}
        for ev in events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            fix_id = after.get("fix_id")
            if not isinstance(fix_id, str) or not fix_id:
                continue
            if fix_filter and fix_id != fix_filter:
                continue
            attempt = after.get("fix_attempt_no")
            attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else 1
            novelty = after.get("fix_attempt_novelty")
            novelty_str = (
                novelty.get("novelty_classification") if isinstance(novelty, dict) else novelty
            )
            entry = by_fix.setdefault(fix_id, {"fix_id": fix_id, "attempts": []})
            entry["attempts"].append({
                "fix_attempt_no": attempt,
                "outcome": after.get("outcome"),
                "novelty": novelty_str,
                "event_id": ev.event_id,
            })
        fixes: list[dict[str, Any]] = []
        for fix_id in sorted(by_fix):
            entry = by_fix[fix_id]
            attempts = sorted(entry["attempts"], key=lambda a: a["fix_attempt_no"])
            max_round = max((a["fix_attempt_no"] for a in attempts), default=0)
            fixes.append({
                "fix_id": fix_id,
                "round_count": len(attempts),
                "max_attempt_no": max_round,
                "latest_outcome": attempts[-1]["outcome"] if attempts else None,
                "attempts": attempts,
                "escalation_candidate": max_round >= self._FIX_ESCALATION_ROUND_THRESHOLD,
            })
        candidates = [f["fix_id"] for f in fixes if f["escalation_candidate"]]
        return {
            "view_id": "fix_history",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "total_fixes": len(fixes),
            "total_fix_events": len(events),
            "fixes": fixes,
            "escalation_candidates": candidates,
            "escalation_round_threshold": self._FIX_ESCALATION_ROUND_THRESHOLD,
            "filtered_by_fix_id": fix_filter,
            "is_empty": len(fixes) == 0,
        }

    # ─── at_reference_inspector (M-3.2 §4.4) — 字段级 @ 引用图 + stale 高亮 ─────

    @staticmethod
    def _ref_row(r: dict[str, Any]) -> dict[str, Any]:
        """归一两种 at_reference 形态 (T-L0-08 source/target vs T-L1-14 typed locator) → 统一展示行。"""
        # typed locator shape (T-L1-14): source_concept_id / target_concept_name
        if "target_concept_name" in r or "source_concept_id" in r:
            return {
                "reference_id": r.get("reference_id"),
                "source": r.get("source_concept_id"),
                "target": r.get("target_concept_name"),
                "locator_type": r.get("locator_type"),
                "field_path": r.get("field_path"),
                "is_active": r.get("is_active", True),
                "is_stale": bool(r.get("is_stale", False)),
                "staleness_status": r.get("staleness_status"),
                "created_at_event_id": r.get("created_at_event_id"),
            }
        # source/target endpoint shape (T-L0-08)
        src = r.get("source") if isinstance(r.get("source"), dict) else {}
        tgt = r.get("target") if isinstance(r.get("target"), dict) else {}
        return {
            "reference_id": r.get("reference_id"),
            "source": src.get("entity_id") if isinstance(src, dict) else None,
            "target": tgt.get("entity_id") if isinstance(tgt, dict) else None,
            "locator_type": r.get("reference_type"),
            "field_path": src.get("field_path") if isinstance(src, dict) else None,
            "is_active": r.get("is_active", True),
            "is_stale": bool(r.get("is_stale", False)),
            "staleness_status": r.get("staleness_status"),
            "created_at_event_id": r.get("created_at_event_id"),
        }

    def _query_at_reference_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """at_reference_graph projection → 字段级 @ 引用列表 + stale 高亮 (§4.4 / F-04e)。"""
        materialized = self._proj("at_reference_graph") is not None
        refs = self._proj_list("at_reference_graph", "references")
        active_only = filters.get("active_only")
        rows = [self._ref_row(r) for r in refs]
        if active_only:
            rows = [r for r in rows if r["is_active"]]
        rows.sort(key=lambda r: str(r["reference_id"]))
        stale = [r for r in rows if r["is_stale"]]
        return {
            "view_id": "at_reference_inspector",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(rows),
            "active_count": sum(1 for r in rows if r["is_active"]),
            "stale_count": len(stale),
            "references": rows,
            "filtered_active_only": bool(active_only),
            "is_empty": len(rows) == 0,
        }

    # ─── stale_references (M-3.2 §4.4) — 悬空引用警告 (critical) ───────────────

    def _query_stale_references(self) -> dict[str, Any]:
        """at_reference_graph 扫 is_stale=True 的 @ ref (pin 指向已 supersede/retired concept)。critical。"""
        materialized = self._proj("at_reference_graph") is not None
        refs = self._proj_list("at_reference_graph", "references")
        rows = [self._ref_row(r) for r in refs if bool(r.get("is_stale", False))]
        rows.sort(key=lambda r: str(r["reference_id"]))
        return {
            "view_id": "stale_references",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total_references": len(refs),
            "stale_count": len(rows),
            "stale_references": rows,
            "alert_level": "critical" if rows else "ok",
            "is_empty": len(rows) == 0,
        }

    # ─── consumer_list (M-3.2 §4.4) — concept 消费方列表 (at_ref 反向 + concept_graph) ─

    def _query_consumer_list(self, filters: dict[str, Any]) -> dict[str, Any]:
        """at_reference_graph 反向 + concept_graph → per concept_id 列消费方 (supersede 影响面评估)。"""
        at_mat = self._proj("at_reference_graph") is not None
        cg_mat = self._proj("concept_graph") is not None
        refs = [self._ref_row(r) for r in self._proj_list("at_reference_graph", "references")]
        cg = self._proj("concept_graph") or {}
        cg_nodes = cg.get("nodes", []) if isinstance(cg, dict) else []
        name_by_id = {
            str(n.get("concept_id")): n.get("name")
            for n in cg_nodes if isinstance(n, dict) and n.get("concept_id")
        }
        concept_filter = filters.get("concept_id")
        # 反向索引: target concept → 消费它的 source 列表 (只看 active 引用)
        consumers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in refs:
            if not r["is_active"]:
                continue
            target = r["target"]
            if not isinstance(target, str) or not target:
                continue
            consumers[target].append({
                "consumer": r["source"],
                "reference_id": r["reference_id"],
                "locator_type": r["locator_type"],
                "field_path": r["field_path"],
            })
        concept_ids = sorted(set(consumers) | set(name_by_id))
        if concept_filter:
            concept_ids = [c for c in concept_ids if c == concept_filter]
        rows = [
            {
                "concept_id": cid,
                "concept_name": name_by_id.get(cid),
                "in_concept_graph": cid in name_by_id,
                "consumer_count": len(consumers.get(cid, [])),
                "consumers": sorted(consumers.get(cid, []), key=lambda d: str(d["reference_id"])),
            }
            for cid in concept_ids
        ]
        rows = [r for r in rows if r["consumer_count"] > 0 or r["in_concept_graph"]]
        return {
            "view_id": "consumer_list",
            "watermark": self._watermark(),
            "materialized": at_mat or cg_mat,
            "at_reference_materialized": at_mat,
            "concept_graph_materialized": cg_mat,
            "total_concepts": len(rows),
            "concepts": rows,
            "filtered_by_concept_id": concept_filter,
            "is_empty": len(rows) == 0,
        }

    # ─── resolver_decision_inspector (M-3.2 §4.5) — ObligationScopeJudged 矩阵 ─

    def _query_resolver_decision_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """ObligationScopeJudged 事件 → per (obligation_id, task_type) hit/miss/uncertain 分布 (§4.5)。

        judgment ∈ {in_scope(hit) / out_of_scope(miss) / uncertain}; task_type 取 after_state.task_scope.
        spec 数据源是 "projection of ObligationScopeJudged events" — 本仓库无单独 scope-judgment projection,
        故直接聚合事件 (诚实标 source), 渲 matrix per (obligation_id, task_type) → hit/miss/uncertain rate。
        """
        events = self._events_by_type(EventType.OBLIGATION_SCOPE_JUDGED)
        obl_filter = filters.get("obligation_id")
        cells: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        for ev in events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            obl_id = after.get("obligation_id")
            scope = after.get("task_scope") if isinstance(after.get("task_scope"), dict) else {}
            task_type = scope.get("task_type") if isinstance(scope, dict) else None
            judgment = after.get("judgment")
            if not isinstance(obl_id, str) or not obl_id:
                continue
            if obl_filter and obl_id != obl_filter:
                continue
            cells[(obl_id, str(task_type))][str(judgment)] += 1
        matrix: list[dict[str, Any]] = []
        for (obl_id, task_type) in sorted(cells):
            c = cells[(obl_id, task_type)]
            total = sum(c.values())
            hit = c.get("in_scope", 0)
            miss = c.get("out_of_scope", 0)
            uncertain = c.get("uncertain", 0)
            matrix.append({
                "obligation_id": obl_id,
                "task_type": task_type,
                "total": total,
                "hit": hit,
                "miss": miss,
                "uncertain": uncertain,
                "hit_rate": round(hit / total, 4) if total else None,
                "miss_rate": round(miss / total, 4) if total else None,
                "uncertain_rate": round(uncertain / total, 4) if total else None,
            })
        return {
            "view_id": "resolver_decision_inspector",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "total_judgments": len(events),
            "cell_count": len(matrix),
            "matrix": matrix,
            "filtered_by_obligation_id": obl_filter,
            "is_empty": len(matrix) == 0,
        }

    # ─── info_need_graph (M-3.2 §4.6) — 信息需求图 per session ─────────────────

    def _query_info_need_graph(self, filters: dict[str, Any]) -> dict[str, Any]:
        """information_need_graph projection → per-session 信息需求节点 + red-line 高亮 (§4.6)。

        filters.session_id 选单 session; 缺省返回全部 session。节点状态 pending/resolved/superseded,
        red_line 节点高亮 (采访进行时实时看 gap)。
        """
        materialized = self._proj("information_need_graph") is not None
        data = self._proj("information_need_graph") or {}
        sessions_raw = data.get("sessions") if isinstance(data, dict) else {}
        sessions_raw = sessions_raw if isinstance(sessions_raw, dict) else {}
        session_filter = filters.get("session_id")
        sessions: list[dict[str, Any]] = []
        for sid in sorted(sessions_raw):
            if session_filter and sid != session_filter:
                continue
            sess = sessions_raw[sid]
            nodes_raw = sess.get("nodes", []) if isinstance(sess, dict) else []
            nodes = [n for n in nodes_raw if isinstance(n, dict)]
            node_rows = sorted(
                ({
                    "need_id": n.get("need_id"),
                    "description": n.get("description"),
                    "source": n.get("source"),
                    "status": n.get("status"),
                    "red_line": bool(n.get("red_line", False)),
                    "red_line_reason": n.get("red_line_reason"),
                    "nature_original_quote": n.get("nature_original_quote"),
                    "meaning_interpretation": n.get("meaning_interpretation"),
                } for n in nodes),
                key=lambda d: str(d["need_id"]),
            )
            sessions.append({
                "session_id": sid,
                "node_count": len(node_rows),
                "by_status": dict(sorted(Counter(str(n.get("status")) for n in nodes).items())),
                "red_line_count": sum(1 for n in nodes if n.get("red_line")),
                "nodes": node_rows,
            })
        return {
            "view_id": "info_need_graph",
            "watermark": self._watermark(),
            "materialized": materialized,
            "session_count": len(sessions),
            "sessions": sessions,
            "filtered_by_session_id": session_filter,
            "is_empty": len(sessions) == 0,
        }

    # ─── interview_progress (M-3.2 §4.6) — 采访进度聚合 ───────────────────────

    def _query_interview_progress(self, filters: dict[str, Any]) -> dict[str, Any]:
        """information_need_graph 聚合 → total / resolved / red_line_pending / ai_reachable_pending /
        nature_unique_pending (§4.6)。从 information_need_graph 重算 (声明数据源), 不读 interview_progress
        projection (那是 reducer 的 SoT, view 按声明源算更诚实)。"""
        materialized = self._proj("information_need_graph") is not None
        data = self._proj("information_need_graph") or {}
        sessions_raw = data.get("sessions") if isinstance(data, dict) else {}
        sessions_raw = sessions_raw if isinstance(sessions_raw, dict) else {}
        session_filter = filters.get("session_id")
        rows: list[dict[str, Any]] = []
        for sid in sorted(sessions_raw):
            if session_filter and sid != session_filter:
                continue
            sess = sessions_raw[sid]
            nodes_raw = sess.get("nodes", []) if isinstance(sess, dict) else []
            nodes = [n for n in nodes_raw if isinstance(n, dict)]
            total = len(nodes)
            resolved = sum(1 for n in nodes if n.get("status") == "resolved")
            pending = [n for n in nodes if n.get("status") == "pending"]
            red_line_pending = sum(1 for n in pending if n.get("red_line"))
            ai_reachable_pending = sum(1 for n in pending if n.get("source") == "ai_reachable")
            nature_unique_pending = sum(1 for n in pending if n.get("source") == "nature_unique")
            rows.append({
                "session_id": sid,
                "total": total,
                "resolved": resolved,
                "pending": len(pending),
                "red_line_pending": red_line_pending,
                "ai_reachable_pending": ai_reachable_pending,
                "nature_unique_pending": nature_unique_pending,
                "by_status": dict(sorted(Counter(str(n.get("status")) for n in nodes).items())),
            })
        return {
            "view_id": "interview_progress",
            "watermark": self._watermark(),
            "materialized": materialized,
            "session_count": len(rows),
            "sessions": rows,
            "filtered_by_session_id": session_filter,
            "is_empty": len(rows) == 0,
        }

    # ─── brief_inspector (M-3.2 §4.6) — brief 内容 + supersede 链 ──────────────

    def _query_brief_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """InterviewBriefPublished 事件 → 当前 brief + supersede 链 (§4.6)。

        payload flat (kind=InterviewBriefPublished): brief_id/session_id/owner_facing_summary/
        goal_completion_condition/concept_seeds_created/supersedes。supersede 链: supersedes 指前一
        brief event_id → 标 superseded; 最新 (未被任何 brief supersede 的) 为 current。
        """
        events = self._events_by_type(EventType.INTERVIEW_BRIEF_PUBLISHED)
        session_filter = filters.get("session_id")
        superseded_event_ids: set[str] = set()
        briefs: list[dict[str, Any]] = []
        for ev in events:
            p = self._payload(ev)
            sid = p.get("session_id")
            if session_filter and sid != session_filter:
                continue
            supersedes = p.get("supersedes")
            if isinstance(supersedes, str) and supersedes:
                superseded_event_ids.add(supersedes)
            briefs.append({
                "brief_id": p.get("brief_id"),
                "session_id": sid,
                "event_id": ev.event_id,
                "owner_facing_summary": p.get("owner_facing_summary"),
                "goal_completion_condition": p.get("goal_completion_condition"),
                "owner_facing_style_ref": p.get("owner_facing_style_ref"),
                "concept_seeds_created": p.get("concept_seeds_created", []),
                "supersedes": supersedes,
            })
        for b in briefs:
            b["is_current"] = b["event_id"] not in superseded_event_ids
        briefs.sort(key=lambda b: str(b["event_id"]))
        current = [b for b in briefs if b["is_current"]]
        return {
            "view_id": "brief_inspector",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "total_briefs": len(briefs),
            "current_count": len(current),
            "superseded_count": len(briefs) - len(current),
            "briefs": briefs,
            "filtered_by_session_id": session_filter,
            "is_empty": len(briefs) == 0,
        }

    # ─── nature_judgment_timeline (M-3.2 §4.6 ★) — NJ 时间线 + 类聚升级 candidate ─

    _NJ_UPGRADE_CLUSTER_THRESHOLD = 3  # 同类 NJ ≥3 次 → obligation candidate (spec §4.6 alert)

    def _query_nature_judgment_timeline(self) -> dict[str, Any]:
        """NatureJudgmentCaptured 事件 → 时间线 (seq 序) + 按 scope 类聚; 同类 ≥3 → obligation candidate。"""
        events = self._events_by_type(EventType.NATURE_JUDGMENT_CAPTURED)
        timeline: list[dict[str, Any]] = []
        clusters: Counter[str] = Counter()
        for ev in events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            scope = after.get("scope")
            timeline.append({
                "judgment_id": after.get("judgment_id"),
                "quote": after.get("quote"),
                "meaning": after.get("meaning"),
                "scope": scope,
                "is_value_judgment": after.get("is_value_judgment"),
                "event_id": ev.event_id,
                "seq": ev.sequence_number,
            })
            if isinstance(scope, str) and scope:
                clusters[scope] += 1
        upgrade_candidates = sorted(
            ({"scope": s, "count": n}
             for s, n in clusters.items() if n >= self._NJ_UPGRADE_CLUSTER_THRESHOLD),
            key=lambda d: (-d["count"], d["scope"]),
        )
        return {
            "view_id": "nature_judgment_timeline",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "total": len(timeline),
            "timeline": timeline,
            "by_scope": dict(sorted(clusters.items())),
            "upgrade_candidates": upgrade_candidates,
            "upgrade_cluster_threshold": self._NJ_UPGRADE_CLUSTER_THRESHOLD,
            "is_empty": len(timeline) == 0,
        }

    # ─── mismatch_inbox (M-3.2 §4.8) — MismatchDetected + ResolutionDecided ───

    def _query_mismatch_inbox(self, filters: dict[str, Any]) -> dict[str, Any]:
        """MismatchDetected + MismatchResolutionDecided 事件 join by mismatch_id (§4.8)。filter task_id。"""
        detected = self._events_by_type(EventType.MISMATCH_DETECTED)
        resolved = self._events_by_type(EventType.MISMATCH_RESOLUTION_DECIDED)
        task_filter = filters.get("task_id")
        resolutions: dict[str, dict[str, Any]] = {}
        for ev in resolved:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            mid = after.get("mismatch_id")
            if isinstance(mid, str) and mid:
                resolutions[mid] = {
                    "resolution_action": after.get("resolution_action"),
                    "outcome": after.get("outcome"),
                    "executor_rationale": after.get("executor_rationale"),
                    "advisor_consult_id": after.get("advisor_consult_id"),
                    "finding_id": after.get("finding_id"),
                    "resolved_event_id": ev.event_id,
                }
        items: list[dict[str, Any]] = []
        for ev in detected:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            mid = after.get("mismatch_id")
            if not isinstance(mid, str) or not mid:
                continue
            task_id = after.get("task_id")
            if task_filter and task_id != task_filter:
                continue
            res = resolutions.get(mid)
            items.append({
                "mismatch_id": mid,
                "task_id": task_id,
                "mismatch_summary": after.get("mismatch_summary"),
                "affected_aspect": after.get("affected_aspect"),
                "severity_judgment": after.get("severity_judgment"),
                "detection_time": after.get("detection_time"),
                "detected_event_id": ev.event_id,
                "resolution": res,
                "is_resolved": res is not None,
            })
        items.sort(key=lambda i: str(i["mismatch_id"]))
        unresolved = [i for i in items if not i["is_resolved"]]
        return {
            "view_id": "mismatch_inbox",
            "watermark": self._watermark(),
            "materialized": len(detected) > 0,
            "total": len(items),
            "unresolved_count": len(unresolved),
            "by_affected_aspect": dict(sorted(
                Counter(str(i["affected_aspect"]) for i in items).items())),
            "mismatches": items,
            "filtered_by_task_id": task_filter,
            "is_empty": len(items) == 0,
        }

    # ─── advisor_consult_log (M-3.2 §4.8) — Consult + Verdict join ────────────

    def _query_advisor_consult_log(self, filters: dict[str, Any]) -> dict[str, Any]:
        """AdvisorConsultRequested + AdvisorVerdictDelivered 事件 join by consult_id (§4.8)。"""
        requested = self._events_by_type(EventType.ADVISOR_CONSULT_REQUESTED)
        delivered = self._events_by_type(EventType.ADVISOR_VERDICT_DELIVERED)
        task_filter = filters.get("task_id")
        verdicts: dict[str, dict[str, Any]] = {}
        for ev in delivered:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            cid = after.get("consult_id")
            if isinstance(cid, str) and cid:
                verdicts[cid] = {
                    "decision": after.get("decision"),
                    "rationale": after.get("rationale"),
                    "confidence": after.get("confidence"),
                    "specific_steps": after.get("specific_steps", []),
                    "delivered_event_id": ev.event_id,
                }
        items: list[dict[str, Any]] = []
        for ev in requested:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            cid = after.get("consult_id")
            if not isinstance(cid, str) or not cid:
                continue
            task_id = after.get("task_id")
            if task_filter and task_id != task_filter:
                continue
            v = verdicts.get(cid)
            items.append({
                "consult_id": cid,
                "task_id": task_id,
                "triggered_by": after.get("triggered_by"),
                "question": after.get("question"),
                "requested_event_id": ev.event_id,
                "verdict": v,
                "is_answered": v is not None,
            })
        items.sort(key=lambda i: str(i["consult_id"]))
        pending = [i for i in items if not i["is_answered"]]
        return {
            "view_id": "advisor_consult_log",
            "watermark": self._watermark(),
            "materialized": len(requested) > 0,
            "total": len(items),
            "pending_count": len(pending),
            "by_triggered_by": dict(sorted(
                Counter(str(i["triggered_by"]) for i in items).items())),
            "consults": items,
            "filtered_by_task_id": task_filter,
            "is_empty": len(items) == 0,
        }

    # ─── planning_uncertainty_inbox (M-3.2 §4.9) — PlanningUncertainty 事件 ────

    def _query_planning_uncertainty_inbox(self, filters: dict[str, Any]) -> dict[str, Any]:
        """PlanningUncertainty 事件 (flat payload) by uncertainty_id latest-wins → inbox (§4.9)。

        同 uncertainty_id 多次 emit (resolved=true latest-wins, 同 freeze 门约定); 未解决的 red_line
        排前 (阻塞 plan freeze)。
        """
        events = self._events_by_type(EventType.PLANNING_UNCERTAINTY)
        plan_filter = filters.get("plan_id")
        by_uid: dict[str, dict[str, Any]] = {}  # latest-wins by uncertainty_id (seq 序覆盖)
        for ev in events:
            p = self._payload(ev)
            uid = p.get("uncertainty_id")
            if not isinstance(uid, str) or not uid:
                continue
            plan_id = p.get("plan_id")
            if plan_filter and plan_id != plan_filter:
                continue
            by_uid[uid] = {
                "uncertainty_id": uid,
                "plan_id": plan_id,
                "description": p.get("description"),
                "impact": p.get("impact"),
                "severity": p.get("severity"),
                "resolution_action": p.get("resolution_action"),
                "resolved": bool(p.get("resolved", False)),
                "blocking_tasks": p.get("blocking_tasks", []),
                "latest_event_id": ev.event_id,
            }
        items = list(by_uid.values())
        # 未解决的 red_line 排最前 (阻塞), 再未解决 advisory, 再已解决
        items.sort(key=lambda i: (
            i["resolved"],
            0 if (not i["resolved"] and i["severity"] == "red_line") else 1,
            str(i["uncertainty_id"]),
        ))
        unresolved = [i for i in items if not i["resolved"]]
        blocking = [i for i in unresolved if i["severity"] == "red_line"]
        return {
            "view_id": "planning_uncertainty_inbox",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "total": len(items),
            "unresolved_count": len(unresolved),
            "blocking_count": len(blocking),
            "by_severity": dict(sorted(Counter(str(i["severity"]) for i in items).items())),
            "uncertainties": items,
            "filtered_by_plan_id": plan_filter,
            "is_empty": len(items) == 0,
        }

    # ─── cross_plan_dashboard (M-3.2 §4.9) — task_graph 按 plan_id 聚合 ────────

    def _query_cross_plan_dashboard(self) -> dict[str, Any]:
        """task_graph nodes 按 plan_id 聚合 → 跨 plan 总览 (每 plan task 数 + status 分布) (§4.9)。"""
        materialized = self._proj("task_graph") is not None
        nodes = self._proj_list("task_graph", "nodes")
        by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for n in nodes:
            plan_id = n.get("plan_id")
            by_plan[str(plan_id)].append(n)
        plans: list[dict[str, Any]] = []
        for plan_id in sorted(by_plan):
            pnodes = by_plan[plan_id]
            by_status = dict(sorted(Counter(str(n.get("status")) for n in pnodes).items()))
            done = sum(1 for n in pnodes if n.get("status") == "done")
            plans.append({
                "plan_id": plan_id,
                "task_count": len(pnodes),
                "done_count": done,
                "by_status": by_status,
            })
        return {
            "view_id": "cross_plan_dashboard",
            "watermark": self._watermark(),
            "materialized": materialized,
            "plan_count": len(plans),
            "total_tasks": len(nodes),
            "plans": plans,
            "is_empty": len(plans) == 0,
        }

    # ─── finding_fix_task_joined (M-3.2 §4.11) — finding × fix × task 联合 ─────

    def _query_finding_fix_task_joined(self, filters: dict[str, Any]) -> dict[str, Any]:
        """finding_lifecycle + FixCompleted 事件 + task_graph 三源联合 (§4.11)。

        本仓库 finding projection 无硬 FK 到 task/fix (finding 携 related_patch_event_id); 故 join 为
        best-effort: 每个 finding 带 lifecycle/closure, 旁列全部 FixCompleted (fix_id/outcome/attempt) 与
        task 节点摘要, 诚实标 "projection 无 finding→task→fix 硬外键, 联合按可得引用"。非空壳: 真读三源。
        """
        fl_mat = self._proj("finding_lifecycle") is not None
        tg_mat = self._proj("task_graph") is not None
        findings = self._proj_list("finding_lifecycle", "findings")
        sev_filter = filters.get("severity")
        if sev_filter:
            findings = [f for f in findings if f.get("severity") == sev_filter]
        finding_rows = sorted(
            ({
                "finding_id": f.get("finding_id"),
                "severity": f.get("severity"),
                "lifecycle_state": f.get("lifecycle_state"),
                "closure_state": f.get("closure_state"),
                "related_patch_event_id": f.get("related_patch_event_id"),
            } for f in findings),
            key=lambda d: (_SEVERITY_ORDER.get(str(d["severity"]), 99), str(d["finding_id"])),
        )
        fix_events = self._events_by_type(EventType.FIX_COMPLETED)
        fixes: list[dict[str, Any]] = []
        for ev in fix_events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            fixes.append({
                "fix_id": after.get("fix_id"),
                "outcome": after.get("outcome"),
                "fix_attempt_no": after.get("fix_attempt_no"),
                "event_id": ev.event_id,
            })
        tasks = sorted(
            ({"task_id": n.get("task_id"), "status": n.get("status"),
              "plan_id": n.get("plan_id")} for n in self._proj_list("task_graph", "nodes")),
            key=lambda d: str(d["task_id"]),
        )
        return {
            "view_id": "finding_fix_task_joined",
            "watermark": self._watermark(),
            "materialized": fl_mat or tg_mat or len(fix_events) > 0,
            "finding_count": len(finding_rows),
            "fix_count": len(fixes),
            "task_count": len(tasks),
            "findings": finding_rows,
            "fixes": fixes,
            "tasks": tasks,
            "join_note": "projection 无 finding→task→fix 硬外键 — 联合按可得引用 (related_patch_event_id), "
                         "三源真读非空壳; 精确 FK join 待 fix_lifecycle projection 物化。",
            "filtered_by_severity": sev_filter,
            "is_empty": len(finding_rows) == 0 and len(fixes) == 0 and len(tasks) == 0,
        }

    # ─── detection_rules_lifecycle_inspector (Phase D) — rule 状态机 ──────────

    def _query_detection_rules_lifecycle_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """detection_rule_lifecycle projection → rule 状态机 (proposed/shadow/active_warning/
        enforced/retired) (Patch M-2.3-G)。"""
        materialized = self._proj("detection_rule_lifecycle") is not None
        rules = self._proj_list("detection_rule_lifecycle", "rules")
        state_filter = filters.get("lifecycle_state")
        if state_filter:
            rules = [r for r in rules if r.get("lifecycle_state") == state_filter]
        rows = sorted(
            ({
                "rule_id": r.get("rule_id"),
                "lifecycle_state": r.get("lifecycle_state"),
                "rule_definition": r.get("rule_definition"),
                "created_at_event_id": r.get("created_at_event_id"),
                "last_state_change_event_id": r.get("last_state_change_event_id"),
            } for r in rules),
            key=lambda d: str(d["rule_id"]),
        )
        return {
            "view_id": "detection_rules_lifecycle_inspector",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(rows),
            "by_lifecycle_state": dict(sorted(
                Counter(str(r.get("lifecycle_state")) for r in rules).items())),
            "rules": rows,
            "filtered_by_lifecycle_state": state_filter,
            "is_empty": len(rows) == 0,
        }

    # ─── patterns_surface_candidates (Phase D) — HistoricalPatternSurfaceCandidate ─

    def _query_patterns_surface_candidates(self) -> dict[str, Any]:
        """HistoricalPatternSurfaceCandidate 事件 + dedup_state projection (Patch M-2.3-G)。

        每 candidate: grouping_dimension/grouping_key/frequency(≥3)/span_runs(≥2)/finding_event_ids。
        dedup_state projection 缺失 → 诚实标 (本仓库未物化, 非假造)。
        """
        events = self._events_by_type(EventType.HISTORICAL_PATTERN_SURFACE_CANDIDATE)
        # dedup_state 非 ProjectionStore 注册 projection (data_source 声明但本仓库无 reducer 物化它) —
        # store.read 对未注册名抛 ValueError, 故防御性读, 缺失诚实标 False (非假造)。
        try:
            dedup_mat = self._proj("dedup_state") is not None
        except ValueError:
            dedup_mat = False
        candidates: list[dict[str, Any]] = []
        for ev in events:
            after = self._payload(ev).get("after_state", {})
            after = after if isinstance(after, dict) else {}
            candidates.append({
                "candidate_id": after.get("candidate_id"),
                "grouping_dimension": after.get("grouping_dimension"),
                "grouping_key": after.get("grouping_key"),
                "frequency": after.get("frequency"),
                "span_runs": after.get("span_runs"),
                "finding_event_count": len(after.get("finding_event_ids", []))
                if isinstance(after.get("finding_event_ids"), list) else 0,
                "first_observed_at": after.get("first_observed_at"),
                "last_observed_at": after.get("last_observed_at"),
                "event_id": ev.event_id,
            })
        candidates.sort(key=lambda c: str(c["candidate_id"]))
        return {
            "view_id": "patterns_surface_candidates",
            "watermark": self._watermark(),
            "materialized": len(events) > 0,
            "dedup_state_materialized": dedup_mat,
            "total": len(candidates),
            "by_grouping_dimension": dict(sorted(
                Counter(str(c["grouping_dimension"]) for c in candidates).items())),
            "candidates": candidates,
            "is_empty": len(candidates) == 0,
        }

    # ─── escalations_lifecycle_inspector (Phase D / M-2.3-E) — escalation_lifecycle ─

    def _query_escalations_lifecycle_inspector(self, filters: dict[str, Any]) -> dict[str, Any]:
        """escalation_lifecycle projection inspector — 5 状态机 (raised/triaged/resolved/
        post_resolved_learning_captured) + triage/resolution 留痕 (Patch M-2.3-G + M-2.3-E)。

        注: escalation_inbox (§4.10) 走 EscalationRaised 事件取产品语言富字段; 本 inspector 走
        escalation_lifecycle projection 看 lifecycle 工程态 (互补, 不重复)。
        """
        materialized = self._proj("escalation_lifecycle") is not None
        escs = self._proj_list("escalation_lifecycle", "escalations")
        state_filter = filters.get("lifecycle_state")
        if state_filter:
            escs = [e for e in escs if e.get("lifecycle_state") == state_filter]
        rows = sorted(
            ({
                "escalation_id": e.get("escalation_id"),
                "lifecycle_state": e.get("lifecycle_state"),
                "raised_by_skill": e.get("raised_by_skill"),
                "escalation_kind": e.get("escalation_kind"),
                "raised_at": e.get("raised_at"),
                "triage_category": e.get("triage_category"),
                "resolution": e.get("resolution"),
                "related_finding_ids": e.get("related_finding_ids", []),
                "related_task_ids": e.get("related_task_ids", []),
            } for e in escs),
            key=lambda d: str(d["escalation_id"]),
        )
        return {
            "view_id": "escalations_lifecycle_inspector",
            "watermark": self._watermark(),
            "materialized": materialized,
            "total": len(rows),
            "by_lifecycle_state": dict(sorted(
                Counter(str(e.get("lifecycle_state")) for e in escs).items())),
            "escalations": rows,
            "filtered_by_lifecycle_state": state_filter,
            "is_empty": len(rows) == 0,
        }

    # ════════════════════════════════════════════════════════════════════════
    #  §9 提示性 view / candidate alert (RUN-086) — surface 4 类 candidate, 不替 Nature 决策
    # ════════════════════════════════════════════════════════════════════════

    def surface_candidates(self) -> list[dict[str, Any]]:
        """§9 — 统一收集 4 类提示性 candidate (只 surface 识别, 由 Nature 显式触发 action)。

        Inv5 (宪法 4 物理实施): 纯 read-only —— 不 emit 事件 / 不自动转 NatureJudgment / 不自动
        obligation evolve / 不自动跑任何 action。`actionable_command` 只是给 Nature 的 towow 命令文本
        hint, 由她看后自己跑, view 绝不替她执行。每条 candidate 按 §9.2 alert 优先级 schema 结构化。
        """
        out: list[dict[str, Any]] = []

        # 1. obligation upgrade — 同 scope NatureJudgment ≥ 阈值 (§9.1)
        nj = self._query_nature_judgment_timeline()
        last_event_by_scope: dict[str, Any] = {}
        for row in nj.get("timeline", []):  # timeline 升序 → 末次覆盖 = 最新触发事件
            sc = row.get("scope")
            if isinstance(sc, str) and sc:
                last_event_by_scope[sc] = row.get("event_id")
        for cand in nj.get("upgrade_candidates", []):
            scope = cand.get("scope")
            out.append({
                "candidate_type": "obligation_upgrade",
                "level": "info",
                "subject": scope,
                "detail": (
                    f"同类 NatureJudgment 已聚 {cand.get('count')} 次 "
                    f"(≥{nj.get('upgrade_cluster_threshold')} 阈值)"
                ),
                "trigger_event_ref": last_event_by_scope.get(scope),
                "actionable_command": f"towow obligation evolve --from-judgment-scope {scope!r}",
                "expires_at": None,
            })

        # 2. escalation — 多轮 fix ≥ 阈值 (§9.1)
        fh = self._query_fix_history({})
        for fx in fh.get("fixes", []):
            if not fx.get("escalation_candidate"):
                continue
            attempts = fx.get("attempts", [])
            out.append({
                "candidate_type": "escalation",
                "level": "warning",
                "subject": fx.get("fix_id"),
                "detail": (
                    f"fix 已进 {fx.get('max_attempt_no')} 轮 "
                    f"(≥{fh.get('escalation_round_threshold')} 阈值)"
                ),
                "trigger_event_ref": attempts[-1].get("event_id") if attempts else None,
                "actionable_command": "towow status --view fix_history",
                "expires_at": None,
            })

        # 3. concept supersede — stale @ref pin 指向已 supersede/retired concept (§9.1)
        sr = self._query_stale_references()
        for row in sr.get("stale_references", []):
            out.append({
                "candidate_type": "concept_supersede",
                "level": "critical",
                "subject": row.get("reference_id"),
                "detail": f"@ref pin 指向已 supersede/retired concept ({row.get('staleness_status')})",
                "trigger_event_ref": row.get("created_at_event_id"),
                "actionable_command": "towow status --view stale_references",
                "expires_at": None,
            })

        # 4. knowledge pack drift — DriftDetected (knowledge pack 类) (§9.1)
        ds = self._query_drift_stream()
        for d in ds.get("drifts", []):
            dt = d.get("drift_type")
            if not (isinstance(dt, str) and "knowledge" in dt.lower()):
                continue
            nodes = d.get("drift_nodes") or []
            out.append({
                "candidate_type": "knowledge_pack_drift",
                "level": "warning",
                "subject": nodes[0].get("entity_id") if nodes else None,
                "detail": f"knowledge pack drift detected (drift_type={dt})",
                "trigger_event_ref": d.get("event_id"),
                "actionable_command": "towow status --view drift_stream",
                "expires_at": None,
            })

        out.sort(key=lambda c: (str(c["candidate_type"]), str(c.get("subject") or "")))
        return out

    def _query_candidate_alerts(self) -> dict[str, Any]:
        """§9 提示性 view 的结构化数据 — 4 类 candidate 汇总 + 计数。"""
        cands = self.surface_candidates()
        return {
            "view_id": "candidate_alerts",
            "watermark": self._watermark(),
            "total": len(cands),
            "by_type": dict(sorted(Counter(str(c["candidate_type"]) for c in cands).items())),
            "by_level": dict(sorted(Counter(str(c["level"]) for c in cands).items())),
            "candidates": cands,
            "is_empty": len(cands) == 0,
        }

    # ════════════════════════════════════════════════════════════════════════
    #  §6.2 render_view  (markdown — query_view + 渲染层)
    # ════════════════════════════════════════════════════════════════════════

    def render_view(
        self,
        view_id: str,
        fmt: str = "markdown",
        filters: dict[str, Any] | None = None,
        *,
        write: bool = True,
    ) -> str:
        """渲染 view 为指定格式 (§6.2)。markdown / json。write=True → 写 .towow/views/{file_path}。

        body 部分 deterministic (§7.3): 同 projection state + 同 filters → 同 body。frontmatter 的
        generated_at 是元数据 (会变), 不进 body —— idempotency 比对 body / query_view 即可。
        """
        if view_id not in VIEW_REGISTRY:
            raise ViewNotFoundError(view_id)
        vdef = VIEW_REGISTRY[view_id]
        data = self.query_view(view_id, filters)

        if fmt == "json":
            rendered = json.dumps(data, ensure_ascii=False, indent=2)
            out_path = self._views_dir / vdef.file_path.replace(".md", ".json")
        elif fmt == "markdown":
            body = self._render_markdown_body(view_id, data)
            rendered = self._frontmatter(vdef, fmt) + body + self._provenance_block(vdef)
            out_path = self._rendered_file(vdef)
        else:
            raise ValueError(f"unsupported format: {fmt!r} (markdown | json)")

        if write:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered, encoding="utf-8")
        return rendered

    def _frontmatter(self, vdef: ViewDefinition, fmt: str) -> str:
        wm = self._watermark()
        sources = "".join(f"\n  - {s}" for s in vdef.data_sources)
        return (
            "---\n"
            f"view_id: {vdef.view_id}\n"
            f"data_sources:{sources}\n"
            f"generated_at: {datetime.now(UTC).isoformat()}\n"
            f"projection_watermark: {wm}\n"
            f"render_format: {fmt}\n"
            f"view_schema_version: {_VIEW_SCHEMA_VERSION}\n"
            "---\n\n"
        )

    def _provenance_block(self, vdef: ViewDefinition) -> str:
        sources = "\n".join(f"- `.towow/graph/{s}.json` (watermark: {self._watermark()})"
                            for s in vdef.data_sources if s not in ("all_projections", "events"))
        if not sources:
            sources = f"- 聚合多 projection + event log (watermark: {self._watermark()})"
        return (
            "\n\n---\n\n"
            "**Provenance**: This view was generated from:\n"
            f"{sources}\n\n"
            f"**Projection watermark**: {self._watermark()}\n\n"
            f"**To refresh**: `towow views render {vdef.view_id}`\n\n"
            "**To dig deeper**: 每个 event_id 可在 event log 反查 canonical 事件。\n"
        )

    def _render_markdown_body(self, view_id: str, data: dict[str, Any]) -> str:
        renderers = {
            "dashboard": self._render_dashboard_md,
            "provenance_trace": self._render_provenance_md,
            "finding_inbox": self._render_finding_inbox_md,
            "activation_heatmap": self._render_activation_heatmap_md,
            "capsule_inspector": self._render_capsule_inspector_md,
            "capsule_session_attribution": self._render_capsule_session_attribution_md,
            "resolver_trend": self._render_resolver_trend_md,
            "drift_stream": self._render_drift_stream_md,
            "snapshot_inspector": self._render_snapshot_inspector_md,
            "consolidation_history": self._render_consolidation_history_md,
            "retention_health": self._render_retention_health_md,
            "gc_diagnostic": self._render_gc_diagnostic_md,
            "validation_report": self._render_validation_report_md,
            # RUN-082 — M-3.2 critical 批
            "task_list": self._render_task_list_md,
            "task_graph": self._render_task_graph_md,
            "critical_path": self._render_critical_path_md,
            "concept_graph": self._render_concept_graph_md,
            "escalation_inbox": self._render_escalation_inbox_md,
            "owner_inbox": self._render_owner_inbox_md,
            "obligation_dashboard": self._render_obligation_dashboard_md,
            "commit_history": self._render_commit_history_md,
            "gate_check_firing": self._render_gate_check_firing_md,  # T-FU-09 可观测
            # RUN-085 — M-3.2 view 扩展剩余 18 视图
            "finding_lifecycle": self._render_finding_lifecycle_md,
            "fix_history": self._render_fix_history_md,
            "at_reference_inspector": self._render_at_reference_inspector_md,
            "stale_references": self._render_stale_references_md,
            "consumer_list": self._render_consumer_list_md,
            "resolver_decision_inspector": self._render_resolver_decision_inspector_md,
            "info_need_graph": self._render_info_need_graph_md,
            "interview_progress": self._render_interview_progress_md,
            "brief_inspector": self._render_brief_inspector_md,
            "nature_judgment_timeline": self._render_nature_judgment_timeline_md,
            "mismatch_inbox": self._render_mismatch_inbox_md,
            "advisor_consult_log": self._render_advisor_consult_log_md,
            "planning_uncertainty_inbox": self._render_planning_uncertainty_inbox_md,
            "cross_plan_dashboard": self._render_cross_plan_dashboard_md,
            "finding_fix_task_joined": self._render_finding_fix_task_joined_md,
            "detection_rules_lifecycle_inspector":
                self._render_detection_rules_lifecycle_inspector_md,
            "patterns_surface_candidates": self._render_patterns_surface_candidates_md,
            "escalations_lifecycle_inspector": self._render_escalations_lifecycle_inspector_md,
            "candidate_alerts": self._render_candidate_alerts_md,
        }
        if view_id in renderers:
            return renderers[view_id](data)
        raise ViewRendererNotImplemented(view_id)

    @staticmethod
    def _render_dashboard_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# v3 系统状态总览 (dashboard)", ""]
        lines.append(f"projection 水位: **{data['watermark']}** · "
                     f"已物化板块: {len(data['materialized_sections'])}")
        lines.append("")
        sec = data["sections"]

        cap = sec.get("capabilities", {})
        if cap.get("materialized"):
            lines.append(f"## 能力看板 — {cap['total']} 项")
            for cls, n in cap["by_classification"].items():
                lines.append(f"- {cls}: {n}")
            # RUN-096 — 生效层级 (built vs enforced): 让 done-real 头条数字诚实
            enf = cap.get("by_enforcement")
            if enf is not None:
                lines.append("")
                lines.append(
                    f"### 生效层级 (enforcement_level) — {enf['enforced']} enforced / "
                    f"{enf['built_or_unmarked']} built-or-unmarked",
                )
                lines.append(
                    "- **built** = 建好 + 单测过; **enforced** = 效果在真路径物理成立 "
                    "(agent 绕不过的物理 gate + 真路径实证)",
                )
                lines.append(
                    f"- done-real 里: **{cap.get('done_real_enforced', 0)} enforced** / "
                    f"{cap.get('done_real_built_only', 0)} built-only",
                )
            lines.append("")

        cm = sec.get("commits", {})
        if cm.get("materialized"):
            lines.append(f"## Commit — {cm['total']} 条")
            for v, n in cm["by_verdict"].items():
                lines.append(f"- {v}: {n}")
            if cm.get("recent"):
                lines.append("- 最近 5 条:")
                for r in cm["recent"]:
                    lines.append(f"  - `{r['commit_event_id']}` [{r['verdict']}]")
            lines.append("")

        dec = sec.get("decisions", {})
        if dec.get("materialized"):
            lines.append(f"## 决策/判断 — {dec['total']} 条 (current_validity)")
            for v, n in dec["by_validity"].items():
                lines.append(f"- {v}: {n}")
            lines.append("- 按类型:")
            for t, n in dec["by_type"].items():
                lines.append(f"  - {t}: {n}")
            lines.append("")

        deb = sec.get("debts", {})
        if deb.get("materialized"):
            lines.append(f"## 自报债 — {deb['total']} 条")
            for s, n in deb["by_state"].items():
                lines.append(f"- {s}: {n}")
            lines.append("")

        obl = sec.get("obligations", {})
        if obl.get("materialized"):
            lines.append(f"## Obligation — {obl['total']} 条")
            for s, n in obl["by_canonical_state"].items():
                lines.append(f"- {s}: {n}")
            lines.append("")

        con = sec.get("concepts", {})
        if con.get("materialized"):
            lines.append(f"## 概念图 — {con['nodes']} 节点 / {con['edges']} 边")
            lines.append("")

        # 未物化板块诚实列出 (不假造数字)
        unmaterialized = [k for k, v in sec.items()
                          if isinstance(v, dict) and not v.get("materialized")]
        if unmaterialized:
            lines.append("## 未物化板块 (无对应事件 — 诚实标 0, 不假造)")
            for k in sorted(unmaterialized):
                note = sec[k].get("note", "projection 未物化")
                lines.append(f"- {k}: {note}")
            lines.append("")

        if data["alerts"]:
            lines.append("## ⚠ Alerts")
            for a in data["alerts"]:
                lines.append(f"- **{a['kind']}** ({a['count']}): {a['detail']}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _render_provenance_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Provenance Trace", ""]
        lines.append(f"seed: `{data.get('seed')}` · kind: **{data.get('seed_kind')}** · "
                     f"resolved: {data.get('resolved')}")
        lines.append("")
        if data.get("is_empty"):
            lines.append("_无法解析 seed 或追溯链为空。_")
            return "\n".join(lines)

        lf = data["links_followed"]
        lines.append(f"链长 {len(data['chain'])} 跳 · 跟随的 link: "
                     f"projection_backlink={lf['projection_backlink']} / "
                     f"causation={lf['causation']} / supersede={lf['supersede']} / "
                     f"subject_index={lf['subject_index']}")
        lines.append("")

        src = data.get("source_event")
        con = data.get("conclusion_event")
        if src:
            lines.append(f"**源头** (seq {src['seq']}): `{src['event_id']}` "
                         f"[{src['event_type']}] by {src['actor_id']}")
        if con:
            lines.append(f"**结论** (seq {con['seq']}): `{con['event_id']}` "
                         f"[{con['event_type']}] by {con['actor_id']}")
        lines.append("")

        lines.append("## 事件链 (按 sequence_number 升序 = 源 → 结论)")
        for hop in data["chain"]:
            ps = hop.get("payload_summary", {})
            ps_str = ", ".join(f"{k}={v}" for k, v in ps.items())
            lines.append(f"- seq **{hop['seq']}** `{hop['event_id']}` [{hop['event_type']}]")
            lines.append(f"  - actor: {hop['actor_type']}/{hop['actor_id']} · "
                         f"reached_via: {', '.join(hop['reached_via'])}")
            if ps_str:
                lines.append(f"  - payload: {ps_str}")
        lines.append("")

        if data["causation_gaps"]:
            lines.append("## ⚠ causation 断点 (debt-532b9d47a6c9 — 反向 link 未强制)")
            lines.append(f"链上 {len(data['causation_gaps'])} 跳的 causation_event_id 为 null — "
                         "靠 projection backlink 兜底接续, 非纯 causation 链:")
            for g in data["causation_gaps"]:
                lines.append(f"- `{g['event_id']}` [{g['event_type']}]")
            lines.append("")

        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-071 markdown 渲染 — M-0.2 UI 视图型 + M-0.7 §13.8 4 视图
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _render_finding_inbox_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Finding Inbox (review_inbox)", ""]
        if not data["materialized"]:
            lines.append("_finding_lifecycle projection 未物化 (无 FindingCreated 事件) — "
                         "诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 finding · "
                     f"projection 水位 {data['watermark']}")
        if data.get("filtered_by_severity"):
            lines.append(f"(已按 severity={data['filtered_by_severity']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 finding。_")
            return "\n".join(lines)
        lines.append("## 按 lifecycle_state 分布")
        for st, n in data["by_lifecycle_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## 按 severity 分布")
        for sv, n in data["by_severity"].items():
            lines.append(f"- {sv}: {n}")
        lines.append("")
        lines.append("## Findings (severity 升序 critical→minor)")
        for f in data["findings"]:
            lines.append(f"- `{f['finding_id']}` [{f['severity']}] — {f['lifecycle_state']}")
            detail = []
            if f.get("closure_state"):
                detail.append(f"closure={f['closure_state']}")
            if f.get("verification_state"):
                detail.append(f"verify={f['verification_state']}")
            if f.get("review_dimension"):
                detail.append(f"dimension={f['review_dimension']}")
            if detail:
                lines.append(f"  - {' · '.join(detail)}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_activation_heatmap_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Obligation 激活热力图 (activation_heatmap)", ""]
        lines.append(f"共 **{data['total_activations']}** 次激活 · "
                     f"{data['distinct_obligations']} obligation × {data['distinct_tasks']} task · "
                     f"水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 ObligationActivated 事件 — 诚实标空。_")
        else:
            lines.append("## obligation × task 激活矩阵 (count 降序)")
            for cell in data["matrix"]:
                lines.append(f"- `{cell['obligation_id']}` × `{cell['task_id']}`: "
                             f"{cell['count']}")
            lines.append("")
            lines.append("## 每 obligation 激活频次")
            for obl, n in data["per_obligation_count"].items():
                lines.append(f"- `{obl}`: {n}")
            lines.append("")
        if data["frequency_anomalies"]:
            lines.append("## ⚠ 频次异常 (远高于中位)")
            for a in data["frequency_anomalies"]:
                lines.append(f"- `{a['obligation_id']}`: {a['count']}")
            lines.append("")
        if data["never_activated"]:
            lines.append(f"## 从未激活的 obligation ({len(data['never_activated'])} 条)")
            for obl in data["never_activated"]:
                lines.append(f"- `{obl}`")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_capsule_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Capsule Assembly Inspector (capsule_inspector)", ""]
        lines.append(f"共 **{data['total']}** 次 resolver 决策 · "
                     f"{data['distinct_tasks']} 个 task · 水位 {data['watermark']}")
        if data.get("filtered_by_task_id"):
            lines.append(f"(已按 task_id={data['filtered_by_task_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 ResolverDecisionMade 事件 — 诚实标空。_")
            return "\n".join(lines)
        lines.append("## 各 task 的 capsule assembly 结果")
        for d in data["decisions"]:
            lines.append(f"- task `{d['task_id']}` · resolver `{d['resolver_id']}` "
                         f"(seq {d['seq']})")
            lines.append(f"  - candidates={d['candidate_count']} · "
                         f"hit={d['mechanical_hit_count']} · "
                         f"miss={d['mechanical_miss_count']} · "
                         f"placement={d['placement_count']}")
            lines.append(f"  - cache_hit={d['cache_hit']} · "
                         f"fallback={d['fallback_applied']} · "
                         f"active_set={len(d['final_active_set'])} 项")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_capsule_session_attribution_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Capsule 会话归属 (capsule_session_attribution)", ""]
        lines.append(f"共 **{data['total_sessions']}** 个会话单元 · 水位 {data['watermark']}")
        if data.get("filtered_by_session_id"):
            lines.append(f"(已按 session_id={data['filtered_by_session_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append(
                "_无带 provenance.session_id 的 CapsuleCompiled 事件 — 诚实标空 "
                "(源头 fix F-CIR-capsule-fork-not-session 后的胶囊才带会话归属)。_",
            )
            return "\n".join(lines)
        lines.append("## 各会话单元注入的胶囊归属 (真实 ID, 非阶段均值/计数兜底)")
        for s in data["sessions"]:
            skill = f" · skill `{s['skill_id']}`" if s.get("skill_id") else ""
            lines.append(f"### 会话 `{s['session_id']}`{skill}")
            lines.append(f"- 胶囊单元 {s['capsule_unit_count']} 个 · "
                         f"概念 {s['distinct_concept_count']} · "
                         f"义务 {s['distinct_obligation_count']} "
                         f"(红线 {s['distinct_red_line_count']})")
            if s["red_line_ids"]:
                lines.append(f"- 🔴 红线: {', '.join(f'`{r}`' for r in s['red_line_ids'])}")
            if s["obligation_ids"]:
                lines.append(f"- 义务: {', '.join(f'`{o}`' for o in s['obligation_ids'])}")
            if s["concept_ids"]:
                lines.append(f"- 概念: {', '.join(f'`{c}`' for c in s['concept_ids'])}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_resolver_trend_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Resolver 趋势 (resolver_trend)", ""]
        lines.append(f"调用总数: **{data['total_calls']}** · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 ResolverDecisionMade 事件 — 诚实标空。_")
            return "\n".join(lines)
        lines.append(f"- cache 命中: {data['cache_hit_count']} / {data['total_calls']} "
                     f"(命中率 {data['cache_hit_rate']})")
        lines.append(f"- fallback: {data['fallback_count']} / {data['total_calls']} "
                     f"(fallback 率 {data['fallback_rate']})")
        if data["avg_runtime_ms"] is not None:
            lines.append(f"- 平均 resolver 耗时: {data['avg_runtime_ms']} ms "
                         f"({data['runtime_samples']} 个非 cache-hit 样本)")
        else:
            lines.append("- 平均耗时: 无样本 (全 cache hit 或无 runtime 记录)")
        lines.append("")
        lines.append(f"> {data['confidence_note']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_drift_stream_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Drift Stream (drift_stream)", ""]
        lines.append(f"共 **{data['total']}** 条 drift · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 DriftDetected 事件 — 诚实标空。_")
            return "\n".join(lines)
        lines.append("## 按 drift_type 分布")
        for dt, n in data["by_drift_type"].items():
            lines.append(f"- {dt}: {n}")
        lines.append("")
        lines.append("## Drift 事件 (时间倒序)")
        for d in data["drifts"]:
            nodes = ", ".join(
                f"{n['entity_type']}:{n['entity_id']}" for n in d["drift_nodes"]
            ) or "(无)"
            lines.append(f"- seq **{d['seq']}** `{d['event_id']}` [{d['drift_type']}] "
                         f"{d['timestamp']}")
            lines.append(f"  - 涉及节点: {nodes}")
            if d.get("envelope_event_id"):
                lines.append(f"  - envelope: `{d['envelope_event_id']}`")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_snapshot_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Snapshot Inspector (snapshot_inspector)", ""]
        lines.append(f"共 **{data['total']}** 个 snapshot · "
                     f"最新 offset {data['latest_offset']} · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 SnapshotCreated 事件 — 诚实标空。_")
            return "\n".join(lines)
        for s in data["snapshots"]:
            lines.append(f"## snapshot @ offset {s['event_offset']} "
                         f"[{s['snapshot_type']}] (seq {s['seq']})")
            lines.append(f"- bundle_hash: `{s['bundle_hash']}`")
            lines.append(f"- 存储: `{s['snapshot_storage_path']}`")
            lines.append(f"- 覆盖 projection: {len(s['covered_projections'])} 个")
            if s["per_projection"]:
                lines.append("- per-projection state hash:")
                for pp in s["per_projection"]:
                    lines.append(f"  - `{pp['projection_name']}` @ wm {pp['watermark']}: "
                                 f"`{pp['projection_state_hash']}`")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_consolidation_history_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Consolidation 历史 (consolidation_history)", ""]
        lines.append(f"共 **{data['total']}** 次 consolidation · "
                     f"折叠事件总数 {data['total_events_consolidated']} · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 CrossRunConsolidationCommitted 事件 — 诚实标空。_")
            return "\n".join(lines)
        lines.append("## Consolidation timeline (seq 升序)")
        for c in data["consolidations"]:
            seg = c["segment_range"]
            seg_str = (f"{seg.get('start_seq')}–{seg.get('end_seq')}"
                       if isinstance(seg, dict) else str(seg))
            lines.append(f"- seq **{c['seq']}** `{c['event_id']}` {c['timestamp']}")
            lines.append(f"  - 段范围: {seg_str} · 折叠 {c['original_event_count']} 事件 → "
                         f"digest `{c['digest_hash']}` [{c['digest_type']}]")
            if c.get("consolidation_run_id"):
                lines.append(f"  - run: `{c['consolidation_run_id']}` · "
                             f"snapshot: `{c['snapshot_event_id']}`")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_retention_health_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Retention 健康度 (retention_health)", ""]
        lines.append(f"projection 水位 {data['watermark']}")
        lines.append("")
        if not data["retention_watermark_materialized"]:
            lines.append("_retention-watermark.json 未生成 (无 GC/snapshot run push) — "
                         "诚实标空, 非假造。_")
        else:
            lines.append("## Retention watermark")
            lines.append(f"- retention_watermark: **{data['retention_watermark']}**")
            lines.append(f"- consumer_watermark: {data['consumer_watermark']}")
            lines.append(f"- hot_replay_floor: {data['hot_replay_floor']}")
            lines.append(f"- 最近 push: {data['pushed_at']}")
            lines.append("")
        lines.append("## Snapshot / hot segment")
        lines.append(f"- snapshot 数: {data['snapshot_count']} · "
                     f"最新 offset {data['latest_snapshot_offset']}")
        lines.append(f"- hot segment: {data['hot_segment_files']} 文件 / "
                     f"{data['hot_segment_bytes']} bytes")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_gc_diagnostic_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# GC 诊断 (gc_diagnostic)", ""]
        lines.append(f"projection 水位 {data['watermark']}")
        lines.append("")
        if not data["gc_state_materialized"]:
            lines.append("_gc-state.json 未生成 (无 GC run) — 诚实标空, 非假造。_")
            lines.append("")
            lines.append(f"> {data['root_set_note']}")
            return "\n".join(lines)
        lines.append("## GC run 历史")
        lines.append(f"- 累计 run 次数: **{data['run_count']}**")
        lines.append(f"- 最近 GC 时间: {data['last_gc_at']}")
        lines.append(f"- archive_seq_bound: {data['last_gc_archive_seq_bound']}")
        lines.append(f"- consumer_watermark: {data['last_gc_consumer_watermark']}")
        lines.append(f"- hot_replay_floor: {data['last_gc_hot_replay_floor']}")
        lines.append(f"- gc_run_state_hash: `{data['gc_run_state_hash']}`")
        lines.append("")
        lines.append(f"> {data['root_set_note']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_validation_report_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# M-3.4 验证大盘 (validation_report)", ""]
        lines.append(
            f"projection 水位: **{data['watermark']}** · "
            f"留痕事件 {data['total_runs']} 条 · 覆盖场景 {data['total_scenarios']} 个 (per-scenario 取最新)"
        )
        lines.append("")
        if data["is_empty"]:
            lines.append("_尚无 ValidationScenarioRun 留痕 — 诚实标空, 非假造。_")
            return "\n".join(lines)

        lines.append("## outcome 分布 (每场景最新)")
        for outcome, n in data["by_outcome_latest"].items():
            lines.append(f"- {outcome}: {n}")
        lines.append("")

        passed = data["passed"]
        lines.append(f"## ✅ passed — {len(passed)} 个 (真跑通)")
        for r in passed:
            lines.append(
                f"- `{r['scenario_id']}` · {r['invariant_count']} invariants · "
                f"observed {r['observed_event_count']} events · `{r['event_id']}`"
            )
        lines.append("")

        failed = data["failed"]
        lines.append(f"## ❌ failed — {len(failed)} 个 (真暴露问题 / 决定性负对照)")
        if not failed:
            lines.append("- (无)")
        for r in failed:
            lines.append(f"- `{r['scenario_id']}` [{r['outcome']}] · `{r['event_id']}`")
            for fi in r["failed_invariants"]:
                vp = fi.get("violation_observation_point") or ""
                lines.append(f"  - 违反: {fi['invariant_name']}" + (f" — {vp}" if vp else ""))
            cs = r.get("correction_signal")
            if cs:
                lines.append(f"  - 修正信号: {cs.get('exposed_design_assumption')}")
                lines.append(f"    → {cs.get('correction_direction')}")
        lines.append("")

        aborted = data["aborted"]
        lines.append(f"## ⏸ aborted_external — {len(aborted)} 个 (明账 skip, 不假跑/不假 pass)")
        lines.append("> daemon-gated / live-LLM-chain / deferred-gated — 依赖未满足 (daemon 没开是红线)。")
        for r in aborted:
            lines.append(f"- `{r['scenario_id']}` [{r['outcome']}] · `{r['event_id']}`")
        lines.append("")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-082 markdown 渲染 — M-3.2 critical 批
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _render_task_list_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 任务总览 (task_list)", ""]
        if not data["materialized"]:
            lines.append("_task_graph projection 未物化 (无 TaskNodeCreated 事件) — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 个 task · projection 水位 {data['watermark']}")
        if data.get("filtered_by_status"):
            lines.append(f"(已按 status={data['filtered_by_status']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 task。_")
            return "\n".join(lines)
        lines.append("## 按 status 分布")
        for st, n in data["by_status"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## Tasks (task_id 升序)")
        for t in data["tasks"]:
            lines.append(f"- `{t['task_id']}` [{t['status']}] — type={t['task_type']}")
            detail = []
            if t.get("model_tier"):
                detail.append(f"tier={t['model_tier']}")
            if t.get("last_run_outcome"):
                detail.append(f"last_run={t['last_run_outcome']}")
            if t.get("latest_event"):
                detail.append(f"event=`{t['latest_event']}`")
            if detail:
                lines.append(f"  - {' · '.join(detail)}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _mermaid_alias_map(ids: list[str], prefix: str) -> dict[str, str]:
        """concept_id / task_id 含 @/-/: 等 mermaid 非法字符 → 稳定 alias (按 id 排序)。"""
        return {cid: f"{prefix}{i}" for i, cid in enumerate(sorted(ids))}

    @staticmethod
    def _mermaid_label(text: str) -> str:
        """mermaid 节点 label 安全化 — 去引号/方括号, 截断。"""
        safe = str(text).replace('"', "'").replace("[", "(").replace("]", ")")
        return safe[:48]

    @classmethod
    def _render_task_graph_md(cls, data: dict[str, Any]) -> str:
        lines: list[str] = ["# 任务依赖图 (task_graph)", ""]
        lines.append(f"{data['node_count']} 节点 / {data['edge_count']} 边 · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_task_graph 无 task 节点 — 诚实标空。_")
            return "\n".join(lines)
        alias = cls._mermaid_alias_map([str(n["task_id"]) for n in data["nodes"]], "t")
        lines.append("```mermaid")
        lines.append("graph TD")
        for n in data["nodes"]:
            tid = str(n["task_id"])
            label = cls._mermaid_label(f"{tid} [{n.get('status')}]")
            lines.append(f'  {alias[tid]}["{label}"]')
        for e in data["edges"]:
            src, tgt = str(e["source"]), str(e["target"])
            if src in alias and tgt in alias:
                dep = e.get("dependency_type") or ""
                lines.append(f"  {alias[src]} -->|{cls._mermaid_label(dep)}| {alias[tgt]}")
        lines.append("```")
        lines.append("")
        lines.append("## 节点图例 (alias → task_id)")
        for tid in sorted(alias):
            lines.append(f"- `{alias[tid]}` = `{tid}`")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_critical_path_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 关键路径 (critical_path)", ""]
        if data["is_empty"]:
            lines.append("_task_graph 无 task 节点 — 诚实标空。_")
            return "\n".join(lines)
        lines.append(f"{data['node_count']} 节点 / {data['edge_count']} 边 · 水位 {data['watermark']}")
        lines.append("")
        lines.append(f"## 最长依赖链 ({data['path_length']} hop)")
        if data["longest_path"]:
            lines.append("`" + " → ".join(data["longest_path"]) + "`")
        else:
            lines.append("_无依赖边 (孤立节点)。_")
        lines.append("")
        lines.append("## ⓘ duration estimate")
        lines.append(f"- {data['duration_note']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _humanize_waited(seconds: object) -> str:
        """秒 → owner 一眼可读的等待时长 ('N 天/小时/分钟/秒')。"""
        s = max(0.0, float(seconds)) if isinstance(seconds, (int, float)) else 0.0
        if s < 60:
            return f"{int(s)} 秒"
        if s < 3600:
            return f"{int(s // 60)} 分钟"
        if s < 86400:
            return f"{int(s // 3600)} 小时"
        return f"{int(s // 86400)} 天"

    @staticmethod
    def _render_owner_inbox_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Owner 收件箱 (owner_inbox)", ""]
        if not data.get("materialized"):
            lines.append("_无在等你的事 (诚实标空)。_")
            return "\n".join(lines)
        if data.get("signal_lit"):
            lines.append(f"## 🔴 {data['signal_headline']}")
        else:
            lines.append(f"## ⚪ {data['signal_headline']}")
        lines.append("")
        lines.append(f"blocking: {data['blocking_count']} · FYI: {data['fyi_count']}")
        lines.append("")
        for e in data.get("entries", []):
            lines.append(f"### [{e['urgency']}] session `{e['session_id']}` — {e['essence']}")
            lines.append(f"- 已等 {ViewEngine._humanize_waited(e.get('waited_seconds', 0.0))}")
            lines.append(f"- 已备好: {e['prep_summary']}")
            lines.append(f"- 点进 → `{e['enter_session_handle']}`")
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def _render_concept_graph_md(cls, data: dict[str, Any]) -> str:
        lines: list[str] = ["# 概念图 (concept_graph)", ""]
        if not data["materialized"]:
            lines.append("_concept_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"{data['node_count']} 概念 / {data['edge_count']} 边 · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_concept_graph 无概念节点 — 诚实标空。_")
            return "\n".join(lines)
        alias = cls._mermaid_alias_map([str(n["concept_id"]) for n in data["nodes"]], "c")
        lines.append("```mermaid")
        lines.append("graph LR")
        for n in data["nodes"]:
            cid = str(n["concept_id"])
            label = cls._mermaid_label(str(n.get("name") or cid))
            lines.append(f'  {alias[cid]}["{label}"]')
        for e in data["edges"]:
            src, tgt = str(e["source"]), str(e["target"])
            if src in alias and tgt in alias:
                et = e.get("edge_type") or ""
                arrow = "-->" if e.get("is_active", True) else "-.->"
                lines.append(f"  {alias[src]} {arrow}|{cls._mermaid_label(et)}| {alias[tgt]}")
        lines.append("```")
        lines.append("")
        lines.append("## 边类型分布")
        for et, n in data["by_edge_type"].items():
            lines.append(f"- {et}: {n}")
        lines.append("")
        lines.append("## 概念图例 (alias → concept_id · name)")
        for n in data["nodes"]:
            cid = str(n["concept_id"])
            lines.append(f"- `{alias[cid]}` = `{cid}` — {n.get('name')}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_escalation_inbox_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 升级队列 (escalation_inbox)", ""]
        if not data["materialized"]:
            lines.append("_无 EscalationRaised 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条升级 · 待决 **{data['pending_count']}** 条 · "
                     f"水位 {data['watermark']}")
        if data.get("filtered_by_lifecycle_state"):
            lines.append(f"(已按 lifecycle_state={data['filtered_by_lifecycle_state']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无升级。_")
            return "\n".join(lines)
        lines.append("## 按状态分布")
        for st, n in data["by_lifecycle_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## 待你决策 (pending 在前)")
        for e in data["escalations"]:
            mark = "🔵 待决" if e["lifecycle_state"] not in (
                "resolved", "post_resolved_learning_captured") else "✅ 已决"
            lines.append(f"### {mark} · `{e['escalation_id']}` [{e['lifecycle_state']}]")
            lines.append(f"- {e.get('nature_facing_summary')}")
            if e.get("decision_needed_from_nature"):
                lines.append(f"- **需要你定**: {e['decision_needed_from_nature']}")
            if e.get("options"):
                lines.append("- 可选方向:")
                for o in e["options"]:
                    lines.append(f"  - **{o.get('option')}** — 取舍: {o.get('tradeoff')} · "
                                 f"对产品: {o.get('product_impact')}")
            if e.get("engineering_detail_ref"):
                lines.append(f"- (工程细节: {e['engineering_detail_ref']})")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_obligation_dashboard_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Obligation 总览 (obligation_dashboard)", ""]
        if not data["materialized"]:
            lines.append("_obligation_lifecycle_state projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 obligation · 水位 {data['watermark']}")
        flt = []
        if data.get("filtered_by_canonical_state"):
            flt.append(f"canonical_state={data['filtered_by_canonical_state']}")
        if data.get("filtered_by_severity"):
            flt.append(f"severity={data['filtered_by_severity']}")
        if flt:
            lines.append(f"(已过滤: {' · '.join(flt)})")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 obligation。_")
            return "\n".join(lines)
        lines.append("## 按 canonical_state")
        for st, n in data["by_canonical_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## 按 severity")
        for sv, n in data["by_severity"].items():
            lines.append(f"- {sv}: {n}")
        lines.append("")
        lines.append("## 按 scope_type")
        for sc, n in data["by_scope_type"].items():
            lines.append(f"- {sc}: {n}")
        lines.append("")
        lines.append("## Obligations (severity 升序 blocking→minor)")
        for o in data["obligations"]:
            lines.append(f"- `{o['obligation_id']}` [{o['severity']}] — {o['canonical_state']} "
                         f"· scope={o['scope_type']} · {o.get('nature')}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_commit_history_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Commit 历史 (commit_history)", ""]
        if not data["materialized"]:
            lines.append("_commit_history projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 commit · 水位 {data['watermark']}")
        if data.get("filtered_by_verdict"):
            lines.append(f"(已按 verdict={data['filtered_by_verdict']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 commit。_")
            return "\n".join(lines)
        lines.append("## 按 verdict 分布")
        for v, n in data["by_verdict"].items():
            lines.append(f"- {v}: {n}")
        lines.append("")
        lines.append(f"## 最近 {data['recent_limit']} 条 (projection 顺序)")
        for c in data["recent"]:
            lines.append(f"- `{c['commit_event_id']}` [{c['verdict']}] "
                         f"← envelope `{c['envelope_event_id']}`")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_gate_check_firing_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Commit gate check 触发观测 (gate_check_firing)", ""]
        if not data["materialized"]:
            lines.append("_gate_check_firing projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"水位 {data['watermark']}")
        lines.append("")
        lt = data["live_target"]
        lines.append("## live-target-execution-evidence@v1 · goal 收口门")
        lines.append("")
        if lt["latent"]:
            lines.append(
                f"🟡 **潜伏 (LATENT)** — 门已接线在跑 (总触发 **{lt['total_firing']}** 次), "
                f"但真实开火 **0** 次: 全部是 not_applicable 空放行。**这个门至今没真正保护过任何东西。**"
            )
        else:
            lines.append(
                f"🟢 **在保护 (ACTIVE)** — 总触发 {lt['total_firing']} 次, 其中真实评估 observable "
                f"**{lt['real_firing']}** 次 (通过 {lt['real_pass']} / 拒 {lt['real_reject']})。"
            )
        lines.append("")
        lines.append("| 语义 | 计数 | 含义 |")
        lines.append("|---|---:|---|")
        lines.append(f"| vacuous (not_applicable) | {lt['vacuous_not_applicable']} | 门跑了但该 goal 无 "
                     "live_target_observable 可评 = 空放行 (潜伏) |")
        lines.append(f"| real_pass | {lt['real_pass']} | 真评估 observable 且全满足 |")
        lines.append(f"| real_reject | {lt['real_reject']} | 真拒 completion (承重开火) |")
        lines.append(f"| **real_firing** | **{lt['real_firing']}** | real_pass + real_reject |")
        lines.append("")
        if lt["real_reject"]:
            lines.append("真拒 breakdown:")
            for r, n in lt["reject_breakdown"].items():
                lines.append(f"- `{r}`: {n}")
            lines.append("")
        if lt["latent"]:
            lines.append("### 为什么潜伏 (依赖未落地, 链到 open 债)")
            lines.append("")
            lines.append("门要真开火需 goal 的完成条件声明结构化 `live_target_observable`。当下这仍是自由文本, "
                         "无门强制声明 → 门每次收口都读到空 observable 集 → not_applicable 放行。潜伏根因:")
            lines.append("")
            for d in data["latency_debts"]:
                lines.append(f"- **{d['debt_id']}** (open): {d['why']}")
            lines.append("")
        lines.append("## 全 check_type result 分布 (accepted 路径)")
        lines.append("")
        lines.append("| check_type | result 分布 |")
        lines.append("|---|---|")
        for ct, buckets in data["by_check_type"].items():
            dist = ", ".join(f"{r}={n}" for r, n in buckets.items())
            mark = " ⬅ 本门" if ct == ViewEngine._LIVE_TARGET_CHECK else ""
            lines.append(f"| `{ct}`{mark} | {dist} |")
        lines.append("")
        lines.append("## 拒批分布 (rejected 路径, 按 failure_reason)")
        lines.append("")
        if data["rejections_by_reason"]:
            for r, n in data["rejections_by_reason"].items():
                mark = " ⬅ 本门真开火" if r in ViewEngine._LIVE_TARGET_REJECT_REASONS else ""
                lines.append(f"- `{r}`: {n}{mark}")
        else:
            lines.append("_无拒批。_")
        lines.append("")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════════════
    #  RUN-085 — 18 视图 markdown 渲染层 (与上方 query 配对)
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _render_finding_lifecycle_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Finding 生命周期 (finding_lifecycle)", ""]
        if not data["materialized"]:
            lines.append("_finding_lifecycle projection 未物化 (无 FindingCreated 事件) — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 个 finding · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 finding。_")
            return "\n".join(lines)
        lines.append("## 按 lifecycle_state")
        for st, n in data["by_lifecycle_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## Finding 时间线 (severity 升序)")
        for f in data["findings"]:
            lines.append(f"### `{f['finding_id']}` [{f['severity']}] — {f['lifecycle_state']}")
            chain = [f"created@`{f['created_at_event_id']}`"]
            if f.get("verification_state"):
                chain.append(f"verified→{f['verification_state']}")
            if f.get("resolution"):
                chain.append(f"resolved→{f['resolution']}")
            if f.get("closure_state"):
                chain.append(f"closure={f['closure_state']}")
            if f.get("last_state_change_event_id"):
                chain.append(f"last@`{f['last_state_change_event_id']}`")
            lines.append("- " + " · ".join(chain))
            meta = []
            if f.get("review_dimension"):
                meta.append(f"dim={f['review_dimension']}")
            if f.get("detection_method"):
                meta.append(f"detect={f['detection_method']}")
            if meta:
                lines.append("  - " + " · ".join(meta))
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_fix_history_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Fix 历史 / 多轮 fix dashboard (fix_history)", ""]
        if not data["materialized"]:
            lines.append("_无 FixCompleted 事件 — 诚实标空, 非假造。_")
            lines.append("_注: 本仓库无独立 fix_lifecycle projection, 读 FixCompleted 事件聚合。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total_fixes']}** 个 fix · {data['total_fix_events']} 条 FixCompleted · "
                     f"水位 {data['watermark']}")
        lines.append("")
        if data["escalation_candidates"]:
            lines.append(f"## ⚠️ EscalationCandidate (≥{data['escalation_round_threshold']} 轮 fix)")
            for fid in data["escalation_candidates"]:
                lines.append(f"- `{fid}` — 多轮未闭合, 建议升级")
            lines.append("")
        if data["is_empty"]:
            lines.append("_无 fix。_")
            return "\n".join(lines)
        lines.append("## Fix 趋势 (by fix_id)")
        for f in data["fixes"]:
            mark = " ⚠️" if f["escalation_candidate"] else ""
            lines.append(f"### `{f['fix_id']}` — {f['round_count']} 轮 (max attempt {f['max_attempt_no']})"
                         f" · latest={f['latest_outcome']}{mark}")
            for a in f["attempts"]:
                nov = f" · novelty={a['novelty']}" if a.get("novelty") else ""
                lines.append(f"- 第 {a['fix_attempt_no']} 轮: {a['outcome']}{nov} (`{a['event_id']}`)")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_at_reference_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 字段级 @ 引用图 (at_reference_inspector)", ""]
        if not data["materialized"]:
            lines.append("_at_reference_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条引用 · active {data['active_count']} · "
                     f"stale {data['stale_count']} · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 @ 引用。_")
            return "\n".join(lines)
        if data["stale_count"]:
            lines.append("## ⚠️ Stale 引用 (pin 指向已 supersede/retired)")
            for r in data["references"]:
                if r["is_stale"]:
                    lines.append(f"- `{r['reference_id']}` {r['source']} → {r['target']} "
                                 f"({r.get('staleness_status') or 'stale'})")
            lines.append("")
        lines.append("## 全部引用 (reference_id 升序)")
        for r in data["references"]:
            flag = " 🔴stale" if r["is_stale"] else ("" if r["is_active"] else " (inactive)")
            fp = f" .{r['field_path']}" if r.get("field_path") else ""
            lines.append(f"- `{r['reference_id']}`: {r['source']} →{fp} {r['target']} "
                         f"[{r.get('locator_type')}]{flag}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_stale_references_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 悬空引用警告 (stale_references)", ""]
        if not data["materialized"]:
            lines.append("_at_reference_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"扫描 {data['total_references']} 条引用 · stale **{data['stale_count']}** 条 · "
                     f"alert={data['alert_level']} · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_✅ 无悬空引用 (所有 @ ref pin 指向活跃 concept)。_")
            return "\n".join(lines)
        lines.append("## 🔴 critical — pin 指向已 supersede/retired concept")
        for r in data["stale_references"]:
            lines.append(f"- `{r['reference_id']}`: {r['source']} → **{r['target']}** "
                         f"({r.get('staleness_status') or 'stale'}) [{r.get('locator_type')}]")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_consumer_list_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Concept 消费方列表 (consumer_list)", ""]
        if not data["materialized"]:
            lines.append("_at_reference_graph / concept_graph 均未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total_concepts']}** 个 concept · 水位 {data['watermark']}")
        lines.append(f"(at_reference 物化={data['at_reference_materialized']} · "
                     f"concept_graph 物化={data['concept_graph_materialized']})")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 concept 消费关系。_")
            return "\n".join(lines)
        lines.append("## per concept_id 消费方 (supersede 影响面)")
        for c in data["concepts"]:
            name = f" — {c['concept_name']}" if c.get("concept_name") else ""
            lines.append(f"### `{c['concept_id']}`{name} ({c['consumer_count']} 消费方)")
            for cons in c["consumers"]:
                fp = f" .{cons['field_path']}" if cons.get("field_path") else ""
                lines.append(f"- {cons['consumer']}{fp} (`{cons['reference_id']}`)")
            if not c["consumers"]:
                lines.append("- _无消费方 (supersede 安全)_")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_resolver_decision_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Obligation Scope 判定矩阵 (resolver_decision_inspector)", ""]
        if not data["materialized"]:
            lines.append("_无 ObligationScopeJudged 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total_judgments']}** 次 scope 判定 · {data['cell_count']} 个 "
                     f"(obligation_id × task_type) 单元 · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 scope 判定。_")
            return "\n".join(lines)
        lines.append("## hit/miss/uncertain 分布 per (obligation_id, task_type)")
        lines.append("")
        lines.append("| obligation_id | task_type | total | hit | miss | uncertain | hit_rate |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in data["matrix"]:
            lines.append(f"| `{m['obligation_id']}` | {m['task_type']} | {m['total']} | "
                         f"{m['hit']} | {m['miss']} | {m['uncertain']} | {m['hit_rate']} |")
        lines.append("")
        lines.append("_用途: 调 obligation scope_rule 精度 (hit=in_scope / miss=out_of_scope)。_")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_info_need_graph_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 信息需求图 (info_need_graph)", ""]
        if not data["materialized"]:
            lines.append("_information_need_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['session_count']}** 个 session · 水位 {data['watermark']}")
        if data.get("filtered_by_session_id"):
            lines.append(f"(已按 session_id={data['filtered_by_session_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无采访 session。_")
            return "\n".join(lines)
        for s in data["sessions"]:
            lines.append(f"## session `{s['session_id']}` — {s['node_count']} 需求 "
                         f"(red-line {s['red_line_count']})")
            for st, n in s["by_status"].items():
                lines.append(f"- status {st}: {n}")
            lines.append("")
            for nd in s["nodes"]:
                flag = " 🔴red-line" if nd["red_line"] else ""
                lines.append(f"- `{nd['need_id']}` [{nd['status']}] ({nd['source']}){flag}: "
                             f"{nd['description']}")
                if nd["red_line"] and nd.get("red_line_reason"):
                    lines.append(f"  - 红线理由: {nd['red_line_reason']}")
                if nd.get("nature_original_quote"):
                    lines.append(
                        f"  - Owner-approved judgment: {nd['nature_original_quote']}"
                    )
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_interview_progress_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 采访进度 (interview_progress)", ""]
        if not data["materialized"]:
            lines.append("_information_need_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['session_count']}** 个 session · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无采访 session。_")
            return "\n".join(lines)
        for s in data["sessions"]:
            lines.append(f"## session `{s['session_id']}`")
            lines.append(f"- total: {s['total']} · resolved: {s['resolved']} · pending: {s['pending']}")
            lines.append(f"- 🔴 red_line_pending: {s['red_line_pending']}")
            lines.append(f"- nature_unique_pending: {s['nature_unique_pending']} "
                         f"· ai_reachable_pending: {s['ai_reachable_pending']}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_brief_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Brief 查看器 (brief_inspector)", ""]
        if not data["materialized"]:
            lines.append("_无 InterviewBriefPublished 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total_briefs']}** 个 brief · current {data['current_count']} · "
                     f"superseded {data['superseded_count']} · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 brief。_")
            return "\n".join(lines)
        lines.append("## 当前 brief (未被 supersede)")
        for b in data["briefs"]:
            if not b["is_current"]:
                continue
            lines.append(f"### `{b['brief_id']}` (session `{b['session_id']}`)")
            lines.append(f"- {b['owner_facing_summary']}")
            if b.get("goal_completion_condition"):
                lines.append(f"- 完成条件: {b['goal_completion_condition']}")
            if b.get("concept_seeds_created"):
                lines.append(f"- 种子 concept: {', '.join(map(str, b['concept_seeds_created']))}")
        lines.append("")
        superseded = [b for b in data["briefs"] if not b["is_current"]]
        if superseded:
            lines.append("## Supersede 历史")
            for b in superseded:
                lines.append(f"- `{b['brief_id']}` (`{b['event_id']}`) ← 被后续 brief 取代")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_nature_judgment_timeline_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Nature 判断时间线 (nature_judgment_timeline)", ""]
        if not data["materialized"]:
            lines.append("_无 NatureJudgmentCaptured 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 Nature 判断 · 水位 {data['watermark']}")
        lines.append("")
        if data["upgrade_candidates"]:
            lines.append(f"## ⚠️ Obligation candidate (同类 ≥{data['upgrade_cluster_threshold']} 次)")
            for c in data["upgrade_candidates"]:
                lines.append(f"- scope `{c['scope']}` 出现 {c['count']} 次 → 建议升级为 obligation")
            lines.append("")
        if data["is_empty"]:
            lines.append("_无 Nature 判断。_")
            return "\n".join(lines)
        lines.append("## 时间线 (seq 序)")
        for j in data["timeline"]:
            vj = "价值判断" if j.get("is_value_judgment") else "事实判断"
            lines.append(f"### `{j['judgment_id']}` [{vj}] · scope={j['scope']}")
            lines.append(f"- 原话: {j['quote']}")
            if j.get("meaning"):
                lines.append(f"- 含义: {j['meaning']}")
            lines.append(f"- (`{j['event_id']}` seq={j['seq']})")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_mismatch_inbox_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Mismatch 处理历史 (mismatch_inbox)", ""]
        if not data["materialized"]:
            lines.append("_无 MismatchDetected 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 mismatch · 未解决 **{data['unresolved_count']}** 条 · "
                     f"水位 {data['watermark']}")
        if data.get("filtered_by_task_id"):
            lines.append(f"(已按 task_id={data['filtered_by_task_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 mismatch。_")
            return "\n".join(lines)
        lines.append("## Mismatch 列表")
        for m in data["mismatches"]:
            mark = "✅ 已解决" if m["is_resolved"] else "🔵 待处理"
            lines.append(f"### {mark} · `{m['mismatch_id']}` (task `{m['task_id']}`)")
            lines.append(f"- [{m['severity_judgment']}/{m['affected_aspect']}] {m['mismatch_summary']}")
            if m["resolution"]:
                r = m["resolution"]
                lines.append(f"- 处理: {r['resolution_action']} → outcome={r['outcome']}")
                if r.get("executor_rationale"):
                    lines.append(f"  - 理由: {r['executor_rationale']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_advisor_consult_log_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Advisor 咨询历史 (advisor_consult_log)", ""]
        if not data["materialized"]:
            lines.append("_无 AdvisorConsultRequested 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条咨询 · 待裁决 **{data['pending_count']}** 条 · "
                     f"水位 {data['watermark']}")
        if data.get("filtered_by_task_id"):
            lines.append(f"(已按 task_id={data['filtered_by_task_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 advisor 咨询。_")
            return "\n".join(lines)
        lines.append("## 咨询列表 (看 advisor 决策模式 / 是否被滥用)")
        for c in data["consults"]:
            mark = "✅ 已裁决" if c["is_answered"] else "🔵 待裁决"
            lines.append(f"### {mark} · `{c['consult_id']}` (task `{c['task_id']}`)")
            lines.append(f"- 触发: {c['triggered_by']} · 问: {c['question']}")
            if c["verdict"]:
                v = c["verdict"]
                lines.append(f"- 裁决: {v['decision']} (confidence={v['confidence']}) — {v['rationale']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_planning_uncertainty_inbox_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 计划不确定性 inbox (planning_uncertainty_inbox)", ""]
        if not data["materialized"]:
            lines.append("_无 PlanningUncertainty 事件 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条不确定性 · 未解决 **{data['unresolved_count']}** 条 · "
                     f"阻塞 **{data['blocking_count']}** 条 · 水位 {data['watermark']}")
        if data.get("filtered_by_plan_id"):
            lines.append(f"(已按 plan_id={data['filtered_by_plan_id']} 过滤)")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无计划不确定性。_")
            return "\n".join(lines)
        lines.append("## 不确定性 (未解决 red_line 阻塞排前)")
        for u in data["uncertainties"]:
            if u["resolved"]:
                mark = "✅ 已解决"
            elif u["severity"] == "red_line":
                mark = "🔴 阻塞 freeze"
            else:
                mark = "🔵 advisory"
            lines.append(f"### {mark} · `{u['uncertainty_id']}` (plan `{u['plan_id']}`)")
            lines.append(f"- 不确定: {u['description']}")
            lines.append(f"- 影响: {u['impact']} · 处理: {u['resolution_action']}")
            if u.get("blocking_tasks"):
                lines.append(f"- 阻塞 task: {', '.join(map(str, u['blocking_tasks']))}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_cross_plan_dashboard_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 跨 plan 总览 (cross_plan_dashboard)", ""]
        if not data["materialized"]:
            lines.append("_task_graph projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['plan_count']}** 个 plan · {data['total_tasks']} 个 task · "
                     f"水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 plan (task_graph 无 task)。_")
            return "\n".join(lines)
        lines.append("## per plan 状态")
        for p in data["plans"]:
            lines.append(f"### plan `{p['plan_id']}` — {p['task_count']} task "
                         f"(done {p['done_count']})")
            for st, n in p["by_status"].items():
                lines.append(f"- {st}: {n}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_finding_fix_task_joined_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Finding × Fix × Task 联合 (finding_fix_task_joined)", ""]
        if not data["materialized"]:
            lines.append("_finding_lifecycle / task_graph / FixCompleted 均无数据 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"finding {data['finding_count']} · fix {data['fix_count']} · "
                     f"task {data['task_count']} · 水位 {data['watermark']}")
        lines.append("")
        lines.append(f"_{data['join_note']}_")
        lines.append("")
        if data["is_empty"]:
            lines.append("_三源均空。_")
            return "\n".join(lines)
        if data["findings"]:
            lines.append("## Findings (severity 升序) + closure")
            for f in data["findings"]:
                patch = f" ← patch `{f['related_patch_event_id']}`" if f.get("related_patch_event_id") else ""
                lines.append(f"- `{f['finding_id']}` [{f['severity']}] {f['lifecycle_state']} "
                             f"closure={f['closure_state']}{patch}")
            lines.append("")
        if data["fixes"]:
            lines.append("## Fixes (FixCompleted)")
            for fx in data["fixes"]:
                lines.append(f"- `{fx['fix_id']}` outcome={fx['outcome']} "
                             f"attempt={fx['fix_attempt_no']} (`{fx['event_id']}`)")
            lines.append("")
        if data["tasks"]:
            lines.append("## Tasks")
            for t in data["tasks"]:
                lines.append(f"- `{t['task_id']}` [{t['status']}] plan={t['plan_id']}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_detection_rules_lifecycle_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Detection Rule 状态机 (detection_rules_lifecycle_inspector)", ""]
        if not data["materialized"]:
            lines.append("_detection_rule_lifecycle projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条规则 · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 detection rule。_")
            return "\n".join(lines)
        lines.append("## 按 lifecycle_state (proposed→shadow→active_warning→enforced→retired)")
        for st, n in data["by_lifecycle_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## 规则列表")
        for r in data["rules"]:
            lines.append(f"- `{r['rule_id']}` [{r['lifecycle_state']}]: {r['rule_definition']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_patterns_surface_candidates_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 历史模式浮现候选 (patterns_surface_candidates)", ""]
        if not data["materialized"]:
            lines.append("_无 HistoricalPatternSurfaceCandidate 事件 — 诚实标空, 非假造。_")
            lines.append(f"_dedup_state projection 物化={data['dedup_state_materialized']}。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 个候选 · 水位 {data['watermark']} · "
                     f"dedup_state 物化={data['dedup_state_materialized']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无历史模式候选。_")
            return "\n".join(lines)
        lines.append("## 候选 (frequency≥3 · span_runs≥2 跨 run)")
        for c in data["candidates"]:
            lines.append(f"### `{c['candidate_id']}` — {c['grouping_dimension']}={c['grouping_key']}")
            lines.append(f"- 频次 {c['frequency']} · 跨 {c['span_runs']} run · "
                         f"{c['finding_event_count']} 个 finding 事件")
            lines.append(f"- 观察窗: {c['first_observed_at']} → {c['last_observed_at']}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_escalations_lifecycle_inspector_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# Escalation 生命周期 inspector (escalations_lifecycle_inspector)", ""]
        if not data["materialized"]:
            lines.append("_escalation_lifecycle projection 未物化 — 诚实标空, 非假造。_")
            return "\n".join(lines)
        lines.append(f"共 **{data['total']}** 条 escalation · 水位 {data['watermark']}")
        lines.append("")
        if data["is_empty"]:
            lines.append("_无 escalation。_")
            return "\n".join(lines)
        lines.append("## 按 lifecycle_state")
        for st, n in data["by_lifecycle_state"].items():
            lines.append(f"- {st}: {n}")
        lines.append("")
        lines.append("## Escalation 列表 (工程态 — 产品语言见 escalation_inbox)")
        for e in data["escalations"]:
            lines.append(f"### `{e['escalation_id']}` [{e['lifecycle_state']}] "
                         f"· kind={e['escalation_kind']} · by={e['raised_by_skill']}")
            if e.get("triage_category"):
                lines.append(f"- triage: {e['triage_category']}")
            if e.get("resolution"):
                lines.append(f"- resolution: {e['resolution']}")
            rel = []
            if e.get("related_finding_ids"):
                rel.append(f"finding={','.join(map(str, e['related_finding_ids']))}")
            if e.get("related_task_ids"):
                rel.append(f"task={','.join(map(str, e['related_task_ids']))}")
            if rel:
                lines.append("- 关联: " + " · ".join(rel))
        lines.append("")
        return "\n".join(lines)

    # ─── candidate_alerts (M-3.2 §9 ★) — 4 类 candidate, surface 不替 Nature 决策 ─
    @staticmethod
    def _render_candidate_alerts_md(data: dict[str, Any]) -> str:
        lines: list[str] = ["# 提示性 candidate alert (M-3.2 §9)", ""]
        # Inv5: surface candidate not advise —— view 只识别, 不替 Nature 做决策。
        lines.append(
            "> 下面是系统**识别**出的 candidate（不替你做决定）。看后由你 (Nature) **显式触发** "
            "对应 action，view 不自动做任何事。")
        lines.append("")
        if data["is_empty"]:
            lines.append("_当前无 candidate。_")
            return "\n".join(lines) + "\n"
        lines.append(
            f"共 **{data['total']}** 条 · 按类: "
            + ", ".join(f"{k}={v}" for k, v in data["by_type"].items()))
        lines.append("")
        for c in data["candidates"]:
            subj = c.get("subject") or "—"
            lines.append(f"## [{c['level']}] {c['candidate_type']} — {subj}")
            lines.append(f"- {c['detail']}")
            trig = c.get("trigger_event_ref")
            if trig:
                lines.append(
                    "- 触发事件: "
                    + event_link(str(trig), from_view="alerts/candidate_alerts.md"))
            cmd = c.get("actionable_command")
            if cmd:
                lines.append(f"- 你若决定处理，可跑: `{cmd}`")
            lines.append("")
        return "\n".join(lines) + "\n"


__all__ = [
    "ProjectionUnavailableError",
    "ViewDescriptor",
    "ViewEngine",
    "ViewNotFoundError",
    "ViewRendererNotImplemented",
]
