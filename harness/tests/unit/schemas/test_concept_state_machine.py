"""F4+F5 schema unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import EventType
from towow.schemas.payloads.concept_state_machine import (
    ConceptStateMachineDefinedPayload,
    ConceptStateMachineState,
    ConceptStateMachineTransition,
)


def test_event_type_concept_state_machine_defined_registered() -> None:
    assert EventType.CONCEPT_STATE_MACHINE_DEFINED.value == "ConceptStateMachineDefined"


def test_csm_payload_minimum_valid() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="concept-foo@v1",
        states=[
            ConceptStateMachineState(name="pending", state_name="Pending", is_initial=True),
            ConceptStateMachineState(name="resolved", state_name="Resolved", is_terminal=True),
        ],
    )
    assert p.kind == "ConceptStateMachineDefined"
    assert len(p.states) == 2


def test_csm_payload_with_transitions() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="concept-bar@v1",
        states=[
            ConceptStateMachineState(name="open", state_name="Open", is_initial=True),
            ConceptStateMachineState(name="closed", state_name="Closed", is_terminal=True),
        ],
        transitions=[
            ConceptStateMachineTransition(
                from_state="open",
                to_state="closed",
                triggering_event="ClosedByOwner",
                actor="owner",
                guard="closure evidence is complete",
                action="emit ConceptStateTransition",
                compensation="reopen when evidence is disproved",
            ),
        ],
    )
    assert len(p.transitions) == 1


def test_csm_states_min_length_1() -> None:
    with pytest.raises(ValidationError):
        ConceptStateMachineDefinedPayload(concept_id="c@v1", states=[])


def test_csm_round_trip_json() -> None:
    p = ConceptStateMachineDefinedPayload(concept_id="c@v1", states=[ConceptStateMachineState(name="single")])
    s = p.model_dump_json()
    assert ConceptStateMachineDefinedPayload.model_validate_json(s) == p


def test_fork_shape_aliases_preserve_rich_transition_fields() -> None:
    payload = ConceptStateMachineDefinedPayload(
        concept_id="cutover@v1",
        states=[
            {
                "state_id": "cold",
                "state_name": "Cold",
                "description": "read only",
                "is_initial": True,
                "is_terminal": False,
            },
            {
                "state_id": "active",
                "state_name": "Active",
                "description": "serving",
                "is_initial": False,
                "is_terminal": True,
            },
        ],
        transitions=[
            {
                "from_state_id": "cold",
                "to_state_id": "active",
                "trigger_condition": "owner authorizes",
                "actor": "deployment executor",
                "guard": "old writer stopped",
                "action": "emit ConceptStateTransition",
                "compensation": "stop new writer and restore old writer",
            },
        ],
        saga_pattern=True,
    )
    dumped = payload.model_dump()
    assert dumped["states"][0]["name"] == "cold"
    assert dumped["states"][0]["state_name"] == "Cold"
    assert dumped["transitions"][0]["actor"] == "deployment executor"
    assert dumped["transitions"][0]["compensation"] == "stop new writer and restore old writer"
