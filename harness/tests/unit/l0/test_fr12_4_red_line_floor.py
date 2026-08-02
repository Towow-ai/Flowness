"""f-r12-4 (c): resolver 成功路径的 red_line/invariant 下限 (_red_line_floor_applies).

finding f-r12-4-bgfork-bash-surface-hardening 子点 (c): conservative_fallback 强制 include 全部
candidate, 但成功 verdict 路径纯按 hit —— 单条 (被注入/出错的) 低置信 hit=false 可静默丢一条
red_line obligation, 两路径对 red_line 不对称。修复给成功路径加对称下限:

  red_line 严重度 OR invariant 性质的 candidate, 若 resolver 给 hit=false 且置信 < 阈值
  (_LOW_CONFIDENCE_THRESHOLD) → 强制 include (force-include, 防低置信/垃圾 false 静默丢 red_line);
  高置信 (>= 阈值) 的 hit=false → 尊重 (resolver 确定时仍信它)。

下面四象限 + 边界把这个下限钉死。
"""

from __future__ import annotations

from towow.l0.capsule.pipeline import (
    _LOW_CONFIDENCE_THRESHOLD,
    _CandidateRecord,
    _red_line_floor_applies,
)
from towow.schemas.enums import (
    ObligationCanonicalState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
)
from towow.schemas.resolver import ObligationJudgment


def _cand(
    oid: str,
    *,
    severity: ObligationSeverity = ObligationSeverity.NORMAL,
    nature: ObligationNature = ObligationNature.CONTRACT,
) -> _CandidateRecord:
    return _CandidateRecord(
        obligation_id=oid, nature=nature, severity=severity,
        scope_type=ObligationScopeType.GLOBAL, scope_rule="r", definition="d",
        canonical_state=ObligationCanonicalState.ACTIVE,
        scope_hint=None, concept_anchors=frozenset(),
    )


def _judgment(oid: str, *, hit: bool, confidence: float) -> ObligationJudgment:
    # confidence < 0.7 时 schema 强制 uncertainty 非空。
    uncertainty = None if confidence >= 0.7 else "low confidence judgment"
    return ObligationJudgment(
        obligation_id=oid, hit=hit, confidence=confidence,
        reason="r", uncertainty=uncertainty, fallback_applied=False,
    )


# ─── 四象限 (severity/nature × confidence) ──────────────────────────────────


def test_red_line_low_confidence_false_is_floored() -> None:
    # (red_line, hit=false, conf=0.5) → 强制 include
    cand = _cand("o", severity=ObligationSeverity.RED_LINE)
    assert _red_line_floor_applies(cand, _judgment("o", hit=False, confidence=0.5)) is True


def test_red_line_high_confidence_false_is_respected() -> None:
    # (red_line, hit=false, conf=0.8) → 尊重 resolver 的高置信否决, 不 floor
    cand = _cand("o", severity=ObligationSeverity.RED_LINE)
    assert _red_line_floor_applies(cand, _judgment("o", hit=False, confidence=0.8)) is False


def test_normal_low_confidence_false_is_not_floored() -> None:
    # (normal 严重度 + contract 性质, hit=false, conf=0.5) → 不在下限范围, 不 floor
    cand = _cand("o", severity=ObligationSeverity.NORMAL, nature=ObligationNature.CONTRACT)
    assert _red_line_floor_applies(cand, _judgment("o", hit=False, confidence=0.5)) is False


def test_invariant_nature_low_confidence_false_is_floored() -> None:
    # (invariant 性质 + normal 严重度, hit=false, conf=0.5) → invariant 也进下限 → floor
    cand = _cand("o", severity=ObligationSeverity.NORMAL, nature=ObligationNature.INVARIANT)
    assert _red_line_floor_applies(cand, _judgment("o", hit=False, confidence=0.5)) is True


# ─── 边界 / 退化 ────────────────────────────────────────────────────────────


def test_threshold_boundary_is_respected_not_floored() -> None:
    # conf 恰等于阈值 (0.7) = 高置信侧 (floor 仅在 < 阈值触发) → 尊重 drop
    cand = _cand("o", severity=ObligationSeverity.RED_LINE)
    j = _judgment("o", hit=False, confidence=_LOW_CONFIDENCE_THRESHOLD)
    assert _red_line_floor_applies(cand, j) is False


def test_hit_true_is_never_floored() -> None:
    # hit=true 走正常 include 路径, 不算 floor (floor 只描述 hit=false 的强制 include)
    cand = _cand("o", severity=ObligationSeverity.RED_LINE)
    assert _red_line_floor_applies(cand, _judgment("o", hit=True, confidence=0.1)) is False


def test_missing_judgment_is_not_floored() -> None:
    # judgment 缺失 (None) → 不 floor (check_covers 保证 needs_resolver 都有 judgment; 防御性退化)
    cand = _cand("o", severity=ObligationSeverity.RED_LINE)
    assert _red_line_floor_applies(cand, None) is False
