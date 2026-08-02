"""会话活性派生信号 — RUN-028 task-run028-liveness.

# spec source:
#   harness/docs/RUN-028-E5-SPAWN-ROOTCAUSE.md
#   concept-orchestrator-session-liveness-signal / concept-spawn-progress-not-statejson-detail
#   docs/claude-code-daemon-mechanics.md §3.1 (state lifecycle: working→done|blocked|stopped)
#
# Why this module exists (E.5 自驱动闭环"卡 starting"病的根):
#   daemon 的 ~/.claude/jobs/<short>/state.json 的 `detail` 字段基本只在会话创建时写一次
#   ("starting…")，之后会话思考/执行整个过程不更新 → 任何读 `detail` 判进度的代码或人
#   都会把正在跑的会话误判成卡死 → 过早掐掉。RUN-028 三个独立会话实证 (主对话自身 + 实验
#   F/G): state.json 写着 detail="starting…" 时, 会话其实在 "thinking with xhigh effort"
#   真跑任务。
#
#   但 `state` 字段 (不是 detail) 是可靠的: working / done / blocked / stopped 真实反映
#   会话生命周期。本模块据此派生一个**可信、可程序化查询**的活性信号, 真信号优先级:
#     1. 会话自报的 v3 事件 (ground truth: emit 过 GoalSessionTerminated = 自报完工)
#     2. daemon job `state` 字段 (正确词表; 绝不读 detail)
#
#   首要消费者是发起者/Agent (机器可读): reconciler 兜底对账、orchestrator status 渲染、
#   未来 orchestrator 派活决策三方复用**同一信号**, 不再各写各的 state.json 解读 (避免
#   词表漂移 —— 旧 _default_bg_job_terminal 漏认 daemon 真实终态词 stopped/blocked 的 bug)。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from towow.schemas.enums import EventType

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog


class SessionLivenessVerdict(StrEnum):
    """一个被追踪会话的活性裁决 (派生信号, 非 daemon 原始字段)。"""

    RUNNING = "running"        # 在飞行中 (daemon state=working/active) — 别掐
    COMPLETED = "completed"    # 自报 GoalSessionTerminated 或 daemon state=done/exited
    STOPPED = "stopped"        # daemon state=stopped/killed/failed — 被外部终止
    STALLED = "stalled"        # daemon state=blocked — 活着但卡在等输入 (后台无人应)
    MISSING = "missing"        # 无 state.json (daemon 已清/归档 job dir) — 视作终态未知
    UNKNOWN = "unknown"        # state.json 在但 state 词不认识 — 保守不当终态


# daemon `state` 词表 (来自 docs/claude-code-daemon-mechanics.md §3.1 + 防御性同义词)。
# 关键: 这是**唯一**词表来源, reconciler / status / Agent 决策共用, 杜绝词表漂移。
_DAEMON_STATE_RUNNING = frozenset(
    {"working", "running", "active", "starting", "queued", "pending", "spawning"},
)
_DAEMON_STATE_COMPLETED = frozenset(
    {"done", "complete", "completed", "exited", "finished", "success", "succeeded"},
)
_DAEMON_STATE_STOPPED = frozenset(
    {"stopped", "killed", "cancelled", "canceled", "failed", "error", "aborted", "crashed"},
)
_DAEMON_STATE_STALLED = frozenset({"blocked", "waiting"})

# 终态-可清理 = 这些裁决下, 会话已不再推进, reconciler 该补完工 + 清 pending 标记。
# 注意 STALLED 不在内 (活着、等人, 不该 silent 当完工; 由 status 显著标出需关注)。
# UNKNOWN 不在内 (保守: 不认识的状态不冒险 silent 兜底)。
_TERMINAL_FOR_CLEANUP = frozenset(
    {SessionLivenessVerdict.COMPLETED, SessionLivenessVerdict.STOPPED, SessionLivenessVerdict.MISSING},
)


@dataclass(frozen=True)
class SessionLiveness:
    """一个会话的派生活性快照 — Agent / status / reconciler 共用。"""

    goal_session_id: str
    verdict: SessionLivenessVerdict
    daemon_state: str | None                 # daemon state.json 的 `state` 原值 (不含 detail)
    self_reported_termination: bool          # 会话自己 emit 过 GoalSessionTerminated
    termination_reason: str | None           # 若自报, 其 reason (completion/unreachable/...)
    last_event_seq: int | None               # 关联到本会话的最后一条 v3 事件 seq (进度锚)
    last_event_type: str | None
    last_event_ts: str | None

    @property
    def is_terminal_for_cleanup(self) -> bool:
        """True = 已不再推进, reconciler 应补完工 + 清 pending。"""
        return self.verdict in _TERMINAL_FOR_CLEANUP

    @property
    def is_running(self) -> bool:
        """True = 在飞行中, 别误判卡死、别掐。"""
        return self.verdict is SessionLivenessVerdict.RUNNING

    @property
    def needs_attention(self) -> bool:
        """True = 卡在等输入 (blocked) — status 该显著标出, 但不 silent 当完工。"""
        return self.verdict is SessionLivenessVerdict.STALLED


def _default_jobs_dir() -> Path:
    return Path.home() / ".claude" / "jobs"


def read_daemon_state(
    goal_session_id: str,
    *,
    jobs_dir: Path | None = None,
) -> str | None:
    """读 ~/.claude/jobs/<short>/state.json 的 `state` 字段 (不是 detail)。

    返回 state 字符串 (小写); 文件缺 / 不可解析 / 无 state 字段 → None。
    **只读 `state`, 绝不读 `detail`** (detail 是滞后假信号, 见模块 docstring)。
    """
    base = jobs_dir if jobs_dir is not None else _default_jobs_dir()
    state_path = base / goal_session_id / "state.json"
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    return state.lower() if isinstance(state, str) else None


def _classify_daemon_state(state: str | None) -> SessionLivenessVerdict:
    """daemon `state` 词 → 活性裁决 (state.json 缺 → MISSING)。"""
    if state is None:
        return SessionLivenessVerdict.MISSING
    if state in _DAEMON_STATE_COMPLETED:
        return SessionLivenessVerdict.COMPLETED
    if state in _DAEMON_STATE_STOPPED:
        return SessionLivenessVerdict.STOPPED
    if state in _DAEMON_STATE_STALLED:
        return SessionLivenessVerdict.STALLED
    if state in _DAEMON_STATE_RUNNING:
        return SessionLivenessVerdict.RUNNING
    return SessionLivenessVerdict.UNKNOWN


def _scan_session_v3_events(
    event_log: EventLog,
    goal_session_id: str,
) -> tuple[bool, str | None, int | None, str | None, str | None]:
    """扫事件日志, 找关联到本会话的 v3 事件。

    关联键: GoalSessionTerminated/Started 的 payload goal_session_id (兼容 canonical
    after_state + stub-rewrap 顶层), 或 provenance.session_id == goal_session_id。

    Returns:
        (self_terminated, termination_reason, last_seq, last_type, last_ts)
    """
    # 延迟导入避免循环 (orchestrator 也 import 本模块)。
    from towow.l2.orchestrator import _extract_goal_session_id, _extract_reason, _unwrap_stub_rewrap

    self_terminated = False
    termination_reason: str | None = None
    last_seq: int | None = None
    last_type: str | None = None
    last_ts: str | None = None

    # 2026-07-02 崩机根治收尾: all_records() 每次都重新扫盘+全量 pydantic 重解析
    # (它自己的 docstring 就标注"test/debug; consumers should use 9 Read APIs")——
    # daemon 首次点火实测: 单次 assess_session_liveness 调用 12-30s, run_startup_catchup_pass
    # 对每个 pending session 各调一次、daemon 首轮总耗时被拖到分钟级、且 CPU 密集期间对
    # SIGTERM 无响应。committed_index().records() 是同一份 committed 快照的内存缓存视图
    # (T-FND-02 stuck-baton sweep 已验证的同一模式), 类型/顺序完全等价, 只是不重新扫盘。
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        matches = _extract_goal_session_id(payload) == goal_session_id
        if not matches:
            prov = getattr(rec, "provenance", None)
            sid = getattr(prov, "session_id", None) if prov is not None else None
            matches = sid == goal_session_id
        if not matches:
            continue
        last_seq = rec.sequence_number
        last_type = etype
        # F-028-2: rec.timestamp 是 datetime, 必须转 ISO 字符串——否则流进 snapshot 后
        # orchestrator status --json 的 json.dumps 抛 TypeError (头号交付崩)。转 isoformat
        # 也让 last_event_ts: str | None 标注成真。
        _ts = getattr(rec, "timestamp", None)
        last_ts = _ts.isoformat() if _ts is not None else None
        if etype == EventType.GOAL_SESSION_TERMINATED.value:
            self_terminated = True
            termination_reason = _extract_reason(payload)

    return self_terminated, termination_reason, last_seq, last_type, last_ts


def assess_session_liveness(
    goal_session_id: str,
    event_log: EventLog,
    *,
    daemon_state_fn: Callable[[str], str | None] | None = None,
    roster_ids: frozenset[str] | None = None,
) -> SessionLiveness:
    """派生一个会话的活性信号 (真信号优先, 不读 state.json.detail)。

    优先级:
      1. 会话自报 GoalSessionTerminated → COMPLETED (ground truth, 压过 daemon state)。
      2. 否则按 daemon `state` 字段分类 (正确词表; state.json 缺 → MISSING)。
      3. roster 矛盾覆盖 (stale-relay-reap 根治): `~/.claude/jobs/<id>/state.json` 是 daemon 写一次、
         此后未必回写的滞后信号——真实进程被外部杀掉 (账号限额驱逐 / 宿主重启 / daemon roster 淘汰)
         时, 这个文件可能永远停在 "working" 之类的活态词, 派生出 RUNNING/STALLED, 让下游 reconciler
         把它当活会话【永久】跳过收割 (2026-07-17 owner 实证: 编排器反复空转, 需人工 `goal terminate`
         清 stale relay)。`roster_ids` 是同一轮只查一次的 `claude agents --json --all` 官方结果集
         (session_vitality.roster_session_ids) ——不在 state.json 之外新引入信号来源, 只是把已经设计
         好但从未接线的官方全局索引真正喂给判定。仅当 roster 查询【成功】(roster_ids is not None) 且
         这个 gsid 确凿不在官方名单里, 才把 RUNNING/STALLED 降级为 MISSING (= 已在
         `_TERMINAL_FOR_CLEANUP`, 走既有兜底完工/收割路径, 不新增分支)。roster_ids=None (查询失败/
         未注入) → 完全不覆盖, 行为与改动前一致 (FLP-safe: 信号不可得时保守不判死, 绝不因为一次瞬态
         子进程失败就把所有活会话一并误杀)。

    Args:
        goal_session_id: daemon short_id (= 真 launched 会话的 goal_session_id)。
        event_log: 打开的事件日志, 用来查会话自报事件。
        daemon_state_fn: 注入点 (测试用)。None → 读真 ~/.claude/jobs。
        roster_ids: 本轮 `claude agents --json --all` 的 id 集合 (调用方每轮只查一次, 复用给所有
            候选会话); None = 未注入/查询失败, 不做 roster 覆盖 (向后兼容默认行为不变)。
    """
    state_fn = daemon_state_fn if daemon_state_fn is not None else (
        lambda gsid: read_daemon_state(gsid)
    )
    self_terminated, reason, last_seq, last_type, last_ts = _scan_session_v3_events(
        event_log, goal_session_id,
    )
    daemon_state = state_fn(goal_session_id)

    verdict = (
        SessionLivenessVerdict.COMPLETED
        if self_terminated
        else _classify_daemon_state(daemon_state)
    )

    if (
        not self_terminated
        and roster_ids is not None
        and goal_session_id not in roster_ids
        and verdict in (SessionLivenessVerdict.RUNNING, SessionLivenessVerdict.STALLED)
    ):
        # 官方名单确凿不认这个会话, 但本地文件仍称活着 → 文件滞后, 信官方 (T-SELFHEAL-STALE-RELAY)。
        verdict = SessionLivenessVerdict.MISSING

    return SessionLiveness(
        goal_session_id=goal_session_id,
        verdict=verdict,
        daemon_state=daemon_state,
        self_reported_termination=self_terminated,
        termination_reason=reason,
        last_event_seq=last_seq,
        last_event_type=last_type,
        last_event_ts=last_ts,
    )


__all__ = [
    "SessionLiveness",
    "SessionLivenessVerdict",
    "assess_session_liveness",
    "read_daemon_state",
]
