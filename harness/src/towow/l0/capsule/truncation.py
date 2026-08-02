"""M-0.3 §7.2 capsule truncation strategy (priority-based) + truncation 留痕.

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §7.1 MAX_CAPSULE_TOKENS limits (L536..L541)
#     §7.2 truncation strategy + truncation_log (L543..L590)
#
# T-L0-18 (波2): truncate_capsule(capsule) — while estimated_tokens > max_tokens, take the
#   lowest-priority non-empty section and truncate it by the spec-designated method
#   (KNOWN_FACTS → reduce_neighborhood_hops; TASK_BRIEFING / MUST_NOT_DO → summarize_section;
#   FILES_TO_READ / TOOL_CONTEXT / HIDDEN_REMINDER → reduce_items). RED_LINE_OBLIGATIONS /
#   TASK / MUST_RETURN are never truncated (priority 1/2/3). If still over-limit after every
#   truncatable section is exhausted → raise PipelineError("capsule_too_large_after_truncation").
#
# T-L0-19 (波2): a truncation that fires sets capsule.truncation_applied=True and appends one
#   TruncationLogEntry per (section, step) recording method + tokens_before/after — so the
#   CapsuleCompiled留痕 (capsule_sections_summary) can audit exactly what was cut.
#
# Pure function over the assembled CapsuleResult — no I/O, no event emit. The KNOWN_FACTS
# reduce_neighborhood_hops re-render uses the Neighborhood the pipeline already expanded (no
# projection re-read): it re-derives the KNOWN_FACTS body at hops-1 (2→1→0/direct), which is
# the realized effect of the spec's `reduce_neighborhood_hops`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from towow.schemas.capsule import (
    CapsuleResult,
    CapsuleSectionContent,
    TruncationLogEntry,
)
from towow.schemas.enums import CapsuleSection

if TYPE_CHECKING:
    from towow.l0.capsule.neighborhood import Neighborhood


# M-0.3 §7.2 — sections that are NEVER truncated (priority 1/2/3).
_NEVER_TRUNCATE: frozenset[CapsuleSection] = frozenset(
    {
        CapsuleSection.RED_LINE_OBLIGATIONS,
        CapsuleSection.TASK,
        CapsuleSection.MUST_RETURN,
    },
)

# M-0.3 §7.2 — truncation candidate order, lowest priority FIRST (the order
# `lowest_priority_nonempty_section()` walks). HIDDEN_REMINDER (T-L0-13, lower than
# TOOL_CONTEXT) is the lowest — truncated first. Mirrors the CapsuleSection enum tail
# in reverse, minus the three never-truncate sections.
_TRUNCATION_ORDER: tuple[CapsuleSection, ...] = (
    CapsuleSection.HIDDEN_REMINDER,
    CapsuleSection.TOOL_CONTEXT,
    CapsuleSection.FILES_TO_READ,
    CapsuleSection.KNOWN_FACTS,
    CapsuleSection.TASK_BRIEFING,
    CapsuleSection.MUST_NOT_DO,
)

_TRUNCATED_MARKER = "\n[truncated]"


def _estimate_tokens(content: str) -> int:
    """Same heuristic the pipeline uses when assembling sections (len // 4, floor 1)."""
    return max(1, len(content) // 4)


class _TruncationAbort(Exception):
    """Raised internally when truncation cannot bring the capsule under the limit.

    The pipeline (truncate_capsule's caller boundary) translates this to a PipelineError so
    callers see a single error type (M-0.3 §2.10 / §7.2 abort).
    """


def truncate_capsule(
    capsule: CapsuleResult,
    *,
    neighborhood: Neighborhood | None = None,
    render_neighborhood: Callable[[Neighborhood, int], str] | None = None,
) -> CapsuleResult:
    """M-0.3 §7.2 — bring `capsule` under its own `max_tokens` by priority-based truncation.

    Returns the original capsule unchanged when it already fits. Otherwise returns a new
    CapsuleResult with truncated sections, truncation_applied=True, and a truncation_log
    recording every cut. Raises `_TruncationAbort` if no further truncation is possible and
    the capsule is still over-limit (caller maps to PipelineError).

    `neighborhood` + `render_neighborhood` are optional: when supplied, KNOWN_FACTS reduction
    re-renders the neighborhood at decreasing hops (the spec's reduce_neighborhood_hops). When
    absent (e.g. a synthetic capsule), KNOWN_FACTS falls back to reduce_items like the other
    list sections — still a real cut, just not hops-aware.
    """
    if capsule.estimated_tokens <= capsule.max_tokens:
        return capsule

    # Mutable working copy: section -> content; plus a per-section hops cursor for KNOWN_FACTS.
    sections: dict[CapsuleSection, str] = {s.section: s.content for s in capsule.sections}
    log: list[TruncationLogEntry] = []
    # KNOWN_FACTS hops cursor — start one below the neighborhood's max reach so the first
    # reduce actually shrinks it; floored at 0 (seeds only / direct).
    known_facts_hops = _initial_hops(neighborhood)

    while _total_tokens(sections) > capsule.max_tokens:
        section = _lowest_priority_nonempty(sections)
        if section is None:
            # No truncatable non-empty section left — abort (§7.2).
            raise _TruncationAbort
        before = _estimate_tokens(sections[section])

        if section is CapsuleSection.KNOWN_FACTS and neighborhood is not None and render_neighborhood is not None:
            method = "reduce_neighborhood_hops"
            if known_facts_hops <= 0:
                # Already at direct/seeds-only and still over — drop the section body entirely.
                new_content = ""
            else:
                known_facts_hops -= 1
                new_content = render_neighborhood(neighborhood, known_facts_hops)
        elif section in {CapsuleSection.TASK_BRIEFING, CapsuleSection.MUST_NOT_DO}:
            method = "summarize_section"
            new_content = _summarize(sections[section], max_tokens=max(1, before // 2))
        else:
            # FILES_TO_READ / TOOL_CONTEXT / HIDDEN_REMINDER / KNOWN_FACTS-without-neighborhood.
            method = "reduce_items"
            new_content = _reduce_items(sections[section], keep_ratio=0.5)

        after = _estimate_tokens(new_content) if new_content.strip() else 0
        if after >= before:
            # Guard against a no-op step (would spin forever): force the section empty.
            new_content = ""
            after = 0
        sections[section] = new_content
        log.append(
            TruncationLogEntry(
                section=section,
                method=method,
                tokens_before=before,
                tokens_after=after,
            ),
        )

    # Reassemble sections preserving the original (priority) order; drop now-empty bodies
    # except never-truncate sections (which keep their content) — schema requires ≥1 section.
    rebuilt: list[CapsuleSectionContent] = []
    for original in capsule.sections:
        content = sections[original.section]
        if not content.strip() and original.section not in _NEVER_TRUNCATE:
            continue
        rebuilt.append(
            CapsuleSectionContent(
                section=original.section,
                content=content,
                estimated_tokens=_estimate_tokens(content) if content.strip() else 0,
            ),
        )
    if not rebuilt:
        # Defensive: never-truncate sections were absent and everything got cut. Keep TASK.
        rebuilt = [s for s in capsule.sections if s.section is CapsuleSection.TASK] or list(
            capsule.sections[:1],
        )

    return capsule.model_copy(
        update={
            "sections": rebuilt,
            "estimated_tokens": sum(s.estimated_tokens for s in rebuilt),
            "truncation_applied": True,
            "truncation_log": log,
        },
    )


def _initial_hops(neighborhood: Neighborhood | None) -> int:
    """Starting hops cursor for KNOWN_FACTS reduction (spec: 2 → 1 → direct)."""
    # The pipeline's scene templates use ≤2 hops; start at 2 so the first reduce → 1.
    if neighborhood is None:
        return 0
    return 2


def _total_tokens(sections: dict[CapsuleSection, str]) -> int:
    return sum(_estimate_tokens(c) for c in sections.values() if c.strip())


def _lowest_priority_nonempty(sections: dict[CapsuleSection, str]) -> CapsuleSection | None:
    """M-0.3 §7.2 lowest_priority_nonempty_section() over the truncatable sections."""
    for section in _TRUNCATION_ORDER:
        body = sections.get(section)
        if body is not None and body.strip():
            return section
    return None


def _summarize(content: str, *, max_tokens: int) -> str:
    """M-0.3 §7.2 summarize_section — keep the leading max_tokens worth, mark the tail."""
    char_budget = max(1, max_tokens * 4)
    if len(content) <= char_budget:
        # Already within budget but section was chosen — drop a trailing chunk so progress is made.
        char_budget = max(1, len(content) // 2)
    head = content[:char_budget].rstrip()
    return head + _TRUNCATED_MARKER


def _reduce_items(content: str, *, keep_ratio: float) -> str:
    """M-0.3 §7.2 reduce_items — keep the first ceil(keep_ratio * n) newline-delimited items."""
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    keep = max(1, int(len(lines) * keep_ratio))
    if keep >= len(lines):
        keep = len(lines) - 1  # always drop at least one so the loop progresses
    if keep <= 0:
        return ""
    return "\n".join(lines[:keep])


__all__ = ["truncate_capsule"]
