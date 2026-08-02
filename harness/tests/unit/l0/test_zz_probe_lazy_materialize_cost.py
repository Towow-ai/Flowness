"""Throwaway diagnostic probe (reviewer-authored, not part of the suite) — DELETE after use.

Checks whether committed_index().records() on a LazyEventIndex (the realistic cold-CLI-process
adopt scenario) pays a full per-record mmap+parse pass NOT counted by the
test_commit_single_materialization.py regression test's _iter_committed_records spy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.commit_gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.event_log.index import LazyEventIndex
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


def _make_envelope_intent(task_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"env-{task_id}",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "task_id": task_id,
            "run_id": f"r-{task_id}",
            "capsule_compiled_event_id": "evt-stub-capsule-single-mat",
            "read_set": [{"entity_type": "task", "entity_id": task_id, "version": "evt-cap"}],
            "write_set": [{"entity_type": "task", "entity_id": task_id, "change_type": "modified"}],
            "patches": [],
            "self_check": {"passed": True, "checks_run": ["smoke"]},
            "as_of_projection_seq": 0,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=f"fork-{task_id}",
            task_id=task_id,
            run_id=f"r-{task_id}",
            parent_session_id=f"sess-{task_id}",
            fork_name=f"fork-{task_id}",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_probe_lazy_materialize_on_cold_adopt(tmp_path: Path, capsys) -> None:
    seed_log = EventLog(tmp_path / "events.log")
    seed_gate = CommitGate(seed_log)
    for i in range(5):
        assert seed_gate.attempt_commit(_make_envelope_intent(f"seed{i}"), domain_intents=[])[0] is not None

    cold_log = EventLog(tmp_path / "events.log")
    with capsys.disabled():
        print("\ncold_log._index type:", type(cold_log._index).__name__)
        print("is LazyEventIndex:", isinstance(cold_log._index, LazyEventIndex))

    assert isinstance(cold_log._index, LazyEventIndex), "adopt should have produced a LazyEventIndex"
    with capsys.disabled():
        print("cache size before any .records():", len(cold_log._index._cache))

    real_materialize = cold_log._index._materialize
    counter = {"n": 0}

    def spy_materialize(event_id):
        counter["n"] += 1
        return real_materialize(event_id)

    cold_log._index._materialize = spy_materialize  # type: ignore[method-assign]

    cold_gate = CommitGate(cold_log)
    measured = cold_gate.attempt_commit(_make_envelope_intent("cold-measured"), domain_intents=[])
    with capsys.disabled():
        print("attempt_commit ok:", measured[0] is not None)
        print("total _materialize calls (incl. cache hits):", counter["n"])
        print("cache size after:", len(cold_log._index._cache))
