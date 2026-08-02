"""Unit tests for state_transition payload schemas.

Covers 21 base (M-0.1 §3.1) + 6 Phase D (M-2.1/2.2/2.3) + 1 M-0.5a-3 LockReleased payloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    AtReferenceType,
    CascadeAffectedEntityType,
    CascadeAffectedViaSlice,
    CascadeResponseExpectation,
    ClosureVerificationMethod,
    ConceptCreatedStage,
    ConceptEdgeDirection,
    ConceptEdgeType,
    DaemonName,
    DaemonOutcome,
    EvidenceSourceType,
    FixOutcome,
    InformationNeedLifecycleStatus,
    InformationNeedResolutionMethod,
    InformationNeedSource,
    ObligationRetireCandidateReason,
    PatchType,
    SubjectEntityType,
    TargetEntityType,
    TaskDependencyStrength,
    TaskDependencyType,
    TaskModelTier,
    TaskRunOutcome,
    TaskType,
    TransitionType,
)
from towow.schemas.payloads import state_transition as st
from towow.schemas.payloads import task_package as tp
from towow.schemas.payloads.finding import ClosureCriterion


def test_concept_created_payload() -> None:
    p = st.ConceptCreatedPayload(
        after_state=st.ConceptCreatedAfter(
            concept_id="concept-1",
            name="Auth Flow",
            definition="rate-limited login pipeline",
            created_by_stage=ConceptCreatedStage.ENGINEERING_CONSENSUS,
        ),
    )
    assert p.target_entity_type is TargetEntityType.CONCEPT
    assert p.transition_type is TransitionType.CREATED


def test_concept_created_rejects_wrong_target_entity_type() -> None:
    """Literal[TargetEntityType.CONCEPT] enforces the discriminator."""
    with pytest.raises(ValidationError):
        st.ConceptCreatedPayload(
            target_entity_type=TargetEntityType.TASK,  # type: ignore[arg-type]
            after_state=st.ConceptCreatedAfter(
                concept_id="c-1",
                name="x",
                definition="x",
                created_by_stage=ConceptCreatedStage.INTERVIEW,
            ),
        )


def test_concept_edge_added_payload() -> None:
    st.ConceptEdgeAddedPayload(
        after_state=st.ConceptEdgeAddedAfter(
            edge_id="e-1",
            source_concept_id="c-1",
            target_concept_id="c-2",
            edge_type=ConceptEdgeType.DEPENDENCY,
            direction=ConceptEdgeDirection.UNIDIRECTIONAL,
        ),
    )


def test_concept_edge_removed_payload() -> None:
    p = st.ConceptEdgeRemovedPayload(
        before_state=st.ConceptEdgeRemovedBefore(
            edge_id="e-1",
            source_concept_id="c-1",
            target_concept_id="c-2",
            edge_type=ConceptEdgeType.CONTRACT,
        ),
        after_state=st.ConceptEdgeRemovedAfter(removal_reason="design supersede"),
    )
    assert p.after_state.status == "removed"


def test_concept_state_transition_payload() -> None:
    state = st.ConceptStateTransitionState(concept_id="c-1", saga_state="proposed")
    p = st.ConceptStateTransitionPayload(before_state=state, after_state=state)
    assert p.before_state.saga_state == "proposed"


def test_concept_graph_proposal_payload_accepts_concept_or_edge() -> None:
    after = st.ConceptGraphProposalAfter(
        proposed_changes=[
            st.ConceptGraphProposalChange(
                change_type="add_edge",
                entity_type=SubjectEntityType.CONCEPT_EDGE,
                entity_id="e-new",
                new_value={"edge_type": "dependency"},
            ),
        ],
    )
    p1 = st.ConceptGraphProposalPayload(
        target_entity_type=TargetEntityType.CONCEPT,
        after_state=after,
    )
    p2 = st.ConceptGraphProposalPayload(
        target_entity_type=TargetEntityType.CONCEPT_EDGE,
        after_state=after,
    )
    assert p1.transition_type is TransitionType.SUPERSEDED
    assert p2.target_entity_type is TargetEntityType.CONCEPT_EDGE


def test_concept_graph_proposal_rejects_unrelated_target() -> None:
    after = st.ConceptGraphProposalAfter(
        proposed_changes=[
            st.ConceptGraphProposalChange(
                change_type="add",
                entity_type=SubjectEntityType.CONCEPT,
                entity_id="c-1",
                new_value={},
            ),
        ],
    )
    with pytest.raises(ValidationError):
        st.ConceptGraphProposalPayload(
            target_entity_type=TargetEntityType.TASK,  # type: ignore[arg-type]
            after_state=after,
        )


def test_task_node_created_payload() -> None:
    p = st.TaskNodeCreatedPayload(
        after_state=st.TaskNodeCreatedAfter(
            task_id="t-1",
            task_type=TaskType.CODE_CHANGE,
            description="implement rate limiting",
            target_artifacts=["src/auth/login.py"],
        ),
    )
    assert p.after_state.task_type is TaskType.CODE_CHANGE


def test_task_dependency_edge_added_payload() -> None:
    st.TaskDependencyEdgeAddedPayload(
        after_state=st.TaskDependencyEdgeAddedAfter(
            source_task_id="t-1",
            target_task_id="t-2",
            dependency_type=TaskDependencyType.DATA_DEPENDENCY,
            evidence="t-1 写 concept-x, t-2 读 concept-x",  # T-L1-24 Patch W: 必填
            strength=TaskDependencyStrength.HARD,
        ),
    )


def test_task_read_set_claimed_payload() -> None:
    st.TaskReadSetClaimedPayload(
        after_state=st.TaskReadSetClaimedAfter(
            task_id="t-1",
            read_set=[st.EntityRef(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1")],
        ),
    )


def test_task_write_set_claimed_payload() -> None:
    st.TaskWriteSetClaimedPayload(
        after_state=st.TaskWriteSetClaimedAfter(
            task_id="t-1",
            write_set=[st.EntityRef(entity_type=SubjectEntityType.FILE, entity_id="src/x.py")],
        ),
    )


def test_task_model_tier_assigned_payload() -> None:
    st.TaskModelTierAssignedPayload(
        after_state=st.TaskModelTierAssignedAfter(
            task_id="t-1",
            model_tier=TaskModelTier.OPUS,
            assignment_reason="high-complexity refactor",
        ),
    )


def test_task_run_completed_payload_minimal() -> None:
    """既有形态仍合法: affected_concept_ids 是可选新字段 (default None), 旧事件不带也过。"""
    p = st.TaskRunCompletedPayload(
        after_state=st.TaskRunCompletedAfter(
            task_id="t-1",
            run_id="run-1",
            outcome=TaskRunOutcome.SUCCESS,
        ),
    )
    assert p.after_state.affected_concept_ids is None


def test_task_run_completed_payload_carries_affected_concept_ids() -> None:
    """R01 阶段2: after_state.affected_concept_ids = 系统从 SIS 算的 CIS 涟漪信号 (O-14)。"""
    p = st.TaskRunCompletedPayload(
        after_state=st.TaskRunCompletedAfter(
            task_id="t-1",
            run_id="run-1",
            outcome=TaskRunOutcome.SUCCESS,
            affected_concept_ids=["c-foo@v1", "c-down@v1"],
        ),
    )
    assert p.after_state.affected_concept_ids == ["c-foo@v1", "c-down@v1"]


def test_task_run_completed_after_rejects_unknown_field() -> None:
    """_STRICT (extra=forbid): 未声明字段被拒 — affected_concept_ids 必须是显式 schema 字段。"""
    with pytest.raises(ValidationError):
        st.TaskRunCompletedAfter(
            task_id="t-1",
            run_id="run-1",
            outcome=TaskRunOutcome.SUCCESS,
            bogus_field=["x"],  # type: ignore[call-arg]
        )


def _valid_self_contained_content() -> tp.TaskPackageContent:
    """A minimal §3.6 package_content whose done_criteria passes the §11.1 门1 floor.

    verification_type=test_passes claims a mechanical result, so (finding-r05) it must carry a
    gate-recomputable machine_check (here: a test machine_check with a test_selector) for the
    package to back an is_self_contained=true publish.
    """
    return tp.TaskPackageContent(
        task_type=TaskType.CODE_CHANGE,
        description="add a write-boundary invariant",
        done_criteria=[
            tp.DoneCriterion(
                criterion_id="dc-1",
                description="the new tests pass",
                verification_type="test_passes",
                evidence_ref=tp.EvidenceRef(
                    source_type="brief.completion_condition",
                    source_id="b-1",
                    quoted_claim="write boundary must reject empty self-contained packages",
                ),
                machine_check=ClosureCriterion(
                    condition="write-boundary tests pass",
                    verification_method=ClosureVerificationMethod.TEST,
                    expected_result="pytest exit 0",
                    test_selector="tests/unit/schemas/payloads/test_state_transition.py::test_write_boundary",
                ),
            ),
        ],
        model_tier=TaskModelTier.SONNET,
        model_tier_rationale="mechanical schema change",
        review_required=False,
    )


def test_task_package_published_payload_self_contained_requires_backed_package() -> None:
    """is_self_contained=true with a fully-backed package_content validates (the CLI publish path)."""
    p = st.TaskPackagePublishedPayload(
        after_state=st.TaskPackagePublishedAfter(
            task_id="t-1",
            package_hash="sha256:abc",
            is_self_contained=True,
            package_content=_valid_self_contained_content(),
        ),
    )
    assert p.after_state.is_self_contained is True
    assert p.after_state.package_content is not None


def test_task_package_published_not_self_contained_allows_missing_content() -> None:
    """is_self_contained=false (blocked / pre-T-L1-28 replay shape) still validates without content.

    finding-r05-...: the write-boundary invariant only constrains the DERIVED-true claim, so it
    never breaks replay of historical events that carried only task_id/hash/flag.
    """
    st.TaskPackagePublishedPayload(
        after_state=st.TaskPackagePublishedAfter(
            task_id="t-1",
            package_hash="sha256:abc",
            is_self_contained=False,
        ),
    )


def test_task_package_published_self_contained_rejects_absent_content() -> None:
    """finding-r05-...: is_self_contained=true with NO package_content is rejected at the write boundary."""
    with pytest.raises(ValidationError, match="缺 package_content"):
        st.TaskPackagePublishedAfter(
            task_id="t-1",
            package_hash="sha256:abc",
            is_self_contained=True,
        )


def test_task_package_published_self_contained_rejects_empty_done_criteria() -> None:
    """finding-r05-...: a self-contained package with EMPTY done_criteria (the misleading empty
    acceptance package the finding flags) is rejected at the write boundary."""
    content = _valid_self_contained_content().model_copy(update={"done_criteria": []})
    with pytest.raises(ValidationError, match="done_criteria is empty"):
        st.TaskPackagePublishedAfter(
            task_id="t-1",
            package_hash="sha256:abc",
            is_self_contained=True,
            package_content=content,
        )


def test_task_package_published_self_contained_rejects_non_recomputable_criterion() -> None:
    """finding-r05-...: a self-contained package whose only criterion is a 纯语义 manual_verify with
    no gate-recomputable machine_check fails the §11.1 门1 floor at the write boundary too."""
    manual = tp.DoneCriterion(
        criterion_id="dc-manual",
        description="someone eyeballs it",
        verification_type="manual_verify",
        evidence_ref=tp.EvidenceRef(
            source_type="nature_judgment",
            source_id="n-1",
            quoted_claim="looks right",
        ),
    )
    content = _valid_self_contained_content().model_copy(update={"done_criteria": [manual]})
    with pytest.raises(ValidationError, match="纯语义不可机器复算"):
        st.TaskPackagePublishedAfter(
            task_id="t-1",
            package_hash="sha256:abc",
            is_self_contained=True,
            package_content=content,
        )


def test_task_run_completed_payload() -> None:
    st.TaskRunCompletedPayload(
        after_state=st.TaskRunCompletedAfter(
            task_id="t-1",
            run_id="r-1",
            outcome=TaskRunOutcome.SUCCESS,
        ),
    )


def test_information_need_created_payload() -> None:
    # M-1.1 §5.2/§10.2 Patch R flat shape (T-L1-05): source/red_line/red_line_reason 三正交维.
    p = st.InformationNeedCreatedPayload(
        need_id="n-1",
        description="need clarification on rate limit threshold",
        source=InformationNeedSource.NATURE_UNIQUE,
        red_line=True,
        red_line_reason="缺它工程师只能瞎猜阈值",
        downstream_impact=["engineering_design"],
        dependencies=[],
        session_id="sess-interview-abc",
    )
    assert p.source is InformationNeedSource.NATURE_UNIQUE
    assert p.red_line is True
    assert p.target_entity_type is TargetEntityType.INFORMATION_NEED


def test_information_need_created_red_line_reason_optional_when_not_red_line() -> None:
    p = st.InformationNeedCreatedPayload(
        need_id="n-2",
        description="nice-to-have telemetry detail",
        source=InformationNeedSource.AI_REACHABLE,
        red_line=False,
        session_id="sess-interview-abc",
    )
    assert p.red_line is False
    assert p.red_line_reason is None


def test_information_need_status_changed_payload() -> None:
    # M-1.1 §5.2 Patch R flat shape: old_status/new_status + resolution_method.
    p = st.InformationNeedStatusChangedPayload(
        need_id="n-1",
        old_status=InformationNeedLifecycleStatus.PENDING,
        new_status=InformationNeedLifecycleStatus.RESOLVED,
        nature_original_quote="阈值就定 100 QPS",
        resolution_method=InformationNeedResolutionMethod.NATURE_ANSWERED,
        session_id="sess-interview-abc",
    )
    assert p.new_status is InformationNeedLifecycleStatus.RESOLVED
    assert p.resolution_method is InformationNeedResolutionMethod.NATURE_ANSWERED


def test_at_reference_added_payload() -> None:
    st.AtReferenceAddedPayload(
        after_state=st.AtReferenceAddedAfter(
            reference_id="ref-1",
            source_entity=st.AtReferenceEndpoint(type="concept", id="c-1"),
            target_entity=st.AtReferenceEndpoint(type="task", id="t-1"),
            reference_type=AtReferenceType.SEMANTIC,
        ),
    )


def test_at_reference_removed_payload() -> None:
    st.AtReferenceRemovedPayload(
        before_state=st.AtReferenceRemovedBefore(
            reference_id="ref-1",
            source_entity=st.AtReferenceEndpoint(type="concept", id="c-1"),
            target_entity=st.AtReferenceEndpoint(type="task", id="t-1"),
        ),
    )


def test_patch_proposed_payload() -> None:
    # M-1.4 §3.1 shape (T-L1-37): patch_type / diff_hash / worktree_commit_sha /
    # integration_commit_sha replace the M-0.1 v1 file-level shape.
    payload = st.PatchProposedPayload(
        after_state=st.PatchProposedAfter(
            patch_id="p-1",
            task_id="t-1",
            fork_session_id="sess-work-1",
            patch_type=PatchType.CODE_DIFF,
            content_summary="add rate-limit decorator",
            affected_entities=[
                st.EntityRef(entity_type=SubjectEntityType.TASK, entity_id="t-1"),
            ],
            diff_hash="sha256:deadbeef",
            worktree_commit_sha="abc123",
            integration_commit_sha=None,
        ),
    )
    assert payload.after_state.patch_type is PatchType.CODE_DIFF
    assert payload.after_state.integration_commit_sha is None


def test_fix_proposed_payload() -> None:
    st.FixProposedPayload(
        after_state=st.FixProposedAfter(
            fix_id="f-1",
            related_finding_id="finding-1",
            patch_id="p-2",
            fix_approach="introduce input validation",
            patch_type=PatchType.CODE_DIFF,  # T-L1-67 §3.1 富字段必填
            diff_hash="sha256:abc",
            worktree_commit_sha="deadbeef",
        ),
    )


def test_fix_completed_payload() -> None:
    p = st.FixCompletedPayload(
        after_state=st.FixCompletedAfter(fix_id="f-1", outcome=FixOutcome.RESOLVED),
    )
    assert p.after_state.outcome is FixOutcome.RESOLVED


def test_consumer_list_published_payload() -> None:
    st.ConsumerListPublishedPayload(
        after_state=st.ConsumerListPublishedAfter(
            concept_id="c-1",
            consumers=[
                st.ConsumerRef(
                    entity_type=SubjectEntityType.TASK,
                    entity_id="t-1",
                    consumption_type="reads",
                ),
            ],
        ),
    )


def test_engineering_consensus_freezed_payload() -> None:
    p = st.EngineeringConsensusFreezedPayload(
        after_state=st.EngineeringConsensusFreezedAfter(
            plan_id="plan-1",
            concept_graph_snapshot_hash="sha256:def",
            freezed_at_event_offset=1000,
        ),
    )
    assert p.transition_type is TransitionType.FROZEN


# ─── Phase D payloads ────────────────────────────────────────────────────────────


@pytest.mark.patch
def test_invalidation_cascade_payload_patch_m21a() -> None:  # Patch M-2.1-A
    """Patch M-2.1-A: InvalidationCascade with cascade_depth bound + affected entities."""
    p = st.InvalidationCascadePayload(
        after_state=st.InvalidationCascadeAfter(
            cascade_id="cascade-1",
            triggered_by_event_id="evt-cst-1",
            source_concept_id="c-upstream",
            cascade_depth=3,
            affected_entities=[
                st.CascadeAffectedEntity(
                    entity_type=CascadeAffectedEntityType.TASK,
                    entity_id="t-downstream",
                    relationship_path=["e-1", "e-2"],
                    affected_via_slice=CascadeAffectedViaSlice.FORWARD,
                ),
            ],
            response_expectation=[
                st.CascadeResponseExpectationItem(
                    entity_type=CascadeAffectedEntityType.TASK,
                    suggested_response=CascadeResponseExpectation.REVERIFY,
                ),
            ],
        ),
    )
    assert p.after_state.cascade_depth == 3


@pytest.mark.invariant
def test_invalidation_cascade_depth_capped_at_5() -> None:
    """M-2.1 §3.1 invariant: cascade_depth ≤ 5 (simplification §7.4)."""
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        st.InvalidationCascadeAfter(
            cascade_id="c-1",
            triggered_by_event_id="e",
            source_concept_id="c",
            cascade_depth=6,  # over cap
            affected_entities=[
                st.CascadeAffectedEntity(
                    entity_type=CascadeAffectedEntityType.CONCEPT,
                    entity_id="c-x",
                    relationship_path=[],
                    affected_via_slice=CascadeAffectedViaSlice.BACKWARD,
                ),
            ],
        )


@pytest.mark.patch
def test_daemon_run_completed_payload_patch_m21a() -> None:  # Patch M-2.1-A
    p = st.DaemonRunCompletedPayload(
        after_state=st.DaemonRunCompletedAfter(
            daemon_run_id="dr-1",
            daemon_name=DaemonName.CONCEPT_GRAPH_HEALTH,
            scanned_count=120,
            findings_produced=3,
            cascade_events_produced=1,
            run_duration_ms=4500,
            outcome=DaemonOutcome.COMPLETED,
        ),
    )
    assert p.after_state.outcome is DaemonOutcome.COMPLETED


@pytest.mark.patch
def test_obligation_retire_candidate_payload_patch_m22a() -> None:  # Patch M-2.2-A
    st.ObligationRetireCandidatePayload(
        after_state=st.ObligationRetireCandidateAfter(
            candidate_id="orc-1",
            obligation_id="obl-task-constraint-abc",
            candidate_reason=ObligationRetireCandidateReason.TASK_COMPLETED_LONG_AGO,
            evidence=[
                st.EvidenceItem(
                    source_type=EvidenceSourceType.EVENT,
                    source_id="evt-task-run-completed-1",
                    relevance="task completed 90 days ago, no further activity",
                ),
            ],
            retire_completion_path="towow obligation retire --id obl-task-constraint-abc",
        ),
    )


@pytest.mark.patch
def test_historical_pattern_surface_candidate_payload_patch_m23a() -> None:  # Patch M-2.3-A
    """M-2.3 §3.1 invariants: ≥3 finding_event_ids, ≥2 span_runs."""
    st.HistoricalPatternSurfaceCandidatePayload(
        after_state=st.HistoricalPatternSurfaceCandidateAfter(
            candidate_id="hpsc-1",
            grouping_dimension="same_risk_surface",
            grouping_key="auth_module",
            finding_event_ids=["evt-f1", "evt-f2", "evt-f3"],
            frequency=3,
            first_observed_at="2026-01-01T00:00:00Z",
            last_observed_at="2026-05-01T00:00:00Z",
            span_runs=3,
            raw_quotes=[
                st.RawFindingQuote(
                    finding_event_id="evt-f1",
                    description="missing auth check in /api/users",
                    risk_surface="auth_module",
                    detection_method="manual_review",
                    created_at="2026-01-01T00:00:00Z",
                ),
            ],
            cross_reference_with_manual_feed=st.ManualFeedCrossReference(
                is_already_in_manual_feed=False,
            ),
        ),
    )


@pytest.mark.invariant
def test_historical_pattern_invariant_frequency_threshold() -> None:
    """M-2.3 §3.1: ≥ 3 finding_event_ids — single-run multi-finding not a cross-run pattern."""
    with pytest.raises(ValidationError, match="at least 3 items"):
        st.HistoricalPatternSurfaceCandidateAfter(
            candidate_id="hpsc-1",
            grouping_dimension="same_risk_surface",
            grouping_key="x",
            finding_event_ids=["evt-1", "evt-2"],  # only 2 — below 3
            frequency=2,
            first_observed_at="2026-01-01T00:00:00Z",
            last_observed_at="2026-05-01T00:00:00Z",
            span_runs=2,
            raw_quotes=[
                st.RawFindingQuote(
                    finding_event_id="evt-1",
                    description="x",
                    risk_surface="x",
                    detection_method="x",
                    created_at="2026-01-01T00:00:00Z",
                ),
            ],
            cross_reference_with_manual_feed=st.ManualFeedCrossReference(
                is_already_in_manual_feed=False,
            ),
        )


@pytest.mark.patch
def test_escalation_triaged_payload_patch_m23a() -> None:  # Patch M-2.3-A
    st.EscalationTriagedPayload(
        after_state=st.EscalationTriagedAfter(
            triage_category="technical_blocker",
            triage_reasoning="multi-round fix without novelty",
            expected_nature_action="reject_and_retry",
            expected_nature_action_reasoning="signal indicates retry is plausible",
            triage_time_lag_ms=1500,
        ),
    )


@pytest.mark.patch
def test_escalation_resolved_payload_patch_m23a() -> None:  # Patch M-2.3-A
    st.EscalationResolvedPayload(
        after_state=st.EscalationResolvedAfter(
            resolution="nature_accepted_workaround",
            nature_judgment_event_id="evt-njc-1",
            resolution_explanation="Nature accepted with caveat",
            raised_to_resolved_duration_ms=86_400_000,
        ),
    )


@pytest.mark.patch
def test_lock_released_payload_patch_m05a3() -> None:  # Patch M-0.5a-3
    p = st.LockReleasedPayload(
        after_state=st.LockReleasedAfter(
            task_id="t-1",
            run_id="r-1",
            entities=[st.EntityRef(entity_type=SubjectEntityType.FILE, entity_id="src/x.py")],
            release_reason="abandoned_envelope_timeout",
        ),
    )
    assert p.after_state.release_reason == "abandoned_envelope_timeout"


# ─── Patch grep anchors + spec source ──────────────────────────────────────────


_ST_PATH = Path(st.__file__)
_ST_SOURCE = _ST_PATH.read_text(encoding="utf-8")


@pytest.mark.patch
@pytest.mark.parametrize(
    "anchor",
    ["Patch M-2.1-A", "Patch M-2.2-A", "Patch M-2.3-A", "Patch M-0.5a-3"],
)
def test_patch_anchor_in_state_transition_source(anchor: str) -> None:
    assert anchor in _ST_SOURCE


def test_state_transition_spec_source_header() -> None:
    assert "spec source" in _ST_SOURCE
    assert "M-0.1-event-log-detailed-design.md" in _ST_SOURCE
    assert "§3.1" in _ST_SOURCE
