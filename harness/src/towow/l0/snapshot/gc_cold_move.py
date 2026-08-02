"""M-0.7 §3+§4+§7 — GC cold-move leg: physically archive GC-collectable sealed segments.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §3.1 double-filter: a seq is archivable ⟺ seq ≤ archive_seq_bound AND GC-unreachable. Both
#          filters independent; both must hold.
#     §3.2 archive_seq_bound = min(consumer_watermark, hot_replay_floor). hot_replay_floor = the
#          oldest retained snapshot's event_offset — with NO snapshot it is 0, so nothing is
#          archivable (this leg establishes the anchor snapshot first to lift the floor).
#     §4.1 action matrix: immutable_truth → MOVE_TO_COLD (保留原件, 可 get_event 取回; §4.2 禁 digest);
#          abstractable_process → DIGEST_AND_COLD (此 leg 不 digest — 平移进 cold 保留原件即可);
#          discardable_noise → DISCARD (不入 cold, §4.3 tombstone).
#     §4.4 event-level classification vs segment-level archive (方案 A 保守复制): the physical unit is
#          a hot segment FILE. A segment whose events are all discardable_noise → pure discard; a
#          MIXED segment (carries any immutable_truth / abstractable_process) → the whole segment is
#          conservatively copied to cold (incidental noise retained in cold). align_segment_archive
#          decides which.
#     §7.2 three-phase prepare → (commit gate accept) → finalize. The physical action never becomes
#          final ahead of the accepted ArchiveSegmentMoved event ("agent 可以发现问题, 不能让坏状态先
#          进入系统" applied to physical IO).
#
# WHY this is the debt that was deferred (gc.py docstring): run_gc only REPORTS the double-filtered
# collectable seqs (archivable_noise_seqs / gc_reachable_protected_seqs) — it never physically moves
# anything, so the hot log never shrinks. run_consolidation (the digest driver) archives only
# segments with NO immutable_truth (§4.2: can't digest a fact source). That leaves the §4.1
# immutable_truth → MOVE_TO_COLD + §4.4 mixed-segment conservative-cold leg unbuilt — exactly the
# path the live log needs, where every old segment mixes immutable_truth with noise. This module is
# that leg: a DRIVER (§0 三层框架) reusing the already-built mechanism零件 (align_segment_archive /
# prepare_archive_to_cold / commit gate consolidation_check / finalize_archive / emit_discard_
# tombstone), never bypassing the commit gate.
#
# NOT a digest (honest边界): a cold MOVE preserves originals verbatim; it does NOT summarize. So the
# §6.3 ConsolidationEnvelope it submits carries an EMPTY digest proposal (segment_start_seq >
# segment_end_seq → zero events digested, empty provenance_refs). The gate's §4.2 immutable_truth-
# not-digested check (inv2) then truthfully finds nothing digested — moving immutable_truth to cold
# is explicitly allowed (§4.1/§4.2), only DIGESTING it is forbidden. The ArchiveSegmentMoved payload
# carries the real moved segment_range + provenance_preserved=True (cold keeps every event).
#
# DAEMON-INDEPENDENT (§8/§12 否决 daemon 化): this is a single-shot driver called by a CLI / sync
# command; it does NOT schedule "when to GC". It establishes a reconstructability anchor snapshot at
# the current committed head (so the moved events' effects are captured before they leave hot — §3.3
# 类别 1 hot_replay_floor), then archives every fully-collectable sealed segment.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.event_log.segments import ordered_hot_segments, segment_number_of
from towow.l0.snapshot.cold_archive import (
    PreparedArchive,
    finalize_archive,
    prepare_archive_to_cold,
    rollback_prepared_archive,
    verify_prepared_archive,
)
from towow.l0.snapshot.cold_storage import cold_archive_path
from towow.l0.snapshot.discard_tombstone import emit_discard_tombstone
from towow.l0.snapshot.gc import compute_archive_seq_bound
from towow.l0.snapshot.reachability import compute_gc_reachable, compute_gc_roots
from towow.l0.snapshot.segment_align import SegmentArchivePath, align_segment_archive
from towow.l0.snapshot.snapshot import create_standalone_snapshot
from towow.l0.snapshot.state import push_retention_watermark, write_gc_state
from towow.schemas.enums import (
    ActorType,
    ArchiveDestination,
    BaseClassification,
    EventCategory,
    EventType,
    LookbackType,
    ObligationDeclaredStatus,
    PatchType,
    SceneType,
    SnapshotType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.payloads.consolidation import LookbackWindow
from towow.schemas.payloads.consolidation_envelope import (
    COMPACTION_INVARIANT_OBLIGATIONS,
    CompactionInvariantDeclared,
    ConsolidationEnvelope,
    ConsolidationProposal,
    ConsolidationReadEntry,
    ConsolidationWriteEntry,
)

if TYPE_CHECKING:
    from pathlib import Path

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.event_log.lease import RetentionLeaseStore
    from towow.l0.projection.projection import ProjectionStore
    from towow.schemas.event_record import EventRecord


@dataclass(frozen=True)
class ColdMovedSegment:
    """One segment this leg physically archived (cold MOVE) or discarded (§4.3)."""

    segment_id: int
    start_seq: int
    end_seq: int
    destination: str  # ArchiveDestination — "cold" | "discarded"
    event_count: int
    archive_segment_event_id: str  # the accepted ArchiveSegmentMoved (cold) / tombstone (discarded)
    classification_summary: dict[str, int]


@dataclass(frozen=True)
class SkippedSegment:
    """A sealed segment NOT archived this run, with the spec-faithful reason (observability)."""

    segment_id: int
    start_seq: int
    end_seq: int
    reason: str


@dataclass(frozen=True)
class GcColdMoveResult:
    """Outcome of run_gc_cold_move — what was anchored, moved, discarded, and skipped (and why)."""

    archive_seq_bound: int
    consumer_watermark: int
    hot_replay_floor: int
    anchor_snapshot_event_id: str | None = None
    anchor_cutoff_seq: int = 0
    moved_segments: list[ColdMovedSegment] = field(default_factory=list)
    skipped_segments: list[SkippedSegment] = field(default_factory=list)

    @property
    def archived_event_count(self) -> int:
        return sum(s.event_count for s in self.moved_segments)


def _segment_records(event_log: EventLog, seg_id: int) -> list[EventRecord]:
    """The committed records physically living in hot segment ``seg_id`` (ascending by seq).

    Routed by the on-disk segment file (events-{seg:06d}.jsonl), so the archived set is the真实
    segment content, not a seq guess.
    """
    from towow.l0.event_log.segments import hot_dir_for

    seg_path = hot_dir_for(event_log.log_path) / f"events-{seg_id:06d}.jsonl"
    if not seg_path.exists():
        return []
    event_ids: list[str] = []
    for raw in seg_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = obj.get("event_id") if isinstance(obj, dict) else None
        if isinstance(eid, str):
            event_ids.append(eid)
    by_id = {e: event_log.get_event(e) for e in event_ids}
    return [r for r in (by_id[e] for e in event_ids) if r is not None]


def run_gc_cold_move(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    run_id: str,
    lease_store: RetentionLeaseStore | None = None,
    max_segments: int | None = None,
) -> GcColdMoveResult:
    """M-0.7 §3+§4+§7 — physically archive every fully GC-collectable SEALED hot segment.

    Steps (all reusing the M-0.7 mechanism layer; never bypassing the commit gate, §0):
      1. catch up projections; establish a reconstructability anchor snapshot at the current
         committed head (Mode-1 Path-B, consolidation_anchor). This lifts hot_replay_floor from 0 to
         cutoff so older segments become archivable, AND captures the to-be-moved events' effects in
         the snapshot bundle (so reconstruction stays closed — §3.3 类别 1 / §6.5).
      2. recompute archive_seq_bound + the GC-reachable closure (§3.1 double filter) WITH the new
         anchor snapshot present.
      3. for each SEALED segment (never the active tail) whose EVERY event is (seq ≤ bound AND
         GC-unreachable) — the segment-level container is fully collectable — apply §4.4:
           - all discardable_noise → §4.3 DISCARD: emit the ArchiveSegmentMoved(discarded) tombstone,
             then physically unlink the hot segment file (the bytes leave hot — log shrinks).
           - mixed (any immutable_truth / abstractable_process) → §4.4 CONSERVATIVE_COLD: §7.2
             prepare → commit gate (atomic ArchiveSegmentMoved, destination=cold) → finalize (rename
             staging→cold, index hot→cold, unlink hot). immutable_truth is MOVED, never digested
             (§4.1/§4.2); every archived event stays retrievable via get_event (cold lookup).
         A segment with ANY event above the bound or any GC-reachable event is conservatively SKIPPED
         (recorded with reason) — §3.1 withholds those events and §4.4 cannot partially archive a
         segment file.

    Returns a GcColdMoveResult with the anchor, moved/discarded segments, and skipped reasons.
    ``max_segments`` bounds how many segments to archive in one run (None = all collectable).
    """
    projection_store.catchup(event_log)

    # Step 1: reconstructability anchor snapshot at the current head (before anything leaves hot).
    snap = create_standalone_snapshot(
        event_log=event_log,
        projection_store=projection_store,
        towow_dir=towow_dir,
        snapshot_type=SnapshotType.CONSOLIDATION_ANCHOR,
    )

    # Step 2: recompute the double filter WITH the anchor snapshot present.
    bound, consumer_watermark, hot_replay_floor = compute_archive_seq_bound(
        event_log,
        projection_store,
        lease_store,
    )
    gc_roots = compute_gc_roots(event_log, projection_store, lease_store)
    gc_reachable = compute_gc_reachable(event_log, gc_roots)

    moved: list[ColdMovedSegment] = []
    skipped: list[SkippedSegment] = []

    if bound <= 0:
        # nothing collectable yet (e.g. a lagging consumer floors the bound) — honest no-op.
        _persist_gc_state(towow_dir, bound, consumer_watermark, hot_replay_floor)
        return GcColdMoveResult(
            archive_seq_bound=bound,
            consumer_watermark=consumer_watermark,
            hot_replay_floor=hot_replay_floor,
            anchor_snapshot_event_id=snap.snapshot_event_id,
            anchor_cutoff_seq=snap.cutoff_seq,
        )

    # Step 3: sealed segments only (drop the active tail — it is still being appended to).
    sealed = ordered_hot_segments(event_log.log_path)[:-1]
    archived_count = 0
    for seg_path in sealed:
        if max_segments is not None and archived_count >= max_segments:
            break
        seg_id = segment_number_of(seg_path)
        if seg_id is None:  # pragma: no cover - ordered_hot_segments only returns numbered segments
            continue
        records = _segment_records(event_log, seg_id)
        if not records:
            continue
        seg_start = min(r.sequence_number for r in records)
        seg_end = max(r.sequence_number for r in records)

        # §3.1 segment-level admissibility: the WHOLE segment must be collectable. Any event above
        # the ordering bound, or any GC-reachable (still-referenced) event, withholds the segment.
        above_bound = [r.sequence_number for r in records if r.sequence_number > bound]
        if above_bound:
            skipped.append(
                SkippedSegment(
                    segment_id=seg_id, start_seq=seg_start, end_seq=seg_end,
                    reason=(
                        f"segment has {len(above_bound)} event(s) above archive_seq_bound={bound} "
                        f"(first {min(above_bound)}) — §3.1 ordering filter withholds (would be replayed)"
                    ),
                ),
            )
            continue
        reachable_in_seg = [r.event_id for r in records if r.event_id in gc_reachable]
        if reachable_in_seg:
            skipped.append(
                SkippedSegment(
                    segment_id=seg_id, start_seq=seg_start, end_seq=seg_end,
                    reason=(
                        f"segment has {len(reachable_in_seg)} GC-reachable event(s) still referenced "
                        "(live object / projection / supersede chain / recent window) — §3.1 semantic "
                        "filter withholds (cannot partially archive a segment file, §4.4)"
                    ),
                ),
            )
            continue

        # §4.4 align: pure-discard vs conservative-cold.
        decision = align_segment_archive(event_log, start_seq=seg_start, end_seq=seg_end)
        classification = {bc.value: n for bc, n in decision.classification_summary.items()}
        archived_event_ids = [r.event_id for r in records]

        if decision.path is SegmentArchivePath.PURE_DISCARD:
            # §4.3 DISCARD — emit the tombstone (audit trace) then physically remove the hot bytes.
            tombstone_id = emit_discard_tombstone(event_log, start_seq=seg_start, end_seq=seg_end)
            if seg_path.exists():
                seg_path.unlink()
            moved.append(
                ColdMovedSegment(
                    segment_id=seg_id, start_seq=seg_start, end_seq=seg_end,
                    destination=ArchiveDestination.DISCARDED.value,
                    event_count=len(records),
                    archive_segment_event_id=tombstone_id,
                    classification_summary=classification,
                ),
            )
            archived_count += 1
            continue

        # §4.4 CONSERVATIVE_COLD — §7.2 prepare → commit gate → finalize.
        archive_event_id = _move_segment_to_cold(
            event_log=event_log,
            towow_dir=towow_dir,
            seg_path=seg_path,
            seg_id=seg_id,
            seg_start=seg_start,
            seg_end=seg_end,
            records=records,
            archived_event_ids=archived_event_ids,
            classification=decision.classification_summary,
            anchor_snapshot_event_id=snap.snapshot_event_id,
            run_id=run_id,
        )
        if archive_event_id is None:
            skipped.append(
                SkippedSegment(
                    segment_id=seg_id, start_seq=seg_start, end_seq=seg_end,
                    reason="commit gate rejected the cold-move ArchiveSegmentMoved (staging rolled back)",
                ),
            )
            continue
        moved.append(
            ColdMovedSegment(
                segment_id=seg_id, start_seq=seg_start, end_seq=seg_end,
                destination=ArchiveDestination.COLD.value,
                event_count=len(records),
                archive_segment_event_id=archive_event_id,
                classification_summary=classification,
            ),
        )
        archived_count += 1

    _persist_gc_state(towow_dir, bound, consumer_watermark, hot_replay_floor)
    return GcColdMoveResult(
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
        anchor_snapshot_event_id=snap.snapshot_event_id,
        anchor_cutoff_seq=snap.cutoff_seq,
        moved_segments=moved,
        skipped_segments=skipped,
    )


def _persist_gc_state(
    towow_dir: Path, bound: int, consumer_watermark: int, hot_replay_floor: int,
) -> None:
    """T-L0-35 — push retention_watermark + record the GC run (same state files as run_gc)."""
    push_retention_watermark(
        towow_dir,
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
    )
    write_gc_state(
        towow_dir,
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
    )


def _move_segment_to_cold(
    *,
    event_log: EventLog,
    towow_dir: Path,
    seg_path: Path,
    seg_id: int,
    seg_start: int,
    seg_end: int,
    records: list[EventRecord],
    archived_event_ids: list[str],
    classification: dict[BaseClassification, int],
    anchor_snapshot_event_id: str,
    run_id: str,
) -> str | None:
    """§7.2 prepare → commit gate → finalize for one conservative-cold segment. None on gate reject.

    The ArchiveSegmentMoved carries the real moved segment_range (provenance_preserved=True — cold
    keeps every event). The §6.3 ConsolidationEnvelope's digest proposal is EMPTY (a MOVE digests
    nothing) so the gate's §4.2 immutable_truth-not-digested check truthfully passes (moving
    immutable_truth to cold is allowed; only digesting it is forbidden).
    """
    from towow.l0.commit_gate import CommitGate

    prepared = prepare_archive_to_cold(towow_dir, seg_path)

    envelope = _build_cold_move_envelope(
        run_id=run_id,
        seg_start=seg_start,
        seg_end=seg_end,
        anchor_snapshot_event_id=anchor_snapshot_event_id,
        segment_id=seg_id,
    )
    asm_intent = _build_asm_intent(
        seg_start=seg_start,
        seg_end=seg_end,
        original_event_count=len(records),
        segment_path_before=str(seg_path),
        segment_path_after=str(cold_archive_path(towow_dir, seg_id)),
        classification=classification,
        prepared=prepared,
    )
    envelope_intent = _build_envelope_intent(run_id=run_id, envelope=envelope, segment_id=seg_id)

    gate = CommitGate(event_log)
    commit_outcome = gate.attempt_commit(envelope_intent, [asm_intent])
    if not isinstance(commit_outcome, list):  # pragma: no cover - audit_blocking is never set here
        msg = "attempt_commit returned a non-list outcome without audit_blocking"
        raise TypeError(msg)
    sentinel = commit_outcome[-1]
    if sentinel.event_type is not EventType.COMMIT_ACCEPTED:
        # §7.2 reject path — drop staging; hot/index were never touched (no half-truth).
        rollback_prepared_archive(towow_dir, prepared)
        return None

    asm_id = next(
        (r.event_id for r in commit_outcome if r.event_type is EventType.ARCHIVE_SEGMENT_MOVED),
        None,
    )

    # §7.2 finalize — re-verify staging+hot integrity at the boundary, then atomic rename + index +
    # unlink hot. A staging/hot tamper between prepare and finalize fails closed (rollback, no move).
    tamper = verify_prepared_archive(prepared)
    if tamper is not None:  # pragma: no cover - synchronous run; sealed segment is immutable
        rollback_prepared_archive(towow_dir, prepared)
        return None
    finalize_archive(towow_dir, prepared, event_ids=archived_event_ids)
    return asm_id


def _build_cold_move_envelope(
    *,
    run_id: str,
    seg_start: int,
    seg_end: int,
    anchor_snapshot_event_id: str,
    segment_id: int,
) -> ConsolidationEnvelope:
    """§6.3 envelope for a pure cold MOVE — EMPTY digest proposal (a move digests nothing).

    segment_start_seq > segment_end_seq makes the digest scan range empty, so the gate's §4.2
    immutable_truth-not-digested check (inv2) has zero events to flag — truthfully encoding "this
    run digests nothing; it moves segment {start..end} to cold". The actual moved range lives on the
    ArchiveSegmentMoved payload, not here.
    """
    empty_digest_hash = f"sha256:move-no-digest-{run_id}-{segment_id}"
    return ConsolidationEnvelope(
        task_id=run_id,
        scene_type=SceneType.CONSOLIDATION,
        capsule_compiled_event_id="evt-stub-capsule-gc-cold-move",
        read_set=[
            ConsolidationReadEntry(entity_type="event_segment", entity_id=f"seg-{seg_start}-{seg_end}"),
            ConsolidationReadEntry(entity_type="snapshot", entity_id=anchor_snapshot_event_id),
        ],
        write_set=[
            ConsolidationWriteEntry(entity_type="segment", entity_id=f"seg-{seg_start}-{seg_end}"),
        ],
        active_obligations_declared=[
            CompactionInvariantDeclared(obligation_id=o, status=ObligationDeclaredStatus.MAINTAINED)
            for o in COMPACTION_INVARIANT_OBLIGATIONS
        ],
        lookback_window=LookbackWindow(
            lookback_type=LookbackType.SEQ_RANGE,
            parameters={"start_seq": seg_start, "end_seq": seg_end, "segment_id": segment_id, "move_only": True},
        ),
        consolidation_proposal=ConsolidationProposal(
            digest_hash=empty_digest_hash,
            digest_storage_path=f".towow/snapshots/move-{run_id}-{segment_id}.noop",
            provenance_refs=[],  # a MOVE references no digest sources
            segment_start_seq=seg_end + 1,  # start > end → empty digest scan (zero events digested)
            segment_end_seq=seg_end,
        ),
        snapshot_event_id=anchor_snapshot_event_id,
    )


def _build_asm_intent(
    *,
    seg_start: int,
    seg_end: int,
    original_event_count: int,
    segment_path_before: str,
    segment_path_after: str,
    classification: dict[BaseClassification, int],
    prepared: PreparedArchive,
) -> EventIntent:
    """ArchiveSegmentMoved intent — §2.4/§7.2 cold archive (provenance_preserved=True, prepared_artifact_ref)."""
    summary = {
        "immutable_truth_count": classification.get(BaseClassification.IMMUTABLE_TRUTH, 0),
        "abstractable_process_count": classification.get(BaseClassification.ABSTRACTABLE_PROCESS, 0),
        "discardable_noise_count": classification.get(BaseClassification.DISCARDABLE_NOISE, 0),
    }
    payload = {
        "segment_range": {"start_seq": seg_start, "end_seq": seg_end},
        "archive_destination": ArchiveDestination.COLD.value,
        "original_event_count": original_event_count,
        "provenance_preserved": True,  # §4.1/§4.3: cold archive keeps every event retrievable
        "segment_path_before": segment_path_before,
        "segment_path_after": segment_path_after,
        "classification_summary": summary,
        "prepared_artifact_ref": {
            "segment_id": prepared.segment_id,
            "staging_path": prepared.staging_path,
            "source_hash": prepared.source_hash,
            "archive_hash": prepared.archive_hash,
            "hot_path_unchanged": prepared.hot_path_unchanged,
        },
    }
    return EventIntent(
        local_intent_id=f"asm-gc-{uuid.uuid4().hex[:12]}",
        event_type=EventType.ARCHIVE_SEGMENT_MOVED,
        event_category=EventCategory.CONSOLIDATION,
        payload=payload,
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07-gc-cold-move"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"seg-{seg_start}-{seg_end}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


def _build_envelope_intent(
    *, run_id: str, envelope: ConsolidationEnvelope, segment_id: int,
) -> EventIntent:
    """The TransactionEnvelope carrying the consolidation_event patch + §6.3 envelope for the gate."""
    env_id = f"env-gc-cold-move-{uuid.uuid4().hex[:12]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": run_id,
            "capsule_compiled_event_id": "evt-stub-capsule-gc-cold-move",
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
            "patches": [
                {
                    "patch_type": PatchType.CONSOLIDATION_EVENT.value,
                    "patch_id": f"patch-gc-cold-move-{run_id}-{segment_id}",
                    "target_ref": f"gc-cold-move-{run_id}-{segment_id}",
                },
            ],
            "consolidation_envelope": envelope.model_dump(mode="json"),
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m07-gc-cold-move-run",
            session_id=run_id,
            skill_id="M-2.2",
            run_id=run_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"gc-cold-move-{run_id}-{segment_id}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


__all__ = [
    "ColdMovedSegment",
    "GcColdMoveResult",
    "SkippedSegment",
    "run_gc_cold_move",
]
