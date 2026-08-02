"""TransactionBatch — Path A atomic append unit (per E.1.1 LEDGER Conflict 4/6/7).

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     附录 B Patch 1 §1.4 (L1562..L1587) TransactionBatch schema
#     附录 B Patch 2 §2.1-2.2 (L1591..L1625) committed-visible + recovery
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#     §6.4 batch composition (L800..L810) — gate-detected lifecycle events
#     §6.5 CommitAccepted/CommitRejected payload (L811..L848)
#     §11.x line 1031 — rejected batch 允许包含 gate-detected lifecycle events
#       (ObligationViolated / LockReleased) 跟 CommitRejected sentinel 原子
#
# ─── E.1.1 Conflict 4/6/7 (resolved per docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md) ───
#   M-0.1a Patch 1 §1.4 字面写 batch[0] = TransactionEnvelopeSubmitted；
#   M-0.4 §4.2 写 envelope path B 先于 commit gate 落盘。
#
#   Canonical decision: **envelope is NEVER in path-A batch** — it walks path B,
#   立即 audit-visible, batch_id=None. Path A batch composition becomes:
#
#     accepted batch  = [domain events..., CommitAccepted]
#                       (events[0] 可以是任意 domain event；envelope 不在 batch 内)
#     rejected batch  = [gate-detected lifecycle events..., CommitRejected]
#                       (无 envelope；no agent-proposed domain events)
#     recovery batch  = [LockReleased, CommitRejected(recovery_of_abandoned=true)]
#                       (长度 = 2；recovery 无 synthetic envelope)
#
#   Envelope ↔ outcome correlation through `CommitAccepted/Rejected.envelope_event_id`
#   payload field (not through batch[0] position). This preserves audit-visibility of
#   abandoned attempts (envelope persisted via path B even if outcome never written)
#   while keeping commit outcomes atomic.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import BatchStatus, EventType
from towow.schemas.event_record import EventRecord


class TransactionBatch(BaseModel):
    """Path A atomic append unit (E.1.1-canonical).

    Composition per E.1.1 LEDGER Conflict 4/6/7:

      accepted batch (status=committed):
        events[0..N-2] = domain EventRecords (ConceptCreated / ObligationActivated / ...)
        events[N-1]    = CommitAccepted (sentinel)
        envelope is NOT in batch (lives on path B, see commit_gate)

      rejected batch (status=rejected):
        events[0..N-2] = gate-detected lifecycle events
                         (ObligationViolated / LockReleased) — optional
        events[N-1]    = CommitRejected (sentinel)
        envelope is NOT in batch.
        No agent-proposed domain events (M-0.5 §11.x line 1031).

      recovery batch (subset of rejected; produced by scan_abandoned):
        events = [LockReleased, CommitRejected(recovery_of_abandoned=true)]
        No synthetic envelope; envelope_event_id payload links to abandoned envelope.

    Invariants enforced (E.1.1-canonical):
      - events[-1] sentinel matches status (CommitAccepted committed / CommitRejected rejected)
      - TransactionEnvelopeSubmitted NEVER appears in any batch position
      - sequence_number strictly increasing across events
      - every EventRecord's transaction_id / batch_id matches the batch
      - min length 1 (sentinel alone is the minimum — a path-A "no domain" accepted batch
        with just CommitAccepted is structurally valid; the typical accepted run has ≥ 2)
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    batch_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    status: BatchStatus
    events: list[EventRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_batch_invariants(self) -> Self:
        evs = self.events

        # E.1.1 Conflict 4/6/7: TransactionEnvelopeSubmitted MUST NOT be in batch
        for idx, ev in enumerate(evs):
            if ev.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED:
                raise ValueError(
                    f"TransactionEnvelopeSubmitted must not appear in path-A batch "
                    f"(E.1.1 LEDGER Conflict 4/6/7 — envelope lives on path B); "
                    f"found at batch position {idx}",
                )

        # sequence_number 严格升序 (Patch 2 §2.1)
        for prev, curr in pairwise(evs):
            if curr.sequence_number <= prev.sequence_number:
                raise ValueError(
                    f"sequence_number must be strictly increasing within batch: "
                    f"{prev.sequence_number} → {curr.sequence_number}",
                )

        # batch_id / transaction_id 跟每个 event 一致
        for ev in evs:
            if ev.transaction_id != self.transaction_id:
                raise ValueError(
                    f"event {ev.event_id} transaction_id={ev.transaction_id!r} "
                    f"differs from batch transaction_id={self.transaction_id!r}",
                )
            if ev.batch_id != self.batch_id:
                raise ValueError(
                    f"event {ev.event_id} batch_id={ev.batch_id!r} "
                    f"differs from batch batch_id={self.batch_id!r}",
                )

        # sentinel composition per M-0.5 §6.4 + E.1.1 canonical
        expected_sentinel = (
            EventType.COMMIT_ACCEPTED
            if self.status is BatchStatus.COMMITTED
            else EventType.COMMIT_REJECTED
        )
        if evs[-1].event_type is not expected_sentinel:
            raise ValueError(
                f"batch status={self.status.value} requires sentinel={expected_sentinel.value}; "
                f"got events[-1]={evs[-1].event_type.value}",
            )

        return self


__all__ = [
    "TransactionBatch",
]
