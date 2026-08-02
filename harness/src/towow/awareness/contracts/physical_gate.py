"""物理闸位接口契约 (层④ 容器/受控放行)。

设计依据: solution-1 / rc-v01-physical-write-guard-still-convention / 30-layer45-container-governor.md
/ KEY-DECISIONS A3 (owner 定口径)。

## 本质
无人值守跑时, 不可逆动作 (生产部署/对外发布/资金/删数据/公开承诺) 必须有**物理 fail-closed 拦**,
即使 agent 没问也拦住。现状是半残: 判定脚本在, 但 PreToolUse matcher 不含 mcp__ → 调外部 SaaS
(Gmail 等) 根本不触发门 = "对外发布已物理封口"是假的。

## 关键设计判断 (owner 2026-06-20 定, 可证伪)
- **物理闸 = 路由层 × 判定层, 缺一为零**: matcher 必须含 `mcp__.*` (让高危工具家族真进 hook) ×
  字样级 fail-closed 白名单。只补判定不修 matcher = 空转。
- **owner 定的具体口径**: 上线部署 = 经"严格部署流程"即可自动放行 (不进必停类); 发邮件/对外发帖 =
  不自动化 (根本不做, 硬拦); 资金变动 = 必停、必人工; 删数据 = 归对外承诺类、硬拦。
- **从严、宁误拦** (owner 拍): 不可逆动作误拦 (agent 换路/上报) 代价 << 漏拦 (不可逆已发生)。
  对因为: 字样级匹配 (不解析语义) 必有绕过面 (引号/变量/通配展开 hook 看不到)。塌于 Y: 字样划太宽
  把日常也算高危 → autopilot 频撞 admin-bypass → owner 烦了摘门, 门形同虚设。所以硬信号必须窄到"真不可逆"。
- **统一 admin-bypass 受控出口** + 物理拒 best-effort emit canonical (IrreversibleActionBlocked)
  上看板, 不静默 exit。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class HighRiskClass(str, Enum):
    PROD_DEPLOY = "prod_deploy"  # 经严格部署流程可放行
    OUTBOUND_PUBLISH = "outbound_publish"  # 发邮件/发帖 = 不自动化、硬拦
    FUNDS = "funds"  # 必停、必人工
    DELETE_DATA = "delete_data"  # 归对外承诺、硬拦
    PUBLIC_COMMITMENT = "public_commitment"  # 改公开承诺文件、硬拦


class GateDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"  # fail-closed 物理拦
    ALLOW_VIA_PROCESS = "allow_via_process"  # 经严格部署流程 (CI 门+canary)
    ALLOW_VIA_BYPASS = "allow_via_bypass"  # owner 一次性 admin-bypass.flag


@dataclass(frozen=True)
class ActionContext:
    tool_name: str  # 含 mcp__* (matcher 必须路由它们进来)
    command_or_payload: str
    write_set: tuple[str, ...] = ()  # 触及的文件/端点 (用于硬信号识别)


@dataclass(frozen=True)
class GateVerdict:
    decision: GateDecision
    risk_class: HighRiskClass | None
    reason: str  # 多行教育性说明 (拒时给 agent)
    emit_blocked_event: bool  # 是否 best-effort emit IrreversibleActionBlocked 上看板


class PhysicalGate(ABC):
    """不可逆动作物理闸。实现侧 = PreToolUse hook (Claude Code 调任何工具前的唯一物理卡点)。"""

    @abstractmethod
    def classify(self, action: ActionContext) -> HighRiskClass | None:
        """字样级识别高危类。窄到"真不可逆", 宁误拦。None = 非高危 (软打标负责语义)。"""

    @abstractmethod
    def check(self, action: ActionContext) -> GateVerdict:
        """fail-closed 判定。高危类 → DENY/ALLOW_VIA_PROCESS/ALLOW_VIA_BYPASS, 缺标默认拒不放行。"""
