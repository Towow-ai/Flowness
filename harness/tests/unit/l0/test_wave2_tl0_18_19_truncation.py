"""波2 T-L0-18 / T-L0-19: M-0.3 §7.2 capsule 截断策略 (priority-based) + 截断留痕.

源表 done_criteria:
  T-L0-18 — 超 token 上限的 capsule 经 priority 截断后落在上限内交付; 无法压缩时 abort
            (capsule_too_large_after_truncation)。
  T-L0-19 — 被截断的 capsule 其 CompiledEvent 的 truncation_applied=true 且 truncation_log
            记录砍了哪些 section/items。

spec §7.2 优先级 (高→低, 高不截): RED_LINE_OBLIGATIONS / TASK / MUST_RETURN 永不截 >
MUST_NOT_DO (summarize) > TASK_BRIEFING (summarize) > KNOWN_FACTS (reduce_neighborhood_hops) >
FILES_TO_READ (reduce_items) > TOOL_CONTEXT (reduce_items)。while 超限 → 取最低优先级非空
section 截 → 仍超限 raise PipelineError。
"""

from __future__ import annotations

import pytest

from towow.l0.capsule.neighborhood import Neighborhood
from towow.l0.capsule.truncation import _TruncationAbort, truncate_capsule
from towow.schemas.capsule import CapsuleResult, CapsuleSectionContent
from towow.schemas.enums import CapsuleSection, SceneType


def _section(section: CapsuleSection, n_chars: int) -> CapsuleSectionContent:
    body = ("x" * 79 + "\n") * max(1, n_chars // 80)  # newline-delimited "items"
    return CapsuleSectionContent(
        section=section,
        content=body,
        estimated_tokens=max(1, len(body) // 4),
    )


def _capsule(sections: list[CapsuleSectionContent], *, max_tokens: int) -> CapsuleResult:
    return CapsuleResult(
        input_hash="sha256:deadbeef",
        scene_type=SceneType.EXECUTION,
        target_fork_id="fork-x",
        sections=sections,
        estimated_tokens=sum(s.estimated_tokens for s in sections),
        max_tokens=max_tokens,
    )


# ─── T-L0-18: under-limit is a no-op ─────────────────────────────────────────


def test_under_limit_no_truncation() -> None:
    cap = _capsule(
        [_section(CapsuleSection.TASK, 40), _section(CapsuleSection.KNOWN_FACTS, 40)],
        max_tokens=10_000,
    )
    out = truncate_capsule(cap)
    assert out is cap  # untouched
    assert out.truncation_applied is False
    assert out.truncation_log == []


# ─── T-L0-18: priority order — low-priority cut first, never-truncate intact ──


def test_truncates_lowest_priority_first_and_lands_under_limit() -> None:
    red = _section(CapsuleSection.RED_LINE_OBLIGATIONS, 4000)
    task = _section(CapsuleSection.TASK, 4000)
    must_return = _section(CapsuleSection.MUST_RETURN, 4000)
    tool = _section(CapsuleSection.TOOL_CONTEXT, 8000)
    files = _section(CapsuleSection.FILES_TO_READ, 8000)
    cap = _capsule([red, task, must_return, tool, files], max_tokens=3500)

    out = truncate_capsule(cap)

    # Landed under the limit (done_criteria: 截断后 ≤ 上限).
    assert out.estimated_tokens <= out.max_tokens
    # Never-truncate sections kept full content byte-for-byte.
    kept = {s.section: s.content for s in out.sections}
    assert kept[CapsuleSection.RED_LINE_OBLIGATIONS] == red.content
    assert kept[CapsuleSection.TASK] == task.content
    assert kept[CapsuleSection.MUST_RETURN] == must_return.content
    # The two lowest-priority sections were truncated (TOOL_CONTEXT first).
    cut_sections = {e.section for e in out.truncation_log}
    assert CapsuleSection.TOOL_CONTEXT in cut_sections
    # T-L0-19留痕: applied + non-empty log recording method + before/after.
    assert out.truncation_applied is True
    assert out.truncation_log
    for e in out.truncation_log:
        assert e.tokens_after < e.tokens_before  # a real cut, not a no-op
        assert e.method in {"reduce_items", "summarize_section", "reduce_neighborhood_hops"}


def test_higher_priority_only_cut_after_lower_exhausted() -> None:
    """A capsule small enough that cutting only the lowest section suffices must NOT touch
    a higher-priority section (proves priority ordering, not blanket truncation)."""
    task = _section(CapsuleSection.TASK, 400)
    briefing = _section(CapsuleSection.TASK_BRIEFING, 4000)  # higher priority than the two below
    tool = _section(CapsuleSection.TOOL_CONTEXT, 40_000)  # huge — cutting this alone fixes it
    cap = _capsule([task, briefing, tool], max_tokens=2000)

    out = truncate_capsule(cap)
    assert out.estimated_tokens <= out.max_tokens
    cut = {e.section for e in out.truncation_log}
    assert CapsuleSection.TOOL_CONTEXT in cut
    assert CapsuleSection.TASK_BRIEFING not in cut  # higher priority untouched
    # TASK_BRIEFING content preserved intact.
    kept = {s.section: s.content for s in out.sections}
    assert kept[CapsuleSection.TASK_BRIEFING] == briefing.content


# ─── T-L0-18: KNOWN_FACTS reduce_neighborhood_hops ───────────────────────────


def test_known_facts_uses_reduce_neighborhood_hops() -> None:
    known = _section(CapsuleSection.KNOWN_FACTS, 40_000)
    task = _section(CapsuleSection.TASK, 400)
    cap = _capsule([task, known], max_tokens=2000)

    nb = Neighborhood(concept_ids=frozenset({"c1", "c2"}))
    calls: list[int] = []

    def render(_nb: Neighborhood, hops: int) -> str:
        calls.append(hops)
        # Smaller render at fewer hops (simulates fewer neighborhood nodes).
        return ("k" * 79 + "\n") * max(1, hops * 5)

    out = truncate_capsule(cap, neighborhood=nb, render_neighborhood=render)
    assert out.estimated_tokens <= out.max_tokens
    assert calls  # the hops re-render was actually invoked
    assert all(e.method == "reduce_neighborhood_hops" for e in out.truncation_log)
    # hops decrease monotonically toward direct (2 → 1 → 0).
    assert calls == sorted(calls, reverse=True)


# ─── T-L0-18: abort when nothing left to truncate ────────────────────────────


def test_abort_when_uncompressible() -> None:
    """Only never-truncate sections, still over-limit → _TruncationAbort (→ PipelineError)."""
    red = _section(CapsuleSection.RED_LINE_OBLIGATIONS, 40_000)
    task = _section(CapsuleSection.TASK, 40_000)
    cap = _capsule([red, task], max_tokens=100)
    with pytest.raises(_TruncationAbort):
        truncate_capsule(cap)


# ─── 反 vacuous 负对照 (T-L0-18): break the priority cut → still over-limit ──


def test_negative_control_no_truncation_leaves_capsule_over_limit() -> None:
    """If truncation were a no-op (the bug we guard against), the capsule would stay over-limit.

    This pins the real assertion: a truncated capsule MUST end ≤ max_tokens; a pipeline that
    short-circuits truncation (returns the assembled capsule unchanged) would fail this.
    """
    tool = _section(CapsuleSection.TOOL_CONTEXT, 80_000)
    task = _section(CapsuleSection.TASK, 400)
    cap = _capsule([task, tool], max_tokens=2000)
    # Sanity: the un-truncated capsule is genuinely over-limit (the precondition is real).
    assert cap.estimated_tokens > cap.max_tokens
    out = truncate_capsule(cap)
    assert out.estimated_tokens <= out.max_tokens
    assert out.truncation_applied is True
