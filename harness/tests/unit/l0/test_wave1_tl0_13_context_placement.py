"""波1 T-L0-13: Context Placement — low_confidence obligation → HIDDEN_REMINDER + stage_6 留痕.

源表 done_criteria: 生成 capsule 时 low_confidence obligation 落 hidden_reminder, placement 排在
decision event 前, stage_6_placement 留痕非空 (现状硬编码 [])。
"""

from __future__ import annotations

from towow.l0.capsule.pipeline import _LOW_CONFIDENCE_THRESHOLD, _CandidateRecord, _placement_map
from towow.schemas.enums import (
    CapsuleSection,
    ObligationCanonicalState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
)


def _cand(oid: str, *, severity: ObligationSeverity, nature: ObligationNature) -> _CandidateRecord:
    return _CandidateRecord(
        obligation_id=oid, nature=nature, severity=severity,
        scope_type=ObligationScopeType.GLOBAL, scope_rule="r", definition="d",
        canonical_state=ObligationCanonicalState.ACTIVE,
        scope_hint=None, concept_anchors=frozenset(),
    )


def test_low_confidence_goes_to_hidden_reminder() -> None:
    cand = _cand("o-low", severity=ObligationSeverity.NORMAL, nature=ObligationNature.INVARIANT)
    placements = _placement_map([cand], {"o-low": _LOW_CONFIDENCE_THRESHOLD - 0.1})
    assert placements["o-low"] is CapsuleSection.HIDDEN_REMINDER


def test_high_confidence_uses_nature_section_not_hidden() -> None:
    cand = _cand("o-tool", severity=ObligationSeverity.NORMAL, nature=ObligationNature.TOOL_SPECIFIC)
    # 高置信 (在 map 且 >= 阈值) → 正常 section, 非 hidden
    placements = _placement_map([cand], {"o-tool": 0.95})
    assert placements["o-tool"] is CapsuleSection.TOOL_CONTEXT


def test_mechanical_hit_not_in_confidence_map_is_not_low() -> None:
    # 机械命中不在 confidence_map (高置信) → 不落 hidden
    cand = _cand("o-mech", severity=ObligationSeverity.NORMAL, nature=ObligationNature.TASK_CONSTRAINT)
    placements = _placement_map([cand], {})
    assert placements["o-mech"] is CapsuleSection.TASK_BRIEFING


def test_red_line_takes_precedence_over_low_confidence() -> None:
    # red_line 永远显著, 不因低置信降级到 hidden
    cand = _cand("o-red", severity=ObligationSeverity.RED_LINE, nature=ObligationNature.INVARIANT)
    placements = _placement_map([cand], {"o-red": 0.1})
    assert placements["o-red"] is CapsuleSection.RED_LINE_OBLIGATIONS
