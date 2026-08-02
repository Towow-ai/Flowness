"""envelope category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.6 EnvelopeFields (L186..L200)
#     §3.6 (L785..L811) — 2 envelope-class event payloads
#   03-l0-truth-source/M-0.4-envelope-detailed-design.md
#     §2 (L31..L119) full envelope schema — referenced; full TransactionEnvelope model in Step 1.e
#
# Note: TransactionEnvelopeSubmitted payload here uses the M-0.1 §3.6 summary shape;
# the full M-0.4 envelope model lives in schemas/envelope.py (Step 1.e).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    NodeTouchedEntityType,
    NodeTouchedTouchType,
    ObligationDeclaredStatus,
    PatchType,
    SubjectEntityType,
)

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class EntityRefWithVersion(BaseModel):
    """read_set[] per M-0.1 §3.6 — entity ref with consistency version."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class EntityRefWithChangeType(BaseModel):
    """write_set[] per M-0.1 §3.6."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    change_type: str = Field(min_length=1)


class ActiveObligationDeclared(BaseModel):
    """active_obligations_declared[] per M-0.1 §2.3.6 + §3.6."""

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    status: ObligationDeclaredStatus


class ClaimItem(BaseModel):
    """claims[] per M-0.1 §3.6."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    claim_type: str = Field(min_length=1)


class PatchSummary(BaseModel):
    """patches[] inline summary per M-0.1 §3.6 (full diffs live in artifacts)."""

    model_config = _STRICT

    file_path: str = Field(min_length=1)
    diff_summary: str
    patch_type: PatchType | None = None


class SelfCheck(BaseModel):
    """self_check sub-object per M-0.1 §3.6."""

    model_config = _STRICT

    passed: bool
    checks_run: list[str] = Field(default_factory=list)


class TransactionEnvelopeSubmittedPayload(BaseModel):
    """M-0.1 §3.6 envelope summary — full envelope model in Step 1.e (schemas/envelope.py)."""

    model_config = _STRICT

    read_set: list[EntityRefWithVersion] = Field(default_factory=list)
    write_set: list[EntityRefWithChangeType] = Field(default_factory=list)
    # RUN-091 R1 (M-0.4 §3.1): agent's declared write intent, preserved when it diverges from the
    # system-derived write_set fact (declared-vs-actual reconciliation). Empty when they matched.
    declared_write_set: list[EntityRefWithChangeType] = Field(default_factory=list)
    active_obligations_declared: list[ActiveObligationDeclared] = Field(default_factory=list)
    claims: list[ClaimItem] = Field(default_factory=list)
    patches: list[PatchSummary] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    self_check: SelfCheck


class NodeTouchedPayload(BaseModel):
    """M-0.1 §3.6 — high-frequency low-value touch trace; discardable_noise."""

    model_config = _STRICT

    target_entity_type: NodeTouchedEntityType
    target_entity_id: str = Field(min_length=1)
    touch_type: NodeTouchedTouchType


__all__ = [
    "ActiveObligationDeclared",
    "ClaimItem",
    "EntityRefWithChangeType",
    "EntityRefWithVersion",
    "NodeTouchedPayload",
    "PatchSummary",
    "SelfCheck",
    "TransactionEnvelopeSubmittedPayload",
]
