"""T-BRAIN-01 — unit tests for the concept-graph neighborhood seed derivation.

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md §2.2 / §3
#     (neighborhood_center = "task_scope.target_artifacts 对应的概念/文件节点")
#
# The fix: assemble_capsule auto-derives the neighborhood SEEDS from the task's own concept
# anchors (the concept-typed read_set entries in the task_graph projection node) instead of
# relying on every caller to hand-fill --touched-node. These pure-function tests pin the
# derivation + the order-stable dedupe used to union caller-supplied + auto-derived seeds.
"""

from __future__ import annotations

from towow.l0.capsule.pipeline import _dedupe_preserve_order, _derive_task_concept_anchors


def _task_graph_with(task_id: str, read_set: list[dict[str, object]]) -> dict[str, object]:
    return {
        "nodes": [
            {"task_id": "other-task", "read_set": [{"entity_type": "concept", "entity_id": "noise@v1"}]},
            {"task_id": task_id, "read_set": read_set, "target_artifacts": ["x.py"]},
        ],
        "edges": [],
    }


# ─── _derive_task_concept_anchors ────────────────────────────────────────────


def test_derives_concept_typed_read_set_entries() -> None:
    tg = _task_graph_with(
        "T1",
        [
            {"entity_type": "concept", "entity_id": "alpha@v1"},
            {"entity_type": "concept", "entity_id": "beta@v1"},
        ],
    )
    assert _derive_task_concept_anchors(tg, "T1") == ["alpha@v1", "beta@v1"]


def test_only_concept_entities_become_anchors() -> None:
    # file: / task: read_set entries are NOT concept anchors — only concept-typed ones seed the graph.
    tg = _task_graph_with(
        "T1",
        [
            {"entity_type": "file", "entity_id": "src/foo.py"},
            {"entity_type": "concept", "entity_id": "alpha@v1"},
            {"entity_type": "task", "entity_id": "T-other"},
        ],
    )
    assert _derive_task_concept_anchors(tg, "T1") == ["alpha@v1"]


def test_picks_the_matching_task_node_only() -> None:
    # The 'other-task' node's concept must NOT leak into T1's anchors.
    tg = _task_graph_with("T1", [{"entity_type": "concept", "entity_id": "alpha@v1"}])
    assert _derive_task_concept_anchors(tg, "T1") == ["alpha@v1"]


def test_task_not_found_returns_empty() -> None:
    tg = _task_graph_with("T1", [{"entity_type": "concept", "entity_id": "alpha@v1"}])
    assert _derive_task_concept_anchors(tg, "T-absent") == []


def test_node_without_concept_read_set_returns_empty() -> None:
    # A task that declares only file/task reads (no concept anchor) → no auto seeds → no
    # regression vs today's empty-seed behavior.
    tg = _task_graph_with("T1", [{"entity_type": "file", "entity_id": "src/foo.py"}])
    assert _derive_task_concept_anchors(tg, "T1") == []


def test_missing_or_malformed_read_set_returns_empty() -> None:
    assert _derive_task_concept_anchors({"nodes": [{"task_id": "T1"}]}, "T1") == []
    assert _derive_task_concept_anchors({"nodes": [{"task_id": "T1", "read_set": "oops"}]}, "T1") == []
    assert _derive_task_concept_anchors({"nodes": [{"task_id": "T1", "read_set": [42, "x"]}]}, "T1") == []


def test_non_dict_or_empty_task_graph_returns_empty() -> None:
    assert _derive_task_concept_anchors(None, "T1") == []
    assert _derive_task_concept_anchors("not-a-graph", "T1") == []
    assert _derive_task_concept_anchors({}, "T1") == []
    assert _derive_task_concept_anchors({"nodes": "oops"}, "T1") == []
    assert _derive_task_concept_anchors({"nodes": []}, "T1") == []


def test_ignores_concept_entry_without_entity_id() -> None:
    tg = _task_graph_with(
        "T1",
        [
            {"entity_type": "concept"},  # no entity_id
            {"entity_type": "concept", "entity_id": ""},  # empty
            {"entity_type": "concept", "entity_id": "alpha@v1"},
        ],
    )
    assert _derive_task_concept_anchors(tg, "T1") == ["alpha@v1"]


# ─── _dedupe_preserve_order ──────────────────────────────────────────────────


def test_dedupe_preserves_first_occurrence_order() -> None:
    assert _dedupe_preserve_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_dedupe_drops_falsy() -> None:
    assert _dedupe_preserve_order(["", "a", "", "b"]) == ["a", "b"]


def test_dedupe_empty() -> None:
    assert _dedupe_preserve_order([]) == []


def test_union_caller_and_auto_derived_is_order_stable() -> None:
    # The call-site contract: caller touched_nodes first, then auto-derived anchors, deduped.
    caller = ["explicit@v1"]
    auto = ["alpha@v1", "explicit@v1"]  # alpha new, explicit already present
    assert _dedupe_preserve_order([*caller, *auto]) == ["explicit@v1", "alpha@v1"]
