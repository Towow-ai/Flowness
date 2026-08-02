"""RUN-038 L0增强 M-0.7 §6.3 ConsolidationEnvelope + §6.4 verify_consolidation_invariants_full.

# spec source: 03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#   §6.3 ConsolidationEnvelope schema (read_set / write_set / 三个 compaction invariant 声明 /
#        consolidation_proposal with provenance_refs)
#   §6.4 verify_consolidation_invariants(envelope) -> VerifyResult — 三 invariant 各自结果
#   §6.5 inv1 reconstructability (本批: 结构性检查 — baseline snapshot 存在+完整; 5% full-replay
#        抽样 driver-gated)
#   §4.2 / §6.4 inv2 — immutable_truth 不可 digest + provenance_refs 可达
#   §6.3 / O-06 Inv3 — correctable consolidation: supersede 必指有效旧 event_id
#
# Anti-vacuous: each invariant is proven to FAIL on a real violation (broke-the-confirmation negative
# control) AND PASS on a real clean envelope — fed a REAL EventLog with REAL records (not hand-mocked
# results). verify-against-contract: the envelope is the §6.3 typed schema, the records are real
# write_direct EventRecords, immutable_truth is a real bootstrap obligation event.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.snapshot.consolidation_verify import verify_consolidation_invariants_full
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    LookbackType,
    ObligationDeclaredStatus,
    ProvenanceRefRelevance,
    SceneType,
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

    from towow.schemas.event_record import EventRecord


@pytest.fixture
def event_log(tmp_path: Path) -> EventLog:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    log = EventLog(towow / "events.log")
    CommitGate(log).bootstrap_commit(bootstrap_protocol_invariants())
    return log


def _process_event(log: EventLog, eid_tag: str) -> EventRecord:
    """A real abstractable_process event (digestable — not a fact source)."""
    return log.write_direct(
        EventIntent(
            local_intent_id=f"nt-{eid_tag}-{uuid.uuid4().hex[:8]}",
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={"kind": "test", "entity_type": "concept", "entity_id": eid_tag, "touch_type": "read"},
            provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="t"),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=eid_tag, role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
    )


def _snapshot(log: EventLog, *, offset: int, state_hash: str) -> EventRecord:
    return log.write_direct(
        EventIntent(
            local_intent_id=f"snap-{offset}",
            event_type=EventType.SNAPSHOT_CREATED,
            event_category=EventCategory.SNAPSHOT,
            payload={
                "event_offset": offset,
                "projection_state_hash": state_hash,
                "snapshot_type": "consolidation_anchor",
                "covered_projections": ["concept_graph"],
            },
            provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07"),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=state_hash, role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
    )


def _clean_envelope(
    *,
    snapshot_event_id: str,
    seg_start: int,
    seg_end: int,
    provenance_event_ids: list[str],
    superseded_snapshot_event_ids: list[str] | None = None,
    superseded_digest_event_ids: list[str] | None = None,
    declared: list[CompactionInvariantDeclared] | None = None,
) -> ConsolidationEnvelope:
    return ConsolidationEnvelope(
        task_id="crun-task-1",
        scene_type=SceneType.CONSOLIDATION,
        capsule_compiled_event_id="evt-capsule-1",
        read_set=[ConsolidationReadEntry(entity_type="event_segment", entity_id=f"seg-{seg_start}-{seg_end}")],
        write_set=[ConsolidationWriteEntry(entity_type="digest", entity_id="digest-sha1")],
        active_obligations_declared=declared
        or [
            CompactionInvariantDeclared(obligation_id=obl, status=ObligationDeclaredStatus.MAINTAINED)
            for obl in COMPACTION_INVARIANT_OBLIGATIONS
        ],
        lookback_window=LookbackWindow(lookback_type=LookbackType.SEQ_RANGE, parameters={}),
        consolidation_proposal=ConsolidationProposal(
            digest_hash="sha1",
            digest_storage_path=".towow/snapshots/digest-sha1.json",
            provenance_refs=[
                ConsolidationProvenanceRef(event_id=e, relevance=ProvenanceRefRelevance.PRIMARY)
                for e in provenance_event_ids
            ],
            segment_start_seq=seg_start,
            segment_end_seq=seg_end,
        ),
        snapshot_event_id=snapshot_event_id,
        superseded_snapshot_event_ids=superseded_snapshot_event_ids or [],
        superseded_digest_event_ids=superseded_digest_event_ids or [],
    )


# ─── §6.3 envelope schema ────────────────────────────────────────────────────────


def test_envelope_scene_type_pinned_to_consolidation() -> None:
    """§6.3 — scene_type is pinned to consolidation (§10.3 Patch K)."""
    env = _clean_envelope(snapshot_event_id="evt-x", seg_start=1, seg_end=2, provenance_event_ids=[])
    assert env.scene_type is SceneType.CONSOLIDATION
    # NEGATIVE: a non-consolidation scene_type is rejected by the Literal pin.
    with pytest.raises(ValidationError):
        ConsolidationEnvelope(
            task_id="t",
            scene_type=SceneType.EXECUTION,  # type: ignore[arg-type]
            capsule_compiled_event_id="c",
            read_set=[ConsolidationReadEntry(entity_type="snapshot", entity_id="snapshot-1")],
            write_set=[ConsolidationWriteEntry(entity_type="digest", entity_id="digest-1")],
            active_obligations_declared=[
                CompactionInvariantDeclared(obligation_id=o, status=ObligationDeclaredStatus.MAINTAINED)
                for o in COMPACTION_INVARIANT_OBLIGATIONS
            ],
            lookback_window=LookbackWindow(lookback_type=LookbackType.MANUAL, parameters={}),
            consolidation_proposal=ConsolidationProposal(
                digest_hash="h", digest_storage_path="p", segment_start_seq=0, segment_end_seq=1,
            ),
        )


# ─── §6.4 verify — clean PASS ──────────────────────────────────────────────────────


def test_verify_clean_envelope_passes(event_log: EventLog) -> None:
    """§6.4 — a clean envelope (real intact baseline snapshot, abstractable segment, accessible
    provenance, declared invariants) passes all three."""
    p1 = _process_event(event_log, "p1")
    p2 = _process_event(event_log, "p2")
    snap = _snapshot(event_log, offset=p2.sequence_number, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=snap.event_id,
        seg_start=p1.sequence_number,
        seg_end=p2.sequence_number,
        provenance_event_ids=[p1.event_id, p2.event_id],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is True
    assert all(r.passed for r in result.invariant_results.values())


# ─── §6.4 inv1 reconstructability — NEGATIVE ──────────────────────────────────────


def test_inv1_fails_when_baseline_snapshot_missing(event_log: EventLog) -> None:
    """§6.4 inv1 — a baseline snapshot that does not exist in the log → reconstructability fails."""
    p1 = _process_event(event_log, "p1")
    env = _clean_envelope(
        snapshot_event_id="evt-nonexistent-snap",
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=[p1.event_id],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["reconstructability"].passed is False


def test_inv1_fails_when_no_baseline_named(event_log: EventLog) -> None:
    """§6.4 inv1 — snapshot_event_id=None is NOT vacuously OK; no baseline → cannot reconstruct."""
    p1 = _process_event(event_log, "p1")
    env = _clean_envelope(
        snapshot_event_id=None,  # type: ignore[arg-type]
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=[p1.event_id],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["reconstructability"].passed is False


# ─── §6.4 inv2 provenance — NEGATIVE ──────────────────────────────────────────────


def test_inv2_fails_when_segment_folds_immutable_truth(event_log: EventLog) -> None:
    """§6.4 inv2 / §4.2 — a digest segment covering a real bootstrap immutable_truth event fails."""
    bootstrap = [r for r in event_log.all_records() if r.base_classification is BaseClassification.IMMUTABLE_TRUTH]
    assert bootstrap, "bootstrap must have produced immutable_truth obligation events"
    lo = min(r.sequence_number for r in bootstrap)
    hi = max(r.sequence_number for r in bootstrap)
    snap = _snapshot(event_log, offset=hi, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=snap.event_id,
        seg_start=lo,
        seg_end=hi,
        provenance_event_ids=[],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["provenance"].passed is False


def test_inv2_fails_when_provenance_ref_unretrievable(event_log: EventLog) -> None:
    """§6.4 inv2 — a provenance_ref pointing at a non-existent event → broken provenance, fails."""
    p1 = _process_event(event_log, "p1")
    snap = _snapshot(event_log, offset=p1.sequence_number, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=snap.event_id,
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=["evt-vanished"],  # not in the log
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["provenance"].passed is False


# ─── §6.4 inv3 correctability — NEGATIVE ──────────────────────────────────────────


def test_inv3_fails_on_dangling_supersede_target(event_log: EventLog) -> None:
    """§6.4 inv3 — a SnapshotSuperseded/DigestSuperseded naming a non-existent old event fails."""
    p1 = _process_event(event_log, "p1")
    snap = _snapshot(event_log, offset=p1.sequence_number, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=snap.event_id,
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=[p1.event_id],
        superseded_digest_event_ids=["evt-no-such-digest"],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["correctability"].passed is False


def test_inv3_passes_on_valid_supersede_target(event_log: EventLog) -> None:
    """§6.4 inv3 — a supersede pointing at a REAL existing event passes correctability."""
    p1 = _process_event(event_log, "p1")
    old_snap = _snapshot(event_log, offset=p1.sequence_number, state_hash="state-v0")
    new_snap = _snapshot(event_log, offset=p1.sequence_number + 1, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=new_snap.event_id,
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=[p1.event_id],
        superseded_snapshot_event_ids=[old_snap.event_id],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.invariant_results["correctability"].passed is True


# ─── §6.4 declared-complete (anti-vacuous) — NEGATIVE ─────────────────────────────


def test_declared_incomplete_fails_not_vacuous(event_log: EventLog) -> None:
    """§6.3/§6.4 — omitting a compaction invariant declaration does NOT pass by default."""
    p1 = _process_event(event_log, "p1")
    snap = _snapshot(event_log, offset=p1.sequence_number, state_hash="state-v1")
    env = _clean_envelope(
        snapshot_event_id=snap.event_id,
        seg_start=p1.sequence_number,
        seg_end=p1.sequence_number,
        provenance_event_ids=[p1.event_id],
        # only declare ONE of the three — the other two are missing.
        declared=[
            CompactionInvariantDeclared(
                obligation_id=COMPACTION_INVARIANT_OBLIGATIONS[0],
                status=ObligationDeclaredStatus.MAINTAINED,
            ),
        ],
    )
    result = verify_consolidation_invariants_full(env, event_log)
    assert result.passed is False
    assert result.invariant_results["declared_complete"].passed is False
