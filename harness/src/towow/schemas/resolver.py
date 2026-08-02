"""ObligationActivationResolver input / verdict schemas.

# spec source:
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §6.1 (L770..L772) — sole caller is M-0.3 §2.4 stage 4 activation
#     §6.2 (L774..L816) ResolverInput full schema
#     §6.3 (L818..L846) ResolverVerdict full schema + M-0.3 §3.3 verdict validation rules
#
# Cross-validation invariant per M-0.6 §6.3 / M-0.3 §3.3:
#   ResolverVerdict.judgments[].obligation_id set == ResolverInput.candidates[].obligation_id set
#   (exact match — no missing, no extra; verdict validates via .check_covers(input)).
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    ResolverOutputFormat,
    SceneType,
    SubjectEntityType,
    VerdictEvidenceRefType,
)
from towow.schemas.obligation import ScopeHint

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class EntityRefShort(BaseModel):
    """Generic (entity_type, entity_id) used in declared_write_set / touched_nodes."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)


class ReentryContext(BaseModel):
    """ResolverInput.task_scope.reentry_context per M-0.6 §6.2 — only when reentry=true."""

    model_config = _STRICT

    previous_run_id: str = Field(min_length=1)
    previous_verdict_event_id: str = Field(min_length=1)


class TaskScope(BaseModel):
    """ResolverInput.task_scope per M-0.6 §6.2.

    task_type re-uses SceneType (same 9-value enum as capsule scene templates).
    Invariant: reentry=true ↔ reentry_context required.
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    task_type: SceneType
    target_artifacts: list[str] = Field(default_factory=list)
    declared_write_set: list[EntityRefShort] = Field(default_factory=list)
    touched_nodes: list[EntityRefShort] = Field(default_factory=list)
    reentry: bool
    reentry_context: ReentryContext | None = None

    @model_validator(mode="after")
    def enforce_reentry_context(self) -> Self:
        if self.reentry and self.reentry_context is None:
            raise ValueError("reentry=true requires reentry_context")
        if not self.reentry and self.reentry_context is not None:
            raise ValueError("reentry=false forbids reentry_context")
        return self


class ObligationCandidate(BaseModel):
    """ResolverInput.candidates[] per M-0.6 §6.2 — simplified obligation view sent to resolver."""

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    nature: ObligationNature
    severity: ObligationSeverity
    scope_type: ObligationScopeType
    scope_rule: str = Field(min_length=1)
    scope_hint: ScopeHint | None = None
    definition: str = Field(min_length=1)
    attached_to: list[EntityRefShort] = Field(default_factory=list)


class ConceptGraphNeighborhood(BaseModel):
    """ResolverInput.projection_slice.concept_graph_neighborhood per M-0.6 §6.2.

    N-hop neighborhood around task touched_nodes — exact node/edge schemas left as
    dict[str, object] since concept graph node format is M-0.2 projection layer concern.
    """

    model_config = _STRICT

    nodes: list[dict[str, object]] = Field(default_factory=list)
    edges: list[dict[str, object]] = Field(default_factory=list)


class ProjectionSlice(BaseModel):
    """ResolverInput.projection_slice per M-0.6 §6.2."""

    model_config = _STRICT

    concept_graph_neighborhood: ConceptGraphNeighborhood
    at_reference_graph_slice: list[dict[str, object]] | None = None
    task_graph_slice: list[dict[str, object]] | None = None


class ResolverConfig(BaseModel):
    """ResolverInput.resolver_config per M-0.6 §6.2 — fixed model=sonnet, strict_json output."""

    model_config = _STRICT

    model: str = Field(default="sonnet", min_length=1)  # v3 design decision: fixed sonnet
    timeout_ms: int = Field(default=30000, ge=1000)
    contract_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    output_format: ResolverOutputFormat = ResolverOutputFormat.STRICT_JSON


class ResolverInput(BaseModel):
    """M-0.6 §6.2 ResolverInput — input to ObligationActivationResolver."""

    model_config = _STRICT

    task_scope: TaskScope
    candidates: list[ObligationCandidate] = Field(min_length=1)
    projection_slice: ProjectionSlice
    resolver_config: ResolverConfig = Field(default_factory=ResolverConfig)


class VerdictEvidence(BaseModel):
    """ResolverVerdict.judgments[].evidence[] per M-0.6 §6.3."""

    model_config = _STRICT

    ref_type: VerdictEvidenceRefType
    ref: str = Field(min_length=1)


class ObligationJudgment(BaseModel):
    """ResolverVerdict.judgments[] per M-0.6 §6.3.

    Invariants per spec §6.3 + M-0.3 §3.3:
      - confidence ∈ [0.0, 1.0]
      - uncertainty required when confidence < 0.7
      - fallback_applied=true only when conservative_fallback engaged (else false)
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    hit: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    evidence: list[VerdictEvidence] = Field(default_factory=list)
    uncertainty: str | None = None
    fallback_applied: bool

    @model_validator(mode="after")
    def enforce_low_confidence_uncertainty(self) -> Self:
        if self.confidence < 0.7 and self.uncertainty is None:
            raise ValueError(
                f"confidence={self.confidence} < 0.7 requires uncertainty explanation",
            )
        return self


class VerdictMeta(BaseModel):
    """ResolverVerdict.meta per M-0.6 §6.3."""

    model_config = _STRICT

    verdict_id: str = Field(min_length=1)
    resolver_runtime_ms: int = Field(ge=0)
    model: str = Field(min_length=1)
    contract_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    total_candidates: int = Field(ge=0)
    total_hits: int = Field(ge=0)
    total_fallbacks: int = Field(ge=0)


class ResolverVerdict(BaseModel):
    """M-0.6 §6.3 ResolverVerdict — output from ObligationActivationResolver.

    Use .check_covers(input) to verify M-0.3 §3.3 cross-input coverage invariant.
    """

    model_config = _STRICT

    judgments: list[ObligationJudgment] = Field(min_length=1)
    meta: VerdictMeta

    @model_validator(mode="after")
    def enforce_meta_consistency(self) -> Self:
        if self.meta.total_candidates != len(self.judgments):
            raise ValueError(
                f"meta.total_candidates={self.meta.total_candidates} != "
                f"len(judgments)={len(self.judgments)}",
            )
        actual_hits = sum(1 for j in self.judgments if j.hit)
        if self.meta.total_hits != actual_hits:
            raise ValueError(
                f"meta.total_hits={self.meta.total_hits} != "
                f"actual hits={actual_hits}",
            )
        actual_fallbacks = sum(1 for j in self.judgments if j.fallback_applied)
        if self.meta.total_fallbacks != actual_fallbacks:
            raise ValueError(
                f"meta.total_fallbacks={self.meta.total_fallbacks} != "
                f"actual fallbacks={actual_fallbacks}",
            )
        return self

    def check_covers(self, resolver_input: ResolverInput) -> None:
        """Cross-input coverage check per M-0.3 §3.3.

        Raises ValueError if judgment obligation_id set differs from input candidates'
        (extra, missing, or duplicate); raises nothing on exact match.
        """
        input_ids = {c.obligation_id for c in resolver_input.candidates}
        verdict_ids_list = [j.obligation_id for j in self.judgments]
        verdict_ids = set(verdict_ids_list)
        if len(verdict_ids) != len(verdict_ids_list):
            duplicates = [oid for oid in verdict_ids_list if verdict_ids_list.count(oid) > 1]
            raise ValueError(f"duplicate obligation_id in judgments: {sorted(set(duplicates))}")
        missing = input_ids - verdict_ids
        extra = verdict_ids - input_ids
        if missing or extra:
            raise ValueError(
                f"verdict judgments coverage mismatch: missing={sorted(missing)} "
                f"extra={sorted(extra)}",
            )


__all__ = [
    "ConceptGraphNeighborhood",
    "EntityRefShort",
    "ObligationCandidate",
    "ObligationJudgment",
    "ProjectionSlice",
    "ReentryContext",
    "ResolverConfig",
    "ResolverInput",
    "ResolverVerdict",
    "TaskScope",
    "VerdictEvidence",
    "VerdictMeta",
]
