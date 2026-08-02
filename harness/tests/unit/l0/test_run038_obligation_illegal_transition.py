"""RUN-038 L0增强 M-0.6 Cap1: 三态 canonical lifecycle 显式禁止非法转移 (§3.2).

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §3.2
  合法转移:  (none) → active   [ObligationCaptured]
             active → superseded [ObligationEvolved 针对旧 obligation]
             active → retired    [ObligationRetired]
  显式禁止:  superseded → active   (被取代不能复活)
             retired → active      (退役不能复活)
             superseded → retired  (已 superseded 不需再 retire)
             retired → superseded   (对称: 已 retired 不能再被 evolve 改写)

superseded / retired 是 terminal 态。reducer 必须拒绝任何把 terminal 态再改成别的
canonical 态的转移 (canonical_state 不动); scoped 字段 (last_observed_lifecycle_event_type)
仍可记录"观测到该事件类型"以保审计完整, 但 canonical_state 不被非法转移污染。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.obligations.lifecycle import IllegalObligationTransitionError, is_legal_canonical_transition
from towow.l0.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    ObligationCanonicalState,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


def _record(event_type: EventType, payload: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.OBLIGATION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


# ─── pure state-machine predicate ────────────────────────────────────────────


def test_legal_transitions_accepted() -> None:
    A = ObligationCanonicalState.ACTIVE
    S = ObligationCanonicalState.SUPERSEDED
    R = ObligationCanonicalState.RETIRED
    # active is the only non-terminal source of canonical transitions
    assert is_legal_canonical_transition(A, S) is True
    assert is_legal_canonical_transition(A, R) is True
    # idempotent self-stays are not a transition → legal (no-op)
    assert is_legal_canonical_transition(A, A) is True


def test_illegal_transitions_rejected() -> None:
    A = ObligationCanonicalState.ACTIVE
    S = ObligationCanonicalState.SUPERSEDED
    R = ObligationCanonicalState.RETIRED
    # the four explicitly-forbidden transitions in §3.2
    assert is_legal_canonical_transition(S, A) is False  # superseded → active
    assert is_legal_canonical_transition(R, A) is False  # retired → active
    assert is_legal_canonical_transition(S, R) is False  # superseded → retired
    assert is_legal_canonical_transition(R, S) is False  # retired → superseded (symmetric)


# ─── reducer enforcement: terminal canonical_state never re-transitioned ──────


def test_reducer_blocks_evolve_on_retired(tmp_path: Path) -> None:
    """retired obligation 收到 ObligationEvolved → canonical_state 仍 retired (不变 superseded)."""
    store = ProjectionStore(tmp_path / "graph")
    oid = "obl-x"
    store._apply(_record(EventType.OBLIGATION_CAPTURED, {"obligation_id": oid, "attached_to": []}, 1))
    store._apply(_record(EventType.OBLIGATION_RETIRED, {"obligation_id": oid}, 2))
    # illegal: retired → superseded must be refused (canonical stays retired)
    store._apply(_record(EventType.OBLIGATION_EVOLVED, {"obligation_id": oid}, 3))
    proj = store.read("obligation_lifecycle_state")
    node = next(n for n in proj["obligations"] if n["obligation_id"] == oid)
    assert node["canonical_state"] == ObligationCanonicalState.RETIRED.value


def test_reducer_blocks_retire_on_superseded(tmp_path: Path) -> None:
    """superseded obligation 收到 ObligationRetired → canonical_state 仍 superseded."""
    store = ProjectionStore(tmp_path / "graph")
    oid = "obl-y"
    store._apply(_record(EventType.OBLIGATION_CAPTURED, {"obligation_id": oid, "attached_to": []}, 1))
    store._apply(_record(EventType.OBLIGATION_EVOLVED, {"obligation_id": oid}, 2))
    # illegal: superseded → retired must be refused (canonical stays superseded)
    store._apply(_record(EventType.OBLIGATION_RETIRED, {"obligation_id": oid}, 3))
    proj = store.read("obligation_lifecycle_state")
    node = next(n for n in proj["obligations"] if n["obligation_id"] == oid)
    assert node["canonical_state"] == ObligationCanonicalState.SUPERSEDED.value


def test_reducer_scoped_event_does_not_touch_canonical_on_terminal(tmp_path: Path) -> None:
    """terminal 态下 scoped 事件 (Checked) 仍只记 scoped 字段, canonical 不被改 (本就 §3.3)."""
    store = ProjectionStore(tmp_path / "graph")
    oid = "obl-z"
    store._apply(_record(EventType.OBLIGATION_CAPTURED, {"obligation_id": oid, "attached_to": []}, 1))
    store._apply(_record(EventType.OBLIGATION_RETIRED, {"obligation_id": oid}, 2))
    chk = _record(EventType.OBLIGATION_CHECKED, {"obligation_id": oid}, 3)
    store._apply(chk)
    proj = store.read("obligation_lifecycle_state")
    node = next(n for n in proj["obligations"] if n["obligation_id"] == oid)
    assert node["canonical_state"] == ObligationCanonicalState.RETIRED.value
    # scoped audit trail still records the observed event type (completeness)
    assert node["last_check_event_id"] == chk.event_id


def test_transition_error_raisable() -> None:
    """IllegalObligationTransitionError 可被显式抛 (供需要 hard-fail 的调用点用)."""
    with pytest.raises(IllegalObligationTransitionError):
        raise IllegalObligationTransitionError(
            ObligationCanonicalState.SUPERSEDED,
            ObligationCanonicalState.ACTIVE,
        )
