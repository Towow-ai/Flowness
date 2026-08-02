"""RUN-038 波2 T-L1-29 — high_risk_tasks_have_review 用真 risk 信号判别 (M-1.3 §3.8).

done_criteria (LANDING-PLAN-TASKS): 构造高风险 task (red-line/状态机变更) 无 review, freeze
被拒; 现判别恒 false 恒 pass。

旧缺陷 (证伪对照, 见 test_OLD_vacuous_*): 旧 check_high_risk_tasks_have_review 用
  is_high_risk = bool(ast.get("risk_surface")) or ast.get("task_type") in ("config_migration","execution")
两个判别都用根本不存在的信号:
  - "risk_surface" 不是 TaskNodeCreatedAfter 的字段 (schema 无此字段 → 真事件上恒 None)。
  - "config_migration"/"execution" 不是 TaskType 合法枚举值 (合法: code_change/design_change/
    doc_change/review/fix/maintain), 真事件 task_type 永不取这俩值。
→ is_high_risk 恒 False → check 恒 pass (high-risk 无 review 也过)。这是恒 pass 假门。

新判别 (真信号, schema 支撑): 一个 task 是高风险当且仅当事件流里有以下任一真信号:
  1. 它被分配了 opus tier (TaskModelTierAssigned.model_tier == opus) — opus = "做错代价高"
     (§10/§13: red-line / 状态机 / invariant / 关键路径), 是 schema 支撑的真风险信号。
  2. 它是 state_machine_transition_dependency 边的端点 (§10 状态机变更信号)。
  3. 它的 opus 分配 matched_opus_factors 含 red_line / state_machine / invariant 因素。
高风险 task 无 review_required=true → freeze 真拒。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.plan_freezed import (
    check_high_risk_tasks_have_review,
    run_all_blocking_checks,
)

if TYPE_CHECKING:
    from pathlib import Path

_PLAN = "plan-tl129"


def _ev(event_type: str, after: dict[str, object]) -> dict[str, object]:
    return {"event_type": event_type, "payload": {"after_state": after}}


def _write_log(towow: Path, events: list[dict[str, object]]) -> None:
    towow.mkdir(parents=True, exist_ok=True)
    with (towow / "events.log").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _task(tid: str, *, review: bool) -> dict[str, object]:
    return _ev("TaskNodeCreated", {
        "task_id": tid, "plan_id": _PLAN, "task_type": "code_change",
        "description": tid, "review_required": review,
    })


def _opus_tier(tid: str, *, factors: list[dict[str, object]] | None = None) -> dict[str, object]:
    return _ev("TaskModelTierAssigned", {
        "task_id": tid, "model_tier": "opus",
        "assignment_reason": "high做错代价",
        "matched_opus_factors": factors or [{"factor": "red_line_obligation", "weight": 3}],
    })


def _sonnet_tier(tid: str) -> dict[str, object]:
    return _ev("TaskModelTierAssigned", {
        "task_id": tid, "model_tier": "sonnet", "assignment_reason": "标准实现",
    })


def _sm_edge(src: str, tgt: str) -> dict[str, object]:
    return _ev("TaskDependencyEdgeAdded", {
        "source_task_id": src, "target_task_id": tgt,
        "dependency_type": "state_machine_transition_dependency",
    })


# ─── 新真判别 (绿) ────────────────────────────────────────────────────────────


def test_opus_task_without_review_fails(tmp_path: Path) -> None:
    """A task assigned opus tier (= high做错代价) but review_required=false → freeze fails."""
    towow = tmp_path / ".towow"
    _write_log(towow, [_task("T-A", review=False), _opus_tier("T-A")])
    passed, evidence = check_high_risk_tasks_have_review(towow, _PLAN)
    assert not passed, "opus (high-risk) task without review must fail the gate"
    assert "T-A" in evidence


def test_opus_task_with_review_passes(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    _write_log(towow, [_task("T-A", review=True), _opus_tier("T-A")])
    passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert passed, "opus task with review_required=true must pass"


def test_state_machine_dep_endpoint_without_review_fails(tmp_path: Path) -> None:
    """A task that is an endpoint of a state_machine_transition_dependency edge is high-risk."""
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _task("T-A", review=False), _task("T-B", review=False),
        _sm_edge("T-A", "T-B"),
    ])
    passed, evidence = check_high_risk_tasks_have_review(towow, _PLAN)
    assert not passed
    # both endpoints touch the state-machine transition → both high-risk, both flagged
    assert "T-A" in evidence or "T-B" in evidence


def test_red_line_factor_without_review_fails(tmp_path: Path) -> None:
    """opus matched_opus_factors carrying a red_line factor → unambiguously high-risk."""
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _task("T-A", review=False),
        _opus_tier("T-A", factors=[{"factor": "red_line_obligation", "weight": 3}]),
    ])
    passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert not passed


def test_low_risk_sonnet_task_without_review_passes(tmp_path: Path) -> None:
    """A plain sonnet task with no risk signal does NOT require review → passes."""
    towow = tmp_path / ".towow"
    _write_log(towow, [_task("T-A", review=False), _sonnet_tier("T-A")])
    passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert passed, "low-risk sonnet task without review must NOT trip the gate (no over-blocking)"


def test_wired_into_freeze_gate(tmp_path: Path) -> None:
    """run_all_blocking_checks fails closed when a high-risk task lacks review."""
    towow = tmp_path / ".towow"
    _write_log(towow, [_task("T-A", review=False), _opus_tier("T-A")])
    all_passed, results = run_all_blocking_checks(towow, _PLAN)
    hr = next(c for c in results if c.check_id == "planning.high_risk_tasks_have_review")
    assert hr.status == "failed"
    assert not all_passed


# ─── latest-wins 回归 (tier 可修正 — R01 dogfood 实撞) ───────────────────────────


def test_downgraded_opus_to_sonnet_not_high_risk(tmp_path: Path) -> None:
    """tier 可修正: 一个 task 先发 opus 占位、随后带 cross_module 因子降 sonnet → 最新档位
    sonnet, 不再 high-risk。逐事件聚合 (旧 bug) 会因早期 opus 事件永久把它钉成 high-risk →
    freeze 永久被拒、re-emit 无法解。latest-wins 取最新一条判风险。R01 freeze 卡死的真根因。
    """
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _task("T-A", review=False),
        _opus_tier("T-A"),                       # 早期占位 opus (后被 supersede)
        _ev("TaskModelTierAssigned", {           # 最新: sonnet + cross_module (无 high-risk 因子)
            "task_id": "T-A", "model_tier": "sonnet",
            "assignment_reason": "机械接线实现",
            "matched_opus_factors": [{"factor": "cross_module", "weight": 1}],
        }),
    ])
    passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert passed, "降级到 sonnet 的 task 不该被早期 opus 历史永久钉成 high-risk (latest-wins)"


def test_downgraded_red_line_factor_cleared(tmp_path: Path) -> None:
    """早期 opus 分配带 state_machine 因子、随后降 sonnet 去掉该因子 → 最新无 high-risk 因子,
    不 high-risk。逐事件聚合会从陈旧 matched_opus_factors 读到 state_machine 永久钉死。
    """
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _task("T-A", review=False),
        _opus_tier("T-A", factors=[{"factor": "state_machine_or_invariant_change", "weight": 3}]),
        _ev("TaskModelTierAssigned", {
            "task_id": "T-A", "model_tier": "sonnet",
            "assignment_reason": "复核后机械实现",
            "matched_opus_factors": [{"factor": "cross_module", "weight": 1}],
        }),
    ])
    passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert passed, "降级后陈旧 state_machine 因子不该让 task 永久 high-risk (latest-wins)"


def test_upgraded_sonnet_to_opus_still_high_risk(tmp_path: Path) -> None:
    """反方向: 先 sonnet 后升 opus → 最新档位 opus, 仍 high-risk (latest-wins 不是放水门)。"""
    towow = tmp_path / ".towow"
    _write_log(towow, [
        _task("T-A", review=False),
        _sonnet_tier("T-A"),
        _opus_tier("T-A"),  # 最新升 opus
    ])
    passed, evidence = check_high_risk_tasks_have_review(towow, _PLAN)
    assert not passed, "最新档位 opus 的 task 必须 high-risk — latest-wins 两向都钉"
    assert "T-A" in evidence


# ─── 旧恒 pass 假门证伪 (反 vacuous) ──────────────────────────────────────────


def test_OLD_vacuous_signals_never_match_real_events(tmp_path: Path) -> None:
    """证伪旧恒 pass: 旧判别用的两个信号在真事件上恒不命中。

    旧码: is_high_risk = bool(ast.get("risk_surface")) or ast.get("task_type") in
          ("config_migration", "execution")

    本测试直接断言这两个信号的前提为假:
      - TaskType 合法枚举不含 config_migration / execution。
      - TaskNodeCreatedAfter schema 不含 risk_surface 字段 (extra=forbid)。
    所以旧判别对任何合法 TaskNodeCreated 恒 False → 旧 check 恒 pass。新判别已替换它。
    """
    from towow.schemas.enums import TaskType
    from towow.schemas.payloads.state_transition import TaskNodeCreatedAfter

    legal = {t.value for t in TaskType}
    assert "config_migration" not in legal
    assert "execution" not in legal

    assert "risk_surface" not in TaskNodeCreatedAfter.model_fields, (
        "risk_surface is not a TaskNodeCreatedAfter field — the old high-risk signal could "
        "never appear on a real (schema-conforming) TaskNodeCreated event"
    )


def test_OLD_vacuous_would_pass_high_risk_no_review(tmp_path: Path) -> None:
    """实证旧恒 pass: 用旧判别逻辑跑 'opus 高风险 + 无 review' 的高风险场景, 旧逻辑会 PASS
    (放过), 新逻辑 FAIL (拦截)。改前后行为对比固化于此, 防恒 pass 假门回潮。
    """
    towow = tmp_path / ".towow"
    events = [_task("T-A", review=False), _opus_tier("T-A")]
    _write_log(towow, events)

    # OLD logic, replayed inline against the same high-risk-no-review events.
    def _after(e: dict[str, object]) -> dict[str, object]:
        p = e.get("payload", {})
        return p.get("after_state", {}) if isinstance(p, dict) else {}

    old_high_risk_no_review: list[str] = []
    for t in (e for e in events if e["event_type"] == "TaskNodeCreated"):
        ast = _after(t)
        old_is_high_risk = bool(ast.get("risk_surface")) or ast.get("task_type") in (
            "config_migration", "execution",
        )
        if old_is_high_risk and not ast.get("review_required", False):
            old_high_risk_no_review.append(str(ast.get("task_id")))
    old_passed = not old_high_risk_no_review
    assert old_passed, "旧判别恒 pass — high-risk(opus)+无 review 被旧逻辑放过 (证伪旧假门)"

    # NEW logic on the identical events → fails closed.
    new_passed, _ = check_high_risk_tasks_have_review(towow, _PLAN)
    assert not new_passed, "新判别拦截同一高风险无 review 场景 (修复实证)"
