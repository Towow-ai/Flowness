"""R08 T-R08-7: 答复硬闭环回流 (escalation-answer-reflow) — 落地 0 闭环的命门。

实证缺口 (R08 prep-research + escalation-answer-reflow@v1 概念): escalation_subscribe 已有
等待侧 (bg session 轮询 responds_to_escalation_event_id), 但**答复内容从未被投递进等待任务** —
历史 0 次真闭环。owner 答复落了账, 那个等他的活却收不到、继续干不下去。

本模块建**回流逻辑 + 应用留痕结构**: 把 owner 的答复 (NJ, 带 responds_to_escalation_event_id)
匹配到那个死等它的任务, 产出 AnswerApplication (= 该 emit 的 EscalationAnswerApplied 留痕内容),
并判定被卡链可 resume。匹配不到 → 返回 NO_WAITING_TASK, **由调用层路由到死信收件箱**
(`dead_letter_inbox`, 接线层做), 本模块只判定"无人死等"、不自己 enqueue, 但绝不静默丢。

硬闭环 (不变量 answer-hard-closure): 只有"答复真匹配到等待任务 + 应用 + resume"才算闭环;
owner 答复仅落账 (NJ emit) 不算。本模块产 ReflowOutcome.APPLIED 即闭环判据成立的证据。

🔴 红线 (v1 契约, 下方 v1 API 仍适用): 本模块这部分纯计算, 不 emit event / 不改 canonical /
不启 daemon。真正的 EscalationAnswerApplied emit + 唤醒等待 session 由 orchestrator 接线消费
本模块的 AnswerApplication (daemon-gated)。

⚠️ v1 已知缺陷 (2026-07 owner 实战坐实, C-2 修复): `reflow_answer` 一旦匹配到 waiting task 就
无条件断言 `resumed=True`, 从不核实那个目标真收到没有。对"goal-bg 会话级"等待方 (waiting_on 是
一个真实在跑的交互式会话、不是可重派的 exec task), 这个断言是空心的: 历史上答复落了账、
EscalationAnswerApplied 也 emit 了, 但会话卡在 escalation_subscribe 的 600s wait-resume pull
里, LLM 会话撑不住超时 sys.exit(124) silent-death, 答复从未真送达 (飞书回复丢失根因)。
`reflow_answer`/`OwnerAnswer`/`WaitingTask`/`ReflowOutcome`/`AnswerApplication` **保留不动**
(仍是 `stranded_batch_disposition.resolve_escalation` 等纯匹配消费方的合法依赖, 那些场景本就
只需要"答复匹配到了没", 不涉及"送不送得进一个活会话"这层)。v3 API (见本文件下方) 是专治
"goal-bg 主路径真送达"这条命门的新增, 不替换 v1, 只是补 v1 从没覆盖到的这一层。

concept: @concept:escalation-answer-reflow@v1 (v1 API) / @concept:escalation-answer-reflow@v3
(下方 v3 API — 共识未冻结, 见 harness/docs/RESUME-escalation-answer-reflow-impl.md; 按裁决跳过
freeze 直接落地代码, 正规 freeze 留给服务器上的正规流程补办)
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from types import ModuleType

    from towow.l0.event_log import EventLog
    from towow.l2.session_vitality import SessionVitality

_STRICT = ConfigDict(strict=True, extra="forbid")


class ReflowOutcome(StrEnum):
    APPLIED = "applied"                  # 答复真送回等待任务并被消费 → 闭环
    NO_WAITING_TASK = "no_waiting_task"  # 没有任务死等它 → 调用层路由到死信收件箱 (不静默丢)


class OwnerAnswer(BaseModel):
    """owner 的答复 (一条 NJ, 带 responds_to_escalation_event_id)。"""

    model_config = _STRICT

    nj_event_id: str = Field(min_length=1)
    escalation_event_id: str = Field(min_length=1)  # 它在回应哪条 escalation
    answer_text: str = Field(min_length=1)


class WaitingTask(BaseModel):
    """一个死等某 escalation 答复的任务/会话。"""

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    waiting_on_escalation_event_id: str = Field(min_length=1)


class AnswerApplication(BaseModel):
    """应用留痕 = 该 emit 的 EscalationAnswerApplied 内容 (证明答复被消费, 非只落账)。"""

    model_config = _STRICT

    escalation_event_id: str
    nj_event_id: str
    applied_to_task_id: str
    applied_to_session_id: str
    answer_text: str
    resumed: bool  # 被卡链解冻续跑


def reflow_answer(
    answer: OwnerAnswer,
    waiting_tasks: list[WaitingTask],
) -> tuple[ReflowOutcome, AnswerApplication | None]:
    """把 owner 答复匹配到死等它的任务并产应用留痕 (硬闭环)。

    - 找 waiting_on_escalation_event_id == answer.escalation_event_id 的任务。
    - 找到 → APPLIED + AnswerApplication(resumed=True): 答复真投递进该任务, 被卡链可 resume。
    - 找不到 → NO_WAITING_TASK + None: 无人死等; 调用层据此路由到死信收件箱 (不静默丢)。

    硬闭环判据: 返回 APPLIED 才算这条 escalation 真闭环了; 上游 owner 答复仅落账不调用本回流,
    则等待任务永远收不到答案 (正是历史 0 闭环的样子)。
    """
    for t in waiting_tasks:
        if t.waiting_on_escalation_event_id == answer.escalation_event_id:
            return ReflowOutcome.APPLIED, AnswerApplication(
                escalation_event_id=answer.escalation_event_id,
                nj_event_id=answer.nj_event_id,
                applied_to_task_id=t.task_id,
                applied_to_session_id=t.session_id,
                answer_text=answer.answer_text,
                resumed=True,
            )
    return ReflowOutcome.NO_WAITING_TASK, None


# ════════════════════════════════════════════════════════════════════════════════
#  v3 API — escalation-answer-reflow@v3 (C-2: goal-bg 主路径真送达)
#
#  scope: 只管带 goal_session_id 的 goal-bg 会话级等待方 (orchestrator._resolve_reflow_waiting_
#  party 解析出 party.task_id is None 的那条分支 —— 真正在跑、无 exec task 映射的交互式会话)。
#  fix/exec 链的 exec-task 重派(party.task_id 非空)不归这里管, 那条链的 resumed=True 已经诚实:
#  try_spawn_for_decision 保证下次重派该 task_id 时消费 park 的答案, 是另一条已验证可靠的送达
#  机制 (见 orchestrator.py _write_escalation_answer_injection / read_escalation_answer_injection)。
#
#  交付步按 vitality (towow.l2.session_vitality, 与 `./tw vitality --session <id>` 同一套接线,
#  不额外焊接 process_alive_fn/holds_lock_fn —— 那是 CLI 单会话查询本身的既有口径, 不是本模块
#  抄近道) 判活分两条真实路径:
#    parked_resumable (还活着只是泊车) → 复用 attach-pty-inject-live-session@v1 (wake-watcher
#      现成机制, 动态装载 .claude/wake-watcher/wake_watcher.py, 与既有集成测试
#      tests/integration/test_wake_watcher_resilience.py 同一装载方式) 把答复 push 进活会话续跑,
#      读 transcript 核实 user turn 真落地才算 APPLIED (push_fn 内建这层核实, 本模块只信它的
#      布尔返回, 不再自己假装"没抛异常就是成功")。
#    dead (真死) → ANSWER_PARKED_STRANDED, 调用方 (orchestrator.py) 负责持久 park 答案 + 浮出
#      死信搁浅单 + emit 专属终态事件 (不是重派 —— 交互式 goal-bg 会话无 exec 重派句柄, 见文件
#      头部 docstring "为何 dead 不重派")。
#  其余 verdict (alive_working / stuck_waiting / done / unknown, 或 parked_resumable 但查不到
#  可 attach 的 short_id, 或 push 未核实到落地) → UNRESOLVED —— 不是"确定的失败", 是"这轮还判不
#  出该 push 还是该搁浅", 调用方走有界重试, 耗尽了才落 SAGA 死信 (镜像 concept state_machine 的
#  VitalityUnknownDeadLettered 转移)。绝不在此处以"查不清"为由假装任何一种确定结局。
# ════════════════════════════════════════════════════════════════════════════════


class GoalReflowOutcome(StrEnum):
    """v3 交付结果 — 对应 concept state_machine 里 target_resolved 状态的三条出边。"""

    APPLIED = "applied"                                # push 成功且核实真落地 → 硬闭环
    ANSWER_PARKED_STRANDED = "answer_parked_stranded"   # 会话真死, 答案需持久 park + 浮出死信
    UNRESOLVED = "unresolved"                           # 尚非确定裁决 → 调用方有界重试


@dataclass(frozen=True)
class GoalSessionTarget:
    """已解析出的 goal-bg 会话目标 (`resolve_goal_session_target` 的产物)。"""

    goal_session_id: str
    short_id: str | None          # `claude attach <短id>` 用; 查不到活跃条目时 None (无法 push)
    full_session_id: str | None   # transcript 匹配用的完整 sessionId; 查不到条目时退回 goal_session_id
    transcript_path: str | None
    vitality: SessionVitality


def resolve_goal_session_target(
    event_log: EventLog,
    goal_session_id: str,
    *,
    list_sessions_fn: Callable[..., list[dict[str, object]]] | None = None,
) -> GoalSessionTarget:
    """判活 (与 CLI `towow vitality --session <id>` 单会话分支同一套接线)。

    f-goal-session-identity-blind-shared-lock-misterminate-20260718 ①: goal_session_id 曾经
    恒等于 bg_session_id (`claude --bg` spawn 之后才分配), 该 fix 之后两者对 review/consensus/
    fix/planning 路径的会话已解耦 (goal_session_id 是 spawn 前预生成的领域身份)。判活/attach
    (`claude agents --json` 的 id/sessionId 字段) 认的是真实进程 id, 不是领域身份 —— 先经
    `orchestrator._resolve_bg_session_id` 反查一次 (从对应 GoalSessionStarted.bg_session_id 取,
    execution fan-out 等未解耦路径 / 该字段缺失时退化返回 goal_session_id 自身, 与本函数改动前
    完全一致的行为, 零回归)。函数签名/调用方不动, 只在内部多一次反查。
    """
    from towow.l2.orchestrator import _resolve_bg_session_id
    from towow.l2.session_vitality import assess_vitality, build_work_product_map, list_sessions

    bg_session_id = _resolve_bg_session_id(event_log, goal_session_id)
    sessions = (list_sessions_fn or list_sessions)(include_done=True)
    entry = next(
        (
            s for s in sessions
            if isinstance(s, dict)
            and (s.get("id") == bg_session_id or s.get("sessionId") == bg_session_id)
        ),
        None,
    )
    wp_map = build_work_product_map(event_log)
    _entry_sid = (entry or {}).get("sessionId")
    tx_id: str = _entry_sid if isinstance(_entry_sid, str) and _entry_sid else bg_session_id
    vitality = assess_vitality(
        bg_session_id,
        task_id=None,
        agents_entry=entry,
        work_product_fn=lambda k: wp_map.get(k, (False, False, False))[:2],
        task_run_aborted=wp_map.get(tx_id, (False, False, False))[2],
    )
    _entry_id = (entry or {}).get("id")
    short_id = _entry_id if isinstance(_entry_id, str) and _entry_id else None
    return GoalSessionTarget(
        goal_session_id=goal_session_id,
        short_id=short_id,
        full_session_id=tx_id,
        transcript_path=vitality.transcript_path,
        vitality=vitality,
    )


_WAKE_WATCHER_MODULE: ModuleType | None = None


def _find_wake_watcher_dir() -> Path | None:
    """从本文件向上找 `.claude/wake-watcher` (同一 repo, harness/ 子树之外, 独立 launchd 常驻
    脚本) —— 与 tests/integration/test_wake_watcher_resilience.py `_find_wake_watcher_dir` 同一
    定位方式 (established pattern, 非本模块首创): 不假设固定层级深度, worktree/主树/未来目录
    搬迁都不因层级数假设而碎; sparse checkout 不含它时 (cand 不存在) 返回 None, 调用方 fail-
    closed (push_fn 返回 False, 绝不假装能 push)。"""
    for parent in Path(__file__).resolve().parents:
        cand = parent / ".claude" / "wake-watcher"
        if (cand / "classify.py").is_file() and (cand / "wake_watcher.py").is_file():
            return cand
    return None


def _load_wake_watcher_module() -> ModuleType | None:
    """动态装载 wake_watcher.py 复用其 `_pty_attach_inject` (概念 attach-pty-inject-live-
    session@v1 的唯一实现)。找不到目录 → None (调用方 fail-closed)。模块级缓存: 同进程内只装
    一次 (exec_module 本身无副作用 — `if __name__ == "__main__"` 守卫, 见该文件末尾, spec 装载
    时 __name__ 不等于 "__main__", 不会误跑它的守护循环)。"""
    global _WAKE_WATCHER_MODULE
    if _WAKE_WATCHER_MODULE is not None:
        return _WAKE_WATCHER_MODULE
    ww_dir = _find_wake_watcher_dir()
    if ww_dir is None:
        return None
    if str(ww_dir) not in sys.path:
        sys.path.insert(0, str(ww_dir))
    spec = importlib.util.spec_from_file_location("towow_ww_wake_watcher", ww_dir / "wake_watcher.py")
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _WAKE_WATCHER_MODULE = module
    return module


def _default_push_fn(
    short_id: str, message: str, session_id: str | None, transcript_path: str | None,
) -> bool:
    """真实推送: 委托 wake_watcher._pty_attach_inject (attach+PTY 注入 + transcript 核实一体)。"""
    ww = _load_wake_watcher_module()
    if ww is None:
        return False
    return bool(
        ww._pty_attach_inject(short_id, message, session_id=session_id, transcript_path=transcript_path),
    )


def render_owner_answer_push_message(
    *, escalation_event_id: str, nj_event_id: str, answer_text: str,
) -> str:
    """渲染成单行 —— PTY 注入的经验有效包络是单行 (wake_watcher 自己的 WAKE_MESSAGE 常量本身
    就是单行; 多行输入是否会被 Claude Code 输入框当续行/特殊处理未经验证)。owner 答案可能带
    换行 (飞书回复原文), 一律拍平, 不引入未经验证的多行注入路径。"""
    flat = " ".join(answer_text.split())
    return (
        f"owner 已对你的升级 (escalation {escalation_event_id}) 拍板 "
        f"(NatureJudgmentCaptured {nj_event_id})：「{flat}」"
        "。这是 owner 显式决策, 直接据此推进; 若答复未覆盖你真正卡的点, 再 escalate 一次新的。"
    )


def deliver_goal_answer(
    target: GoalSessionTarget,
    *,
    escalation_event_id: str,
    nj_event_id: str,
    answer_text: str,
    push_fn: Callable[[str, str, str | None, str | None], bool] | None = None,
) -> GoalReflowOutcome:
    """按 vitality 分流交付 (v3 交付步)。

    parked_resumable + 有 short_id → 真 push (push_fn 自带 transcript 核实, 见
    attach-pty-inject-live-session@v1); push_fn 返回 True 才算 APPLIED, 不核实到落地就是
    UNRESOLVED (绝不"没抛异常就当成功")。
    dead → ANSWER_PARKED_STRANDED (调用方负责持久 park + dead-letter enqueue + emit, 本函数不
    碰 canonical / dead_letter_inbox)。
    其余 → UNRESOLVED, 调用方走有界重试。
    """
    from towow.l2.session_vitality import VitalityVerdict

    verdict = target.vitality.verdict
    if verdict == VitalityVerdict.PARKED_RESUMABLE:
        if not target.short_id:
            return GoalReflowOutcome.UNRESOLVED
        message = render_owner_answer_push_message(
            escalation_event_id=escalation_event_id, nj_event_id=nj_event_id, answer_text=answer_text,
        )
        pusher = push_fn or _default_push_fn
        ok = pusher(target.short_id, message, target.full_session_id, target.transcript_path)
        return GoalReflowOutcome.APPLIED if ok else GoalReflowOutcome.UNRESOLVED
    if verdict == VitalityVerdict.DEAD:
        return GoalReflowOutcome.ANSWER_PARKED_STRANDED
    return GoalReflowOutcome.UNRESOLVED


__all__ = [
    "AnswerApplication",
    "GoalReflowOutcome",
    "GoalSessionTarget",
    "OwnerAnswer",
    "ReflowOutcome",
    "WaitingTask",
    "deliver_goal_answer",
    "reflow_answer",
    "render_owner_answer_push_message",
    "resolve_goal_session_target",
]
