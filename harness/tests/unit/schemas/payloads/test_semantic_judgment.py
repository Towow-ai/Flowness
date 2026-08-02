"""Unit tests for semantic_judgment payload schemas (M-0.1 §3.2 + Patch M-2.3-A/C)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    AuditTriggerReason,
    AuditVerdictValue,
    ConsistencyVerdictValue,
    DecidedState,
    DecisionMadeBy,
    EvidenceSourceType,
    ImpactType,
    JudgmentType,
    LearningType,
    ObligationScopeJudgment,
    SemanticUpgradeChangeType,
    SubjectEntityType,
    TaskType,
)
from towow.schemas.payloads import semantic_judgment as sj
from towow.schemas.payloads.state_transition import EvidenceItem


def _ev() -> EvidenceItem:
    return EvidenceItem(
        source_type=EvidenceSourceType.EVENT,
        source_id="evt-x",
        relevance="trace",
    )


def test_nature_judgment_captured_confidence_must_be_null() -> None:
    """invariant: Nature 判断不打 confidence."""
    sj.NatureJudgmentCapturedPayload(
        after_state=sj.NatureJudgmentCapturedAfter(
            judgment_id="nj-1",
            quote="Don't make stopping condition a checklist",
            meaning="resist procedural reductionism",
            scope="all design tasks",
            is_value_judgment=True,
        ),
        evidence=[
            EvidenceItem(
                source_type=EvidenceSourceType.NATURE_QUOTE,
                source_id="session-001",
                relevance="direct",
            ),
        ],
    )


def test_nature_judgment_rejects_confidence() -> None:
    with pytest.raises(ValidationError):
        sj.NatureJudgmentCapturedPayload(
            after_state=sj.NatureJudgmentCapturedAfter(
                judgment_id="nj-2",
                quote="x",
                meaning="x",
                scope="x",
                is_value_judgment=False,
            ),
            confidence=0.9,  # type: ignore[arg-type]  # must be None
            evidence=[_ev()],
        )


def test_semantic_upgrade_declaration_payload() -> None:
    sj.SemanticUpgradeDeclarationPayload(
        after_state=sj.SemanticUpgradeDeclarationAfter(
            sibling_event_id="evt-cc-1",
            semantic_changes=[
                sj.SemanticChange(
                    change_type=SemanticUpgradeChangeType.NEW_CONCEPT,
                    affected_entity_id="c-1",
                    relationship_to_graph="adds isolated concept node",
                ),
            ],
        ),
    )


def test_resolver_decision_made_payload() -> None:
    p = sj.ResolverDecisionMadePayload(
        after_state=sj.ResolverDecisionMadeAfter(
            resolver_id="ObligationActivationResolver",
            task_id="t-1",
            input_hash="sha256:abc",
            pipeline_stages=sj.ResolverPipelineStages(
                stage_2_candidate_retrieval={"total_candidates": 5},
                stage_3_mechanical_filtering={"hit_mechanically": []},
                stage_4_semantic_judgment={"judgments": []},
                stage_6_placement=[],
            ),
            final_active_set=["obl-1"],
            cache_hit=False,
            resolver_runtime_ms=420,
        ),
        fallback_applied=False,
    )
    assert p.judgment_type is JudgmentType.RESOLVER_DECISION


def test_obligation_scope_judged_payload() -> None:
    sj.ObligationScopeJudgedPayload(
        after_state=sj.ObligationScopeJudgedAfter(
            obligation_id="obl-1",
            task_scope=sj.TaskScopeRef(
                task_id="t-1",
                task_type=TaskType.CODE_CHANGE,
                target_artifacts=["src/auth.py"],
            ),
            judgment=ObligationScopeJudgment.IN_SCOPE,
            scope_rule_applied="applies when modifying src/auth/",
        ),
        confidence=0.85,
        evidence=[_ev()],
    )


def test_obligation_scope_confidence_clamped() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        sj.ObligationScopeJudgedPayload(
            after_state=sj.ObligationScopeJudgedAfter(
                obligation_id="obl-1",
                task_scope=sj.TaskScopeRef(
                    task_id="t-1",
                    task_type=TaskType.CODE_CHANGE,
                ),
                judgment=ObligationScopeJudgment.UNCERTAIN,
                scope_rule_applied="x",
            ),
            confidence=1.5,
            evidence=[_ev()],
        )


def test_risk_surface_assigned_payload() -> None:
    sj.RiskSurfaceAssignedPayload(
        after_state=sj.RiskSurfaceAssignedAfter(
            finding_id="f-1",
            risk_surface="auth_module",
            assignment_reason="touches authentication path",
        ),
        confidence=0.9,
        evidence=[_ev()],
    )


def test_impact_assessment_made_payload() -> None:
    sj.ImpactAssessmentMadePayload(
        after_state=sj.ImpactAssessmentMadeAfter(
            change_event_id="evt-1",
            affected_entities=[
                sj.AffectedEntityWithImpact(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id="c-1",
                    impact_type=ImpactType.DIRECT,
                ),
            ],
            assessment_summary="touches authentication concept",
        ),
        confidence=0.75,
        evidence=[_ev()],
    )


def test_consistency_judgment_made_payload() -> None:
    sj.ConsistencyJudgmentMadePayload(
        after_state=sj.ConsistencyJudgmentMadeAfter(
            subject_event_id="evt-x",
            reference_event_ids=["evt-y"],
            verdict=ConsistencyVerdictValue.INCONSISTENT,
            inconsistency_detail="mismatched contract",
        ),
        confidence=0.95,
        evidence=[_ev()],
    )


def test_mismatch_verdict_payload_deleted() -> None:
    """T-L1-46: MismatchVerdict event + payload deleted (M-1.4 §3.4 v2.1).

    mismatch-judge fork 已删, MismatchVerdict 失去 owner; 功能并入 MismatchResolutionDecided
    (executor 自决) / AdvisorVerdictDelivered (经 advisor)。这条守门防 payload / judgment_type /
    EventType 任一被重新引回 (回归 = 方向相反残留复发)。详 LEDGER Conflict 10。
    """
    assert not hasattr(sj, "MismatchVerdictPayload")
    assert not hasattr(sj, "MismatchVerdictAfter")
    assert "MISMATCH_VERDICT" not in JudgmentType.__members__
    assert "mismatch_verdict" not in {j.value for j in JudgmentType}


def test_critical_path_identified_payload() -> None:
    sj.CriticalPathIdentifiedPayload(
        after_state=sj.CriticalPathIdentifiedAfter(
            plan_id="plan-1",
            critical_path_tasks=["t-1", "t-2", "t-3"],
        ),
        confidence=0.8,
        evidence=[
            EvidenceItem(
                source_type=EvidenceSourceType.PROJECTION,
                source_id="task_graph",
                relevance="task dependencies",
            ),
        ],
    )


def test_audit_triggered_payload() -> None:
    p = sj.AuditTriggeredPayload(
        after_state=sj.AuditTriggeredAfter(
            trigger_reason=AuditTriggerReason.HIGH_RISK_PATCH,
            envelope_event_id="evt-env-1",
            audit_scope="auth module changes",
        ),
    )
    assert p.confidence is None


def test_audit_verdict_received_payload() -> None:
    sj.AuditVerdictReceivedPayload(
        after_state=sj.AuditVerdictReceivedAfter(
            audit_id="a-1",
            trigger_event_id="evt-at-1",
            verdict=AuditVerdictValue.PASS,
            findings=[sj.AuditFinding(description="ok", severity="minor")],
        ),
        confidence=1.0,
        evidence=[_ev()],
    )


# ─── Phase D ────────────────────────────────────────────────────────────────────


@pytest.mark.patch
def test_escalation_decision_made_patch_m23c_nature() -> None:  # Patch M-2.3-C
    """decision_made_by=nature ↔ confidence is None."""
    p = sj.EscalationDecisionMadePayload(
        after_state=sj.EscalationDecisionMadeAfter(
            decided_state=DecidedState.RESOLVED,
            decision_summary="approve workaround",
            decision_made_by=DecisionMadeBy.NATURE,
        ),
    )
    assert p.confidence is None
    assert p.judgment_type == "escalation_decision"


@pytest.mark.patch
def test_escalation_decision_made_patch_m23c_agent() -> None:  # Patch M-2.3-C
    sj.EscalationDecisionMadePayload(
        after_state=sj.EscalationDecisionMadeAfter(
            decided_state=DecidedState.DEFERRED,
            decision_summary="agent self-resolved",
            decision_made_by=DecisionMadeBy.AGENT_SELF_RESOLVED,
        ),
        confidence=0.7,
    )


@pytest.mark.patch
def test_escalation_learning_captured_payload_patch_m23a() -> None:  # Patch M-2.3-A
    sj.EscalationLearningCapturedPayload(
        after_state=sj.EscalationLearningCapturedAfter(
            learning_type=LearningType.DETECTION_RULE_CANDIDATE,
            seed_for_event_id="evt-drp-1",
            capturing_reasoning="frequent escalation, mechanical rule possible",
            nature_already_provided_feedback=False,
        ),
        confidence=0.8,
        evidence=[_ev()],
    )


@pytest.mark.patch
def test_escalation_learning_no_learning_seed_optional() -> None:  # Patch M-2.3-A invariant
    """learning_type=no_learning ↔ seed_for_event_id is None (schema-level optional, runtime invariant)."""
    p = sj.EscalationLearningCapturedPayload(
        after_state=sj.EscalationLearningCapturedAfter(
            learning_type=LearningType.NO_LEARNING,
            seed_for_event_id=None,
            capturing_reasoning="one-off event, no pattern",
            nature_already_provided_feedback=False,
        ),
        confidence=0.5,
        evidence=[_ev()],
    )
    assert p.after_state.learning_type is LearningType.NO_LEARNING


# ─── Patch grep anchors + spec source ──────────────────────────────────────────


_SJ_SOURCE = Path(sj.__file__).read_text(encoding="utf-8")


@pytest.mark.patch
@pytest.mark.parametrize("anchor", ["Patch M-2.3-A", "Patch M-2.3-C"])
def test_semantic_judgment_patch_anchors(anchor: str) -> None:
    assert anchor in _SJ_SOURCE


def test_semantic_judgment_spec_source_header() -> None:
    assert "spec source" in _SJ_SOURCE
    assert "§3.2" in _SJ_SOURCE
