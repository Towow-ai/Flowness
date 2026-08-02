"""RUN-038 波3 — RePlanTriggered schema 注册 (T-L1-42 M-1.4 产生方 / T-L1-71 M-1.6 fix 产生方).

# spec source:
#   04-l1-intelligence/M-1.3-planner-skill-detailed-design.md §14.2  (schema 定稿 owner)
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §3.8  (M-1.4 产生方场景)
#   04-l1-intelligence/M-1.6-fix-skill-detailed-design.md       §3.4  (fix_scope_violation 触发源)
#   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Conflict 12         (payload-shape 统一裁定)
#
# 病 (RUN-038 波3 前): RePlanTriggered EventType 在 enum / PAYLOAD_REGISTRY 全无 (grep 全 src
#   零 RePlan), 多份 spec (M-1.4 §9 L754 / M-1.6 §3.4 注 "M-0.1 已注册") 谎称已加 → 实际无任何
#   东西能 emit 真 canonical RePlanTriggered。F-14 re-plan 衔接没有真事件源。
#
# 修 (RUN-038 波3): 真注册 EventType + payload + 两个 trigger 枚举, 照 T-L1-37 PatchProposed /
#   T-L1-38 MismatchDetected 范式 — payload conforming, 留在 PAYLOAD_VALIDATION_ENFORCED。
#   M-1.3 §14.2 是 schema 定稿 owner; trigger_source 枚举并入 M-1.4 §3.8 + M-1.6 §3.4 场景。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    EventType,
    RePlanSuggestedAction,
    RePlanTriggerSource,
    TargetEntityType,
)
from towow.schemas.payloads.registry import (
    PAYLOAD_REGISTRY,
    PAYLOAD_VALIDATION_ENFORCED,
    conforms,
)
from towow.schemas.payloads.state_transition import RePlanTriggeredPayload


def _good_replan() -> dict[str, object]:
    return {
        "target_entity_type": "concept",
        "transition_type": "modified",
        "after_state": {
            "plan_id": "plan-1",
            "trigger_source": "done_criteria_conflict",
            "trigger_event_id": "evt-mismatch-1",
            "affected_tasks": ["t-1", "t-2"],
            "suggested_action": "partial_replan",
            "evidence_refs": [
                {"source_type": "mismatch", "source_id": "mis-1", "finding": "done_criteria 跟 brief 矛盾"},
            ],
        },
    }


def _good_fix_scope() -> dict[str, object]:
    return {
        "target_entity_type": "concept",
        "transition_type": "modified",
        "after_state": {
            "plan_id": "plan-1",
            "task_id": "t-9",
            "trigger_source": "fix_scope_violation",
            "trigger_event_id": "evt-fix-7",
            "affected_tasks": ["t-9"],
            "suggested_action": "partial_replan",
            "trigger_evidence": {
                "fix_id": "fix-7",
                "finding_id": "find-3",
                "scope_violation_detail": "修复需触碰 t-9 declared_scope 外的 concept-z",
            },
        },
    }


# ─── EventType + entity type 注册 ────────────────────────────────────────────────


def test_event_type_registered() -> None:
    assert EventType.RE_PLAN_TRIGGERED.value == "RePlanTriggered"


def test_target_entity_type_is_concept() -> None:
    # M-1.3 §14.2 文本写 'plan' 但 TargetEntityType 无 plan 成员; 既有 M-1.3 plan-event
    # (PlanningUncertainty / PlanFreezed) 统一 plan-as-concept → RePlanTriggered 同约定。LEDGER Conflict 12。
    assert TargetEntityType.CONCEPT.value == "concept"


def test_registry_maps_event_type() -> None:
    assert PAYLOAD_REGISTRY[EventType.RE_PLAN_TRIGGERED] is RePlanTriggeredPayload


def test_replan_is_fail_closed_enforced() -> None:
    """照 T-L1-37 范式: M-1.4 / M-1.6 emit conforming → 留在 ENFORCED (非 exempt 空转)."""
    assert EventType.RE_PLAN_TRIGGERED in PAYLOAD_VALIDATION_ENFORCED


# ─── trigger_source 枚举并入 M-1.3 §14.2 + M-1.4 §3.8 + M-1.6 §3.4 ──────────────


def test_trigger_source_includes_m13_six() -> None:
    """M-1.3 §14.2 原 6 个 trigger_source 全在."""
    vals = {s.value for s in RePlanTriggerSource}
    for v in (
        "investigation_completed",
        "nature_judgment_changed",
        "concept_superseded",
        "cross_plan_conflict_emerged",
        "execution_failure_pattern",
        "resource_constraint_changed",
    ):
        assert v in vals, v


def test_trigger_source_includes_m14_hardcoded_critical() -> None:
    """M-1.4 §3.8 hardcoded critical: pin stale / done_criteria 矛盾 / advisor replan / task 边界错."""
    vals = {s.value for s in RePlanTriggerSource}
    for v in ("pin_stale", "done_criteria_conflict", "advisor_verdict_replan", "task_boundary_error"):
        assert v in vals, v


def test_trigger_source_includes_m16_fix_scope_violation() -> None:
    """M-1.6 §3.4: fix 越 scope 触发源 (T-L1-71)."""
    assert "fix_scope_violation" in {s.value for s in RePlanTriggerSource}


def test_suggested_action_three() -> None:
    assert {s.value for s in RePlanSuggestedAction} == {
        "partial_replan",
        "full_replan",
        "abort_plan",
    }


# ─── payload validate (§14.2 字段) ──────────────────────────────────────────────


def test_replan_payload_conforms() -> None:
    assert conforms(EventType.RE_PLAN_TRIGGERED, _good_replan()) is None
    p = RePlanTriggeredPayload.model_validate(_good_replan(), strict=False)
    assert p.after_state.trigger_source is RePlanTriggerSource.DONE_CRITERIA_CONFLICT
    assert p.after_state.suggested_action is RePlanSuggestedAction.PARTIAL_REPLAN
    assert p.after_state.affected_tasks == ["t-1", "t-2"]


def test_fix_scope_payload_conforms() -> None:
    """M-1.6 §3.4 fix-side shape: task_id + trigger_evidence + fix_scope_violation."""
    assert conforms(EventType.RE_PLAN_TRIGGERED, _good_fix_scope()) is None
    p = RePlanTriggeredPayload.model_validate(_good_fix_scope(), strict=False)
    assert p.after_state.trigger_source is RePlanTriggerSource.FIX_SCOPE_VIOLATION
    assert p.after_state.task_id == "t-9"
    assert p.after_state.trigger_evidence is not None
    assert p.after_state.trigger_evidence.fix_id == "fix-7"


def test_replan_extra_field_rejected() -> None:
    """extra=forbid: 混入异类字段被写边界拒 (event_type↔payload mismatch)."""
    bad = _good_replan()
    bad["bogus_top_field"] = "x"
    assert conforms(EventType.RE_PLAN_TRIGGERED, bad) is not None


def test_replan_missing_plan_id_rejected() -> None:
    bad = _good_replan()
    del bad["after_state"]["plan_id"]  # type: ignore[union-attr]
    assert conforms(EventType.RE_PLAN_TRIGGERED, bad) is not None


def test_replan_empty_trigger_event_id_rejected() -> None:
    """trigger_event_id 是投资证据 (M-1.3 §14.2 注 '触发 event 的 id'), 不能空."""
    bad = _good_replan()
    bad["after_state"]["trigger_event_id"] = ""  # type: ignore[index]
    with pytest.raises(ValidationError):
        RePlanTriggeredPayload.model_validate(bad, strict=False)


def test_replan_unknown_trigger_source_rejected() -> None:
    bad = _good_replan()
    bad["after_state"]["trigger_source"] = "made_up_source"  # type: ignore[index]
    assert conforms(EventType.RE_PLAN_TRIGGERED, bad) is not None
