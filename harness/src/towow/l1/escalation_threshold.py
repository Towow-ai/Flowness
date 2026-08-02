"""RUN-080 漏洞3 — 升级轮次阈值定量 (technical_blocker 自动 retry + awaiting_nature timeout).

# spec source:
#   harness/docs/SPEC-PATCH-CONCURRENCY-RUN080.md §3 (升级轮次阈值定量)
#   harness/docs/SPEC-GAP-CONCURRENCY-FINDINGS.md 漏洞3
#   M-1.6 §7.2 novelty gate (已有, T-L1-77 → src/towow/l1/novelty_gate.py)
#   M-2.3 §6.1-6.2 escalation lifecycle

为什么存在:
  owner 要"系统全自动、只有'升级'概念, 多轮后真正需 owner 拍板的才升级"。spec 此前说"多轮"
  没定几轮。其中**振荡升级**(连续 N 轮 fix 无 novelty → 升级)已由 novelty_gate 量化(阈值 4,
  见 DEFAULT_NOVELTY_GATE_THRESHOLD)。本模块补两个**仍未量化**的轴, 不与 novelty_gate 重叠:

  ① technical_blocker 自动 retry 上限 —— "owner 决策前系统能自动做什么": 一个 technical_blocker
     (如临时环境/网络/锁竞争失败)系统自动重试 N 次再升级, 不一遇阻就惊动 owner。owner 授权
     RUN-080 定 N=3(fix≥3 自动升级)。这是"自动恢复"轴, 区别于 novelty_gate 的"振荡"轴
     (后者看的是改同一处换说法的死循环)。
  ② awaiting_nature 超时 —— escalation 升到 owner 后不能无限静默挂着没人知道。超过阈值即
     标记为"超时待提醒"供 dashboard surface。注意: **不自动替 owner 决策**(只有 owner 能裁
     nature-bound escalation), 超时只用于提醒/可见性, 不改 escalation 结论。
"""

from __future__ import annotations

# ① technical_blocker 自动 retry 上限 (owner 授权 RUN-080: fix≥3 自动升级)。
#    达到这个尝试次数仍未恢复 → 不再自动重试, 产 EscalationRaised(technical_blocker) 给 owner。
MAX_TECHNICAL_BLOCKER_AUTO_RETRIES = 3

# ② awaiting_nature 超时 (秒)。escalation 进入 awaiting_nature 后超过此时长仍无 owner 响应 →
#    标记"超时待提醒"。24h: 给 owner 充足响应窗口, 又不至于无限静默挂死。
AWAITING_NATURE_TIMEOUT_S = 24 * 60 * 60


def should_escalate_technical_blocker(
    retry_count: int,
    *,
    threshold: int = MAX_TECHNICAL_BLOCKER_AUTO_RETRIES,
) -> bool:
    """一个 technical_blocker 自动重试 retry_count 次后是否该升级给 owner。

    retry_count = 已尝试(并失败)的自动恢复次数。达到 threshold(默认 3)即 True:
    系统已尽力自动恢复, 该升级而非继续无限重试。

    raise ValueError on threshold < 1 / retry_count < 0 (调用方应传合法计数)。
    """
    if threshold < 1:
        raise ValueError(f"threshold must be ≥ 1, got {threshold}")
    if retry_count < 0:
        raise ValueError(f"retry_count must be ≥ 0, got {retry_count}")
    return retry_count >= threshold


def is_awaiting_nature_timed_out(
    raised_at_unix: float,
    now_unix: float,
    *,
    timeout_s: float = AWAITING_NATURE_TIMEOUT_S,
) -> bool:
    """awaiting_nature 的 escalation 是否已超时(供 dashboard 提醒, 不自动决策)。

    raised_at_unix = escalation 进入 awaiting_nature 的时刻; now_unix = 当前时刻。
    (now - raised) > timeout_s → True。仅用于可见性/提醒, **不改 escalation 结论**
    (nature-bound escalation 只有 owner 能裁)。
    """
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be > 0, got {timeout_s}")
    return (now_unix - raised_at_unix) > timeout_s


__all__ = [
    "AWAITING_NATURE_TIMEOUT_S",
    "MAX_TECHNICAL_BLOCKER_AUTO_RETRIES",
    "is_awaiting_nature_timed_out",
    "should_escalate_technical_blocker",
]
