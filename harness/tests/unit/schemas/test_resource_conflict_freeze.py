"""RUN-031 0-D T-L1-25 — cross-task write_set intersection enters the plan-freeze gate.

done_criteria (LANDING-PLAN-TASKS): 构造两 task write_set 交集，plan freeze 因冲突 fail-closed;
现 claims 空直接 skip,无跨 plan 检测。

check_no_resource_conflict is the new freeze blocking_check (M-1.3 §8 / §3.3). It reads the same
events.log the other freeze checks read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.plan_freezed import check_no_resource_conflict, run_all_blocking_checks

if TYPE_CHECKING:
    from pathlib import Path

_PLAN = "plan-tl125"


def _ev(event_type: str, after: dict[str, object]) -> dict[str, object]:
    return {"event_type": event_type, "payload": {"after_state": after}}


def _write_log(towow: Path, events: list[dict[str, object]]) -> None:
    (towow).mkdir(parents=True, exist_ok=True)
    with (towow / "events.log").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _two_tasks(*, shared_write: bool, ordered: bool) -> list[dict[str, object]]:
    e1 = "concept:shared@v1" if shared_write else "concept:a-only@v1"
    e2 = "concept:shared@v1" if shared_write else "concept:b-only@v1"
    events: list[dict[str, object]] = [
        _ev("TaskNodeCreated", {"task_id": "T-A", "plan_id": _PLAN, "task_type": "code_change", "description": "a"}),
        _ev("TaskNodeCreated", {"task_id": "T-B", "plan_id": _PLAN, "task_type": "code_change", "description": "b"}),
        _ev("TaskWriteSetClaimed", {"task_id": "T-A", "write_set": [{"entity_type": "concept", "entity_id": e1.split(":")[1]}]}),
        _ev("TaskWriteSetClaimed", {"task_id": "T-B", "write_set": [{"entity_type": "concept", "entity_id": e2.split(":")[1]}]}),
    ]
    if ordered:
        events.append(
            _ev("TaskDependencyEdgeAdded", {"source_task_id": "T-A", "target_task_id": "T-B", "dependency_type": "resource_conflict"}),
        )
    return events


def test_intersecting_write_sets_no_ordering_fails(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, _two_tasks(shared_write=True, ordered=False))
    passed, evidence = check_no_resource_conflict(towow, _PLAN)
    assert not passed
    assert "T-A" in evidence and "T-B" in evidence


def test_intersecting_write_sets_with_ordering_edge_passes(tmp_path: Path) -> None:
    """An explicit dependency edge serializes the pair → no parallel conflict."""
    towow = tmp_path / ".towow"
    _write_log(towow, _two_tasks(shared_write=True, ordered=True))
    passed, _ = check_no_resource_conflict(towow, _PLAN)
    assert passed


def test_disjoint_write_sets_pass(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, _two_tasks(shared_write=False, ordered=False))
    passed, _ = check_no_resource_conflict(towow, _PLAN)
    assert passed


def test_resource_conflict_check_wired_into_freeze_gate(tmp_path: Path) -> None:
    """run_all_blocking_checks includes planning.no_resource_conflict and fails on intersection."""
    towow = tmp_path / ".towow"
    _write_log(towow, _two_tasks(shared_write=True, ordered=False))
    all_passed, results = run_all_blocking_checks(towow, _PLAN)
    rc = next(c for c in results if c.check_id == "planning.no_resource_conflict")
    assert rc.status == "failed"
    assert not all_passed, "freeze must fail-closed when a resource conflict is present"
