"""JudgmentCase 富化持久化 payload — judgment-case@v1 (T-JLM-01).

# concept source: judgment-case@v1 (frozen, plan-interview-oldhand-rework)
#   判例 JudgmentCase — 对一条 NatureJudgmentCaptured 原始事件的结构化富化包装 (本体独立于事件、经
#   source_event_id 引用它, 不改 L0 event schema)。
#
# 这个模块钉死判例的 9 字段 strict schema + draft→enriched 转换。JUDGMENT_CASE_ENRICHED 事件的
# canonical payload = 一个 JudgmentCase (registry.py 注册)。
#
# 为什么恰好 9 字段、没有独立的 case_id: 一个判例包装【一条】NatureJudgmentCaptured 事件, 故
# source_event_id 天然是判例身份 (counter_example_case_ids 用它相互引用; 富化/纠正的版本演化靠
# EventBase.supersede 链 event_id→superseded_event_id 追踪, 同 source_event_id 的多版本)。
#
# 纠正处理不新造机制 (task package 硬约束): 判例被 Nature 后续反应修正 (weight_tier/applicable/
# non_applicable 任一变化) 时, 新版本走【标准 envelope 通用 supersede】(is_supersede=true +
# superseded_event_id 指回旧事件), 非 concept 专属仲裁机制。见 l1/judgment_case.py。
#
# draft vs enriched 不变量 (本模块 model_validator 钉死):
#   draft    ⟺ 最小骨架: weight_tier=None 且 applicable_conditions / non_applicable_conditions /
#              counter_example_case_ids / affordance 全空 (stability 有缺省, 骨架期即在);
#   enriched ⟺ 五个行李字段【实质有值】: weight_tier 非 None + applicable_conditions 非空 +
#              affordance 非空。non_applicable_conditions / counter_example_case_ids 允许为空
#              (concept: non_applicable"允许为空,随新反例增长"; counter_example 同理随新反例长)。
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import EnrichmentStatus, Stability, WeightTier

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class JudgmentCase(BaseModel):
    """判例 JudgmentCase — judgment-case@v1 的 9 字段 strict schema。

    JUDGMENT_CASE_ENRICHED 事件的 canonical payload。frozen — 富化 / 纠正返回新实例
    (enrich / revise), 版本演化经 envelope supersede 链落账, 不原地改。
    """

    model_config = _STRICT

    # ① source_event_id — 指向原始 NatureJudgmentCaptured event_id, 不可变 (兼作判例身份)。
    source_event_id: str = Field(min_length=1)
    # ② scenario — 这句话是在什么项目 / 任务 / 压力下说的。
    scenario: str = Field(min_length=1)
    # ③ weight_tier — 判例权重五级 (draft=None; enriched 必有值)。
    weight_tier: WeightTier | None = None
    # ④ stability — 稳定性三级, 缺省 temporary_tactic (骨架期即在, 反复印证才升级)。
    stability: Stability = Stability.TEMPORARY_TACTIC
    # ⑤ applicable_conditions — 这条在哪些场景适用。
    applicable_conditions: list[str] = Field(default_factory=list)
    # ⑥ non_applicable_conditions — 明确不适用的场景 (允许为空, 随新反例增长)。
    non_applicable_conditions: list[str] = Field(default_factory=list)
    # ⑦ counter_example_case_ids — 指向其他判例的 source_event_id (双向语义单向存储去重)。
    counter_example_case_ids: list[str] = Field(default_factory=list)
    # ⑧ affordance — 这条未来能帮什么忙 (不是字面说什么, 是能在哪些看似无关的场景被想起来用)。
    affordance: list[str] = Field(default_factory=list)
    # ⑨ enrichment_status — draft (最小骨架) | enriched (五行李字段实质有值)。
    enrichment_status: EnrichmentStatus

    @model_validator(mode="after")
    def enforce_enrichment_invariant(self) -> Self:
        """draft ⟺ 最小骨架; enriched ⟺ 五行李字段实质有值 (concept judgment-case@v1)。"""
        if self.enrichment_status is EnrichmentStatus.DRAFT:
            filled = [
                name
                for name, is_filled in (
                    ("weight_tier", self.weight_tier is not None),
                    ("applicable_conditions", bool(self.applicable_conditions)),
                    ("non_applicable_conditions", bool(self.non_applicable_conditions)),
                    ("counter_example_case_ids", bool(self.counter_example_case_ids)),
                    ("affordance", bool(self.affordance)),
                )
                if is_filled
            ]
            if filled:
                raise ValueError(
                    "enrichment_status=draft 是最小骨架, 禁止填行李字段: " + ", ".join(filled),
                )
        else:  # ENRICHED
            missing = [
                name
                for name, has_value in (
                    ("weight_tier", self.weight_tier is not None),
                    ("applicable_conditions", bool(self.applicable_conditions)),
                    ("affordance", bool(self.affordance)),
                )
                if not has_value
            ]
            if missing:
                raise ValueError(
                    "enrichment_status=enriched 要求实质行李字段非空: " + ", ".join(missing),
                )
        return self

    @classmethod
    def draft_skeleton(cls, *, source_event_id: str, scenario: str) -> Self:
        """建一个 draft 骨架 (仅 source_event_id + scenario)。"""
        return cls(
            source_event_id=source_event_id,
            scenario=scenario,
            enrichment_status=EnrichmentStatus.DRAFT,
        )

    def enrich(
        self,
        *,
        weight_tier: WeightTier,
        applicable_conditions: list[str],
        affordance: list[str],
        stability: Stability | None = None,
        non_applicable_conditions: list[str] | None = None,
        counter_example_case_ids: list[str] | None = None,
    ) -> Self:
        """draft → enriched: 填五行李字段, 返回新 enriched 实例。

        只能对 draft 富化 (对已 enriched 的判例做修正走 revise + envelope supersede)。
        """
        if self.enrichment_status is not EnrichmentStatus.DRAFT:
            raise ValueError(
                "enrich() 只能作用于 draft; 已 enriched 的判例修正请走 revise() + envelope supersede",
            )
        return type(self)(
            source_event_id=self.source_event_id,
            scenario=self.scenario,
            weight_tier=weight_tier,
            stability=stability if stability is not None else self.stability,
            applicable_conditions=list(applicable_conditions),
            non_applicable_conditions=list(non_applicable_conditions or []),
            counter_example_case_ids=list(counter_example_case_ids or []),
            affordance=list(affordance),
            enrichment_status=EnrichmentStatus.ENRICHED,
        )

    def revise(
        self,
        *,
        weight_tier: WeightTier | None = None,
        stability: Stability | None = None,
        applicable_conditions: list[str] | None = None,
        non_applicable_conditions: list[str] | None = None,
        counter_example_case_ids: list[str] | None = None,
        affordance: list[str] | None = None,
    ) -> Self:
        """对一个已 enriched 判例做纠正 → 新 enriched 实例 (被 Nature 后续反应修正)。

        只改传入的字段, 其余沿用。这个新实例经 l1.judgment_case.correct_judgment_case emit 时带
        envelope supersede 指回旧事件 — 版本演化复用通用 supersede, 不新造纠正机制 (dc-2)。
        """
        if self.enrichment_status is not EnrichmentStatus.ENRICHED:
            raise ValueError("revise() 只作用于已 enriched 判例 (draft 请用 enrich())")
        return type(self)(
            source_event_id=self.source_event_id,
            scenario=self.scenario,
            weight_tier=weight_tier if weight_tier is not None else self.weight_tier,
            stability=stability if stability is not None else self.stability,
            applicable_conditions=(
                list(applicable_conditions)
                if applicable_conditions is not None
                else list(self.applicable_conditions)
            ),
            non_applicable_conditions=(
                list(non_applicable_conditions)
                if non_applicable_conditions is not None
                else list(self.non_applicable_conditions)
            ),
            counter_example_case_ids=(
                list(counter_example_case_ids)
                if counter_example_case_ids is not None
                else list(self.counter_example_case_ids)
            ),
            affordance=list(affordance) if affordance is not None else list(self.affordance),
            enrichment_status=EnrichmentStatus.ENRICHED,
        )


# ════════════════════════════════════════════════════════════════════════════════
#  T-JLM-03 — preference-as-test-harness@v1 回归验证门 结论载荷
# ════════════════════════════════════════════════════════════════════════════════
#
# JudgmentRegressionEvaluated 事件的 canonical payload = 一次回归运行的结论。冻结不变量
# preference-as-test-gate-blocks-index-infra: 未来索引/嵌入设施类 task 的立项前置检查读
# gate_result_state=passed 才放行。pass_threshold≥0.70 是占位值 (threshold_is_placeholder=True),
# 具体数字待 Nature 校准 —— 本 task 只钉结构, 不锁死数字。

GateResultState = Literal["passed", "not_passed"]


class JudgmentRegressionCaseResult(BaseModel):
    """一条 holdout 判例的回归结果 (审计可复盘: 预测方向 vs 真实方向 是否一致)。

    predicted_direction 取自【检索机制在候选池 (已剔除 holdout) 上】命中的最强判例的真实方向;
    检索无命中时 predicted_direction="none" (记为方向不一致)。matched=预测方向==真实方向。
    """

    model_config = _STRICT

    holdout_case_id: str = Field(min_length=1)
    actual_direction: str = Field(min_length=1)
    predicted_direction: str = Field(min_length=1)
    retrieved_case_id: str | None = None
    matched: bool


class JudgmentRegressionEvaluated(BaseModel):
    """一次 PreferenceAsTestHarness 回归运行的结论载荷 (preference-as-test-harness@v1)。

    score = matched_count / holdout_size (holdout 为空时 0.0)。gate_result_state=passed 当且仅当
    holdout_size>0 且 score≥pass_threshold —— holdout 为空 / 候选池为空 / 全部预测未命中 都自然落到
    not_passed (无法证明通过就不通过)。threshold_is_placeholder 恒 True 提醒 pass_threshold 是占位。
    """

    model_config = _STRICT

    score: float = Field(ge=0.0, le=1.0)
    pass_threshold: float = Field(ge=0.0, le=1.0)
    gate_result_state: GateResultState
    holdout_size: int = Field(ge=0)
    candidate_pool_size: int = Field(ge=0)
    matched_count: int = Field(ge=0)
    # pass_threshold 是占位值 (concept 冻结: ≥70% 待 Nature 校准, 本概念先占位不锁死)。
    threshold_is_placeholder: bool = True
    case_results: list[JudgmentRegressionCaseResult] = Field(default_factory=list)


__all__ = [
    "GateResultState",
    "JudgmentCase",
    "JudgmentRegressionCaseResult",
    "JudgmentRegressionEvaluated",
]
