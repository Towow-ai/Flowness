"""Layer2 缺口3 / Finding A — cross-plan write_set intersection enters the plan-freeze gate.

The intra-plan twin (test_resource_conflict_freeze.py) only catches two tasks in the SAME plan
writing a common entity. This proves the cross-plan gate: when plan B freezes while another
already-frozen, still-live plan A has a task writing the same entity with no serializing edge,
B's freeze is rejected (CLI fail-closed). Modeled on canonical events the freeze checks read.

Red-team intent: prove the gate (1) blocks the real collision, (2) does NOT over-block legitimate
cases (other plan not yet frozen / other task already done / cross-plan ordering edge declared /
re-freezing a plan against its own prior freeze).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.plan_freezed import (
    check_no_cross_plan_resource_conflict,
    run_all_blocking_checks,
)

if TYPE_CHECKING:
    from pathlib import Path

_PLAN_A = "plan-A-first"
_PLAN_B = "plan-B-second"
_SHARED = {"entity_type": "code", "entity_id": "orchestrator.py"}
_A_ONLY = {"entity_type": "code", "entity_id": "a_only.py"}
_B_ONLY = {"entity_type": "code", "entity_id": "b_only.py"}


def _ev(event_type: str, after: dict[str, object]) -> dict[str, object]:
    return {"event_type": event_type, "payload": {"after_state": after}}


def _ev_before(event_type: str, before: dict[str, object]) -> dict[str, object]:
    return {"event_type": event_type, "payload": {"before_state": before}}


def _node(task_id: str, plan_id: str) -> dict[str, object]:
    return _ev("TaskNodeCreated", {"task_id": task_id, "plan_id": plan_id, "task_type": "code_change"})


def _claim(task_id: str, entity: dict[str, str]) -> dict[str, object]:
    return _ev("TaskWriteSetClaimed", {"task_id": task_id, "write_set": [entity]})


def _edge(src: str, tgt: str) -> dict[str, str | object]:
    after = {"source_task_id": src, "target_task_id": tgt, "dependency_type": "resource_conflict"}
    return _ev("TaskDependencyEdgeAdded", after)


def _write_log(towow: Path, events: list[dict[str, object]]) -> None:
    towow.mkdir(parents=True, exist_ok=True)
    with (towow / "events.log").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _scenario(
    *,
    shared_write: bool,
    plan_a_frozen: bool = True,
    cross_plan_edge: bool = False,
    a_task_done: bool = False,
) -> list[dict[str, object]]:
    """Plan A (task T-A1) + plan B (task T-B1, being frozen now → no PlanFreezed for B)."""
    a_entity = _SHARED if shared_write else _A_ONLY
    b_entity = _SHARED if shared_write else _B_ONLY
    events: list[dict[str, object]] = [
        _node("T-A1", _PLAN_A),
        _node("T-B1", _PLAN_B),
        _claim("T-A1", a_entity),
        _claim("T-B1", b_entity),
    ]
    if plan_a_frozen:
        events.append(_ev("PlanFreezed", {"plan_id": _PLAN_A, "frozen_task_ids": ["T-A1"]}))
    if a_task_done:
        events.append(_ev("TaskRunCompleted", {"task_id": "T-A1", "outcome": "success"}))
    if cross_plan_edge:
        events.append(_edge("T-B1", "T-A1"))
    return events


# ── the collision the gate exists to stop ──────────────────────────────────────


def test_cross_plan_shared_write_no_edge_blocks_second_freeze(tmp_path: Path) -> None:
    """Plan A frozen first; plan B freezing now writes the same entity, no edge → B blocked."""
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=True))
    passed, evidence = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert not passed, "second plan to freeze with a colliding write must be rejected"
    assert "T-B1" in evidence
    assert "T-A1" in evidence
    assert "orchestrator.py" in evidence


# ── must NOT over-block (red-team: prove it's not a blunt blocker) ──────────────


def test_cross_plan_with_serializing_edge_passes(tmp_path: Path) -> None:
    """A cross-plan dependency edge declares the ordering → conflict cleared."""
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=True, cross_plan_edge=True))
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert passed


def test_other_plan_not_frozen_passes(tmp_path: Path) -> None:
    """Other plan has the write claim but has NOT frozen (still mutable) → not a commitment → no block."""
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=True, plan_a_frozen=False))
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert passed


def test_other_task_done_success_passes(tmp_path: Path) -> None:
    """Other plan's conflicting task already completed successfully → its write is committed → no
    concurrent collision → liveness filter excludes it."""
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=True, a_task_done=True))
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert passed


def test_disjoint_writes_pass(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=False))
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert passed


def test_self_refreeze_not_blocked_by_own_prior_freeze(tmp_path: Path) -> None:
    """plan amend re-runs the gate; a plan that already has a PlanFreezed must not be blocked by its
    own (same-plan) prior frozen tasks — only OTHER frozen plans count."""
    towow = tmp_path / ".towow"
    events = [
        _node("T-B1", _PLAN_B),
        _node("T-B2", _PLAN_B),
        _claim("T-B1", _SHARED),
        _claim("T-B2", _SHARED),
        _ev("PlanFreezed", {"plan_id": _PLAN_B, "frozen_task_ids": ["T-B1", "T-B2"]}),
    ]
    _write_log(towow, events)
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert passed, "cross-plan check must self-exclude; intra-plan collisions are no_resource_conflict's job"


def test_removed_edge_does_not_clear_conflict(tmp_path: Path) -> None:
    """Fail-closed: a cross-plan edge that was later REMOVED no longer serializes → conflict stands."""
    towow = tmp_path / ".towow"
    events = _scenario(shared_write=True, cross_plan_edge=True)
    removed = {"source_task_id": "T-B1", "target_task_id": "T-A1", "dependency_type": "resource_conflict"}
    events.append(_ev_before("TaskDependencyEdgeRemoved", removed))
    _write_log(towow, events)
    passed, _ = check_no_cross_plan_resource_conflict(towow, _PLAN_B)
    assert not passed, "a removed serializing edge must re-expose the conflict (fail-closed)"


# ── proves the check is really invoked at freeze (acceptance: 真被调, 非仅存在) ──


def test_cross_plan_check_wired_into_freeze_gate(tmp_path: Path) -> None:
    """run_all_blocking_checks includes planning.no_cross_plan_resource_conflict and fails on a
    cross-plan collision (freeze fail-closed via the CLI gate)."""
    towow = tmp_path / ".towow"
    _write_log(towow, _scenario(shared_write=True))
    all_passed, results = run_all_blocking_checks(towow, _PLAN_B)
    check_ids = [c.check_id for c in results]
    assert "planning.no_cross_plan_resource_conflict" in check_ids, "check must be wired into the gate"
    cp = next(c for c in results if c.check_id == "planning.no_cross_plan_resource_conflict")
    assert cp.status == "failed"
    assert not all_passed, "freeze must fail-closed when a cross-plan resource conflict is present"
