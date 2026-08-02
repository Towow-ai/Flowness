"""M-1.1 §7 sub-skill invocation skipped event payload (F11 fix).

spec source: DOGFOOD-RUN-002 review F11 closure_contract.

agent (采访棒) 判定 sub-skill 触发条件未命中时, 应 emit SubSkillInvocationSkipped
留痕原因 (而不是 silent skip). 未来回头审, 能区分 'agent 真判未命中' 还是 'agent
忘了调'.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class SubSkillInvocationSkippedPayload(BaseModel):
    """SubSkillInvocationSkipped event payload (M-1.1 §7 触发审计).

    Mandatory:
      - session_id non-empty (provenance.session_id 应 match)
      - sub_skill_id 非空 (e.g. "interview-conops-construct")
      - decision_rationale 一句话为什么判 skipped
      - triggering_condition_evaluation: spec line + 实际对话评估

    Audit use:
      F11 closure — 当 agent 决定不调某 sub-skill 时, emit 此 event 留痕原因.
      未来 review 可反查: 是真没触发 还是 agent 忘了 / 错判.
    """

    model_config = _STRICT

    kind: Literal["SubSkillInvocationSkipped"] = "SubSkillInvocationSkipped"
    session_id: str = Field(min_length=1)
    sub_skill_id: str = Field(min_length=1)
    decision_rationale: str = Field(min_length=1)
    triggering_condition_evaluation: str = Field(default="")


__all__ = ["SubSkillInvocationSkippedPayload"]
