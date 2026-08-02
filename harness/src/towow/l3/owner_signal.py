"""R08 T-R08-4: 可见升级提示 (visible-escalation-signal) — 非 CLI 的明显信号。

owner 2026-06-14: "我要有一个显示的提示, 而不只是在 CLI 里面提示我。有一个明显的提示告诉我
说, 哪个 session 在等我输入。" 当前只有 pull-on-prompt (要 owner 主动发 prompt 才看见),
打断不了不在场的 owner。

本模块产**可见提示的结构化载荷 + headline 渲染**: 哪条 session 在等他、卡什么 (一句话本质)、
急不急、等了多久、当前在等几件。只为 blocking 级点亮 (lit=True); FYI 级静默入收件箱 (不点亮)。
具体外部投递通道 (桌面/菜单栏角标/不触后台常驻红线) 是 INF-R08-10 工程探查项, 与本载荷正交——
本模块定"提示该长什么样、何时点亮", 通道层消费这个载荷去真触达。

concept: @concept:visible-escalation-signal@v1 → @concept:owner-session-live-interaction@v1
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid")


class SignalUrgency(StrEnum):
    BLOCKING = "blocking"   # 有任务死等 owner → 点亮可见提示
    FYI = "fyi"             # 仅知会, 无人死等 → 静默入收件箱, 不点亮


class WaitingItem(BaseModel):
    """一件在等 owner 的事 (可见提示的最小可辨识单元)。"""

    model_config = _STRICT

    session_id: str = Field(min_length=1)   # owner 可点进的那个 session
    escalation_id: str = Field(min_length=1)
    essence: str = Field(min_length=1)      # 卡什么 (一句话本质, 已经过 prep 收敛)
    urgency: SignalUrgency
    waited_seconds: float = Field(ge=0)


class VisibleSignal(BaseModel):
    """可见提示载荷 — 通道层据此渲染常驻/桌面提示。"""

    model_config = _STRICT

    lit: bool                               # 是否点亮可见提示 (有 blocking 才点亮)
    headline: str                           # 明显信号一句话: "有 N 件在等你, 最急: <哪个session> <卡什么>"
    blocking_items: list[WaitingItem] = Field(default_factory=list)
    fyi_count: int = Field(ge=0, default=0)  # 静默入箱的 FYI 数 (owner 来看时才呈现)
    total_waiting: int = Field(ge=0)


def build_signal(items: list[WaitingItem]) -> VisibleSignal:
    """从在等 owner 的事构建可见提示: 只 blocking 点亮, 按急/等待时长排, FYI 静默计数。"""
    blocking = [i for i in items if i.urgency is SignalUrgency.BLOCKING]
    fyi = [i for i in items if i.urgency is SignalUrgency.FYI]
    # blocking 内按等待时长降序 (卡最久顶上来)
    blocking.sort(key=lambda i: i.waited_seconds, reverse=True)

    lit = len(blocking) > 0
    if lit:
        top = blocking[0]
        headline = (
            f"⚠ 有 {len(blocking)} 件在等你拍板 — 最急: session {top.session_id} · {top.essence}"
        )
    elif fyi:
        headline = f"{len(fyi)} 条知会在收件箱等你来看 (无人死等)"
    else:
        headline = "没有在等你的事"

    return VisibleSignal(
        lit=lit,
        headline=headline,
        blocking_items=blocking,
        fyi_count=len(fyi),
        total_waiting=len(items),
    )


__all__ = ["SignalUrgency", "WaitingItem", "VisibleSignal", "build_signal"]
