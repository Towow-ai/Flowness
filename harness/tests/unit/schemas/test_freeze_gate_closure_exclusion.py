"""readyset-closure-exclusion-contract@v1 — coverage_complete / all_tasks_have_model_tier 排除 closed task.

T-PRW-TEST-DEBUG retired-close 实证: 该契约在 resource-conflict 两道门与 dispatch 层已实施,
但 coverage_complete 与 all_tasks_have_model_tier 漏了 —— 一个 gate-verified closed
(retired/done_elsewhere 终态) 的 task 永不执行, 却仍被这两道门当作"缺包/缺 tier"拒 freeze,
把已合规退役的占位 task 变成永久 freeze 阻塞。本文件钉住排除语义 (真 flat TaskNodeClosed 排除;
stub-rewrap close 不算 — 复用 closed_task_ids 的 anti-fake-done 判据)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.plan_freezed import (
    build_coverage_matrix,
    check_all_tasks_have_model_tier,
)

if TYPE_CHECKING:
    from pathlib import Path

_PLAN = "plan-closure-exclusion"


def _ev(event_type: str, after: dict[str, object]) -> dict[str, object]:
    return {"event_type": event_type, "payload": {"after_state": after}}


def _write_log(towow: Path, events: list[dict[str, object]]) -> None:
    towow.mkdir(parents=True, exist_ok=True)
    with (towow / "events.log").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _closed(task_id: str) -> dict[str, object]:
    return _ev("TaskNodeClosed", {"task_id": task_id, "reason": "retired"})


def _base_events(*, close_placeholder: bool) -> list[dict[str, object]]:
    """task-real (有 tier 有包) + task-placeholder (无 tier 无包, 可选已关闭)。"""
    events: list[dict[str, object]] = [
        _ev("TaskNodeCreated", {"task_id": "task-real", "plan_id": _PLAN, "task_type": "code_change"}),
        _ev("TaskNodeCreated", {"task_id": "task-placeholder", "plan_id": _PLAN, "task_type": "doc_change"}),
        _ev("TaskModelTierAssigned", {"task_id": "task-real", "model_tier": "opus"}),
        _ev(
            "TaskPackagePublished",
            {
                "task_id": "task-real",
                "package_content": {
                    "done_criteria": [
                        {
                            "criterion": "条件达成",
                            "evidence_ref": {
                                "source_type": "brief.completion_condition",
                                "source_id": "brief-x",
                                "quoted_claim": "条件",
                            },
                        }
                    ],
                },
            },
        ),
        _ev(
            "InterviewBriefPublished",
            {"brief_id": "brief-x", "goal_completion_condition": "条件; evidence: report"},
        ),
        _ev("PlanFreezed", {"plan_id": _PLAN + "-link", "frozen_task_ids": []}),
    ]
    if close_placeholder:
        events.append(_closed("task-placeholder"))
    return events


def test_model_tier_gate_excludes_closed_task(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, _base_events(close_placeholder=True))
    passed, evidence = check_all_tasks_have_model_tier(towow, _PLAN)
    assert passed, evidence


def test_model_tier_gate_still_blocks_open_task_without_tier(tmp_path: Path) -> None:
    """回归: 未关闭的缺 tier task 照拒 (排除只对 closed 生效)。"""
    towow = tmp_path / ".towow"
    _write_log(towow, _base_events(close_placeholder=False))
    passed, evidence = check_all_tasks_have_model_tier(towow, _PLAN)
    assert not passed
    assert "task-placeholder" in evidence


def test_coverage_gate_excludes_closed_task_from_missing_pkg(tmp_path: Path) -> None:
    """closed task 缺包不再算 ready-frontier 缺口 (它永不执行, 不需要包)。"""
    towow = tmp_path / ".towow"
    _write_log(towow, _base_events(close_placeholder=True))
    _passed, _matrix, evidence = build_coverage_matrix(towow, _PLAN)
    # 关键断言: 不再以 task-placeholder 缺包为由拒 (brief 解析等其余环节不在本测试范围,
    # 只要失败理由不是占位 task 的 ready-frontier 缺包即可)。
    assert "ready-frontier tasks without TaskPackagePublished: ['task-placeholder']" not in evidence


def test_coverage_gate_still_blocks_open_unpackaged_frontier_task(tmp_path: Path) -> None:
    """回归: 未关闭的 ready-frontier 缺包 task 照拒。"""
    towow = tmp_path / ".towow"
    _write_log(towow, _base_events(close_placeholder=False))
    passed, _matrix, evidence = build_coverage_matrix(towow, _PLAN)
    assert not passed
    assert "task-placeholder" in evidence
