"""M-1.3 §10.2-10.5 — Model tier 评分算法 (opus_score 加权) + freeze 门 evidence 校验 — T-L1-26.

# spec source:
#   04-l1-intelligence/M-1.3-planner-skill-detailed-design.md
#     §10.2 因素分级 (每因素带分量) / §10.3 决策算法 (opus_score = Σ weight) /
#     §10.4 组合规则 (Rule 1-4) / §10.6 Evidence 要求
#   src/towow/skills/planning/knowledge/model-tier-policy.md (opus 因素 10 项 weight + sonnet 因素
#     + 决策算法 + 组合规则 + Evidence 要求)
#
# Why this module (T-L1-26, 源表): plan model-tier 之前 --tier 自由选 (opus|sonnet) + 默认 reason
#   串 — 算法 (§10.3 opus_score 加权) 从未跑过, "可解释" 的 model_tier 是空话。default reason
#   ("planner default tier assignment") 这种无 evidence 的串能 freeze. This module computes
#   opus_score per §10.3 weights and provides the freeze-gate evidence check: a TaskModelTierAssigned
#   whose reason is the default placeholder / carries no matched-factor evidence is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# §10.2 Opus 因素 → weight (knowledge/model-tier-policy.md "Opus 因素" 表).
OPUS_FACTOR_WEIGHTS: dict[str, int] = {
    "red_line_obligation": 3,            # red-line obligation 涉及
    "state_machine_or_invariant_change": 3,  # state_machine / invariant 变更
    "critical_path_medium_plus": 3,      # 关键路径上 + 复杂度中等以上
    "high_fan_out_concept": 2,           # high fan-out concept (≥5 消费方)
    "write_set_gt_3": 2,                 # write_set > 3 entity
    "ambiguous_completion_condition": 2,  # 模糊 completion_condition / 多解释
    "review_prep_or_investigation": 2,   # review_prep / investigation 类
    "supersede_decision": 2,             # 涉及 supersede 决策
    "cross_module": 1,                   # 跨多个独立模块
    "complex_task_description": 1,       # task 描述本身复杂 (>500 字)
    # §10.5 升级机制 — task 在执行/escalation 阶段暴露 "比 planner 估计更难"。这本身是 weight=3
    # 的强证据 (一票通过 opus): spec §10.5 "v3 初版简化 — sonnet task 中途失败 → 自动升 opus 重跑"
    # 用因素表达, 让升级走现成 score_tier 算法 + 天然满足 §10.6 evidence (matched factor 非空)。
    "execution_escalation": 3,
}

# §10.5 升级触发因素名 — upgrade_tier 重评时强制叠加 (escalation 即 "比估计更难" 的强证据)。
EXECUTION_ESCALATION_FACTOR = "execution_escalation"

# tier 高低序 (升级只能从低 tier → 更高 tier; 同/降级不 re-emit)。
_TIER_RANK: dict[str, int] = {"sonnet": 0, "opus": 1}

# §10.2 Sonnet 因素 (无 weight — 仅在 opus_score 低时影响决策).
SONNET_FACTORS: frozenset[str] = frozenset(
    {
        "standard_implementation",
        "standard_test",
        "documentation",
        "single_module",
        "done_criteria_mechanically_verifiable",
        "critical_path_but_simple",
    },
)

# The default placeholder reason the old CLI shipped — an evidence-less串 must not freeze.
DEFAULT_PLACEHOLDER_REASONS: frozenset[str] = frozenset(
    {
        "planner default tier assignment",
        "default",
        "",
    },
)


@dataclass(frozen=True)
class TierScoreResult:
    tier: str                       # "opus" | "sonnet"
    opus_score: int
    matched_opus_factors: list[dict[str, object]] = field(default_factory=list)
    matched_sonnet_factors: list[str] = field(default_factory=list)
    decision_rule: str = ""


def score_tier(
    matched_opus_factors: list[str],
    matched_sonnet_factors: list[str] | None = None,
    *,
    on_critical_path: bool = False,
    red_line_related: bool = False,
) -> TierScoreResult:
    """§10.3 决策算法 + §10.4 组合规则. Returns tier + opus_score + matched factor evidence.

    opus_score = Σ weight(matched opus factors).
      ≥ 3                              → opus       (Rule 3: 任一 weight=3 一票通过 也落这)
      = 2                              → opus (保守) (Rule 2)
      = 1 + 关键路径/red-line          → opus       (Rule 3 组合)
      = 1 其他                         → sonnet
      = 0 + 有 sonnet 因素             → sonnet
      = 0 + 无 sonnet 因素             → opus (保守 — fork 看不清说明不该决定; Rule 4)
    """
    sonnet = matched_sonnet_factors or []
    unknown = [f for f in matched_opus_factors if f not in OPUS_FACTOR_WEIGHTS]
    if unknown:
        msg = f"unknown opus factor(s): {unknown}; must be in OPUS_FACTOR_WEIGHTS"
        raise ValueError(msg)

    factor_evidence = [
        {"factor": f, "weight": OPUS_FACTOR_WEIGHTS[f]} for f in matched_opus_factors
    ]
    opus_score = sum(OPUS_FACTOR_WEIGHTS[f] for f in matched_opus_factors)

    if opus_score >= 3:
        tier, rule = "opus", "opus_score>=3"
    elif opus_score == 2:
        tier, rule = "opus", "opus_score==2 (保守)"
    elif opus_score == 1:
        if on_critical_path or red_line_related:
            tier, rule = "opus", "opus_score==1 + 关键路径/red-line"
        else:
            tier, rule = "sonnet", "opus_score==1 其他"
    elif sonnet:
        tier, rule = "sonnet", "opus_score==0 + 有 sonnet 因素"
    else:
        tier, rule = "opus", "opus_score==0 + 无因素 (保守 Rule 4)"

    return TierScoreResult(
        tier=tier,
        opus_score=opus_score,
        matched_opus_factors=factor_evidence,
        matched_sonnet_factors=sonnet,
        decision_rule=rule,
    )


@dataclass(frozen=True)
class TierUpgradeResult:
    """§10.5 升级重评结果。upgraded=True 才该 re-emit 新 (更高 tier) TaskModelTierAssigned。"""

    upgraded: bool                  # new tier 严格高于 current_tier 时 True
    from_tier: str                  # 升级前的 tier
    to_tier: str                    # 重评后的 tier (未升级时 == from_tier)
    score: TierScoreResult          # 重评结果 (携 opus_score + matched factor evidence + rule)
    push_back_reason: str = ""      # upgraded=False 时填: 为什么不升 (已是最高 tier)


def upgrade_tier(
    current_tier: str,
    *,
    prior_opus_factors: list[str] | None = None,
    additional_opus_factors: list[str] | None = None,
    prior_sonnet_factors: list[str] | None = None,
    on_critical_path: bool = False,
    red_line_related: bool = False,
) -> TierUpgradeResult:
    """§10.5 升级机制 (sonnet → opus) — 执行/escalation 后按更高 OPUS_FACTOR 重评 tier.

    复用 score_tier (不另造平行升级逻辑): 在 current 分配时的 opus 因素 (prior_opus_factors) +
    执行阶段新发现的因素 (additional_opus_factors) 之上, 强制叠加 EXECUTION_ESCALATION_FACTOR
    (weight=3 — escalation 本身是 "比 planner 估计更难" 的强证据)。escalation 因素 weight=3 ⇒
    opus_score≥3 ⇒ opus (§10.4 Rule 3 一票通过), 故任何 sonnet task escalation 后必升 opus,
    且新分配天然携 matched factor evidence (满足 §10.6, 非 evidence-less 自动升)。

    判定:
      - to_tier 严格高于 current_tier → upgraded=True (caller re-emit 更高 tier 的
        TaskModelTierAssigned; projection latest-wins ⇒ 自然 supersede 旧分配)。
      - to_tier == current_tier (current 已 opus) → upgraded=False + push_back
        (§10.5 "拒绝 → 解释 push back": 已是最高 tier, escalation 无法再升)。

    Raises:
        ValueError: current_tier 不是已知 tier ("sonnet"/"opus"), 或因素名不在 OPUS_FACTOR_WEIGHTS。
    """
    if current_tier not in _TIER_RANK:
        msg = f"unknown current_tier {current_tier!r}; must be one of {sorted(_TIER_RANK)}"
        raise ValueError(msg)

    # prior + 新发现 + escalation, 去重保序 (score_tier 会校验因素名合法性)。
    combined: list[str] = []
    for f in [*(prior_opus_factors or []), *(additional_opus_factors or []), EXECUTION_ESCALATION_FACTOR]:
        if f not in combined:
            combined.append(f)

    score = score_tier(
        combined,
        prior_sonnet_factors,
        on_critical_path=on_critical_path,
        red_line_related=red_line_related,
    )

    if _TIER_RANK[score.tier] > _TIER_RANK[current_tier]:
        return TierUpgradeResult(
            upgraded=True,
            from_tier=current_tier,
            to_tier=score.tier,
            score=score,
        )
    return TierUpgradeResult(
        upgraded=False,
        from_tier=current_tier,
        to_tier=current_tier,
        score=score,
        push_back_reason=(
            f"current tier {current_tier!r} 已是最高 tier — escalation 重评仍 {score.tier!r} "
            f"(opus_score={score.opus_score}), 无法再升 (§10.5 push back)"
        ),
    )


def validate_tier_assignment_evidence(
    *,
    reason: str,
    matched_opus_factors: list[str],
    matched_sonnet_factors: list[str] | None,
) -> tuple[bool, list[str]]:
    """§10.6 Evidence 要求 — a TaskModelTierAssigned must carry real evidence, not a placeholder.

    fail-closed on: empty/default placeholder reason; OR zero matched factors of either kind
    (no evidence for the tier decision at all). Returns (is_valid, errors).
    """
    errors: list[str] = []
    if reason.strip().lower() in DEFAULT_PLACEHOLDER_REASONS:
        errors.append(
            f"reason {reason!r} is a default placeholder — §10.6 要求带具体 rationale (非默认串)",
        )
    if not matched_opus_factors and not (matched_sonnet_factors or []):
        errors.append(
            "no matched_opus_factors nor matched_sonnet_factors — §10.6 要求 tier 分配带因素 evidence",
        )
    return (not errors), errors


__all__ = [
    "DEFAULT_PLACEHOLDER_REASONS",
    "EXECUTION_ESCALATION_FACTOR",
    "OPUS_FACTOR_WEIGHTS",
    "SONNET_FACTORS",
    "TierScoreResult",
    "TierUpgradeResult",
    "score_tier",
    "upgrade_tier",
    "validate_tier_assignment_evidence",
]
