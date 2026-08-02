"""M-3.3 散落形态迁移工具 event payload schemas + OriginMetadata (RUN-066).

# spec source:
#   00-original-inputs/v3-design-bundle/02-phase-c-design/
#     M-3.3-scattered-form-migration-detailed-design.md
#       §5.1 OriginMetadata (first-class + immutable, content_hash 必验)
#       §5.2 MigrationStepRecorded payload
#       §5.3 MigrationRunCompleted payload
#       §11.1 4 event_type (RunStarted / StepRecorded / BatchSubmitted / RunCompleted)
#       §14.2 ConceptCreated / ObligationCaptured payload 加可选 origin_metadata 字段
#
# OriginMetadata is shared by:
#   - state_transition.ConceptCreatedPayload.origin_metadata (Patch M-3.3-2)
#   - obligation.ObligationCapturedPayload.origin_metadata (Patch M-3.3-3)
#   - the 4 Migration* event payloads here
# It lives in this leaf module (depends only on enums) so the two payload modules
# can import it without a cycle.
#
# 设计要点 (反退路, §13):
#   - OriginMetadata frozen=True (永远 immutable, §5.1 "一旦写入不可改") — Inv2/Inv7。
#   - migration_schema_version first-class (§13.3 Inv7 修正方向: 跨 run 留痕格式演化用 supersede,
#     先靠 version 字段把"哪个 schema 版本"钉死)。
#   - 4 event payload 走 PAYLOAD_VALIDATION_ENFORCED (sole producer = 迁移工具 + emit 即 conforming,
#     同 ValidationScenarioRun 范式) — NOT exempt。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from towow.schemas.enums import (
    MigrationSourceType,
    MigrationStepOutcome,
    MigrationTranslatorType,
    TargetEntityType,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)

# 当前迁移留痕 schema 版本 (§13.3 Inv7) — 跨 migration_run 一致, 演化用 supersede。
MIGRATION_SCHEMA_VERSION = "1.0.0"


# ════════════════════════════════════════════════════════════════════════════════
#  §5.1 OriginMetadata — first-class + immutable provenance on每个迁移产物 event
# ════════════════════════════════════════════════════════════════════════════════


class OriginMigrationSource(BaseModel):
    """§5.1 origin_metadata.migration_source — 追溯到原始 v2.x 文件 + content_hash。"""

    model_config = _STRICT

    source_id: str = Field(min_length=1)
    source_type: MigrationSourceType
    source_path: str = Field(min_length=1)
    source_section: str = Field(min_length=1)  # 文件内具体 section
    source_content_hash: str = Field(min_length=1)  # 内容指纹 (验证源完整性) — Inv2
    source_extracted_at: str = Field(min_length=1)


class OriginMigrationRun(BaseModel):
    """§5.1 origin_metadata.migration_run — 哪次 run / 哪个 batch / 关联 step。"""

    model_config = _STRICT

    migration_run_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    # 翻译时 MigrationStepRecorded 尚未拿到 event_id (同 batch 内产出); migration_step_id 是
    # 翻译时已知的逻辑 id (必填, 用于反向追溯), event_id backfill 后可选填充。
    migration_step_id: str = Field(min_length=1)
    migration_step_event_id: str | None = None


class OriginTranslation(BaseModel):
    """§5.1 origin_metadata.translation — 谁翻译的 + confidence + 采用了哪些 mapping。"""

    model_config = _STRICT

    translator_type: MigrationTranslatorType
    translator_confidence: float = Field(ge=0.0, le=1.0)
    translation_strategy: str = Field(min_length=1)
    mapping_proposals_used: list[str] = Field(default_factory=list)


class OriginMetadata(BaseModel):
    """§5.1 — 每个 ConceptCreated / ObligationCaptured 产自 M-3.3 必含的留痕字段。

    immutable (frozen) — 一旦写入不可改, 跟 supersede 协议正交 (§5.1 关键约束)。
    反向追溯: concept_id → ConceptCreated.origin_metadata → migration_source.source_path
    + content_hash → 原始 v2.x 文件。
    """

    model_config = _STRICT

    migration_source: OriginMigrationSource
    migration_run: OriginMigrationRun
    translation: OriginTranslation
    # §13.3 Inv7 — 跨 migration_run 留痕格式一致性的版本锚。
    migration_schema_version: str = Field(default=MIGRATION_SCHEMA_VERSION, min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  §11.1 4 Migration* event payloads
# ════════════════════════════════════════════════════════════════════════════════


class MigrationRunStartedPayload(BaseModel):
    """§11.1 MigrationRunStarted — 启动新 migration run (run 留痕链起点)。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MIGRATION_RUN] = TargetEntityType.MIGRATION_RUN
    migration_run_id: str = Field(min_length=1)
    source_root: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    total_sources_in_catalog: int = Field(ge=0)


class MigrationStepTranslationResult(BaseModel):
    """§5.2 MigrationStepRecorded.translation_result。"""

    model_config = _STRICT

    success: bool
    produced_intent_count: int = Field(ge=0)
    confidence_scores: list[float] = Field(default_factory=list)
    translation_strategy: str = Field(min_length=1)
    # 链接到 AI 翻译过程的 reasoning log (.towow/migration/runs/{run_id}/ai-translations/)。
    ai_reasoning_log_ref: str | None = None


class MigrationStepRecordedPayload(BaseModel):
    """§5.2 MigrationStepRecorded — 单 source 的 migration step 留痕。

    进 apply-batch commit gate envelope (跟 ConceptCreated/ObligationCaptured 同 batch)。
    base_classification=immutable_truth (§5.2)。
    """

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MIGRATION_STEP] = TargetEntityType.MIGRATION_STEP
    migration_step_id: str = Field(min_length=1)
    migration_run_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_content_hash: str = Field(min_length=1)
    translation_result: MigrationStepTranslationResult
    outcome: MigrationStepOutcome
    outcome_evidence: str = Field(min_length=1)


class MigrationBatchSubmittedPayload(BaseModel):
    """§11.1 MigrationBatchSubmitted — batch 提交 (关联 envelope) path-B 留痕。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MIGRATION_RUN] = TargetEntityType.MIGRATION_RUN
    migration_run_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    batch_theme: str = Field(min_length=1)
    envelope_event_id: str = Field(min_length=1)  # apply-batch 走 commit gate 产的 envelope
    commit_outcome: str = Field(min_length=1)  # accepted / rejected
    source_ids: list[str] = Field(default_factory=list)
    produced_event_ids: list[str] = Field(default_factory=list)


class MigrationCatalogSummarySourcesByType(BaseModel):
    model_config = _STRICT

    claude_rules: int = Field(ge=0, default=0)
    adr: int = Field(ge=0, default=0)
    towow_state: int = Field(ge=0, default=0)
    memory: int = Field(ge=0, default=0)
    knowledge_pack: int = Field(ge=0, default=0)


class MigrationDecisionsBreakdown(BaseModel):
    model_config = _STRICT

    migrated: int = Field(ge=0, default=0)
    skipped_obsolete: int = Field(ge=0, default=0)
    skipped_not_applicable: int = Field(ge=0, default=0)
    deferred: int = Field(ge=0, default=0)
    manual_handle: int = Field(ge=0, default=0)


class MigrationCatalogSummary(BaseModel):
    model_config = _STRICT

    total_sources: int = Field(ge=0)
    sources_by_type: MigrationCatalogSummarySourcesByType
    decisions_breakdown: MigrationDecisionsBreakdown


class MigrationEventsProduced(BaseModel):
    model_config = _STRICT

    concept_created: int = Field(ge=0, default=0)
    obligation_captured: int = Field(ge=0, default=0)
    migration_step_recorded: int = Field(ge=0, default=0)


class MigrationUnmigratedEntry(BaseModel):
    """§5.3 / §6 unmigrated_catalog[] — 没成的 source + 暴露的 design gap (反退路 endpoint)。"""

    model_config = _STRICT

    source_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    design_gap_identified: str | None = None  # §13.3 Inv4 — model gap 必填此字段
    recommended_action: str = Field(min_length=1)


class MigrationRunCompletedPayload(BaseModel):
    """§5.3 MigrationRunCompleted — 完成报告 + catalog summary + provenance map。"""

    model_config = _STRICT

    target_entity_type: Literal[TargetEntityType.MIGRATION_RUN] = TargetEntityType.MIGRATION_RUN
    migration_run_id: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    catalog_summary: MigrationCatalogSummary
    events_produced: MigrationEventsProduced
    unmigrated_catalog: list[MigrationUnmigratedEntry] = Field(default_factory=list)
    provenance_map_path: str = Field(min_length=1)


__all__ = [
    "MIGRATION_SCHEMA_VERSION",
    "MigrationBatchSubmittedPayload",
    "MigrationCatalogSummary",
    "MigrationCatalogSummarySourcesByType",
    "MigrationDecisionsBreakdown",
    "MigrationEventsProduced",
    "MigrationRunCompletedPayload",
    "MigrationRunStartedPayload",
    "MigrationStepRecordedPayload",
    "MigrationStepTranslationResult",
    "MigrationUnmigratedEntry",
    "OriginMetadata",
    "OriginMigrationRun",
    "OriginMigrationSource",
    "OriginTranslation",
]
