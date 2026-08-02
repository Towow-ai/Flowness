"""RUN-038 波2 T-L1-33 — 分批/渐进发布门 (M-1.3 §11.2).

done_criteria (LANDING-PLAN-TASKS): dep 未完成的下游 task package-publish 被拒, dep 完成后才允许;
现 batch 字段存但无渐进强制。

§11.2 progressive publication: "前置 task 完成后才发布依赖它的 task"。
机制 (check_dependencies_completed_for_publish):
  - 边 source→target = source 必须先于 target (有向 DAG, 与 critical-path/cycle 检测同向)。
    所以发布 task T 时, 它的前置 = 所有 S 使得边 S→T 存在 (S 先于 T)。
  - 前置 task 完成 = 该前置有 TaskRunCompleted 且 outcome=success。
  - 仅 strength=hard 的依赖强制 (medium 可先做但 re-check, 不阻塞发布)。
  - 任一 hard 前置未 success-完成 → 该下游 publish 被门拒 (blocker)。

反 vacuous: 短路掉前置完成检查 → 测真挂。
"""

from __future__ import annotations

from towow.schemas.payloads.task_package import check_dependencies_completed_for_publish

_PLAN = "plan-tl133"


def _task(tid: str) -> dict[str, object]:
    return {
        "event_type": "TaskNodeCreated",
        "payload": {"after_state": {"task_id": tid, "plan_id": _PLAN, "task_type": "code_change"}},
    }


def _edge(src: str, tgt: str, strength: str = "hard") -> dict[str, object]:
    return {
        "event_type": "TaskDependencyEdgeAdded",
        "payload": {"after_state": {
            "source_task_id": src, "target_task_id": tgt,
            "dependency_type": "ordering", "strength": strength,
        }},
    }


def _run_completed(tid: str, outcome: str = "success") -> dict[str, object]:
    return {
        "event_type": "TaskRunCompleted",
        "payload": {"after_state": {"task_id": tid, "run_id": f"run-{tid}", "outcome": outcome}},
    }


def test_downstream_blocked_while_prereq_incomplete() -> None:
    """Publishing downstream task-b (depends on task-a) is blocked while task-a is not completed."""
    events = [_task("task-a"), _task("task-b"), _edge("task-a", "task-b")]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert blockers, "downstream publish must be blocked while a hard prerequisite is incomplete"
    assert any("task-a" in b for b in blockers)


def test_downstream_allowed_after_prereq_success() -> None:
    events = [
        _task("task-a"), _task("task-b"), _edge("task-a", "task-b"),
        _run_completed("task-a", "success"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert not blockers, "downstream publish must be allowed after the prerequisite completed success"


def test_prereq_aborted_does_not_count_as_complete() -> None:
    """An aborted (non-success) TaskRunCompleted is NOT a completion → still blocks."""
    events = [
        _task("task-a"), _task("task-b"), _edge("task-a", "task-b"),
        _run_completed("task-a", "aborted_unrecoverable"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert blockers, "a non-success run must not unblock the downstream publish"


def test_medium_strength_dependency_does_not_block() -> None:
    """strength=medium = 可先做但 re-check — does not block progressive publication."""
    events = [_task("task-a"), _task("task-b"), _edge("task-a", "task-b", strength="medium")]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert not blockers, "medium-strength dependency must not block publish (only hard does)"


def test_upstream_with_no_prereqs_publishes_freely() -> None:
    """task-a has no incoming edge → no prerequisite → publishes with no blocker."""
    events = [_task("task-a"), _task("task-b"), _edge("task-a", "task-b")]
    blockers = check_dependencies_completed_for_publish("task-a", events, _PLAN)
    assert not blockers, "a task with no prerequisites must publish freely"


def test_multiple_prereqs_all_must_complete() -> None:
    """task-c depends on both task-a and task-b; only one completed → still blocked."""
    events = [
        _task("task-a"), _task("task-b"), _task("task-c"),
        _edge("task-a", "task-c"), _edge("task-b", "task-c"),
        _run_completed("task-a", "success"),  # task-b not done
    ]
    blockers = check_dependencies_completed_for_publish("task-c", events, _PLAN)
    assert blockers
    assert any("task-b" in b for b in blockers)
    assert not any("task-a" in b for b in blockers), "task-a is done → not in blockers"


def test_publication_gate_threads_task_id_and_blocks(tmp_path: object) -> None:
    """End-to-end through validate_publication_gate: a downstream package with an incomplete hard
    prerequisite is rejected (is_self_contained=false) even when otherwise self-contained.
    """
    from tests.unit.schemas._tl1_33_pkg_fixture import minimal_self_contained_content
    from towow.schemas.payloads.task_package import compute_package_hash, validate_publication_gate

    content = minimal_self_contained_content()
    events = [_task("task-a"), _task("task-b"), _edge("task-a", "task-b")]
    h = compute_package_hash(content)
    result = validate_publication_gate(
        content, h, events=events, plan_id=_PLAN, publishing_task_id="task-b",
    )
    assert not result.is_self_contained
    assert any("task-a" in b for b in result.blockers)

    # after task-a completes, the same package publishes
    events.append(_run_completed("task-a", "success"))
    result2 = validate_publication_gate(
        content, h, events=events, plan_id=_PLAN, publishing_task_id="task-b",
    )
    assert result2.is_self_contained, result2.blockers


# ── dogfood R07 2026-06-14: gate must respect TaskDependencyEdgeRemoved (net-active edges) ──────
def _edge_removed(src: str, tgt: str, dep_type: str = "ordering") -> dict[str, object]:
    return {
        "event_type": "TaskDependencyEdgeRemoved",
        "payload": {"before_state": {
            "source_task_id": src, "target_task_id": tgt, "dependency_type": dep_type,
        }},
    }


def test_removed_edge_does_not_phantom_block() -> None:
    """An edge added then removed must NOT count as a prerequisite (net-active = removed)."""
    events = [_task("task-a"), _task("task-b"), _edge("task-a", "task-b"), _edge_removed("task-a", "task-b")]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert blockers == [], blockers


def test_corrected_edge_direction_only_active_counts() -> None:
    """Reverse-correction (remove a→b wrong, add b→a) must leave only the active edge in force:
    publishing task-a (now downstream of b) blocks; publishing task-b (now the root) is free.
    This is the exact R07 dogfood scenario that previously dead-locked the whole plan."""
    events = [
        _task("task-a"), _task("task-b"),
        _edge("task-a", "task-b"),            # wrong direction
        _edge_removed("task-a", "task-b"),    # corrected away
        _edge("task-b", "task-a"),            # correct direction
    ]
    # task-b is now the prerequisite (root) → publishes freely
    assert check_dependencies_completed_for_publish("task-b", events, _PLAN) == []
    # task-a now depends on task-b (incomplete) → blocked
    blockers_a = check_dependencies_completed_for_publish("task-a", events, _PLAN)
    assert any("task-b" in b for b in blockers_a), blockers_a


def test_removal_cannot_bypass_active_prereq() -> None:
    """Red-team: removing an UNRELATED edge must not unblock a downstream whose real prereq is
    still active and incomplete. Removal is not a bypass."""
    events = [
        _task("task-a"), _task("task-b"), _task("task-c"),
        _edge("task-a", "task-c"),            # real, active prereq (incomplete)
        _edge("task-b", "task-c"),            # second prereq
        _edge_removed("task-b", "task-c"),    # only this one retracted
    ]
    blockers = check_dependencies_completed_for_publish("task-c", events, _PLAN)
    # task-a still blocks; task-b no longer does
    assert any("task-a" in b for b in blockers), blockers
    assert not any("task-b" in b for b in blockers), blockers


def test_readd_after_remove_counts_again() -> None:
    """Add → remove → add again: the edge is net-active and must block (last event wins)."""
    events = [
        _task("task-a"), _task("task-b"),
        _edge("task-a", "task-b"), _edge_removed("task-a", "task-b"), _edge("task-a", "task-b"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert any("task-a" in b for b in blockers), blockers


# ── D-1 bootstrap fix (T-LKC chain investigation, 01-reconciliation/error-due-diligence-2026-07-13/
# reports/T-LKC-chain-investigation.md 问3/问4): the §11.2 gate must recognize TaskNodeClosed
# (reason=done_elsewhere) as an equally-honest terminal state for a hard prerequisite, aligned with
# the dispatch layer's T-DEC-3 contract (execution_dispatch.compute_ready_tasks already folds
# closed_task_ids into its "satisfied" set — this gate previously did not, which is exactly why
# T-LKC-05's honest aborted_external + later TaskNodeClosed(done_elsewhere) could never unblock
# T-LKC-06/07's publish). Before this fix, case ① below fails red (pinning the bug).
def _closed(tid: str, reason: str = "done_elsewhere") -> dict[str, object]:
    return {
        "event_type": "TaskNodeClosed",
        "payload": {"after_state": {"task_id": tid, "reason": reason}},
    }


def test_prereq_closed_done_elsewhere_unblocks_publish() -> None:
    """① prereq aborted_external + a gate-verified TaskNodeClosed(done_elsewhere) → publish gate
    must treat the prerequisite as satisfied, same as the dispatch-layer T-DEC-3 contract."""
    events = [
        _task("task-a"), _task("task-b"), _edge("task-a", "task-b"),
        _run_completed("task-a", "aborted_external"),
        _closed("task-a", "done_elsewhere"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert not blockers, (
        "a TaskNodeClosed(done_elsewhere) prerequisite must satisfy progressive publication "
        f"(T-DEC-3 alignment) — got blockers: {blockers}"
    )


def test_prereq_aborted_without_closure_still_blocks() -> None:
    """② prereq aborted, no TaskNodeClosed at all → still rejected (closure must be real, not
    inferred from the mere presence of a non-success run)."""
    events = [
        _task("task-a"), _task("task-b"), _edge("task-a", "task-b"),
        _run_completed("task-a", "aborted_external"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert blockers, "an aborted prerequisite with no accepted TaskNodeClosed must still block"
    assert any("task-a" in b for b in blockers)


def test_pure_success_prereq_unaffected_by_closure_union() -> None:
    """③ original success-only path is untouched by folding in closed_task_ids."""
    events = [
        _task("task-a"), _task("task-b"), _edge("task-a", "task-b"),
        _run_completed("task-a", "success"),
    ]
    blockers = check_dependencies_completed_for_publish("task-b", events, _PLAN)
    assert not blockers, "pure success-path behavior must be unchanged by the T-DEC-3 union"
