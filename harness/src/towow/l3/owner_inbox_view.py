"""R08 T-R08-5: 独立受限的明显前端收件箱 (owner inbox front-end)。

owner 2026-06-14: "收件箱你要有一个明显的前端界面"; "在哪看一定是一个独立的受限项"; "我能
点进去跟它发提示词"。当前 dashboard 无 escalation 板块、escalation_inbox view 是只读 md 流,
没有一个"打开就知道系统在等我几件事、每件已备好、能一步点进去"的 owner 专属台面。

本模块产 owner 专属收件箱的结构化呈现: 列出所有在等他的事 (每件已经过 prep, 带完整上下文
摘要), blocking 排前 / 卡最久顶上, 每件带可点进的 session handle (一步进入对话)。owner 来看时
组装 (来看时刷新, 不新增常驻进程, 贴合 owner 后台常驻红线敏感)。

"受限" = owner 专属控制面 (本模块产呈现, 访问控制由调用层施加)。具体前端渲染 (HTML/TUI) 消费
本结构。

concept: @concept:owner-escalation-closed-loop-visibility → @concept:owner-session-live-interaction@v1
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.l3.owner_signal import SignalUrgency

_STRICT = ConfigDict(strict=True, extra="forbid")


class InboxEntry(BaseModel):
    """收件箱一条 — 已 prep、可一步点进 session。"""

    model_config = _STRICT

    escalation_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)        # 点进去对话的那个 session
    essence: str = Field(min_length=1)           # 卡什么 (一句话, 已收敛)
    urgency: SignalUrgency
    waited_seconds: float = Field(ge=0)
    prep_summary: str = Field(min_length=1)      # 完整上下文摘要 (背景+选项+推荐+取舍), owner 一眼能判
    enter_session_handle: str = Field(min_length=1)  # 一步点进 session 的句柄

    @classmethod
    def assemble(
        cls,
        *,
        escalation_id: str,
        session_id: str,
        essence: str,
        urgency: SignalUrgency,
        waited_seconds: float,
        prep_summary: str,
    ) -> InboxEntry:
        return cls(
            escalation_id=escalation_id,
            session_id=session_id,
            essence=essence,
            urgency=urgency,
            waited_seconds=waited_seconds,
            prep_summary=prep_summary,
            enter_session_handle=f"session:{session_id}",  # 调用层据此 attach/发 prompt
        )


class OwnerInbox(BaseModel):
    """owner 专属收件箱呈现。"""

    model_config = _STRICT

    entries: list[InboxEntry] = Field(default_factory=list)  # 已排序: blocking 先, 卡最久顶上
    blocking_count: int = Field(ge=0)
    fyi_count: int = Field(ge=0)
    is_empty: bool


def build_inbox(entries: list[InboxEntry]) -> OwnerInbox:
    """组装 owner 收件箱: blocking 排前, 组内卡最久顶上 (owner 来看时刷新)。"""
    def sort_key(e: InboxEntry) -> tuple[int, float]:
        # blocking=0 排前; 组内按等待时长降序 (取负)
        return (0 if e.urgency is SignalUrgency.BLOCKING else 1, -e.waited_seconds)

    ordered = sorted(entries, key=sort_key)
    blocking = sum(1 for e in ordered if e.urgency is SignalUrgency.BLOCKING)
    return OwnerInbox(
        entries=ordered,
        blocking_count=blocking,
        fyi_count=len(ordered) - blocking,
        is_empty=len(ordered) == 0,
    )


__all__ = ["InboxEntry", "OwnerInbox", "build_inbox"]
