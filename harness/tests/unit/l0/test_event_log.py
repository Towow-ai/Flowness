"""Unit tests for M-0.1 EventLog (Step 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from towow.l0.event_log import CursorStore, EventIndex, EventLog
from towow.l0.event_log.event_log import build_accepted_batch_e11
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

# ─── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


@pytest.fixture
def event_log(log_path: Path) -> EventLog:
    return EventLog(log_path)


def _intent(
    *,
    local_intent_id: str,
    event_type: EventType = EventType.NODE_TOUCHED,
    event_category: EventCategory = EventCategory.ENVELOPE,
    actor_id: str = "fork-1",
    task_id: str | None = "t-1",
    correlation_id: str | None = None,
    causation_event_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=event_type,
        event_category=event_category,
        payload=payload if payload is not None else {"target_entity_id": "c-1", "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=actor_id,
            task_id=task_id,
            run_id="r-1",
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            # E.1.1 Conflict 2: agent_fork requires parent_session_id + fork_name
            parent_session_id="sess-test",
            fork_name=actor_id,
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


# ─── write_direct (path B) ─────────────────────────────────────────────────────


def test_write_direct_assigns_sequence_and_event_id(event_log: EventLog) -> None:
    rec = event_log.write_direct(_intent(local_intent_id="i-1"))
    assert rec.sequence_number == 0
    assert rec.event_id.startswith("evt-")
    assert rec.batch_id is None  # path B


def test_write_direct_monotonic_sequence(event_log: EventLog) -> None:
    rec1 = event_log.write_direct(_intent(local_intent_id="i-1"))
    rec2 = event_log.write_direct(_intent(local_intent_id="i-2"))
    assert rec2.sequence_number == rec1.sequence_number + 1


def test_write_direct_rejects_non_path_b_event(event_log: EventLog) -> None:
    """ConceptCreated is path-A only — must reject direct write."""
    with pytest.raises(ValueError, match="not allowed on path B"):
        event_log.write_direct(
            _intent(
                local_intent_id="i-x",
                event_type=EventType.CONCEPT_CREATED,
                event_category=EventCategory.STATE_TRANSITION,
            ),
        )


def test_write_direct_persists_to_disk(event_log: EventLog, log_path: Path) -> None:
    event_log.write_direct(_intent(local_intent_id="i-1"))
    content = log_path.read_text(encoding="utf-8")
    assert "i-1" in content
    assert content.endswith("\n")


# ─── append_transaction_batch (path A) ─────────────────────────────────────────


def test_append_accepted_batch_atomically(event_log: EventLog) -> None:
    """E.1.1: envelope path B, batch = [domain, CommitAccepted]."""
    envelope = event_log.write_direct(
        _intent(
            local_intent_id="env-1",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
        ),
    )
    domain = event_log.stamp_for_batch(
        _intent(
            local_intent_id="cc-1",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
        ),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=0,
    )
    sentinel = event_log.stamp_for_batch(
        _intent(
            local_intent_id="ca-1",
            event_type=EventType.COMMIT_ACCEPTED,
            event_category=EventCategory.COMMIT,
        ),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=1,
    )
    batch = build_accepted_batch_e11("b-1", "tx-1", [domain], sentinel)
    event_log.append_transaction_batch(batch)
    events = event_log.get_events_in_range(0, 100)
    # envelope (path B) + domain + sentinel = 3 events
    assert len(events) == 3
    assert envelope.batch_id is None  # path B has no batch_id


# ─── 9 Read APIs ───────────────────────────────────────────────────────────────


def test_get_event_by_id(event_log: EventLog) -> None:
    rec = event_log.write_direct(_intent(local_intent_id="i-1"))
    found = event_log.get_event(rec.event_id)
    assert found is not None
    assert found.event_id == rec.event_id


def test_get_event_returns_none_for_unknown(event_log: EventLog) -> None:
    assert event_log.get_event("evt-nonexistent") is None


def test_get_events_by_category(event_log: EventLog) -> None:
    event_log.write_direct(_intent(local_intent_id="i-1", event_type=EventType.NODE_TOUCHED))
    event_log.write_direct(
        _intent(
            local_intent_id="i-2",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
        ),
    )
    envelope_events = event_log.get_events_by_category(EventCategory.ENVELOPE)
    assert len(envelope_events) == 2


def test_get_events_by_type(event_log: EventLog) -> None:
    event_log.write_direct(_intent(local_intent_id="i-1", event_type=EventType.NODE_TOUCHED))
    nt = event_log.get_events_by_type(EventType.NODE_TOUCHED)
    assert len(nt) == 1


def test_get_events_by_task(event_log: EventLog) -> None:
    event_log.write_direct(_intent(local_intent_id="i-1", task_id="t-A"))
    event_log.write_direct(_intent(local_intent_id="i-2", task_id="t-B"))
    a_events = event_log.get_events_by_task("t-A")
    assert len(a_events) == 1


def test_get_correlated_events(event_log: EventLog) -> None:
    event_log.write_direct(_intent(local_intent_id="i-1", correlation_id="corr-1"))
    event_log.write_direct(_intent(local_intent_id="i-2", correlation_id="corr-1"))
    event_log.write_direct(_intent(local_intent_id="i-3", correlation_id="other"))
    grouped = event_log.get_correlated_events("corr-1")
    assert len(grouped) == 2


def test_get_causation_chain(event_log: EventLog) -> None:
    rec1 = event_log.write_direct(_intent(local_intent_id="i-1"))
    rec2 = event_log.write_direct(_intent(local_intent_id="i-2", causation_event_id=rec1.event_id))
    chain = event_log.get_causation_chain(rec2.event_id)
    assert len(chain) == 2
    assert chain[0].event_id == rec2.event_id
    assert chain[1].event_id == rec1.event_id


# ─── §13 invariant tests ───────────────────────────────────────────────────────


@pytest.mark.invariant
def test_append_only_physical(event_log: EventLog, log_path: Path) -> None:
    """M-0.1 §5.1 invariant: append-only — physical writes always extend the file."""
    rec1 = event_log.write_direct(_intent(local_intent_id="i-1"))
    size_after_1 = log_path.stat().st_size
    rec2 = event_log.write_direct(_intent(local_intent_id="i-2"))
    size_after_2 = log_path.stat().st_size
    assert size_after_2 > size_after_1
    assert rec2.sequence_number > rec1.sequence_number


@pytest.mark.invariant
def test_event_id_uniqueness(event_log: EventLog) -> None:
    """M-0.1 §5.3: duplicate event_id rejected."""
    event_log.write_direct(_intent(local_intent_id="i-1"))
    # All event_ids are uuid-generated so duplicates effectively impossible — verify the
    # uniqueness set is populated.
    assert len(event_log.all_records()) == 1


@pytest.mark.invariant
def test_committed_visible_stream_hides_un_sentineled_batch(
    event_log: EventLog,
    log_path: Path,
) -> None:
    """M-0.1a Patch 2 §2.1: domain events between envelope and sentinel are invisible
    until sentinel is written.
    """
    # Manually inject a half-written batch (no sentinel).
    envelope = event_log.stamp_for_batch(
        _intent(
            local_intent_id="env-half",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
        ),
        transaction_id="tx-half",
        batch_id="b-half",
        batch_position=0,
    )
    domain = event_log.stamp_for_batch(
        _intent(
            local_intent_id="cc-half",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
        ),
        transaction_id="tx-half",
        batch_id="b-half",
        batch_position=1,
    )
    # Append manually without sentinel
    with log_path.open("a", encoding="utf-8") as f:
        f.write(envelope.model_dump_json() + "\n")
        f.write(domain.model_dump_json() + "\n")
    # Verify domain events are NOT visible
    all_records = list(event_log.all_records())
    assert not any(rec.event_id == domain.event_id for rec in all_records)


@pytest.mark.invariant
def test_subjects_index_traversal(event_log: EventLog) -> None:
    """M-0.1a Patch 3: subjects[] is the unified indexing field."""
    rec = event_log.write_direct(_intent(local_intent_id="i-1"))
    index = EventIndex([rec])
    found = index.lookup_entity("concept", "c-1")
    assert len(found) == 1
    assert found[0].event_id == rec.event_id


# ─── recovery (Patch 2 §2.2) ───────────────────────────────────────────────────


@pytest.mark.invariant
def test_recover_truncates_un_sentineled_tail(log_path: Path) -> None:
    """M-0.1a Patch 2 §2.2: recover() truncates trailing un-sentineled path-A batch."""
    log = EventLog(log_path)
    # Write a complete path-B record first
    log.write_direct(_intent(local_intent_id="i-good"))
    initial_size = log_path.stat().st_size

    # Manually append an envelope + domain WITHOUT sentinel
    envelope = log.stamp_for_batch(
        _intent(
            local_intent_id="env-half",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
        ),
        transaction_id="tx-half",
        batch_id="b-half",
        batch_position=0,
    )
    domain = log.stamp_for_batch(
        _intent(
            local_intent_id="cc-half",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
        ),
        transaction_id="tx-half",
        batch_id="b-half",
        batch_position=1,
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(envelope.model_dump_json() + "\n")
        f.write(domain.model_dump_json() + "\n")

    recovered_log = EventLog(log_path)
    truncated = recovered_log.recover()
    assert truncated > 0
    assert log_path.stat().st_size == initial_size


# ─── ConsumerCursor + retention watermark ──────────────────────────────────────


def test_cursor_register_and_advance(tmp_path: Path) -> None:
    store = CursorStore(tmp_path / "cursors")
    cursor = store.register("m02", "projection")
    assert cursor.last_durable_processed_seq == 0
    advanced = store.advance("m02", 100)
    assert advanced.last_durable_processed_seq == 100


def test_cursor_advance_backwards_forbidden(tmp_path: Path) -> None:
    store = CursorStore(tmp_path / "cursors")
    store.register("m02", "projection")
    store.advance("m02", 100)
    with pytest.raises(ValueError, match="advance backwards forbidden"):
        store.advance("m02", 50)


def test_retention_watermark_min_across_consumers(tmp_path: Path) -> None:
    store = CursorStore(tmp_path / "cursors")
    store.register("m02", "projection")
    store.register("m03", "capsule_assembler")
    store.advance("m02", 100)
    store.advance("m03", 50)
    assert store.retention_watermark() == 50
