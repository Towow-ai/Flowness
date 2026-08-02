"""GP-05 — gate-side graph edge integrity check (f-sub-graph-protocol-readside-dormant).

# 病灶 (finding f-sub-graph-protocol-readside-dormant, GP-05 major):
#   图协议契约 (graph_protocol.py §完整性 / facade.integrity_violations) 要求每条边的 `to` 必须
#   ∈ 图内节点 ∪ 合法 external_anchor (指 repo 内 spec 路径 / url 的合法外部引用), 否则导航撞空 =
#   完整性破。契约**声称** commit gate 在加边时校验 ("commit gate 用它在加边时校验", graph_protocol.py
#   §integrity_violations docstring), 但门**零执行**这道校验 —— 一个 target 指向不存在概念的
#   ConceptEdgeAdded 干净过关, 悬空边越长越多 (现有 20 条), 让 investigate 的跨域 navigate 走进空洞。

# 为什么放这里、为什么"门自己算" (反 presence-only):
#   completeness.py 的 REQUIRED_COMPLETENESS_CHECKS 是 **presence-only** —— 只验某 check_id 在
#   self_check.blocking_checks 里且 status==passed。对完整性约束这是【可伪造】的: 直接 emit 一个
#   ConceptEdgeAdded 自带一条 {check_id: edge_target_resolvable, status: passed} 的自报 check 就能过,
#   target 仍可指向虚空。真牙齿必须**门自己从 edge payload 算 target 可解析性**, 跟
#   check_concept_create_relatedness / check_concept_retire_migration 在 gate._run_checks 里同一个
#   pattern: 对 domain_intents 无条件跑、本批无 ConceptEdgeAdded → not_applicable、任一 target 不可
#   解析 → fail-closed 拒。这与 completeness.py 的 execution.livefire_recomputed "门重算而非信自报"
#   同一原则 (rc-gate-verifies-claim-not-substance-flag-only 根因簇 C 的对治)。

# scope 边界:
#   只管 ConceptEdgeAdded (concept_graph 是生产真在拦的承重墙, facade 完整性契约的主对象)。
#   TaskDependencyEdgeAdded 的 target 是 task (生命周期在 planning/task 图侧校验), 不在本门范围 ——
#   避免误拦合法 plan 依赖边。校验 **target** (`to`) 可解析性 = 契约字面 ("边的 to 必须 ∈ ...");
#   source (`from_id`) 的存在性是另一类 (更罕见) 问题, 不在本 finding (edge_target_resolvable) 范围。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from towow.awareness.graph.facade import _is_external_anchor
from towow.l0.commit_gate.concept_create_gate import (
    _event_type_name,
    _extract_new_concepts,
    _payload_of,
    existing_concepts_from_records,
)

_CONCEPT_EDGE_ADDED = "ConceptEdgeAdded"


@dataclass(frozen=True)
class _ConceptEdge:
    edge_id: str
    source: str
    target: str


@dataclass(frozen=True)
class GraphEdgeIntegrityResult:
    """Outcome of the gate-side graph-edge-integrity check."""

    passed: bool
    applicable: bool = False
    failure_reason: str | None = None
    failure_evidence: dict[str, str] | None = None
    notices: list[str] = field(default_factory=list)


def _extract_concept_edges(domain_intents: Iterable[Any]) -> list[_ConceptEdge]:
    """从本批 domain_intents 提取 ConceptEdgeAdded 的 (edge_id, source, target)。

    edge 数据在 payload.after_state (CREATED transition); 缺 source/target → 跳过 (relatedness
    门同样只看带两端的边, 缺字段的异形 edge 由 envelope schema 层挡)。
    """
    out: list[_ConceptEdge] = []
    for intent in domain_intents:
        if _event_type_name(intent) != _CONCEPT_EDGE_ADDED:
            continue
        after = _payload_of(intent).get("after_state")
        if not isinstance(after, dict):
            continue
        source = after.get("source_concept_id")
        target = after.get("target_concept_id")
        if not isinstance(source, str) or not source:
            continue
        if not isinstance(target, str) or not target:
            continue
        edge_id = after.get("edge_id")
        out.append(
            _ConceptEdge(
                edge_id=str(edge_id) if isinstance(edge_id, str) else "",
                source=source,
                target=target,
            ),
        )
    return out


def check_graph_edge_integrity(
    domain_intents: Iterable[Any],
    committed_records: Iterable[Any],
) -> GraphEdgeIntegrityResult:
    """门自算 ConceptEdgeAdded 的 target 可解析性, fail-closed。

    domain_intents: 本批待提交事件 (gate 给 EventIntent; 测试可给鸭子)。只看 ConceptEdgeAdded ——
        无则 applicable=False, passed=True (not_applicable, 与 relatedness/retire 门同口径不误拒)。
    committed_records: 已提交事件记录 (gate 给 event_log.all_records())。从中枚举既有概念 id =
        target 的可解析节点集之一。

    可解析判据 (契约 graph_protocol.py §完整性): target ∈
      ① 既有概念节点 (committed_records 里的 ConceptCreated, 双 shape, **含已退役-仍在节点**, 复用
         existing_concepts_from_records(include_retired=True) 不另造) ∪
      ② 本批同时 co-created 的概念 (同 envelope 的 ConceptCreated —— 先建概念再连边的合法批) ∪
      ③ 合法 external_anchor (repo 内 spec 路径 / url, 复用 facade._is_external_anchor 同一判据)。
    任一 target 都不命中 → 该边导航撞空 = 完整性破 → 整批 fail-closed 拒 (第一条违规即返回, evidence
    指名 edge_id + 不可解析的 target)。

    退役概念为何仍算可解析 (f-gp05-edge-gate-stricter-than-facade-retired-target): 本门逐字引用
    facade.integrity_violations 契约 (target ∈ 图内节点), 而 reducer 从不删节点 (supersede 只就地改
    字段) → 退役概念**仍是 concept_graph 节点**, facade 判它 RESOLVABLE。故 existing_ids 必须
    include_retired=True ——若用相关性门那套退役过滤 (existing_concepts_from_records 默认), 边门会比它
    所声称的 facade 契约**严**, 对合法血缘回链 (典型 @vN --reference--> @v(N-1), 经独立 edge-add 命令,
    target 已在更早批次退役) fail-closed 误拒, 且谎报『非图内节点/导航撞空』(事实错: target 仍是图节点)。
    """
    edges = _extract_concept_edges(domain_intents)
    if not edges:
        return GraphEdgeIntegrityResult(passed=True, applicable=False)

    existing_ids = {
        c.concept_id
        for c in existing_concepts_from_records(committed_records, include_retired=True)
    }
    co_created = {nc.concept_id for nc in _extract_new_concepts(domain_intents)}
    resolvable_nodes = existing_ids | co_created

    for edge in edges:
        if edge.target in resolvable_nodes:
            continue
        if _is_external_anchor(edge.target):
            continue
        return GraphEdgeIntegrityResult(
            passed=False,
            applicable=True,
            failure_reason="edge_target_unresolvable",
            failure_evidence={
                "edge_id": edge.edge_id,
                "source_concept_id": edge.source,
                "target_concept_id": edge.target,
                "detail": (
                    "ConceptEdgeAdded.target 既非图内节点 (既有概念 ∪ 同批 co-created) "
                    "也非合法 external_anchor → 导航撞空 = 完整性破"
                ),
            },
        )

    return GraphEdgeIntegrityResult(
        passed=True,
        applicable=True,
        notices=[f"graph_edge_integrity: {len(edges)} 条 ConceptEdgeAdded target 均可解析"],
    )


__all__ = [
    "GraphEdgeIntegrityResult",
    "check_graph_edge_integrity",
]
