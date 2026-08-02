"""M-0.4 envelope builder — read_set / write_set / claims derivation (debt-payoff block 1).

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md §2 / §3.1 / §3.3
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from towow.l0.envelope.builder import (
    build_envelope,
    derive_claims,
    derive_read_set,
    derive_write_set,
)
from towow.schemas.enums import (
    BaseClassification,
    EnvelopeClaimType,
    EnvelopeReadSetEntityType,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
)
from towow.schemas.envelope import ReadEntry, SelfCheck, WriteEntry
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance


def _intent(
    event_type: EventType,
    *,
    subjects: list[Subject],
    is_supersede: bool = False,
) -> EventIntent:
    return EventIntent(
        local_intent_id=f"li-{event_type.value}-{subjects[0].entity_id}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={},
        provenance_hint=ProvenanceHint(actor_type="system", actor_id="sys-1"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=(
            Supersede(
                is_supersede=True,
                superseded_event_id="evt-old",
                novelty="superseded for test",
                novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
            )
            if is_supersede
            else Supersede(is_supersede=False)
        ),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _subj(entity_type: SubjectEntityType, entity_id: str, role: SubjectRole) -> Subject:
    return Subject(entity_type=entity_type, entity_id=entity_id, role=role)


def _claim_record(seq: int, event_type: EventType, entities: list[dict[str, str]]) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 5, 26, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id=None,
        batch_id=None,
        batch_position=None,
        record_hash=f"h-{seq}",
        local_intent_id=f"li-{seq}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"claimed_entities": entities},
        provenance=Provenance(actor_type="system", actor_id="planner", writer_id="m01-writer"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[_subj(SubjectEntityType.TASK, "t-1", SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


# ── derive_write_set ────────────────────────────────────────────────────────


def test_write_set_from_primary_subject_created() -> None:
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
    ]
    ws = derive_write_set(intents)
    assert ws == [
        WriteEntry(
            entity_type=EnvelopeWriteSetEntityType.CONCEPT,
            entity_id="c-1",
            change_type=EnvelopeWriteChangeType.CREATED,
        )
    ]


def test_write_set_supersede_is_modified() -> None:
    intents = [
        _intent(
            EventType.CONCEPT_CREATED,
            subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)],
            is_supersede=True,
        )
    ]
    ws = derive_write_set(intents)
    assert ws[0].change_type is EnvelopeWriteChangeType.MODIFIED


def test_write_set_skips_non_write_roles_and_unmappable_types() -> None:
    intents = [
        _intent(
            EventType.CONCEPT_CREATED,
            subjects=[
                _subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY),
                _subj(SubjectEntityType.CONCEPT, "c-evidence", SubjectRole.EVIDENCE),  # non-write role
                _subj(SubjectEntityType.FINDING, "f-1", SubjectRole.PRIMARY),  # no write-set equivalent
            ],
        )
    ]
    ws = derive_write_set(intents)
    assert [(e.entity_type, e.entity_id) for e in ws] == [(EnvelopeWriteSetEntityType.CONCEPT, "c-1")]


def test_write_set_dedup_strongest_change_type_wins() -> None:
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
        _intent(
            EventType.CONCEPT_CREATED,
            subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)],
            is_supersede=True,  # modified > created
        ),
    ]
    ws = derive_write_set(intents)
    assert len(ws) == 1
    assert ws[0].change_type is EnvelopeWriteChangeType.MODIFIED


# ── derive_read_set ─────────────────────────────────────────────────────────


def test_read_set_floor_is_task_package_when_empty() -> None:
    rs = derive_read_set(task_id="t-1", capsule_compiled_event_id="evt-cap")
    assert rs == [ReadEntry(entity_type=EnvelopeReadSetEntityType.TASK, entity_id="t-1", version="evt-cap")]


def test_read_set_includes_capsule_files() -> None:
    rs = derive_read_set(
        task_id="t-1",
        capsule_compiled_event_id="evt-cap",
        capsule_files=["src/a.py", "src/b.py"],
    )
    assert [e.entity_id for e in rs] == ["src/a.py", "src/b.py"]
    assert all(e.entity_type is EnvelopeReadSetEntityType.FILE for e in rs)


def test_read_set_dedup_declared_and_capsule() -> None:
    declared = [ReadEntry(entity_type=EnvelopeReadSetEntityType.FILE, entity_id="src/a.py", version="v1")]
    rs = derive_read_set(
        task_id="t-1",
        capsule_compiled_event_id="evt-cap",
        capsule_files=["src/a.py"],
        declared_read_set=declared,
    )
    assert len(rs) == 1  # src/a.py appears once


# ── read_set #3 neighborhood upper bound (Cap5, M-0.4 §3.3 #3) ───────────────


def test_read_set_includes_neighborhood_concepts_as_upper_bound() -> None:
    """M-0.4 §3.3 #3: when no real read trace exists, the capsule's projection neighborhood is the
    read_set UPPER BOUND — each neighborhood concept becomes a CONCEPT read entry versioned by the
    capsule it came from (the agent had access to exactly this slice)."""
    rs = derive_read_set(
        task_id="t-1",
        capsule_compiled_event_id="evt-cap",
        capsule_files=["src/a.py"],
        neighborhood_concept_ids=["c-beta@v1", "c-alpha@v1"],
    )
    files = [e.entity_id for e in rs if e.entity_type is EnvelopeReadSetEntityType.FILE]
    concepts = [(e.entity_id, e.version) for e in rs if e.entity_type is EnvelopeReadSetEntityType.CONCEPT]
    assert files == ["src/a.py"]
    # Neighborhood concepts present, versioned by the capsule (their access provenance).
    assert set(concepts) == {("c-beta@v1", "evt-cap"), ("c-alpha@v1", "evt-cap")}


def test_read_set_neighborhood_dedup_with_declared_concept() -> None:
    """A neighborhood concept already declared (e.g. agent named it) is not duplicated."""
    declared = [ReadEntry(entity_type=EnvelopeReadSetEntityType.CONCEPT, entity_id="c-beta@v1", version="v9")]
    rs = derive_read_set(
        task_id="t-1",
        capsule_compiled_event_id="evt-cap",
        declared_read_set=declared,
        neighborhood_concept_ids=["c-beta@v1"],
    )
    beta = [e for e in rs if e.entity_id == "c-beta@v1"]
    assert len(beta) == 1
    assert beta[0].version == "v9"  # declared entry kept (first wins)


def test_read_set_neighborhood_does_not_trigger_floor() -> None:
    """If only a neighborhood is present (no files / declared), read_set is the neighborhood — the
    task-package floor only fires when read_set would otherwise be empty (§2 non-empty invariant)."""
    rs = derive_read_set(
        task_id="t-1",
        capsule_compiled_event_id="evt-cap",
        neighborhood_concept_ids=["c-x@v1"],
    )
    assert [e.entity_id for e in rs] == ["c-x@v1"]
    assert all(e.entity_type is EnvelopeReadSetEntityType.CONCEPT for e in rs)


# ── derive_claims ───────────────────────────────────────────────────────────


def test_claims_empty_when_no_events() -> None:
    assert derive_claims(None) == []
    assert derive_claims([]) == []


def test_claims_from_read_and_write_claim_events() -> None:
    events = [
        _claim_record(1, EventType.TASK_READ_SET_CLAIMED, [{"entity_type": "concept", "entity_id": "c-1"}]),
        _claim_record(2, EventType.TASK_WRITE_SET_CLAIMED, [{"entity_type": "task", "entity_id": "t-9"}]),
    ]
    claims = derive_claims(events)
    by_type = {(c.entity_type.value, c.entity_id): c.claim_type for c in claims}
    assert by_type[("concept", "c-1")] is EnvelopeClaimType.READ_CLAIM
    assert by_type[("task", "t-9")] is EnvelopeClaimType.WRITE_CLAIM


# ── build_envelope end-to-end ───────────────────────────────────────────────


def test_build_envelope_produces_schema_valid_with_derived_boundaries() -> None:
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
        _intent(EventType.TASK_NODE_CREATED, subjects=[_subj(SubjectEntityType.TASK, "t-2", SubjectRole.PRIMARY)]),
    ]
    env = build_envelope(
        envelope_id="env-abc123",
        task_id="t-1",
        run_id="r-1",
        fork_id="fork-1",
        domain_intents=intents,
        capsule_compiled_event_id="evt-cap",
        as_of_projection_seq=42,
        self_check=SelfCheck(passed=True, checks_run=[]),
    )
    assert env.envelope_id == "env-abc123"
    assert {(e.entity_type.value, e.entity_id) for e in env.write_set} == {("concept", "c-1"), ("task", "t-2")}
    assert env.read_set  # non-empty floor
    assert env.claims == []
    assert env.as_of_projection_seq == 42


# ── derive_capsule_compiled_event_id (Cap1, M-0.4 §2/§3.1 capsule-injected) ──


def _capsule_compiled_record(
    seq: int,
    *,
    target_fork_id: str,
    correlation_id: str = "cap-corr-x",
    task_id: str | None = None,
    as_of_projection_seq: int = 0,
) -> EventRecord:
    """A real-shaped CapsuleCompiled record (path B; provenance carries correlation_id/task_id)."""
    return EventRecord(
        event_id=f"evt-cap-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 5, 30, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id=None,
        batch_id=None,
        batch_position=None,
        record_hash=f"h-cap-{seq}",
        local_intent_id=f"cap-{seq}",
        event_type=EventType.CAPSULE_COMPILED,
        event_category=EventCategory.CAPSULE,
        payload={
            "input_hash": "sha256:abc",
            "scene_type": "execution",
            "target_fork_id": target_fork_id,
            "as_of_projection_seq": as_of_projection_seq,
            "capsule_sections_summary": {
                "obligation_count": 0,
                "red_line_count": 0,
                "known_facts_count": 0,
                "files_to_read_count": 0,
            },
        },
        provenance=Provenance(
            actor_type="capsule_assembler",
            actor_id="m03-capsule-assembler",
            task_id=task_id,
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[_subj(SubjectEntityType.CAPSULE, correlation_id, SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_capsule_compiled_event_id_derived_from_run_fork() -> None:
    """M-0.4 §3.1: capsule_compiled_event_id is auto-associated from the run's real
    CapsuleCompiled event (matched by target_fork_id) — not agent-supplied.
    """
    from towow.l0.envelope.builder import derive_capsule_compiled_event_id

    records = [
        _capsule_compiled_record(10, target_fork_id="fork-other"),
        _capsule_compiled_record(11, target_fork_id="fork-mine"),
    ]
    assert derive_capsule_compiled_event_id(records, fork_id="fork-mine") == "evt-cap-11"


def test_capsule_compiled_event_id_picks_latest_for_fork_on_reentry() -> None:
    """Re-entry (M-0.3 §2.9) re-runs the pipeline → a fresh CapsuleCompiled for the same fork.
    The envelope must associate the LATEST one (highest seq), the capsule the run actually used.
    """
    from towow.l0.envelope.builder import derive_capsule_compiled_event_id

    records = [
        _capsule_compiled_record(20, target_fork_id="fork-r"),
        _capsule_compiled_record(35, target_fork_id="fork-r"),  # re-entry capsule
    ]
    assert derive_capsule_compiled_event_id(records, fork_id="fork-r") == "evt-cap-35"


def test_capsule_compiled_event_id_none_when_no_capsule_for_fork() -> None:
    """No CapsuleCompiled for this fork → derivation yields None (caller keeps the floor token)."""
    from towow.l0.envelope.builder import derive_capsule_compiled_event_id

    records = [_capsule_compiled_record(10, target_fork_id="fork-other")]
    assert derive_capsule_compiled_event_id(records, fork_id="fork-mine") is None


def test_canonicalize_overrides_agent_forged_capsule_compiled_event_id() -> None:
    """Agent-supplied capsule_compiled_event_id is OVERRIDDEN by the system-derived one
    (M-0.4 §3.1 capsule-injected — agent 不能伪造). Negative-control: a forged id loses.
    """
    from towow.l0.envelope.builder import canonicalize_envelope_payload

    records = [_capsule_compiled_record(11, target_fork_id="fork-mine")]
    raw = {
        "envelope_id": "env-forge",
        "task_id": "t-1",
        "fork_id": "fork-mine",
        "capsule_compiled_event_id": "evt-FORGED-by-agent",
        "self_check": {"passed": True, "checks_run": ["x"]},
    }
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
    ]
    out = canonicalize_envelope_payload(
        raw,
        intents,
        fallback_session_ref="sess-1",
        prov_fork_id="fork-mine",
        committed_records=records,
    )
    assert out["capsule_compiled_event_id"] == "evt-cap-11"  # system-derived wins


def test_canonicalize_keeps_agent_capsule_id_when_no_records_provided() -> None:
    """Backward compat: callers that do not pass committed_records (legacy / bootstrap) keep the
    agent/raw capsule_compiled_event_id — derivation is opt-in, no silent behavior change.
    """
    from towow.l0.envelope.builder import canonicalize_envelope_payload

    raw = {
        "envelope_id": "env-legacy",
        "task_id": "t-1",
        "fork_id": "fork-mine",
        "capsule_compiled_event_id": "evt-agent-supplied",
        "self_check": {"passed": True, "checks_run": ["x"]},
    }
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
    ]
    out = canonicalize_envelope_payload(raw, intents, fallback_session_ref="sess-1", prov_fork_id="fork-mine")
    assert out["capsule_compiled_event_id"] == "evt-agent-supplied"


# ── derive_resolver_decision_event_id (Cap2, M-0.4 §2/§3.1) ──────────────────


def _resolver_decision_record(
    seq: int,
    *,
    correlation_id: str,
    resolver_called: bool,
) -> EventRecord:
    """A real-shaped ResolverDecisionMade record (path B; same correlation_id as its capsule)."""
    return EventRecord(
        event_id=f"evt-rdm-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 5, 30, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id=None,
        batch_id=None,
        batch_position=None,
        record_hash=f"h-rdm-{seq}",
        local_intent_id=f"rdm-{seq}",
        event_type=EventType.RESOLVER_DECISION_MADE,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "judgment_type": "resolver_decision",
            "after_state": {
                "resolver_id": "obligation_activation_resolver",
                "pipeline_stages": {"stage_4_semantic_judgment": {"resolver_called": resolver_called}},
                "final_active_set": [],
            },
        },
        provenance=Provenance(
            actor_type="capsule_assembler",
            actor_id="m03-capsule-assembler",
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[_subj(SubjectEntityType.CAPSULE, correlation_id, SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_resolver_decision_event_id_derived_when_resolver_called() -> None:
    """M-0.4 §2/§3.1: when the run's resolver was invoked, resolver_decision_event_id is
    auto-associated to the ResolverDecisionMade event sharing the capsule's correlation_id.
    """
    from towow.l0.envelope.builder import derive_resolver_decision_event_id

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", correlation_id="corr-A")
    rdm = _resolver_decision_record(10, correlation_id="corr-A", resolver_called=True)
    records = [rdm, cap]
    assert (
        derive_resolver_decision_event_id(records, capsule_compiled_event_id="evt-cap-11")
        == "evt-rdm-10"
    )


def test_resolver_decision_event_id_none_when_resolver_not_called() -> None:
    """§2: resolver_decision_event_id is OPTIONAL — only set "如果 resolver 被调用". A
    ResolverDecisionMade留痕 with resolver_called=false (mechanical-only run) → no association.
    """
    from towow.l0.envelope.builder import derive_resolver_decision_event_id

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", correlation_id="corr-A")
    rdm = _resolver_decision_record(10, correlation_id="corr-A", resolver_called=False)
    assert (
        derive_resolver_decision_event_id([rdm, cap], capsule_compiled_event_id="evt-cap-11")
        is None
    )


def test_resolver_decision_event_id_matches_only_same_correlation() -> None:
    """The associated resolver decision must belong to THIS capsule (same correlation_id), not a
    different run's resolver decision (negative control against cross-run leakage)."""
    from towow.l0.envelope.builder import derive_resolver_decision_event_id

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", correlation_id="corr-MINE")
    other_rdm = _resolver_decision_record(10, correlation_id="corr-OTHER", resolver_called=True)
    assert (
        derive_resolver_decision_event_id([other_rdm, cap], capsule_compiled_event_id="evt-cap-11")
        is None
    )


def test_resolver_decision_event_id_none_when_no_capsule() -> None:
    """No capsule resolved (capsule_compiled_event_id absent/unknown) → no resolver association."""
    from towow.l0.envelope.builder import derive_resolver_decision_event_id

    rdm = _resolver_decision_record(10, correlation_id="corr-A", resolver_called=True)
    assert derive_resolver_decision_event_id([rdm], capsule_compiled_event_id="evt-missing") is None
    assert derive_resolver_decision_event_id([rdm], capsule_compiled_event_id=None) is None


# ── derive_as_of_projection_seq (Cap3, M-0.4 §2 = capsule bundle.as_of_seq) ──


def test_as_of_projection_seq_derived_from_capsule() -> None:
    """M-0.4 §2: as_of_projection_seq is taken from the run's CapsuleCompiled (= bundle.as_of_seq,
    M-0.3 §9.2) — the WriteConflict / FreshnessDrift baseline. Not agent-supplied.
    """
    from towow.l0.envelope.builder import derive_as_of_projection_seq

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", as_of_projection_seq=77)
    assert derive_as_of_projection_seq([cap], capsule_compiled_event_id="evt-cap-11") == 77


def test_as_of_projection_seq_none_when_no_capsule() -> None:
    """No resolved capsule (None / unknown id) → no baseline derivable (caller keeps its own)."""
    from towow.l0.envelope.builder import derive_as_of_projection_seq

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", as_of_projection_seq=77)
    assert derive_as_of_projection_seq([cap], capsule_compiled_event_id="evt-other") is None
    assert derive_as_of_projection_seq([cap], capsule_compiled_event_id=None) is None


def test_as_of_projection_seq_none_when_capsule_has_no_baseline() -> None:
    """A CapsuleCompiled留痕 with as_of_projection_seq=0 (no baseline, e.g. legacy emit) → None,
    so the canonicalizer does not clobber a caller-supplied baseline with a meaningless 0.
    """
    from towow.l0.envelope.builder import derive_as_of_projection_seq

    cap = _capsule_compiled_record(11, target_fork_id="fork-mine", as_of_projection_seq=0)
    assert derive_as_of_projection_seq([cap], capsule_compiled_event_id="evt-cap-11") is None


def test_canonicalize_uses_capsule_as_of_seq_over_agent_value() -> None:
    """The stored envelope's as_of_projection_seq is the capsule's baseline, overriding the agent's
    raw value (M-0.4 §2 capsule-injected baseline)."""
    from towow.l0.envelope.builder import canonicalize_envelope_payload

    records = [_capsule_compiled_record(11, target_fork_id="fork-mine", as_of_projection_seq=99)]
    raw = {
        "envelope_id": "env-a",
        "task_id": "t-1",
        "fork_id": "fork-mine",
        "capsule_compiled_event_id": "evt-FORGED",
        "as_of_projection_seq": 3,  # agent-claimed baseline
        "self_check": {"passed": True, "checks_run": ["x"]},
    }
    intents = [
        _intent(EventType.CONCEPT_CREATED, subjects=[_subj(SubjectEntityType.CONCEPT, "c-1", SubjectRole.PRIMARY)]),
    ]
    out = canonicalize_envelope_payload(
        raw, intents, fallback_session_ref="s", prov_fork_id="fork-mine", committed_records=records,
    )
    assert out["as_of_projection_seq"] == 99  # capsule baseline wins
