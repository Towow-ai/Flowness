"""T-L0-01 — write-boundary typed-payload validation (M-0.1 §5.3.1).

External-observable done_criteria (LANDING-PLAN-TASKS T-L0-01):
  "拿一个 event_type 与 payload 不匹配的 EventIntent 走 write_direct，写入被拒报类型错；
   合法 payload 正常写入。"
plus verify_dep: "PAYLOAD_REGISTRY 映射真覆盖全 event_type" + "畸形 payload 样本被实际写边界拒
(不靠门自证)".

These tests drive the registry/dispatch directly at the EventLog write boundary — the rejection
is observed at the actual `write_direct` / `append_transaction_batch` API, not via a self-asserting
helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.event_log import build_accepted_batch_e11
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    DebtSeverity,
    DebtType,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.payloads.debt import DebtRegisteredPayload
from towow.schemas.payloads.registry import (
    PAYLOAD_REGISTRY,
    PAYLOAD_VALIDATION_ENFORCED,
    PAYLOAD_VALIDATION_EXEMPT,
    PayloadSchemaValidationError,
    conforms,
    validate_event_payload,
)

if TYPE_CHECKING:
    from pathlib import Path

# A payload shape that belongs to NodeTouched, deliberately wrong for any other event_type.
_FOREIGN_PAYLOAD = {"target_entity_type": "concept", "target_entity_id": "c-1", "touch_type": "read"}


def _valid_debt_payload() -> dict[str, object]:
    return DebtRegisteredPayload(
        debt_id="debt-test-1",
        debt_type=DebtType.STUB,
        severity=DebtSeverity.NORMAL,
        title="t",
        description="d",
        against_capability="cap@v1",
        resolution_criteria="rc",
    ).model_dump(mode="json")


def _intent(
    *,
    event_type: EventType,
    event_category: EventCategory,
    payload: dict[str, object],
    local_intent_id: str = "i-1",
) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=event_type,
        event_category=event_category,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id="test",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


@pytest.fixture
def event_log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "events.log")


# ─── registry completeness (verify_dep: 映射真覆盖全 event_type) ───────────────────


def test_registry_covers_every_event_type() -> None:
    assert set(PAYLOAD_REGISTRY) == set(EventType)


def test_enforced_and_exempt_partition_the_registry() -> None:
    # every registered type is either fail-closed or explicitly exempt (no third silent state)
    assert PAYLOAD_VALIDATION_ENFORCED.isdisjoint(PAYLOAD_VALIDATION_EXEMPT)
    assert set(EventType) == PAYLOAD_VALIDATION_ENFORCED | PAYLOAD_VALIDATION_EXEMPT
    # the ruler must be non-trivial — not a fake-done empty set
    assert len(PAYLOAD_VALIDATION_ENFORCED) >= 40


# ─── done_criteria: write_direct rejects mismatch, accepts valid ──────────────────


def test_write_direct_rejects_event_type_payload_mismatch(event_log: EventLog) -> None:
    """DebtRegistered (path-B, enforced) carrying a NodeTouched-shaped payload is rejected."""
    with pytest.raises(PayloadSchemaValidationError) as exc:
        event_log.write_direct(
            _intent(
                event_type=EventType.DEBT_REGISTERED,
                event_category=EventCategory.SEMANTIC_JUDGMENT,
                payload=_FOREIGN_PAYLOAD,
            ),
        )
    assert exc.value.event_type is EventType.DEBT_REGISTERED
    # nothing was written
    assert event_log.all_records() == []


def test_write_direct_accepts_valid_payload(event_log: EventLog) -> None:
    rec = event_log.write_direct(
        _intent(
            event_type=EventType.DEBT_REGISTERED,
            event_category=EventCategory.SEMANTIC_JUDGMENT,
            payload=_valid_debt_payload(),
        ),
    )
    assert rec.event_type is EventType.DEBT_REGISTERED
    assert len(event_log.all_records()) == 1


# ─── done_criteria mirror on path A (append_transaction_batch) ────────────────────


def test_append_batch_rejects_enforced_domain_payload_mismatch(event_log: EventLog) -> None:
    """An enforced path-A domain record with a foreign payload is rejected by the batch writer."""
    domain = event_log.stamp_for_batch(
        _intent(
            event_type=EventType.TASK_MODEL_TIER_ASSIGNED,
            event_category=EventCategory.STATE_TRANSITION,
            payload=_FOREIGN_PAYLOAD,
            local_intent_id="d-1",
        ),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=0,
    )
    sentinel = event_log.stamp_for_batch(
        _intent(
            event_type=EventType.COMMIT_ACCEPTED,  # exempt — sentinel never blocks
            event_category=EventCategory.COMMIT,
            payload={"x": 1},
            local_intent_id="s-1",
        ),
        transaction_id="tx-1",
        batch_id="b-1",
        batch_position=1,
    )
    batch = build_accepted_batch_e11("b-1", "tx-1", [domain], sentinel)
    with pytest.raises(PayloadSchemaValidationError) as exc:
        event_log.append_transaction_batch(batch)
    assert exc.value.event_type is EventType.TASK_MODEL_TIER_ASSIGNED
    assert event_log.all_records() == []  # atomic — nothing landed


# ─── graduated rollout: exempt types pass through (producer-alignment debt tracked) ─


def test_exempt_type_passes_through_nonconforming(event_log: EventLog) -> None:
    """NodeTouched is exempt (stub-rewrap producers) — a non-conforming payload still writes.

    This is the honest graduated-rollout boundary, not a hole: the gap is tracked as debt.
    """
    assert EventType.NODE_TOUCHED in PAYLOAD_VALIDATION_EXEMPT
    rec = event_log.write_direct(
        _intent(
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={"kind": "wrapped", "anything": True},
        ),
    )
    assert rec.event_type is EventType.NODE_TOUCHED


# ─── conforms() pure check ────────────────────────────────────────────────────────


def test_conforms_returns_error_on_mismatch_none_on_match() -> None:
    assert conforms(EventType.DEBT_REGISTERED, _FOREIGN_PAYLOAD) is not None
    assert conforms(EventType.DEBT_REGISTERED, _valid_debt_payload()) is None


def test_validate_event_payload_shadow_sink_records_nonenforced() -> None:
    """For an exempt type, validate_event_payload records into shadow_sink and does not raise."""
    sink: list[tuple[EventType, object]] = []
    validate_event_payload(EventType.NODE_TOUCHED, {"bad": 1}, shadow_sink=sink)  # type: ignore[arg-type]
    assert len(sink) == 1
    assert sink[0][0] is EventType.NODE_TOUCHED
