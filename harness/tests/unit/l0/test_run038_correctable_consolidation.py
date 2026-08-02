"""RUN-038 L0增强 M-0.1: Inv3 Correctable Consolidation — digest/snapshot amend 流 (§6.3).

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   §6.3 Invariant 3 Correctable Consolidation:
#     1. DigestSuperseded 存在 — digest 错了产新 DigestSuperseded (supersede 子类, 带 novelty),
#        指向被取代的 digest event
#     2. 不会"原地改 digest" — 所有修正走 supersede 链 (append-only)
#     3. SnapshotSuperseded 同理 — snapshot 错了不改原 snapshot, 产新 event supersede 旧的
#   §3.4 DigestSuperseded / §3.3 SnapshotSuperseded / §3.4 CrossRunConsolidationCommitted
#   §4.1.9 get_supersede_chain
#   附录 B Patch5 §5.1 — "当前有效判断由 M-0.2 projection 据 supersede chain 推导" (M-0.1 只保证
#     supersede 链可查 / novelty 必填 / 被 supersede 可追溯; 不维护"当前有效性")

Scope (honest): this proves the M-0.1-LAYER amend flow — that a real digest/snapshot event can be
amended by a real superseding event through the actual EventLog write path, that the original is
NOT modified in place (append-only), and that get_supersede_chain resolves the correction. It does
NOT exercise the consolidation RUN or cold-storage archival (those are M-0.7 / dependency-blocked,
tracked as a registered debt for Inv2). Current-effective-digest derivation is M-0.2's job, not
asserted here (Patch5 §5.1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
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


def _consolidation_digest(*, digest_hash: str, start: int, end: int) -> EventIntent:
    """A real CrossRunConsolidationCommitted intent (path B per §5.1)."""
    return EventIntent(
        local_intent_id=f"digest-{digest_hash}",
        event_type=EventType.CROSS_RUN_CONSOLIDATION_COMMITTED,
        event_category=EventCategory.CONSOLIDATION,
        payload={
            "segment_range": {"start_seq": start, "end_seq": end},
            "digest_hash": digest_hash,
            "digest_type": "summary",
            "original_event_count": end - start + 1,
            "retained_provenance": True,  # O-06 Inv2 schema-enforced
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SNAPSHOT_MODULE.value,
            actor_id="m07-consolidation",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=digest_hash, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _digest_superseded(*, new_hash: str, superseded_event_id: str) -> EventIntent:
    """A real DigestSuperseded amend intent (supersede subtype, novelty required by §6.3)."""
    return EventIntent(
        local_intent_id=f"digest-fix-{new_hash}",
        event_type=EventType.DIGEST_SUPERSEDED,
        event_category=EventCategory.CONSOLIDATION,
        payload={"digest_hash": new_hash},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SNAPSHOT_MODULE.value,
            actor_id="m07-consolidation",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(
            is_supersede=True,
            superseded_event_id=superseded_event_id,
            novelty="错误 digest 被修正 — 重新聚合后哈希变化",
            novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
        ),
        subjects=[
            Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=new_hash, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _snapshot_created(*, offset: int, state_hash: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"snap-{offset}",
        event_type=EventType.SNAPSHOT_CREATED,
        event_category=EventCategory.SNAPSHOT,
        payload={
            "event_offset": offset,
            "projection_state_hash": state_hash,
            "snapshot_type": "routine",
            "covered_projections": ["concept_graph"],
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07-snap"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=state_hash, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _snapshot_superseded(*, offset: int, state_hash: str, superseded_event_id: str) -> EventIntent:
    return EventIntent(
        local_intent_id=f"snap-fix-{offset}",
        event_type=EventType.SNAPSHOT_SUPERSEDED,
        event_category=EventCategory.SNAPSHOT,
        payload={"event_offset": offset, "projection_state_hash": state_hash},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07-snap"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(
            is_supersede=True,
            superseded_event_id=superseded_event_id,
            novelty="新 snapshot 覆盖更完整的状态",
            novelty_type=SupersedeNoveltyType.SCOPE_CHANGE,
        ),
        subjects=[Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=state_hash, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


# ─── Inv3.1 + Inv3.2 : digest amend via DigestSuperseded, original not modified in place ──


def test_digest_amend_via_supersede_chain(event_log: EventLog) -> None:
    """§6.3.1-3: a wrong digest is amended by a DigestSuperseded; chain resolves the correction."""
    bad = event_log.write_direct(_consolidation_digest(digest_hash="sha-bad", start=0, end=9))
    fix = event_log.write_direct(_digest_superseded(new_hash="sha-good", superseded_event_id=bad.event_id))

    # The amend points back at the original digest.
    assert fix.supersede.is_supersede is True
    assert fix.supersede.superseded_event_id == bad.event_id
    assert fix.supersede.novelty  # §6.3 / O-12: novelty must be present on a correction

    # get_supersede_chain from the fix resolves [fix, bad] (§4.1.9 traverses superseded_event_id).
    chain = event_log.get_supersede_chain(fix.event_id)
    assert [c.event_id for c in chain] == [fix.event_id, bad.event_id]


def test_original_digest_not_modified_in_place(event_log: EventLog, log_path: Path) -> None:
    """§6.3.2: append-only — the superseded digest record is unchanged + still fully retrievable.

    "不会出现原地改 digest" — the original record's bytes and hash must survive the amend, and
    get_event must still return it (provenance-preserving traceability of the superseded original).
    """
    bad = event_log.write_direct(_consolidation_digest(digest_hash="sha-bad", start=0, end=9))
    bad_hash_before = bad.record_hash

    event_log.write_direct(_digest_superseded(new_hash="sha-good", superseded_event_id=bad.event_id))

    still = event_log.get_event(bad.event_id)
    assert still is not None
    assert still.record_hash == bad_hash_before  # not rewritten in place
    assert still.payload["digest_hash"] == "sha-bad"  # original digest content intact
    assert event_log.verify_record_integrity(still)  # tamper gate agrees: untouched


# ─── Inv3.3 : SnapshotSuperseded amend ──────────────────────────────────────────


def test_snapshot_amend_via_supersede_chain(event_log: EventLog) -> None:
    """§6.3.3: a snapshot is corrected by a SnapshotSuperseded, not by editing the original."""
    snap = event_log.write_direct(_snapshot_created(offset=5, state_hash="state-v1"))
    fix = event_log.write_direct(
        _snapshot_superseded(offset=12, state_hash="state-v2", superseded_event_id=snap.event_id),
    )
    chain = event_log.get_supersede_chain(fix.event_id)
    assert [c.event_id for c in chain] == [fix.event_id, snap.event_id]
    # Original snapshot still retrievable + intact.
    original = event_log.get_event(snap.event_id)
    assert original is not None
    assert original.payload["projection_state_hash"] == "state-v1"
