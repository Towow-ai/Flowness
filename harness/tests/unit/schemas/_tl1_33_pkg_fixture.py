"""Shared fixture for T-L1-33 progressive-publish gate tests: a minimal self-contained package."""

from __future__ import annotations

from towow.schemas.enums import ClosureVerificationMethod, TaskModelTier, TaskType
from towow.schemas.payloads.finding import ClosureCriterion
from towow.schemas.payloads.task_package import (
    ConceptRefEntry,
    DoneCriterion,
    EntityRefDerived,
    EvidenceRef,
    TaskPackageContent,
)


def minimal_self_contained_content() -> TaskPackageContent:
    """A package that passes every §11.1 structural threshold (so only the T-L1-33 progressive-
    publish gate can be the deciding blocker in the e2e test)."""
    return TaskPackageContent(
        task_type=TaskType.CODE_CHANGE,
        description="下游 task 实现",
        done_criteria=[
            DoneCriterion(
                criterion_id="dc-1",
                description="测试通过",
                verification_type="test_passes",
                evidence_ref=EvidenceRef(
                    source_type="brief.completion_condition",
                    source_id="obs-1",
                    quoted_claim="下游可验证",
                ),
                # finding-r05: test_passes 必须并带 gate-recomputable machine_check 才过发布门。
                machine_check=ClosureCriterion(
                    condition="下游 task 测试通过",
                    verification_method=ClosureVerificationMethod.TEST,
                    expected_result="pytest exit 0",
                    test_selector="tests/unit/test_downstream.py::test_impl",
                ),
            ),
        ],
        concept_refs=[
            ConceptRefEntry(
                concept_id="concept-x",
                at_reference="@concept:x",
                locking_policy="pin_to_snapshot",
                why_locked="基于确定概念",
                lock_event_id="evt-lock-1",
            ),
        ],
        file_refs=[],
        read_set=[EntityRefDerived(entity_type="concept", entity_id="concept-x", derived_from="cr-1")],
        write_set=[EntityRefDerived(entity_type="file", entity_id="x.py", derived_from="dc-1")],
        active_obligations=[],
        constraints=[],
        model_tier=TaskModelTier.SONNET,
        model_tier_rationale="标准实现",
        review_required=False,
        completion_checks=[],
    )
