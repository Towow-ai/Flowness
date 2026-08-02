"""RUN-038 波2 — AdvisorConsultRequested / AdvisorVerdictDelivered schema 注册 (T-L1-40 / T-L1-49).

# spec source:
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §3.5 advisor-consult 协议
#
# 病 (RUN-038 波2 前): advisor-consult 协议事件 (AdvisorConsultRequested / AdvisorVerdictDelivered)
#   未注册 — 不在 EventType / PAYLOAD_REGISTRY, 缺 payload。executor 遇需 advisor 决策的
#   不确定时无 canonical 事件可 emit, advisor fork 事件链不可达。
#
# 修: 真注册 EventType + payload + entity type, 照 T-L1-37/T-L1-38 范式 —
#   payload conforming, 留在 PAYLOAD_VALIDATION_ENFORCED (fail-closed)。
"""

from __future__ import annotations

from towow.schemas.enums import (
    AdvisorConsultTrigger,
    AdvisorVerdictConfidence,
    AdvisorVerdictDecision,
    EventType,
    SubjectEntityType,
    TargetEntityType,
)
from towow.schemas.payloads.registry import (
    PAYLOAD_REGISTRY,
    PAYLOAD_VALIDATION_ENFORCED,
    conforms,
)
from towow.schemas.payloads.state_transition import (
    AdvisorConsultRequestedPayload,
    AdvisorVerdictDeliveredPayload,
)


def _good_consult() -> dict[str, object]:
    return {
        "target_entity_type": "advisor_consult",
        "transition_type": "created",
        "after_state": {
            "consult_id": "adv-1",
            "task_id": "t-1",
            "triggered_by": "mismatch",
            "question": "declared read_set entity missing — self_heal or escalate?",
            "executor_tentative_answer": "lean self_heal by re-pinning",
            "executor_uncertainty": "unsure whether re-pin breaks plan assumption",
            "mismatch_id": "mis-1",
        },
    }


# ─── EventType + entity type 注册 ────────────────────────────────────────────────


def test_event_type_registered() -> None:
    assert EventType.ADVISOR_CONSULT_REQUESTED.value == "AdvisorConsultRequested"


def test_entity_type_has_advisor_consult() -> None:
    assert TargetEntityType.ADVISOR_CONSULT.value == "advisor_consult"
    assert SubjectEntityType.ADVISOR_CONSULT.value == "advisor_consult"


def test_trigger_enum_has_6_values() -> None:
    """M-1.4 §3.5 triggered_by 6 类 (含 v2.1 新增 verdict_evidence_scope_breach)."""
    values = {t.value for t in AdvisorConsultTrigger}
    assert values == {
        "mismatch",
        "self_check_doubt",
        "mid_execution_judgment",
        "done_criteria_ambiguity",
        "implementation_choice",
        "verdict_evidence_scope_breach",
    }


def test_registry_maps_event_type() -> None:
    assert PAYLOAD_REGISTRY[EventType.ADVISOR_CONSULT_REQUESTED] is AdvisorConsultRequestedPayload


def test_is_fail_closed_enforced() -> None:
    """照 T-L1-37/T-L1-38 范式: 唯一生产者 emit conforming → 留在 ENFORCED (非 exempt 空转)."""
    assert EventType.ADVISOR_CONSULT_REQUESTED in PAYLOAD_VALIDATION_ENFORCED


# ─── payload validate (§3.5 字段) ───────────────────────────────────────────────


def test_consult_payload_conforms() -> None:
    assert conforms(EventType.ADVISOR_CONSULT_REQUESTED, _good_consult()) is None
    p = AdvisorConsultRequestedPayload.model_validate(_good_consult(), strict=False)
    assert p.after_state.triggered_by is AdvisorConsultTrigger.MISMATCH
    assert p.after_state.consult_id == "adv-1"
    assert p.after_state.mismatch_id == "mis-1"


def test_consult_optional_fields_default_none() -> None:
    """mismatch_id / previous_verdict_event_id / new_evidence_summary 视 triggered_by 可空."""
    payload = _good_consult()
    after = payload["after_state"]
    assert isinstance(after, dict)
    after.pop("mismatch_id")
    p = AdvisorConsultRequestedPayload.model_validate(payload, strict=False)
    assert p.after_state.mismatch_id is None
    assert p.after_state.previous_verdict_event_id is None


def test_consult_extra_field_rejected() -> None:
    """extra=forbid: event_type↔payload mismatch (混入异类字段) 被写边界拒."""
    bad = _good_consult()
    bad["bogus_top_field"] = "x"
    assert conforms(EventType.ADVISOR_CONSULT_REQUESTED, bad) is not None


def test_consult_missing_required_rejected() -> None:
    """缺 question (必填 min_length=1) → 写边界拒."""
    bad = _good_consult()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after.pop("question")
    assert conforms(EventType.ADVISOR_CONSULT_REQUESTED, bad) is not None


def test_consult_empty_question_rejected() -> None:
    """question min_length=1 → 空串拒 (防 vacuous consult)."""
    bad = _good_consult()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["question"] = ""
    assert conforms(EventType.ADVISOR_CONSULT_REQUESTED, bad) is not None


def test_consult_bad_trigger_rejected() -> None:
    """triggered_by 必须是 6 个枚举之一 → 杂值拒."""
    bad = _good_consult()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["triggered_by"] = "not_a_real_trigger"
    assert conforms(EventType.ADVISOR_CONSULT_REQUESTED, bad) is not None


# ════════════════════════════════════════════════════════════════════════════════
#  AdvisorVerdictDelivered (T-L1-49) — M-1.4 §3.5 v2.1 独立化
# ════════════════════════════════════════════════════════════════════════════════


def _good_verdict() -> dict[str, object]:
    return {
        "target_entity_type": "advisor_consult",
        "transition_type": "modified",
        "after_state": {
            "consult_id": "adv-1",
            "advisor_session_id": "opus-fork-1",
            "decision": "use_path_self_heal",
            "rationale": "re-pin is safe; plan assumption unaffected since v2 supersedes v1 cleanly",
            "confidence": "high",
            "evidence_scope_summary": "read TaskPackage done_criteria + concept_graph @v1/@v2 diff",
        },
    }


def test_verdict_event_registered() -> None:
    assert EventType.ADVISOR_VERDICT_DELIVERED.value == "AdvisorVerdictDelivered"


def test_verdict_decision_enum_has_5_values() -> None:
    assert {d.value for d in AdvisorVerdictDecision} == {
        "use_path_self_heal",
        "use_path_consult_again",
        "use_path_trigger_replan",
        "use_path_abort_task",
        "custom_action",
    }


def test_verdict_confidence_enum_has_3_values() -> None:
    assert {c.value for c in AdvisorVerdictConfidence} == {"high", "medium", "low"}


def test_verdict_registry_and_enforced() -> None:
    assert PAYLOAD_REGISTRY[EventType.ADVISOR_VERDICT_DELIVERED] is AdvisorVerdictDeliveredPayload
    assert EventType.ADVISOR_VERDICT_DELIVERED in PAYLOAD_VALIDATION_ENFORCED


def test_verdict_payload_conforms() -> None:
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, _good_verdict()) is None
    p = AdvisorVerdictDeliveredPayload.model_validate(_good_verdict(), strict=False)
    assert p.after_state.decision is AdvisorVerdictDecision.USE_PATH_SELF_HEAL
    assert p.after_state.confidence is AdvisorVerdictConfidence.HIGH


def test_verdict_empty_rationale_rejected() -> None:
    """rationale min_length=1 (必填) → 空串拒 (防 oracle verdict 无理由)."""
    bad = _good_verdict()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["rationale"] = ""
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, bad) is not None


def test_verdict_empty_evidence_scope_rejected() -> None:
    """evidence_scope_summary v2.1 必填 min_length=1 → 空拒 (evidence scope binding)."""
    bad = _good_verdict()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["evidence_scope_summary"] = ""
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, bad) is not None


def test_verdict_custom_action_requires_steps() -> None:
    """decision=custom_action 且无 specific_steps → 拒 (M-1.4 §3.5, 否则 verdict 空心)."""
    bad = _good_verdict()
    after = bad["after_state"]
    assert isinstance(after, dict)
    after["decision"] = "custom_action"
    # no specific_steps → reject
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, bad) is not None
    # with specific_steps → conform
    after["specific_steps"] = ["step A", "step B"]
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, bad) is None


def test_verdict_extra_field_rejected() -> None:
    bad = _good_verdict()
    bad["bogus"] = "x"
    assert conforms(EventType.ADVISOR_VERDICT_DELIVERED, bad) is not None


# ─── T-L1-49: Mismatch/Advisor 4 类全注册 (Narrow Patch Y 收口) ───────────────────


def test_mismatch_advisor_cluster_all_registered_and_enforced() -> None:
    """T-L1-49 done_criteria: Mismatch/Advisor 4 类全在 PAYLOAD_REGISTRY 且 gate 校验。

    v2.1 cleanup 后 cluster = MismatchDetected / MismatchResolutionDecided /
    AdvisorConsultRequested / AdvisorVerdictDelivered (不含已删 MismatchVerdict, T-L1-46)。
    Patch Y "6 event_type" = 这 4 + PatchProposed + TaskRunCompleted。
    """
    cluster = [
        EventType.MISMATCH_DETECTED,
        EventType.MISMATCH_RESOLUTION_DECIDED,
        EventType.ADVISOR_CONSULT_REQUESTED,
        EventType.ADVISOR_VERDICT_DELIVERED,
    ]
    for et in cluster:
        assert et in PAYLOAD_REGISTRY, et
        assert et in PAYLOAD_VALIDATION_ENFORCED, et
    # Patch Y 全 6: cluster 4 + PatchProposed + TaskRunCompleted
    for et in (EventType.PATCH_PROPOSED, EventType.TASK_RUN_COMPLETED):
        assert et in PAYLOAD_REGISTRY, et
    # MismatchVerdict 不复活 (T-L1-46)
    assert "MISMATCH_VERDICT" not in EventType.__members__
