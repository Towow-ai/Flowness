"""Unit tests for capsule.py + capsule_templates.py (Step 1.f)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from towow.schemas.capsule import CapsuleResult, CapsuleSectionContent, TruncationLogEntry
from towow.schemas.capsule_templates import (
    MAINTENANCE_TEMPLATE,
    SCENE_TEMPLATES,
    MaintenanceCallerParams,
    SceneTemplate,
    get_scene_template,
)
from towow.schemas.enums import (
    CapsuleSection,
    EventType,
    MaintenanceMode,
    ProjectionName,
    SceneType,
)


def _section(section: CapsuleSection, tokens: int = 100) -> CapsuleSectionContent:
    return CapsuleSectionContent(section=section, content="...", estimated_tokens=tokens)


def test_capsule_result_minimal_valid() -> None:
    c = CapsuleResult(
        input_hash="sha256:abc",
        scene_type=SceneType.EXECUTION,
        target_fork_id="fork-1",
        sections=[
            _section(CapsuleSection.RED_LINE_OBLIGATIONS, 200),
            _section(CapsuleSection.TASK, 300),
            _section(CapsuleSection.MUST_RETURN, 100),
        ],
        estimated_tokens=600,
        max_tokens=80000,
    )
    assert c.truncation_applied is False
    assert len(c.sections) == 3


def test_capsule_result_with_truncation_log() -> None:
    c = CapsuleResult(
        input_hash="sha256:abc",
        scene_type=SceneType.PLANNING,
        target_fork_id="fork-2",
        sections=[_section(CapsuleSection.TASK, 100)],
        estimated_tokens=80000,
        max_tokens=80000,
        truncation_applied=True,
        truncation_log=[
            TruncationLogEntry(
                section=CapsuleSection.KNOWN_FACTS,
                method="reduce_neighborhood_hops",
                tokens_before=15000,
                tokens_after=5000,
            ),
        ],
    )
    assert c.truncation_applied
    assert c.truncation_log[0].method == "reduce_neighborhood_hops"


def test_capsule_result_sections_min_length() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        CapsuleResult(
            input_hash="x",
            scene_type=SceneType.EXECUTION,
            target_fork_id="f-1",
            sections=[],
            estimated_tokens=0,
            max_tokens=80000,
        )


# ─── 9 scene templates ──────────────────────────────────────────────────────────


def test_scene_templates_registered() -> None:
    """All 10 SceneType values have a registered template (9 base + Patch K consolidation)."""
    assert len(SCENE_TEMPLATES) == 10
    for scene_type in SceneType:
        tmpl = SCENE_TEMPLATES[scene_type]
        assert tmpl.scene_type is scene_type
        assert len(tmpl.required_projections) >= 1


def test_patch_k_consolidation_template_specifics() -> None:
    """M-0.7 §10.3 Patch K / §6.2 — consolidation scene template registered with spec shape.

    §6.2 fixes: neighborhood_hops=0 (consolidation 看全局不做邻域扩展), required_projections
    含 concept_graph (GC roots) + obligation_lifecycle + finding_lifecycle + ownership (lock),
    MUST_RETURN 含 consolidation envelope schema, MUST_NOT_DO 含三条硬约束 (不改 immutable_truth /
    不绕 verify / 不超 lookback window)。
    """
    t = SCENE_TEMPLATES[SceneType.CONSOLIDATION]
    assert t.scene_type is SceneType.CONSOLIDATION
    # §6.2: neighborhood_hops: 0 (consolidation 不做邻域扩展，看全局)
    assert t.neighborhood_hops == 0
    # §6.2 required_projections — the L0 frozen-registry subset that exists as ProjectionName
    assert ProjectionName.CONCEPT_GRAPH in t.required_projections  # GC 根集
    assert ProjectionName.OBLIGATION_LIFECYCLE_STATE in t.required_projections  # active obligation
    assert ProjectionName.FINDING_LIFECYCLE in t.required_projections  # 活跃 Finding
    assert ProjectionName.OWNERSHIP in t.required_projections  # lock
    # §6.2 additional_event_reads — 最近 SnapshotCreated + NatureJudgmentCaptured (learning window)
    assert EventType.SNAPSHOT_CREATED in t.additional_event_reads
    assert EventType.NATURE_JUDGMENT_CAPTURED in t.additional_event_reads
    # §6.2 capsule_section_customization — MUST_RETURN consolidation envelope schema + MUST_NOT_DO
    assert CapsuleSection.MUST_RETURN in t.capsule_section_customization
    assert CapsuleSection.MUST_NOT_DO in t.capsule_section_customization
    # §6.2 MUST_NOT_DO 必含三条硬约束的语义锚 (immutable_truth / verify / lookback)
    must_not = t.capsule_section_customization[CapsuleSection.MUST_NOT_DO]
    assert "immutable_truth" in must_not
    assert "verify" in must_not
    assert "lookback" in must_not


def test_get_scene_template_returns_correct() -> None:
    tmpl = get_scene_template(SceneType.MAINTENANCE)
    assert tmpl is MAINTENANCE_TEMPLATE


def test_scene_template_hops_bounded_at_5() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        SceneTemplate(
            scene_type=SceneType.AUDIT,
            required_projections=[ProjectionName.CONCEPT_GRAPH],
            neighborhood_hops=6,
            neighborhood_center="x",
        )


def test_interview_template_specifics() -> None:
    t = SCENE_TEMPLATES[SceneType.INTERVIEW]
    assert t.neighborhood_hops == 1
    assert EventType.NATURE_JUDGMENT_CAPTURED in t.additional_event_reads
    assert ProjectionName.CONCEPT_GRAPH in t.required_projections
    assert CapsuleSection.KNOWN_FACTS in t.capsule_section_customization


@pytest.mark.patch
def test_maintenance_template_caller_params_patch_m22d() -> None:  # Patch M-2.2-D
    """Patch M-2.2-D: maintenance template caller_params adds 3 fields."""
    params = MaintenanceCallerParams(
        maintenance_mode=MaintenanceMode.CONSOLIDATE,
        lookback_window="30d",
        shared_knowledge_required=["concept_freshness_guide"],
    )
    assert params.maintenance_mode is MaintenanceMode.CONSOLIDATE
    assert params.lookback_window == "30d"


@pytest.mark.patch
def test_maintenance_caller_params_optional_lookback() -> None:
    p = MaintenanceCallerParams(maintenance_mode=MaintenanceMode.GC)
    assert p.lookback_window is None
    assert p.shared_knowledge_required == []


# ─── Patch grep anchor ──────────────────────────────────────────────────────────


_TEMPLATES_SOURCE = Path(MAINTENANCE_TEMPLATE.__class__.__module__).name
# Patch M-2.2-D anchor should be in capsule_templates.py source
from towow.schemas import capsule_templates as ct  # noqa: E402

_CT_SOURCE = Path(ct.__file__).read_text(encoding="utf-8")


@pytest.mark.patch
def test_patch_m22d_anchor_in_templates() -> None:
    assert "Patch M-2.2-D" in _CT_SOURCE
