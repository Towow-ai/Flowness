"""RUN-038 波2 T-L1-27 — CriticalPathIdentified 最长链算法 (M-1.3 §3.5).

done_criteria (LANDING-PLAN-TASKS): 关键路径由算法从 dep 图计算 (而非人传 --task) 并标
alternative_critical_paths/estimated_makespan; 现靠人 --task 传。

算法契约 (compute_critical_path):
  - 边 source→target = source 必须先于 target (有向 DAG, 与 check_no_circular_dependency 同向)。
  - 关键路径 = 图中最长链 (节点数最多的 source→...→target 路径)。
  - 多条等长最长链都识别 (alternative_critical_paths)。
  - makespan = 最长链的节点数 (启发式: 每 task 1 单位)。

反 vacuous: 构造一条短链 + 一条长链, 算法必须挑长链 (短路成"任意链/第一条"则测真挂)。
"""

from __future__ import annotations

from towow.schemas.payloads.plan_freezed import compute_critical_path

_PLAN = "plan-tl127"


def _task(tid: str) -> dict[str, object]:
    return {
        "event_type": "TaskNodeCreated",
        "payload": {"after_state": {"task_id": tid, "plan_id": _PLAN, "task_type": "code_change"}},
    }


def _edge(src: str, tgt: str) -> dict[str, object]:
    return {
        "event_type": "TaskDependencyEdgeAdded",
        "payload": {"after_state": {"source_task_id": src, "target_task_id": tgt}},
    }


def test_longest_chain_picked_over_short_chain() -> None:
    """A→B→C→D (len 4) vs E→F (len 2): algorithm must return the long chain, not any chain."""
    events = [
        _task("A"), _task("B"), _task("C"), _task("D"), _task("E"), _task("F"),
        _edge("A", "B"), _edge("B", "C"), _edge("C", "D"),
        _edge("E", "F"),
    ]
    result = compute_critical_path(events, _PLAN)
    assert result.makespan == 4, f"makespan must be longest-chain length, got {result.makespan}"
    assert result.primary_path == ["A", "B", "C", "D"], (
        f"primary path must be the longest chain, got {result.primary_path}"
    )
    # The short chain E→F must NOT be the answer.
    assert result.primary_path != ["E", "F"]


def test_multiple_equal_length_critical_paths_all_listed() -> None:
    """Two disjoint chains of equal max length → both reported as critical paths."""
    events = [
        _task("A"), _task("B"), _task("C"),
        _task("X"), _task("Y"), _task("Z"),
        _edge("A", "B"), _edge("B", "C"),
        _edge("X", "Y"), _edge("Y", "Z"),
    ]
    result = compute_critical_path(events, _PLAN)
    assert result.makespan == 3
    all_paths = [result.primary_path, *result.alternative_paths]
    assert ["A", "B", "C"] in all_paths
    assert ["X", "Y", "Z"] in all_paths
    assert len(all_paths) == 2, f"both equal-length paths must be listed, got {all_paths}"


def test_diamond_longest_path() -> None:
    """Diamond: A→B→D and A→C→D both len 3 (A,B,D / A,C,D) — both critical."""
    events = [
        _task("A"), _task("B"), _task("C"), _task("D"),
        _edge("A", "B"), _edge("A", "C"), _edge("B", "D"), _edge("C", "D"),
    ]
    result = compute_critical_path(events, _PLAN)
    assert result.makespan == 3
    all_paths = [result.primary_path, *result.alternative_paths]
    assert ["A", "B", "D"] in all_paths
    assert ["A", "C", "D"] in all_paths


def test_single_node_no_edges() -> None:
    """A plan with one task and no edges → makespan 1, that single task is the path."""
    events = [_task("solo")]
    result = compute_critical_path(events, _PLAN)
    assert result.makespan == 1
    assert result.primary_path == ["solo"]
    assert result.alternative_paths == []


def test_bottleneck_highest_fan_in() -> None:
    """D has fan-in 2 (B→D, C→D) → D is a bottleneck."""
    events = [
        _task("A"), _task("B"), _task("C"), _task("D"),
        _edge("A", "B"), _edge("A", "C"), _edge("B", "D"), _edge("C", "D"),
    ]
    result = compute_critical_path(events, _PLAN)
    assert "D" in result.bottlenecks


def test_only_plan_scoped_tasks_and_edges() -> None:
    """Tasks/edges from other plans are ignored (no cross-plan chain leakage)."""
    other = {
        "event_type": "TaskNodeCreated",
        "payload": {"after_state": {"task_id": "OTHER", "plan_id": "other-plan", "task_type": "code_change"}},
    }
    events = [
        _task("A"), _task("B"), other,
        _edge("A", "B"),
        # edge referencing a foreign task must not extend the chain
        {"event_type": "TaskDependencyEdgeAdded",
         "payload": {"after_state": {"source_task_id": "B", "target_task_id": "OTHER"}}},
    ]
    result = compute_critical_path(events, _PLAN)
    assert result.makespan == 2
    assert result.primary_path == ["A", "B"]
