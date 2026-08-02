"""M-0.4 Envelope reader — reconstruct a TransactionEnvelope from the event log.

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md
#   §6.2 (L340..L342) — "Envelope 不独立持久化": an envelope is one event in the log;
#     reading it goes through the M-0.1 Read API (get_event / get_events_by_type), never
#     a second store. This module is the "读出" half of debt-payoff block 1.
#   §2 — the reconstructed object is the canonical TransactionEnvelope schema.
#
# Honesty contract: a boundary-less stub payload (no read_set / write_set) is NOT a real
# TransactionEnvelope. read_envelope reconstructs the canonical schema and raises rather
# than silently fabricating a half-envelope — a stub that cannot be read back as a proper
# package is exactly the debt block 1 exists to surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from towow.schemas.enums import EventType
from towow.schemas.envelope import TransactionEnvelope

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


class EnvelopeReadError(Exception):
    """Raised when an envelope cannot be read back as a canonical TransactionEnvelope."""


def read_envelope(event_log: EventLog, envelope_event_id: str) -> TransactionEnvelope:
    """Read a TransactionEnvelopeSubmitted event back and reconstruct the M-0.4 schema.

    Raises EnvelopeReadError if the event is absent, is not an envelope event, or its
    payload does not satisfy the TransactionEnvelope schema (e.g. a boundary-less stub).
    """
    record = event_log.get_event(envelope_event_id)
    if record is None:
        raise EnvelopeReadError(f"no event with id {envelope_event_id}")
    if record.event_type is not EventType.TRANSACTION_ENVELOPE_SUBMITTED:
        raise EnvelopeReadError(
            f"event {envelope_event_id} is {record.event_type.value}, not an envelope",
        )
    return _reconstruct(record)


def list_envelopes(event_log: EventLog) -> list[tuple[str, TransactionEnvelope]]:
    """Reconstruct every readable envelope; skip stub payloads that fail the schema.

    Returns (event_id, envelope) pairs in log order. Stub / legacy payloads that cannot
    be reconstructed are omitted (use read_envelope for a single id to get the error).
    """
    out: list[tuple[str, TransactionEnvelope]] = []
    for record in event_log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED):
        try:
            out.append((record.event_id, _reconstruct(record)))
        except EnvelopeReadError:
            continue
    return out


def _reconstruct(record: EventRecord) -> TransactionEnvelope:
    # strict=False: the payload is JSON-serialized (enums/datetime as strings); it was
    # already strict-validated when built, so reconstruction coerces stored types back.
    try:
        return TransactionEnvelope.model_validate(record.payload, strict=False)
    except ValidationError as exc:
        raise EnvelopeReadError(
            f"event {record.event_id} payload is not a canonical TransactionEnvelope: {exc}",
        ) from exc


__all__ = ["EnvelopeReadError", "list_envelopes", "read_envelope"]
