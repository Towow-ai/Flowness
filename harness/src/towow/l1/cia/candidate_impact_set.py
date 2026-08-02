"""R01 §2 — CandidateImpactSetComputer: 从 SIS 算候选影响集 CIS (两路并集).

# concept source: c-candidate-impact-set-system-derived@v1
#   候选影响集 CIS = 系统从 SIS 出发算的 **recall 优先** 候选波及面, 两路并集:
#     结构路: 沿概念图依赖边的传递闭包 (复用既有 impact_graph 投影 + CIAQueryService,
#       M-2.1 forward slice, max_depth<=5; 绝不重造遍历)。
#     历史路: evolutionary-coupling-mining 挖的共变耦合 (结构边漏掉的隐性耦合)。
#   CIS 天然过估 (切片保守), 是 precision 排序剪枝的输入、也是 AIS 算精确/召回的被比对方。

## 为什么是这两路的并集

SIS (起始影响集 = agent `work complete --touched-node` 声明的"我碰了哪些概念") 小、可声明;
CIS (候选影响集 = 整个可能波及面) 大、**由系统算**, 绝不让 agent 自己声明 (那等于让运动员当裁判)。
两条独立路径互补:

- **结构路** 抓"显式依赖" —— 概念图里有边连着的下游消费方。改了 X, 谁依赖 X 就可能被波及。
  直接复用 `CIAQueryService.forward_slice` (M-2.1 §4 "消费方发现"): 沿 impact_graph 归一化后的
  dependency/reference 出边走传递闭包 (max_depth<=5)。**本模块不自己遍历图** —— 遍历、edge_type
  过滤、path 记录、watermark 全由 cia_query 那层负责, 这里只把每个 SIS 种子的 forward slice 并起来。
- **历史路** 抓"隐性耦合" —— 概念图上没连边、但历史上总一起改的概念 (演化耦合)。复用
  `EvolutionaryCouplingMiner` 的产物 `MiningResult.neighbors(seed)`: 前件=种子的 corresponding
  耦合边的 target。结构边断了 (概念图碎成多块) 时, 这条路是补召回的关键。

并集 (而非交集) 是 recall 优先的直接体现: 任一路命中即进 CIS。过估正常 —— 下游 (T-R01-4 AIS /
后续 precision 剪枝) 才负责收口, CIS 这一层宁滥勿缺。

## SIS ⊆ CIS

经典 CIA (Bohner & Arnold, *Software Change Impact Analysis*): SIS ⊆ CIS ⊆ System,
AIS 对 CIS 度量过估/欠估。本实现据此把 SIS 种子本身也纳入 CIS (`is_seed=True` 标记) —— 已知变更
也是"被波及"的一部分, recall 优先不丢; AIS 对比时种子天然应在候选面里, 否则召回度量失真。

## provenance 是 precision 剪枝的输入

每个 CIS 成员带"怎么进来的"证据 (`CISMember`): 结构路给最短到达跳数 (越浅越可能真波及),
历史路给最强共变 confidence (越高越可能真耦合), 两路都命中信号最强。下游 precision 排序剪枝
按这些信号排序, 不必重算。

## id 空间契约

两路必须同 id 空间 (都是 concept_id): coupling result 须由 caller 用 **概念级篮子**
(envelope/commit 的 touched concept 集, 见 evolutionary_coupling.baskets_from_change_sets) 挖出,
与 SIS / concept_graph 同 id 空间。喂模块级篮子 (文件 stem) 则历史路 target 与概念图对不上, 并集失真。
本模块按 id 求并, 不做跨空间映射 (那是 caller / T-R01-7 共变回填的职责)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from towow.l1.cia_query import DEFAULT_SLICE_EDGE_TYPES, MAX_DEPTH_CAP, EntityRef

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l1.cia.evolutionary_coupling import MiningResult
    from towow.l1.cia_query import CIAQueryService


@dataclass(frozen=True)
class CISMember:
    """CIS 的一个成员 —— 一个可能被波及的概念, 带它"怎么进 CIS"的 provenance.

    provenance 是下游 precision 排序剪枝的输入 (concept: "precision 排序剪枝的输入"):
    `min_structural_depth` 越浅越可能真波及; `max_coupling_confidence` 越高越可能真共变;
    `via_structural and via_historical` (两路都命中) 信号最强。`is_seed` 标 SIS 本体 (已知变更)。
    """

    concept_id: str
    is_seed: bool  # 本身就在 SIS 里 (已知变更, 非新发现的候选)
    via_structural: bool  # 经结构切片 (概念图依赖/引用闭包) 到达
    via_historical: bool  # 经历史共变耦合到达
    min_structural_depth: int | None  # 结构路最短到达跳数 (非结构路 → None)
    max_coupling_confidence: float | None  # 历史路最强共变 confidence (非历史路 → None)
    reached_from: tuple[str, ...]  # 哪些 SIS 种子把它带进 CIS (升序去重)

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "is_seed": self.is_seed,
            "via_structural": self.via_structural,
            "via_historical": self.via_historical,
            "min_structural_depth": self.min_structural_depth,
            "max_coupling_confidence": self.max_coupling_confidence,
            "reached_from": list(self.reached_from),
        }


@dataclass(frozen=True)
class CandidateImpactSet:
    """一次 CIS 计算的产物 — SIS ∪ 结构切片 ∪ 历史共变, 带每个成员的 provenance。"""

    sis: tuple[str, ...]  # 起始影响集 (去重保序后的输入种子)
    members: tuple[CISMember, ...]  # 全体候选成员 (按 concept_id 升序)
    max_depth: int  # 结构路实际用的 (已 clamp 到 [0,5]) 深度
    edge_types: tuple[str, ...]  # 结构路沿走的 edge_type
    projection_watermark: int  # 结构路查询时 impact_graph 的 watermark (复用 cia_query 的)

    def concept_ids(self) -> set[str]:
        """完整 CIS = 全体成员 id (含 SIS 种子)。"""
        return {m.concept_id for m in self.members}

    def structural(self) -> set[str]:
        """仅经结构路到达的成员 id (= 各种子 forward slice 的并集)。"""
        return {m.concept_id for m in self.members if m.via_structural}

    def historical(self) -> set[str]:
        """仅经历史共变路到达的成员 id。"""
        return {m.concept_id for m in self.members if m.via_historical}

    def candidates(self) -> set[str]:
        """新发现的候选波及面 = CIS 去掉 SIS 种子 (下游真正要审视的"额外"波及)。"""
        return {m.concept_id for m in self.members if not m.is_seed}

    def as_dict(self) -> dict[str, Any]:
        return {
            "sis": list(self.sis),
            "members": [m.as_dict() for m in self.members],
            "max_depth": self.max_depth,
            "edge_types": list(self.edge_types),
            "projection_watermark": self.projection_watermark,
        }


def _clamp_depth(depth: int) -> int:
    """与 cia_query 同口径 — max_depth clamp 到 [0, MAX_DEPTH_CAP] (§4.4 防图爆炸)。"""
    return max(0, min(MAX_DEPTH_CAP, depth))


def _seed_id(seed: str | EntityRef) -> str:
    return seed.entity_id if isinstance(seed, EntityRef) else str(seed)


class CandidateImpactSetComputer:
    """从 SIS (变更集) 算 CIS (候选影响集), 两路并集。recall 优先 (过估正常, 由下游剪枝)。

    构造时拿一个 `CIAQueryService` (结构路) 与一个可选的 `MiningResult` (历史路)。无 coupling
    result 时退化为"结构路 + SIS" (仍合法 —— 概念图未碎、历史挖掘未跑时的退化形态)。

    本模块只做"并集 + provenance 汇总", **不重造任何图遍历或共变挖掘** —— 结构遍历归
    `CIAQueryService.forward_slice`, 共变挖掘归 `EvolutionaryCouplingMiner` (其产物经
    `MiningResult.neighbors` 取用)。这是 concept "复用既有 impact_graph 投影 + CIAQueryService,
    绝不重造遍历"的直接落地。
    """

    def __init__(
        self,
        query_service: CIAQueryService,
        coupling_result: MiningResult | None = None,
    ) -> None:
        self._svc = query_service
        self._coupling = coupling_result

    def compute(
        self,
        sis: Iterable[str | EntityRef],
        *,
        max_depth: int = MAX_DEPTH_CAP,
        edge_types: list[str] | None = None,
        only_corresponding: bool = True,
    ) -> CandidateImpactSet:
        """从 SIS 算 CIS。

        - `max_depth`: 结构路传递闭包深度, clamp 到 [0,5] (复用 cia_query 上限)。
        - `edge_types`: 结构路沿走的概念边类型, 默认 (dependency, reference) (= cia_query 默认)。
        - `only_corresponding`: 历史路是否只取通过对应性检查 (去假阳性) 的共变边。默认 True
          (= evolutionary_coupling 的去噪默认); 置 False 可进一步抬高 recall (代价是更多假阳性)。
        """
        # SIS 去重保序 (输入可能含重复 / 空)。
        seed_ids: list[str] = []
        for s in sis:
            sid = _seed_id(s)
            if sid and sid not in seed_ids:
                seed_ids.append(sid)

        etypes = tuple(edge_types) if edge_types is not None else tuple(DEFAULT_SLICE_EDGE_TYPES)
        depth = _clamp_depth(max_depth)

        struct_depth: dict[str, int] = {}  # concept_id → 最短结构到达跳数
        hist_conf: dict[str, float] = {}  # concept_id → 最强共变 confidence
        reached_from: dict[str, set[str]] = {}  # concept_id → 带它进来的种子集
        watermark = 0

        for seed in seed_ids:
            # ── 结构路: 复用 forward_slice (下游消费方传递闭包), 不自己遍历 ──
            sliced = self._svc.forward_slice(seed, max_depth=depth, edge_types=list(etypes))
            watermark = sliced.projection_watermark
            for r in sliced.results:
                cid = r.entity.entity_id
                prev = struct_depth.get(cid)
                struct_depth[cid] = r.depth if prev is None else min(prev, r.depth)
                reached_from.setdefault(cid, set()).add(seed)

            # ── 历史路: 复用共变挖掘产物 (前件=种子的耦合边 target), 不自己挖掘 ──
            if self._coupling is not None:
                for edge in self._coupling.neighbors(seed, only_corresponding=only_corresponding):
                    cid = edge.target
                    prev_c = hist_conf.get(cid)
                    hist_conf[cid] = edge.confidence if prev_c is None else max(prev_c, edge.confidence)
                    reached_from.setdefault(cid, set()).add(seed)

        # CIS = SIS ∪ 结构 ∪ 历史 (SIS ⊆ CIS, recall 优先)。
        seed_set = set(seed_ids)
        all_ids = seed_set | set(struct_depth) | set(hist_conf)
        members = tuple(
            CISMember(
                concept_id=cid,
                is_seed=cid in seed_set,
                via_structural=cid in struct_depth,
                via_historical=cid in hist_conf,
                min_structural_depth=struct_depth.get(cid),
                max_coupling_confidence=hist_conf.get(cid),
                reached_from=tuple(sorted(reached_from.get(cid, set()))),
            )
            for cid in sorted(all_ids)
        )
        return CandidateImpactSet(
            sis=tuple(seed_ids),
            members=members,
            max_depth=depth,
            edge_types=etypes,
            projection_watermark=watermark,
        )
