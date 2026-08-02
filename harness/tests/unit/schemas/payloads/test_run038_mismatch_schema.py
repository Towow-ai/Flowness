"""RUN-038 加固(2) — MismatchDetected / MismatchResolutionDecided schema 注册 (T-L1-38/39).

# spec source:
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §3.2 / §3.3
#   01-reconciliation/PHASE-2-PLAN.md §2 加固(2) (debt-0337d1ae14f7)
#
# 病 (RUN-038 前): no_unhandled_mismatch 门逻辑真 fail-closed, 但 MismatchDetected /
#   MismatchResolutionDecided 未注册 (不在 EventType / PAYLOAD_REGISTRY, 缺 payload,
#   TargetEntityType/SubjectEntityType 无 mismatch) → 生产无任何东西能 emit 真 canonical
#   MismatchDetected → 门永远扫空集 return True (vacuous-pass)。
#
# 修 (RUN-038): 真注册 EventType + payload + entity type, 照 T-L1-37 PatchProposed 范式 —
#   payload conforming, 留在 PAYLOAD_VALIDATION_ENFORCED (fail-closed)。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    EventType,
    MismatchAffectedAspect,
    MismatchDetectionTime,
    MismatchResolutionAction,
    MismatchResolutionOutcome,
    MismatchSeverity,
    SubjectEntityType,
    TargetEntityType,
)
from towow.schemas.payloads.registry import (
    PAYLOAD_REGISTRY,
    PAYLOAD_VALIDATION_ENFORCED,
    conforms,
)
from towow.schemas.payloads.state_transition import (
    MismatchDetectedPayload,
    MismatchResolutionDecidedPayload,
)


def _good_detected() -> dict[str, object]:
    return {
        "target_entity_type": "mismatch",
        "transition_type": "created",
        "after_state": {
            "mismatch_id": "mis-1",
            "task_id": "t-1",
            "fork_session_id": "s-1",
            "detection_time": "mid_execution",
            "mismatch_summary": "declared read_set entity missing at runtime",
            "affected_aspect": "read_set_entity",
            "severity_judgment": "high",
            "evidence": {
                "declared_state": {"read_set": ["concept-x@v1"]},
                "actual_state": {"read_set": []},
                "discrepancy": "concept-x@v1 not present",
                "detection_evidence_refs": [
                    {"source_type": "projection", "source_id": "concept_graph", "finding": "absent"},
                ],
            },
        },
    }


def _good_resolution() -> dict[str, object]:
    return {
        "target_entity_type": "mismatch",
        "transition_type": "modified",
        "after_state": {
            "mismatch_id": "mis-1",
            "resolution_action": "self_heal",
            "executor_rationale": "re-pinned to concept-x@v2",
            "outcome": "continued",
        },
    }


# ─── EventType + entity type 注册 ────────────────────────────────────────────────


def test_event_types_registered() -> None:
    assert EventType.MISMATCH_DETECTED.value == "MismatchDetected"
    assert EventType.MISMATCH_RESOLUTION_DECIDED.value == "MismatchResolutionDecided"


def test_entity_types_have_mismatch() -> None:
    assert TargetEntityType.MISMATCH.value == "mismatch"
    assert SubjectEntityType.MISMATCH.value == "mismatch"


def test_registry_maps_both_event_types() -> None:
    assert PAYLOAD_REGISTRY[EventType.MISMATCH_DETECTED] is MismatchDetectedPayload
    assert PAYLOAD_REGISTRY[EventType.MISMATCH_RESOLUTION_DECIDED] is MismatchResolutionDecidedPayload


def test_both_are_fail_closed_enforced() -> None:
    """照 T-L1-37 范式: 唯一生产者 emit conforming → 留在 ENFORCED (非 exempt 空转)."""
    assert EventType.MISMATCH_DETECTED in PAYLOAD_VALIDATION_ENFORCED
    assert EventType.MISMATCH_RESOLUTION_DECIDED in PAYLOAD_VALIDATION_ENFORCED


# ─── payload validate (§3.2 / §3.3 字段) ────────────────────────────────────────


def test_detected_payload_conforms() -> None:
    # 写边界 (registry.conforms) 用 strict=False — JSON-derived dict, 枚举是字符串 (序列化产物)。
    assert conforms(EventType.MISMATCH_DETECTED, _good_detected()) is None
    p = MismatchDetectedPayload.model_validate(_good_detected(), strict=False)
    assert p.after_state.detection_time is MismatchDetectionTime.MID_EXECUTION
    assert p.after_state.affected_aspect is MismatchAffectedAspect.READ_SET_ENTITY
    assert p.after_state.severity_judgment is MismatchSeverity.HIGH


def test_resolution_payload_conforms() -> None:
    assert conforms(EventType.MISMATCH_RESOLUTION_DECIDED, _good_resolution()) is None
    p = MismatchResolutionDecidedPayload.model_validate(_good_resolution(), strict=False)
    assert p.after_state.resolution_action is MismatchResolutionAction.SELF_HEAL
    assert p.after_state.outcome is MismatchResolutionOutcome.CONTINUED


def test_detected_extra_field_rejected() -> None:
    """extra=forbid: event_type↔payload mismatch (混入异类字段) 被写边界拒."""
    bad = _good_detected()
    bad["bogus_top_field"] = "x"
    assert conforms(EventType.MISMATCH_DETECTED, bad) is not None


def test_detected_missing_required_rejected() -> None:
    bad = _good_detected()
    del bad["after_state"]
    assert conforms(EventType.MISMATCH_DETECTED, bad) is not None


def test_detected_wrong_discriminator_rejected() -> None:
    """transition_type 必须 created (discriminator) — 写错被拒."""
    bad = _good_detected()
    bad["transition_type"] = "modified"
    assert conforms(EventType.MISMATCH_DETECTED, bad) is not None


def test_detected_bad_enum_rejected_nonstrict() -> None:
    """即便非 strict (写边界模式), 非法 severity 枚举值仍被拒 — 不是装样子的宽松 pass."""
    bad = _good_detected()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["severity_judgment"] = "not-a-severity"
    assert conforms(EventType.MISMATCH_DETECTED, bad) is not None
    with pytest.raises(ValidationError):
        MismatchDetectedPayload.model_validate(bad, strict=False)
