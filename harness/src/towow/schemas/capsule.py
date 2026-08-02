"""Capsule canonical assembly result schema.

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §7 capsule size management + 8-section priority order (L528..L590)
#     §9.2 CapsuleCompiled event payload (L661+; event-level in payloads/capsule.py)
#     §7.1 MAX_CAPSULE_TOKENS limits (L536..L541)
#     §7.2 truncation strategy + truncation_log (L543..L590)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import CapsuleSection, SceneType

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ─── Per-section content (typed wrappers) ───────────────────────────────────────


class CapsuleSectionContent(BaseModel):
    """A single capsule section after assembly.

    Content kept as str — concrete rendering (markdown / plain text) is the
    skill-side concern; M-0.3 only knows the section identity + token estimate.
    """

    model_config = _STRICT

    section: CapsuleSection
    content: str
    estimated_tokens: int = Field(ge=0)


# ─── Truncation log ─────────────────────────────────────────────────────────────


class TruncationLogEntry(BaseModel):
    """Per-section truncation record per M-0.3 §7.2 (capsule.truncation_log)."""

    model_config = _STRICT

    section: CapsuleSection
    method: str = Field(min_length=1)  # "reduce_neighborhood_hops" / "summarize_section" / etc.
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)


# ─── CapsuleResult — assembled capsule object ───────────────────────────────────


class CapsuleResult(BaseModel):
    """Full assembled capsule per M-0.3 §7 + §9.2.

    Sections list must be sorted by priority (highest priority first); each section
    appears at most once. Truncation invariants per M-0.3 §7.2:
      - RED_LINE_OBLIGATIONS / TASK / MUST_RETURN never truncated (priority 1/2/3)
      - estimated_tokens (after truncation) ≤ max_tokens
      - truncation_applied=true ↔ truncation_log non-empty
    """

    model_config = _STRICT

    input_hash: str = Field(min_length=1)
    scene_type: SceneType
    target_fork_id: str = Field(min_length=1)
    sections: list[CapsuleSectionContent] = Field(min_length=1)
    estimated_tokens: int = Field(ge=0)
    max_tokens: int = Field(gt=0)
    truncation_applied: bool = False
    truncation_log: list[TruncationLogEntry] = Field(default_factory=list)


__all__ = [
    "CapsuleResult",
    "CapsuleSectionContent",
    "TruncationLogEntry",
]
