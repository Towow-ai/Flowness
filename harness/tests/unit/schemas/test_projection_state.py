"""Unit tests for projection_state.py — 10 typed graph + 6 UI stubs (Step 1.g)."""

from __future__ import annotations

import pytest

from towow.schemas.enums import (
    ConceptEdgeDirection,
    ConceptEdgeType,
    DetectionRuleLifecycleState,
    EscalationLifecycleState,
    FindingDetectionMethod,
    FindingLifecycleState,
    FindingSeverity,
    ObligationCanonicalState,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SubjectEntityType,
    TaskDependencyType,
    TaskType,
)
from towow.schemas.projection_state import (
    AtReferenceEndpoint,
    AtReferenceEntry,
    AtReferenceGraphState,
    CapsuleInspectorState,
    CommitHistoryEntry,
    CommitHistoryProjection,
    ConceptEdge,
    ConceptGraphState,
    ConceptNode,
    ConsumerEntry,
    ConsumerRelationEntry,
    ConsumerRelationState,
    DashboardState,
    DetectionRuleEntry,
    DetectionRuleLifecycleProjection,
    DriftStreamState,
    EntityRef,
    EscalationLifecycleEntry,
    EscalationLifecycleProjection,
    FindingLifecycleEntry,
    FindingLifecycleProjection,
    ObligationHeatmapState,
    ObligationLifecycleNode,
    ObligationLifecycleStateProjection,
    OwnershipEntry,
    OwnershipState,
    ResolverTrendState,
    ReviewInboxState,
    TaskEdge,
    TaskGraphState,
    TaskNode,
)


def test_concept_graph_state_empty() -> None:
    cg = ConceptGraphState()
    assert cg.nodes == []
    assert cg.edges == []


def test_concept_graph_state_populated() -> None:
    cg = ConceptGraphState(
        nodes=[
            ConceptNode(
                concept_id="c-1",
                name="Auth",
                definition="rate-limited auth",
                created_by_stage="engineering_consensus",
                created_at_event_id="evt-1",
            ),
        ],
        edges=[
            ConceptEdge(
                edge_id="e-1",
                source_concept_id="c-1",
                target_concept_id="c-2",
                edge_type=ConceptEdgeType.DEPENDENCY,
                direction=ConceptEdgeDirection.UNIDIRECTIONAL,
                created_at_event_id="evt-2",
            ),
        ],
        supersede_index={"c-1": ["evt-supersede-1"]},
    )
    assert cg.nodes[0].concept_id == "c-1"
    assert cg.edges[0].is_active is True


def test_task_graph_state() -> None:
    tg = TaskGraphState(
        nodes=[
            TaskNode(
                task_id="t-1",
                task_type=TaskType.CODE_CHANGE,
                description="implement rate limiting",
                status="planned",
            ),
        ],
        edges=[
            TaskEdge(
                source_task_id="t-1",
                target_task_id="t-2",
                dependency_type=TaskDependencyType.DATA_DEPENDENCY,
            ),
        ],
    )
    assert tg.nodes[0].task_type is TaskType.CODE_CHANGE


@pytest.mark.patch  # Narrow Patch E: canonical_state in obligation_lifecycle
def test_obligation_lifecycle_with_canonical_state() -> None:
    """Narrow Patch E: obligation_lifecycle entry includes canonical_state field."""
    p = ObligationLifecycleStateProjection(
        obligations=[
            ObligationLifecycleNode(
                obligation_id="obl-1",
                lifecycle_state=ObligationFieldsLifecycleState.CAPTURED,
                canonical_state=ObligationCanonicalState.ACTIVE,
                scope_rule="all patches",
                scope_type=ObligationScopeType.ALL_PATCHES,
                severity=ObligationSeverity.RED_LINE,
                nature=ObligationNature.INTENT_ATTRIBUTION,
                definition="every patch traces to Goal",
                created_at_event_id="evt-1",
                last_state_change_event_id="evt-1",
            ),
        ],
    )
    assert p.obligations[0].canonical_state is ObligationCanonicalState.ACTIVE


def test_ownership_state() -> None:
    o = OwnershipState(
        entities=[
            OwnershipEntry(
                entity_type=SubjectEntityType.FILE,
                entity_id="src/x.py",
                owner_task_id="t-1",
                write_locked=True,
                lock_holder_run_id="r-1",
            ),
        ],
    )
    assert o.entities[0].write_locked is True


def test_at_reference_graph_state() -> None:
    AtReferenceGraphState(
        references=[
            AtReferenceEntry(
                reference_id="ref-1",
                source=AtReferenceEndpoint(
                    entity_type=SubjectEntityType.CONCEPT, entity_id="c-1"
                ),
                target=AtReferenceEndpoint(
                    entity_type=SubjectEntityType.TASK, entity_id="t-1"
                ),
                reference_type="semantic",
            ),
        ],
    )


def test_consumer_relation_state() -> None:
    ConsumerRelationState(
        relations=[
            ConsumerRelationEntry(
                concept_id="c-1",
                consumers=[
                    ConsumerEntry(
                        entity_type=SubjectEntityType.TASK,
                        entity_id="t-1",
                        consumption_type="reads",
                    ),
                ],
            ),
        ],
    )


def test_finding_lifecycle_projection() -> None:
    FindingLifecycleProjection(
        findings=[
            FindingLifecycleEntry(
                finding_id="f-1",
                lifecycle_state=FindingLifecycleState.CREATED,
                severity=FindingSeverity.MAJOR,
                risk_surface="auth",
                detection_method=FindingDetectionMethod.MANUAL_REVIEW,
                created_at_event_id="evt-1",
                last_state_change_event_id="evt-1",
            ),
        ],
    )


def test_detection_rule_lifecycle_projection() -> None:
    DetectionRuleLifecycleProjection(
        rules=[
            DetectionRuleEntry(
                rule_id="r-1",
                lifecycle_state=DetectionRuleLifecycleState.SHADOW,
                rule_definition="no unbounded loops",
                shadow_stats={"runs": 100, "hits": 5, "false_positives": 1},
                created_at_event_id="evt-1",
                last_state_change_event_id="evt-2",
            ),
        ],
    )


@pytest.mark.patch  # Patch M-2.3-E
def test_escalation_lifecycle_projection_patch_m23e() -> None:
    """Patch M-2.3-E: new escalation_lifecycle projection."""
    p = EscalationLifecycleProjection(
        escalations=[
            EscalationLifecycleEntry(
                escalation_id="esc-1",
                lifecycle_state=EscalationLifecycleState.TRIAGED,
                raised_at="2026-05-21T12:00:00Z",
                raised_by_skill="M-1.6",
                escalation_kind="multi_round_fix",
                triage_category="technical_blocker",
                triage_reasoning="no novelty after 3 retries",
                triaged_at="2026-05-21T12:05:00Z",
            ),
        ],
    )
    assert p.escalations[0].lifecycle_state is EscalationLifecycleState.TRIAGED


def test_commit_history_projection() -> None:
    CommitHistoryProjection(
        commits=[
            CommitHistoryEntry(
                commit_event_id="evt-c-1",
                envelope_event_id="evt-env-1",
                verdict="accepted",
                occurred_at_event_id="evt-c-1",
            ),
        ],
    )


# ─── 6 UI view stubs ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cls",
    [
        ReviewInboxState,
        DashboardState,
        CapsuleInspectorState,
        ResolverTrendState,
        ObligationHeatmapState,
        DriftStreamState,
    ],
)
def test_ui_view_stub_defaults(cls: type) -> None:
    instance = cls()
    assert instance.entry_count == 0
    assert instance.last_updated_event_id is None


def test_entity_ref_shared() -> None:
    """EntityRef is the shared (entity_type, entity_id) wrapper."""
    e = EntityRef(entity_type=SubjectEntityType.OBLIGATION, entity_id="obl-1")
    assert e.entity_type is SubjectEntityType.OBLIGATION
