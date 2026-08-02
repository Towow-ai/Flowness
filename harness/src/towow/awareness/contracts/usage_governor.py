"""Usage governor 接口契约 (层⑤ 真实环境 governor)。

设计依据: rc-layer-realenv-usage-governor / 30-layer45-container-governor.md / KEY-DECISIONS A2。

## 本质
它该一直转 (不是硬成本上限), 但尊重真实 API 窗口。盯 usage/并发槽/负载, 答"现在能不能派 /
该不该 back-off / 何时 resume"。本轮整个 workflow 撞服务端限流大面积挂 = 缺这层的活样本。

## 关键设计判断 (可证伪)
- **usage 源 = OTel metric `claude_code.token.usage`** (程序可读, 确定)。`/usage` 是人读命令、
  ccusage/JSONL 有 ~100x 精度坑, **都排除作自动源**。开放: "剩余 %" 口径 + 非交互会话走哪个池,
  需一次本机 OTel 标定 (低成本可逆, L5 前置)。塌于 Y: 口径标错 → 过早停 (浪费窗口) 或过晚停 (还撞墙)。
- **两层, 单向**: per-agent 撞限流 → 层① 复活 (个体); 系统反复撞 → governor 降全局派发率。
  governor 全局信号**压**个体复活 (复活前查 can_dispatch), 防"复活了立刻又撞墙"的活锁。
- **泛化资源 governor**: usage/并发槽/负载统一答; 但各资源时间尺度不同 (usage 5h 窗口 / 槽秒级 /
  负载分钟级), 内部按资源分别算 resume_at, 对外统一答 can_dispatch。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceReading:
    resource: str  # "usage_5h_window" | "concurrency_slots" | "machine_load" | ...
    used_fraction: float  # 0.0–1.0
    resume_at_epoch: float | None  # 该资源预计可恢复的绝对时刻 (None=立即/不适用)


class UsageGovernor(ABC):
    """资源感知节流。派发器 + 觉知层复活前都查它。"""

    @abstractmethod
    def read(self) -> list[ResourceReading]:
        """读各资源水位 (usage 走 OTel)。周期调 (如 ~10 分钟)。"""

    @abstractmethod
    def can_dispatch(self) -> bool:
        """现在能不能派新活。任一资源近满 (过阈值, 默认 80%) → False (在飞的让它干完)。"""

    @abstractmethod
    def should_backoff(self) -> bool:
        """是否该全局降派发率 (系统反复撞限流)。"""

    @abstractmethod
    def resume_at_epoch(self) -> float | None:
        """预计何时可恢复派发 (取各资源 resume_at 的最晚者)。定时器据此自动恢复。"""
