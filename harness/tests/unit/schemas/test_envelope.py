"""Unit tests for TransactionEnvelope canonical schema (Step 1.e)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from towow.schemas.enums import (
    EnvelopeClaimType,
    EnvelopeReadSetEntityType,
    EnvelopeUncertaintySeverity,
    EnvelopeUncertaintySuggestedAction,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    ObligationDeclaredStatus,
    PatchNoveltyType,
    PatchType,
    SupersedeNoveltyType,
)
from towow.schemas.envelope import (
    ActiveObligationDeclared,
    ClaimEntry,
    PatchEntry,
    ReadEntry,
    SelfCheck,
    SelfCheckResult,
    TransactionEnvelope,
    UncertaintyEntry,
    WriteEntry,
)


def _envelope_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "envelope_id": "env-abc123",
        "task_id": "t-1",
        "run_id": "r-1",
        "fork_id": "fork-1",
        "read_set": [
            ReadEntry(
                entity_type=EnvelopeReadSetEntityType.FILE,
                entity_id="src/auth.py",
                version="evt-1",
            ),
        ],
        "write_set": [
            WriteEntry(
                entity_type=EnvelopeWriteSetEntityType.FILE,
                entity_id="src/auth.py",
                change_type=EnvelopeWriteChangeType.MODIFIED,
            ),
        ],
        "self_check": SelfCheck(
            passed=True,
            checks_run=[SelfCheckResult(check_type="type_check", passed=True)],
        ),
        "capsule_compiled_event_id": "evt-cap-1",
        "as_of_projection_seq": 100,
        "schema_version": "1.0.0",
        "submitted_at": datetime(2026, 5, 21, 12, tzinfo=UTC),
    }
    defaults.update(overrides)
    return defaults


def test_envelope_minimal_valid() -> None:
    env = TransactionEnvelope.model_validate(_envelope_kwargs())
    assert env.envelope_id == "env-abc123"
    assert env.read_set[0].entity_id == "src/auth.py"


def test_envelope_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        TransactionEnvelope.model_validate(_envelope_kwargs(envelope_id="bad-id"))


@pytest.mark.invariant
def test_envelope_read_set_must_be_non_empty() -> None:
    """M-0.4 §2: every run reads at least the task package."""
    with pytest.raises(ValidationError, match="at least 1 item"):
        TransactionEnvelope.model_validate(_envelope_kwargs(read_set=[]))


@pytest.mark.invariant
def test_envelope_write_set_must_be_non_empty() -> None:
    """M-0.4 §2: no-op runs should be TaskRunCompleted, not envelope."""
    with pytest.raises(ValidationError, match="at least 1 item"):
        TransactionEnvelope.model_validate(_envelope_kwargs(write_set=[]))


def test_envelope_with_claims() -> None:
    env = TransactionEnvelope.model_validate(
        _envelope_kwargs(
            claims=[
                ClaimEntry(
                    entity_type=EnvelopeWriteSetEntityType.FILE,
                    entity_id="src/auth.py",
                    claim_type=EnvelopeClaimType.WRITE_CLAIM,
                ),
            ],
        ),
    )
    assert env.claims[0].claim_type is EnvelopeClaimType.WRITE_CLAIM


@pytest.mark.invariant
def test_active_obligation_violated_requires_justification() -> None:
    """M-0.4 §2: violated / not_applicable status require justification."""
    with pytest.raises(ValidationError, match="requires non-empty justification"):
        ActiveObligationDeclared(
            obligation_id="obl-1",
            status=ObligationDeclaredStatus.VIOLATED,
            justification=None,
        )


@pytest.mark.invariant
def test_active_obligation_not_applicable_requires_justification() -> None:
    with pytest.raises(ValidationError, match="requires non-empty justification"):
        ActiveObligationDeclared(
            obligation_id="obl-1",
            status=ObligationDeclaredStatus.NOT_APPLICABLE,
            justification="   ",  # whitespace-only counts as empty
        )


def test_active_obligation_maintained_no_justification_needed() -> None:
    aod = ActiveObligationDeclared(
        obligation_id="obl-1",
        status=ObligationDeclaredStatus.MAINTAINED,
    )
    assert aod.justification is None


@pytest.mark.invariant
def test_patch_supersede_requires_completeness() -> None:
    """E.1.1 Conflict 5: is_supersede=true requires superseded_entity_ref + novelty_type
    + supersede_bridge (no silent automatic mapping).
    """
    from towow.schemas.envelope import PatchSupersedeBridge

    # Missing superseded_entity_ref → fail
    with pytest.raises(ValidationError, match="requires non-None"):
        PatchEntry(
            patch_id="p-1",
            patch_type=PatchType.CONCEPT_EVENT,
            target="concept:auth",
            summary="x",
            is_supersede=True,
            superseded_entity_ref=None,
            novelty_type=PatchNoveltyType.SCOPE_CHANGE,
            supersede_bridge=PatchSupersedeBridge(
                supersede_novelty_type=SupersedeNoveltyType.SCOPE_CHANGE,
                mapping_reason="patch and event both pure scope change",
            ),
        )


@pytest.mark.invariant
def test_patch_supersede_requires_bridge_e11_conflict_5() -> None:
    """E.1.1 Conflict 5: is_supersede=true requires supersede_bridge."""
    with pytest.raises(ValidationError, match="supersede_bridge"):
        PatchEntry(
            patch_id="p-1",
            patch_type=PatchType.CONCEPT_EVENT,
            target="concept:x",
            summary="x",
            is_supersede=True,
            superseded_entity_ref="concept:x-v1",
            novelty_type=PatchNoveltyType.NEW_RESOLUTION,
            supersede_bridge=None,  # missing — invariant violated
        )


@pytest.mark.invariant
def test_patch_supersede_bridge_requires_mapping_reason() -> None:
    """E.1.1 Conflict 5: bridge mapping_reason is required (non-empty)."""
    from towow.schemas.envelope import PatchSupersedeBridge

    with pytest.raises(ValidationError):
        PatchSupersedeBridge(
            supersede_novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
            mapping_reason="",  # empty — violates min_length=1
        )


def test_patch_supersede_bridge_valid() -> None:
    """E.1.1 Conflict 5: valid bridge enables PatchNoveltyType↔SupersedeNoveltyType mapping."""
    from towow.schemas.envelope import PatchSupersedeBridge

    bridge = PatchSupersedeBridge(
        supersede_novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
        mapping_reason="new_resolution here addresses a prior incorrect verdict",
    )
    p = PatchEntry(
        patch_id="p-1",
        patch_type=PatchType.CONCEPT_EVENT,
        target="concept:x",
        summary="x",
        is_supersede=True,
        superseded_entity_ref="concept:x-v1",
        novelty_type=PatchNoveltyType.NEW_RESOLUTION,
        supersede_bridge=bridge,
    )
    assert p.supersede_bridge is bridge


def test_patch_non_supersede_optional_fields_allowed() -> None:
    """Non-supersede patches don't require superseded_entity_ref / novelty_type / bridge."""
    p = PatchEntry(
        patch_id="p-1",
        patch_type=PatchType.CODE_DIFF,
        target="src/auth.py",
        summary="add rate limit",
    )
    assert p.is_supersede is False
    assert p.novelty_type is None
    assert p.supersede_bridge is None


def test_patch_with_obligation_event_type() -> None:
    """Patch M-0.6-I: obligation_event 6th patch_type."""
    p = PatchEntry(
        patch_id="p-1",
        patch_type=PatchType.OBLIGATION_EVENT,
        target="obl-x",
        summary="capture new obligation",
    )
    assert p.patch_type is PatchType.OBLIGATION_EVENT


def test_envelope_with_uncertainties() -> None:
    env = TransactionEnvelope.model_validate(
        _envelope_kwargs(
            uncertainties=[
                UncertaintyEntry(
                    uncertainty_id="u-1",
                    description="rate limit threshold unclear",
                    severity=EnvelopeUncertaintySeverity.MEDIUM,
                    suggested_action=EnvelopeUncertaintySuggestedAction.REVIEW_RECOMMENDED,
                ),
            ],
        ),
    )
    assert env.uncertainties[0].severity is EnvelopeUncertaintySeverity.MEDIUM


def test_envelope_with_active_obligations() -> None:
    env = TransactionEnvelope.model_validate(
        _envelope_kwargs(
            active_obligations_declared=[
                ActiveObligationDeclared(
                    obligation_id="obl-1",
                    status=ObligationDeclaredStatus.MAINTAINED,
                ),
                ActiveObligationDeclared(
                    obligation_id="obl-2",
                    status=ObligationDeclaredStatus.VIOLATED,
                    justification="bypass for emergency hotfix",
                ),
            ],
        ),
    )
    assert len(env.active_obligations_declared) == 2


def test_envelope_json_round_trip() -> None:
    env = TransactionEnvelope.model_validate(_envelope_kwargs())
    raw = env.model_dump_json()
    rebuilt = TransactionEnvelope.model_validate_json(raw)
    assert rebuilt == env


@pytest.mark.invariant
def test_envelope_frozen() -> None:
    """Event sourcing invariant: TransactionEnvelope is immutable."""
    env = TransactionEnvelope.model_validate(_envelope_kwargs())
    with pytest.raises(ValidationError, match="frozen"):
        env.envelope_id = "env-mutated"
