"""T-TRACK-01 capability status event payload schemas (capability_status real projection).

# spec source: 01-reconciliation/STATUS-603.md §5 "真正的修法" +
#   LANDING-PLAN 新增项 T-TRACK-01 "能力状态真 projection"。
#
# Why these events exist:
#   The 603-capability board (CAPABILITY-PANORAMA.md → STATUS-603.md) was a static 2026-05-29
#   markdown snapshot reconciled by hand: when a wave task lands and flips a capability from
#   fake-done → done-real, the board did not move (the disease the whole phase is named after,
#   finding-phase-status-not-a-projection). The cure makes capability status event-sourced:
#     - CapabilityBaselineIngested — a ONE-TIME ingestion of the panorama baseline into the
#       event log (with source-file + git-commit provenance), seeding the capability_status
#       projection so refresh no longer reads markdown.
#     - CapabilityStatusAdvanced — emitted whenever a landing/debt-resolution moves a single
#       capability's classification, flipping its current_classification in the projection.
#   gen_status_603.py then derives the board purely from capability_status + debt_registry
#   (both event-derived projections).
#
# Category: reuses semantic_judgment (the system judging a capability's done-state) — same
# family as DebtRegistered, no new EventCategory. Path-B writes (immediately audit-visible).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import CapabilityClassification, EnforcementLevel

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class CapabilityBaselineEntry(BaseModel):
    """One capability row of the panorama baseline (seed of a capability_status node)."""

    model_config = _STRICT

    # stable ref — "{module}::{name}" (free-form, deterministic from the panorama parse).
    capability_id: str = Field(min_length=1)
    module: str = Field(min_length=1)
    name: str = Field(min_length=1)
    classification: CapabilityClassification
    # debt-ids the panorama row links this capability to (parsed from the row text). When all
    # are resolved the board shows the capability as debt-advanced (a derived overlay).
    linked_debts: list[str] = Field(default_factory=list)


class CapabilityBaselineIngestedPayload(BaseModel):
    """CapabilityBaselineIngested — one-time ingestion of the panorama baseline into the log."""

    model_config = _STRICT

    # provenance of the baseline: where it was read from + at which git commit.
    source_ref: str = Field(min_length=1)
    source_commit: str | None = None
    ingested_count: int = Field(ge=0)
    capabilities: list[CapabilityBaselineEntry]


class CapabilityStatusAdvancedPayload(BaseModel):
    """CapabilityStatusAdvanced — one capability moved to a new classification (a landing)."""

    model_config = _STRICT

    capability_id: str = Field(min_length=1)
    from_classification: CapabilityClassification | None = None
    to_classification: CapabilityClassification
    # what moved it — task_id (e.g. "T-L1-37") / run_id / debt_id.
    advanced_by: str = Field(min_length=1)
    # external evidence the advance is real (run id + audit/test reference).
    evidence_ref: str = Field(min_length=1)
    advanced_by_event_id: str | None = None
    # RUN-096 — ORTHOGONAL enforcement layer (built|enforced). None = unmarked = built (default).
    # Set to ENFORCED only with real-path evidence (physical gate + TDD probe + audit/provenance
    # artifact) in evidence_ref. Does NOT replace to_classification — a done-real capability stays
    # done-real; enforcement_level just records that its effect was proven to physically hold.
    enforcement_level: EnforcementLevel | None = None


__all__ = [
    "CapabilityBaselineEntry",
    "CapabilityBaselineIngestedPayload",
    "CapabilityStatusAdvancedPayload",
]
