"""M-0.7 §4.2 — immutable_truth-not-digestable hard constraint (verify_consolidation_invariants).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §4.2 第一层硬约束: immutable_truth 不允许 digest 替代 (digest 是摘要; immutable_truth 原件
#          必须永久保留, 不允许 digest 取代后丢原件)
#     §6.4 inv2 (consolidation-invariants.md #2): "immutable_truth event 不可被 consolidate 进
#          digest; abstractable_process 可以; discardable_noise 在 cursor watermark 之后可丢"
#
# Why: consolidation 的 digest 把一段 event 摘成一条 CrossRunConsolidationCommitted (摘要)。
# immutable_truth 是事实源 (NatureJudgmentCaptured / CommitAccepted / ObligationCaptured / …);
# 把事实源摘掉=丢真相。本守门在 consolidation 真提交前 fail-closed 拒绝任何把 immutable_truth
# 卷进 digest 段的提案 (M-0.7 §6: 失败立刻产 FindingCreated, 不 silent retry — 调用方决定如何报)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.schemas.enums import BaseClassification

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog
    from towow.schemas.event_record import EventRecord


class ImmutableTruthDigestError(RuntimeError):
    """Raised when a consolidation proposal would digest (abstract away) an immutable_truth event.

    §4.2 hard constraint: immutable_truth originals must be permanently retained; a digest must
    never replace them. This is fail-closed — the consolidation is rejected, not silently trimmed.
    """


@dataclass(frozen=True)
class ConsolidationInvariantReport:
    """Outcome of verify_consolidation_invariants for one proposed digest segment.

    ``ok`` is True only when no invariant is violated. ``immutable_truth_in_segment`` lists the
    event_ids of every immutable_truth event the proposal would have folded into the digest — each
    is a §4.2 violation (they cannot be abstracted away).
    """

    ok: bool
    immutable_truth_in_segment: list[str] = field(default_factory=list)


def _events_in_segment(event_log: EventLog, start_seq: int, end_seq: int) -> list[EventRecord]:
    return [r for r in event_log.all_records() if start_seq <= r.sequence_number <= end_seq]


def verify_consolidation_invariants(
    event_log: EventLog,
    *,
    start_seq: int,
    end_seq: int,
) -> ConsolidationInvariantReport:
    """M-0.7 §4.2 / §6.4 inv2 — does digesting [start_seq, end_seq] respect immutable_truth?

    Scans the proposed digest segment for immutable_truth events. Any such event is a violation:
    immutable_truth cannot be consolidated into a digest (the original must be preserved, not
    replaced by a summary). Returns ok=True iff the segment is digest-safe (no immutable_truth).

    This is the verification half; ``assert_no_immutable_truth_digested`` is the fail-closed guard
    a consolidation runner calls before committing a CrossRunConsolidationCommitted.
    """
    offenders = [
        r.event_id
        for r in _events_in_segment(event_log, start_seq, end_seq)
        if r.base_classification is BaseClassification.IMMUTABLE_TRUTH
    ]
    return ConsolidationInvariantReport(ok=not offenders, immutable_truth_in_segment=offenders)


def assert_no_immutable_truth_digested(
    event_log: EventLog,
    *,
    start_seq: int,
    end_seq: int,
) -> None:
    """Fail-closed guard: raise ImmutableTruthDigestError if the proposed digest hits §4.2.

    A consolidation runner calls this before committing the digest. If it raises, the runner must
    NOT commit the consolidation (M-0.7 §6 — emit a FindingCreated anomaly instead of retrying).
    """
    report = verify_consolidation_invariants(event_log, start_seq=start_seq, end_seq=end_seq)
    if not report.ok:
        msg = (
            "consolidation would digest immutable_truth originals (M-0.7 §4.2 hard constraint): "
            f"{report.immutable_truth_in_segment}"
        )
        raise ImmutableTruthDigestError(msg)


__all__ = [
    "ConsolidationInvariantReport",
    "ImmutableTruthDigestError",
    "assert_no_immutable_truth_digested",
    "verify_consolidation_invariants",
]
