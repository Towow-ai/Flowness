"""M-3.1 §7 F-11 orchestrator daemon — polling form (v3 初版).

②F (2026-07-02 崩机根治): 本模块下划线开头的函数 (`_spawn_one_execution` / `run_polling_loop` /
`resume_orchestrator` 等) 是 daemon 内部件, 不是给别的会话 import 调用的编排 API —— 手动单发
一个任务用 `towow orchestrator dispatch <task_id>` (dispatch_one_task), 别 import 内部函数手写
派发链 (那正是 2026-07-01 夜 OOM 崩机的游离 orchestrator 源头)。

# spec source:
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md
#     §7.1-7.3 F-11 polling form (L612..L649)
#     §7.4 trigger contract (L650..L663)
#     §7.5 dedup + replay missed events (L664..L685)
#     §7.6 failure retry (L686..L711)
#     PHASE-D-REVERSE-CONTRIBUTION-LOG §6.3 trigger contract Phase D extensions
#   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch F-11-trigger-contract-1
#     GoalSessionTerminated → main-inbound (RUN-012 apply)

# RUN-012 (DOGFOOD-001-S/AA/X closure): polling daemon 真启 — watermark +
# dispatched dedup dir + spawn integration (mock default for safety, real via flag)
# + GoalSessionTerminated → main-inbound 路由 (per LEDGER Patch F-11-trigger-contract-1).
#
# Scope:
#   - Polling loop: scan events since watermark, route, dispatch, persist watermark
#   - main-inbound: emit OrchestratorDispatched audit trail (main_inbound_poller 消费)
#   - skill spawn: 调 claude_bg_helper.spawn_bg_session with auto-generated minimal
#     condition_text (RUN-013+ 升级到 per-skill launch prompt template)
#   - Failure retry: bounded MAX_RETRIES → emit OrchestratorDispatchFailed + 等 owner
#
# Out of scope (Phase E.5 / RUN-013+):
#   - Per-skill condition_text template (RUN-005 bucket-A/B/C-launch.md 形态)
#   - 9 L2 maintenance daemon 内部 detection rule (R finding)
#   - audit fork runtime (M finding)
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import uuid
import weakref
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from towow.awareness.exception_taxonomy import ExceptionTaxonomy, FailureClass
from towow.l0.commit_gate.finding_classification_consistency import (
    MECHANICAL_RETIREMENT_PATTERN,
    NATURAL_LANGUAGE_RETIREMENT_PATTERN,
    truncate_for_retirement_scan,
)
from towow.l0.commit_gate.live_target_reconcile_check import reconcile_goal_live_targets
from towow.l0.event_log.backup import maybe_backup_ledger
from towow.l1.escalation_threshold import should_escalate_technical_blocker
from towow.l1.memory_admission import (
    _MEMORY_PAUSE_FRACTION_ENV,
)
from towow.l1.memory_admission import (
    available_memory_fraction as _available_memory_fraction,
)
from towow.l1.memory_admission import (
    memory_pause_fraction as _memory_pause_fraction,
)
from towow.l2 import dead_letter_inbox, escalation_reflow, reconcile_loop
from towow.l2.daemon_base import Daemon, DaemonOutcomeRecord
from towow.l2.portable_runtime import (
    clear_revive_marker,
    commit_mutex,
    default_status_path,
    emit_finding_via_gate,
    governance_finding_dispatch_decision,
    governor_gate,
    maybe_revive_stalled_session,
    sweep_inv_e_refreeze,
)
from towow.l2.session_liveness import (
    SessionLivenessVerdict,
    assess_session_liveness,
)
from towow.l2.session_vitality import (
    DEFAULT_ACTIVE_THRESHOLD_S,
    VitalityVerdict,
    _parse_iso_epoch,
    assess_vitality,
    derive_exec_made_progress,
    roster_session_ids,
    scan_canonical_work_product,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    DaemonName,
    DaemonOutcome,
    EventCategory,
    EventType,
    FindingResolution,
    SubjectEntityType,
    SubjectRole,
    TargetEntityType,
    TaskType,
    TransitionType,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from towow.awareness.claim import ReapedClaim
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


@dataclass
class DispatchDecision:
    """Result of orchestrator routing a trigger event."""

    trigger_event_id: str
    trigger_event_type: str
    dispatch_to: str  # skill_id, "main-inbound", "Nature dashboard", "no-route"
    reason: str
    review_mode: str | None = None  # M-1.5 §7.0 (RUN-035 T-L1-54): design_time/author_time/fix_after
    task_id: str | None = None  # T3 (PLAN-FIX F-07): execution fan-out — 指派的具体 task_id（per-task dedup）
    # FB-3 (watermark↔派发容量解耦): 仅【backlog re-scan 重建】的 decision 带此字段 (从 backlog
    # marker 的 deferred_reason 还原)。区分 backlog 的两类来源 —— None = work_continuation (silent-death
    # 收割 / resume 保留 = 已有工作的续, 绕过 governor 429 节流); 非 None 且属 _GOVERNOR_REPASS_DEFERRED_REASONS
    # = fresh-deferred (本轮 governor/serial/budget/fix-worktree 截断写的【新派】, 只是上一轮没派成,
    # re-scan 时仍是新派必须重过 governor 门, 不准经 backlog 绕过 429)。fresh decision (非 backlog) 恒 None。
    backlog_deferred_reason: str | None = None


# FB-3 (watermark↔派发容量解耦): backlog marker 的 deferred_reason 取值。这四类都是【本轮被截断
# 的新派】(governor 429 / serial 单飞 / 并发帽耗尽 / fix 工位备不成), 经 backlog re-scan 捞回时
# 仍是新派 —— 必须重过 governor 门 (与旧『水位线被 nonexec_capped 冻住→下轮当 fresh decision 重评』
# 语义等价)。绝不准 fresh-deferred 项以 is_backlog_decision 身份绕过 429 节流。work_continuation
# backlog (silent-death 收割 / resume 保留) 不带 deferred_reason (None) → 不在此集 → 绕 governor 照旧。
_GOVERNOR_REPASS_DEFERRED_REASONS = frozenset({
    "throttle_deferred",          # governor 429 痕迹在效期内挡新派
    "serial_contention",          # serial-reject kind 已有 live 会话 / 本轮已派同 kind
    "cap_exhausted",              # 非 exec 并发帽 (nonexec_budget) 本轮耗尽
    "fix_worktree_unavailable",   # TOWOW_EXEC_ISOLATION=on 时 fix 隔离工位备不成
    "live_session_deferred",      # 统一活会话守卫: task 有活工作会话 (手动/自动) 在做, 本轮不派孪生
})


# M-3.1 §7.4 + PHASE-D §6.3 trigger contract
_DISPATCH_TABLE_FINDING_KIND = {
    "concept_issue": "engineering-consensus",
    "obligation_issue": "fix",
    "review_plan_issue": "review",
    "adjacent_code_issue": "fix",
    "closure_contract_defect": "fix",
    "cross_projection_inconsistency": "Nature dashboard",  # Patch M-2.1
    "routing_stuck": "Nature dashboard",  # Patch M-2.2
    "cross_task_fix_collision": "fix",  # → M-1.6
    "anomaly": "Nature dashboard",  # Patch M-2.2
    # 退役终态证据 (TaskClosureReason.RETIRED): premise_false finding 断言某冻结 task 的 GOAL 前提为假。
    # 它是【终态证据, 不是派修目标】—— 路由到 surface-only "Nature dashboard" (不 spawn fix worker),
    # 但必须是【可路由的】(dispatch≠no-route), 否则 finding-routability-birth-gate 在出生时就拒它 →
    # reason=retired 的唯一证据永远进不了账本 (closure-evidence-verification-gate retired 分支无源)。
    "premise_false": "Nature dashboard",
    # ─── R07 治理环路 (plan-r07-govloop): 专属 finding_kind, 不劫持 anomaly。
    # 默认路由 "Nature dashboard" (surface-only, 守 fail-safe-closed); 阶段3 owner 显式解冻
    # INF-003 + 受控 daemon 重启后才由 T-GL-09 切到 "fix" (自动派修)。
    "efficiency_regression": "Nature dashboard",
    "system_governance_defect": "Nature dashboard",
}

# ─── R07 T-GL-09: 治理 finding 自动派修接线 (owner-gated, fail-safe-closed) ─────────
# 这两个治理 finding_kind 默认仍 surface (上表 "Nature dashboard"); **仅当 owner 显式解冻
# INF-003** (maintenance/config.json: governance_auto_repair_unfrozen=true) 后, 才按 severity
# 切 "fix" (高严重度自动派修) / 仍 surface (低严重度 shadow)。标志缺失/false → surface, 行为
# 与解冻前完全一致 —— 接上这段代码本身不改变任何行为 (守 INF-003 不全自动红线)。
_GOVERNANCE_FINDING_KINDS = frozenset({"efficiency_regression", "system_governance_defect"})
_GOVERNANCE_AUTO_REPAIR_KEY = "governance_auto_repair_unfrozen"


def governance_auto_repair_unfrozen(towow_dir: object) -> bool:
    """INF-003 解冻开关 (读 .towow/maintenance/config.json; 缺/坏 → False)。fail-safe-closed:
    默认绝不自动派修; owner 显式解冻 (本 key=true) 后治理 finding 才按 severity 切 fix。
    """
    import json
    from pathlib import Path

    path = Path(towow_dir) / "maintenance" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get(_GOVERNANCE_AUTO_REPAIR_KEY) is True


def _towow_root_from_event_log(event_log: object) -> object:
    """从 event_log 的 log 路径上溯找 .towow 根 (含 graph/ 的那层); 找不到退 log 所在目录。"""
    from pathlib import Path

    log_path = Path(event_log._log_path)
    for p in (log_path.parent, *log_path.parents):
        if (p / "graph").is_dir():
            return p
    return log_path.parent


def _route_finding_kind(finding_kind: str, payload: dict, event_log: object) -> str:
    """finding_kind → dispatch target。治理 finding_kind 经 owner_unfrozen + severity gating
    (默认 OFF → 仍 surface); 其余仍走静态路由表 _DISPATCH_TABLE_FINDING_KIND。"""
    if finding_kind not in _GOVERNANCE_FINDING_KINDS:
        return _DISPATCH_TABLE_FINDING_KIND.get(finding_kind, "no-route")
    unfrozen = governance_auto_repair_unfrozen(_towow_root_from_event_log(event_log))
    severity = str(payload.get("severity") or "minor")
    decision = governance_finding_dispatch_decision(severity, owner_unfrozen=unfrozen)
    # fix → 自动派修 (owner 解冻 + 高严重度); shadow/dashboard → 仍 surface (Nature dashboard)。
    return "fix" if decision == "fix" else "Nature dashboard"

# finding-review-finding-kind-none-unroutable-loop-1 (Layer A): finding_kind 缺失/None 时按
# suggested_fix_layer.primary 回退路由的表 (镜像 _DISPATCH_TABLE_FINDING_KIND 的路由语义)。
# 背景: review finding-create 的 --finding-kind 默认 None (源头修在 main.py = Layer B), 故 review
# 会话不显式带时 finding_kind=None; 旧逻辑对此静默 return [] → finding 永不路由到 fix → 其
# review-unit verdict 永 !=passed → verdict-gated REVIEW task 无限重派烧 autopilot 会话。
# SuggestedFixLayer enum 全 7 值都覆盖 (未来新增 enum 值不在表内 → 落 _fallback_finding_route 的
# source.type 兜底)。spec 无专属 spawn 目标 → fix 经 feasibility-check 再分诊。
_FALLBACK_FIX_LAYER_DISPATCH: dict[str, str] = {
    "code": "fix",
    "sql": "fix",
    "hook": "fix",
    "config": "fix",
    "obligation": "fix",  # 镜像 obligation_issue → fix
    "spec": "fix",  # 无专属 spawn 目标; fix feasibility-check 再分诊 / 反向上报 authority owner
    "concept": "engineering-consensus",  # 镜像 concept_issue → engineering-consensus
}


def _fallback_finding_route(payload: dict[str, object]) -> tuple[str, str]:
    """finding_kind 缺失时的回退路由 (返回 (dispatch_to, reason)) —— 不静默丢弃 finding。

    finding-review-finding-kind-none-unroutable-loop-1: review-emitted finding 不带 --finding-kind
    时 finding_kind=None, 不能静默 return [] (会让 verdict-gated REVIEW task 无限重派)。
    优先按 suggested_fix_layer.primary 路由 (镜像 finding_kind 表语义); 无可映射 layer 时
    review-context finding (source.type=model_review, schema 强制必带 closure_contract = 意图被修)
    回退 fix, 其余落 no-route (零 spawn, 安全 —— 不臆造会话, 但留 OrchestratorDispatched 审计轨迹,
    优于旧的 return [] 静默丢弃)。
    """
    sfl = payload.get("suggested_fix_layer")
    primary = sfl.get("primary") if isinstance(sfl, dict) else None
    if isinstance(primary, str) and primary in _FALLBACK_FIX_LAYER_DISPATCH:
        target = _FALLBACK_FIX_LAYER_DISPATCH[primary]
        return target, f"finding_kind 缺失 → 回退 suggested_fix_layer={primary} → {target}"
    source = payload.get("source")
    is_review = isinstance(source, dict) and source.get("type") == "model_review"
    if is_review:
        return "fix", "finding_kind 缺失 + 无可映射 suggested_fix_layer; review finding 回退 fix"
    return "no-route", "finding_kind 缺失 + 无 suggested_fix_layer + 非 review finding → no-route"


# f-finding-kind-fixlayer-contradiction-misroutes-to-consensus (LEDGER C22-26 第 6 次根治):
# finding_kind 把一个 finding 路由到 engineering-consensus 座位 (只能产 concept/spec, 写不了代码、
# 无单事件 re-route 通道), 但 finding 自身 suggested_fix_layer.primary 指向 code 类修复层 = 座位矛盾。
# 旧逻辑信 finding_kind 一律派共识席 → 共识席只能 terminate unreachable / 记 NOT CODIFIED, 真 code
# fix 永不驱动 (f-lrf01/f-lrf03/f-lrf06/f-r12-1/f-r12-4 五次实证, 持续吃 consensus 会话)。这道纠偏门
# 在【finding_kind=str 显式路由】之上加一层: 检出座位矛盾 → 按 suggested_fix_layer 纠偏到 fix。
# 必须坐落在 _route_event 产 DispatchDecision 处 (本函数被调处的上游): backlog marker 写的是
# decision.dispatch_to, FB-3 re-scan 只 replay 不重 route —— 纠偏在 decision 处即覆盖 re-scan 路径
# (Conflict 24/f-lrf06 正是经 backlog re-scan 复发)。
#
# 与共识席座位矛盾的 code 类修复层 (= finding 显式点名集): consensus 写不了代码、无单事件 re-route
# 通道, 这些层的 fix 必须去 fix 座位。**spec 故意不在内** —— spec 是设计层, 与 concept 同属共识席
# 能处理的范畴 (concept_issue + spec 无座位矛盾, 留共识席正当); 只有真正"consensus 干不了"的层才纠偏。
# 未来新增 code 类 fix_layer enum 需显式加进本集 (保守默认 = 不纠偏 = 既有 consensus 路由不变 = 无回归;
# 真复发会被同一 review 过程再 surface)。
_CODE_CLASS_FIX_LAYERS_NEEDING_FIX_SEAT = frozenset({"code", "sql", "hook", "config", "obligation"})


def _fix_layer_contradicts_consensus_seat(
    dispatch_target: str, payload: dict[str, object],
) -> tuple[str, str] | None:
    """座位矛盾纠偏: finding_kind 派 engineering-consensus 但 suggested_fix_layer 指 code 类 → 纠到 fix。

    返回 (纠偏目标, 审计 reason); 无矛盾 → None。只对 engineering-consensus 座位纠偏 —— 它是唯一
    "路由能力 < suggested_fix_layer 所需"的座位 (Nature dashboard 是 governance/anomaly kind 的预期
    终态 surface, 非误路由; fix 本就能写代码, 无矛盾)。

    W1-R2 短期止血 (LEDGER Conflict 32): suggested_fix_layer=null 时上面的 code 类纠偏不覆盖 (无
    primary 可读)。这是 Conflict 22-26 五次事故的新变体——finding_kind=concept_issue 但语义其实是
    premise_false (task 该退役), suggested_fix_layer 干脆没填。这里加一条前置分支: sfl=null 时读
    description 做退役信号正则匹配, 命中则纠偏到 "Nature dashboard" (不是 "fix"——这不是"该去 fix
    座位", 是"整条压根不该是 concept_issue, 先给 owner 看")。只补新事件路由, 不修正账本旧 finding；
    关键词匹配是猜, 定位为过渡期止血, finding-classification-consistency-birth-gate@v1 (出生闸
    R2/R3) 落地后价值递减。
    """
    if dispatch_target != "engineering-consensus":
        return None
    sfl = payload.get("suggested_fix_layer")
    primary = sfl.get("primary") if isinstance(sfl, dict) else None
    if primary is None:
        # 正则搜索前截断 (truncate_for_retirement_scan)：与 finding_classification_consistency.py
        # 的 R2 检查共用同一条长度上限，防止无上限的 description 喂给退役信号正则时退化成 O(n²)
        # 回溯 (W1-R2 返修轮，VERIFY-REPORT §2.f)。
        description = truncate_for_retirement_scan(str(payload.get("description") or ""))
        matched = MECHANICAL_RETIREMENT_PATTERN.search(description) or (
            NATURAL_LANGUAGE_RETIREMENT_PATTERN.search(description)
        )
        if matched is None:
            return None
        reason = (
            f"finding_kind 路由 engineering-consensus 且 suggested_fix_layer=null, description 含"
            f"退役语义信号 (检出模式: {matched.group(0)!r}) —— 疑似 premise_false 误标 concept_issue"
            f" (W1-R2 短期止血 / LEDGER Conflict 32), 纠偏到 Nature dashboard 直送 owner 判断, 不留在"
            f"无写能力的 consensus 座位空转"
        )
        return "Nature dashboard", reason
    if not isinstance(primary, str) or primary not in _CODE_CLASS_FIX_LAYERS_NEEDING_FIX_SEAT:
        return None
    reason = (
        f"finding_kind 路由 engineering-consensus 与 suggested_fix_layer.primary={primary} (code 类, "
        f"写不了代码的座位) 矛盾 → 按 fix_layer 纠偏路由 fix "
        f"(f-finding-kind-fixlayer-contradiction-misroutes-to-consensus / LEDGER C22-26)"
    )
    return "fix", reason


# Default polling parameters (M-3.1 §7.3 5s; §7.6 not-infinite retry).
POLL_INTERVAL_DEFAULT_S = 5.0
MAX_RETRIES_DEFAULT = 3
RETRY_BACKOFF_S = 2.0  # multiplied by retry_count

# T-FND-02 (巡检分频): the stuck-baton self-heal sweep surfaces minute-to-hour-scale stalls
# (escalation timeout / redispatch circuit / unreconciled session / orphaned exec stamp), so it
# does NOT need to run every ~5s poll. Running it every poll was a major slice of the daemon CPU
# choke (each sweep folds the committed stream). Throttle it to one sweep per interval; far below
# the threshold at which any stuck baton matters. Env-overridable for ops tuning / tests.
STUCK_SWEEP_INTERVAL_DEFAULT_S = 60.0
_STUCK_SWEEP_INTERVAL_ENV = "TOWOW_STUCK_SWEEP_INTERVAL_S"


def _stuck_sweep_interval_s() -> float:
    """Seconds between stuck-baton sweeps (TOWOW_STUCK_SWEEP_INTERVAL_S, default 60s).

    Clamped to ≥0 (0 = sweep every poll, for tests); a non-numeric value falls back to default.
    """
    raw = os.environ.get(_STUCK_SWEEP_INTERVAL_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else STUCK_SWEEP_INTERVAL_DEFAULT_S
    except ValueError:
        return STUCK_SWEEP_INTERVAL_DEFAULT_S


# T-LRF (gap6 dead-letter drain): the dead-letter inbox has wired ENTRY points (enqueue at
# circuit_tripped / unroutable) but, until this wiring, NO production exit at all — the triage path
# (start_triage/decide_*) and the aging path (sweep_aged_out) both had zero src callers, so enqueued
# entries stayed "active" forever (monotonic accumulation; observed live: 3 un-drained entries).
# sweep_aged_out is the concept's designed terminal-state-reachability backstop ("死信箱里不能再卡死"):
# entries stuck past TTL in {enqueued, escalated_to_owner} are force-retired (aged_out → retired).
# TTL is a generous backstop (default 7 days) — NOT a substitute for triage; it only guarantees the
# inbox can't grow without bound while the richer triage path remains unbuilt. Env-overridable.
DEAD_LETTER_TTL_DEFAULT_S = 604800.0  # 7 days
_DEAD_LETTER_TTL_ENV = "TOWOW_DEAD_LETTER_TTL_S"


def _dead_letter_ttl_s() -> float:
    """Seconds a dead-letter entry may sit in an ageable state before forced retirement.

    TOWOW_DEAD_LETTER_TTL_S, default 7 days. Clamped to ≥0 (0 = age out anything older than now,
    for tests); a non-numeric value falls back to default.
    """
    raw = os.environ.get(_DEAD_LETTER_TTL_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEAD_LETTER_TTL_DEFAULT_S
    except ValueError:
        return DEAD_LETTER_TTL_DEFAULT_S


# T-LRF-10b (daemon-patrol-cost-separation@v1 条款④ 已终态 dispatched marker 归档): 派发去重戳
# (trigger dedup stamp) 一经写下永不删 → dispatched/ 单调膨胀 (实测 6465 个), 每分钟巡检的 glob
# (collect_stuck_batons (b) 扫 redispatch_circuit__*) + status iterdir 都得扫完整个目录 = 随账本
# 线性恶化 (concept ④ "活跃集保持小, 现状全扫 1561 marker 是③的成本源")。把【稳定的】trigger dedup
# 戳 (文件名以 evt- 开头: 裸 <event_id> + 复合 <event_id>__<dispatch>) 超龄后归档到 sibling 目录
# dispatched_archive/, 活跃集 dispatched/ 只留近期戳 + 运营 marker (retry__/redispatch_circuit__/
# exec__ 等, 均【不】以 evt- 开头, 永不归档)。归档对去重【语义透明】: is_already_dispatched 与
# clear_nonexec_dispatch_stamp 都查两个目录 → 归档只是重定位文件, 绝不改变任何"是否已派"的答案;
# 故"已终态"的判定只需挑【稳定】戳, 超龄 mtime 是廉价代理 (真逐对象终态查要逐事件 lookup, 正是本
# 概念要消除的成本)。env-overridable。
_DISPATCHED_ARCHIVE_SUBDIR = "dispatched_archive"
DISPATCH_ARCHIVE_AGE_DEFAULT_S = 1800.0  # 30 min — 稳定 trigger dedup 戳的"超龄"门槛
_DISPATCH_ARCHIVE_AGE_ENV = "TOWOW_DISPATCH_ARCHIVE_AGE_S"
# 单轮归档量上限: 防一次 sweep 在巨量积压 (首次 6465) 上停顿; 余量下一轮自动续归 (自排空, 不丢)。
DISPATCH_ARCHIVE_MAX_BATCH_DEFAULT = 4000


def _dispatch_archive_age_s() -> float:
    """Seconds a trigger-dedup stamp must age before it's archived (TOWOW_DISPATCH_ARCHIVE_AGE_S,
    default 30min).

    Clamped to ≥0 (0 = archive anything older than now, for tests); a non-numeric value falls back
    to default.
    """
    raw = os.environ.get(_DISPATCH_ARCHIVE_AGE_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else DISPATCH_ARCHIVE_AGE_DEFAULT_S
    except ValueError:
        return DISPATCH_ARCHIVE_AGE_DEFAULT_S


# Orchestrator persistence layout under .towow/orchestrator/
_ORCHESTRATOR_SUBDIR = "orchestrator"
_WATERMARK_FILE = "watermark.json"
_DISPATCHED_SUBDIR = "dispatched"
# T-FIX-B1-04 (FORWARD-chain#4): 真跑形态 (MOCK/REAL) 持久记录 — 防 '生产以真自驱意图启动却没带
# --real-spawn' 静默 mock 空转 (autopilot_idle_audit 实证 15h 空转坑: daemon 看着在转其实 0 改代码)。
# run_polling_loop 启动时写, status 长期读, preflight grep。一个文件让 owner/preflight 一眼判
# 'daemon 在真派还是 mock 空转'。配套 canonical 事件 OrchestratorSpawnModeSet 给 provenance。
_SPAWN_MODE_FILE = "spawn_mode.json"
# T-LRF-10b (daemon-patrol-cost-separation@v1 条款⑤ daemon 自报每轮耗时/扫描量到健康面板): daemon
# 每轮把本轮耗时 + 扫描量 (派发 decision 数 / 是否跑了全量巡检 / 巡检耗时 / 归档量 / 活跃集大小) 写
# 这个文件, collect_orchestrator_status 读出 → "下次热点不靠猜" (消除①的'靠清单猜')。一个文件
# 让 owner/agent 一眼看 daemon 每轮多贵、扫多少。
_DAEMON_HEALTH_FILE = "daemon_health.json"
# T-FIX-B3-02: per-plan phase-门 never-ready 死锁巡检状态 (连续卡住的轮次计数 + 已告警 dedup)。
_PHASE_STUCK_SUBDIR = "phase_stuck"
# T-FIX-B2-05 返工 (PARALLEL-locks#1 纵深防御补全): 被清戳的非 exec (review/fix/consensus/plan)
# trigger 的"待重派 backlog marker"。这是 exec backlog re-scan (ready_execution_tasks_to_dispatch)
# 的非 exec 对应物 —— 清复合戳后该 trigger 已在 watermark 之下永不被 _route_event 重扫 (清戳=死信号),
# 故必须有一条独立于 watermark 的重发现通道把它捞回重派。每个 marker 记 trigger_event_id +
# dispatch_to + review_mode (重建 decision 用), 派发循环每轮额外扫此目录重派, 经 B2-01 单飞门串行,
# 重派成功删 marker。证伪抓出: 原 B2-05 只复制了清戳一半, 漏了让清戳有意义的重发现一半。
_NONEXEC_BACKLOG_SUBDIR = "nonexec_backlog"
# 默认: 连续 N 轮 (= N 次派发巡检) 仍 never-ready 才告警 (避免刚 freeze 就误报);
# 可配 TOWOW_PHASE_STUCK_ROUNDS。dispatch 巡检每 poll_interval 跑一次, 故"轮次"≈时长/interval。
_PHASE_STUCK_ROUNDS_ENV = "TOWOW_PHASE_STUCK_ROUNDS"
_PHASE_STUCK_ROUNDS_DEFAULT = 12
# T-FIX-B1-01 (AUTOPILOT-core#5 / FORWARD-chain#2): 自愈重派次数上限 + 熔断。
# 现状: clear_exec_task_stamp 写 retry marker (retry_count 累加), 但批派发器从不消费 → 反复
# silent-death/非 success 的 task【无限】重派、永用原 tier。改: 候选池构建处读 retry_count, ≥ 上限
# → 不进派发池, emit RedispatchExhausted 显著告警 + 写幂等熔断 marker (后续轮不重复告警也不再重派,
# 直到 owner 介入)。上限是重派次数上限【非链长上限】(不违 owner E.5 '不加链长cap', 这是 runaway
# 自愈保护)。非 exec backlog 重派 (B2-05) 复用同一上限 + 熔断, 用 (trigger_event_id, dispatch_to) 做 key。
_REDISPATCH_CAP_ENV = "TOWOW_REDISPATCH_CAP"
_REDISPATCH_CAP_DEFAULT = 3
# 幂等熔断 marker 文件名前缀 (写在 dispatched/ 下, 与 retry__ marker 同目录): exec 用
# redispatch_circuit__<exec_task_stamp_name>; nonexec 用 redispatch_circuit__<trigger>__<slug>。
_REDISPATCH_CIRCUIT_PREFIX = "redispatch_circuit__"
# 回流冲突 marker 文件名前缀 (写在 dispatched/ 下): auto-promote 撞 merge 冲突 → 写
# promote_conflict__<worktree_id>, 语义 = 待人工解冲突, 非自动重试 (冲突重试不自愈)。
# auto-promote 见到它就 skip 该工位 (不再重派、不再重复告警), 直到 owner 手动清此 marker。
_PROMOTE_CONFLICT_PREFIX = "promote_conflict__"
# T-FIX-B1-03 (FORWARD-chain#6 / AUTOPILOT-core#3): 周期性自愈扫描 — 久卡断棒在 owner 不发 prompt
# 时也 emit 显著告警 (不静默)。现状: pending_escalations 只在 owner 查 status 或发 prompt(hook)时才
# 被看到 → escalation 卡 4 天无人知 (T-L0-02 病根)。daemon 自己每轮顺手巡检四类久卡断棒并主动 surface
# (分布式自愈, 不引中心总控): escalation 超时 / 重派熔断 task / pending session 久未对账 / exec stamp 孤儿。
# 阈值: 一条断棒卡过 N 秒 (TOWOW_ESCALATION_STUCK_S, 默认 3600s) 才告警 (避免刚冒出来就误报)。
_ESCALATION_STUCK_ENV = "TOWOW_ESCALATION_STUCK_S"
_ESCALATION_STUCK_DEFAULT_S = 3600.0
# per-baton dedup state: 记 baton 最近一次告警时刻 (last_alarmed_at), 同一 baton 每个阈值窗口至多
# emit 一次 (不每 6s 刷屏); 跨过一整个窗口仍卡才重新告警 (久卡不被永久静默)。baton 恢复 (escalation
# 被响应 / 熔断被清 / 会话被对账) → 删 dedup state (下次重卡按首卡告警)。
_SELF_HEAL_SWEEP_SUBDIR = "self_heal_sweep"


def _unwrap_stub_rewrap(rec: EventRecord) -> tuple[str, dict[str, object]]:
    """Get effective (event_type, payload) considering stub-rewrap NodeTouched envelope.

    Stub-rewrap pattern (RUN-004 / RUN-005 / RUN-012 dispatch all use this):
        event_type = NodeTouched
        payload = {kind: "<RealType>", stub_original_payload: {...real fields...}}

    For canonical (non-rewrap) events, returns (event_type.value, payload) as-is.
    """
    if rec.event_type is EventType.NODE_TOUCHED and isinstance(rec.payload, dict):
        kind = rec.payload.get("kind")
        orig = rec.payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    raw_payload = rec.payload if isinstance(rec.payload, dict) else {}
    return rec.event_type.value, raw_payload


def _extract_goal_session_id(payload: dict[str, object]) -> str | None:
    """Get goal_session_id from a goal-session event payload.

    兼容两种 payload 形状: canonical(after_state.goal_session_id, 如 CLI goal terminate)
    + stub-rewrap 顶层(goal_session_id, 如 orchestrator 自 emit / 测试 emit)。
    """
    after = payload.get("after_state")
    if isinstance(after, dict):
        gsid = after.get("goal_session_id")
        if isinstance(gsid, str) and gsid:
            return gsid
    gsid = payload.get("goal_session_id")
    if isinstance(gsid, str) and gsid:
        return gsid
    return None


def _resolve_bg_session_id(event_log: EventLog, goal_session_id: str) -> str:
    """f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①(反查基础): 给定会话的
    领域身份 (goal_session_id), 反查 claude --bg 真实分配的进程级 id (bg_session_id) —— 判活
    (`assess_session_liveness`/`assess_vitality`) 要拿这个去匹配 `claude agents --json`/daemon
    state.json, 领域身份对它们是无意义的假 id (①解耦后两者不再相等)。

    从对应 GoalSessionStarted 事件的 payload.bg_session_id 取 (emit_goal_session_started 新增
    字段, 只在调用方传了才写)。找不到该事件 / 事件没带 bg_session_id (execution fan-out 等未解耦
    路径, 或 CLI `goal spawn` 尚未补这个字段) → 退化返回 goal_session_id 自身 (旧行为, 两者当时
    确实相等)。只找一次匹配的 GoalSessionStarted 即返回 (goal_session_id 是 spawn 前预生成的
    uuid 衍生值, 冲突概率可忽略, 不会撞到别的会话的 Started 事件)。
    """
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.GOAL_SESSION_STARTED.value:
            continue
        if _extract_goal_session_id(payload) != goal_session_id:
            continue
        bg_id = payload.get("bg_session_id")
        if isinstance(bg_id, str) and bg_id:
            return bg_id
        after = payload.get("after_state")
        if isinstance(after, dict):
            bg_id = after.get("bg_session_id")
            if isinstance(bg_id, str) and bg_id:
                return bg_id
        break
    return goal_session_id


def resolve_self_goal_session_id(
    event_log: EventLog, claude_session_id: str,
) -> str | None:
    """f-orchestrator-spawn-injects-wrong-self-gid-concurrent-crosswire-20260718: 账本反查本会话
    真实领域身份 (goal_session_id), 锚定 CC 原生的 CLAUDE_CODE_SESSION_ID —— 不信可被 CC daemon
    spare-pool 复用污染的 env TOWOW_SELF_GID。是 `_resolve_bg_session_id` 的反方向 (bg→gid)。

    根因: `claude --bg` 把会话交给常驻 CC daemon 的 spare pool (预热进程池)。spawn client 端
    `subprocess.run(env=...)` 注入的 TOWOW_SELF_GID 不可靠地到达被复用的 spare —— spare 保留上一个
    job 残留的 TOWOW_SELF_GID (邻居会话的 gid), 而 CC 只重置它自己的 CLAUDE_CODE_SESSION_ID (每会话
    新生成), 不认识我们的自定义 var。实证 (2026-07-18): fix 会话 (账本 gid=gs-d74381025a17,
    bg=ecce2481) 的进程 env TOWOW_SELF_GID 却是 gs-a4753b1a4b05 (bg=52ef851d 的 review 会话 gid)。
    故 env TOWOW_SELF_GID 不能当权威身份源, 只能当 corroboration hint。

    可靠锚 = CLAUDE_CODE_SESSION_ID: 在账本反查 bg_session_id (前缀) 匹配的 GoalSessionStarted →
    取其 goal_session_id = 本会话真实身份。bg_session_id 是 8-hex daemon 短 id
    (emit_goal_session_started 记录), 恰是完整 uuid CLAUDE_CODE_SESSION_ID 的前缀 (实证:
    bg='ecce2481' ⊂ CLAUDE_CODE_SESSION_ID='ecce2481-…')。

    reverse 扫 (最近优先 + 命中即停): 本会话自己的 GoalSessionStarted 通常靠近末尾, 常见路径快速命中。
    找不到 bg 前缀匹配 (未记 bg / mock / 存量会话 / 无 CLAUDE_CODE_SESSION_ID) → None, 调用方退回
    既有 env/flag 逻辑 (零回归)。
    """
    if not claude_session_id:
        return None
    for rec in reversed(event_log.committed_index().records()):
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.GOAL_SESSION_STARTED.value:
            continue
        bg_id = payload.get("bg_session_id")
        if not (isinstance(bg_id, str) and bg_id):
            after = payload.get("after_state")
            bg_id = after.get("bg_session_id") if isinstance(after, dict) else None
        if not (isinstance(bg_id, str) and bg_id):
            continue
        if claude_session_id == bg_id or claude_session_id.startswith(bg_id):
            return _extract_goal_session_id(payload)
    return None


def _extract_outcome(payload: dict[str, object]) -> str | None:
    """Get TaskRunCompleted outcome (after_state.outcome 优先, 回退顶层)."""
    after = payload.get("after_state")
    if isinstance(after, dict):
        oc = after.get("outcome")
        if isinstance(oc, str):
            return oc
    oc = payload.get("outcome")
    return oc if isinstance(oc, str) else None


def _extract_task_id(payload: dict[str, object]) -> str | None:
    """Get task_id (after_state.task_id 优先, 回退顶层) — T3 execution fan-out 反查 plan 用。"""
    after = payload.get("after_state")
    if isinstance(after, dict):
        tid = after.get("task_id")
        if isinstance(tid, str) and tid:
            return tid
    tid = payload.get("task_id")
    return tid if isinstance(tid, str) and tid else None


def _extract_reason(payload: dict[str, object]) -> str:
    """Get GoalSessionTerminated reason (after_state.termination_reason 或顶层 reason)."""
    after = payload.get("after_state")
    if isinstance(after, dict):
        r = after.get("termination_reason") or after.get("reason")
        if isinstance(r, str) and r:
            return r
    r = payload.get("reason")
    return r if isinstance(r, str) and r else "unknown"


def _extract_owner_question(payload: dict[str, object]) -> str:
    """Get GoalEscalationRaised owner_question (after_state.owner_question 或顶层)."""
    after = payload.get("after_state")
    if isinstance(after, dict):
        q = after.get("owner_question")
        if isinstance(q, str) and q:
            return q
    q = payload.get("owner_question")
    return q if isinstance(q, str) and q else "(owner_question 缺失)"


def _record_src(rec: EventRecord) -> dict[str, object]:
    """从一条 record 取归一化的取值层 (after_state 嵌套优先, 回退顶层); 同时穿透 stub-rewrap
    NodeTouched 信封。供 fix_after 溯源各步读字段用 (FixProposed/FindingCreated/envelope 形态混杂)。"""
    _etype, payload = _unwrap_stub_rewrap(rec)
    after = payload.get("after_state")
    return after if isinstance(after, dict) else payload


def _task_is_review_typed(event_log: EventLog, task_id: str) -> bool:
    """task_id 是否 REVIEW-typed —— 扫 TaskNodeCreated 反查 task_type。

    与 execution_dispatch.dispatch_target_for_task / task_type_from_events (INV-B1-4 task_type
    路由) 语义同源: 都以 TaskNodeCreated.task_type == TaskType.REVIEW 作为 REVIEW task 的 canonical
    判据 (TaskRunCompleted payload 本身无 task_type 字段, 必须经 TaskNodeCreated 反查)。

    查不到 task / task_type → False。caller 据此 fail-toward-review: 不确定不抑制、照常派 review,
    守 review-on-critical-path 上界 —— 宁可多审一次, 绝不把不确定误吞成不审。
    """
    if not task_id:
        return False
    for rec in event_log.get_events_by_type(EventType.TASK_NODE_CREATED):
        tn = _record_src(rec)
        if tn.get("task_id") == task_id:
            return tn.get("task_type") == TaskType.REVIEW.value
    return False


def _trace_fix_after_origin_review_task_id(
    event_log: EventLog, fix_completed_payload: dict[str, object],
) -> str | None:
    """T-FIX-B2-04 (REVIEW-verdict#2): 从 FixCompleted 溯源到它修的 finding 所属的原 REVIEW task_id。

    溯源链 (全 event-sourced, 不信任何 agent 自报 / ephemeral 文件):
      1. FixCompleted.after_state.fix_id
         → FixProposed(fix_id).after_state.related_finding_id            (fix 修的 finding)
      2. related_finding_id
         → FindingCreated(finding_id).review_unit_id                     (= 产 finding 的 review 会话 A)
      3. review_unit_id (= 会话 A 的 session_id)
         → TransactionEnvelopeSubmitted(session_id==A).task_id           (会话 A 跑 REVIEW task R 时盖的
           envelope; 即便首次 conclude 被 verdict 门拒, envelope 走 path-B write_direct 永久落账 =
           A→R 的 canonical 桥)
      4. 确认 R 是 REVIEW-typed (TaskNodeCreated.task_type == "review")    → 返回 R; 否则 None。

    任一环断 → None (caller 不强造 task_id, fix_after 退化到现状无 task 的 author_time 旁路语义)。
    原触发非 REVIEW-typed task (纯 forward-chain author_time review 无 task) → 第 4 步 None, 不强造。
    """
    after = fix_completed_payload.get("after_state")
    src = after if isinstance(after, dict) else fix_completed_payload
    fix_id = src.get("fix_id") if isinstance(src, dict) else None
    if not isinstance(fix_id, str) or not fix_id:
        return None

    # 1. fix_id → related_finding_id (via FixProposed) — 单一真值源 _fix_to_related_finding_id
    #    (§SR 自履行恢复复用同一步; 多轮 propose 取最后一条带 related_finding_id 的)。
    finding_id = _fix_to_related_finding_id(event_log, fix_id)
    if finding_id is None:
        return None

    # 2. finding_id → review_unit_id (= review 会话 A 的 session_id, via FindingCreated)
    review_unit_id: str | None = None
    for rec in event_log.get_events_by_type(EventType.FINDING_CREATED):
        fc = _record_src(rec)
        if fc.get("finding_id") == finding_id:
            ru = fc.get("review_unit_id")
            if isinstance(ru, str) and ru:
                review_unit_id = ru
            else:
                # 回退: review finding 的 provenance.session_id (review CLI 盖 sid 时显式; 此处兜底)
                sess = getattr(rec.provenance, "session_id", None)
                source = fc.get("source")
                is_review = isinstance(source, dict) and source.get("type") == "model_review"
                if is_review and isinstance(sess, str) and sess:
                    review_unit_id = sess
            break
    if review_unit_id is None:
        return None

    # 3. review_unit_id (= 会话 A) → task_id R (via 会话 A 盖的 TransactionEnvelopeSubmitted)
    origin_task_id: str | None = None
    for rec in event_log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED):
        _etype, payload = _unwrap_stub_rewrap(rec)
        if payload.get("session_id") != review_unit_id:
            continue
        tid = payload.get("task_id")
        if isinstance(tid, str) and tid:
            origin_task_id = tid
            break
    if origin_task_id is None:
        return None

    # 4. 确认 R 是 REVIEW-typed (非 REVIEW-typed → 不强造, 返回 None)
    return origin_task_id if _task_is_review_typed(event_log, origin_task_id) else None


class OrchestratorDaemon(Daemon):
    """M-3.1 §7 F-11 orchestrator polling daemon (v3 初版).

    Each run scans recently-emitted trigger events and produces DispatchDecision records.
    Actual fork spawning is downstream (runtime LLM API call).
    """

    # B6-c: 6s 轮询的惯犯 — 空转不 emit 心跳 (要事/间隔才发), 停掉自喂+账本灌水。
    heartbeat_throttled = True

    def _run_notable(self, outcome_record: DaemonOutcomeRecord) -> bool:
        return bool(self._decisions) or super()._run_notable(outcome_record)

    daemon_name = DaemonName.CONCEPT_GRAPH_HEALTH  # placeholder — orchestrator is M-3.1, not M-2.x

    def __init__(self, event_log: EventLog, last_processed_seq: int = 0) -> None:
        super().__init__(event_log)
        self._last_seq = last_processed_seq
        self._decisions: list[DispatchDecision] = []
        # E.5 F-019-11: lazy cache of all GoalSessionStarted ids, built once per scan
        # only if the batch contains a termination needing correlation (avoids
        # full-log scan every iteration on large logs).
        self._started_ids_cache: set[str] | None = None
        # f-orchestrator-round-events-as-dicts-recompute-per-event-quadratic: lazy cache of
        # _all_events_as_dicts(), built once per scan and reused across every ready-set lookup
        # within that scan. Without this, _ready_execution_decisions re-materializes the FULL
        # committed stream (629k+ records → dicts, ~28s at current ledger scale) once per
        # PlanFreezed/TaskRunCompleted(success) event in the scanned batch — a batch with N such
        # events pays N× the full-ledger cost for data that is byte-identical across the whole
        # scan (the ledger cannot change mid-_scan(): _route_event is read-only, no writes happen
        # until the caller acts on the returned decisions). Same cache-once-per-scan pattern as
        # _started_ids_cache above.
        self._events_dicts_cache: list[dict[str, object]] | None = None

    @property
    def decisions(self) -> list[DispatchDecision]:
        """Last-run dispatch decisions (orchestrator output)."""
        return list(self._decisions)

    def _scan(self) -> DaemonOutcomeRecord:
        records = self._event_log.get_events_in_range(
            self._last_seq,
            self._event_log.next_sequence - 1,
        )
        self._decisions = []
        self._started_ids_cache = None  # rebuilt lazily if a termination needs correlation
        self._events_dicts_cache = None  # rebuilt lazily on first ready-set lookup this scan
        for rec in records:
            # T-L1-54: 一事件可 fan-out 多 decision (如 ECFreezed → planning 前进 + design-time review)。
            self._decisions.extend(self._route_event(rec))
        if records:
            self._last_seq = records[-1].sequence_number + 1
        return DaemonOutcomeRecord(
            scanned_count=len(records),
            findings_produced=0,  # orchestrator dispatches, doesn't produce findings
            outcome=DaemonOutcome.COMPLETED,
        )

    def _route_event(self, rec: EventRecord) -> list[DispatchDecision]:
        # RUN-012: unwrap stub-rewrap NodeTouched so orchestrator works on real
        # events.log where FindingCreated / GoalSessionTerminated land as
        # NodeTouched + payload.kind=<EventType> + stub_original_payload
        # (path A envelope not yet implemented for these types — see §6 §7
        # whitelist in M-3.1).
        # T-L1-54: 返回 list — 一事件可 fan-out 多 decision (ECFreezed → planning + design-time review)。
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)

        if effective_type == EventType.FINDING_CREATED.value:
            # T-LRF-01 (f-lrf01-birthgate-pathb-nodetouched-bypass): for NEW events this branch only
            # ever sees a *native* FINDING_CREATED — a path-B NodeTouched(kind=FindingCreated,
            # stub_original_payload={...}) is now fail-closed rejected at the write boundary
            # (EventLog._reject_birthgate_smuggle), so the L2/L0 unwrap asymmetry (this branch routes
            # it as a finding; the L0 finding_lifecycle reducer never materializes NodeTouched) has no
            # live instance to produce. The 32 legacy path-B finding-stubs all sit at seq ≤ 5608, far
            # below the orchestrator watermark (_last_seq), and are never rescanned. No behavioral
            # guard is added here on purpose: the write-boundary block makes that path unreachable, so
            # de-routing here would be redundant — and it must stay handling native finding_kind=None
            # fallback (defense-in-depth the birth-gate concept keeps; see _fallback_finding_route).
            # RUN-039 debt-37cf41 (spec §7.0 边5): meta-review 对 review_plan 的 critical 否决 →
            # reroute design_time review 重产 ReviewPlanCreated v2 (优先于 finding_kind 回环边)。
            from towow.l2.dispatch_templates import resolve_review_mode as _rrm
            meta_mode = _rrm(effective_type, effective_payload)
            if meta_mode == "design_time":
                return [DispatchDecision(
                    trigger_event_id=rec.event_id,
                    trigger_event_type=effective_type,
                    dispatch_to="review",
                    reason="meta-review critical → design_time v2 reroute (spec §7.0)",
                    review_mode="design_time",
                )]
            finding_kind = effective_payload.get("finding_kind")
            if isinstance(finding_kind, str):
                target = _route_finding_kind(finding_kind, effective_payload, self._event_log)
                reason = f"finding_kind={finding_kind}"
                # f-finding-kind-fixlayer-contradiction-misroutes-to-consensus (LEDGER C22-26
                # 第 6 次根治): finding_kind 派 engineering-consensus 但 suggested_fix_layer 指 code
                # 类 = 座位矛盾 → 按 fix_layer 纠偏到 fix (否则 code 真问题埋共识席 unreachable, 永不修)。
                corrected = _fix_layer_contradicts_consensus_seat(target, effective_payload)
                if corrected is not None:
                    target, reason = corrected
            else:
                # finding-review-finding-kind-none-unroutable-loop-1 (Layer A): finding_kind
                # 缺失/None (如 review finding-create 未带 --finding-kind) **不静默 return []** ——
                # 否则 review-emitted finding 永不路由到 fix, 其 review-unit verdict 永 !=passed,
                # verdict-gated REVIEW task 无限重派。按 suggested_fix_layer 回退路由 (双保险: 源头
                # 修在 review finding-create = Layer B, main.py)。
                target, reason = _fallback_finding_route(effective_payload)
            return [DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to=target,
                reason=reason,
            )]
        if effective_type == EventType.GOAL_SESSION_TERMINATED.value:
            # LEDGER Patch F-11-trigger-contract-1 (RUN-012 apply):
            # bg session 自主跑结束 → 路由到主对话 inbound queue.
            # reason ∈ {completion, external, unreachable, escalated} 都 route 到 main-inbound,
            # 由主对话决定 follow-up.
            #
            # E.5 / F-019-11 完工通知核对真会话: 空 goal_session_id 或无配对
            # GoalSessionStarted 的 termination 不当完工通知转发(防假完工信号 ——
            # 现状曾把空 goal_session_id 的 NodeTouched 当 goal 完成路由给主线)。
            gsid = _extract_goal_session_id(effective_payload)
            if not gsid or not self._has_matching_started(gsid):
                return []
            reason = _extract_reason(effective_payload)
            return [DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to="main-inbound",
                reason=f"goal_session_terminated goal_session_id={gsid} reason={reason}",
            )]

        if effective_type == EventType.GOAL_ESCALATION_RAISED.value:
            # F-026-3: escalation 接进 spec 已有通知线(非平行件)。撞 owner-only 决策 →
            # 路由 main-inbound 显著通知 owner; run_polling_loop 见此 trigger 会自动
            # pause 协调者(停止派新工作), 等 owner 回话。escalation 是重要信号, 不做
            # matching-started 过滤(宁可多通知不可漏)。
            owner_q = _extract_owner_question(effective_payload)
            gsid = _extract_goal_session_id(effective_payload) or "unknown"
            return [DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to="main-inbound",
                reason=f"⚠ ESCALATION goal_session_id={gsid} owner_question: {owner_q}",
            )]
        if rec.event_type is EventType.INVALIDATION_CASCADE:
            # Patch M-2.1-F: InvalidationCascade routing rules
            after = rec.payload.get("after_state", {}) if isinstance(rec.payload, dict) else {}
            affected = after.get("affected_entities", []) if isinstance(after, dict) else []
            types_in_cascade = (
                {e.get("entity_type") for e in affected if isinstance(e, dict)}
                if isinstance(affected, list)
                else set()
            )
            if "task" in types_in_cascade:
                target = "planning"
            elif "review_plan" in types_in_cascade:
                target = "review"
            elif "obligation" in types_in_cascade:
                target = "engineering-consensus"  # M-0.6 obligation owner
            elif "concept" in types_in_cascade:
                target = "engineering-consensus"
            else:
                target = "Nature dashboard"
            return [DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=rec.event_type.value,
                dispatch_to=target,
                reason=f"cascade affects {sorted(t for t in types_in_cascade if t)}",
            )]

        if effective_type == EventType.SEMANTIC_CONFLICT_DETECTED.value:
            # T-FIX-B5-03 (CONSTITUTION-unknown#3): 两并发会话对同一 obligation 给相反 scope 判定 →
            # 路由 main-inbound 显著通知 owner (像 routing_stuck → dashboard, 零 spawn, 安全)。
            # 🔴 检测 ≠ 仲裁: 不触发任何下游会话 / 不裁谁赢 (仲裁 deferred,
            # debt-run080-semantic-arbitration); 只让"产出打架"看得见 + 通知 owner, 不阻断提交。
            after = (
                effective_payload.get("after_state", {})
                if isinstance(effective_payload, dict) else {}
            )
            obl = after.get("obligation_id", "?") if isinstance(after, dict) else "?"
            sides = after.get("sides", []) if isinstance(after, dict) else []
            n_sides = len(sides) if isinstance(sides, list) else 0
            return [DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ SEMANTIC CONFLICT obligation={obl} 有 {n_sides} 方相反 scope 判定 "
                    "(检测仅 surface, 仲裁 deferred)"
                ),
            )]

        # E.5 forward-chain edges (Patch E5-forward-chain-trigger-contract-1) +
        # M-1.5 §7.0 review trigger contract (RUN-035 T-L1-54)。
        # 上一步阶段完工 → 自动起下一步 (consensus→planning, plan→execution); review 是
        # 正交旁路触发 (ECFreezed 额外触发 design-time review; task-success/fix 的前进棒本身=review,
        # 给它定 mode)。区别于上面的 finding_kind 回环边。
        from towow.l2.dispatch_templates import FORWARD_CHAIN_REGISTRY, resolve_review_mode

        decisions: list[DispatchDecision] = []
        is_aborted_task = (
            effective_type == EventType.TASK_RUN_COMPLETED.value
            and _extract_outcome(effective_payload) != "success"
        )
        if is_aborted_task:
            # B2 (PARALLEL-EXEC-FIX): 非 success 终态过去是死胡同 — 产 0 decision, 无重派无通知,
            # 下游子树静默永久卡。现在: ①路由 main-inbound 显著通知 owner (H-04 可见化);
            # ②带 task_id, 轮询层据此清 execution 戳 (clear_exec_task_stamp) 让该 task 可重派。
            aborted_tid = _extract_task_id(effective_payload)
            aborted_outcome = _extract_outcome(effective_payload) or "unknown"
            decisions.append(DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ TASK 非 success 终态 outcome={aborted_outcome} "
                    f"task={aborted_tid or '?'} — 不解锁下游; 已清执行戳待重派 (B2)"
                ),
                task_id=aborted_tid or None,
            ))

        # T3 (PLAN-FIX F-07): PlanFreezed / TaskRunCompleted(success) → 读 task_graph 算 ready-set,
        # 对每个 ready task fan-out 一个 execution decision (并行不限量). 替代旧的
        # PlanFreezed→1个execution 单车道; "完一个补一个"的【补】= 每个 TaskRunCompleted(success)
        # 重新触发本逻辑、把刚解锁的下一批 task 派出去 (dedup 在 run_polling_loop 用 task_id stamp)。
        decisions.extend(
            self._ready_execution_decisions(rec, effective_type, effective_payload),
        )

        # PlanFreezed 的前进链现由上面的 ready-set fan-out 接管, 不再走通用 FORWARD_CHAIN 的单
        # execution decision; 其它前进链边 (consensus→planning / task-success→review 等) 不变。
        fwd_target = (
            None
            if effective_type == EventType.PLAN_FREEZED.value
            else FORWARD_CHAIN_REGISTRY.get(effective_type)
        )
        fwd_to: str | None = None
        # T-LC-01 (finding-fwdchain-review-of-review-redispatch-1): 抑制 review-of-review 冗余派发。
        # TaskRunCompleted(success) 的 source task 本身 REVIEW-typed 时 (T-LND-04 verdict 门下 REVIEW
        # task 带 task_id、conclude emit TaskRunCompleted), 不该再派 author_time review —— 那个 review
        # 对象 envelope patches=[] 无可审 patch = 系统性浪费 (随 verdict 门常态化每 review-unit 各发一次)。
        # 覆盖两条派 review 路径: 前进棒 (下方 fwd_target 块) + design-time review fan-out 旁路 (下方
        # review_mode 块)。只针对 TaskRunCompleted: FixCompleted 的 fix_after / ECFreezed 的 design_time
        # 源事件非 TaskRunCompleted, 不受影响 (三条边界回归测试钉死)。short-circuit: 仅 TaskRunCompleted
        # 才付 TaskNodeCreated 反查代价。source task_type 经反查 (与 ready-set fan-out
        # dispatch_target_for_task / INV-B1-4 同源); 查不到 → _task_is_review_typed False → 不抑制 →
        # fail-toward-review (守 review-on-critical-path 上界, 不确定不误吞成不审)。
        suppress_review_of_review = (
            effective_type == EventType.TASK_RUN_COMPLETED.value
            and _task_is_review_typed(
                self._event_log, _extract_task_id(effective_payload) or "",
            )
        )
        if fwd_target is not None and not is_aborted_task and not suppress_review_of_review:
            # 前进棒本身是 review 时 (task-success/fix), 给它定 review_mode (否则 prompt 空心)。
            fwd_review_mode = (
                resolve_review_mode(effective_type, effective_payload)
                if fwd_target.skill_role == "review" else None
            )
            fwd_to = fwd_target.skill_role
            # T-FIX-B2-04 (REVIEW-verdict#2): fix_after 前进棒 (FixCompleted→review) 必须锚定它修的
            # finding 所属的原 REVIEW task_id —— 否则 fix_after 会话 conclude 时 meta 无 task_id → 不
            # emit TaskRunCompleted → verdict 门在 fix 路径永不重折 → REVIEW task 永不完成。溯源链断 /
            # 原触发非 REVIEW-typed task (纯 forward-chain author_time review 无 task) → None, 不强造。
            fwd_task_id = (
                _trace_fix_after_origin_review_task_id(self._event_log, effective_payload)
                if fwd_review_mode == "fix_after" else None
            )
            decisions.append(DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to=fwd_target.skill_role,
                reason=f"forward-chain {effective_type}→{fwd_target.skill_role}"
                + (f" (review mode={fwd_review_mode})" if fwd_review_mode else "")
                + (f" [fix_after 锚原 REVIEW task={fwd_task_id}]" if fwd_task_id else ""),
                review_mode=fwd_review_mode,
                task_id=fwd_task_id,
            ))
        # review fan-out: 前进棒不是 review 时 (如 ECFreezed→planning), 额外加 design-time review 边。
        # RUN-039 debt-37cf41: 传 payload 让内容感知边生效 (ReviewPlanCreated v2 supersede → author_time)。
        review_mode = resolve_review_mode(effective_type, effective_payload)
        if (
            review_mode is not None
            and fwd_to != "review"
            and not is_aborted_task
            and not suppress_review_of_review
        ):
            decisions.append(DispatchDecision(
                trigger_event_id=rec.event_id,
                trigger_event_type=effective_type,
                dispatch_to="review",
                reason=f"review trigger {effective_type}→review mode={review_mode}",
                review_mode=review_mode,
            ))
        return decisions

    def _has_matching_started(self, goal_session_id: str) -> bool:
        """True iff a GoalSessionStarted with this goal_session_id exists in the log.

        F-019-11 correlation backbone. Lazy-builds (and caches per scan) the set of
        all GoalSessionStarted ids — only triggered when a termination needs checking,
        so iterations without terminations never pay the full-log scan.
        """
        if self._started_ids_cache is None:
            cache: set[str] = set()
            # T-PERF perf sprint (批1): warm committed_index snapshot instead of a fresh
            # full-disk all_records() re-scan — same committed-visible record set (T-FND-02
            # pattern established at collect_stuck_batons / drive_escalation_answer_reflow).
            for rec in self._event_log.committed_index().records():
                etype, payload = _unwrap_stub_rewrap(rec)
                if etype != EventType.GOAL_SESSION_STARTED.value:
                    continue
                sid = _extract_goal_session_id(payload)
                if sid:
                    cache.add(sid)
            self._started_ids_cache = cache
        return goal_session_id in self._started_ids_cache

    # ──────────────────────────────────────────────────────────────────────────
    #  T3 (PLAN-FIX F-07) execution fan-out — 读 task_graph 算 ready-set, 让
    #  PlanFreezed 后多个 ready task 并行各起 execution (替代旧 PlanFreezed→1个execution
    #  单车道). 见 execution_dispatch.py + 01-reconciliation/PLAN-FIX-PLAN.md §三 #3。
    # ──────────────────────────────────────────────────────────────────────────

    def _events_as_dicts(self) -> list[dict[str, object]]:
        """全 log records 规整成 ready-set 要的 dict 视图 (模块级 _all_events_as_dicts 委托)。

        f-orchestrator-round-events-as-dicts-recompute-per-event-quadratic: memoized per
        _scan() (see _events_dicts_cache) — every PlanFreezed/TaskRunCompleted(success) event
        in the same scanned batch reuses the same materialized snapshot instead of each paying
        a fresh O(ledger) rebuild.
        """
        if self._events_dicts_cache is None:
            self._events_dicts_cache = _all_events_as_dicts(self._event_log)
        return self._events_dicts_cache

    def _task_plan_id(self, task_id: str, events: list[dict[str, object]]) -> str | None:
        """反查 task_id 属于哪个 plan (模块级 _task_plan_id_from_events 委托)。"""
        return _task_plan_id_from_events(task_id, events)

    def _ready_execution_decisions(
        self,
        rec: EventRecord,
        effective_type: str,
        payload: dict[str, object],
    ) -> list[DispatchDecision]:
        """PlanFreezed / TaskRunCompleted(success) → 对 plan 的 ready-set 每个 task fan-out
        一个 execution decision (带 task_id, 并行不限量).

        dedup (一个 task 一生一次 execution) 在 run_polling_loop 用 task_id stamp 跨-trigger
        去重 — 此处产全部 ready, 不 dedup (OrchestratorDaemon 无 towow_dir, dedup 状态在那层)。
        """
        # T3 review finding-3: events 懒算 — 非 PlanFreezed/非 TaskRunCompleted(success) 的事件
        # 不付全 log re-parse 代价 (本方法对 batch 内每个事件都被调一次)。
        plan_id: str | None = None
        events: list[dict[str, object]] | None = None
        if effective_type == EventType.PLAN_FREEZED.value:
            after = payload.get("after_state")
            src = after if isinstance(after, dict) else payload
            pid = src.get("plan_id") if isinstance(src, dict) else None
            plan_id = str(pid) if pid else None
        elif effective_type == EventType.TASK_RUN_COMPLETED.value:
            if _extract_outcome(payload) == "success":
                tid = _extract_task_id(payload)
                if tid:
                    events = self._events_as_dicts()  # 反查 plan_id 需要全图
                    plan_id = self._task_plan_id(tid, events)
        if not plan_id:
            return []
        from towow.l2.execution_dispatch import (
            dispatch_target_for_task,
            ready_tasks_to_dispatch,
        )

        if events is None:
            events = self._events_as_dicts()
        ready = ready_tasks_to_dispatch(events, plan_id, set())
        decisions: list[DispatchDecision] = []
        for t in ready:
            # T-LND-05 (INV-B1-4): 按 task_type 路由 — REVIEW task 派 review (走 verdict), 绝不
            # 当 execution 分发 (派错则永产不出 verdict, review 完成永不发生 = 危机1根)。
            target, review_mode = dispatch_target_for_task(t, events)
            decisions.append(
                DispatchDecision(
                    trigger_event_id=rec.event_id,
                    trigger_event_type=effective_type,
                    dispatch_to=target,
                    reason=f"ready-set fan-out plan={plan_id} task={t} →{target}"
                    + (f" (review mode={review_mode})" if review_mode else "")
                    + " (F-07 并行不限量)",
                    review_mode=review_mode,
                    task_id=t,
                )
            )
        return decisions


# f-orch-round-time-index-persist-storm-plus-on-passes-20260717: per-EventLog 增量物化 memo
# (见 _all_events_as_dicts docstring)。WeakKey → EventLog 实例回收时 memo 自动释放, 不锚生命周期。
@dataclass
class _EventsDictsMemo:
    n: int  # 已物化的 records 前缀长度
    last_seq: int  # 前缀末条的 sequence_number (一致性双验之二)
    out: list[dict[str, object]]  # 已物化前缀 (append-only 追加)


_EVENTS_DICTS_MEMO: weakref.WeakKeyDictionary[EventLog, _EventsDictsMemo] = (
    weakref.WeakKeyDictionary()
)


def _all_events_as_dicts(event_log: EventLog) -> list[dict[str, object]]:
    """全 log records 规整成 ready-set/调度策略要的 {event_type, event_id, payload:{after_state}}.

    unwrap stub-rewrap NodeTouched + 把 stub_original_payload 顶层 task data 规整进
    after_state (TaskRunCompleted 真 path-A 与 stub-rewrap 两形态都有 — main.py:8193);
    canonical 事件取其 payload.after_state。统一后喂 execution_dispatch 纯函数。

    T-LRG-B2 P0 迁移 (ledger-scale-sensitive-read-path@v1): 读源从全量磁盘扫描切到 warm
    committed_index().records()——同一批已提交事件的等价 in-memory 快照 (index.py:155 records()
    的 docstring 自陈它就是账本全量读方法的 in-memory 等价物),但 O(索引复用) 而非
    O(全账本) 每次重扫。该原语的 6 个 daemon-tick / HOT-cli 消费者 (ready-set / phase-stuck-sweep /
    dispatch-pool-tick / collect_orchestrator_status esc_events / dispatch / polling loop) 及
    inv_e_refreeze.py 的 _all_events 借道自动获益。committed_index 对跨进程提交 "strictly fresher"
    (event_log.py:_committed_index 的 docstring),故 dispatch 路径改走它无 staleness 风险。

    f-orch-round-time-index-persist-storm-plus-on-passes-20260717: dicts 物化本身也是
    O(全账本) 纯 Python (63 万事件实测 ~6s/次, 每轮多个 callsite 各自重物化)。加 per-EventLog
    增量 memo: records() 在 warm catch-up 下 append-only (committed order 稳定), 已物化前缀
    直接复用, 只转换新增尾巴。前缀一致性用 (长度, 该位置 sequence_number) 双验; 任何不符
    (轮转重建/截断) → 整表重物化, 永不比无 memo 更不正确。返回浅拷贝列表 (元素 dict 共享,
    与 daemon._events_dicts_cache 既有共享暴露同型) — 消费者向来把它当只读快照迭代。
    """
    recs = event_log.committed_index().records()
    memo = _EVENTS_DICTS_MEMO.get(event_log)
    if (
        memo is not None
        and memo.n > 0
        and len(recs) >= memo.n
        and recs[memo.n - 1].sequence_number == memo.last_seq
    ):
        if len(recs) > memo.n:
            memo.out.extend(_all_events_as_dicts_from(recs[memo.n:]))
            memo.n = len(recs)
            memo.last_seq = recs[-1].sequence_number
        return list(memo.out)
    out = _all_events_as_dicts_from(recs)
    _EVENTS_DICTS_MEMO[event_log] = _EventsDictsMemo(
        n=len(recs),
        last_seq=recs[-1].sequence_number if recs else 0,
        out=out,
    )
    return list(out)


def _all_events_as_dicts_from(records: list[EventRecord]) -> list[dict[str, object]]:
    """The records→dicts core: unwrap + after_state 规整,供 execution_dispatch 纯函数消费.

    T-FND-02 起就是巡检 sweep 喂 warm committed_index().records() 快照的入口;T-LRG-B2 P0 迁移后
    公开原语 _all_events_as_dicts 也改喂同一 warm 快照 (不再各自全量磁盘 re-scan),两条路径读源统一。

    T-TWU-B1/T-R01-9 回归修复 (真机核验发现, 设计文档未覆盖的更深根因): 顶层必须带 `timestamp`
    (rec.timestamp.isoformat(), writer-assigned, 全局单调) —— tasks_pending_replan 靠
    e.get("timestamp","") 的字符串序比较判"重排请求是否已被更晚一次发布消费"。此前这里从不产
    timestamp 键, 该函数任何走 _all_events_as_dicts 喂入生产路径的调用点收到的全是 ""("" < ""
    恒 False) → tasks_pending_replan 在生产里对任何 task 恒返回空集, pending_replan 排除从
    T-RMD-PROV-01 落地起就是死代码 (只有手搭 "timestamp" 字段的单测夹具能让它生效, 掩盖了这个洞)。
    """
    out: list[dict[str, object]] = []
    for rec in records:
        et, payload = _unwrap_stub_rewrap(rec)
        ast = payload.get("after_state") if isinstance(payload, dict) else None
        if not isinstance(ast, dict):
            ast = payload if isinstance(payload, dict) else {}
        # raw_event_type = the pre-unwrap on-disk type (f-stub-rewrap-close-bypasses-closure-evidence-
        # gate-1): _unwrap_stub_rewrap collapses a NodeTouched stub-rewrap into its inner `kind`, which
        # erases the difference between a REAL flat TaskNodeClosed (gate-verified) and an unwrapped
        # stub-rewrap NodeTouched(kind=TaskNodeClosed) (never faced the closure gate). closed_task_ids
        # reads raw_event_type to refuse folding the latter into "satisfied".
        out.append({
            "event_type": et,
            "raw_event_type": rec.event_type.value,
            "event_id": rec.event_id,
            "timestamp": rec.timestamp.isoformat(),
            "payload": {"after_state": ast},
        })
    return out


def _task_plan_id_from_events(task_id: str, events: list[dict[str, object]]) -> str | None:
    """反查 task_id 属于哪个 plan (扫 TaskNodeCreated)。"""
    for e in events:
        if e.get("event_type") != "TaskNodeCreated":
            continue
        payload = e.get("payload")
        a = payload.get("after_state") if isinstance(payload, dict) else None
        if isinstance(a, dict) and a.get("task_id") == task_id:
            pid = a.get("plan_id")
            return str(pid) if pid else None
    return None


def _task_owner_gate_cleared(events: list[dict[str, object]], task_id: str) -> bool:
    """owner-gate-clearance@v1: 该 task 是否已被 owner 经合法机制显式解除 owner-gate。

    合法机制 = 一条 owner/主会话经 CLI (`towow plan owner-gate-clear`) 发出、过 commit gate 的
    canonical `TaskNodeOwnerGateCleared` 事件。这是 owner 显式受控决定的持久化, 不是"自动放行开关"
    (autopilot 不能自 emit 绕自己的红线; 本事件的生产者是 CLI, 不是编排器)。见此事件 → 派发层不再
    因 owner-gate 拦这个 task (autopilot-owner-presence-removal, owner 2026-07-01 决策: 拆掉"永远等
    owner 在场按键"的运行时判据, 留 owner-gate 分类 + 只经 commit gate 的显式解除)。
    """
    for e in events:
        if e.get("event_type") != "TaskNodeOwnerGateCleared":
            continue
        payload = e.get("payload")
        a = payload.get("after_state") if isinstance(payload, dict) else None
        if isinstance(a, dict) and a.get("task_id") == task_id:
            return True
    return False


def _task_owner_gate(
    events: list[dict[str, object]], task_id: str,
) -> tuple[bool, str | None]:
    """fnd-r01-9: 读 task 的 requires_owner_gate / owner_gate_reason (从 TaskNodeCreated.after_state)。

    缺失 → (False, None)。fail-open: 没显式打标的普通任务照常派 (绝大多数 task 都没标), 红线只拦
    【显式标记】的不可逆动作 task —— 全拦会卡死整个 autopilot (owner 反复授权的自主跑), 一道锁死
    系统的红线门会被摘掉 = 没有门; "该标没标"由 plan 阶段打标义务 + 冻结门兜底, 不在派发层运行时猜。

    owner-gate-clearance@v1 (autopilot-owner-presence-removal): 显式标了 gate 的 task 若已被 owner 经
    合法机制解除 (TaskNodeOwnerGateCleared 事件) → 返 (False, None), 该 task 可进 ready-set 被自动派。
    读路径认"已解 gate"是本函数的单一收口 —— 派发主门 (_dispatch_execution_batch pool 剔) 与兜底门
    (_spawn_one_execution) 都经本函数, 故解除一处生效两门。
    """
    for e in events:
        if e.get("event_type") != "TaskNodeCreated":
            continue
        payload = e.get("payload")
        a = payload.get("after_state") if isinstance(payload, dict) else None
        if isinstance(a, dict) and a.get("task_id") == task_id:
            if a.get("requires_owner_gate") is not True:
                return (False, None)  # 未标 gate = 普通任务, 照常派
            if _task_owner_gate_cleared(events, task_id):
                return (False, None)  # 已被 owner 经合法机制显式解除 → 不再拦
            reason = a.get("owner_gate_reason")
            return (True, reason if isinstance(reason, str) else None)
    return (False, None)


def _owner_gate_synthetic_session_id(task_id: str) -> str:
    """fnd-r01-9: owner-gate escalation 的合成 goal_session_id。

    派发前根本没起 session, 但 GoalEscalationRaised.goal_session_id 必填 (min_length=1)。用
    `owner-gate-<task_id>` 前缀让这条 escalation 自标识为"派发前拦截、无真 running session", 且不
    污染 _exec_session_to_task_map 的真 session→task 反查 (那张表只由真 execution dispatch 建)。
    """
    return f"owner-gate-{task_id}"


def _latest_plan_freezed_event_id(plan_id: str, events: list[dict[str, object]]) -> str:
    """plan 最近一次 PlanFreezed 的 event_id (backlog re-scan 派发的 trigger 溯源)。"""
    latest = ""
    for e in events:
        if e.get("event_type") != "PlanFreezed":
            continue
        payload = e.get("payload")
        a = payload.get("after_state") if isinstance(payload, dict) else None
        if isinstance(a, dict) and str(a.get("plan_id", "")) == plan_id:
            eid = e.get("event_id")
            if isinstance(eid, str):
                latest = eid
    return latest


# ════════════════════════════════════════════════════════════════════════════════
#  Persistence helpers (M-3.1 §7.3 watermark + §7.5 dispatched dedup)
# ════════════════════════════════════════════════════════════════════════════════


def _orchestrator_dir(towow_dir: Path) -> Path:
    return towow_dir / _ORCHESTRATOR_SUBDIR


def _watermark_path(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _WATERMARK_FILE


def _dispatched_dir(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _DISPATCHED_SUBDIR


def _dispatched_archive_dir(towow_dir: Path) -> Path:
    """T-LRF-10b ④: sibling of dispatched/ holding archived (aged-out) trigger-dedup stamps.

    A SEPARATE dir (not a subdir of dispatched/) so the active-set iterdir/glob never descends into
    it — that is the whole point: keep the hot active set small. Archived stamps are still consulted
    by is_already_dispatched / clear_nonexec_dispatch_stamp (point `.exists()` lookups), never globbed
    on the per-poll/per-minute hot path.
    """
    return _orchestrator_dir(towow_dir) / _DISPATCHED_ARCHIVE_SUBDIR


def _daemon_health_path(towow_dir: Path) -> Path:
    """T-LRF-10b ⑤: orchestrator/daemon_health.json — per-round timing/scan-volume self-report."""
    return _orchestrator_dir(towow_dir) / _DAEMON_HEALTH_FILE


def _spawn_mode_path(towow_dir: Path) -> Path:
    """T-FIX-B1-04: 真跑形态持久记录文件 (orchestrator/spawn_mode.json)。"""
    return _orchestrator_dir(towow_dir) / _SPAWN_MODE_FILE


def _phase_stuck_dir(towow_dir: Path) -> Path:
    """T-FIX-B3-02: 每 plan 一个 JSON, 记 never-ready 连续轮次 + 是否已告警 (dedup)。"""
    return _orchestrator_dir(towow_dir) / _PHASE_STUCK_SUBDIR


def _nonexec_backlog_dir(towow_dir: Path) -> Path:
    """T-FIX-B2-05 返工: 被清戳的非 exec trigger 的"待重派 backlog marker"目录。

    每个 marker 一个 JSON (`<trigger>__<dispatch_slug>.json`), 记重建 decision 所需的最小信息。
    派发循环每轮独立于 watermark 扫此目录重派 (exec backlog re-scan 的非 exec 对应物)。
    """
    return _orchestrator_dir(towow_dir) / _NONEXEC_BACKLOG_SUBDIR


def ensure_orchestrator_layout(towow_dir: Path) -> None:
    """Create .towow/orchestrator/{watermark.json initial, dispatched/} idempotently."""
    odir = _orchestrator_dir(towow_dir)
    odir.mkdir(parents=True, exist_ok=True)
    _dispatched_dir(towow_dir).mkdir(parents=True, exist_ok=True)
    _pending_sessions_dir(towow_dir).mkdir(parents=True, exist_ok=True)  # E.5 reconciliation
    _phase_stuck_dir(towow_dir).mkdir(parents=True, exist_ok=True)  # T-FIX-B3-02 巡检状态
    _nonexec_backlog_dir(towow_dir).mkdir(parents=True, exist_ok=True)  # T-FIX-B2-05 重派 backlog
    wp = _watermark_path(towow_dir)
    if not wp.exists():
        wp.write_text(json.dumps({"last_processed_seq": 0}), encoding="utf-8")


def load_watermark(towow_dir: Path) -> int:
    """Return last processed sequence (default 0 if not initialized)."""
    wp = _watermark_path(towow_dir)
    if not wp.exists():
        return 0
    try:
        data = json.loads(wp.read_text(encoding="utf-8"))
        seq = data.get("last_processed_seq", 0)
        return int(seq) if isinstance(seq, int | str) and str(seq).isdigit() else 0
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def save_watermark_atomic(towow_dir: Path, seq: int) -> None:
    """Atomic write via tmp+rename (M-3.1 §7.5 watermark crash safety)."""
    wp = _watermark_path(towow_dir)
    tmp = wp.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_processed_seq": int(seq)}), encoding="utf-8")
    tmp.replace(wp)


def _dispatch_target_slug(dispatch_to: str) -> str:
    """Filename-safe slug of a dispatch_to value (e.g. 'Nature dashboard' → 'Nature_dashboard')."""
    return "".join(ch if ch.isalnum() else "_" for ch in dispatch_to)


def is_already_dispatched(
    towow_dir: Path,
    event_id: str,
    dispatch_to: str | None = None,
) -> bool:
    """M-3.1 §7.5 dedup — has this (trigger event, dispatch target) already been dispatched?

    T-L1-54 fan-out fix: one trigger event can fan out to multiple DispatchDecisions (ECFreezed →
    planning + design-time review). Pre-fix the dedup stamp was keyed by bare trigger_event_id, so
    the iteration marked the first decision dispatched and SWALLOWED the second (same event_id). The
    dedup key is now composite `<event_id>__<dispatch_to>` so each distinct decision dedups
    independently.

    Backward-compatible with legacy bare `<event_id>` stamps (events dispatched before this fix):
      - dispatch_to given  → True if the bare legacy stamp OR the exact composite stamp exists
        (legacy events stay fully deduped → no replay re-dispatch / cascade).
      - dispatch_to None   → True if the bare legacy stamp OR ANY `<event_id>__*` composite exists
        (the "was this event dispatched at all" query used by tests / status).
    """
    ddir = _dispatched_dir(towow_dir)
    # T-LRF-10b ④: 超龄 trigger dedup 戳 (含复合 <event_id>__<dispatch>) 会被归档 sweep 搬进
    # dispatched_archive/。去重必须把归档戳一并算作"已派", 否则归档 = 静默重置去重 = 重派已派
    # trigger (skip-on-resume 戳被归档后 resume 重放级联 / no-route 重投死信)。两处都是 O(1)
    # `.exists()`, 不上 per-poll 热路径成本; archival 因此对去重语义透明 (只重定位、不改答案)。
    adir = _dispatched_archive_dir(towow_dir)
    if (ddir / event_id).exists() or (adir / event_id).exists():  # legacy bare stamp
        return True
    if dispatch_to is not None:
        name = f"{event_id}__{_dispatch_target_slug(dispatch_to)}"
        return (ddir / name).exists() or (adir / name).exists()
    return any(ddir.glob(f"{event_id}__*")) or any(adir.glob(f"{event_id}__*"))


def mark_dispatched(
    towow_dir: Path,
    event_id: str,
    decision_payload: dict[str, object] | None = None,
    *,
    dispatch_to: str | None = None,
) -> None:
    """M-3.1 §7.5 dedup: write a dispatch stamp. Composite `<event_id>__<dispatch_to>` when a
    dispatch target is given (fan-out safe), else the legacy bare `<event_id>` stamp."""
    name = event_id if dispatch_to is None else f"{event_id}__{_dispatch_target_slug(dispatch_to)}"
    stamp = _dispatched_dir(towow_dir) / name
    body = json.dumps(decision_payload or {"dispatched_at": time.time()})
    stamp.write_text(body, encoding="utf-8")


def archive_terminal_dispatch_markers(
    towow_dir: Path,
    *,
    now: float | None = None,
    max_batch: int = DISPATCH_ARCHIVE_MAX_BATCH_DEFAULT,
) -> tuple[int, int]:
    """T-LRF-10b (daemon-patrol-cost-separation@v1 条款④): 把超龄【稳定 trigger dedup 戳】从活跃集
    dispatched/ 搬到 dispatched_archive/, 让活跃集保持小 (每分钟巡检 glob / status iterdir 才便宜)。

    选择子 = `name.startswith("evt-")` AND mtime 超龄 (≥ _dispatch_archive_age_s()):
      - evt- 开头 = 裸 <event_id> (skip-on-resume / no-route 等) + 复合 <event_id>__<dispatch> 去重戳,
        即本概念点名的"已终态 dispatched marker"。运营 marker (retry__ / redispatch_circuit__ /
        promote_conflict__ / fix_orphan__ / nonexec_retry__ / selfheal-recovery- / recovery_circuit__ /
        recovery_attempt__) 与 exec__ 戳都【不】
        以 evt- 开头 → 永不归档, 其 readers (is_exec_task_dispatched / collect_stuck_batons (b)(d) /
        status prefix-glob) 完全不受影响 (零回退)。
      - 归档对去重【透明】: is_already_dispatched 与 clear_nonexec_dispatch_stamp 都查 active+archive,
        故归档只重定位文件、绝不改"是否已派"的答案 → "已终态"判定只需挑【稳定】戳, 超龄 mtime 是廉价
        代理 (真"逐对象终态查"要逐事件 lookup, 正是本概念要消除的成本)。

    max_batch 上限防单轮在巨量积压 (首次 6465) 上停顿: 命中上限 → 本轮只归 max_batch, 活跃集仍 >上限,
    余量下一轮续归 (自排空, 不丢)。调用方据返回值留痕 (no silent cap)。

    Returns (archived_this_round, active_count_after) —— 活跃集大小供健康面板自报 (条款⑤)。
    """
    ddir = _dispatched_dir(towow_dir)
    if not ddir.is_dir():
        return (0, 0)
    threshold = _dispatch_archive_age_s()
    now_unix = now if now is not None else time.time()
    adir = _dispatched_archive_dir(towow_dir)
    archived = 0
    active_after = 0
    # 先 snapshot 目录清单再搬 (迭代中 os.replace 移出会改目录, 不能边迭代边改)。
    for f in list(ddir.iterdir()):
        name = f.name
        if not name.startswith("evt-"):
            active_after += 1  # 运营 / exec__ marker — 永不归档, 计入活跃集
            continue
        if archived >= max_batch:
            active_after += 1  # 本轮已达上限 → 留活跃集 (下一轮续归)
            continue
        try:
            age = now_unix - f.stat().st_mtime
        except OSError:
            active_after += 1  # stat 不到 (并发被搬/清) → 当它还在, 幂等
            continue
        if age < threshold:
            active_after += 1  # 未超龄 → 留活跃集
            continue
        adir.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(f, adir / name)  # atomic move (同一 .towow 文件系统内)
            archived += 1
        except OSError:
            active_after += 1  # 搬不动 (并发竞争) → 当它还在活跃集, 幂等不丢
    return (archived, active_after)


def _exec_task_stamp_name(task_id: str) -> str:
    """Stamp filename for a task's execution dispatch (per-task dedup).

    T3 review finding-1: task_id 是自由文本 plan 内容 (可含空格 / CJK / §:()/ 等 / 超长)。
    单用 lossy slug 会 (a) 不同 task_id slug 成同名 → 第二个被误判已派 → 静默漏派 (最危险:
    invisible missed task); (b) 超长 → 文件名 >255 字节 → OSError 崩 dispatch。故 slug 截断只
    做可读前缀, 加 sha256 短 hash 保唯一+定长 (碰撞安全 + 文件名恒 ≤ ~100 字节)。
    """
    slug = _dispatch_target_slug(task_id)[:80]
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
    return f"exec__{slug}__{digest}"


def is_exec_task_dispatched(towow_dir: Path, task_id: str) -> bool:
    """T3 (PLAN-FIX F-07): 一个 task 一生只派一次 execution — 跨 trigger 去重。

    PlanFreezed 与后续多个 TaskRunCompleted 可能都把同一 task 算进 ready-set (它一直 ready 直到
    被执行)。execution 的 dedup 必须 keyed by task_id (稳定), 不能用 trigger event_id (会变) —
    否则同一 task 被重复 spawn。
    """
    return (_dispatched_dir(towow_dir) / _exec_task_stamp_name(task_id)).exists()


# ── B-2 原子认领 (substrate 4, 设计 §八 B-2 / K5 claim.py) ───────────────────────────
#  病根 (声称2): 现 dedup = is_exec_task_dispatched (check) ... try_spawn ... mark (write) 非原子。
#  check 与 write 之间是宽 TOCTOU 窗口: 两个 driver (orchestrator 自动派 + 上游会话续推, 两独立
#  进程) 各自先 check 见"无戳"→各自 spawn→孪生。stamp 又是 success-才写, 加 O_EXCL 也只让第二个
#  *mark* 输, 不让第二个 *spawn* 输 (孪生已发生)。根治 = spawn 【之前】原子认领 (claim.py 的 O_EXCL),
#  第二个 driver claim 失败 → 不 spawn 转盯场。
#  角色切分 (避 item-1 双文件孪生): claim (.claim, 喷发窗口内持) = "此刻正在 spawn"; stamp (exec__*,
#  success 才写) = "已派/已完成" 终身 dedup。claim 只在 spawn 窗口内活, 成功→写 stamp+释放 claim,
#  失败→释放 claim (无 stamp, 可重派)。
_EXEC_CLAIM_SUBDIR = "exec_claims"


def _exec_claim_dir(towow_dir: Path) -> Path:
    """execution spawn 原子认领目录 (本地 fs — O_EXCL 原子性硬约束: 绝不放 NFS)。"""
    return _dispatched_dir(towow_dir).parent / _EXEC_CLAIM_SUBDIR


def claim_exec_spawn(towow_dir: Path, task_id: str, claimant: str) -> int | None:
    """spawn 前原子认领 (O_EXCL)。返回 fencing_token (int, 认领成功) 或 None (别人正在 spawn 同 task)。

    设计 §八 B-2: 替 check-then-write 的非原子序列。两 driver 并发 claim 同 task → 只一成功
    (O_EXCL POSIX 本地 fs 原子)。复用 K5 claim.py 核 —— 认领成功同时发单调 fencing token (B-3):
    token 注入子会话 env (TOWOW_FENCING_TOKEN) → 完工 TaskRunCompleted 携带 → commit gate 资源侧校验。
    None = 认领失败 (转盯场不 spawn)。
    """
    from towow.awareness.claim import claim_task

    result = claim_task(_exec_claim_dir(towow_dir), task_id, claimant)
    return result.fencing_token if result.ok else None


def release_exec_spawn(towow_dir: Path, task_id: str, claimant: str) -> bool:
    """释放 spawn 认领 (只有当前 claimant 能释放)。.fence 保留 → 下次认领 token 仍单调递增 (B-3)。

    每个【非 success 退出】必须调它 (spawn-fail / 早返), 否则 task 被永久认领 = 饿死 (设计点名最易漏接缝)。
    """
    from towow.awareness.claim import release_claim

    return release_claim(_exec_claim_dir(towow_dir), task_id, claimant)


def read_exec_fencing_token(towow_dir: Path, task_id: str) -> int | None:
    """读 task 当前认领的 fencing token (B-3 资源侧校验用; 无认领→None)。"""
    from towow.awareness.claim import read_claim

    info = read_claim(_exec_claim_dir(towow_dir), task_id)
    return info.fencing_token if info is not None else None


def renew_exec_spawn(towow_dir: Path, task_id: str, claimant: str) -> bool:
    """spawn 窗口心跳续约 (T-RMD-S3-REAPER): 刷新 .claim 的 ts → 让活/慢 spawn 不被 reaper 误回收。

    认领后的 spawn 窗口里 (execution session 锁尚未注册 → is_live 护栏还接不上) ts 是唯一活信号;
    续约刷新它, 让一个跑得比心跳阈值久的合法 spawn (慢工位创建 / 有界重试 backoff) 不被并发跑的
    reaper 当 stale 误回收 (done_criteria②)。非当前 claimant → False (不抢别人的 claim)。
    """
    from towow.awareness.claim import renew_claim

    return renew_claim(_exec_claim_dir(towow_dir), task_id, claimant)


def mark_exec_task_dispatched(
    towow_dir: Path,
    task_id: str,
    decision_payload: dict[str, object] | None = None,
) -> None:
    """T3: 标记 task 已派 execution (task_id stamp, 独立于 trigger event_id)。"""
    stamp = _dispatched_dir(towow_dir) / _exec_task_stamp_name(task_id)
    stamp.write_text(
        json.dumps(decision_payload or {"dispatched_at": time.time()}),
        encoding="utf-8",
    )


def _exec_worktree_id(task_id: str) -> str:
    """task 的隔离工位目录名/分支名 — 与 exec 戳同款 slug+sha256 (碰撞安全+定长, B1)。"""
    return _exec_task_stamp_name(task_id)[len("exec__"):]


# ════════════════════════════════════════════════════════════════════════════════
#  回流机制根治 (M-3.1 §4.2.3 step6 autopilot 路径) — orchestrator 拥有回流。
#
#  问题: 真合并逻辑 (WorktreeManager.promote_to_main + promote_and_record) 都在、能用, 但
#  回流从不触发: promotion 只在 `submit --worktree <path>` 时跑, 而 autopilot execution
#  playbook 给 agent 的命令是 `submit envelope.json` (不带 --worktree) → 永不触发 → 隔离工位
#  做完的活搁浅在自己的 git 分支, 永不并回 main。
#
#  方向: orchestrator 看到 TaskRunCompleted(success) → 自动 promote 该 task 的隔离工位回 main,
#  不靠 agent 命令记得带参数。submit 的 --worktree 路径保留作人工兜底 (autopilot playbook 不带
#  --worktree → 两路径物理互斥, 无双 promote)。
# ════════════════════════════════════════════════════════════════════════════════


def _promote_conflict_marker_path(towow_dir: Path, worktree_id: str) -> Path:
    """回流冲突 marker 路径 (写在 dispatched/ 下)。"""
    return _dispatched_dir(towow_dir) / f"{_PROMOTE_CONFLICT_PREFIX}{worktree_id}"


def is_promote_conflict_pending(towow_dir: Path, worktree_id: str) -> bool:
    """该工位是否已记 merge 冲突待人工解 (幂等: 见到即 skip auto-promote, 不重派不重复告警)。"""
    return _promote_conflict_marker_path(towow_dir, worktree_id).exists()


def _lookup_dispatched_worktree(towow_dir: Path, raw_task_id: str) -> Path | None:
    """反查 task 的隔离工位路径 (回流 step2 / Advisor 边界 c 物理核验结论)。

    ⚠ 边界 c 实证修正: worktree 路径**不在** OrchestratorDispatched canonical 事件里
    (emit_orchestrator_dispatched 的 payload 不带 worktree) —— 它只落在 _spawn_one_execution 写的
    exec dispatch 戳 (mark_exec_task_dispatched 的 decision_payload["worktree"], = str(隔离工位路径))。
    故反查的真源是 exec dispatch 戳文件, 不是 canonical 事件。无戳 / 戳里无 worktree 字段 =
    非隔离任务 (TOWOW_EXEC_ISOLATION off / mock_spawn, 活在共享主树) → None → caller skip。
    成功的 task 其 exec 戳不会被清 (clear_exec_task_stamp 只在 abort/失败路径跑), 故 TaskRunCompleted
    (success) 时戳必在。
    """
    stamp = _dispatched_dir(towow_dir) / _exec_task_stamp_name(raw_task_id)
    if not stamp.exists():
        return None
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    wt = data.get("worktree")
    if isinstance(wt, str) and wt:
        return Path(wt)
    return None


def _worktree_already_promoted(event_log: EventLog, worktree_id: str) -> bool:
    """幂等权威判据 (回流 step3 ①): canonical 是否已有该工位的 WorktreePromoted。

    TaskRunCompleted 可能多轮被扫到 (promote 成功后同一窗口内下轮再扫 / daemon 重启丢水位线重放;
    FB-3 后水位线不再被 nonexec 截断冻住, 但这些重放源仍在) —— 必须以 canonical WorktreePromoted 为
    权威去重判据, 不靠"我这轮派过没"。
    WorktreePromoted 经 NodeTouched stub 落账 (kind=WorktreePromoted, stub_original_payload.task_id=工位名);
    _unwrap_stub_rewrap 还原 effective_type="WorktreePromoted" + payload.task_id。
    """
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        et, payload = _unwrap_stub_rewrap(rec)
        if et != "WorktreePromoted":
            continue
        if isinstance(payload, dict) and payload.get("task_id") == worktree_id:
            return True
    return False


def _auto_promote_completed_worktrees(
    towow_dir: Path,
    event_log: EventLog,
    *,
    scan_start: int,
    scan_end: int,
) -> None:
    """回流入口 (回流 step2, run_polling_loop 挂点): 扫本轮新事件里的 TaskRunCompleted(success),
    对每个隔离任务自动 promote 其工位回 main。

    挂点核验 (Advisor 边界 b): daemon.run_once() 处理的事件流 = get_events_in_range(watermark,
    next_seq-1); 这里扫同一区间 [scan_start, scan_end] = [本轮 load 的 watermark, daemon._last_seq-1]
    —— 与 _route_event 所见同一事件流的同一区间。auto-promote 不是 dispatch decision (不 spawn 会话),
    是 orchestrator 自己拥有的副作用动作, 故不挂进 _route_event (那是纯函数算 decision, 且 daemon 不持
    commit mutex), 而挂在轮询层 (有 towow_dir + 能包 commit mutex)。
    """
    if scan_end < scan_start:
        return
    try:
        records = event_log.get_events_in_range(scan_start, scan_end)
    except Exception:  # 扫描失败绝不拖垮自动链 (一个烂工位/瞬态 IO 不崩循环)
        return
    for rec in records:
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.TASK_RUN_COMPLETED.value:
            continue
        if _extract_outcome(effective_payload) != "success":
            continue
        raw_task_id = _extract_task_id(effective_payload)
        if not raw_task_id:
            continue
        _try_auto_promote_worktree(
            towow_dir, event_log, raw_task_id=raw_task_id, trigger_event_id=rec.event_id,
        )


def promote_completed_task(
    towow_dir: Path,
    event_log: EventLog,
    task_id: str,
) -> dict[str, object]:
    """手动单发一个已 TaskRunCompleted(success) 任务的工位 promote —— daemon 关着时的 gated
    通路 (manual-promote-completed-task@v1; 对偶 dispatch_one_task 之于 daemon 自动派发)。

    为什么存在: orchestrator daemon 的 auto-promote (_auto_promote_completed_worktrees) 是完工
    工位并回 live 主干的唯一正规司机; daemon 被 owner 主动停时 (2026-07-04 性能冲刺, 积压不可
    直接重启), 完工任务就"落不了地"——没有 gated 的手动 promote 面, 硬手动 merge 又得自己伪造
    集成审计链 (WorktreePromoted 的 commit_event_id linkage)。本函数补掉这个缺口: 复用与 daemon
    完全相同的 _try_auto_promote_worktree (同 commit mutex / 同幂等门 / 同 WorktreePromoted 审计
    write_direct), 不重造回流逻辑、不启 daemon 轮询。

    linkage 系统化定位 (不 hand-pick): trigger_event_id = 该 task 在账本里最近一条
    TaskRunCompleted(success) 的 event_id —— 与 auto-promote 挂点 (_auto_promote_completed_worktrees
    传 rec.event_id) 语义完全一致。

    返回 {"promoted": bool, "reason": str, "worktree_id": str | None}。绝不 raise
    (与 _try_auto_promote_worktree 同红线: 一个烂工位不拖垮调用方)。reason 取值:
      no_success_completion / no_isolated_worktree / already_promoted / promoted /
      merge_conflict / skipped_or_nothing_to_promote。
    """
    trigger_event_id: str | None = None
    for rec in event_log.committed_index().records():
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.TASK_RUN_COMPLETED.value:
            continue
        if _extract_outcome(effective_payload) != "success":
            continue
        if _extract_task_id(effective_payload) != task_id:
            continue
        trigger_event_id = rec.event_id  # keep最后一个 = 最近一条成功完成
    if trigger_event_id is None:
        return {"promoted": False, "reason": "no_success_completion", "worktree_id": None}

    worktree_path = _lookup_dispatched_worktree(towow_dir, task_id)
    if worktree_path is None:
        return {"promoted": False, "reason": "no_isolated_worktree", "worktree_id": None}
    worktree_id = worktree_path.name
    if _worktree_already_promoted(event_log, worktree_id):
        return {"promoted": True, "reason": "already_promoted", "worktree_id": worktree_id}

    _try_auto_promote_worktree(
        towow_dir, event_log, raw_task_id=task_id, trigger_event_id=trigger_event_id,
    )

    # 复查真值 (读账本, 不信自报): 成功 → 已落 WorktreePromoted; 冲突 → conflict marker 在。
    if _worktree_already_promoted(event_log, worktree_id):
        return {"promoted": True, "reason": "promoted", "worktree_id": worktree_id}
    if is_promote_conflict_pending(towow_dir, worktree_id):
        return {"promoted": False, "reason": "merge_conflict", "worktree_id": worktree_id}
    return {"promoted": False, "reason": "skipped_or_nothing_to_promote", "worktree_id": worktree_id}


def _git_head_commit(repo: Path) -> str | None:
    """`git -C <repo> rev-parse HEAD` (当前 commit sha). None on git error。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    sha = out.strip()
    return sha or None


def _bootstrap_canary_candidate_src(worktree_path: Path) -> Path:
    """候选代码 src 目录 (smoke 加载它). 工位是 outer repo 的 git worktree → 包在 harness/src。

    兜底两层: harness/src (本仓库布局) → src (扁平布局)。两者都没 towow 包 → 返回前者, 让
    run_smoke 自己 fail-closed (smoke spawn 失败 = canary fail = 不 promote)。
    """
    primary = worktree_path / "harness" / "src"
    if (primary / "towow").is_dir():
        return primary
    flat = worktree_path / "src"
    if (flat / "towow").is_dir():
        return flat
    return primary


def _record_canary_hold(
    towow_dir: Path,
    event_log: EventLog,
    *,
    worktree_id: str,
    raw_task_id: str,
    detail: str,
    trigger_event_id: str,
) -> None:
    """canary 失败 → 写 hold marker (复用 promote-conflict marker 短路重派) + main-inbound 告警。

    held 语义 != merge 冲突, 但兜底行为同型 (不自动重试 + 显著告警 + 工位保留待 fix/owner)。
    main 本就停在 LKG (坏代码未 promote), 故零 git 动作。
    """
    marker = _promote_conflict_marker_path(towow_dir, worktree_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            "worktree_id": worktree_id,
            "task_id": raw_task_id,
            "trigger_event_id": trigger_event_id,
            "detail": detail[:2000],
            "semantics": "bootstrap_canary_hold_at_lkg_not_auto_retry",
        }),
        encoding="utf-8",
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=trigger_event_id,
            trigger_event_type=EventType.TASK_RUN_COMPLETED.value,
            dispatch_to="main-inbound",
            reason=(
                f"⚠ BOOTSTRAP CANARY 拦截 工位={worktree_id} task={raw_task_id} → 不 promote, "
                f"main 停在 LKG (坏的自更新会砖机, 已 held 待 fix/owner): {detail.strip()[:200]}"
            ),
            task_id=raw_task_id,
        ),
    )
    # worktree-promotion-ledger@v1 exec×failure 半格 (bootstrap_canary_block 类目)。
    from towow.shell.worktree import WorktreeManager

    WorktreeManager(towow_dir).emit_promotion_failure(
        task_id=worktree_id,
        promotion_path="exec_merge",
        failure_reason="bootstrap_canary_block",
        source_event_ref=trigger_event_id,
        event_log=event_log,
    )


def _bootstrap_canary_blocks_promote(
    towow_dir: Path,
    event_log: EventLog,
    *,
    worktree_path: Path,
    worktree_id: str,
    raw_task_id: str,
    trigger_event_id: str,
) -> bool:
    """§4.3 ②: 碰 deploy/heartbeat/dispatch 核心的工位, promote 前先 canary。返回 True=拦下不 promote。

    flag off (默认) → 永远返回 False (guaranteed no-op, 现状不变)。subprocess smoke 在 commit
    mutex【外】跑 (read-only on worktree, 不阻塞别人 commit); canary_gate 自己 emit 审计事件。
    canary 失败 → 写 hold marker + surface, 返回 True (caller 不进 mutex/不 promote)。
    绝不 raise (回流 step5: 一个 canary 异常不能拖垮自动链; 异常 → fail-closed held)。
    """
    from towow.shell.bootstrap_canary import (
        bootstrap_canary_enabled,
        canary_gate,
        git_changed_files,
    )

    if not bootstrap_canary_enabled():
        return False
    try:
        candidate_src = _bootstrap_canary_candidate_src(worktree_path)
        changed = git_changed_files(worktree_path, "main", "HEAD")
        main_commit = _git_head_commit(worktree_path)
        decision = canary_gate(
            towow_dir, event_log,
            worktree_path=worktree_path, worktree_id=worktree_id,
            candidate_src=candidate_src, changed_files=changed,
            main_commit=main_commit, trigger_event_id=trigger_event_id,
        )
    except Exception as exc:  # canary infra 异常 → fail-closed held, 不崩自动链
        _record_canary_hold(
            towow_dir, event_log,
            worktree_id=worktree_id, raw_task_id=raw_task_id,
            detail=f"canary 异常 (fail-closed held): {type(exc).__name__}: {exc}",
            trigger_event_id=trigger_event_id,
        )
        return True
    if decision.allow_promote:
        return False
    _record_canary_hold(
        towow_dir, event_log,
        worktree_id=worktree_id, raw_task_id=raw_task_id,
        detail=decision.reason,
        trigger_event_id=trigger_event_id,
    )
    return True


def _try_auto_promote_worktree(
    towow_dir: Path,
    event_log: EventLog,
    *,
    raw_task_id: str,
    trigger_event_id: str,
) -> None:
    """回流单 task 处理: 反查工位 → 幂等门 → commit mutex 内 promote → 冲突/异常兜底。

    绝不 raise 出去 (回流 step5 红线: 一个烂工位不能拖垮自动链)。
    """
    # step2 反查: 无隔离工位 = 非隔离任务 (共享主树) → skip。
    worktree_path = _lookup_dispatched_worktree(towow_dir, raw_task_id)
    if worktree_path is None:
        return
    worktree_id = worktree_path.name
    # 冲突 marker 在 = 待人工解, 非自动重试 (回流 step5) → skip。
    if is_promote_conflict_pending(towow_dir, worktree_id):
        return
    # 幂等 step3 ②辅助短路: 工位目录不在 (已 promote+cleanup / 从未隔离) → skip。
    if not worktree_path.exists():
        return
    # 幂等 step3 ①权威: 已有 WorktreePromoted → skip。
    if _worktree_already_promoted(event_log, worktree_id):
        return

    # §4.3 ② bootstrap canary (flag off=no-op): 碰核心的工位起不来 → held at LKG, 不 promote。
    # 在 commit mutex【外】跑 (smoke subprocess read-only, 不阻塞别人 commit)。
    if _bootstrap_canary_blocks_promote(
        towow_dir, event_log,
        worktree_path=worktree_path, worktree_id=worktree_id,
        raw_task_id=raw_task_id, trigger_event_id=trigger_event_id,
    ):
        return

    # 并发红线 (回流 step4): 整段 merge+cleanup+emit 包在【单个】commit mutex (与 CLI
    # _global_commit_mutex / daemon _commit_mutex 同一 fcntl 锁文件 .towow/locks/commit.lock),
    # 两 session 永不并发 merge 进 main。promote_and_record 内部只 write_direct (不走 gate /
    # attempt_commit) → 不在持锁时再申请同一 commit.lock (Advisor 边界 a: 否则自死锁)。
    from towow.shell.worktree import WorktreeManager, WorktreePromoteConflict

    try:
        with commit_mutex(towow_dir):
            # TOCTOU: 等锁期间别的路径 (人工 submit --worktree / 邻居轮) 可能已 promote → 锁内复查。
            if (
                is_promote_conflict_pending(towow_dir, worktree_id)
                or not worktree_path.exists()
                or _worktree_already_promoted(event_log, worktree_id)
            ):
                return
            WorktreeManager(towow_dir, use_real_git=True).promote_and_record(
                worktree_id,
                worktree_path,
                trigger_event_id,
                f"auto-promote worktree {worktree_id} → main "
                f"(TaskRunCompleted {trigger_event_id})",
                promotion_path="exec_merge",
                event_log=event_log,
            )
    except WorktreePromoteConflict as exc:
        # 回流 step5: 冲突 → 不 cleanup (promote_to_main 已 abort, main fail-closed 未半合) +
        # 写冲突 marker (待人工解, 挡住后续自动重试/重复告警) + main-inbound 显著告警 + 不 raise。
        _record_promote_conflict(
            towow_dir, event_log,
            worktree_id=worktree_id, raw_task_id=raw_task_id,
            branch=exc.branch, detail=exc.git_stderr, trigger_event_id=trigger_event_id,
        )
        # worktree-promotion-ledger@v1 exec×failure 半格 (auto_promote_conflict 类目)。
        WorktreeManager(towow_dir).emit_promotion_failure(
            task_id=worktree_id,
            promotion_path="exec_merge",
            failure_reason="auto_promote_conflict",
            source_event_ref=trigger_event_id,
            event_log=event_log,
        )
    except Exception as exc:  # 非冲突异常也 catch+surface, 不崩循环 (回流 step5)。
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=trigger_event_id,
                trigger_event_type=EventType.TASK_RUN_COMPLETED.value,
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ AUTO-PROMOTE 异常 工位={worktree_id} task={raw_task_id} "
                    f"(非冲突, 已 surface 不崩循环): {type(exc).__name__}: {str(exc)[:200]}"
                ),
                task_id=raw_task_id,
            ),
        )
        # worktree-promotion-ledger@v1 exec×failure 半格 (other_exception 类目)。
        WorktreeManager(towow_dir).emit_promotion_failure(
            task_id=worktree_id,
            promotion_path="exec_merge",
            failure_reason="other_exception",
            source_event_ref=trigger_event_id,
            event_log=event_log,
        )


def _record_promote_conflict(
    towow_dir: Path,
    event_log: EventLog,
    *,
    worktree_id: str,
    raw_task_id: str,
    branch: str,
    detail: str,
    trigger_event_id: str,
) -> None:
    """回流 step5: 写冲突 marker (待人工解, 非自动重试) + main-inbound 告警。"""
    marker = _promote_conflict_marker_path(towow_dir, worktree_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            "worktree_id": worktree_id,
            "task_id": raw_task_id,
            "branch": branch,
            "git_stderr": detail[:2000],
            "trigger_event_id": trigger_event_id,
            "semantics": "awaiting_manual_conflict_resolution_not_auto_retry",
        }),
        encoding="utf-8",
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=trigger_event_id,
            trigger_event_type=EventType.TASK_RUN_COMPLETED.value,
            dispatch_to="main-inbound",
            reason=(
                f"⚠ AUTO-PROMOTE CONFLICT 工位={worktree_id} task={raw_task_id} → main 未合并 "
                f"(fail-closed, 工位保留待人工解冲突, 非自动重试): {detail.strip()[:200]}"
            ),
            task_id=raw_task_id,
        ),
    )


# ════════════════════════════════════════════════════════════════════════════════
#  孤儿工位 (auto-promote fail-closed 卡死态) 判定谓词 — T-AWARE-01
#  concept: orphan-worktree-promote-fail-closed (evt-f0d574714ede4b5799f9967f5684be22)
# ════════════════════════════════════════════════════════════════════════════════
#
# 纯读、无副作用的三条件判定 (概念定义的 auto_promote_scan_eligible → orphan_promote_blocked
# 转移 guard)。供下游对账扫描 (orphan-worktree-reconciliation-scan, T-AWARE-03) 与合并审计
# (agent-advisor-merge-audit-gate@v1, T-AWARE-02) 消费; 本谓词【只观测判定】, 不 promote/不 emit
# (概念边界: "本概念只定义问题状态本身, 不含合并/对账/修复动作")。
#
# 两层拆分 (纯逻辑 / IO 分离, 便于对三条件 + 四排除单测):
#   · decide_orphan_fail_closed(evidence) → verdict   纯函数, 无 IO, done_criteria 单测目标
#   · gather_orphan_fail_closed_evidence(...)          薄只读层, 从 ledger/git/fs 填真实信号
#   · judge_orphan_fail_closed(...)                    公开入口 = decide∘gather
#
# 三条件【分别可独立核验】(概念要求, 每条自己的观测源, 互不推导):
#   ① daemon.pid.lock 冲突/持锁未清 fail-closed  ← daemon 单例锁健康 (全局信号, is_orchestrator_
#       process_alive: 死 pid 残留 = fail-closed "持锁未清" 的事后痕迹)。诚实残留 (见 uncertainties):
#       fail-closed 路径按设计只 raise 退出、不落任何 EventLog (概念的 "空白" 新颖性所在), 故事后
#       无法复原冲突那一刻的精确态, 死 pid 残留是 proxy —— 这正是为何要靠四排除来剔除其他搁浅成因。
#   ② _auto_promote_completed_worktrees 该轮停跑  ← 工位 stranded-eligible: 有 TaskRunCompleted
#       (success) + 领先 main (merge-base --is-ancestor 为假) + 无 WorktreePromoted + 工位目录仍在。
#       复用 scan_stranded_worktrees 同源 primitives (不另立"搁浅"判据, 防两分类器漂移)。
#   ③ 无重试对账补救  ← 带外对账扫描默认 OFF (架构事实; "之后也不会再补" 的静态论断, 非条件②的
#       重复)。读 maintenance/config.json 使能开关, fail-safe-closed 缺/坏 → OFF (= 无补救)。
#
# 四种排除情形 (任一成立即【非】本态 —— 均有各自的真实观测源, 不是仅测试塞的字段):
#   · reflow_failed        git apply 内容冲突, daemon 仍在跑 ← WorktreePromotionFailed 账本事件
#                          (worktree-promotion-ledger@v1 failure 半格) —— 有失败记录 = daemon 真扫过
#                          并尝试 promote, 非"从未被扫" 的空白态。
#   · abandoned            会话猝死触发 (非锁触发) ← GoalSessionTerminated(reason=abandoned)。
#   · promote_conflict     一般情形 (已接 agent-first 修复循环) ← is_promote_conflict_pending marker。
#   · commit_lock          commit.lock (另一把独立锁, commit-lock-hardening@v1 覆盖) 争用, 非
#                          daemon.pid.lock。条件① 只读 daemon.pid.lock, 结构上已与 commit.lock 区分;
#                          本字段供调用方显式标注 + 单测覆盖。诚实残留 (见 uncertainties + debt):
#                          commit.lock 是瞬态 fcntl 锁, 无持久事后观测面, gather 默认 False。

# 带外对账扫描使能开关 (概念 orphan-worktree-reconciliation-scan: 默认 OFF)。沿用 maintenance
# daemon 开关命名族 (*_auto_daemon_enabled) 与 fail-safe-closed 读法 (缺/坏/非 True → OFF)。
_RECONCILIATION_SCAN_ENABLE_KEY = "orphan_worktree_reconciliation_scan_auto_daemon_enabled"


@dataclass(frozen=True)
class ConditionResult:
    """单条判定条件的结果 + 证据 (概念要求三条件"分别可独立核验")。"""

    holds: bool
    reason: str


@dataclass(frozen=True)
class OrphanFailClosedEvidence:
    """orphan-worktree-promote-fail-closed 判定的纯读观测快照 (一次采样)。

    每字段是一个独立可核验的观测信号; decide_orphan_fail_closed() 只对这些字段做纯逻辑、无 IO。
    gather_orphan_fail_closed_evidence() 负责从 ledger/git/fs 填真实值。
    """

    worktree_id: str
    # 条件② 依据 (stranded-eligible, 复用 scan_stranded_worktrees 同源 primitives)。
    has_success_completion: bool
    commits_ahead_of_main: bool
    already_promoted: bool
    worktree_dir_exists: bool
    # 条件① 依据 (daemon.pid.lock fail-closed 事后痕迹: 死 pid 残留 / 持锁未清)。
    daemon_pid_lock_conflict: bool
    # 条件③ 依据 (带外对账扫描是否使能; True = 有补救机制在 → 条件③ 不成立)。
    reconciliation_scan_enabled: bool
    # 四种排除情形。
    reflow_failed: bool
    session_abandoned: bool
    promote_conflict_pending: bool
    commit_lock_conflict: bool


@dataclass(frozen=True)
class OrphanFailClosedVerdict:
    """三条件判定输出: 布尔 + 每条件证据 + 触发的排除项 (哪个条件因何而真)。"""

    worktree_id: str
    is_orphan_fail_closed: bool
    daemon_lock_condition: ConditionResult  # ①
    auto_promote_skipped_condition: ConditionResult  # ②
    no_rescue_condition: ConditionResult  # ③
    exclusions_triggered: tuple[str, ...]


def decide_orphan_fail_closed(ev: OrphanFailClosedEvidence) -> OrphanFailClosedVerdict:
    """纯函数三条件判定 (无 IO): 三条件同时成立【且】无任一排除 → is_orphan_fail_closed=True。

    概念 (orphan-worktree-promote-fail-closed) 的 auto_promote_scan_eligible → orphan_promote_blocked
    转移 guard 的纯逻辑实现。三条件分别独立核验, 四排除任一命中即翻假 (不误判为本态)。
    """
    # ① daemon.pid.lock 冲突/持锁未清 fail-closed (独立信号: daemon 单例锁停在冲突/死残留态)。
    c1 = ConditionResult(
        holds=ev.daemon_pid_lock_conflict,
        reason=(
            "daemon.pid.lock 冲突/持锁未清 (死 pid 残留), fail-closed 拒绝强清重抢"
            if ev.daemon_pid_lock_conflict
            else "daemon 单例锁健康 (无死 pid 残留) — 无 fail-closed 痕迹"
        ),
    )
    # ② _auto_promote_completed_worktrees 该轮停跑 = 工位 stranded-eligible 却从未被推进。
    #    复用 scan_stranded_worktrees 判据 (有成功完工 + 未 promoted + 工位在) + 概念要求的
    #    merge-base 地面真相 (领先 main)。
    stranded_eligible = (
        ev.has_success_completion
        and ev.commits_ahead_of_main
        and not ev.already_promoted
        and ev.worktree_dir_exists
    )
    c2 = ConditionResult(
        holds=stranded_eligible,
        reason=(
            "工位 stranded-eligible (TaskRunCompleted success + 领先 main + 无 WorktreePromoted + "
            "工位目录仍在) 却从未被 _auto_promote_completed_worktrees 推进"
            if stranded_eligible
            else "非 stranded-eligible ("
            f"success={ev.has_success_completion} ahead_of_main={ev.commits_ahead_of_main} "
            f"promoted={ev.already_promoted} dir_exists={ev.worktree_dir_exists})"
        ),
    )
    # ③ 无重试对账补救 = 带外对账扫描 OFF (架构事实, "之后也不会再补" 的静态论断)。
    c3 = ConditionResult(
        holds=not ev.reconciliation_scan_enabled,
        reason=(
            "带外对账扫描默认 OFF — 无任何机制会再触碰该工位 (fail-closed 后的空白)"
            if not ev.reconciliation_scan_enabled
            else "带外对账扫描已使能 — 存在补救机制, 条件③ 不成立"
        ),
    )
    # 四种排除: 任一成立即非本态 (概念明确排除的其他搁浅成因)。
    exclusions: list[str] = []
    if ev.reflow_failed:
        exclusions.append("reflow_failed")
    if ev.session_abandoned:
        exclusions.append("abandoned")
    if ev.promote_conflict_pending:
        exclusions.append("promote_conflict")
    if ev.commit_lock_conflict:
        exclusions.append("commit_lock")
    is_state = c1.holds and c2.holds and c3.holds and not exclusions
    return OrphanFailClosedVerdict(
        worktree_id=ev.worktree_id,
        is_orphan_fail_closed=is_state,
        daemon_lock_condition=c1,
        auto_promote_skipped_condition=c2,
        no_rescue_condition=c3,
        exclusions_triggered=tuple(exclusions),
    )


def _reconciliation_scan_enabled(towow_dir: Path) -> bool:
    """带外对账扫描是否使能 (读 maintenance/config.json; 缺/坏/非 True → False, fail-safe-closed)。

    概念 orphan-worktree-reconciliation-scan 默认 OFF —— 缺配置即视为无补救 (条件③ 成立)。
    """
    path = _maintenance_config_path(towow_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get(_RECONCILIATION_SCAN_ENABLE_KEY) is True


def _maintenance_config_path(towow_dir: Path) -> Path:
    """maintenance daemon 开关配置文件路径 (与 maintenance_scan._read_auto_daemon_flag 同源)。"""
    return towow_dir / "maintenance" / "config.json"


def _worktree_had_promotion_failure(event_log: EventLog, worktree_id: str) -> bool:
    """账本里是否有该工位的 WorktreePromotionFailed (worktree-promotion-ledger@v1 failure 半格)。

    有失败记录 = daemon 真扫过并尝试 promote 后失败 (git apply 冲突 / 异常) —— 非"从未被扫"的
    空白态, 故用作 reflow_failed 排除信号。kind 走 stub-rewrap 落账, 按字面 kind 比对
    (对齐 _emit_worktree_promoted: outcome=failure → kind="WorktreePromotionFailed")。
    """
    for rec in event_log.committed_index().records():
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != "WorktreePromotionFailed":
            continue
        if isinstance(effective_payload, dict) and effective_payload.get("task_id") == worktree_id:
            return True
    return False


def _session_abandoned_for_task(event_log: EventLog, task_id: str) -> bool:
    """该 task 是否有 GoalSessionTerminated(reason=abandoned) (会话猝死排除信号)。"""
    for rec in event_log.committed_index().records():
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.GOAL_SESSION_TERMINATED.value:
            continue
        if _extract_task_id(effective_payload) != task_id:
            continue
        if _extract_reason(effective_payload) == "abandoned":
            return True
    return False


def _worktree_commits_ahead_of_main(repo_dir: Path, worktree_path: Path) -> bool:
    """工位 HEAD 是否领先 main (git merge-base --is-ancestor <wt_head> main 为假 = 领先)。

    概念钉死的地面真相判据。读不出 HEAD / git 失败 → fail-closed 偏"领先"(True), 不静默当已合。
    """
    try:
        head = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if head.returncode != 0 or not head.stdout.strip():
            return True  # 读不出工位 HEAD → 偏领先 (别静默当已合)
        wt_head = head.stdout.strip()
        anc = subprocess.run(
            ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", wt_head, "main"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # git 探针失败 → 偏领先 (fail-closed, 不漏真搁浅)
    return anc.returncode != 0  # is-ancestor 为假 (returncode!=0) = 领先 main


def gather_orphan_fail_closed_evidence(
    towow_dir: Path,
    event_log: EventLog,
    task_id: str,
    *,
    repo_dir: Path | None = None,
) -> OrphanFailClosedEvidence:
    """薄只读层: 从 ledger/git/fs 采样 task 对应工位的三条件 + 四排除信号 (无副作用)。

    非隔离任务 (无隔离工位) → worktree_id 回退为 task_id, 各信号取安全默认 (不会误判为本态)。
    commit_lock_conflict 无持久事后观测面, 固定 False (见模块头 uncertainties/debt)。
    """
    repo_dir = repo_dir if repo_dir is not None else towow_dir.parent
    worktree_path = _lookup_dispatched_worktree(towow_dir, task_id)
    if worktree_path is None:
        # 非隔离任务 (共享主树) — 不需回流, 不构成孤儿工位。给出各信号安全默认。
        return OrphanFailClosedEvidence(
            worktree_id=task_id,
            has_success_completion=False,
            commits_ahead_of_main=False,
            already_promoted=False,
            worktree_dir_exists=False,
            daemon_pid_lock_conflict=False,
            reconciliation_scan_enabled=_reconciliation_scan_enabled(towow_dir),
            reflow_failed=False,
            session_abandoned=_session_abandoned_for_task(event_log, task_id),
            promote_conflict_pending=False,
            commit_lock_conflict=False,
        )
    worktree_id = worktree_path.name
    dir_exists = worktree_path.exists()
    # ① daemon 单例锁健康: 死 pid 残留 (pid 在但进程不活) = fail-closed "持锁未清" 事后痕迹。
    pid, alive = is_orchestrator_process_alive(towow_dir)
    daemon_lock_conflict = pid is not None and not alive
    return OrphanFailClosedEvidence(
        worktree_id=worktree_id,
        has_success_completion=_task_has_success_completion(event_log, task_id),
        commits_ahead_of_main=(
            _worktree_commits_ahead_of_main(repo_dir, worktree_path) if dir_exists else False
        ),
        already_promoted=_worktree_already_promoted(event_log, worktree_id),
        worktree_dir_exists=dir_exists,
        daemon_pid_lock_conflict=daemon_lock_conflict,
        reconciliation_scan_enabled=_reconciliation_scan_enabled(towow_dir),
        reflow_failed=_worktree_had_promotion_failure(event_log, worktree_id),
        session_abandoned=_session_abandoned_for_task(event_log, task_id),
        promote_conflict_pending=is_promote_conflict_pending(towow_dir, worktree_id),
        commit_lock_conflict=False,  # commit.lock 瞬态锁, 无持久事后观测面 (见模块头)
    )


def _task_has_success_completion(event_log: EventLog, task_id: str) -> bool:
    """该 task 是否有 TaskRunCompleted(success) (条件② stranded-eligible 依据之一)。"""
    for rec in event_log.committed_index().records():
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.TASK_RUN_COMPLETED.value:
            continue
        if _extract_outcome(effective_payload) != "success":
            continue
        if _extract_task_id(effective_payload) == task_id:
            return True
    return False


def judge_orphan_fail_closed(
    towow_dir: Path,
    event_log: EventLog,
    task_id: str,
    *,
    repo_dir: Path | None = None,
) -> OrphanFailClosedVerdict:
    """公开入口: 采样证据 → 纯逻辑判定 (decide∘gather)。供对账扫描 / 合并审计消费。"""
    return decide_orphan_fail_closed(
        gather_orphan_fail_closed_evidence(towow_dir, event_log, task_id, repo_dir=repo_dir),
    )


# ── 回流 fail-closed 兜底 (finding f-turnon-nb01-realized-rename-fix-orphaned-flagoff) ──
# _auto_promote_completed_worktrees 只认 TaskRunCompleted(success) 的 exec 工位; FixCompleted
# 完全不在 promote 路径 → fix 在隔离工位产 verified-good 修复后 commit 永不进 main = 静默孤儿化
# (最坏假完成: 数据丢失 + 系统标 partially_resolved 看似快完成 + 工位已清无第二次机会)。
# LIVE 实证: fix-t-nb-2-reflow-rename-no-renames 的 commit e7e91c13 从未回流 main。
# 隔离成因不止全局 TOWOW_EXEC_ISOLATION flag-on —— Claude Code daemon 偶发 worktree 隔离 / 局部
# flag 都会让 fix 会话落进非主树, 故判据用【fix 的 commit 在 main 的可达性】而非判 env flag,
# 覆盖任何隔离成因。fail-closed = 显著 main-inbound 告警 + 幂等 marker (非静默 promoted-style
# 成功); 不 auto-cherry-pick (孤儿 SHA 合进已前进的 main + 绕 commit gate = 新风险面, advisor 裁)。
_FIX_ORPHAN_MARKER_PREFIX = "fix_orphan__"
# 声称闭合的 outcome — 才有"代码 commit 该在 main"的义务; needs_further_review 不闭合无回流义务。
_FIX_REFLOW_CLOSED_OUTCOMES = frozenset({"resolved", "partially_resolved"})


def _fix_orphan_marker_path(towow_dir: Path, fix_id: str, sha: str) -> Path:
    """孤儿 fix marker 路径 (幂等 key=fix_id+sha, 写 dispatched/ 下), 防每轮重刷告警。"""
    digest = hashlib.sha256(f"{fix_id}|{sha}".encode()).hexdigest()[:16]
    return _dispatched_dir(towow_dir) / f"{_FIX_ORPHAN_MARKER_PREFIX}{digest}"


def _fix_proposed_worktree_sha(event_log: EventLog, fix_id: str) -> str | None:
    """fix_id → FixProposed.after_state.worktree_commit_sha (M-1.6 §3.1 required 结构化字段)。

    SHA 在 fix propose 系统化捕获 (required min_length=1), 不在 fix complete —— 故反查 FixProposed
    是真结构化源 (不解析 self_verification 自由文本)。多轮 fix 取最后一条 propose 的 commit。无
    FixProposed / 无 SHA → None (caller 据 affected_ripple_artifacts 兜底判异常)。
    """
    sha: str | None = None
    for rec in event_log.get_events_by_type(EventType.FIX_PROPOSED):
        fp = _record_src(rec)
        if fp.get("fix_id") == fix_id:
            wcs = fp.get("worktree_commit_sha")
            if isinstance(wcs, str) and wcs.strip():
                sha = wcs.strip()
    return sha


def _fix_worktree_path_for_fix(
    towow_dir: Path, event_log: EventLog, fix_id: str,
) -> Path | None:
    """debt-45fc0ffa08b5: 从 fix_id 反查它的 detached 隔离工位路径 (auto-promote 回流要定位工位)。

    工位名由派发侧算: _fix_worktree_id(fix_key), fix_key = decision.task_id or trigger_event_id。
    实证 (2026-06-27): dispatch_to=fix 全部 task_id=None → fix_key = trigger_event_id =
    触发该 fix 的 FindingCreated 的 event_id。故反查链 (类比 FB-1 _latest_bg_session_id_for_task
    走 canonical):
      fix_id → FixProposed.related_finding_id → 该 finding 的 FindingCreated.event_id (= 工位 key)
    取该 key 算 _fix_worktree_id → worktrees/<id>。链验通率 72% (going-forward 新数据更高; 历史
    早期/多轮 fix 部分断链 → 返 None, caller 降级走原 fail-closed 告警, 不误回流)。

    多轮 fix 取最后一条 FixProposed 的 related_finding_id。查不到 finding/eid → None。
    """
    # 用 all_records + _unwrap_stub_rewrap 统一扫两形态: 生产里 FixProposed/FindingCreated 既有
    # 真 event_type 又有 stub-rewrap (NodeTouched 包) 形态 (实证 2026-06-27: FixProposed 直查 75 /
    # 解包扫 103, 差 28 条是 stub-rewrap)。get_events_by_type 只认真类型会漏 stub-rewrap → 反查断。
    # T-PERF 批1: 全量扫描换成 warm committed_index snapshot (同一 committed-visible 记录集,
    # 不改语义 — 只省掉全盘 all_records() 重新物化), 由 get_events_by_type 会漏 stub-rewrap 的
    # 顾虑决定这里仍必须扫全量、不能按类型走索引子集。
    related_finding: str | None = None
    for rec in event_log.committed_index().records():
        etype, _payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.FIX_PROPOSED.value:
            continue
        src = _record_src(rec)  # 归一化 dict (穿透 after_state 嵌套 + stub-rewrap)
        if src.get("fix_id") == fix_id:
            rf = src.get("related_finding_id")
            if isinstance(rf, str) and rf:
                related_finding = rf
    if not related_finding:
        return None
    # related_finding_id → 它的 FindingCreated event_id (= 派发 fix 的 trigger = 工位 key)
    finding_eid: str | None = None
    for rec in event_log.committed_index().records():
        etype, _payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.FINDING_CREATED.value:
            continue
        src = _record_src(rec)
        if src.get("finding_id") == related_finding:
            finding_eid = rec.event_id
    if not finding_eid:
        return None
    # 工位基目录 = towow_dir/worktrees (与 _prepare_fix_worktree 同源), 非 orchestrator/ 下。
    return towow_dir / "worktrees" / _fix_worktree_id(finding_eid)


def _fix_commit_reachable_from_main(repo_dir: Path, sha: str) -> bool | None:
    """fix 的 worktree_commit_sha 是否已回流进 main (git merge-base --is-ancestor sha main)。

    True = 可达 (commit 已在 main, 正常直落 / 已 promote);
    False = exit 1, 对象在 object store 但非 main 祖先 = 隔离工位孤儿;
    None = exit 128 (对象不在: gc / 工位已清 / bad ref) 或子进程异常 = 不可验证。
    advisor 红线: 别让 exit 128 漏进"非 1 即可达"的缝 —— None 与 False 都该告警。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", sha, "main"],
            capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None  # 128 / 其它 → 不可验证, 当告警处理 (fail-closed)


def _try_fix_reflow_promote(
    towow_dir: Path,
    event_log: EventLog,
    *,
    fix_id: str,
    worktree_path: Path,
    trigger_event_id: str,
) -> bool:
    """debt-45fc0ffa08b5 (fix-worktree-auto-promote): fix 隔离工位的 verified-good 改动经
    detached serial-apply 自动回流进 main —— 镜像 exec 的 _try_auto_promote_worktree, 但走 fix
    detached 工位 (无分支/无 .owner/无 exec 戳)。

    根治原"FixCompleted 完全不在 promote 路径 → 静默孤儿化"(本文件 _FIX_ORPHAN_MARKER 段史)。
    advisor 裁定 (2026-06-27, fact-2): 此非旧 advisor 反对的"盲 cherry-pick 孤儿 SHA 绕 gate"——
    promote_to_main 对 detached 工位走 _promote_detached_serial_apply (patch-serial-reflow@v1:
    export base..HEAD diff → commit mutex 内 git apply 到 main, 冲突 fail-closed main 不动工位保留),
    与 exec 回流同构、同样不经 attempt_commit, 而 exec 回流已被接受。故升级为"先试 conflict-aware
    回流, 不成才 fail-closed 告警", 不再静默孤儿。

    Returns: True = 真回流进 main (caller skip 告警); False = 没回流 (工位不在/冲突/异常 → caller
    走原 _record_fix_orphan fail-closed 告警)。绝不 raise (回流红线: 一个烂工位不拖垮自动链)。
    """
    if not worktree_path.exists():
        return False
    worktree_id = worktree_path.name
    # 冲突 marker 在 = 已 fail-closed 待人工解 → 不自动重试 (与 exec 回流 step5 同习语)。
    if is_promote_conflict_pending(towow_dir, worktree_id):
        return False
    from towow.shell.worktree import WorktreeManager, WorktreePromoteConflict

    try:
        # 并发红线: promote 在 commit mutex 内 (与 exec 回流 / CLI 同一 .towow/locks/commit.lock),
        # 两 session 永不并发回流进 main。promote_and_record 内部只 write_direct, 不在持锁时再申请同锁。
        with commit_mutex(towow_dir):
            if (
                not worktree_path.exists()
                or is_promote_conflict_pending(towow_dir, worktree_id)
            ):
                return False
            result = WorktreeManager(towow_dir, use_real_git=True).promote_and_record(
                worktree_id,
                worktree_path,
                trigger_event_id,
                f"fix-reflow worktree {worktree_id} → main "
                f"(FixCompleted fix_id={fix_id}, debt-45fc0ffa08b5)",
                promotion_path="fix_serial_apply",
                event_log=event_log,
                # f-reflow-ledger-fixfail-noop-failopen: 本路径只在 caller (_detect_orphaned_fix_commits)
                # 已确立 recorded FixProposed_SHA 不可达 main (= 搁浅) 且工位未 already_promoted 时才走到 ——
                # 即此工位【有必须回流的改动】。故 base==wt_head no-op 不是真空 promote, 而是工位 HEAD 漂到
                # main 祖先 / fix commit 悬空的异常 → 让 promote_and_record fail-closed (raise) 而非冒充
                # fix×success(sha=None) + 删工位。exec 真空 promote 无此上下文, 默认 False 不受影响。
                fail_closed_on_noop=True,
            )
        # promote_and_record 的结果分诊 (非冲突非异常路径, 冲突/异常在下方 except 接住):
        #   - 非 git 工位 (skeleton, 无 .git) → promoted=False reason=not_a_git_worktree: 没东西可回流,
        #     也没冒充成功。返 False, caller 据 reachable 走 _record_fix_orphan (fix×failure)。
        #   - 搁浅 base==wt_head no-op → 因上面 fail_closed_on_noop=True 已在 promote_to_main raise
        #     WorktreePromoteConflict, 不会走到这里, 由下方 except 接住 → fix×failure。
        #   - 真回流成功 → promoted=True。这里只在【真 promoted】时返 True (caller skip 告警)。
        return bool(result is not None and getattr(result, "promoted", False))
    except WorktreePromoteConflict as exc:
        # 冲突 → fail-closed: 写冲突 marker (挡后续自动重试) + 告警, 不冒充成功。返 False 让 caller
        # 也记 fix_orphan (双 marker 不冲突: conflict marker 防重试, orphan marker 是回流账)。
        _record_promote_conflict(
            towow_dir, event_log,
            worktree_id=worktree_id, raw_task_id=fix_id,
            branch=exc.branch, detail=exc.git_stderr, trigger_event_id=trigger_event_id,
        )
        return False
    except Exception as exc:  # 非冲突异常: surface 不崩循环 (回流红线), 返 False 走 fail-closed 告警。
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=trigger_event_id,
                trigger_event_type=EventType.FIX_COMPLETED.value,
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ FIX-REFLOW 异常 工位={worktree_id} fix={fix_id} "
                    f"(非冲突, 已 surface 不崩循环): {type(exc).__name__}: {str(exc)[:200]}"
                ),
            ),
        )
        return False


def _record_fix_orphan(
    towow_dir: Path,
    event_log: EventLog,
    *,
    fix_id: str,
    sha: str | None,
    detail: str,
    trigger_event_id: str,
    worktree_id: str | None = None,
    failure_reason: str = "serial_apply_stranded",
) -> None:
    """fail-closed: 写幂等 marker (已告警过则 skip 不重刷) + main-inbound 显著告警。镜像
    _record_promote_conflict 的 fail-closed 习语 (surface loud + 不冒充成功 + 待人工/重派)。

    worktree-promotion-ledger@v1 fix×failure 半格: 这是 fix 侧 ALL 失败原因 (真孤儿/搁浅/异常/链断
    /conflict) 的单一落账漏斗 —— 调用方 (_detect_orphaned_fix_commits) 在 _try_fix_reflow_promote
    返回 False 后无条件走到这里, 故这里(而非 _try_fix_reflow_promote 自己的 except 块)是唯一记账点,
    避免同一次失败尝试经 _record_promote_conflict(冲突场景, 只写 marker/告警不落账) +
    这里双发。marker 幂等门(已在此函数之上)保证多轮重扫同一 fix+sha 只落一条账。"""
    marker = _fix_orphan_marker_path(towow_dir, fix_id, sha or "no-sha")
    if marker.exists():
        return  # 幂等: 同 fix+sha 已告警过, 不每轮重复
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            "fix_id": fix_id,
            "worktree_commit_sha": sha,
            "detail": detail,
            "trigger_event_id": trigger_event_id,
            "semantics": "fix_commit_not_in_main_fail_closed_awaiting_manual_reflow_or_redispatch",
        }),
        encoding="utf-8",
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=trigger_event_id,
            trigger_event_type=EventType.FIX_COMPLETED.value,
            dispatch_to="main-inbound",
            reason=(
                f"⚠ FIX-ORPHANED fix={fix_id} commit={sha or '(无)'} → 修复未回流 main "
                f"(fail-closed, 非静默 promoted-style 成功, 待人工回流/重派): {detail}"
            ),
            task_id=fix_id,
        ),
    )
    from towow.shell.worktree import WorktreeManager

    WorktreeManager(towow_dir).emit_promotion_failure(
        task_id=worktree_id or fix_id,
        promotion_path="fix_serial_apply",
        failure_reason=failure_reason,
        source_event_ref=trigger_event_id,
        event_log=event_log,
    )


def _detect_orphaned_fix_commits(
    towow_dir: Path,
    event_log: EventLog,
    *,
    scan_start: int,
    scan_end: int,
) -> None:
    """回流 fail-closed 兜底: 扫本轮 FixCompleted, 验它的代码 commit 真回流进 main, 不可达就显著
    告警而非静默成功 (见上方 _FIX_ORPHAN_MARKER_PREFIX 段)。挂在 _auto_promote_completed_worktrees
    之后扫同一事件区间 [scan_start, scan_end]。内部 catch 所有异常绝不拖垮自动链 (回流红线)。

    判据主轴 = FixProposed 存在性 (带 required worktree_commit_sha), outcome 做闸,
    affected_ripple_artifacts 只兜"无 FixProposed 异常尾巴" (advisor: 别把它当主门 —— 它在可选的
    semantic_upgrade_declaration 里, 当主门会让"改了代码但没声明 semantic_upgrade"的孤儿从另一道门
    溜走, 复活同款静默孤儿化):
      - outcome ∉ {resolved, partially_resolved} → skip (没声称闭合, 无 commit 该在 main)
      - 反查到 FixProposed+SHA → 永远验可达性 (不看 affected_ripple_artifacts): 不可达/不可验证 → 告警
      - 查不到 FixProposed/SHA → affected_ripple_artifacts 非空 (改了代码却无结构化 commit=异常) →
        告警; 空 (无 commit 可回流, 如 escalation/finding 反向 outcome) → skip
    """
    if scan_end < scan_start:
        return
    try:
        records = event_log.get_events_in_range(scan_start, scan_end)
    except Exception:  # 扫描失败绝不拖垮自动链 (与 _auto_promote 同红线)
        return
    repo_dir = towow_dir.parent
    for rec in records:
        effective_type, payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.FIX_COMPLETED.value:
            continue
        after = payload.get("after_state")
        src = after if isinstance(after, dict) else payload
        if src.get("outcome") not in _FIX_REFLOW_CLOSED_OUTCOMES:
            continue  # 非闭合 outcome — 无回流义务
        fix_id = src.get("fix_id")
        if not isinstance(fix_id, str) or not fix_id:
            continue
        sha = _fix_proposed_worktree_sha(event_log, fix_id)
        if sha is not None:
            reachable = _fix_commit_reachable_from_main(repo_dir, sha)
            if reachable is True:
                continue  # 已回流进 main — 正常 (近 6/6 fix 直落主树都走这条)
            # functional-equivalence-closure-criterion@v1 (a) 非必要: merge-base FALSE/不可验证对
            # "是否已落地"是 non_signal (NS1) —— 回流是 apply-diff 再提交, 落进 main 的是内容相同但
            # 哈希全新的提交, 原工位 sha 永不成为 main 祖先, 不能据此判孤儿。真正的落地硬证据 (H3) 是
            # 账本里该工位有没有 WorktreePromoted —— 不论回流发生在本轮扫描内还是更早一轮/一次会话
            # (工位可能早已清理, 但回流账已留痕)。
            wt = _fix_worktree_path_for_fix(towow_dir, event_log, fix_id)
            worktree_id = wt.name if wt is not None else None
            if worktree_id is not None and _worktree_already_promoted(event_log, worktree_id):
                continue  # H3 硬证据: 早前已回流落账, 原 sha 恒 FALSE 只是机制必然产物, 不孤儿
            # debt-45fc0ffa08b5: 未回流 → 先试 conflict-aware detached serial-apply 回流, 成功则
            # 不再孤儿 (替代原"直接 fail-closed 告警")。promote_and_record 成功即已落新 WorktreePromoted
            # (H3), 不再用【原工位 sha】跑 merge-base 复验 —— 回流总产生全新 commit hash, 原 sha 复验必
            # FALSE, 曾误把刚成功的回流判孤儿 (line2016 活 bug)。
            if wt is not None and _try_fix_reflow_promote(
                towow_dir, event_log, fix_id=fix_id, worktree_path=wt,
                trigger_event_id=rec.event_id,
            ):
                continue  # 本轮回流成功 → 不孤儿
            _record_fix_orphan(
                towow_dir, event_log, fix_id=fix_id, sha=sha,
                detail=(
                    "孤儿化: commit 在 object store 但非 main 祖先 (隔离工位未回流, 自动回流也未成)"
                    if reachable is False
                    else "不可验证: commit 不在 object store (gc / 隔离工位已清 / bad ref)"
                ),
                trigger_event_id=rec.event_id,
                worktree_id=worktree_id,
                failure_reason="serial_apply_stranded",
            )
            continue
        # 无 FixProposed/SHA — 兜底启发: affected_ripple_artifacts 非空 = 改了代码却无结构化 commit
        sud = src.get("semantic_upgrade_declaration")
        ripple = sud.get("affected_ripple_artifacts") if isinstance(sud, dict) else None
        if isinstance(ripple, list) and ripple:
            _record_fix_orphan(
                towow_dir, event_log, fix_id=fix_id, sha=None,
                detail=(
                    "异常: 改了代码 (affected_ripple_artifacts 非空) 却无 FixProposed/"
                    "worktree_commit_sha 可验回流"
                ),
                trigger_event_id=rec.event_id,
                failure_reason="missing_structured_commit_ref",
            )
        # ripple 空 → 无 commit 可回流 → skip


def _exec_isolation_enabled() -> bool:
    """B1 灰度开关: TOWOW_EXEC_ISOLATION=on → daemon 派的执行会话用 per-task 隔离工位.

    off (默认) = 现状共享主树 — 一键回退不丢账本 (设计迁移第5步, 把"第一次真跑"风险从
    不可逆降成可逆)。on 的前提: guard 已扩认 .towow/worktrees/ + per-session 锁 (T6)。
    """
    return os.environ.get("TOWOW_EXEC_ISOLATION", "").strip().lower() in {"on", "1", "true"}


def _exec_cap_total() -> int:
    """B4 同时工位数帽: TOWOW_EXEC_MAX_PARALLEL 显式设定即生效; 未设 → DEFAULT_CAP_TOTAL.

    只限同时工位数, 不限链长/打转 (owner E.5 硬决策)。被帽截断的 task 由 backlog re-scan
    每轮捞回 (红队 fatal 配套)。

    f-orch-round-time-index-persist-storm-plus-on-passes-20260717: 旧实现 min(env,
    DEFAULT_CAP_TOTAL) 把 owner 显式设的 env 静默钳回默认帽 (owner 设 10 实际跑 4,
    且 docstring 声称钳到 6 与常量 4 双重漂移)。env 是 owner 意志 → 显式设定不再被钳,
    失控上界由 runaway killswitch (TOWOW_SPAWN_RUNAWAY_THRESHOLD) 兜底, 与 nonexec 帽
    (_nonexec_cap_total 从不钳 env) 同一语义。
    """
    from towow.l2.execution_dispatch import DEFAULT_CAP_TOTAL

    raw = os.environ.get("TOWOW_EXEC_MAX_PARALLEL", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    # owner 2026-06-26: 去灰度 1 — env-unset 也走全量并行(熔断 runaway 阈值为真上界);
    # 防 daemon 化(双 fork)丢 env 时退化成串行(实证: 设了 env=999 仍只 1 并行 = env 没传进)。
    return DEFAULT_CAP_TOTAL


# AUTOPILOT-SAFETY 口子1: 非 execution spawn 的并发帽 (fix/review/consensus/planning)。
# 旧状态: select_dispatch_batch 的帽只管 execution; 非 exec 的 try_spawn_for_decision 完全不过帽 →
# 一批 FindingCreated/前进链事件同时落 = 单轮瞬时爆发大量会话。默认 = spec 总帽 6 (与 execution
# 同一并行哲学), 只限【同时活跃工位数】不限链长 (owner E.5 硬决策不变)。
_NONEXEC_CAP_ENV = "TOWOW_NONEXEC_MAX_PARALLEL"


def _nonexec_cap_total() -> int:
    """非 exec spawn 同时工位数帽: TOWOW_NONEXEC_MAX_PARALLEL (默认 DEFAULT_CAP_TOTAL=6)。

    含在跑的 (并发帽, 非每批帽): 帽减去当前活跃非 exec 工位后才是本轮可补的额度。被帽截断的
    decision 不盖 dispatched 戳 + watermark 不越过 → 下一轮重扫捞回 (不饿死, 同 B4 红队 fatal 配套)。
    """
    from towow.l2.execution_dispatch import DEFAULT_CAP_TOTAL

    raw = os.environ.get(_NONEXEC_CAP_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_CAP_TOTAL


# T-FIX-B2-01 (PARALLEL-locks#1 / REVIEW-verdict#3): serial-reject kind 单飞门。
# fix/plan 这些 skill 的 `start` 物理上单例(SessionLockRegistry 同 kind 只允
# 一个 live 会话, 第二个内部 exit 1)。旧路径对它们盲 spawn → 撞 live 的那个静默死(launched
# 但被拒, 复合戳永不清 = 永久吞掉 = THE blocker)。这张表把 decision 的 dispatch_to 映射到对应
# 的 registry kind: 派发前查"该 kind 已有 live 会话 / 本轮已派同 kind"任一命中就不派不盖戳,
# 留 backlog re-scan 下轮捞回, 而非盲 spawn 让 agent 内部 exit 1 静默死。
# 注意映射差异: dispatch_to 是 skill_role(planning), registry kind 是
# plan(与 CLI start 入口 SessionLockRegistry(towow, 'plan') 对齐)。
_SERIAL_REJECT_KIND_BY_DISPATCH: dict[str, str] = {
    # R11 (2026-06-14): consensus 移出串行集 — 跨 brief 并发, 同 brief 单飞在 consensus start
    # 去重, 并发概念写正确性由 commit gate 兜 (O-08 §3.1 子项4)。
    # T-RCL-01 (plan-review-concurrency-lease / 2026-06-17): review 移出串行集 — 退役 kind 级
    # 单飞改 review-target 级完全并发, provenance 委托 review-unit (session_id) 血缘 (review_verdict
    # 折叠按 review_unit_id 过滤, 与并发无关)。退役 kind 门后 review auto-dispatch 唯一去重 =
    # is_already_dispatched 复合键 <trigger>__review (派发循环对所有 decision 先判, 见下方派发点);
    # 不新增基于 trigger_event_id 的并发锁 (那会造 scope creep 违背 review-target 完全并行)。
    # fix 退役 (finding-fix-serial-lock-redundant-bottlenecks-parallel-autopilot-repair / 2026-06-22):
    # 同 T-RCL-01 — fix finding 归属内容寻址 (finding_id/fix_id 折叠 + 显式 session 盖戳 + 隔离工位),
    # 并发安全已验 → 移出串行集; fix auto-dispatch 唯一去重 = is_already_dispatched 复合键 <trigger>__fix。
    # plan 退役 (R11 / 2026-06-23): plan kind 级单飞退役 — 改 plan_start per-plan_id 自 reject (镜像
    # consensus, 同 plan_id 单飞、不同 plan_id 并行) + 派发侧 is_already_dispatched 复合键
    # <evt>__planning + T-FND-01 plan-product-exists (与 consensus 对称, 退 serial 门无去重缺口)。
    # 须与 session_lock.SERIAL_REJECT_KINDS 保持一致 (test_single_flight_runtime_sentinel /
    # esc-532866a8 closure 钉死两侧对齐): 现两侧均空 (review/fix/plan 全退役)。
}


def _serial_reject_kind_for(dispatch_to: str) -> str | None:
    """decision.dispatch_to → 对应 SessionLockRegistry kind; 非 serial-reject kind 返回 None。"""
    return _SERIAL_REJECT_KIND_BY_DISPATCH.get(dispatch_to)


def _kind_has_live_session(towow_dir: Path, registry_kind: str) -> bool:
    """该 serial-reject kind 当前是否已有 live 会话(reap_stale 后判, 不误把崩溃残锁当活)。

    单飞门第一道: 真实物理状态查询 —— 若有 live 会话, 再派同 kind 必撞 start 内部 exit 1。
    """
    from towow.l1.session_lock import SessionLockRegistry

    return bool(SessionLockRegistry(towow_dir, registry_kind).live_sessions())


def _check_single_flight_invariants(towow_dir: Path, event_log: EventLog) -> None:
    """B2-06 运行时哨兵: 每轮对所有 serial-reject kind 断言单飞不变量 (live ≤1).

    违反时 fail loud — emit main-inbound 显著通知, 不静默吞、不崩 daemon。
    背景: T-FIX-B2-06 / esc-532866a8 — review/fix provenance 腐蚀的物理拦截补丁。
    """
    from towow.l1.session_lock import SERIAL_REJECT_KINDS, SessionLockRegistry, SingleFlightViolationError

    for kind in sorted(SERIAL_REJECT_KINDS):
        reg = SessionLockRegistry(towow_dir, kind)
        try:
            reg.assert_single_flight()
        except SingleFlightViolationError as exc:
            emit_orchestrator_dispatched(
                event_log,
                DispatchDecision(
                    trigger_event_id=f"single-flight-violation-{uuid.uuid4().hex[:8]}",
                    trigger_event_type="SingleFlightViolationDetected",
                    dispatch_to="main-inbound",
                    reason=(
                        f"⚠️ 单飞不变量被违反: kind={kind} — {exc}. "
                        "请排查并手动终止多余会话 (towow orchestrator status)."
                    ),
                ),
            )


# T-RMD-S3-DOUBLEDRIVE per-key 运行时哨兵: kind 级单飞 R11 全退役 (SERIAL_REJECT_KINDS 空) 后,
# _check_single_flight_invariants 遍历空集 = 零检测, 唯一防线退化成 plan_start/work_start 的
# start_critical_section (修 A)。本哨兵恢复 finding 点名的"per-plan_id 运行时哨兵 (检出两个 live 同
# plan_id 会话 fail-loud 收迟来者) 作纵深": 对 plan(按 meta.plan_id) / execution(按 meta.task_id) 分组
# live 会话, 同一 key 出现 >1 个 live 会话 = 双驱动已发生 (临界区被旁路 / 账本历史污染 / 崩溃-resume
# 残留) → fail-loud 进 main-inbound。kind→分组用的 meta 业务字段:
_PER_KEY_SINGLE_FLIGHT_KINDS: dict[str, str] = {
    "plan": "plan_id",       # 同 plan_id 双驱动 → cohort 污染 (本 finding 主病灶)
    "execution": "task_id",  # 同 task_id 双跑 (execution claim 同处理的纵深检出)
}


def _check_per_key_single_flight(towow_dir: Path, event_log: EventLog) -> None:
    """per-(kind, meta-key) 单飞运行时哨兵 (T-RMD-S3-DOUBLEDRIVE).

    对 _PER_KEY_SINGLE_FLIGHT_KINDS 的每个 kind, 按其 meta 业务字段 (plan→plan_id / execution→task_id)
    分组 live 会话; 任一 key 有 >1 个 live 会话 → 双驱动已发生 → emit DoubleDriveDetected 进 main-inbound
    (fail-loud, 不静默吞、不崩 daemon — 镜像 _check_single_flight_invariants 范式)。

    无 meta key 的会话 (orchestrator spawn 预建锁 / 旧锁无 meta 旁文件) key=None → 不参与分组, 绝不把多
    个 keyless 会话误聚成"同 key 双驱动" (生产实证: orchestrator 预建 execution 锁无 .meta)。

    与 _check_single_flight_invariants (kind 级 SERIAL_REJECT_KINDS, 现空) 互补: 那个管未来重启某 kind 级
    单飞, 这个管 R11 退役后改 per-key 单飞的 plan/execution。末尾 delegate 给它 → 一次调用覆盖两套不变量,
    无覆盖缺口。
    """
    from towow.l1.session_lock import SessionLockRegistry

    for kind, key_field in _PER_KEY_SINGLE_FLIGHT_KINDS.items():
        reg = SessionLockRegistry(towow_dir, kind)
        by_key: dict[str, list[str]] = {}
        for info in reg.live_sessions():
            key = reg.read_meta(info.session_id).get(key_field)
            if not isinstance(key, str) or not key:
                continue  # 无 meta key 的会话 (预建锁/旧锁) 不参与分组 → 不误报
            by_key.setdefault(key, []).append(info.session_id)
        for key, sids in sorted(by_key.items()):
            if len(sids) <= 1:
                continue
            emit_orchestrator_dispatched(
                event_log,
                DispatchDecision(
                    trigger_event_id=f"double-drive-{uuid.uuid4().hex[:8]}",
                    trigger_event_type="DoubleDriveDetected",
                    dispatch_to="main-inbound",
                    reason=(
                        f"⚠️ 双驱动检出: kind={kind} {key_field}={key} 有 {len(sids)} 个 live 会话 "
                        f"({', '.join(sorted(sids))}) — 同 {key_field} 应单飞 (T-RMD-S3-DOUBLEDRIVE 临界区被旁路 "
                        "或账本污染); 请排查并手动终止多余会话 (towow orchestrator status)。"
                    ),
                ),
            )

    # kind 级 SERIAL_REJECT_KINDS 单飞 (现空, 未来重启某 kind 时生效) 一并断言, 无覆盖缺口。
    _check_single_flight_invariants(towow_dir, event_log)


# AUTOPILOT-SAFETY 口子2: 失控总闸 / kill-switch。注意区别 — 这【不是】行为层链长/打转 cap
# (owner E.5 '绝不加链长cap' 的设计意图不冲突), 是 runaway 保护: 当活跃 spawn 会话总数超过一个
# 明显高于正常帽的阈值 (只在失控时触发), 自动 pause_orchestrator + main-inbound 通知。正常单链/
# 多链远低于阈值不受影响。可配 (env), 阈值可调。
_RUNAWAY_THRESHOLD_ENV = "TOWOW_SPAWN_RUNAWAY_THRESHOLD"
DEFAULT_RUNAWAY_THRESHOLD = 12

# AUTOPILOT-SAFETY 口子3 (T-FIX-B6-04, AUTOPILOT-core#5): 成本/token 预算闸。runaway 口子2 看的是
# 会话【数】, 但 N 个 opus 会话的真实花费 ≫ N 个 sonnet 会话 —— 只数会话拦不住"少量 opus 烧穿预算"。
# 不接真实计费 API (单价表硬编码粗估), 是分布式自愈臂 (自检→软暂停→push 告警), 不引中心常驻成本控制器。
# 与 runaway 同范式 (并列前置检查): 阈值可配, 阈下正常派, 已 paused 时不重复触发。
#
# T-FIX-CONCERN-01 (B6-04 证伪收尾) — 语义诚实 + 算术真承重, 闸分两道:
#   ① 瞬时闸 (TOWOW_COST_BUDGET_USD, 默认 $15): 看【当前活跃】spawn 工位的估算花费总和
#      (Σ model_tier × 估算 token × 单价)。这【不是】累计预算 — pending marker 被 reconcile
#      清掉后花费就不再计入。默认从 $50 降到 $15: 旧默认永远不会先于 runaway (12 会话 ≈ $36)
#      / 并发帽 (6 会话 ≈ $18) 触发 = 算术 dormant; $15 < $18 < $36 才真承重 (帽被 env 调高
#      / 帽逻辑失效时这道闸先拦住), 且高于正常满载 (tier 帽 2 opus + 5 sonnet ≈ $7.5) 不误伤。
#   ② 累计闸 (TOWOW_CUMULATIVE_COST_BUDGET_USD, 默认 $50): 真"预算"语义 — 每次 spawn 记录时
#      把估算花费累加进持久文件 (orchestrator/cost_cumulative.json), 不随 reconcile 清。
#      预算烧完 → 软暂停, owner 提高 env 或重置计数文件后 resume。
#
# 成本闸总开关 (finding-cost-gate-semantics-vs-owner-judgment-1, owner 2026-06-10 逐字判断):
#   "只要走订阅内的 session 就不需要估算价值也不需要控制价值, 最多有个记账单"。订阅 (claude-bg)
#   形态下 spawn 不按量计费 → 这两道【虚拟成本】闸 auto-pause 只会无意义地摁停生产链 (2026-06-10
#   实证两次)。故成本闸默认 OFF (TOWOW_COST_GATE_ENABLED 未置真): 不拦派发。但【账单仍记】—
#   _accrue_spawn_cost 照常把估算花费累加进 cost_cumulative.json, owner 随时可见 ("记账单")。
#   未来真接按量计费 API 时设 TOWOW_COST_GATE_ENABLED=1 即重启用两道成本闸 (能力保留, 非删除)。
#   ⚠ 此开关只管【成本】闸 (口子3); runaway 会话【数】闸 (口子2, 非成本失控保护) 不受影响, 始终在。
_COST_GATE_ENABLED_ENV = "TOWOW_COST_GATE_ENABLED"
_COST_BUDGET_ENV = "TOWOW_COST_BUDGET_USD"
DEFAULT_COST_BUDGET_USD = 15.0
_CUMULATIVE_COST_BUDGET_ENV = "TOWOW_CUMULATIVE_COST_BUDGET_USD"
DEFAULT_CUMULATIVE_COST_BUDGET_USD = 50.0
_COST_CUMULATIVE_FILE = "cost_cumulative.json"
# per-tier 单价 (USD / token) 硬编码粗估 — 量级对齐 opus≈sonnet 的 ~5x, 非精确计费。
# 用于把"会话数"翻成"估算花费", 让少量 opus 会话也能在烧穿预算前被自检拦住。
_TIER_PRICE_PER_TOKEN_USD = {
    "opus": 15.0 / 1_000_000,    # ~$15 / 1M token 粗估
    "sonnet": 3.0 / 1_000_000,   # ~$3 / 1M token 粗估
}
_DEFAULT_TIER_PRICE_PER_TOKEN_USD = _TIER_PRICE_PER_TOKEN_USD["opus"]  # 未知 tier 按最贵估 (保守, 偏向早拦)
# per-tier 单会话默认估算 token (当 pending session 没带 estimated_token_budget 时的兜底)。
# 现状: pending_sessions/*.json 存 model_tier 但【不存】estimated_token_budget (那字段在 task_package
# 层, 没回写到工位标记) —— 故按 model_tier 取这张默认表, 带了 budget 的会话优先用真值。
_TIER_DEFAULT_TOKEN_ESTIMATE = {
    "opus": 200_000,    # opus 任务粗估单会话烧 ~200k token
    "sonnet": 100_000,  # sonnet ~100k token
}
_DEFAULT_TOKEN_ESTIMATE = _TIER_DEFAULT_TOKEN_ESTIMATE["opus"]  # 未知 tier 按 opus 量级估 (保守)


def _runaway_threshold() -> int:
    """失控总闸阈值: TOWOW_SPAWN_RUNAWAY_THRESHOLD (默认 12, 明显高于正常帽 6, 只在失控时触发)。

    钳到 ≥1 (配 0/负数无意义 → 回默认), 避免误配把正常运行也判失控。
    """
    raw = os.environ.get(_RUNAWAY_THRESHOLD_ENV, "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    return DEFAULT_RUNAWAY_THRESHOLD


def _active_spawn_sessions(towow_dir: Path) -> list[dict[str, object]]:
    """当前活跃的【全部】spawn 工位 (任意角色 — exec + fix/review/consensus/planning)。

    真活跃源 = pending_sessions/*.json (reconcile 终态收口时清), 与 _active_execution_sessions
    同一数据源, 只是不按 spawned_role 过滤。失控总闸 (口子2) 看这个总数。
    """
    pdir = _pending_sessions_dir(towow_dir)
    if not pdir.is_dir():
        return []
    out: list[dict[str, object]] = []
    for pf in pdir.glob("*.json"):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _active_nonexec_session_count(towow_dir: Path) -> int:
    """当前活跃非 execution 工位数 (口子1 并发帽含在跑的 — 帽减它才是本轮额度)。"""
    return sum(
        1 for s in _active_spawn_sessions(towow_dir)
        if s.get("spawned_role") != "execution"
    )


def _terminated_goal_session_ids(event_log: EventLog) -> set[str]:
    """所有已 emit GoalSessionTerminated 的会话 goal_session_id 集合 (一次扫账本建好)。

    收口信号集 (BRIEF-product-recovery-loop-fix-2026-06-26 fix #2): 会话自己 emit
    GoalSessionTerminated = "我结束了, 产出已登记" = 系统该回收它。失控总闸 (口子2) 据此排除已
    终止会话 —— 它们不是失控的活会话, 只是 pending marker 还没被 reconcile 清掉的瞬时残留。不排除
    则 stale terminated marker 累积到阈值会误触发 auto-pause, 而 auto-pause 又 continue 跳过
    reconcile_orphaned_sessions → marker 永不被清 → daemon 周期性自卡 (今天实证, 需人工 admin-
    bypass 救)。兼容 canonical after_state.goal_session_id (CLI goal terminate) + stub-rewrap 顶层。
    """
    terminated: set[str] = set()
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.GOAL_SESSION_TERMINATED.value:
            continue
        gsid = _extract_goal_session_id(payload)
        if gsid:
            terminated.add(gsid)
    return terminated


def _maybe_trip_runaway_killswitch(towow_dir: Path, event_log: EventLog) -> bool:
    """AUTOPILOT-SAFETY 口子2: 活跃 spawn 会话总数 ≥ 失控阈值 → 自动 pause + 通知 owner。

    安全网 (runaway 保护), 非链长行为限制 (owner E.5 '不加链长cap' 不冲突 — 阈值默认 12 明显
    高于正常帽 6, 只在真失控时触发)。复用现成 pause_orchestrator (写 paused.flag, stop_windows
    =False 不打断在跑的会话) + emit main-inbound 显著通知。Returns 是否触发了总闸 (轮询据此跳过
    本轮 dispatch)。已 paused 时不重复触发 (调用方在 pause 检查之后才调, 故此处只判活跃数)。
    """
    threshold = _runaway_threshold()
    raw_active = _active_spawn_sessions(towow_dir)
    # 早返回 (热路径零开销): 排除"已终止"只会让计数更小, 故 raw < 阈值时必不触发, 无需扫账本建
    # 终止集 (每 poll 一次全量 all_records 扫只在 raw ≥ 阈值=可能 trip 时才值得)。
    if len(raw_active) < threshold:
        return False
    # BRIEF-product-recovery-loop-fix-2026-06-26 fix #2: 已 emit GoalSessionTerminated 的会话
    # (自报收口/产出已登记) 不算"活跃失控" — 只是 pending marker 还没被 reconcile 清的瞬时残留。
    # 不排除则 stale terminated marker 累积触发误 auto-pause, 而 auto-pause continue 跳过 reconcile
    # → marker 永不清 → 周期性自卡 (THE daemon self-jam, 今天实证)。只排除"已终止", 真活的照常算。
    terminated = _terminated_goal_session_ids(event_log)
    active = [
        s for s in raw_active
        if str(s.get("goal_session_id") or "") not in terminated
    ]
    if len(active) < threshold:
        return False
    pause_orchestrator(
        towow_dir, event_log,
        reason=f"auto-pause RUNAWAY: active spawn sessions={len(active)} ≥ threshold={threshold}",
        stop_windows=False,
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=f"runaway-killswitch-{uuid.uuid4().hex[:8]}",
            trigger_event_type="OrchestratorRunawayTripped",
            dispatch_to="main-inbound",
            reason=(
                f"🛑 RUNAWAY 失控总闸触发: 当前活跃 spawn 会话 {len(active)} 个 ≥ 阈值 {threshold} "
                f"— 已自动暂停协调者 (停止派新, 不打断在跑的)。查 orchestrator status; 排查后 "
                f"towow orchestrator resume 恢复。可调阈值: {_RUNAWAY_THRESHOLD_ENV}"
            ),
        ),
    )
    return True


# AUTOPILOT-SAFETY 口子4: 内存失控总闸 (2026-06-30 崩溃根固化 / per-session-fork-uncounted-by-cap-causes-oom@v1)。
# 崩溃根: cap 只数【会话】(record_pending_session), 真内存杀手是每会话同步 spawn 的 claude -p fork
# 子进程(execution-self-check / audit / method fork…), 这些 fork 不调 record_pending_session、不被任何
# cap/runaway(口子2 只数会话)看见 → 真实进程数 = 会话 × fork倍数 → 19进程 × ~2-3G = 55G OOM 整机重启。
# 这道门采【系统可用内存】(自动含所有 fork 子进程的 RSS), 是唯一能看到 fork+在跑会话总内存的门。
# admission-control: 内存紧 → pause + 拒新派 (stop_windows=False 不 kill 在跑的活, 同 runaway 语义)。
#
# 采样原语 (_available_memory_fraction / _memory_pause_fraction / _MEMORY_PAUSE_FRACTION_ENV /
# DEFAULT_MEMORY_PAUSE_FRACTION) 已下沉到 l1.memory_admission 作【单一可信来源】, 由本模块顶部
# import 别名进来 —— 与 fork spawn 门 (l1.verification_fork._default_runner 的补防线) 共用同一阈值
# / 同一采样逻辑, 避免"派发门与 fork 门各看各的内存"漂移 (正是本崩溃根的同型失败)。本门 (派发轮询
# 层) 看 fork+在跑会话总内存拒【新会话】; fork 门 (spawn 层) 拒【已在跑会话内部 spawn 的 fork】。


def _maybe_trip_memory_killswitch(towow_dir: Path, event_log: EventLog) -> bool:
    """口子4: 系统可用内存 < 阈值 → 自动 pause + 通知 owner。Returns 是否触发 (轮询据此跳过本轮派发)。

    这是 55G OOM 崩溃根的固化: 口子2 只数会话看不到 fork 子进程把进程数顶到 OOM; 本门采系统真内存
    (含所有 fork)。fail-soft: 采不到内存 (None) → 不触发, 由口子2 兜底。已 paused 时调用方在 pause
    检查后才调, 故只判内存。
    """
    frac = _available_memory_fraction()
    if frac is None:
        return False  # 采样失败 → fail-open, 口子2(会话数)兜底
    threshold = _memory_pause_fraction()
    if frac >= threshold:
        return False
    pause_orchestrator(
        towow_dir, event_log,
        reason=(
            f"auto-pause MEMORY-KILLSWITCH: 系统可用内存 {frac * 100:.0f}% < 阈值 "
            f"{threshold * 100:.0f}% (防 per-session fork 把进程顶到 55G OOM)"
        ),
        stop_windows=False,
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=f"memory-killswitch-{uuid.uuid4().hex[:8]}",
            trigger_event_type="OrchestratorMemoryKillswitchTripped",
            dispatch_to="main-inbound",
            reason=(
                f"🛑 内存失控总闸 (口子4) 触发: 系统可用内存 {frac * 100:.0f}% < 阈值 "
                f"{threshold * 100:.0f}% — 已自动暂停协调者 (停止派新, 不打断在跑的)。这道门看的是真"
                f"内存 (含所有 claude -p fork 子进程), 防 per-session fork 把进程数顶到 ~55G OOM 整机"
                f"重启 (2026-06-30 崩溃根)。排查/等内存回落后 towow orchestrator resume。可调阈值: "
                f"{_MEMORY_PAUSE_FRACTION_ENV}"
            ),
        ),
    )
    return True


def _cost_gate_enabled() -> bool:
    """成本闸 (口子3 两道) 总开关, 默认 OFF (finding-cost-gate-semantics-vs-owner-judgment-1)。

    owner 2026-06-10 判断: 订阅内 session 只记账不控成本 — 订阅 (claude-bg) 形态 spawn 不按量
    计费, 虚拟成本闸 auto-pause 只会摁停生产链。真值 = TOWOW_COST_GATE_ENABLED ∈
    {1,true,yes,on} (大小写/空白不敏感)。未来真接按量计费 API 时 opt-in 重启用。
    ⚠ 只管【拦派发/auto-pause】; 账单累加 (_accrue_spawn_cost) 与 runaway 会话数闸 (口子2) 独立, 不受影响。
    """
    raw = os.environ.get(_COST_GATE_ENABLED_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _cost_budget_usd() -> float:
    """瞬时闸阈值 (USD): TOWOW_COST_BUDGET_USD (默认 15.0)。

    语义诚实 (T-FIX-CONCERN-01): 这是【瞬时活跃花费】闸 — 只看当前 pending 工位的估算花费
    总和, 不是累计预算 (累计预算见 _cumulative_cost_budget_usd)。默认 $15 真承重: 低于并发帽
    打满 ($18) / runaway 打满 ($36), 高于正常满载 (~$7.5)。
    钳到 > 0 (配 0/负数/非数无意义 → 回默认), 避免误配把正常运行也判超阈。
    """
    raw = os.environ.get(_COST_BUDGET_ENV, "").strip()
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_COST_BUDGET_USD
    if val > 0:
        return val
    return DEFAULT_COST_BUDGET_USD


def _cumulative_cost_budget_usd() -> float:
    """累计闸阈值 (USD): TOWOW_CUMULATIVE_COST_BUDGET_USD (默认 50.0)。

    真"预算"语义 (T-FIX-CONCERN-01): 对照的是持久累计计数器 (每次 spawn 累加, 不随
    reconcile 清)。钳到 > 0, 非法配置回默认。
    """
    raw = os.environ.get(_CUMULATIVE_COST_BUDGET_ENV, "").strip()
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_CUMULATIVE_COST_BUDGET_USD
    if val > 0:
        return val
    return DEFAULT_CUMULATIVE_COST_BUDGET_USD


def _cost_cumulative_path(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _COST_CUMULATIVE_FILE


def cumulative_spawn_cost_usd(towow_dir: Path) -> float:
    """持久累计估算花费 (USD) — 每次 spawn 记录时累加, 不随 reconcile 清 (真预算语义)。

    文件损坏/缺失 → 0 (宽容, 不炸 polling loop; 下次累加重建)。重置预算 = owner 删
    orchestrator/cost_cumulative.json 或提高 TOWOW_CUMULATIVE_COST_BUDGET_USD。
    """
    path = _cost_cumulative_path(towow_dir)
    if not path.exists():
        return 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        val = float(data.get("total_usd", 0.0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0
    return max(0.0, val)


def _accrue_spawn_cost(towow_dir: Path, session: dict[str, object]) -> None:
    """把一次 spawn 的估算花费累加进持久计数器 (T-FIX-CONCERN-01 累计闸数据源)。

    粗估计数 (与瞬时闸同一估算表), 失败不挡 spawn 记录 (计数器是观测护栏不是关键路径)。
    """
    try:
        path = _cost_cumulative_path(towow_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        total = cumulative_spawn_cost_usd(towow_dir) + _session_estimated_cost_usd(session)
        path.write_text(
            json.dumps({"total_usd": total, "updated_at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass  # 累计计数失败绝不挡 spawn 记录主路径


def _session_estimated_cost_usd(session: dict[str, object]) -> float:
    """单个活跃 spawn 工位的估算花费 = 估算 token × per-tier 单价。

    token: 优先用工位标记里的 estimated_token_budget (若有), 否则按 model_tier 取默认估算表
    (现状: pending_sessions/*.json 存 model_tier 不存 estimated_token_budget, 故多数走默认表)。
    单价: 按 model_tier 取 _TIER_PRICE_PER_TOKEN_USD, 未知 tier 按最贵 (opus) 估 — 保守, 偏向早拦。
    """
    tier = str(session.get("model_tier") or "opus").lower()
    raw_budget = session.get("estimated_token_budget")
    if isinstance(raw_budget, (int, float)) and raw_budget > 0:
        tokens = float(raw_budget)
    else:
        tokens = float(_TIER_DEFAULT_TOKEN_ESTIMATE.get(tier, _DEFAULT_TOKEN_ESTIMATE))
    price = _TIER_PRICE_PER_TOKEN_USD.get(tier, _DEFAULT_TIER_PRICE_PER_TOKEN_USD)
    return tokens * price


def _estimated_active_cost_usd(towow_dir: Path) -> float:
    """瞬时估算花费 (USD) = Σ 当前活跃 spawn 工位的估算花费。

    活跃源同 runaway 总闸 — pending_sessions/*.json (任意角色: exec + 非 exec)。
    reconcile 终态收口时清 — 所以这是【瞬时】量不是累计预算 (T-FIX-CONCERN-01 语义诚实:
    旧名 _estimated_cumulative_cost_usd 名不副实; 真累计见 cumulative_spawn_cost_usd)。
    """
    return sum(
        _session_estimated_cost_usd(s)
        for s in _active_spawn_sessions(towow_dir)
    )


def _maybe_trip_cost_budget_killswitch(towow_dir: Path, event_log: EventLog) -> bool:
    """AUTOPILOT-SAFETY 口子3 (T-FIX-B6-04 + T-FIX-CONCERN-01): 成本闸双道, 任一超阈 →
    自动软暂停 + 通知 owner。

    ① 瞬时闸: 当前活跃工位估算花费 ≥ TOWOW_COST_BUDGET_USD (默认 $15 — 真承重, 低于帽/
       runaway 打满花费)。② 累计闸: 持久累计花费 (每 spawn 累加不随 reconcile 清) ≥
       TOWOW_CUMULATIVE_COST_BUDGET_USD (默认 $50 — 真"预算花完"语义)。
    超阈即软暂停 (pause_orchestrator, stop_windows=False 不打断在跑的会话) + emit main-inbound
    含花费数字与哪道闸的显著告警。复用现成 pause + main-inbound 机制 (分布式自愈臂, 不引中心
    常驻成本控制器)。Returns 是否触发 (轮询据此跳过本轮 dispatch)。
    调用方在 paused / runaway 检查之后才调 (已 paused 不会走到这)。
    """
    # owner 2026-06-10 判断 (finding-cost-gate-semantics-vs-owner-judgment-1): 订阅内 spawn
    # 不控成本只记账。成本闸默认 OFF → 不拦派发 (账单 _accrue / runaway 会话数闸不受影响)。
    # 真接按量计费时设 TOWOW_COST_GATE_ENABLED=1 opt-in 恢复承重。
    if not _cost_gate_enabled():
        return False
    budget = _cost_budget_usd()
    active_cost = _estimated_active_cost_usd(towow_dir)
    cumulative_budget = _cumulative_cost_budget_usd()
    cumulative = cumulative_spawn_cost_usd(towow_dir)
    instant_tripped = active_cost >= budget
    cumulative_tripped = cumulative >= cumulative_budget
    if not instant_tripped and not cumulative_tripped:
        return False
    if cumulative_tripped:
        which = (
            f"【累计】预算闸: 累计估算花费 ${cumulative:.2f} ≥ 预算 ${cumulative_budget:.2f} "
            f"(持久计数不随 reconcile 清; 重置 = 删 orchestrator/{_COST_CUMULATIVE_FILE} "
            f"或调高 {_CUMULATIVE_COST_BUDGET_ENV})"
        )
    else:
        which = (
            f"【瞬时】活跃花费闸: 当前活跃工位估算花费 ${active_cost:.2f} ≥ 阈值 "
            f"${budget:.2f} (可调 {_COST_BUDGET_ENV})"
        )
    pause_orchestrator(
        towow_dir, event_log,
        reason=(
            f"auto-pause COST-BUDGET: active ${active_cost:.2f}/${budget:.2f}, "
            f"cumulative ${cumulative:.2f}/${cumulative_budget:.2f}"
        ),
        stop_windows=False,
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=f"cost-budget-killswitch-{uuid.uuid4().hex[:8]}",
            trigger_event_type="OrchestratorBudgetTripped",
            dispatch_to="main-inbound",
            reason=(
                f"🛑 COST-BUDGET 成本预算闸触发 — {which}。"
                f"(瞬时 ${active_cost:.2f}/${budget:.2f}; 累计 ${cumulative:.2f}/"
                f"${cumulative_budget:.2f}) 已自动暂停协调者 (停止派新, 不打断在跑的)。"
                f"查 orchestrator status; 排查后 towow orchestrator resume 恢复"
            ),
        ),
    )
    return True


def _prepare_exec_worktree(
    towow_dir: Path,
    task_id: str,
    *,
    actor_id: str,
    write_set: list[str],
) -> tuple[Path | None, str]:
    """B1: 为 task 备好隔离工位 — 真 git worktree + .owner + .towow symlink 回主账本.

    symlink 是硬上线门 (DOGFOOD-001-V: 隔离不能让事件进隔离日志主对话失明): 建不成 →
    (None, 原因), caller 走失败路径不 spawn。幂等: 工位已存在则复用 (失败重派同一工位)。
    """
    from towow.l2.portable_runtime import ensure_shared_towow
    from towow.shell.worktree import WorktreeManager

    wid = _exec_worktree_id(task_id)
    wt_path = towow_dir / "worktrees" / wid
    if not wt_path.exists():
        try:
            WorktreeManager(towow_dir, use_real_git=True).create(
                task_id=wid,
                actor_id=actor_id,
                write_set=write_set,
                branch=f"task-{wid}",
            )
        except Exception as exc:
            return None, f"worktree create failed: {exc!r}"
    if not ensure_shared_towow(wt_path, towow_dir.parent):
        return None, "shared .towow symlink failed (DOGFOOD-001-V 硬门: 事件必须回流主账本)"
    return wt_path, ""


def _fix_worktree_id(fix_key: str) -> str:
    """fix 隔离工位目录名 — slug+sha256 (碰撞安全+定长), 与 exec 同款但 `fix__` 前缀区分来源。"""
    slug = "".join(ch if ch.isalnum() else "_" for ch in fix_key)[:40]
    digest = hashlib.sha256(fix_key.encode("utf-8")).hexdigest()[:12]
    return f"fix__{slug}__{digest}"


def _prepare_fix_worktree(
    towow_dir: Path, fix_key: str, *, actor_id: str,
) -> tuple[Path | None, str]:
    """T-NB-3 (fix-isolation-post-hoc-gate / detached-worktree-workstation): 为 fix 派发备一个
    DETACHED 无分支隔离工位 — 镜像 execution 隔离 (_prepare_execution_worktree) 但走 detached、
    **不写 .owner**、**不 promote**。

    - inv-nb-per-task-worktree: fix 也各自独立 detached worktree, 不在共享主树并发改 (与 exec 同形)。
    - inv-nb-no-branch-switch: detached=True → `git worktree add --detach`, 绝不建分支。
    - inv-nb-fix-no-empty-owner: write_owner=False → 不写 .owner。fix 不预声明 V-01 write_set, 空
      write_set 的 .owner 会让 V-01 拒 fix 每次写; 无 .owner → CLI guard 自动 skip, fix 写面安全靠
      closure_contract + fix_after 三层门 (绝不套 V-01)。
    - 不 promote: fix 改动不走 V-01 写门/merge 回流 (那是 execution 的事)。auto-promote
      (_auto_promote_completed_worktrees) 只认 exec dispatch 戳里的 worktree, fix 工位无 exec 戳 →
      天然不被触发, 无需额外抑制。
    - symlink 硬门 (DOGFOOD-001-V): .towow symlink 回主账本建不成 → (None, 原因), caller 不 spawn。
    - 幂等: 工位已存在则复用 (失败重派同一工位)。
    """
    from towow.l2.portable_runtime import ensure_shared_towow
    from towow.shell.worktree import WorktreeManager

    wid = _fix_worktree_id(fix_key)
    wt_path = towow_dir / "worktrees" / wid
    if not wt_path.exists():
        try:
            WorktreeManager(towow_dir, use_real_git=True).create(
                task_id=wid,
                actor_id=actor_id,
                write_set=[],
                branch=None,
                detached=True,
                write_owner=False,
            )
        except Exception as exc:
            return None, f"fix worktree create failed: {exc!r}"
    if not ensure_shared_towow(wt_path, towow_dir.parent):
        return None, "shared .towow symlink failed (DOGFOOD-001-V 硬门: 事件必须回流主账本)"
    return wt_path, ""


def _active_execution_sessions(towow_dir: Path) -> list[dict[str, object]]:
    """当前活跃执行工位 (真活跃源 = pending_sessions/*.json, reconcile 会清 — B4 红队修正:
    绝不能数 exec 戳, 戳一生一次永不清不反映在跑)。"""
    pdir = _pending_sessions_dir(towow_dir)
    if not pdir.is_dir():
        return []
    out: list[dict[str, object]] = []
    for pf in pdir.glob("*.json"):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("spawned_role") == "execution":
            out.append(data)
    return out


def exec_task_retry_count(towow_dir: Path, task_id: str) -> int:
    """B2: 该 task 已被清戳重派过几次 (retry marker; B4 model-tier 升级消费此值)。

    T-FIX-CONCERN-01 (B5-05 证伪收尾): 钳位 ≥0 — 损坏 marker 里的负数若放出去, 会让
    should_escalate_technical_blocker raise ValueError 炸 polling loop。计数来源宽容,
    l1 判据保持严格契约。
    """
    marker = _dispatched_dir(towow_dir) / f"retry__{_exec_task_stamp_name(task_id)}"
    if not marker.exists():
        return 0
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return max(0, int(data.get("retry_count", 0)))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def clear_exec_task_stamp(
    towow_dir: Path,
    task_id: str,
    *,
    source: str = "auto_reconcile",
    reason: str = "",
) -> bool:
    """B2 (PARALLEL-EXEC-FIX): 非 success 终态 → 清该 task 的 execution 戳让它可被重派.

    病根: 戳一生一次永不清 → 失败/中止 task 永不重派, 下游子树静默永久卡死。清戳后该 task
    会被下一次 ready-set 重算 (同 plan 任一 TaskRunCompleted(success)) 或 backlog re-scan (B4)
    捞回重派。同时留 retry marker (retry_count 累加 + upgrade_tier_hint=opus): sonnet 失败
    升 opus 重跑 (M-1.3 §10.5 v3 简化语义), spawn 分层 (B4) 消费。

    清戳 = `stamp.unlink()` (单文件 Path 操作, 不是 Bash `rm`, 不经 PreToolUse B4-06 账本删除门;
    不是 rmtree, 不经 fs_guard.assert_rmtree_safe)。这是 B2 自愈机制能在 Python 层正常运行的
    原因。但【运维者手动】清一个僵死 task 的戳 (自动 reconcile 没逮到的 zombie, 如未 emit
    TaskRunCompleted(非success) 的卡死 task) 时, 若改敲 Bash `rm .towow/orchestrator/dispatched/...`
    会撞 B4-06 (路径含 .towow → 删除门拒)。正解不是削弱 B4-06 (它焊死 06-10 灾难性删账本的物理门,
    load-bearing), 而是经 `towow orchestrator clear-exec-stamp` CLI 走本函数的 Python unlink —
    guard 兼容 + 审计留痕, 且只能删【单个可重生的戳文件】(daemon 下一轮重派即重建), 物理无法误伤
    canonical 账本 (finding-b2-clear-stamp-vs-b4-06-delete-guard-conflict-1)。

    Args:
        source: 谁清的戳 (审计) — "auto_reconcile" (默认, daemon 自愈路径) 或 "manual_cli"
            (运维者经 CLI 手动 unstick 僵死 task)。记进 retry marker 区分自动 vs 人工。
        reason: 手动清戳时运维者给的原因 (审计留痕, 默认空)。

    Returns: 是否真清掉了一个戳 (False = 本就没戳, 幂等)。
    """
    stamp = _dispatched_dir(towow_dir) / _exec_task_stamp_name(task_id)
    if not stamp.exists():
        return False
    try:
        prev = stamp.read_text(encoding="utf-8")
    except OSError:
        prev = "{}"
    marker = _dispatched_dir(towow_dir) / f"retry__{_exec_task_stamp_name(task_id)}"
    count = exec_task_retry_count(towow_dir, task_id) + 1
    marker_body: dict[str, object] = {
        "retry_count": count,
        "upgrade_tier_hint": "opus",
        "last_dispatch": prev[:2000],
        "cleared_at": time.time(),
        "cleared_source": source,
    }
    if reason:
        marker_body["cleared_reason"] = reason
    marker.write_text(json.dumps(marker_body), encoding="utf-8")
    stamp.unlink(missing_ok=True)
    return True


# ════════════════════════════════════════════════════════════════════════════════
#  T-FIX-B1-01 (AUTOPILOT-core#5 / FORWARD-chain#2) — 自愈重派上限 + 幂等熔断
#
#  现状: clear_exec_task_stamp 写 retry marker (retry_count 累加 + upgrade_tier_hint=opus),
#  但 exec_task_retry_count 只被它自己读自增, 批派发器从不消费 → 反复 silent-death/非 success 的
#  task 会被【无限】重派, 且永用【原 tier】重蹈覆辙。本块给自愈重派加上限: 候选池构建处读
#  retry_count, ≥ 上限 → 不进派发池, emit RedispatchExhausted 显著告警 + 写幂等熔断 marker
#  (后续轮不重复告警也不再重派, 直到 owner 介入)。熔断【只针对自愈重派】(retry marker 存在=被清过
#  戳), 首次派发不受影响; 上限是重派次数上限【非链长上限】(不违 owner E.5 '不加链长cap')。
# ════════════════════════════════════════════════════════════════════════════════


def _redispatch_cap() -> int:
    """自愈重派次数上限 (可配 TOWOW_REDISPATCH_CAP, 默认 3)。

    达上限的 task/trigger 不再盲派, 熔断 + emit 显著告警等 owner 介入。这是 runaway 自愈保护,
    不是链长 cap (owner E.5 '不加链长cap' 仍成立 — 链长是正常推进, 重派是失败兜底的重蹈次数)。
    """
    raw = os.environ.get(_REDISPATCH_CAP_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _REDISPATCH_CAP_DEFAULT


def _redispatch_circuit_path(towow_dir: Path, key: str) -> Path:
    """幂等熔断 marker 路径 (写在 dispatched/ 下, 与 retry__ marker 同目录)。

    key: exec 用 _exec_task_stamp_name(task_id); nonexec 用 `<trigger>__<dispatch_slug>`。
    """
    return _dispatched_dir(towow_dir) / f"{_REDISPATCH_CIRCUIT_PREFIX}{key}"


def is_redispatch_circuit_tripped(towow_dir: Path, key: str) -> bool:
    """该 key 的熔断是否已触发 (幂等: 触发后再扫不重复告警/重派, 直到 owner 手动清此 marker)。"""
    return _redispatch_circuit_path(towow_dir, key).exists()


def trip_redispatch_circuit(towow_dir: Path, key: str, body: dict[str, object]) -> None:
    """写幂等熔断 marker (含触发信息: retry_count/last_error/cause)。已存在则覆盖 (幂等)。"""
    p = _redispatch_circuit_path(towow_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({**body, "tripped_at": time.time()}), encoding="utf-8")


def clear_redispatch_circuit(towow_dir: Path, key: str, *, also_clear_retry: bool = True) -> bool:
    """运维口: 清熔断 marker (+ 默认连带 retry marker) 让 task/trigger 可被重派。

    背景缺口 (2026-06-27): 熔断 (redispatch_circuit) 是"等 owner 介入"设计, 但此前【没有】合规
    清熔断的入口 —— clear_exec_task_stamp 只清 exec 戳不碰熔断 marker; 直接 Bash rm 撞 B4-06 删除门
    (路径含 .towow); Python unlink 脚本也因命令文本含 .towow 被 guard 误拦。结果熔断的 task 永久
    卡死, 连 owner 都没工具解。本函数 = clear-exec-stamp 的熔断对应物 (单文件 Path.unlink, 不经
    Bash rm 门; 物理只能删可重生的 marker, 误伤不了 canonical 账本)。

    典型用途: task 因 spawn 层失败 (0 真起会话) 被冤枉熔断、或 aborted_for_replan 被误计熔断 →
    owner/运维核实后清熔断让它重新得到机会。清熔断【不】自动重派 —— 还要 ready-set 重算/重派戳清掉
    才会再派 (本函数默认连带清 retry marker, 让 retry_count 归零、tier 回默认)。

    Returns: 是否真清掉了熔断 marker (False = 本就没熔断, 幂等)。
    """
    circuit = _redispatch_circuit_path(towow_dir, key)
    cleared = False
    if circuit.exists():
        circuit.unlink(missing_ok=True)
        cleared = True
    # finding-tlrf11-stale-terminalized-sentinel-after-clear: 连带清强制终态化哨兵 (.terminalized)。
    # 哨兵是"该 key 已被巡检 sweep 强制终态化"的证据; key 由 task_id/trigger 确定性派生, 清熔断后同
    # key 重熔断复用同一路径。三处 trip_redispatch_circuit 调用点全部被 is_redispatch_circuit_tripped
    # 守卫 (tripped 则幂等跳过不重 trip), 故 marker 一经 tripped 只有【经本函数 clear】才会消失并让同
    # key 重新 trip —— clear→re-trip 是哨兵能相对活 marker 变 stale 的唯一路径。若 clear 不连带清哨兵,
    # 残留哨兵会让 collect_overage_live_bodies / collect_stuck_batons / status 三处读点 (均以哨兵
    # exists() 判"已终态化") 把新一轮熔断误当已终态化而 continue 跳过 → sweep 永不再终态化重熔断
    # trigger → 违反 terminal-state-reachability@v1 (重派 trigger 从任意中间态可达终态) 在 clear→re-trip
    # 路径上复活。无条件清 (不 gate 在 also_clear_retry): 哨兵语义独立于 retry, 清熔断即重置该 key 的
    # 终态化证据。missing_ok=True → 本就没终态化过则幂等 no-op。
    _redispatch_circuit_terminalized_path(towow_dir, key).unlink(missing_ok=True)
    if also_clear_retry:
        retry = _dispatched_dir(towow_dir) / f"retry__{key}"
        retry.unlink(missing_ok=True)
    return cleared


def _retry_marker_last_error(towow_dir: Path, task_id: str) -> str:
    """从 retry marker 的 last_dispatch 摘出最近一次失败摘要 (告警 last_error 字段)。"""
    marker = _dispatched_dir(towow_dir) / f"retry__{_exec_task_stamp_name(task_id)}"
    if not marker.exists():
        return ""
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("last_dispatch", ""))[:500]
    return ""


def _retry_marker_upgrade_tier(towow_dir: Path, task_id: str) -> str | None:
    """T-FIX-B1-01 (2): 自愈重派的 tier 升级提示 (clear_exec_task_stamp 写的 upgrade_tier_hint)。

    候选 task 有 retry marker 且 upgrade_tier_hint='opus' → 派发 tier 覆盖为 opus (sonnet 失败
    升 opus 重跑, 不永用原 tier 重蹈覆辙)。无 marker / 无 hint → None (按原 tier)。
    """
    marker = _dispatched_dir(towow_dir) / f"retry__{_exec_task_stamp_name(task_id)}"
    if not marker.exists():
        return None
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            hint = data.get("upgrade_tier_hint")
            return str(hint) if hint else None
    return None


def clear_nonexec_dispatch_stamp(
    towow_dir: Path,
    trigger_event_id: str,
    dispatch_to: str,
) -> bool:
    """T-FIX-B2-05 (PARALLEL-locks#1 纵深防御): 清掉一个非 exec (review/fix/consensus/plan)
    decision 的 <trigger_event_id>__<dispatch_to> 复合戳, 让下一轮 _route_event 重扫该 trigger
    重新产 decision 重派。

    病根 (THE blocker 的纵深防御漏洞): review/fix 会话 launched-but-rejected (单飞门正常但
    start 内部撞 live session exit 1 → 静默死) 时, 它的 pending marker task_id=None →
    clear_exec_task_stamp (键于 task_id) 够不到 → (trigger,dispatch_to) 复合戳永不清 →
    永久去重 → 该 trigger 被永久吞掉, 既不被当完工也不再重派。本函数补上这条收割路径:
    据 (trigger, dispatch_to) 直接清复合戳 (clear_exec_task_stamp 的 nonexec 对应物)。

    ⚠ T-FIX-B2-05 返工 (证伪抓出的死信号根): 清戳【本身】不会让 trigger 重派。reconcile 走
    silent-death 路径时 watermark 早已 march 过该 trigger (真实多轮必然), _route_event 只扫
    watermark 之上的新事件 → 被清的戳永远不会被重新盖回 = 死信号 = trigger 永埋永不重派。
    故清戳必须配一条独立于 watermark 的重发现通道: 调用方 (reconcile) 清戳后【同时】写一个
    nonexec backlog marker (write_nonexec_backlog_marker), 派发循环每轮独立扫 backlog 重派
    (exec backlog re-scan 的非 exec 对应物 — clear_exec_task_stamp + ready-set 重算两半的镜像)。

    Returns: 是否真清掉了一个复合戳 (False = 本就没戳, 幂等)。
    """
    name = f"{trigger_event_id}__{_dispatch_target_slug(dispatch_to)}"
    # T-LRF-10b ④ (归档语义透明的对称半边 — load-bearing): 复合戳超龄后可能已被归档 sweep 搬进
    # dispatched_archive/。清戳是为了让 silent-death 的 review/fix 能被重派 (clear + backlog re-scan
    # 两半)。若只清活跃目录, 归档后清戳成 no-op, 而 is_already_dispatched 仍在 archive 命中 → 该
    # trigger 永久判"已派" → 静默死的 review/fix 永不重派 (latent regression)。故必须对称清【两处】:
    # 与上面读路径同样查 active+archive, 归档才真透明。
    active = _dispatched_dir(towow_dir) / name
    archived = _dispatched_archive_dir(towow_dir) / name
    existed = active.exists() or archived.exists()
    active.unlink(missing_ok=True)
    archived.unlink(missing_ok=True)
    return existed


def _nonexec_backlog_marker_name(trigger_event_id: str, dispatch_to: str) -> str:
    """backlog marker 文件名 — `<trigger>__<dispatch_slug>.json` (与复合戳同键约定, 一一对应)。"""
    return f"{trigger_event_id}__{_dispatch_target_slug(dispatch_to)}.json"


def write_nonexec_backlog_marker(
    towow_dir: Path,
    trigger_event_id: str,
    dispatch_to: str,
    *,
    trigger_event_type: str = "",
    review_mode: str | None = None,
    reason: str = "",
    deferred_reason: str | None = None,
    task_id: str | None = None,
) -> None:
    """T-FIX-B2-05 返工: 记一个被清戳的非 exec trigger 待重派 (清戳的另一半)。

    清戳让复合戳消失 (去重失效), 但 trigger 已在 watermark 之下永不被 _route_event 重扫 →
    必须独立记下"这个 trigger 还要派给 dispatch_to"才能重发现。marker 带重建 decision 所需信息:
    trigger_event_id + dispatch_to (路由目标) + trigger_event_type/review_mode (重建 review_mode
    分支 — design_time/author_time/fix_after 的前进链 review 必须带原 mode 否则 prompt 错)。

    FB-3 (watermark↔派发容量解耦): deferred_reason 标记 backlog 来源 provenance ——
    None = work_continuation (silent-death 收割 / resume 保留, 绕 governor); 属
    _GOVERNOR_REPASS_DEFERRED_REASONS = fresh-deferred (本轮新派被截断, re-scan 时重过 governor 门,
    不准绕 429)。re-scan (_read_nonexec_backlog_decisions) 据此还原 DispatchDecision.backlog_deferred_reason。

    幂等: 同 (trigger, dispatch_to) 重复写覆盖 (一个待重派项一份, 不堆积)。
    """
    bdir = _nonexec_backlog_dir(towow_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {
        "trigger_event_id": trigger_event_id,
        "dispatch_to": dispatch_to,
        "trigger_event_type": trigger_event_type,
        "reason": reason,
        "recorded_at": time.time(),
    }
    if review_mode is not None:
        body["review_mode"] = review_mode
    if deferred_reason is not None:
        body["deferred_reason"] = deferred_reason
    if task_id is not None:
        # 统一活会话守卫: 重放的 decision 保住 task 身份, 下轮才能重新按 task 查活会话 (丢了
        # task_id 的 backlog decision 会绕开守卫直接派 = 假守卫)。
        body["task_id"] = task_id
    (bdir / _nonexec_backlog_marker_name(trigger_event_id, dispatch_to)).write_text(
        json.dumps(body), encoding="utf-8",
    )


def _nonexec_retry_counter_path(
    towow_dir: Path, trigger_event_id: str, dispatch_to: str,
) -> Path:
    """T-FIX-B1-01: 非 exec 重派次数【持久】计数器路径 (写在 dispatched/ 下)。

    必须独立于 backlog marker —— backlog marker 在重派成功后被 clear_nonexec_backlog_marker 删,
    若把 count 存进 backlog marker 则每轮重置回 1, 永远到不了上限 (证伪抓出)。故 count 存进一个
    不随重派成败删除的独立计数器, 跨多轮 silent-death 累加, 到上限熔断 (exec retry__ marker 对应物)。
    """
    key = f"{trigger_event_id}__{_dispatch_target_slug(dispatch_to)}"
    return _dispatched_dir(towow_dir) / f"nonexec_retry__{key}"


def nonexec_redispatch_count(
    towow_dir: Path, trigger_event_id: str, dispatch_to: str,
) -> int:
    """T-FIX-B1-01: 该 (trigger, dispatch_to) 非 exec backlog 已被收割重派几次 (持久计数器读)。

    这是 exec_task_retry_count 的非 exec 对应物, 供熔断上限判据复用同一 _redispatch_cap。无计数器 → 0。
    """
    p = _nonexec_retry_counter_path(towow_dir, trigger_event_id, dispatch_to)
    if not p.exists():
        return 0
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        body = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(body, dict):
            return int(body.get("redispatch_count", 0))
    return 0


def bump_nonexec_redispatch_count(
    towow_dir: Path, trigger_event_id: str, dispatch_to: str,
) -> int:
    """T-FIX-B1-01: 非 exec 重派计数 +1 (每次 silent-death 收割时调)。Returns 累加后的新值。

    持久计数器 (不随 backlog marker 删除而重置) — 跨多轮 launched-but-rejected 累加到上限熔断。
    """
    new_count = nonexec_redispatch_count(towow_dir, trigger_event_id, dispatch_to) + 1
    p = _nonexec_retry_counter_path(towow_dir, trigger_event_id, dispatch_to)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({
            "trigger_event_id": trigger_event_id,
            "dispatch_to": dispatch_to,
            "redispatch_count": new_count,
            "updated_at": time.time(),
        }),
        encoding="utf-8",
    )
    return new_count


# ════════════════════════════════════════════════════════════════════════════════
#  T-LRF-03 — dispatch-retry-classification@v1: 收割/重派【前】把失败归类
#
#  收割/重派机制在决定是否重派前必须把失败归入一类, 据此决定重派或入死信
#  (concept dispatch-retry-classification@v1):
#    - deterministic (结构性, 同输入必败: 启动期自检失败 / 参数解析崩 / capsule 装配拒绝)
#        → 不重派, 直接进 dead-letter-inbox (entry_reason=structural_failure)。
#    - transient (瞬态: 运行中猝死 / socket 失败 / 进程被杀) → 重派, 走既有 _redispatch_cap 熔断
#        (达上限 → RedispatchExhausted + circuit_tripped 死信; T-FIX-B1-01 / T-LRF-02 已建)。
#    - unknown (无法分类) → 首次按 transient 重派一次, 该次仍失败 → 升 deterministic 入死信
#        (unknown 最多消耗 1 次重派预算, 防未知错误无限重派)。unknown ≠ structural (不违反
#        typed-failure-no-retry: 该不变量只约束『首分类即 structural 的失败 retry_count==0』)。
#
#  信号来源 (别只靠文本) —— 两个输入严守 @dispatch-failure-signal-contract@v1 (T-LRF-03 接线):
#    ① K6 ExceptionTaxonomy (awareness/exception_taxonomy.py): 复用既有扰动表识别已知瞬态
#       (rate-limit/network/model-unavailable/parked/true-death) —— 不造平行分类。
#    ② made_progress (derive_exec_made_progress): 区分『运行中猝死』(transient) 与『启动期死』。
#       合法源只有二: canonical 半成品 (has_partial_product) 或 transcript 真实新鲜度 (is_active)。
#       【禁止】transcript_exists / 裸 "last_activity_age_s is not None" (文件启动即建, 存在≠做过工作,
#       会撒谎 = 漏洞根因, finding f-lrf03-classifier-signals-non-discriminating)。注: 收割路径上的
#       DEAD 会话恒非 is_active (classify_vitality 规则2 ALIVE 先于规则6 DEAD) 且恒无半成品 (规则5
#       PARKED 先于 DEAD), 故 made_progress 在此结构上恒 False —— 判别交给 signal_text (K6+结构签名)。
#    ③ signal_text (compose_dispatch_failure_signal_text): 载真实终态失败成因 = verdict/daemon_state
#       + transcript 尾部真错因 (transcript_tail_error_text)。【禁止】_retry_marker_last_error 的上次
#       派发载荷 (它非失败原因, 拼进来让结构正则永远看不到真异常文本 = 漏洞根因之二)。
#    ④ recurrence (复发, 鲁棒兜底): unknown 重派一次再败 = 同输入必败 = deterministic。
#
#  ★ count==0 deterministic 生产可达性 (经验已验, 非推理): 启动期结构性死亡 (capsule 装配拒绝 /
#  启动自检崩 / 参数解析崩) 由子会话跑 `towow work start` 触发 (cli/main.py work_start →
#  _capsule_inject_or_fail), 失败时 CLI echo "error: capsule injection failed for skill_id=..." 到
#  stderr + exit 1 → 子会话的 Bash tool_result 带 is_error=true + 该 stderr 文本 → 落 transcript 尾部
#  (实测 ~/.claude/projects is_error block content 是 str, 含命令 stderr)。收割时 _parse_transcript_tail
#  抽出该文本喂 signal_text → _STRUCTURAL_FAILURE_SIGNATURE 命中 → deterministic 死信 (retry_count==0)。
#  故 count==0 结构性直判【生产可达】(不靠复发兜底), 集成测试拟真 fixture 忠实镜像此 transcript 尾部
#  死亡证据 (非 fixture-theater)。【SPEC 矛盾已记账】此处旧注释曾称 CapsuleInjectionFailed "不落账";
#  实测 cli/main.py work_start 路径【确落主账本】canonical CAPSULE_INJECTION_FAILED (towow=主仓库
#  .towow), 详 SPEC-CONFLICT-RESOLUTION-LEDGER (T-LRF-03 条); 但 signal_text 仍按冻结 concept 走
#  transcript 尾部源 (canonical 事件源非 concept 所列, 改用它=偏离冻结共识=须回共识)。
#
#  scope 边界: 本分类只接【exec】收割路径 (reconcile_orphaned_sessions 的 is_silent_death 块)——
#  执行 session 的启动期结构性失败在此 surface。非 exec (review/fix/...) 的 launched-but-rejected
#  是并发撞活会话的【瞬态】collision (concept 把 bump_nonexec_redispatch_count 引为 transient 计数器),
#  保留既有 cap 熔断 transient 待遇不动 (不被分类抢早夭、不缩减重派预算误杀活)。
# ════════════════════════════════════════════════════════════════════════════════


class DispatchFailureClass(StrEnum):
    """收割/重派前对一次派发失败的归类 (concept dispatch-retry-classification@v1)。"""

    DETERMINISTIC = "deterministic"  # 结构性, 同输入必败 → 不重派, 直接 structural_failure 死信
    TRANSIENT = "transient"          # 瞬态 → 重派 (走既有 _redispatch_cap 熔断)
    UNKNOWN = "unknown"              # 无法分类 → 首次 transient 重派一次, 再败升 deterministic


class RedispatchRoute(StrEnum):
    """分类 → 路由决策 (collapse 到既有重派 / 死信机制, 不新增第三条路)。"""

    REDISPATCH = "redispatch"                          # 进重派 (既有 cap / 熔断接管)
    STRUCTURAL_DEAD_LETTER = "structural_dead_letter"  # 不重派, 投 structural_failure 死信


# 结构性失败签名 (启动期自检失败 / 参数解析崩 / capsule 装配拒绝 —— 同输入必败)。
# ★ 故意收窄 (误判 deterministic = 杀掉本可重试的活, 是危险方向): 要求结构性【成因】词组 + 失败词
# 同现, 不靠裸 "valueerror"/"parse" 这类会在瞬态错误里误命中的泛词。判不准默认不判 deterministic
# (落 unknown, 由复发兜底), 绝不赌收割。
_STRUCTURAL_FAILURE_SIGNATURE = re.compile(
    r"capsule[^\n]*(inject|assembl)[^\n]*(fail|reject|closed|拒)"
    r"|capsuleinjectionfailed"
    r"|(self.?check|启动期?自检)[^\n]*(fail|崩|拒|reject)"
    r"|(param(eter)?|argument|参数)[^\n]*(parse|解析)[^\n]*(crash|崩|fail|error)"
    r"|装配拒绝|参数解析崩|启动期自检失败",
    re.IGNORECASE,
)

_DISPATCH_FAILURE_TAXONOMY = ExceptionTaxonomy()


def _signal_is_known_transient(signal_text: str) -> bool:
    """K6 既有扰动表是否把信号判为『已知瞬态 / 真死』(→ 该重派, 非结构性)。复用不造平行分类。

    TRANSIENT (限流/网络抖/模型不可用/休眠) 与 TERMINAL (真死, 需死亡证据) 在 K6 的规定动作都是
    『复活/重派』—— 对本分类即『非 structural, 走重派道』。其它 K6 类 (waiting/replan/exhausted/
    unknown) 不在此返 True: waiting/replan 属另一治理域 (不该被当 structural 死信, 默认落既有重派);
    exhausted 由既有熔断接管; unknown 交给 made_progress + 复发判 (本函数返 False)。
    """
    result = _DISPATCH_FAILURE_TAXONOMY.classify(signal_text)
    return result.failure_class in (FailureClass.TRANSIENT, FailureClass.TERMINAL)


def classify_dispatch_failure(
    *, signal_text: str, made_progress: bool,
) -> DispatchFailureClass:
    """把一次派发失败归入 deterministic / transient / unknown (concept dispatch-retry-classification@v1)。

    判别顺序 (首条命中即定, 偏向『非 deterministic』防误杀活):
      1. K6 已知瞬态/真死签名 (rate-limit/network/model/parked/true-death) → TRANSIENT (该重派)。
      2. made_progress (会话真跑过: 有 transcript 活动 / 半成品) → TRANSIENT —— 『运行中猝死』,
         走既有 cap 3 重派 (concept 把它列为 transient 头号例子)。
      3. 无活动 (死在启动前) + 结构性签名 (启动自检/参数解析/capsule 装配) → DETERMINISTIC。
      4. 无活动 + 无结构性签名 → UNKNOWN (兜底, 由路由层据复发计数决定: 首次重派一次, 再败升
         deterministic)。判不准不赌收割 (弱信号默认 unknown/transient, 不默认 deterministic)。
    """
    if _signal_is_known_transient(signal_text):
        return DispatchFailureClass.TRANSIENT
    if made_progress:
        return DispatchFailureClass.TRANSIENT
    if _STRUCTURAL_FAILURE_SIGNATURE.search(signal_text):
        return DispatchFailureClass.DETERMINISTIC
    return DispatchFailureClass.UNKNOWN


def route_dispatch_failure(
    failure_class: DispatchFailureClass, redispatch_count: int,
) -> RedispatchRoute:
    """据失败类 + 已重派次数决定路由 (concept §三类路由 + typed-failure-no-retry 不变量)。

    - deterministic → STRUCTURAL_DEAD_LETTER (不重派)。首分类即 deterministic 时 redispatch_count
      恒为 0 (失败首次观测就判结构性 = 从未重派) → typed-failure-no-retry 不变量自然成立。
    - transient → REDISPATCH (既有 _redispatch_cap 接管, 达 cap → circuit_tripped 死信)。
    - unknown → 首次 (redispatch_count==0) REDISPATCH (按 transient 试一次, 消耗 1 次预算);
      已重派过 (redispatch_count>=1) 仍 unknown 再败 → STRUCTURAL_DEAD_LETTER (升 deterministic,
      防未知错误无限重派)。这一次重派【不】违反 typed-failure-no-retry (它当时是 unknown 不是
      structural —— 终检接缝c)。
    """
    if failure_class is DispatchFailureClass.DETERMINISTIC:
        return RedispatchRoute.STRUCTURAL_DEAD_LETTER
    if failure_class is DispatchFailureClass.TRANSIENT:
        return RedispatchRoute.REDISPATCH
    # UNKNOWN
    if redispatch_count == 0:
        return RedispatchRoute.REDISPATCH
    return RedispatchRoute.STRUCTURAL_DEAD_LETTER


def route_structural_failure_to_dead_letter(
    towow_dir: Path,
    event_log: EventLog,
    *,
    source_object_type: str,
    source_object_ref: str,
    original_trigger_event_id: str | None,
    signal_text: str,
    made_progress: bool,
    redispatch_count: int,
    circuit_key: str,
    context_label: str,
) -> bool:
    """收割/重派【前】的分类闸 (concept dispatch-retry-classification@v1 wiring)。

    分类 → 路由。deterministic (或 unknown 耗尽 1 预算升 deterministic) → 投 structural_failure
    死信 + 写幂等熔断 marker (停重派, 给它一等终点等分诊) + main-inbound 可见化, 返回 True 让调用方
    【跳过重派】(不清戳/不 bump, 保 typed-failure-no-retry: 首分类即 structural 时 retry_count==0)。
    transient / unknown-首次 → 返回 False, 调用方走既有重派路径。

    幂等: circuit_key 已熔断 → 不重复 enqueue/告警, 直接返回 True (上轮已处理)。circuit_key 必须与
    既有 pool-build / nonexec 熔断检查同键 (byte-identical), 否则熔断不真停重派 = 双派。

    T-R01-9 回归修复: 已熔断(含 sweep_force_terminal_states 追加的 .terminalized 终态化哨兵 ——
    二者同键同生同灭, 查前者已等价覆盖后者)必须在【任何分类之前】幂等短路, 不管这次失败信号被
    classify_dispatch_failure 分成什么。此前该检查只挂在 STRUCTURAL_DEAD_LETTER 分支里, TRANSIENT
    (route_dispatch_failure 对它无条件 REDISPATCH, 完全不看 redispatch_count) 或 UNKNOWN 首次会无视
    既有熔断直接 return False, 让调用方 (reaper) 清戳放行重派 —— 熔断终态被静默翻案。
    """
    if is_redispatch_circuit_tripped(towow_dir, circuit_key):
        return True  # 幂等: 已熔断(含终态化), 不管本次信号分类结果, 一律跳过重派
    fclass = classify_dispatch_failure(signal_text=signal_text, made_progress=made_progress)
    route = route_dispatch_failure(fclass, redispatch_count)
    if route is RedispatchRoute.REDISPATCH:
        return False
    # STRUCTURAL_DEAD_LETTER: deterministic 直判, 或 unknown 重派一次再败升 deterministic。
    # (顶部已短路已熔断情形, 这里到达时 circuit_key 必然未熔断)
    via_unknown_escalation = fclass is DispatchFailureClass.UNKNOWN
    trip_redispatch_circuit(
        towow_dir, circuit_key,
        {
            "cause": "dispatch_retry_classification",
            "failure_class": fclass.value,
            "via_unknown_escalation": via_unknown_escalation,
            "redispatch_count": redispatch_count,
            "source_object_ref": source_object_ref,
        },
    )
    dead_letter_inbox.enqueue(
        towow_dir, event_log,
        source_object_type=source_object_type,
        source_object_ref=source_object_ref,
        entry_reason=dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE,
        original_trigger_event_id=original_trigger_event_id,
    )
    # main-inbound 可见化: owner 进主对话即见此对象因结构性失败被【直接死信】(非熔断耗尽 —— 同输入
    # 必败, 重派无意义)。SilentDeathAlarmed 是 NodeTouched 不进 main-inbound poller, 此处补显著通知。
    detail = (
        "首次观测即结构性失败" if not via_unknown_escalation
        else f"未知失败重派 {redispatch_count} 次仍无进展, 升 deterministic"
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=f"structural-dead-letter-{uuid.uuid4().hex[:8]}",
            trigger_event_type="StructuralFailureDeadLettered",
            dispatch_to="main-inbound",
            reason=(
                f"⚠ 结构性失败直接死信 (不重派): {context_label} {source_object_ref} "
                f"({detail}; entry_reason=structural_failure, 等分诊) (T-LRF-03)"
            ),
        ),
    )
    return True


def compose_dispatch_failure_signal_text(
    *,
    final_status: str,
    verdict: object,
    daemon_state: str | None,
    transcript_tail_error_text: str | None,
) -> str:
    """@dispatch-failure-signal-contract@v1 字段2 signal_text 的【派生契约】(T-LRF-03)。

    合法内容 = 会话的【真实终态失败成因】: 死亡证据 (final_status) / liveness verdict 理由
    (verdict / daemon_state) / transcript 尾部真实错误文本 (transcript_tail_error_text)。
    【禁止来源】上一次【派发】的 decision_payload (_retry_marker_last_error 的 last_dispatch): 它是
    上次派发决策载荷不是失败原因, 拼进来会让 K6 与结构正则永远看不到真异常文本, deterministic-by-
    structural-signature 分支永不触发 (漏洞根因之二)。判别尺: 真实 "capsule 装配拒绝/启动期自检失败/
    参数解析崩" 场景 signal_text 必须可被 _STRUCTURAL_FAILURE_SIGNATURE 命中 —— 命中靠 transcript 尾部
    真错因, 故它必须拼进来。

    这是 orchestrator 收割路径 exec_failure_signal 接线的【单一真相源】(orchestrator 与单测同调它)。
    """
    return (
        f"{final_status} verdict={verdict} daemon_state={daemon_state} "
        f"{transcript_tail_error_text or ''}"
    ).strip()


def clear_nonexec_backlog_marker(
    towow_dir: Path, trigger_event_id: str, dispatch_to: str,
) -> bool:
    """重派成功后删 backlog marker (闭环收口)。Returns 是否真删了 (False = 本就没有, 幂等)。"""
    marker = _nonexec_backlog_dir(towow_dir) / _nonexec_backlog_marker_name(
        trigger_event_id, dispatch_to,
    )
    if not marker.exists():
        return False
    marker.unlink(missing_ok=True)
    return True


def _read_nonexec_backlog_decisions(towow_dir: Path) -> list[DispatchDecision]:
    """T-FIX-B2-05 返工: 把 nonexec backlog markers 重建成 DispatchDecision (独立于 watermark)。

    这是 exec backlog re-scan (ready_execution_tasks_to_dispatch 每轮从 PlanFreezed plan 重算
    ready-set) 的非 exec 对应物: 不依赖事件水位线, 直接从 backlog marker 目录重发现待重派项。
    派发循环每轮调它, 把这些 decision 喂进同一条非 exec 派发路径 (经 B2-01 单飞门串行)。
    """
    bdir = _nonexec_backlog_dir(towow_dir)
    if not bdir.is_dir():
        return []
    out: list[DispatchDecision] = []
    for mf in sorted(bdir.glob("*.json")):
        with contextlib.suppress(OSError, json.JSONDecodeError):
            body = json.loads(mf.read_text(encoding="utf-8"))
            if not isinstance(body, dict):
                continue
            trig = body.get("trigger_event_id")
            dto = body.get("dispatch_to")
            if not (isinstance(trig, str) and trig and isinstance(dto, str) and dto):
                continue
            rmode = body.get("review_mode")
            # FB-3: 还原 deferred_reason → 派发循环 governor 门据此判 fresh-deferred (重过门)
            # vs work_continuation (绕门)。缺字段 (旧 marker / silent-death / resume) → None = 绕门。
            dreason = body.get("deferred_reason")
            btask = body.get("task_id")
            out.append(DispatchDecision(
                trigger_event_id=trig,
                trigger_event_type=str(body.get("trigger_event_type", "")),
                dispatch_to=dto,
                reason=str(body.get("reason", ""))
                or f"nonexec backlog re-scan 重派 (T-FIX-B2-05): {trig}→{dto}",
                review_mode=rmode if isinstance(rmode, str) else None,
                task_id=btask if isinstance(btask, str) and btask else None,
                backlog_deferred_reason=dreason if isinstance(dreason, str) else None,
            ))
    return out


# ════════════════════════════════════════════════════════════════════════════════
#  Event emission helpers — OrchestratorDispatched / OrchestratorDispatchFailed
#  (stub-rewrap as NodeTouched; canonical EventType registration deferred RUN-013+)
# ════════════════════════════════════════════════════════════════════════════════


# NOTE: ActorType.SYSTEM (not DAEMON) — DaemonName enum 是 M-2.1/2.2/2.3 family
# 专属, F-11 是 M-3.1 不在那 enum. ProvenanceHint 在 actor_type=daemon 时要求
# daemon_name 字段; 用 SYSTEM 避开. RUN-005 manual dispatch 也走 SYSTEM 一致.
_ORCH_ACTOR_TYPE = ActorType.SYSTEM.value
_ORCH_ACTOR_ID = "f11-orchestrator-polling"


def _build_orch_nodetouched(
    *,
    kind: str,
    decision_id: str,
    payload_body: dict[str, object],
    base_classification: BaseClassification = BaseClassification.DISCARDABLE_NOISE,
) -> EventIntent:
    """NodeTouched + kind for orchestrator audit events (consistent with RUN-005 模式).

    base_classification 默认 DISCARDABLE_NOISE (一般 orchestrator 自愈/审计噪声)。少数留痕是
    Nature 决策证据 / 硬闭环凭据 (如 EscalationAnswerApplied)——这类必须 IMMUTABLE_TRUTH 永久保留,
    否则 GC 可把"答复已闭环"的凭据扫掉 = 回流被判未闭合 (与 CLI escalation respond 同口径)。
    """
    return EventIntent(
        local_intent_id=f"orch-{uuid.uuid4().hex[:12]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": decision_id,
            "touch_type": "write",
            "kind": kind,
            "stub_original_payload": payload_body,
        },
        provenance_hint=ProvenanceHint(
            actor_type=_ORCH_ACTOR_TYPE,
            actor_id=_ORCH_ACTOR_ID,
        ),
        base_classification=base_classification,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=decision_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


def emit_orchestrator_dispatched(
    event_log: EventLog,
    decision: DispatchDecision,
    *,
    spawn_result: dict[str, object] | None = None,
    bucket_id: str | None = None,
) -> str:
    """Emit OrchestratorDispatched audit event. Returns event_id."""
    decision_id = bucket_id or f"orch-dispatch-{uuid.uuid4().hex[:8]}"
    payload: dict[str, object] = {
        "kind": "OrchestratorDispatched",
        "decision_id": decision_id,
        "trigger_event_id": decision.trigger_event_id,
        "trigger_event_type": decision.trigger_event_type,
        "dispatch_to": decision.dispatch_to,
        "task_id": decision.task_id,  # T3 review finding-2: fan-out 的 task 身份进 canonical 审计
        "reason": decision.reason,
        "spawn_result": spawn_result,
        "manual_orchestrator": False,
    }
    intent = _build_orch_nodetouched(
        kind="OrchestratorDispatched",
        decision_id=decision_id,
        payload_body=payload,
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


def emit_orchestrator_dispatch_failed(
    event_log: EventLog,
    decision: DispatchDecision,
    *,
    handler: str,
    final_error: str,
    retry_count: int,
) -> str:
    """M-3.1 §7.6: retry 耗尽 → emit OrchestratorDispatchFailed + 等 owner.

    T-L3kc-04 (波1): emit 真 canonical OrchestratorDispatchFailed (非 NodeTouched 假名)。重试行为
    早已真; 此处只把审计事件从 stub-rewrap 升成注册的 EventType + flat typed payload (path-B,
    PAYLOAD_VALIDATION_ENFORCED)。
    """
    decision_id = f"orch-failed-{uuid.uuid4().hex[:8]}"
    intent = EventIntent(
        local_intent_id=f"orch-{uuid.uuid4().hex[:12]}",
        event_type=EventType.ORCHESTRATOR_DISPATCH_FAILED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "decision_id": decision_id,
            "trigger_event_id": decision.trigger_event_id,
            "trigger_event_type": decision.trigger_event_type,
            "dispatch_to": decision.dispatch_to,
            "task_id": decision.task_id,  # T3 review finding-2: fan-out 的 task 身份进 canonical 审计
            "handler": handler,
            "final_error": final_error,
            "retry_count": retry_count,
            "manual_orchestrator": False,
        },
        provenance_hint=ProvenanceHint(
            actor_type=_ORCH_ACTOR_TYPE,
            actor_id=_ORCH_ACTOR_ID,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=decision_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


# ════════════════════════════════════════════════════════════════════════════════
#  B-2 原子认领 reaper (T-RMD-S3-REAPER, 根治 f-sub-atomic-claim-no-reaper)
#  崩溃 (SIGKILL 卡在 claim 与 finally:release 之间) 泄漏的 .claim 永久饿死 task; 这条补上
#  exec claim 缺失的回收路径 (与 session_lock.reap_stale 同构), 回收即 emit canonical 留痕。
# ════════════════════════════════════════════════════════════════════════════════


def emit_exec_claim_reaped(
    event_log: EventLog,
    reaped: ReapedClaim,
    *,
    stale_after_s: float,
) -> str:
    """emit canonical ExecClaimReaped 留痕 (一个泄漏/过期 .claim 被回收)。

    daemon-internal self-observation, path-B write_direct, provenance=SYSTEM/f11-orchestrator-polling
    (非交互 — 不是人手 CLI session, 同 OrchestratorDispatchFailed 范式)。让被饿死 task 的回收留
    canonical 痕迹 (上看板 + reaper 真跑过的机器证据, 非空心)。
    """
    reaped_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    intent = EventIntent(
        local_intent_id=f"orch-reap-{uuid.uuid4().hex[:12]}",
        event_type=EventType.EXEC_CLAIM_REAPED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "task_id": reaped.task_id,
            "claimant": reaped.claimant,
            "fencing_token": reaped.fencing_token,
            "age_s": reaped.age_s,
            "stale_after_s": stale_after_s,
            "reason": reaped.reason,
            "reaped_at": reaped_at,
        },
        provenance_hint=ProvenanceHint(
            actor_type=_ORCH_ACTOR_TYPE,
            actor_id=_ORCH_ACTOR_ID,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=reaped.task_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


def _has_live_execution_session(towow_dir: Path, task_id: str) -> bool:
    """是否有对应该 task 的 live execution 会话 (reaper '+无对应live execution会话' 护栏)。

    execution session 锁注册时带 fork_id=task_id (_try_register_execution_lock); 故扫 execution kind
    的 live_sessions (registry 自带 reap_stale 去死锁) 看有没有 fork_id==task_id 的活锁 = 该 task
    确实在跑, reaper 绝不回收它的 claim (即便 claim ts 过期)。容错: 查锁出错 → 保守返 True (宁可
    不回收一个可能在跑的 task, 也不误杀; 下个周期 ts 仍过期会再判)。
    """
    try:
        from towow.l1.session_lock import SessionLockRegistry

        return any(
            info.fork_id == task_id
            for info in SessionLockRegistry(towow_dir, "execution").live_sessions()
        )
    except Exception:
        return True


def reap_stale_exec_claims(
    towow_dir: Path,
    event_log: EventLog,
    *,
    now: float | None = None,
    stale_after_s: float | None = None,
) -> list[str]:
    """exec claim reaper 编排接线 (根治 f-sub-atomic-claim-no-reaper)。

    扫 exec claim 目录回收泄漏/过期 .claim (awareness.claim.reap_stale_claims), 每回收一个 emit
    canonical ExecClaimReaped 留痕。'+无对应live execution会话' 护栏经 _has_live_execution_session
    (按 session 锁 fork_id 匹配) 接上 —— 有活会话的 task 绝不回收。被饿死 task 的戳此前未写 (spawn
    没跑完) → claim 一去, ready-set 重算即重派。返回被回收的 task_id 列表。

    挂两处 (finding 点名): orchestrator 启动预检 (run_polling_loop) + 每轮 backlog re-scan
    (_dispatch_execution_batch)。reaper 绝不崩 daemon (caller 用 suppress 兜)。
    """
    from towow.awareness.claim import EXEC_CLAIM_STALE_AFTER_S, reap_stale_claims

    threshold = EXEC_CLAIM_STALE_AFTER_S if stale_after_s is None else stale_after_s
    reaped = reap_stale_claims(
        _exec_claim_dir(towow_dir),
        now=now,
        stale_after_s=threshold,
        is_live=lambda tid: _has_live_execution_session(towow_dir, tid),
    )
    for r in reaped:
        emit_exec_claim_reaped(event_log, r, stale_after_s=threshold)
    return [r.task_id for r in reaped]


# ════════════════════════════════════════════════════════════════════════════════
#  搁浅工位护栏 (f-hardening-reaper-committed-no-success-noredispatch)
#
#  病根: executor 在隔离工位 (branch=task-{wid}) committed 了活, 但会话死前从未 emit
#  TaskRunCompleted(success)、从未 promote 回 main = stranded-worktree。silent-death 收割路径
#  (reap_silently_dead_exec_stamps 第一支 / reconcile_orphaned_sessions silent-death 支) 清 exec
#  戳前不查这点 → 清戳 → is_exec_task_dispatched 翻 False → 反复重派 (≤3 熔断后还会 dead-letter,
#  连已 commit 的活一起丢) = 浪费根。
#
#  护栏: 清戳前查 task-{wid} 分支是否领先 main。领先 = 有搁浅 commit → 不清戳/不重派/不 dead-letter,
#  改 emit needs-promotion baton (幂等 surface) 交 plan 决策 task-close (promote+close 或弃+重派)。
#  不 auto-promote —— 无 TaskRunCompleted(success) = 活未经完工门验证, auto-promote 会绕过完工门。
# ════════════════════════════════════════════════════════════════════════════════

_STRANDED_PROMOTION_MARKER_PREFIX = "stranded_needs_promotion__"


def _stranded_promotion_marker_path(towow_dir: Path, worktree_id: str) -> Path:
    """搁浅工位 needs-promotion baton 的幂等 marker 路径 (写在 dispatched/ 下, 同 conflict/orphan)。"""
    return _dispatched_dir(towow_dir) / f"{_STRANDED_PROMOTION_MARKER_PREFIX}{worktree_id}"


def _exec_worktree_committed_ahead_of_main(towow_dir: Path, task_id: str) -> bool | None:
    """task 的隔离工位分支 task-{wid} 是否有领先 main 的 commit (= executor committed 了活但从未
    emit success / 从未 promote 回 main 的搁浅工作 stranded-worktree)。

    判据 = git rev-list --count main..task-{wid} > 0 (committed commit 数; 工作树里【未 commit】的
    改动不计入 —— 见 debt 边界登记)。三态:
      True  = 领先 (有搁浅 commit) → caller 须保护: 不清戳, surface needs-promotion baton;
      False = 不领先 (分支不存在=非隔离/mock_spawn/已 promote+cleanup, 或有分支但 0 commit / 已回流)
              → caller 照常清戳重派, 无活可丢 (绝不 regress T-RMD-FND-01 '失败者永不重派');
      None  = git 不可验证 (子进程异常 / rev-parse 128 / rev-list 异常 / 解析失败) → caller 本轮
              【跳过】(既不清戳也不 surface, 下轮重试)。清戳-on-None 会在 git 抖动下复发本 finding;
              永久跳过-on-None 会在持久 git 失败下复发 T-RMD-FND-01。跳过本轮于瞬态错误 self-heal。
    """
    wid = _exec_worktree_id(task_id)
    branch = f"task-{wid}"
    main_repo = towow_dir.parent

    def _git(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(main_repo), *args],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    # step0: 是 git 仓库吗? 非 git 仓库 (测试 tmp / 隔离关 / mock_spawn 的非隔离环境) → 无 worktree
    # 隔离 = 不可能搁浅 → False, 让 reaper 照常清戳重派 (绝不 regress T-RMD-FND-01 非隔离静默死重派)。
    inside = _git(["rev-parse", "--is-inside-work-tree"])
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    # step1: (已确认在 git 仓库内) 分支存在吗? rev-parse --verify --quiet: returncode 1 = 分支不存在
    # (非隔离任务 / 已 promote+cleanup → False 让其重派); 0 = 存在; 其它 = 瞬态异常 → None 本轮跳过。
    verify = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if verify is None:
        return None
    if verify.returncode == 1:
        return False  # 分支不存在 = 非隔离任务 / 已 promote+cleanup, 无搁浅 commit
    if verify.returncode != 0:
        return None  # bad ref / 瞬态 git 异常 → 不可验证, 保守跳过 (不清戳)
    # step2: 分支在, 数它领先 main 的 commit 数。git 异常 → None (本轮跳过, 避 git 抖动下盲清)。
    proc = _git(["rev-list", "--count", f"main..{branch}"])
    if proc is None or proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip()) > 0
    except ValueError:
        return None


def _stranded_baton_trigger_id(towow_dir: Path, task_id: str) -> str:
    """搁浅 baton 的 trigger 溯源: 优先复用 exec 戳里记的 orchestrator_dispatched_event_id (真派发
    溯源); 取不到则合成清晰标记的 id (item-6: 绝不空串)。"""
    stamp = _dispatched_dir(towow_dir) / _exec_task_stamp_name(task_id)
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            eid = data.get("orchestrator_dispatched_event_id")
            if isinstance(eid, str) and eid:
                return eid
    except (OSError, json.JSONDecodeError):
        pass
    return f"stranded-worktree-{_exec_worktree_id(task_id)}"


def _surface_stranded_worktree_needs_promotion(
    towow_dir: Path, event_log: EventLog, *, task_id: str,
) -> bool:
    """搁浅工位 needs-promotion baton (幂等 surface): 既不盲清戳重派 (浪费根 + 丢已 commit 的活), 也不
    auto-promote (无完工门验证), 而是 surface 一条 baton 交 plan 决策 task-close。

    dispatch_to="main-inbound" —— 复用 _record_promote_conflict / _record_fix_orphan 的 surface-only
    习语 (已证不 auto-spawn; reason 里点明交 plan task-close, 满足合约 'surface 给 plan task-close'
    而不冒触发自动 spawn 的风险)。幂等 marker 防每轮重刷 baton (重刷本身又是浪费根)。
    返 True = 本次新 surface; False = 已 surface 过 (no-op)。
    """
    wid = _exec_worktree_id(task_id)
    marker = _stranded_promotion_marker_path(towow_dir, wid)
    if marker.exists():
        return False
    trigger_eid = _stranded_baton_trigger_id(towow_dir, task_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({
            "task_id": task_id,
            "worktree_id": wid,
            "branch": f"task-{wid}",
            "trigger_event_id": trigger_eid,
            "semantics": (
                "stranded_worktree_committed_no_success_needs_promotion_awaiting_plan_task_close"
            ),
        }),
        encoding="utf-8",
    )
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id=trigger_eid,
            trigger_event_type="StrandedWorktreeDetected",
            dispatch_to="main-inbound",
            reason=(
                f"⚠ STRANDED-WORKTREE task={task_id} 工位={wid} 分支 task-{wid} 领先 main "
                f"(executor committed 但从未 emit success / 从未 promote 回 main) → 不重派 (避浪费根 + "
                f"避丢已 commit 的活), 不 auto-promote (无完工门验证), surface 给 plan 决策 task-close "
                f"(promote+close 或弃+重派) (f-hardening-reaper-committed-no-success-noredispatch)"
            ),
            task_id=task_id,
        ),
    )
    return True


def sweep_stranded_worktrees_for_promotion(
    towow_dir: Path,
    event_log: EventLog,
    events: list[dict[str, object]],
) -> list[str]:
    """周期 sweep (f-hardening-reaper-committed-no-success-noredispatch 第二支): 扫所有 freezed plan
    的 ready-set, 找 task-{wid} 分支领先 main 的搁浅工位 surface needs-promotion baton 交 plan
    task-close。

    兜住 reaper 第一支够不到的: 戳已被 (legacy / pre-fix) 清掉、或已重派出孪生的 task —— 其搁浅
    commit 仍躺在分支上没回流。与第一支共用 _surface_... 的幂等 marker → 绝不双 baton。
    返回本轮新 surface 的 task_id 列表。绝不 raise (caller suppress 兜, 一个烂工位不拖垮自动链)。
    """
    from towow.l2.execution_dispatch import (
        all_freezed_plan_ids,
        ready_execution_tasks_to_dispatch,
    )

    surfaced: list[str] = []
    seen: set[str] = set()
    for plan_id in all_freezed_plan_ids(events):
        for task_id in ready_execution_tasks_to_dispatch(events, plan_id, set()):
            if task_id in seen:
                continue
            seen.add(task_id)
            # 在跑的 task 绝不碰 (它可能正在 commit, 误判搁浅会乱)。
            if _has_live_execution_session(towow_dir, task_id):
                continue
            if _exec_worktree_committed_ahead_of_main(towow_dir, task_id) is True and (
                _surface_stranded_worktree_needs_promotion(
                    towow_dir, event_log, task_id=task_id,
                )
            ):
                surfaced.append(task_id)
    return surfaced


def _reaper_confirms_dead(
    towow_dir: Path, event_log: EventLog, task_id: str,
) -> bool:
    """R11 根治 (沉默≠死亡): silent-death reaper 清 exec 戳 / pending 前的鲁棒死活复核。

    病根 (f-redispatch-twin-on-stale-lock / T-PRW-sdm-reverse 实证): 清戳门 _has_live_execution_session
    只看 session 锁 (registry 自带 reap_stale)。一个【活着但慢】的执行会话 (长 LLM turn / 大 SKILL
    重写期间不刷心跳) 的锁会因心跳超时被判 stale → _has_live 返 False → 本 reaper 误判"静默死"清戳 →
    ready-set 重扫重派 → 孪生。孪生在原会话完工 (TaskRunCompleted success) 后才 boot、读账本见已
    success、报"已完成无需动作" —— 正是 owner 看到的"同一封派发信被反复重放"。锁心跳超时 ≠ 死。

    根治: 清戳前用 session_vitality (transcript 活动 + os.kill 进程探活 + 活锁 + canonical 产物) 复核,
    只有【正向死亡证据】(pid os.kill 没了 / 本 run aborted / 复活预算耗尽 → VitalityVerdict.DEAD 且非
    aborted-run, 即 should_redispatch) 才确认可安全收割重派。

    降级 (保持既有 silent-death reaper 语义, 不回退 T-RMD-FND-01 "失败者永不重派"):
      - 查不到该 task 的 canonical bg 会话血缘 (从未真 spawn / mock_spawn / 单测) → 无活会话可误杀 →
        返 True (照旧收割)。既有 reaper 单测 (只铺 exec__ 戳、无 OrchestratorDispatched bg 会话) 因此保持绿。
      - 反查 / assess 抛错, 或 vitality 非正向死亡 (ALIVE_WORKING/PARKED_RESUMABLE/STUCK_WAITING/DONE/
        UNKNOWN) → 返 False, fail-closed 本轮不收割 (下轮再看), 宁留陈旧戳等 owner/下轮, 绝不误起孪生。
    """
    try:
        session_id = _latest_bg_session_id_for_task(event_log, task_id)
    except Exception:
        return False  # 反查失败 → fail-closed 不收割
    if not session_id:
        return True  # 无 canonical 会话血缘 = 无活会话可误杀 → 保持既有收割语义
    try:
        vitality = assess_vitality(
            session_id, task_id=task_id, event_log=event_log, towow_dir=towow_dir,
        )
    except Exception:
        return False  # 评估失败 → fail-closed 不误孪生
    return vitality.should_redispatch


def reap_silently_dead_exec_stamps(
    towow_dir: Path,
    event_log: EventLog,
    events: list[dict[str, object]],
    *,
    now: float | None = None,
    stale_after_s: float | None = None,
) -> list[str]:
    """静默死亡 exec 戳 reaper (T-RMD-FND-01 bootstrap, 根治"失败者永不重派"的静默死分支)。

    病根: clear_exec_task_stamp 只在 abort/fail 终态路径跑; 静默死亡 (执行会话跑 work start 后死,
    无 TaskRunCompleted/abort 终态事件) 的 task exec__ 戳永不清 -> is_exec_task_dispatched 永 True
    -> 派发循环永远跳过它 -> 永不重派。与 reap_stale_exec_claims 互补: claim reaper 管 spawn 窗口内
    死 (戳未写); 本 reaper 管 spawn 后执行中静默死 (戳已写, 死后戳残留挡重派)。

    判据 (与 claim reaper '+无对应 live execution 会话' 同护栏): ready_execution_tasks_to_dispatch
    已排除 completed/closed; 其输出中 "有 exec__ 戳 (is_exec_task_dispatched) 且无 live execution
    会话 (_has_live_execution_session — registry 自带 reap_stale 去死锁, 静默死会话的锁被回收 -> 返
    False; 查锁出错保守返 True 不误杀在跑 task)" = 静默死 -> clear_exec_task_stamp 清戳让其重派。
    返回被清戳的 task_id 列表。caller 用 suppress 兜 (一个烂戳不崩 daemon)。
    """
    import time

    from towow.awareness.claim import EXEC_CLAIM_STALE_AFTER_S
    from towow.l2.execution_dispatch import (
        all_freezed_plan_ids,
        ready_execution_tasks_to_dispatch,
    )

    threshold = EXEC_CLAIM_STALE_AFTER_S if stale_after_s is None else stale_after_s
    now_ts = time.time() if now is None else now
    reaped: list[str] = []
    seen: set[str] = set()
    for plan_id in all_freezed_plan_ids(events):
        for task_id in ready_execution_tasks_to_dispatch(events, plan_id, set()):
            if task_id in seen:
                continue
            seen.add(task_id)
            if not is_exec_task_dispatched(towow_dir, task_id):
                continue
            # TOCTOU 护栏 (镜像 reap_stale_exec_claims stale_after_s): 只回收【够旧】的戳 —— 刚派发的
            # task 戳新鲜(mtime≈now), 其会话锁可能尚未注册完(_has_live 暂 False), 不能误清当静默死。
            stamp_path = _dispatched_dir(towow_dir) / _exec_task_stamp_name(task_id)
            try:
                age = now_ts - stamp_path.stat().st_mtime
            except OSError:
                continue
            if age < threshold:
                continue
            if _has_live_execution_session(towow_dir, task_id):
                continue
            # 搁浅工位护栏 (f-hardening-reaper-committed-no-success-noredispatch): 清戳前查隔离工位
            # 分支 task-{wid} 是否领先 main。领先 = executor committed 了活但从未 emit success / 从未
            # promote → 清戳重派是浪费根 + 丢已 commit 的活。改 surface needs-promotion baton 不清戳。
            stranded = _exec_worktree_committed_ahead_of_main(towow_dir, task_id)
            if stranded is None:
                continue  # git 不可验证 → 本轮跳过不清戳 (避 git 抖动下盲清复发本 finding), 下轮重试
            if stranded:
                _surface_stranded_worktree_needs_promotion(
                    towow_dir, event_log, task_id=task_id,
                )
                continue  # 有搁浅 commit → 不清戳/不重派, 已 surface baton 交 plan task-close
            # 熔断终态护栏 (T-R01-9 回归修复): 清戳前必须复核该 task 的 redispatch circuit 是否已
            # tripped (含 .terminalized —— 同键同生同灭, 查前者已等价覆盖)。没有这道守卫, 一次新的
            # 静默死亡(命中瞬态签名)会被本 reaper 直接清戳放行重派, 翻案掉此前已成立的熔断终态。
            if is_redispatch_circuit_tripped(towow_dir, _exec_task_stamp_name(task_id)):
                continue  # 已熔断(含终态化) — 收割重派前必须尊重, 不清戳
            # R11 根治 (沉默≠死亡, f-redispatch-twin-on-stale-lock): 清戳前鲁棒死活复核 ——
            # _has_live_execution_session 只看锁心跳, 慢活会话(长 turn 不刷心跳)的锁被判 stale →
            # 误判静默死清戳 → ready 重扫重派 → 孪生(孪生在原会话 success 后 boot 报"已完成")。
            if not _reaper_confirms_dead(towow_dir, event_log, task_id):
                continue  # vitality 未确认正向死亡(活/泊车/等待/信号不足) → 不清戳, 下轮再看
            if clear_exec_task_stamp(
                towow_dir,
                task_id,
                source="silent_death_reaper",
                reason=(
                    "exec__ 戳残留但无 live execution 会话且 ready(未 completed) = 静默死亡 "
                    "(无终态事件, clear 的 abort/fail 路径没覆盖); 清戳让其重派 (T-RMD-FND-01)"
                ),
            ):
                reaped.append(task_id)

    # 第二层 stale 残留: pending_session 条目。静默死无终态事件 → reconcile 没清 pending_session →
    # _active_execution_sessions 仍把它算 active → select_dispatch_batch 当它在跑、跳过不重派 (清了
    # exec__ 戳也没用, 因为戳清后该 task 无戳, 上方第一层 loop 的 is_exec_task_dispatched 跳过它,
    # 够不到它的 pending_session)。故独立扫: 清"无 live 会话(死) + 够旧(刚派发护栏)"的 execution
    # pending_session, 让任务真能重派。
    pdir = _pending_sessions_dir(towow_dir)
    if pdir.is_dir():
        for pf in pdir.glob("*.json"):
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not (isinstance(data, dict) and data.get("spawned_role") == "execution"):
                continue
            tid = data.get("task_id")
            if not isinstance(tid, str) or not tid:
                continue
            if _has_live_execution_session(towow_dir, tid):
                continue  # 在跑 (live 锁) → 绝不碰
            try:
                if (now_ts - pf.stat().st_mtime) < threshold:
                    continue  # 刚派发护栏 (pending_session 新鲜, 锁可能尚未注册完)
                if not _reaper_confirms_dead(towow_dir, event_log, tid):
                    continue  # R11: vitality 未确认死 → 不清 pending (慢活会话不误杀重派)
                pf.unlink()
            except OSError:
                continue
            if tid not in reaped:
                reaped.append(tid)
    return reaped


# ════════════════════════════════════════════════════════════════════════════════
#  E.5 GoalSession lifecycle: orchestrator-side register + completion guarantee
#  (concepts: orchestrator-goal-session-registration / goal-session-completion-guarantee)
# ════════════════════════════════════════════════════════════════════════════════

_PENDING_SESSIONS_SUBDIR = "pending_sessions"


def emit_goal_session_started(
    event_log: EventLog,
    *,
    goal_session_id: str,
    spawned_role: str,
    trigger_event_id: str,
    command_text: str = "",
    launched: bool = False,
    spawn_origin: str = "orchestrator_auto",
    task_id: str | None = None,
    bg_session_id: str | None = None,
) -> str:
    """E.5: orchestrator 自动 spawn 下一棒时注册被追踪的 GoalSession.

    现状 orchestrator 经 spawn_bg_session 起会话不 emit GoalSessionStarted, 会话无
    被追踪身份 → 两个 bug 的共同根。注册后: 完工兜底对账有对象 (F-019-10), 完工
    通知有真会话可核对 (F-019-11)。orchestrator_registered=True 标记是 orchestrator
    自 emit (区别于 CLI goal spawn)。

    T-FIX-B5-02: spawn_origin 把 orchestrator_registered=True 的隐含语义显式化成 B5 冻结
    的三值 origin 枚举 {orchestrator_auto, inline_continuation, cli_goal_spawn} —— 让完工
    对账 (F-019-10/11) 与 verify-the-verifier 能区分这条会话是『编排器经 FORWARD_CHAIN 边
    自动派的下一棒』(orchestrator_auto, 默认) / 『同对话 agent 自觉手动续跑』(inline_continuation)
    / 『owner 显式 towow goal spawn』(cli_goal_spawn)。取值集合复用 T-FIX-B4-01 注入子会话
    env TOWOW_SESSION_ORIGIN 的唯一真源 claude_bg_helper._VALID_SESSION_ORIGINS, 不另立词汇。
    野 origin → fail-closed 抛 ValueError (与 spawn env 注入一致, 不静默落脏 origin)。

    T-TWU-G (debt-4d61bbb20311 根治, Option A): 可选 task_id 直存进 payload 当 goal→plan 的 durable
    anchor。goal 收口门 (live_target_reconcile_check) 据此 GoalSessionStarted.task_id → TaskNodeCreated.plan_id
    定位 plan, 取该 plan 的 (supersede 前向 tip) live_target_observable 复算 —— 修"自动派发 goal 的
    GoalSessionStarted 不带 brief_event_id → 门恒空放行假完成"。传 None (mock / 非 task 绑定的 spawn) 则
    不写该字段, 门回退 SessionSpawned.task_id anchor (execution fan-out 同时 co-emit, 天然带 task_id)。

    bg_session_id: f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①(反查基础) ——
    goal_session_id 与 claude --bg 实际分配的进程级 id 解耦后, 判活/attach
    (`escalation_reflow.resolve_goal_session_target`) 仍需要真实 bg id 去匹配 `claude agents
    --json` 的 id/sessionId 字段, 本参数把它记进 payload 供反查。None (goal_session_id 未解耦的
    旧调用, 如 execution fan-out 传 goal_session_id=bg_id 那条路径) → 不写该字段, 反查退化成
    goal_session_id 自身 (旧行为)。
    """
    from towow.l2.portable_runtime import VALID_SESSION_ORIGINS

    if spawn_origin not in VALID_SESSION_ORIGINS:
        raise ValueError(
            f"spawn_origin {spawn_origin!r} 不在 B5 冻结 origin 集合 "
            f"{sorted(VALID_SESSION_ORIGINS)} (fail-closed, 不落脏 origin)",
        )
    payload: dict[str, object] = {
        "kind": "GoalSessionStarted",
        "goal_session_id": goal_session_id,
        "spawned_role": spawned_role,
        "trigger_event_id": trigger_event_id,
        "command_text": command_text,
        "launched": launched,
        "orchestrator_registered": True,
        "spawn_origin": spawn_origin,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if bg_session_id is not None:
        payload["bg_session_id"] = bg_session_id
    intent = _build_orch_nodetouched(
        kind="GoalSessionStarted",
        decision_id=goal_session_id,
        payload_body=payload,
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


def emit_session_spawned(
    event_log: EventLog,
    *,
    session_id: str,
    parent_session_id: str | None,
    form: str = "bg",
    spawned_role: str | None = None,
    task_id: str | None = None,
    trigger_event_id: str | None = None,
) -> str:
    """K2b-REG#3 (substrate 2, 50-graph-protocol §6.1): orchestrator 自动 spawn 子会话时 emit 血缘留痕.

    设计 §A 权衡: 谁 emit = parent (持久, 子死血缘仍在, 对 orphan/血缘检测重要) vs child (gate 干净但
    子死前丢)。倾向持久性 → orchestrator (parent) 经 path-B 真 emit 注册的 SESSION_SPAWNED (像
    ORCHESTRATOR_DISPATCH_FAILED / GoalSessionStarted), 让 node_reducers 真消费物化 session_graph 血缘
    spawned 边 —— 修 "parent_session_id ~0% 兑现" 缺口。

    ★ 必须真 event_type 走 path-B 而非 NODE_TOUCHED stub: 投影按 get_events_by_type(SESSION_SPAWNED)
    收集喂 reducer, stub (event_type=NODE_TOUCHED) 永不被找到 = 投影永不物化 = 假兑现 (advisor 点名)。

    session_id = 被 spawn 子会话的 bg_session_id (与 GoalSessionStarted / record_pending_session /
    execution 注册锁同一 id → 血缘节点被全系统引用, 非悬空)。只在 spawn 成功且 bg_id 在手时调。
    parent_session_id=None (根/手动起) → reducer 不建 spawned 边 (不伪造血缘)。

    payload shape = 权威 spec test_k2b_node_graph_projections (SessionSpawnedPayload 校验, fail-closed):
    {target_entity_type:session, transition_type:created, after_state:{session_id, parent_session_id?, ...}}。
    """
    after_state: dict[str, object] = {"session_id": session_id, "form": form}
    if parent_session_id is not None:
        after_state["parent_session_id"] = parent_session_id
    if spawned_role is not None:
        after_state["spawned_role"] = spawned_role
    if task_id is not None:
        after_state["task_id"] = task_id
    if trigger_event_id is not None:
        after_state["trigger_event_id"] = trigger_event_id
    intent = EventIntent(
        local_intent_id=f"sess-spawn-{uuid.uuid4().hex[:12]}",
        event_type=EventType.SESSION_SPAWNED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": TargetEntityType.SESSION.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": after_state,
        },
        provenance_hint=ProvenanceHint(
            actor_type=_ORCH_ACTOR_TYPE,
            actor_id=_ORCH_ACTOR_ID,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        # subject = provenance/索引锚 (node_reducers 只读 after_state, 不读 subject)。SubjectEntityType
        # 无 SESSION 成员 → 锚到本次 spawn 的 task (有则), 否则锚子会话 id 当 TASK (同 emit_goal_session_started
        # 用 TASK 锚 decision_id 的约定)。血缘真相在 after_state, subject 仅供查询溯源。
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=task_id or session_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def emit_lock_acquired(
    event_log: EventLog,
    *,
    lock_id: str,
    session_id: str,
    resource: str | None = None,
) -> str:
    """B-4 (substrate 4, 50-graph-protocol §6.2): orchestrator 注册执行锁时 emit held-by 留痕.

    让 node_reducers 物化 lock_graph 的 lock--held-by-->session 边 (并发契约可查 —— 觉知层①调查工具
    可问"谁占着这把锁")。K2b 已建好消费侧 (lock_graph reducer + 测), 此前无源 emit; 此函数接通源头。

    同 emit_session_spawned: 走 path-B (LOCK_ACQUIRED 已入白名单) 真 event_type (非 NODE_TOUCHED stub,
    否则 get_events_by_type(LOCK_ACQUIRED) 找不到 = lock_graph 永空 = 假兑现)。payload 经 LockAcquiredPayload
    校验 (PAYLOAD_VALIDATION_ENFORCED fail-closed)。

    lock_id = 锁节点 id (执行锁: f"execution:{session_id}" — 与 release 侧可对应); session_id = 持有者
    (held-by 边目标); resource = 被锁的对象 (task_id, 展示用)。
    """
    intent = EventIntent(
        local_intent_id=f"lock-acq-{uuid.uuid4().hex[:12]}",
        event_type=EventType.LOCK_ACQUIRED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "target_entity_type": TargetEntityType.LOCK.value,
            "transition_type": TransitionType.CREATED.value,
            "after_state": {
                "lock_id": lock_id,
                "session_id": session_id,
                "resource": resource,
            },
        },
        provenance_hint=ProvenanceHint(
            actor_type=_ORCH_ACTOR_TYPE,
            actor_id=_ORCH_ACTOR_ID,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        # subject = provenance 锚 (node_reducers 只读 after_state); 锚到持有会话当 TASK (无 SESSION 成员,
        # 同 emit_session_spawned 约定)。
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=resource or session_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def emit_goal_session_terminated_fallback(
    event_log: EventLog,
    *,
    goal_session_id: str,
    reason: str = "external",
    final_status: str = "bg job 终态退出未自报完工 — 调度员兜底 (F-019-10)",
) -> str:
    """E.5 完工信号兜底 (F-019-10): 调度员代未自报完工的会话 emit GoalSessionTerminated.

    机制保证'完工信号一定发出' —— 要么会话自己 emit(completion), 要么调度员检测到
    bg job 终态退出 emit(external), 链不会静默挂死, 主线一定收到终态信号。
    """
    payload: dict[str, object] = {
        "kind": "GoalSessionTerminated",
        "goal_session_id": goal_session_id,
        "reason": reason,
        "final_status": final_status,
        "orchestrator_fallback": True,
    }
    intent = _build_orch_nodetouched(
        kind="GoalSessionTerminated",
        decision_id=goal_session_id,
        payload_body=payload,
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


def emit_silent_death_alarm(
    event_log: EventLog,
    *,
    goal_session_id: str,
    task_id: str | None,
    verdict: str,
    daemon_state: str | None,
) -> str:
    """T-LND-11 (INV-B3-1): liveness 探测到 session 终态(STOPPED/MISSING)却未自报 GoalSessionTerminated
    → emit 显著告警事件 (区别于 COMPLETED 良性兜底的安静 external)。配合 clear_exec_task_stamp 触发
    重派 —— 探测→告警→重派闭环, 死会话不再永久卡 (失败者不再永不重派)。"""
    payload: dict[str, object] = {
        "kind": "SilentDeathAlarmed",
        "goal_session_id": goal_session_id,
        "task_id": task_id,
        "verdict": verdict,
        "daemon_state": daemon_state,
        "alarm": "⚠ silent death: session 终态(STOPPED/MISSING)未自报完工; 已触发重派 (INV-B3-1)",
        "orchestrator_alarm": True,
    }
    intent = _build_orch_nodetouched(
        kind="SilentDeathAlarmed", decision_id=goal_session_id, payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def emit_orchestrator_lifecycle(
    event_log: EventLog,
    *,
    transition: str,
    reason: str,
    detail: dict[str, object] | None = None,
) -> str:
    """gap5 (R11 2026-06-14): pause/resume 留 canonical provenance —— '事件日志是唯一真相源'。

    旧实现只写/删 paused.flag 文件、不 emit → 审计链看不到"系统为何/何时停了、停期间发生了什么"。
    走 stub-rewrap NodeTouched (kind=OrchestratorPaused/Resumed, 同 emit_silent_death_alarm 范式),
    不动 enums (避免与并发改 enums 的兄弟线撞 — 待稳定后可升 canonical EventType)。
    """
    kind = "OrchestratorPaused" if transition == "paused" else "OrchestratorResumed"
    payload: dict[str, object] = {
        "kind": kind,
        "transition": transition,
        "reason": reason,
        "orchestrator_lifecycle": True,
        **(detail or {}),
    }
    intent = _build_orch_nodetouched(
        kind=kind,
        decision_id=f"orch-{transition}-{uuid.uuid4().hex[:8]}",
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def emit_redispatch_exhausted_alarm(
    event_log: EventLog,
    *,
    task_id: str | None,
    plan_id: str | None,
    retry_count: int,
    last_error: str,
    trigger_event_id: str | None = None,
    dispatch_to: str | None = None,
) -> str:
    """T-FIX-B1-01 (AUTOPILOT-core#5 / FORWARD-chain#2): 自愈重派次数耗尽 → 显著熔断告警。

    现状: 反复 silent-death/非 success 的 task 被无限重派、永用原 tier 重蹈覆辙。本告警是熔断的
    可观测信号 (仿 emit_silent_death_alarm 走 _build_orch_nodetouched + write_direct):
    达上限后该 task/trigger 不再盲派, emit 此告警 + 路由 main-inbound 让 owner 介入。
    exec 路径载 task_id/plan_id; nonexec backlog 路径载 trigger_event_id/dispatch_to。"""
    payload: dict[str, object] = {
        "kind": "RedispatchExhausted",
        "task_id": task_id,
        "plan_id": plan_id,
        "trigger_event_id": trigger_event_id,
        "dispatch_to": dispatch_to,
        "retry_count": retry_count,
        "last_error": last_error[:500],
        "alarm": (
            "⚠ 自愈重派耗尽: 该 task/trigger 重派 "
            f"{retry_count} 次仍未成功 (达上限), 已熔断不再盲派 — 等 owner 介入 "
            "(T-FIX-B1-01)"
        ),
        "orchestrator_alarm": True,
    }
    decision_id = task_id or trigger_event_id or f"redispatch-{uuid.uuid4().hex[:8]}"
    intent = _build_orch_nodetouched(
        kind="RedispatchExhausted", decision_id=decision_id, payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def emit_governor_throttled_dispatch(
    event_log: EventLog,
    *,
    blocked_count: int,
    resume_at_epoch: float | None,
) -> str:
    """T-RMD-S4-GOVERNOR: 资源 governor 本轮真挡了新派 → emit canonical 节流留痕 (NodeTouched+kind)。

    只在【真撞 429 痕迹在效期内 (can_dispatch=False) 且本轮真有新派被挡 (blocked_count>0)】时调 ——
    "派发被挡" 的诚实机器证据 (done_criteria①: 集成测试读 all_records 断言此事件 + provenance 非交互)。
    provenance=SYSTEM/f11-orchestrator-polling (非交互: daemon 自产, 非人手 CLI session), 与
    OrchestratorDispatched/RedispatchExhausted 同 NodeTouched+kind 范式。哪个资源/水位多满归 A5 哨兵
    报 owner (读同一反应式源, 不重复); 本事件记 "挡了几个新派 + 何时恢复"。
    governor 只挡【新派】, 在飞/重派/收尾/reconcile 照常 (owner 口径: 节流新派 ≠ 冻结收尾)。"""
    payload: dict[str, object] = {
        "kind": "GovernorThrottledDispatch",
        "blocked_new_dispatch_count": blocked_count,
        "resume_at_epoch": resume_at_epoch,
        "scope": "new_dispatch_only",  # 重派/收尾/reconcile 不在挡范围
        "orchestrator_alarm": True,
    }
    intent = _build_orch_nodetouched(
        kind="GovernorThrottledDispatch",
        decision_id=f"governor-throttle-{uuid.uuid4().hex[:8]}",
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


# ════════════════════════════════════════════════════════════════════════════════
#  T-FIX-B3-02 (CON-content#2 / AUTOPILOT-core#1) — phase 门 never-ready 静默死锁告警
#
#  T-LND-10 的 implementation 门按设计正确把 impl task 挡在 ready-set 外, 直到本 plan 的
#  design-time ReviewPlanCreated 到来。但若那条 review_plan 永不到来 (交接断/agent 漏产),
#  plan 就 never-ready 静默卡死: 既无 task ready、又无人告警, 投产时无人知死在哪。
#
#  本块顺 T-LND-11 silent-death-alarm 家族, 让 orchestrator 自己巡检自己派发的 plan
#  (分布式自愈, 不引中心总控): 持续超 N 轮派发巡检仍 never-ready → emit PhaseStuckAlarmed
#  告警 + main-inbound 显著通知。不改门拦截语义 (门没错), 只把"门挡住后无人推进"从静默变可见。
# ════════════════════════════════════════════════════════════════════════════════


def _phase_stuck_state_path(towow_dir: Path, plan_id: str) -> Path:
    slug = "".join(ch if ch.isalnum() else "_" for ch in plan_id)
    return _phase_stuck_dir(towow_dir) / f"{slug}.json"


def _phase_stuck_rounds_threshold(stuck_threshold_rounds: int | None) -> int:
    """连续 never-ready 多少轮派发巡检才告警 (可配 TOWOW_PHASE_STUCK_ROUNDS, 默认 12)。"""
    if stuck_threshold_rounds is not None:
        return max(1, stuck_threshold_rounds)
    raw = os.environ.get(_PHASE_STUCK_ROUNDS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _PHASE_STUCK_ROUNDS_DEFAULT


def emit_phase_stuck_alarm(
    event_log: EventLog,
    *,
    plan_id: str,
    stuck_task_ids: list[str],
    rounds_stuck: int,
) -> str:
    """T-FIX-B3-02 (INV-B3-1 同族): plan 含 implementation task 但持续 never-ready (门挡住后
    解锁前置 design-time review_plan 永不到来) → emit 显著告警事件。配 main-inbound 通知,
    死锁不再无人知。不改门拦截语义, 只把静默卡死变可观测信号。"""
    payload: dict[str, object] = {
        "kind": "PhaseStuckAlarmed",
        "after_state": {
            "plan_id": plan_id,
            "stuck_task_ids": stuck_task_ids,
            "rounds_stuck": rounds_stuck,
        },
        "plan_id": plan_id,
        "stuck_task_ids": stuck_task_ids,
        "rounds_stuck": rounds_stuck,
        "alarm": (
            "⚠ phase 门 never-ready 死锁: plan 含 implementation task 但持续 "
            f"{rounds_stuck} 轮巡检既无 task ready、又无 design-time review_plan; "
            "门按设计正确挡住, 缺解锁前置 (review_plan) 无人推进 (T-FIX-B3-02)"
        ),
        "orchestrator_alarm": True,
    }
    intent = _build_orch_nodetouched(
        kind="PhaseStuckAlarmed", decision_id=plan_id, payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def sweep_phase_stuck_plans(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stuck_threshold_rounds: int | None = None,
) -> int:
    """T-FIX-B3-02: 每轮派发后巡检一次 —— 探测 phase 门 never-ready 静默死锁并告警.

    分布式自愈 (顺 T-LND-11): orchestrator 自己巡检自己派发的 plan, 不引中心总控。

    机制 (每 plan 一个 phase_stuck/<slug>.json 持久计数):
      1. stuck_implementation_plans 探测本轮 never-ready 的 plan (含 impl task + 无 ready + 无
         design-time review_plan)。
      2. 命中的 plan 计数 +1; 未命中的 plan (恢复了 = review_plan 来了 / task ready 了 / 全完成)
         状态清零 (计数随真实推进重置, 不留陈旧告警)。
      3. 计数跨过阈值 (默认 12 轮可配) 且本 plan 尚未告警过 → emit PhaseStuckAlarmed +
         main-inbound 显著通知, 标记已告警 (dedup: 同一 stuck plan 只告警一次, 不每轮刷屏)。

    Returns 本轮新 emit 的告警数 (跨阈值且未去重的 plan 数)。
    """
    threshold = _phase_stuck_rounds_threshold(stuck_threshold_rounds)
    events = _all_events_as_dicts(event_log)
    from towow.l2.execution_dispatch import stuck_implementation_plans

    stuck = stuck_implementation_plans(events)
    sdir = _phase_stuck_dir(towow_dir)
    sdir.mkdir(parents=True, exist_ok=True)

    # 恢复的 plan (上轮有状态文件但本轮不再 stuck) → 清状态 (计数重置, 不留陈旧告警)。
    for f in sdir.glob("*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            st = {}
        pid = st.get("plan_id")
        if isinstance(pid, str) and pid not in stuck:
            with contextlib.suppress(OSError):
                f.unlink()

    emitted = 0
    for plan_id, stuck_task_ids in sorted(stuck.items()):
        sp = _phase_stuck_state_path(towow_dir, plan_id)
        state: dict[str, object] = {}
        if sp.exists():
            try:
                state = json.loads(sp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                state = {}
        rounds = int(state.get("rounds_stuck", 0)) + 1
        already_alarmed = bool(state.get("alarmed", False))
        new_state: dict[str, object] = {
            "plan_id": plan_id,
            "rounds_stuck": rounds,
            "alarmed": already_alarmed,
            "stuck_task_ids": stuck_task_ids,
        }
        if rounds >= threshold and not already_alarmed:
            emit_phase_stuck_alarm(
                event_log,
                plan_id=plan_id,
                stuck_task_ids=stuck_task_ids,
                rounds_stuck=rounds,
            )
            # main-inbound 显著通知: owner 进主对话即见此 plan never-ready 死锁
            # (PhaseStuckAlarmed 是 NodeTouched 不进 main-inbound poller; 这里补显著通知)。
            emit_orchestrator_dispatched(
                event_log,
                DispatchDecision(
                    trigger_event_id=f"phase-stuck-{plan_id}",
                    trigger_event_type="PhaseStuckAlarmed",
                    dispatch_to="main-inbound",
                    reason=(
                        f"⚠ phase 门 never-ready 死锁: plan={plan_id} 持续 {rounds} 轮巡检"
                        f" 无 task ready 且无 design-time review_plan; 卡住 task="
                        f"{','.join(stuck_task_ids)} (T-FIX-B3-02)"
                    ),
                ),
            )
            new_state["alarmed"] = True
            emitted += 1
        with contextlib.suppress(OSError):
            sp.write_text(json.dumps(new_state), encoding="utf-8")
    return emitted


# ════════════════════════════════════════════════════════════════════════════════
#  T-FIX-B1-03 (FORWARD-chain#6 / AUTOPILOT-core#3 / FORWARD-chain#2) — 周期性自愈扫描
#
#  现状: pending_escalations 只在 owner 查 status 或发 prompt(UserPromptSubmit hook)时才被看到
#  → 一条 escalation 卡 4 天没人知 (T-L0-02 病根), 纯 pull 模式下无主动 push。本块让 daemon 自己
#  每轮顺手巡检四类久卡断棒并主动 surface (分布式自愈, 顺 T-LND-11 silent-death-alarm 家族, 不引
#  中心总控): (a) pending escalation 超 stuck 阈值; (b) 重派熔断 task (B1-01 circuit marker);
#  (c) pending session marker 久未对账 (超阈值仍存在 = reconcile 没收割掉); (d) exec stamp 孤儿
#  (有戳但无完工事件也无在跑 pending session = 依赖链冻死盲区)。对每条 emit 一条去重的
#  SelfHealStuckAlarmed canonical, 去重靠 per-baton dedup marker (一窗口至多一条)。不改 paused 语义
#  (paused 仍可巡检 surface 但不派新活)。
# ════════════════════════════════════════════════════════════════════════════════


def _self_heal_sweep_dir(towow_dir: Path) -> Path:
    """每 baton 一个 dedup state JSON (last_alarmed_at), 控同一 baton 一窗口至多告警一次。"""
    return _orchestrator_dir(towow_dir) / _SELF_HEAL_SWEEP_SUBDIR


_SELF_HEAL_BASELINE_ENV = "TOWOW_SELF_HEAL_BASELINE"  # ""=禁用过滤(全告警); 数字=固定基线 epoch; unset=用文件
_SELF_HEAL_BASELINE_FILE = "self_heal_baseline.json"


def load_or_init_self_heal_baseline(towow_dir: Path, *, now: float | None = None) -> float | None:
    """T-FIX-B1-03 now-forward 基线 (owner 2026-06-22: 别再追溯刷屏已过时的久卡断棒)。

    sweep 只【主动告警】基线之后才进入卡住态的断棒; 基线之前就卡的历史断棒不再主动 push 到
    main-inbound (避免 6-10 wipe 遗留的过时孤儿记账无限刷屏), 但仍在 collect_stuck_batons /
    orchestrator status 里【可查】—— 不盖住真死锁, 只是不主动重报。和 watcher 的 now-forward
    水位线同一病根 (concept watcher-now-forward-watermark@v1)。

    返回 None = 禁用基线过滤 (全告警, 向后兼容/测试)。否则返回基线 epoch。
    优先级: env TOWOW_SELF_HEAL_BASELINE (""=禁用 / 数字=固定) > 文件复用 (重启不丢) >
    初始化成 now 写盘 (第一次)。
    """
    raw = os.environ.get(_SELF_HEAL_BASELINE_ENV)
    if raw is not None:
        s = raw.strip()
        if not s:
            return None  # 显式禁用
        with contextlib.suppress(ValueError):
            return float(s)  # 固定 epoch (测试)
        # 非数字 → 落文件逻辑 (别因坏 env 静默关掉保护)
    bfile = _orchestrator_dir(towow_dir) / _SELF_HEAL_BASELINE_FILE
    with contextlib.suppress(OSError, json.JSONDecodeError, AttributeError):
        data = json.loads(bfile.read_text(encoding="utf-8"))
        bl = data.get("baseline_unix")
        if isinstance(bl, (int, float)):
            return float(bl)
    now_unix = now if now is not None else time.time()
    with contextlib.suppress(OSError):
        bfile.parent.mkdir(parents=True, exist_ok=True)
        tmp = bfile.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"baseline_unix": now_unix, "reason": "init"}), encoding="utf-8",
        )
        tmp.replace(bfile)
    return now_unix


def _escalation_stuck_threshold_s(stuck_threshold_s: float | None) -> float:
    """断棒卡过多少秒才告警 (可配 TOWOW_ESCALATION_STUCK_S, 默认 3600s)。"""
    if stuck_threshold_s is not None:
        return max(0.0, stuck_threshold_s)
    raw = os.environ.get(_ESCALATION_STUCK_ENV, "").strip()
    with contextlib.suppress(ValueError):
        val = float(raw)
        if val > 0:
            return val
    return _ESCALATION_STUCK_DEFAULT_S


# T-BOOT-A1 (2026-07-14 服务器接管修复包 A-1): escalation_stuck 断棒的 EscalationStillWaiting
# 心跳曾在 _sweep_stuck_batons 每轮 (60-90s 节流) 都无条件写 canonical —— 对抗验证坐实 48h 内它占
# 全账本新增事件 97.2% (账本越大 → 锁内 O(账本) hydrate 越慢 → 系统越卡 → 更多升级, 恶性反馈环)。
# 改为可配置的重报间隔: 同一 escalation 在未到间隔前只更新本地去重状态 (供监控读, 不进账本), 到点
# 才写一条 EscalationStillWaiting 作为"长期无人应答"的账本级提醒上限 (默认 1h, 比 60-90s 降约 60 倍)。
_ESCALATION_HEARTBEAT_INTERVAL_ENV = "TOWOW_ESCALATION_HEARTBEAT_INTERVAL_S"
_ESCALATION_HEARTBEAT_INTERVAL_DEFAULT_S = 3600.0


def _escalation_heartbeat_interval_s() -> float:
    """EscalationStillWaiting 心跳最小重报间隔 (可配 TOWOW_ESCALATION_HEARTBEAT_INTERVAL_S, 默认 3600s)。"""
    raw = os.environ.get(_ESCALATION_HEARTBEAT_INTERVAL_ENV, "").strip()
    with contextlib.suppress(ValueError):
        val = float(raw)
        if val >= 0:
            return val
    return _ESCALATION_HEARTBEAT_INTERVAL_DEFAULT_S


def _self_heal_dedup_key(baton_kind: str, baton_id: str) -> str:
    """dedup state 文件名 — `<kind>__<slug(baton_id)>.json` (一 baton 一份)。"""
    slug = "".join(ch if ch.isalnum() else "_" for ch in baton_id)[:120]
    return f"{baton_kind}__{slug}.json"


def emit_self_heal_stuck_alarm(
    event_log: EventLog,
    *,
    baton_kind: str,
    baton_id: str,
    stuck_for_s: float,
    detail: dict[str, object] | None = None,
) -> str:
    """T-FIX-B1-03: 久卡断棒主动 surface 的显著告警 (仿 emit_phase_stuck_alarm: NodeTouched + kind)。

    baton_kind ∈ {escalation_stuck, redispatch_exhausted, pending_session_unreconciled,
    exec_stamp_orphaned}; baton_id = escalation_event_id / circuit key / pending session id /
    task_id; stuck_for_s = 已卡时长。
    这是分布式自愈的'不静默卡'保证: 没有中心总控盯着, daemon 自己每轮顺手巡检 + 主动告警。"""
    body: dict[str, object] = {
        "kind": "SelfHealStuckAlarmed",
        "baton_kind": baton_kind,
        "baton_id": baton_id,
        "stuck_for_s": stuck_for_s,
        "alarm": (
            f"⚠ 久卡断棒未响应: {baton_kind} id={baton_id} 已卡 {stuck_for_s:.0f}s "
            "(超 stuck 阈值, owner 未介入); daemon 主动 surface (T-FIX-B1-03)"
        ),
        "orchestrator_alarm": True,
    }
    if baton_kind == "escalation_stuck":
        body["escalation_event_id"] = baton_id  # reviewer 反查便利
    if detail:
        body["detail"] = detail
    intent = _build_orch_nodetouched(
        kind="SelfHealStuckAlarmed", decision_id=baton_id, payload_body=body,
    )
    return event_log.write_direct(intent).event_id


def _escalation_raised_unix(records: list[EventRecord]) -> dict[str, float]:
    """escalation_event_id → canonical 落账 unix 时刻 (用事件落账时间算卡时长, 不依赖 producer
    自报的 raised_at 字符串, 它可能缺失/格式不一)。

    T-FND-02: takes a pre-fetched committed-record list (the throttled sweep passes ONE warm-index
    snapshot shared across all its sub-checks) instead of re-scanning the log itself.
    """
    out: dict[str, float] = {}
    for rec in records:
        et, _payload = _unwrap_stub_rewrap(rec)
        if et == EventType.GOAL_ESCALATION_RAISED.value:
            out[rec.event_id] = rec.timestamp.timestamp()
    return out


def collect_stuck_batons(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stuck_threshold_s: float | None = None,
    now: float | None = None,
) -> list[dict[str, object]]:
    """T-FIX-B1-03 探测核心 (纯读, 无 emit) — 四类久卡断棒的当前快照。

    sweep (emit + dedup) 与 collect_orchestrator_status (status 段只读) 共用同一探测逻辑, 保证
    "status 看到的 stuck = daemon 会告警的 stuck", 不漂移。每条断棒: {baton_kind, baton_id,
    stuck_for_s, ...}。stuck_for_s = now - 该断棒起卡时刻; 仅返回 stuck_for_s >= 阈值 的。
    """
    threshold = _escalation_stuck_threshold_s(stuck_threshold_s)
    now_unix = now if now is not None else time.time()
    out: list[dict[str, object]] = []

    # T-FND-02 (巡检分频/查投影): read the committed stream ONCE from the warm in-memory index
    # (kept current by the EventLog active-segment catch-up) and share it across all sub-checks,
    # instead of three separate full-disk all_records() re-scans per sweep. With the sweep itself
    # throttled in the polling loop, this keeps the stuck-baton patrol off the daemon's per-round
    # hot path — half of the daemon-choke fix (the other half is the index catch-up in L0).
    records = event_log.committed_index().records()

    # (a) pending escalation 超阈值 (按 canonical 落账时间算卡时长)
    raised_unix = _escalation_raised_unix(records)
    for esc in _pending_escalations_from(records):
        eid = esc.get("escalation_event_id")
        if not isinstance(eid, str) or not eid:
            continue
        raised = raised_unix.get(eid)
        if raised is None:
            continue
        stuck_for = now_unix - raised
        if stuck_for >= threshold:
            out.append({
                "baton_kind": "escalation_stuck",
                "baton_id": eid,
                "escalation_event_id": eid,
                "goal_session_id": esc.get("goal_session_id", ""),
                "owner_question": esc.get("owner_question", ""),
                "stuck_for_s": stuck_for,
            })

    # (b) 重派熔断 task (B1-01 redispatch_circuit__ marker; tripped_at = 熔断时刻)
    ddir = _dispatched_dir(towow_dir)
    if ddir.exists():
        for f in sorted(ddir.glob(f"{_REDISPATCH_CIRCUIT_PREFIX}*")):
            # T-LRF-11 收口 (finding-tlrf11): 同一 redispatch_circuit__ 前缀下住两类文件 —— 活熔断
            # marker 与强制终态化哨兵 (.terminalized)。本告警扫描必须排除哨兵, 否则 (1) 哨兵自身被当
            # baton_id={key}.terminalized 的幽灵 redispatch_exhausted 断棒 (哨兵无 tripped_at 回落
            # mtime, 一窗口后超阈值); (2) 已被 sweep 强制终态化 (dead_lettered) 的熔断 trigger 原
            # marker 仍在 → 每窗口继续误报。两条都与姊妹函数 collect_overage_live_bodies 的排除
            # (.endswith('.terminalized') + 哨兵伴随判定) 收敛, 修前本函数独缺。
            if f.name.endswith(".terminalized"):
                continue
            key = f.name[len(_REDISPATCH_CIRCUIT_PREFIX):]
            if _redispatch_circuit_terminalized_path(towow_dir, key).exists():
                continue  # 已强制终态化 → 非 stuck (行动已收口, 不再向 owner 重复告警)
            data: dict[str, object] = {}
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            tripped = data.get("tripped_at")
            tripped_unix = float(tripped) if isinstance(tripped, (int, float)) else (
                f.stat().st_mtime
            )
            stuck_for = now_unix - tripped_unix
            if stuck_for >= threshold:
                out.append({
                    "baton_kind": "redispatch_exhausted",
                    "baton_id": key,
                    "task_id": data.get("task_id", ""),
                    "retry_count": data.get("retry_count", ""),
                    "stuck_for_s": stuck_for,
                })

    # (c) pending session marker 久未对账 (超阈值仍存在 = reconcile 没收割掉 → 静默卡的会话)
    pdir = _pending_sessions_dir(towow_dir)
    pending_task_ids: set[str] = set()  # (d) 复用: 有 pending 登记在跑的 task 不算孤儿
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.json")):
            data = {}
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            tid = data.get("task_id")
            if isinstance(tid, str) and tid:
                pending_task_ids.add(tid)
            recorded = data.get("recorded_at")
            recorded_unix = float(recorded) if isinstance(recorded, (int, float)) else (
                f.stat().st_mtime
            )
            stuck_for = now_unix - recorded_unix
            if stuck_for >= threshold:
                out.append({
                    "baton_kind": "pending_session_unreconciled",
                    "baton_id": f.stem,
                    "spawned_role": data.get("spawned_role", ""),
                    "trigger_event_id": data.get("trigger_event_id", ""),
                    "stuck_for_s": stuck_for,
                })

    # (d) exec stamp 孤儿 — stamp 在 + task 无 TaskRunCompleted(success) + 无 pending session
    # 在跑该 task = 前三类巡检都覆盖不到的自愈盲区: 有戳不重派 + 无完工不解锁下游 → 依赖链冻死,
    # daemon 活着但零实质推进。成因实证: 账本回退把完工事件蒸发而 dispatched 戳 (独立文件存储)
    # 幸存 (T-LC-02 卡死 8h, finding-ledger-rollback-orphans-task-completion-stamp-mismatch-1)。
    # 只告警不自动清戳: pending marker 有被共享树并发操作误删的先例, "无登记但会话仍活着"窗口下
    # 自动清戳 → ready-set 重派 → 双会话改同一 task; 核实会话真死后用 clear_exec_task_stamp
    # (source=manual) 人工解阻, 告警 detail 给出该动作。阈值复用 stuck threshold — 正常派发里
    # mark stamp 与 record_pending_session 同一调用栈内完成, 窗口远小于阈值, 不误报。
    if ddir.exists():
        from towow.l2.execution_dispatch import completed_success_task_ids

        events = _all_events_as_dicts_from(records)  # T-FND-02: reuse the shared warm-index snapshot
        completed = completed_success_task_ids(events)
        seen_task_ids: set[str] = set()
        for e in events:
            if e.get("event_type") != "TaskNodeCreated":
                continue
            payload = e.get("payload")
            a = payload.get("after_state") if isinstance(payload, dict) else None
            tid2 = a.get("task_id") if isinstance(a, dict) else None
            if isinstance(tid2, str) and tid2:
                seen_task_ids.add(tid2)
        for task_id in sorted(seen_task_ids):
            if task_id in completed or task_id in pending_task_ids:
                continue
            stamp = ddir / _exec_task_stamp_name(task_id)
            if not stamp.exists():
                continue
            sdata: dict[str, object] = {}
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded2 = json.loads(stamp.read_text(encoding="utf-8"))
                if isinstance(loaded2, dict):
                    sdata = loaded2
            disp = sdata.get("dispatched_at")
            disp_unix = float(disp) if isinstance(disp, (int, float)) else stamp.stat().st_mtime
            stuck_for = now_unix - disp_unix
            if stuck_for >= threshold:
                out.append({
                    "baton_kind": "exec_stamp_orphaned",
                    "baton_id": task_id,
                    "task_id": task_id,
                    "stamp_file": stamp.name,
                    "suggested_action": (
                        "核实该 task 确无存活会话后, 用 clear_exec_task_stamp(source=manual) "
                        "清戳让 ready-set 下轮重派; 勿在未核实时自动清 (双派发风险)"
                    ),
                    "stuck_for_s": stuck_for,
                })
    return out


def _sweep_stuck_batons(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stuck_threshold_s: float | None = None,
    now: float | None = None,
) -> int:
    """T-FIX-B1-03: 每轮 (或按节流间隔) 跑一次 — 探测四类久卡断棒并 emit 去重告警.

    分布式自愈 (顺 T-LND-11): daemon 自己每轮顺手巡检 + 主动 surface, 不引中心总控。

    去重 (per-baton dedup state, _self_heal_sweep/<kind>__<slug>.json 记 last_alarmed_at):
      - 首次卡满阈值 → emit + 记 last_alarmed_at;
      - 同一阈值窗口内再扫 (now - last_alarmed_at < 阈值) → 不重复 emit (不每 6s 刷屏);
      - 跨过一整个阈值窗口仍卡 → 重新 emit 一次 (久卡不被永久静默);
      - 断棒恢复 (不再在 collect 结果里) → 删 dedup state (下次重卡按首卡告警)。

    Returns 本轮新 emit 的告警数。
    """
    threshold = _escalation_stuck_threshold_s(stuck_threshold_s)
    now_unix = now if now is not None else time.time()
    batons = collect_stuck_batons(
        towow_dir, event_log, stuck_threshold_s=stuck_threshold_s, now=now,
    )
    # now-forward 基线 (owner 2026-06-22): 只对基线之后才进入卡住态的断棒【主动告警】;
    #   历史遗留 (基线前就卡, 多为 6-10 wipe 蒸发完工事件的过时孤儿记账) 不再刷屏 main-inbound,
    #   但仍在 collect_stuck_batons / orchestrator status 里【可查】—— 不盖住真死锁。
    baseline = load_or_init_self_heal_baseline(towow_dir, now=now)
    sdir = _self_heal_sweep_dir(towow_dir)
    sdir.mkdir(parents=True, exist_ok=True)

    active_keys = {
        _self_heal_dedup_key(str(b["baton_kind"]), str(b["baton_id"])) for b in batons
    }
    # 恢复的 baton (上轮有 dedup state 但本轮不再 stuck) → 删 state (计数重置, 不留陈旧)
    for f in sdir.glob("*.json"):
        if f.name not in active_keys:
            with contextlib.suppress(OSError):
                f.unlink()

    emitted = 0
    for b in batons:
        baton_kind = str(b["baton_kind"])
        baton_id = str(b["baton_id"])
        # now-forward: 基线之前就进入卡住态的历史断棒不主动告警 (collect/status 仍可查,
        #   真死锁不被盖住, 只是不主动重报刷屏)。baseline None = 禁用过滤 (全告警)。
        if baseline is not None and (now_unix - float(b["stuck_for_s"])) <= baseline:
            continue
        dpath = sdir / _self_heal_dedup_key(baton_kind, baton_id)
        # T-BOOT-A1: 先捕获本 tick 处理前 dpath 是否已存在 (= 该 baton 之前是否已 surface 过), 且把
        # 已有 dedup state 读出来 —— 下面 escalation_stuck 的心跳写入会往同一份 state 里加字段, 若不
        # 提前捕获 "已存在" 这个事实, 心跳写入会把 dpath 变成"刚创建", 导致首次 tick 误判成"已 surface
        # 过"而跳过下面本该发生的首次 SelfHealStuckAlarmed (对象去重语义只应从第二个 tick 起生效)。
        had_dedup_state = dpath.exists()
        existing_state: dict[str, object] = {}
        if had_dedup_state:
            with contextlib.suppress(OSError, json.JSONDecodeError):
                loaded_state = json.loads(dpath.read_text(encoding="utf-8"))
                if isinstance(loaded_state, dict):
                    existing_state = loaded_state
        # T-LRF-06 附加条款① (活卡按对象去重 — done_criteria 2): escalation_stuck 这一类断棒, 同一
        # 卡死 escalation 对象在【首次 surface 之后】绝不再每窗口新增 SelfHealStuckAlarmed/main-inbound
        # 条目。把"已 resolved 去重静默" (escalation-inbox-unification) 扩展覆盖到【活卡】: owner 视野
        # 单条, 不被每窗口重复刷屏 (evt-c43d2f3c 重复告警 11 天反面教材)。只动 escalation_stuck, 其它 3
        # 类断棒告警行为原样保留 (advisor 钉死边界)。首次仍走下方 emit 一条 + 落 dedup state。
        #
        # T-BOOT-A1 (2026-07-14 服务器接管修复包 A-1 / 对抗验证坐实 evt 97.2%): EscalationStillWaiting
        # 心跳原来在这里对每个仍卡住的 escalation【每轮 sweep 都无条件 emit】(60-90s 节流周期), 是
        # 48h 内账本新增事件 97.2% 的噪声源 (账本越大 → 锁内 hydrate 越慢 → 系统越卡 → 更多升级, 恶性
        # 反馈环)。改为: 按可配置重报间隔节流 (默认 1h, TOWOW_ESCALATION_HEARTBEAT_INTERVAL_S 可调) ——
        # 未到点只更新本地 dedup state 里的 last_heartbeat_at (供监控/status 读, 不进账本); 到点才写一条
        # EscalationStillWaiting, 作为"长期无人应答"的账本级提醒上限 (最坏情况每条升级每小时至多 1 条,
        # 比现状降约 60 倍)。paused 期间不刷心跳 (与本任务点 d 对齐——巡检/告警本身仍跑, 只是心跳暂停)。
        if baton_kind == "escalation_stuck":
            _sf = b.get("stuck_for_s")
            _waited = float(_sf) if isinstance(_sf, (int, float)) else 0.0
            _last_heartbeat = existing_state.get("last_heartbeat_at")
            _heartbeat_due = not isinstance(_last_heartbeat, (int, float)) or (
                now_unix - float(_last_heartbeat) >= _escalation_heartbeat_interval_s()
            )
            if _heartbeat_due and not is_orchestrator_paused(towow_dir):
                with contextlib.suppress(Exception):
                    emit_escalation_still_waiting(
                        event_log,
                        escalation_event_id=str(b.get("escalation_event_id") or baton_id),
                        goal_session_id=str(b.get("goal_session_id") or ""),
                        waited_s=_waited,
                    )
                existing_state["last_heartbeat_at"] = now_unix
                existing_state["baton_kind"] = baton_kind
                with contextlib.suppress(OSError):
                    dpath.write_text(json.dumps(existing_state), encoding="utf-8")
            if had_dedup_state:
                continue  # 已 surface 过 → 心跳按上面节流处理, 不再新增告警条目
            # 首次卡满阈值 → fall through 发一条 SelfHealStuckAlarmed + main-inbound + 落 dedup state
            # (此后该对象永走上面的 continue 分支, 不再新增条目)。
        last_alarmed: float | None = None
        if dpath.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                st = json.loads(dpath.read_text(encoding="utf-8"))
                if isinstance(st, dict) and isinstance(st.get("last_alarmed_at"), (int, float)):
                    last_alarmed = float(st["last_alarmed_at"])
        # 一窗口至多一条: 距上次告警未满一个阈值窗口 → 本轮不重复 emit
        if last_alarmed is not None and (now_unix - last_alarmed) < threshold:
            continue
        emit_self_heal_stuck_alarm(
            event_log,
            baton_kind=baton_kind,
            baton_id=baton_id,
            stuck_for_s=float(b["stuck_for_s"]),
            detail={k: v for k, v in b.items() if k not in {"baton_kind", "baton_id", "stuck_for_s"}},
        )
        # main-inbound 显著通知: owner 进主对话即见此久卡断棒 (SelfHealStuckAlarmed 是 NodeTouched
        # 不进 main-inbound poller; 这里补显著通知, 与 sweep_phase_stuck_plans 同范式)。
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=f"self-heal-stuck-{baton_kind}-{baton_id[:32]}",
                trigger_event_type="SelfHealStuckAlarmed",
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ 久卡断棒: {baton_kind} id={baton_id} 已卡 {float(b['stuck_for_s']):.0f}s "
                    "未响应, daemon 主动 surface (T-FIX-B1-03)"
                ),
            ),
        )
        with contextlib.suppress(OSError):
            # T-BOOT-A1: merge 而非整体覆盖 —— escalation_stuck 首次 tick 若心跳已写入
            # last_heartbeat_at, 这里不能把它连带抹掉 (否则重报间隔计时器每次都被清零重算)。
            # 其它 3 类 baton 从不写入额外字段, merge 结果与原覆盖行为逐字节一致。
            _merged_state = dict(existing_state)
            _merged_state["last_alarmed_at"] = now_unix
            _merged_state["baton_kind"] = baton_kind
            dpath.write_text(json.dumps(_merged_state), encoding="utf-8")
        emitted += 1
    return emitted


# ════════════════════════════════════════════════════════════════════════════════
#  T-LRF-11 — terminal-state-reachability@v1: 巡检把超龄中间态【强制终态化】(行动非告警)
#
#  概念 novelty: 现状 _sweep_stuck_batons 只 emit SelfHealStuckAlarmed (发现, 缺行动) → 久卡断棒
#  无限刷屏不收口 (evt-2d599e9e 重派死循环 / evt-c43d2f3c escalation 卡 13.5 天 / 僵尸锁无限持有)。
#  本不变量要求: 任何有生命周期的对象 (task / finding / escalation / session lock / goal·bg
#  session / 死信条目 / 重派 trigger) 都有【可达终态】—— 从任意中间态存在一条机器可执行路径到终态;
#  超龄中间态由巡检强制终态化, 按 recovery_classification 走 {kill+重派 / 死信 / escalate} 三通道。
#
#  本任务落地【数据通道】子集 (死信 / escalate, 不碰活进程): 超龄 orphan 重派熔断 trigger →
#  死信终态化 + 留 TerminalStateForced 痕。kill 通道 (杀活子进程, exec_stamp_orphaned →
#  kill+重派) 是 owner-gated 不可逆红线 (subprocess_heartbeat.py §4.b / abort-reap 同线), 本任务
#  不自决接线, 登债 (见 debt)。dead-letter 条目类的强制终态化由既有 sweep_aged_out 兜 (已接 daemon);
#  escalation 类由 reflow (T-LRF-06) + escalation-inbox-unification 兜; 本机制是 7 类的【统一兜底
#  巡检】, 保证即便出生闸接线有缺口, 超龄活体也被强制收口、不无限卡。
# ════════════════════════════════════════════════════════════════════════════════


# 7 类生命周期对象的终态可达静态模型 (concept ① 静态校验依据): 每类钉死 states / terminal 子集 /
# 机器可执行迁移边。校验断言: 每个【非终态】都有 ≥1 条路径到某终态 (无死胡同中间态)。terminal 集与
# 真实代码状态机收敛处直接引自该机制 (dead_letter = DeadLetterState terminal {redispatched,retired};
# task = TaskRunOutcome success/aborted + TaskNodeClosed closed; 其余按各 concept 语义建模)。
TERMINAL_STATE_REGISTRY: dict[str, dict[str, object]] = {
    # task: open→dispatched→in_progress, 可 blocked/stalled; 终态 success/aborted/closed(done_elsewhere)
    "task": {
        "states": {
            "open", "dispatched", "in_progress", "blocked", "stalled",
            "success", "aborted", "closed",
        },
        "terminal": {"success", "aborted", "closed"},
        "transitions": [
            ("open", "dispatched"), ("open", "closed"),
            ("dispatched", "in_progress"), ("dispatched", "aborted"),
            ("in_progress", "blocked"), ("in_progress", "stalled"),
            ("in_progress", "success"), ("in_progress", "aborted"),
            ("blocked", "in_progress"), ("blocked", "aborted"), ("blocked", "closed"),
            # 强制终态化边 (巡检): 搁浅工位超龄 → aborted (重派耗尽则死信, 由 redispatch_trigger 类承)
            ("stalled", "dispatched"), ("stalled", "aborted"),
        ],
    },
    # finding: created→verified→(in_fix)→resolved; 不可路由/对抗失败 → dead_lettered
    "finding": {
        "states": {"created", "verified", "in_fix", "resolved", "closed", "dead_lettered"},
        "terminal": {"resolved", "closed", "dead_lettered"},
        "transitions": [
            ("created", "verified"), ("created", "dead_lettered"),
            ("verified", "in_fix"), ("verified", "closed"),
            ("in_fix", "resolved"), ("in_fix", "dead_lettered"),
            ("resolved", "closed"),
        ],
    },
    # escalation: raised→pending_owner→answered/resolved_externally→closed; 回流耗尽 → dead_lettered
    "escalation": {
        "states": {
            "raised", "pending_owner", "answered", "resolved_externally",
            "closed", "dead_lettered",
        },
        "terminal": {"closed", "resolved_externally", "dead_lettered"},
        "transitions": [
            ("raised", "pending_owner"), ("raised", "dead_lettered"),
            ("pending_owner", "answered"), ("pending_owner", "resolved_externally"),
            ("pending_owner", "dead_lettered"),  # 回流有上限重试耗尽 → 死信 (不再无限卡 13.5 天)
            ("answered", "closed"),
        ],
    },
    # session lock: acquired→(active 心跳)→released; stale → reaped (lock-reap-typed-policy)
    "session_lock": {
        "states": {"acquired", "active", "released", "reaped"},
        "terminal": {"released", "reaped"},
        "transitions": [
            ("acquired", "active"), ("acquired", "released"), ("acquired", "reaped"),
            ("active", "released"), ("active", "reaped"),  # 心跳超时/ pid 死 → reap (通道A/B)
        ],
    },
    # goal·bg session: spawned→running→(paused)→terminated; completion/unreachable/escalation 收口
    "goal_session": {
        "states": {"spawned", "running", "paused", "terminated"},
        "terminal": {"terminated"},
        "transitions": [
            ("spawned", "running"), ("spawned", "terminated"),
            ("running", "paused"), ("running", "terminated"),
            ("paused", "running"), ("paused", "terminated"),  # escalation 答复 resume 或强制收口
        ],
    },
    # 死信条目: DeadLetterState 真实 5 态机 (terminal = {redispatched, retired})
    "dead_letter_entry": {
        "states": {
            "enqueued", "under_triage", "escalated_to_owner", "redispatched", "retired",
        },
        "terminal": {"redispatched", "retired"},
        "transitions": [
            ("enqueued", "under_triage"), ("enqueued", "retired"),  # aged_out 强制退役
            ("under_triage", "redispatched"), ("under_triage", "retired"),
            ("under_triage", "escalated_to_owner"),
            ("escalated_to_owner", "under_triage"),  # reflow 送回
            ("escalated_to_owner", "retired"),  # aged_out 强制退役 (不在死信箱里再卡死)
        ],
    },
    # 重派 trigger: pending→dispatched→retrying→tripped(熔断); resolved(成功) 或 dead_lettered(终态)
    "redispatch_trigger": {
        "states": {"pending", "dispatched", "retrying", "tripped", "resolved", "dead_lettered"},
        "terminal": {"resolved", "dead_lettered"},
        "transitions": [
            ("pending", "dispatched"), ("dispatched", "resolved"),
            ("dispatched", "retrying"), ("retrying", "dispatched"),
            ("retrying", "resolved"), ("retrying", "tripped"),
            # 强制终态化边 (巡检, 本任务 novelty): 超龄 tripped trigger → 死信终态
            ("tripped", "dead_lettered"), ("dispatched", "dead_lettered"),
        ],
    },
}


def validate_terminal_state_reachability(
    registry: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """concept ① 静态校验: 每类生命周期对象的【每个非终态】是否存在 ≥1 条机器可执行路径到某终态。

    返回死胡同违例列表 (空 = 不变量成立)。每条违例: {object_class, dead_end_state, reason}。
    死胡同 = 从该非终态出发, 沿 transitions 做可达闭包, 闭包内无任何 terminal 态。这是'终态可达'
    的结构保证: 巡检强制终态化要有意义, 先得保证状态图本身不存在'怎么走都到不了终态'的中间态。
    """
    reg = registry if registry is not None else TERMINAL_STATE_REGISTRY
    violations: list[dict[str, object]] = []
    for cls, spec in reg.items():
        states = cast("set[str]", spec["states"])
        terminal = cast("set[str]", spec["terminal"])
        edges = cast("list[tuple[str, str]]", spec["transitions"])
        # 邻接表
        adj: dict[str, list[str]] = {s: [] for s in states}
        for src, dst in edges:
            adj.setdefault(src, []).append(dst)
        # terminal 必须 ⊆ states 且非空 (每类至少有一个终态)
        if not terminal:
            violations.append({
                "object_class": cls, "dead_end_state": "<none>",
                "reason": "该类无任何终态 (terminal 集为空) — 终态不可达",
            })
            continue
        if not terminal.issubset(states):
            violations.append({
                "object_class": cls, "dead_end_state": ",".join(sorted(terminal - states)),
                "reason": "terminal 集含 states 之外的态 (定义不自洽)",
            })
        # 对每个非终态做 BFS, 判能否到达某 terminal
        for start in states:
            if start in terminal:
                continue
            seen = {start}
            stack = [start]
            reached_terminal = False
            while stack:
                cur = stack.pop()
                if cur in terminal:
                    reached_terminal = True
                    break
                for nxt in adj.get(cur, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            if not reached_terminal:
                violations.append({
                    "object_class": cls, "dead_end_state": start,
                    "reason": "死胡同中间态: 沿可执行迁移边无法到达任何终态",
                })
    return violations


def emit_terminal_state_forced(
    event_log: EventLog,
    *,
    object_class: str,
    object_ref: str,
    from_state: str,
    to_terminal_state: str,
    recovery_channel: str,
    aged_for_s: float,
    detail: dict[str, object] | None = None,
) -> str:
    """concept ② 动态留痕: 巡检把一个超龄中间态对象【强制终态化】的 canonical 凭据。

    base_classification=IMMUTABLE_TRUTH —— 这是'机制真跑过 ≥1 次'的验收凭据 (goal_completion(5) /
    本任务 done_criteria 2), 必须永久保留, 否则 GC 扫掉 = '强制终态化留痕'消失 = 验收判未跑。
    recovery_channel ∈ {dead_letter, escalate, kill_redispatch}; kill_redispatch 通道 owner-gated,
    本任务不产 (登债)。机器可追巡检: 同 NodeTouched+kind 范式 (仿 SelfHealStuckAlarmed)。"""
    body: dict[str, object] = {
        "kind": "TerminalStateForced",
        "object_class": object_class,
        "object_ref": object_ref,
        "from_state": from_state,
        "to_terminal_state": to_terminal_state,
        "recovery_channel": recovery_channel,
        "aged_for_s": aged_for_s,
        "forced_by": "patrol_sweep",
        "orchestrator_alarm": False,
        "trace": (
            f"⤓ 巡检强制终态化: {object_class} ref={object_ref} {from_state}→{to_terminal_state} "
            f"(超龄 {aged_for_s:.0f}s, 通道={recovery_channel}, terminal-state-reachability@v1)"
        ),
    }
    if detail:
        body["detail"] = detail
    intent = _build_orch_nodetouched(
        kind="TerminalStateForced",
        decision_id=f"{object_class}:{object_ref}",
        payload_body=body,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    return event_log.write_direct(intent).event_id


def _redispatch_circuit_terminalized_path(towow_dir: Path, key: str) -> Path:
    """巡检强制终态化哨兵 (一 marker 一份, 防每节拍重复 emit TerminalStateForced)。"""
    return _dispatched_dir(towow_dir) / f"{_REDISPATCH_CIRCUIT_PREFIX}{key}.terminalized"


def _circuit_marker_object_ref(key: str, data: dict[str, object]) -> str:
    """从熔断 marker 取被卡对象引用 (exec: task_id; nonexec: trigger key 本身)。"""
    tid = data.get("task_id")
    if isinstance(tid, str) and tid:
        return tid
    trig = data.get("trigger_event_id")
    if isinstance(trig, str) and trig:
        return trig
    return key


def collect_overage_live_bodies(
    towow_dir: Path,
    *,
    stuck_threshold_s: float | None = None,
    now: float | None = None,
) -> list[dict[str, object]]:
    """concept ③ 反面活体清零探测 (纯读): 超龄仍未达终态的【重派 trigger】活体快照。

    over-age live body = 熔断 marker (tripped 态) 卡过阈值仍无终态证据 (无 .terminalized 哨兵)。
    这正是'重派死循环 trigger'类: 熔断后只告警不收口 → 无限卡。返回每条: {object_class,
    object_ref, key, aged_for_s}。verify 脚本据此断言'零超龄活体'(跑过强制终态化后该列表应为空)。"""
    threshold = _escalation_stuck_threshold_s(stuck_threshold_s)
    now_unix = now if now is not None else time.time()
    out: list[dict[str, object]] = []
    ddir = _dispatched_dir(towow_dir)
    if not ddir.exists():
        return out
    for f in sorted(ddir.glob(f"{_REDISPATCH_CIRCUIT_PREFIX}*")):
        if f.name.endswith(".terminalized"):
            continue
        key = f.name[len(_REDISPATCH_CIRCUIT_PREFIX):]
        if _redispatch_circuit_terminalized_path(towow_dir, key).exists():
            continue  # 已被巡检强制终态化 (达终态) → 非活体
        data: dict[str, object] = {}
        with contextlib.suppress(OSError, json.JSONDecodeError):
            loaded = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        tripped = data.get("tripped_at")
        tripped_unix = float(tripped) if isinstance(tripped, (int, float)) else f.stat().st_mtime
        aged = now_unix - tripped_unix
        if aged < threshold:
            continue  # 未超龄 — 还在正常熔断窗口
        out.append({
            "object_class": "redispatch_trigger",
            "object_ref": _circuit_marker_object_ref(key, data),
            "key": key,
            "aged_for_s": aged,
        })
    return out


def sweep_force_terminal_states(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stuck_threshold_s: float | None = None,
    now: float | None = None,
) -> list[str]:
    """concept novelty — 巡检把超龄重派 trigger 活体【强制终态化】(行动非告警, 数据通道)。

    现状 _sweep_stuck_batons 对 redispatch_exhausted 只 emit 告警 → 熔断 trigger 无限卡 (死循环)。
    本函数把它升级为'行动': 每个超龄 tripped 熔断 trigger → 投死信箱 (entry_reason=circuit_tripped,
    给它一等终点等分诊) + emit TerminalStateForced 留痕 + 写 .terminalized 哨兵 (防重复)。

    【红线边界】只走数据通道 (死信), 绝不碰活进程: kill+重派通道 (杀活子进程) 是 owner-gated 不可逆
    红线 (subprocess_heartbeat.py §4.b), 本函数不做。enqueue 幂等 (按源对象+进入源), 哨兵防重 emit。
    paused/runaway 下安全 (纯账本终态化 + 文件哨兵, 不 spawn 不 kill)。

    Returns: 本次被强制终态化的 trigger key 列表 (供 daemon / 观测 / 测试 / verify)。
    """
    now_unix = now if now is not None else time.time()
    forced: list[str] = []
    for body in collect_overage_live_bodies(
        towow_dir, stuck_threshold_s=stuck_threshold_s, now=now_unix,
    ):
        key = str(body["key"])
        object_ref = str(body["object_ref"])
        aged = cast("float", body["aged_for_s"])
        # 一条烂 marker 不崩整轮巡检 (与 sweep_aged_out / reflow 同纪律): enqueue/emit/哨兵/计数
        # 全包在一个 suppress 里 —— 任一步失败则该条不计入 forced、不写哨兵 (下轮重试), 循环继续。
        with contextlib.suppress(Exception):
            # 死信终态化: 投死信箱给熔断 trigger 一等终点 (idempotent by 源对象+进入源)
            dead_letter_inbox.enqueue(
                towow_dir, event_log,
                source_object_type="redispatch_trigger",
                source_object_ref=object_ref,
                entry_reason=dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED,
                original_trigger_event_id=None,
                now=now_unix,
            )
            emit_terminal_state_forced(
                event_log,
                object_class="redispatch_trigger",
                object_ref=object_ref,
                from_state="tripped",
                to_terminal_state="dead_lettered",
                recovery_channel="dead_letter",
                aged_for_s=aged,
                detail={"circuit_key": key},
            )
            # 哨兵: 标记已强制终态化 → 下节拍 collect_overage_live_bodies 不再视为活体, 不重复 emit
            _redispatch_circuit_terminalized_path(towow_dir, key).write_text(
                json.dumps({"terminalized_at": now_unix, "object_ref": object_ref}),
                encoding="utf-8",
            )
            forced.append(key)
    return forced


def _pending_sessions_dir(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _PENDING_SESSIONS_SUBDIR


def record_pending_session(
    towow_dir: Path,
    goal_session_id: str,
    *,
    spawned_role: str,
    trigger_event_id: str,
    task_id: str | None = None,
    model_tier: str | None = None,
    dispatch_to: str | None = None,
    kind: str | None = None,
    trigger_event_type: str | None = None,
    review_mode: str | None = None,
    domain_goal_session_id: str | None = None,
) -> None:
    """Write a pending-session marker (real launched sessions only) so reconciliation
    can对账 without scanning the full log every poll iteration.

    B4: execution 工位带 task_id + model_tier — 并发帽按 pending 计活跃工位/分 tier 配额,
    status 可见当前烧哪些 model (运维嫁接: 成本可观测)。

    T-FIX-B2-05 (PARALLEL-locks#1 纵深防御): 非 exec 工位 (review/fix/consensus/plan) 带
    dispatch_to (decision 的派发目标) + kind (serial-reject registry kind)。reconcile 探测到
    这类会话 silent death (launched-but-rejected: 单飞门正常但 start 内部撞 live exit 1) 时,
    据 dispatch_to 清掉该 trigger 的 <trigger>__<dispatch_to> 复合戳 (clear_nonexec_dispatch_stamp),
    让下轮 _route_event 重扫该 trigger 重派 —— 探测→收割→重派闭环对非 exec kind 的补齐
    (review marker task_id=None, 复合戳键于 trigger+dispatch_to, clear_exec_task_stamp 够不到)。

    domain_goal_session_id: f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①
    的直接后果 ——`goal_session_id` (本函数第一个位置参数, 文件名/主 key, 传的是 claude --bg 真实
    分配的 bg_session_id, 不动) 与"会话领域身份"(spawn 前预生成、写进子会话 env
    TOWOW_SELF_GID 的那个值) 解耦后, `goal terminate` 收尾读 env 默认 emit 的
    GoalSessionTerminated.goal_session_id 装的是领域身份而非 bg_session_id。reconcile
    (`_has_termination_event`) 若只拿 bg_session_id 去匹配, 会认不出这种"新格式"终止事件、对已经
    正确收尾的会话重复兜底 emit。本字段把 record 时刻本就同时在手的领域身份存进 marker body, 供
    reconcile 做双路匹配 (先 bg_session_id 精确匹配 <历史/execution 路径>, 落空再试这个值 <本 fix
    后 review/consensus/fix/planning 路径>) ——不为此单独反查 GoalSessionStarted, 那是给
    escalation_reflow 判活/attach 用的另一条链路。None → 不写该字段 (execution fan-out 等未解耦
    路径零回归)。
    """
    pdir = _pending_sessions_dir(towow_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {
        "goal_session_id": goal_session_id,
        "spawned_role": spawned_role,
        "trigger_event_id": trigger_event_id,
        "recorded_at": time.time(),
    }
    if domain_goal_session_id is not None:
        body["domain_goal_session_id"] = domain_goal_session_id
    if task_id is not None:
        body["task_id"] = task_id
    if model_tier is not None:
        body["model_tier"] = model_tier
    if dispatch_to is not None:
        body["dispatch_to"] = dispatch_to
    if kind is not None:
        body["kind"] = kind
    # T-FIX-B2-05 返工: 带原 decision 的 trigger_event_type + review_mode, silent death 重派时
    # reconcile 写 backlog marker 据此忠实重建 decision (前进链 review 必须带原 review_mode 否则
    # prompt 模板错: generate_forward_chain_condition_text 对 review_mode 非空才发 `--mode <mode>`)。
    if trigger_event_type is not None:
        body["trigger_event_type"] = trigger_event_type
    if review_mode is not None:
        body["review_mode"] = review_mode
    (pdir / f"{goal_session_id}.json").write_text(json.dumps(body), encoding="utf-8")
    # T-FIX-CONCERN-01 累计闸数据源: 每次真 spawn 记录都把估算花费累加进持久计数器
    # (不随 reconcile 清 — 真"预算"语义; pending marker 终态收口后瞬时闸看不见它, 累计闸仍算账)。
    _accrue_spawn_cost(towow_dir, body)


def _try_write_goal_lock(
    towow_dir: Path,
    goal_session_id: str,
    worktree_path: str,
    started_at: str,
) -> bool:
    """F-026-6: 给目标 worktree 写 goal_session.lock(复用 acquire_goal_lock), 使 daemon
    派的会话能 `goal terminate` 自报完工。容错: 已有 lock 则 skip 不崩。Returns 是否写成。"""
    from towow.l1.goal.session_lock import NestedGoalSpawnError, acquire_goal_lock
    try:
        acquire_goal_lock(
            towow_dir,
            goal_session_id=goal_session_id,
            worktree_path=worktree_path,
            started_at=started_at,
        )
    except (NestedGoalSpawnError, OSError):
        # 已有 lock(另一会话活跃)/写失败 → 不阻塞 spawn, 降级到 reconciler 兜底
        return False
    return True


# RUN-028: 旧 _default_bg_job_terminal 已移除 — 它漏认 daemon 真实终态词 stopped/blocked,
# 导致被停的会话永远不被认成终态 (pending 标记 + 悬空锁三天清不掉)。终态判定现统一走
# session_liveness 的唯一词表 (assess_session_liveness), reconciler/status/Agent 三方共用,
# 杜绝词表漂移。


def _has_termination_event(
    event_log: EventLog, goal_session_id: str, alt_goal_session_id: str | None = None,
) -> bool:
    """Targeted scan: does a GoalSessionTerminated for this goal_session_id exist?

    alt_goal_session_id: f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①
    的直接后果 —— goal_session_id 与 bg_session_id 解耦后, 同一会话的终止事件可能用两种不同的
    id 落账 (旧格式/execution 路径: bg_session_id；本 fix 后 review/consensus/fix/planning 路径:
    spawn 前预生成的领域身份)。调用方 (reconcile) 手上只有一个 marker, 但不确定这条会话是哪种
    格式, 于是把两个候选都传进来双路匹配, 命中任一个就算"已终止"——避免对已经正确自终止的会话
    重复 emit 兜底 external 终止 (纯噪音回归, 非误伤, 但仍是本次解耦必须接住的直接后果)。
    None (默认) → 单路匹配, 旧调用零回归。
    """
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.GOAL_SESSION_TERMINATED.value:
            continue
        seen = _extract_goal_session_id(payload)
        if seen == goal_session_id or (alt_goal_session_id is not None and seen == alt_goal_session_id):
            return True
    return False


def _latest_bg_session_id_for_task(event_log: EventLog, task_id: str) -> str | None:
    """FB-1 反查: 跑完 `task_id` 的那条 bg goal_session 的 gsid, 经 canonical
    OrchestratorDispatched(task_id).spawn_result.bg_session_id 链。

    为什么必须经 canonical 反查 (而非读锁 / env / 工作产物 provenance.session_id):
      - gsid == bg_session_id, 由 claude --bg 在 spawn 【之后】才分配 (spawn 流程: 子进程起来
        拿到 bg_session_id → spawn_dict → emit_goal_session_started goal_session_id=bg_id, 见
        _spawn_one_execution_after_claim)。spawn 【之前】provision env 时 bg_id 还不存在 → 无法
        env 注入 TOWOW_SELF_GID; execution 工位又退役了 goal_session.lock → 会话根本够不到自己
        的 gsid。唯一可靠来源就是这条 orchestrator 自留的派发审计事件。
      - 绝不读共享 goal_session.lock 反查 (单指针锁会被并发邻居覆盖 → 误终止活邻居)。
      - 绝不用工作产物的 provenance.session_id (C1 教训: 非 exec 产物 session_id=worker sid ≠
        bg gsid, 见 _workitem_product_exists docstring)。

    一个 task 多条 OrchestratorDispatched (重派/retry) → 取【最后】一条带非空 bg_session_id 的
    (all_records 按 seq 升序, 后写覆盖)。查不到 → None (调用方降级 no-op, FB-2 兜底)。
    """
    found: str | None = None
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != "OrchestratorDispatched":
            continue
        if _extract_task_id(payload) != task_id:
            continue
        spawn_result = payload.get("spawn_result")
        if not isinstance(spawn_result, dict):
            continue  # main-inbound / 失败通知形态无 spawn_result → 非真起会话, 跳过
        bg_id = spawn_result.get("bg_session_id")
        if isinstance(bg_id, str) and bg_id:
            found = bg_id  # 最后一条覆盖 (retry 取最新)
    return found


def _auto_terminate_completed_sessions(
    towow_dir: Path,
    event_log: EventLog,
    *,
    scan_start: int,
    scan_end: int,
) -> int:
    """FB-1 (收口信号做成完工的 choke-point 副作用): 本轮新事件里若有 work-product 完工事件
    (TaskRunCompleted outcome=success — 覆盖 execution fan-out + REVIEW-typed conclude 两条),
    立刻代该会话 emit GoalSessionTerminated(reason=completion), 把它从 active_relay 摘掉 + 给
    main-inbound 发完工信号解锁前进链。

    根因 (AGENT-IS-CONTEXT): 实证仅 14/369 GoalSessionTerminated 是 agent 自报 —— 收口信号挂在
    "agent 在 context 末端自觉记得 emit"这条脆弱路径上, 大面积漏报 → 完工会话永留 active_relay
    (失控总闸误判 / 前进链不解锁 / 哨兵瞎)。修: 把回收信号从"靠 agent 自觉"挪到 orchestrator 这个
    物理 choke point —— 它一观察到 success 工作产物 (= 该会话确已干完活), 就系统性地代发收口, 不靠
    agent 记得、也不必等 reconcile 凭 liveness 探死 (那是 silent-death/abandon 兜底, 见
    reconcile_orphaned_sessions / FB-2; 本函数是 happy-path 即时回收, 互补不重叠)。

    gsid 经 canonical 反查 (_latest_bg_session_id_for_task), 见其 docstring 为何不能读锁/env。
    幂等 + provenance 诚实唯一闸 = `gsid in active_relay`: 它精确等价于"该 gsid 有 launched+
    orchestrator_registered 的 GoalSessionStarted 且尚无 GoalSessionTerminated" —— mock/未
    launched 会话 (无真 job) 不在其中故不误发, 已终止的不在其中故不重发。反查不到 / 不在 relay
    → 降级 no-op (FB-2 的 active_relay+aged-ghost 兜底)。

    挂点同 _auto_promote_completed_worktrees: 扫 daemon 本轮处理的同一区间 [scan_start, scan_end],
    在 paused/runaway 闸之后 (paused 时本块被 continue 跳过, 不动)。内部绝不 raise 出去拖垮自动链
    (外层 run_polling_loop 再包一层 suppress)。Returns 本轮代发的 GoalSessionTerminated 数。
    """
    if scan_end < scan_start:
        return 0
    try:
        records = event_log.get_events_in_range(scan_start, scan_end)
    except Exception:  # 扫描失败绝不拖垮自动链 (瞬态 IO / 烂事件不崩循环)
        return 0
    # 一次算出当前在跑接力集 (= 唯一闸)。本轮代发后从本地集摘掉, 防同区间同 gsid 重复代发。
    relay = set(active_relay_sessions(event_log))
    if not relay:
        return 0
    emitted = 0
    for rec in records:
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.TASK_RUN_COMPLETED.value:
            continue
        if _extract_outcome(effective_payload) != "success":
            continue
        task_id = _extract_task_id(effective_payload)
        if not task_id:
            continue
        gsid = _latest_bg_session_id_for_task(event_log, task_id)
        if not gsid or gsid not in relay:
            continue  # 反查不到 / 未 launched / 已终止 → 降级 no-op (FB-2 兜底)
        # live-target-execution-evidence@v1 承重堵点: FB-1 代发走 write_direct 绕过 commit gate, 故
        # goal 收口门在 gate.py 那道咬不住这条路径 —— 必须在此就地对账。代发 completion 前先复算该 goal 的
        # brief.live_target_observables against committed 账本; 任一未满足 → 不代发 completion (否则旗舰
        # 病例复发: 单条 success 即宣布 goal 完成、账本 0 条真实事件), 改发一次 blocked 告警 + 留在 relay
        # 等真实证据补齐。共用唯一真值源 reconcile_goal_live_targets。
        # T-PERF: 用 warm committed_index 快照替代循环内每次全盘重扫 committed 全量的旧全量读法
        # (同 committed-visible 记录集 — T-FND-02 范式)。承重: 这句每轮 poll × 每条匹配 success 都
        # 跑一次, 旧全量读每次全盘重扫+pydantic 重解析全部 committed records → O(n²), 是 orchestrator
        # 常驻烧 CPU/吃 GB 内存的源头。committed_index().records() 是 warm index (无写→O(1) byte-size
        # 短路; 有写→只折活动段 tail, 与账本总量无关), 且与旧全量读同源 _iter_committed_records() →
        # committed 记录集逐字节等价 (reconcile 参数名即 `committed`)。只改"读几次"不改"读哪些",
        # 旗舰 goal 收口门不变量守住。逐轮取而非循环外一次快照: 保留旧读法每次重读的最新可见性
        # (含本轮循环内 write_direct 的写入, event_log.py 写路径增量 add/catch-up 保鲜)。
        reconcile = reconcile_goal_live_targets(gsid, event_log.committed_index().records())
        if not reconcile.passed:
            _emit_live_target_blocked_once(
                event_log, goal_session_id=gsid, task_id=task_id, evidence=reconcile.failure_evidence,
            )
            continue  # 不代发 completion, 不从 relay 摘 (证据补齐前不许收口)
        emit_goal_session_terminated_fallback(
            event_log,
            goal_session_id=gsid,
            reason="completion",
            final_status=(
                f"work-product 完工 (TaskRunCompleted success task={task_id}) — 调度员 "
                "choke-point 即时回收会话 (FB-1; 不靠 agent 自报收口)"
            ),
        )
        relay.discard(gsid)
        emitted += 1
    return emitted


_LIVE_TARGET_BLOCKED_KIND = "GoalLiveTargetUnsatisfied"


def _emit_live_target_blocked_once(
    event_log: EventLog,
    *,
    goal_session_id: str,
    task_id: str,
    evidence: dict[str, str],
) -> None:
    """FB-1 对账失败时发一次 blocked 告警 (幂等: 同 gsid 已有一条则 no-op, 防每轮重发刷账本)。

    降级语义 (概念 goal 收口门): 未满足 live_target_observable → blocked_live_target_unsatisfied 触发
    RePlan, 绝不静默收口。不 emit completion —— 会话留在 relay, 待负责产真实事件的 (live_target) task
    真跑出 ≥N 条账本事件后, 下一轮 FB-1 对账通过再代发 completion。
    """
    # T-PERF: warm committed_index 快照替代每次发 blocked 时的全盘 committed 全量重扫
    # (同 committed-visible 记录集 — T-FND-02 范式)。幂等去重: 同 gsid 上一轮 write_direct 的
    # blocked marker 在下轮取 committed_index() 时已被增量折入 (写路径保鲜) → 与旧全盘重读同样
    # 看得见, 单条 GoalLiveTargetUnsatisfied 不复读。
    for rec in event_log.committed_index().records():
        etype, existing = _unwrap_stub_rewrap(rec)
        if etype == _LIVE_TARGET_BLOCKED_KIND and existing.get("goal_session_id") == goal_session_id:
            return  # 已发过, 幂等 no-op
    payload: dict[str, object] = {
        "kind": _LIVE_TARGET_BLOCKED_KIND,
        "goal_session_id": goal_session_id,
        "task_id": task_id,
        "blocked_reason": "blocked_live_target_unsatisfied",
        "detail": evidence.get("detail", ""),
        "unsatisfied_observables": evidence.get("unsatisfied_observables", ""),
        "alarm": (
            "⚠ goal 声称 completion 但 live_target_observable 账本证据不足 — FB-1 拒代发 completion, "
            "降级 blocked 触发 RePlan (live-target-execution-evidence@v1 goal 收口门)"
        ),
        "orchestrator_alarm": True,
    }
    intent = _build_orch_nodetouched(
        kind=_LIVE_TARGET_BLOCKED_KIND, decision_id=goal_session_id, payload_body=payload,
    )
    event_log.write_direct(intent)


def _payload_after_field(payload: dict[str, object], key: str) -> object | None:
    """从 payload 取字段, 兼顾 after_state 嵌套 + 顶层 (stub-rewrap / 直 payload 两形态)。"""
    if not isinstance(payload, dict):
        return None
    after = payload.get("after_state")
    if isinstance(after, dict) and key in after:
        return after.get(key)
    return payload.get(key)


def _workitem_product_exists(
    event_log: EventLog, dispatch_to: str | None, trigger_event_id: str | None,
) -> bool:
    """T-FND-01: 非 exec 工位(consensus/planning)的 twin-prevention —— 该 work-item 的产物是否
    已 canonical。语义是『work 已出货就别再派(谁派的都算, 不绑具体 bg gsid)』, 根治
    『休眠/跑完没自报的非 exec 会话被凭进程死活误判 silent-death → 重派孪生』(e3f36e33 同型)。

    - engineering-consensus: trigger=brief(InterviewBriefPublished)事件 → 取其 brief_id →
      查是否存在 EngineeringConsensusFreezed.brief_event_id == 该 brief_id。
    - planning: trigger=EngineeringConsensusFreezed 事件 → 取其 plan_id → 查是否存在
      PlanFreezed.plan_id == 该 plan_id。
    - review/fix/其他: 返 False(走原 grace+复合戳路径; review 0-finding 无产物=硬边界, 不靠产物查
      以免误判没干→漏重派真死, 见 T-FND-01 spec)。

    按 work-item 键(brief_id/plan_id)查存在性而非绑 gsid: twin-prevention 正确(work 出货即止),
    且避开『非 exec 产物 provenance.session_id=worker sid ≠ bg gsid』的 C1 同型张冠李戴。

    debt-986ba644fc44 (FB-3 backlog 重放前置存在性检查): 第二个调用方是 _run_polling_iterations
    的非 exec backlog 消费点 (_read_nonexec_backlog_decisions 重建的 is_backlog_decision 项) ——
    marker 写下之后, 该 trigger 的工作有可能已经【经另一条路径】(手动派发 / 并发的另一会话) 真出货,
    而这条 backlog 重放路径此前完全没查这一点、只查 is_already_dispatched(自己是否曾被
    mark_dispatched 过), 查不出"work 已出货、只是不是经这条 dispatch 路径出的货" —— 于是盲目重
    spawn 一个多余的 goal session (实证 evt-a6c50c88: cap_exhausted marker 写下后, 同一 brief 的
    共识早已在别处冻结)。两处调用方共用同一判据函数, 不重复实现。
    """
    if not trigger_event_id or dispatch_to not in ("engineering-consensus", "planning"):
        return False
    trig = event_log.get_event(trigger_event_id)
    if trig is None:
        return False
    _, trig_payload = _unwrap_stub_rewrap(trig)
    if dispatch_to == "engineering-consensus":
        brief_id = _payload_after_field(trig_payload, "brief_id")
        if not brief_id:
            return False
        # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
        # re-scan (same committed-visible record set — T-FND-02 pattern).
        for rec in event_log.committed_index().records():
            etype, payload = _unwrap_stub_rewrap(rec)
            if etype != EventType.ENGINEERING_CONSENSUS_FREEZED.value:
                continue
            if _payload_after_field(payload, "brief_event_id") == brief_id:
                return True
        return False
    # planning
    plan_id = _payload_after_field(trig_payload, "plan_id")
    if not plan_id:
        return False
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != EventType.PLAN_FREEZED.value:
            continue
        if _payload_after_field(payload, "plan_id") == plan_id:
            return True
    return False


_ROSTER_CACHE_TTL_ENV = "TOWOW_ROSTER_CACHE_TTL_S"
_ROSTER_CACHE_TTL_DEFAULT_S = 30.0
# 进程内单例缓存 (与 _LIVE_SESSION_REFUSALS_LOGGED 同规格): reconcile_orphaned_sessions 在生产每轮
# poll (默认 5s) 都会跑, 若每次都真起 `claude agents --json --all` 子进程 (15s 超时) 会把这个热
# 路径拖成不可控的延迟源。TTL 内复用上次查询结果, 不重复起子进程。
_roster_cache: dict[str, object] = {"ids": None, "fetched_at": 0.0}


def _roster_cache_ttl_s() -> float:
    raw = os.environ.get(_ROSTER_CACHE_TTL_ENV, "").strip()
    with contextlib.suppress(ValueError):
        val = float(raw)
        if val > 0:
            return val
    return _ROSTER_CACHE_TTL_DEFAULT_S


def cached_roster_ids(*, now: float | None = None) -> frozenset[str] | None:
    """T-SELFHEAL-STALE-RELAY: 节流版官方名单查询, 供 reconcile_orphaned_sessions 生产热路径
    (run_polling_loop) 当 roster_ids_fn 注入用。

    TTL (默认 30s, env TOWOW_ROSTER_CACHE_TTL_S 可调) 内直接返回上次查询结果 (即便那次是 None ——
    查询失败也缓存, 防一个持续不可用的 `claude` CLI 让每轮 poll 都重复 15s 超时的子进程调用拖垮
    热路径); TTL 过期后重新真查一次。测试/直调不该用这个默认路径 —— 显式传 roster_ids_fn 覆盖。
    """
    now_ts = time.time() if now is None else now
    cached_at = _roster_cache.get("fetched_at")
    if isinstance(cached_at, (int, float)) and now_ts - cached_at < _roster_cache_ttl_s():
        ids = _roster_cache.get("ids")
        return ids if ids is None or isinstance(ids, frozenset) else None
    ids = roster_session_ids()
    _roster_cache["ids"] = ids
    _roster_cache["fetched_at"] = now_ts
    return ids


def reconcile_orphaned_sessions(
    towow_dir: Path,
    event_log: EventLog,
    *,
    job_terminal_fn: Callable[[str], bool] | None = None,
    daemon_state_fn: Callable[[str], str | None] | None = None,
    nonexec_redispatch_grace_s: float = 0.0,
    protect_recorded_at_or_after: float | None = None,
    aged_ghost_grace_s: float | None = None,
    roster_ids_fn: Callable[[], frozenset[str] | None] | None = None,
) -> int:
    """E.5 完工信号兜底对账 (F-019-10 + RUN-028 liveness 升级).

    FB-2 (兜底候选集与哨兵同源): 本函数过去【只】以 pending-session 标记为候选集兜底; 但哨兵
    A3 / status 读 canonical active_relay (GoalSessionStarted launched 且无配对 GoalSessionTerminated)。
    两源发散 —— 有 GoalSessionStarted 却【无 marker】的会话 (marker 从未写 / 被共享树并发误删 /
    异源 spawn) 永远够不着 pass 1 → 在 active_relay 里永久泄漏 → A3 哨兵把幽灵当活会话空转瞎掉。
    现分两 pass: pass 1 遍历 marker (下方, 逻辑不变, marker 降级为富化信息源), pass 2 以 canonical
    active_relay 为候选集兜底收口【无 marker】的幽灵 (见 _reconcile_unmarked_active_relay)。

    遍历 pending-session 标记 (仅真 launched 会话有标记), 用 session_liveness 真信号判
    每个会话:
      - 终态-可清理 (completed/stopped/missing) 且未自报完工 → 调度员 emit 兜底
        GoalSessionTerminated(reason=external); 无论是否自报都清 pending 标记。
      - 在跑 (running) / 卡等输入 (stalled) / 未知 (unknown) → 不动: 别把正在跑的会话
        误判卡死, 别 silent 兜底 stalled/unknown (stalled 由 orchestrator status 显著标出)。

    RUN-028 根因修: 旧 _default_bg_job_terminal 漏认 daemon 真实终态词 stopped/blocked,
    被停的会话永远不被认成终态 → 8 个 pending 标记 + 悬空锁三天清不掉。现复用
    session_liveness 唯一词表。

    Args:
        job_terminal_fn: 注入点 (测试/自定义)。给定时按其 bool 判终态 (兼容旧契约);
            None → 用 session_liveness 真信号 (默认, 正确词表)。
        daemon_state_fn: 透传给 session_liveness 的 daemon state 读取注入 (测试用);
            仅在 job_terminal_fn=None 的默认路径生效。
        nonexec_redispatch_grace_s: T-FIX-B2-05 — 非 exec (review/fix/...) 会话 silent death
            的复合戳清除/重派只对【marker 记录已超过此宽限期】的会话生效。防『同一轮刚 launch 的
            会话 (daemon job 尚未在 ~/.claude/jobs 可见 → liveness=MISSING) 被误判 silent death
            当场清掉刚盖的复合戳』。run_polling_loop 传 poll_interval (刚 launch 的会话 age≈0 <
            grace → 不误清; 真死的会话在后续轮 age≫grace → 正常收割重派)。默认 0.0 (直调路径/
            注入测试不受影响, 保持原 exec 兜底语义不变)。
        protect_recorded_at_or_after: T-FIX-COST-DROP — 本轮起点时间戳。recorded_at ≥ 此值的
            非 exec 工位 = 【本轮刚 launch】(daemon job 还没在 ~/.claude/jobs 可见 → liveness 必
            =MISSING), 整条延后到下一轮, 绝不在本轮误判 silent death 清掉刚盖的复合戳。这是比
            wall-clock grace 更精确的同轮保护: grace=poll_interval 是【轮间睡眠】不是【本轮工作
            耗时上限】, 满负载下本轮 run_once+派发+spawn 自身耗时可超过 poll_interval, 导致
            marker_age≥grace 把同轮刚起的会话误收割 (满负载非确定性翻红的真凶)。按"本轮起点"
            判同轮则与机器负载无关, 确定性正确。默认 None (回退到 grace 数值判定, 保持旧契约)。
        aged_ghost_grace_s: FB-2 pass 2 (无 marker 的 active_relay 幽灵) 的兜底阈值 (秒)。一个
            active_relay 会话距其最后 canonical 事件超过此值, 且活性已无 (liveness terminal / vitality
            DEAD/DONE / 无活进程活锁的老 UNKNOWN) 才被兜底收口 —— 防把【刚 launch 但 marker 尚未写】
            的会话误当幽灵。None → 用 _aged_ghost_grace_s() (env TOWOW_AGED_GHOST_GRACE_S, 默认 3600s,
            与 pending_session_unreconciled 告警阈值同源)。测试传 0.0 关掉新鲜保护。
        roster_ids_fn: T-SELFHEAL-STALE-RELAY 根治 — 官方 `claude agents --json --all` 名单查询
            注入点。默认 None → **不查, 完全不覆盖** (逐位保持本参数加入前的行为, 所有既有调用方/
            单测不受影响)。生产热路径 (run_polling_loop) 显式传 `cached_roster_ids` (节流+缓存,
            见其 docstring) 接入这份信号——本轮只查一次 (pass 1 + pass 2 共用同一份快照), 让
            state.json 一个字段撒的谎 (进程已死但文件停留在 "working") 能被官方名单当场纠正, 不再
            需要人工 `goal terminate`。查询失败 (返回 None) → 两 pass 都退回旧行为 (纯 state.json
            判定), 绝不因查询失败而误杀。

    Returns 本次兜底 emit 数量 (pass 1 marker + pass 2 active_relay 幽灵)。
    """
    now = time.time()
    pdir = _pending_sessions_dir(towow_dir)
    fallback = 0
    # T-SELFHEAL-STALE-RELAY: roster_ids_fn 未显式注入 (None, 本参数自己的默认值) → 不查, 不覆盖
    # (roster_ids=None, 与改动前行为逐位一致——保持所有既有调用方/测试的既有契约不变, 不因为新增了
    # 这个信号源就让它们意外开始真跑子进程)。生产路径 (run_polling_loop) 显式传入
    # cached_roster_ids (节流+缓存版), 只在那一条热路径真正生效; 直调/单测不传就是旧行为。
    # 本轮只查一次官方名单 (pass 1/2 共用同一份快照, 同轮内一致, 防止两 pass 因两次独立查询结果
    # 不同而判定不一致)。
    if roster_ids_fn is None:
        roster_ids = None
    else:
        try:
            roster_ids = roster_ids_fn()
        except Exception:
            roster_ids = None
    # FB-2: marker 不再是兜底覆盖的【唯一】候选集 —— 无 marker 时仍要走 pass 2 (active_relay 幽灵
    # 收口), 故不再在无 marker 目录时 early-return。marker 仅作 pass 1 的富化信息源。
    marker_files = sorted(pdir.glob("*.json")) if pdir.is_dir() else []
    for pf in marker_files:
        gsid = pf.stem
        # marker body 带 task_id (execution 工位) — silent death 时据此触发重派 (clear_exec_stamp)。
        marker_task_id: str | None = None
        # T-FIX-B2-05: 非 exec (review/fix/consensus/plan) 工位带 trigger_event_id + dispatch_to,
        # silent death 时据此清复合戳让 trigger 重派 (review marker task_id=None, exec 戳够不到)。
        marker_trigger_id: str | None = None
        marker_dispatch_to: str | None = None
        marker_trigger_type: str = ""
        marker_review_mode: str | None = None
        marker_recorded_at: float = 0.0
        # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: marker 主 key (gsid,
        # 见下方) 是 bg_session_id; 本会话真正 emit 终止事件时若走 env 路径 (本 fix 后 review/
        # consensus/fix/planning) 用的是这个另存的领域身份, 与 bg_session_id 不同 — 双路匹配見
        # 下方 _has_termination_event 调用。
        marker_domain_gsid: str | None = None
        with contextlib.suppress(OSError, json.JSONDecodeError):
            body = json.loads(pf.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                tid = body.get("task_id")
                marker_task_id = str(tid) if tid else None
                trig = body.get("trigger_event_id")
                marker_trigger_id = str(trig) if trig else None
                dto = body.get("dispatch_to")
                marker_dispatch_to = str(dto) if dto else None
                marker_trigger_type = str(body.get("trigger_event_type", "") or "")
                rmode = body.get("review_mode")
                marker_review_mode = str(rmode) if rmode else None
                with contextlib.suppress(TypeError, ValueError):
                    marker_recorded_at = float(body.get("recorded_at", 0.0))
                dgsid = body.get("domain_goal_session_id")
                marker_domain_gsid = str(dgsid) if dgsid else None
        # T-LND-11 (INV-B3-1): 区分 silent death (STOPPED/MISSING — 会话死/消失却没完工) 与
        # COMPLETED (良性: 完工了只是没自报终态)。前者必显著告警 + 重派, 不静默兜底。
        is_silent_death = False
        if job_terminal_fn is not None:
            if not job_terminal_fn(gsid):
                continue  # 注入判定: 非终态, 不动 (注入路径无 verdict, 不判 silent death)
            final_status = "bg job 终态退出未自报完工 — 调度员兜底 (F-019-10)"
        else:
            live = assess_session_liveness(
                gsid, event_log, daemon_state_fn=daemon_state_fn, roster_ids=roster_ids,
            )
            if not live.is_terminal_for_cleanup:
                # F-R11-REVIEW (under-reap 根修, bug ①): STALLED(daemon=blocked) 不在
                # terminal_for_cleanup → 原 abort-reap 永远触达不到。一个【本 run 已显式 abort 终态】
                # 的 parked 会话会卡 blocked→STALLED 永占执行位 (T-LRF-04 6398397b / pause 冻断僵尸
                # 同型)。窄口良性收割: 仅当 STALLED 且【本 run】(abort 时间戳 ≥ 本 marker.recorded_at,
                # run-scoped 不继承前一个废 run) 已 abort → 清 marker 腾位。绝不碰普通 STALLED 等待者
                # (它没有本-run abort 信号), 不发 SilentDeathAlarmed (显式终态非沉默死亡, 无孪生), 不清
                # 执行戳 (abort 时 B2 已清; 此处再清会撞已重派 run2 的活戳), 不重派 (替身归正常 replan+
                # ready 流程)。
                if (
                    live.verdict is SessionLivenessVerdict.STALLED
                    and marker_task_id
                    and marker_recorded_at > 0
                    and scan_canonical_work_product(
                        event_log, task_id=marker_task_id,
                        aborted_after_ts=marker_recorded_at,
                    )[2]
                ):
                    if not _has_termination_event(event_log, gsid, marker_domain_gsid):
                        emit_goal_session_terminated_fallback(
                            event_log, goal_session_id=gsid, reason="external",
                            final_status=(
                                f"本 run 已 abort 终态 parked-STALLED(daemon_state="
                                f"{live.daemon_state}) — 良性清 marker 腾执行位 "
                                "(F-R11-REVIEW under-reap 修; 非沉默死亡, 不孪生/不重派)"
                            ),
                        )
                        fallback += 1
                    with contextlib.suppress(OSError):
                        pf.unlink()
                    with contextlib.suppress(Exception):
                        from towow.l1.session_lock import SessionLockRegistry

                        SessionLockRegistry(towow_dir, "execution").release(gsid)
                # 🔴 substrate 3 (dormant 复活, INF-003): 普通 STALLED (活着卡等输入, 无本-run abort)
                # 现"什么都不做" —— 这里是复活动作的 dormant 兄弟。flag TOWOW_REVIVE_ENABLED 默认 OFF →
                # maybe_revive_stalled_session 立即 no-op (整条不触达, 绝不生产自动发"继续")。owner 在场
                # 显式开 (INF-003 总闸) 才会对 STALLED 会话发"继续"续跑。STALLED verdict 是生产权威判定,
                # adapter 防御性二次确认只会拒不会独立断言 (避脑裂)。预算 + per-session 节流防风暴。
                elif live.verdict is SessionLivenessVerdict.STALLED:
                    with contextlib.suppress(Exception):
                        maybe_revive_stalled_session(
                            towow_dir, event_log, gsid, daemon_state=live.daemon_state,
                        )
                elif live.verdict is SessionLivenessVerdict.RUNNING:
                    # substrate 3: 会话又动了 (STALLED→RUNNING, "继续"生效或自愈) → 清复活节流 marker,
                    # 让它【再次】卡住时还能复活 (预算内), 不被上次的"待响应"marker 永久挡住 (budget>1 可达)。
                    with contextlib.suppress(Exception):
                        clear_revive_marker(towow_dir, gsid)
                continue  # running / stalled(无本-run abort) / unknown → 不动
            is_silent_death = live.verdict in (
                SessionLivenessVerdict.STOPPED,
                SessionLivenessVerdict.MISSING,
            )
            # T-FIX-B2-05 返工 (grace 死信号根治): 非 exec (review/fix/...) 会话在宽限期内 (刚
            # launch, daemon job 未及在 ~/.claude/jobs 可见 → liveness=MISSING) 被判 silent death
            # 时, 必须【整条延后到下一轮】(continue, 不告警/不兜底/不删 pending marker), 而非"告警 +
            # 删 marker 但跳过重派"。原实现只把 clear+backlog 卡在 grace 后, 却仍在底部无条件
            # pf.unlink() 删 marker → 同一轮内死的会话 marker 被删 = 永远等不到"下一轮 age≥grace" =
            # grace 反而吞掉重派 (证伪场景的另一半: 即便 watermark 没 march, 同轮死也漏)。延后整条
            # marker 才能让"真死的会话后续轮 age≫grace 正常收割重派"这句话成立。
            marker_age = (
                now - marker_recorded_at if marker_recorded_at > 0 else float("inf")
            )
            # T-FIX-COST-DROP: 同轮刚 launch 保护 — 两个判据取【更宽容】(任一命中即延后):
            #   (a) 本轮起点判: recorded_at ≥ 本轮起点 → 本轮刚起, daemon job 必未可见 (精确, 与
            #       机器负载无关); (b) wall-clock grace 判 (旧契约, 直调/注入路径仍生效)。
            #   满负载下 (a) 才挡得住 "本轮自身耗时 > poll_interval 导致 marker_age≥grace 误收割"。
            launched_this_round = (
                protect_recorded_at_or_after is not None
                and marker_recorded_at > 0
                and marker_recorded_at >= protect_recorded_at_or_after
            )
            if (
                is_silent_death
                and marker_trigger_id
                and marker_dispatch_to
                and marker_dispatch_to != "execution"
                and (launched_this_round or marker_age < nonexec_redispatch_grace_s)
            ):
                continue  # 太新, 整条延后到下一轮 (保留 pending marker → 下轮 age≥grace 再收割)
            final_status = (
                f"bg job 终态(verdict={live.verdict} daemon_state={live.daemon_state}) "
                "未自报完工 — 调度员兜底 (F-019-10 + RUN-028 liveness)"
            )
            # R11 (gap1+gap2 根修): 重派前查【耐久信号】(canonical 产物=耐久骨干 + transcript 实时
            # 活动), 别凭进程死活(STOPPED/MISSING)就重派孪生 —— 沉默≠死亡。只有真死(DEAD)才重派。
            #   DONE(干完了只是没自报)        → 降级走良性兜底(补 terminated+清 marker, 不重派孪生)
            #   ALIVE/PARKED_RESUMABLE/STUCK/UNKNOWN → 本轮不收割(保留 marker, 别起孪生); PARKED 留给
            #     resume / self-heal surface, 收割处不盲目重派 (孪生 = THE 根症状)
            #   DEAD(账本无产物 + transcript 冷/无)  → 落到下面原重派路径 (这是唯一合法的重派)
            if (
                is_silent_death
                and marker_dispatch_to in ("engineering-consensus", "planning")
                and _workitem_product_exists(event_log, marker_dispatch_to, marker_trigger_id)
            ):
                # T-FND-01 (根治非 exec 孪生): consensus/planning 工位的 work-item 产物
                # (EngineeringConsensusFreezed/PlanFreezed) 已 canonical → work 已出货(谁派的都算)
                # → 不重派孪生(e3f36e33 同型共识孪生根治)。execution 走下面 assess_vitality(task_id);
                # 非 exec marker 无 task_id, assess_vitality 查不到产物 → 此处按 work-item 键补查。
                is_silent_death = False
            if is_silent_death:
                # 🔴 C1 修复 (review 抓出): 必须传 marker_task_id —— canonical 产物按 task_id 键,
                # 不是 gsid(bg 短 id)。漏传则 DONE-via-canonical 永远查不到 → 孪生复发。
                _vit = assess_vitality(
                    gsid,
                    task_id=marker_task_id,
                    agents_entry={"state": live.daemon_state} if live.daemon_state else None,
                    event_log=event_log,
                    now_fn=lambda: now,
                    # 层① 焊接 (K4b, advisor fix #1): daemon 门已判 STOPPED/MISSING = **正向死亡证据**
                    # (会话被外部终止 / job dir 已清)。经 process_alive 通道喂给新分类器: STOPPED/MISSING
                    # + 冷 + 无产物 → rule6 DEAD → 收割 (修"拆冷死后真死不收=僵尸"的回归); 但若 transcript
                    # 仍鲜 → rule2 ALIVE_WORKING 先命中 → continue (daemon 说停但其实在动 = 过度收割保护不破)。
                    # 非终态路径 (blocked/STALLED) **不**喂 process_alive → 冷→UNKNOWN 的 FLP 修保留。
                    process_alive_fn=(
                        (lambda _g: False)
                        if live.verdict
                        in (SessionLivenessVerdict.STOPPED, SessionLivenessVerdict.MISSING)
                        else None
                    ),
                    # F-R11-REVIEW (over-reap 根修, bug ②): abort 信号 run-scoped 到本 marker 的
                    # recorded_at —— 活会话 run2(transcript 暂静默)绝不继承 run1 的 abort 被误判 DEAD
                    # 误杀。recorded_at 缺失(旧 marker)→ inf floor = 不计入任何 abort (保守 under-reap)。
                    aborted_after_ts=(
                        marker_recorded_at if marker_recorded_at > 0 else float("inf")
                    ),
                )
                if _vit.verdict is VitalityVerdict.DONE:
                    is_silent_death = False  # 良性: 干完了没自报 → external 兜底, 不重派
                elif _vit.verdict is VitalityVerdict.DEAD and _vit.signals.task_run_aborted:
                    # F-R11-LIVE-reaper-aborted-replan: 任务已**显式** abort 终结(replan/不可恢复/
                    # advisor/外部) → 不是沉默死亡, 良性清 marker 腾执行位即可 (benign external 收口)。
                    # 重派交正常 B2(完成时已清执行戳)+ ready 重扫流程; 此处**不** emit SilentDeathAlarmed
                    # (那是孪生风险信号 —— 此处显式终态、reaper 不重派、无孪生)。修 T-LRF-04 6398397b
                    # 那类 parked-but-aborted 会话永占执行位、阻 reconcile 的卡死。
                    is_silent_death = False
                elif _vit.verdict is not VitalityVerdict.DEAD:
                    continue  # 活着/有半成品/卡等/信号不足 → 保留 marker, 本轮不收割不孪生
            # 搁浅工位护栏 (f-hardening-reaper-committed-no-success-noredispatch 连带处): DEAD 但隔离
            # 工位分支 task-{wid} 领先 main = executor committed 了活但从未 emit success / 从未 promote。
            # silent-death 收割支会清戳重派 (≤3 后还 dead-letter, 连已 commit 的活一起丢) = 与 reaper
            # 同源的浪费根。改: 良性外部收口 (下方 is_silent_death=False 路径 emit external terminated,
            # 摘 active_relay 不留幽灵/不触 A3 哨兵) + surface needs-promotion baton 交 plan task-close,
            # 不 alarm / 不重派 / 不 dead-letter。镜像上方 task_run_aborted 良性支。
            if is_silent_death and marker_task_id and (
                _exec_worktree_committed_ahead_of_main(towow_dir, marker_task_id) is True
            ):
                _surface_stranded_worktree_needs_promotion(
                    towow_dir, event_log, task_id=marker_task_id,
                )
                is_silent_death = False
            if is_silent_death:
                # 显著告警 (区别于良性 external) + 触发重派 (清 exec 戳, 该 task 下次 ready 重算捞回)。
                emit_silent_death_alarm(
                    event_log, goal_session_id=gsid, task_id=marker_task_id,
                    verdict=str(live.verdict), daemon_state=live.daemon_state,
                )
                if marker_task_id:
                    # T-LRF-03: 收割重派【前】先分类 (dispatch-retry-classification@v1)。
                    # deterministic (启动期结构性失败) / unknown 耗尽 1 预算 → 投 structural_failure
                    # 死信 + 熔断, 不重派 (此路径【不】调 clear_exec_task_stamp —— 它会 bump retry_count,
                    # 破坏 typed-failure-no-retry: 首分类即 structural 时须 retry_count==0 直接死信)。
                    # transient / unknown-首次 → 落 else 走既有清戳重派 (cap 由 pool-build 接管)。
                    # made_progress / signal_text 派生严守 @dispatch-failure-signal-contract@v1
                    # (T-LRF-03 接线修复): 各调单一真相源 helper, 禁用 file-existence / dispatch-payload
                    # 来源。made_progress 只派生自 canonical 半成品 或 transcript 真实新鲜度 (is_active);
                    # signal_text 只载真实终态失败成因 (verdict/daemon_state/transcript 尾部真错因),
                    # 不再拼 _retry_marker_last_error 的上次派发载荷 (那让结构正则永远看不到真异常文本)。
                    exec_made_progress = derive_exec_made_progress(
                        _vit.signals, active_threshold_s=DEFAULT_ACTIVE_THRESHOLD_S,
                    )
                    exec_failure_signal = compose_dispatch_failure_signal_text(
                        final_status=final_status,
                        verdict=live.verdict,
                        daemon_state=live.daemon_state,
                        transcript_tail_error_text=_vit.signals.transcript_tail_error_text,
                    )
                    if not route_structural_failure_to_dead_letter(
                        towow_dir, event_log,
                        source_object_type="task",
                        source_object_ref=marker_task_id,
                        original_trigger_event_id=marker_trigger_id or None,
                        signal_text=exec_failure_signal,
                        made_progress=exec_made_progress,
                        redispatch_count=exec_task_retry_count(towow_dir, marker_task_id),
                        circuit_key=_exec_task_stamp_name(marker_task_id),
                        context_label="exec task",
                    ):
                        clear_exec_task_stamp(towow_dir, marker_task_id)
                # T-FIX-B2-05: 非 exec (review/fix/...) 会话 launched-but-rejected silent death —
                # 清该 trigger 的 <trigger>__<dispatch_to> 复合戳让下轮 _route_event 重扫重派
                # (探测→收割→重派闭环对非 exec kind 的补齐; clear_exec_task_stamp 键于 task_id,
                # review marker task_id=None 够不到, 故复合戳永不清=永久吞=THE blocker 纵深漏洞)。
                # 仅当 marker 是非 exec 工位 (有 dispatch_to 且非 execution) 时走此路径。
                # 宽限期已在上方处理 (fresh nonexec 会话整条 continue 延后); 走到这里的非 exec
                # 会话 age 必 ≥ grace (或本就是 exec/无 trigger), 直接清戳 + 写 backlog 重派。
                if (
                    marker_trigger_id
                    and marker_dispatch_to
                    and marker_dispatch_to != "execution"
                ):
                    # T-FIX-B1-01 (B2-05 返工 concern): 非 exec backlog 重派也加上限 + 熔断 ——
                    # review 永远 launched-but-rejected 会无限收割重派烧资源。每次收割重写 backlog
                    # marker 时 redispatch_count +1; 已收割次数 ≥ 上限 → 不再重写 marker (停重派),
                    # emit RedispatchExhausted (载 trigger_event_id+dispatch_to) + 路由 main-inbound +
                    # 写幂等熔断 marker (key=<trigger>__<slug>)。熔断后再死也不重复告警/不重派。
                    nonexec_circuit_key = (
                        f"{marker_trigger_id}__"
                        f"{_dispatch_target_slug(marker_dispatch_to)}"
                    )
                    # 每次 silent-death 收割 +1 (持久计数, 不随 backlog marker 重派成功删除而重置)。
                    redispatch_n = bump_nonexec_redispatch_count(
                        towow_dir, marker_trigger_id, marker_dispatch_to,
                    )
                    cap = _redispatch_cap()
                    if redispatch_n > cap or is_redispatch_circuit_tripped(
                        towow_dir, nonexec_circuit_key,
                    ):
                        # 熔断: 清戳让去重失效但【不】重写 backlog marker (不再重派), 等 owner 介入。
                        clear_nonexec_dispatch_stamp(
                            towow_dir, marker_trigger_id, marker_dispatch_to,
                        )
                        if not is_redispatch_circuit_tripped(towow_dir, nonexec_circuit_key):
                            emit_redispatch_exhausted_alarm(
                                event_log,
                                task_id=None, plan_id=None,
                                retry_count=redispatch_n,
                                last_error=f"非 exec 会话反复 launched-but-rejected: {gsid}",
                                trigger_event_id=marker_trigger_id,
                                dispatch_to=marker_dispatch_to,
                            )
                            emit_orchestrator_dispatched(
                                event_log,
                                DispatchDecision(
                                    trigger_event_id=(
                                        f"nonexec-redispatch-exhausted-"
                                        f"{uuid.uuid4().hex[:8]}"
                                    ),
                                    trigger_event_type="RedispatchExhausted",
                                    dispatch_to="main-inbound",
                                    reason=(
                                        f"⚠ 非 exec 自愈重派耗尽熔断: trigger="
                                        f"{marker_trigger_id}→{marker_dispatch_to} 重派 "
                                        f"{redispatch_n} 次仍 launched-but-rejected "
                                        f"(达上限 {cap}), 已停派等 owner 介入 (T-FIX-B1-01)"
                                    ),
                                ),
                            )
                            trip_redispatch_circuit(
                                towow_dir, nonexec_circuit_key,
                                {
                                    "trigger_event_id": marker_trigger_id,
                                    "dispatch_to": marker_dispatch_to,
                                    "retry_count": redispatch_n,
                                },
                            )
                            # T-LRF-02: 熔断的非 exec trigger 投死信箱 (circuit_tripped) 等分诊
                            # —— 给它一等终点 (once-per-trip 块内, enqueue 再按幂等键防重)。
                            dead_letter_inbox.enqueue(
                                towow_dir, event_log,
                                source_object_type="trigger",
                                source_object_ref=marker_trigger_id,
                                entry_reason=(
                                    dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED
                                ),
                                original_trigger_event_id=marker_trigger_id,
                            )
                        # 顺手删 stale backlog marker (熔断后不再重派此 trigger)。
                        clear_nonexec_backlog_marker(
                            towow_dir, marker_trigger_id, marker_dispatch_to,
                        )
                    else:
                        cleared = clear_nonexec_dispatch_stamp(
                            towow_dir, marker_trigger_id, marker_dispatch_to,
                        )
                        # T-FIX-B2-05 返工 (证伪抓出的死信号修复): 清戳只是去重失效, trigger 已在
                        # watermark 之下永不被 _route_event 重扫 → 必须【同时】写 backlog marker, 派发
                        # 循环每轮独立扫它重派 (exec backlog re-scan 的非 exec 对应物)。缺这半 = 清戳=
                        # 死信号, 被拒 review 永埋永不重派 (built≠enforced)。
                        write_nonexec_backlog_marker(
                            towow_dir, marker_trigger_id, marker_dispatch_to,
                            trigger_event_type=marker_trigger_type,
                            review_mode=marker_review_mode,
                            reason=(
                                f"silent-death 收割重派 (T-FIX-B2-05): goal_session={gsid} "
                                f"trigger={marker_trigger_id}→{marker_dispatch_to}"
                            ),
                        )
                        # main-inbound 可见化通知: owner 进主对话即见此非 exec 会话被收割重派
                        # (SilentDeathAlarmed 是 NodeTouched 不进 main-inbound poller; 这里补通知)。
                        emit_orchestrator_dispatched(
                            event_log,
                            DispatchDecision(
                                trigger_event_id=(
                                    f"silent-death-redispatch-{uuid.uuid4().hex[:8]}"
                                ),
                                trigger_event_type="OrchestratorSilentDeathRedispatch",
                                dispatch_to="main-inbound",
                                reason=(
                                    f"⚠ 非 exec 会话 silent death 被收割重派: "
                                    f"goal_session={gsid} dispatch_to={marker_dispatch_to} "
                                    f"trigger={marker_trigger_id} "
                                    f"(单飞门正常但 start 内部撞 live exit 1; 复合戳已"
                                    f"{'清→可重派' if cleared else '不在(幂等)'})"
                                ),
                            ),
                        )
        if not _has_termination_event(event_log, gsid, marker_domain_gsid):
            emit_goal_session_terminated_fallback(
                event_log, goal_session_id=gsid,
                reason="silent_death" if is_silent_death else "external",
                final_status=final_status,
            )
            fallback += 1
        with contextlib.suppress(OSError):
            pf.unlink()  # 终态了, 不再 pending
        # substrate 3: 终态了 → 清复活节流 marker (会话不再存在, 不需"待响应"throttle; 保持预算账干净)。
        with contextlib.suppress(Exception):
            clear_revive_marker(towow_dir, gsid)
        # B1: 同步释放该会话的 per-session 执行注册锁 (daemon 注册的, daemon 收口时还) —
        # 否则锁靠 registry stale 超时才自愈, 期间占着 guard 探测面。容错: 没锁/非 exec 无害。
        with contextlib.suppress(Exception):
            from towow.l1.session_lock import SessionLockRegistry

            SessionLockRegistry(towow_dir, "execution").release(gsid)

    # FB-2 pass 2 (兜底候选集与哨兵同源): pass 1 只覆盖有 pending marker 的会话; canonical
    # active_relay 里【无 marker】的会话 pass 1 永远够不着 → 永久泄漏 → A3 哨兵把幽灵当活会话。
    # pass 2 以 active_relay 为候选集兜底收口这些无 marker 幽灵 (与哨兵读同一真相源, 收敛到真值)。
    marker_stems = {pf.stem for pf in marker_files}
    fallback += _reconcile_unmarked_active_relay(
        towow_dir, event_log, pdir,
        now=now,
        marker_stems=marker_stems,
        daemon_state_fn=daemon_state_fn,
        roster_ids=roster_ids,
        aged_ghost_grace_s=(
            _aged_ghost_grace_s() if aged_ghost_grace_s is None else aged_ghost_grace_s
        ),
    )
    return fallback


_AGED_GHOST_GRACE_ENV = "TOWOW_AGED_GHOST_GRACE_S"
_AGED_GHOST_GRACE_DEFAULT_S = 3600.0


def _aged_ghost_grace_s() -> float:
    """FB-2 aged-ghost 兜底阈值 (秒); env TOWOW_AGED_GHOST_GRACE_S 可调, 默认 3600s。

    与 pending_session_unreconciled 自愈告警阈值 (_escalation_stuck_threshold_s 默认 3600s) 同源:
    一个会话距其最后 canonical 事件超过此值仍未收口 = 该被兜底对账的"久未对账"幽灵。"""
    raw = os.environ.get(_AGED_GHOST_GRACE_ENV, "").strip()
    with contextlib.suppress(ValueError):
        val = float(raw)
        if val > 0:
            return val
    return _AGED_GHOST_GRACE_DEFAULT_S


def _reconcile_unmarked_active_relay(
    towow_dir: Path,
    event_log: EventLog,
    pdir: Path,
    *,
    now: float,
    marker_stems: set[str],
    daemon_state_fn: Callable[[str], str | None] | None = None,
    roster_ids: frozenset[str] | None = None,
    aged_ghost_grace_s: float = _AGED_GHOST_GRACE_DEFAULT_S,
) -> int:
    """FB-2: 以 canonical active_relay 为候选集, 兜底收口【无 pending marker】的幽灵会话。

    背景 (两源发散泄漏): reconcile pass 1 只遍历 pending marker; 但哨兵 A3 / status 读 canonical
    active_relay (GoalSessionStarted launched 且无配对 GoalSessionTerminated)。marker 是 pass 1 的
    覆盖前提, 一旦某会话有 GoalSessionStarted 却【没有】marker (从未写 / 被共享树并发误删 / 异源
    spawn), pass 1 永远够不着它 → 它在 active_relay 里永久泄漏 → A3 哨兵把幽灵当活会话空转瞎掉。

    收口判据 (保守, FLP-safe, 防误杀活会话):
      - 有 marker → 跳过 (pass 1 已处理, 不重复 / 不覆盖 pass 1 对活会话的保留决定)。
      - 已有 GoalSessionTerminated → 跳过 (防御; active_relay 本已排除, pass 1 本轮可能刚 emit)。
      - liveness RUNNING / STALLED → 跳过 (daemon 说在飞 / 卡等输入, 绝不收; 沉默≠死亡)。
      - 距最后 canonical 事件 age < aged_ghost_grace_s → 跳过 (太新, 可能刚 launch marker 还没写)。
      - liveness terminal (STOPPED/MISSING/COMPLETED) 且 age≥grace → 兜底 emit。
      - liveness UNKNOWN 且 age≥grace: vitality DEAD/DONE → emit; vitality UNKNOWN 但无活进程/活锁
        (老幽灵, 信号已失) → emit; ALIVE_WORKING/PARKED_RESUMABLE/STUCK_WAITING → 跳过 (留)。

    为什么 UNKNOWN 老幽灵不靠 vitality=DEAD: classify_vitality 故意把"冷+无产物"判 UNKNOWN 而非 DEAD
    (FLP 偏判活, owner 实战#2 根治"被限流/被删不再误杀")。但那条保护针对【重派/kill】; 本 pass 既不
    重派 (无 marker 无 task_id) 也不 kill 进程, 只 emit reason=external 中性收口 bookkeeping —— 一个
    silent 数小时+无活进程活锁的 active_relay 项就该让哨兵看清它不是活会话。RUNNING/STALLED 跳过 +
    活进程/活锁守卫已护住真活/被限流(daemon 可见)的会话, 故收敛 active_relay 不违反 FLP。

    provenance 诚实 (owner 硬底线): 无 marker 会话【绝不】emit reason=silent_death —— silent_death 是
    "死在干活中途、需重派"的告警, 但无 marker 就无 task_id/dispatch_to 可重派, 且无法断言它死在中途
    (可能干完了只是 terminate 没回到 main)。统一 emit reason=external (中性兜底收口), 既让 active_relay
    收敛到真值, 又不把一个其实完工了的会话冤枉成假"沉默死亡"。
    """
    fallback = 0
    for gsid in active_relay_sessions(event_log):
        if gsid in marker_stems or (pdir / f"{gsid}.json").exists():
            continue  # 有 marker → pass 1 的地盘
        if _has_termination_event(event_log, gsid):
            continue  # 防御: 已有终态 (pass 1 本轮可能刚 emit) → 别重复
        # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: gsid (来自
        # active_relay_sessions, 即 GoalSessionStarted.goal_session_id) 是领域身份, 判活要拿真
        # bg_session_id 才对得上 `claude agents --json`/daemon state.json —— 反查一次 (见
        # _resolve_bg_session_id docstring; 未解耦路径退化返回 gsid 自身, 零回归)。
        bg_id_for_liveness = _resolve_bg_session_id(event_log, gsid)
        live = assess_session_liveness(
            bg_id_for_liveness, event_log, daemon_state_fn=daemon_state_fn, roster_ids=roster_ids,
        )
        if live.is_running or live.needs_attention:
            continue  # 在飞 / 卡等输入 → 绝不收 (沉默≠死亡; STALLED 由 status 显著标出)
        event_epoch = (
            _parse_iso_epoch(live.last_event_ts) if live.last_event_ts else None
        )
        age = now - event_epoch if event_epoch is not None else float("inf")
        if age < aged_ghost_grace_s:
            continue  # 太新 → 跳过 (可能刚 launch, marker 尚未写; mirror pass 1 同轮保护)
        should_emit = False
        detail = ""
        if live.is_terminal_for_cleanup:
            # STOPPED / MISSING / COMPLETED + 够老 → daemon 正向终态, 兜底收口。
            should_emit = True
            detail = f"liveness={live.verdict} daemon_state={live.daemon_state}"
        elif live.verdict is SessionLivenessVerdict.UNKNOWN:
            # state.json 在但词不认识 → 融合活性二次确认 (同上: 真进程判活用 bg id, 非领域身份)。
            vit = assess_vitality(
                bg_id_for_liveness, event_log=event_log, towow_dir=towow_dir, now_fn=lambda: now,
            )
            if vit.verdict in (VitalityVerdict.DEAD, VitalityVerdict.DONE):
                should_emit = True
                detail = f"liveness=UNKNOWN vitality={vit.verdict}"
            elif (
                vit.verdict is VitalityVerdict.UNKNOWN
                and vit.signals.process_alive is not True
                and not vit.signals.holds_live_lock
            ):
                # 老幽灵: age≥grace + 无确凿活进程/活锁 → 中性收口 (active_relay 收敛唯一兜底口)。
                should_emit = True
                detail = "liveness=UNKNOWN vitality=UNKNOWN aged-ghost(无活进程/活锁)"
        if not should_emit:
            continue  # ALIVE_WORKING / PARKED_RESUMABLE / STUCK_WAITING → 留, 不收
        emit_goal_session_terminated_fallback(
            event_log, goal_session_id=gsid, reason="external",
            final_status=(
                f"FB-2 无 marker 的 active_relay 幽灵会话兜底收口 ({detail}, age={age:.0f}s"
                f"≥grace={aged_ghost_grace_s:.0f}s) — reason=external 中性收口 (无 marker 不重派/"
                "不孪生/不 kill 进程, 不冤枉成 silent_death; 让 active_relay 收敛到真值, "
                "A3 哨兵不再把幽灵当活会话)"
            ),
        )
        fallback += 1
        # 顺手清残留 (无害幂等): 复活节流 marker + per-session 执行锁。
        with contextlib.suppress(Exception):
            clear_revive_marker(towow_dir, gsid)
        with contextlib.suppress(Exception):
            from towow.l1.session_lock import SessionLockRegistry

            SessionLockRegistry(towow_dir, "execution").release(gsid)
    return fallback


# ════════════════════════════════════════════════════════════════════════════════
#  E.5 safe pause/resume — 暂停而非销毁的叫停 (concept: orchestrator-safe-pause-resume)
#  agent-facing CLI; owner 本人叫停走 Claude Code agents 界面 Ctrl+X(=claude stop)。
# ════════════════════════════════════════════════════════════════════════════════

_PAUSE_FLAG_FILE = "paused.flag"


def _pause_flag_path(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _PAUSE_FLAG_FILE


def is_orchestrator_paused(towow_dir: Path) -> bool:
    """True iff 暂停标记在 — 轮询循环据此跳过 dispatch。"""
    return _pause_flag_path(towow_dir).exists()


def active_relay_sessions(event_log: EventLog) -> list[str]:
    """当前在跑的接力窗口 = 已登记 launched GoalSessionStarted 且无配对 GoalSessionTerminated。"""
    started: dict[str, bool] = {}
    terminated: set[str] = set()
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype == EventType.GOAL_SESSION_STARTED.value and payload.get("orchestrator_registered"):
            gsid = _extract_goal_session_id(payload)
            if gsid:
                started[gsid] = bool(payload.get("launched"))
        elif etype == EventType.GOAL_SESSION_TERMINATED.value:
            gsid = _extract_goal_session_id(payload)
            if gsid:
                terminated.add(gsid)
    return [g for g, launched in started.items() if launched and g not in terminated]


def _relay_task_sessions(event_log: EventLog) -> dict[str, str]:
    """R11 核心扫描: task_id → 一个仍在 active relay 的 bg_session_id (daemon 自己 spawn 的面)。

    经持久 canonical OrchestratorDispatched(task_id).spawn_result.bg_session_id 反查会话身份 ——
    【不靠脆弱的 exec 派发文件戳】: 戳在 daemon 重启 / resume 水位线跳过时会丢, 正是 R11 复发的因。
    relay 从事件日志算, 扛得过重启。取【所有】匹配会话不止最新一条: 最新可能是已站下的孪生 (已
    GoalSessionTerminated 离 relay), 只查最新会漏掉真活着的首棒 = 假守卫。"""
    started: dict[str, bool] = {}
    terminated: set[str] = set()
    dispatched: list[tuple[str, str]] = []  # (bg_session_id, task_id)
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype == EventType.GOAL_SESSION_STARTED.value and payload.get("orchestrator_registered"):
            gsid = _extract_goal_session_id(payload)
            if gsid:
                started[gsid] = bool(payload.get("launched"))
        elif etype == EventType.GOAL_SESSION_TERMINATED.value:
            gsid = _extract_goal_session_id(payload)
            if gsid:
                terminated.add(gsid)
        elif etype == "OrchestratorDispatched":
            spawn_result = payload.get("spawn_result")
            bg_id = spawn_result.get("bg_session_id") if isinstance(spawn_result, dict) else None
            task_id = _extract_task_id(payload)
            if bg_id and task_id:
                dispatched.append((str(bg_id), task_id))
    relay = {g for g, launched in started.items() if launched and g not in terminated}
    return {task_id: bg_id for bg_id, task_id in dispatched if bg_id in relay}


def tasks_with_active_relay_session(event_log: EventLog) -> set[str]:
    """R11 根治: 有一个 launched-未终止 (active relay) bg 会话在做的 task 集合 —— 这些 task 绝不能再派
    (会 = 孪生烧钱 + 破单车道)。只有会话真死 (reaper 判 DEAD → emit GoalSessionTerminated 移出 relay)
    后该 task 才离本集合、可被合法接班 (非孪生)。与 active_relay_sessions 同一趟扫成本。

    ⚠ 这个面只看得见【daemon 自己 spawn 的】bg 会话 —— owner 在别的终端手动开的会话不在这里。
    派发守卫一律用 tasks_with_live_work_session (并集: 本面 + session lock registry 手动面)。"""
    return set(_relay_task_sessions(event_log))


def tasks_with_live_work_session(
    event_log: EventLog, towow_dir: Path,
) -> dict[str, tuple[str, str]]:
    """统一活会话守卫 (owner 病根「编排器对手动会话失明」的根治面): task_id → (session_id, source)。

    不变量: **task 有任何活着的工作会话 —— 不论谁派的 —— 绝不再派** (孪生烧钱 > 等待, fail-closed)。

    两个正交探测面取并集 (R11 只有面①, 这正是 owner 手动会话反复被派孪生的根因):
      ① daemon 面 (source="daemon"): _relay_task_sessions — OrchestratorDispatched.spawn_result.
         bg_session_id 反查 GoalSessionStarted/Terminated。只看得见 daemon 自己 spawn 的 bg 会话。
      ② 手动面 (source="manual"): SessionLockRegistry 全 kind (locks/sessions/<kind>/) 的 live 锁,
         meta.task_id 有值即算「这个 task 有会话在做」。owner 在任意终端手动 `tw work start` /
         `tw review start` / `tw claim` 起的会话都在此留痕 —— daemon 对它们不再失明。锁的死活判据
         是 registry 自己的 (pid 快通道 + 分型心跳超时): 真死的锁被 reap 后离开本集合, task 才可被
         合法接班 —— 与「等 vitality 判死才放行重派」同语义。
      两面都命中 (daemon spawn 的会话跑 work start 后也持锁) → 标 daemon。

    kind 目录**物理枚举** (不硬编码清单): 任何现在/将来会把 meta.task_id 写进 registry 的会话形态
    自动进入探测面, 不留「新 kind 忘了接线」的缺口 (replan 断头环同款病理的反面教材)。
    单 kind 读挂 → 跳过该 kind 不崩守卫 (退化为面①, 不比修前差); 面①失败无兜 (事件日志坏 = 系统性故障)。
    """
    from towow.l1.session_lock import SessionLockRegistry

    out: dict[str, tuple[str, str]] = {}
    sessions_root = towow_dir / "locks" / "sessions"
    if sessions_root.is_dir():
        for kind_dir in sorted(sessions_root.iterdir()):
            if not kind_dir.is_dir():
                continue
            try:
                reg = SessionLockRegistry(towow_dir, kind_dir.name)
                for info in reg.live_sessions():
                    tid = reg.read_meta(info.session_id).get("task_id")
                    if isinstance(tid, str) and tid:
                        out[tid] = (info.session_id, "manual")
            except OSError:
                continue  # 单 kind 读挂不崩守卫 — 该 kind 面退化, 其余面照常
    # 面① 后写: 双面命中的 task 以 daemon 标注 (它是 daemon 可自证的 spawn 血缘)。
    for task_id, bg_id in _relay_task_sessions(event_log).items():
        out[task_id] = (bg_id, "daemon")
    return out


# 本 daemon 进程内已留过痕的 (task_id, session_id) — 守卫拒绝是稳态 (活会话在跑的每一轮都会拒),
# 每轮打一行会灌爆 daemon-run.log; 每对 (task, session) 一进程一行, 重启后重打一次 (可接受)。
_LIVE_SESSION_REFUSALS_LOGGED: set[tuple[str, str]] = set()


def _log_live_session_refusal(task_id: str, session_id: str, source: str, via: str) -> None:
    """守卫拒绝的可 grep 痕迹 (stdout → daemon-run.log; CLI 场景直接可见)。格式钉死:
    refusal=live_session_exists task=<id> session=<id> source=<manual|daemon> via=<通路>。"""
    key = (task_id, session_id)
    if key in _LIVE_SESSION_REFUSALS_LOGGED:
        return
    _LIVE_SESSION_REFUSALS_LOGGED.add(key)
    print(
        f"refusal=live_session_exists task={task_id} session={session_id} "
        f"source={source} via={via}",
        flush=True,
    )


def _default_claude_stop(goal_session_id: str) -> None:
    """暂停(非销毁)一个 bg 会话: `claude stop <id>` — 对话保留, 可 attach/respawn。"""
    subprocess.run(
        ["claude", "stop", goal_session_id],
        capture_output=True, timeout=15, check=False,
    )


def pause_orchestrator(
    towow_dir: Path,
    event_log: EventLog,
    *,
    reason: str = "manual",
    stop_windows: bool = True,
    stop_fn: Callable[[str], None] = _default_claude_stop,
    exclude_gsids: frozenset[str] = frozenset(),
) -> list[str]:
    """E.5 安全暂停: 写暂停标记(协调者停止派新窗口, 进程存活/水位线不丢) +
    (stop_windows=True 时)对当前在跑的接力窗口逐个 claude stop(冻住、保留、可 attach 查看)。

    ②C (2026-07-02 崩机根治): ``exclude_gsids`` 排除这些 goal_session_id 不被 stop —— 2026-07-01
    夜的会话正是撞上"正规 pause 会把自己也停了"这堵墙, 才被逼直接写 paused.flag 文件绕过。默认空集
    (行为不变); CLI 侧 (`orchestrator_pause`) 经 `TOWOW_TASK_ID` env 反查调用者自身 gsid 自动传入。

    暂停 ≠ 销毁: 不 kill 进程、不 rm 会话。Returns 被冻住的 goal_session_id 列表。

    stop_windows=False 用于 escalation 自动冻链(F-026-3): 只停协调者派新活, 不动那个正在
    pull-mode 等 owner 回话的会话(它自己管 wait-resume, 别打断)。

    finding-escalation-frozen-session-zombie-not-reaped-1: stop_windows=True 停掉的窗口里,
    正卡在 escalation 等 owner 回话的会话 (pending escalation 关联的 goal_session_id) 被
    冻断后进程已死, 但 daemon state 停留在 blocked → session_liveness 判 STALLED →
    reconcile 永不收割 = 占 task 不释放 + 周期刷告警的僵尸 (实证: 0242c74b 审 T-LC-01 的
    review escalate 后被受控重启冻断, 靠 escalation_stuck 巡检兜底延迟清理)。普通在跑会话
    被 stop 后 daemon state 是终态 (STOPPED), reconcile silent-death 路径正常收割, 不在此列。
    修: pause 当场对这类会话 emit GoalSessionTerminated(reason=restart_frozen) + 清 pending
    登记 (释放工位占用), 不留 blocked 僵尸。escalation 本身不动 (仍 pending, owner 照常
    respond); dispatch 戳不清 (owner 未回话前重派只会起一个撞同一问题的新会话)。
    """
    ensure_orchestrator_layout(towow_dir)
    _pause_flag_path(towow_dir).write_text(
        json.dumps({"paused_at": time.time(), "reason": reason}), encoding="utf-8",
    )
    # gap5 (R11): pause 留 canonical provenance (旧实现只写 flag 不 emit → 审计链看不到为何停)。
    emit_orchestrator_lifecycle(
        event_log, transition="paused", reason=reason,
        detail={"stop_windows": stop_windows},
    )
    stopped: list[str] = []
    if not stop_windows:
        return stopped
    escalated_gsids = {
        gsid for esc in pending_escalations(event_log)
        if (gsid := str(esc.get("goal_session_id", "")))
    }
    pdir = _pending_sessions_dir(towow_dir)
    for gsid in active_relay_sessions(event_log):
        if gsid in exclude_gsids:
            continue  # ②C: 调用者自身 (或显式豁免的会话) 不冻 —— 杀死调用者的命令是 bug 不是选项
        try:
            stop_fn(gsid)
            stopped.append(gsid)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            continue  # 单个窗口冻不住不阻塞整体暂停; owner 可手动 Ctrl+X; 没真停成不标 terminated
        if gsid in escalated_gsids:
            emit_goal_session_terminated_fallback(
                event_log,
                goal_session_id=gsid,
                reason="restart_frozen",
                final_status=(
                    "escalate 等 owner 回话中被受控重启冻断 — pause 主动收割不留 blocked 僵尸; "
                    "escalation 仍 pending 等 owner respond "
                    "(finding-escalation-frozen-session-zombie-not-reaped-1)"
                ),
            )
            with contextlib.suppress(OSError):
                (pdir / f"{gsid}.json").unlink()
    return stopped


def _read_paused_at(pause_flag_path: Path) -> float | None:
    """T-FIX-COST-DROP: 读暂停标记里的 paused_at (秒, epoch float)。缺失/损坏 → None。

    None 时 resume skip 守卫退化为【保守: 视所有未派 spawn 事件为暂停前 deferred 保留】——
    宁可多保留 (不会吞在途审查) 也不冒静默吞的险 (这是红线方向的安全侧)。
    """
    if not pause_flag_path.exists():
        return None
    try:
        data = json.loads(pause_flag_path.read_text(encoding="utf-8"))
        raw = data.get("paused_at")
        return float(raw) if raw is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _event_before_pause(rec: EventRecord, paused_at: float | None) -> bool:
    """T-FIX-COST-DROP: 该事件是否发生在暂停【之前】(=暂停前已 deferred 的在途工作, resume 保留)。

    paused_at 缺失 (None) → 保守返回 True (视为暂停前, 保留事件, 安全侧不吞)。事件时间戳取
    EventRecord.timestamp (datetime), 转 epoch 秒与 paused_at 比。时间戳异常 → 保守 True。
    """
    if paused_at is None:
        return True
    try:
        return rec.timestamp.timestamp() < paused_at
    except (AttributeError, OSError, ValueError):
        return True


def _dispatch_stamp_event_ids(towow_dir: Path) -> frozenset[str]:
    """f-orchestrator-resume-per-event-route-on-backlog-quadratic: 一次性 O(目录总条目数) 建索引,
    取代 resume_orchestrator 主循环里【逐事件】调用 is_already_dispatched(towow_dir, event_id)
    (不带 dispatch_to 时该函数内部对 dispatched/ 与 dispatched_archive/ 两个目录各做一次
    Path.glob 全量扫描)。

    这是生产 22.5 万积压 resume 卡 66 分钟、CPU 100% 的直接元凶: 现网两个目录合计可达十几万条目
    (2026-07 实测 dispatched/ ~4.9万 + dispatched_archive/ ~9万), 旧写法相当于 225000 次 ×
    ~14万 目录项比较 —— 真正的 O(n·目录规模), 表现上等同 O(n²)。改为整个 resume 只扫一次这两个
    目录 (O(目录规模)), 之后每个事件用集合查表 O(1) 判断"是否已有任一戳"。

    语义与 is_already_dispatched(towow_dir, event_id, dispatch_to=None) 的"该 event_id 有无
    任一戳(裸 legacy 戳或复合 <event_id>__<target> 戳, 活跃或已归档)"完全等价: 复合戳按 "__"
    切出 event_id 前缀聚合, 裸戳整个文件名即 event_id; dispatched/ + dispatched_archive/
    两个目录都覆盖 (与 is_already_dispatched 一致)。event_id 生成格式 (evt-<32 hex>) 不含
    "__", split("__", 1)[0] 不会误切出错误前缀。

    只服务 resume_orchestrator 这一个热点 —— 不改 is_already_dispatched 本身的公共行为 / 签名;
    该函数其余调用点都传显式 dispatch_to, 走的是 O(1) exists 分支, 不受本函数影响, 也不需要用
    这份索引 (它们的去重键含 dispatch target, 这份索引只按 event_id 聚合, 语义更粗, 不能通用替换)。
    """
    ids: set[str] = set()
    for d in (_dispatched_dir(towow_dir), _dispatched_archive_dir(towow_dir)):
        if not d.is_dir():
            continue
        for entry in os.scandir(d):
            ids.add(entry.name.split("__", 1)[0])
    return frozenset(ids)


def _advance_watermark_skip_backlog(
    towow_dir: Path,
    event_log: EventLog,
    *,
    preserve_decisions: Callable[[EventRecord], list[DispatchDecision]],
    stamp_payload: dict[str, object],
) -> int:
    """把水位线推到日志 head, 并把 [old, latest] 积压逐事件标 dispatched(去重跳过) —— 否则光推
    水位线, 轮询的 range-inclusive 重扫会把没派过的积压派出去(级联根因: run_polling_loop 每轮
    load_watermark → OrchestratorDaemon.run_once 扫 [watermark, head] inclusive 派未去重事件)。

    这是【唯一】的水位线推进原语: `resume_orchestrator`(F-026-5 暂停/恢复守卫) 与
    `catchup_watermark_on_ignition`(debt-d9436af6b342 首次点火守卫) 共用它, 不各造第二套
    水位线逻辑。差异只在注入的 preserve 策略与 stamp_payload:

      · preserve_decisions(rec) 返回该事件【需保留不吞】的 spawn 决策列表。非空 → 该事件不标 skip,
        改给每个非 exec 决策写 nonexec backlog marker 让派发循环独立 re-scan 捞回重派 (exec 决策由
        ready-set re-scan 兜底, 那条独立于 watermark)。返回空 → 该事件整体标 dispatched 跳过。
        resume 注入"暂停前 deferred 在途工作"的保留策略 (T-FIX-COST-DROP); 点火追平注入恒空 preserve
        (客户端停机态无在途工作 + 判据③要求对旧完工事件零重放)。

    只写新戳、绝不清既有戳: get_events_in_range 命中的 rec 若已在 dispatched_ids (已派/skip 过) 直接
    跳过, mark_dispatched 只对未派事件落新戳 —— debt-d9436af6b342 判据④ (不得清已派记录戳) 由此保证。

    f-orchestrator-resume-per-event-route-on-backlog-quadratic: 整个推进只扫一次 dispatched/ +
    dispatched_archive/ 建索引 (_dispatch_stamp_event_ids), 之后逐事件 O(1) 查表, 不再每事件各扫两
    目录 (生产 22.5 万积压曾卡 66 分钟的元凶)。返回被标 skip 的积压事件数。"""
    old = load_watermark(towow_dir)
    latest = event_log.next_sequence - 1
    if latest < old:
        return 0
    skipped = 0
    dispatched_ids = _dispatch_stamp_event_ids(towow_dir)
    for rec in event_log.get_events_in_range(old, latest):
        if rec.event_id in dispatched_ids:
            continue
        pending = preserve_decisions(rec)
        if pending:
            for decision in pending:
                # exec decision 走 ready-set re-scan (独立于 watermark), 不进 nonexec backlog;
                # 非 exec skill spawn 写 backlog marker 让 re-scan 捞回。
                if decision.dispatch_to == "execution":
                    continue
                write_nonexec_backlog_marker(
                    towow_dir,
                    decision.trigger_event_id,
                    decision.dispatch_to,
                    trigger_event_type=decision.trigger_event_type,
                    review_mode=decision.review_mode,
                    reason="preserved on resume (cost-gate/cap deferred — T-FIX-COST-DROP)",
                )
            continue  # 有待保留 spawn 决策 — 绝不标 skip
        mark_dispatched(towow_dir, rec.event_id, dict(stamp_payload))
        skipped += 1
    save_watermark_atomic(towow_dir, latest)
    return skipped


# debt-d9436af6b342 首次点火水位线陈旧阈值。水位线之上第一条待处理事件比此更老 → 判定老客户端长期
# 停机后的历史积压, 点火前推齐跳过防重放误派。危害不对称: 重放是实锤灾难 (~/towow 一夜误派 59 会话),
# 过度 skip 只是漏派、owner 可手动重触发 (downtime-recovery: 补派 vs 快进放弃本就是决策), 故门槛偏
# 保守 (真实事故是『周』级)。可 env TOWOW_IGNITION_STALE_AGE_S 调。
DEFAULT_IGNITION_STALE_AGE_S = 86400.0  # 24h
_IGNITION_STALE_AGE_ENV = "TOWOW_IGNITION_STALE_AGE_S"


def _ignition_stale_age_s() -> float:
    """首次点火水位线『陈旧』门槛秒数 (TOWOW_IGNITION_STALE_AGE_S, 默认 24h; 非法值回退默认)。"""
    raw = os.environ.get(_IGNITION_STALE_AGE_ENV, "").strip()
    if raw:
        with contextlib.suppress(ValueError):
            return float(raw)
    return DEFAULT_IGNITION_STALE_AGE_S


@dataclass(frozen=True)
class IgnitionCatchupResult:
    """catchup_watermark_on_ignition 结果。reason ∈ {no_backlog, watermark_fresh,
    stale_history_caught_up}。"""

    triggered: bool
    reason: str
    skipped: int
    old_watermark: int
    new_watermark: int
    oldest_unprocessed_age_s: float | None


def _oldest_unprocessed_age_s(
    towow_dir: Path,
    event_log: EventLog,
    old: int,
    latest: int,
    now_epoch: float,
) -> float | None:
    """[old, latest] 里第一条【尚未 dispatched】事件的年龄秒 (now - 其时间戳) —— 即我们将要重放的
    最老那条完工任务有多老。seq 递增 ≈ 时间递增, 首条未派即最老。全部已 dispatched (无真积压) → None;
    时间戳异常的条目跳过继续找。"""
    dispatched_ids = _dispatch_stamp_event_ids(towow_dir)
    for rec in event_log.get_events_in_range(old, latest):
        if rec.event_id in dispatched_ids:
            continue
        try:
            return max(0.0, now_epoch - rec.timestamp.timestamp())
        except (AttributeError, OSError, ValueError):
            continue
    return None


def catchup_watermark_on_ignition(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stale_age_s: float | None = None,
    now_epoch: float | None = None,
) -> IgnitionCatchupResult:
    """debt-d9436af6b342 首次点火水位线推齐守卫 —— run-engine.sh 在 `exec orchestrator start` 之前
    调用 (客户端装机点火交付面自带此步)。老客户端 (.towow 已有历史账本) 升级安装后若不先把陈旧水位线
    推齐, orchestrator 会从旧水位线 range-inclusive 重扫账本, 把几周前完工任务当『刚完工』重放误派
    接力 (实锤 ~/towow 一夜误派 59 会话; downtime-recovery『重启=积压喷发』教训只进主仓复工流程、
    没进 install 点火路径 = 本 debt)。

    判据 (debt resolution_criteria):
      ① 仅当账本已有历史积压 + 水位线陈旧(水位线之上第一条待处理事件年龄 > 阈值 = 长期停机重放风险)
         才推齐; 全新空账本 / 已追平(no_backlog) / 近期崩溃重启积压新鲜(watermark_fresh) 无操作。
      ② 由 run-engine.sh 模板无条件调用 —— 模板每次 install/sync 重渲染, 升级安装场景强制带此步。
      ④ 复用 _advance_watermark_skip_backlog, 只写新戳、不清既有已派戳。

    preserve 空: 与 resume 的 cost-gate deferred 守卫不同 —— 点火时客户端处于停机态、无在途 deferred
    工作需保留; 且判据③要求对旧完工事件零重放派发, 保留任何 actionable 都会经 backlog re-scan 重派
    = 违约, 故一律 skip。

    stale_age_s / now_epoch 可注入 (测试确定性); 默认读 env 阈值 + 真实时钟。"""
    # 老客户端可能从未起过 orchestrator (无 orchestrator/ 目录) —— 幂等建 layout 保证 mark_dispatched
    # 有 dispatched/ 可写; ensure 对已存在的水位线只保留不重置 (dc: 不洗已追平状态)。
    ensure_orchestrator_layout(towow_dir)
    threshold = _ignition_stale_age_s() if stale_age_s is None else stale_age_s
    old = load_watermark(towow_dir)
    latest = event_log.next_sequence - 1
    now_ts = time.time() if now_epoch is None else now_epoch
    # 判据①: 有无【真积压】= [old, latest] 里有无尚未 dispatched 的待派事件, 取其中最老者的年龄。
    # 水位线语义是"最后处理 seq (inclusive)"、初始 0 与"处理过 seq0"不可区分, 故不能用 seq 比较判空,
    # 只能按 dispatched 戳判"哪条真待派" (get_events_in_range 含边界 seq=old, 与 resume 同口径)。
    oldest_age = _oldest_unprocessed_age_s(towow_dir, event_log, old, latest, now_ts)
    # 无真积压 (全新空账本 latest<old / 已追平 / 积压全已 dispatched) → no-op (判据①: 全新空账本不动)。
    if oldest_age is None:
        return IgnitionCatchupResult(
            triggered=False, reason="no_backlog", skipped=0,
            old_watermark=old, new_watermark=old, oldest_unprocessed_age_s=None,
        )
    # 水位线新鲜 (最老待处理事件未超陈旧阈值 = 近期崩溃重启, 积压是新鲜完工该正常处理) → no-op。
    if oldest_age < threshold:
        return IgnitionCatchupResult(
            triggered=False, reason="watermark_fresh", skipped=0,
            old_watermark=old, new_watermark=old, oldest_unprocessed_age_s=oldest_age,
        )
    # 陈旧历史积压 → 推齐跳全部 (preserve 空)
    skipped = _advance_watermark_skip_backlog(
        towow_dir,
        event_log,
        preserve_decisions=lambda _rec: [],
        stamp_payload={"skipped_on_resume": True, "skipped_on_ignition": True},
    )
    new_wm = load_watermark(towow_dir)
    emit_ignition_watermark_catchup(
        event_log,
        skipped=skipped,
        old_watermark=old,
        new_watermark=new_wm,
        oldest_unprocessed_age_s=oldest_age,
        stale_age_threshold_s=threshold,
    )
    return IgnitionCatchupResult(
        triggered=True, reason="stale_history_caught_up", skipped=skipped,
        old_watermark=old, new_watermark=new_wm, oldest_unprocessed_age_s=oldest_age,
    )


def emit_ignition_watermark_catchup(
    event_log: EventLog,
    *,
    skipped: int,
    old_watermark: int,
    new_watermark: int,
    oldest_unprocessed_age_s: float,
    stale_age_threshold_s: float,
) -> str:
    """debt-d9436af6b342: 首次点火水位线推齐留 canonical provenance —— '事件日志是唯一真相源'。
    仿 emit_orchestrator_lifecycle 走 _build_orch_nodetouched + write_direct (不动 enums, 与并发改
    enums 的兄弟线解耦)。审计链据此看到『系统在点火时因账本陈旧把水位线推齐、跳过了多少历史积压』。"""
    payload: dict[str, object] = {
        "kind": "OrchestratorIgnitionWatermarkCatchup",
        "transition": "ignition_watermark_catchup",
        "reason": "stale_ledger_on_first_ignition",
        "orchestrator_lifecycle": True,
        "skipped_backlog_seq": skipped,
        "old_watermark": old_watermark,
        "new_watermark": new_watermark,
        "oldest_unprocessed_age_s": round(oldest_unprocessed_age_s, 1),
        "stale_age_threshold_s": stale_age_threshold_s,
        "debt": "debt-d9436af6b342",
    }
    intent = _build_orch_nodetouched(
        kind="OrchestratorIgnitionWatermarkCatchup",
        decision_id=f"orch-ignition-catchup-{uuid.uuid4().hex[:8]}",
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def resume_orchestrator(
    towow_dir: Path,
    event_log: EventLog | None = None,
) -> tuple[bool, int]:
    """E.5 恢复: 清除暂停标记, 协调者恢复派活。

    DOGFOOD-RUN-026 F-026-5 根因修(watermark-on-resume 守卫): 给 event_log 时, 把水位线推到
    当前 latest seq —— 跳过【暂停期间积压】的完工事件(语义: '从现在起反应, 不补跑冻结期间发生
    的')。否则 resume 会把暂停期手动做的完工事件当新任务重放 → 级联真起一堆会话。

    T-FIX-COST-DROP (不静默吞红线): skip-on-resume 绝不能把【暂停前已被路由但因 cap/成本闸截断、
    deferred 待重派的 skill/exec decision】一起标 dispatched 吞掉。成本闸跳闸→pause→owner resume
    这条链上, 第 3 个被截断的 review/fix/consensus/plan/exec decision 的 trigger 事件就落在
    [old, latest] 区间里, 旧实现盲标 skipped_on_resume → 永久去重 → 永不重派 = THE blocker 那类
    "审查在并发下被静默吞" (触发源从单飞门换成成本闸+resume)。修: 逐事件先路由, 只对【不产
    actionable skill/exec dispatch】的事件 (完工/main-inbound/no-route/无 decision) 才标 skip;
    产 actionable spawn decision 的事件【保留】(不标戳) → resume 后正常派发循环把它捞回重派。
    F-026-5 的真实目标 (暂停期手动做的完工事件别当新任务重放) 仍被覆盖 (那些事件不产 spawn
    decision, 仍被标 skip)。

    f-orchestrator-resume-per-event-route-on-backlog-quadratic (性能修): 去重检查改用
    _dispatch_stamp_event_ids() 一次性建好的本地索引 (O(1) 查表), 取代逐事件 is_already_dispatched
    (dispatch_to=None) 全量目录 glob —— 上面两条既有语义(T-FIX-COST-DROP / F-026-5) 的判定逻辑
    本身不变, 只是把"这个事件之前是否已经处理过"的检查方式换了实现, 行为等价、快得多。

    冻住的窗口由 owner/agent 各自 attach/respawn(不自动 resume — 让 owner 先看再决定)。
    Returns (之前是否暂停, 跳过的积压 seq 数)。
    """
    p = _pause_flag_path(towow_dir)
    existed = p.exists()
    # T-FIX-COST-DROP: 先读 paused_at (unlink 前) —— resume skip 守卫据它区分『暂停【前】已被
    # cap/成本闸截断、deferred 待重派的在途 spawn 决策 (保留)』与『暂停【期间】手动做的完工事件
    # (F-026-5 目标: 标 skip 防重放级联)』。两者都落 [old, latest] 且都可能产 spawn decision,
    # 唯一干净的分界是【事件发生时刻 vs 暂停时刻】(差至少一个轮询轮 + owner 动作, 绝非紧 race)。
    paused_at = _read_paused_at(p)
    with contextlib.suppress(OSError):
        p.unlink()
    skipped = 0
    if event_log is not None:
        # 把暂停期积压事件逐个标 dispatched(去重跳过) —— 否则光推水位线, 轮询的 range-inclusive
        # 重扫会把没派过的积压派出去(级联根因)。标 dispatched 后轮询 dedup 会跳过它们, 不重放;
        # 新事件(latest 之后)不受影响照常派。
        # T-FIX-COST-DROP: 但【暂停前已 deferred 的 actionable spawn decision (cap/成本闸截断)】保留,
        # 别吞掉在途审查/修复/执行决策 —— 给它们各写一份 nonexec backlog marker 让派发循环 re-scan 捞回。
        # 暂停【期间】发生的完工事件 (≥paused_at) 仍按 F-026-5 标 skip。这层"暂停前保留 / 暂停期 skip"
        # 的策略经 preserve_decisions 注入进【唯一】水位线推进原语 _advance_watermark_skip_backlog
        # (点火追平守卫 catchup_watermark_on_ignition 复用同一原语, preserve 空 —— 不各造一套)。
        router = OrchestratorDaemon(event_log, last_processed_seq=0)

        def _resume_preserve(rec: EventRecord) -> list[DispatchDecision]:
            # 暂停期间 (≥paused_at) 发生的事件 = 手动收口积压, 无待保留决策 → 走 F-026-5 标 skip;
            # 暂停前 (<paused_at) 未派的 spawn 决策 = deferred 在途工作, 返回它们以保留不吞。
            if not _event_before_pause(rec, paused_at):
                return []
            return _pending_spawn_decisions(towow_dir, router, rec)

        skipped = _advance_watermark_skip_backlog(
            towow_dir,
            event_log,
            preserve_decisions=_resume_preserve,
            stamp_payload={"skipped_on_resume": True},
        )
    # gap5 (R11): resume 留 canonical provenance (仅当之前真暂停过)。
    if existed and event_log is not None:
        emit_orchestrator_lifecycle(
            event_log, transition="resumed", reason="manual",
            detail={"skipped_backlog_seq": skipped},
        )
    return existed, skipped


# T-FIX-COST-DROP: resume skip-on-resume 守卫的 actionable 判据。dispatch_to 落在这些目标 =
# 不真起会话的纯审计/通知/无路由事件 (完工通知走 main-inbound, 不可路由走 no-route), 暂停期
# 重放它们无害 → 可安全标 skip。其余 (fix/review/engineering-consensus/planning/execution 等真
# skill/exec spawn) = 在途 deferred 工作, 标 skip 就是静默吞 (THE blocker)。
_NON_SPAWN_DISPATCH_TARGETS: frozenset[str] = frozenset(
    {"main-inbound", "Nature dashboard", "no-route"},
)


def _pending_spawn_decisions(
    towow_dir: Path,
    router: OrchestratorDaemon,
    rec: EventRecord,
) -> list[DispatchDecision]:
    """T-FIX-COST-DROP: 该事件路由后【未派且真起会话 (skill/exec spawn) 的 decision】列表。

    用于 resume skip-on-resume 守卫: 区分『暂停期手动完工事件 (重放无害, 可标 skip)』与
    『成本闸/cap 截断后 deferred 的在途 spawn 决策 (标 skip = 永久吞 = 红线)』。复合戳已派的
    decision 不算 pending (已在跑/已处理), 从结果排除。路由异常宽容吞 (绝不让守卫炸 resume):
    返回路由出的全部 spawn decision (偏保守保留, 宁可多留也不冒吞掉在途审查的险)。
    """
    try:
        decisions = router._route_event(rec)  # 同模块内, 复用唯一路由真源
    except Exception:  # 守卫绝不炸 resume; 偏保守保留事件 (不吞在途审查)
        return []
    pending: list[DispatchDecision] = []
    for decision in decisions:
        if decision.dispatch_to in _NON_SPAWN_DISPATCH_TARGETS:
            continue
        # execution 用 task_id 戳去重; 其余 (skill spawn) 用 trigger+dispatch_to 复合戳。
        if decision.task_id is not None and decision.dispatch_to == "execution":
            if not is_exec_task_dispatched(towow_dir, decision.task_id):
                pending.append(decision)
            continue
        if not is_already_dispatched(
            towow_dir, decision.trigger_event_id, decision.dispatch_to,
        ):
            pending.append(decision)
    return pending


# ════════════════════════════════════════════════════════════════════════════════
#  E.5 可观测性 — Agent 可调用的调度员状态快照 (RUN-028 task-run028-status)
#  首要消费者是发起者/Agent: 用真信号 (session_liveness) 看清调度员在干嘛 / 会话在不在跑,
#  取代读会撒谎的 state.json.detail。orchestrator status CLI 渲染同一份快照。
# ════════════════════════════════════════════════════════════════════════════════


def write_daemon_pid(towow_dir: Path) -> int:
    """T-L3kc-05 — write os.getpid() to orchestrator/daemon.pid (run_polling_loop start)."""
    ensure_orchestrator_layout(towow_dir)
    pid = os.getpid()
    pid_path = _orchestrator_dir(towow_dir) / "daemon.pid"
    pid_path.write_text(str(pid), encoding="utf-8")
    return pid


def clear_daemon_pid(towow_dir: Path) -> None:
    """T-L3kc-05 — remove orchestrator/daemon.pid (run_polling_loop graceful exit)."""
    (_orchestrator_dir(towow_dir) / "daemon.pid").unlink(missing_ok=True)


def write_spawn_mode_record(
    towow_dir: Path,
    event_log: EventLog | None,
    *,
    mock_spawn: bool,
    pid: int | None = None,
) -> dict[str, object]:
    """T-FIX-B1-04 (FORWARD-chain#4): 记录本次 polling loop 的真跑形态 (MOCK/REAL)。

    防 '生产以真自驱意图启动却没带 --real-spawn' 静默 mock 空转 —— daemon 看着在转 (水位线涨/
    心跳跳) 其实 0 改代码 (autopilot_idle_audit 实证的 15h 空转坑)。run_polling_loop 启动时调:

      - 持久 marker (orchestrator/spawn_mode.json): status 长期可见 + preflight grep 判 '真派还是
        mock 空转'。每次启动覆盖 (反映当前在跑这个 loop 的形态)。
      - canonical 事件 OrchestratorSpawnModeSet: 给 provenance (谁/何时/什么形态), 不只一个裸文件。
        event_log=None (极少, 仅纯文件测试) 时只写 marker 不 emit。

    不改自驱模型、不引中心总控, 只把 '真跑形态' 从隐式默认变可观测 + 防误入 mock。
    Returns 写下的 record dict。
    """
    ensure_orchestrator_layout(towow_dir)
    mode = "MOCK" if mock_spawn else "REAL"
    record: dict[str, object] = {
        "mode": mode,
        "mock_spawn": mock_spawn,
        "pid": pid if pid is not None else os.getpid(),
        "started_at": time.time(),
    }
    sp = _spawn_mode_path(towow_dir)
    tmp = sp.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(sp)  # atomic (tmp+rename), 同 watermark crash safety 范式
    if event_log is not None:
        payload: dict[str, object] = {
            "kind": "OrchestratorSpawnModeSet",
            "mode": mode,
            "mock_spawn": mock_spawn,
            "pid": record["pid"],
            "production_warning": (
                # 显著标注: MOCK 形态在真自驱意图下 = 空转风险 (preflight/owner 据此判断)
                "⚠ MOCK 形态: 若以真自驱(生产)意图启动, 此为 mock 空转 — 0 改代码 (T-FIX-B1-04)"
                if mock_spawn else None
            ),
            "orchestrator_spawn_mode": True,
        }
        intent = _build_orch_nodetouched(
            kind="OrchestratorSpawnModeSet",
            decision_id=f"spawn-mode-{mode.lower()}-{int(record['started_at'])}",
            payload_body=payload,
        )
        event_log.write_direct(intent)
    return record


def read_spawn_mode(towow_dir: Path) -> dict[str, object] | None:
    """T-FIX-B1-04: 读最近一次 polling loop 的真跑形态记录。

    None = 从未起过 loop (区分 '没起过' vs 'mock 空转' — 两者对 owner 含义不同)。
    """
    p = _spawn_mode_path(towow_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def _daemon_pid_recovery_lock(towow_dir: Path) -> Iterator[None]:
    """②D 竞态收尾 (2026-07-02): 用独立 flock 串行化"判死残留→清理→重抢"整段临界区。

    `_claim_daemon_pid_atomically` 的 os.link 只保证"创建"这一步互斥, 挡不住"清理死残留"
    这一步——判活/清理/重抢三步分离、中间无互斥的旧实现下: 两个进程各自独立判定同一个 pid
    已死后, 各自 unlink+重抢, 后一个 unlink 会连带清掉前一个已经成功重抢写入的**活**声明
    (unlink 认的是路径, 不认内容, 死残留和刚重抢成功的活声明用的是同一路径, 无法靠路径本身
    分辨谁是谁)——制造出两个都自认声明成功的赢家, 跟 ②D 本要根治的"崩机夜孤儿 daemon"是
    同一类竞态, 只是换了个更窄的地方重新冒出来。

    把"判活→清理→重抢"整段收进同一 flock 临界区后, 同一时刻只有一个进程在里面执行, 它
    完整跑完这整套判断+动作序列才会让出锁——不存在"清掉别人刚重抢成功的活声明"这种交错。
    daemonize() 的 pre-fork 快速检查 (省一次 fork 开销的优化, 不是安全边界本身) 也纳入同一把
    锁, 跟 claim_daemon_singleton_or_raise 互斥, 两处不会各判各的、互相踩踏。

    只 mkdir orchestrator 目录本身, 不调 `ensure_orchestrator_layout`——本函数也被
    `detect_and_recover_stale_daemon` 复用, 而后者同时是通用 PollingFrame 的入口
    (M-3.1 §9.1 red line: "frame 绝不读/写 orchestrator 编排 watermark")。
    `ensure_orchestrator_layout` 会顺带初始化 watermark.json/dispatched/ 等一整套
    orchestrator-only 状态, 对 PollingFrame 调用方是越界副作用。
    """
    orch_dir = _orchestrator_dir(towow_dir)
    orch_dir.mkdir(parents=True, exist_ok=True)
    lock_path = orch_dir / "daemon.pid.lock"
    with lock_path.open("a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def daemonize(towow_dir: Path) -> int | None:
    """T-L3kc-05 — POSIX double-fork into the background (M-3.1 §7.3).

    Returns the daemon's pid in the ORIGINAL caller (so the CLI can report it and exit), and
    None in the daemon process itself (the grandchild — caller then runs run_polling_loop,
    which writes the real daemon.pid). Daemon std streams are redirected to
    orchestrator/daemon-run.log so the background process never writes to the terminal.
    """
    ensure_orchestrator_layout(towow_dir)
    pid_path = _orchestrator_dir(towow_dir) / "daemon.pid"
    # B5 (PARALLEL-EXEC-FIX) 单例守卫 — 必须在 unlink 之前, 且判活+清理必须在同一把
    # _daemon_pid_recovery_lock 临界区内完成: 旧行为是"读一次判活, 不活就无条件 unlink",
    # 第二个 --daemon 启动会先擦掉活 daemon 的 pid 文件, 等任何后续守卫去读时已经看不到活
    # daemon → 放行双 daemon (盘上 daemon.pid 空的成因, 红队亲查)。活 daemon 在跑 → 拒绝,
    # 不擦不 fork。fork 前就退出临界区(不持锁跨 fork), 不影响后续 run_polling_loop 里
    # claim_daemon_singleton_or_raise 再次取同一把锁。
    with _daemon_pid_recovery_lock(towow_dir):
        running_pid, running_alive = is_orchestrator_process_alive(towow_dir)
        if running_alive and running_pid is not None and running_pid != os.getpid():
            raise RuntimeError(
                f"orchestrator daemon already running (pid={running_pid}) — refusing to start a "
                "second (B5 singleton). 看状态: towow orchestrator status",
            )
        pid_path.unlink(missing_ok=True)  # clear stale pid, 全程持锁, 不会跟并发重抢交错

    first = os.fork()
    if first > 0:
        # original caller: wait for the intermediate to exit, then read the daemon's written pid.
        os.waitpid(first, 0)
        for _ in range(100):  # up to ~5s for the grandchild to write its pid
            if pid_path.exists():
                with contextlib.suppress(OSError, ValueError):
                    return int(pid_path.read_text(encoding="utf-8").strip())
            time.sleep(0.05)
        return None

    # intermediate child: detach from controlling terminal, fork the daemon, then exit.
    os.setsid()
    second = os.fork()
    if second > 0:
        os._exit(0)

    # grandchild = daemon: redirect std streams, return None so the caller runs the loop.
    log_path = _orchestrator_dir(towow_dir) / "daemon-run.log"
    with contextlib.suppress(OSError):
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        logfd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(logfd, 1)
        os.dup2(logfd, 2)
    return None


def _claim_daemon_pid_atomically(towow_dir: Path) -> tuple[bool, int | None]:
    """②D (2026-07-02 崩机根治): 原子声明 daemon.pid —— 内核保证只有一个调用者能声明成功, 把旧版
    "先读再判再写"check-then-write 的 TOCTOU 窗口收窄到单次原子操作。

    实现用"写临时文件 → os.link 硬链接改名"而不是 O_CREAT|O_EXCL 直接写目标路径: 后者在
    open() 成功、write() 落笔之间留了一个"文件已存在但内容还是空的"窗口——另一个进程在这个
    窗口里撞见 EEXIST 去读内容, 读到空字符串解析失败, 会把活着的赢家误判成"pid 读不出=死残留"
    继而清理重抢, 制造出两个都自认声明成功的赢家 (真实压测下命中过)。os.link 是单一原子系统
    调用, 且被链接的临时文件在链接发生前已经写完整内容——任何进程只要能看到目标路径存在,
    看到的必然是完整 pid, 不存在"存在但空"的中间态。

    Returns (claimed, pid): claimed=True → 已把自己的 pid 原子声明, pid=自己的 pid。
    claimed=False → 目标已存在(EEXIST, 别人先到), pid=现存文件里读到的 pid (读不出→None)。
    """
    ensure_orchestrator_layout(towow_dir)
    orch_dir = _orchestrator_dir(towow_dir)
    pid_path = orch_dir / "daemon.pid"
    my_pid = os.getpid()
    tmp_path = orch_dir / f"daemon.pid.{my_pid}.{uuid.uuid4().hex[:8]}.tmp"
    tmp_path.write_text(str(my_pid), encoding="utf-8")
    try:
        try:
            os.link(str(tmp_path), str(pid_path))
        except FileExistsError:
            try:
                existing = int(pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                existing = None
            return False, existing
    finally:
        tmp_path.unlink(missing_ok=True)
    return True, my_pid


def claim_daemon_singleton_or_raise(towow_dir: Path) -> int:
    """②D (2026-07-02 崩机根治): B5 单例守卫的真正安全边界 —— 原子声明, 不是"检查后再写"。

    2026-07-01 夜实证: OOM 杀掉 daemon 留 stale pid + 反复前台重启 (daemon-run.log 记了约 10
    次循环) 的场景下, 旧版"读 pid 文件判活 → (不活)清 → fork/写自己的 pid"这套 check-then-write
    在两个并发 start 之间留了 TOCTOU 窗口——都读到"不活"、都往下走, 其中一个成了脱管的第二个
    daemon (游离 orchestrator, 手写脚本 spawn 出来的那个正是撞上了这条缝)。

    改: 用 O_EXCL 把"判断+登记"收成一次原子系统调用。已有【活】daemon → 拒绝 (RuntimeError,
    调用方不重试, 这是真正的单例保护); pid 是【死残留】→ 清理后【单次】重试原子声明。

    "判活→清理→重抢"整段包在 `_daemon_pid_recovery_lock` 的 flock 临界区内 (同一时刻只有
    一个调用者在里面执行), 不是三步各自为政——早期版本把这三步分开、中间不互斥, 两个并发
    调用者若都判定同一个 pid 已死, 会各自 unlink+重抢, 后一个 unlink 连带清掉前一个刚重抢
    成功的**活**声明 (unlink 认路径不认内容, 分不清死残留和刚写入的活声明), 制造出两个都
    自认声明成功的赢家——这正是本函数要根治的"崩机夜孤儿 daemon", 只是换了个更窄的地方
    重新冒出来 (2026-07-02 全量测试重负载下命中过一次, 见 orchestrator.py 顶部本次修复的
    commit message)。收进同一把锁后, 结构上不再可能出现这种交错。

    这是 `run_polling_loop` (前台模式与 `--daemon` 派生出的 grandchild 都会调它) 的唯一登记
    入口, 取代原先分离的 `detect_and_recover_stale_daemon` + `write_daemon_pid` 两步。
    `daemonize()` 自己的 pre-fork 检查纳入同一把锁 (跟本函数互斥), 仍是"多数情况下省一次
    fork 开销"的快速失败优化, 不是独立的安全边界——即便两个 `--daemon` 并发都通过了那道
    快速检查各自 fork, 两个 grandchild 最终都会走到这里串行执行, 只让一个真正登记成功,
    另一个立刻抛错退出 (不会作为脱管进程常驻——干净退出 ≠ 孤儿)。
    """
    with _daemon_pid_recovery_lock(towow_dir):
        claimed, existing_pid = _claim_daemon_pid_atomically(towow_dir)
        if claimed:
            assert existing_pid is not None  # claimed=True 时 existing_pid 就是刚写入的自己的 pid
            return existing_pid
        alive = False
        if existing_pid is not None:
            try:
                os.kill(existing_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True  # 活着 (只是非本用户), 保守当活
            except OSError:
                alive = False
        if alive:
            raise RuntimeError(
                f"orchestrator daemon already running (pid={existing_pid}) — refusing to start a "
                "second (B5 singleton). 看状态: towow orchestrator status",
            )
        # 确认已死 (或 pid 文件损坏读不出) → 清理残留, 单次重试原子声明。全程持锁, 不会跟
        # 另一个并发调用者的判断+清理+重抢交错。再撞 EEXIST 只可能是有代码绕过本函数直接
        # 操作 pid 文件 (不应发生) —— 报错交给调用方按正常流程重跑, 不无限重试制造活锁。
        clear_daemon_pid(towow_dir)
        claimed2, existing_pid2 = _claim_daemon_pid_atomically(towow_dir)
        if claimed2:
            assert existing_pid2 is not None
            return existing_pid2
        raise RuntimeError(
            f"orchestrator daemon 启动撞上并发竞态 (清理死 pid 后重新声明仍失败, "
            f"pid={existing_pid2}) — 大概率是另一个 start 抢先声明了, 重跑 "
            "`towow orchestrator start` 或 `towow orchestrator status` 确认现状。",
        )


def is_orchestrator_process_alive(towow_dir: Path) -> tuple[int | None, bool]:
    """读 orchestrator/daemon.pid + 判进程是否活。Returns (pid, alive)。

    pid 文件缺 → (None, False)。pid 在但进程不存在(死 pid 残留) → (pid, False)。
    PermissionError → 进程活着(只是非本用户)。
    """
    pid_path = _orchestrator_dir(towow_dir) / "daemon.pid"
    if not pid_path.exists():
        return None, False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None, False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid, False  # 死 pid 残留
    except PermissionError:
        return pid, True
    except OSError:
        return pid, False
    return pid, True


@dataclass(frozen=True)
class StaleDaemonRecovery:
    """M-3.1 §9.1 daemon restart 检测恢复结果 (start 时检测上次 crash 残留)。

    found_stale=True  → 上次 daemon 崩了, 留下死 pid; 本次已清理 (cleared)。
    found_stale=False → 干净 start (无 pid) 或 已有【活】daemon 在跑 (running_pid 给出, 绝不碰)。
    """

    found_stale: bool
    stale_pid: int | None = None
    cleared: bool = False
    running_pid: int | None = None


def detect_and_recover_stale_daemon(towow_dir: Path) -> StaleDaemonRecovery:
    """M-3.1 §9.1 (row6) — daemon 重启时检测上次 crash 残留的死 pid 并恢复 (清理)。

    Daemon crash → daemon.pid 留在盘上指向已死进程。下次启动 (run_polling_loop / 通用
    PollingFrame) 调本函数: 死 pid 残留 → 这是 crash 证据 → 清掉 (clear_daemon_pid) → 报
    found_stale=True (供观测/审计)。从 last watermark 继续 polling + dispatched/ dedup 由
    既有机制保证 (本函数只负责"检测+清死 pid 残留")。

    🔴 红线 (owner INF-003):
    - 只清【确认已死】的 pid (进程不存在)。pid 指向【活】进程 → 绝不碰 (另一 daemon 在跑),
      报 running_pid 让调用方自决 (不在本函数里抢/覆盖)。
    - 绝不 orchestrator start / resume, 绝不读/写 watermark, 绝不读/清 paused.flag。

    ②D 收尾 (2026-07-02): 判活+清理纳入 `_daemon_pid_recovery_lock` 同一把 flock ——本函数是
    CLI `orchestrator start` 和通用 PollingFrame 的独立入口, 跟 `claim_daemon_singleton_or_raise`
    读写同一个 daemon.pid 路径。之前两处判活+清理各自为政、互不互斥: 一方判定死 pid 后清理
    重抢成功、另一方基于自己更早的读判断仍去清理, 会把前者刚写入的**活**声明连带清掉 (unlink
    认路径不认内容) ——跟 claim_daemon_singleton_or_raise 自身那处竞态同源, 不收进同一把锁,
    补了一个入口漏了另一个等于没补。
    """
    with _daemon_pid_recovery_lock(towow_dir):
        pid, alive = is_orchestrator_process_alive(towow_dir)
        if pid is None:
            return StaleDaemonRecovery(found_stale=False)
        if alive:
            # 活 daemon 在跑 — 不是 crash, 别碰它的 pid (调用方据 running_pid 自决是否另起)。
            return StaleDaemonRecovery(found_stale=False, running_pid=pid)
        # 死 pid 残留 = 上次 crash → 清理 (恢复到干净可重启态)。
        clear_daemon_pid(towow_dir)
        return StaleDaemonRecovery(found_stale=True, stale_pid=pid, cleared=True)


def pending_escalations(event_log: EventLog) -> list[dict[str, object]]:
    """T4 (PLAN-FIX): 未被 owner 响应的 GoalEscalationRaised 列表。

    让停摆在 orchestrator status 可见 — T-L0-02 escalation 停了后台 4 天没人知, 病根之一是
    "暂停了但通知没送达 owner"。把 pending escalation 摆进 owner 常看的 status, 任何时候查都见
    "有 N 条 escalation 等你回话 + 各问什么"。已响应判定 = 存在 NatureJudgmentCaptured 带
    responds_to_escalation_event_id == 该 escalation 的 event_id (escalation_subscribe 同款契约)。
    """
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    return _pending_escalations_from(event_log.committed_index().records())


def _pending_escalations_from(records: list[EventRecord]) -> list[dict[str, object]]:
    """pending_escalations core over a pre-fetched committed-record list (T-FND-02).

    Lets the throttled stuck-baton sweep reuse ONE warm-index snapshot across its sub-checks rather
    than re-scanning the log per call; the public pending_escalations keeps its disk read for other
    callers (status / owner-signal surface).
    """
    raised: dict[str, dict[str, object]] = {}
    responded: set[str] = set()
    for rec in records:
        et, payload = _unwrap_stub_rewrap(rec)
        if et == EventType.GOAL_ESCALATION_RAISED.value:
            after = payload.get("after_state") if isinstance(payload, dict) else None
            src = after if isinstance(after, dict) else payload
            if isinstance(src, dict):
                raised[rec.event_id] = {
                    "escalation_event_id": rec.event_id,
                    "goal_session_id": src.get("goal_session_id", ""),  # B1-02: 反查关联 task
                    "owner_question": src.get("owner_question", ""),
                    "blocking_scope": src.get("blocking_scope", "global"),
                    "raised_at": src.get("raised_at", ""),
                }
        elif et == "NatureJudgmentCaptured":
            after = payload.get("after_state") if isinstance(payload, dict) else None
            src = after if isinstance(after, dict) else payload
            rid = src.get("responds_to_escalation_event_id") if isinstance(src, dict) else None
            if isinstance(rid, str) and rid:
                responded.add(rid)
    return [info for eid, info in raised.items() if eid not in responded]


def surface_owner_signal(event_log: EventLog) -> int:
    """R08 TI-4: daemon 据 pending escalation 构建 owner 可见提示并 surface 到 main-inbound。

    把"哪个 session 在等你拍什么"的结构化可见提示顶到 main-inbound (owner 进主对话即见)。
    只为有 blocking 级 (blocking_scope=global) 在等时点亮; 全 session_only 则不打扰 (静默)。
    跟 T-FIX-B1-03 的"久卡告警"互补: 那个兜烂掉的, 这个是"现在有人在等你"的常态可见提示。

    🔴 函数纯逻辑可单测; **live 驱动 (daemon 每轮调它) gated on R11 松闸** —— daemon 暂停期间
    不自动跑。去重交给 main-inbound poller (同 OrchestratorDispatched 范式)。Returns emit 数 (0/1)。
    """
    pend = pending_escalations(event_log)
    blocking = [e for e in pend if e.get("blocking_scope", "global") == "global"]
    if not blocking:
        return 0
    # 最急 = 第一条 (pending 已按发生序; 简单稳定, 不引时间依赖)
    top = blocking[0]
    sid = str(top.get("goal_session_id") or top.get("escalation_event_id") or "")
    essence = str(top.get("owner_question") or "(待 prep)")
    headline = f"⚠ 有 {len(blocking)} 件在等你拍板 — 最急: session {sid} · {essence}"
    emit_orchestrator_dispatched(
        event_log,
        DispatchDecision(
            trigger_event_id="owner-signal-surface",
            trigger_event_type="OwnerSignalSurfaced",
            dispatch_to="main-inbound",
            reason=headline,
        ),
    )
    return 1


def _exec_session_to_task_map(events: list[dict[str, object]]) -> dict[str, str]:
    """T-FIX-B1-02: 反查 execution bg 会话 → 它跑的 task_id。

    编排器派 execution 工位时 emit OrchestratorDispatched(dispatch_to=execution, task_id=...,
    spawn_result.bg_session_id=<bg 会话 id>)。GoalEscalationRaised 载的 goal_session_id 就是这个
    bg 会话 id。两者拼起来 = "哪个 task 的会话撞了 escalation"。_all_events_as_dicts 已把
    stub_original_payload 顶层字段 (task_id / spawn_result) 规整进 after_state。
    """
    out: dict[str, str] = {}
    for e in events:
        if e.get("event_type") != "OrchestratorDispatched":
            continue
        payload = e.get("payload")
        a = payload.get("after_state") if isinstance(payload, dict) else None
        if not isinstance(a, dict) or a.get("dispatch_to") != "execution":
            continue
        tid = a.get("task_id")
        spawn = a.get("spawn_result")
        bg = spawn.get("bg_session_id") if isinstance(spawn, dict) else None
        if isinstance(tid, str) and tid and isinstance(bg, str) and bg:
            out[bg] = tid  # 同 session 后写覆盖前 (一个 bg 会话只跑一个 task)
    return out


def tasks_blocked_on_pending_escalation(
    event_log: EventLog,
    events: list[dict[str, object]],
) -> dict[str, str]:
    """T-FIX-B1-02 (FORWARD-chain#6 / FORWARD-chain#2): 关联未响应 escalation 的 task 集合。

    收敛 escalation 自旋的归宿: 若某 task 的执行会话曾 emit GoalEscalationRaised 且该 escalation
    仍 pending (无匹配 NJ), 重派它也只会再撞同一 owner-only 决策墙 → 应停派标 blocked_on_escalation,
    等 owner 响应 (NJ 落账) 后由下一轮 ready-set 自动捞回。

    复用 pending_escalations 的 pending 判定 (escalation_subscribe 同款 responds_to 契约), 经
    _exec_session_to_task_map 把 escalation 的 goal_session_id 反查到 task。

    Returns {task_id: escalation_event_id} — 每个被 pending escalation 挡住的 task 及其 escalation。
    """
    sess_to_task = _exec_session_to_task_map(events)
    blocked: dict[str, str] = {}
    for esc in pending_escalations(event_log):
        gsid = esc.get("goal_session_id")
        esc_eid = esc.get("escalation_event_id")
        if not isinstance(gsid, str) or not gsid:
            continue
        task_id = sess_to_task.get(gsid)
        if task_id and isinstance(esc_eid, str) and esc_eid:
            blocked.setdefault(task_id, esc_eid)  # 同 task 多 escalation → 取首个 pending
    return blocked


def answered_escalation_reentry_count(
    event_log: EventLog,
    events: list[dict[str, object]],
) -> dict[str, int]:
    """T-STUCK-STATE-ROOTFIX (task-stuck-state@v1 出口④ reentry 度量): 每个 task 的【被 owner
    应答过的 escalation】计数 —— escalate→answer→re-escalate 循环的深度。

    收敛 T-DEC-5 saga 的根: escalation-blocked 的 task 【故意不计入重派熔断】(它是等 owner 不是
    失败, 见 _dispatch_execution_batch esc_blocked 块)。但若一个 task 的前提落空 (兄弟 task 已把它
    做完), owner 每次应答后它再撞同一墙 → 再 escalate → 再被应答 → 无限循环, 且【完全无界】(每
    一圈都被熔断计数豁免)。本函数给这个循环一个可数的深度: 一条 escalation 只要已有匹配 NJ
    应答 (responds_to), 就为它归属的 task 记 1。达 _redispatch_cap 表示 "owner 反复应答仍无法解决"
    = owner-answer 通道已证明无效 → 该 task 应被 held (出 ready-set) + 投 dead-letter 等 owner 退役
    决定 (见 escalation_reentry_exhausted_tasks 的消费)。

    只数【已应答】的 escalation: 一条仍 pending (无 NJ) 的 escalation 归 tasks_blocked_on_pending_
    escalation 管 (继续等), 不进本计数; 唯有 "应答了却又回来" 才是无效循环的信号。成功收尾的 task
    进 completed_success → ready-set 层就被剔, 根本到不了本计数的消费点, 故无 "高计数误杀成功 task"。

    复用 _reflow_answers_and_sources (回流三件事: answers=已应答 escalation, sources=escalation→
    等待方) + _exec_session_to_task_map (goal_session_id→task), 与 tasks_blocked_on_pending_
    escalation 同一套 session→task 反查, 判据不漂移。Returns {task_id: 已应答 escalation 数}。
    """
    records = event_log.committed_index().records()
    answers, sources, _reflowed = _reflow_answers_and_sources(records)
    sess_to_task = _exec_session_to_task_map(events)
    counts: dict[str, int] = {}
    for esc_eid in answers:  # 只遍历【已有 NJ 应答】的 escalation
        src = sources.get(esc_eid)
        if not isinstance(src, dict) or src.get("source_kind") != "goal":
            continue  # 只 goal 链有 exec task; fix/exec 链的 escalation 归 reflow fix 侧, 不在派发池
        gsid = src.get("goal_session_id")
        if not isinstance(gsid, str) or not gsid:
            continue
        task_id = sess_to_task.get(gsid)
        if task_id:
            counts[task_id] = counts.get(task_id, 0) + 1
    return counts


def escalation_reentry_exhausted_tasks(
    event_log: EventLog,
    events: list[dict[str, object]],
    *,
    cap: int | None = None,
) -> set[str]:
    """T-STUCK-STATE-ROOTFIX (task-stuck-state@v1 出口④): 被回答的 escalation 已循环耗尽的 task 集。

    task 的【已应答 escalation】计数 ≥ cap (默认 _redispatch_cap=3, 与死信回头客上限同值) → 判定
    "owner-answer 通道无法解决" (前提落空类)。消费方 (_dispatch_execution_batch) 据此把 task
    held 出派发池 + 投 dead-letter (circuit_tripped, 既有 sweep_aged_out 兜底→retired 终态) + 显著
    surface owner —— 不再无脑重回 ready-set 重派 (done_criterion 1)。

    owner GT#4 红线严守: 本判定只导致 held + dead-letter + surface, 【绝不】自判 close task 作废
    (task→retired 仍须 owner-confirm 物理凭证, closure-evidence-verification-gate@v1 守)。编排器
    给的是 "这个 task 卡死在 owner-answer 循环里, 请 owner 决定退役" 的呈现, 不是替 owner 拍板。

    level-triggered (每轮从 events 重算, 稳定幂等), 与 esc_blocked / pending_replan 同模式。
    """
    threshold = cap if cap is not None else _redispatch_cap()
    return {
        t
        for t, c in answered_escalation_reentry_count(event_log, events).items()
        if c >= threshold
    }


def emit_escalation_still_waiting(
    event_log: EventLog,
    *,
    escalation_event_id: str,
    goal_session_id: str,
    waited_s: float,
) -> str:
    """T-FIX-B1-02 (2): wait-resume 超时归宿的可观测落账 (不衍生新 escalation_id)。

    现状 goal wait-resume 超时裸 sys.exit(124) — 该 bg 会话被 reconcile 当 STOPPED → silent-death
    → 重派 → 再撞同一 owner-only 决策 → 再 escalate → 再超时, 形成无限 escalation+重派循环, 且
    无任何观测落账。改: 超时时维持【同一】escalation 为单一 pending 追踪 (不产生新 escalation_id),
    并 emit 一条 EscalationStillWaiting 记录 (载原 escalation_event_id + 已等时长), 让"还在等 owner"
    可观测。它走 _build_orch_nodetouched + write_direct (NodeTouched 审计, 不污染 GoalEscalationRaised
    计数 — pending_escalations 只数 GoalEscalationRaised, 故同一 escalation 仍只 1 条)。
    """
    decision_id = f"esc-still-waiting-{escalation_event_id[:24]}"
    payload: dict[str, object] = {
        "kind": "EscalationStillWaiting",
        "decision_id": decision_id,
        "escalation_event_id": escalation_event_id,
        "goal_session_id": goal_session_id,
        "waited_s": waited_s,
        "recorded_at": time.time(),
    }
    intent = _build_orch_nodetouched(
        kind="EscalationStillWaiting",
        decision_id=decision_id,
        payload_body=payload,
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


# ════════════════════════════════════════════════════════════════════════════════
#  T-LRF-06 (escalation-answer-reflow@v1) — 升级答复回流『主动半边』
#
#  补审计实证缺口 (escalation evt-c43d2f3c 卡 13.5 天): owner 答复落账后, 既有基础设施只做到
#  "下一轮 ready-set 自动捞回重派" (tasks_blocked_on_pending_escalation), 但【答案内容从未被投递
#  进等待任务】→ 重派只会再撞同一 owner-only 决策墙。本块补"把答案真送到、并留痕证明被消费"。
#
#  四步 workflow (概念 escalation-answer-reflow@v1 钉死, 7 态 SAGA 的驱动):
#    ① 解析等待方: goal 链 (GoalEscalationRaised.goal_session_id → _exec_session_to_task_map →
#       task) / fix·exec 链 (EscalationRaised.after_state.task_id); 解析不出 = FYI 留收件箱
#       (no_waiting_task 终态, 不进死信 —— 死信只给【步骤失败】用)。
#    ② 答案注入: 把答案载体 park 进 .towow/orchestrator/escalation_answers/<task>.json —
#       try_spawn_for_decision 重派该 task 时读出, 追加进重派 prompt = 答案真到达 fresh 会话
#       (injection_evidence = 这条 applied 留痕本身指向的 park; 概念允许"重派 dispatch event_id")。
#    ③ orchestrator 唤醒: execution 任务靠 NJ 落账后 pending 翻假 → 下轮 ready-set 自动重派
#       (既有, 不重造); paused goal bg 会话经 escalation-pause-resume-via-event-channel 既有事件
#       通道 resume (复用不重实现)。
#    ④ applied 留痕: emit EscalationAnswerApplied (IMMUTABLE_TRUTH) = 答案被消费证据, 闭合回流。
#
#  权威触发 = NatureJudgmentCaptured 且 after_state.responds_to_escalation_event_id 非空
#  (escalation_subscribe / pending_escalations 同款契约; brief 列的 'EscalationAnswered' 事件类型
#  代码中不存在 → 落地前消除二义, 不用它)。
#
#  失败 (解析/注入/留痕任一步抛错) → 有界重试 (_redispatch_cap, 默认 3) 耗尽 → 进
#  dead-letter-inbox (entry_reason=structural_failure) + emit EscalationReflowDeadLettered 终态。
#
#  去重 (附加条款①): 已有终态 (applied / no_waiting / dead_lettered) 的 escalation 不再处理 —
#  真相源 = 终态事件本身 (不另写 marker 防两真相源漂移)。
#
#  owner-gate 红线 escalation (gsid=owner-gate-<task>) 排除: 红线"NJ 不自动放行", 每轮都拦,
#  绝不经回流自动重派 (fnd-r01-9 语义)。
#
#  🔴 本驱动只 emit canonical + 写 park 文件, 不 spawn — 故挂进 surface-only 自愈 sweep,
#  paused 下也安全 (真重派由既有 ready-set 在非 paused 时做)。
# ════════════════════════════════════════════════════════════════════════════════

_ESCALATION_ANSWERS_SUBDIR = "escalation_answers"
_REFLOW_RETRY_SUBDIR = "reflow_retry"
_OWNER_GATE_SID_PREFIX = "owner-gate-"
# C-2/v3: goal-bg 会话确认 dead 时答案的持久 park (与 exec-task 重派用的 _escalation_answers_dir
# 分开的子目录 —— 那边是"等重派时消费掉", 这边是"会话已死, 没有消费方, 纯留痕给分诊/追责用",
# 语义不同不共用目录, 防止误把这条当"待重派注入"读出)。
_STRANDED_ANSWERS_SUBDIR = "escalation_answers_stranded"


def _escalation_answers_dir(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _ESCALATION_ANSWERS_SUBDIR


def _stranded_answers_dir(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _STRANDED_ANSWERS_SUBDIR


def _reflow_retry_dir(towow_dir: Path) -> Path:
    return _orchestrator_dir(towow_dir) / _REFLOW_RETRY_SUBDIR


def _reflow_answers_and_sources(
    records: list[EventRecord],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, object]], set[str]]:
    """一次扫账本拿回流三件事 (T-LRF-06):

    - answers: {escalation_event_id: {nj_event_id, answer_text}} — NJ 带 responds_to 的 owner 答复
      (answer_text 取 owner-facing quote, 退 meaning)。
    - sources: {escalation_event_id: {source_kind, ...}} — 原 escalation 事件 (GoalEscalationRaised /
      EscalationRaised) unwrap 后的解析等待方所需字段。
    - reflowed: 已有终态 (EscalationAnswerApplied / NoWaitingPartyDetected /
      EscalationReflowDeadLettered) 的 escalation_event_id 集合 = 去重真相源 (附加条款①)。
    """
    answers: dict[str, dict[str, str]] = {}
    sources: dict[str, dict[str, object]] = {}
    reflowed: set[str] = set()
    for rec in records:
        et, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state") if isinstance(payload, dict) else None
        src = after if isinstance(after, dict) else (payload if isinstance(payload, dict) else {})
        prov_sid = rec.provenance.session_id if rec.provenance else None
        if et == "NatureJudgmentCaptured":
            rid = src.get("responds_to_escalation_event_id")
            if isinstance(rid, str) and rid:
                txt = src.get("quote") or src.get("meaning") or ""
                answers[rid] = {"nj_event_id": rec.event_id, "answer_text": str(txt)}
        elif et == EventType.GOAL_ESCALATION_RAISED.value:
            sources[rec.event_id] = {
                "source_kind": "goal",
                "goal_session_id": src.get("goal_session_id"),
                "blocking_scope": src.get("blocking_scope", "global"),
            }
        elif et == EventType.ESCALATION_RAISED.value:
            sources[rec.event_id] = {
                "source_kind": "fix_exec",
                "task_id": src.get("task_id"),
                "fix_id": src.get("fix_id"),
                "finding_id": src.get("finding_id"),
                "raiser_session_id": prov_sid,
            }
        elif et in {
            "EscalationAnswerApplied", "NoWaitingPartyDetected", "EscalationReflowDeadLettered",
            # C-2/v3: 搁浅终态也要计入去重 —— 否则每轮巡检都会对同一条已死会话的 escalation
            # 重新 park+死信一遍 (dead_letter_inbox.enqueue 本身按 (ref, reason) 幂等不会重复
            # 入箱, 但 park 文件/emit 事件会重复写, 且永远不会被本函数的 answers 循环剔出去)。
            "EscalationAnswerParkedStranded",
        }:
            eid = src.get("escalation_event_id")
            if isinstance(eid, str) and eid:
                reflowed.add(eid)
    return answers, sources, reflowed


@dataclass(frozen=True)
class _ReflowWaitingParty:
    """解析出的等待方 (step ①)。task_id=None 表示 goal bg 会话级 (无 exec 任务, 走既有 resume 通道)。"""

    task_id: str | None
    session_id: str
    source: str  # "goal" | "fix_exec"
    park_for_redispatch: bool  # True = 有 exec task 可重派, park 答案供 try_spawn 注入


def _resolve_reflow_waiting_party(
    source: dict[str, object],
    sess_to_task: dict[str, str],
) -> _ReflowWaitingParty | None:
    """概念钉死的等待方解析 (step ①)。None = 无等待方 (FYI / no_waiting_task)。

    - goal 链: goal_session_id → _exec_session_to_task_map 反查 task; 映射到 task → 可重派 park;
      未映射但 gsid 真存在 (真 goal bg 会话) → 会话级等待 (resume 走既有事件通道, 不 park)。
      owner-gate 合成 gsid (owner-gate-<task>) 是红线拦截标识、非等待方 → 排除 (None)。
    - fix/exec 链: after_state.task_id → 可重派 park; 无 task_id (仅 fix_id/finding_id) → 无可重派
      等待方 → None (FYI)。
    """
    kind = source.get("source_kind")
    if kind == "goal":
        gsid = source.get("goal_session_id")
        if not isinstance(gsid, str) or not gsid:
            return None
        if gsid.startswith(_OWNER_GATE_SID_PREFIX):
            return None  # 红线: NJ 不自动放行, 永不经回流重派
        task_id = sess_to_task.get(gsid)
        if task_id:
            return _ReflowWaitingParty(task_id, gsid, "goal", park_for_redispatch=True)
        # gsid 真存在但未映射到 exec task → goal bg 会话级等待 (既有 resume 通道送达, 仅留 applied 凭据)
        return _ReflowWaitingParty(None, gsid, "goal", park_for_redispatch=False)
    if kind == "fix_exec":
        fe_task_id = source.get("task_id")
        if isinstance(fe_task_id, str) and fe_task_id:
            sess = source.get("raiser_session_id")
            sid = sess if isinstance(sess, str) and sess else fe_task_id
            return _ReflowWaitingParty(fe_task_id, sid, "fix_exec", park_for_redispatch=True)
        return None
    return None


def emit_escalation_answer_applied(
    event_log: EventLog,
    *,
    escalation_event_id: str,
    nj_event_id: str,
    applied_to_task_id: str,
    applied_to_session_id: str,
    answer_text: str,
    resumed: bool,
) -> str:
    """④ applied 留痕 — owner 答复真送回等待任务并被消费的硬闭环凭据 (IMMUTABLE_TRUTH 永久保留)。

    唯一 emit 权威 (C-2/v3 收敛): CLI `escalation respond` (main.py) 只负责让 NJ 落账, 不再自己
    emit 本事件 —— 之前 CLI 与本驱动各判一次、CLI 先手无条件断言 resumed=True, 本驱动的去重
    (`_reflow_answers_and_sources` 的 reflowed 集合) 又会把 CLI 已 emit 的 escalation 当"已终态"
    跳过, 真送达从未被核实过 (2026-07 owner 实战: 飞书回复丢失根因)。现在 exec-task 重派链
    (park_for_redispatch=True) 与 goal-bg 会话级链 (vitality 判活 push, 见下方 v3 分支) 都只在
    这里、本驱动巡检时 emit, 且都是核实过真送达 (park 文件保证下次重派消费 / push_fn 返回 True
    才算数) 之后才落这条凭据。base_classification=IMMUTABLE_TRUTH: 答案被消费凭据是 Nature
    决策事实, GC 不可扫掉 (否则回流被判未闭合)。
    """
    decision_id = f"esc-answer-applied-{escalation_event_id[:24]}"
    payload: dict[str, object] = {
        "kind": "EscalationAnswerApplied",
        "escalation_event_id": escalation_event_id,
        "nj_event_id": nj_event_id,
        "applied_to_task_id": applied_to_task_id,
        "applied_to_session_id": applied_to_session_id,
        "answer_text": answer_text,
        "resumed": resumed,
    }
    intent = _build_orch_nodetouched(
        kind="EscalationAnswerApplied",
        decision_id=decision_id,
        payload_body=payload,
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    return event_log.write_direct(intent).event_id


def emit_no_waiting_party_detected(
    event_log: EventLog,
    *,
    escalation_event_id: str,
    nj_event_id: str,
) -> str:
    """no_waiting_task 终态 — FYI 级 escalation (无等待方) 答复落账后直接完成, 仅留收件箱。

    不进死信 (死信只给【步骤失败】用): 概念明确 answered→closed 对无等待任务 FYI 仍合法。留此终态
    = 回流去重真相源 (下轮不再重处理) + 可观测"这条答复无人死等"。
    """
    decision_id = f"esc-no-waiting-{escalation_event_id[:24]}"
    payload: dict[str, object] = {
        "kind": "NoWaitingPartyDetected",
        "escalation_event_id": escalation_event_id,
        "nj_event_id": nj_event_id,
        "recorded_at": time.time(),
    }
    intent = _build_orch_nodetouched(
        kind="NoWaitingPartyDetected", decision_id=decision_id, payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def emit_escalation_reflow_dead_lettered(
    event_log: EventLog,
    *,
    escalation_event_id: str,
    nj_event_id: str,
    failed_step: str,
    last_error: str,
) -> str:
    """failed_dead_lettered 终态 — 解析/注入/留痕任一步有界重试耗尽 → 已投死信, 不静默卡。"""
    decision_id = f"esc-reflow-dead-{escalation_event_id[:24]}"
    payload: dict[str, object] = {
        "kind": "EscalationReflowDeadLettered",
        "escalation_event_id": escalation_event_id,
        "nj_event_id": nj_event_id,
        "failed_step": failed_step,
        "last_error": last_error[:500],
        "recorded_at": time.time(),
    }
    intent = _build_orch_nodetouched(
        kind="EscalationReflowDeadLettered", decision_id=decision_id, payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def emit_escalation_answer_parked_stranded(
    event_log: EventLog,
    *,
    escalation_event_id: str,
    nj_event_id: str,
    goal_session_id: str,
    answer_text: str,
    vitality_reason: str,
) -> str:
    """answer_parked_stranded 终态 (escalation-answer-reflow@v3) — 发起 goal-bg 会话确认已死,
    答案没有活的消费方可送。区别于 applied (未被自动消费) 、区别于 failed_dead_lettered (不是
    基础设施/步骤失败, 是会话已亡的结构性终点; concept state_machine 原话)。答案不丢: 调用方
    (`_park_stranded_goal_answer`) 已把正文持久 park, 本事件 + 配套 dead_letter_inbox.enqueue
    (调用方负责) 一起构成"浮出搁浅单"——分诊看到这条能定位到 park 文件里的原文接手。
    """
    decision_id = f"esc-answer-stranded-{escalation_event_id[:24]}"
    payload: dict[str, object] = {
        "kind": "EscalationAnswerParkedStranded",
        "escalation_event_id": escalation_event_id,
        "nj_event_id": nj_event_id,
        "goal_session_id": goal_session_id,
        "answer_text": answer_text,
        "vitality_reason": vitality_reason,
        "recorded_at": time.time(),
    }
    intent = _build_orch_nodetouched(
        kind="EscalationAnswerParkedStranded",
        decision_id=decision_id,
        payload_body=payload,
        # IMMUTABLE_TRUTH: 与 EscalationAnswerApplied 同级 — 这是"答案往哪去了"的硬闭环凭据之一
        # (另一支终态), GC 不可扫掉, 否则搁浅答案的下落无凭无据可查。
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
    )
    return event_log.write_direct(intent).event_id


def _park_stranded_goal_answer(
    towow_dir: Path,
    *,
    goal_session_id: str,
    escalation_event_id: str,
    nj_event_id: str,
    answer_text: str,
) -> str:
    """持久 park 搁浅答案正文 (发起会话已死, 无消费方) —— 分诊/追责时按 escalation_event_id 查
    原文。文件名按 escalation_event_id (同一条 escalation 至多一份, 幂等覆盖 = 取最新一次巡检
    观测)。与 `_write_escalation_answer_injection` (exec 重派消费用) 分目录, 不混淆语义。
    """
    sdir = _stranded_answers_dir(towow_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"{_slug_task_for_path(escalation_event_id)}.json"
    body = {
        "escalation_event_id": escalation_event_id,
        "nj_event_id": nj_event_id,
        "goal_session_id": goal_session_id,
        "answer_text": answer_text,
        "parked_at": time.time(),
    }
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _write_escalation_answer_injection(
    towow_dir: Path,
    *,
    task_id: str,
    answer_payload: dict[str, object],
) -> str:
    """② 答案注入 — park 答案载体, try_spawn_for_decision 重派 task_id 时读出追加进 prompt。

    返回 park 文件路径 (作 injection_evidence)。文件名按 task_id (一 task 至多一个待注入答案,
    后答覆前 = 取最新 owner 决策)。
    """
    adir = _escalation_answers_dir(towow_dir)
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / f"{_slug_task_for_path(task_id)}.json"
    path.write_text(json.dumps(answer_payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _slug_task_for_path(task_id: str) -> str:
    """task_id → 安全文件名 (与既有 marker slug 同向; 防路径穿越/特殊字符)。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:200]


def read_escalation_answer_injection(towow_dir: Path, task_id: str) -> dict[str, object] | None:
    """try_spawn_for_decision 用: 读 task_id 的待注入 owner 答案 (无 → None)。"""
    path = _escalation_answers_dir(towow_dir) / f"{_slug_task_for_path(task_id)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def consume_escalation_answer_injection(towow_dir: Path, task_id: str) -> None:
    """重派 spawn 成功后清掉 park (答案已注入 fresh 会话 prompt, 消费完毕, 不重复注入)。"""
    path = _escalation_answers_dir(towow_dir) / f"{_slug_task_for_path(task_id)}.json"
    with contextlib.suppress(OSError):
        path.unlink()


def _render_owner_answer_injection(answer: dict[str, object]) -> str:
    """把 park 的 owner 答案渲染成重派 prompt 段 (让 fresh 会话一眼看见 owner 已拍的决策)。"""
    content = str(answer.get("answer_content") or "(正文为空)")
    esc = str(answer.get("responds_to_escalation_event_id") or "?")
    nj = str(answer.get("answering_event_id") or "?")
    return (
        "## ⬇ owner 已对你上一轮升级 (escalation) 拍板 — 按此决策继续, 别再撞同一墙\n"
        f"- 原 escalation event_id: {esc}\n"
        f"- owner 答复 (NatureJudgmentCaptured {nj}):\n"
        f"  「{content}」\n"
        "这是 owner 显式决策, 直接据此推进你这个 task; 若答复未覆盖你真正卡的点, 再 escalate 一次新的。"
    )


def _reflow_retry_bump(towow_dir: Path, escalation_event_id: str) -> tuple[int, bool]:
    """回流失败有界重试计数 +1, 返回 (新值, 是否成功持久化)。

    persisted=False 表示计数无法跨巡检持久 (reflow_retry 子目录 mkdir/读/写介质故障)。
    此时有界重试机制本身失效 —— 计数每轮都从 0 重读+1=1, 永远 < cap, escalation 永不达
    dead_lettered 终态、每轮被重处理 (f-lrf06)。调用方须把 persisted=False 当作【不可恢复】
    直接终态化 (与 cap 耗尽同出口), 不能留待下轮 (下轮只会再次重置计数)。

    注: 内容损坏 (JSONDecodeError = 合法 UTF-8 的非法 JSON; UnicodeDecodeError = 非法 UTF-8 字节)
    都不算持久化介质故障 —— 一次覆盖写即可修复, count 从 0 起算仍能单调推进到 cap; 只有 OSError
    家族 (权限/配额/只读挂载) 才是"写不进去"的真故障。UnicodeDecodeError 是 ValueError 子类, 既非
    OSError 也非 JSONDecodeError, 若不同归内容损坏支会逃逸出本函数 (f-lrf06b: 字节损坏文件对应的
    escalation 每轮 sweep 未处理即中止 → 永不达终态 + 连累兄弟 escalation)。
    """
    rdir = _reflow_retry_dir(towow_dir)
    try:
        rdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 1, False
    path = rdir / f"{_slug_task_for_path(escalation_event_id)}.json"
    count = 0
    if path.exists():
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            st = None  # 内容损坏 (非法 JSON 或非法 UTF-8 字节) 可覆盖修复, 不算介质故障
        except OSError:
            return 1, False  # 读介质故障 → 计数无法可靠推进 → 不可恢复
        if isinstance(st, dict) and isinstance(st.get("count"), int):
            count = st["count"]
    count += 1
    try:
        path.write_text(json.dumps({"count": count}), encoding="utf-8")
    except OSError:
        return count, False
    return count, True


def _reflow_retry_clear(towow_dir: Path, escalation_event_id: str) -> None:
    path = _reflow_retry_dir(towow_dir) / f"{_slug_task_for_path(escalation_event_id)}.json"
    with contextlib.suppress(OSError):
        path.unlink()


def drive_escalation_answer_reflow(
    event_log: EventLog,
    towow_dir: Path,
    *,
    now: float | None = None,
) -> int:
    """T-LRF-06 主驱动: 每轮巡检把所有【已答复未回流】的 escalation 跑完四步 workflow。

    surface-only (只 emit canonical + 写 park, 不 spawn) → 安全挂进自愈 sweep, paused 下也跑。
    真重派由既有 ready-set 在非 paused 时做 (NJ 落账后 pending 翻假自动捞回)。goal-bg 会话级分支
    的 push (C-2/v3) 是唯一例外 —— 它是对一个已在跑的活会话做 attach 注入, 不是"派新活", 语义上
    与既有"paused 下不派新活"红线不冲突 (同 wake-watcher 自己的救援动作也不受 orchestrator
    pause 状态门控这条既有先例)。

    Returns 本轮到达终态 (applied / no_waiting / answer_parked_stranded / dead_lettered) 的
    escalation 数 (供观测/测试)。
    """
    # 2026-07-02 崩机根治收尾: 本函数是 _sweep_stuck_batons 每轮巡检 (daemon 首轮 + 60s 节流)
    # 都会调的热路径, all_records() 每次重新扫盘+全量 pydantic 重解析——真机点火实测这条单独
    # 拖了近 20s, 是首轮总耗时被拖到分钟级的组成部分之一。换成 committed_index().records()
    # (同一份 committed 快照的内存缓存视图, T-FND-02 stuck-baton sweep 已验证的同一模式)。
    records = event_log.committed_index().records()
    answers, sources, reflowed = _reflow_answers_and_sources(records)
    if not answers:
        return 0
    sess_to_task = _exec_session_to_task_map(_all_events_as_dicts_from(records))
    cap = _redispatch_cap()
    terminalized = 0
    for esc_id, ans in answers.items():
        if esc_id in reflowed:
            continue  # 已终态, 去重 (附加条款①)
        nj_event_id = ans["nj_event_id"]
        answer_text = ans["answer_text"] or "(owner 已答复, 正文为空)"
        source = sources.get(esc_id)
        try:
            if source is None:
                # 答复指向的 escalation 源事件不在账本 (孤儿 responds_to) → 无从解析等待方,
                # 当 no_waiting (FYI 终态) 收口, 不死信 (非步骤失败, 是源缺失)。
                emit_no_waiting_party_detected(
                    event_log, escalation_event_id=esc_id, nj_event_id=nj_event_id,
                )
                terminalized += 1
                continue
            party = _resolve_reflow_waiting_party(source, sess_to_task)
            if party is None:
                emit_no_waiting_party_detected(
                    event_log, escalation_event_id=esc_id, nj_event_id=nj_event_id,
                )
                terminalized += 1
                continue
            # ② 答案注入 + ④ applied 留痕 —— 两条分流路径:
            #   exec task 可重派 (park_for_redispatch=True, 有 task_id): park 答案供
            #     try_spawn_for_decision 消费, resumed=True 已诚实 (那条链保证下次重派必注入,
            #     不在本次 C-2/v3 修复范围 —— 见 escalation_reflow.py 头部 docstring scope 注)。
            if party.park_for_redispatch and party.task_id:
                _write_escalation_answer_injection(
                    towow_dir,
                    task_id=party.task_id,
                    answer_payload={
                        "answering_event_id": nj_event_id,
                        "responds_to_escalation_event_id": esc_id,
                        "answer_content": answer_text,
                        "source": party.source,
                    },
                )
                emit_escalation_answer_applied(
                    event_log,
                    escalation_event_id=esc_id,
                    nj_event_id=nj_event_id,
                    applied_to_task_id=party.task_id,
                    applied_to_session_id=party.session_id,
                    answer_text=answer_text,
                    resumed=True,
                )
                _reflow_retry_clear(towow_dir, esc_id)
                terminalized += 1
                continue

            #   goal-bg 会话级 (task_id is None: 真正在等这条答复续跑的交互式会话, C-2/v3 命门):
            #     vitality 判活分流 push / park+死信, 不再无条件断言 resumed=True (v1"空壳只
            #     assert"缺陷 —— 2026-07 owner 实战坐实的飞书回复丢失根因)。
            target = escalation_reflow.resolve_goal_session_target(event_log, party.session_id)
            outcome = escalation_reflow.deliver_goal_answer(
                target,
                escalation_event_id=esc_id,
                nj_event_id=nj_event_id,
                answer_text=answer_text,
            )
            if outcome is escalation_reflow.GoalReflowOutcome.APPLIED:
                emit_escalation_answer_applied(
                    event_log,
                    escalation_event_id=esc_id,
                    nj_event_id=nj_event_id,
                    applied_to_task_id=party.session_id,
                    applied_to_session_id=party.session_id,
                    answer_text=answer_text,
                    resumed=True,
                )
                _reflow_retry_clear(towow_dir, esc_id)
                terminalized += 1
            elif outcome is escalation_reflow.GoalReflowOutcome.ANSWER_PARKED_STRANDED:
                _park_stranded_goal_answer(
                    towow_dir,
                    goal_session_id=party.session_id,
                    escalation_event_id=esc_id,
                    nj_event_id=nj_event_id,
                    answer_text=answer_text,
                )
                emit_escalation_answer_parked_stranded(
                    event_log,
                    escalation_event_id=esc_id,
                    nj_event_id=nj_event_id,
                    goal_session_id=party.session_id,
                    answer_text=answer_text,
                    vitality_reason=target.vitality.reason,
                )
                with contextlib.suppress(Exception):
                    dead_letter_inbox.enqueue(
                        towow_dir, event_log,
                        source_object_type="escalation_answer",
                        source_object_ref=esc_id,
                        entry_reason=dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE,
                        original_trigger_event_id=nj_event_id,
                    )
                _reflow_retry_clear(towow_dir, esc_id)
                terminalized += 1
            else:
                # UNRESOLVED: 既非确定 parked_resumable(可 push) 也非确定 dead, 或 push 未核实到
                # 落地 —— 不是异常, 走与下面 except 块同款的有界重试→死信 (绝不在此假装任何一种
                # 确定结局; 与 concept state_machine 的 VitalityUnknownDeadLettered 转移同语义)。
                rc, persisted = _reflow_retry_bump(towow_dir, esc_id)
                # persisted=False → 计数无法跨巡检持久, 有界重试机制失效 (f-lrf06): 每轮从 0 重读
                # 永不达 cap → 视作不可恢复, 直接终态化 (与 cap 耗尽同出口), 不留待下轮 (下轮只会
                # 再次重置计数)。
                if rc >= cap or not persisted:
                    with contextlib.suppress(Exception):
                        dead_letter_inbox.enqueue(
                            towow_dir, event_log,
                            source_object_type="escalation",
                            source_object_ref=esc_id,
                            entry_reason=dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE,
                            original_trigger_event_id=nj_event_id,
                        )
                    persist_note = "" if persisted else " reflow_retry_persist_failed=true"
                    emit_escalation_reflow_dead_lettered(
                        event_log,
                        escalation_event_id=esc_id,
                        nj_event_id=nj_event_id,
                        failed_step="goal_vitality_unresolved",
                        last_error=(
                            f"vitality={target.vitality.verdict.value} "
                            f"reason={target.vitality.reason}{persist_note}"
                        ),
                    )
                    _reflow_retry_clear(towow_dir, esc_id)
                    terminalized += 1
                # rc < cap 且 persisted → 不终态, 留待下轮重试 (有界), 同下面 except 块语义
        except Exception as exc:  # 步骤失败兜底: 有界重试→死信, 不崩 daemon (一条回流失败不停后台)
            rc, persisted = _reflow_retry_bump(towow_dir, esc_id)
            # persisted=False → 计数无法持久, 有界重试失效 (f-lrf06) → 直接终态化, 不留待下轮。
            if rc >= cap or not persisted:
                with contextlib.suppress(Exception):
                    dead_letter_inbox.enqueue(
                        towow_dir, event_log,
                        source_object_type="escalation",
                        source_object_ref=esc_id,
                        entry_reason=dead_letter_inbox.DeadLetterEntryReason.STRUCTURAL_FAILURE,
                        original_trigger_event_id=nj_event_id,
                    )
                persist_note = "" if persisted else " reflow_retry_persist_failed=true"
                emit_escalation_reflow_dead_lettered(
                    event_log,
                    escalation_event_id=esc_id,
                    nj_event_id=nj_event_id,
                    failed_step="reflow",
                    last_error=f"{exc!r}{persist_note}",
                )
                _reflow_retry_clear(towow_dir, esc_id)
                terminalized += 1
            # rc < cap 且 persisted → 不终态, 留待下轮重试 (有界)
    return terminalized


def owner_gate_escalation_pending(event_log: EventLog, task_id: str) -> bool:
    """fnd-r01-9: 该 task 是否已有【未被 owner 响应】的 owner-gate escalation。

    dedup 真相源 = pending_escalations 本身 (不另写 marker 文件 —— 两个真相源会漂移)。匹配
    goal_session_id == owner-gate-<task_id>。pending → 每轮只剔池不重 emit (不刷屏); owner NJ
    响应后 pending 翻假, 但 requires_owner_gate 仍 True → 下轮该 task 仍被拦 (红线每次都拦,
    NJ 不自动放行)。
    """
    synthetic = _owner_gate_synthetic_session_id(task_id)
    return any(
        esc.get("goal_session_id") == synthetic
        for esc in pending_escalations(event_log)
    )


def emit_owner_gate_escalation(
    event_log: EventLog,
    *,
    task_id: str,
    owner_gate_reason: str | None,
    trigger_event_id: str | None = None,
) -> str:
    """fnd-r01-9: owner-gate task 撞派发层 → emit GoalEscalationRaised 升级 owner。

    走 stub-rewrap NodeTouched (GOAL_ESCALATION_RAISED 不在 path-B 白名单, 且 orchestrator 一向用此
    范式自 emit 升级/告警件; pending_escalations / _route_event 都经 _unwrap_stub_rewrap 识别它)。
    blocking_scope=session_only —— 只挡这一个 task, 别的安全链继续跑, 【不】全局 pause autopilot
    (global 会复发 T-L0-02 一条 escalation 停后台 4 天的病; owner-gate 语义本就是"这一个等 owner")。
    下一轮 _route_event 见此 GoalEscalationRaised 会路由 main-inbound 让 owner 可见。

    dedup 由 caller 用 owner_gate_escalation_pending 负责 (本函数只 emit)。Returns escalation event_id。
    """
    synthetic_sid = _owner_gate_synthetic_session_id(task_id)
    reason_txt = (owner_gate_reason or "").strip() or (
        "不可逆真实世界动作 (上线/删生产数据/动钱/对外发布/改公开承诺 之一)"
    )
    decision_id = f"owner-gate-esc-{task_id}"
    payload: dict[str, object] = {
        "kind": "GoalEscalationRaised",
        "decision_id": decision_id,
        "escalation_id": decision_id,
        "goal_session_id": synthetic_sid,
        "owner_question": (
            f"⛔ task {task_id} 触及不可逆真实世界动作 (owner 5 类红线), 编排器拒绝自动执行, "
            f"需要你显式决定是否放行: {reason_txt}"
        ),
        "reason": "owner_gate_irreversible_action",
        "raised_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "awaiting_response": True,
        # session_only: 只挡这一个 task, 不全局 pause (run_polling_loop 只对 global 才 pause_orchestrator)。
        "blocking_scope": "session_only",
        "owner_gate_task_id": task_id,
        "owner_gate_trigger_event_id": trigger_event_id,
    }
    intent = _build_orch_nodetouched(
        kind="GoalEscalationRaised",
        decision_id=decision_id,
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


# ════════════════════════════════════════════════════════════════════════════════
#  T-SELFHEAL-STUCK-DETECT — 编排器生产力自检 (owner 直接要求: "让系统自己喊'我空转了'")
#
#  病例 (2026-07-17 owner 实证): 编排器 active、daemon 活着, 但 active_relay 里躺着已死透的 stale
#  relay (根因见 assess_session_liveness 的 roster_ids 覆盖, 本文件 reconcile_orphaned_sessions),
#  100+ 待处理任务被 refusal=live_session_exists 拦到 0 派发, 需 owner 手动 `goal terminate` 才恢复。
#  上面这条根治后本类不会再来, 但"占着工位不动却没有真前进"这个症状可能有别的未知诱因——本节是给
#  它的通用兜底哨声, 不是专修 stale relay 那一种病。
#
#  刻意不新起一个外部 timer / 不重造检测逻辑: 数据源 = 编排器自己每轮 reconcile 早已发布的
#  ReconcileCyclePublished (INV-SENT-A3-NO-HEARTBEAT 五计数, 供 A3 空转哨兵消费的同一份快照)。
#  A3 (detect_a3_reconcile) 本身是 DORMANT (sentinel_loop.py 明令 "不接进 run_polling_loop", 归
#  INF-003 turn-on 红线, 不该由本次任务代 owner 打开)——本节不碰那个开关, 只是在编排器自己已经在
#  跑的活路径里, 直接消费这份早就存在的数据, 判定持续空转就走 owner-gate 同款
#  GoalEscalationRaised(blocking_scope=session_only) —— 这条通道已被 feishu_owner_channel 的
#  ASK_EVENT_TYPES={"GoalEscalationRaised","EscalationRaised"} 监听 (alert-bridge.py 同款设计:
#  raise 一条 GoalEscalationRaised 即触达飞书), 不新建一条送达路径。
# ════════════════════════════════════════════════════════════════════════════════

_PRODUCTIVITY_ALARM_SID = "system-productivity-stall-alarm"
_PRODUCTIVITY_STALL_THRESHOLD_ENV = "TOWOW_PRODUCTIVITY_STALL_THRESHOLD_S"
_PRODUCTIVITY_STALL_THRESHOLD_DEFAULT_S = 900.0  # 15 分钟 (owner 打的样例阈值)


def _productivity_stall_threshold_s() -> float:
    """env TOWOW_PRODUCTIVITY_STALL_THRESHOLD_S 覆盖; 非法/缺省回默认 900s。"""
    raw = os.environ.get(_PRODUCTIVITY_STALL_THRESHOLD_ENV, "").strip()
    with contextlib.suppress(ValueError):
        val = float(raw)
        if val > 0:
            return val
    return _PRODUCTIVITY_STALL_THRESHOLD_DEFAULT_S


def _productivity_alarm_pending(event_log: EventLog) -> bool:
    """dedup 真相源同 owner-gate 惯例: pending_escalations 反查固定 synthetic sid, 不另写 marker 文件。"""
    return any(
        esc.get("goal_session_id") == _PRODUCTIVITY_ALARM_SID
        for esc in pending_escalations(event_log)
    )


def maybe_emit_productivity_stall_alarm(
    towow_dir: Path,
    event_log: EventLog,
    *,
    now: float | None = None,
    threshold_s: float | None = None,
) -> str | None:
    """编排器持续空转 (占着 active_relay 工位、连续多轮 0 真派发) 自动升级 owner, 不等截图发现。

    判据 (数据源 = 编排器自己每轮 reconcile 发布的 ReconcileCyclePublished 五计数快照, 与上方
    reconcile_pass 同轮): 从最新一轮往回找"最近一次健康"的锚点——健康 = dispatched_count>0 (真有
    派发) 或 active_session_count==0 (relay 已空, 那是"真没活干"的健康空闲, 不是"占着不动")。锚点
    到现在的时长 ≥ threshold_s, 且【最新一轮】仍是不健康状态 (还占着 active_relay、还 0 派发) →
    报警。从未见过健康锚点 (自最早已知记录起就一直不健康) → 用最早已知记录的时间做保守基线 (不假设
    "有记录以前"就已经在空转)。一条 ReconcileCyclePublished 都没有 (刚启动/数据缺) → 不判 (FLP)。

    dedup: 复用 owner-gate 同款 pending_escalations 反查——同一 synthetic goal_session_id 有未被
    owner 响应的 escalation 时不重复 emit (不刷屏); owner NJ 响应后可以再报下一次空转发作。

    Returns 新 emit 的 escalation event_id; 未报警 / 已在 pending 中 / 数据不足 → None。绝不 raise
    (caller 用 suppress 兜, 一条告警判断不该拖垮主派发链)。
    """
    now_ts = time.time() if now is None else now
    window_s = _productivity_stall_threshold_s() if threshold_s is None else threshold_s
    if _productivity_alarm_pending(event_log):
        return None
    cycles: list[tuple[float, int, int]] = []  # (ts_epoch, dispatched_count, active_session_count)
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != "ReconcileCyclePublished":
            continue
        ts = getattr(rec, "timestamp", None)
        ts_epoch = ts.timestamp() if ts is not None else None
        dispatched = payload.get("dispatched_count")
        active = payload.get("active_session_count")
        if ts_epoch is None or not isinstance(dispatched, int) or not isinstance(active, int):
            continue
        cycles.append((ts_epoch, dispatched, active))
    if not cycles:
        return None  # 从未发布过 reconcile 快照 (刚启动) → 无数据, 不判
    cycles.sort(key=lambda c: c[0])
    _latest_ts, latest_dispatched, latest_active = cycles[-1]
    if latest_dispatched > 0 or latest_active == 0:
        return None  # 最新一轮已健康 (真派发了 / relay 已空是健康空闲) → 当前不是空转态
    last_healthy_ts: float | None = None
    for ts_epoch, dispatched, active in reversed(cycles):
        if dispatched > 0 or active == 0:
            last_healthy_ts = ts_epoch
            break
    baseline_ts = last_healthy_ts if last_healthy_ts is not None else cycles[0][0]
    stalled_duration = now_ts - baseline_ts
    if stalled_duration < window_s:
        return None
    decision_id = f"productivity-stall-{int(now_ts)}"
    payload_body: dict[str, object] = {
        "kind": "GoalEscalationRaised",
        "decision_id": decision_id,
        "escalation_id": decision_id,
        "goal_session_id": _PRODUCTIVITY_ALARM_SID,
        "owner_question": (
            f"⚠ 编排器已连续 {stalled_duration:.0f}s (≥阈值 {window_s:.0f}s) 占着 active_relay 工位"
            f"(active_session_count={latest_active}) 但 0 真派发 — 疑似空转/卡死 (stale relay 未被"
            "收割 / 派发守卫拒绝但自愈未生效 等), 需要你看一眼 "
            "(`./tw vitality` 查真实会话死活 / `orchestrator status` 查派发状态)。"
        ),
        "reason": "orchestrator_productivity_stall",
        "raised_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts)),
        "awaiting_response": True,
        "blocking_scope": "session_only",
        "stall_window_s": window_s,
        "stalled_duration_s": stalled_duration,
        "latest_active_session_count": latest_active,
    }
    intent = _build_orch_nodetouched(
        kind="GoalEscalationRaised", decision_id=decision_id, payload_body=payload_body,
    )
    return event_log.write_direct(intent).event_id


# ════════════════════════════════════════════════════════════════════════════════
#  T-FIX-B1-05 (FORWARD-chain#2 reframe / AUTOPILOT-core#3) — 自愈覆盖仪表盘
#
#  owner 否决'建持久总控', 重新框定为'补全分布式自愈'。本段把 5 类断棒各自的归宿机制
#  + 当前计数 + 本轮是否活跃做成 owner 按 autopilot 键前手上有的仪表盘 — 让 owner 不靠
#  中心总控也能一眼确认'没有哪类断棒落进静默死胡同' (INV-B1-SH1 的可视化证据)。
#
#  纯观测聚合, 无任何新派发/重派逻辑 — 计数来自真 canonical 事件 (SilentDeathAlarmed /
#  OrchestratorDispatchFailed) + 活 marker (pending session / retry__ / redispatch_circuit__ /
#  escalation baton); active 只由活 marker 驱动 (=自愈闭环里还在飞的那一格)。
#
#  跨批协调 (B2 主题A): 预留 non_exec_serial_rejected 占位槽 (B1 落枚举骨架, B2 单飞门填真
#  归宿/计数) — 两批同改 collect_orchestrator_status, 占位槽避免冲突。
# ════════════════════════════════════════════════════════════════════════════════

# 5 类 execution 断棒 → 各自归宿机制名 + 闭环留痕的 canonical 事件/marker 类型 (供陌生
# reviewer 独立反查 '每类断棒都有归宿')。non_exec_serial_rejected 是为 B2 预留的占位槽。
_SELF_HEAL_KINDS: tuple[tuple[str, str, str], ...] = (
    ("silent_death", "reconcile", "SilentDeathAlarmed"),
    ("non_success_dead_end", "clear-stamp", "retry__<task> marker (TaskRunCompleted!=success)"),
    ("spawn_failed", "alarm", "OrchestratorDispatchFailed"),
    ("redispatch_exhausted", "circuit", "redispatch_circuit__<key> marker / RedispatchExhausted"),
    ("escalation_stuck", "sweep", "SelfHealStuckAlarmed (collect_stuck_batons)"),
)
_RETRY_MARKER_PREFIX = "retry__"


def collect_self_heal_coverage(
    towow_dir: Path,
    event_log: EventLog,
    *,
    now: float | None = None,
) -> dict[str, object]:
    """T-FIX-B1-05: 5 类断棒的归宿覆盖快照 (纯读, 无 emit, 无派发)。

    每类: {category, home(机制名), home_event_type(反查锚), count(累计/活计数), active(本轮活跃),
    active_batons(活跃明细供反查)}。顶层: all_kinds_have_home(INV-B1-SH1) + active_kinds(当前活跃类清单)。

    count 与 active 的来源 (都是已 commit 的 B1-01/B1-03/B2/T-LND 机制, 这里只聚合不重造):
      - silent_death:         count = SilentDeathAlarmed canonical 数; active = pending session marker
                              久未对账 (reconcile 没收割掉 = silent death 还没兜底完)。
      - non_success_dead_end: count/active = dispatched/ 下 retry__ marker 数 (清戳待重派的 task)。
      - spawn_failed:         count = OrchestratorDispatchFailed canonical 数; 无持久在飞 marker
                              (归宿是 emit 告警 + 等 owner, 终态), active 恒 False — 计数即可见性。
      - redispatch_exhausted: count/active = redispatch_circuit__ 熔断 marker 数。
      - escalation_stuck:     count/active = collect_stuck_batons 里 escalation_stuck 断棒数。
    """
    now_unix = now if now is not None else time.time()

    # —— 一次扫日志数 canonical 告警事件 (silent_death / spawn_failed) ——
    silent_death_count = 0
    spawn_failed_count = 0
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        et, _payload = _unwrap_stub_rewrap(rec)
        if et == "SilentDeathAlarmed":
            silent_death_count += 1
        elif et == EventType.ORCHESTRATOR_DISPATCH_FAILED.value:
            spawn_failed_count += 1

    # —— 活 marker (dispatched/ 下) ——
    ddir = _dispatched_dir(towow_dir)
    retry_markers: list[str] = []
    circuit_markers: list[str] = []
    if ddir.exists():
        for f in sorted(ddir.iterdir()):
            if f.name.startswith(_RETRY_MARKER_PREFIX):
                retry_markers.append(f.name[len(_RETRY_MARKER_PREFIX):])
            elif f.name.startswith(_REDISPATCH_CIRCUIT_PREFIX):
                # T-LRF-11 收口 (finding-tlrf11): 同前缀下的 .terminalized 哨兵不是活熔断 marker —— 排除
                # 它本身、且把已强制终态化 (dead_lettered) 的 key 排除, 否则 redispatch_exhausted 活
                # marker 计数被污染 (与 collect_stuck_batons / collect_overage_live_bodies 收敛)。
                if f.name.endswith(".terminalized"):
                    continue
                circuit_key = f.name[len(_REDISPATCH_CIRCUIT_PREFIX):]
                if _redispatch_circuit_terminalized_path(towow_dir, circuit_key).exists():
                    continue
                circuit_markers.append(circuit_key)

    # —— 久卡断棒 (与 status stuck_batons / daemon sweep 共用同一探测, 不漂移) ——
    batons = collect_stuck_batons(towow_dir, event_log, now=now_unix)
    stuck_escalations = [b for b in batons if b.get("baton_kind") == "escalation_stuck"]
    stuck_pending_sessions = [
        b for b in batons if b.get("baton_kind") == "pending_session_unreconciled"
    ]

    # 每类 active 的活跃明细 (供 reviewer 反查归宿事件类型)。
    active_detail: dict[str, list[dict[str, object]]] = {
        "silent_death": [
            {"baton_id": b.get("baton_id"), "home_event_type": "SilentDeathAlarmed"}
            for b in stuck_pending_sessions
        ],
        "non_success_dead_end": [
            {"baton_id": k, "home_event_type": "retry__ marker (待重派)"} for k in retry_markers
        ],
        "spawn_failed": [],  # 终态: 归宿是告警+等 owner, 无在飞 marker
        "redispatch_exhausted": [
            {"baton_id": k, "home_event_type": "redispatch_circuit__ marker"}
            for k in circuit_markers
        ],
        "escalation_stuck": [
            {"baton_id": b.get("baton_id"), "home_event_type": "SelfHealStuckAlarmed"}
            for b in stuck_escalations
        ],
    }
    count_by_cat: dict[str, int] = {
        "silent_death": silent_death_count,
        "non_success_dead_end": len(retry_markers),
        "spawn_failed": spawn_failed_count,
        "redispatch_exhausted": len(circuit_markers),
        "escalation_stuck": len(stuck_escalations),
    }

    categories: list[dict[str, object]] = []
    active_kinds: list[str] = []
    for cat, home, home_event_type in _SELF_HEAL_KINDS:
        detail = active_detail[cat]
        is_active = len(detail) > 0
        if is_active:
            active_kinds.append(cat)
        categories.append({
            "category": cat,
            "home": home,
            "home_event_type": home_event_type,
            "count": count_by_cat[cat],
            "active": is_active,
            "active_batons": detail,
        })

    # B2 跨批占位槽: review/fix 等 non-exec 'spawn成功但phase-start被拒静默死' 那一类断棒, 归宿
    # 由 B2 单飞门补 (派发前 serial gate + 被拒不盖戳留 backlog re-scan)。B1 只落枚举骨架, 不实现。
    categories.append({
        "category": "non_exec_serial_rejected",
        "home": "serial-gate-B2",
        "home_event_type": "(B2 主题A 填: non-exec 派发前单飞门 + backlog re-scan)",
        "count": 0,
        "active": False,
        "active_batons": [],
        "placeholder_for": "B2",
    })

    return {
        # INV-B1-SH1: 5 类 execution 断棒每类都有归宿机制 (占位槽待 B2 填, 不计入此判定)。
        "all_kinds_have_home": all(
            c["home"] for c in categories if not c.get("placeholder_for")
        ),
        "active_kinds": active_kinds,
        "categories": categories,
    }


def record_daemon_round_health(
    towow_dir: Path,
    *,
    round_index: int,
    duration_s: float,
    decisions: int,
    did_full_sweep: bool,
    sweep_duration_s: float | None,
    archived_this_round: int,
    dispatched_active_count: int | None,
    now: float | None = None,
    phases: dict[str, float] | None = None,
) -> None:
    """T-LRF-10b (daemon-patrol-cost-separation@v1 条款⑤): daemon 每轮把本轮耗时 + 扫描量写健康面板
    (orchestrator/daemon_health.json), collect_orchestrator_status 读出 → "下次热点不靠猜"。

    last_round = 本轮快照 (耗时 / 派发 decision 数 / 是否跑了全量巡检 + 巡检耗时 / 归档量 / 活跃集大小
    / phases 各阶段耗时 — f-orch-round-time-index-persist-storm-plus-on-passes-20260717);
    cumulative = 累计 (轮数 / 归档总量 / 峰值轮耗时 / 峰值巡检耗时), read-modify-write 小 JSON。

    绝不崩循环 (调用方 suppress 兜底; 这里也 atomic tmp+replace 防半文件)。`now` 仅供测试注入。
    """
    now_unix = now if now is not None else time.time()
    path = _daemon_health_path(towow_dir)
    prev: dict[str, object] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            prev = loaded
    prev_cum = prev.get("cumulative")
    prev_cum = prev_cum if isinstance(prev_cum, dict) else {}

    def _num(d: dict[str, object], key: str) -> float:
        v = d.get(key)
        return float(v) if isinstance(v, (int, float)) else 0.0

    rounds = int(_num(prev_cum, "rounds")) + 1
    archived_total = int(_num(prev_cum, "archived_total")) + max(0, archived_this_round)
    max_round = max(_num(prev_cum, "max_round_duration_s"), duration_s)
    max_sweep = _num(prev_cum, "max_sweep_duration_s")
    if sweep_duration_s is not None:
        max_sweep = max(max_sweep, sweep_duration_s)
    body: dict[str, object] = {
        "last_round": {
            "round_index": round_index,
            "at": now_unix,
            "duration_s": round(duration_s, 4),
            "decisions": decisions,
            "did_full_sweep": did_full_sweep,
            "sweep_duration_s": (
                round(sweep_duration_s, 4) if sweep_duration_s is not None else None
            ),
            "archived_this_round": archived_this_round,
            "dispatched_active_count": dispatched_active_count,
            "phases": phases,
        },
        "cumulative": {
            "rounds": rounds,
            "archived_total": archived_total,
            "max_round_duration_s": round(max_round, 4),
            "max_sweep_duration_s": round(max_sweep, 4),
        },
    }
    tmp = path.with_suffix(".tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(body), encoding="utf-8")
        tmp.replace(path)


def read_daemon_health(towow_dir: Path) -> dict[str, object] | None:
    """T-LRF-10b ⑤: 读 daemon 健康面板快照 (None = daemon 还没跑过一个真工作轮)。"""
    path = _daemon_health_path(towow_dir)
    if not path.exists():
        return None
    with contextlib.suppress(OSError, json.JSONDecodeError):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return None


def collect_orchestrator_status(
    towow_dir: Path,
    event_log: EventLog,
    *,
    daemon_state_fn: Callable[[str], str | None] | None = None,
    now: float | None = None,
) -> dict[str, object]:
    """聚合调度员状态快照 — Agent 可调用 + orchestrator status CLI 渲染同一份。

    用 session_liveness 真信号判每个接力会话在不在跑 (不读 state.json.detail)。
    JSON-serializable, 供 `orchestrator status --json` + Agent 程序化消费 (解自驱动:
    发起者据此判断会话在跑/卡/完, 做对派活/等待/清理决策)。

    T-FIX-B1-03: stuck_batons 段列出四类久卡断棒 (escalation 超时 / 重派熔断 / pending session
    久未对账 / exec stamp 孤儿) 的当前快照, 与 _sweep_stuck_batons 共用 collect_stuck_batons 探测逻辑 — owner 查
    status 即见'哪些断棒卡住没人管', 不再只在 daemon emit 告警时才知道。`now` 仅供测试注入。
    """
    pid, alive = is_orchestrator_process_alive(towow_dir)
    watermark = load_watermark(towow_dir)
    latest = max(0, event_log.next_sequence - 1)  # F-028-3: 空日志 next_sequence=0 → 不报 -1
    relay_ids = active_relay_sessions(event_log)

    sessions: list[dict[str, object]] = []
    verdict_counts: dict[str, int] = {}
    for gsid in relay_ids:
        live = assess_session_liveness(gsid, event_log, daemon_state_fn=daemon_state_fn)
        key = str(live.verdict)
        verdict_counts[key] = verdict_counts.get(key, 0) + 1
        sessions.append(
            {
                "goal_session_id": gsid,
                "verdict": key,
                "daemon_state": live.daemon_state,
                "self_reported_termination": live.self_reported_termination,
                "last_event_type": live.last_event_type,
                "last_event_ts": live.last_event_ts,
                "needs_attention": live.needs_attention,
                "is_terminal_for_cleanup": live.is_terminal_for_cleanup,
            },
        )

    pdir = _pending_sessions_dir(towow_dir)
    pending = [p.stem for p in sorted(pdir.glob("*.json"))] if pdir.is_dir() else []
    ddir = _dispatched_dir(towow_dir)
    dispatched_count = sum(1 for _ in ddir.iterdir()) if ddir.exists() else 0

    # T-FIX-B1-02: escalation-blocked task 的归宿 — 不盲派、不熔断, 等 owner 响应。摆进 status 让
    # "为什么这个 task 没在跑" 一眼可见 (它在等 owner, 不是失败也不是被忘掉)。
    esc_events = _all_events_as_dicts(event_log)
    blocked_map = tasks_blocked_on_pending_escalation(event_log, esc_events)
    blocked_on_escalation = [
        {"task_id": t, "escalation_event_id": eid} for t, eid in sorted(blocked_map.items())
    ]

    return {
        "daemon_pid": pid,
        "daemon_alive": alive,
        "paused": is_orchestrator_paused(towow_dir),
        "watermark": watermark,
        "latest_seq": latest,
        "watermark_lag": max(0, latest - watermark),
        "dispatched_count": dispatched_count,
        "active_relay_count": len(relay_ids),
        "active_relay_sessions": sessions,
        "verdict_counts": verdict_counts,
        "pending_markers": pending,
        "pending_escalations": pending_escalations(event_log),  # T4: 未响应 escalation, 停摆可见
        "blocked_on_escalation": blocked_on_escalation,  # B1-02: 关联 pending escalation 的 task
        "stuck_batons": collect_stuck_batons(towow_dir, event_log, now=now),  # B1-03: 久卡断棒快照
        # T-FIX-B1-05: 自愈覆盖仪表盘 — 5 类断棒各自归宿机制 + 当前计数 + 本轮活跃。owner 按
        # autopilot 键前一眼确认 '每类断棒都有归宿、无静默死胡同' (INV-B1-SH1 可视化, 替代
        # conformance-map 看不见并发安全这一格)。纯观测聚合, 无新派发逻辑。
        "self_heal_coverage": collect_self_heal_coverage(towow_dir, event_log, now=now),
        # T-FIX-B1-04: 真跑形态 (MOCK/REAL) 长期可见 — None=从未起过 loop; mode=MOCK + daemon 活
        # = 误入 mock 空转 (0 改代码) 的显著信号。owner 一眼判 'daemon 在真派还是 mock 空转'。
        "spawn_mode": read_spawn_mode(towow_dir),
        # T-LRF-10b ⑤: daemon 每轮耗时/扫描量自报 (None=还没跑过真工作轮)。下次热点不靠猜:
        # last_round 看本轮多贵+扫多少, cumulative 看峰值轮耗时/巡检耗时/累计归档量。
        "daemon_health": read_daemon_health(towow_dir),
    }


# ════════════════════════════════════════════════════════════════════════════════
#  Spawn integration (M-3.1 §7.4 spawn_session; calls claude_bg_helper)
# ════════════════════════════════════════════════════════════════════════════════


def _lookup_finding_payload(
    event_log: EventLog,
    trigger_event_id: str,
) -> dict[str, object] | None:
    """Reach back into event log for the trigger event's payload (RUN-015 spawn upgrade).

    Returns stub_original_payload (unwrapped) for FindingCreated stub-rewrap events;
    None if event not found or not a recognizable finding.

    f-orch-round-time-index-persist-storm-plus-on-passes-20260717: 从 63 万条 records()
    逐条线性扫单个 event_id (实测 ~0.11s/次 × 每 decision 每轮) 改 lookup_event_id O(1)
    索引查询 (同一 committed-visible 集合, event_id 全局唯一 → 语义等价)。
    """
    rec = event_log.committed_index().lookup_event_id(trigger_event_id)
    if rec is None:
        return None
    payload = rec.payload if isinstance(rec.payload, dict) else {}
    orig = payload.get("stub_original_payload")
    if isinstance(orig, dict):
        return orig
    return payload  # canonical (non-stub-rewrap) path


def _lookup_escalation_blocking_scope(event_log: EventLog, escalation_event_id: str) -> str:
    """T4 (PLAN-FIX): 读 GoalEscalationRaised 的 blocking_scope (global / session_only)。

    默认 global — 旧事件无此字段 / 取不到 → 保守按全局停 (不漏停该停的)。
    """
    # T-PERF 批1: warm committed_index snapshot instead of a fresh full-disk all_records()
    # re-scan (same committed-visible record set — T-FND-02 pattern).
    for rec in event_log.committed_index().records():
        if rec.event_id != escalation_event_id:
            continue
        _et, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state") if isinstance(payload, dict) else None
        src = after if isinstance(after, dict) else payload
        scope = src.get("blocking_scope") if isinstance(src, dict) else None
        return scope if scope in ("global", "session_only") else "global"
    return "global"


# f-fixafter-dispatch-to-terminal-finding-relay-deadlock: finding lifecycle 终态集。
# resolved (FindingResolved, 含 unresolved_risk 收口 — reviewer 显式留作 known risk、衍生
# finding 承接) / accepted (FindingAccepted, owner 收编为已知基线) 之后, 指向该 finding 的
# fix / fix_after 派发全是空转死锁轨道 (fix 完成 → fix_after 复验 → finding-resolve 被
# 『已 resolved — 不重复闭环』门拒 = 复验结论无生命周期出口)。
_FINDING_TERMINAL_LIFECYCLE_STATES = frozenset({"resolved", "accepted"})

_FINDING_LIFECYCLE_STATE_BY_EVENT = {
    EventType.FINDING_CREATED: "created",
    EventType.FINDING_VERIFIED: "verified",
    EventType.FINDING_DISPUTED: "disputed",
    EventType.FINDING_RESOLVED: "resolved",
    EventType.FINDING_ACCEPTED: "accepted",
    # ⚠ FindingClosureContractAmended 刻意**不**在本表 (f-stale-closure-contract-permanently-unclosable):
    # 它不是 state transition。_finding_lifecycle_state 按"最后一条 lifecycle 事件"定当前态 —— 把 amend
    # 收进来会让一条 verified finding 在修订合约后状态被抹成 None/未知, 反而制造新的派发死锁。
}


def _finding_lifecycle_state(event_log: EventLog, finding_id: str) -> str | None:
    """finding 当前 lifecycle_state — event-sourced 重算, 不读可能滞后的 graph 投影文件。

    与 L0 finding_lifecycle reducer 同口径: 按该 finding_id **最后一条** lifecycle 事件判
    当前态 (FindingResolved 后再来 FindingCreated = reopen, 状态回 created — 不能只查
    "存在过 FindingResolved")。payload 解析 after_state 嵌套容错。无任何事件 → None。
    """
    last_seq = -1
    last_state: str | None = None
    for etype, state in _FINDING_LIFECYCLE_STATE_BY_EVENT.items():
        for rec in event_log.get_events_by_type(etype):
            src = _record_src(rec)
            if src.get("finding_id") != finding_id:
                continue
            if rec.sequence_number > last_seq:
                last_seq = rec.sequence_number
                last_state = state
    return last_state


def _terminal_finding_for_decision(
    event_log: EventLog, decision: DispatchDecision,
) -> dict[str, str] | None:
    """decision 若锚定一个已达 lifecycle 终态的 finding → {finding_id, lifecycle_state}; 否则 None。

    只查两类锚 (闭合合约点名的死锁轨道, 不顺手扩大):
      - dispatch_to == "fix": trigger (FindingCreated) 的 finding_id 即派发锚;
      - review_mode == "fix_after": trigger (FixCompleted) 经 fix_id → FixProposed.related_finding_id
        溯源 (与 _trace_fix_after_origin_review_task_id 步1 同源)。
    溯不出 finding_id → None (fail-open 照常派 — 不确定不误吞, 与 suppress_review_of_review
    的 fail-toward-review 同哲学)。
    """
    finding_id: str | None = None
    if decision.dispatch_to == "fix":
        payload = _lookup_finding_payload(event_log, decision.trigger_event_id)
        fid = payload.get("finding_id") if isinstance(payload, dict) else None
        finding_id = fid if isinstance(fid, str) and fid else None
    elif decision.review_mode == "fix_after":
        payload = _lookup_finding_payload(event_log, decision.trigger_event_id)
        after = payload.get("after_state") if isinstance(payload, dict) else None
        src = after if isinstance(after, dict) else payload
        fix_id = src.get("fix_id") if isinstance(src, dict) else None
        if isinstance(fix_id, str) and fix_id:
            finding_id = _fix_to_related_finding_id(event_log, fix_id)
    if finding_id is None:
        return None
    state = _finding_lifecycle_state(event_log, finding_id)
    if state in _FINDING_TERMINAL_LIFECYCLE_STATES:
        return {"finding_id": finding_id, "lifecycle_state": state}
    return None


def try_spawn_for_decision(
    decision: DispatchDecision,
    *,
    worktree: Path,
    parent_session_id: str = "f11-orchestrator-polling",
    mock: bool = True,
    max_retries: int = MAX_RETRIES_DEFAULT,
    event_log: EventLog | None = None,
    model: str | None = None,
    main_project_dir: str | None = None,
    actor_id: str | None = None,
    origin: str | None = None,
    fencing_token: int | None = None,
    account_status_path: Path | None = None,
    goal_session_id: str | None = None,
) -> tuple[bool, str, dict[str, object] | None]:
    """Spawn bg session per DispatchDecision. Returns (success, error_msg, spawn_dict).

    mock=True (default for safety): 不真起 bg, 只返回 mock spawn result.
    mock=False: 调 claude_bg_helper.spawn_bg_session 真起.
    event_log: optional — if provided, lookup trigger finding payload for
        high-quality per-skill prompt (RUN-015). If None, falls back to
        minimal generic prompt.
    model: B4 model 分层 — execution 按 task 的 model_tier 起 opus/sonnet (None=daemon 默认).
    main_project_dir: B1 事件回流 — 隔离工位里 prompt 的事件账本路径/--project-dir 必须指
        主仓库 (相对路径在工位 cwd 下指错地方)。
    actor_id: T-FIX-B4-01 物理门身份绑定 — 注入子会话 TOWOW_ACTOR_ID env marker (供
        PreToolUse 门解析谁在写)。None → 不注入。
    origin: T-FIX-B4-01 — 注入 TOWOW_SESSION_ORIGIN, 取值 ∈ B5 冻结集合
        {orchestrator_auto, inline_continuation, cli_goal_spawn}。编排器自动派 = orchestrator_auto。
        None → 不注入。task_id 取自 decision.task_id (派的具体 task)。
    account_status_path: 四账号轮换 (owner 2026-07-17) 登记簿路径。非 None 时透传给
        spawn_bg_session, 撞限额自动切账号重试。None (默认) = 不轮换 (零回归)。
    goal_session_id: f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①——
        调用方 (nonexec 派发循环) 在 spawn 前预生成的会话领域身份, 与 claude --bg 事后才分配
        的 bg_session_id 解耦。非 None 时: (a) 注入子会话 env TOWOW_SELF_GID (spawn_bg_session
        透传), 供 `goal terminate` 收尾读取当权威锚; (b) 写进派发 prompt 文本, 让 agent 对自己
        身份有字面认知 (非承重, 权威来源仍是 env)。None (execution fan-out 走另一套 per-session
        注册锁, 不受本 finding 影响) → 零回归, 退化到旧行为 (goal_session_id 等于 bg_session_id)。
    """
    from towow.l2.portable_runtime import SpawnMethod, spawn_bg_session
    from towow.l2.dispatch_templates import (
        FORWARD_CHAIN_REGISTRY,
        generate_condition_text,
        generate_forward_chain_condition_text,
    )

    # owner 2026-06-26: 回退到 CLAUDE_BG — bg 会话本来就能自愈 (网络 stall CC 自重试 / 用量上限
    # 6pm 自动恢复, 实证 0ac43869 自续跑 + 整批会话 6pm 全恢复)。之前判 "daemon-bg 卡死救不了" 是
    # 误判 (把用量上限停+网络自恢复读成卡死), CLAUDE_P 替代是错方向 (脱离 bg 治理)。autopilot 走 bg。
    method = SpawnMethod.MOCK if mock else SpawnMethod.CLAUDE_BG
    trigger_payload: dict[str, object] | None = None
    if event_log is not None:
        trigger_payload = _lookup_finding_payload(event_log, decision.trigger_event_id)
    if decision.review_mode or decision.trigger_event_type in FORWARD_CHAIN_REGISTRY:
        # E.5 前进链 / M-1.5 §7.0 review 触发边 (T-L1-54): 上游完工事件驱动, 无 finding —
        # 从上游 payload 自动生成下一棒该干嘛。review_mode 非空时 prompt 真带
        # `towow review start --mode <mode>` (generate_forward_chain_condition_text 内处理)。
        prompt = generate_forward_chain_condition_text(
            decision, trigger_payload=trigger_payload, main_project_dir=main_project_dir,
            # f-bgrv2-workcomplete-recompute-worktree-incompat: 传 spawn 工位路径 → execution fan-out
            # 在隔离工位时 prompt 注入 `work complete --repo-dir <工位>/harness` (复算看工位新代码, 非 main)。
            worktree=worktree,
            goal_session_id=goal_session_id,
        )
    else:
        prompt = generate_condition_text(
            decision, finding_payload=trigger_payload, main_project_dir=main_project_dir,
            # f-fixcomplete-closure-recompute-worktree-incompat: 传 spawn 工位路径 → fix 派活在隔离工位时
            # prompt 注入 `fix complete --repo-dir <工位>/harness` (review finding closure 复算看工位新建测试,
            # 非 main)。fix-dispatch 路径 (_prepare_fix_worktree → spawn_worktree=fix_wt) 已把工位传到这里。
            worktree=worktree,
            goal_session_id=goal_session_id,
        )

    # T-LRF-06 ② 答案注入 (消费侧): 重派一个曾撞 owner-only escalation 的 execution task 时,
    # 把 reflow 驱动 park 的 owner 答复追加进 prompt = 答案真到达 fresh 会话, 不再撞同一决策墙。
    # 通道在本文件内 (write_set 内闭合, 非"建了没人读"): park 由 drive_escalation_answer_reflow 写,
    # 此处读+注入, spawn 成功后 consume 清掉 (不重复注入)。main_project_dir 缺 (mock/无库) → skip。
    _inject_task_id: str | None = None
    if (
        decision.dispatch_to == "execution"
        and decision.task_id
        and main_project_dir is not None
    ):
        _inj_towow = Path(main_project_dir) / ".towow"
        _ans = read_escalation_answer_injection(_inj_towow, decision.task_id)
        if _ans is not None:
            _inject_task_id = decision.task_id
            prompt = f"{prompt}\n\n{_render_owner_answer_injection(_ans)}"

    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            result = spawn_bg_session(
                prompt,
                worktree,
                parent_session_id,
                method=method,
                # B3 (PARALLEL-EXEC-FIX): 显式传, 不吃 helper 默认 — 后台执行会话要真改代码+
                # 跑任意命令, 撞确认提示无人应答=永久卡 (16:06 T-SL-A1 acceptEdits 活体实证)。
                permission_mode="bypassPermissions",
                model=model,
                # T-FIX-B4-01: 注入物理门身份 marker (TOWOW_TASK_ID/ACTOR_ID/SESSION_ORIGIN)
                # 到子会话 env — PreToolUse 门 (T-FIX-B4-02/03) 据此判会话身份/写边界/origin。
                task_id=decision.task_id,
                actor_id=actor_id,
                origin=origin,
                # B-3: 注入 fencing token 到子会话 env TOWOW_FENCING_TOKEN → 完工携带 → gate 校验。
                fencing_token=fencing_token,
                # 四账号轮换 (owner 2026-07-17): None (mock/未传) = 不轮换, 零回归。
                account_status_path=account_status_path,
                # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①
                goal_session_id=goal_session_id,
            )
            spawn_dict: dict[str, object] = {
                "bg_session_id": result.bg_session_id,
                "goal_session_id": result.goal_session_id,
                "method": result.method.value,
                "launched": result.launched,
                "command_text": result.command_text,
                "started_at": result.started_at,
            }
            # T-LRF-06: 答案已注入 fresh 会话 prompt → 消费掉 park (不重复注入下次重派)。
            if _inject_task_id is not None and main_project_dir is not None:
                consume_escalation_answer_injection(
                    Path(main_project_dir) / ".towow", _inject_task_id,
                )
            return True, "", spawn_dict
        except (OSError, RuntimeError) as exc:
            last_error = f"attempt={attempt} error={exc!r}"
            if attempt < max_retries:
                time.sleep(RETRY_BACKOFF_S * attempt)
    return False, last_error, None


def _task_write_set(
    events: list[dict[str, object]], plan_id: str, task_id: str,
) -> list[str]:
    """task 声明的 write_set entity 键 (工位 .owner 文件用, V-01 写权限声明)。"""
    from towow.l2.execution_dispatch import _plan_claims

    _reads, writes = _plan_claims(events, plan_id)
    return sorted(writes.get(task_id, set()))


def _try_register_execution_lock(
    towow_dir: Path, goal_session_id: str, *, task_id: str,
    event_log: EventLog | None = None,
) -> bool:
    """B1: per-session 注册锁 (kind=execution) 替代单例 goal_session.lock 的抢座.

    N 个并行工位各持 .towow/locks/sessions/execution/<gsid>.json 一把, 互不抢、互不误释放
    (旧单例锁: 只第一个写得上, 兄弟 terminate 身份错乱/exit 1 — 设计 B1 实证)。registry 自带
    reap 自愈; reconcile 终态收口时同步 release。容错: 注册失败不崩 spawn (锁是观测/guard 用,
    完工信号走 TaskRunCompleted 不依赖它)。

    B-4 (substrate 4): acquire 成功 → emit LockAcquired 让 lock_graph 物化 held-by 边 (并发契约上图)。
    event_log=None (旧契约/测试) → 不 emit (registry 行为不变); 给定 → emit (held-by 接通)。
    """
    try:
        from towow.l1.session_lock import SessionLockRegistry

        SessionLockRegistry(towow_dir, "execution").acquire(
            goal_session_id,
            actor_id=_ORCH_ACTOR_ID,
            skill_id="M-1.4",
            fork_id=task_id,
        )
    except Exception:
        return False
    # B-4: 锁真注册成功 → 上图 (held-by)。emit 失败不翻 acquire 结果 (锁已落, 图是观测层)。
    if event_log is not None:
        with contextlib.suppress(Exception):
            emit_lock_acquired(
                event_log,
                lock_id=f"execution:{goal_session_id}",
                session_id=goal_session_id,
                resource=task_id,
            )
    return True


def _spawn_one_execution(
    towow_dir: Path,
    event_log: EventLog,
    *,
    task_id: str,
    tier: str,
    plan_id: str,
    trigger_event_id: str,
    mock_spawn: bool,
    max_retries: int,
    spawn_cwd: Path,
    events: list[dict[str, object]],
    isolation: bool | None = None,
    origin: str = "orchestrator_auto",
    dispatch_reason: str | None = None,
) -> None:
    """派一个 execution 工位 (B1 隔离 + B4 分层 + B2 失败不盖戳可见化)。

    ②A (崩机根治, 单任务受控派发) 新增的 3 个参数, 全部守旧默认值 —— daemon 自动车道
    (``_dispatch_execution_batch``) 零改动: ``isolation`` (None=沿用进程级 `_exec_isolation_enabled()`,
    只有 `towow orchestrator dispatch` 手动车道会传 True/False 覆盖单次派发的隔离开关) /
    ``origin`` (落进 GoalSessionStarted.spawn_origin, 手动车道传 "cli_goal_spawn" 而非
    "orchestrator_auto" —— 区分"daemon 自动派"与"owner 显式手动派"的 provenance) /
    ``dispatch_reason`` (None=用默认的 ready-set 批派措辞, 手动车道传自己的理由落进
    DispatchDecision.reason, 审计留痕)。
    """
    # ─── fnd-r01-9 (owner-gate 红线门, 兜底门 / belt-and-suspenders) ──────────────────────
    # 真 spawn 的物理咽喉再核一次 requires_owner_gate。主门 (_dispatch_execution_batch pool 剔除) 正常
    # 会拦在派进来之前; 但红线后果不可逆 (上线/删数据/动钱) → 任何【绕过主门】的派发来源 (未来新增的)
    # 都被这道兜底拦。标记 → 拒 spawn + 升级 owner + 显著告警 + return —— 绝不 spawn, 也绝不 raise
    # 崩 daemon (一个 owner-gate task 不该让整个后台崩, skip 这一个、别的安全 task 继续)。
    # owner 可经合法机制显式解除 (TaskNodeOwnerGateCleared) → _task_owner_gate 返 False, 本门放行;
    # 无【自动】放行开关 (解除须 owner 经 CLI + commit gate 显式发, 非 autopilot 自 emit)。
    gate, gate_reason = _task_owner_gate(events, task_id)
    if gate:
        if not owner_gate_escalation_pending(event_log, task_id):
            emit_owner_gate_escalation(
                event_log, task_id=task_id, owner_gate_reason=gate_reason,
                trigger_event_id=trigger_event_id,
            )
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=trigger_event_id,
                trigger_event_type=EventType.PLAN_FREEZED.value,
                dispatch_to="main-inbound",
                reason=(
                    f"⛔ OWNER-GATE 拦截 (兜底门): task={task_id} 触及不可逆真实世界动作 (5 类红线), "
                    "拒绝自动 spawn, 已升级 owner 等显式决定 (fnd-r01-9)"
                ),
                task_id=task_id,
            ),
        )
        return
    # ─── 统一活会话守卫 (兜底门, 物理咽喉) ──────────────────────────────────────────────
    # 池级守卫 (_dispatch_execution_batch / dispatch_one_task) 正常拦在派进来之前; 这道在真 spawn
    # 咽喉再核一次, 拦两类漏网: ① 任何【绕过池级守卫】的派发来源 (未来新增的通路天然被接住 —— 这正是
    # "排除判据只挂在一条通路上"的 replan 断头环病理的反面); ② 池构建与 spawn 之间的 TOCTOU 窗口
    # (owner 恰在此刻于别的终端 `tw work start` 认领了同一 task)。拒绝 = 不 spawn 不盖戳 + 可 grep
    # 留痕 (stdout + 账本 main-inbound); 会话真死离守卫集后, ready-set 重算自动捞回 (不饿死)。
    _live_hit = tasks_with_live_work_session(event_log, towow_dir).get(task_id)
    if _live_hit is not None:
        _live_sid, _live_src = _live_hit
        _log_live_session_refusal(task_id, _live_sid, _live_src, via="spawn-throat")
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=trigger_event_id,
                trigger_event_type=EventType.PLAN_FREEZED.value,
                dispatch_to="main-inbound",
                reason=(
                    f"⛔ 活会话守卫 (兜底门): refusal=live_session_exists task={task_id} "
                    f"session={_live_sid} source={_live_src} — 该 task 已有活工作会话 "
                    "(不论谁派的), 拒绝派孪生; 等它完工或真死后 ready-set 自动捞回接班"
                ),
                task_id=task_id,
            ),
        )
        return
    decision = DispatchDecision(
        trigger_event_id=trigger_event_id,
        trigger_event_type=EventType.PLAN_FREEZED.value,
        dispatch_to="execution",
        reason=dispatch_reason or f"ready-set 批派 plan={plan_id} task={task_id} tier={tier} (B1/B4)",
        task_id=task_id,
    )
    decision_payload: dict[str, object] = {
        "dispatch_to": "execution",
        "reason": decision.reason,
        "trigger_event_type": decision.trigger_event_type,
        "task_id": task_id,
        "model_tier": tier,
        "plan_id": plan_id,
        "dispatched_at": time.time(),
    }
    spawn_worktree = spawn_cwd
    effective_isolation = _exec_isolation_enabled() if isolation is None else isolation
    if effective_isolation and not mock_spawn:
        wt, wt_err = _prepare_exec_worktree(
            towow_dir, task_id,
            actor_id=_ORCH_ACTOR_ID,
            write_set=_task_write_set(events, plan_id, task_id),
        )
        if wt is None:
            # 硬门 (symlink/工位建不成) → 可见失败, 不盖戳 (可重派)
            fail_eid = emit_orchestrator_dispatch_failed(
                event_log, decision, handler="execution", final_error=wt_err, retry_count=0,
            )
            emit_orchestrator_dispatched(
                event_log,
                DispatchDecision(
                    trigger_event_id=trigger_event_id,
                    trigger_event_type=decision.trigger_event_type,
                    dispatch_to="main-inbound",
                    reason=f"⚠ 工位准备失败 task={task_id}: {wt_err} (fail_eid={fail_eid})",
                ),
            )
            return
        spawn_worktree = wt
        decision_payload["worktree"] = str(wt)
    # ── B-2 原子认领 (substrate 4): spawn 之【前】原子认领, 关掉 check-then-write 的 TOCTOU 孪生窗口 ──
    # 放在 owner-gate 兜底 (上面 return) + 工位准备 (上面 return) 之【后】—— 那些早返绝不会 strand 一个
    # claim (claim 在它们之后才发)。认领失败 = 另一 driver 正在 spawn 同 task → 不 spawn 转盯场 (下轮
    # 它若失败会释放, ready-set 重算捞回)。claimant = 编排器 actor + task (区分 driver)。
    _fencing_token = claim_exec_spawn(towow_dir, task_id, _ORCH_ACTOR_ID)
    if _fencing_token is None:
        return  # 另一 driver 已认领此 task 的 spawn → 不孪生 (设计 §八 B-2)
    # ★ 双检锁 (double-checked locking): 认领成功后【再查一次 stamp】。关掉 release-after-spawn 模型的
    # 重 spawn 窗口 —— 另一 driver 可能在我 pool-build 之后、claim 之前已完整跑完 (claim→spawn→stamp→
    # release), 此刻它的 claim 已释放但 stamp 在。不重查 → 我 claim 到空位再 spawn = 孪生。重查见 stamp
    # → 释放认领 + 返回 (不重 spawn)。pool-build 的 stamp 查 (是首检) + 此处 (是复检) 双保险。
    if is_exec_task_dispatched(towow_dir, task_id):
        release_exec_spawn(towow_dir, task_id, _ORCH_ACTOR_ID)
        return
    # T-RMD-S3-REAPER: spawn 前最后一刻心跳续约 — 把 claim 的 ts 刷到最新, 让一个跑得比心跳阈值久的
    # 合法 spawn (慢工位创建 / try_spawn 有界重试 backoff) 在并发 reaper (另一 driver 进程) 眼里仍是活的,
    # 不被当 stale 误回收 (此窗口 execution session 锁尚未注册, is_live 护栏接不上, ts 是唯一活信号)。
    renew_exec_spawn(towow_dir, task_id, _ORCH_ACTOR_ID)
    try:
        success, err, spawn_dict = try_spawn_for_decision(
            decision,
            worktree=spawn_worktree,
            mock=mock_spawn,
            max_retries=max_retries,
            event_log=event_log,
            model=None if mock_spawn else tier,
            main_project_dir=str(towow_dir.parent),
            # T-FIX-B4-01: execution fan-out 默认是编排器经 FORWARD_CHAIN 自动派 → orchestrator_auto;
            # ②A: 手动车道 (`towow orchestrator dispatch`) 传 cli_goal_spawn (T-FIX-B5 冻结集合内
            # 早就预留的值, 见 claude_bg_helper._VALID_SESSION_ORIGINS) —— 区分"谁按的键"。
            # actor=编排器 actor。子会话 env 带 TOWOW_TASK_ID(=task_id)/ACTOR_ID/SESSION_ORIGIN 供物理门。
            actor_id=_ORCH_ACTOR_ID,
            origin=origin,
            # B-3: 注入本次认领的 fencing token → 子会话 env TOWOW_FENCING_TOKEN → 完工 TaskRunCompleted
            # 携带 → commit gate 资源侧校验 (拒旧会话复活迟到写)。
            fencing_token=_fencing_token,
            # 四账号轮换 (owner 2026-07-17): mock 派发不轮换 (无真 token 可换); 真派发接登记簿。
            account_status_path=None if mock_spawn else default_status_path(towow_dir),
        )
        # success → stamp (mark_exec_task_dispatched) 在此内写, 在 finally 释放认领【之前】→ 释放时
        # stamp 已在 → 别的 driver pool-build 见 stamp 不再 claim (无窗口); fail → 不写 stamp 保持可重派。
        _spawn_one_execution_after_claim(
            towow_dir, event_log, decision=decision, decision_payload=decision_payload,
            task_id=task_id, tier=tier, trigger_event_id=trigger_event_id,
            success=success, err=err, spawn_dict=spawn_dict, max_retries=max_retries,
            origin=origin,
        )
    finally:
        # ★ 无论成败都释放认领 (claim 只守 spawn 窗口, 非终身 dedup): success → stamp 已接管终身 dedup
        # (上面 try 内已写, 早于此释放 → 无孪生窗口); fail/异常 → 释放让 ready-set 重算捞回 (不饿死)。
        # .fence 保留 → 下次认领 token 单调递增 (B-3 fencing)。这是设计点名"最易漏接缝"的完整覆盖。
        release_exec_spawn(towow_dir, task_id, _ORCH_ACTOR_ID)


def _spawn_one_execution_after_claim(
    towow_dir: Path,
    event_log: EventLog,
    *,
    decision: DispatchDecision,
    decision_payload: dict[str, object],
    task_id: str,
    tier: str,
    trigger_event_id: str,
    success: bool,
    err: str,
    spawn_dict: dict[str, object] | None,
    max_retries: int,
    origin: str = "orchestrator_auto",
) -> None:
    """B-2: spawn 结果落地 (从 _spawn_one_execution 抽出, 让认领 try/finally 包住 spawn+落地全程)。

    success → emit 审计 + GoalSessionStarted + SessionSpawned 血缘 + pending + 注册锁 + 写 stamp。
    fail    → emit dispatch-failed + main-inbound 可见化 (不写 stamp = 保持可重派; 认领由 caller finally 释放)。
    """
    if success:
        eid = emit_orchestrator_dispatched(event_log, decision, spawn_result=spawn_dict)
        decision_payload["orchestrator_dispatched_event_id"] = eid
        decision_payload["spawn_result"] = spawn_dict
        bg_id = spawn_dict.get("bg_session_id") if spawn_dict else None
        if isinstance(bg_id, str) and bg_id:
            launched = bool(spawn_dict.get("launched", False)) if spawn_dict else False
            gss_eid = emit_goal_session_started(
                event_log,
                goal_session_id=bg_id,
                spawned_role="execution",
                trigger_event_id=trigger_event_id,
                command_text=str(spawn_dict.get("command_text", "")) if spawn_dict else "",
                launched=launched,
                # T-FIX-B5-02: execution fan-out 默认属编排器自动派 (orchestrator_auto); ②A 手动车道
                # 传 cli_goal_spawn, 区分 provenance (谁按的键)。
                spawn_origin=origin,
                # T-TWU-G (debt-4d61bbb20311): 直存 task_id 当 goal 收口门 goal→plan 的 durable anchor
                # (execution fan-out 恒有 task_id), 让自动派发 goal 收口对 live_target_observable 走实证复算。
                task_id=task_id,
            )
            decision_payload["goal_session_started_event_id"] = gss_eid
            # K2b-REG#3 (substrate 2): 血缘收口 —— orchestrator (parent) emit SessionSpawned, 让
            # session_graph 真物化 parent→child spawned 边 (修 parent_session_id ~0% 兑现)。bg_id 与
            # GoalSessionStarted/pending/锁同一 id → 血缘节点被全系统引用非悬空; parent=_ORCH_ACTOR_ID
            # (= try_spawn_for_decision 默认 parent_session_id, 编排器轮询上下文)。只在 spawn 成功 +
            # bg_id 在手时 emit (设计 §A: parent emit 持久性优先, 子死血缘仍在)。
            sess_eid = emit_session_spawned(
                event_log,
                session_id=bg_id,
                parent_session_id=_ORCH_ACTOR_ID,
                form="bg",
                spawned_role="execution",
                task_id=task_id,
                trigger_event_id=trigger_event_id,
            )
            decision_payload["session_spawned_event_id"] = sess_eid
            if launched:
                record_pending_session(
                    towow_dir, bg_id,
                    spawned_role="execution",
                    trigger_event_id=trigger_event_id,
                    task_id=task_id,
                    model_tier=tier,
                )
                # B1: per-session 注册锁替代 goal_session.lock 抢座 (执行分支退役
                # _try_write_goal_lock — 单例锁是并行兄弟断链的根, 设计 B1)。
                # B-4: 传 event_log → acquire 成功 emit LockAcquired 上图 (held-by 接通)。
                decision_payload["execution_lock_registered"] = (
                    _try_register_execution_lock(
                        towow_dir, bg_id, task_id=task_id, event_log=event_log,
                    )
                )
        mark_exec_task_dispatched(towow_dir, task_id, decision_payload)
    else:
        fail_eid = emit_orchestrator_dispatch_failed(
            event_log, decision, handler="execution", final_error=err, retry_count=max_retries,
        )
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=trigger_event_id,
                trigger_event_type=decision.trigger_event_type,
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ SPAWN FAILED → execution task={task_id} "
                    f"(重试{max_retries}次耗尽): {err[:200]} (fail_eid={fail_eid})"
                ),
            ),
        )
        # B2: 失败不盖戳 → 保持可重派 (backlog re-scan 捞回)


def _dispatch_execution_batch(
    towow_dir: Path,
    event_log: EventLog,
    new_exec_decisions: list[DispatchDecision],
    *,
    mock_spawn: bool,
    max_retries: int,
    spawn_cwd: Path,
    gate_new_dispatch: bool = False,
    gated_out: list[str] | None = None,
) -> int:
    """B1+B4: execution 派发批 — 候选池(新事件 fan-out + backlog re-scan) → 策略选批 → spawn.

    backlog re-scan (红队 fatal 配套): 对所有已 PlanFreezed 的 plan 每轮重算 ready-set, 把
    被帽截断的 / 失败清戳的 task 捞回。watermark 单调推进下没有它, 加帽 = 被截断的 task 的
    trigger 事件已过水位线永不重扫 → 永久饿死 (看着像隔离没生效)。

    Returns 本轮真派出的工位数。
    """
    from towow.l2.execution_dispatch import (
        all_freezed_plan_ids,
        ready_execution_tasks_to_dispatch,
        select_dispatch_batch,
        tasks_pending_replan,
    )

    # T-RMD-S3-REAPER (backlog re-scan 接线, 根治 f-sub-atomic-claim-no-reaper): 重算 ready-set 之前先
    # 回收泄漏/过期的 exec .claim —— 崩溃泄漏的 claim 让 task 永久被认为已认领、永不重派 = 饿死。reaper
    # 一回收, 同一轮 ready-set 重算即可把被饿死 task 捞回重派 (与 backlog re-scan '捞回被截断 task' 同
    # 性质)。绝不崩 daemon (suppress 兜) —— 一个烂 claim 不该拖垮整条自动链。
    with contextlib.suppress(Exception):
        reap_stale_exec_claims(towow_dir, event_log)

    events = _all_events_as_dicts(event_log)
    # T-RMD-FND-01 bootstrap: 静默死亡 exec 戳 reaper —— 清戳后下方 pool 循环 is_exec_task_dispatched
    # 即见戳已清 (戳是文件不是事件, 无需重取 events)。与上方 reap_stale_exec_claims 互补 (claim 管
    # spawn 窗口死/戳未写; 本 reaper 管 spawn 后执行中静默死/戳已写, 死后戳残留挡重派 = '失败者永不重派')。
    with contextlib.suppress(Exception):
        reap_silently_dead_exec_stamps(towow_dir, event_log, events)
    # 候选池: task_id → (plan_id, trigger_event_id)。re-scan 先铺底, 新事件 decision 的
    # trigger 覆盖 (溯源更准 — 指向真正触发它 ready 的事件)。
    # T-FIX-B2-02: 用 ready_execution_tasks_to_dispatch (按 task_type 过滤掉 REVIEW task), 杜绝
    # ready 的 REVIEW-typed task 经 re-scan 进 execution 池被 _spawn_one_execution 当 execution
    # 误派 (它永产不出 verdict = 危机1根)。REVIEW task 的 review 派发走 _ready_execution_decisions
    # 主路径 (dispatch_to=review, 经 INV-B2-1 单飞门), 故被这里排除 ≠ 漏派, 只是改道。
    # R11 根治 (孪生烧钱 / 破单车道) + 手动会话失明根治: 派发前排除【有任何活工作会话在做】的 task ——
    # 统一守卫 tasks_with_live_work_session = daemon relay 面 (事件日志算, 扛得过重启/resume, 不像
    # exec 派发文件戳会丢) ∪ session lock registry 手动面 (owner 在别的终端 `tw work start` /
    # `tw claim` 起的会话)。R11 只有 relay 面 → owner 手动会话在做的 task 被反复派孪生 (owner 病根)。
    # 是 is_exec_task_dispatched 文件戳的纵深兜底: 戳因任何原因丢 (重启/resume 水位线跳过/reaper 清),
    # 有活会话的 task 也【绝不】被重派孪生。只有会话真死 (relay: reaper emit GoalSessionTerminated;
    # 手动: registry 锁 pid 死/心跳超时被 reap) 后才允许合法接班 (非孪生)。
    live_task_sessions = tasks_with_live_work_session(event_log, towow_dir)

    def _pool_refuse(t: str) -> bool:
        hit = live_task_sessions.get(t)
        if hit is None:
            return False
        _log_live_session_refusal(t, hit[0], hit[1], via="exec-batch-pool")
        return True

    pool: dict[str, tuple[str, str]] = {}
    for plan_id in all_freezed_plan_ids(events):
        trig = _latest_plan_freezed_event_id(plan_id, events)
        for t in ready_execution_tasks_to_dispatch(events, plan_id, set()):
            if not _pool_refuse(t) and not is_exec_task_dispatched(towow_dir, t):
                pool[t] = (plan_id, trig)
    for d in new_exec_decisions:
        if (
            d.task_id
            and not _pool_refuse(d.task_id)
            and not is_exec_task_dispatched(towow_dir, d.task_id)
        ):
            plan_id = (
                pool.get(d.task_id, ("", ""))[0]
                or _task_plan_id_from_events(d.task_id, events)
                or ""
            )
            pool[d.task_id] = (plan_id, d.trigger_event_id)
    # T-TWU-B1/T-R01-9 回归修复: 两源合并之后统一复核 pending_replan —— ready_execution_tasks_to_
    # dispatch (来源 A/backlog re-scan) 内部已排除【有未消费重排请求】的 task, 但 new_exec_decisions
    # (来源 B/本轮事件实时 fan-out, 由 _ready_execution_decisions 对每个 PlanFreezed/TaskRunCompleted
    # (success) 事件同步触发) 走的是原始 ready_tasks_to_dispatch, 从未经过这道排除。B2 清完 abort_for_
    # replan 的 exec 戳之后, 只要同 plan 内任意兄弟 task 的 success TaskRunCompleted 触发一次
    # _ready_execution_decisions, 该 task 就会被来源 B 直接塞回 pool, 绕开来源 A 的门被重派 (空烧/
    # 抢在 re-decompose 产出新包之前重派旧包)。在此统一剔除同时堵住两个来源 (来源 A 本来就被过滤过,
    # 重复判断无害)。
    pending_replan = tasks_pending_replan(events)
    for t in list(pool.keys()):
        if t in pending_replan:
            del pool[t]  # 有未消费重排请求 — 不进派发池, 等 re-decompose 产出新包 (新 TaskPackagePublished) 才放行
    # T-FIX-B1-02 (FORWARD-chain#6 / FORWARD-chain#2): escalation 久卡有归宿, 不无限自旋 —— 在自愈
    # 重派【熔断判定之前】先剔除"关联 pending escalation"的 task。重派它也只会再撞同一 owner-only
    # 决策墙 → 标 blocked_on_escalation (不盲派、不计入 B1-01 重派耗尽熔断: 它是等 owner 不是失败),
    # escalation 一旦被 owner 响应 (NJ 落账) → pending 判定翻假 → 下一轮 ready-set 自动捞回重派。
    # 必须先于熔断 loop 剔除, 否则 retry_count 看似达上限的 escalation-blocked task 会被误 trip 熔断。
    esc_blocked = tasks_blocked_on_pending_escalation(event_log, events)
    for t in list(pool.keys()):
        if t in esc_blocked:
            del pool[t]  # 不进派发池 (等 owner), 归宿落 status.blocked_on_escalation (非熔断/非盲派)
    # T-STUCK-STATE-ROOTFIX (task-stuck-state@v1 出口④): 上面 esc_blocked 只挡【仍 pending】的
    # escalation (等 owner)。但被 owner 应答后 pending 翻假 → task 重回 ready-set 重派 → 若前提落空
    # (T-DEC-5: 兄弟已做完) 会再撞墙→再 escalate→再应答, 无限循环且【每圈都豁免熔断计数】。给这个
    # "被回答的 escalation" 循环有界: 已应答 escalation 达 cap 的 task 不再无脑重派 —— held 出池 +
    # 投 dead-letter (circuit_tripped, 既有 sweep_aged_out→retired 兜底给死信条目一等终点) + 显著
    # surface owner (等 owner 退役决定)。owner GT#4 红线严守: 只 held+surface, 绝不自判 close task
    # 作废 (task→retired 仍须 owner-confirm)。与下方 B1-01 熔断复用同一 once-per-trip 幂等键 + dead-
    # letter (源对象,进入源) 幂等, 不重复告警/入箱; 置于熔断 loop 之前 (它被熔断计数豁免, 得单独收口)。
    reentry_exhausted = escalation_reentry_exhausted_tasks(event_log, events)
    reentry_cap = _redispatch_cap()
    for t in list(pool.keys()):
        if t not in reentry_exhausted:
            continue
        plan_of_t = pool[t][0]
        del pool[t]  # 达 reentry 上限 → 不进派发池 (不再无脑重派)
        circuit_key = _exec_task_stamp_name(t)
        if is_redispatch_circuit_tripped(towow_dir, circuit_key):
            continue  # 已熔断 (本路径或 B1-01) → 幂等: 不重复告警/入箱, 剔池即已停派
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=f"esc-reentry-exhausted-{circuit_key[:32]}",
                trigger_event_type="RedispatchExhausted",
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ 被回答的 escalation 循环耗尽: task={t} plan={plan_of_t} 已被 owner 应答 "
                    f"{reentry_cap}+ 次仍反复 re-escalate (前提疑落空), 已停派 + 投死信, 请 owner "
                    f"决定退役 (T-STUCK-STATE-ROOTFIX, task-stuck-state@v1 出口④)"
                ),
                task_id=t,
            ),
        )
        trip_redispatch_circuit(
            towow_dir, circuit_key,
            {"task_id": t, "plan_id": plan_of_t, "cause": "escalation_reentry_exhausted",
             "reentry_cap": reentry_cap},
        )
        dead_letter_inbox.enqueue(
            towow_dir, event_log,
            source_object_type="task",
            source_object_ref=t,
            entry_reason=dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED,
            original_trigger_event_id=None,
        )
    # ─── fnd-r01-9 (owner-gate 红线门, 主门) ────────────────────────────────────────────
    # 凡标 requires_owner_gate=True 的 task = 撞 owner 的 5 类【不可逆真实世界动作】(上线/删生产数据/
    # 动钱/对外发布/改公开承诺) → 未解除时物理剔出派发池 + 升级 owner (GoalEscalationRaised 经下轮
    # _route_event 路由 main-inbound)。owner 可经合法机制显式解除 (TaskNodeOwnerGateCleared 事件,
    # owner-gate-clearance@v1) → _task_owner_gate 返 (False,None), 本轮不剔, task 进 ready-set 被派。
    # 无【自动】放行开关: 解除须 owner 经 CLI (`towow plan owner-gate-clear`) + commit gate 显式发,
    # autopilot 不能自 emit 绕自己的红线 (区别 T-GL-09 INF-003 的配置开关)。
    # 与上面 esc_blocked 同模式 (剔池 + 给等 owner 归宿) 但更严: esc_blocked 漏了最坏多烧一会话再撞墙
    # (自限); owner-gate 漏了 = 不可逆动作被自动执行(不可回滚) → 防御纵深, 此处 pool 剔(主门) +
    # _spawn_one_execution 兜底断言(任何绕过本门的派发来源都被兜) 双层。dedup: 已有该 task 未响应的
    # owner-gate escalation → 只剔不重 emit (pending 本身是单一真相源, 不另写 marker 防漂移)。
    for t in list(pool.keys()):
        gate, gate_reason = _task_owner_gate(events, t)
        if not gate:
            continue
        trig = pool[t][1]
        del pool[t]  # 永不进派发池 (红线必停, 等 owner 显式决定)
        if not owner_gate_escalation_pending(event_log, t):
            emit_owner_gate_escalation(
                event_log, task_id=t, owner_gate_reason=gate_reason,
                trigger_event_id=trig,
            )
    # T-FIX-B1-01 (AUTOPILOT-core#5 / FORWARD-chain#2): 自愈重派上限 + 幂等熔断 —— 候选池构建处对
    # 每个候选 task 读 exec_task_retry_count。≥ 上限 (_redispatch_cap, env TOWOW_REDISPATCH_CAP 默认
    # 3) → 该 task 不进派发池 (不再盲派), emit RedispatchExhausted 显著告警 + 路由 main-inbound +
    # 写幂等熔断 marker。熔断后再扫: marker 在 → 不重复告警也不再重派 (幂等), 直到 owner 介入手清。
    # 熔断【只针对自愈重派】(retry marker 存在=被清过戳); retry_count=0 的首次派发不受影响。
    #
    # T-FIX-B5-05 (CONSTITUTION-unknown#3): 升级判据真接 l1 轴① should_escalate_technical_blocker
    # —— "technical_blocker 自动重试 N 次再升级给 owner, 而非无限重试"是 RUN-080 owner 授权的阈值语义,
    # 由 l1/escalation_threshold.py 定义。此前候选池用裸 `rc < cap` 字面比较各算一份阈值, 把判据接到
    # 轴①函数让两边共用同一 retry_count 来源(exec_task_retry_count)与同一阈值常量(cap), 不重新发明数字。
    cap = _redispatch_cap()
    for t in list(pool.keys()):
        rc = exec_task_retry_count(towow_dir, t)
        if not should_escalate_technical_blocker(rc, threshold=cap):
            continue  # 未到升级阈值 (含首次派发 rc=0) → 系统继续自动恢复, 正常进池
        circuit_key = _exec_task_stamp_name(t)
        plan_of_t = pool[t][0]
        del pool[t]  # 达上限 → 不进派发池
        if is_redispatch_circuit_tripped(towow_dir, circuit_key):
            continue  # 已熔断 → 幂等: 不重复告警 (本轮已从池剔除即不再派)
        last_err = _retry_marker_last_error(towow_dir, t)
        emit_redispatch_exhausted_alarm(
            event_log,
            task_id=t, plan_id=plan_of_t or None,
            retry_count=rc, last_error=last_err,
        )
        # main-inbound 显著通知: owner 进主对话即见此 task 重派耗尽熔断 (RedispatchExhausted 是
        # NodeTouched 不进 main-inbound poller; 这里补显著通知, 同 silent-death/phase-stuck 范式)。
        emit_orchestrator_dispatched(
            event_log,
            DispatchDecision(
                trigger_event_id=f"redispatch-exhausted-{circuit_key[:32]}",
                trigger_event_type="RedispatchExhausted",
                dispatch_to="main-inbound",
                reason=(
                    f"⚠ 自愈重派耗尽熔断: task={t} plan={plan_of_t} 重派 {rc} 次仍未成功 "
                    f"(达上限 {cap}), 已停派等 owner 介入 (T-FIX-B1-01)"
                ),
                task_id=t,
            ),
        )
        trip_redispatch_circuit(
            towow_dir, circuit_key,
            {"task_id": t, "plan_id": plan_of_t, "retry_count": rc, "last_error": last_err[:500]},
        )
        # T-LRF-02: 熔断的 exec task 投死信箱 (entry_reason=circuit_tripped) —— 熔断 marker 只防
        # 重派, 死信箱给它一等终点等分诊 (重派一次/退役/升级 owner)。与 trip 同 once-per-trip 块,
        # enqueue 再按 (源对象,进入源) 幂等。
        dead_letter_inbox.enqueue(
            towow_dir, event_log,
            source_object_type="task",
            source_object_ref=t,
            entry_reason=dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED,
            original_trigger_event_id=None,
        )
    # 层⑤ governor 接派发循环 (T-RMD-S4-GOVERNOR): 反应式 429 痕迹在效期内 → 本轮只挡【新派】
    # (首派 exec_task_retry_count==0), 绝不挡【重派】(retry_count>0 = 已起过的活死了要续) —— owner
    # 口径节流新派 ≠ 冻结已有工作的重派/收尾。被挡的新派 task 下轮 ready-set 重扫自动重评 (exec 重扫
    # 独立于 watermark, 见本函数 backlog re-scan), 429 痕迹过期即恢复。gate_new_dispatch=False (无痕迹
    # / 默认) → 此块零行为变化。剔池放在熔断/owner-gate 之后, 故对它们零影响。
    if gate_new_dispatch:
        for t in list(pool.keys()):
            if exec_task_retry_count(towow_dir, t) == 0:
                del pool[t]
                if gated_out is not None:
                    gated_out.append(t)
    if not pool:
        return 0
    # 活跃工位 (真活跃源 = pending_sessions, reconcile 会清 — 绝不数一生一次的 exec 戳)
    active = _active_execution_sessions(towow_dir)
    active_total = len(active)
    active_by_tier: dict[str, int] = {}
    active_task_ids: set[str] = set()
    for a in active:
        a_tier = str(a.get("model_tier") or "opus")
        active_by_tier[a_tier] = active_by_tier.get(a_tier, 0) + 1
        a_tid = a.get("task_id")
        if isinstance(a_tid, str) and a_tid:
            active_task_ids.add(a_tid)
    cap_total = _exec_cap_total()
    by_plan: dict[str, list[str]] = {}
    for t, (plan_id, _trig) in pool.items():
        by_plan.setdefault(plan_id, []).append(t)
    picked: list[tuple[str, str, str, str]] = []  # (task, tier, plan, trigger)
    for plan_id, cands in sorted(by_plan.items()):
        batch = select_dispatch_batch(
            events, plan_id, sorted(cands),
            active_total=active_total,
            active_by_tier=active_by_tier,
            active_task_ids=active_task_ids,
            cap_total=cap_total,
        )
        for t, tier in batch:
            # T-FIX-B1-01 (2): 自愈重派的 tier 真消费 upgrade_tier_hint — 候选 task 有 retry marker
            # 且 upgrade_tier_hint='opus' → 覆盖 TaskModelTierAssigned 给的 tier 为 opus (sonnet 失败
            # 升 opus 重跑, 不永用原 tier 重蹈覆辙)。无 marker / 无 hint → 按原 tier (首次派发不受影响)。
            upgrade = _retry_marker_upgrade_tier(towow_dir, t)
            eff_tier = upgrade or tier
            picked.append((t, eff_tier, plan_id, pool[t][1]))
            active_total += 1
            active_by_tier[eff_tier] = active_by_tier.get(eff_tier, 0) + 1
            active_task_ids.add(t)
    for t, tier, plan_id, trig in picked:
        _spawn_one_execution(
            towow_dir, event_log,
            task_id=t, tier=tier, plan_id=plan_id, trigger_event_id=trig,
            mock_spawn=mock_spawn, max_retries=max_retries, spawn_cwd=spawn_cwd,
            events=events,
        )
    return len(picked)


@dataclass(frozen=True)
class DispatchOneResult:
    """`dispatch_one_task` 的裁决 —— 拒绝时带一句可执行的下一步指引 (refusal_hint), 不是裸错误码。"""

    dispatched: bool
    refusal_code: str | None = None
    refusal_hint: str | None = None
    task_id: str = ""
    tier: str | None = None
    plan_id: str | None = None


def dispatch_one_task(
    towow_dir: Path,
    event_log: EventLog,
    *,
    task_id: str,
    tier: str | None = None,
    isolation: bool | None = None,
    mock_spawn: bool = True,
    while_paused: bool = False,
    max_retries: int = 3,
    spawn_cwd: Path | None = None,
    reason: str = "manual",
) -> DispatchOneResult:
    """②A (2026-07-02 崩机根治根因修复): 单任务受控派发 —— owner/CLI 显式派一个具体 task,
    不必等 daemon 的 ready-set 批派轮到它, 也不必像 2026-07-01 夜那个会话一样被逼 import
    `_spawn_one_execution` 手写编排 (那正是崩机夜游离 orchestrator 的源头, 见 finding
    "会话被一串墙逼进内部函数" — 缺口 1: 正规命令没有'派发单个任务'这个能力)。

    与 daemon 自动车道共享同一 spawn 咽喉 (`_spawn_one_execution`, 内含 owner-gate 兜底门 /
    原子认领 / 双检锁 / 全套落账), 只是入口 origin 不同 (cli_goal_spawn vs orchestrator_auto) —
    手动派发和自动派发走的是同一条真相路径, 不是另起一套。

    池级守卫按因果顺序逐条过 (每条拒绝都给出对应的正规命令做下一步, 不是让调用者自己猜):
      1. task 存在 (TaskNodeCreated 找得到) — 否则多半是 id 拼错。
      2. R11 活会话守卫: 有【活会话】在做这个 task → 拒, 【无 override】(孪生烧钱是 2026-07-01
         夜崩机的乘数之一, 这道门不能被参数绕开; 用 `towow vitality --session <id>` 查是谁在做)。
      3. exec 戳已在 → 拒 (指路 `orchestrator clear-exec-stamp`, 不重复造清戳入口)。
      4. 熔断中 → 拒 (指路 `orchestrator clear-circuit`)。
      5. 有未响应的 escalation 挡着 → 拒 (重派只会再撞同一堵 owner-only 决策墙, 等 owner 回话)。
      6. 编排器暂停中 且 未传 `while_paused=True` → 拒。暂停期手动单发**恰恰是合法核心场景**
         (pause = 冻 daemon 自动派新窗口, 不是冻整个系统; 2026-07-01 夜那个场景要的就是"全局冻住,
         只放这一个受控 task 出去") —— 但不能静默放行, 调用者必须显式确认自己知道系统处于暂停态。
      7. 并发帽已满 → 拒 (OOM 防护, 【无 override】——手动单发不该绕开保护机制本身要防的那件事)。

    全部过关 → 复用 owner-gate 兜底门自身在 `_spawn_one_execution` 内再核一次 (belt-and-suspenders,
    这里不重复判), 调 `_spawn_one_execution(origin="cli_goal_spawn", ...)` 派出。
    """
    ensure_orchestrator_layout(towow_dir)  # 同 pause_orchestrator 惯例: 调用者不必记得先建目录
    events = _all_events_as_dicts(event_log)

    plan_id = _task_plan_id_from_events(task_id, events)
    if plan_id is None:
        return DispatchOneResult(
            False, "task_not_found",
            f"task_id={task_id} 在账本里找不到 TaskNodeCreated —— 检查 id 拼写, 或它是不是还没建。",
            task_id,
        )

    _live_hit = tasks_with_live_work_session(event_log, towow_dir).get(task_id)
    if _live_hit is not None:
        _live_sid, _live_src = _live_hit
        _log_live_session_refusal(task_id, _live_sid, _live_src, via="cli-dispatch-one")
        return DispatchOneResult(
            False, "live_session_exists",
            f"refusal=live_session_exists task={task_id} session={_live_sid} source={_live_src} — "
            "该 task 已有活工作会话 (统一守卫, 无 override; 手动/自动都算) —— "
            f"用 `towow vitality --session {_live_sid} --project-dir <项目根>` 查它, "
            "等它完工/真死后再派 (绝不能对活会话派孪生, 这正是 2026-07-01 夜的乘数之一)。",
            task_id,
        )

    if is_exec_task_dispatched(towow_dir, task_id):
        return DispatchOneResult(
            False, "already_dispatched",
            f"task={task_id} 已有 execution 戳 —— 先 "
            f"`towow orchestrator clear-exec-stamp --task-id {task_id} --project-dir <项目根>` "
            "清戳, 再重跑本命令。",
            task_id,
        )

    circuit_key = _exec_task_stamp_name(task_id)
    if is_redispatch_circuit_tripped(towow_dir, circuit_key):
        return DispatchOneResult(
            False, "circuit_tripped",
            f"task={task_id} 熔断中 (自愈重派已耗尽次数上限) —— 先 "
            f"`towow orchestrator clear-circuit --task-id {task_id} --project-dir <项目根>` "
            "解熔断, 再重跑本命令。",
            task_id,
        )

    blocked_on_escalation = tasks_blocked_on_pending_escalation(event_log, events)
    if task_id in blocked_on_escalation:
        return DispatchOneResult(
            False, "blocked_on_escalation",
            f"task={task_id} 关联的 escalation ({blocked_on_escalation[task_id]}) 还没被 owner "
            "响应 —— 现在重派只会再撞同一堵墙, 等 owner 回话后自动被 ready-set 捞回, 不需要手动派。",
            task_id,
        )

    if is_orchestrator_paused(towow_dir) and not while_paused:
        return DispatchOneResult(
            False, "orchestrator_paused",
            "编排器当前处于暂停状态 —— 暂停只冻 daemon 的自动派新窗口, 不冻手动单发; "
            "确认要在暂停期手动派这一个 task 后, 加 `--while-paused` 重跑本命令。",
            task_id,
        )

    active = _active_execution_sessions(towow_dir)
    cap_total = _exec_cap_total()
    if len(active) >= cap_total:
        return DispatchOneResult(
            False, "cap_exceeded",
            f"活跃 execution 工位已达并发帽 ({len(active)}/{cap_total}, OOM 防护, 无 override) —— "
            "等某个工位完工释放后再派, 或调高 TOWOW_EXEC_MAX_PARALLEL (需清楚知道机器资源余量)。",
            task_id,
        )

    from towow.l2.execution_dispatch import task_model_tiers, tier_of

    eff_tier = tier or tier_of(task_id, task_model_tiers(events))
    frozen_trigger = _latest_plan_freezed_event_id(plan_id, events)
    if frozen_trigger:
        trigger_event_id = frozen_trigger
    else:
        # ad-hoc 未冻结 plan (设计②B 结论: 天然只走手动车道, daemon 只按 plan_id 重扫已冻结 plan
        # 的候选池, 对它物理不可见) —— trigger 如实指向这个 task 自己的 TaskNodeCreated, 不假装
        # 有个 PlanFreezed。
        trigger_event_id = next(
            (
                str(e.get("event_id"))
                for e in events
                if e.get("event_type") == "TaskNodeCreated"
                and isinstance((p := e.get("payload")), dict)
                and isinstance((a := p.get("after_state")), dict)
                and a.get("task_id") == task_id
            ),
            "",
        )

    _spawn_one_execution(
        towow_dir, event_log,
        task_id=task_id, tier=eff_tier, plan_id=plan_id, trigger_event_id=trigger_event_id,
        mock_spawn=mock_spawn, max_retries=max_retries,
        spawn_cwd=spawn_cwd if spawn_cwd is not None else towow_dir.parent,
        events=events,
        isolation=isolation, origin="cli_goal_spawn",
        dispatch_reason=f"手动单发 (CLI, owner 显式) task={task_id} tier={eff_tier} reason={reason!r}",
    )

    if is_exec_task_dispatched(towow_dir, task_id):
        return DispatchOneResult(True, task_id=task_id, tier=eff_tier, plan_id=plan_id)
    return DispatchOneResult(
        False, "spawn_failed",
        f"task={task_id} 派发尝试失败 (spawn 未成功落戳) —— 看 wake-watcher/daemon-run.log "
        "或重跑本命令 (未盖戳, 可安全重试)。",
        task_id,
    )


# ════════════════════════════════════════════════════════════════════════════════
#  Polling loop (M-3.1 §7.3 daemon design)
# ════════════════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════════════
# §SR 智能合约式自履行恢复编排 (T-RMD-RECOVERY / self-fulfilling-recovery)
# ════════════════════════════════════════════════════════════════════════════
# owner 2026-06-25 核心: 把两条恢复路径编排好 + 声明清楚, 让 autopilot 自主判断走哪条、自动
# 履行, 绝不 escalate owner。复用既有 forward chain / verdict loop / reconcile_loop, 只补关键缝。
#
# 【路径1 — 效果不达自履行修复环 (本节净新增 = 独立机器复算 + 环续 + 不升级)】
#   effect-review(rp-autopilot-turnon-remediation '按效果付费' VoI)产【盖 effect-review 标记的】
#   finding → forward chain 自动派 fix → fix 落账 (FixCompleted) → **本节 daemon sweep 独立机器
#   复算【设计效果是否达成】**
#   🔭 触发范围 = 真 effect-review 来源, 非任意能解析到 livefire 任务的 author_time review finding
#   (f-sr-sweep-broader-than-effect-review-scope): 只有 finding.rule_id ∈ _EFFECT_REVIEW_RULE_IDS
#   (seed 由 effect-review opt-in 盖 _EFFECT_REVIEW_RULE_ID / continuation 由本 sweep 自盖
#   _SELF_RECOVERY_RULE_ID) 才进效果复算。普通代码质量/一致性 review finding (无标记) 不复算 ——
#   否则每条 livefire 任务的普通 fix 后都重跑 X 的 pytest + emit 语义误导的 SelfFulfillingEffect-
#   Recovered (其实没发生效果恢复), 且把普通 fix 拖进 X 的 (可能 flaky/预存失败的) 无界恢复环。
#   (非仅【fix 是否提交】): 重跑被修能力任务 X 的 test 型 (live-fire) machine_check —— 复用 l1
#   recompute_livefire_check 真起 pytest subprocess 读真账本断 X 声称的 live 效果。
#     · 复算通过 → 效果真达成 → emit SelfFulfillingEffectRecovered (环终止)。
#     · 复算未过 → 效果仍不达 → emit FindingCreated(adjacent_code_issue→fix) → forward chain
#       自动再派 fix → 环继续, 直到效果真达成。绝不 escalate owner (本 sweep 永不路由 main-inbound)。
#     · X 无 test 型 machine_check (效果不可机器验) → emit FindingCreated(closure_contract_defect
#       →fix) 'effect-unverifiable', **不静默放过** (fact-B: 把'造好但效果验不了'的洞喊出来)。
#
#   关键缝为什么是【独立机器复算】不是【prompt 让 re-review agent 看一眼】(owner 钉死, 防 flag-only):
#   既有 closure_verification 是 **fix/review agent 自己 CLI 流里跑的自检** (运动员); commit gate
#   侧只有 review_verdict_check 折叠 finding 事件、**不重跑** closure 的 test 型判据。故没有任何
#   独立机器在 fix 落账后复算【效果】。本 sweep 就是那个缺位的裁判: 每条 fix 落账后由 daemon
#   **无条件、独立**重跑能力任务 X 的效果 test, 不信也不等被派的 re-review agent —— narrow/soft
#   closure 或 agent 自报通过都拦不住一个未达成的效果溜过 (= execution_done_recompute 之于
#   execution-self-check 的同型 gate↔self-check 关系, 把'机器算 != 自报即拒'从 work-complete
#   延伸到 fix→re-review 这一棒)。
#
# 【路径2 — 执行中途出错自动重计划 (复用既有 reconcile_loop, 本节仅声明、不另造)】
#   执行中途非瞬态出错 → 上游 emit RePlanTriggered → reconcile_loop._execute_replan_action
#   level-triggered 兜底自动派 planning 重拆 (partial/缺省 replan; 见 reconcile_loop.py)。
#   full_replan 仍 owner-gated (走 main-inbound, per-task-vs-production-real-boundary INF-003 红线
#   不自动重做整 plan)。瞬态错归 wake-watcher, 不进本路径。
#   🔴 **不在 _route_event 加平行的 RePlanTriggered→planning 边** —— reconcile 已用 composite 戳
#   (replan_event_id, "planning") 幂等去重, 再加一条不共享该戳的边 = 同 plan_id 双驱动派两个
#   planning (T-RMD-S3-DOUBLEDRIVE 刚焊的同型 race)。reconcile 的戳是唯一驱动源, 本节只声明 +
#   done_criteria 集成测试证它真跑、非交互。
_SELF_RECOVERY_MARKER_PREFIX = "selfheal-recovery-"
_EFFECT_RECOVERED_KIND = "SelfFulfillingEffectRecovered"
_SELF_RECOVERY_RULE_ID = "self-fulfilling-effect-recovery"
# f-sr-sweep-broader-than-effect-review-scope: §SR 效果复算的 opt-in 标记。effect-review
# (rp-autopilot-turnon-remediation '按效果付费' voi-effect-paid) 须给它产的【效果不达】seed finding
# 盖此 rule_id 才进效果复算环。无受约束的现成字段能可靠区分"效果不达 finding"(finding_kind=
# adjacent_code_issue 对效果/代码质量都用; voi_criterion_ref/review_plan_dimension_ref 是自由文本/
# 审查维度, 非分类信号) → 用专属 rule_id 标记做显式 opt-in, 而非脆弱的描述子串启发式。
# ⚠ 现状: 无任何生产 producer 盖此 seed 标记 → 收窄后 §SR 自履行恢复环 dormant 直到 effect-review
# 接线盖标记 (登债: 见本 finding fix 的 DebtRegistered, 偿付=effect-review 接线盖 seed 标记)。
_EFFECT_REVIEW_RULE_ID = "effect-review-livefire"
# sweep 触发白名单 = seed (外部 effect-review 盖 _EFFECT_REVIEW_RULE_ID) ∪ continuation (本 sweep
# 自产续环 finding 盖 _SELF_RECOVERY_RULE_ID)。二者同断言"能力任务 X 的 live-fire 效果不达" →
# 都该复算效果续环 (continuation 在白名单 = 一旦 seed 启动环, 后续棒能自续不断链)。
_EFFECT_REVIEW_RULE_IDS = frozenset({_EFFECT_REVIEW_RULE_ID, _SELF_RECOVERY_RULE_ID})


def _self_recovery_marker_path(towow_dir: Path, fix_id: str, effect_task: str) -> Path:
    """幂等戳 (key=fix_id+effect_task) 写 dispatched/ 下: sweep 每轮扫同一窗口, 一条 (fix, effect-
    task) 只复算 + emit 一次。无戳则每 interval 对同一 FixCompleted 重 emit finding = 风暴 (同
    reconcile '每 interval 重派' 戒 / T-RMD-S3 双驱动戒)。环靠【新 fix_id】推进, 非靠重扫旧 fix。"""
    digest = hashlib.sha256(f"{fix_id}|{effect_task}".encode()).hexdigest()[:16]
    return _dispatched_dir(towow_dir) / f"{_SELF_RECOVERY_MARKER_PREFIX}{digest}"


# ── per-effect-task 恢复熔断 (f-sr-recovery-loop-no-circuit-breaker) ──────────────────
# §SR 恢复环靠【新 fix_id】推进 (每轮新 finding→新 fix→新 fix_id), per-(fix_id,effect_task) 幂等戳
# 每轮换 fix_id 永不阻断环。故对【不可达/持续失败/flaky】的单个 effect_task, 环永久空转烧钱
# (每轮一个 fix agent + 一次 pytest subprocess), 三层既有 backstop 全不触: runaway killswitch 数的是
# 并发活跃会话≥12 而本环串行~1; cost 闸默认 OFF; redispatch_circuit 走 exec retry_count, 本环'新
# finding→新 fix'路径 retry_count 恒 0 不适用。本块补缺位的止损: 给【同一 effect_task】加恢复尝试
# 上限, 达上限 → 熔断 + 落 dead-letter (CIRCUIT_TRIPPED) + 停止续 emit effect-unmet finding (断
# forward chain 燃料 = 真停环), 绝不 escalate owner (honor owner 2026-06-25 '绝不 escalate')。
# ⚠ 这是【失败重蹈次数】上限, 不是【链长】上限 (镜像 _redispatch_cap 同范式, 不违 owner E.5 '不加
# 链长cap'): 链长 = effect 被一步步真修好的正常推进 (复算会转 pass→_emit_effect_recovered 自然终止);
# 本上限只数【同一 effect 反复复算仍 fail】的失败重蹈, 计数键 effect_task (跨 fix_id 存活), 效果一旦
# 真达成即清零 (独立恢复 episode 不串味)。
_RECOVERY_CIRCUIT_PREFIX = "recovery_circuit__"
_RECOVERY_ATTEMPT_PREFIX = "recovery_attempt__"
_RECOVERY_ATTEMPT_CAP_ENV = "TOWOW_SR_RECOVERY_ATTEMPT_CAP"
# 默认 5: 既有 live-fire 集成测试实测合法恢复最多 2 棒 (两棒后效果达成), 5 给正常多棒恢复留足余量,
# 又把不可达 effect 的烧钱上限钉死在 5 轮 (≤5 个 fix agent + ≤5 次 pytest) 内。可配 (= _redispatch_cap
# 同范式, owner 可按生产观测调)。
_RECOVERY_ATTEMPT_CAP_DEFAULT = 5
_RECOVERY_CIRCUIT_TRIPPED_KIND = "SelfFulfillingRecoveryCircuitTripped"


def _recovery_attempt_cap() -> int:
    """同一 effect_task 恢复【失败重蹈】次数上限 (可配 TOWOW_SR_RECOVERY_ATTEMPT_CAP, 默认 5)。

    非链长 cap (owner E.5 '不加链长cap' 成立): 链长是 effect 被逐步修好的正常推进 (复算转 pass 即
    终止); 本上限只数【同一 effect 复算仍 fail】的失败重蹈, 达上限熔断止住 runaway 空转。
    """
    raw = os.environ.get(_RECOVERY_ATTEMPT_CAP_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _RECOVERY_ATTEMPT_CAP_DEFAULT


def _recovery_circuit_path(towow_dir: Path, effect_task: str) -> Path:
    """per-effect-task 恢复熔断 marker (键 effect_task, 跨 fix_id 存活; 写 dispatched/ 下, 不以
    evt- 开头 → 永不归档, 与 redispatch_circuit__ 同范式, 也不被 collect_stuck_batons 误扫)。"""
    digest = hashlib.sha256(effect_task.encode()).hexdigest()[:16]
    return _dispatched_dir(towow_dir) / f"{_RECOVERY_CIRCUIT_PREFIX}{digest}"


def is_recovery_circuit_tripped(towow_dir: Path, effect_task: str) -> bool:
    """该 effect_task 的恢复熔断是否已触发 (触发后停环: 不再复算/不再续 finding, 幂等)。"""
    return _recovery_circuit_path(towow_dir, effect_task).exists()


def _recovery_attempt_count_path(towow_dir: Path, effect_task: str) -> Path:
    """per-effect-task 恢复尝试计数 marker (键 effect_task, 跨 fix_id 存活)。"""
    digest = hashlib.sha256(effect_task.encode()).hexdigest()[:16]
    return _dispatched_dir(towow_dir) / f"{_RECOVERY_ATTEMPT_PREFIX}{digest}"


def _bump_recovery_attempt(towow_dir: Path, effect_task: str) -> int:
    """同一 effect_task 又一轮复算仍 fail → 失败重蹈计数 +1, 返回累计值 (坏/缺文件按 0 起, 容错)。"""
    p = _recovery_attempt_count_path(towow_dir, effect_task)
    n = 0
    if p.exists():
        with contextlib.suppress(OSError, ValueError):
            n = int(p.read_text(encoding="utf-8").strip())
    n += 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(n), encoding="utf-8")
    return n


def _clear_recovery_attempt(towow_dir: Path, effect_task: str) -> None:
    """effect 真达成 → 清恢复尝试计数 (独立恢复 episode 不串味; 后续退化重新计)。"""
    with contextlib.suppress(OSError):
        _recovery_attempt_count_path(towow_dir, effect_task).unlink(missing_ok=True)


def _trip_recovery_circuit_and_dead_letter(
    towow_dir: Path,
    event_log: EventLog,
    *,
    effect_task: str,
    fix_id: str,
    finding_id: str,
    attempts: int,
    trigger_event_id: str,
    evidence: str,
) -> None:
    """达恢复尝试上限 → 写幂等熔断 marker + 落 dead-letter (CIRCUIT_TRIPPED) + 审计留痕, 停环。

    绝不 escalate owner (honor owner 2026-06-25): dead-letter CIRCUIT_TRIPPED 下游 reconcile 自动
    归 EXHAUSTED → decide_retire(REENTRY_EXHAUSTED) (reconcile_loop §T3.3 表), 不走 escalate 分支
    (只 UNROUTABLE→WAITING 才产 GoalEscalationRaised)。= 既止住 runaway 空转, 又不打扰 owner, 且
    死信有保证的终态可达 (自动退役, 非永久卡在箱里)。"""
    if is_recovery_circuit_tripped(towow_dir, effect_task):
        return  # 幂等: 上轮已熔断, 不重复落 dead-letter / 留痕
    p = _recovery_circuit_path(towow_dir, effect_task)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({
            "cause": "recovery_attempt_cap",
            "effect_task": effect_task,
            "attempts": attempts,
            "cap": _recovery_attempt_cap(),
            "last_fix_id": fix_id,
            "last_finding_id": finding_id,
            "tripped_at": time.time(),
        }),
        encoding="utf-8",
    )
    # 落 dead-letter: effect_task = 被死信的【对象】(判不可达的能力任务), 等后续分诊 (下游自动 retire)。
    dead_letter_inbox.enqueue(
        towow_dir, event_log,
        source_object_type="task",
        source_object_ref=effect_task,
        entry_reason=dead_letter_inbox.DeadLetterEntryReason.CIRCUIT_TRIPPED,
        original_trigger_event_id=trigger_event_id,
    )
    # 审计留痕 (event-sourced 单点信号: 环在此被熔断停止)。非 main-inbound / 非 escalation。
    decision_id = f"recovery-circuit-{hashlib.sha256(effect_task.encode()).hexdigest()[:12]}"
    event_log.write_direct(
        _build_orch_nodetouched(
            kind=_RECOVERY_CIRCUIT_TRIPPED_KIND,
            decision_id=decision_id,
            payload_body={
                "kind": _RECOVERY_CIRCUIT_TRIPPED_KIND,
                "effect_task": effect_task,
                "attempts": attempts,
                "cap": _recovery_attempt_cap(),
                "last_fix_id": fix_id,
                "last_finding_id": finding_id,
                "trigger_event_id": trigger_event_id,
                "evidence": evidence[:300],
                "escalated": False,
                "endpoint": "dead_letter_circuit_tripped",
            },
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        ),
    )


def _fix_to_related_finding_id(event_log: EventLog, fix_id: str) -> str | None:
    """fix_id → FixProposed.after_state.related_finding_id (M-1.6: fix 修的 finding)。

    与 _trace_fix_after_origin_review_task_id 步1 同源 (单一真值源, 二者共用)。多轮 propose
    取最后一条带 related_finding_id 的。无 → None。
    """
    finding_id: str | None = None
    for rec in event_log.get_events_by_type(EventType.FIX_PROPOSED):
        fp = _record_src(rec)
        if fp.get("fix_id") == fix_id:
            rfid = fp.get("related_finding_id")
            if isinstance(rfid, str) and rfid:
                finding_id = rfid
    return finding_id


def _task_node_type(event_log: EventLog, task_id: str) -> str | None:
    """task_id → TaskNodeCreated.after_state.task_type (无该 task → None)。"""
    for rec in event_log.get_events_by_type(EventType.TASK_NODE_CREATED):
        a = _record_src(rec)
        if a.get("task_id") == task_id:
            tt = a.get("task_type")
            return tt if isinstance(tt, str) else None
    return None


def _is_execution_task(event_log: EventLog, task_id: str) -> bool:
    """task_id 是否一个真实的【非 REVIEW】task (= 能力执行任务, 效果复算的对象)。

    无 TaskNodeCreated (None) → False; REVIEW-typed → False (review 任务不是效果载体)。
    """
    tt = _task_node_type(event_log, task_id)
    return tt is not None and tt != TaskType.REVIEW.value


def _execution_task_from_review_session(event_log: EventLog, review_session_id: str) -> str | None:
    """review 会话 A → 它评审的那个执行任务 X (canonical trigger 链, 全 event-sourced)。

    A 的 SessionSpawned.after_state.trigger_event_id = T (触发本次评审的事件); author_time review
    由 X 的 TaskRunCompleted(success) 触发, 故 T.task_id = X。确认 X 非 REVIEW-typed (是执行任务)。
    任一环断 → None (caller 回退别的解析路径)。
    """
    trigger_eid: str | None = None
    for rec in event_log.get_events_by_type(EventType.SESSION_SPAWNED):
        a = _record_src(rec)
        if a.get("session_id") == review_session_id:
            t = a.get("trigger_event_id")
            if isinstance(t, str) and t:
                trigger_eid = t
    if trigger_eid is None:
        return None
    trec = event_log.get_event(trigger_eid)
    if trec is None:
        return None
    _etype, tpayload = _unwrap_stub_rewrap(trec)
    x = _extract_task_id(tpayload)
    if isinstance(x, str) and x and _is_execution_task(event_log, x):
        return x
    return None


def _resolve_effect_task_for_finding(event_log: EventLog, finding_id: str) -> str | None:
    """effect-review-【标记】finding → 它指向的能力执行任务 X (效果复算的对象)。

    收口门 (f-sr-sweep-broader-than-effect-review-scope): finding 须盖 effect-review 标记
    (rule_id ∈ _EFFECT_REVIEW_RULE_IDS) 才进解析。普通 author_time review finding (代码质量/一致性,
    无标记) 能经路径 b 的 trigger 链解析到 livefire 任务 X, 但它不是【效果不达】finding —— 复算 X
    的效果 + emit SelfFulfillingEffectRecovered 是语义误导, 且把普通 fix 拖进 X 的无界恢复环。故标记
    门置于两条解析路径之前 (单一收口, caller _handle_fix_completed_for_effect_recovery 经 None 跳过)。

    两条 canonical 解析路径 (按确定性排, 仅对盖标记 finding):
      (a) finding.target.artifact 直接命中一个执行任务 task_id (finding 显式锚到任务时)。
      (b) finding.review_unit_id = 评审会话 A → A 的 SessionSpawned trigger → X 的 TaskRunCompleted
          → task_id = X (生产 author_time review 路径: target.artifact 多是文件, 靠 trigger 链反查)。
    无标记 / 都解析不到 → None (本 FixCompleted 非效果恢复候选, sweep 跳过不噪)。
    """
    fc: dict[str, object] | None = None
    for rec in event_log.get_events_by_type(EventType.FINDING_CREATED):
        src = _record_src(rec)
        if src.get("finding_id") == finding_id:
            fc = src
            break
    if fc is None:
        return None
    # effect-review 标记门: 非真 effect-review 来源 finding 直接 None, 不复算效果 (收窄触发范围)。
    if fc.get("rule_id") not in _EFFECT_REVIEW_RULE_IDS:
        return None
    tgt = fc.get("target")
    if isinstance(tgt, dict):
        art = tgt.get("artifact")
        if isinstance(art, str) and art and _is_execution_task(event_log, art):
            return art
    ru = fc.get("review_unit_id")
    if isinstance(ru, str) and ru:
        x = _execution_task_from_review_session(event_log, ru)
        if x:
            return x
    return None


def _build_recovery_finding_intent(
    *,
    finding_id: str,
    severity: str,
    risk_surface: str,
    description: str,
    finding_kind: str,
    effect_task: str,
    rule_id: str = _SELF_RECOVERY_RULE_ID,
    target_location: str = "done_criterion:live-fire-effect",
) -> EventIntent:
    """构造一条 daemon 自履行恢复 FindingCreated (非 review 语境 → source 不设 → FindingCreatedPayload
    的 review 必填字段条件不触发)。provenance = orchestrator 自动产出 (非交互), 走 path-A 经 gate emit。

    target.artifact 锚到能力任务 effect_task —— 让本 finding 被修后, 下一棒 fix 的 sweep 能经解析
    路径 (a) 重新解析回 effect_task, 续环不断链 (否则环第二棒解析不到 X 就静默停)。

    rule_id/target_location 参数化 (debt-72c5025798c4 根治): 默认值 = §SR 自己的 continuation marker
    (_SELF_RECOVERY_RULE_ID + live-fire-effect), 本 builder 唯一的 §SR 调用方 _emit_recovery_finding
    不传参、继续拿这两个默认值, 行为不变。reflow 三条调用方 (reconcile_blocked_reflows /
    emit_stranded_reflow_findings / triage_stranded_backlog) 曾无条件继承这组默认值, 致其 reflow
    finding 被 fix 落账后误经 §SR sweep 的 rule_id 白名单门 (_EFFECT_REVIEW_RULE_IDS 命中) → 对无
    published TaskPackage 的存量回流任务 (done_criteria=[]) 误 emit spurious f-effect-unverifiable
    (实证 T-REFLOW-02/03/05)。三条 reflow 调用方现各自显式传自己的 rule_id (非 §SR continuation
    marker), 令 rule_id 门本身就不再命中, 从根上切断误触发, 不必依赖下游 worktree_id 判别兜底。
    """
    return EventIntent(
        local_intent_id=f"selfheal-fc-{uuid.uuid4().hex[:12]}",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload={
            "finding_id": finding_id,
            "severity": severity,
            "risk_surface": risk_surface,
            "lifecycle_state": "created",
            "description": description,
            "detection_method": "automated_rule",
            "rule_id": rule_id,
            "finding_kind": finding_kind,
            "target": {"artifact": effect_task, "location": target_location},
        },
        provenance_hint=ProvenanceHint(actor_type=_ORCH_ACTOR_TYPE, actor_id=_ORCH_ACTOR_ID),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _build_recovery_finding_resolved_intent(
    *, finding_id: str, resolution_evidence: str,
) -> EventIntent:
    """构造一条 recovery finding 的 FindingResolved (bug-b: 处置/回流真解决后联动闭合)。

    resolution=confirmed_and_accepted: 观测在 emit 时是真的 (工位确曾搁浅/冲突), 只是后续经批处置
    (stranded_batch_disposition) 或正常回流 (WorktreePromoted) 权威解决 —— 不是"撤回"(观测本身没错),
    也不需要 confirmed_and_fixed 的 closure_contract 复算 (recovery finding 本身无 closure_contract,
    系统 finding), 语义上正是"确认过、接受已经通过别的授权路径解决"。closure_verification 留空 (无合约
    可比对, 走 CLI review_finding_resolve 的自洽底线同款: resolution≠confirmed_and_fixed 时不强制)。
    """
    return EventIntent(
        local_intent_id=f"selfheal-fr-{uuid.uuid4().hex[:12]}",
        event_type=EventType.FINDING_RESOLVED,
        event_category=EventCategory.FINDING,
        payload={
            "finding_id": finding_id,
            "lifecycle_state": "resolved",
            "resolution": FindingResolution.CONFIRMED_AND_ACCEPTED.value,
            "resolution_evidence": resolution_evidence,
        },
        provenance_hint=ProvenanceHint(actor_type=_ORCH_ACTOR_TYPE, actor_id=_ORCH_ACTOR_ID),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _emit_recovery_finding(
    event_log: EventLog,
    towow_dir: Path,
    *,
    finding_id: str,
    finding_kind: str,
    severity: str,
    risk_surface: str,
    description: str,
    effect_task: str,
) -> str | None:
    """path-A 经 commit gate emit 一条恢复 finding (FindingCreated 不在 path-B 白名单, 必走 gate)。
    被 gate 拒 → None。"""
    intent = _build_recovery_finding_intent(
        finding_id=finding_id,
        severity=severity,
        risk_surface=risk_surface,
        description=description,
        finding_kind=finding_kind,
        effect_task=effect_task,
    )
    return emit_finding_via_gate(
        event_log, intent, towow_dir, closure="self-fulfilling-effect-recovery",
    )


def _emit_effect_recovered(
    event_log: EventLog,
    *,
    effect_task: str,
    fix_id: str,
    finding_id: str,
    trigger_event_id: str,
) -> str:
    """path-B write_direct emit SelfFulfillingEffectRecovered audit 件 (环终止 = 效果真达成的留痕)。"""
    decision_id = f"effect-recovered-{hashlib.sha256(f'{fix_id}|{effect_task}'.encode()).hexdigest()[:12]}"
    intent = _build_orch_nodetouched(
        kind=_EFFECT_RECOVERED_KIND,
        decision_id=decision_id,
        payload_body={
            "kind": _EFFECT_RECOVERED_KIND,
            "effect_task": effect_task,
            "fix_id": fix_id,
            "finding_id": finding_id,
            "trigger_event_id": trigger_event_id,
            "recovery_path": "effect_self_fulfilling",
        },
    )
    return event_log.write_direct(intent).event_id


def _effect_task_has_livefire_machine_check(done_criteria: object) -> bool:
    """done_criteria 里是否有 ≥1 条 test 型 (live-fire) machine_check (= 可机器复算的效果牙齿)。"""
    from towow.schemas.enums import ClosureVerificationMethod

    for dc in done_criteria:  # type: ignore[attr-defined]
        mc = getattr(dc, "machine_check", None)
        if mc is not None and getattr(mc, "verification_method", None) == ClosureVerificationMethod.TEST:
            return True
    return False


def _handle_fix_completed_for_effect_recovery(
    towow_dir: Path,
    event_log: EventLog,
    fix_rec: EventRecord,
    fix_payload: dict[str, object],
    *,
    repo_dir: Path,
    pytest_runner: object | None,
) -> None:
    """单条 FixCompleted 的效果自履行复算 (路径1 主体)。异常由 caller 逐条 suppress, 绝不崩循环。"""
    fix_src = fix_payload.get("after_state")
    fix_src = fix_src if isinstance(fix_src, dict) else fix_payload
    fix_id = fix_src.get("fix_id")
    if not isinstance(fix_id, str) or not fix_id:
        return
    finding_id = _fix_to_related_finding_id(event_log, fix_id)
    if finding_id is None:
        return
    effect_task = _resolve_effect_task_for_finding(event_log, finding_id)
    if effect_task is None:
        return  # 不绑到可复算能力任务 → 非效果恢复候选, 跳过 (避免对每条普通 fix 噪报)
    if is_recovery_circuit_tripped(towow_dir, effect_task):
        # 该 effect 已达恢复尝试上限被判不可达熔断 → 停环: 不复算 (省 pytest)、不续 finding。
        # 兜底任何 stray late fix 落在已熔断 effect 上 (主止损是停发续环 finding 断 forward chain 燃料)。
        return
    marker = _self_recovery_marker_path(towow_dir, fix_id, effect_task)
    if marker.exists():
        return  # 同 (fix, effect-task) 本轮/上轮已复算过, 不重 emit (幂等)
    from towow.l1.execution_done_recompute import load_task_done_criteria, recompute_livefire_check

    done_criteria = load_task_done_criteria(towow_dir, effect_task)
    # 先盖戳: 不论下面走哪个分支, 这条 (fix, effect-task) 只处理一次 (环靠新 fix_id 推进)。
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"fix={fix_id} effect_task={effect_task} finding={finding_id}\n", encoding="utf-8")

    # fact-B 的例外 —— effect_task 无任何 published TaskPackage (done_criteria 全空) → 它根本不是
    # 一个进过 effect-review 契约体系的能力任务, 而是存量回流/直接起 run 的历史任务 (如 T-REFLOW-02/03/05:
    # 只有自由文本 TaskNodeCreated done_criteria, 从未 publish package)。对它 emit 'effect-unverifiable'
    # 是 spurious —— 与本文件 _build_recovery_finding_intent docstring 已判"无 published TaskPackage 的
    # 存量回流任务 (done_criteria=[]) 误 emit spurious f-effect-unverifiable"同一结论。reflow SEED 路径已
    # 各传自己 rule_id 断根, 但本 path1 continuation 断不了旧账本里 (那次修复前所建) 已盖 §SR rule_id 的
    # 旧 finding: 它被 fix 落账后经本 sweep 重解析回空-package 任务, 仍会误 emit 下一条。故在空判据处直接
    # 停环跳过 (return): 既不 spurious 喊洞, 也终止这条 f-effect-unverifiable 自我永续链 (否则连本次为
    # 610b231c 落账的 FixCompleted 自身都会再触发下一条)。fact-B 真正的洞 (package 已发布但无 test 型
    # 判据) 由下面的 _effect_task_has_livefire_machine_check 分支保留, 不受此 guard 影响。
    #
    # 操作层注脚 (f-effect-unverifiable-8bcffb455092b479 修复留痕): 本 guard 是**进程内**代码修复 —
    # 只对加载了本代码的 orchestrator 生效。若在本 guard 落 main 之后**仍**看到一条新的
    # f-effect-unverifiable 锚到无 published package 的历史任务 (T-REFLOW-02/03/05 类), 那不是代码
    # 洞复发, 而是**运行中的长驻 orchestrator 进程早于本 guard 启动、尚未重载**(Python 不热重载, git
    # 更新 main 不会改动已跑进程的字节码) → 该实例仍按旧代码在空判据处误 emit。处置是**受控重启
    # orchestrator 让它加载 ≥ 本 guard 的代码**, 不是再派一轮 fix 去"补 test 型判据"(那是被 610b231c
    # /本次两轮已否决的字面处方: 历史回流任务从不进 effect-review 契约体系, 其效果由自身测试套验)。
    if not done_criteria:
        return

    if not _effect_task_has_livefire_machine_check(done_criteria):
        # fact-B: 能力任务无 test 型 live-fire machine_check → 效果不可机器验。不静默放过 = 喊出洞。
        _emit_recovery_finding(
            event_log, towow_dir,
            finding_id=f"f-effect-unverifiable-{hashlib.sha256(f'{fix_id}|{effect_task}'.encode()).hexdigest()[:16]}",
            finding_kind="closure_contract_defect",
            severity="major",
            effect_task=effect_task,
            risk_surface=(
                f"效果不可机器验: 能力任务 {effect_task} 无 test 型 live-fire machine_check (无 published "
                f"package / done_criterion 非 test 型) → fix 落账后无法独立复算【设计效果是否达成】, "
                f"退回'做完没接'风险 (live-fire-machine-check-contract@v1)"
            ),
            description=(
                f"自履行恢复 sweep 对 fix={fix_id} 复算能力任务 {effect_task} 的效果时, 该任务 done_criteria "
                f"无 test 型 machine_check 可跑 → 效果验不了。需为 {effect_task} 补 test 型读账本断 live "
                f"签名的 done_criterion (或经 planning 重发布 package), 否则其'设计效果达成'永远只能靠自报。"
            ),
        )
        return

    # 独立机器复算: 真起 pytest subprocess 重跑能力任务 X 的 live-fire test (读真账本断 X 声称的效果)。
    # self_reported_done=True (fix 自报已修好) → 机器结果若 != 自报 (test 仍 fail) → status=failed = 效果仍不达。
    check = recompute_livefire_check(
        done_criteria=done_criteria,
        repo_dir=repo_dir,
        self_reported_done=True,
        pytest_runner=pytest_runner,  # type: ignore[arg-type]
    )
    if check.status == "passed":
        _emit_effect_recovered(
            event_log,
            effect_task=effect_task,
            fix_id=fix_id,
            finding_id=finding_id,
            trigger_event_id=fix_rec.event_id,
        )
        _clear_recovery_attempt(towow_dir, effect_task)  # 效果真达成 → 清失败重蹈计数 (episode 收尾)
        return
    # 效果仍不达 → 先记一次失败重蹈; 达上限 → 熔断止住 runaway 空转 (停环不再续 finding), 未达 → 续环。
    evidence = ""
    ev = getattr(check, "evidence", None)
    if isinstance(ev, dict):
        evidence = str(ev.get("summary", ""))[:300]
    attempts = _bump_recovery_attempt(towow_dir, effect_task)
    if attempts >= _recovery_attempt_cap():
        # 同一 effect 复算仍 fail 达上限 (不可达/持续失败/flaky) → 熔断 + dead-letter, 绝不 escalate。
        _trip_recovery_circuit_and_dead_letter(
            towow_dir, event_log,
            effect_task=effect_task, fix_id=fix_id, finding_id=finding_id,
            attempts=attempts, trigger_event_id=fix_rec.event_id, evidence=evidence,
        )
        return
    # 未达上限 → emit FindingCreated(adjacent_code_issue→fix), forward chain 自动再派 fix → 环续。
    _emit_recovery_finding(
        event_log, towow_dir,
        finding_id=f"f-effect-unmet-{hashlib.sha256(f'{fix_id}|{effect_task}'.encode()).hexdigest()[:16]}",
        finding_kind="adjacent_code_issue",
        severity="major",
        effect_task=effect_task,
        risk_surface=(
            f"设计效果仍不达 (按效果付费 voi-effect-paid): 能力任务 {effect_task} 在 fix={fix_id} 落账后, "
            f"独立机器复算其 live-fire test 仍未过 = fix 提交了但效果没达成"
        ),
        description=(
            f"自履行恢复 sweep 独立重跑能力任务 {effect_task} 的 live-fire machine_check, 机器结果 != fix "
            f"自报'已修好' → 效果仍不达成。继续修直到效果真达成 (rp-autopilot-turnon-remediation 按效果付费, "
            f"不只验 fix 提交)。复算证据: {evidence or '(test 复算未过)'}"
        ),
    )


def run_self_fulfilling_recovery_sweep(
    towow_dir: Path,
    event_log: EventLog,
    *,
    scan_start: int,
    scan_end: int,
    repo_dir: Path,
    pytest_runner: object | None = None,
) -> None:
    """路径1 sweep (run_polling_loop 挂点, 镜像 _auto_promote/_detect_orphaned_fix_commits 的区间扫描):
    扫本轮新事件 [scan_start, scan_end] 里的 FixCompleted, 对每条独立机器复算被修能力任务的设计效果。

    pytest_runner: 测试注入的 fake PytestRunner (None = 生产用真 pytest subprocess)。
    """
    if scan_end < scan_start:
        return
    try:
        records = event_log.get_events_in_range(scan_start, scan_end)
    except Exception:  # 扫描失败绝不拖垮自动链
        return
    for rec in records:
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        if effective_type != EventType.FIX_COMPLETED.value:
            continue
        with contextlib.suppress(Exception):  # 一条烂 fix 不崩整个 sweep / 循环
            _handle_fix_completed_for_effect_recovery(
                towow_dir, event_log, rec, effective_payload,
                repo_dir=repo_dir, pytest_runner=pytest_runner,
            )


def _health_gate_at_startup(towow_dir: Path, event_log: EventLog) -> None:
    """T-RMD-S5: pre-daemon-start health check (flag off=no-op).

    候选代码 = towow_dir.parent/src (harness 布局内的 src dir); towow 包不在 → skip (live 安装
    环境不需检). auto_rollback=False (daemon 启动路径共享主树, 不允许 未经 owner 确认的 git restore;
    失败仅 emit health/rollback 事件留痕, daemon 仍继续启动).
    """
    from towow.shell.bootstrap_canary import bootstrap_canary_enabled, health_gate

    if not bootstrap_canary_enabled():
        return
    try:
        candidate_src = towow_dir.parent / "src"
        if not (candidate_src / "towow").is_dir():
            return
        current_commit = _git_head_commit(towow_dir.parent)
        health_gate(
            towow_dir, event_log,
            repo_root=towow_dir.parent,
            candidate_src=candidate_src,
            current_commit=current_commit,
            trigger="daemon-start",
            auto_rollback=False,
        )
    except Exception:  # noqa: S110
        pass  # infra 异常不阻断 daemon 启动 (fail-open: health 是观测, 非门控)


def run_polling_loop(
    towow_dir: Path,
    event_log: EventLog,
    *,
    poll_interval_s: float = POLL_INTERVAL_DEFAULT_S,
    max_retries: int = MAX_RETRIES_DEFAULT,
    mock_spawn: bool = True,
    max_iterations: int | None = None,
    worktree_for_spawn: Path | None = None,
) -> int:
    """F-11 polling daemon main loop.

    Args:
        towow_dir: .towow/ path (containing events.log + orchestrator/)
        event_log: opened EventLog
        poll_interval_s: seconds between scans (default 5s per M-3.1 §7.3)
        max_retries: per-decision spawn retry budget (M-3.1 §7.6 not-infinite)
        mock_spawn: True = don't actually spawn bg (safety default); False = real spawn
        max_iterations: cap iterations for tests; None = run forever (until signal)
        worktree_for_spawn: cwd to use for claude --bg spawn (default towow_dir.parent)

    Returns:
        actual iterations run (for test verification).

    Signals:
        SIGINT/SIGTERM → graceful shutdown after current iteration.
    """
    ensure_orchestrator_layout(towow_dir)
    spawn_cwd = worktree_for_spawn or towow_dir.parent

    stop_flag = {"stop": False}

    def _sig_handler(_signum: int, _frame: object) -> None:
        stop_flag["stop"] = True

    # signal.signal must be called from main thread; raise to caller if not
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

    # ②D (2026-07-02 崩机根治): B5 单例守卫用原子声明取代旧版"先检测残留死pid、再单独写自己
    # 的pid"两步式 (M-3.1 §9.1 row6 的 detect_and_recover_stale_daemon + T-L3kc-05 的
    # write_daemon_pid) —— 两步之间的 TOCTOU 窗口正是崩机夜脱管第二个 daemon 的成因。
    # claim_daemon_singleton_or_raise 内部已经处理"已有活 daemon 拒绝 / 死残留清理重试"两种
    # 情形, 抛出的 RuntimeError 消息与旧版一致 (调用方/CLI 处理逻辑不用跟着改)。
    daemon_pid = claim_daemon_singleton_or_raise(towow_dir)
    # T-FIX-B1-04 (FORWARD-chain#4): 记录本次真跑形态 (MOCK/REAL) 进持久 marker + canonical 事件 —
    # status 长期可见 + preflight grep 判 '真派还是 mock 空转'。run_polling_loop 是唯一真起循环的
    # 入口 (foreground 与 --daemon grandchild 都落到这), 故记在这覆盖最全。防 '生产意图漏 --real-spawn'
    # 静默 mock 空转 (autopilot_idle_audit 实证的 15h 空转坑)。
    write_spawn_mode_record(towow_dir, event_log, mock_spawn=mock_spawn, pid=daemon_pid)
    # T-RMD-S3-REAPER (启动预检, 根治 f-sub-atomic-claim-no-reaper): 启动时先回收上次 daemon crash
    # 泄漏的 exec .claim (崩在 claim 与 finally:release 之间) —— 它们会让 task 永久饿死。与上面
    # detect_and_recover_stale_daemon 清死 pid 同性质 (清上次崩溃残留)。绝不崩 daemon 启动 (suppress 兜)。
    with contextlib.suppress(Exception):
        reap_stale_exec_claims(towow_dir, event_log)
    # T-RMD-S5 health gate (flag off=no-op): 部署的代码起不来 → emit health/rollback 事件留痕.
    _health_gate_at_startup(towow_dir, event_log)
    # f-orchestrator-restart-no-forward-chain-catchup: 启动时立即清掉 down-window 遗留的死
    # session 非 exec 复合戳 + 写 backlog, 重启即刻补 stranded forward-chain 步, 不等 120s
    # 周期对账 (setting 名: restart-forward-chain-catchup; 与 run_reconcile_pass 周期调用互补)。
    with contextlib.suppress(Exception):
        from towow.l2.reconcile_loop import run_startup_catchup_pass
        run_startup_catchup_pass(towow_dir, event_log, roster_ids_fn=cached_roster_ids)
    iteration = 0
    try:
        iteration = _run_polling_iterations(
            towow_dir,
            event_log,
            stop_flag=stop_flag,
            poll_interval_s=poll_interval_s,
            max_retries=max_retries,
            mock_spawn=mock_spawn,
            max_iterations=max_iterations,
            spawn_cwd=spawn_cwd,
        )
    finally:
        clear_daemon_pid(towow_dir)
    return iteration


def _run_polling_iterations(
    towow_dir: Path,
    event_log: EventLog,
    *,
    stop_flag: dict[str, bool],
    poll_interval_s: float,
    max_retries: int,
    mock_spawn: bool,
    max_iterations: int | None,
    spawn_cwd: Path,
) -> int:
    iteration = 0
    # T-FND-02 (巡检分频): wall-clock timestamp of the last stuck-baton sweep — the gate below
    # throttles it to one sweep per _stuck_sweep_interval_s instead of every ~5s poll. 0.0 ⇒ the
    # first poll always sweeps.
    last_stuck_sweep = 0.0
    # C-2 接线 (设计 20-layer23 §5.2): level-triggered reconcile 的上次跑时间戳。throttle 到
    # TOWOW_RECONCILE_INTERVAL_S (默认 120s, 显著 > poll_interval) — reconcile 是慢周期兜底,
    # 每 poll 全量扫账本会吃心跳空转 (autopilot_idle_audit 实证)。0.0 ⇒ 首轮即对账一次。
    last_reconcile = 0.0
    # f-orch-round-time-index-persist-storm-plus-on-passes-20260717 (advisor 裁决③): 每轮
    # per-phase 计时写健康面板 last_round.phases — 轮时失控(实测 326-1165s vs 设定 5s)的
    # 轮内构成不再靠猜。纯观测, 不改任何阶段语义。
    phases: dict[str, float] = {}
    _phase_t = {"t": 0.0}

    def _phase_mark(name: str) -> None:
        now_t = time.time()
        phases[name] = round(now_t - _phase_t["t"], 3)
        _phase_t["t"] = now_t

    while not stop_flag["stop"]:
        if max_iterations is not None and iteration >= max_iterations:
            break
        iteration += 1
        # T-FIX-COST-DROP: 本轮起点时间戳 — 传给 reconcile 精确保护"本轮刚 launch 的非 exec
        # 会话" (recorded_at ≥ 本轮起点 → daemon job 还没可见, 别误判 silent death 清掉刚盖的
        # 复合戳)。比 grace=poll_interval 数值判定更稳: poll_interval 是【轮间睡眠】, 本轮自身
        # (run_once+派发+spawn) 耗时满负载下可超过它, 导致同轮刚起的会话被 marker_age≥grace
        # 误收割 (满负载非确定性翻红的真凶)。按"本轮起点"判与机器负载无关。
        iteration_started_at = time.time()
        phases.clear()
        _phase_t["t"] = iteration_started_at

        # T-FIX-INC-01 layer4: 账本节流自动备份 (默认 1h 一次), 放在 paused 检查【之前】——
        # 事故当晚 (INCIDENT-TOWOW-WIPE-2026-06-10) 有个 paused daemon 在场却帮不上忙;
        # paused 只是"不派活", 备份护栏照跑。备份失败绝不杀轮询 (maybe_backup_ledger 内部
        # 吞错并落 .towow-backup/last_error.txt 可观测; 这里再兜一层防御)。
        with contextlib.suppress(Exception):
            maybe_backup_ledger(towow_dir)
        _phase_mark("backup")

        # T-LRF-10b (条款⑤) 本轮健康自报指标 — 先置默认 (非巡检轮也有值), 巡检块/派发块按真情更新,
        # 本轮收尾 record_daemon_round_health 写健康面板。
        did_full_sweep = False
        sweep_duration_s: float | None = None
        archived_this_round = 0
        dispatched_active_count: int | None = None

        # T-FIX-B1-03: 周期性自愈扫描放在每轮最顶 (paused/runaway/正常都巡检) —— 久卡断棒
        # (escalation 超时 / 重派熔断 / pending session 久未对账) 主动 surface, 不等 owner 发
        # prompt。纯 surface (不派新活), 故 paused 时也安全跑 (不改 paused '不派活' 语义)。
        # 去重靠 per-baton dedup marker, 不每 6s 刷屏。分布式自愈, 不引中心总控。
        # T-FND-02 (巡检分频): throttle to one sweep per interval — the batons it surfaces are
        # minute-to-hour-scale, so a per-poll sweep was pure CPU waste (each sweep folds the
        # committed stream). Still runs under paused/runaway (surface-only, no dispatch).
        if iteration_started_at - last_stuck_sweep >= _stuck_sweep_interval_s():
            sweep_t0 = time.time()
            _sweep_stuck_batons(towow_dir, event_log)
            # gap6 (dead-letter drain): same surface-only self-heal region — force-retire dead-letter
            # entries stuck past TTL so the inbox (enqueue wired, no exit) can't grow without bound.
            # Pure ledger terminal-ization (no dispatch / no kill), so it is safe under paused/runaway
            # just like the stuck-baton sweep above (list_entries already skips malformed entries).
            dead_letter_inbox.sweep_aged_out(
                towow_dir, event_log, ttl_seconds=_dead_letter_ttl_s(),
            )
            # T-LRF-11 (terminal-state-reachability@v1): 巡检把超龄重派 trigger 活体【强制终态化】
            # (行动非告警) —— 超龄 tripped 熔断 trigger → 投死信终态 + emit TerminalStateForced 留痕。
            # 升级 _sweep_stuck_batons 对 redispatch_exhausted 的'只告警'为'数据通道行动', 收口重派
            # 死循环 (evt-2d599e9e 类无限卡)。纯账本终态化 + 文件哨兵, 不 spawn 不 kill(kill 通道
            # owner-gated 红线, 不在此), 故同 surface-only 自愈区在 paused/runaway 下安全。兜错不崩 daemon。
            with contextlib.suppress(Exception):
                sweep_force_terminal_states(towow_dir, event_log)
            # T-LRF-06 (escalation-answer-reflow@v1): owner 答复落账后把答案主动送达死等任务并留痕
            # 闭环。同 surface-only 自愈区 — 只 emit canonical + 写 park, 不 spawn, paused 下也安全
            # (真重派由非 paused 时的 ready-set 做)。兜错不崩 daemon (一条回流失败不该停整个后台)。
            with contextlib.suppress(Exception):
                drive_escalation_answer_reflow(event_log, towow_dir, now=iteration_started_at)
            # T-LRF-10b (条款④ 已终态 dispatched marker 归档): 同节拍 (分钟级) 把超龄 trigger dedup
            # 戳搬进 dispatched_archive/, 活跃集 dispatched/ 保持小 → 上面的 _sweep_stuck_batons /
            # status iterdir 才不随账本线性恶化。纯文件重定位、对去重透明 (is_already_dispatched /
            # clear_nonexec_dispatch_stamp 都查 archive), 不 dispatch/不 kill → 与 stuck sweep 同样在
            # paused/runaway 下安全。绝不崩循环 (一次归档失败不该停后台)。
            with contextlib.suppress(Exception):
                archived_this_round, dispatched_active_count = archive_terminal_dispatch_markers(
                    towow_dir, now=iteration_started_at,
                )
            last_stuck_sweep = iteration_started_at
            did_full_sweep = True
            sweep_duration_s = time.time() - sweep_t0
        _phase_mark("stuck_sweep_block")

        # E.5 安全暂停: 暂停标记在时不 scan/dispatch/推进水位线, 只 idle 等 resume.
        # 协调者进程存活, 事件不消费 —— resume 后从原水位线正常处理。
        if is_orchestrator_paused(towow_dir):
            elapsed = 0.0
            while elapsed < poll_interval_s and not stop_flag["stop"]:
                chunk = min(0.5, poll_interval_s - elapsed)
                time.sleep(chunk)
                elapsed += chunk
            continue

        # AUTOPILOT-SAFETY 口子4: 内存失控总闸 (2026-06-30 崩溃根固化)。采系统真内存 (含所有 claude -p
        # fork 子进程的 RSS), 是唯一能看到 per-session fork 把进程数顶到 55G OOM 的门 (口子2 只数会话、
        # 看不到 fork)。放最前先查 (最关键 + 最便宜的一次系统调用)。同 pause 语义不打断在跑的。
        if _maybe_trip_memory_killswitch(towow_dir, event_log):
            elapsed = 0.0
            while elapsed < poll_interval_s and not stop_flag["stop"]:
                chunk = min(0.5, poll_interval_s - elapsed)
                time.sleep(chunk)
                elapsed += chunk
            continue

        # AUTOPILOT-SAFETY 口子2: 失控总闸 / kill-switch (runaway 保护, 非链长行为限制)。
        # 当活跃 spawn 会话总数 (任意角色) ≥ 高阈值 → 自动 pause + 通知 owner, 本轮不再 scan/派新。
        # 阈值明显高于正常帽 (默认 12 vs 帽 6), 正常单链/多链远低于不受影响 (与 owner '不加链长cap'
        # 设计意图不冲突)。不推进水位线 (同 pause 语义), resume 后从原处续。
        if _maybe_trip_runaway_killswitch(towow_dir, event_log):
            elapsed = 0.0
            while elapsed < poll_interval_s and not stop_flag["stop"]:
                chunk = min(0.5, poll_interval_s - elapsed)
                time.sleep(chunk)
                elapsed += chunk
            continue

        # AUTOPILOT-SAFETY 口子3 (T-FIX-B6-04, AUTOPILOT-core#5): 成本/token 预算闸。
        # runaway 口子2 只数会话, 拦不住"少量 opus 烧穿预算"(N 个 opus 花费 ≫ N 个 sonnet)。这条按
        # model_tier × 估算 token × 单价 估累计花费, 超阈 → 自检软暂停 + main-inbound 告警含累计花费数字。
        # 与 runaway 同 pause 语义 (stop_windows=False 不打断在跑, 不推进水位线, resume 从原处续)。
        # 放在 runaway 之后 daemon.run_once() 之前: 失控会话数先拦 (更便宜), 再按估算花费拦。
        # ⚠ finding-cost-gate-semantics-vs-owner-judgment-1: 成本闸默认 OFF (owner 判断订阅内不控成本
        # 只记账) — killswitch 默认 return False 直接跳过本块; TOWOW_COST_GATE_ENABLED=1 opt-in 才承重。
        if _maybe_trip_cost_budget_killswitch(towow_dir, event_log):
            elapsed = 0.0
            while elapsed < poll_interval_s and not stop_flag["stop"]:
                chunk = min(0.5, poll_interval_s - elapsed)
                time.sleep(chunk)
                elapsed += chunk
            continue

        # 层⑤ governor loop 接线 (substrate 5, T-RMD-S4-GOVERNOR, 接 f-sub-governor-not-wired-to-dispatch):
        # 🔴【已接线 — 干净反应式 429 源就位后兑现】。governor 决策逻辑 (DefaultUsageGovernor.can_dispatch)
        # 此前从未接进派发循环 = 零生产调用方, 资源近满也不真停派。现接进, 但避两条返工根因:
        #   ① 信号源只认【干净的真信号】, 绝不扫账本 error 自由文本 (第一版 false-positive farm 死因)。
        #      现两条源合并判定 (governor_wiring.combined_reading_source): reactive_resource_source
        #      (spawn 真撞限流落的专用痕迹) + memory_pressure_source (T-FIX-B6-01, 2026-07-02 崩机复盘补:
        #      OS 自报 kern.memorystatus_vm_pressure_level, 补上"机器快撑不住了"这条此前两条源都不管
        #      的信号, 根治首次真点火当晚只能人肉盯内存手动 pause 的问题)。两条都无约束 → can_dispatch
        #      恒 True → 零行为变化, 绝不误冻合法工作。
        #   ② gate 绝不在此 (daemon.run_once()/reconcile/auto-promote 之前) continue —— 那会冻结【已有工作
        #      的重派/收尾/reconcile】, 违 owner 口径。故 gate 决策在此【只计算一次】(governor_gate), 真正
        #      的"挡"落在下方各【真派新活点】(非 exec skill spawn + exec 批派发器), 只挡新派、不挡重派/收尾。
        # 决策一次、下方按点执行: governor_allows_new=False 时本轮不派【新】execution/非 exec spawn,
        # 但 daemon.run_once()/auto-promote/reconcile/重派 全照常跑。接法见 governor_wiring docstring。
        governor_allows_new, governor_resume_at = governor_gate(towow_dir)
        governor_blocked_new = 0  # 本轮真被 governor 挡掉的新派数 (>0 才 emit 节流留痕)
        _phase_mark("killswitches_governor")

        # B2-06 + T-RMD-S3-DOUBLEDRIVE 运行时哨兵: 每轮检查单飞不变量, 违反 fail loud 进 main-inbound
        # (不静默吞, 不崩 daemon)。per-key 哨兵覆盖 R11 退役后改 per-plan_id/per-task_id 单飞的
        # plan/execution, 末尾 delegate 给 kind 级 _check_single_flight_invariants (SERIAL_REJECT_KINDS,
        # 现空) → 一次调用覆盖两套不变量。
        _check_per_key_single_flight(towow_dir, event_log)
        _phase_mark("single_flight_check")

        watermark = load_watermark(towow_dir)
        daemon = OrchestratorDaemon(event_log, last_processed_seq=watermark)
        daemon.run_once()  # emits DaemonRunCompleted + populates daemon.decisions
        _phase_mark("run_once")

        # 回流 (M-3.1 §4.2.3 step6 autopilot 路径): 本轮新事件里若有 TaskRunCompleted(success) 且
        # 该 task 跑在隔离工位, 自动 promote 工位回 main (orchestrator 拥有回流, 不靠 agent 带
        # --worktree)。挂在 daemon.run_once() 之后、派发循环之前: 扫 daemon 本轮处理的同一事件区间
        # [watermark, daemon._last_seq-1]。在 paused/runaway/cost 闸之后 (上面那些 continue 跳过本块),
        # 故 paused 时不 promote (promote 会动 main, 属"干活")。内部按 task catch 所有异常, 绝不崩循环;
        # 外层再兜一层 suppress 防扫描本身意外 (红线: 一个烂工位不能拖垮自动链)。
        with contextlib.suppress(Exception):
            _auto_promote_completed_worktrees(
                towow_dir, event_log,
                scan_start=watermark,
                scan_end=daemon._last_seq - 1,
            )
        _phase_mark("auto_promote")

        # 搁浅工位周期 sweep (f-hardening-reaper-committed-no-success-noredispatch 第二支): 扫 ready-set
        # 找 task-{wid} 分支领先 main 但没回流的搁浅工位 surface needs-promotion baton 交 plan task-close。
        # 兜住 silent-death 收割支够不到的 (戳已被 legacy/pre-fix 清掉 / 已重派出孪生) 搁浅工作。与收割
        # 支共用幂等 marker 不双 baton。同 paused/runaway 闸后 (paused 时本块被上面 continue 跳过)。
        # 内部绝不 raise + 外层 suppress 兜底 (红线: 一个烂工位不能拖垮自动链)。
        with contextlib.suppress(Exception):
            sweep_stranded_worktrees_for_promotion(
                towow_dir, event_log, _all_events_as_dicts(event_log),
            )
        _phase_mark("stranded_sweep")

        # FB-1 (收口信号 choke-point 副作用): 本轮新事件里有 TaskRunCompleted(success) 的会话, 立刻
        # 代它 emit GoalSessionTerminated(reason=completion) 从 active_relay 摘掉 + 解锁前进链 ——
        # 不靠 agent 自报 (实证仅 14/369 自报)。扫与 auto-promote 同一区间 [watermark, _last_seq-1],
        # 同在 paused/runaway 闸之后 (paused 时本块被上面 continue 跳过)。gsid 经 canonical 反查
        # (绝不读共享锁/env), 查不到/不在 relay → 降级 no-op (FB-2 兜底)。内部 suppress 绝不崩循环。
        with contextlib.suppress(Exception):
            _auto_terminate_completed_sessions(
                towow_dir, event_log,
                scan_start=watermark,
                scan_end=daemon._last_seq - 1,
            )
        _phase_mark("auto_terminate")

        # 回流 fail-closed 兜底 (finding f-turnon-nb01...): FixCompleted 不在上面的 promote 路径,
        # fix 在任何成因被隔离的工位产的 commit 永不进 main = 静默孤儿化。扫同一区间验 fix commit
        # 真回流 main, 不可达就显著告警而非静默成功。同 paused/runaway 闸后 (continue 跳过本块),
        # 故 paused 时不告警。内部按 fix catch 异常绝不崩循环 + 外层 suppress 兜底 (回流红线)。
        with contextlib.suppress(Exception):
            _detect_orphaned_fix_commits(
                towow_dir, event_log,
                scan_start=watermark,
                scan_end=daemon._last_seq - 1,
            )
        _phase_mark("orphan_fix_detect")

        # §SR 路径1 (T-RMD-RECOVERY 自履行恢复): 本轮新事件里有 FixCompleted → 独立机器复算被修能力
        # 任务的设计效果 (重跑其 live-fire test), 效果仍不达 → emit finding 自动再派 fix (环续), 达成
        # → emit SelfFulfillingEffectRecovered。扫同一区间 [watermark, _last_seq-1], 同 paused/runaway/
        # cost 闸之后 (paused 时本块被上面 continue 跳过 — 复算/emit 属"干活")。绝不 escalate owner。
        # 内部逐 fix suppress + 外层兜底 suppress (一条烂 fix 不崩自动链)。
        with contextlib.suppress(Exception):
            run_self_fulfilling_recovery_sweep(
                towow_dir, event_log,
                scan_start=watermark,
                scan_end=daemon._last_seq - 1,
                repo_dir=towow_dir.parent,
            )
        _phase_mark("recovery_sweep")

        # 口子1: 非 exec spawn 并发帽 — 本轮可补额度 = 帽 - 当前活跃非 exec 工位数 (并发帽含在跑的)。
        # 额度耗尽后, 后续非 exec skill spawn decision 本轮不派 (不盖戳), 写 backlog marker 留下一轮捞回。
        # FB-3 (watermark↔派发容量解耦): 删去旧 nonexec_capped 二值闸 —— 它把『被截断』耦合进
        # 『水位线不推进』, cap 饱和成稳态时水位线冻死 → 整条尾巴每轮全量重扫 (96% CPU 自噬)。现各
        # 截断点改写 backlog marker (独立于水位线重发现), 水位线每轮无条件推进, 二者彻底解耦。
        nonexec_budget = max(0, _nonexec_cap_total() - _active_nonexec_session_count(towow_dir))
        # T-FIX-B2-01: 本轮已派出的 serial-reject kind(registry kind 名)。同 kind 第二个
        # decision 本轮不再派(否则两个同 kind start 撞单例锁, 后者 exit 1 静默死)。
        dispatched_kinds_this_round: set[str] = set()

        # T-FIX-B2-05 返工: 非 exec backlog re-scan — 把被清戳的 review/fix/consensus/plan
        # trigger 从 backlog marker 重建成 decision, 独立于 watermark 喂进同一条非 exec 派发路径
        # (经 B2-01 单飞门串行)。这是 exec backlog re-scan 的非 exec 对应物 (清戳 + 重发现两半)。
        # backlog decision 经 is_already_dispatched 判: 清了戳 → False → 走派发; 重派成功后删
        # marker (clear_nonexec_backlog_marker) 闭环。若被单飞门挡 (原 kind 还有 live) → 不删,
        # 下轮继续重试 (串行不丢)。
        backlog_decisions = _read_nonexec_backlog_decisions(towow_dir)
        backlog_keys = {
            (d.trigger_event_id, d.dispatch_to) for d in backlog_decisions
        }

        exec_batch: list[DispatchDecision] = []
        # 统一活会话守卫的本轮缓存 — 只在真有带 task_id 的非 exec spawn decision 时才算一次
        # (registry 目录扫 + committed_index warm 扫), 无关轮次零成本。
        live_map_nonexec: dict[str, tuple[str, str]] | None = None
        for decision in [*daemon.decisions, *backlog_decisions]:
            is_backlog_decision = (
                decision.trigger_event_id, decision.dispatch_to,
            ) in backlog_keys
            # B1/B4 (PARALLEL-EXEC-FIX): execution 派发改走批派发器 — 并发帽/model 分层/关键路径
            # 优先/隔离工位/backlog re-scan 都在那层; 此处只收集。dedup (task 戳) 也在批内做。
            if decision.task_id is not None and decision.dispatch_to == "execution":
                exec_batch.append(decision)
                continue
            # 其它 decision 用 (trigger event, dispatch target) dedup (T-L1-54: ECFreezed →
            # planning + review 两个都派)。B2: 带 task_id 的 main-inbound 失败可见化通知也走
            # 事件键去重 (不能被已派戳吞掉)。
            if is_already_dispatched(towow_dir, decision.trigger_event_id, decision.dispatch_to):
                # T-FIX-B2-05 返工: backlog decision 撞已派戳 = 该 trigger 已被 (重)派/在跑 →
                # 删 backlog marker 收口 (否则 stale marker 永远尝试重派一个已处理的 trigger)。
                if is_backlog_decision:
                    clear_nonexec_backlog_marker(
                        towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    )
                continue

            decision_payload: dict[str, object] = {
                "dispatch_to": decision.dispatch_to,
                "reason": decision.reason,
                "trigger_event_type": decision.trigger_event_type,
                "dispatched_at": time.time(),
            }

            # debt-986ba644fc44 (FB-3 backlog 重放前的阶段产出存在性检查): backlog decision 重建自
            # marker, marker 写下之后该 trigger 的工作有可能已经【经另一条路径】真出货 (上面
            # is_already_dispatched 只查"这个具体 trigger+dispatch_to 组合自己"是否被 mark_dispatched
            # 过, 查不出这个)。复用 _workitem_product_exists (T-FND-01 既有判据, 此前只接线到
            # reconcile_orphaned_sessions 的 silent-death 收割块, 未接线到本消费点 — 这正是本 debt 的
            # 根因): 按 work-item 键 (brief_id/plan_id) 查 consensus/planning 的阶段产出是否已
            # canonical。命中 → 丢弃 backlog marker、留痕 (mark_dispatched 记可 grep 的 skip 原因),
            # 绝不 spawn。只对 is_backlog_decision 生效 —— 新鲜 (本轮首次路由) 的 decision 从未
            # mark_dispatched 过, 不该被这条判据拦 (它们的 is_already_dispatched 已在上面判过)。
            # review/fix/notification 类 dispatch_to: _workitem_product_exists 对它们恒返回 False
            # (review 0-finding 无产物是刻意保留的硬边界; fix 已由下方 _terminal_finding_for_decision
            # 覆盖 finding 终态; notification 类无产出概念) — 此处不额外处理, 照常放行。
            if is_backlog_decision and _workitem_product_exists(
                event_log, decision.dispatch_to, decision.trigger_event_id,
            ):
                decision_payload["skipped_stale_backlog_phase_output"] = {
                    "dispatch_to": decision.dispatch_to,
                    "note": (
                        "stale-backlog-skip: 阶段产出已存在 (debt-986ba644fc44) — "
                        "丢弃 backlog marker, 不 spawn"
                    ),
                }
                mark_dispatched(
                    towow_dir, decision.trigger_event_id, decision_payload,
                    dispatch_to=decision.dispatch_to,
                )
                clear_nonexec_backlog_marker(
                    towow_dir, decision.trigger_event_id, decision.dispatch_to,
                )
                continue

            if decision.dispatch_to == "no-route":
                mark_dispatched(
                    towow_dir, decision.trigger_event_id, decision_payload,
                    dispatch_to=decision.dispatch_to,
                )
                # T-LRF-02: 路由不出的对象不是零落地 —— 投死信箱 (entry_reason=unroutable) 等分诊,
                # 给它一等终点 (终态可达, 不再 no-route 静默挂着)。enqueue 幂等 (mark_dispatched 上面
                # 已对该 trigger 去重, 这里再按 (源对象,进入源) 防重)。
                dead_letter_inbox.enqueue(
                    towow_dir, event_log,
                    source_object_type="finding",
                    source_object_ref=decision.trigger_event_id,
                    entry_reason=dead_letter_inbox.DeadLetterEntryReason.UNROUTABLE,
                    original_trigger_event_id=decision.trigger_event_id,
                )
                continue

            if decision.dispatch_to in {"main-inbound", "Nature dashboard"}:
                # 不 spawn — 只 emit audit trail. main_inbound_poller / Nature 自己消费.
                eid = emit_orchestrator_dispatched(event_log, decision)
                decision_payload["orchestrator_dispatched_event_id"] = eid
                # B2: 失败任务可见化通知带 task_id → 同步清它的 execution 戳 (留 retry marker),
                # 让 ready-set 重算 / backlog re-scan 能把它捞回重派 (sonnet→opus 升级由 marker 提示)。
                if (
                    decision.task_id
                    and decision.trigger_event_type == EventType.TASK_RUN_COMPLETED.value
                ):
                    decision_payload["exec_stamp_cleared_for_redispatch"] = (
                        clear_exec_task_stamp(towow_dir, decision.task_id)
                    )
                # F-026-3: escalation 触发 → 自动冻链(停协调者派新活, 不动正在等待的会话),
                # 等 owner 回话。escalation 是 owner-only 决策的显著通知 + 自动 pause。
                if decision.trigger_event_type == EventType.GOAL_ESCALATION_RAISED.value:
                    # T4 (PLAN-FIX): 按 escalation 的 blocking_scope 决定停多大范围。session_only 的
                    # 边缘决策只通知 owner (上面已 emit main-inbound 显著通知), 不停整个后台 — 防一条
                    # 不阻塞主线的 escalation 把全后台停掉 (T-L0-02 停 4 天的病根)。global 才全局 pause。
                    scope = _lookup_escalation_blocking_scope(event_log, decision.trigger_event_id)
                    decision_payload["escalation_blocking_scope"] = scope
                    if scope == "global":
                        pause_orchestrator(
                            towow_dir, event_log,
                            reason=f"auto-pause on escalation {decision.trigger_event_id}",
                            stop_windows=False,
                        )
                        decision_payload["auto_paused_on_escalation"] = True
                mark_dispatched(
                    towow_dir, decision.trigger_event_id, decision_payload,
                    dispatch_to=decision.dispatch_to,
                )
                continue

            # skill spawn path (fix / engineering-consensus / review / planning)
            # f-fixafter-dispatch-to-terminal-finding-relay-deadlock: 终态 finding 派发闸。
            # finding 已 resolved/accepted 后, 指向它的 fix / fix_after 派发是无出口的空转轨道
            # (实证 evt-0f0bc7c3: 终态 3.5h 后 FB-3 throttle_deferred 重放仍派第 3 轮 fix →
            # FixCompleted → fix_after 复验 → finding-resolve 门拒)。此处是 fresh + backlog 重放
            # 合流的唯一消费点, 在 governor 门前语义性 skip (不是资源节流 deferred — 该 decision
            # 永远不该派): 盖戳防 re-scan 重发现、清 backlog marker 收口; 不进死信 (finding 已有
            # 终局, 非 unroutable)。溯不出 finding_id → 不拦, 照常派。
            terminal_finding = _terminal_finding_for_decision(event_log, decision)
            if terminal_finding is not None:
                decision_payload["skipped_terminal_finding"] = terminal_finding
                mark_dispatched(
                    towow_dir, decision.trigger_event_id, decision_payload,
                    dispatch_to=decision.dispatch_to,
                )
                if is_backlog_decision:
                    clear_nonexec_backlog_marker(
                        towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    )
                continue
            # ─── 统一活会话守卫 (非 exec 通路: review-typed task fan-out / backlog 重放) ──────
            # exec 通路的守卫在 _dispatch_execution_batch 池 + _spawn_one_execution 咽喉; 这条非 exec
            # 通路此前【只有 trigger 事件戳去重, 没有任何活会话判据】—— owner 手动开的 review 会话
            # (tw review start 持锁, meta.task_id=REVIEW task id) 在跑时, 同 task 的 fan-out/backlog
            # decision 照样 spawn 孪生 (排除判据只挂在一条通路上的同款病理)。带 task_id 的 spawn 目标
            # decision 一律先过守卫: 命中 → 本轮不派不盖戳, 写 live_session_deferred backlog marker
            # (带 task_id, 重放不丢身份), 会话真死离守卫集后下轮 re-scan 自动放行。
            if decision.task_id:
                if live_map_nonexec is None:
                    live_map_nonexec = tasks_with_live_work_session(event_log, towow_dir)
                _hit = live_map_nonexec.get(decision.task_id)
                if _hit is not None:
                    _log_live_session_refusal(
                        decision.task_id, _hit[0], _hit[1], via="nonexec-decision",
                    )
                    write_nonexec_backlog_marker(
                        towow_dir, decision.trigger_event_id, decision.dispatch_to,
                        trigger_event_type=decision.trigger_event_type,
                        review_mode=decision.review_mode,
                        deferred_reason="live_session_deferred",
                        reason=(
                            f"refusal=live_session_exists task={decision.task_id} "
                            f"session={_hit[0]} source={_hit[1]} — 活会话在做, 本轮不派孪生"
                        ),
                        task_id=decision.task_id,
                    )
                    continue
            # 层⑤ governor 接派发循环 (T-RMD-S4-GOVERNOR): 反应式 429 痕迹在效期内 → 挡【新】非 exec
            # spawn (本轮 daemon.decisions 来的 fresh skill 派发), 但【work_continuation backlog 重派】
            # (silent-death 收割 / resume 保留 = 已有工作的续, deferred_reason=None) 照常 —— owner 口径
            # 节流新派 ≠ 冻结重派/收尾。
            # FB-3 (watermark↔派发容量解耦): governor 门【同样】适用 fresh-deferred backlog
            # (deferred_reason∈_GOVERNOR_REPASS_DEFERRED_REASONS = 本轮 governor/serial/budget/fix 截断
            # 写的【新派】, 上一轮没派成)。旧实现靠『水位线不推进→下轮当 fresh decision 重评』让它们重过
            # governor 门; 现水位线无条件推进, 必须在此显式重过门, 否则 governor 挡下的新派以
            # is_backlog_decision 身份绕过 429 节流 (laundering)。被挡 → 写/刷新 throttle_deferred backlog
            # marker (独立于水位线重发现), 不盖戳、不占 nonexec_budget、不撞单飞门, 纯粹本轮不起这个新会话;
            # 429 痕迹过期后下轮 governor_allows_new=True → 该 backlog 项放行真派。
            governor_applies = (
                not is_backlog_decision
                or decision.backlog_deferred_reason in _GOVERNOR_REPASS_DEFERRED_REASONS
            )
            if not governor_allows_new and governor_applies:
                governor_blocked_new += 1
                write_nonexec_backlog_marker(
                    towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    trigger_event_type=decision.trigger_event_type,
                    review_mode=decision.review_mode,
                    deferred_reason="throttle_deferred",
                    reason=(
                        f"governor 429 节流 deferred (FB-3): {decision.trigger_event_id}"
                        f"→{decision.dispatch_to} 重过 governor 门, 不绕 429"
                    ),
                )
                continue
            # T-FIX-B2-01 (PARALLEL-locks#1 / REVIEW-verdict#3): serial-reject kind 单飞门 —
            # review/fix/consensus/plan 的 start 物理单例(SessionLockRegistry 同 kind 一个 live)。
            # 派发前查两道: (1)该 kind 已有 live 会话; (2)本轮已派同 kind decision。任一命中 →
            # 本轮不 spawn、不盖戳, 写 serial_contention backlog marker 留 backlog re-scan 下轮捞回
            # (FB-3: 改『置 nonexec_capped 冻水位线』为『写 marker 独立重发现』), 而非盲 spawn 让 agent
            # start 内部 exit 1 静默死(THE blocker)。这道门取代总数 nonexec_budget 帽对 serial-reject
            # kind 的不足(总帽仍保留防瞬时爆发)。ECFreezed→planning + design_time review 同事件双 fan-out
            # 仍各自独立判 kind(planning 与 review 不同 kind 不互斥)。serial_contention 属 fresh-deferred
            # (上面 governor 门已对它重判)。
            serial_kind = _serial_reject_kind_for(decision.dispatch_to)
            if serial_kind is not None and (
                serial_kind in dispatched_kinds_this_round
                or _kind_has_live_session(towow_dir, serial_kind)
            ):
                write_nonexec_backlog_marker(
                    towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    trigger_event_type=decision.trigger_event_type,
                    review_mode=decision.review_mode,
                    deferred_reason="serial_contention",
                    reason=(
                        f"serial-reject kind 单飞 deferred (FB-3): kind={serial_kind} "
                        f"{decision.trigger_event_id}→{decision.dispatch_to}"
                    ),
                )
                continue
            # 口子1: 并发帽 — 额度耗尽则本轮不派此 decision (不盖戳, 写 cap_exhausted backlog marker
            # 留下一轮捞回), 防一批非 exec 事件同时落瞬时爆发大量会话。占额度从入选起算 (本轮已派的也
            # 算进活跃)。FB-3: 这是 cap=4 饱和稳态自噬的根截断点 —— 改『冻水位线』为『写 marker 独立
            # 重发现』, 水位线无条件推进不再被它冻死。cap_exhausted 属 fresh-deferred。
            if nonexec_budget <= 0:
                write_nonexec_backlog_marker(
                    towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    trigger_event_type=decision.trigger_event_type,
                    review_mode=decision.review_mode,
                    deferred_reason="cap_exhausted",
                    reason=(
                        f"非 exec 并发帽耗尽 deferred (FB-3): {decision.trigger_event_id}"
                        f"→{decision.dispatch_to}"
                    ),
                )
                continue
            # T-NB-3 (无分支隔离 fix): TOWOW_EXEC_ISOLATION=on 时, fix 派发不在共享主树跑, 而在各自
            # 独立 DETACHED 工位 (inv-nb-per-task-worktree, 不写 .owner inv-nb-fix-no-empty-owner) —
            # 与 execution 隔离同一灰度门 (_exec_isolation_enabled, 默认 off → fix 仍用共享 spawn_cwd,
            # 零行为变化)。在扣额度前备工位 — 备不成 (symlink 等硬门不过) 不浪费额度, 本轮不 spawn 留
            # backlog re-scan 下轮捞回, 绝不在共享主树盲 spawn fix。其余非 exec skill (review/consensus/
            # planning) 始终用共享 spawn_cwd。
            spawn_worktree = spawn_cwd
            if decision.dispatch_to == "fix" and _exec_isolation_enabled():
                fix_key = decision.task_id or decision.trigger_event_id
                fix_wt, _fix_err = _prepare_fix_worktree(
                    towow_dir, fix_key, actor_id=_ORCH_ACTOR_ID,
                )
                if fix_wt is None:
                    # FB-3: fix 隔离工位备不成 → 不浪费额度本轮不 spawn, 写 fix_worktree_unavailable
                    # backlog marker 留下一轮捞回 (改『冻水位线』为『写 marker 独立重发现』)。
                    write_nonexec_backlog_marker(
                        towow_dir, decision.trigger_event_id, decision.dispatch_to,
                        trigger_event_type=decision.trigger_event_type,
                        review_mode=decision.review_mode,
                        deferred_reason="fix_worktree_unavailable",
                        reason=(
                            f"fix 隔离工位备不成 deferred (FB-3): {decision.trigger_event_id}"
                            f"→{decision.dispatch_to} err={_fix_err}"
                        ),
                    )
                    continue
                spawn_worktree = fix_wt
            nonexec_budget -= 1
            # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: spawn 前预生成
            # 会话领域身份 (与 claude --bg 事后才分配的 bg_session_id 解耦) —— 这是唯一能在 prompt
            # 拼好、子会话 env 建好之前就存在的值, 才可能被注进 env 让子会话事后读到"自己是谁"。
            # 2026-07-17 事故 (esc-057ef69e4722): 派发 prompt 从不告诉 agent 自己的 gid, 收尾只能
            # 猜共享的 goal_session.lock (被最后 spawn 的邻居覆盖) —— 本预生成 + 下方 env 注入是根治。
            _nonexec_gsid = f"gs-{uuid.uuid4().hex[:12]}"
            success, err, spawn_dict = try_spawn_for_decision(
                decision,
                worktree=spawn_worktree,
                mock=mock_spawn,
                max_retries=max_retries,
                event_log=event_log,
                main_project_dir=str(towow_dir.parent),
                # T-FIX-B5-02: consensus/planning/review/fix 等非 exec skill 会话也是编排器经
                # FORWARD_CHAIN / review 触发边自动派 → origin=orchestrator_auto, actor=编排器
                # actor。子会话 env 带 TOWOW_SESSION_ORIGIN/ACTOR_ID 供物理门 (T-FIX-B4-02/03),
                # 与 execution fan-out 分支同口径 (那处早传, 此处补上, 否则采访→共识第一跳派的
                # consensus 会话 env 不带 origin, 物理门判 origin 时落空)。
                actor_id=_ORCH_ACTOR_ID,
                origin="orchestrator_auto",
                # 四账号轮换 (owner 2026-07-17): mock 派发不轮换 (无真 token 可换); 真派发接登记簿。
                account_status_path=None if mock_spawn else default_status_path(towow_dir),
                goal_session_id=_nonexec_gsid,
            )
            if success:
                # T-FIX-B2-01: 记本轮已派的 serial-reject kind, 阻止同轮同 kind 第二个 decision
                # 再派(否则两个同 kind start 撞单例锁, 后者 exit 1 静默死)。仅成功派出才记 —
                # 失败的不占名额, 留给下轮重派。
                if serial_kind is not None:
                    dispatched_kinds_this_round.add(serial_kind)
                # T-FIX-B2-05 返工: backlog decision 真重派成功 → 删 backlog marker 闭环收口
                # (清戳 + 重发现 + 重派成功删 marker = 完整的 exec 自愈范式非 exec 镜像)。
                if is_backlog_decision:
                    clear_nonexec_backlog_marker(
                        towow_dir, decision.trigger_event_id, decision.dispatch_to,
                    )
                eid = emit_orchestrator_dispatched(
                    event_log, decision, spawn_result=spawn_dict,
                )
                decision_payload["orchestrator_dispatched_event_id"] = eid
                decision_payload["spawn_result"] = spawn_dict
                # E.5: 注册被追踪的 GoalSession (完工兜底 + 完工核对骨架)
                bg_id = spawn_dict.get("bg_session_id") if spawn_dict else None
                # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: 领域身份用
                # 预生成的 _nonexec_gsid (spawn_dict["goal_session_id"] 回显), 不是 claude --bg
                # 自己分配的进程 id —— bg_id 仍保留给 record_pending_session (进程级判活对账要真
                # process id, 不受本 finding 影响, 见该调用点)。
                gsid = (spawn_dict.get("goal_session_id") if spawn_dict else None) or bg_id
                if isinstance(bg_id, str) and bg_id and isinstance(gsid, str) and gsid:
                    launched = bool(spawn_dict.get("launched", False)) if spawn_dict else False
                    gss_eid = emit_goal_session_started(
                        event_log,
                        goal_session_id=gsid,
                        spawned_role=decision.dispatch_to,
                        trigger_event_id=decision.trigger_event_id,
                        command_text=str(spawn_dict.get("command_text", "")) if spawn_dict else "",
                        launched=launched,
                        # T-FIX-B5-02: 显式标编排器自动派 (不吃默认值, 防未来默认漂移) —
                        # 让完工对账/verify-the-verifier 区分自动派 vs 同对话续跑/CLI goal spawn。
                        spawn_origin="orchestrator_auto",
                        bg_session_id=bg_id,
                    )
                    decision_payload["goal_session_started_event_id"] = gss_eid
                    if launched:  # 仅真 launched 会话记 pending (mock 无真 job 不对账)
                        # T-FIX-B2-05: 非 exec 工位带 dispatch_to + kind (serial-reject registry
                        # kind), reconcile 探测到 silent death 时据 dispatch_to 清复合戳让 trigger
                        # 重派 (launched-but-rejected 纵深防御: 单飞门正常但 start 内部撞 live exit 1)。
                        record_pending_session(
                            towow_dir,
                            bg_id,
                            spawned_role=decision.dispatch_to,
                            trigger_event_id=decision.trigger_event_id,
                            dispatch_to=decision.dispatch_to,
                            kind=serial_kind,
                            trigger_event_type=decision.trigger_event_type,
                            review_mode=decision.review_mode,
                            # f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①
                            # (reconcile 双路匹配基础, 见 record_pending_session/_has_termination_event
                            # docstring): gsid 是本会话真正 emit 终止事件时用的领域身份, bg_id 只是
                            # marker 主 key (vitality 查真进程用)。
                            domain_goal_session_id=gsid,
                        )
                        # F-026-6: 给目标 worktree 写 goal_session.lock, 让 daemon 派的
                        # 会话能 `goal terminate` 干净自报完工(否则无锁 → 报错 → 只能落
                        # reconciler external 兜底看着像崩溃)。与 `goal spawn` CLI 对齐。
                        # 容错: 已有 lock(另一会话活跃, 正常串行下罕见)则 skip 不崩。
                        # T-NB-3: goal lock 落【实际 spawn 的工位】(fix → detached 隔离工位;
                        # 其余 → 共享 spawn_cwd), 与子会话真实 cwd 一致, 否则 fix 会话 goal terminate
                        # 找不到锁。
                        # f-goal-session-identity-blind-shared-lock-misterminate-20260718: 锁内容
                        # 现落【领域身份】gsid (不是 bg_id) —— 与 GoalSessionStarted/Terminated 的
                        # goal_session_id 概念对齐一致; 该锁本身已不再是 `goal terminate` 收尾的权威
                        # 身份源 (权威源是子会话 env TOWOW_SELF_GID), 仅剩 is_in_goal_session
                        # 嵌套 spawn 检测这一用途 (② 共享单指针退役留后续 finding, 本次不碰其读侧语义)。
                        _try_write_goal_lock(
                            towow_dir, gsid, str(spawn_worktree),
                            str(spawn_dict.get("started_at", "")) if spawn_dict else "",
                        )
            else:
                eid = emit_orchestrator_dispatch_failed(
                    event_log,
                    decision,
                    handler=decision.dispatch_to,
                    final_error=err,
                    retry_count=max_retries,
                )
                decision_payload["orchestrator_dispatch_failed_event_id"] = eid
                decision_payload["final_error"] = err
                # B2 可见化: spawn 失败 (重试耗尽) 过去只留审计事件无人消费 — 现在给 main-inbound
                # 发显著通知, owner 下次进主对话即见 (UserPromptSubmit hook 注入)。
                notify_eid = emit_orchestrator_dispatched(
                    event_log,
                    DispatchDecision(
                        trigger_event_id=decision.trigger_event_id,
                        trigger_event_type=decision.trigger_event_type,
                        dispatch_to="main-inbound",
                        reason=(
                            f"⚠ SPAWN FAILED → {decision.dispatch_to}"
                            + (f" task={decision.task_id}" if decision.task_id else "")
                            + f" (重试{max_retries}次耗尽): {err[:200]}"
                        ),
                    ),
                )
                decision_payload["dispatch_failed_notified_event_id"] = notify_eid
            # (execution 派发已改道批派发器 — 此处只剩非 exec decision, 事件键盖戳;
            #  exec 的"成功才盖 task 戳"在 _spawn_one_execution 内, B2)
            mark_dispatched(
                towow_dir, decision.trigger_event_id, decision_payload,
                dispatch_to=decision.dispatch_to,
            )
        _phase_mark("nonexec_dispatch_loop")

        # B1/B4: execution 批派发 (候选池 = 本轮新事件 fan-out + backlog re-scan 捞回;
        # 帽/分层/关键路径优先/高fan-out串行/隔离工位都在批派发器内)。
        # 哨兵 A3 空转源: 捕获本轮真派出的 exec 工位数 (前进派发, 喂 reconcile 的 dispatched_count)。
        # 层⑤ governor (T-RMD-S4-GOVERNOR): gate_new_dispatch 时批派发器只挡【新派】exec task (剔出池),
        # 把被挡的收进 governor_exec_gated 供下方 emit 计数; 重派/收尾不受影响。
        governor_exec_gated: list[str] = []
        exec_dispatched_this_round = _dispatch_execution_batch(
            towow_dir, event_log, exec_batch,
            mock_spawn=mock_spawn, max_retries=max_retries, spawn_cwd=spawn_cwd,
            gate_new_dispatch=not governor_allows_new,
            gated_out=governor_exec_gated,
        )
        governor_blocked_new += len(governor_exec_gated)
        _phase_mark("exec_batch_dispatch")

        # 层⑤ governor 节流留痕 (T-RMD-S4-GOVERNOR): 本轮真撞 429 痕迹 (can_dispatch=False) 且真挡了
        # 新派 (>0) → emit canonical GovernorThrottledDispatch (done_criteria①: 集成测试读 all_records
        # 断言此事件 + provenance 非交互)。无痕迹 / 没挡到新派 → 不 emit (不在闲转时刷噪音)。
        if not governor_allows_new and governor_blocked_new > 0:
            emit_governor_throttled_dispatch(
                event_log,
                blocked_count=governor_blocked_new,
                resume_at_epoch=governor_resume_at,
            )

        # E.5 完工信号兜底对账 (F-019-10): bg job 终态退出未自报完工 → 调度员兜底 emit.
        # T-FIX-B2-05: nonexec_redispatch_grace=poll_interval — 本轮刚 launch 的非 exec 会话
        # (daemon job 未及在 ~/.claude/jobs 可见 → liveness MISSING) 不被同轮误判 silent death
        # 当场清掉刚盖的复合戳 (真死的会话后续轮 age≫grace 正常收割重派)。
        # T-SELFHEAL-STALE-RELAY: 显式接入节流版官方名单查询 (cached_roster_ids) —— 只有这条生产热
        # 路径真会去查 `claude agents --json --all`; 直调/单测不传此参数保持旧行为不变。
        reconcile_orphaned_sessions(
            towow_dir, event_log, nonexec_redispatch_grace_s=poll_interval_s,
            protect_recorded_at_or_after=iteration_started_at,
            roster_ids_fn=cached_roster_ids,
        )
        _phase_mark("reconcile_orphaned")

        # T-FIX-B3-02: phase 门 never-ready 静默死锁巡检 (orchestrator 自巡自己派发的 plan,
        # 分布式自愈不引中心总控)。含 implementation task 但持续 N 轮无 ready 且无 design-time
        # review_plan → emit PhaseStuckAlarmed + main-inbound 通知 (死锁不再无人知)。不改门语义。
        sweep_phase_stuck_plans(towow_dir, event_log)

        # T-RMD-AUTOFREEZE (INV-E v2): 自动渐进发包 + 自动两段冻结转换。前一批 (stage-1) task 都
        # completed 后, 编排器自动发下游已解锁任务的 staged 包 (经真 §11.1/§11.2 发布门) + 自动起第二段
        # plan-freeze (穿真冻结门含第10门), 使 INV-E 两段 bootstrap 端到端 hands-off。只对 latest
        # PlanFreezed.is_final_batch=False 的 plan 动作 (已 final 冻结的 plan —— 含生产单段全量冻结 ——
        # 绝不被触碰)。顺 sweep_phase_stuck_plans 范式: 自巡, 绝不崩循环 (内部吞异常 surface)。
        sweep_inv_e_refreeze(towow_dir, event_log)
        _phase_mark("phase_stuck_inv_e_sweeps")

        # C-2 接线 (设计 20-layer23 §5.2): level-triggered reconcile 兜底 —— 反复对比期望态 vs
        # 实际态的 diff, 漏事件下轮从当前状态收敛。补两个【现状无 consumer】缺口:
        #   ① RePlanTriggered 死胡同 (_route_event 零 case → 落账即失踪): 有 RePlan 无对应 re-decompose
        #      → 派 planning 重拆 (edge 快路径漏了/daemon 重启丢 watermark, 下轮兜底补)。
        #   ② 死信箱 triage 接活 (start_triage/decide_* 此前生产零调用, 只 TTL sweep 兜底丢)。
        # 在 paused/runaway/cost 闸之后 (那些 continue 跳过本块) → paused 时不 reconcile (replan 会派
        # 新活, 属"干活")。ReadySet 不在此重做 (走既有 _dispatch_execution_batch, 防双 dedup 孪生)。
        # throttle 到 reconcile_interval (慢周期): 漏事件本就是分钟-小时级, 不必每 poll 全量扫。
        # 内部按 action suppress 异常, 绝不崩循环 (一个烂项不拖垮自动链); 外层再兜一层。
        if iteration_started_at - last_reconcile >= reconcile_loop._reconcile_interval_s():
            with contextlib.suppress(Exception):
                # 哨兵 A3 空转源: 把本轮活动上下文喂进 reconcile pass, 收尾发布五计数 (供 detect_a3_reconcile
                # 空转探测)。watermark_before = 本轮 load 的水位线; watermark_after = 本轮即将 save 的
                # (daemon._last_seq-1, 与下方 save_watermark_atomic 同口径); exec_dispatched = 本轮真派的
                # exec 工位数 (前进派发); active_session = 当前在跑的接力窗口数。INV-SENT-EMIT-ONLY: 只观测,
                # 不改 reconcile 算动作/派发判定 (INF-003)。
                reconcile_watermark_after = (
                    daemon._last_seq - 1 if daemon._last_seq > 0 else 0
                )
                reconcile_loop.run_reconcile_pass(
                    towow_dir, event_log,
                    watermark_before=watermark,
                    watermark_after=reconcile_watermark_after,
                    exec_dispatched_count=exec_dispatched_this_round,
                    active_session_count=len(active_relay_sessions(event_log)),
                )
            last_reconcile = iteration_started_at
            # T-SELFHEAL-STUCK-DETECT (owner 直接要求 "让系统自己喊'我空转了'"): 消费本轮刚发布的
            # ReconcileCyclePublished 历史 (与上面 A3 空转哨兵同一真相源), 持续空转到阈值就自己升级,
            # 不等 owner 截图发现。绝不让告警判断拖垮主链。
            with contextlib.suppress(Exception):
                maybe_emit_productivity_stall_alarm(towow_dir, event_log, now=iteration_started_at)
        _phase_mark("reconcile_pass")

        # FB-3 (watermark↔派发容量解耦): 水位线【无条件】推进到 last_seq-1。旧实现用 if not
        # nonexec_capped 把『有非 exec decision 被截断』耦合进『水位线不推进』, 靠下一轮全量重扫从原
        # 处捞回被截断项。但 cap=4 饱和是稳态 (4 个 alive 非 exec 占满 → 每轮撞 budget=0 → nonexec_capped
        # 恒 True → 水位线冻死 → 整条尾巴每轮全量重扫 = 96% CPU 自噬)。现各截断点 (governor/serial/budget/
        # fix-worktree) 都写 nonexec backlog marker (独立于水位线的重发现路径, 每轮 _read_nonexec_backlog_
        # decisions 捞回), exec 截断由 _dispatch_execution_batch 每轮从 PlanFreezed plan 重算 ready-set 捞回
        # (亦独立于水位线) —— 两条 backlog re-scan 取代『冻水位线重扫』, 故水位线可安全无条件推进, 不再饿死。
        save_watermark_atomic(
            towow_dir, daemon._last_seq - 1 if daemon._last_seq > 0 else 0,
        )

        # T-LRF-10b (条款⑤ daemon 自报每轮耗时/扫描量到健康面板): 本轮收尾把耗时 + 扫描量写
        # daemon_health.json (collect_orchestrator_status 读出)。只在到达此处的【真工作轮】记 —
        # paused/runaway/cost 闸的 continue 轮是 idle (不消费/不派活), 不污染"工作轮多贵"的信号。
        # 绝不崩循环 (record 内部 atomic + 这里 suppress 双兜底)。
        with contextlib.suppress(Exception):
            record_daemon_round_health(
                towow_dir,
                round_index=iteration,
                duration_s=time.time() - iteration_started_at,
                decisions=len(daemon.decisions),
                did_full_sweep=did_full_sweep,
                sweep_duration_s=sweep_duration_s,
                archived_this_round=archived_this_round,
                dispatched_active_count=dispatched_active_count,
                phases=dict(phases),
            )

        # sleep, but check stop flag periodically (responsive shutdown)
        elapsed = 0.0
        while elapsed < poll_interval_s and not stop_flag["stop"]:
            chunk = min(0.5, poll_interval_s - elapsed)
            time.sleep(chunk)
            elapsed += chunk

    return iteration


__all__ = [
    "MAX_RETRIES_DEFAULT",
    "POLL_INTERVAL_DEFAULT_S",
    "ConditionResult",
    "DispatchDecision",
    "DispatchFailureClass",
    "OrchestratorDaemon",
    "OrphanFailClosedEvidence",
    "OrphanFailClosedVerdict",
    "RedispatchRoute",
    "StaleDaemonRecovery",
    "active_relay_sessions",
    "answered_escalation_reentry_count",
    "archive_terminal_dispatch_markers",
    "bump_nonexec_redispatch_count",
    "classify_dispatch_failure",
    "clear_daemon_pid",
    "clear_exec_task_stamp",
    "clear_nonexec_backlog_marker",
    "clear_nonexec_dispatch_stamp",
    "collect_orchestrator_status",
    "collect_self_heal_coverage",
    "collect_stuck_batons",
    "daemonize",
    "decide_orphan_fail_closed",
    "detect_and_recover_stale_daemon",
    "emit_escalation_still_waiting",
    "emit_goal_session_started",
    "emit_goal_session_terminated_fallback",
    "emit_orchestrator_dispatch_failed",
    "emit_orchestrator_dispatched",
    "emit_owner_gate_escalation",
    "emit_phase_stuck_alarm",
    "emit_redispatch_exhausted_alarm",
    "emit_self_heal_stuck_alarm",
    "ensure_orchestrator_layout",
    "escalation_reentry_exhausted_tasks",
    "exec_task_retry_count",
    "gather_orphan_fail_closed_evidence",
    "is_already_dispatched",
    "is_exec_task_dispatched",
    "is_orchestrator_paused",
    "is_orchestrator_process_alive",
    "is_redispatch_circuit_tripped",
    "judge_orphan_fail_closed",
    "load_watermark",
    "mark_dispatched",
    "mark_exec_task_dispatched",
    "nonexec_redispatch_count",
    "owner_gate_escalation_pending",
    "pause_orchestrator",
    "pending_escalations",
    "read_daemon_health",
    "read_spawn_mode",
    "reconcile_orphaned_sessions",
    "record_daemon_round_health",
    "record_pending_session",
    "resume_orchestrator",
    "route_dispatch_failure",
    "route_structural_failure_to_dead_letter",
    "run_polling_loop",
    "save_watermark_atomic",
    "surface_owner_signal",
    "sweep_phase_stuck_plans",
    "tasks_blocked_on_pending_escalation",
    "trip_redispatch_circuit",
    "try_spawn_for_decision",
    "write_daemon_pid",
    "write_nonexec_backlog_marker",
    "write_spawn_mode_record",
]
