"""Portable Path-A commit helpers used by reflow recovery.

This module is deliberately narrower than ``daemon_run_once``: it owns only
the real CommitGate transaction required to create or resolve a recovery
finding.  It starts no daemon, advances no watermark and performs no dispatch.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

from towow.schemas.enums import EventType

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog
    from towow.schemas.event_intent import EventIntent


def commit_mutex(towow_dir: Path):  # noqa: ANN201
    """Use the canonical global commit lock shared by every Path-A writer."""

    from towow.l0.commit_gate.global_lock import global_commit_mutex

    return global_commit_mutex(towow_dir)


def _emit_intent_via_gate(
    event_log: EventLog,
    intent: object,
    towow_dir: Path,
    *,
    closure: str,
    expected_type: EventType,
    envelope_prefix: str,
) -> str | None:
    """Commit one finding intent and return its event id, failing closed."""

    from towow.l0.commit_gate.gate import CommitAttemptResult, CommitGate
    from towow.schemas.enums import BaseClassification, EventCategory
    from towow.schemas.event_intent import EventIntent, ProvenanceHint, Supersede

    fi = cast("EventIntent", intent)
    if fi.event_type is not expected_type or not fi.subjects:
        return None
    provenance = fi.provenance_hint
    envelope_id = f"{envelope_prefix}-{uuid.uuid4().hex[:12]}"
    envelope_intent = EventIntent(
        local_intent_id=envelope_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": envelope_id,
            "capsule_compiled_event_id": f"evt-stub-capsule-{envelope_prefix}",
            "self_check": {
                "passed": True,
                "checks_run": [],
                "blocking_checks": [
                    {
                        "check_id": "review.review_contract_present",
                        "status": "passed",
                        "evidence": {"closure": closure},
                    }
                ],
            },
            "active_obligations_declared": [],
        },
        provenance_hint=ProvenanceHint(
            actor_type=provenance.actor_type,
            actor_id=provenance.actor_id,
            daemon_name=provenance.daemon_name,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[fi.subjects[0]],
        schema_version="1.0.0",
    )
    with commit_mutex(towow_dir):
        result = CommitGate(event_log).attempt_commit(envelope_intent, [fi])
    records = result.records if isinstance(result, CommitAttemptResult) else result
    if not records or records[-1].event_type is not EventType.COMMIT_ACCEPTED:
        return None
    return next(
        (record.event_id for record in records if record.event_type is expected_type),
        None,
    )


def emit_finding_via_gate(
    event_log: EventLog,
    finding_intent: object,
    towow_dir: Path,
    *,
    closure: str,
) -> str | None:
    """Create a FindingCreated through the canonical Path-A CommitGate."""

    return _emit_intent_via_gate(
        event_log,
        finding_intent,
        towow_dir,
        closure=closure,
        expected_type=EventType.FINDING_CREATED,
        envelope_prefix="env-reflow-finding",
    )


def emit_finding_resolved_via_gate(
    event_log: EventLog,
    resolved_intent: object,
    towow_dir: Path,
    *,
    closure: str,
) -> str | None:
    """Create a FindingResolved through the canonical Path-A CommitGate."""

    return _emit_intent_via_gate(
        event_log,
        resolved_intent,
        towow_dir,
        closure=closure,
        expected_type=EventType.FINDING_RESOLVED,
        envelope_prefix="env-reflow-finding-resolved",
    )
