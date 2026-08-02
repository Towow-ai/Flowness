"""RUN-038 波2 T-L1-31 — PlanningUncertainty emit + red_line 阻塞 freeze (M-1.3 §3.7 + §14.1.b).

done_criteria (LANDING-PLAN-TASKS): 注册 PLANNING_UNCERTAINTY event + CLI; 存在未解 red_line
uncertainty 时 freeze fail-closed; 现整机制 0 代码。

机制:
  - PlanningUncertainty event (severity ∈ {red_line, advisory}) 记录计划期不确定性。
  - 解决一个 uncertainty = 再 emit 同 uncertainty_id 的 PlanningUncertainty, resolved=true。
    freeze 门按 uncertainty_id 取最新一条状态 (latest-wins)。
  - check_no_unresolved_red_line_uncertainty: plan 存在 severity=red_line 且最新 resolved=false
    的 uncertainty → freeze fail-closed。advisory 不阻塞。解决后 (resolved=true) 才能 freeze。

反 vacuous: 短路掉 red_line 阻塞 (check 恒 pass) → 真挂。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.plan_freezed import (
    check_no_unresolved_red_line_uncertainty,
    run_all_blocking_checks,
)

if TYPE_CHECKING:
    from pathlib import Path

_PLAN = "plan-tl131"


def _uncertainty(
    uid: str, *, severity: str, resolved: bool = False, plan_id: str = _PLAN,
) -> dict[str, object]:
    return {
        "event_type": "PlanningUncertainty",
        "payload": {
            "target_entity_type": "concept",
            "transition_type": "created",
            "uncertainty_id": uid,
            "plan_id": plan_id,
            "description": f"不确定 {uid}",
            "impact": "影响下游 task",
            "severity": severity,
            "resolution_action": "ask_nature",
            "resolved": resolved,
            "blocking_tasks": ["T-A"],
        },
    }


def _write_log(towow: Path, events: list[dict[str, object]]) -> None:
    towow.mkdir(parents=True, exist_ok=True)
    with (towow / "events.log").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ─── schema ─────────────────────────────────────────────────────────────────


def test_planning_uncertainty_schema_validates() -> None:
    from towow.schemas.enums import EventType
    from towow.schemas.payloads.registry import validate_event_payload

    payload = _uncertainty("u-1", severity="red_line")["payload"]
    # registry must map PLANNING_UNCERTAINTY to a real payload model (no KeyError / passthrough).
    validate_event_payload(EventType.PLANNING_UNCERTAINTY, payload)


def test_planning_uncertainty_event_type_registered() -> None:
    from towow.schemas.enums import EventType

    assert EventType.PLANNING_UNCERTAINTY.value == "PlanningUncertainty"


# ─── freeze gate ──────────────────────────────────────────────────────────────


def test_unresolved_red_line_blocks_freeze(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [_uncertainty("u-1", severity="red_line", resolved=False)])
    passed, evidence = check_no_unresolved_red_line_uncertainty(towow, _PLAN)
    assert not passed, "unresolved red_line uncertainty must block freeze"
    assert "u-1" in evidence


def test_resolved_red_line_allows_freeze(tmp_path: Path) -> None:
    """latest-wins: a later resolved=true for the same uncertainty_id clears the block."""
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _uncertainty("u-1", severity="red_line", resolved=False),
        _uncertainty("u-1", severity="red_line", resolved=True),  # resolution
    ])
    passed, _ = check_no_unresolved_red_line_uncertainty(towow, _PLAN)
    assert passed, "resolved red_line uncertainty must no longer block"


def test_advisory_uncertainty_does_not_block(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [_uncertainty("u-1", severity="advisory", resolved=False)])
    passed, _ = check_no_unresolved_red_line_uncertainty(towow, _PLAN)
    assert passed, "advisory (non red_line) uncertainty must not block freeze"


def test_no_uncertainty_passes(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [])
    passed, _ = check_no_unresolved_red_line_uncertainty(towow, _PLAN)
    assert passed


def test_other_plan_uncertainty_ignored(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [_uncertainty("u-x", severity="red_line", plan_id="other-plan")])
    passed, _ = check_no_unresolved_red_line_uncertainty(towow, _PLAN)
    assert passed, "another plan's red_line uncertainty must not block this plan"


def test_wired_into_freeze_gate(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [_uncertainty("u-1", severity="red_line", resolved=False)])
    all_passed, results = run_all_blocking_checks(towow, _PLAN)
    chk = next(c for c in results if c.check_id == "planning.no_unresolved_red_line_uncertainty")
    assert chk.status == "failed"
    assert not all_passed
