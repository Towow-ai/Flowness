"""review_plan category event payload schema (M-1.5 §3.1 Patch Z).

# spec source:
#   04-l1-intelligence/M-1.5-review-skill-detailed-design.md §3.1 ReviewPlanCreated
#   §10 Patch Z → M-0.1 加 ReviewPlanCreated event + review_plan target_entity_type
#
# RUN-035 T-L1-50: design-time review mode 唯一产出。review_plan 是 author-time / fix-after
# mode 的稳定共同前提 (dimensions + VoI criteria + historical_failures_feed)。0 历史真事件,
# M-1.5 唯一生产者 → 注册 enforced (非 exempt, 同 T-L1-05 Patch R 范式)。
#
# supersede 协议 (Patch d) 走 EventBase.supersede (is_supersede/superseded_event_id/novelty),
# 不在 payload 重定义 — meta-review fork 发现 review_plan v1 缺陷时 orchestrator 重调
# design-time mode 产 v2, M-0.5 NoveltyCheck 自动应用。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    ReviewConsolidationType,
    ReviewPlanDimension,
    ReviewTriggerReason,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class ReviewPlanBatchInfo(BaseModel):
    """M-1.5 §3.1 batch_info — 分批冻结对接 M-1.2 §11.3 EngineeringConsensusFreezed batch_info.

    Optional on the payload: EC batch_info 本体依赖 T-L1-09 (波1 未做); 非分批冻结的
    consensus 不带 batch_info。
    """

    model_config = _STRICT

    batch_number: int = Field(ge=0)
    is_final_batch: bool
    previous_review_plan_ids: list[str] = Field(default_factory=list)
    consolidation_type: ReviewConsolidationType


class ReviewPlanDimensionEntry(BaseModel):
    """M-1.5 §3.1 Layer 1 dimensions[] — 风险面 → review 维度映射."""

    model_config = _STRICT

    dimension_id: ReviewPlanDimension
    triggered_by_risk_surfaces: list[str] = Field(default_factory=list)
    trigger_reason: ReviewTriggerReason


class ReviewPlanVoiCriterion(BaseModel):
    """M-1.5 §3.1 Layer 2 voi_criteria[] — 什么样的 finding 在该 dimension 下有效.

    criterion_statement + task_context_anchor 必填 (绑定到 task 具体 context, 不能太泛);
    example_good/bad_findings 可选。
    """

    model_config = _STRICT

    criterion_id: str = Field(min_length=1)
    dimension_ref: str = Field(min_length=1)
    criterion_statement: str = Field(min_length=1)
    task_context_anchor: str = Field(min_length=1)
    example_good_findings: list[str] | None = None
    example_bad_findings: list[str] | None = None


class ReviewPlanHistoricalFailure(BaseModel):
    """M-1.5 §3.1 historical_failures_feed[] (F-08e) — 历史失败模式喂入."""

    model_config = _STRICT

    failure_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    observed_in: list[str] = Field(default_factory=list)
    author_address: str | None = None


class ReviewPlanCreatedPayload(BaseModel):
    """M-1.5 §3.1 ReviewPlanCreated — design-time review mode 产出 (RUN-035 T-L1-50)."""

    model_config = _STRICT

    review_plan_id: str = Field(min_length=1)
    associated_consensus_id: str = Field(min_length=1)
    associated_brief_id: str = Field(min_length=1)
    batch_info: ReviewPlanBatchInfo | None = None
    dimensions: list[ReviewPlanDimensionEntry] = Field(min_length=1)
    voi_criteria: list[ReviewPlanVoiCriterion] = Field(min_length=1)
    historical_failures_feed: list[ReviewPlanHistoricalFailure] = Field(default_factory=list)


class ReviewDimensionExercisedPayload(BaseModel):
    """T-RMD-S2-REVFIX (finding f-glob-review-fix-no-proof-of-work / M15-F1) — proof-of-work that one
    review dimension fork was really exercised by the author-time driver.

    M15-F1 病灶: 维度方法论 fork 跑完 0 finding 时, verdict 门把空 findings 折叠成 passed 却在账本里
    零痕 —— "patch 在该维度下干净" 与 "该维度根本没被验过" 不可区分, 抓假的最后一道 (独立复核) 自己
    证明不了真跑过。本事件为每个真跑的维度 fork 落一条 canonical 痕 (含 0-finding 情形): dimension +
    fork_spawned (真起独立子进程? replay/inline=False) + fork_completed (fork 完成分析) + finding_count
    (该维度产出的 finding proposal 数, 0 = 该维度下 patch 干净)。

    无 reducer 消费 —— proof-of-work 只需活在 EventLog.all_records() 供独立复算/审计, 不进投影。
    flat payload (同 ReviewPlanCreatedPayload 范式), event_category=state_transition; 新事件无 legacy
    producer → 留 PAYLOAD_VALIDATION_ENFORCED (fail-closed), 同 ReviewPlanCreated 范式。
    """

    model_config = _STRICT

    review_plan_id: str = Field(min_length=1)
    dimension: ReviewPlanDimension
    fork_spawned: bool
    fork_completed: bool
    fork_model: str = Field(min_length=1)
    fork_mode: str = Field(min_length=1)
    finding_count: int = Field(ge=0)


__all__ = [
    "ReviewDimensionExercisedPayload",
    "ReviewPlanBatchInfo",
    "ReviewPlanCreatedPayload",
    "ReviewPlanDimensionEntry",
    "ReviewPlanHistoricalFailure",
    "ReviewPlanVoiCriterion",
]
