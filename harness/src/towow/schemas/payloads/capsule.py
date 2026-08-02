"""capsule category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.5 CapsuleFields (L172..L184)
#     §3.5 (L772..L783) — 1 CapsuleCompiled payload
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §6 9 scene templates (full scene-aware payload in Step 1.f)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import SceneType

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class CapsuleTruncationLogEntry(BaseModel):
    """M-0.3 §7.2 — one truncated-section留痕 entry on CapsuleCompiled (T-L0-19, 波2).

    Mirrors towow.schemas.capsule.TruncationLogEntry but lives on the event payload so the
    CapsuleCompiled留痕 is self-describing for audit (which section, by what method, how much
    was cut). method ∈ {reduce_neighborhood_hops, summarize_section, reduce_items}.
    """

    model_config = _STRICT

    section: str = Field(min_length=1)
    method: str = Field(min_length=1)
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)


class CapsuleSectionsSummary(BaseModel):
    """CapsuleCompiled.capsule_sections_summary per M-0.1 §3.5 — summary only, not full content.

    T-L0-19 (波2, M-0.3 §7.2): truncation_applied + truncation_log留痕 whether/what the capsule
    was truncated. Both default off/empty (backward compatible — pre-truncation events validate
    unchanged; a non-truncated capsule sets truncation_applied=False with an empty log).
    """

    model_config = _STRICT

    obligation_count: int = Field(ge=0)
    red_line_count: int = Field(ge=0)
    known_facts_count: int = Field(ge=0)
    files_to_read_count: int = Field(ge=0)
    truncation_applied: bool = False
    truncation_log: list[CapsuleTruncationLogEntry] = Field(default_factory=list)


class CapsuleSourceEventRef(BaseModel):
    """M-0.3 §9.2 / Patch 4 — one event the capsule材料 came from (additional_event_reads).

    Records which M-0.1 event the capsule pulled context from, bounded to bundle.as_of_seq
    (Patch 2). Lets audit prove "这条 capsule 引用了哪些 event"。
    """

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    seq: int = Field(ge=0)


class CapsuleEntityRef(BaseModel):
    """M-0.3 §9.2 / Patch 4 — a typed entity ref (touched_nodes element)."""

    model_config = _STRICT

    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)


class CapsuleCoveredSubject(BaseModel):
    """M-0.3 §9.2 / Patch 4 — covered_subjects element (from the event's subjects[])."""

    model_config = _STRICT

    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    role: str = Field(min_length=1)


class CapsuleCompiledPayload(BaseModel):
    """M-0.1 §3.5 CapsuleCompiled — produced by M-0.3 capsule assembler (path B)."""

    model_config = _STRICT

    input_hash: str = Field(min_length=1)
    scene_type: SceneType
    target_fork_id: str = Field(min_length=1)
    capsule_sections_summary: CapsuleSectionsSummary
    # M-0.3 §2.1/§2.8/§9.2 (L68/L212/L681): the committed cutoff (= bundle.as_of_seq) the whole
    # pipeline read against. M-0.4 §2 derives the envelope's as_of_projection_seq from this — the
    # WriteConflict / FreshnessDrift baseline. Optional (default 0 = no baseline) so pre-existing
    # CapsuleCompiled events / emitters validate unchanged (backward compatible); the real pipeline
    # now populates it with the read_consistent watermark.
    as_of_projection_seq: int = Field(default=0, ge=0)
    # M-0.3 §2.2 neighborhood concept ids the capsule was assembled over (the N-hop slice around
    # task_scope.touched_nodes). M-0.4 §3.3 #3 uses this as the read_set UPPER BOUND when no real
    # read trace exists (the agent had access to exactly this projection neighborhood). Optional /
    # default empty (backward compatible — pre-existing events validate unchanged); the real
    # pipeline populates it with the sorted Neighborhood.concept_ids.
    neighborhood_concept_ids: list[str] = Field(default_factory=list)
    # M-0.3 §6 / Patch 2 + §9.2 / Patch 4 — the additional_event_reads the capsule consumed
    # (NatureJudgmentCaptured / TaskPackagePublished / PatchProposed / …), bounded to as_of_seq.
    # Optional / default empty (backward compatible — pre-existing events validate unchanged); the
    # real pipeline populates it with the bounded, deduped, seq-sorted refs.
    source_event_refs: list[CapsuleSourceEventRef] = Field(default_factory=list)
    # ─── M-0.3 §9.2 / Patch 4 (L914..L960) 审计指纹 ───────────────────────────────────────
    # 不存 capsule 全文 (撑爆 event log), 但能证明"当时给 fork 的东西是什么" + 支持 audit/drift/debug。
    # 全部 Optional / 安全默认 (向后兼容 — pre-existing CapsuleCompiled 事件验证不变); 真 pipeline 填值。
    # capsule_content_hash: 整个 capsule 内容的 hash (fork 留副本可对照校验)。
    capsule_content_hash: str = ""
    # section_hashes: 每个 section 名 → 该 section content hash (定位 drift 在哪个 section)。
    section_hashes: dict[str, str] = Field(default_factory=dict)
    template_id: str = ""  # 场景模板标识 (= scene_type value)
    template_version: str = ""  # 模板版本 (SceneTemplate.template_version)
    projection_bundle_hash: str = ""  # 读取的 bundle hash (折进 input_hash 的同一个值, 这里显式留痕)
    task_scope_hash: str = ""  # 任务 scope 的 hash
    caller_params_hash: str = ""  # 调用方参数 (task/why_this_fork/must_return/…) 的 hash
    # capsule_assembly_hash (Patch3 L903): hash(resolver_input_hash + scene_type + template_version
    # + caller_params_hash + truncation_config) — 整个 assembly 层面的指纹 (区别 resolver 判定层 input_hash)。
    capsule_assembly_hash: str = ""
    touched_nodes: list[CapsuleEntityRef] = Field(default_factory=list)  # 实际 touched 节点
    covered_subjects: list[CapsuleCoveredSubject] = Field(default_factory=list)  # 从 subjects[] 统一


class CapsuleInjectionFailedPayload(BaseModel):
    """M-3.1 §8.3 CapsuleInjectionFailed (RUN-031 T-L3kc-06) — path B fail-closed signal.

    Emitted when a skill-start aborts because a required knowledge file is missing; the active
    session is NOT created. Replaces the prior NodeTouched stub-rewrap (kind field).
    """

    model_config = _STRICT

    skill_id: str = Field(min_length=1)
    missing_file: str = Field(min_length=1)
    recovery_hint: str = Field(min_length=1)


class CapsuleAssemblyFailedPayload(BaseModel):
    """M-0.3 §2.8/§2.10 CapsuleAssemblyFailed (T-L0-16, 波1) — capsule 装配 abort 留痕 (path B)。

    M-0.1 写入失败时 capsule 装配中止: pipeline 不交付无留痕半截 capsule (抛受控 PipelineError),
    并 emit 本事件作为留痕 —— 记下失败发生在哪个 stage、原因、以及失败前已写的 partial event ids
    (这些事件被本 FAILED 标记作废)。留痕本身 best-effort: 存储全死时连本事件都写不进 (吞掉),
    但 abort 抛错的硬保证不破。final_error 可能为空串 (原样异常信息), 不加 min_length。
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    failed_stage: str = Field(min_length=1)
    final_error: str
    partial_event_ids: list[str] = Field(default_factory=list)
    input_hash: str | None = None


__all__ = [
    "CapsuleAssemblyFailedPayload",
    "CapsuleCompiledPayload",
    "CapsuleCoveredSubject",
    "CapsuleEntityRef",
    "CapsuleInjectionFailedPayload",
    "CapsuleSectionsSummary",
    "CapsuleSourceEventRef",
    "CapsuleTruncationLogEntry",
]
