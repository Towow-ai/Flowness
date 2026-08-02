"""Unit tests for the M-0.4 envelope engine (task-facing assembly API).

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md §2 / §3.3 / §5 / §6.1 / §6.2
#
# Covers each engine public function + its boundaries per task-m04-envelope-engine done_criteria:
#   build_envelope_intent            — §6.1 EventIntent shape + subjects (affected + primary)
#   derive_write_set                 — §3.3 events ∪ git files, strongest change_type wins
#   derive_read_set                  — §3.3 capsule files + touched nodes + fallback, non-empty floor
#   check_claims_boundary            — §3.3 empty-claims skip / out-of-bounds detection
#   read_envelope_from_event         — §6.2 round-trip consistency / wrong-type + malformed reject
#   patches_to_domain_intent_specs   — §5 patch_type routing (git-only vs domain-event)
#   required-empty-array reject      — §2 read_set / write_set non-empty schema invariant
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from towow.l0.envelope.engine import (
    BoundaryViolation,
    EnvelopeEngineError,
    build_envelope_intent,
    check_claims_boundary,
    derive_read_set,
    derive_write_set,
    patches_to_domain_intent_specs,
    read_envelope_from_event,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EnvelopeClaimType,
    EnvelopeReadSetEntityType,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    EventCategory,
    EventType,
    PatchType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.envelope import (
    ClaimEntry,
    PatchEntry,
    ReadEntry,
    SelfCheck,
    TransactionEnvelope,
    WriteEntry,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance


def _envelope(
    *,
    write_set: list[WriteEntry] | None = None,
    read_set: list[ReadEntry] | None = None,
    claims: list[ClaimEntry] | None = None,
    patches: list[PatchEntry] | None = None,
) -> TransactionEnvelope:
    return TransactionEnvelope(
        envelope_id="env-test-0001",
        task_id="task-x",
        run_id="run-x",
        fork_id="fork-x",
        read_set=read_set
        or [ReadEntry(entity_type=EnvelopeReadSetEntityType.TASK, entity_id="task-x", version="evt-cap")],
        write_set=write_set
        or [
            WriteEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/a.py",
                change_type=EnvelopeWriteChangeType.MODIFIED,
            ),
        ],
        claims=claims or [],
        patches=patches or [],
        self_check=SelfCheck(passed=True),
        capsule_compiled_event_id="evt-cap",
        as_of_projection_seq=0,
        schema_version="1.0.0",
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _domain_intent(event_type: EventType, subjects: list[Subject]) -> EventIntent:
    return EventIntent(
        local_intent_id="local-x",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="sys"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _record_from_intent(intent: EventIntent) -> EventRecord:
    return EventRecord(
        event_id="evt-envrec001",
        sequence_number=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        record_hash="h",
        local_intent_id=intent.local_intent_id,
        event_type=intent.event_type,
        event_category=intent.event_category,
        payload=intent.payload,
        provenance=Provenance(**intent.provenance_hint.model_dump()),
        base_classification=intent.base_classification,
        supersede=intent.supersede,
        subjects=intent.subjects,
        schema_version=intent.schema_version,
    )


# ── (1) build_envelope_intent — §6.1 ──────────────────────────────────────────


def test_build_envelope_intent_shape_matches_spec_6_1() -> None:
    env = _envelope()
    intent = build_envelope_intent(env, parent_session_id="sess-parent", fork_name="execution")

    assert intent.local_intent_id == "env-test-0001"
    assert intent.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED
    assert intent.event_category is EventCategory.ENVELOPE
    assert intent.base_classification is BaseClassification.ABSTRACTABLE_PROCESS
    assert intent.supersede.is_supersede is False
    # payload carries the full envelope
    assert intent.payload["envelope_id"] == "env-test-0001"
    assert intent.payload["write_set"][0]["entity_id"] == "src/a.py"
    # provenance §6.1: agent_fork / actor_id=fork_id / task_id / run_id (+ boundary fields)
    prov = intent.provenance_hint
    assert prov.actor_type == ActorType.AGENT_FORK.value
    assert prov.actor_id == "fork-x"
    assert prov.task_id == "task-x"
    assert prov.run_id == "run-x"
    assert prov.parent_session_id == "sess-parent"
    assert prov.fork_name == "execution"


def test_build_envelope_intent_subjects_affected_plus_primary() -> None:
    env = _envelope(
        write_set=[
            WriteEntry(
                entity_type=EnvelopeWriteSetEntityType.CONCEPT,
                entity_id="c-1",
                change_type=EnvelopeWriteChangeType.CREATED,
            ),
        ],
    )
    intent = build_envelope_intent(env, parent_session_id="sess-p", fork_name="execution")
    subjects = {(s.entity_type, s.entity_id, s.role) for s in intent.subjects}
    assert (SubjectEntityType.CONCEPT, "c-1", SubjectRole.AFFECTED) in subjects
    assert (SubjectEntityType.TASK, "task-x", SubjectRole.PRIMARY) in subjects


# ── (2) derive_write_set — §3.3 ───────────────────────────────────────────────


def test_derive_write_set_unions_events_and_git_files() -> None:
    intent = _domain_intent(
        EventType.CONCEPT_CREATED,
        [Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-9", role=SubjectRole.PRIMARY)],
    )
    result = derive_write_set([intent], ["src/new.py", "  ", "src/two.py"])
    ids = {(w.entity_type, w.entity_id) for w in result}
    assert (EnvelopeWriteSetEntityType.CONCEPT, "c-9") in ids
    assert (EnvelopeWriteSetEntityType.FILE, "src/new.py") in ids
    assert (EnvelopeWriteSetEntityType.FILE, "src/two.py") in ids
    # blank path skipped
    assert (EnvelopeWriteSetEntityType.FILE, "") not in ids


def test_derive_write_set_git_only_and_empty() -> None:
    assert derive_write_set([], None) == []
    only_git = derive_write_set([], ["src/x.py"])
    assert len(only_git) == 1
    assert only_git[0].entity_type is EnvelopeWriteSetEntityType.FILE
    assert only_git[0].change_type is EnvelopeWriteChangeType.MODIFIED


def test_derive_write_set_strongest_change_type_wins() -> None:
    # Same (FILE, src/z.py) key surfaced by both a removed-type event and a git-modified path →
    # removed wins (strongest). The FILE-subject intent is a fixture to exercise the merge
    # precedence over one entity key (event name → REMOVED via _REMOVED_FRAGMENTS).
    intent = _domain_intent(
        EventType.OBLIGATION_RETIRED,
        [Subject(entity_type=SubjectEntityType.FILE, entity_id="src/z.py", role=SubjectRole.PRIMARY)],
    )
    result = derive_write_set(
        [intent], ["src/z.py"], git_change_type=EnvelopeWriteChangeType.MODIFIED,
    )
    zs = [w for w in result if w.entity_id == "src/z.py"]
    assert len(zs) == 1
    assert zs[0].change_type is EnvelopeWriteChangeType.REMOVED


# ── (3) derive_read_set — §3.3 ────────────────────────────────────────────────


def test_derive_read_set_combines_sources() -> None:
    result = derive_read_set(
        ["src/read.py"], ["c-touch"], ["c-nbr"], task_id="task-x", version="evt-cap",
    )
    files = {r.entity_id for r in result if r.entity_type is EnvelopeReadSetEntityType.FILE}
    concepts = {r.entity_id for r in result if r.entity_type is EnvelopeReadSetEntityType.CONCEPT}
    assert "src/read.py" in files
    assert "c-touch" in concepts
    assert "c-nbr" in concepts
    assert all(r.version == "evt-cap" for r in result)


def test_derive_read_set_nonempty_floor() -> None:
    # nothing supplied → §2 floor: the task package as a TASK read entry (never empty).
    result = derive_read_set(None, None, None, task_id="task-x", version="evt-cap")
    assert len(result) == 1
    assert result[0].entity_type is EnvelopeReadSetEntityType.TASK
    assert result[0].entity_id == "task-x"


# ── (4) check_claims_boundary — §3.3 / M-0.5 §3.6 ─────────────────────────────


def test_check_claims_boundary_absent_claims_skipped() -> None:
    # Planner declared no claims → skip (empty violations), NOT a violation.
    assert check_claims_boundary(_envelope(claims=[])) == []


def test_check_claims_boundary_compliant() -> None:
    env = _envelope(
        write_set=[
            WriteEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/a.py",
                change_type=EnvelopeWriteChangeType.MODIFIED,
            ),
        ],
        claims=[
            ClaimEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/a.py",
                claim_type=EnvelopeClaimType.WRITE_CLAIM,
            ),
            ClaimEntry(
                entity_type=EnvelopeWriteSetEntityType.TASK,
                entity_id="task-x",
                claim_type=EnvelopeClaimType.READ_CLAIM,
            ),
        ],
    )
    assert check_claims_boundary(env) == []


def test_check_claims_boundary_write_exceeds_detected() -> None:
    env = _envelope(
        write_set=[
            WriteEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/out_of_bounds.py",
                change_type=EnvelopeWriteChangeType.MODIFIED,
            ),
        ],
        read_set=[
            ReadEntry(entity_type=EnvelopeReadSetEntityType.TASK, entity_id="task-x", version="evt-cap"),
        ],
        claims=[
            # claims cover the read task + a DIFFERENT file, so the write is out of bounds.
            ClaimEntry(
                entity_type=EnvelopeWriteSetEntityType.TASK,
                entity_id="task-x",
                claim_type=EnvelopeClaimType.READ_CLAIM,
            ),
            ClaimEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/allowed.py",
                claim_type=EnvelopeClaimType.WRITE_CLAIM,
            ),
        ],
    )
    violations = check_claims_boundary(env)
    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, BoundaryViolation)
    assert v.entity_id == "src/out_of_bounds.py"
    assert v.violation_type == "write_set_exceeds_claims"


def test_check_claims_boundary_accepts_payload_dict() -> None:
    env = _envelope(claims=[])
    assert check_claims_boundary(env.model_dump(mode="json")) == []


# ── (5) read_envelope_from_event — §6.2 ───────────────────────────────────────


def test_read_envelope_round_trip_consistent() -> None:
    env = _envelope()
    intent = build_envelope_intent(env, parent_session_id="sess-p", fork_name="execution")
    record = _record_from_intent(intent)
    back = read_envelope_from_event(record)
    # build → submit → readback loses no declared field.
    assert back.envelope_id == env.envelope_id
    assert back.task_id == env.task_id
    assert back.write_set == env.write_set
    assert back.read_set == env.read_set


def test_read_envelope_wrong_event_type_rejected() -> None:
    intent = _domain_intent(
        EventType.CONCEPT_CREATED,
        [Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
    )
    record = _record_from_intent(intent)
    with pytest.raises(EnvelopeEngineError, match="not an envelope"):
        read_envelope_from_event(record)


def test_read_envelope_malformed_payload_rejected() -> None:
    # a TransactionEnvelopeSubmitted event whose payload is a boundary-less stub is not
    # a canonical envelope → EnvelopeEngineError, never a silently half-built object.
    stub = EventRecord(
        event_id="evt-stub00001",
        sequence_number=2,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        record_hash="h",
        local_intent_id="env-stub",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={"envelope_id": "env-stub", "capsule_compiled_event_id": "evt-x"},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="sys"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="task-x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    with pytest.raises(EnvelopeEngineError, match="not a canonical TransactionEnvelope"):
        read_envelope_from_event(stub)


# ── (6) patches_to_domain_intent_specs — §5 / §4.3 ────────────────────────────


def test_patches_routing_git_vs_domain_event() -> None:
    patches = [
        PatchEntry(
            patch_id="p-code",
            patch_type=PatchType.CODE_DIFF,
            target="src/a.py",
            summary="edit",
            detail_ref="git:abc123",
        ),
        PatchEntry(
            patch_id="p-concept",
            patch_type=PatchType.CONCEPT_EVENT,
            target="concept:c-1",
            summary="add concept + edge",
            detail_ref="evt:e1,evt:e2",
        ),
    ]
    specs = {s.patch_id: s for s in patches_to_domain_intent_specs(patches)}

    code = specs["p-code"]
    assert code.produces_domain_event is False
    assert code.git_refs == ["abc123"]
    assert code.domain_event_refs == []

    concept = specs["p-concept"]
    assert concept.produces_domain_event is True
    assert concept.domain_event_refs == ["e1", "e2"]
    assert concept.git_refs == []


def test_patches_routing_empty() -> None:
    assert patches_to_domain_intent_specs([]) == []


# ── required empty-array reject — §2 invariant ────────────────────────────────


def test_envelope_empty_write_set_rejected() -> None:
    # §2 invariant: write_set non-empty (a no-op run is TaskRunCompleted no_change, not an envelope).
    with pytest.raises(ValidationError):
        TransactionEnvelope(
            envelope_id="env-test-0001",
            task_id="task-x",
            run_id="run-x",
            fork_id="fork-x",
            read_set=[
                ReadEntry(entity_type=EnvelopeReadSetEntityType.TASK, entity_id="task-x", version="evt-cap"),
            ],
            write_set=[],
            self_check=SelfCheck(passed=True),
            capsule_compiled_event_id="evt-cap",
            as_of_projection_seq=0,
            schema_version="1.0.0",
            submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_envelope_empty_read_set_rejected() -> None:
    # §2 invariant: read_set non-empty (every run reads at least its task package).
    with pytest.raises(ValidationError):
        TransactionEnvelope(
            envelope_id="env-test-0001",
            task_id="task-x",
            run_id="run-x",
            fork_id="fork-x",
            read_set=[],
            write_set=[
                WriteEntry(
                    entity_type=EnvelopeWriteSetEntityType.FILE,
                    entity_id="src/a.py",
                    change_type=EnvelopeWriteChangeType.MODIFIED,
                ),
            ],
            self_check=SelfCheck(passed=True),
            capsule_compiled_event_id="evt-cap",
            as_of_projection_seq=0,
            schema_version="1.0.0",
            submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
