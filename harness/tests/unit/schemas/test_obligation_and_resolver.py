"""Unit tests for canonical Obligation + ResolverInput/Verdict (Step 1.d)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    CheckerPatternType,
    ObligationAttachedToEntityType,
    ObligationCaptureSource,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SceneType,
    SubjectEntityType,
    VerdictEvidenceRefType,
)
from towow.schemas.obligation import (
    CaptureProvenance,
    CheckerMetadata,
    Obligation,
    ObligationAttachment,
    ScopeHint,
)
from towow.schemas.resolver import (
    ConceptGraphNeighborhood,
    EntityRefShort,
    ObligationCandidate,
    ObligationJudgment,
    ProjectionSlice,
    ReentryContext,
    ResolverInput,
    ResolverVerdict,
    TaskScope,
    VerdictEvidence,
    VerdictMeta,
)

# ─── Obligation canonical object ────────────────────────────────────────────────


def _build_obligation(**overrides: object) -> Obligation:
    defaults: dict[str, object] = {
        "obligation_id": "obl-intent-attribution-a3f2b1c4",
        "nature": ObligationNature.INTENT_ATTRIBUTION,
        "severity": ObligationSeverity.RED_LINE,
        "scope_type": ObligationScopeType.ALL_PATCHES,
        "scope_rule": "applies to all patches in any task",
        "definition": "every patch must trace to a Goal",
        "attached_to": [
            ObligationAttachment(entity_type=ObligationAttachedToEntityType.ARTIFACT, entity_id="*")
        ],
        "checker_metadata": CheckerMetadata(mechanically_checkable=False),
        "capture_provenance": CaptureProvenance(
            capture_source=ObligationCaptureSource.SYSTEM_BOOTSTRAP,
            source_event_refs=[],
            captured_at=datetime(2026, 5, 21, tzinfo=UTC),
            captured_by_actor_id="system",
        ),
    }
    defaults.update(overrides)
    return Obligation.model_validate(defaults)


def test_obligation_minimal_valid() -> None:
    o = _build_obligation()
    assert o.obligation_id.startswith("obl-")
    assert o.severity is ObligationSeverity.RED_LINE


def test_obligation_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        _build_obligation(obligation_id="invalid-id")


def test_obligation_attached_to_min_length() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        _build_obligation(attached_to=[])


def test_checker_metadata_mechanically_checkable_with_pattern() -> None:
    cm = CheckerMetadata(
        mechanically_checkable=True,
        forbidden_pattern=r"\bcheckpoint\b",
        pattern_type=CheckerPatternType.REGEX,
        checker_scope_hint="stopping_condition section",
    )
    assert cm.pattern_type is CheckerPatternType.REGEX


def test_capture_provenance_system_bootstrap() -> None:
    cp = CaptureProvenance(
        capture_source=ObligationCaptureSource.SYSTEM_BOOTSTRAP,
        source_event_refs=[],
        captured_at=datetime(2026, 5, 21, 12, tzinfo=UTC),
        captured_by_actor_id="system",
    )
    assert cp.capture_source is ObligationCaptureSource.SYSTEM_BOOTSTRAP


# ─── ResolverInput ──────────────────────────────────────────────────────────────


def _task_scope(reentry: bool = False, reentry_context: ReentryContext | None = None) -> TaskScope:
    return TaskScope(
        task_id="t-1",
        task_type=SceneType.EXECUTION,
        target_artifacts=["src/auth.py"],
        declared_write_set=[
            EntityRefShort(entity_type=SubjectEntityType.FILE, entity_id="src/auth.py"),
        ],
        touched_nodes=[
            EntityRefShort(entity_type=SubjectEntityType.CONCEPT, entity_id="c-auth"),
        ],
        reentry=reentry,
        reentry_context=reentry_context,
    )


def _candidate(obligation_id: str = "obl-x-abc12345") -> ObligationCandidate:
    return ObligationCandidate(
        obligation_id=obligation_id,
        nature=ObligationNature.CONTRACT,
        severity=ObligationSeverity.NORMAL,
        scope_type=ObligationScopeType.CONCEPT_NEIGHBORHOOD,
        scope_rule="applies if task touches auth",
        scope_hint=ScopeHint(concept_anchors=["c-auth"]),
        definition="preserve rate-limit contract",
        attached_to=[
            EntityRefShort(entity_type=SubjectEntityType.CONCEPT, entity_id="c-auth"),
        ],
    )


def _projection_slice() -> ProjectionSlice:
    return ProjectionSlice(
        concept_graph_neighborhood=ConceptGraphNeighborhood(
            nodes=[{"id": "c-auth"}],
            edges=[],
        ),
    )


def test_resolver_input_valid() -> None:
    inp = ResolverInput(
        task_scope=_task_scope(),
        candidates=[_candidate()],
        projection_slice=_projection_slice(),
    )
    assert inp.resolver_config.model == "sonnet"


def test_resolver_input_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ResolverInput(
            task_scope=_task_scope(),
            candidates=[],
            projection_slice=_projection_slice(),
        )


@pytest.mark.invariant
def test_task_scope_reentry_requires_context() -> None:
    """M-0.6 §6.2: reentry=true ↔ reentry_context required."""
    with pytest.raises(ValidationError, match="reentry=true requires reentry_context"):
        _task_scope(reentry=True, reentry_context=None)


@pytest.mark.invariant
def test_task_scope_no_reentry_forbids_context() -> None:
    """M-0.6 §6.2: reentry=false forbids reentry_context."""
    rc = ReentryContext(previous_run_id="r-prev", previous_verdict_event_id="evt-prev")
    with pytest.raises(ValidationError, match="reentry=false forbids reentry_context"):
        _task_scope(reentry=False, reentry_context=rc)


def test_task_scope_reentry_with_context() -> None:
    rc = ReentryContext(previous_run_id="r-prev", previous_verdict_event_id="evt-prev")
    ts = _task_scope(reentry=True, reentry_context=rc)
    assert ts.reentry is True


# ─── ResolverVerdict ────────────────────────────────────────────────────────────


def _judgment(
    obligation_id: str = "obl-x-abc12345",
    *,
    confidence: float = 0.9,
    hit: bool = True,
    fallback_applied: bool = False,
    uncertainty: str | None = None,
) -> ObligationJudgment:
    return ObligationJudgment(
        obligation_id=obligation_id,
        hit=hit,
        confidence=confidence,
        reason="touches auth concept neighborhood",
        evidence=[VerdictEvidence(ref_type=VerdictEvidenceRefType.CONCEPT_NODE, ref="c-auth")],
        uncertainty=uncertainty,
        fallback_applied=fallback_applied,
    )


def _meta(*, total: int = 1, hits: int = 1, fallbacks: int = 0) -> VerdictMeta:
    return VerdictMeta(
        verdict_id="vid-1",
        resolver_runtime_ms=420,
        model="sonnet",
        contract_version="1.0.0",
        total_candidates=total,
        total_hits=hits,
        total_fallbacks=fallbacks,
    )


def test_resolver_verdict_valid() -> None:
    ResolverVerdict(judgments=[_judgment()], meta=_meta())


@pytest.mark.invariant
def test_verdict_low_confidence_requires_uncertainty() -> None:
    """M-0.6 §6.3: confidence < 0.7 must include uncertainty explanation."""
    with pytest.raises(ValidationError, match="requires uncertainty"):
        _judgment(confidence=0.6, uncertainty=None)


def test_verdict_low_confidence_with_uncertainty_ok() -> None:
    _judgment(confidence=0.6, uncertainty="ambiguous scope phrasing")


@pytest.mark.invariant
def test_verdict_meta_total_candidates_consistency() -> None:
    """meta.total_candidates must equal len(judgments)."""
    with pytest.raises(ValidationError, match="total_candidates=2"):
        ResolverVerdict(judgments=[_judgment()], meta=_meta(total=2))


@pytest.mark.invariant
def test_verdict_meta_total_hits_consistency() -> None:
    with pytest.raises(ValidationError, match="total_hits"):
        ResolverVerdict(judgments=[_judgment(hit=False)], meta=_meta(hits=1))


@pytest.mark.invariant
def test_verdict_meta_total_fallbacks_consistency() -> None:
    with pytest.raises(ValidationError, match="total_fallbacks"):
        ResolverVerdict(judgments=[_judgment(fallback_applied=False)], meta=_meta(fallbacks=1))


# ─── check_covers cross-validation ──────────────────────────────────────────────


@pytest.mark.invariant
def test_check_covers_exact_match() -> None:
    """M-0.3 §3.3: judgment obligation_id set == candidate obligation_id set."""
    inp = ResolverInput(
        task_scope=_task_scope(),
        candidates=[_candidate("obl-a-abc12345"), _candidate("obl-b-def67890")],
        projection_slice=_projection_slice(),
    )
    verdict = ResolverVerdict(
        judgments=[
            _judgment("obl-a-abc12345"),
            _judgment("obl-b-def67890"),
        ],
        meta=_meta(total=2, hits=2),
    )
    verdict.check_covers(inp)  # no exception


def test_check_covers_missing() -> None:
    inp = ResolverInput(
        task_scope=_task_scope(),
        candidates=[_candidate("obl-a-abc12345"), _candidate("obl-b-def67890")],
        projection_slice=_projection_slice(),
    )
    verdict = ResolverVerdict(
        judgments=[_judgment("obl-a-abc12345")],
        meta=_meta(total=1, hits=1),
    )
    with pytest.raises(ValueError, match=r"missing=.*obl-b-def67890"):
        verdict.check_covers(inp)


def test_check_covers_extra() -> None:
    inp = ResolverInput(
        task_scope=_task_scope(),
        candidates=[_candidate("obl-a-abc12345")],
        projection_slice=_projection_slice(),
    )
    verdict = ResolverVerdict(
        judgments=[
            _judgment("obl-a-abc12345"),
            _judgment("obl-extra-xyz67890"),
        ],
        meta=_meta(total=2, hits=2),
    )
    with pytest.raises(ValueError, match=r"extra=.*obl-extra"):
        verdict.check_covers(inp)


def test_check_covers_duplicates() -> None:
    inp = ResolverInput(
        task_scope=_task_scope(),
        candidates=[_candidate("obl-a-abc12345")],
        projection_slice=_projection_slice(),
    )
    verdict = ResolverVerdict(
        judgments=[
            _judgment("obl-a-abc12345"),
            _judgment("obl-a-abc12345"),
        ],
        meta=_meta(total=2, hits=2),
    )
    with pytest.raises(ValueError, match="duplicate obligation_id"):
        verdict.check_covers(inp)
