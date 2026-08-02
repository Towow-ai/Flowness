"""M-0.7 §4.4 — event-level classification vs segment-level archive 对齐 (方案 A 保守复制).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §4.4 核心约束: 归档决策是 event-level (按 base_classification); 物理归档是 segment-level
#          container (hot segment 文件是物理单位)。一个 segment 可能混 immutable_truth +
#          abstractable_process + discardable_noise。两个粒度需要对齐。
#     §4.4 v3 初版方案 A 保守复制:
#       if 所有 events 都是 discardable_noise: 走纯 discard 路径 (§4.3) —— 删 hot 文件, 不入 cold
#       else: mixed segment 保守路径 —— 整段复制到 cold (含 incidental 保留的 discardable_noise);
#             ArchiveSegmentMoved.classification_summary 如实统计三类占比;
#             discardable_noise 在该 segment 内 eligible-to-drop but conservatively retained in cold
#
# Why: dropping a hot segment file is segment-level (a whole file), but whether an event MAY be
# dropped is event-level (its base_classification). If a segment mixes a fact source (immutable_
# truth) with noise, you cannot delete the file — you'd lose the fact. Scheme A resolves the
# mismatch conservatively: pure-discard only when the WHOLE segment is noise; otherwise copy the
# whole segment to cold (keeping incidental noise) so no event-level "must keep" is ever violated
# by a segment-level "drop". This module computes that aligned decision; it does not perform the
# physical cold copy (§7, debt) — it decides the path + the honest classification_summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING

from towow.l0.snapshot.reachability import determine_archive_action
from towow.schemas.enums import ArchiveAction, BaseClassification

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog
    from towow.schemas.event_record import EventRecord


@unique
class SegmentArchivePath(StrEnum):
    """§4.4 方案 A — the two segment-level physical archive paths."""

    PURE_DISCARD = "pure_discard"  # 整段都是 discardable_noise → 删 hot, 不入 cold (§4.3)
    CONSERVATIVE_COLD = "conservative_cold"  # mixed → 整段入 cold (含 incidental noise)
    EMPTY = "empty"  # no events in range — nothing to archive


@dataclass(frozen=True)
class SegmentArchiveDecision:
    """§4.4 aligned segment-level decision + honest classification_summary.

    ``path`` is the segment-level physical path. ``classification_summary`` is the as-is per-class
    event count over [start_seq, end_seq] (the spec's classification_summary 三类占比). ``conserva
    tively_retained_noise_seqs`` lists discardable_noise events that ARE eligible-to-drop but, being
    in a mixed segment, are conservatively retained in cold (scheme A) — these are exactly the
    seqs where event-level "may drop" yields to segment-level "must keep the file".
    """

    path: SegmentArchivePath
    classification_summary: dict[BaseClassification, int]
    conservatively_retained_noise_seqs: list[int]


def _events_in_segment(event_log: EventLog, start_seq: int, end_seq: int) -> list[EventRecord]:
    return [r for r in event_log.all_records() if start_seq <= r.sequence_number <= end_seq]


def align_segment_archive(
    event_log: EventLog,
    *,
    start_seq: int,
    end_seq: int,
) -> SegmentArchiveDecision:
    """M-0.7 §4.4 — decide the segment-level archive path aligned with event-level classification.

    Scans [start_seq, end_seq], tallies the three base_classifications, and applies scheme A:
      - all events discardable_noise → PURE_DISCARD (the §4.3 tombstone/delete path is valid; no
        event-level must-keep is violated because there is none).
      - any non-discardable event present → CONSERVATIVE_COLD (the whole segment, including
        incidental discardable_noise, goes to cold; the noise seqs are reported as conservatively
        retained — the precise alignment point where segment granularity overrides event "may drop").

    The decision is provably aligned with event-level determine_archive_action: in CONSERVATIVE_
    COLD every non-discardable event's event-level action is a cold-bound one (MOVE_TO_COLD /
    DIGEST_AND_COLD), so the segment going to cold cannot lose any event the event-level layer
    wanted kept.
    """
    events = _events_in_segment(event_log, start_seq, end_seq)
    summary: dict[BaseClassification, int] = {
        BaseClassification.IMMUTABLE_TRUTH: 0,
        BaseClassification.ABSTRACTABLE_PROCESS: 0,
        BaseClassification.DISCARDABLE_NOISE: 0,
    }
    for r in events:
        summary[r.base_classification] += 1

    if not events:
        return SegmentArchiveDecision(
            path=SegmentArchivePath.EMPTY,
            classification_summary=summary,
            conservatively_retained_noise_seqs=[],
        )

    non_noise = (
        summary[BaseClassification.IMMUTABLE_TRUTH]
        + summary[BaseClassification.ABSTRACTABLE_PROCESS]
    )
    if non_noise == 0:
        # whole segment is discardable_noise → pure discard is safe & aligned
        return SegmentArchiveDecision(
            path=SegmentArchivePath.PURE_DISCARD,
            classification_summary=summary,
            conservatively_retained_noise_seqs=[],
        )
    # mixed → conservative cold copy; incidental noise is eligible-to-drop but retained in cold
    retained_noise = sorted(
        r.sequence_number
        for r in events
        if r.base_classification is BaseClassification.DISCARDABLE_NOISE
    )
    return SegmentArchiveDecision(
        path=SegmentArchivePath.CONSERVATIVE_COLD,
        classification_summary=summary,
        conservatively_retained_noise_seqs=retained_noise,
    )


def verify_segment_alignment(
    event_log: EventLog,
    decision: SegmentArchiveDecision,
    *,
    start_seq: int,
    end_seq: int,
) -> bool:
    """§4.4 alignment invariant — the segment decision never contradicts event-level actions.

    Returns True iff:
      - PURE_DISCARD ⟹ every event in the segment is discardable_noise (event-level DISCARD), so
        deleting the file drops only droppable events; AND
      - CONSERVATIVE_COLD ⟹ every non-discardable event's event-level determine_archive_action is
        a cold-bound action (MOVE_TO_COLD / DIGEST_AND_COLD), so moving the whole segment to cold
        keeps every event the event-level layer wanted kept.

    This is the load-bearing "two granularities do not conflict" check.
    """
    events = _events_in_segment(event_log, start_seq, end_seq)
    if decision.path is SegmentArchivePath.PURE_DISCARD:
        return all(
            determine_archive_action(r.base_classification) is ArchiveAction.DISCARD
            for r in events
        )
    if decision.path is SegmentArchivePath.CONSERVATIVE_COLD:
        cold_bound = {ArchiveAction.MOVE_TO_COLD, ArchiveAction.DIGEST_AND_COLD}
        return all(
            determine_archive_action(r.base_classification) in cold_bound
            for r in events
            if r.base_classification is not BaseClassification.DISCARDABLE_NOISE
        )
    return not events  # EMPTY is aligned iff there really are no events


__all__ = [
    "SegmentArchiveDecision",
    "SegmentArchivePath",
    "align_segment_archive",
    "verify_segment_alignment",
]
