"""T-JLM-01 judgment-case@v1 单测 — 9 字段 schema + draft→enriched 转换 (dc-1) + 纠正走 supersede (dc-2)。

done_criteria 精确对应:
  dc-1-schema           : JudgmentCase 9 字段 strict schema + enrichment_status draft→enriched 转换。
  dc-2-supersede-correction: 对已 enriched 判例做纠正时新事件带 is_supersede=true + superseded_event_id
                            指回旧事件, 不新造纠正机制 (同 JUDGMENT_CASE_ENRICHED 类型 + 通用 supersede)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from towow.l0.event_log import EventLog
from towow.l1.judgment_case import correct_judgment_case, emit_judgment_case
from towow.schemas.enums import (
    EnrichmentStatus,
    EventType,
    Stability,
    SupersedeNoveltyType,
    WeightTier,
)
from towow.schemas.payloads.judgment_case import JudgmentCase
from towow.schemas.payloads.registry import conforms

if TYPE_CHECKING:
    from pathlib import Path


# ─── dc-1: 9 字段 strict schema ────────────────────────────────────────────────


def test_nine_fields_exactly() -> None:
    """判例恰好 9 个字段 (source_event_id 兼作身份, 无独立 case_id)。"""
    assert set(JudgmentCase.model_fields) == {
        "source_event_id",
        "scenario",
        "weight_tier",
        "stability",
        "applicable_conditions",
        "non_applicable_conditions",
        "counter_example_case_ids",
        "affordance",
        "enrichment_status",
    }


def test_schema_is_strict_extra_forbidden() -> None:
    """strict schema — 未知字段被拒 (extra=forbid)。"""
    with pytest.raises(ValidationError):
        JudgmentCase(
            source_event_id="evt-x",
            scenario="s",
            enrichment_status=EnrichmentStatus.DRAFT,
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_source_event_id_and_scenario_non_empty() -> None:
    """source_event_id / scenario 不能空。"""
    with pytest.raises(ValidationError):
        JudgmentCase(source_event_id="", scenario="s", enrichment_status=EnrichmentStatus.DRAFT)
    with pytest.raises(ValidationError):
        JudgmentCase(source_event_id="evt-x", scenario="", enrichment_status=EnrichmentStatus.DRAFT)


def test_stability_defaults_temporary_tactic() -> None:
    """stability 缺省 temporary_tactic (骨架期即在)。"""
    d = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="s")
    assert d.stability is Stability.TEMPORARY_TACTIC


# ─── dc-1: draft → enriched 转换 ───────────────────────────────────────────────


def test_draft_skeleton_is_minimal() -> None:
    """draft = 仅 source_event_id + scenario 的最小骨架 (weight_tier=None, 行李全空)。"""
    d = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="interview — brief 确认")
    assert d.enrichment_status is EnrichmentStatus.DRAFT
    assert d.weight_tier is None
    assert d.applicable_conditions == []
    assert d.non_applicable_conditions == []
    assert d.counter_example_case_ids == []
    assert d.affordance == []


def test_draft_to_enriched_transition() -> None:
    """draft.enrich() → enriched, 五行李字段实质有值。"""
    d = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="s")
    e = d.enrich(
        weight_tier=WeightTier.REAL_DECISION,
        applicable_conditions=["自动化路径类决策"],
        affordance=["下次遇到二元开关式提问时想起来: 追问路径而非要不要"],
        non_applicable_conditions=["纯执行细节"],
    )
    assert e.enrichment_status is EnrichmentStatus.ENRICHED
    assert e.weight_tier is WeightTier.REAL_DECISION
    assert e.applicable_conditions == ["自动化路径类决策"]
    assert e.affordance
    assert e.non_applicable_conditions == ["纯执行细节"]
    # 骨架身份字段跨转换不变
    assert e.source_event_id == d.source_event_id
    assert e.scenario == d.scenario


def test_draft_forbids_luggage() -> None:
    """draft 是最小骨架 — 带任何行李字段直接被拒。"""
    with pytest.raises(ValidationError):
        JudgmentCase(
            source_event_id="evt-x",
            scenario="s",
            weight_tier=WeightTier.CORRECTION,
            enrichment_status=EnrichmentStatus.DRAFT,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"applicable_conditions": ["a"], "affordance": ["f"]},  # 缺 weight_tier
        {"weight_tier": WeightTier.CORRECTION, "affordance": ["f"]},  # 缺 applicable
        {"weight_tier": WeightTier.CORRECTION, "applicable_conditions": ["a"]},  # 缺 affordance
    ],
)
def test_enriched_requires_substantive_luggage(kwargs: dict[str, object]) -> None:
    """enriched 要求 weight_tier + applicable_conditions + affordance 实质有值, 缺一即拒。"""
    with pytest.raises(ValidationError):
        JudgmentCase(
            source_event_id="evt-x",
            scenario="s",
            enrichment_status=EnrichmentStatus.ENRICHED,
            **kwargs,  # type: ignore[arg-type]
        )


def test_enrich_only_from_draft() -> None:
    """enrich() 只能作用于 draft; 对已 enriched 再 enrich 报错。"""
    e = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="s").enrich(
        weight_tier=WeightTier.CORRECTION, applicable_conditions=["a"], affordance=["f"],
    )
    with pytest.raises(ValueError, match="enrich"):
        e.enrich(weight_tier=WeightTier.CORRECTION, applicable_conditions=["a"], affordance=["f"])


def test_payload_conforms_to_registry() -> None:
    """enriched 判例作为 JUDGMENT_CASE_ENRICHED 的 payload 通过 registry 校验 (ENFORCED 不会拒真产物)。"""
    e = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="s").enrich(
        weight_tier=WeightTier.ACTIVE_PREFERENCE, applicable_conditions=["a"], affordance=["f"],
    )
    assert conforms(EventType.JUDGMENT_CASE_ENRICHED, e.model_dump(mode="json")) is None


# ─── dc-2: 纠正走标准 envelope 通用 supersede ────────────────────────────────────


def _make_log(tmp_path: Path) -> EventLog:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    log_path = towow / "events.log"
    log_path.write_text("", encoding="utf-8")
    return EventLog(log_path)


def test_revise_produces_enriched_with_changed_fields() -> None:
    """revise() 只改传入字段, 其余沿用, 结果仍 enriched。"""
    e = JudgmentCase.draft_skeleton(source_event_id="evt-x", scenario="s").enrich(
        weight_tier=WeightTier.ACTIVE_PREFERENCE, applicable_conditions=["a"], affordance=["f"],
    )
    r = e.revise(weight_tier=WeightTier.CORRECTION, non_applicable_conditions=["新反例"])
    assert r.enrichment_status is EnrichmentStatus.ENRICHED
    assert r.weight_tier is WeightTier.CORRECTION  # 改了
    assert r.applicable_conditions == ["a"]  # 沿用
    assert r.non_applicable_conditions == ["新反例"]  # 改了


def test_correction_emits_supersede_not_new_mechanism(tmp_path: Path) -> None:
    """纠正 = 新 JUDGMENT_CASE_ENRICHED 事件带 is_supersede=true + superseded_event_id 指回旧事件。

    钉死 dc-2: 不新造纠正事件类型 (同 JUDGMENT_CASE_ENRICHED), 走通用 EventBase.supersede。
    """
    log = _make_log(tmp_path)
    enriched = JudgmentCase.draft_skeleton(source_event_id="evt-nj-1", scenario="s").enrich(
        weight_tier=WeightTier.ACTIVE_PREFERENCE, applicable_conditions=["a"], affordance=["f"],
    )
    first = emit_judgment_case(log, enriched)
    assert first.supersede.is_supersede is False

    correction = correct_judgment_case(
        log,
        prior_event_id=first.event_id,
        revised=enriched.revise(weight_tier=WeightTier.CORRECTION),
        novelty="Nature 后续反应把这条从偏好升级为纠正",
    )
    # 不新造机制: 纠正事件类型仍是 JUDGMENT_CASE_ENRICHED (非 "JudgmentCaseCorrected")
    assert correction.event_type is EventType.JUDGMENT_CASE_ENRICHED
    # 走通用 envelope supersede
    assert correction.supersede.is_supersede is True
    assert correction.supersede.superseded_event_id == first.event_id
    assert correction.supersede.novelty_type is SupersedeNoveltyType.NATURE_OVERRIDE
    assert correction.supersede.novelty
    # payload 是修正后的判例 (weight_tier 变了), source_event_id 不变 (同一判例的新版本)
    assert correction.payload["weight_tier"] == WeightTier.CORRECTION.value
    assert correction.payload["source_event_id"] == "evt-nj-1"


def test_correction_rejects_draft_as_revision(tmp_path: Path) -> None:
    """纠正产出必须是 enriched (draft 骨架不能作为纠正版本)。"""
    log = _make_log(tmp_path)
    draft = JudgmentCase.draft_skeleton(source_event_id="evt-nj-1", scenario="s")
    first = emit_judgment_case(log, draft)
    with pytest.raises(ValueError, match="enriched"):
        correct_judgment_case(
            log,
            prior_event_id=first.event_id,
            revised=draft,
            novelty="试图用 draft 纠正",
        )
