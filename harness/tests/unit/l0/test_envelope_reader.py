"""M-0.4 envelope reader — read back / reconstruct from event log (debt-payoff block 1).

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md §6.2 / §2
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.envelope.builder import build_envelope
from towow.l0.envelope.reader import EnvelopeReadError, list_envelopes, read_envelope
from towow.l0.event_log import EventLog
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.envelope import SelfCheck
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _domain_intent(entity_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"li-{entity_id}",
        event_type=EventType.CONCEPT_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={},
        provenance_hint=ProvenanceHint(actor_type="system", actor_id="sys-1"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _write_real_envelope(log: EventLog, *, envelope_id: str, concept_id: str) -> str:
    env = build_envelope(
        envelope_id=envelope_id,
        task_id="t-1",
        run_id="r-1",
        fork_id="fork-1",
        domain_intents=[_domain_intent(concept_id)],
        capsule_compiled_event_id="evt-cap",
        as_of_projection_seq=7,
        self_check=SelfCheck(passed=True, checks_run=[]),
    )
    intent = EventIntent(
        local_intent_id=envelope_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload=env.model_dump(mode="json"),
        provenance_hint=ProvenanceHint(
            actor_type="agent_fork",
            actor_id="fork-1",
            parent_session_id="sess-1",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    return log.write_direct(intent).event_id


def test_roundtrip_build_write_read(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.log")
    event_id = _write_real_envelope(log, envelope_id="env-roundtrip1", concept_id="c-1")

    env = read_envelope(log, event_id)
    assert env.envelope_id == "env-roundtrip1"
    assert env.task_id == "t-1"
    assert env.as_of_projection_seq == 7
    assert {(e.entity_type.value, e.entity_id) for e in env.write_set} == {("concept", "c-1")}
    assert env.read_set  # non-empty floor preserved through the log


def test_read_missing_event_raises(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.log")
    with pytest.raises(EnvelopeReadError, match="no event"):
        read_envelope(log, "evt-does-not-exist")


def test_read_non_envelope_event_raises(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.log")
    node_touched = EventIntent(
        local_intent_id="nt-1",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"kind": "x"},
        provenance_hint=ProvenanceHint(actor_type="system", actor_id="sys-1"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    rec = log.write_direct(node_touched)
    with pytest.raises(EnvelopeReadError, match="not an envelope"):
        read_envelope(log, rec.event_id)


def test_read_stub_payload_raises(tmp_path: Path) -> None:
    """A boundary-less stub envelope cannot be read back as a canonical TransactionEnvelope."""
    log = EventLog(tmp_path / "events.log")
    stub = EventIntent(
        local_intent_id="env-stub",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={"envelope_id": "env-stub", "self_check": {"passed": True}},  # no read_set/write_set
        provenance_hint=ProvenanceHint(actor_type="system", actor_id="sys-1"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    rec = log.write_direct(stub)
    with pytest.raises(EnvelopeReadError, match="not a canonical TransactionEnvelope"):
        read_envelope(log, rec.event_id)


def test_list_envelopes_skips_stubs(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.log")
    real_id = _write_real_envelope(log, envelope_id="env-real1", concept_id="c-1")
    stub = EventIntent(
        local_intent_id="env-stub2",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={"envelope_id": "env-stub2"},
        provenance_hint=ProvenanceHint(actor_type="system", actor_id="sys-1"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    log.write_direct(stub)

    found = list_envelopes(log)
    assert [eid for eid, _ in found] == [real_id]
