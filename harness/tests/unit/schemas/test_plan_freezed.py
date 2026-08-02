"""F7 fix — PlanFreezedPayload + 5 项 blocking_check function 单测.

verify F7 closure_criteria:
  - EventType.PLAN_FREEZED 注册
  - PlanFreezedPayload pydantic 定义 round-trip
  - 5 项 blocking_check function 行为 (pass / fail evidence)
  - CLI plan freeze 命令存在 (integration test 留 tests/integration/)
"""

from __future__ import annotations

import json
import uuid as _uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.payloads.plan_freezed import (
    MIGRATION_INVARIANT_CONCEPT_ID,
    PlanBatchInfo,
    PlanBlockingCheck,
    PlanFreezedPayload,
    _resolve_plan_frozen_invariant_observables,
    build_coverage_matrix,
    check_all_tasks_self_contained,
    check_cia_dependency_scan,
    check_coverage_complete,
    check_critical_path_identified,
    check_high_risk_tasks_have_review,
    check_migration_old_path_removal,
    check_no_circular_dependency,
    cia_dependency_scan,
    is_old_path_removal_criterion,
    migration_class_task_ids,
    migration_concept_ids,
    resolve_plan_brief_completion_condition,
    run_all_blocking_checks,
    task_migration_old_path_criteria,
)


def _pkg_event(task_id: str, *, quoted: str, with_content: bool = True) -> dict:
    """A TaskPackagePublished event with §3.6 package_content whose done_criterion quotes `quoted`."""
    after: dict = {"task_id": task_id, "package_hash": "sha256:x", "is_self_contained": True}
    if with_content:
        after["package_content"] = {
            "task_type": "code_change",
            "description": "d",
            "done_criteria": [
                {
                    "criterion_id": f"{task_id}-dc",
                    "description": "x",
                    "verification_type": "test_passes",
                    "evidence_ref": {
                        "source_type": "brief.completion_condition",
                        "source_id": "obs-1",
                        "quoted_claim": quoted,
                    },
                },
            ],
            "read_set": [{"entity_type": "c", "entity_id": "a", "derived_from": "cr"}],
            "write_set": [{"entity_type": "f", "entity_id": "b", "derived_from": "dc"}],
            "model_tier": "sonnet",
            "model_tier_rationale": "r",
            "review_required": False,
        }
    return {"event_type": "TaskPackagePublished", "payload": {"after_state": after}}


def _brief_chain_events(plan_id: str, gcc: str) -> list[dict]:
    """Minimal events linking plan_id → consensus(brief_id) → InterviewBriefPublished(gcc)."""
    return [
        {
            "event_id": "evt-brief-1",
            "event_type": "InterviewBriefPublished",
            "payload": {"brief_id": "brief-abc", "goal_completion_condition": gcc},
        },
        {
            "event_type": "EngineeringConsensusFreezed",
            "payload": {"after_state": {"plan_id": plan_id, "brief_event_id": "brief-abc"}},
        },
    ]


def test_event_type_plan_freezed_registered() -> None:
    assert EventType.PLAN_FREEZED.value == "PlanFreezed"


def test_plan_freezed_payload_minimum_valid() -> None:
    p = PlanFreezedPayload(
        plan_id="plan-test",
        frozen_task_ids=["task-a"],
        batch_info=PlanBatchInfo(batch_number=1, is_final_batch=True),
        self_check_passed=True,
        blocking_checks=[
            PlanBlockingCheck(check_id="planning.all_tasks_self_contained", status="passed", evidence="ok"),
        ],
    )
    assert p.kind == "PlanFreezed"
    assert p.batch_info.batch_number == 1


def test_plan_blocking_check_status_strict() -> None:
    with pytest.raises(ValidationError):
        PlanBlockingCheck(check_id="x", status="invalid", evidence="ok")


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    """Helper — write events to tmp_path/.towow/events.log."""
    towow = tmp_path / ".towow"
    towow.mkdir()
    log = towow / "events.log"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return towow


def test_check_all_tasks_self_contained_pass(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        {"event_type": "TaskReadSetClaimed", "payload": {"after_state": {"task_id": "t1"}}},
        {"event_type": "TaskWriteSetClaimed", "payload": {"after_state": {"task_id": "t1"}}},
    ])
    passed, evidence = check_all_tasks_self_contained(towow, "p1")
    assert passed is True
    assert "1 tasks" in evidence


def test_check_all_tasks_self_contained_fail_missing_write(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        {"event_type": "TaskReadSetClaimed", "payload": {"after_state": {"task_id": "t1"}}},
        # no write claim
    ])
    passed, evidence = check_all_tasks_self_contained(towow, "p1")
    assert passed is False
    assert "t1" in evidence


def test_check_coverage_complete_fail_no_packages(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
    ])
    passed, evidence = check_coverage_complete(towow, "p1")
    assert passed is False
    assert "no TaskPackagePublished" in evidence


def _node(task_id: str, plan_id: str = "p1") -> dict:
    return {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": task_id, "plan_id": plan_id}}}


def _edge(src: str, tgt: str) -> dict:
    return {
        "event_type": "TaskDependencyEdgeAdded",
        "payload": {"after_state": {"source_task_id": src, "target_task_id": tgt}},
    }


def test_check_no_circular_dependency_pass(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [_node("t1"), _node("t2"), _edge("t1", "t2")])
    passed, _evidence = check_no_circular_dependency(towow, "p1")
    assert passed is True


def test_check_no_circular_dependency_fail_cycle(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [_node("t1"), _node("t2"), _edge("t1", "t2"), _edge("t2", "t1")])
    passed, evidence = check_no_circular_dependency(towow, "p1")
    assert passed is False
    assert "cycle" in evidence


def test_check_critical_path_identified_fail(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
    ])
    passed, evidence = check_critical_path_identified(towow, "p1")
    assert passed is False
    assert "no CriticalPathIdentified" in evidence


def test_check_critical_path_identified_pass(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "CriticalPathIdentified", "payload": {"after_state": {"plan_id": "p1", "path": ["t1", "t2"]}}},
    ])
    passed, _ = check_critical_path_identified(towow, "p1")
    assert passed is True


def test_check_high_risk_tasks_have_review_fail(tmp_path: Path) -> None:
    # T-L1-29: high-risk is now keyed off a REAL signal (opus tier = 做错代价高), not the prior
    # vacuous task_type="execution" (not a valid TaskType) which made the check 恒 pass.
    towow = _write_events(tmp_path, [
        {
            "event_type": "TaskNodeCreated",
            "payload": {
                "after_state": {
                    "task_id": "t1", "plan_id": "p1", "task_type": "code_change", "review_required": False,
                },
            },
        },
        {
            "event_type": "TaskModelTierAssigned",
            "payload": {
                "after_state": {
                    "task_id": "t1", "model_tier": "opus", "assignment_reason": "高风险",
                    "matched_opus_factors": [{"factor": "red_line_obligation", "weight": 3}],
                },
            },
        },
    ])
    passed, evidence = check_high_risk_tasks_have_review(towow, "p1")
    assert passed is False
    assert "t1" in evidence


def test_run_all_blocking_checks_returns_5_results(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
    ])
    all_passed, results = run_all_blocking_checks(towow, "p1")
    # RUN-031 T-L1-25 added no_resource_conflict; RUN-038 波2 T-L1-26 added
    # model_tier_assignments_have_evidence; T-L1-31 added no_unresolved_red_line_uncertainty;
    # M-2.1 §4.1 caller-2 added cia_dependency_scan; T-LRF-12 added migration_old_path_removal;
    # Layer2-缺口3 added no_cross_plan_resource_conflict; T-RMD-FIX2 (修2) added
    # new_capability_livefire_machine_check → 13 checks.
    assert len(results) == 13
    assert all_passed is False  # missing many things
    check_ids = [r.check_id for r in results]
    assert "planning.all_tasks_self_contained" in check_ids
    assert "planning.coverage_complete" in check_ids
    assert "planning.no_circular_dependency" in check_ids
    assert "planning.critical_path_identified" in check_ids
    assert "planning.high_risk_tasks_have_review" in check_ids
    assert "planning.all_tasks_have_model_tier" in check_ids
    assert "planning.no_resource_conflict" in check_ids
    assert "planning.no_cross_plan_resource_conflict" in check_ids
    assert "planning.model_tier_assignments_have_evidence" in check_ids
    assert "planning.no_unresolved_red_line_uncertainty" in check_ids
    # T-LRF-12: migration_old_path_removal is in the freeze blocking_checks (passed here — the
    # minimal plan has no migration concept edges so no migration-class tasks to enforce against).
    assert "planning.migration_old_path_removal" in check_ids
    mig = next(r for r in results if r.check_id == "planning.migration_old_path_removal")
    assert mig.status == "passed", "no migration-class tasks → migration check passes"
    # M-2.1 §4.1 caller-2: cia_dependency_scan is record-only (always passed even when the plan
    # is otherwise broken — it is 发现并记录, not a blocker).
    assert "planning.cia_dependency_scan" in check_ids
    scan = next(r for r in results if r.check_id == "planning.cia_dependency_scan")
    assert scan.status == "passed", "cia_dependency_scan is record-only, never blocks"
    # T-RMD-FIX2 (修2): new_capability_livefire_machine_check is in the freeze blocking_checks
    # (passed here — the minimal plan's t1 has no capability-path write_set / new-event-type
    # done_criterion, so the classifier flags no new-capability task to enforce against).
    assert "planning.new_capability_livefire_machine_check" in check_ids
    nc = next(r for r in results if r.check_id == "planning.new_capability_livefire_machine_check")
    assert nc.status == "passed", "no new-capability tasks → live-fire check passes"


# ─── T-L1-30: coverage_matrix (real coverage gate, was count-only fake-done) ─────


def test_coverage_resolver_via_consensus_brief_id(tmp_path: Path) -> None:
    """Resolver matches the InterviewBriefPublished by brief_id (the value consensus stores)."""
    events = _brief_chain_events("p1", "用户能创建 batch")
    gcc, how = resolve_plan_brief_completion_condition(events, "p1")
    assert gcc == "用户能创建 batch"
    assert "brief_ref=brief-abc" in how


def test_coverage_resolver_single_brief_fallback(tmp_path: Path) -> None:
    """No consensus link but exactly one active brief ⇒ fallback resolves it."""
    events = [
        {"event_id": "evt-b", "event_type": "InterviewBriefPublished",
         "payload": {"brief_id": "brief-x", "goal_completion_condition": "只此一条"}},
    ]
    gcc, how = resolve_plan_brief_completion_condition(events, "p1")
    assert gcc == "只此一条"
    assert "single-active-brief" in how


def test_coverage_resolver_via_frozen_concept_source_brief() -> None:
    """dogfood R07: 并发下 (12 并行线) 共识 freeze 抓错 brief_event_id (全局活跃 brief 非本 plan 的),
    但冻结概念的 source_brief_event_id 显式指向真 brief ⇒ 经概念权威反查纠回正确 brief。"""
    events = [
        {"event_id": "evt-mine", "event_type": "InterviewBriefPublished",
         "payload": {"brief_id": "brief-mine", "goal_completion_condition": "我的真完成条件"}},
        {"event_id": "evt-other", "event_type": "InterviewBriefPublished",
         "payload": {"brief_id": "brief-other", "goal_completion_condition": "别的 line 的完成条件"}},
        {"event_type": "ConceptCreated",
         "payload": {"after_state": {"concept_id": "c1@v1", "source_brief_event_id": "evt-mine"}}},
        {"event_type": "EngineeringConsensusFreezed",
         "payload": {"after_state": {"plan_id": "p1", "brief_event_id": "brief-other",
                                     "frozen_concept_ids": ["c1@v1"]}}},
    ]
    gcc, how = resolve_plan_brief_completion_condition(events, "p1")
    assert gcc == "我的真完成条件", how  # 概念反查纠正了 freeze 抓错的全局活跃 brief
    assert "frozen-concept source_brief" in how


def test_coverage_resolver_multi_source_concept_no_guess() -> None:
    """冻结概念指向多个 source_brief (来源混了) ⇒ 不猜, 回落 brief_ref/single-active。"""
    events = [
        {"event_id": "evt-a", "event_type": "InterviewBriefPublished",
         "payload": {"brief_id": "brief-a", "goal_completion_condition": "A"}},
        {"event_type": "ConceptCreated",
         "payload": {"after_state": {"concept_id": "c1@v1", "source_brief_event_id": "evt-a"}}},
        {"event_type": "ConceptCreated",
         "payload": {"after_state": {"concept_id": "c2@v1", "source_brief_event_id": "evt-z"}}},
        {"event_type": "EngineeringConsensusFreezed",
         "payload": {"after_state": {"plan_id": "p1", "brief_event_id": "brief-a",
                                     "frozen_concept_ids": ["c1@v1", "c2@v1"]}}},
    ]
    gcc, how = resolve_plan_brief_completion_condition(events, "p1")
    assert gcc == "A"  # 多源不猜 → 回落到 brief_ref=brief-a
    assert "frozen-concept" not in how


def test_coverage_pass_with_grounded_package(tmp_path: Path) -> None:
    """A plan whose task package quote-grounds in the brief observable passes + emits a matrix entry."""
    gcc = "条件; evidence: report"
    events = [
        *_brief_chain_events("p1", gcc),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event("t1", quoted="条件"),
    ]
    towow = _write_events(tmp_path, events)
    passed, matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is True, evidence
    assert len(matrix) == 1
    assert matrix[0].observable == gcc
    assert matrix[0].covered_by_tasks == ["t1"]


def test_coverage_fail_brief_unresolvable(tmp_path: Path) -> None:
    """No brief ⇒ no independent observable source ⇒ fail-closed (not a silent pass)."""
    events = [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event("t1", quoted="anything"),
    ]
    towow = _write_events(tmp_path, events)
    passed, _matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is False
    assert "cannot verify coverage" in evidence


def test_coverage_fail_count_only_no_package_content(tmp_path: Path) -> None:
    """A package WITHOUT package_content (the old count-only shape) is rejected (fake-done killed)."""
    events = [
        *_brief_chain_events("p1", "条件"),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event("t1", quoted="条件", with_content=False),
    ]
    towow = _write_events(tmp_path, events)
    passed, _matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is False
    assert "no package_content" in evidence


def test_coverage_fail_ungrounded_quote_is_not_circular(tmp_path: Path) -> None:
    """quoted_claim that is NOT a substring of the brief observable does not count as coverage.

    This is the non-circular teeth: a task cannot fabricate coverage by self-declaring an
    evidence_ref whose quoted_claim isn't actually drawn from the brief.
    """
    events = [
        *_brief_chain_events("p1", "用户能创建 batch"),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event("t1", quoted="我瞎编的覆盖声明"),
    ]
    towow = _write_events(tmp_path, events)
    passed, matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is False
    assert "not covered by any quote-grounded" in evidence
    assert matrix[0].covered_by_tasks == []


# ─── consensus-derived sub-batch: ground in a frozen invariant, not the brief observable ──────


def _pkg_event_invariant(task_id: str, *, quoted: str) -> dict:
    """A TaskPackagePublished whose done_criterion grounds in a frozen invariant (concept_definition
    source_type), not the brief — the shape a consensus-derived sub-batch task uses."""
    return {
        "event_type": "TaskPackagePublished",
        "payload": {
            "after_state": {
                "task_id": task_id,
                "package_hash": "sha256:x",
                "is_self_contained": True,
                "package_content": {
                    "task_type": "test",
                    "description": "d",
                    "done_criteria": [
                        {
                            "criterion_id": f"{task_id}-dc",
                            "description": "x",
                            "verification_type": "test_passes",
                            "evidence_ref": {
                                "source_type": "concept_definition",
                                "source_id": "inv-eventlog-cross-process-read-consistency",
                                "quoted_claim": quoted,
                            },
                        },
                    ],
                    "read_set": [{"entity_type": "c", "entity_id": "a", "derived_from": "cr"}],
                    "write_set": [{"entity_type": "f", "entity_id": "b", "derived_from": "dc"}],
                    "model_tier": "opus",
                    "model_tier_rationale": "r",
                    "review_required": True,
                },
            },
        },
    }


def _consensus_freeze_with_invariant(plan_id: str, *, invariant_desc: str, gcc: str | None) -> list[dict]:
    """A plan frozen by consensus carrying an invariant — optionally linked to a brief whose
    goal_completion_condition does NOT mention this concern (the derived-sub-batch topology)."""
    events: list[dict] = [
        {
            "event_type": "EngineeringConsensusFreezed",
            "payload": {
                "after_state": {
                    "plan_id": plan_id,
                    "brief_event_id": "brief-abc",
                    "invariants": [
                        {
                            "invariant_id": "inv-eventlog-cross-process-read-consistency",
                            "description": invariant_desc,
                            "mechanically_verifiable": True,
                        },
                    ],
                },
            },
        },
    ]
    if gcc is not None:
        events.append(
            {
                "event_id": "evt-brief-1",
                "event_type": "InterviewBriefPublished",
                "payload": {"brief_id": "brief-abc", "goal_completion_condition": gcc},
            },
        )
    return events


def test_resolve_plan_frozen_invariant_observables() -> None:
    """The frozen invariant descriptions for a plan surface as observables (the second source)."""
    events = _consensus_freeze_with_invariant(
        "p1", invariant_desc="EventLog 读 API 必须反映所有进程已 committed 的 append", gcc=None,
    )
    obs = _resolve_plan_frozen_invariant_observables(events, "p1")
    assert obs == ["EventLog 读 API 必须反映所有进程已 committed 的 append"]
    # different plan_id ⇒ no leak
    assert _resolve_plan_frozen_invariant_observables(events, "p2") == []


def test_coverage_pass_via_frozen_invariant_when_brief_unrelated(tmp_path: Path) -> None:
    """Consensus-derived sub-batch: the brief observable is real but unrelated; the task grounds in
    the plan's frozen invariant (source_type=concept_definition) ⇒ coverage passes. This is the
    plan-l0-read-consistency-consensus topology (derived from brief-06fd0fa4)."""
    inv = "EventLog 读 API 必须反映读取时刻所有进程已 committed 的 append; invalidate-on-write 维持新鲜度"
    events = [
        *_consensus_freeze_with_invariant("p1", invariant_desc=inv, gcc="死循环终结：6 个 trigger 全部到达终态"),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event_invariant("t1", quoted="invalidate-on-write 维持新鲜度"),
    ]
    towow = _write_events(tmp_path, events)
    passed, matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is True, evidence
    assert matrix[0].covered_by_tasks == ["t1"]


def test_coverage_invariant_path_is_non_circular(tmp_path: Path) -> None:
    """The invariant path has the same non-circular teeth as the brief path: a quoted_claim that is
    NOT a substring of any frozen invariant does not count as coverage."""
    events = [
        *_consensus_freeze_with_invariant("p1", invariant_desc="真实不变量文本", gcc=None),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event_invariant("t1", quoted="我瞎编的覆盖声明"),
    ]
    towow = _write_events(tmp_path, events)
    passed, _matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is False
    assert "not covered by any quote-grounded" in evidence


def test_coverage_pass_invariant_only_no_brief(tmp_path: Path) -> None:
    """A plan with no resolvable brief but a frozen invariant is NOT fail-closed — the invariant is a
    valid independent observable on its own."""
    events = [
        *_consensus_freeze_with_invariant("p1", invariant_desc="不变量唯一可观察来源", gcc=None),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        _pkg_event_invariant("t1", quoted="唯一可观察来源"),
    ]
    towow = _write_events(tmp_path, events)
    passed, _matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is True, evidence


def test_coverage_fail_unpackaged_task(tmp_path: Path) -> None:
    """Every plan task must have a published package — an unpackaged task fails coverage."""
    events = [
        *_brief_chain_events("p1", "条件"),
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t1", "plan_id": "p1"}}},
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"task_id": "t2", "plan_id": "p1"}}},
        _pkg_event("t1", quoted="条件"),  # t2 has no package
    ]
    towow = _write_events(tmp_path, events)
    passed, _matrix, evidence = build_coverage_matrix(towow, "p1")
    assert passed is False
    assert "without TaskPackagePublished" in evidence


def _ev_line(etype, after):
    import json as _json
    return _json.dumps({"event_type": etype, "payload": {"after_state": after}})


def test_coverage_progressive_pending_chain_passes(tmp_path: Path) -> None:
    """§11.2 渐进发布感知(会话锁施工 dogfood 实撞): 串行链只有 frontier 任务有包,
    下游缺包但有未完成 hard 前置 → progressive-pending, coverage 不因此拒。"""
    from towow.schemas.payloads.plan_freezed import build_coverage_matrix

    towow = tmp_path / ".towow"
    towow.mkdir()
    gcc = "完成时 X 测试全绿"
    pkg = {
        "task_id": "T-1",
        "package_content": {"done_criteria": [{
            "criterion_id": "d1", "description": "x", "verification_type": "test_passes",
            "evidence_ref": {"source_type": "brief.completion_condition",
                             "source_id": "evt-b", "quoted_claim": "X 测试全绿"},
        }]},
    }
    lines = [
        _ev_line("InterviewBriefPublished", {"brief_id": "b-1", "goal_completion_condition": gcc}),
        _ev_line("EngineeringConsensusFreezed", {"plan_id": "plan-pp", "source_brief_event_id": "evt-b"}),
        _ev_line("TaskNodeCreated", {"task_id": "T-1", "plan_id": "plan-pp"}),
        _ev_line("TaskNodeCreated", {"task_id": "T-2", "plan_id": "plan-pp"}),
        _ev_line("TaskDependencyEdgeAdded", {"source_task_id": "T-1", "target_task_id": "T-2", "strength": "hard"}),
        _ev_line("TaskPackagePublished", pkg),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _passed, _m, evidence = build_coverage_matrix(towow, "plan-pp")
    # T-2 缺包但其 hard 前置 T-1 未完成 → progressive-pending → 不因缺包拒
    assert "without TaskPackagePublished" not in evidence


def test_coverage_frontier_unpackaged_still_fails(tmp_path: Path) -> None:
    """对偶向: frontier 任务(无未完成前置)缺包 → 仍拒(门不放水)。"""
    from towow.schemas.payloads.plan_freezed import build_coverage_matrix

    towow = tmp_path / ".towow"
    towow.mkdir()
    lines = [
        _ev_line("TaskNodeCreated", {"task_id": "T-1", "plan_id": "plan-pp2"}),
        _ev_line("TaskNodeCreated", {"task_id": "T-2", "plan_id": "plan-pp2"}),
        # 无依赖边: 两个都是 frontier; 只给 T-1 打包
        _ev_line("TaskPackagePublished", {"task_id": "T-1", "package_content": {"done_criteria": [{
            "criterion_id": "d1", "description": "x", "verification_type": "test_passes",
            "evidence_ref": {"source_type": "brief.completion_condition",
                             "source_id": "evt-b", "quoted_claim": "q"}}]}}),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    passed, _m, evidence = build_coverage_matrix(towow, "plan-pp2")
    assert not passed
    assert "ready-frontier" in evidence
    assert "T-2" in evidence


# ════════════════════════════════════════════════════════════════════════════════
#  M-2.1 §4.1 caller-2 — planning.cia_dependency_scan (plan freeze 调 CIA forward_slice)
# ════════════════════════════════════════════════════════════════════════════════


def _rec(event_type: EventType, after: dict, seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{_uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{_uuid.uuid4().hex}",
        local_intent_id=f"li-{_uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": after},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _cedge_rec(eid: str, src: str, tgt: str, etype: str, seq: int) -> EventRecord:
    return _rec(
        EventType.CONCEPT_EDGE_ADDED,
        {"edge_id": eid, "source_concept_id": src, "target_concept_id": tgt,
         "edge_type": etype, "direction": "unidirectional"},
        seq,
    )


def _task_node(task_id: str, *, plan_id: str, read_set=None, write_set=None, concept_refs=None) -> dict:
    after: dict = {"task_id": task_id, "plan_id": plan_id}
    if read_set is not None:
        after["read_set"] = read_set
    if write_set is not None:
        after["write_set"] = write_set
    if concept_refs is not None:
        after["concept_refs"] = concept_refs
    return after


def test_cia_dependency_scan_direction_empirically_verified(tmp_path: Path) -> None:
    """§49 铁律 — 不信 docstring 标签, 用真数据 probe forward_slice 方向。

    真 impact_graph: 原始概念边 `B --dependency--> X` (source_concept_id=B, target_concept_id=X)
    = "B 依赖 X" (M-0.2 §3.2 depends_on 原始约定 source=依赖方=下游)。impact_graph 归一化后翻成
    X→B; 故 forward_slice(X) 必返回 B (依赖 X 的下游) —— 这正是 caller-2 要的"传递性下游"。
    本测试直接断言 forward_slice 行为, 锁死方向不靠推理。
    """
    from towow.l1.cia_query import CIAQueryService

    store = ProjectionStore(tmp_path / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-X@v1", "name": "X"}, 1))
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-B@v1", "name": "B"}, 2))
    # raw edge: B --dependency--> X  (B depends on X)
    store._apply(_cedge_rec("e-b-x", "concept-B@v1", "concept-X@v1", "dependency", 3))

    fs = CIAQueryService(store).forward_slice("concept-X@v1")
    downstream = {r.entity.entity_id: r for r in fs.results}
    assert "concept-B@v1" in downstream, (
        f"forward_slice(X) MUST return the downstream dependent B; got {list(downstream)}"
    )
    assert downstream["concept-B@v1"].depth == 1
    assert downstream["concept-B@v1"].path == ["e-b-x"]


def test_cia_dependency_scan_function_empty_when_no_concept_edges(tmp_path: Path) -> None:
    """cia_dependency_scan (the lower-level fn) over plan tasks whose concepts have no impact_graph
    edges → transitive_edges=[] but watermark present (= the scan ran, reproducibly).
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-iso@v1", "name": "iso"}, 1))
    plan_tasks = [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": _task_node(
            "task-iso", plan_id="p",
            write_set=[{"entity_type": "concept", "entity_id": "concept-iso@v1"}],
        )}},
    ]
    ev = cia_dependency_scan(plan_tasks, store, events=plan_tasks, plan_id="p")
    assert ev["transitive_edges"] == []
    assert isinstance(ev["projection_watermark"], int)
    assert ev["scanned_tasks"] == [{"task_id": "task-iso", "concepts": ["concept-iso@v1"]}]


def test_cia_dependency_scan_maps_downstream_concept_back_to_writing_task(tmp_path: Path) -> None:
    """(a) 真有边: task-up 触及 concept-X (concept_ref); task-down WRITE 一个依赖 X 的下游概念
    concept-B (write_set entity_type=concept)。scan 必须把传递性下游 concept-B 映射回 task-down,
    记一条 (task-up → task-down, via concept-X, downstream concept-B, depth1) edge + watermark。
    """
    plan_id = "plan-c2"
    # plan tasks (raw events.log) — task-up touches concept-X, task-down writes concept-B.
    towow = tmp_path / ".towow"
    towow.mkdir()
    lines = [
        _ev_line("TaskNodeCreated", _task_node(
            "task-up", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-X@v1", "at_reference": "@concept:concept-X",
                           "locking_policy": "pin_to_snapshot"}],
            read_set=[{"entity_type": "concept", "entity_id": "concept-X@v1"}],
            write_set=[{"entity_type": "task", "entity_id": "task-up"}],
        )),
        _ev_line("TaskNodeCreated", _task_node(
            "task-down", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-B@v1", "at_reference": "@concept:concept-B",
                           "locking_policy": "pin_to_snapshot"}],
            read_set=[{"entity_type": "concept", "entity_id": "concept-B@v1"}],
            write_set=[{"entity_type": "concept", "entity_id": "concept-B@v1"}],
        )),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # real impact_graph: concept-B depends_on concept-X (raw source=B=下游).
    store = ProjectionStore(towow / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-X@v1", "name": "X"}, 1))
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-B@v1", "name": "B"}, 2))
    store._apply(_cedge_rec("e-b-x", "concept-B@v1", "concept-X@v1", "dependency", 3))

    passed, evidence_json = check_cia_dependency_scan(towow, plan_id, store=store)
    assert passed is True  # record-only
    ev = json.loads(evidence_json)
    assert isinstance(ev["projection_watermark"], int)
    edges = ev["transitive_edges"]
    assert len(edges) == 1, edges
    e = edges[0]
    assert e["upstream_task"] == "task-up"
    assert e["downstream_task"] == "task-down"
    assert e["via_concept"] == "concept-X@v1"
    assert e["downstream_concept"] == "concept-B@v1"
    assert e["depth"] == 1
    assert e["concept_path"] == ["e-b-x"]
    # scanned_tasks records each task's touched concepts (audit trail).
    scanned = {s["task_id"]: s["concepts"] for s in ev["scanned_tasks"]}
    assert "concept-X@v1" in scanned["task-up"]
    assert "concept-B@v1" in scanned["task-down"]


def test_cia_dependency_scan_surfaces_read_only_downstream_task(tmp_path: Path) -> None:
    """§4.3 "下游 task"=涉及(touch)下游概念的 task —— 一个**只 READ** 下游概念 concept-B
    (无 write_set concept 项、无 concept_ref) 的 task 仍是合法下游 (它消费的概念被上游传递性影响),
    必须浮出。锁死 _concept_to_touching_tasks 纳入 read_set —— 独立审查 caller-2 抓的 minor:
    旧版倒排只 write/define → 纯读消费方下游被漏 (省略)。旧版此测试必红, 新版过。
    """
    plan_id = "plan-c2-readonly"
    towow = tmp_path / ".towow"
    towow.mkdir()
    lines = [
        _ev_line("TaskNodeCreated", _task_node(
            "task-up", plan_id=plan_id,
            read_set=[{"entity_type": "concept", "entity_id": "concept-X@v1"}],
            write_set=[{"entity_type": "task", "entity_id": "task-up"}],
        )),
        # task-down ONLY READS concept-B — no concept_ref, write_set is its own task node only.
        _ev_line("TaskNodeCreated", _task_node(
            "task-down", plan_id=plan_id,
            read_set=[{"entity_type": "concept", "entity_id": "concept-B@v1"}],
            write_set=[{"entity_type": "task", "entity_id": "task-down"}],
        )),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = ProjectionStore(towow / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-X@v1", "name": "X"}, 1))
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-B@v1", "name": "B"}, 2))
    store._apply(_cedge_rec("e-b-x", "concept-B@v1", "concept-X@v1", "dependency", 3))

    passed, evidence_json = check_cia_dependency_scan(towow, plan_id, store=store)
    assert passed is True  # record-only
    ev = json.loads(evidence_json)
    edges = ev["transitive_edges"]
    assert len(edges) == 1, f"read-only 下游 task 必须浮出 (touch=read 也算): {edges}"
    e = edges[0]
    assert e["upstream_task"] == "task-up"
    assert e["downstream_task"] == "task-down"  # 仅靠 read_set 映射出来的
    assert e["downstream_concept"] == "concept-B@v1"


def test_cia_dependency_scan_catches_what_resource_conflict_misses(tmp_path: Path) -> None:
    """scan 抓到 read/write 直接交集门(no_resource_conflict)漏的传递性影响 — 举真例。

    task-A reads concept-X; task-B writes concept-Y; X 与 Y 写集无交集 → no_resource_conflict 看不到
    任何关系。但 concept-Y depends_on concept-X (Y 是 X 的传递性下游), 所以改 X 会波及写 Y 的 task-B。
    cia_dependency_scan 沿 impact_graph 边走, 把这条传递性 (task-A → task-B) 摆出来 (record-only)。
    """
    from towow.schemas.payloads.plan_freezed import check_no_resource_conflict

    plan_id = "plan-c2-miss"
    towow = tmp_path / ".towow"
    towow.mkdir()
    lines = [
        _ev_line("TaskNodeCreated", _task_node(
            "task-A", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-X@v1", "at_reference": "@concept:concept-X",
                           "locking_policy": "pin_to_snapshot"}],
            read_set=[{"entity_type": "concept", "entity_id": "concept-X@v1"}],
            write_set=[{"entity_type": "task", "entity_id": "task-A"}],
        )),
        _ev_line("TaskWriteSetClaimed", {"task_id": "task-A", "write_set": [
            {"entity_type": "task", "entity_id": "task-A"}]}),
        _ev_line("TaskNodeCreated", _task_node(
            "task-B", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-Y@v1", "at_reference": "@concept:concept-Y",
                           "locking_policy": "pin_to_snapshot"}],
            read_set=[{"entity_type": "concept", "entity_id": "concept-Y@v1"}],
            write_set=[{"entity_type": "concept", "entity_id": "concept-Y@v1"}],
        )),
        _ev_line("TaskWriteSetClaimed", {"task_id": "task-B", "write_set": [
            {"entity_type": "concept", "entity_id": "concept-Y@v1"}]}),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # no_resource_conflict sees NO conflict — task-A writes only its own task node, task-B writes
    # concept-Y; the write_sets are disjoint.
    rc_ok, rc_ev = check_no_resource_conflict(towow, plan_id)
    assert rc_ok is True, rc_ev  # the direct-overlap gate is blind to the transitive link

    # but the transitive dependency IS there: concept-Y depends_on concept-X.
    store = ProjectionStore(towow / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-X@v1", "name": "X"}, 1))
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-Y@v1", "name": "Y"}, 2))
    store._apply(_cedge_rec("e-y-x", "concept-Y@v1", "concept-X@v1", "dependency", 3))

    _passed, evidence_json = check_cia_dependency_scan(towow, plan_id, store=store)
    ev = json.loads(evidence_json)
    edges = {(e["upstream_task"], e["downstream_task"]) for e in ev["transitive_edges"]}
    assert ("task-A", "task-B") in edges, (
        "cia_dependency_scan MUST surface the transitive link no_resource_conflict misses"
    )


def test_cia_dependency_scan_empty_graph_passes_with_watermark(tmp_path: Path) -> None:
    """(b) 空图(无概念边/无下游): scan 仍 passed — transitive_edges=[] + projection_watermark
    (证明扫了, 可复算)。空结果不是失败, 是"扫过、没传递性下游"。
    """
    plan_id = "plan-c2-empty"
    towow = tmp_path / ".towow"
    towow.mkdir()
    lines = [
        _ev_line("TaskNodeCreated", _task_node(
            "task-lone", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-iso@v1", "at_reference": "@concept:concept-iso",
                           "locking_policy": "pin_to_snapshot"}],
            read_set=[{"entity_type": "concept", "entity_id": "concept-iso@v1"}],
            write_set=[{"entity_type": "concept", "entity_id": "concept-iso@v1"}],
        )),
    ]
    (towow / "events.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = ProjectionStore(towow / "graph")
    store._apply(_rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-iso@v1", "name": "iso"}, 1))

    passed, evidence_json = check_cia_dependency_scan(towow, plan_id, store=store)
    assert passed is True
    ev = json.loads(evidence_json)
    assert ev["transitive_edges"] == []
    assert isinstance(ev["projection_watermark"], int)
    # the lone task's concept is still recorded as scanned (the scan really ran).
    assert any(s["task_id"] == "task-lone" for s in ev["scanned_tasks"])


def test_cia_dependency_scan_builds_store_from_disk_when_none_given(tmp_path: Path) -> None:
    """store=None fallback: check_cia_dependency_scan 从 towow_dir + events.log 自建 catchup'd store,
    CIA 仍读到真 impact_graph (非空跑)。用真 EventRecords 落盘 → catchup → 真 watermark。
    """
    plan_id = "plan-c2-fallback"
    towow = tmp_path / ".towow"
    (towow / "events").mkdir(parents=True, exist_ok=True)
    recs = [
        _rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-up@v1", "name": "up"}, 0),
        _rec(EventType.CONCEPT_CREATED, {"concept_id": "concept-dn@v1", "name": "dn"}, 1),
        # concept-dn depends_on concept-up (raw source=dn=下游) → forward_slice(up) reaches dn.
        _cedge_rec("e-dn-up", "concept-dn@v1", "concept-up@v1", "dependency", 2),
        _rec(EventType.TASK_NODE_CREATED, _task_node(
            "task-u", plan_id=plan_id,
            concept_refs=[{"concept_id": "concept-up@v1", "at_reference": "@concept:concept-up",
                           "locking_policy": "pin_to_snapshot"}],
            write_set=[{"entity_type": "task", "entity_id": "task-u"}],
        ), 3),
        _rec(EventType.TASK_NODE_CREATED, _task_node(
            "task-d", plan_id=plan_id,
            write_set=[{"entity_type": "concept", "entity_id": "concept-dn@v1"}],
        ), 4),
    ]
    # proper EventRecords on disk: readable by BOTH the raw-line readers (plan tasks) AND
    # ProjectionStore.catchup (impact_graph) — same disk-write pattern as the caller-1 fallback test.
    (towow / "events.log").write_text(
        "\n".join(json.dumps(r.model_dump(mode="json"), default=str) for r in recs) + "\n",
        encoding="utf-8",
    )

    # NO store passed — fallback must build + catchup a real store.
    passed, evidence_json = check_cia_dependency_scan(towow, plan_id)
    assert passed is True
    ev = json.loads(evidence_json)
    assert ev["projection_watermark"] >= 0
    edges = {(e["upstream_task"], e["downstream_task"]) for e in ev["transitive_edges"]}
    assert ("task-u", "task-d") in edges, (
        "fallback store must read the real impact_graph and surface task-u → task-d"
    )


# ─── T-LRF-12: migration-done-requires-old-path-removal@v1 (planning 侧门) ────────


def _concept_edge(
    src: str, tgt: str, *, edge_type: str = "reference", alias: bool = False,
    edge_id: str | None = None,
) -> dict[str, object]:
    """A ConceptEdgeAdded event src → tgt. alias=True uses the from_concept/to_concept field names
    (containment-style edges use those); default uses source_concept_id/target_concept_id (the
    migration reference edges use those) — both must be read (盘上两套并存). edge_id defaults to a
    deterministic src→tgt id: real ConceptEdgeAdded always carries a schema-required edge_id
    (ConceptEdgeAddedAfter.edge_id, min_length=1), and migration_concept_ids' net-edge-state replay
    keys removal on it — so the fixture must carry one too."""
    eid = edge_id or f"edge-{src}->{tgt}"
    after: dict[str, object]
    if alias:
        after = {"from_concept": src, "to_concept": tgt, "edge_type": edge_type, "edge_id": eid}
    else:
        after = {"source_concept_id": src, "target_concept_id": tgt, "edge_type": edge_type, "edge_id": eid}
    return {"event_type": "ConceptEdgeAdded", "payload": {"after_state": after}}


def _concept_edge_removed(edge_id: str) -> dict[str, object]:
    """A ConceptEdgeRemoved event withdrawing edge_id. The withdraw identity lives in before_state
    (ConceptEdgeRemovedBefore.edge_id) — mirrors the real payload the `consensus edge-remove` CLI emits."""
    return {
        "event_type": "ConceptEdgeRemoved",
        "payload": {"before_state": {"edge_id": edge_id}},
    }


def _mig_task(
    task_id: str, *, plan_id: str, concept_refs: list[str], read_concepts: list[str] | None = None,
) -> dict[str, object]:
    """TaskNodeCreated with concept_refs (delivery binding) + optional read_set concepts."""
    after: dict[str, object] = {
        "task_id": task_id,
        "plan_id": plan_id,
        "concept_refs": [{"concept_id": c} for c in concept_refs],
    }
    if read_concepts:
        after["read_set"] = [{"entity_type": "concept", "entity_id": c} for c in read_concepts]
    return {"event_type": "TaskNodeCreated", "payload": {"after_state": after}}


def _grep_removal_dc(criterion_id: str, *, pattern: str, expected: int = 0) -> dict[str, object]:
    """A DoneCriterion carrying a grep machine_check (old-path-removal form when expected==0)."""
    return {
        "criterion_id": criterion_id,
        "description": f"old path {pattern} removed",
        "verification_type": "manual_verify",
        "evidence_ref": {"source_type": "concept_definition", "source_id": "c", "quoted_claim": "q"},
        "machine_check": {
            "condition": f"{pattern} gone",
            "verification_method": "grep",
            "expected_result": "0 occurrences",
            "verification_pattern": pattern,
            "expected_occurrences": expected,
            "search_scope": "src",
        },
    }


def _plain_dc(criterion_id: str, *, vtype: str = "test_passes") -> dict[str, object]:
    """A DoneCriterion with no machine_check (not an old-path-removal criterion)."""
    return {
        "criterion_id": criterion_id,
        "description": "d",
        "verification_type": vtype,
        "evidence_ref": {"source_type": "concept_definition", "source_id": "c", "quoted_claim": "q"},
    }


def _mig_pkg(task_id: str, criteria: list[dict[str, object]]) -> dict[str, object]:
    """TaskPackagePublished carrying the given structured done_criteria."""
    return {
        "event_type": "TaskPackagePublished",
        "payload": {"after_state": {"task_id": task_id, "package_content": {"done_criteria": criteria}}},
    }


def test_migration_concept_ids_from_edges_both_aliases(tmp_path: Path) -> None:
    # instance → invariant edges flag the instance as a migration concept; both field-name aliases work.
    events = [
        _concept_edge("execution-lock-migration-closeout@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _concept_edge("fork-spawn@v1", MIGRATION_INVARIANT_CONCEPT_ID, alias=True),
        _concept_edge("unrelated@v1", "some-other-concept"),  # not pointing at the invariant
    ]
    mig = migration_concept_ids(events)
    assert mig == {"execution-lock-migration-closeout@v1", "fork-spawn@v1"}
    # the invariant itself is never a migration concept (no self-edge → not included)
    assert MIGRATION_INVARIANT_CONCEPT_ID not in mig


def test_migration_concept_ids_honors_removal(tmp_path: Path) -> None:
    # FND-1 core: a ConceptEdgeRemoved (by edge_id) withdraws a governance-implying edge — the concept
    # must STOP being classified migration, matching the canonical concept_graph projection (is_active=
    # false on removal). The prior added-only scan ignored removal and diverged from the projection.
    added_only = [_concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e1")]
    assert migration_concept_ids(added_only) == {"mig@v1"}, "added edge ⇒ migration concept"

    added_then_removed = [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e1"),
        _concept_edge_removed("e1"),
    ]
    assert migration_concept_ids(added_then_removed) == set(), "removed by edge_id ⇒ no longer migration"
    # re-replay idempotent: same events → same net state (no order/accumulation surprise)
    assert migration_concept_ids(added_then_removed) == migration_concept_ids(list(added_then_removed))


def test_migration_concept_ids_readd_revives(tmp_path: Path) -> None:
    # remove then re-add (real edge-add mints a fresh edge_id) revives the migration classification —
    # net-edge-state semantics, same as _active_dependency_edges for task edges.
    events = [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e1"),
        _concept_edge_removed("e1"),
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e2"),
    ]
    assert migration_concept_ids(events) == {"mig@v1"}


def test_migration_concept_ids_removal_independent_per_edge(tmp_path: Path) -> None:
    # Removing ONE concept's governance edge must not affect another concept's still-active edge.
    events = [
        _concept_edge("contrast@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e-contrast"),
        _concept_edge("genuine@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e-genuine"),
        _concept_edge_removed("e-contrast"),
    ]
    assert migration_concept_ids(events) == {"genuine@v1"}


def test_fnd1_contrast_edge_removed_task_no_longer_migration_class(tmp_path: Path) -> None:
    # End-to-end FND-1: a new-enforcement concept carried a contrast `reference` edge to the invariant
    # (mistaken for governance). A task DELIVERING it was wrongly migration-class. After the edge is
    # withdrawn, the task is no longer migration-class → the freeze gate passes WITHOUT any (fabricated)
    # old-path-removal criterion — the whole point: don't force a grep=0 criterion on a task with no
    # old production path to delete.
    towow = _write_events(tmp_path, [
        _concept_edge("new-enforcement@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e-contrast"),
        _mig_task("t-new", plan_id="p1", concept_refs=["new-enforcement@v1"]),
        _mig_pkg("t-new", [_plain_dc("dc1")]),  # NO old-path-removal criterion
        _concept_edge_removed("e-contrast"),
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is True, f"contrast edge removed ⇒ task not migration-class ⇒ gate passes; got: {evidence}"
    assert "no migration-class tasks" in evidence


def test_redteam_genuine_migration_edge_not_removed_still_fails(tmp_path: Path) -> None:
    # TEETH INTACT (red team): a GENUINE migration concept whose governance edge is NOT removed, with a
    # task delivering it and NO old-path-removal criterion, must STILL fail the freeze gate. The honor-
    # removal fix must not weaken the gate for real migrations — only stop false-positiving contrast refs.
    towow = _write_events(tmp_path, [
        _concept_edge("execution-lock-migration-closeout@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e-real"),
        _mig_task("t-real", plan_id="p1", concept_refs=["execution-lock-migration-closeout@v1"]),
        _mig_pkg("t-real", [_plain_dc("dc1")]),  # NO old-path-removal criterion
        # a DIFFERENT, unrelated edge gets removed — must not rescue t-real
        _concept_edge("other@v1", MIGRATION_INVARIANT_CONCEPT_ID, edge_id="e-other"),
        _concept_edge_removed("e-other"),
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is False, "genuine migration (edge live) lacking old-path-removal must still be blocked"
    assert "t-real" in evidence
    assert "old-path-removal" in evidence


def test_migration_class_uses_concept_refs_not_read_set(tmp_path: Path) -> None:
    # A task that only READS a migration concept (concept_refs = the invariant) is NOT migration-class
    # — this is the meta-mechanism task's own shape (T-LRF-12). A task DELIVERING it (concept_ref) is.
    events = [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        # meta task: concept_refs = invariant, reads the migration instance — must NOT be migration-class
        _mig_task("t-meta", plan_id="p1", concept_refs=[MIGRATION_INVARIANT_CONCEPT_ID], read_concepts=["mig@v1"]),
        # real migration task: delivers mig@v1 via concept_refs
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
    ]
    mc = migration_class_task_ids(events, "p1")
    assert "t-real" in mc
    assert mc["t-real"] == ["mig@v1"]
    assert "t-meta" not in mc, "reading a migration concept ≠ delivering a migration"


def test_migration_task_missing_old_path_removal_criterion_fails(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        _mig_pkg("t-real", [_plain_dc("dc1"), _plain_dc("dc2", vtype="concept_state_reached")]),
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is False
    assert "t-real" in evidence
    assert "old-path-removal" in evidence


def test_migration_task_with_grep_zero_criterion_passes(tmp_path: Path) -> None:
    towow = _write_events(tmp_path, [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        _mig_pkg("t-real", [_plain_dc("dc1"), _grep_removal_dc("dc2", pattern="_write_active_execution_session")]),
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is True
    assert "1 迁移类 task" in evidence


def test_non_migration_plan_passes(tmp_path: Path) -> None:
    # No concept edges → no migration concepts → no migration-class tasks → pass (don't break normal plans).
    towow = _write_events(tmp_path, [
        _mig_task("t1", plan_id="p1", concept_refs=["plain@v1"]),
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is True
    assert "no migration-class tasks" in evidence


def test_grep_expected_nonzero_is_not_old_path_removal(tmp_path: Path) -> None:
    # A grep machine_check expecting >0 asserts the NEW symbol's presence, not the OLD symbol's
    # absence — it must NOT count as an old-path-removal criterion, so the task fails the gate.
    towow = _write_events(tmp_path, [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        _mig_pkg("t-real", [_grep_removal_dc("dc1", pattern="new_symbol", expected=5)]),
    ])
    passed, _evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is False


def test_unpackaged_migration_task_skipped_progressive_pending(tmp_path: Path) -> None:
    # A migration-class task without a published package can't be structurally checked (machine_check
    # lives only on the package) → skipped as progressive-pending, not failed (mirrors coverage_complete).
    towow = _write_events(tmp_path, [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        # no TaskPackagePublished for t-real
    ])
    passed, evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is True
    assert "progressive-pending" in evidence


def test_read_side_defensive_compat_not_falsely_required(tmp_path: Path) -> None:
    # 读侧防御性兼容段豁免: a migration task whose package has ONE grep=0 criterion (the write symbol)
    # passes — the gate only requires the PRESENCE of a removal criterion, it does NOT scan code nor
    # demand a removal criterion for the deliberately-kept read-side union symbol. So keeping a
    # read-side defensive compat segment never causes a failure.
    towow = _write_events(tmp_path, [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        _mig_pkg("t-real", [
            _grep_removal_dc("dc-write", pattern="_write_active_execution_session"),  # write-path removal
            _plain_dc("dc-read-compat"),  # read-side defensive compat kept — not a removal criterion, fine
        ]),
    ])
    passed, _evidence = check_migration_old_path_removal(towow, "p1")
    assert passed is True


def test_is_old_path_removal_criterion_predicate() -> None:
    assert is_old_path_removal_criterion(_grep_removal_dc("dc", pattern="x", expected=0)) is True
    assert is_old_path_removal_criterion(_grep_removal_dc("dc", pattern="x", expected=3)) is False
    assert is_old_path_removal_criterion(_plain_dc("dc")) is False
    # non-grep machine_check
    non_grep: dict[str, object] = {"machine_check": {"verification_method": "test", "test_selector": "x"}}
    assert is_old_path_removal_criterion(non_grep) is False
    # empty pattern
    empty_pattern: dict[str, object] = {
        "machine_check": {"verification_method": "grep", "verification_pattern": "", "expected_occurrences": 0},
    }
    assert is_old_path_removal_criterion(empty_pattern) is False


def test_task_migration_old_path_criteria_bridge(tmp_path: Path) -> None:
    # The public bridge the review side uses: returns the grep removal criteria for a migration task,
    # [] for a non-migration task or one with no removal criteria.
    events = [
        _concept_edge("mig@v1", MIGRATION_INVARIANT_CONCEPT_ID),
        _mig_task("t-real", plan_id="p1", concept_refs=["mig@v1"]),
        _mig_pkg("t-real", [_plain_dc("dc1"), _grep_removal_dc("dc2", pattern="old_sym")]),
        _mig_task("t-plain", plan_id="p1", concept_refs=["plain@v1"]),
        _mig_pkg("t-plain", [_grep_removal_dc("dcx", pattern="whatever")]),
    ]
    crit = task_migration_old_path_criteria(events, "t-real")
    assert len(crit) == 1
    machine_check = crit[0]["machine_check"]
    assert isinstance(machine_check, dict)
    assert machine_check["verification_pattern"] == "old_sym"
    # non-migration task → [] even though it has a grep=0 criterion (it's just not migration-class)
    assert task_migration_old_path_criteria(events, "t-plain") == []
