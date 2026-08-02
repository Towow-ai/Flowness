"""波2 T-L1-26: Model tier 评分算法 §10.3 opus_score 加权 + §10.6 evidence 校验.

# spec source:
#   04-l1-intelligence/M-1.3-planner-skill-detailed-design.md §10.2-10.6
#   src/towow/skills/planning/knowledge/model-tier-policy.md
#
done_criteria (源表): plan-model-tier 用算法算 score, 默认 reason 串被 freeze 拒; 现 --tier
自由选 + 默认 reason。本测试钉住 §10.3 决策算法 (各分支) + §10.4 组合规则 + §10.6 evidence
fail-closed (默认 placeholder reason / 无因素 被拒)。
"""

from __future__ import annotations

import pytest

from towow.schemas.payloads.model_tier_scoring import (
    OPUS_FACTOR_WEIGHTS,
    score_tier,
    validate_tier_assignment_evidence,
)


# ─── §10.2 weights — spec ground-truth ────────────────────────────────────────
def test_weights_match_spec() -> None:
    assert OPUS_FACTOR_WEIGHTS["red_line_obligation"] == 3
    assert OPUS_FACTOR_WEIGHTS["state_machine_or_invariant_change"] == 3
    assert OPUS_FACTOR_WEIGHTS["critical_path_medium_plus"] == 3
    assert OPUS_FACTOR_WEIGHTS["high_fan_out_concept"] == 2
    assert OPUS_FACTOR_WEIGHTS["write_set_gt_3"] == 2
    assert OPUS_FACTOR_WEIGHTS["cross_module"] == 1
    assert OPUS_FACTOR_WEIGHTS["complex_task_description"] == 1


# ─── §10.3 / §10.4 decision algorithm ─────────────────────────────────────────
def test_weight3_factor_one_vote_opus() -> None:
    """Rule 3: 任一 weight=3 因素 → opus 一票通过。"""
    r = score_tier(["red_line_obligation"])
    assert r.tier == "opus"
    assert r.opus_score == 3


def test_two_weight2_factors_sum_to_opus() -> None:
    """Rule 2: 多个 weight=2 因素累加 → opus。"""
    r = score_tier(["high_fan_out_concept", "write_set_gt_3"])
    assert r.opus_score == 4
    assert r.tier == "opus"


def test_single_weight2_is_opus_conservative() -> None:
    r = score_tier(["supersede_decision"])
    assert r.opus_score == 2
    assert r.tier == "opus"


def test_score1_non_critical_is_sonnet() -> None:
    r = score_tier(["cross_module"])
    assert r.opus_score == 1
    assert r.tier == "sonnet"


def test_score1_on_critical_path_is_opus() -> None:
    """Rule: opus_score==1 + 关键路径 → opus。"""
    r = score_tier(["cross_module"], on_critical_path=True)
    assert r.opus_score == 1
    assert r.tier == "opus"


def test_score0_with_sonnet_factor_is_sonnet() -> None:
    r = score_tier([], ["standard_implementation", "single_module"])
    assert r.opus_score == 0
    assert r.tier == "sonnet"


def test_score0_no_factor_is_opus_conservative() -> None:
    """Rule 4: 完全无匹配 → opus (保守 — fork 看不清不该决定)。"""
    r = score_tier([], [])
    assert r.opus_score == 0
    assert r.tier == "opus"


def test_unknown_factor_rejected() -> None:
    with pytest.raises(ValueError, match="unknown opus factor"):
        score_tier(["not_a_real_factor"])


def test_matched_factors_carry_weight_evidence() -> None:
    r = score_tier(["red_line_obligation", "write_set_gt_3"])
    weights = {f["factor"]: f["weight"] for f in r.matched_opus_factors}
    assert weights == {"red_line_obligation": 3, "write_set_gt_3": 2}


# ─── §10.6 evidence requirement (freeze gate) ─────────────────────────────────
def test_default_placeholder_reason_rejected() -> None:
    ok, errors = validate_tier_assignment_evidence(
        reason="planner default tier assignment",
        matched_opus_factors=["red_line_obligation"],
        matched_sonnet_factors=[],
    )
    assert not ok
    assert any("placeholder" in e for e in errors)


def test_empty_reason_rejected() -> None:
    ok, _ = validate_tier_assignment_evidence(
        reason="",
        matched_opus_factors=["red_line_obligation"],
        matched_sonnet_factors=[],
    )
    assert not ok


def test_no_factors_rejected() -> None:
    ok, errors = validate_tier_assignment_evidence(
        reason="this is opus because it touches the commit gate core logic",
        matched_opus_factors=[],
        matched_sonnet_factors=[],
    )
    assert not ok
    assert any("evidence" in e for e in errors)


def test_real_reason_with_factors_accepted() -> None:
    ok, errors = validate_tier_assignment_evidence(
        reason="opus: changes runs.status state_machine semantics (weight-3 invariant factor)",
        matched_opus_factors=["state_machine_or_invariant_change"],
        matched_sonnet_factors=[],
    )
    assert ok, errors
