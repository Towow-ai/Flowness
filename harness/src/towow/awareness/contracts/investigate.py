"""investigate 接口契约 (层①, 吃图协议)。

设计依据: rc-universal-awareness-layer / 10-layer1-awareness.md §investigate / KEY-DECISIONS A5
(可下沉 Claude Code 工具层)。

## 本质
一个顶层"调查"工具: 任何 agent / owner 想查另一个 agent 的处境, 都走这一个工具、一个过程
(不再各处各写 vitality 调用 —— 现状 assess_vitality 被 4+ 处各拼各的)。它在图协议上一跳拿
lineage + 占的锁, 输出机器可读 + 一个"建议动作", 调用方据此行动。

## 关键设计判断 (可证伪)
- **只读、不唤醒泊车会话**: 探活只用 os.kill(pid, 0) 这类不投递信号的方式, 绝不 attach/reply。
  对因为: read-only 保证调它本身零副作用, 可被任何 agent/owner 高频安全调 (含哨兵、调试)。
  塌于 Y: 若某探活方式会唤醒目标进程, "只读"垮 —— 必须用不唤醒的探测。
- **输出带 recommended_action (复活/重派/escalate)**, 但**自己不执行** (诊断 ≠ 执行, 审计独立性;
  动作归编排器单点, 防两个 investigate 同时动同一 agent)。
- **强依赖图协议血缘**: lineage/holds_locks 从图上读; 若 session/lock 没上图 (parent 现 ~0%),
  它给的 lineage 就是错的。所以 investigate 的正确性是图协议 (TG.4) + per-form (lineage) 的下游验收点。
- **可下沉 CC 工具层** (owner 已允许): 感知/心跳下沉换通用稳; 治理内核 (claim/fencing/reconcile)
  留 towow 后端。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .per_form_adapter import Lineage, Vitality


@dataclass(frozen=True)
class InvestigateResult:
    agent_id: str
    state: Vitality
    last_signal: str  # 末次决定性信号 (出 token / pid / 末次错误类 / 产物)
    work_product_so_far: dict  # 它干到哪 (canonical after_state 摘要)
    lineage: Lineage
    holds_locks: tuple[str, ...]
    raised_escalations: tuple[str, ...]  # 它提的待决 escalation id (owner 决策可追)
    recommended_action: str  # "revive" | "redispatch" | "escalate" | "harvest" | "wait" | "circuit_break"
    rationale: str  # 为何这么建议 (证据可被调用方/哨兵直接复核, 不靠"信它")


class Investigate(ABC):
    """统一调查原语。谁查谁都走它。"""

    @abstractmethod
    def investigate(self, agent_id: str) -> InvestigateResult:
        """只读查一个 agent 的完整处境 + 建议动作。不唤醒、不执行动作。"""
