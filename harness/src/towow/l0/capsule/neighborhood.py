"""M-0.3 §2.2 concept-graph neighborhood expansion (BFS).

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §2.2 阶段 2 — neighborhood = expand_neighborhood(bundle.concept_graph,
#       task_scope.touched_nodes, hops=scene_template.neighborhood_hops)
#     §2.7 阶段 7 — KNOWN_FACTS = bundle.neighborhood_slice
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §6.2 ResolverInput.projection_slice.concept_graph_neighborhood (N-hop nodes/edges)
#
# Pure function — no I/O, no event emit, no projection re-read. Consumed by the capsule
# pipeline stage 2 to scope candidate obligations (stage 3 anchors∩neighborhood filter),
# to fill the KNOWN_FACTS capsule section, and to populate
# ResolverInput.projection_slice.concept_graph_neighborhood.
#
# Block 2 (RUN-027 §1 block-2): replaces the "neighborhood expansion deferred" stub —
# pipeline.py:304-306 / :405 (projection_slice nodes=[]/edges=[]).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Neighborhood:
    """N-hop induced subgraph around a set of seed concept nodes.

    concept_ids — every node id within `hops` of a seed (seeds included at hop 0).
    nodes / edges — the induced subgraph (node dicts for concept_ids; active edge dicts
    whose both endpoints are in concept_ids). nodes/edges are taken verbatim from the
    concept_graph projection so downstream rendering / resolver input see real shapes.
    """

    concept_ids: frozenset[str]
    nodes: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, object]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.concept_ids


def expand_neighborhood(
    concept_graph: dict[str, object] | None,
    seed_concept_ids: Iterable[str],
    hops: int,
) -> Neighborhood:
    """M-0.3 §2.2 — BFS the concept graph N hops out from `seed_concept_ids`.

    Edges are traversed *undirected* over `is_active=true` edges only — "邻域" is a
    reachability notion, not a directed-dependency one, so an obligation anchored to a
    concept reachable within N hops (in either direction) is in-neighborhood.

    `hops=0` returns just the seeds. Malformed / missing concept_graph (None, non-dict,
    missing nodes/edges) degrades to an empty-edge graph — seeds still form the
    neighborhood so an obligation anchored directly to a touched node still matches.
    Seeds that are not materialized nodes are kept in `concept_ids` (so anchor∩seed
    still hits) but contribute no node dict.
    """
    seeds = {s for s in seed_concept_ids if s}
    if not seeds:
        return Neighborhood(concept_ids=frozenset())

    raw_nodes = _as_dict_list(concept_graph.get("nodes")) if isinstance(concept_graph, dict) else []
    raw_edges = _as_dict_list(concept_graph.get("edges")) if isinstance(concept_graph, dict) else []

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in raw_edges:
        if edge.get("is_active") is False:
            continue  # ConceptEdgeRemoved marks is_active=false (M-0.2 §3.1) — not traversable
        src = edge.get("source_concept_id")
        dst = edge.get("target_concept_id")
        if isinstance(src, str) and isinstance(dst, str) and src and dst:
            adjacency[src].add(dst)
            adjacency[dst].add(src)

    visited: set[str] = set(seeds)
    frontier: set[str] = set(seeds)
    for _ in range(max(0, hops)):
        next_frontier: set[str] = set()
        for node_id in frontier:
            for neighbor in adjacency.get(node_id, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier

    nodes = [n for n in raw_nodes if n.get("concept_id") in visited]
    edges = [
        e
        for e in raw_edges
        if e.get("is_active") is not False
        and e.get("source_concept_id") in visited
        and e.get("target_concept_id") in visited
    ]
    return Neighborhood(concept_ids=frozenset(visited), nodes=nodes, edges=edges)


def _as_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


__all__ = ["Neighborhood", "expand_neighborhood"]
