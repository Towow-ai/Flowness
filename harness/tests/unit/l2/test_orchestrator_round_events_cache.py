"""f-orchestrator-round-events-as-dicts-recompute-per-event-quadratic 回归测试。

背景 (ledger-perf-diagnosis 确诊, 2026-07-17): `OrchestratorDaemon._ready_execution_decisions`
对扫描批次里【每一个】PlanFreezed / TaskRunCompleted(success) 事件都重新调用
`_events_as_dicts()` → 模块级 `_all_events_as_dicts(event_log)`, 把整条账本重新展开成 dict
列表 (实测 62.9 万事件规模下单次 ~28s)。一轮扫描若命中 N 个此类事件, 就重复付 N 次这个全账本
代价 —— 生产上一轮 1409.4s 里的大头就是它 (sweep 本身只占 15.65s, 已被节流保护)。

修法: `OrchestratorDaemon` 加一个每次 `_scan()` 重置一次的 `_events_dicts_cache`
(与既有的 `_started_ids_cache` 同款模式), `_events_as_dicts()` 命中缓存就不重算。`_scan()`
内部读写不变 (`_route_event` 是纯读, 扫描期间账本不会被自己写脏), 故这是纯性能修复,
不改变任何派发决策。

本文件钉两件事:
  1. test_events_as_dicts_memoized_within_one_scan — 一次 `_scan()` 命中多个
     TaskRunCompleted(success) 事件时, 底层 `_all_events_as_dicts` 只被真正调用一次。
  2. test_ready_set_fanout_unchanged_with_cache — 缓存生效后, PlanFreezed 的 ready-set
     fan-out 决策与语义未变 (behavior equivalence)。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l2 import orchestrator
from towow.l2.orchestrator import OrchestratorDaemon, ensure_orchestrator_layout
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _make_towow(tmp_path: Path) -> tuple[Path, EventLog]:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "events.log").write_text("", encoding="utf-8")
    event_log = EventLog(towow / "events.log")
    ensure_orchestrator_layout(towow)
    return towow, event_log


def _stub(event_log: EventLog, kind: str, after_state: dict[str, object]) -> str:
    """Emit a stub-rewrap NodeTouched carrying `kind` + after_state (same pattern as
    test_dispatch_one_task.py's helper — matches production's real on-disk shape)."""
    intent = EventIntent(
        local_intent_id=f"{kind.lower()}-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": str(after_state.get("task_id") or after_state.get("plan_id") or kind),
            "touch_type": "write",
            "kind": kind,
            "stub_original_payload": {"kind": kind, "after_state": after_state, **after_state},
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-emitter"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=str(after_state.get("task_id") or after_state.get("plan_id") or kind),
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def test_events_as_dicts_memoized_within_one_scan(
    tmp_path: Path, monkeypatch: object,
) -> None:
    """一次 _scan() 里 3 个 TaskRunCompleted(success) 事件, 只应触发 1 次全账本物化。"""
    _towow, event_log = _make_towow(tmp_path)

    for i in range(3):
        _stub(
            event_log,
            "TaskRunCompleted",
            {"task_id": f"T-quad-{i}", "outcome": "success"},
        )

    call_count = {"n": 0}
    real_all_events_as_dicts = orchestrator._all_events_as_dicts

    def _counting(event_log_arg: EventLog) -> list[dict[str, object]]:
        call_count["n"] += 1
        return real_all_events_as_dicts(event_log_arg)

    monkeypatch.setattr(orchestrator, "_all_events_as_dicts", _counting)

    daemon = OrchestratorDaemon(event_log, last_processed_seq=0)
    daemon.run_once()

    assert call_count["n"] == 1, (
        f"expected exactly 1 full-ledger materialization per scan (memoized), "
        f"got {call_count['n']} for 3 qualifying events in the batch — "
        "cache regression (f-orchestrator-round-events-as-dicts-recompute-per-event-quadratic)"
    )


def test_ready_set_fanout_unchanged_with_cache(tmp_path: Path) -> None:
    """缓存生效后, PlanFreezed → ready-set fan-out 的派发决策语义不变 (回归 behavior equivalence)。"""
    _towow, event_log = _make_towow(tmp_path)

    plan_id = "plan-cache-equiv"
    task_a, task_b = "T-cache-a", "T-cache-b"
    _stub(event_log, "PlanFreezed", {"plan_id": plan_id})
    _stub(
        event_log, "TaskNodeCreated",
        {"task_id": task_a, "task_type": "code_change", "plan_id": plan_id},
    )
    _stub(
        event_log, "TaskNodeCreated",
        {"task_id": task_b, "task_type": "code_change", "plan_id": plan_id},
    )
    _stub(event_log, "TaskModelTierAssigned", {"task_id": task_a, "model_tier": "sonnet"})
    _stub(event_log, "TaskModelTierAssigned", {"task_id": task_b, "model_tier": "sonnet"})

    daemon = OrchestratorDaemon(event_log, last_processed_seq=0)
    daemon.run_once()

    dispatched_task_ids = {d.task_id for d in daemon.decisions if d.task_id}
    assert dispatched_task_ids == {task_a, task_b}, (
        f"ready-set fan-out changed under cache: expected both independent tasks ready, "
        f"got {dispatched_task_ids}"
    )
    for d in daemon.decisions:
        if d.task_id in {task_a, task_b}:
            assert d.dispatch_to == "execution"
