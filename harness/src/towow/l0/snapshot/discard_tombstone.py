"""M-0.7 §4.3 — discardable_noise 删除留痕 (ArchiveSegmentMoved tombstone).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §4.3 第三层删除留痕: 删 discardable_noise 仍产 ArchiveSegmentMoved
#          (archive_destination=discarded, provenance_preserved=false) —— 让所有归档动作走同一
#          event 链路 (不分两套), 事后审计能查到"这段 segment 被 discard 了"。
#     §4.1 矩阵: discardable_noise → 直接删除 (不入 cold), 留痕 event = ArchiveSegmentMoved
#               (archive_destination=discarded, provenance_preserved=false)
#
# Scope: this emits the §4.3 TOMBSTONE (the audit trace of what/why was discarded). The
# destructive hot-log file removal + index update is §7 cold-storage machinery (out of scope —
# tracked as M-0.7 §7 debt); a tombstone without the physical delete is still correct and useful
# (the audit record exists; the bytes linger until §7 compaction). Mixed segments do NOT take this
# pure-discard path (§4.4) — only a segment whose events are all discardable_noise.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

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
    from towow.l0.event_log.event_log import EventLog

# §4.3 fixed tombstone fields — discarded segments never enter cold and keep no provenance.
DISCARD_DESTINATION = "discarded"


def emit_discard_tombstone(
    event_log: EventLog,
    *,
    start_seq: int,
    end_seq: int,
) -> str:
    """emit the §4.3 ArchiveSegmentMoved tombstone for a discarded discardable_noise segment.

    Records that [start_seq, end_seq] was discarded (archive_destination=discarded,
    provenance_preserved=false) with original_event_count = number of events in the range, so an
    audit can later answer "what segment was dropped". Returns the tombstone event_id.

    The "what was discarded" is segment_range + original_event_count; "why it was droppable" is
    structurally encoded — archive_destination=discarded only ever applies to an all-discardable_
    noise segment past every consumer (§4.3/§4.4), and provenance_preserved=false records that no
    trace was kept. The payload stays byte-for-byte the canonical M-0.1 §3.4 ArchiveSegmentMoved
    schema (extra="forbid", PAYLOAD_VALIDATION_ENFORCED) — no out-of-schema fields.

    Does NOT itself delete hot-log bytes — that destructive step is §7 (debt). The tombstone is the
    durable audit trace the spec requires regardless.
    """
    count = sum(1 for r in event_log.all_records() if start_seq <= r.sequence_number <= end_seq)
    intent = EventIntent(
        local_intent_id=f"discard-{uuid.uuid4().hex[:12]}",
        event_type=EventType.ARCHIVE_SEGMENT_MOVED,
        event_category=EventCategory.CONSOLIDATION,
        payload={
            "segment_range": {"start_seq": start_seq, "end_seq": end_seq},
            "archive_destination": DISCARD_DESTINATION,
            "original_event_count": count,
            "provenance_preserved": False,  # §4.3: discarded → no provenance retained
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07"),
        # the tombstone itself is an audit trace of a delete — abstractable process留痕, not a fact.
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"discard-{start_seq}-{end_seq}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


__all__ = ["DISCARD_DESTINATION", "emit_discard_tombstone"]
