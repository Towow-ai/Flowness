"""GP-05 — unit coverage for check_graph_edge_integrity (pure function, duck-typed intents).

Mirrors the test_t_r01_5 unit layer: the enforcement (real gate fail-closed) lives in
tests/integration/test_edge_integrity_gate_fault_inject.py; here we pin the function's branches —
not_applicable (no edge), unresolvable target, committed-target, co-created-target, external_anchor.
"""

from __future__ import annotations

from types import SimpleNamespace

from towow.l0.commit_gate.graph_integrity_check import check_graph_edge_integrity


def _concept(cid: str) -> SimpleNamespace:
    return SimpleNamespace(event_type="ConceptCreated", payload={"concept_id": cid, "concept_name": cid})


def _edge(src: str, dst: str, edge_id: str = "e1") -> SimpleNamespace:
    return SimpleNamespace(
        event_type="ConceptEdgeAdded",
        payload={"after_state": {"edge_id": edge_id, "source_concept_id": src, "target_concept_id": dst}},
    )


def _committed(*cids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(event_type="ConceptCreated", payload={"concept_id": c, "concept_name": c}) for c in cids]


def test_no_edge_is_not_applicable() -> None:
    other = SimpleNamespace(event_type="PatchProposed", payload={})
    result = check_graph_edge_integrity([other], _committed("c-x@v1"))
    assert result.passed is True
    assert result.applicable is False


def test_target_committed_passes() -> None:
    result = check_graph_edge_integrity([_edge("c-a@v1", "c-b@v1")], _committed("c-a@v1", "c-b@v1"))
    assert result.passed is True
    assert result.applicable is True


def test_target_cocreated_in_batch_passes() -> None:
    # target c-b@v1 is co-created in the same batch (not yet in committed records).
    result = check_graph_edge_integrity([_concept("c-b@v1"), _edge("c-a@v1", "c-b@v1")], _committed("c-a@v1"))
    assert result.passed is True


def test_target_external_anchor_passes() -> None:
    result = check_graph_edge_integrity([_edge("c-a@v1", "docs/spec/x.md")], _committed("c-a@v1"))
    assert result.passed is True


def test_unresolvable_target_fails_closed() -> None:
    result = check_graph_edge_integrity([_edge("c-a@v1", "c-ghost@v1", "e-bad")], _committed("c-a@v1"))
    assert result.passed is False
    assert result.applicable is True
    assert result.failure_reason == "edge_target_unresolvable"
    assert result.failure_evidence is not None
    assert result.failure_evidence["edge_id"] == "e-bad"
    assert result.failure_evidence["target_concept_id"] == "c-ghost@v1"
