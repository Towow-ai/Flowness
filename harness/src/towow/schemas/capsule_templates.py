"""9 scene templates for capsule assembly.

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §6 (L376..L526) 9 scene templates: interview / engineering_consensus /
#       planning / execution / mismatch / review / fix / maintenance / audit
#   07-process-handoffs/PHASE-D-REVERSE-CONTRIBUTION-LOG.md
#     Patch M-2.2-D: scene_type=maintenance template caller_params adds 3 fields
#       (maintenance_mode / lookback_window / shared_knowledge_required)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    CapsuleSection,
    EventType,
    MaintenanceMode,
    ProjectionName,
    SceneType,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class SceneTemplate(BaseModel):
    """Scene template per M-0.3 §6 — declarative pipeline configuration."""

    model_config = _STRICT

    scene_type: SceneType
    required_projections: list[ProjectionName] = Field(min_length=1)
    neighborhood_hops: int = Field(ge=0, le=5)  # cap matches M-2.1 cascade depth bound
    neighborhood_center: str = Field(min_length=1)  # human description of seed selection
    additional_event_reads: list[EventType] = Field(default_factory=list)
    capsule_section_customization: dict[CapsuleSection, str] = Field(default_factory=dict)
    # M-0.3 §9.2 / Patch 4 — template_version 进 CapsuleCompiled 审计指纹 (+ Patch3 capsule_assembly_hash)。
    # 模板结构改了 (bump 此值) → assembly 指纹随之变, audit 可识别 capsule 是哪版模板装的。
    template_version: str = Field(default="1.0.0", min_length=1)


class MaintenanceCallerParams(BaseModel):  # Patch M-2.2-D
    """maintenance scene_type caller_params per Patch M-2.2-D.

    Three fields added at Phase D so M-2.2 daemons can call the maintenance scene with
    enough context (mode / lookback / shared knowledge requirements).
    """

    model_config = _STRICT

    maintenance_mode: MaintenanceMode
    lookback_window: str | None = None  # e.g. "30d" / "1y" / None for full history
    shared_knowledge_required: list[str] = Field(default_factory=list)


# ─── 9 hardcoded scene templates ───────────────────────────────────────────────


INTERVIEW_TEMPLATE = SceneTemplate(
    scene_type=SceneType.INTERVIEW,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.AT_REFERENCE_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
    ],
    neighborhood_hops=1,
    neighborhood_center="task_scope.target_artifacts → 关联的概念节点",
    additional_event_reads=[
        EventType.NATURE_JUDGMENT_CAPTURED,
        EventType.INFORMATION_NEED_STATUS_CHANGED,
    ],
    capsule_section_customization={
        CapsuleSection.KNOWN_FACTS: "info_map_snapshot + 已有概念节点 + nature_judgments",
        CapsuleSection.MUST_RETURN: "interview brief 格式约束",
    },
)

ENGINEERING_CONSENSUS_TEMPLATE = SceneTemplate(
    scene_type=SceneType.ENGINEERING_CONSENSUS,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.AT_REFERENCE_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.CONSUMER_RELATION,
    ],
    neighborhood_hops=2,
    neighborhood_center="上游采访产出的概念种子节点",
    additional_event_reads=[
        EventType.NATURE_JUDGMENT_CAPTURED,
        EventType.ENGINEERING_CONSENSUS_FREEZED,
    ],
    capsule_section_customization={
        CapsuleSection.KNOWN_FACTS: "已有概念图结构 + 消费方关系",
        CapsuleSection.TASK_BRIEFING: "概念定义 + 边类型约束",
    },
)

PLANNING_TEMPLATE = SceneTemplate(
    scene_type=SceneType.PLANNING,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.TASK_GRAPH,
        ProjectionName.AT_REFERENCE_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.OWNERSHIP,
    ],
    neighborhood_hops=2,
    neighborhood_center="上游工程共识产出的概念节点",
    additional_event_reads=[
        EventType.TASK_READ_SET_CLAIMED,
        EventType.TASK_WRITE_SET_CLAIMED,
    ],
    capsule_section_customization={
        CapsuleSection.KNOWN_FACTS: "概念图 + 已有任务图 + ownership",
        CapsuleSection.MUST_RETURN: "TaskPackage 自包含格式",
    },
)

EXECUTION_TEMPLATE = SceneTemplate(
    scene_type=SceneType.EXECUTION,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.TASK_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.OWNERSHIP,
        ProjectionName.AT_REFERENCE_GRAPH,
    ],
    neighborhood_hops=1,
    neighborhood_center="task_scope.target_artifacts 对应的概念/文件节点",
    additional_event_reads=[EventType.TASK_PACKAGE_PUBLISHED],
    capsule_section_customization={
        CapsuleSection.FILES_TO_READ: "任务包引用的文件列表",
        CapsuleSection.TOOL_CONTEXT: "目标文件相关的 tool-specific obligation",
    },
)

MISMATCH_TEMPLATE = SceneTemplate(
    scene_type=SceneType.MISMATCH,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        # finding-T-BRAIN-01-mismatch-scene-coverage-claim-false: mismatch 是 task-bearing 场景,
        # 与 EXECUTION/REVIEW/FIX 一样应进 T-BRAIN-01 概念邻域自动播种"单一漏斗"(pipeline.py
        # _derive_task_concept_anchors 读 bundle 内 task_graph)。此前漏声明 → bundle 无 task_graph
        # → 自动播种对 mismatch 恒空, 与 pipeline.py 两处覆盖声明名实不符。补齐至与其余三场景对齐。
        ProjectionName.TASK_GRAPH,
    ],
    neighborhood_hops=1,
    neighborhood_center="不符判定涉及的文件/概念节点",
    additional_event_reads=[EventType.PATCH_PROPOSED, EventType.TASK_RUN_COMPLETED],
    capsule_section_customization={
        CapsuleSection.TASK: "不符判定的具体问题描述",
        CapsuleSection.KNOWN_FACTS: "原始任务 spec + 实际 patch 内容",
        # T-L1-45 (RUN-038 波3): MUST_RETURN 补全为 advisor verdict 完整结构 — 不只是事件命名引用。
        # advisor-consult fork (OPUS 决策者, scene=mismatch) 读本 capsule 做判断, 必须知道要返回
        # 什么结构。结构对齐 M-1.4 §3.5 AdvisorVerdictDelivered payload + skills/advisor-consult/
        # SKILL.md §"输出 Structured Result"。T-L1-46 已删 MismatchVerdict — 不再引该已删格式。
        CapsuleSection.MUST_RETURN: (
            "返回 advisor verdict (mismatch 判断结果), 结构 (M-1.4 §3.5):\n"
            "- decision: 5 选 1 — use_path_self_heal (executor 自解) / use_path_consult_again "
            "(capsule 不足, 要求补) / use_path_trigger_replan (走 RePlanTriggered) / "
            "use_path_abort_task (放弃 task) / custom_action (定制步骤)\n"
            "- rationale: 必填 — 为什么这个 decision, 为什么不用其它 path\n"
            "- specific_steps: decision=custom_action 时必填 — 每步 {step, expected_outcome}, "
            "executor 拿到能直接执行 (否则 verdict 空心)\n"
            "- confidence: high | medium | low\n"
            "- evidence_refs: 判断基于什么 — [{source_type (mental_model_principle | "
            "casebook_analog | mismatch_evidence), source_id, finding}]\n"
            "- evidence_scope_summary: v2.1 必填 — 本 verdict 基于哪些 capsule/evidence (我看到的范围); "
            "executor 实施时遇 scope 外新证据或物理检查跟 verdict 矛盾, 须带新证据重新 consult "
            "(triggered_by=verdict_evidence_scope_breach), 不机械执行 stale verdict\n"
            "- if_consult_again: decision=use_path_consult_again 时 — {missing_info: executor 还要补什么}\n"
            "\n"
            "落库路径: executor 自决 → MismatchResolutionDecided (M-1.4 §3.3, 带 resolution_action + "
            "executor_rationale); 经 advisor → 先 AdvisorVerdictDelivered (M-1.4 §3.5, 上述结构), "
            "再 executor 实施后 MismatchResolutionDecided 引用 verdict event_id。两者都经 commit gate path A。"
        ),
    },
)

REVIEW_TEMPLATE = SceneTemplate(
    scene_type=SceneType.REVIEW,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.TASK_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.FINDING_LIFECYCLE,
    ],
    neighborhood_hops=2,
    neighborhood_center="被 review 的 patch 涉及的概念节点",
    additional_event_reads=[
        EventType.PATCH_PROPOSED,
        EventType.TASK_RUN_COMPLETED,
        EventType.FINDING_CREATED,
    ],
    capsule_section_customization={
        CapsuleSection.KNOWN_FACTS: "review plan + 概念图邻域 + 历史 finding",
        CapsuleSection.TOOL_CONTEXT: "reviewer 不能调用的工具限制（V-02）",
        CapsuleSection.MUST_RETURN: "Finding 格式",
    },
)

FIX_TEMPLATE = SceneTemplate(
    scene_type=SceneType.FIX,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.TASK_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.FINDING_LIFECYCLE,
    ],
    neighborhood_hops=1,
    neighborhood_center="Finding 关联的概念/文件节点",
    additional_event_reads=[EventType.FINDING_RESOLVED],
    capsule_section_customization={
        CapsuleSection.TASK: "finding 描述 + 修复方向",
    },
)

MAINTENANCE_TEMPLATE = SceneTemplate(  # Patch M-2.2-D adds caller_params (see MaintenanceCallerParams)
    scene_type=SceneType.MAINTENANCE,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.AT_REFERENCE_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
    ],
    neighborhood_hops=2,
    neighborhood_center="维护 Fork 要检查的概念图区域",
    additional_event_reads=[EventType.SEMANTIC_UPGRADE_DECLARATION],
    capsule_section_customization={
        CapsuleSection.TASK: "维护任务描述（检查图新鲜度 / 语义升级）",
        CapsuleSection.MUST_RETURN: "ConceptGraphProposal 格式",
    },
)

AUDIT_TEMPLATE = SceneTemplate(
    scene_type=SceneType.AUDIT,
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,
        ProjectionName.OWNERSHIP,
    ],
    neighborhood_hops=1,
    neighborhood_center="被审计的 envelope 涉及的节点",
    additional_event_reads=[
        EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        EventType.AUDIT_TRIGGERED,
    ],
    capsule_section_customization={
        CapsuleSection.TASK: "audit scope + trigger reason",
        CapsuleSection.KNOWN_FACTS: "envelope 内容 + 关联概念图",
        CapsuleSection.MUST_RETURN: "AuditVerdict 格式",
    },
)


CONSOLIDATION_TEMPLATE = SceneTemplate(  # M-0.7 §10.3 Patch K / §6.2 (10th scene template)
    scene_type=SceneType.CONSOLIDATION,
    # §6.2 required_projections lists 6: concept_graph / obligation_lifecycle / decision_projection /
    # finding_lifecycle / ownership / snapshot_index. decision_projection + snapshot_index are
    # UI/derived views NOT in the M-0.2 frozen ProjectionName registry (a separate reverse patch to
    # M-0.2 — out of this L0 batch's scope, tracked as debt). Wired here with the 4 that exist in
    # the registry; the 2 missing are recorded in neighborhood_center + a registered debt.
    required_projections=[
        ProjectionName.CONCEPT_GRAPH,  # §6.2 — GC 根集
        ProjectionName.OBLIGATION_LIFECYCLE_STATE,  # §6.2 — active obligation
        ProjectionName.FINDING_LIFECYCLE,  # §6.2 — 活跃 Finding
        ProjectionName.OWNERSHIP,  # §6.2 — lock
    ],
    neighborhood_hops=0,  # §6.2 — consolidation 不做邻域扩展（看全局）
    neighborhood_center=(
        "全局视图 (hops=0)：GC roots + active obligation + finding + lock。"
        "§6.2 还要 decision_projection + snapshot_index 两个 UI/derived 视图，不在 M-0.2 "
        "frozen ProjectionName registry（M-0.2 反向 patch debt）"
    ),
    additional_event_reads=[
        EventType.SNAPSHOT_CREATED,  # §6.2 — 最近 N 个 SnapshotCreated
        EventType.NATURE_JUDGMENT_CAPTURED,  # §6.2 — 最近 N 个 NatureJudgmentCaptured (learning window)
        EventType.CROSS_RUN_CONSOLIDATION_COMMITTED,  # §6.2 — 已有 digest (F-18 RunDigestPublished 未注册)
    ],
    capsule_section_customization={
        # §6.2 TASK_BRIEFING — lookback window 参数 + 要固结的 run 列表 + F-18 digests 摘要
        CapsuleSection.TASK_BRIEFING: "lookback window 参数 + 要固结的 run 列表 + 已有 digests 摘要",
        # §6.2 KNOWN_FACTS — GC roots 当前组成 + retention_watermark + base_classification 三层统计
        CapsuleSection.KNOWN_FACTS: "GC roots 当前组成 + retention_watermark + base_classification 三层统计",
        # §6.2 MUST_RETURN — consolidation envelope schema (read_set / write_set / 三个 compaction invariant)
        CapsuleSection.MUST_RETURN: (
            "consolidation envelope schema (§6.3): read_set / write_set / 三个 compaction "
            "invariant 声明 (reconstructability / digest-provenance / correctable-consolidation)"
        ),
        # §6.2 MUST_NOT_DO — 不修改 immutable_truth event；不绕过 verify；不超出 lookback window
        CapsuleSection.MUST_NOT_DO: (
            "不修改 immutable_truth event (§4.2 硬约束)；不绕过 verify_consolidation_invariants "
            "(§6.4)；不超出 lookback window (§6.2)"
        ),
    },
)


SCENE_TEMPLATES: dict[SceneType, SceneTemplate] = {
    SceneType.INTERVIEW: INTERVIEW_TEMPLATE,
    SceneType.ENGINEERING_CONSENSUS: ENGINEERING_CONSENSUS_TEMPLATE,
    SceneType.PLANNING: PLANNING_TEMPLATE,
    SceneType.EXECUTION: EXECUTION_TEMPLATE,
    SceneType.MISMATCH: MISMATCH_TEMPLATE,
    SceneType.REVIEW: REVIEW_TEMPLATE,
    SceneType.FIX: FIX_TEMPLATE,
    SceneType.MAINTENANCE: MAINTENANCE_TEMPLATE,
    SceneType.AUDIT: AUDIT_TEMPLATE,
    SceneType.CONSOLIDATION: CONSOLIDATION_TEMPLATE,  # M-0.7 §10.3 Patch K / §6.2
}


def get_scene_template(scene_type: SceneType) -> SceneTemplate:
    """Look up the canonical scene template for `scene_type`."""
    return SCENE_TEMPLATES[scene_type]


__all__ = [
    "AUDIT_TEMPLATE",
    "CONSOLIDATION_TEMPLATE",
    "ENGINEERING_CONSENSUS_TEMPLATE",
    "EXECUTION_TEMPLATE",
    "FIX_TEMPLATE",
    "INTERVIEW_TEMPLATE",
    "MAINTENANCE_TEMPLATE",
    "MISMATCH_TEMPLATE",
    "PLANNING_TEMPLATE",
    "REVIEW_TEMPLATE",
    "SCENE_TEMPLATES",
    "MaintenanceCallerParams",
    "SceneTemplate",
    "get_scene_template",
]
