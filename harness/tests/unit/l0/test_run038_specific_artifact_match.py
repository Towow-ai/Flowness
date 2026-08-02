"""RUN-038 L0增强 M-0.3 §2.3 — specific_artifact scope_type 机械 artifact_match。

spec source:
  03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md §2.3 (L106):
    elif obligation.scope_type == "specific_artifact" and artifact_match: → mechanical_hit or miss
  03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §2.1 (L79/L89):
    specific_artifact — 跟 attached_to 字符串匹配; scope_hint.target_artifacts 填 specific_artifact 的目标

现状缺口: _mechanical_filter 把 specific_artifact 一律保守 miss (注释称依赖 at_reference_graph reducer,
但 §2.3/§2.1 的机械判定只需字符串匹配 obligation.scope_hint.target_artifacts ∩ task.target_artifacts)。
本次实化为真机械判定 — 任务声明的 target_artifacts 与 obligation 目标 artifact 字符串相交 → hit, 否则 miss。

锁死:
  (1) obligation.target_artifacts ∩ task.target_artifacts 非空 → mechanical_hit;
  (2) 不相交 → mechanical_miss (不是 needs_resolver — specific_artifact 是机械可判, 不送 resolver);
  (3) obligation 无 scope_hint / 无 target_artifacts → 无法机械命中 → miss (保守, 不误命中);
  (4) task 无 target_artifacts → 任何 specific_artifact 都 miss。

负对照: artifact_match 永远 True → (2)/(3)/(4) 该 miss 的变 hit 真挂; 永远 False → (1) 该 hit 的 miss 真挂。
"""

from __future__ import annotations

from towow.l0.capsule.neighborhood import Neighborhood
from towow.l0.capsule.pipeline import _CandidateRecord, _mechanical_filter
from towow.schemas.enums import (
    ObligationCanonicalState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SceneType,
)
from towow.schemas.obligation import ScopeHint


def _artifact_cand(obligation_id: str, target_artifacts: list[str] | None) -> _CandidateRecord:
    scope_hint = ScopeHint(target_artifacts=target_artifacts) if target_artifacts is not None else None
    return _CandidateRecord(
        obligation_id=obligation_id,
        nature=ObligationNature.INVARIANT,
        severity=ObligationSeverity.RED_LINE,
        scope_type=ObligationScopeType.SPECIFIC_ARTIFACT,
        scope_rule="applies when modifying src/auth",
        definition="def",
        canonical_state=ObligationCanonicalState.ACTIVE,
        scope_hint=scope_hint,
        concept_anchors=frozenset(),
    )


def _ids(cands: list[_CandidateRecord]) -> set[str]:
    return {c.obligation_id for c in cands}


def test_artifact_overlap_hits() -> None:
    c = _artifact_cand("obl-art-hit", ["src/auth/login.py", "src/auth/rate.py"])
    out = _mechanical_filter(
        [c], SceneType.EXECUTION, Neighborhood(frozenset()),
        target_artifacts=["src/auth/login.py"],
    )
    assert _ids(out.mechanical_hits) == {"obl-art-hit"}
    assert not out.mechanical_misses
    assert not out.needs_resolver  # specific_artifact 机械可判 — 不送 resolver


def test_artifact_disjoint_misses() -> None:
    c = _artifact_cand("obl-art-miss", ["src/auth/login.py"])
    out = _mechanical_filter(
        [c], SceneType.EXECUTION, Neighborhood(frozenset()),
        target_artifacts=["src/billing/invoice.py"],
    )
    assert _ids(out.mechanical_misses) == {"obl-art-miss"}
    assert not out.mechanical_hits


def test_no_scope_hint_conservative_miss() -> None:
    """obligation 没声明目标 artifact → 无法机械命中 → miss (不误命中)。"""
    c = _artifact_cand("obl-art-nohint", None)
    out = _mechanical_filter(
        [c], SceneType.EXECUTION, Neighborhood(frozenset()),
        target_artifacts=["src/auth/login.py"],
    )
    assert _ids(out.mechanical_misses) == {"obl-art-nohint"}
    assert not out.mechanical_hits


def test_empty_task_artifacts_misses() -> None:
    """task 没声明 target_artifacts → 任何 specific_artifact 都 miss (交集必空)。"""
    c = _artifact_cand("obl-art-emptytask", ["src/auth/login.py"])
    out = _mechanical_filter([c], SceneType.EXECUTION, Neighborhood(frozenset()), target_artifacts=[])
    assert _ids(out.mechanical_misses) == {"obl-art-emptytask"}
    assert not out.mechanical_hits


def test_hit_and_miss_distinct_in_one_pass() -> None:
    """同一次过滤里 hit 与 miss 分流正确 — 不是恒过/恒拒 (反 vacuous 主断言)。"""
    hit = _artifact_cand("obl-hit", ["src/auth/login.py"])
    miss = _artifact_cand("obl-miss", ["src/other/x.py"])
    out = _mechanical_filter(
        [hit, miss], SceneType.EXECUTION, Neighborhood(frozenset()),
        target_artifacts=["src/auth/login.py"],
    )
    assert _ids(out.mechanical_hits) == {"obl-hit"}
    assert _ids(out.mechanical_misses) == {"obl-miss"}
