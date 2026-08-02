"""per-form 适配器接口契约 (层① 通用觉知层)。

设计依据: rc-universal-awareness-layer / rc-agent-forms-and-uniform-adapter /
rc-liveness-realtime-evidence-primary@v1 / 10-layer1-awareness.md。

## 本质
统一状态机 + per-form adapter: 核心循环只认接口、不认形态; 新形态加一个 adapter 不改核心。
层① 真相 = 焊两套半成品 (session_vitality 判得准不会救 + subprocess_heartbeat 会救没接线) +
接上"判出 PARKED 后真去复活"那根断线 (reconcile:3176 现在是 continue)。

## 关键设计判断 (可证伪)
- **parked ≠ dead (铁律)**: parked (限流/网络抖/模型不可用/休眠/持锁) → 复活 (给同一会话发"继续"),
  绝不重派。误判活为死 = 杀+起孪生 = 最坏腐蚀 (owner 实战: 卡死被删了一直重发到分不清谁是谁)。
- **DEAD 只由正向死亡证据判** (pid 确没了 os.kill / 显式 abort), 沉默 (transcript 冷+无产物) →
  UNKNOWN 保守不收割。对因为: FLP 证慢/死判不准, 只能往代价小处错 (偏判活)。塌于 Y: 真死但持锁的会话
  会被多留一会儿 → 必须配 claim 的 claimant 心跳回收 (靠"进程死且超时", 不靠"会话冷")。
- **复活 ≠ 重派, 且物理互斥**: 复活幂等登记 + 复活预算 (OTP max-restart-intensity, 默认 N=3)
  耗尽才降 DEAD; 认领戳唯一抢占保证复活与重派不同时发生 (防孪生)。
- **探针失败恒保守**: process_alive 探针查不到 (隔离/远程) → UNKNOWN, 绝不反推 DEAD (防新误杀)。
- **新形态若本质不可复活** (如无状态纯函数 fork, 只能重跑): revive 显式返回不可复活, 核心走重派路径,
  不假装能复活。接口预留这个表达。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class AgentForm(str, Enum):
    BG_SESSION = "bg_session"
    FORK = "fork"
    SHELL = "shell"
    SUB_AGENT = "sub_agent"
    WORKFLOW = "workflow"


class Vitality(str, Enum):
    """统一裁决态。UNKNOWN 是一等态: 判不准时规定动作 = 保守等下一轮, 不赌。"""

    LAUNCHING = "launching"
    WORKING = "working"
    PARKED = "parked"  # 瞬态阻塞但活 → 复活
    STUCK = "stuck"  # 等输入/等 owner → escalate
    DONE = "done"  # 收割 + 触发下游
    DEAD = "dead"  # 正向死亡证据确认 → 才重派
    UNKNOWN = "unknown"  # 判不准 → 保守不收割
    RUNAWAY = "runaway"  # → 熔断


@dataclass(frozen=True)
class AgentState:
    verdict: Vitality
    reason: str
    revivable: bool  # 本形态此刻能否复活 (无状态纯函数=False, 核心走重派)
    holds_locks: tuple[str, ...] = ()  # 持有的锁 id (强活信号)
    revive_count: int = 0  # 已复活次数 (幂等登记, 对比复活预算)


@dataclass(frozen=True)
class ReviveResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class Lineage:
    parent: str | None
    children: tuple[str, ...]
    siblings: tuple[str, ...]


class AgentFormAdapter(ABC):
    """任何 agent 形态实现同一接口。核心循环只调这四个方法, 不认形态差异。"""

    form: AgentForm

    @abstractmethod
    def detect_state(self, agent_id: str) -> AgentState:
        """判态: 多源信号 (实时存活证据为主 + canonical 产物 + 锁 + 滞后信号佐证), 分层偏序裁决。
        合并读 base+hot 事件 (只读一段=假阴)。拿不准 → UNKNOWN。"""

    @abstractmethod
    def revive(self, agent_id: str) -> ReviveResult:
        """复活: 对 PARKED 给同一会话发"继续", 不起新的。必须先核复活预算未耗尽 + 认领戳未易主。
        本形态不可复活 → 返回 ok=False, detail 说明 (核心据此走重派)。"""

    @abstractmethod
    def harvest(self, agent_id: str) -> dict:
        """收割: 取它的产物 (canonical 事件 / drop-file / stdout / 返回值 / 各步产物, 按形态)。"""

    @abstractmethod
    def lineage(self, agent_id: str) -> Lineage:
        """血缘: 从图协议读 (spawned/sibling 边)。血缘真兑现靠 spawn 时 emit SessionSpawned。"""
