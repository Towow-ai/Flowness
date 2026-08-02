"""Unit tests for EventIntent / Supersede / Subject / ProvenanceHint."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    NoveltyType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede


def test_provenance_hint_minimal() -> None:
    """non-bounded actor_type (SYSTEM) — no extra fields required."""
    p = ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="system-1")
    assert p.actor_type == ActorType.SYSTEM.value
    assert p.actor_id == "system-1"
    assert p.session_id is None


def test_provenance_hint_agent_fork_requires_parent_session_id_and_fork_name() -> None:
    """E.1.1 Conflict 2: agent_fork must include parent_session_id + fork_name."""
    with pytest.raises(ValidationError, match="agent_fork requires non-None"):
        ProvenanceHint(actor_type=ActorType.AGENT_FORK.value, actor_id="fork-1")


def test_provenance_hint_agent_fork_complete() -> None:
    """E.1.1 Conflict 2: agent_fork with full boundary fields validates."""
    p = ProvenanceHint(
        actor_type=ActorType.AGENT_FORK.value,
        actor_id="fork-1",
        parent_session_id="sess-1",
        fork_name="reviewer-fork",
    )
    assert p.parent_session_id == "sess-1"
    assert p.fork_name == "reviewer-fork"


def test_provenance_hint_daemon_requires_daemon_name() -> None:
    """E.1.1 Conflict 2: daemon must include daemon_name."""
    with pytest.raises(ValidationError, match="daemon requires daemon_name"):
        ProvenanceHint(actor_type=ActorType.DAEMON.value, actor_id="m21-1")


def test_provenance_hint_daemon_complete() -> None:
    p = ProvenanceHint(
        actor_type=ActorType.DAEMON.value,
        actor_id="m21-cascade-1",
        daemon_name="m21.cascade_daemon",
    )
    assert p.daemon_name == "m21.cascade_daemon"


def test_provenance_hint_agent_session_requires_session_id_and_skill_id() -> None:
    """E.1.1 Conflict 2: agent_session must include session_id + skill_id."""
    with pytest.raises(ValidationError, match="agent_session requires non-None"):
        ProvenanceHint(actor_type=ActorType.AGENT_SESSION.value, actor_id="orch-1")


def test_provenance_hint_agent_session_complete() -> None:
    p = ProvenanceHint(
        actor_type=ActorType.AGENT_SESSION.value,
        actor_id="orch-1",
        session_id="sess-top-1",
        skill_id="M-1.6",
    )
    assert p.session_id == "sess-top-1"
    assert p.skill_id == "M-1.6"


def test_provenance_hint_actor_id_min_length() -> None:
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="")


def test_provenance_hint_frozen() -> None:
    p = ProvenanceHint(actor_type=ActorType.NATURE.value, actor_id="nature-1")
    with pytest.raises(ValidationError, match="frozen"):
        p.actor_id = "other"


def test_supersede_false_must_have_no_other_fields() -> None:
    s = Supersede(is_supersede=False)
    assert s.superseded_event_id is None
    assert s.novelty is None
    assert s.novelty_type is None


def test_supersede_false_rejects_other_fields_set() -> None:
    with pytest.raises(ValidationError, match="is_supersede=false forbids"):
        Supersede(is_supersede=False, superseded_event_id="evt-abc", novelty=None, novelty_type=None)


def test_supersede_true_requires_all_other_fields() -> None:
    with pytest.raises(ValidationError, match="is_supersede=true requires non-None"):
        Supersede(is_supersede=True)


def test_supersede_true_partial_missing() -> None:
    with pytest.raises(ValidationError, match="novelty_type"):
        Supersede(
            is_supersede=True,
            superseded_event_id="evt-old",
            novelty="new evidence emerged",
            novelty_type=None,
        )


def test_supersede_true_complete() -> None:
    s = Supersede(
        is_supersede=True,
        superseded_event_id="evt-old",
        novelty="new evidence emerged",
        novelty_type=NoveltyType.NEW_EVIDENCE,
    )
    assert s.is_supersede is True
    assert s.novelty_type is NoveltyType.NEW_EVIDENCE


def test_subject_minimal() -> None:
    s = Subject(
        entity_type=SubjectEntityType.OBLIGATION,
        entity_id="obl-intent-attribution-abc12345",
        role=SubjectRole.PRIMARY,
    )
    assert s.entity_type is SubjectEntityType.OBLIGATION
    assert s.role is SubjectRole.PRIMARY


def test_subject_entity_id_min_length() -> None:
    with pytest.raises(ValidationError):
        Subject(entity_type=SubjectEntityType.TASK, entity_id="", role=SubjectRole.PRIMARY)


def test_event_intent_minimal_valid(event_intent_envelope: EventIntent) -> None:
    """Fixture-built envelope EventIntent is valid."""
    assert event_intent_envelope.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED
    assert event_intent_envelope.event_category is EventCategory.ENVELOPE
    assert event_intent_envelope.supersede.is_supersede is False
    assert len(event_intent_envelope.subjects) == 1


def test_event_intent_requires_at_least_one_subject(
    provenance_hint: ProvenanceHint,
    supersede_no: Supersede,
) -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        EventIntent(
            local_intent_id="i-1",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={},
            provenance_hint=provenance_hint,
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=supersede_no,
            subjects=[],
            schema_version="1.0.0",
        )


def test_event_intent_rejects_extra_field(
    provenance_hint: ProvenanceHint,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> None:
    with pytest.raises(ValidationError, match="extra"):
        EventIntent(
            local_intent_id="i-1",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={},
            provenance_hint=provenance_hint,
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            schema_version="1.0.0",
            event_id="should-not-be-here",  # type: ignore[call-arg]
        )


def test_event_intent_schema_version_pattern(
    provenance_hint: ProvenanceHint,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> None:
    """schema_version 必须匹配 semver pattern '^\\d+\\.\\d+\\.\\d+$'."""
    with pytest.raises(ValidationError, match="String should match pattern"):
        EventIntent(
            local_intent_id="i-1",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={},
            provenance_hint=provenance_hint,
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            schema_version="version-one",
        )


def test_event_intent_strict_no_coercion(
    provenance_hint: ProvenanceHint,
    supersede_no: Supersede,
    subject_primary_task: Subject,
) -> None:
    """pydantic strict mode: int → str coercion not allowed for local_intent_id."""
    with pytest.raises(ValidationError):
        EventIntent(
            local_intent_id=123,  # type: ignore[arg-type]
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={},
            provenance_hint=provenance_hint,
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=supersede_no,
            subjects=[subject_primary_task],
            schema_version="1.0.0",
        )


def test_event_intent_json_round_trip(event_intent_envelope: EventIntent) -> None:
    raw = event_intent_envelope.model_dump_json()
    rebuilt = EventIntent.model_validate_json(raw)
    assert rebuilt == event_intent_envelope


@pytest.mark.invariant
def test_event_intent_frozen(event_intent_envelope: EventIntent) -> None:
    """Event sourcing invariant: EventIntent is immutable once constructed."""
    with pytest.raises(ValidationError, match="frozen"):
        event_intent_envelope.local_intent_id = "mutated"
