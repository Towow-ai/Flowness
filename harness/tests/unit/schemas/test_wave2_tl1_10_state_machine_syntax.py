"""波2 T-L1-10: ConceptStateMachineDefined §6.1 6条语法强制校验 + §6.3 可达性.

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §6.1 + §6.3
#
done_criteria (源表): 提交违反"initial唯一/可达性"的状态机被 freeze 拒; events.log 出现
ConceptStateMachineDefined 且 reducer 入图。本测试钉住语法层 6 条 fail-closed 校验
(initial唯一 / ≥2态 / ≥1转移 / transition ref 真实 / SAGA / 可达性)。reducer 物化测试见
tests/integration/test_wave2_tl1_10_state_machine_reducer.py。
"""

from __future__ import annotations

from towow.schemas.payloads.concept_state_machine import (
    ConceptStateMachineDefinedPayload,
    ConceptStateMachineState,
    ConceptStateMachineTransition,
)
from towow.schemas.payloads.state_machine_syntax import (
    state_machine_syntax_evidence,
    validate_state_machine_syntax,
)


def _state(name: str, *, initial: bool = False, terminal: bool = False) -> ConceptStateMachineState:
    return ConceptStateMachineState(
        name=name,
        state_name=name.replace("_", " ").title(),
        is_initial=initial,
        is_terminal=terminal,
    )


def _transition(from_state: str, to_state: str, event: str) -> ConceptStateMachineTransition:
    return ConceptStateMachineTransition(
        from_state=from_state,
        to_state=to_state,
        triggering_event=event,
        actor="executor",
        guard="source state is current",
        action="emit ConceptStateTransition",
        compensation="restore the source state",
    )


def _legal() -> ConceptStateMachineDefinedPayload:
    """A legal SAGA state machine: queued → running → done, all reachable."""
    return ConceptStateMachineDefinedPayload(
        concept_id="concept-runs-status@v1",
        states=[
            _state("queued", initial=True),
            _state("running"),
            _state("done", terminal=True),
        ],
        transitions=[
            _transition("queued", "running", "Started"),
            _transition("running", "done", "Finished"),
        ],
        saga_pattern=True,
    )


def test_legal_state_machine_passes() -> None:
    ok, errors = validate_state_machine_syntax(_legal())
    assert ok, f"legal machine wrongly rejected: {errors}"
    assert errors == []


# ─── C1: ≥ 2 states ──────────────────────────────────────────────────────────
def test_c1_single_state_rejected() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("only", initial=True)],
        transitions=[_transition("only", "only", "X")],
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C1") for e in errors), errors


# ─── C2: exactly 1 initial ───────────────────────────────────────────────────
def test_c2_zero_initial_rejected() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a"), _state("b", terminal=True)],
        transitions=[_transition("a", "b", "X")],
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C2") for e in errors), errors


def test_c2_two_initials_rejected() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a", initial=True), _state("b", initial=True)],
        transitions=[_transition("a", "b", "X")],
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C2") for e in errors), errors


# ─── C3: ≥ 1 transition ──────────────────────────────────────────────────────
def test_c3_no_transitions_rejected() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a", initial=True), _state("b", terminal=True)],
        transitions=[],
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C3") for e in errors), errors


# ─── C4: transition refs real states ─────────────────────────────────────────
def test_c4_dangling_transition_ref_rejected() -> None:
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a", initial=True), _state("b", terminal=True)],
        transitions=[_transition("a", "ghost", "X")],
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C5 transition_ref") for e in errors), errors


# ─── C5: SAGA enforced ───────────────────────────────────────────────────────
def test_c5_non_saga_rejected() -> None:
    p = _legal().model_copy(update={"saga_pattern": False})
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C6") for e in errors), errors


# ─── C6: reachability ────────────────────────────────────────────────────────
def test_c6_unreachable_dead_state_rejected() -> None:
    """orphan state 不被任何 transition 指向 → 不可达死态。"""
    p = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a", initial=True), _state("b", terminal=True), _state("orphan")],
        transitions=[_transition("a", "b", "X")],
        saga_pattern=True,
    )
    ok, errors = validate_state_machine_syntax(p)
    assert not ok
    assert any(e.startswith("C7") for e in errors), errors


def test_missing_rich_transition_contract_rejected() -> None:
    payload = ConceptStateMachineDefinedPayload(
        concept_id="c@v1",
        states=[_state("a", initial=True), _state("b", terminal=True)],
        transitions=[
            ConceptStateMachineTransition(
                from_state="a",
                to_state="b",
                triggering_event="X",
            ),
        ],
    )
    ok, errors = validate_state_machine_syntax(payload)
    assert not ok
    assert any(e.startswith("C5 transition_contract") for e in errors), errors


def test_evidence_shape_for_blocking_check() -> None:
    ev = state_machine_syntax_evidence(_legal())
    assert ev["states_count"] == 3
    assert ev["initial_states_count"] == 1
    assert ev["terminal_states_count"] == 1
    assert ev["transitions_count"] == 2
    assert ev["saga_pattern"] is True
    assert ev["all_passed"] is True
