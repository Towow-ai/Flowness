"""task-m04-wire-cli-submit — CLI submit-path envelope payoff, end-to-end.

Locks the observable contract task-m04 exists to deliver: the CLI submit path no longer
hand-rolls a boundary-less stub dict payload but routes an envelope through the l0/envelope
engine so the STORED TransactionEnvelope is a real, derivable, boundary-checked record.

Each test pins one of task-m04's four read-set concepts against the real `towow submit` path
(the same path the execution playbook drives with `submit envelope.json`):

  - m04-envelope-build-from-model  : envelope stored as a canonical TransactionEnvelope event
  - m04-readset-writeset-derivation : write_set is SYSTEM-derived from the committed domain
                                      events (not the agent's self-report)
  - m04-claims-boundary-check       : a write outside the Planner claim is rejected at the gate
  - m04-envelope-roundtrip-readback : read_envelope reconstructs the submitted fields losslessly

# spec source: M-0.4-envelope-detailed-design.md §3.1 (submit-wrapper derives) / §3.3 (derive
#   rules) / §4 (path B storage) / §6.2 (read-back) ; M-0.5 §3.6 (ClaimsBoundary).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from towow.cli.main import cli
from towow.l0.envelope.reader import read_envelope
from towow.l0.event_log import EventLog
from towow.schemas.enums import EventType

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(tmp_path: Path, runner: CliRunner) -> Path:
    result = runner.invoke(cli, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _concept_intent(concept_id: str) -> dict[str, object]:
    return {
        "local_intent_id": f"li-{concept_id}",
        "event_type": "ConceptCreated",
        "event_category": "state_transition",
        "payload": {"concept_id": concept_id},
        "provenance_hint": {"actor_type": "system", "actor_id": "sys-m04"},
        "base_classification": "abstractable_process",
        "supersede": {"is_supersede": False},
        "subjects": [{"entity_type": "concept", "entity_id": concept_id, "role": "primary"}],
        "schema_version": "1.0.0",
    }


def _claim_intent(tag: str, event_type: str, task_id: str, set_key: str, entities: list[dict]) -> dict:
    return {
        "local_intent_id": f"{tag}-{set_key}",
        "event_type": event_type,
        "event_category": "state_transition",
        "payload": {
            "target_entity_type": "task",
            "transition_type": "modified",
            "after_state": {"task_id": task_id, set_key: entities},
        },
        "provenance_hint": {"actor_type": "system", "actor_id": "m13-planner"},
        "base_classification": "abstractable_process",
        "supersede": {"is_supersede": False},
        "subjects": [{"entity_type": "task", "entity_id": task_id, "role": "primary"}],
        "schema_version": "1.0.0",
    }


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _last_event(project: Path) -> dict:
    lines = (project / ".towow" / "events.log").read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


def _emit_planner_claims(
    project: Path,
    runner: CliRunner,
    tmp_path: Path,
    *,
    task_id: str,
    write_concept: str,
    tag: str,
) -> None:
    """Pre-seed Planner read/write claims so the submit-derived envelope.claims has a real
    boundary to check against (claims are SYSTEM-derived from Planner events, not agent-filled)."""
    claim_env = tmp_path / f"claim-{tag}.json"
    _write_json(
        claim_env,
        {
            "envelope_id": f"env-claim-{tag}",
            "task_id": task_id,
            "run_id": "r-claim",
            "fork_id": "fork-claim",
            "capsule_compiled_event_id": "evt-stub-capsule-claim",
            "as_of_projection_seq": 0,
            "self_check": {"passed": True, "checks_run": [{"check_type": "claim", "passed": True}]},
            "event_intents": [
                _claim_intent(
                    tag, "TaskReadSetClaimed", task_id, "read_set",
                    [{"entity_type": "task", "entity_id": task_id}],
                ),
                _claim_intent(
                    tag, "TaskWriteSetClaimed", task_id, "write_set",
                    [{"entity_type": "concept", "entity_id": write_concept}],
                ),
            ],
        },
    )
    r = runner.invoke(cli, ["submit", str(claim_env), "--project-dir", str(project)])
    assert r.exit_code == 0, r.output


def test_compliant_envelope_accepted_and_reads_back_identical(
    project: Path, runner: CliRunner, tmp_path: Path,
) -> None:
    """m04-envelope-build-from-model + m04-envelope-roundtrip-readback:
    a compliant envelope is accepted and reconstructs losslessly from the event log."""
    _emit_planner_claims(project, runner, tmp_path, task_id="t-m04", write_concept="c-m04", tag="ok")
    env_path = tmp_path / "env.json"
    _write_json(
        env_path,
        {
            "envelope_id": "env-m04-ok",
            "task_id": "t-m04",
            "run_id": "r-m04",
            "fork_id": "fork-m04",
            "capsule_compiled_event_id": "evt-stub-capsule-m04",
            "as_of_projection_seq": 0,
            "self_check": {"passed": True, "checks_run": [{"check_type": "m04-roundtrip", "passed": True}]},
            "event_intents": [_concept_intent("c-m04")],
        },
    )
    result = runner.invoke(cli, ["submit", str(env_path), "--project-dir", str(project)])
    assert result.exit_code == 0, result.output
    assert "✓ commit accepted" in result.output

    log = EventLog(project / ".towow" / "events.log")
    env_records = log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED)
    submitted = next(r for r in env_records if r.payload.get("envelope_id") == "env-m04-ok")
    # M-0.4 §4: the envelope lives on Path B (no batch_id), audit-visible on its own.
    assert submitted.batch_id is None
    # M-0.4 §6.2: read-back reconstructs the canonical model with the submitted identity intact.
    env = read_envelope(log, submitted.event_id)
    assert env.envelope_id == "env-m04-ok"
    assert env.task_id == "t-m04"
    assert env.run_id == "r-m04"
    assert env.fork_id == "fork-m04"
    assert {(e.entity_type.value, e.entity_id) for e in env.write_set} == {("concept", "c-m04")}
    assert env.claims  # Planner-derived claims carried through


def test_write_set_is_system_derived_not_agent_selfreport(
    project: Path, runner: CliRunner, tmp_path: Path,
) -> None:
    """m04-readset-writeset-derivation: write_set is derived from the committed domain events,
    OVERRIDING an agent self-report that claims a different (fake) write."""
    _emit_planner_claims(
        project, runner, tmp_path, task_id="t-m04", write_concept="c-real", tag="derive",
    )
    env_path = tmp_path / "env.json"
    _write_json(
        env_path,
        {
            "envelope_id": "env-m04-derive",
            "task_id": "t-m04",
            "run_id": "r-m04",
            "fork_id": "fork-m04",
            "capsule_compiled_event_id": "evt-stub-capsule-m04",
            "as_of_projection_seq": 0,
            "self_check": {"passed": True, "checks_run": [{"check_type": "m04-derive", "passed": True}]},
            # agent self-reports writing a concept it did NOT actually emit an event for:
            "write_set": [{"entity_type": "concept", "entity_id": "c-agent-lied", "change_type": "created"}],
            "event_intents": [_concept_intent("c-real")],
        },
    )
    result = runner.invoke(cli, ["submit", str(env_path), "--project-dir", str(project)])
    assert result.exit_code == 0, result.output

    log = EventLog(project / ".towow" / "events.log")
    env_records = log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED)
    submitted = next(r for r in env_records if r.payload.get("envelope_id") == "env-m04-derive")
    env = read_envelope(log, submitted.event_id)
    # The stored write_set reflects the REAL committed event (c-real), not the agent's claim.
    assert {(e.entity_type.value, e.entity_id) for e in env.write_set} == {("concept", "c-real")}
    assert not any(e.entity_id == "c-agent-lied" for e in env.write_set)


def test_out_of_bounds_write_rejected_at_claims_boundary(
    project: Path, runner: CliRunner, tmp_path: Path,
) -> None:
    """m04-claims-boundary-check: a write outside the Planner's write-claim is rejected by the
    gate (CLAIM_BOUNDARY_EXCEEDED / write_set_exceeds_claims)."""
    _emit_planner_claims(
        project, runner, tmp_path, task_id="t-m04", write_concept="c-allowed", tag="oob",
    )
    env_path = tmp_path / "env.json"
    _write_json(
        env_path,
        {
            "envelope_id": "env-m04-oob",
            "task_id": "t-m04",
            "run_id": "r-m04",
            "fork_id": "fork-m04",
            "capsule_compiled_event_id": "evt-stub-capsule-m04",
            "as_of_projection_seq": 0,
            "self_check": {"passed": True, "checks_run": [{"check_type": "m04-oob", "passed": True}]},
            "read_set": [{"entity_type": "task", "entity_id": "t-m04", "version": "evt-cap-m04"}],
            "event_intents": [_concept_intent("c-unclaimed")],
        },
    )
    result = runner.invoke(cli, ["submit", str(env_path), "--project-dir", str(project)])
    assert result.exit_code == 2, result.output
    assert "commit rejected" in result.output
    sentinel = _last_event(project)
    assert sentinel["event_type"] == "CommitRejected"
    assert sentinel["payload"]["rejection_type"] == "claim_boundary_exceeded"
    assert sentinel["payload"]["rejection_reasons"][0]["failure_reason"] == "write_set_exceeds_claims"
