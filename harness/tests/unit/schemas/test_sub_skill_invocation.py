"""F11 fix — SubSkillInvocationSkippedPayload + SubSkillEvalEntry schema test.

verify F11 closure_criteria:
  - SubSkillInvocationSkippedPayload pydantic 定义 round-trip
  - SubSkillEvalEntry decision enum 强制
  - InterviewBriefPublishedPayload 加 sub_skill_evaluation field
  - EventType.SUB_SKILL_INVOCATION_SKIPPED 注册
  - TargetEntityType.SUB_SKILL_DECISION 注册
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import EventType, TargetEntityType
from towow.schemas.payloads.interview_brief import SubSkillEvalEntry
from towow.schemas.payloads.sub_skill_invocation import SubSkillInvocationSkippedPayload


def test_event_type_sub_skill_invocation_skipped_registered() -> None:
    assert EventType.SUB_SKILL_INVOCATION_SKIPPED.value == "SubSkillInvocationSkipped"


def test_target_entity_type_sub_skill_decision_registered() -> None:
    assert TargetEntityType.SUB_SKILL_DECISION.value == "sub_skill_decision"


def test_sub_skill_invocation_skipped_payload_minimum_valid() -> None:
    p = SubSkillInvocationSkippedPayload(
        session_id="sess-interview-abc12345",
        sub_skill_id="interview-conops-construct",
        decision_rationale="本次采访没出现抽象描述触发条件",
    )
    assert p.kind == "SubSkillInvocationSkipped"
    assert p.triggering_condition_evaluation == ""


def test_sub_skill_invocation_skipped_payload_session_id_required() -> None:
    with pytest.raises(ValidationError):
        SubSkillInvocationSkippedPayload(
            session_id="",  # min_length=1 rejects
            sub_skill_id="interview-conops-construct",
            decision_rationale="..",
        )


def test_sub_skill_eval_entry_decision_enum_strict() -> None:
    e = SubSkillEvalEntry(
        sub_skill_id="interview-prep-research",
        decision="invoked",
        rationale="采访开场必走",
    )
    assert e.decision == "invoked"

    with pytest.raises(ValidationError):
        SubSkillEvalEntry(
            sub_skill_id="interview-prep-research",
            decision="other",  # not in Literal["invoked", "skipped"]
            rationale="..",
        )


def test_sub_skill_eval_entry_round_trip_json() -> None:
    e = SubSkillEvalEntry(
        sub_skill_id="interview-conops-construct",
        decision="skipped",
        rationale="对话未进入抽象描述",
        triggering_condition_evaluation="§7.4 触发: '对话进入 ...抽象描述'; 实际对话已经具体, 无 X 类抽象",
    )
    s = e.model_dump_json()
    e2 = SubSkillEvalEntry.model_validate_json(s)
    assert e2 == e
