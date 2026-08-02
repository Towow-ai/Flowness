"""Unit tests for snapshot / consolidation / capsule / envelope / commit / finding /
detection_rule / obligation category payloads (Step 1.c categories 3..10).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    ArchiveDestination,
    CommitVerdict,
    DetectionRuleLifecycleState,
    DetectionRuleProposedSeverity,
    DigestSupersedeNoveltyType,
    EventCategory,
    FindingDetectionMethod,
    FindingKind,
    FindingResolution,
    FindingSeverity,
    LookbackType,
    NodeTouchedEntityType,
    NodeTouchedTouchType,
    ObligationActivationSource,
    ObligationCheckMethod,
    ObligationCheckOutcome,
    ObligationDeclaredStatus,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    ObligationViolatedRecommendedAction,
    PatchNoveltyType,
    ProvenanceRefRelevance,
    RejectionType,
    SceneType,
    SnapshotSupersedeNoveltyType,
    SnapshotType,
    SubjectEntityType,
)
from towow.schemas.payloads import (
    capsule,
    commit,
    consolidation,
    detection_rule,
    envelope,
    finding,
    obligation,
    snapshot,
)

# ─── snapshot ────────────────────────────────────────────────────────────────────


def test_snapshot_created_payload() -> None:
    snapshot.SnapshotCreatedPayload(
        event_offset=1000,
        projection_state_hash="sha256:abc",
        snapshot_type=SnapshotType.ROUTINE,
        covered_projections=["concept_graph", "task_graph"],
    )


def test_snapshot_superseded_payload() -> None:
    # backward-compat: the minimal M-0.1 §3.3 form (event_offset + projection_state_hash) still valid.
    snapshot.SnapshotSupersededPayload(
        event_offset=2000,
        projection_state_hash="sha256:def",
    )


def test_snapshot_superseded_full_payload_m07_22() -> None:
    """M-0.7 §2.2 — SnapshotSuperseded full payload: superseded ref + novelty + replacement.

    §2.2 semantic boundary: SnapshotSuperseded is only for "this snapshot's content is wrong"
    (e.g. reconstructability sampling failed), NOT for "create a new snapshot". The novelty block
    carries which kind of error, and no_new_information must be false (a correction always carries
    novelty).
    """
    p = snapshot.SnapshotSupersededPayload(
        event_offset=2000,
        projection_state_hash="sha256:def",
        superseded_snapshot_event_id="evt-old-snap",
        superseded_at_event_offset=1999,
        novelty=snapshot.SnapshotSupersedeNovelty(
            novelty_type=SnapshotSupersedeNoveltyType.CORRECTED_ERROR,
            no_new_information=False,
            corrected_error="reconstructability 抽样验证失败: concept_graph state_hash mismatch",
        ),
        replacement_snapshot_event_id="evt-new-snap",
    )
    assert p.superseded_snapshot_event_id == "evt-old-snap"
    assert p.novelty is not None
    assert p.novelty.novelty_type is SnapshotSupersedeNoveltyType.CORRECTED_ERROR


@pytest.mark.invariant
def test_snapshot_superseded_no_new_information_must_be_false() -> None:
    """M-0.7 §2.2 — novelty.no_new_information must be false (a supersede always carries novelty).

    NEGATIVE: setting no_new_information=True is a schema-layer invariant violation — a
    SnapshotSuperseded that claims "no new information" is contradictory (why supersede then?).
    """
    with pytest.raises(ValidationError):
        snapshot.SnapshotSupersedeNovelty(
            novelty_type=SnapshotSupersedeNoveltyType.HASH_MISMATCH,
            no_new_information=True,  # type: ignore[arg-type]  # invariant violation
            hash_mismatch="bundle_hash != actual dump",
        )


# ─── consolidation ───────────────────────────────────────────────────────────────


@pytest.mark.invariant
def test_consolidation_retained_provenance_must_be_true() -> None:
    """O-06 Invariant 2: CrossRunConsolidationCommitted.retained_provenance must be True."""
    consolidation.CrossRunConsolidationCommittedPayload(
        segment_range=consolidation.SegmentRange(start_seq=0, end_seq=100),
        digest_hash="sha256:xyz",
        original_event_count=100,
    )
    with pytest.raises(ValidationError):
        consolidation.CrossRunConsolidationCommittedPayload(
            segment_range=consolidation.SegmentRange(start_seq=0, end_seq=100),
            digest_hash="sha256:xyz",
            original_event_count=100,
            retained_provenance=False,  # type: ignore[arg-type]  # invariant violation
        )


def test_consolidation_committed_full_payload_m07_23() -> None:
    """M-0.7 §2.3 — CrossRunConsolidationCommitted full payload.

    §2.3: consolidation_run_id / lookback_window (lookback_type enum + parameters) /
    digest (digest_storage_path + provenance_refs[] with relevance enum) / f18_digests_consumed[] /
    snapshot_event_id. The flat digest_hash/digest_type/original_event_count/retained_provenance
    base fields (M-0.1 §3.4) remain for backward compat + the O-06 Inv2 invariant.
    """
    p = consolidation.CrossRunConsolidationCommittedPayload(
        segment_range=consolidation.SegmentRange(start_seq=0, end_seq=100),
        digest_hash="sha256:xyz",
        original_event_count=101,
        consolidation_run_id="crun-1",
        lookback_window=consolidation.LookbackWindow(
            lookback_type=LookbackType.RUN_GENERATIONS,
            parameters={"n_runs": 50},
        ),
        digest_storage_path=".towow/snapshots/digest-sha256xyz.json",
        provenance_refs=[
            consolidation.ProvenanceRef(event_id="evt-a", relevance=ProvenanceRefRelevance.PRIMARY),
            consolidation.ProvenanceRef(event_id="evt-b", relevance=ProvenanceRefRelevance.SUPPORTING),
        ],
        f18_digests_consumed=["evt-f18-1"],
        snapshot_event_id="evt-snap-after",
    )
    assert p.consolidation_run_id == "crun-1"
    assert p.lookback_window is not None
    assert p.lookback_window.lookback_type is LookbackType.RUN_GENERATIONS
    assert len(p.provenance_refs) == 2
    assert p.provenance_refs[0].relevance is ProvenanceRefRelevance.PRIMARY
    assert p.snapshot_event_id == "evt-snap-after"


def test_consolidation_archive_segment_moved() -> None:
    # backward-compat: the minimal M-0.1 §3.4 form (4 fields) still valid. Per M-0.7 §2.4,
    # archive_destination is the enum [cold, discarded] — the cold PATH lives in segment_path_after,
    # not in archive_destination (the old test passed a path there, which §2.4 corrects).
    consolidation.ArchiveSegmentMovedPayload(
        segment_range=consolidation.SegmentRange(start_seq=100, end_seq=200),
        archive_destination=ArchiveDestination.COLD,
        original_event_count=100,
        provenance_preserved=True,
    )


def test_consolidation_archive_segment_moved_full_payload_m07_24() -> None:
    """M-0.7 §2.4 + §7.2 — ArchiveSegmentMoved full payload (cold archive case).

    §2.4: segment_path_before / archive_destination enum / segment_path_after / classification_summary
    (3 base_classification counts) / triggered_by_consolidation_event_id / triggered_by_gc_run_id.
    §7.2: prepared_artifact_ref (3段式 prepare/commit/finalize 的 staging 留痕).
    """
    p = consolidation.ArchiveSegmentMovedPayload(
        segment_range=consolidation.SegmentRange(start_seq=100, end_seq=200),
        archive_destination=ArchiveDestination.COLD,
        original_event_count=100,
        provenance_preserved=True,  # §2.4: cold archive 时必 true
        segment_path_before=".towow/events/hot/events-000023.jsonl",
        segment_path_after=".towow/events/cold/archive-000023.jsonl.gz",
        classification_summary=consolidation.ClassificationSummary(
            immutable_truth_count=40,
            abstractable_process_count=55,
            discardable_noise_count=5,
        ),
        triggered_by_consolidation_event_id="evt-consolidation-1",
        prepared_artifact_ref=consolidation.PreparedArtifactRef(
            segment_id=23,
            staging_path=".towow/events/cold/staging/archive-000023.jsonl.gz.tmp",
            source_hash="sha256:src",
            archive_hash="sha256:arc",
            hot_path_unchanged=".towow/events/hot/events-000023.jsonl",
        ),
    )
    assert p.archive_destination is ArchiveDestination.COLD
    assert p.segment_path_after is not None
    assert p.classification_summary is not None
    assert p.classification_summary.immutable_truth_count == 40
    assert p.prepared_artifact_ref is not None
    assert p.prepared_artifact_ref.segment_id == 23


def test_consolidation_archive_segment_moved_discarded_destination() -> None:
    """M-0.7 §2.4/§4.3 — discarded destination (third-tier tombstone) is a valid enum value.

    NEGATIVE: an out-of-enum archive_destination string is rejected (was a free str before §2.4).
    """
    consolidation.ArchiveSegmentMovedPayload(
        segment_range=consolidation.SegmentRange(start_seq=300, end_seq=400),
        archive_destination=ArchiveDestination.DISCARDED,
        original_event_count=10,
        provenance_preserved=False,  # §4.3: discarded → no provenance
    )
    with pytest.raises(ValidationError):
        consolidation.ArchiveSegmentMovedPayload(
            segment_range=consolidation.SegmentRange(start_seq=300, end_seq=400),
            archive_destination="frozen",  # type: ignore[arg-type]  # not a valid ArchiveDestination
            original_event_count=10,
            provenance_preserved=False,
        )


def test_consolidation_digest_superseded() -> None:
    # backward-compat: minimal form (digest_hash only) still valid.
    consolidation.DigestSupersededPayload(digest_hash="sha256:new")


def test_consolidation_digest_superseded_full_payload_m07_25() -> None:
    """M-0.7 §2.5 — DigestSuperseded full payload: superseded/new digest refs + novelty.

    §2.5 supersede chain — a wrong/stale digest is amended by a new CrossRunConsolidationCommitted,
    NOT by editing the original. novelty.no_new_information must be false; explanation is required.
    """
    p = consolidation.DigestSupersededPayload(
        digest_hash="sha256:new",
        superseded_digest_event_id="evt-old-digest",
        new_digest_event_id="evt-new-digest",
        novelty=consolidation.DigestSupersedeNovelty(
            novelty_type=DigestSupersedeNoveltyType.PROVENANCE_REPAIR,
            no_new_information=False,
            explanation="旧 digest provenance 链断了, 重新 digest 修复 refs",
        ),
    )
    assert p.superseded_digest_event_id == "evt-old-digest"
    assert p.new_digest_event_id == "evt-new-digest"
    assert p.novelty is not None
    assert p.novelty.novelty_type is DigestSupersedeNoveltyType.PROVENANCE_REPAIR


@pytest.mark.invariant
def test_consolidation_digest_superseded_no_new_information_must_be_false() -> None:
    """M-0.7 §2.5 — novelty.no_new_information must be false (a digest amend carries novelty).

    NEGATIVE: no_new_information=True is a schema-layer invariant violation.
    """
    with pytest.raises(ValidationError):
        consolidation.DigestSupersedeNovelty(
            novelty_type=DigestSupersedeNoveltyType.CORRECTED_ERROR,
            no_new_information=True,  # type: ignore[arg-type]  # invariant violation
            explanation="contradiction",
        )


def test_retention_policy_changed() -> None:
    consolidation.RetentionPolicyChangedPayload(
        after_state=consolidation.RetentionPolicyChangedAfter(
            policy_id="default",
            new_rules=[
                consolidation.RetentionRule(
                    event_category=EventCategory.STATE_TRANSITION,
                    retention_days=365,
                    can_digest=False,
                ),
            ],
            change_reason="quarterly review",
        ),
    )


# ─── capsule ────────────────────────────────────────────────────────────────────


def test_capsule_compiled_payload() -> None:
    capsule.CapsuleCompiledPayload(
        input_hash="sha256:input",
        scene_type=SceneType.EXECUTION,
        target_fork_id="fork-123",
        capsule_sections_summary=capsule.CapsuleSectionsSummary(
            obligation_count=5,
            red_line_count=2,
            known_facts_count=10,
            files_to_read_count=3,
        ),
    )


# ─── envelope ───────────────────────────────────────────────────────────────────


def test_transaction_envelope_submitted_payload() -> None:
    envelope.TransactionEnvelopeSubmittedPayload(
        read_set=[
            envelope.EntityRefWithVersion(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id="c-1",
                version="v1",
            ),
        ],
        write_set=[
            envelope.EntityRefWithChangeType(
                entity_type=SubjectEntityType.FILE,
                entity_id="src/x.py",
                change_type="modify",
            ),
        ],
        active_obligations_declared=[
            envelope.ActiveObligationDeclared(
                obligation_id="obl-1",
                status=ObligationDeclaredStatus.MAINTAINED,
            ),
        ],
        self_check=envelope.SelfCheck(passed=True, checks_run=["type-check", "tests"]),
    )


def test_node_touched_payload() -> None:
    envelope.NodeTouchedPayload(
        target_entity_type=NodeTouchedEntityType.CONCEPT,
        target_entity_id="c-1",
        touch_type=NodeTouchedTouchType.READ,
    )


# ─── commit ─────────────────────────────────────────────────────────────────────


def test_commit_accepted_payload() -> None:
    """E.1.1: payload 含 gate_run_id + accepted_domain_event_count."""
    p = commit.CommitAcceptedPayload(
        envelope_event_id="evt-env-1",
        capsule_compiled_event_id="evt-cap-1",
        gate_run_id="gr-1",
        accepted_domain_event_count=1,
        local_to_event_id_mapping=[
            commit.LocalIntentMapping(local_intent_id="i-1", event_id="evt-1"),
        ],
    )
    assert p.verdict is CommitVerdict.ACCEPTED
    assert p.gate_run_id == "gr-1"


def test_commit_rejected_payload() -> None:
    """E.1.1: payload 含 gate_run_id + recovery_of_abandoned."""
    p = commit.CommitRejectedPayload(
        envelope_event_id="evt-env-2",
        capsule_compiled_event_id="evt-cap-2",
        rejection_type=RejectionType.VERSION_CONFLICT,
        gate_run_id="gr-2",
        recovery_of_abandoned=False,
        rejection_reasons=[
            commit.RejectionReasonItem(
                check_type="version_check",
                failure_reason="stale baseline",
                evidence={"current": 100, "expected": 99},
            ),
        ],
        rebase_allowed=True,
    )
    assert p.verdict is CommitVerdict.REJECTED
    assert p.rebase_allowed
    assert p.recovery_of_abandoned is False


def test_drift_detected_payload() -> None:
    commit.DriftDetectedPayload(
        envelope_event_id="evt-env-3",
        drift_nodes=[
            commit.EntityRefShort(entity_type=SubjectEntityType.FILE, entity_id="src/y.py"),
        ],
        original_assembly_event_id="evt-cap-3",
    )


# ─── finding ────────────────────────────────────────────────────────────────────


def test_finding_created_payload_with_kind() -> None:
    finding.FindingCreatedPayload(
        finding_id="f-1",
        severity=FindingSeverity.MAJOR,
        risk_surface="auth_module",
        description="missing rate limit",
        detection_method=FindingDetectionMethod.MANUAL_REVIEW,
        finding_kind=FindingKind.CONCEPT_ISSUE,
    )


def test_finding_verified_payload() -> None:
    finding.FindingVerifiedPayload(
        finding_id="f-1",
        verification_method="independent code review",
        false_positive_eliminated=True,
    )


def test_finding_disputed_payload() -> None:
    finding.FindingDisputedPayload(
        finding_id="f-1",
        dispute_reason="threat model excludes this surface",
    )


def test_finding_resolved_payload() -> None:
    finding.FindingResolvedPayload(
        finding_id="f-1",
        resolution=FindingResolution.CONFIRMED_AND_FIXED,
    )


# ─── detection_rule ─────────────────────────────────────────────────────────────


@pytest.mark.patch
def test_detection_rule_proposed_with_escalation_origin_patch_m23d() -> None:  # Patch M-2.3-D
    """Patch M-2.3-D: spawned_from_escalation_id optional, non-breaking."""
    detection_rule.DetectionRuleProposedPayload(
        rule_id="r-1",
        rule_definition="no unbounded loops in critical path",
        spawned_from_escalation_id="esc-1",
        proposed_severity=DetectionRuleProposedSeverity.WARNING,
    )


def test_detection_rule_shadowing_payload() -> None:
    detection_rule.DetectionRuleShadowingPayload(
        rule_id="r-1",
        shadow_start_event_offset=5000,
    )


def test_detection_rule_promoted_canonical_emit_only() -> None:
    """E.1.1 Conflict 3: DetectionRulePromoted emit only active_warning / enforced (canonical)."""
    p = detection_rule.DetectionRulePromotedPayload(
        rule_id="r-1",
        rule_lifecycle_state=DetectionRuleLifecycleState.ACTIVE_WARNING,
        promotion_reason="2-week shadow with low FP",
        shadow_stats=detection_rule.ShadowStats(runs=100, hits=10, false_positives=2),
    )
    assert p.rule_lifecycle_state is DetectionRuleLifecycleState.ACTIVE_WARNING


def test_detection_rule_promoted_rejects_deprecated_alias_warning() -> None:
    """E.1.1 Conflict 3: emitter does not accept deprecated alias 'warning'."""
    with pytest.raises(ValidationError):
        detection_rule.DetectionRulePromotedPayload(
            rule_id="r-1",
            rule_lifecycle_state=DetectionRuleLifecycleState.WARNING,  # type: ignore[arg-type]
            promotion_reason="x",
            shadow_stats=detection_rule.ShadowStats(runs=10, hits=1, false_positives=0),
        )


def test_detection_rule_retired_payload() -> None:
    detection_rule.DetectionRuleRetiredPayload(
        rule_id="r-1",
        retirement_reason="superseded by newer rule",
    )


# ─── obligation lifecycle events ────────────────────────────────────────────────


def test_obligation_captured_payload() -> None:
    obligation.ObligationCapturedPayload(
        obligation_id="obl-intent-attribution-abc",
        attached_to=[
            obligation.ObligationAttachmentRef(
                entity_type=SubjectEntityType.PATCH,
                entity_id="patch-1",
            ),
        ],
        scope_rule="applies to all patches",
        scope_type=ObligationScopeType.ALL_PATCHES,
        severity=ObligationSeverity.RED_LINE,
        nature=ObligationNature.INTENT_ATTRIBUTION,
        definition="every patch must trace to a Goal",
    )


def test_obligation_activated_payload() -> None:
    obligation.ObligationActivatedPayload(
        obligation_id="obl-1",
        activated_for_task_id="t-1",
        activation_source=ObligationActivationSource.RESOLVER_JUDGMENT,
        resolver_decision_event_id="evt-rdm-1",
    )


def test_obligation_checked_payload() -> None:
    # Cap2 (RUN-038, M-0.6 §4.2.3): check_method + check_outcome are now required typed fields.
    obligation.ObligationCheckedPayload(
        obligation_id="obl-1",
        checked_in_envelope_event_id="evt-env-1",
        check_method=ObligationCheckMethod.SELF_DECLARED_ONLY,
        check_outcome=ObligationCheckOutcome.MAINTAINED,
        check_result=ObligationDeclaredStatus.MAINTAINED,
    )


def test_obligation_violated_payload() -> None:
    obligation.ObligationViolatedPayload(
        obligation_id="obl-1",
        violated_in_envelope_event_id="evt-env-2",
        violation_description="missing intent reference",
        recommended_action=ObligationViolatedRecommendedAction.FIX,
    )


def test_obligation_evolved_payload() -> None:
    # Cap3 (RUN-038, M-0.6 §4.1.2): novelty is now a required, validated block.
    obligation.ObligationEvolvedPayload(
        obligation_id="obl-1",
        before_state=obligation.ObligationEvolvedBeforeAfter(
            scope_rule="old rule",
            severity=ObligationSeverity.NORMAL,
            definition="old definition",
        ),
        after_state=obligation.ObligationEvolvedBeforeAfter(
            scope_rule="new rule",
            severity=ObligationSeverity.RED_LINE,
            definition="new definition",
        ),
        novelty=obligation.ObligationEvolvedNovelty(
            novelty_type=PatchNoveltyType.NEW_CONSTRAINT,
            no_new_information=False,
            new_constraint="now also requires X",
        ),
    )


def test_obligation_retired_payload() -> None:
    obligation.ObligationRetiredPayload(
        obligation_id="obl-1",
        retirement_reason="task closed, scope no longer applicable",
    )


# ─── Patch grep anchors ─────────────────────────────────────────────────────────


@pytest.mark.patch
def test_patch_anchors_in_payloads() -> None:
    """Phase D Patch anchors in payloads dir — M-2.3-D detection_rule + M-0.5a-3 LockReleased (in state_transition)."""
    base = Path(detection_rule.__file__).parent
    sources = "".join((base / f.name).read_text(encoding="utf-8") for f in base.glob("*.py"))
    assert "Patch M-2.3-D" in sources
    # M-0.5a-3 LockReleased anchor is in state_transition.py
    assert "Patch M-0.5a-3" in sources
    # M-0.5a-4 / M-0.5a-6 in commit.py
    assert "Patch M-0.5a-4" in sources
