"""RUN-038 L0增强 M-0.6 Cap4: scope_type 5种机械过滤分流 (M-0.6 §2.1 / M-0.3 §2.3).

已覆盖 advance — _mechanical_filter 已按 5 个 scope_type 真分流。本测试是反 vacuous 负对照:
逐 scope_type 断言它落到对的桶 (hit / miss / needs_resolver), 且对照不同 scope_type 落不同桶,
证明分流不是恒过 / 全塞一个桶。

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §2.1 scope_type 5 种:
  global              — 每次 fork 必 hit
  all_patches         — patch 类 task 才 hit (与 task_type 机械判)
  specific_artifact   — obligation.scope_hint.target_artifacts ∩ task.target_artifacts 非空 → hit, 空 → miss
                        (本模块的用例不传 target_artifacts → 交集必空 → miss; 真 artifact_match 命中见
                         test_run038_specific_artifact_match.py)
  concept_neighborhood— anchors∩neighborhood 非空 → needs_resolver, 空 → miss
  semantic_dependent  — 永远 needs_resolver (全语义判)
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


def _cand(scope_type: ObligationScopeType, anchors: frozenset[str] = frozenset()) -> _CandidateRecord:
    return _CandidateRecord(
        obligation_id=f"obl-{scope_type.value}",
        nature=ObligationNature.INVARIANT,
        severity=ObligationSeverity.RED_LINE,
        scope_type=scope_type,
        scope_rule="rule",
        definition="def",
        canonical_state=ObligationCanonicalState.ACTIVE,
        scope_hint=None,
        concept_anchors=anchors,
    )


def _ids(cands: list[_CandidateRecord]) -> set[str]:
    return {c.obligation_id for c in cands}


def test_global_always_hits() -> None:
    out = _mechanical_filter([_cand(ObligationScopeType.GLOBAL)], SceneType.REVIEW, Neighborhood(frozenset()))
    assert _ids(out.mechanical_hits) == {"obl-global"}
    assert not out.mechanical_misses
    assert not out.needs_resolver


def test_all_patches_hits_only_on_patch_scene() -> None:
    c = _cand(ObligationScopeType.ALL_PATCHES)
    on_patch = _mechanical_filter([c], SceneType.EXECUTION, Neighborhood(frozenset()))
    assert _ids(on_patch.mechanical_hits) == {"obl-all_patches"}
    # non-patch scene (review/interview) → miss, NOT hit (proves task_type gating is real)
    off_patch = _mechanical_filter([c], SceneType.REVIEW, Neighborhood(frozenset()))
    assert _ids(off_patch.mechanical_misses) == {"obl-all_patches"}
    assert not off_patch.mechanical_hits


def test_specific_artifact_misses_with_no_artifacts() -> None:
    # 不传 target_artifacts (默认空) + 候选无 scope_hint → 交集必空 → miss。真 artifact_match
    # 命中/分流见 test_run038_specific_artifact_match.py。
    out = _mechanical_filter(
        [_cand(ObligationScopeType.SPECIFIC_ARTIFACT)],
        SceneType.EXECUTION,
        Neighborhood(frozenset()),
    )
    assert _ids(out.mechanical_misses) == {"obl-specific_artifact"}


def test_concept_neighborhood_hit_routes_to_resolver() -> None:
    c = _cand(ObligationScopeType.CONCEPT_NEIGHBORHOOD, anchors=frozenset({"concept:auth"}))
    nb = Neighborhood(frozenset({"concept:auth", "concept:db"}))
    out = _mechanical_filter([c], SceneType.EXECUTION, nb)
    assert _ids(out.needs_resolver) == {"obl-concept_neighborhood"}
    # disjoint neighborhood → miss, NOT needs_resolver (proves anchors∩neighborhood is real)
    out2 = _mechanical_filter([c], SceneType.EXECUTION, Neighborhood(frozenset({"concept:unrelated"})))
    assert _ids(out2.mechanical_misses) == {"obl-concept_neighborhood"}
    assert not out2.needs_resolver


def test_semantic_dependent_always_resolver() -> None:
    out = _mechanical_filter(
        [_cand(ObligationScopeType.SEMANTIC_DEPENDENT)],
        SceneType.REVIEW,
        Neighborhood(frozenset()),
    )
    assert _ids(out.needs_resolver) == {"obl-semantic_dependent"}


def test_five_scope_types_land_in_distinct_buckets() -> None:
    """5 种同时进 → 各落对应桶, 不是恒过 / 全塞一桶 (反 vacuous 主断言)。"""
    cands = [
        _cand(ObligationScopeType.GLOBAL),
        _cand(ObligationScopeType.ALL_PATCHES),
        _cand(ObligationScopeType.SPECIFIC_ARTIFACT),
        _cand(ObligationScopeType.CONCEPT_NEIGHBORHOOD, anchors=frozenset({"concept:x"})),
        _cand(ObligationScopeType.SEMANTIC_DEPENDENT),
    ]
    nb = Neighborhood(frozenset({"concept:x"}))
    out = _mechanical_filter(cands, SceneType.EXECUTION, nb)
    assert _ids(out.mechanical_hits) == {"obl-global", "obl-all_patches"}
    assert _ids(out.mechanical_misses) == {"obl-specific_artifact"}
    assert _ids(out.needs_resolver) == {"obl-concept_neighborhood", "obl-semantic_dependent"}
