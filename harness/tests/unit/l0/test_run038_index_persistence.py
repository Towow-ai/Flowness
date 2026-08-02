"""RUN-038 L0增强 M-0.1: persistent subjects[]-derived index + point-lookup fast path.

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   §7.1 文件布局 — .towow/events/index/*.idx (event_id / type / task / entity / ...)
#   附录 B Patch 3 §3.1-§3.2 — subjects[] unified indexing field + 15 indexes,
#     "全部可重建 (从 event 文件扫描重建) —— 索引不是事实源, event 文件才是"
#   §4.1.1 get_event point lookup; §4.1.8 get_event_by_input_hash
#   §4.2 SLO — 点查 <10ms / input_hash <20ms (索引查询, 非全扫)

Capabilities proven here:
  - Cap1: 15 indexes built from subjects[]; persisted to .idx; rebuildable from events
    consistently; deleting .idx then rebuilding yields the same lookups.
  - Cap2: get_event / get_event_by_input_hash / get_events_by_entity go through the
    O(1) index fast path, NOT an O(n) full scan (structural proof: the index lookup is
    consulted and a full scan over all records is not performed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog, EventLogIndexStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


@pytest.fixture
def event_log(log_path: Path) -> EventLog:
    return EventLog(log_path)


def _node_touched(
    *,
    local_intent_id: str,
    entity_id: str = "c-1",
    task_id: str | None = "t-1",
) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id=task_id,
            run_id="r-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _resolver_decision(*, local_intent_id: str, resolver_id: str, input_hash: str) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.RESOLVER_DECISION_MADE,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "judgment_type": "resolver_decision",
            "after_state": {
                "resolver_id": resolver_id,
                "task_id": "t-1",
                "input_hash": input_hash,
                "pipeline_stages": {
                    "stage_2_candidate_retrieval": {},
                    "stage_3_mechanical_filtering": {},
                    "stage_4_semantic_judgment": {},
                    "stage_6_placement": [],
                },
                "final_active_set": [],
                "uncertainties": [],
                "cache_hit": False,
                "resolver_runtime_ms": 1,
            },
            "fallback_applied": False,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.CAPSULE_ASSEMBLER.value,
            actor_id="m03",
            task_id="t-1",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


# ─── Cap1: .idx persistence + rebuild-from-events consistency ────────────────────


def test_index_persists_to_idx_files(event_log: EventLog, log_path: Path) -> None:
    """Cap1: persist_indexes writes the per-spec .idx files under events/index/."""
    rec = event_log.write_direct(_node_touched(local_intent_id="i-1"))
    store = EventLogIndexStore(log_path.parent)
    store.persist(event_log.all_records())

    index_dir = log_path.parent / "events" / "index"
    assert index_dir.is_dir()
    # §7.1 / Patch3 §3.2 names — at minimum the four named in §7.1 plus entity (subjects).
    for name in ("event_id.idx", "type.idx", "task.idx", "entity.idx"):
        assert (index_dir / name).exists(), f"missing {name}"

    # The persisted event_id index maps the written record.
    persisted = store.load()
    assert persisted is not None
    assert persisted.lookup_event_id(rec.event_id) is not None


def test_index_rebuilds_from_events_when_idx_deleted(event_log: EventLog, log_path: Path) -> None:
    """Cap1: delete the .idx files, rebuild from event files → identical lookups.

    Indexes are not the source of truth (Patch3 §3.2). Rebuild must reproduce the same
    entity / type / event_id lookups as the live index.
    """
    rec_a = event_log.write_direct(_node_touched(local_intent_id="i-a", entity_id="c-a"))
    rec_b = event_log.write_direct(_node_touched(local_intent_id="i-b", entity_id="c-b"))

    store = EventLogIndexStore(log_path.parent)
    store.persist(event_log.all_records())
    index_dir = log_path.parent / "events" / "index"
    assert any(index_dir.iterdir())

    # Nuke the persisted .idx files entirely.
    for p in index_dir.glob("*.idx"):
        p.unlink()
    assert store.load() is None  # nothing persisted now

    # Rebuild straight from the event log's records.
    rebuilt = store.rebuild_from_records(event_log.all_records())
    assert {r.event_id for r in rebuilt.lookup_entity("concept", "c-a")} == {rec_a.event_id}
    assert {r.event_id for r in rebuilt.lookup_entity("concept", "c-b")} == {rec_b.event_id}
    assert rebuilt.lookup_event_id(rec_a.event_id) is not None

    # And the rebuild re-persisted consistent .idx files.
    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.lookup_event_id(rec_b.event_id) is not None


def test_persisted_index_matches_live_index(event_log: EventLog, log_path: Path) -> None:
    """Cap1: persisted-then-loaded index agrees with a fresh in-memory index over the same records."""
    from towow.l0.event_log.index import EventIndex

    for i in range(5):
        event_log.write_direct(_node_touched(local_intent_id=f"i-{i}", entity_id=f"c-{i % 2}"))
    records = event_log.all_records()
    store = EventLogIndexStore(log_path.parent)
    store.persist(records)

    live = EventIndex(records)
    loaded = store.load()
    assert loaded is not None
    for et, eid in (("concept", "c-0"), ("concept", "c-1")):
        assert {r.event_id for r in loaded.lookup_entity(et, eid)} == {
            r.event_id for r in live.lookup_entity(et, eid)
        }


# ─── Cap2: point-lookup goes through index fast path (structural proof) ──────────


def test_get_event_uses_index_not_full_scan(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap2: repeated get_event hits the cached O(1) index, not a per-call O(n) scan.

    Structural proof: warm the index once (the single amortized build scan), then poison the
    full-scan iterator so any *further* scan raises. The subsequent get_event must still resolve
    the record — proving the second lookup did not re-scan.
    """
    rec = event_log.write_direct(_node_touched(local_intent_id="i-1"))
    event_log.get_event(rec.event_id)  # warm the cached index (one amortized build)

    def _boom() -> object:
        raise AssertionError("get_event must NOT re-scan _iter_committed_records per call")

    monkeypatch.setattr(event_log, "_iter_committed_records", _boom)
    found = event_log.get_event(rec.event_id)
    assert found is not None
    assert found.event_id == rec.event_id
    # a miss also resolves via the index (still no scan)
    assert event_log.get_event("evt-does-not-exist") is None


def test_get_event_by_input_hash_uses_index_not_full_scan(
    event_log: EventLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cap2: input_hash lookup goes through the (resolver_id, input_hash) index, not a full scan."""
    rec = event_log.write_direct(
        _resolver_decision(local_intent_id="i-1", resolver_id="ObligationActivationResolver", input_hash="h-abc"),
    )
    event_log.get_event_by_input_hash("ObligationActivationResolver", "h-abc")  # warm

    def _boom() -> object:
        raise AssertionError("get_event_by_input_hash must NOT re-scan per call")

    monkeypatch.setattr(event_log, "_iter_committed_records", _boom)
    found = event_log.get_event_by_input_hash("ObligationActivationResolver", "h-abc")
    assert found is not None
    assert found.event_id == rec.event_id
    # miss returns None, still via index (no scan)
    assert event_log.get_event_by_input_hash("ObligationActivationResolver", "nope") is None


def test_get_events_by_entity_uses_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap2: new get_events_by_entity Read API resolves via the subjects[] entity index."""
    rec = event_log.write_direct(_node_touched(local_intent_id="i-1", entity_id="c-99"))
    event_log.get_events_by_entity("concept", "c-99")  # warm the cached index

    def _boom() -> object:
        raise AssertionError("get_events_by_entity must NOT re-scan per call")

    monkeypatch.setattr(event_log, "_iter_committed_records", _boom)
    found = event_log.get_events_by_entity("concept", "c-99")
    assert {r.event_id for r in found} == {rec.event_id}


# ─── Cap2: index respects committed-visibility (correctness, not just speed) ─────


def test_index_excludes_uncommitted_batch_events(event_log: EventLog, log_path: Path) -> None:
    """Cap2/Patch2 §2.1: the index must only contain committed-visible records.

    An un-sentineled path-A domain event is invisible to Read APIs; the index must not surface
    it either (otherwise the fast path would leak uncommitted state the scan path hides).
    """
    # Manually append an envelope + domain WITHOUT a sentinel (uncommitted batch tail).
    env = event_log.stamp_for_batch(
        EventIntent(
            local_intent_id="env-half",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
            payload={
                "read_set": [],
                "write_set": [],
                "active_obligations_declared": [],
                "claims": [],
                "patches": [],
                "uncertainties": [],
                "self_check": {"passed": True, "checks_run": []},
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.AGENT_FORK.value,
                actor_id="fork-1",
                task_id="t-1",
                parent_session_id="sess-test",
                fork_name="fork-1",
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY),
            ],
            schema_version="1.0.0",
        ),
        transaction_id="tx-half",
        batch_id="b-half",
        batch_position=0,
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(env.model_dump_json() + "\n")

    # The uncommitted envelope must not be resolvable via the index fast path.
    assert event_log.get_event(env.event_id) is None
