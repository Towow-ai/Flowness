"""K6 · 统一异常分类表 (层①③⑥ 共用)。

设计依据: rc-layer-non-happy-path-completeness / 20-layer23-concurrency-nonhappy.md §T3.2 /
Ashby 必要变化律 / owner "列功能要列全 + 可扩展"。

## 本质 (owner 强调的"列功能"在这里正确兑现)
Ashby: 响应种类必须 ≥ 扰动种类, 否则有失控点。所以**把扰动空间列全**(限流/网络抖/模型不可用/
休眠/慢/真死/卡等/重派耗尽/重规划...), 每类配一个规定响应。**但列的是可扩展的数据表, 不是写死的
if-else**: 新扰动模式 = 注册一行 (register_rule), 不改核心代码。这就是"列全 + 可扩展", 既满足
Ashby(不漏一格), 又不退化成迟早漏项的死清单。

## 关键设计判断 (可证伪)
- **①③⑥ 共用同一张表**: 同一个"限流"在觉知层(①)、非顺利路(③)、治理观测(⑥) 必须判一致, 否则
  行为不可预测(现状三套分叉)。对因为: 一处分类→处处同裁决。塌于 Y: 若某消费者绕过本表自判, 分叉复发。
- **可扩展是硬要求**: class 开放 + matcher 可注册。塌于 Y: 若做成固定枚举的硬编 class, 遇到第 N+1
  类扰动(如"模型 API 改 schema 致 fork 全灭"这种本轮真栽过的) 仍要改代码 = 没解决枚举问题。
- **首条匹配 + 兜底 UNKNOWN→保守**: 判不准不赌, 规定动作 = 保守等/升级, 不是默认收割。
- **prescribed_action 与 ① 判活/复活对齐**: transient→revive(不重派)/terminal→redispatch(需死亡证据)/
  waiting→escalate/replan→re_decompose/exhausted→triage。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class FailureClass(str, Enum):
    TRANSIENT = "transient"      # 瞬态阻塞, 会自愈 → 复活/重试
    TERMINAL = "terminal"        # 真终态 → (死亡证据确认后)重派
    REPLAN = "replan"            # 边界错, 要重规划 → re-decompose
    WAITING = "waiting"          # 等输入/等 owner → escalate
    EXHAUSTED = "exhausted"      # 重派耗尽 → 真分诊(redispatch/retire/escalate)
    UNKNOWN = "unknown"          # 判不准 → 保守等, 绝不赌收割


# 规定动作 (与层① per-form 契约对齐)
ACTION = {
    FailureClass.TRANSIENT: "revive",
    FailureClass.TERMINAL: "redispatch",
    FailureClass.REPLAN: "re_decompose",
    FailureClass.WAITING: "escalate",
    FailureClass.EXHAUSTED: "triage",
    FailureClass.UNKNOWN: "wait",
}


@dataclass(frozen=True)
class DisturbanceRule:
    id: str
    failure_class: FailureClass
    pattern: str | None = None  # 正则(对 signal 文本); 与 predicate 二选一
    predicate: Callable[[str], bool] | None = None
    evidence_required: bool = False  # 是否需正向证据才下此裁决(如 terminal 需死亡证据)
    note: str = ""

    def matches(self, signal: str) -> bool:
        if self.predicate is not None:
            return self.predicate(signal)
        if self.pattern is not None:
            return re.search(self.pattern, signal, re.IGNORECASE) is not None
        return False


@dataclass(frozen=True)
class ClassifyResult:
    failure_class: FailureClass
    prescribed_action: str
    rule_id: str
    evidence_required: bool


# --- 默认扰动表 (Ashby: 列全已知扰动空间; 顺序 = 优先级, 首条匹配) ---
# 新扰动模式只需在此加一行 或 运行时 register_rule, 不改 classify 逻辑。
_DEFAULT_RULES: list[DisturbanceRule] = [
    DisturbanceRule("rate_limit", FailureClass.TRANSIENT,
                    pattern=r"rate.?limit|429|too many requests|quota|overloaded|529",
                    note="限流 → 复活(不重派), 系统反复撞则 governor 降全局率"),
    DisturbanceRule("network_blip", FailureClass.TRANSIENT,
                    pattern=r"connection (reset|closed|refused|aborted)|timeout|timed out|ECONN|network",
                    note="网络抖 → 复活重试"),
    DisturbanceRule("model_unavailable", FailureClass.TRANSIENT,
                    pattern=r"model.*(unavailable|not found|overloaded)|503|service unavailable",
                    note="模型暂不可用 → 复活退避"),
    DisturbanceRule("parked_sleep", FailureClass.TRANSIENT,
                    pattern=r"parked|sleep|idle|休眠|泊车",
                    note="休眠/泊车 → 复活发继续, 绝不当 dead 重派(owner 实战#2 根症状)"),
    DisturbanceRule("replan_needed", FailureClass.REPLAN,
                    pattern=r"replan|re-?decompose|boundary.*(wrong|changed)|aborted_for_replan",
                    note="边界错要重规划 → re-decompose(level-triggered 消费, 非死胡同)"),
    DisturbanceRule("waiting_owner", FailureClass.WAITING,
                    pattern=r"awaiting (owner|nature)|needs? (owner|human)|escalat|class.?a|blocked.?on.?owner",
                    note="等 owner 决策 → escalate(统一通道)"),
    DisturbanceRule("redispatch_exhausted", FailureClass.EXHAUSTED,
                    pattern=r"exhausted|max.?retries|circuit.?trip|redispatch.*exhaust|熔断",
                    note="重派耗尽 → 真分诊, 不静默丢"),
    DisturbanceRule("true_death", FailureClass.TERMINAL, evidence_required=True,
                    pattern=r"process (gone|dead|not found)|pid.*(gone|dead)|exit code|killed|segfault|真死",
                    note="真死 → 需正向死亡证据(pid没/显式abort)才重派; 沉默不算"),
]


class ExceptionTaxonomy:
    """可扩展异常分类法。①③⑥ 共用一个实例。"""

    def __init__(self, rules: list[DisturbanceRule] | None = None):
        self._rules: list[DisturbanceRule] = list(_DEFAULT_RULES if rules is None else rules)

    def register_rule(self, rule: DisturbanceRule, *, prepend: bool = False) -> None:
        """加一类扰动 = 一行, 不改 classify 逻辑(Ashby 可扩展)。prepend=高优先。"""
        self._rules.insert(0, rule) if prepend else self._rules.append(rule)

    def classify(self, signal: str) -> ClassifyResult:
        """首条匹配的扰动 → 规定动作。无匹配 → UNKNOWN 保守(绝不默认收割)。"""
        for r in self._rules:
            if r.matches(signal):
                return ClassifyResult(r.failure_class, ACTION[r.failure_class], r.id, r.evidence_required)
        return ClassifyResult(FailureClass.UNKNOWN, ACTION[FailureClass.UNKNOWN], "unmatched", False)

    @property
    def disturbance_count(self) -> int:
        return len(self._rules)
