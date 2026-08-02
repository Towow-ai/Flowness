"""M-1.2 §6.1 + §6.3 — ConceptStateMachineDefined 语法层强制校验 (T-L1-10).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §6.1 v3 强制语法层 (8 条, L671..L684):
#       1. states ≥ 2
#       2. 恰好 1 个 is_initial=true
#       3. 0 或多个 is_terminal=true
#       4. 每个 state 有 state_id + state_name + description
#       5. transitions ≥ 1
#       6. 每个 transition 有 from/to/trigger + actor + guard + action + compensation
#       7. saga_pattern: true (v3 强制)
#       8. 状态转移产生 event (事件化 — emit 路径保证)
#     §6.3 SkillArtifactSelfCheck blocking_checks (L699..L718):
#       - consensus.state_machine_syntax_valid (states/initial/terminal/transitions/saga)
#       - consensus.state_machine_reachability (all_states_reachable_from_initial /
#         no_unreachable_dead_states)
#
# Why this module (T-L1-10):
#   ConceptStateMachineDefinedPayload (F5) was a pure shape — it accepted a single state,
#   zero transitions, no SAGA, and unreachable dead states. The spec DESIGNED a fail-closed
#   syntax gate ("v3 语法层强制" §6.1 + blocking_checks §6.3) but nothing enforced it, so an
#   illegal state machine (no initial / unreachable state / no SAGA) could be emitted clean.
#   Same class as F-026-1 (gate designed but unwired). This module is the wired gate: an
#   illegal state-machine definition is rejected; a legal one passes; the concept_graph
#   reducer materializes the validated machine onto the node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.schemas.payloads.concept_state_machine import (
        ConceptStateMachineDefinedPayload,
    )


def _reachable_state_names(
    initial_name: str,
    transitions: list[tuple[str, str]],
) -> set[str]:
    """BFS over transition edges (from_state → to_state) from the initial state."""
    adjacency: dict[str, list[str]] = {}
    for frm, to in transitions:
        adjacency.setdefault(frm, []).append(to)
    seen = {initial_name}
    frontier = [initial_name]
    while frontier:
        node = frontier.pop()
        for nxt in adjacency.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def validate_state_machine_syntax(
    payload: ConceptStateMachineDefinedPayload,
) -> tuple[bool, list[str]]:
    """M-1.2 §6.1 强制语法 + §6.3 reachability. Returns (all_passed, errors).

    The seven enforced syntax-layer checks are:
      C1. states ≥ 2
      C2. 恰好 1 个 is_initial=true
      C3. transitions ≥ 1
      C4. 每个 state 保留非空 state_name
      C5. transition 引用真实 state，并保留 actor/guard/action/compensation 及事件动作
      C6. saga_pattern: true (v3 强制 SAGA — 含补偿动作)
      C7 (§6.3). 可达性: 从 initial 出发所有非 initial state 可达 + 无不可达死态

    State/transition identifiers and trigger non-emptiness are guaranteed at the Pydantic
    schema layer; this function additionally enforces the fork/publish semantic contract and
    graph-level invariants that field shapes cannot express.
    """
    errors: list[str] = []
    state_names = [s.name for s in payload.states]

    # C1: ≥ 2 states
    if len(payload.states) < 2:
        errors.append(
            f"C1 states_count: 需 ≥ 2 个状态 (语法层强制), 实得 {len(payload.states)}",
        )

    # C2: exactly 1 initial
    initials = [s for s in payload.states if s.is_initial]
    if len(initials) != 1:
        errors.append(
            f"C2 initial_unique: 需恰好 1 个 is_initial=true, 实得 {len(initials)}",
        )

    # C3: ≥ 1 transition
    if len(payload.transitions) < 1:
        errors.append(
            f"C3 transitions_count: 需 ≥ 1 个 transition, 实得 {len(payload.transitions)}",
        )

    # C4: every state carries the human-readable name required by the fork contract.
    for state in payload.states:
        if not state.state_name.strip():
            errors.append(
                f"C4 state_name: state {state.name!r} 缺非空 state_name",
            )

    # C5: every transition references existing states and retains the rich executable contract.
    name_set = set(state_names)
    for t in payload.transitions:
        if t.from_state not in name_set:
            errors.append(
                f"C5 transition_ref: from_state {t.from_state!r} 不在 states 列表",
            )
        if t.to_state not in name_set:
            errors.append(
                f"C5 transition_ref: to_state {t.to_state!r} 不在 states 列表",
            )
        rich_fields = {
            "actor": t.actor,
            "guard": t.guard,
            "action": t.action,
            "compensation": t.compensation,
        }
        missing = [name for name, value in rich_fields.items() if not value.strip()]
        if missing:
            errors.append(
                f"C5 transition_contract: {t.from_state!r}→{t.to_state!r} "
                f"缺非空字段 {missing}",
            )
        if t.action.strip() and "ConceptStateTransition" not in t.action:
            errors.append(
                f"C5 transition_event: {t.from_state!r}→{t.to_state!r} 的 action "
                "未声明 emit ConceptStateTransition",
            )

    # C6: saga_pattern must be true (v3 强制)
    if not payload.saga_pattern:
        errors.append(
            "C6 saga_pattern: v3 强制 SAGA 模式 (saga_pattern=true, 含补偿动作)",
        )

    # C7 (§6.3): reachability — needs a valid single initial + valid transition refs first.
    if len(initials) == 1 and not any(e.startswith("C5 transition_ref") for e in errors):
        reachable = _reachable_state_names(
            initials[0].name,
            [(t.from_state, t.to_state) for t in payload.transitions],
        )
        unreachable = sorted(set(state_names) - reachable)
        if unreachable:
            errors.append(
                f"C7 reachability: 从 initial {initials[0].name!r} 不可达的死态: {unreachable}",
            )

    return (not errors), errors


def state_machine_syntax_evidence(
    payload: ConceptStateMachineDefinedPayload,
) -> dict[str, object]:
    """Audit evidence for the §6.3 blocking_check payload (states/initial/terminal/...)."""
    initials = [s for s in payload.states if s.is_initial]
    terminals = [s for s in payload.states if s.is_terminal]
    all_passed, errors = validate_state_machine_syntax(payload)
    return {
        "states_count": len(payload.states),
        "initial_states_count": len(initials),
        "terminal_states_count": len(terminals),
        "transitions_count": len(payload.transitions),
        "saga_pattern": payload.saga_pattern,
        "all_passed": all_passed,
        "errors": errors,
    }


def validate_state_transition(
    state_machine: dict[str, object] | None,
    from_state: str,
    to_state: str,
) -> tuple[bool, list[str]]:
    """M-1.2 §3.4 — validate a ConceptStateTransition against the concept's materialized machine.

    T-L1-11: a SAGA state transition is only legal if the concept HAS a state machine (T-L1-10
    dependency — 转移必须对照状态机校验), the machine is SAGA (saga_pattern=true, §6.1 #7 v3 强制),
    and (from_state → to_state) is a transition DEFINED in that machine (not an arbitrary jump).

    ``state_machine`` is the materialized dict on the concept_graph node
    ({states, transitions, saga_pattern}) — None / empty means the concept never had a machine
    defined → reject (转移无机可对). Returns (is_legal, errors).
    """
    errors: list[str] = []
    if not isinstance(state_machine, dict) or not state_machine.get("transitions"):
        errors.append(
            "目标 concept 无状态机定义 (先 ConceptStateMachineDefined) — 转移无状态机可校验",
        )
        return False, errors
    if not bool(state_machine.get("saga_pattern", False)):
        errors.append("状态机非 SAGA (saga_pattern≠true) — v3 强制 SAGA 转移 (§6.1 #7)")
    transitions_raw = state_machine.get("transitions")
    transitions = transitions_raw if isinstance(transitions_raw, list) else []
    states_raw = state_machine.get("states")
    state_names = {
        s.get("name") for s in states_raw if isinstance(s, dict)
    } if isinstance(states_raw, list) else set()
    if state_names and from_state not in state_names:
        errors.append(f"from_state {from_state!r} 不在状态机 states")
    if state_names and to_state not in state_names:
        errors.append(f"to_state {to_state!r} 不在状态机 states")
    defined = any(
        isinstance(t, dict) and t.get("from_state") == from_state and t.get("to_state") == to_state
        for t in transitions
    )
    if not defined:
        errors.append(
            f"transition {from_state!r}→{to_state!r} 未在状态机 transitions 中定义 (非法跳转)",
        )
    return (not errors), errors


__all__ = [
    "state_machine_syntax_evidence",
    "validate_state_machine_syntax",
    "validate_state_transition",
]
