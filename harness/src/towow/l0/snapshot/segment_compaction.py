"""M-0.7 v3.6+ 方案 B (DESIGN-M07-v3.6-live-log-shrink.md) — 段内细粒度 compaction (candidate 乙)
+ index 跟随瘦身 (candidate 丁-1).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §4.4 (v3.6+ 方案 B 占位句)
#   01-reconciliation/DESIGN-M07-v3.6-live-log-shrink.md §3.1 / §4.2 (落地设计) / §4.4 (灰度/回滚)
#
# WHY (the live log卡死 the segment-level leg can't fix): the段级 cold-move leg (gc_cold_move.py) is
# "全有或全无" — a sealed segment with even ONE GC-reachable (live-referenced) event is withheld
# whole, so the live log's two sealed segments (each pinned by ~15 scattered Nature judgments /
# unclosed findings / supersede refs) shrink 0. §4.4 names the fix 方案 B and defers it to v3.6+.
#
# WHAT candidate 乙 does (per-sealed-segment, the one new destructive primitive):
#   pinned   = events still GC-reachable (must stay hot — exactly the §3.1 semantic filter)
#   movable  = the rest (noise + GC-unreachable immutable_truth/abstractable — the §4.1 cold-bound set)
#   1. compute the live head bundle_hash BEFORE (the reconstructability target).
#   2. the WHOLE segment goes verbatim to cold via the §7.2 three-phase prepare→gate→cold (reusing
#      gc_cold_move's already-gate-trodden empty-digest ConsolidationEnvelope + ArchiveSegmentMoved).
#      真相零丢失: every event — including the movable ones — is byte-for-byte in cold/archive-N.jsonl.gz
#      and retrievable via get_event (cold lookup).
#   3. the ONLY new破坏性 action: rewrite the hot segment file to a "thin segment" carrying just the
#      pinned events (atomic tmp→fsync→replace). This is what makes hot shrink 99.8% (31 pinned vs
#      14014). The pinned events keep their record_hash/seq verbatim, so replay is unchanged.
#   4. candidate 丁-1: prune the movable events' full records from event_id.idx (they are verbatim in
#      cold now; event_id.idx is a derived cache — §7.2 "索引不是事实源"). This shrinks the 43MB index.
#   5. recompute the head bundle_hash AFTER (rebuild projections from the COMPACTED log). It must equal
#      BEFORE — a 100% full check (stronger than §6.4 inv1's 5% sampling, because this is a破坏性 rewrite).
#      Mismatch → fail-closed (the move is aborted/rolled back; cold still holds everything verbatim).
#
# RECONSTRUCTABILITY (3 道, DESIGN §4.2): (1) 真相零丢失 — whole segment verbatim in cold first;
# (2) 状态可重建 — anchor snapshot before any destruction + pinned events' record_hash/seq verbatim +
# 全量 bundle_hash before==after强制校验; (3) 比 inv1 更严 — 100% not 5%.
#
# CRASH RECOVERY: the compaction ArchiveSegmentMoved carries rewritten_hot_segment=true so its终态
# ("cold present + hot thin segment present") is NOT mistaken by recover_after_crash for "crash before
# unlink" (which would delete the thin segment). recover_compaction_after_crash redoes the idempotent
# tail (rename staging→cold if needed → write thin segment from cold → index movable → prune) for any
# compaction move whose finalize did not complete.
#
# GRAY / ROLLBACK (DESIGN §4.4): TOWOW_SEGMENT_COMPACTION=1 gates real runs (default off — `towow
# compact` refuses, touches nothing). --dry-run predicts before/after with zero byte change.
# uncompact_segment is the byte-level inverse (cold is verbatim): decompress the cold archive back to
# the hot segment, drop the cold index entries, restore the full records to event_id.idx.
#
# DAEMON-INDEPENDENT (§8/§12 否决 daemon 化 + owner 后台全自动红线): a single-shot driver behind a
# manual CLI. It does NOT schedule "when to compact". First live run is owner-gated (破坏性 + 动真相源).
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.event_log.index import EventLogIndexStore
from towow.l0.event_log.segments import (
    hot_dir_for,
    ordered_hot_segments,
    segment_number_of,
)
from towow.l0.snapshot.cold_archive import (
    prepare_archive_to_cold,
    record_finalize_completed,
    rollback_prepared_archive,
    verify_prepared_archive,
)
from towow.l0.snapshot.cold_storage import (
    cold_archive_path,
    cold_dir,
    cold_index_path,
    load_cold_index,
    update_cold_index,
)
from towow.l0.snapshot.gc import compute_archive_seq_bound
from towow.l0.snapshot.gc_cold_move import (
    _build_asm_intent,
    _build_cold_move_envelope,
    _build_envelope_intent,
    _persist_gc_state,
    _segment_records,
)
from towow.l0.snapshot.reachability import compute_gc_reachable, compute_gc_roots
from towow.l0.snapshot.segment_align import align_segment_archive
from towow.l0.snapshot.snapshot import create_standalone_snapshot
from towow.schemas.enums import EventType, SnapshotType

if TYPE_CHECKING:
    from pathlib import Path

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.event_log.lease import RetentionLeaseStore
    from towow.l0.projection.projection import ProjectionStore
    from towow.l0.snapshot.cold_archive import PreparedArchive
    from towow.schemas.event_record import EventRecord

# DESIGN §4.4 — gray switch (default off). When unset/0, `towow compact` refuses + touches nothing.
SEGMENT_COMPACTION_ENV = "TOWOW_SEGMENT_COMPACTION"


def segment_compaction_enabled() -> bool:
    """True iff TOWOW_SEGMENT_COMPACTION is a truthy value (1/true/yes/on). Default off (§4.4 gray)."""
    return os.environ.get(SEGMENT_COMPACTION_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


class SegmentCompactionDisabledError(RuntimeError):
    """Raised when run_segment_compaction is called with the gray switch off (fail-closed, §4.4)."""


class ReconstructabilityClosureError(RuntimeError):
    """DESIGN §4.2 道 3 — the post-compaction head bundle_hash != pre-compaction. The destructive
    rewrite would have lost projection state → fail-closed (cold still holds everything verbatim)."""


@dataclass(frozen=True)
class SegmentCompactionPlan:
    """What a dry-run predicts for one sealed segment (DESIGN §4.4 --dry-run: zero byte change)."""

    segment_id: int
    start_seq: int
    end_seq: int
    total_events: int
    pinned_count: int
    movable_count: int
    movable_noise_count: int  # DISCARD-class movable (would not even need cold, but kept verbatim)
    movable_cold_count: int  # immutable_truth + abstractable movable (the §4.1 cold-bound set)
    hot_bytes_before: int
    hot_bytes_after_estimate: int  # thin segment serialized size estimate (pinned events only)
    skip_reason: str | None = None


@dataclass(frozen=True)
class CompactedSegment:
    """One sealed segment this run compacted: whole→cold + thin (pinned) written back to hot."""

    segment_id: int
    start_seq: int
    end_seq: int
    total_events: int
    pinned_count: int
    movable_count: int
    archive_segment_event_id: str
    hot_bytes_before: int
    hot_bytes_after: int
    index_records_pruned: int


@dataclass(frozen=True)
class SegmentCompactionResult:
    """Outcome of run_segment_compaction (or a dry-run)."""

    dry_run: bool
    archive_seq_bound: int
    anchor_snapshot_event_id: str | None
    anchor_cutoff_seq: int
    plans: list[SegmentCompactionPlan] = field(default_factory=list)  # dry-run predictions / per-seg
    compacted: list[CompactedSegment] = field(default_factory=list)
    head_bundle_hash_before: str | None = None
    head_bundle_hash_after: str | None = None

    @property
    def hot_bytes_reclaimed(self) -> int:
        return sum(c.hot_bytes_before - c.hot_bytes_after for c in self.compacted)

    @property
    def index_records_pruned(self) -> int:
        return sum(c.index_records_pruned for c in self.compacted)


# ─── helpers ────────────────────────────────────────────────────────────────────


def _live_head_bundle_hash(event_log: EventLog, projection_store: ProjectionStore, towow_dir: Path) -> str:
    """The reconstructability target: catch projections up to head, then hash the materialized bundle.

    Uses the EXACT same recipe create_standalone_snapshot uses for bundle_hash (sha256 of the sorted
    {projection_name: sha256(file_bytes)} map over .towow/graph/*.json), so "before == after" means
    the destructive rewrite left every projection's materialized state byte-identical.
    """
    import hashlib

    projection_store.catchup(event_log)
    graph_dir = towow_dir / "graph"
    file_hashes: dict[str, str] = {}
    for proj_file in sorted(graph_dir.glob("*.json")):
        file_hashes[proj_file.stem] = "sha256:" + hashlib.sha256(proj_file.read_bytes()).hexdigest()
    return "sha256:" + hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _recompute_head_bundle_hash_from_anchor_replay(
    event_log: EventLog, towow_dir: Path, anchor_cutoff_seq: int,
) -> str:
    """Independent reconstructability recompute (the §6.5 / gc_cold_move-proven method): restore the
    anchor snapshot (taken at the pre-compaction head) into a SCRATCH store, then replay the events
    AFTER its cutoff, and hash the resulting bundle the same way.

    Why restore-anchor+replay, NOT a plain rebuild: a plain rebuild reads only the HOT segments — it
    cannot see the events compaction moved to cold (they are gone from hot). But those moved events
    all sit at seq ≤ anchor cutoff (sealed segments are older than the head), so their contribution to
    projection state is already BAKED INTO the anchor bundle. Restoring the anchor recovers that state
    without reading the moved events; replaying events > cutoff (the compaction's own SnapshotCreated /
    CommitAccepted / ArchiveSegmentMoved, all still in hot) reconstructs the rest. The result must
    equal the pre-compaction live head — a matching hash proves no archived event's state contribution
    was lost when its bytes left hot (DESIGN §4.2 道 2). This is exactly the closure gc_cold_move's
    integration test asserts; here it is run IN-LINE as a 100% mandatory check (DESIGN §4.2 道 3).
    """
    import hashlib
    import tempfile

    from towow.l0.projection.projection import ProjectionStore
    from towow.l0.snapshot.snapshot import restore_from_snapshot

    with tempfile.TemporaryDirectory(prefix="m07-compact-recon-") as scratch:
        from pathlib import Path as _P

        scratch_store = ProjectionStore(_P(scratch))
        restore_from_snapshot(
            event_log=event_log, projection_store=scratch_store, towow_dir=towow_dir,
            snapshot_dir=towow_dir / "snapshots" / f"snapshot-{anchor_cutoff_seq}",
        )
        file_hashes: dict[str, str] = {}
        for proj_file in sorted(_P(scratch).glob("*.json")):
            file_hashes[proj_file.stem] = "sha256:" + hashlib.sha256(proj_file.read_bytes()).hexdigest()
    return "sha256:" + hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _write_thin_segment(seg_path: Path, pinned: list[EventRecord]) -> int:
    """Atomically replace the hot segment file with a thin segment carrying ONLY the pinned events.

    Each pinned record is re-serialized via model_dump_json (record_hash/seq preserved verbatim →
    replay-identical). Written to tmp + fsync + os.replace over the hot path so a crash never leaves a
    half-written hot segment (either the old full segment or the complete thin one). Returns the new
    byte size. If pinned is empty the segment becomes a 0-line file (the seg号 stays — its events are
    all in cold; ordered_hot_segments tolerates an empty numbered segment).
    """
    body = "".join(r.model_dump_json() + "\n" for r in pinned)
    tmp = seg_path.with_suffix(seg_path.suffix + ".thin.tmp")
    tmp.write_text(body, encoding="utf-8")
    fd = os.open(str(tmp), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    tmp.replace(seg_path)
    dir_fd = os.open(str(seg_path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return seg_path.stat().st_size


def _cold_records_for_segment(towow_dir: Path, seg_id: int) -> list[EventRecord]:
    """Decompress cold/archive-{seg}.jsonl.gz back to EventRecords (verbatim, field-identical)."""
    from towow.schemas.event_record import EventRecord as _ER

    cold_path = cold_archive_path(towow_dir, seg_id)
    out: list[_ER] = []
    if not cold_path.exists():
        return out
    with gzip.open(cold_path, "rb") as f:
        for raw in f:
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            out.append(_ER.model_validate_json(line))
    return out


def _split_pinned_movable(
    records: list[EventRecord], gc_reachable: set[str],
) -> tuple[list[EventRecord], list[EventRecord]]:
    """Split a segment's records into (pinned, movable) with PATH-A BATCH CLOSURE.

    The naive split (pinned = GC-reachable, movable = the rest) breaks the committed-visibility rule
    (M-0.1 Patch2 §2.1): path-A domain events are only visible once their trailing CommitAccepted/
    CommitRejected sentinel is observed (_iter_committed_records buffers by batch_id until the
    sentinel). If a batch's sentinel were moved to cold while a pinned domain event of the SAME batch
    stayed hot, that pinned event would be buffered forever and never yielded — get_event would return
    None (真相 invisible). So we close the pinned set under batch_id: if ANY event of a path-A batch is
    GC-reachable, the WHOLE batch stays hot (a batch never straddles a segment boundary — the rotation
    invariant — so the whole batch is in this segment). Path-B records (batch_id=None) are independent:
    each is pinned iff GC-reachable. This keeps every kept path-A event visible (its sentinel is hot too)
    and is conservative (keeps more hot, never moves a still-needed event out).
    """
    pinned_batches: set[str] = set()
    for r in records:
        if r.batch_id is not None and r.event_id in gc_reachable:
            pinned_batches.add(r.batch_id)
    pinned: list[EventRecord] = []
    movable: list[EventRecord] = []
    for r in records:
        is_pinned = (
            (r.batch_id is not None and r.batch_id in pinned_batches)
            or (r.batch_id is None and r.event_id in gc_reachable)
        )
        (pinned if is_pinned else movable).append(r)
    return pinned, movable


# ─── dry-run planning (DESIGN §4.4 — zero byte change) ────────────────────────────


def _plan_segment(
    event_log: EventLog,
    seg_path: Path,
    seg_id: int,
    *,
    bound: int,
    gc_reachable: set[str],
) -> SegmentCompactionPlan | None:
    records = _segment_records(event_log, seg_id)
    if not records:
        return None
    seg_start = min(r.sequence_number for r in records)
    seg_end = max(r.sequence_number for r in records)
    hot_bytes = seg_path.stat().st_size if seg_path.exists() else 0

    above_bound = [r for r in records if r.sequence_number > bound]
    if above_bound:
        return SegmentCompactionPlan(
            segment_id=seg_id, start_seq=seg_start, end_seq=seg_end, total_events=len(records),
            pinned_count=len(records), movable_count=0, movable_noise_count=0, movable_cold_count=0,
            hot_bytes_before=hot_bytes, hot_bytes_after_estimate=hot_bytes,
            skip_reason=(
                f"{len(above_bound)} event(s) above archive_seq_bound={bound} — §3.1 ordering filter "
                "withholds (not yet durably consumed)"
            ),
        )

    pinned, movable = _split_pinned_movable(records, gc_reachable)
    if not movable:
        return SegmentCompactionPlan(
            segment_id=seg_id, start_seq=seg_start, end_seq=seg_end, total_events=len(records),
            pinned_count=len(pinned), movable_count=0, movable_noise_count=0, movable_cold_count=0,
            hot_bytes_before=hot_bytes, hot_bytes_after_estimate=hot_bytes,
            skip_reason="every event in this segment is pinned (GC-reachable) — nothing to compact",
        )
    from towow.schemas.enums import BaseClassification

    movable_noise = sum(1 for r in movable if r.base_classification is BaseClassification.DISCARDABLE_NOISE)
    movable_cold = len(movable) - movable_noise
    thin_estimate = sum(len(r.model_dump_json()) + 1 for r in pinned)
    return SegmentCompactionPlan(
        segment_id=seg_id, start_seq=seg_start, end_seq=seg_end, total_events=len(records),
        pinned_count=len(pinned), movable_count=len(movable),
        movable_noise_count=movable_noise, movable_cold_count=movable_cold,
        hot_bytes_before=hot_bytes, hot_bytes_after_estimate=thin_estimate,
    )


# ─── the driver ───────────────────────────────────────────────────────────────────


def run_segment_compaction(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    run_id: str,
    lease_store: RetentionLeaseStore | None = None,
    max_segments: int | None = None,
    dry_run: bool = False,
) -> SegmentCompactionResult:
    """M-0.7 v3.6+ candidate 乙 + 丁-1 — compact sealed segments to pinned-only thin segments.

    dry_run=True predicts before/after for every sealed segment and touches ZERO bytes (no anchor
    snapshot, no cold write, no hot rewrite, no index change — DESIGN §4.4 --dry-run). dry_run=False
    requires the gray switch on (TOWOW_SEGMENT_COMPACTION) and performs the destructive compaction.

    Reuses gc_cold_move's gate-trodden零件 (anchor snapshot / archive_seq_bound / GC closure / empty-
    digest envelope + ArchiveSegmentMoved / §7.2 prepare-verify); the only NEW physical actions are
    the thin-segment write-back + the event_id.idx prune + the 100% bundle_hash closure check.
    """
    if not dry_run and not segment_compaction_enabled():
        msg = (
            f"segment compaction is gray-gated off — set {SEGMENT_COMPACTION_ENV}=1 to enable "
            "(or use dry_run=True to predict without touching bytes). DESIGN §4.4."
        )
        raise SegmentCompactionDisabledError(msg)

    projection_store.catchup(event_log)

    # ── dry-run: predict only, no anchor snapshot, no byte change ──
    if dry_run:
        # The real run takes a reconstructability anchor snapshot FIRST, which lifts hot_replay_floor
        # from 0 to head — so the EFFECTIVE post-anchor bound is the consumer_watermark (the floor the
        # anchor establishes is ≥ consumer_watermark once projections are caught up). Predict against
        # that effective bound (NOT the pre-anchor bound, which is 0 with no snapshot — would mis-
        # report everything as "above bound"). No snapshot is actually taken (zero byte change).
        _pre_bound, consumer_watermark, _hf = compute_archive_seq_bound(
            event_log, projection_store, lease_store,
        )
        bound = consumer_watermark
        gc_reachable = compute_gc_reachable(
            event_log, compute_gc_roots(event_log, projection_store, lease_store),
        )
        plans: list[SegmentCompactionPlan] = []
        for seg_path in ordered_hot_segments(event_log.log_path)[:-1]:
            sid = segment_number_of(seg_path)
            if sid is None:  # pragma: no cover - ordered_hot_segments only returns numbered segments
                continue
            plan = _plan_segment(event_log, seg_path, sid, bound=bound, gc_reachable=gc_reachable)
            if plan is not None:
                plans.append(plan)
        return SegmentCompactionResult(
            dry_run=True, archive_seq_bound=bound,
            anchor_snapshot_event_id=None, anchor_cutoff_seq=0, plans=plans,
        )

    # ── real run ──
    # DESIGN §4.2 道 2: anchor snapshot at head BEFORE any destruction — it captures the to-be-moved
    # (≤ cutoff) events' effects into its bundle, so reconstruction stays closed after their bytes
    # leave hot. The closure check below compares the CURRENT live head (post-compaction, incl. the
    # compaction's own events) against restore(anchor)+replay(>cutoff) — both reach the same head.
    snap = create_standalone_snapshot(
        event_log=event_log, projection_store=projection_store,
        towow_dir=towow_dir, snapshot_type=SnapshotType.CONSOLIDATION_ANCHOR,
    )

    bound, consumer_watermark, hot_replay_floor = compute_archive_seq_bound(
        event_log, projection_store, lease_store,
    )
    gc_reachable = compute_gc_reachable(
        event_log, compute_gc_roots(event_log, projection_store, lease_store),
    )

    compacted: list[CompactedSegment] = []
    plans = []
    if bound <= 0:
        _persist_gc_state(towow_dir, bound, consumer_watermark, hot_replay_floor)
        return SegmentCompactionResult(
            dry_run=False, archive_seq_bound=bound,
            anchor_snapshot_event_id=snap.snapshot_event_id, anchor_cutoff_seq=snap.cutoff_seq,
        )

    sealed = ordered_hot_segments(event_log.log_path)[:-1]
    done = 0
    for seg_path in sealed:
        if max_segments is not None and done >= max_segments:
            break
        sid = segment_number_of(seg_path)
        if sid is None:  # pragma: no cover - ordered_hot_segments only returns numbered segments
            continue
        plan = _plan_segment(event_log, seg_path, sid, bound=bound, gc_reachable=gc_reachable)
        if plan is None:
            continue
        plans.append(plan)
        if plan.skip_reason is not None:
            continue
        result = _compact_one_segment(
            event_log=event_log, towow_dir=towow_dir, seg_path=seg_path, seg_id=sid,
            gc_reachable=gc_reachable, anchor_snapshot_event_id=snap.snapshot_event_id, run_id=run_id,
        )
        if result is not None:
            compacted.append(result)
            done += 1

    _persist_gc_state(towow_dir, bound, consumer_watermark, hot_replay_floor)

    # DESIGN §4.2 道 3 — 100% reconstructability closure check (stronger than §6.4 inv1's 5% sampling,
    # because compaction is a破坏性 rewrite). The CURRENT live head state (caught up over the compacted
    # log, including compaction's own events) must equal restore(anchor)+replay(>cutoff). Both reach
    # the same head; a mismatch means a moved event's projection contribution was lost → fail-closed.
    head_bundle_hash_before = _live_head_bundle_hash(event_log, projection_store, towow_dir)
    head_bundle_hash_after = _recompute_head_bundle_hash_from_anchor_replay(
        event_log, towow_dir, snap.cutoff_seq,
    )
    if compacted and head_bundle_hash_after != head_bundle_hash_before:
        msg = (
            "reconstructability closure FAILED after segment compaction: restore(anchor)+replay "
            f"{head_bundle_hash_after} != live head {head_bundle_hash_before}. A destructive rewrite "
            "lost projection state — fail-closed (cold holds every event verbatim; uncompact the "
            f"affected segment(s) {[c.segment_id for c in compacted]} to restore hot)."
        )
        raise ReconstructabilityClosureError(msg)

    return SegmentCompactionResult(
        dry_run=False, archive_seq_bound=bound,
        anchor_snapshot_event_id=snap.snapshot_event_id, anchor_cutoff_seq=snap.cutoff_seq,
        plans=plans, compacted=compacted,
        head_bundle_hash_before=head_bundle_hash_before,
        head_bundle_hash_after=head_bundle_hash_after,
    )


def _compact_one_segment(
    *,
    event_log: EventLog,
    towow_dir: Path,
    seg_path: Path,
    seg_id: int,
    gc_reachable: set[str],
    anchor_snapshot_event_id: str,
    run_id: str,
) -> CompactedSegment | None:
    """Compact ONE sealed segment: whole→cold (verbatim, gate) → thin (pinned) written back → index
    movable→cold + prune event_id.idx. None on gate reject (staging rolled back, hot untouched).
    """
    from towow.l0.commit_gate import CommitGate

    records = _segment_records(event_log, seg_id)
    seg_start = min(r.sequence_number for r in records)
    seg_end = max(r.sequence_number for r in records)
    hot_bytes_before = seg_path.stat().st_size
    pinned, movable = _split_pinned_movable(records, gc_reachable)
    movable_ids = [r.event_id for r in movable]
    pinned_ids = [r.event_id for r in pinned]
    decision = align_segment_archive(event_log, start_seq=seg_start, end_seq=seg_end)

    # ── §7.2 prepare: whole segment verbatim → staging cold tmp (hot/index untouched) ──
    prepared = prepare_archive_to_cold(towow_dir, seg_path)

    # ── commit gate: empty-digest envelope + ArchiveSegmentMoved(rewritten_hot_segment=true) ──
    envelope = _build_cold_move_envelope(
        run_id=run_id, seg_start=seg_start, seg_end=seg_end,
        anchor_snapshot_event_id=anchor_snapshot_event_id, segment_id=seg_id,
    )
    base_asm = _build_asm_intent(
        seg_start=seg_start, seg_end=seg_end, original_event_count=len(records),
        segment_path_before=str(seg_path), segment_path_after=str(cold_archive_path(towow_dir, seg_id)),
        classification=decision.classification_summary, prepared=prepared,
    )
    # DESIGN §4.2 — enrich the ASM payload with the compaction終態 flags (crash recovery + audit).
    # EventIntent is frozen → build a fresh payload + model_copy (don't mutate in place).
    asm_intent = base_asm.model_copy(
        update={
            "payload": {
                **base_asm.payload,
                "rewritten_hot_segment": True,
                "compacted_retained_event_ids": pinned_ids,
            },
        },
    )
    envelope_intent = _build_envelope_intent(run_id=run_id, envelope=envelope, segment_id=seg_id)

    gate = CommitGate(event_log)
    commit_outcome = gate.attempt_commit(envelope_intent, [asm_intent])
    if not isinstance(commit_outcome, list):  # pragma: no cover - audit_blocking never set here
        msg = "attempt_commit returned a non-list outcome without audit_blocking"
        raise TypeError(msg)
    if commit_outcome[-1].event_type is not EventType.COMMIT_ACCEPTED:
        rollback_prepared_archive(towow_dir, prepared)
        return None
    asm_id = next(
        (r.event_id for r in commit_outcome if r.event_type is EventType.ARCHIVE_SEGMENT_MOVED),
        None,
    )

    # finalize boundary re-verify (a staging/hot tamper between prepare and accept fails closed).
    if verify_prepared_archive(prepared) is not None:  # pragma: no cover - synchronous immutable seg
        rollback_prepared_archive(towow_dir, prepared)
        return None

    # ── the destructive finalize sequence (crash-safe order, mirrored by recovery) ──
    # 1. rename staging → cold (whole segment now verbatim in cold; every event retrievable).
    _rename_staging_to_cold(towow_dir, prepared)
    # 2. route the MOVABLE events to cold (pinned events stay served from the hot thin segment).
    update_cold_index(towow_dir, event_ids=movable_ids, segment_id=seg_id)
    # 3. NEW破坏性 action: rewrite hot to the thin (pinned-only) segment.
    hot_bytes_after = _write_thin_segment(seg_path, pinned)
    # 4. candidate 丁-1: prune the movable events' full records from event_id.idx (verbatim in cold).
    pruned = EventLogIndexStore(towow_dir).prune_event_ids(set(movable_ids))
    # 5. record finalize completed (idempotency marker for crash recovery).
    record_finalize_completed(towow_dir, prepared)
    # invalidate the EventLog's in-memory point-lookup index so the next read rebuilds over the thin
    # hot segment + cold delegation (without this, a moved event would still resolve from the stale
    # in-memory index instead of cold — the index must reflect the post-compaction hot reality).
    event_log._index = None

    return CompactedSegment(
        segment_id=seg_id, start_seq=seg_start, end_seq=seg_end, total_events=len(records),
        pinned_count=len(pinned), movable_count=len(movable), archive_segment_event_id=asm_id or "",
        hot_bytes_before=hot_bytes_before, hot_bytes_after=hot_bytes_after, index_records_pruned=pruned,
    )


def _rename_staging_to_cold(towow_dir: Path, prepared: PreparedArchive) -> None:
    """Atomic rename staging→cold (idempotent: skip if cold already present, drop orphan staging)."""
    from pathlib import Path as _P

    from towow.l0.projection.projection import os_replace_with_fsync

    cold_path = cold_archive_path(towow_dir, prepared.segment_id)
    staging = _P(prepared.staging_path)
    if staging.exists() and not cold_path.exists():
        cold_dir(towow_dir).mkdir(parents=True, exist_ok=True)
        os_replace_with_fsync(staging, cold_path, cold_dir(towow_dir))
    elif staging.exists() and cold_path.exists():
        staging.unlink()


# ─── crash recovery (DESIGN §4.2 — compaction終態 branch) ──────────────────────────


@dataclass(frozen=True)
class CompactionRecoveryReport:
    """Outcome of recover_compaction_after_crash."""

    recovered_segments: list[int]
    findings: list[str]


def recover_compaction_after_crash(towow_dir: Path, event_log: EventLog) -> CompactionRecoveryReport:
    """DESIGN §4.2 — redo the idempotent finalize tail for any compaction move whose finalize did not
    complete. Recognizes the compaction ArchiveSegmentMoved (rewritten_hot_segment=true) and brings
    physical state to its終态: cold archive present + hot thin segment (pinned only) + movable routed
    to cold + event_id.idx pruned.

    Recoverable interrupted states (cold is the verbatim source of truth for the thin segment):
      - staging present, no cold      → rename staging→cold, then thin+index+prune (continue from rename).
      - cold present, hot = full seg  → write thin from cold, index movable, prune (continue from thin).
      - cold present, hot = thin      → index+prune are idempotent; record completed (already done).
      - neither staging nor cold      → cannot recover (refuse to fabricate) → finding.

    Reads only accepted ArchiveSegmentMoved events; writes zero events.
    """
    accepted = event_log.get_events_by_type(EventType.ARCHIVE_SEGMENT_MOVED)
    recovered: list[int] = []
    findings: list[str] = []
    for rec in accepted:
        if rec.payload.get("rewritten_hot_segment") is not True:
            continue
        prepared = _prepared_from_asm_payload(rec.payload)
        if prepared is None:
            continue
        pinned_ids = [e for e in (rec.payload.get("compacted_retained_event_ids") or []) if isinstance(e, str)]
        seg_id = prepared.segment_id
        seg_path = hot_dir_for(event_log.log_path) / f"events-{seg_id:06d}.jsonl"
        cold_path = cold_archive_path(towow_dir, seg_id)
        from pathlib import Path as _P

        staging = _P(prepared.staging_path)

        from towow.l0.snapshot.cold_archive import is_finalize_completed

        # finalize ran to completion AND the thin segment is already present + correct → nothing to do.
        # (If finalize-log says done but hot is not the thin segment — a torn state — fall through and
        # rebuild the thin segment from cold below.)
        if (
            is_finalize_completed(towow_dir, prepared)
            and seg_path.exists()
            and _segment_is_thin(seg_path, pinned_ids)
        ):
            continue

        # bring cold into existence if only staging survived.
        if staging.exists() and not cold_path.exists():
            _rename_staging_to_cold(towow_dir, prepared)
        if not cold_path.exists():
            findings.append(
                f"seg {seg_id}: compaction ArchiveSegmentMoved but no cold archive nor staging survives "
                "— cannot reconstruct the thin segment (refuse to fabricate)",
            )
            continue

        cold_records = _cold_records_for_segment(towow_dir, seg_id)
        if not cold_records:
            findings.append(f"seg {seg_id}: cold archive empty/corrupt — cannot reconstruct thin segment")
            continue
        pinned_set = set(pinned_ids)
        pinned_records = [r for r in cold_records if r.event_id in pinned_set]
        movable_ids = [r.event_id for r in cold_records if r.event_id not in pinned_set]
        # write the thin segment (idempotent — same pinned set → same bytes), route movable to cold,
        # prune event_id.idx (both idempotent), then record completed.
        _write_thin_segment(seg_path, pinned_records)
        update_cold_index(towow_dir, event_ids=movable_ids, segment_id=seg_id)
        EventLogIndexStore(towow_dir).prune_event_ids(set(movable_ids))
        record_finalize_completed(towow_dir, prepared)
        recovered.append(seg_id)

    event_log._index = None
    return CompactionRecoveryReport(recovered_segments=recovered, findings=findings)


def _segment_is_thin(seg_path: Path, pinned_ids: list[str]) -> bool:
    """True iff the hot segment file already contains exactly the pinned event_ids (the thin state)."""
    present: set[str] = set()
    for ln in seg_path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            present.add(json.loads(ln)["event_id"])
    return present == set(pinned_ids)


def _prepared_from_asm_payload(payload: dict[str, object]) -> PreparedArchive | None:
    """Reconstruct a PreparedArchive from an ArchiveSegmentMoved.prepared_artifact_ref (recovery)."""
    from towow.l0.snapshot.cold_archive import _prepared_from_payload

    return _prepared_from_payload(payload)


# ─── uncompact (DESIGN §4.4 回滚安全网 — byte-level inverse) ─────────────────────────


@dataclass(frozen=True)
class UncompactResult:
    """Outcome of uncompact_segment."""

    segment_id: int
    restored_event_count: int
    hot_bytes_after: int
    index_records_restored: int
    cold_index_entries_removed: int


class UncompactError(RuntimeError):
    """uncompact_segment cannot proceed (no cold archive to restore from, etc.) — fail-closed."""


def uncompact_segment(
    towow_dir: Path, event_log: EventLog, seg_id: int,
) -> UncompactResult:
    """DESIGN §4.4 — byte-level inverse of compaction (cold is verbatim, so it is fully reversible).

    Decompress cold/archive-{seg}.jsonl.gz back to the hot segment file (every event returns to hot
    verbatim), drop that segment's entries from the cold routing index, and restore the full records
    to event_id.idx. After this the segment is exactly as it was before compaction (the cold archive
    .gz is left in place as the verbatim backup — re-running compaction is safe). Fail-closed if there
    is no cold archive to restore from.
    """
    cold_path = cold_archive_path(towow_dir, seg_id)
    if not cold_path.exists():
        msg = f"uncompact seg {seg_id}: no cold archive {cold_path.name} to restore from (fail-closed)"
        raise UncompactError(msg)
    cold_records = _cold_records_for_segment(towow_dir, seg_id)
    if not cold_records:
        msg = f"uncompact seg {seg_id}: cold archive empty/corrupt (fail-closed)"
        raise UncompactError(msg)

    seg_path = hot_dir_for(event_log.log_path) / f"events-{seg_id:06d}.jsonl"
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    # restore the FULL segment (verbatim) to hot — same write recipe as a thin segment (record_hash/
    # seq preserved), only now carrying every event.
    hot_bytes_after = _write_thin_segment(seg_path, cold_records)

    # remove this segment's entries from the cold routing index (events are back in hot now).
    index = load_cold_index(towow_dir)
    removed = 0
    for eid in list(index):
        entry = index[eid]
        if isinstance(entry, dict) and entry.get("segment") == seg_id:
            del index[eid]
            removed += 1
    _write_cold_index(towow_dir, index)

    # restore the full records to event_id.idx (rebuild the persisted index over the now-uncompacted
    # committed stream — the authoritative event files).
    store = EventLogIndexStore(towow_dir)
    event_log._index = None
    restored = store.persist(list(event_log.all_records()))
    index_records_restored = restored.total_records

    return UncompactResult(
        segment_id=seg_id, restored_event_count=len(cold_records),
        hot_bytes_after=hot_bytes_after, index_records_restored=index_records_restored,
        cold_index_entries_removed=removed,
    )


def _write_cold_index(towow_dir: Path, index: dict[str, dict[str, object]]) -> None:
    """Atomic write of the cold routing index after an uncompact removal (mirror of cold_storage)."""
    from towow.l0.projection.projection import os_replace_with_fsync

    path = cold_index_path(towow_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    os_replace_with_fsync(tmp, path, path.parent)


__all__ = [
    "SEGMENT_COMPACTION_ENV",
    "CompactedSegment",
    "CompactionRecoveryReport",
    "ReconstructabilityClosureError",
    "SegmentCompactionDisabledError",
    "SegmentCompactionPlan",
    "SegmentCompactionResult",
    "UncompactError",
    "UncompactResult",
    "recover_compaction_after_crash",
    "run_segment_compaction",
    "segment_compaction_enabled",
    "uncompact_segment",
]
