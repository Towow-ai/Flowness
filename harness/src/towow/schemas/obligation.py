"""Canonical Obligation one-class object (not event-level payload).

# spec source:
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §2.1 (L53..L121) — full Obligation canonical schema
#     §2.2 (L123..L156) field semantics (obligation_id format, scope_rule vs scope_hint)
#     §3.1 (L172..L191) three-state canonical lifecycle (active/superseded/retired)
#     §6 (L770..L817) resolver protocol — see schemas/resolver.py
#
# Distinct from schemas/payloads/obligation.py — that file is event-level lifecycle
# payloads (Captured / Activated / Checked / Violated / Evolved / Retired). This file
# is the canonical persistent object that M-0.6 owns the semantic of.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    CheckerPatternType,
    ObligationAttachedToEntityType,
    ObligationCaptureSource,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class ScopeHint(BaseModel):
    """Obligation.scope_hint per M-0.6 §2.1 — mechanical filter assistance.

    Used by M-0.3 §2.3 mechanical filtering stage. Fields are conditionally relevant:
      - specific_artifact obligations populate target_artifacts
      - concept_neighborhood obligations populate concept_anchors
      - all_patches obligations may further filter by task_type_filter
    """

    model_config = _STRICT

    target_artifacts: list[str] | None = None
    concept_anchors: list[str] | None = None
    task_type_filter: list[str] | None = None


class ObligationAttachment(BaseModel):
    """Obligation.attached_to[] per M-0.6 §2.1.

    Multi-attach allowed — e.g. red-line obligation can attach to all Artifact nodes.
    """

    model_config = _STRICT

    entity_type: ObligationAttachedToEntityType
    entity_id: str = Field(min_length=1)


class CheckerMetadata(BaseModel):
    """Obligation.checker_metadata per M-0.6 §2.1 + M-0.5a Patch 3.

    mechanically_checkable=true ↔ forbidden_pattern + pattern_type must be set;
    mechanically_checkable=false ↔ checker_scope_hint is the resolver's guidance string.
    """

    model_config = _STRICT

    mechanically_checkable: bool
    forbidden_pattern: str | None = None
    pattern_type: CheckerPatternType | None = None
    checker_scope_hint: str | None = None


class CaptureProvenance(BaseModel):
    """Obligation.capture_provenance per M-0.6 §2.1.

    capture_source=system_bootstrap is special — bootstrap obligations cannot be retired
    per M-0.6 §5.1 (ProtocolInvariantImmutabilityError); enforced at the obligation
    system layer, not at this schema level.
    """

    model_config = _STRICT

    capture_source: ObligationCaptureSource
    source_event_refs: list[str] = Field(default_factory=list)
    captured_at: datetime  # informational only — authoritative timestamp from M-0.1 writer
    captured_by_actor_id: str = Field(min_length=1)


class Obligation(BaseModel):
    """Obligation one-class canonical object per M-0.6 §2.1.

    obligation_id format: "obl-{slug}-{short_uuid}" where slug is 5-30 lowercase chars +
    digits + hyphens, short_uuid is 8 chars base32 (uuid truncated).
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1, pattern=r"^obl-[a-z0-9-]{3,40}-[A-Za-z0-9]{4,16}$")
    nature: ObligationNature
    severity: ObligationSeverity
    scope_type: ObligationScopeType
    scope_rule: str = Field(min_length=1)
    scope_hint: ScopeHint | None = None
    definition: str = Field(min_length=1)
    attached_to: list[ObligationAttachment] = Field(min_length=1)
    checker_metadata: CheckerMetadata
    capture_provenance: CaptureProvenance


__all__ = [
    "CaptureProvenance",
    "CheckerMetadata",
    "Obligation",
    "ObligationAttachment",
    "ScopeHint",
]
