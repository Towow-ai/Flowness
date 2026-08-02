"""M-1.2 §3.3 ConceptStateMachineDefined event payload (F5 fix). Sequential emit; atomic 留 future."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class ConceptStateMachineState(BaseModel):
    model_config = _STRICT
    # ``state-machine-define`` emits state_id/state_name.  ``name`` remains the serialized
    # compatibility key because existing concept_graph consumers read states[].name.
    name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("name", "state_id"),
    )
    state_name: str = ""
    description: str = ""
    is_initial: bool = False
    is_terminal: bool = False


class ConceptStateMachineTransition(BaseModel):
    model_config = _STRICT
    # Accept the fork contract while serializing the legacy graph keys.  The rich fields are
    # retained structurally rather than collapsed into ``condition`` prose.
    from_state: str = Field(
        min_length=1,
        validation_alias=AliasChoices("from_state", "from_state_id"),
    )
    to_state: str = Field(
        min_length=1,
        validation_alias=AliasChoices("to_state", "to_state_id"),
    )
    triggering_event: str = Field(
        min_length=1,
        validation_alias=AliasChoices("triggering_event", "trigger_condition"),
    )
    actor: str = ""
    guard: str = ""
    action: str = ""
    compensation: str = ""
    condition: str = ""


class ConceptStateMachineDefinedPayload(BaseModel):
    model_config = _STRICT
    kind: Literal["ConceptStateMachineDefined"] = "ConceptStateMachineDefined"
    concept_id: str = Field(min_length=1)
    states: list[ConceptStateMachineState] = Field(min_length=1)
    transitions: list[ConceptStateMachineTransition] = Field(default_factory=list)
    # M-1.2 §3.3 / §6.1 #7 — v3 强制 SAGA 模式 (含补偿动作). 语法层校验
    # (state_machine_syntax.validate_state_machine_syntax) 要求其为 true; 这里默认 true 保留
    # 既有 schema 构造调用向后兼容, 校验在 §6.3 blocking gate.
    saga_pattern: bool = True


__all__ = [
    "ConceptStateMachineDefinedPayload",
    "ConceptStateMachineState",
    "ConceptStateMachineTransition",
]
