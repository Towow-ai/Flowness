"""判活/心跳接口契约 (层①)。

设计依据: rc-liveness-realtime-evidence-primary@v1 / 10-layer1-awareness.md §判活 / 故障检测 FLP+phi-accrual。

## 本质
对"一个 agent 此刻活没活", 最接近 ground truth 的是**实时正向存活证据** (正出 token / 活进程
os.kill(pid,0) / claude logs spinner), 不是滞后代理 (transcript 新鲜度会因泊车而冷、30 天清、
长推理不写盘; canonical 产物只对 DONE 权威)。判活 = 实时证据为主 + 滞后信号佐证 + 多信号互相打脸交叉。

## 关键设计判断 (可证伪)
- **分层偏序, 不是投票**: 每类信号只在它"不假阳"的方向当最高权威 (canonical 产物→判 DONE 永不假阳;
  pid 没了→判 DEAD 强; transcript 新鲜→判 ALIVE; 锁→不可收割)。对因为: 投票把会撒谎的弱信号
  (daemon state 词, 死了 10 天还显 blocked) 和不可伪造的强信号拉平权。塌于 Y: 若某信号两向都假阳
  (state 词), 它只作一票、永不单用。
- **DEAD 必要条件 = process_alive==False 或 task_run_aborted==True** (正向死亡证据); 纯"冷+无产物"
  降级 UNKNOWN (持锁则 PARKED)。
- **探针失败 → UNKNOWN, 绝不反推 DEAD** (隔离 worktree/远程/权限查不到时)。这是硬约束, 防新误杀。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LivenessEvidence:
    """一次探活的多源证据。各字段 None = 该源未取到 (→ 偏 UNKNOWN, 不偏 DEAD)。"""

    emitting_tokens: bool | None  # 实时: 正在出 token (最强活证据)
    process_alive: bool | None  # 实时: os.kill(pid,0) 不投递信号; None=查不到→UNKNOWN
    transcript_fresh: bool | None  # 滞后: 末段时间戳新鲜 (佐证)
    has_canonical_product: bool  # canonical 产物存在 (只对 DONE 权威)
    holds_live_lock: bool  # 持锁 = 认领着工作 = 强活信号 (不可收割)
    task_run_aborted: bool  # 本 run 有显式 abort 终态 (正向死亡证据之一)
    last_error_class: str | None  # 末次错误分类 (瞬态 vs 终态, 喂异常分类表)


class Heartbeat(ABC):
    """判活原语。被 per-form adapter 的 detect_state 调用喂信号。"""

    @abstractmethod
    def probe(self, agent_id: str) -> LivenessEvidence:
        """采集多源存活证据 (实时为主)。不唤醒目标。合并读 base+hot 事件。"""
