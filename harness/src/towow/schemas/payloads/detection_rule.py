"""detection_rule category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.9 DetectionRuleFields (L228..L237)
#     §3.9 (L897..L937) — 4 base detection_rule payloads
#   05-l2-maintenance/M-2.3-candidate-curation-lifecycle-detailed-design.md
#     §3 Patch M-2.3-D: DetectionRuleProposed.spawned_from_escalation_id (optional, non-breaking)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    DetectionRuleLifecycleState,
    DetectionRuleProposedSeverity,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class DetectionRuleProposedPayload(BaseModel):
    """M-0.1 §3.9 DetectionRuleProposed + Patch M-2.3-D spawned_from_escalation_id."""

    model_config = _STRICT

    rule_id: str = Field(min_length=1)
    rule_lifecycle_state: Literal[DetectionRuleLifecycleState.PROPOSED] = (
        DetectionRuleLifecycleState.PROPOSED
    )
    rule_definition: str = Field(min_length=1)
    spawned_from_finding_id: str | None = None
    spawned_from_escalation_id: str | None = None  # Patch M-2.3-D (non-breaking)
    proposed_severity: DetectionRuleProposedSeverity


class DetectionRuleShadowingPayload(BaseModel):
    """M-0.1 §3.9 — auto-promoted from proposed by M-2.3 detection_rule_lifecycle daemon."""

    model_config = _STRICT

    rule_id: str = Field(min_length=1)
    rule_lifecycle_state: Literal[DetectionRuleLifecycleState.SHADOW] = (
        DetectionRuleLifecycleState.SHADOW
    )
    shadow_start_event_offset: int = Field(ge=0)


class ShadowStats(BaseModel):
    """DetectionRulePromoted.shadow_stats per M-0.1 §3.9 / §2.3.9."""

    model_config = _STRICT

    runs: int = Field(ge=0)
    hits: int = Field(ge=0)
    false_positives: int = Field(ge=0)


class DetectionRulePromotedPayload(BaseModel):
    """M-0.1 §3.9 — Nature-triggered promotion shadow → active_warning → enforced.

    E.1.1 LEDGER Conflict 3 resolved: rule_lifecycle_state emit canonical-only —
    only active_warning / enforced are valid promotion targets. The deprecated alias
    "warning" is normalized to "active_warning" on parse via
    `parse_detection_rule_state()`; emitters never produce "warning".
    """

    model_config = _STRICT

    rule_id: str = Field(min_length=1)
    rule_lifecycle_state: Literal[
        DetectionRuleLifecycleState.ACTIVE_WARNING,
        DetectionRuleLifecycleState.ENFORCED,
    ]
    promotion_reason: str
    shadow_stats: ShadowStats


class DetectionRuleRetiredPayload(BaseModel):
    """M-0.1 §3.9 — auto by M-2.3 daemon (high FP / no hits) or Nature-triggered."""

    model_config = _STRICT

    rule_id: str = Field(min_length=1)
    rule_lifecycle_state: Literal[DetectionRuleLifecycleState.RETIRED] = (
        DetectionRuleLifecycleState.RETIRED
    )
    retirement_reason: str = Field(min_length=1)


__all__ = [
    "DetectionRulePromotedPayload",
    "DetectionRuleProposedPayload",
    "DetectionRuleRetiredPayload",
    "DetectionRuleShadowingPayload",
    "ShadowStats",
]
