"""RUN-038 L0增强 M-0.3 §3.1: Resolver 调用合约 error_handling —
timeout / m06_error / m06_unreachable → conservative fallback; schema_invalid → abort。

spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
  §3.1 调用协议 (L252..L268) error_handling:
    timeout:          → conservative_fallback()
    m06_error:        → conservative_fallback()
    schema_invalid:   → pipeline_abort("resolver_schema_invalid")
    m06_unreachable:  → conservative_fallback()
  §3.2 Conservative Fallback (L270..L289): needs_resolver 候选一律 hit=true / confidence=0.0 /
    fallback_applied=true ("宁可多注入也不能让 agent 裸跑")。
  §3.3 验证 (L292..L301): verdict.judgments 必须不多不少覆盖 needs_resolver; 否则 schema_invalid abort。

现状: pipeline 此前直接调 conservative_fallback_verdict, 无真"调用→出错→分流"机器 (fallback 是 E.1.5
唯一 resolver, LLM resolver E.3 才落)。本 cap 把 §3.1 error_handling 合约做成真可测的 call_resolver
seam (resolver 可注入), fallback 仍是默认 resolver。

锁死: timeout/error/unreachable → fallback verdict (hit=true/conf=0.0/fallback_applied, 不抛);
schema_invalid (coverage 不符) → ResolverSchemaInvalid (抛, 不 fallback)。
负对照: 把 schema_invalid 也当 fallback 吞掉 → schema_invalid 测真挂。
"""

from __future__ import annotations

import pytest

from towow.l0.obligations.resolver import (
    ResolverError,
    ResolverSchemaInvalid,
    ResolverTimeout,
    ResolverUnreachable,
    call_resolver,
    conservative_fallback_verdict,
)
from towow.schemas.enums import (
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SceneType,
)
from towow.schemas.resolver import (
    ConceptGraphNeighborhood,
    ObligationCandidate,
    ObligationJudgment,
    ProjectionSlice,
    ResolverInput,
    ResolverVerdict,
    TaskScope,
    VerdictMeta,
)


def _resolver_input() -> ResolverInput:
    return ResolverInput(
        task_scope=TaskScope(
            task_id="t-err",
            task_type=SceneType.EXECUTION,
            reentry=False,
        ),
        candidates=[
            ObligationCandidate(
                obligation_id="obl-1",
                nature=ObligationNature.INVARIANT,
                severity=ObligationSeverity.NORMAL,
                scope_type=ObligationScopeType.SEMANTIC_DEPENDENT,
                scope_rule="applies to X",
                definition="fixture obligation",
            ),
        ],
        projection_slice=ProjectionSlice(
            concept_graph_neighborhood=ConceptGraphNeighborhood(nodes=[], edges=[]),
        ),
    )


def _good_verdict(resolver_input: ResolverInput) -> ResolverVerdict:
    """A schema-valid, fully-covering verdict (high confidence, no fallback)."""
    judgments = [
        ObligationJudgment(
            obligation_id=c.obligation_id,
            hit=True,
            confidence=0.95,
            reason="resolver judged in scope",
            fallback_applied=False,
        )
        for c in resolver_input.candidates
    ]
    meta = VerdictMeta(
        verdict_id="vid-good",
        resolver_runtime_ms=12,
        model="sonnet",
        contract_version="1.0.0",
        total_candidates=len(judgments),
        total_hits=sum(1 for j in judgments if j.hit),
        total_fallbacks=0,
    )
    return ResolverVerdict(judgments=judgments, meta=meta)


def _coverage_mismatch_verdict(resolver_input: ResolverInput) -> ResolverVerdict:
    """A constructible but coverage-INVALID verdict — judges a different obligation_id (§3.3 fail)."""
    judgments = [
        ObligationJudgment(
            obligation_id="obl-WRONG",  # not in candidates → coverage mismatch
            hit=True,
            confidence=0.95,
            reason="judged the wrong obligation",
            fallback_applied=False,
        ),
    ]
    meta = VerdictMeta(
        verdict_id="vid-bad",
        resolver_runtime_ms=12,
        model="sonnet",
        contract_version="1.0.0",
        total_candidates=1,
        total_hits=1,
        total_fallbacks=0,
    )
    return ResolverVerdict(judgments=judgments, meta=meta)


def _assert_is_conservative_fallback(verdict: ResolverVerdict) -> None:
    assert len(verdict.judgments) == 1
    j = verdict.judgments[0]
    assert j.obligation_id == "obl-1"
    assert j.hit is True
    assert j.confidence == 0.0  # §3.2 conservative include at confidence 0.0
    assert j.fallback_applied is True


def test_timeout_falls_back() -> None:
    """§3.1 timeout → conservative_fallback (不抛, 候选一律 hit=true/conf=0.0/fallback_applied)。"""
    def _raise_timeout(_: ResolverInput) -> ResolverVerdict:
        raise ResolverTimeout("exceeded 30s")

    verdict = call_resolver(_resolver_input(), resolver=_raise_timeout)
    _assert_is_conservative_fallback(verdict)


def test_m06_error_falls_back() -> None:
    """§3.1 m06_error → conservative_fallback。"""
    def _raise_error(_: ResolverInput) -> ResolverVerdict:
        raise ResolverError("m06 internal failure")

    verdict = call_resolver(_resolver_input(), resolver=_raise_error)
    _assert_is_conservative_fallback(verdict)


def test_m06_unreachable_falls_back() -> None:
    """§3.1 m06_unreachable → conservative_fallback。"""
    def _raise_unreachable(_: ResolverInput) -> ResolverVerdict:
        raise ResolverUnreachable("resolver service down")

    verdict = call_resolver(_resolver_input(), resolver=_raise_unreachable)
    _assert_is_conservative_fallback(verdict)


def test_schema_invalid_aborts_not_fallback() -> None:
    """§3.1 schema_invalid → ResolverSchemaInvalid 抛 (pipeline abort), **不** fallback。"""
    with pytest.raises(ResolverSchemaInvalid):
        call_resolver(_resolver_input(), resolver=_coverage_mismatch_verdict)


def test_schema_invalid_is_not_a_resolver_error() -> None:
    """schema_invalid 不是 ResolverError 子类 (否则会被 fallback 吞掉 → 该 abort 的不 abort)。"""
    assert not issubclass(ResolverSchemaInvalid, ResolverError)


def test_valid_verdict_passes_through() -> None:
    """resolver 返回合法且覆盖完整的 verdict → 原样返回 (非 fallback)。"""
    verdict = call_resolver(_resolver_input(), resolver=_good_verdict)
    assert verdict.judgments[0].confidence == 0.95
    assert verdict.judgments[0].fallback_applied is False


def test_default_resolver_is_conservative_fallback() -> None:
    """默认 resolver (E.1.5, LLM 未落) = conservative_fallback — 与直接调一致。"""
    ri = _resolver_input()
    via_call = call_resolver(ri)
    direct = conservative_fallback_verdict(ri)
    assert [j.model_dump(exclude={"verdict_id"}) for j in via_call.judgments] == [
        j.model_dump(exclude={"verdict_id"}) for j in direct.judgments
    ]
