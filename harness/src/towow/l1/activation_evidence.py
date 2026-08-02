"""activation-evidence-organic-only@v1 判据 — is_organic 纯函数。

# spec source:
#   concept activation-evidence-organic-only@v1 (ConceptCreated evt-c2883711f0594d209208dae38e18049b)
#     "激活证据只认真实有机生产流量, 绝不认演习/demo/livefire/合成探测"
#     "机械验证: 激活证据事件的 provenance 指向真实 workload（actor 非 drill/synthetic、
#      会话非测试/演习），且事件由真实触发链产生。"
#   concept fire-drill-mechanism@v1
#     演习事件 payload 携带机器可过滤标记 drill_marker: {is_drill, drill_id, injected_by, injected_at}
#     "从 emit 起全程带标记，任何消费方一行判断即可过滤"

判据被 activation-acceptance-gate@v1 用作放行条件、被 activation-debt@v1 用作
「真空期(等真流量) vs 到期失败」的分界。

【失败方向 · fail-closed】放行方向的误判是灾难性的: 把演习/合成误判为有机 (返回 True)
= 静默放行假激活 = 击穿整个 goal。故凡真歧义 / 无法解析出 provenance → 返回 False
(宁可把真流量误判为非有机、让 activation-debt 挂真空期欠条, 也绝不把假激活放行)。

【信号层级】
  PRIMARY (权威)  : payload.drill_marker —— fire-drill 机制保证演习从 emit 起全程带此标记,
                    真实有机事件绝不携带; 故 drill_marker 存在即判非有机 (不依赖 is_drill 具体值)。
  SECONDARY (纵深): provenance 身份字段命中 drill/synthetic/test/demo/livefire token。

【纯判据边界】无副作用、不 emit、不读账本。输入即事件 / provenance 对象 (pydantic 或 dict)。
只据【本对象上出现的标记】判定; 概念第三条「事件由真实触发链产生」需读上游账本才能验,
纯判据够不着 —— 是 l1 纯函数的固有边界, 不是缺陷。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# 概念明确排除的非有机类别: 演习(drill) / 合成探测(synthetic) / 测试会话(test) / demo / livefire。
# 只实现概念点名的集合, 不自行扩充 (扩充=解释性 scope 蔓延, 会误伤真流量)。
# 注意刻意【不】收 "probe": activation-multisource-cli-probe@v1 是真实 CLI 取证探针,
# 概念排除的是「合成探测」= synthetic, 收 probe 会误杀真实取证路径。
_NON_ORGANIC_TOKENS: frozenset[str] = frozenset(
    {"drill", "synthetic", "test", "demo", "livefire"},
)

# 扫描的 provenance 身份字段 —— 概念点名的 actor 字段 + 会话类字段。
# 刻意不含 task_id / run_id / correlation_id: 前者可能合法含 "test" 类业务词 (误伤),
# 后二者是不透明事件 id (无语义信号)。
_PROVENANCE_ID_FIELDS: tuple[str, ...] = (
    "actor_type",
    "actor_id",
    "session_id",
    "parent_session_id",
    "fork_name",
    "daemon_name",
)


def _get(container: object, key: str) -> object:
    """从 Mapping 或对象上取字段, 统一 dict / pydantic 两种输入形态。"""
    if isinstance(container, Mapping):
        return container.get(key)
    return getattr(container, key, None)


def _has(container: object, key: str) -> bool:
    if isinstance(container, Mapping):
        return key in container
    return hasattr(container, key)


def _extract(obj: object) -> tuple[object | None, object | None]:
    """从输入抽出 (payload, provenance)。识别不了 provenance → provenance=None (fail-closed)。

    支持四种形态:
      - 事件对象 (dict 含 "provenance" 键 / pydantic 含 .provenance 属性)
      - provenance 对象本体 (dict 含 actor_type/actor_id / pydantic Provenance)
    """
    # 事件形态: 带 provenance 子对象。
    if _has(obj, "provenance"):
        return _get(obj, "payload"), _get(obj, "provenance")
    # provenance 本体形态: 带 actor 身份字段。
    if _has(obj, "actor_type") or _has(obj, "actor_id"):
        return None, obj
    # 认不出的形态 → 无法确认有机 → fail-closed。
    return None, None


def _carries_drill_marker(payload: object) -> bool:
    """PRIMARY 信号: payload 是否携带 fire-drill 演习标记。

    存在 drill_marker (dict 或带 is_drill 的对象) 即判为演习 —— 真实有机事件绝不携带它,
    故不依赖 is_drill 的具体真值 (防 "true"/1/嵌套等绕过)。
    """
    dm = _get(payload, "drill_marker")
    if dm is None:
        return False
    if isinstance(dm, Mapping):
        return True
    # pydantic drill_marker 模型
    return _has(dm, "is_drill")


def _tokenize(value: str) -> list[str]:
    """按非字母数字边界切 token, 避免 substring 误命中 (attestation/latest 不误中 test)。"""
    return [tok for tok in re.split(r"[^a-z0-9]+", value.lower()) if tok]


def _has_non_organic_marker(provenance: object) -> bool:
    """SECONDARY 信号: provenance 身份字段是否命中 drill/synthetic/test/demo/livefire token。"""
    for field in _PROVENANCE_ID_FIELDS:
        val = _get(provenance, field)
        if isinstance(val, str) and any(
            tok in _NON_ORGANIC_TOKENS for tok in _tokenize(val)
        ):
            return True
    return False


def is_organic(event_or_provenance: object) -> bool:
    """判某激活证据事件 / provenance 是否真实有机生产流量。

    True  = 真实 workload 自然走过路径产生的账本留痕 (actor 非 drill/synthetic、
            会话非测试/演习、本对象无演习标记)。
    False = 演习 / demo / livefire / 合成探测 / 无法解析的可疑输入 (fail-closed)。

    纯函数: 无副作用、不 emit、不读账本。
    """
    payload, provenance = _extract(event_or_provenance)
    if provenance is None:
        # 解析不出 provenance —— 无法确认有机, fail-closed 判非有机。
        return False
    if payload is not None and _carries_drill_marker(payload):
        return False
    return not _has_non_organic_marker(provenance)


__all__ = ["is_organic"]
