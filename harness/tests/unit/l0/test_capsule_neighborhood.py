"""Unit tests for M-0.3 §2.2 expand_neighborhood (RUN-027 block 2 — 棒 2.1).

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md §2.2
#
# Pure BFS — verifies hop counting, undirected traversal, inactive-edge exclusion,
# induced-subgraph node/edge selection, and defensive degradation.
"""

from __future__ import annotations

from towow.l0.capsule.neighborhood import expand_neighborhood


def _graph() -> dict[str, object]:
    # a — b — c — d   (chain) ; e isolated
    return {
        "nodes": [
            {"concept_id": "a", "name": "A", "definition": "node a"},
            {"concept_id": "b", "name": "B", "definition": "node b"},
            {"concept_id": "c", "name": "C", "definition": "node c"},
            {"concept_id": "d", "name": "D", "definition": "node d"},
            {"concept_id": "e", "name": "E", "definition": "node e"},
        ],
        "edges": [
            {"edge_id": "ab", "source_concept_id": "a", "target_concept_id": "b", "is_active": True},
            {"edge_id": "bc", "source_concept_id": "b", "target_concept_id": "c", "is_active": True},
            {"edge_id": "cd", "source_concept_id": "c", "target_concept_id": "d", "is_active": False},
        ],
    }


def test_hop_zero_is_just_seeds() -> None:
    nb = expand_neighborhood(_graph(), ["b"], hops=0)
    assert nb.concept_ids == frozenset({"b"})
    assert {n["concept_id"] for n in nb.nodes} == {"b"}
    assert nb.edges == []


def test_hop_one_includes_direct_neighbors_undirected() -> None:
    # b reaches a (b is target of a→b) and c (b→c) — undirected.
    nb = expand_neighborhood(_graph(), ["b"], hops=1)
    assert nb.concept_ids == frozenset({"a", "b", "c"})
    # induced subgraph edges: ab + bc (both endpoints in set), cd excluded (inactive + d out).
    assert {e["edge_id"] for e in nb.edges} == {"ab", "bc"}


def test_hop_two_extends_one_more_level() -> None:
    nb = expand_neighborhood(_graph(), ["a"], hops=2)
    # a -> b (hop1) -> c (hop2). cd is inactive so d unreachable.
    assert nb.concept_ids == frozenset({"a", "b", "c"})


def test_inactive_edge_not_traversed() -> None:
    # Seeding at c: cd is inactive, so d must NOT be reached even at high hops.
    nb = expand_neighborhood(_graph(), ["c"], hops=5)
    assert "d" not in nb.concept_ids
    assert nb.concept_ids == frozenset({"a", "b", "c"})


def test_isolated_seed_has_only_itself() -> None:
    nb = expand_neighborhood(_graph(), ["e"], hops=3)
    assert nb.concept_ids == frozenset({"e"})


def test_seed_not_in_graph_still_in_concept_ids() -> None:
    # An obligation anchored to a touched node that isn't (yet) a materialized concept
    # must still be matchable (hop-0 hit), even with no node dict.
    nb = expand_neighborhood(_graph(), ["ghost"], hops=2)
    assert nb.concept_ids == frozenset({"ghost"})
    assert nb.nodes == []


def test_empty_seeds_is_empty_neighborhood() -> None:
    nb = expand_neighborhood(_graph(), [], hops=2)
    assert nb.is_empty
    assert nb.concept_ids == frozenset()


def test_none_graph_degrades_to_seeds_only() -> None:
    nb = expand_neighborhood(None, ["a"], hops=2)
    assert nb.concept_ids == frozenset({"a"})
    assert nb.nodes == []
    assert nb.edges == []


def test_malformed_graph_degrades_gracefully() -> None:
    nb = expand_neighborhood({"nodes": "oops", "edges": 5}, ["a", "b"], hops=1)
    assert nb.concept_ids == frozenset({"a", "b"})
    assert nb.nodes == []
