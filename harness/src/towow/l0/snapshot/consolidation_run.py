"""M-0.7 §6.1 — real consolidation RUN driver (replaces the E.2 `towow consolidate` stub).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §6.1 consolidation 复用 runtime protocol — consolidation 是普通 run, 走 commit gate; 产
#          CrossRunConsolidationCommitted + SnapshotCreated + ArchiveSegmentMoved (§6.1 batch).
#     §6.3 ConsolidationEnvelope (read_set 含"基于的 snapshot"=reconstructability 锚点, write_set 含
#          新 digest/snapshot/segment); §6.4 三 compaction invariant 由 M-0.5 commit gate 验.
#     §7.2 archive prepare → (commit gate accept) → finalize 三段式 (物理动作不先于 accepted event).
#     §5.1 Mode-2 snapshot: consolidation 的 reconstructability 锚点.
#
# WHY this is a driver, not new mechanism (§0 三层框架): 所有零件 (snapshot / cold archive 三段 /
# segment_align / verify_consolidation_invariants / commit gate consolidation_check) 都已造好 + 测过
# (M-0.7 机制层). 本模块只把它们**接成一次真 consolidation RUN** —— 选一个已封段, 取
# reconstructability 锚点 snapshot, prepare 段到 cold staging, 产真 digest, 经 commit gate 原子写
# CrossRunConsolidationCommitted + ArchiveSegmentMoved, accept 后 finalize 归档. 不发明调度 (何时
# 触发 = maintenance daemon, §8 / §12 否决 daemon 化, gated 留债), 不绕 commit gate (§0 硬约束).
#
# SNAPSHOT ANCHOR 设计 (诚实): §6.4 实现的 inv1 reconstructability 要求 envelope.snapshot_event_id
# 是一个**已提交 + 完整性可验**的 SnapshotCreated (verify_record_integrity) —— 一个同 batch 未提交的
# intent 无法被完整性校验. 所以本驱动在 work 阶段先经 Mode-1 路径真写 reconstructability 锚点
# snapshot (snapshot_type=consolidation_anchor, cutoff_seq=当前 max committed seq ≥ 被归档段 end_seq),
# 它**先于**归档 batch 持久落账; 这正是 §6.3 read_set "基于的 snapshot". 这比 "snapshot 同 batch" 更强
# (锚点先于归档 durably 存在 vs 与归档原子), 且是过 frozen inv1 的唯一不削弱路径. 归档 batch 再原子写
# CrossRunConsolidationCommitted + ArchiveSegmentMoved.
"""

from __future__ import annotations

import hashlib
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
from towow.l0.snapshot.segment_align import SegmentArchivePath, align_segment_archive
from towow.l0.snapshot.snapshot import create_standalone_snapshot
from towow.schemas.enums import (
    ActorType,
    ArchiveDestination,
    BaseClassification,
    EventCategory,
    EventType,
    LookbackType,
    ObligationDeclaredStatus,
    PatchType,
    ProvenanceRefRelevance,
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
    ConsolidationProvenanceRef,
    ConsolidationReadEntry,
    ConsolidationWriteEntry,
)

if TYPE_CHECKING:
    from pathlib import Path

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.projection.projection import ProjectionStore
    from towow.schemas.event_record import EventRecord


@dataclass(frozen=True)
class ConsolidationRunResult:
    """Outcome of run_consolidation — the real consolidation RUN's products (or why it was a no-op)."""

    accepted: bool
    skipped_reason: str | None = None
    rejection_reason: str | None = None
    consolidation_event_id: str | None = None  # CrossRunConsolidationCommitted
    archive_segment_event_id: str | None = None
    snapshot_event_id: str | None = None  # reconstructability anchor (read_set baseline)
    archived_segment_id: int | None = None
    archived_event_ids: list[str] = field(default_factory=list)
    digest_hash: str | None = None
    digest_storage_path: str | None = None
    cutoff_seq: int = 0
    segment_start_seq: int = 0
    segment_end_seq: int = 0
    classification_summary: dict[str, int] = field(default_factory=dict)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _segment_records(event_log: EventLog, seg_id: int) -> list[EventRecord]:
    """The committed records physically living in hot segment ``seg_id`` (ascending by seq).

    Routed by the on-disk segment file rather than a seq guess: a sealed hot segment's events are
    exactly the lines in events-{seg:06d}.jsonl, so the digest's segment_range + provenance_refs are
    the真实 archived set, not an approximation.
    """
    from towow.l0.event_log.segments import hot_dir_for

    seg_path = hot_dir_for(event_log._log_path) / f"events-{seg_id:06d}.jsonl"
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


def _pick_consolidatable_segment(event_log: EventLog) -> tuple[Path | None, str | None]:
    """Pick the oldest SEALED hot segment whose events are digest-consolidatable, or (None, reason).

    ``ordered_hot_segments`` is ascending; the LAST is the active segment still being appended to
    (never archive the active one — it is not sealed). Anything before it is sealed. Among sealed
    segments, the oldest one whose range carries NO immutable_truth event is chosen: §4.2 forbids
    folding immutable_truth into a digest (and §6.4 inv2 would reject it), so a digest-consolidation
    targets old abstractable/noise history. A segment that mixes in immutable_truth is skipped here
    (its conservative-cold MOVE is a GC concern, not a digest run). Returns the path + None on
    success, else (None, human reason) — used for the no-op skipped_reason.
    """
    segs = ordered_hot_segments(event_log._log_path)
    sealed = segs[:-1]  # drop the active tail
    if not sealed:
        return None, "no_sealed_segment: only the active hot segment exists — nothing to consolidate"
    skipped_immutable = 0
    for seg_path in sealed:
        seg_id = segment_number_of(seg_path)
        if seg_id is None:
            continue
        records = _segment_records(event_log, seg_id)
        if not records:
            continue
        if any(r.base_classification is BaseClassification.IMMUTABLE_TRUTH for r in records):
            skipped_immutable += 1
            continue  # §4.2 — can't digest immutable_truth; try an older-history segment instead
        return seg_path, None
    if skipped_immutable:
        return None, (
            f"no digest-consolidatable sealed segment: {skipped_immutable} sealed segment(s) carry "
            "immutable_truth (§4.2 forbids digesting it — that is a GC conservative-cold move, not a "
            "consolidation digest)"
        )
    return None, "no sealed segment has readable events to consolidate"


def run_consolidation(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    run_id: str,
) -> ConsolidationRunResult:
    """M-0.7 §6.1 — run one real consolidation: anchor snapshot + digest + cold-archive a sealed segment.

    Steps (all reusing the M-0.7 mechanism layer, never bypassing commit gate / §0):
      1. catch up projections; pick the oldest SEALED hot segment (the archivable unit). None → no-op.
      2. §4.4 align: a pure-discardable-noise segment is a §4.3 discard path, not a digest+cold
         consolidation → no-op here (a digest folding only noise carries no provenance worth keeping).
      3. take the reconstructability anchor SnapshotCreated (Mode-1 Path-B, consolidation_anchor) at
         cutoff_seq = current max committed seq (≥ segment end → §6.4 inv1 anchor is valid + committed).
      4. §7.2 prepare: copy the sealed segment to cold staging (hot/index untouched).
      5. build the real digest (segment summary + provenance_refs back to every archived event) +
         the §6.3 ConsolidationEnvelope; submit CrossRunConsolidationCommitted + ArchiveSegmentMoved
         through the commit gate (M-0.5 runs §6.4 三 invariant; reject → rollback staging, no half-truth).
      6. §7.2 finalize on accept: atomic rename staging→cold + index hot→cold + unlink hot. The
         archived events stay retrievable via get_event (cold lookup) — 归档不丢真相.
    """
    from towow.l0.commit_gate import CommitGate

    projection_store.catchup(event_log)

    target, skip_reason = _pick_consolidatable_segment(event_log)
    if target is None:
        return ConsolidationRunResult(accepted=False, skipped_reason=skip_reason)
    seg_id = segment_number_of(target)
    if seg_id is None:  # pragma: no cover - _oldest_sealed_segment only returns numbered segments
        return ConsolidationRunResult(accepted=False, skipped_reason="target segment not numbered")

    records = _segment_records(event_log, seg_id)
    if not records:
        return ConsolidationRunResult(
            accepted=False, skipped_reason=f"sealed segment {seg_id} has no readable events",
        )
    seg_start = min(r.sequence_number for r in records)
    seg_end = max(r.sequence_number for r in records)
    archived_event_ids = [r.event_id for r in records]

    decision = align_segment_archive(event_log, start_seq=seg_start, end_seq=seg_end)
    classification = {
        bc.value: n for bc, n in decision.classification_summary.items()
    }
    if decision.path is SegmentArchivePath.PURE_DISCARD:
        # §4.4: an all-discardable-noise segment is the §4.3 discard path, not a digest+cold
        # consolidation (a digest summarizing pure noise has no provenance worth retaining).
        return ConsolidationRunResult(
            accepted=False,
            skipped_reason=f"segment {seg_id} is all discardable_noise — §4.3 discard path, not consolidation",
            archived_segment_id=seg_id,
            segment_start_seq=seg_start,
            segment_end_seq=seg_end,
            classification_summary=classification,
        )

    # Step 3: reconstructability anchor snapshot (committed BEFORE the archive batch — §6.4 inv1).
    snap = create_standalone_snapshot(
        event_log=event_log,
        projection_store=projection_store,
        towow_dir=towow_dir,
        snapshot_type=SnapshotType.CONSOLIDATION_ANCHOR,
    )
    cutoff_seq = snap.cutoff_seq

    # Step 4: §7.2 prepare — copy the sealed segment to cold staging (hot/index untouched).
    prepared = prepare_archive_to_cold(towow_dir, target)

    # Step 5: real digest + §6.3 envelope.
    type_breakdown: dict[str, int] = {}
    for r in records:
        type_breakdown[r.event_type.value] = type_breakdown.get(r.event_type.value, 0) + 1
    digest_content = {
        "consolidation_run_id": run_id,
        "segment_range": {"start_seq": seg_start, "end_seq": seg_end},
        "original_event_count": len(records),
        "classification_summary": classification,
        "event_type_breakdown": type_breakdown,
        "provenance_refs": [
            {"event_id": e, "relevance": ProvenanceRefRelevance.PRIMARY.value} for e in archived_event_ids
        ],
        "anchor_snapshot_event_id": snap.snapshot_event_id,
        "summary": (
            f"consolidated {len(records)} event(s) (seq {seg_start}..{seg_end}) from hot segment "
            f"{seg_id} into cold archive; provenance retained for every folded event"
        ),
    }
    digest_text = json.dumps(digest_content, sort_keys=True, ensure_ascii=False)
    digest_hash = _sha256_text(digest_text)
    digest_storage_path = f".towow/snapshots/digest-{digest_hash[7:7 + 16]}.json"
    digest_file = towow_dir / "snapshots" / f"digest-{digest_hash[7:7 + 16]}.json"
    digest_file.parent.mkdir(parents=True, exist_ok=True)
    digest_file.write_text(json.dumps(digest_content, indent=2, ensure_ascii=False), encoding="utf-8")

    provenance_refs = [
        ConsolidationProvenanceRef(event_id=e, relevance=ProvenanceRefRelevance.PRIMARY)
        for e in archived_event_ids
    ]
    lookback = LookbackWindow(
        lookback_type=LookbackType.SEQ_RANGE,
        parameters={"start_seq": seg_start, "end_seq": seg_end, "segment_id": seg_id},
    )
    envelope = ConsolidationEnvelope(
        task_id=run_id,
        scene_type=SceneType.CONSOLIDATION,
        capsule_compiled_event_id="evt-stub-capsule-consolidation",
        read_set=[
            ConsolidationReadEntry(entity_type="event_segment", entity_id=f"seg-{seg_start}-{seg_end}"),
            ConsolidationReadEntry(entity_type="snapshot", entity_id=f"snapshot-{cutoff_seq}"),
        ],
        write_set=[
            ConsolidationWriteEntry(entity_type="digest", entity_id=f"digest-{digest_hash[7:7 + 16]}"),
            ConsolidationWriteEntry(entity_type="segment", entity_id=f"seg-{seg_start}-{seg_end}"),
        ],
        active_obligations_declared=[
            CompactionInvariantDeclared(obligation_id=o, status=ObligationDeclaredStatus.MAINTAINED)
            for o in COMPACTION_INVARIANT_OBLIGATIONS
        ],
        lookback_window=lookback,
        consolidation_proposal=ConsolidationProposal(
            digest_hash=digest_hash,
            digest_storage_path=digest_storage_path,
            provenance_refs=provenance_refs,
            segment_start_seq=seg_start,
            segment_end_seq=seg_end,
        ),
        snapshot_event_id=snap.snapshot_event_id,
    )

    crcc_intent = _build_crcc_intent(
        run_id=run_id,
        seg_start=seg_start,
        seg_end=seg_end,
        digest_hash=digest_hash,
        original_event_count=len(records),
        lookback=lookback,
        digest_storage_path=digest_storage_path,
        provenance_refs=archived_event_ids,
        snapshot_event_id=snap.snapshot_event_id,
    )
    asm_intent = _build_asm_intent(
        seg_start=seg_start,
        seg_end=seg_end,
        original_event_count=len(records),
        segment_path_before=str(target),
        segment_path_after=str(cold_archive_path(towow_dir, seg_id)),
        classification=decision.classification_summary,
        prepared=prepared,
    )
    envelope_intent = _build_envelope_intent(run_id=run_id, envelope=envelope)

    gate = CommitGate(event_log)
    commit_outcome = gate.attempt_commit(envelope_intent, [crcc_intent, asm_intent])
    # audit_blocking defaults False → attempt_commit always returns the list[EventRecord] batch here.
    if not isinstance(commit_outcome, list):  # pragma: no cover - audit_blocking is never set here
        msg = "attempt_commit returned a non-list outcome without audit_blocking"
        raise TypeError(msg)
    commit_records = commit_outcome
    sentinel = commit_records[-1]
    if sentinel.event_type is not EventType.COMMIT_ACCEPTED:
        # §7.2 reject path — drop staging; hot/index were never touched (no half-truth).
        rollback_prepared_archive(towow_dir, prepared)
        reason = _rejection_detail(sentinel)
        return ConsolidationRunResult(
            accepted=False,
            rejection_reason=reason,
            snapshot_event_id=snap.snapshot_event_id,
            archived_segment_id=seg_id,
            segment_start_seq=seg_start,
            segment_end_seq=seg_end,
            classification_summary=classification,
            cutoff_seq=cutoff_seq,
        )

    crcc_id, asm_id = _accepted_domain_ids(commit_records)

    # §7.2 finalize — re-verify staging+hot integrity at the boundary, then atomic rename + index +
    # unlink hot. A staging/hot tamper between prepare and finalize fails closed (rollback, no move).
    tamper = verify_prepared_archive(prepared)
    if tamper is not None:  # pragma: no cover - synchronous run; sealed segment is immutable
        rollback_prepared_archive(towow_dir, prepared)
        return ConsolidationRunResult(
            accepted=False,
            rejection_reason=f"archive prepared-artifact verify failed at finalize: {tamper}",
            consolidation_event_id=crcc_id,
            archive_segment_event_id=asm_id,
            snapshot_event_id=snap.snapshot_event_id,
            archived_segment_id=seg_id,
        )
    finalize_archive(towow_dir, prepared, event_ids=archived_event_ids)

    return ConsolidationRunResult(
        accepted=True,
        consolidation_event_id=crcc_id,
        archive_segment_event_id=asm_id,
        snapshot_event_id=snap.snapshot_event_id,
        archived_segment_id=seg_id,
        archived_event_ids=archived_event_ids,
        digest_hash=digest_hash,
        digest_storage_path=digest_storage_path,
        cutoff_seq=cutoff_seq,
        segment_start_seq=seg_start,
        segment_end_seq=seg_end,
        classification_summary=classification,
    )


def _build_crcc_intent(
    *,
    run_id: str,
    seg_start: int,
    seg_end: int,
    digest_hash: str,
    original_event_count: int,
    lookback: LookbackWindow,
    digest_storage_path: str,
    provenance_refs: list[str],
    snapshot_event_id: str,
) -> EventIntent:
    """CrossRunConsolidationCommitted intent — M-0.1 §3.4 base + M-0.7 §2.3 enrichment (schema-enforced)."""
    payload = {
        "segment_range": {"start_seq": seg_start, "end_seq": seg_end},
        "digest_hash": digest_hash,
        "digest_type": "summary",
        "original_event_count": original_event_count,
        "retained_provenance": True,
        "consolidation_run_id": run_id,
        "lookback_window": lookback.model_dump(mode="json"),
        "digest_storage_path": digest_storage_path,
        "provenance_refs": [
            {"event_id": e, "relevance": ProvenanceRefRelevance.PRIMARY.value} for e in provenance_refs
        ],
        "f18_digests_consumed": [],
        "snapshot_event_id": snapshot_event_id,
    }
    return EventIntent(
        local_intent_id=f"crcc-{uuid.uuid4().hex[:12]}",
        event_type=EventType.CROSS_RUN_CONSOLIDATION_COMMITTED,
        event_category=EventCategory.CONSOLIDATION,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07-consolidation", run_id=run_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"digest-{digest_hash[7:7 + 16]}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
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
        "provenance_preserved": True,  # §4.3: cold archive keeps every event retrievable
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
        local_intent_id=f"asm-{uuid.uuid4().hex[:12]}",
        event_type=EventType.ARCHIVE_SEGMENT_MOVED,
        event_category=EventCategory.CONSOLIDATION,
        payload=payload,
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07-consolidation"),
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


def _build_envelope_intent(*, run_id: str, envelope: ConsolidationEnvelope) -> EventIntent:
    """The TransactionEnvelope carrying the consolidation_event patch + §6.3 envelope for the gate."""
    env_id = f"env-consolidate-{uuid.uuid4().hex[:12]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": run_id,
            "capsule_compiled_event_id": "evt-stub-capsule-consolidation",
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
            "patches": [
                {
                    "patch_type": PatchType.CONSOLIDATION_EVENT.value,
                    "patch_id": f"patch-consolidation-{run_id}",
                    "target_ref": f"consolidation-{run_id}",
                },
            ],
            "consolidation_envelope": envelope.model_dump(mode="json"),
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_SESSION.value,
            actor_id="m07-consolidation-run",
            session_id=run_id,
            skill_id="M-2.2",
            run_id=run_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"consolidation-{run_id}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


@dataclass(frozen=True)
class ConsolidationAuditResult:
    """§8.5 audit-consolidation — read-only reconstructability + provenance verdict for one digest."""

    consolidation_event_id: str
    snapshot_event_id: str | None
    snapshot_intact: bool
    provenance_total: int
    provenance_missing: list[str]
    passed: bool
    detail: str


def audit_recent_consolidations(
    *,
    event_log: EventLog,
    limit: int = 5,
) -> list[ConsolidationAuditResult]:
    """M-0.7 §8.5 audit-consolidation mode — read-only verify of the most recent consolidations.

    For each of the last ``limit`` CrossRunConsolidationCommitted events, re-checks (writing NOTHING
    — pure inspection, §8.3 不写 event): (a) the reconstructability anchor snapshot it named still
    exists + is integrity-intact (verify_record_integrity), and (b) every provenance_ref still
    resolves via get_event (hot OR cold — an archived segment's events must remain retrievable;
    归档不丢真相). A digest whose anchor vanished/tampered or whose provenance broke fails the audit.
    Surfacing a real failure as SnapshotSuperseded/FindingCreated is escalation (driver/Nature, §8.2)
    — out of this read-only pass.
    """
    crcc = event_log.get_events_by_type(EventType.CROSS_RUN_CONSOLIDATION_COMMITTED)
    recent = sorted(crcc, key=lambda r: r.sequence_number)[-limit:]
    results: list[ConsolidationAuditResult] = []
    for rec in recent:
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        snap_id = payload.get("snapshot_event_id")
        snap_id = snap_id if isinstance(snap_id, str) else None
        snapshot_intact = False
        if snap_id is not None:
            snap = event_log.get_event(snap_id)
            snapshot_intact = snap is not None and event_log.verify_record_integrity(snap)
        refs_raw = payload.get("provenance_refs", [])
        ref_ids: list[str] = [
            r["event_id"]
            for r in refs_raw
            if isinstance(r, dict) and isinstance(r.get("event_id"), str)
        ]
        missing = [e for e in ref_ids if event_log.get_event(e) is None]
        passed = snapshot_intact and not missing and (snap_id is not None)
        if passed:
            detail = (
                f"anchor snapshot {snap_id} intact + all {len(ref_ids)} provenance_ref(s) retrievable"
            )
        elif snap_id is None:
            detail = "consolidation names no anchor snapshot_event_id — reconstructability unverifiable"
        elif not snapshot_intact:
            detail = f"anchor snapshot {snap_id} missing or integrity-failed"
        else:
            detail = f"{len(missing)} provenance_ref(s) no longer retrievable: {missing[:5]}"
        results.append(
            ConsolidationAuditResult(
                consolidation_event_id=rec.event_id,
                snapshot_event_id=snap_id,
                snapshot_intact=snapshot_intact,
                provenance_total=len(ref_ids),
                provenance_missing=missing,
                passed=passed,
                detail=detail,
            ),
        )
    return results


def _accepted_domain_ids(commit_records: list[EventRecord]) -> tuple[str | None, str | None]:
    """Pull the CrossRunConsolidationCommitted + ArchiveSegmentMoved event_ids out of an accepted batch."""
    crcc_id: str | None = None
    asm_id: str | None = None
    for rec in commit_records:
        if rec.event_type is EventType.CROSS_RUN_CONSOLIDATION_COMMITTED:
            crcc_id = rec.event_id
        elif rec.event_type is EventType.ARCHIVE_SEGMENT_MOVED:
            asm_id = rec.event_id
    return crcc_id, asm_id


def _rejection_detail(sentinel: EventRecord) -> str:
    payload = sentinel.payload if isinstance(sentinel.payload, dict) else {}
    reason = payload.get("rejection_type") or payload.get("failure_reason") or sentinel.event_type.value
    return str(reason)


__all__ = [
    "ConsolidationAuditResult",
    "ConsolidationRunResult",
    "audit_recent_consolidations",
    "run_consolidation",
]
