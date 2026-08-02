"""Heartbeat 实现 (层①, 实现 contracts.heartbeat.Heartbeat)。

探活原语**注入**(环境缝, 接口先行): process_alive 用 os.kill(pid,0)(不投递信号=不唤醒)、transcript_fresh
用文件 mtime、has_canonical_product/task_run_aborted 用账本查、holds_live_lock 用 claim/锁图查、
emitting_tokens 用实时输出探测。这些真绑定是 Claude Code/env 层, 不在本模块; 本模块只组装 LivenessEvidence。
hermetic 可验(注入 fake 探针)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .contracts.heartbeat import Heartbeat, LivenessEvidence


@dataclass(frozen=True)
class ProbeBindings:
    """注入的探活原语。每个返回该信号; None = 探针失败/查不到 → 上层落 UNKNOWN 保守(绝不反推死)。"""

    is_emitting: Callable[[str], bool | None]        # 实时出 token
    process_alive: Callable[[str], bool | None]      # os.kill(pid,0) 不唤醒; None=查不到
    transcript_fresh: Callable[[str], bool | None]   # 末段 mtime 新鲜
    has_canonical_product: Callable[[str], bool]     # 账本 after_state.task_id 产物
    holds_live_lock: Callable[[str], bool]           # claim/锁图: 是否持活锁
    task_run_aborted: Callable[[str], bool]          # 账本: 本 run 显式 abort 终态
    last_error_class: Callable[[str], str | None]    # 末次错误分类(喂异常分类表)


class DefaultHeartbeat(Heartbeat):
    def __init__(self, bindings: ProbeBindings):
        self._b = bindings

    def probe(self, agent_id: str) -> LivenessEvidence:
        b = self._b
        return LivenessEvidence(
            emitting_tokens=b.is_emitting(agent_id),
            process_alive=b.process_alive(agent_id),
            transcript_fresh=b.transcript_fresh(agent_id),
            has_canonical_product=b.has_canonical_product(agent_id),
            holds_live_lock=b.holds_live_lock(agent_id),
            task_run_aborted=b.task_run_aborted(agent_id),
            last_error_class=b.last_error_class(agent_id),
        )
