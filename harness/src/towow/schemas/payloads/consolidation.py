"""consolidation category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.4 ConsolidationFields (L161..L170)
#     §3.4 (L730..L770) — 4 base consolidation payloads
#     §6.2 Provenance-Preserving Abstraction invariant — retained_provenance must be true
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    ArchiveDestination,
    BaseClassification,
    DigestSupersedeNoveltyType,
    DigestType,
    EventCategory,
    LookbackType,
    ProvenanceRefRelevance,
    RetentionAction,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class SegmentRange(BaseModel):
    """ConsolidationFields.segment_range per M-0.1 §2.3.4."""

    model_config = _STRICT

    start_seq: int = Field(ge=0)
    end_seq: int = Field(ge=0)


class LookbackWindow(BaseModel):
    """M-0.7 §2.3 CrossRunConsolidationCommitted.lookback_window — how the固结 window was chosen."""

    model_config = _STRICT

    lookback_type: LookbackType
    parameters: dict[str, object] = Field(default_factory=dict)


class ProvenanceRef(BaseModel):
    """M-0.7 §2.3 digest.provenance_refs[] — a source event the digest references (O-06 Inv2).

    retained_provenance=True (on the payload) means every digest carries these refs back to the
    original events; immutable_truth refs must stay hot/cold accessible (§6.4 inv2 / §4.2).
    """

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    relevance: ProvenanceRefRelevance


class CrossRunConsolidationCommittedPayload(BaseModel):
    """CrossRunConsolidationCommitted payload — M-0.1 §3.4 base + M-0.7 §2.3 full enrichment.

    Backward compatible: the M-0.1 §3.4 base fields (segment_range / digest_hash / digest_type /
    original_event_count / retained_provenance) remain — retained_provenance is pinned True
    (O-06 Invariant 2, schema-enforced). M-0.7 §2.3 adds the consolidation-run context:
    consolidation_run_id, lookback_window, digest_storage_path, provenance_refs (the digest's
    references back to source events, O-06 Inv2), f18_digests_consumed (which F-18
    RunDigestPublished events were folded in), and snapshot_event_id (the SnapshotCreated produced
    in the same batch). All §2.3 additions default to None / empty so existing producers validate.

    NOTE (minor spec naming variance): §2.3 lists digest_type ∈ [summary | semantic_consolidation |
    procedural_pattern] while the M-0.1 §2.3.4 DigestType enum is [summary | aggregation |
    archive_pointer]. digest_type stays pinned to SUMMARY here (the existing constraint + producer);
    reconciling the two digest_type vocabularies is a frozen-enum reverse patch (out of this batch).
    """

    model_config = _STRICT

    segment_range: SegmentRange
    digest_hash: str = Field(min_length=1)
    digest_type: Literal[DigestType.SUMMARY] = DigestType.SUMMARY
    original_event_count: int = Field(ge=0)
    retained_provenance: Literal[True] = True  # O-06 Invariant 2 enforced at schema layer
    # ─── M-0.7 §2.3 enrichment (optional for M-0.1 base-form backward compat) ───
    consolidation_run_id: str | None = None
    lookback_window: LookbackWindow | None = None
    digest_storage_path: str | None = None
    provenance_refs: list[ProvenanceRef] = Field(default_factory=list)
    f18_digests_consumed: list[str] = Field(default_factory=list)
    snapshot_event_id: str | None = None


class ClassificationSummary(BaseModel):
    """M-0.7 §2.4 ArchiveSegmentMoved.classification_summary — per-class event counts.

    The as-is base_classification breakdown over the moved segment (the §4.4 honest
    classification_summary 三类占比 — used by audit to verify the §4.1 retention matrix decision).
    """

    model_config = _STRICT

    immutable_truth_count: int = Field(ge=0)
    abstractable_process_count: int = Field(ge=0)
    discardable_noise_count: int = Field(ge=0)


class PreparedArtifactRef(BaseModel):
    """M-0.7 §7.2 ArchiveSegmentMoved.prepared_artifact_ref — Prepare/Commit/Finalize 留痕.

    §11.9: archive 物理 IO 走三段式; prepare 阶段写 staging cold tmp (不动 hot/index). This ref
    records the staging artifact + its hashes + the (unchanged) hot path, so a finalize can verify
    the committed event matches the prepared bytes (event log 唯一事实源, §7.2 forbidden recovery).
    """

    model_config = _STRICT

    segment_id: int = Field(ge=0)
    staging_path: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    archive_hash: str = Field(min_length=1)
    hot_path_unchanged: str = Field(min_length=1)


class ArchiveSegmentMovedPayload(BaseModel):
    """ArchiveSegmentMoved payload — M-0.1 §3.4 minimal form + M-0.7 §2.4/§7.2 full enrichment.

    Backward compatible: the M-0.1 §3.4 / §4.3 tombstone producer supplies the 4 base fields
    (segment_range / archive_destination / original_event_count / provenance_preserved). M-0.7 §2.4
    adds segment_path_before / segment_path_after / classification_summary /
    triggered_by_consolidation_event_id / triggered_by_gc_run_id; §7.2 adds prepared_artifact_ref.
    All §2.4/§7.2 additions default to None so the existing tombstone keeps validating.

    archive_destination is now the ArchiveDestination enum [cold, discarded] (M-0.7 §2.4) — the
    cold PATH lives in segment_path_after, not in archive_destination. provenance_preserved is true
    for cold archive, false for discarded (§4.3).
    """

    model_config = _STRICT

    segment_range: SegmentRange
    archive_destination: ArchiveDestination
    original_event_count: int = Field(ge=0)
    provenance_preserved: bool
    # ─── M-0.7 §2.4 enrichment (optional for §4.3 tombstone backward compat) ───
    segment_path_before: str | None = None
    segment_path_after: str | None = None  # cold 目录路径; discarded 时为 null (§2.4)
    classification_summary: ClassificationSummary | None = None
    triggered_by_consolidation_event_id: str | None = None
    triggered_by_gc_run_id: str | None = None
    # ─── M-0.7 §7.2 enrichment (Prepare/Commit/Finalize 三段式 staging 留痕) ───
    prepared_artifact_ref: PreparedArtifactRef | None = None
    # ─── M-0.7 v3.6+ 方案 B (段内细粒度 compaction, DESIGN-M07-v3.6 §4.2) ───
    # When candidate 乙 compacts a sealed segment: the WHOLE segment goes to cold verbatim (this
    # ArchiveSegmentMoved, destination=cold), THEN a "thin segment" carrying only the pinned events
    # is written back to hot at the SAME segment number. ``rewritten_hot_segment`` flags that終態 so
    # crash recovery does not mistake "cold archive present + hot file with same seg号 present" for
    # the forbidden-cold anomaly — it is compaction's normal end state. ``compacted_retained_event_
    # ids`` records exactly which events the thin segment kept (the pinned set), so recovery /audit
    # can verify the hot thin segment against the accepted event. Both default None → every existing
    # producer (§4.3 tombstone / §7.2 full cold MOVE / consolidation) keeps validating unchanged.
    rewritten_hot_segment: bool | None = None
    compacted_retained_event_ids: list[str] | None = None


class DigestSupersedeNovelty(BaseModel):
    """M-0.7 §2.5 DigestSuperseded.novelty — why the digest is being amended.

    no_new_information is pinned to False (Literal[False]) — §2.5 invariant: a digest amend
    always carries novelty. explanation is required (free-text rationale).
    """

    model_config = _STRICT

    novelty_type: DigestSupersedeNoveltyType
    no_new_information: Literal[False] = False  # §2.5 invariant — must be false
    explanation: str = Field(min_length=1)


class DigestSupersededPayload(BaseModel):
    """DigestSuperseded payload — M-0.1 §3.4 minimal form + M-0.7 §2.5 full enrichment.

    Backward compatible: the M-0.1 §3.4 producer supplies only digest_hash (the new digest's
    hash). M-0.7 §2.5 adds the supersede chain refs (superseded_digest_event_id pointing at the
    old CrossRunConsolidationCommitted, new_digest_event_id pointing at the replacement) plus the
    novelty block (why amended). The §2.5 fields default to None so existing minimal producers
    keep validating. O-06 Invariant 3 (correctable consolidation): amends go through a supersede
    chain, never an in-place edit.
    """

    model_config = _STRICT

    digest_hash: str = Field(min_length=1)
    # ─── M-0.7 §2.5 enrichment (optional for M-0.1 minimal-form backward compat) ───
    superseded_digest_event_id: str | None = None
    new_digest_event_id: str | None = None
    novelty: DigestSupersedeNovelty | None = None


class RetentionRule(BaseModel):
    """RetentionPolicyChanged.after_state.new_rules[] per M-0.1 §3.4 / M-0.7 §2.6.

    T-L0-36: 补 retention_action (4 态) + base_classification + event_type (M-0.7 §2.6 完整 payload)。
    event_category 降为 optional (§2.6 三个适用条件 category?/type?/base_classification? 任填)。
    retention_action 是核心新字段; 省略时由 GC 按 base_classification 走 §4 矩阵默认 (见
    retention_policy.default_retention_action)。
    """

    model_config = _STRICT

    event_category: EventCategory | None = None
    event_type: str | None = None
    base_classification: BaseClassification | None = None
    retention_action: RetentionAction | None = None
    retention_days: int = Field(default=0, ge=0)
    can_digest: bool = False


class RetentionPolicyChangedAfter(BaseModel):
    model_config = _STRICT

    policy_id: str = Field(min_length=1)
    new_rules: list[RetentionRule] = Field(min_length=1)
    change_reason: str


class RetentionPolicyChangedPayload(BaseModel):
    """M-0.1 §3.4 — base_classification=immutable_truth (policy 变更不可丢)."""

    model_config = _STRICT

    after_state: RetentionPolicyChangedAfter


__all__ = [
    "ArchiveSegmentMovedPayload",
    "ClassificationSummary",
    "CrossRunConsolidationCommittedPayload",
    "DigestSupersedeNovelty",
    "DigestSupersededPayload",
    "LookbackWindow",
    "PreparedArtifactRef",
    "ProvenanceRef",
    "RetentionPolicyChangedAfter",
    "RetentionPolicyChangedPayload",
    "RetentionRule",
    "SegmentRange",
]
