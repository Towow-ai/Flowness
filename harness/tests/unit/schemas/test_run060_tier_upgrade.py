"""RUN-060 EPIC-12: model tier 升级机制 (§10.5) — upgrade_tier 重评 + execution_escalation 因素.

# spec source:
#   04-l1-intelligence/M-1.3-planner-skill-detailed-design.md §10.5 升级机制 (sonnet → opus)
#   src/towow/skills/planning/knowledge/model-tier-policy.md §升级机制
#
合同 (RUN-060.md): 执行/escalation 后按更高 OPUS_FACTOR 重评 tier → re-emit 升级版
TaskModelTierAssigned。复用现成 score_tier + TaskModelTier enum, 补升级触发入口 + re-emit 路径。

本测钉住评分层 (upgrade_tier): escalation 因素 weight=3 ⇒ sonnet task escalation 后必升 opus;
已是 opus 则 push back 不升; 重评携 §10.6 evidence (matched factor 非空, 非 evidence-less 自动升)。
"""

from __future__ import annotations

import pytest

from towow.schemas.payloads.model_tier_scoring import (
    EXECUTION_ESCALATION_FACTOR,
    OPUS_FACTOR_WEIGHTS,
    upgrade_tier,
)


# ─── execution_escalation 是 weight=3 的 opus 因素 (§10.5 'sonnet 失败→自动升 opus' 用因素表达) ───
def test_escalation_factor_registered_weight3() -> None:
    assert EXECUTION_ESCALATION_FACTOR == "execution_escalation"
    assert OPUS_FACTOR_WEIGHTS["execution_escalation"] == 3


# ─── 核心: 低 tier (sonnet) escalation 后升 opus ───────────────────────────────
def test_sonnet_escalation_upgrades_to_opus() -> None:
    """sonnet task escalation → execution_escalation(w=3) ⇒ opus_score≥3 ⇒ opus (§10.4 Rule 3)。"""
    r = upgrade_tier("sonnet")
    assert r.upgraded is True
    assert r.from_tier == "sonnet"
    assert r.to_tier == "opus"
    assert r.score.opus_score >= 3
    # §10.6 evidence: 升级分配携 matched factor (非 evidence-less 自动升)。
    factors = {f["factor"] for f in r.score.matched_opus_factors}
    assert EXECUTION_ESCALATION_FACTOR in factors


def test_sonnet_with_prior_and_new_factors_carries_all_evidence() -> None:
    """prior 因素 + 执行新发现因素 + escalation 全进重评 evidence (可解释升级)。"""
    r = upgrade_tier(
        "sonnet",
        prior_opus_factors=["cross_module"],          # 原分配看出的 (w=1)
        additional_opus_factors=["write_set_gt_3"],   # 执行新发现 (w=2)
    )
    assert r.upgraded is True
    assert r.to_tier == "opus"
    factors = {f["factor"] for f in r.score.matched_opus_factors}
    assert factors == {"cross_module", "write_set_gt_3", EXECUTION_ESCALATION_FACTOR}
    # 1 + 2 + 3 = 6
    assert r.score.opus_score == 6


def test_dedup_prior_factor_equal_to_escalation() -> None:
    """prior 已含 escalation 因素 → 去重, 不重复计分。"""
    r = upgrade_tier("sonnet", prior_opus_factors=["execution_escalation"])
    names = [f["factor"] for f in r.score.matched_opus_factors]
    assert names.count(EXECUTION_ESCALATION_FACTOR) == 1
    assert r.score.opus_score == 3


# ─── push back: 已是最高 tier 不升 (§10.5 '拒绝→解释 push back') ────────────────
def test_opus_escalation_push_back_no_upgrade() -> None:
    r = upgrade_tier("opus")
    assert r.upgraded is False
    assert r.from_tier == "opus"
    assert r.to_tier == "opus"
    assert "push back" in r.push_back_reason
    assert "无法再升" in r.push_back_reason


# ─── 校验 ────────────────────────────────────────────────────────────────────
def test_unknown_current_tier_rejected() -> None:
    with pytest.raises(ValueError, match="unknown current_tier"):
        upgrade_tier("haiku")


def test_unknown_additional_factor_rejected() -> None:
    """新发现因素名非法 → score_tier 校验拒 (复用现成校验, 不另造)。"""
    with pytest.raises(ValueError, match="unknown opus factor"):
        upgrade_tier("sonnet", additional_opus_factors=["made_up_factor"])
