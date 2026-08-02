"""design-freeze-surface@v1 — machine-decidable, event-sourced predicate.

# concept source: design-freeze-surface@v1 (ConceptCreated, engineering_consensus)
#   plan: maintainability-gate-ledger-bound-coverage / task: T3-build-design-freeze-surface-predicate

一个 event 类型 E 是 **设计冻结面 (design-freeze surface)** 当且仅当 emit 它会在事件
日志上落下 canonical、耐久的设计内容，且该内容成为下游任务的稳定前提。已知实例：
EngineeringConsensusFreezed（落冻结概念）、PlanFreezed（落冻结计划）、
ReviewPlanCreated（落冻结 review 计划）。反例：TaskRunCompleted、record-only scan
事件（cia_downstream_scan 等）、纯运行时观测事件——它们不落"下游据以开工的稳定前提"。

## 为什么 event-sourced，不是硬编码白名单

成员资格由"账本上落了什么设计内容"派生——`discover_design_freeze_surfaces` 扫账本、
按 payload 的结构签名认成员，模块里 **没有** `{"PlanFreezed", ...}` 这种事件类型名字
的字面清单。这是刻意的：一张硬编码清单本身要"靠某人记得回来补一行"，恰恰违反
design-maintainability-gate@v1 判据⑤ maintenance_not_by_memory。

## v1 信号是结构代理，不是终极谓词（诚实限制，必读）

本 v1 用的判据是 **batch-freeze provenance 的结构代理**：payload 携带 `batch_info`
（批量冻结的谱系块 batch_number / is_final_batch / previous_batch_*_freeze_ids），或
`after_state` 里携带 `frozen_*` id 集合。经全 hot 账本核实，这个签名精确命中上述 3 个
冻结事件、且无假阳。

但要诚实交代它的盲点：把"硬编码 3 个事件类型名"换成"认 `batch_info` 这个 magic key"，
本质仍是**靠 freeze 作者记得带上该 marker**——一个将来被写出来、却不带 `batch_info`
的设计冻结事件会 **静默逃逸** 本谓词。这与它对标的 harness-capability-registry@v1 有一档
差距：注册表的成员资格由构成能力的同一批边派生（注册=加边、发现=读边，没有额外要记的
token）；本谓词的 `batch_info` 是独立要记得带的 token，鲁棒性弱一档。概念明写本谓词是
"机器可判形态，供下游精化"——v1 结构信号被明确允许，但不带 marker 的未来面需下游精化
（如追踪谁 @ 引用/pin 到该 entity 的语义版）才能密闭。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol


class _EventLike(Protocol):
    """结构协议：任何暴露 event_type + payload 的记录（EventRecord 天然满足）。

    让谓词能在真账本 (EventRecord) 与合成测试记录上同样工作，不逼测试构造重量级
    EventRecord。EventType 是 StrEnum，`event_type` 本身即 str 子类。
    """

    @property
    def event_type(self) -> Any: ...

    @property
    def payload(self) -> Mapping[str, Any]: ...


def payload_carries_freeze_signature(payload: Mapping[str, Any]) -> bool:
    """单条 payload 是否结构性携带 batch-freeze provenance（设计冻结面的 v1 结构代理）。

    命中任一即为真：
    - 顶层 `batch_info` 非空（ReviewPlanCreated 形态）；
    - `after_state.batch_info` 非空（EngineeringConsensusFreezed / PlanFreezed 形态）；
    - `after_state` 含任一 `frozen_*` id 集合——显式的鲁棒性 OR：当前数据上它被
      `batch_info` 完全覆盖（带 frozen_ 的两个都带 batch_info），保留是为兜住"带 frozen_
      冻结集合却漏带 batch_info"的将来形态，不是死码。

    注意本函数只判 **结构签名**，不解释"设计内容是否真耐久"——那超出 v1 机器可判形态。
    """
    if payload.get("batch_info"):
        return True
    after_state = payload.get("after_state")
    if isinstance(after_state, Mapping):
        if after_state.get("batch_info"):
            return True
        if any(key.startswith("frozen_") for key in after_state):
            return True
    return False


def discover_design_freeze_surfaces(records: Iterable[_EventLike]) -> frozenset[str]:
    """扫账本、返回携带设计冻结签名的 event 类型集合（event-sourced，非硬编码清单）。

    成员资格纯由 records 里实际出现、且 payload 命中 `payload_carries_freeze_signature`
    的 event 类型派生。空账本 → 空集（与 coverage-drift-scan 一致：只发现账本上真出现过
    的面）。
    """
    surfaces: set[str] = set()
    for record in records:
        if payload_carries_freeze_signature(record.payload):
            # EventType 是 StrEnum；str() 归一到 canonical 名字字符串。
            surfaces.add(str(record.event_type))
    return frozenset(surfaces)


def is_design_freeze_surface(
    event_type: str,
    surfaces: Iterable[str],
) -> bool:
    """某 event 类型是否设计冻结面——查已发现的成员集合。

    `surfaces` 应是 `discover_design_freeze_surfaces` 的返回值（或等价的已发现集合），
    以免每次判定都重扫账本；调用方扫一次账本、多次判定。
    """
    return event_type in set(surfaces)
