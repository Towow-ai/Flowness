"""Unit tests for M-0.5 gate completeness requirement (RUN-027 block 3 — F-026-1).

# spec source:
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md §1.2 / §971
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §64
"""

from __future__ import annotations

from towow.l0.commit_gate.completeness import (
    REQUIRED_COMPLETENESS_CHECKS,
    check_completeness_requirements,
)
from towow.schemas.enums import EventType

_PASS = {"check_id": "consensus.consumer_scan_complete", "status": "passed"}
_FAIL = {"check_id": "consensus.consumer_scan_complete", "status": "failed"}
# M-2.1 §4.1 caller-1 — the cia_downstream_scan check is now ALSO required on a consensus freeze.
_CIA_PASS = {"check_id": "consensus.cia_downstream_scan", "status": "passed"}
# design-maintainability-gate@v1 — maintainability clause presence is now ALSO required on a freeze.
_MAINT_PASS = {"check_id": "consensus.maintainability_clause_present", "status": "passed"}


def test_consensus_freeze_is_registered() -> None:
    required = REQUIRED_COMPLETENESS_CHECKS[EventType.ENGINEERING_CONSENSUS_FREEZED]
    assert "consensus.consumer_scan_complete" in required
    # M-2.1 §4.1 caller-1: the CIA downstream scan is a required completeness check on freeze.
    assert "consensus.cia_downstream_scan" in required


def test_no_required_checks_for_unregistered_event_passes() -> None:
    # A ConceptCreated has no required completeness checks → empty self_check is fine.
    res = check_completeness_requirements([EventType.CONCEPT_CREATED], {"blocking_checks": []})
    assert res.passed


def test_freeze_with_passed_required_check_passes() -> None:
    # All required freeze checks present + passed → gate-side completeness satisfied.
    res = check_completeness_requirements(
        [EventType.ENGINEERING_CONSENSUS_FREEZED],
        {"blocking_checks": [_PASS, _CIA_PASS, _MAINT_PASS]},
    )
    assert res.passed


def test_freeze_missing_cia_downstream_scan_rejected() -> None:
    # M-2.1 §4.1 caller-1 enforcement: consumer_scan present but cia_downstream_scan ABSENT →
    # rejected. This is what makes the CIA scan un-bypassable at the M-0.5 总闸 — a freeze that
    # skips the forward_slice scan never lands.
    res = check_completeness_requirements(
        [EventType.ENGINEERING_CONSENSUS_FREEZED],
        {"blocking_checks": [_PASS]},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_missing"
    assert res.failure_evidence == {"check_id": "consensus.cia_downstream_scan"}


def test_freeze_missing_required_check_rejected() -> None:
    # The core F-026-1 fix: zero blocking_checks ≠ pass for a stage-freeze. With two required
    # checks, the first missing one (sorted) is cia_downstream_scan.
    res = check_completeness_requirements(
        [EventType.ENGINEERING_CONSENSUS_FREEZED],
        {"blocking_checks": []},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_missing"
    assert res.failure_evidence == {"check_id": "consensus.cia_downstream_scan"}


def test_freeze_failed_required_check_rejected() -> None:
    # cia_downstream_scan present+passed (so it isn't the missing one), consumer_scan present but
    # failed → present-but-failed path rejects.
    res = check_completeness_requirements(
        [EventType.ENGINEERING_CONSENSUS_FREEZED],
        {"blocking_checks": [_FAIL, _CIA_PASS]},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_failed"
    assert res.failure_evidence == {"check_id": "consensus.consumer_scan_complete", "status": "failed"}


def test_malformed_self_check_treated_as_missing() -> None:
    res = check_completeness_requirements([EventType.ENGINEERING_CONSENSUS_FREEZED], None)
    assert not res.passed
    assert res.failure_reason == "completeness_check_missing"


# ── M-2.1 §4.1 caller-3 — review.cia_upstream_scan required on ReviewPlanCreated ──

_UPSTREAM_PASS = {"check_id": "review.cia_upstream_scan", "status": "passed"}


def test_review_plan_created_is_registered() -> None:
    # M-2.1 §4.1 caller-3: the CIA upstream (backward) scan is a required completeness check on
    # ReviewPlanCreated — review_plan 链的第一个 require。
    required = REQUIRED_COMPLETENESS_CHECKS[EventType.REVIEW_PLAN_CREATED]
    assert "review.cia_upstream_scan" in required


def test_review_plan_created_with_upstream_scan_passes() -> None:
    res = check_completeness_requirements(
        [EventType.REVIEW_PLAN_CREATED],
        {"blocking_checks": [_UPSTREAM_PASS]},
    )
    assert res.passed


def test_review_plan_created_missing_upstream_scan_rejected() -> None:
    # caller-3 enforcement: a ReviewPlanCreated reaching the gate WITHOUT review.cia_upstream_scan
    # (e.g. a direct emit bypassing the CLI) is rejected — un-bypassable at the M-0.5 总闸.
    res = check_completeness_requirements(
        [EventType.REVIEW_PLAN_CREATED],
        {"blocking_checks": []},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_missing"
    assert res.failure_evidence == {"check_id": "review.cia_upstream_scan"}


def test_review_plan_created_failed_upstream_scan_rejected() -> None:
    res = check_completeness_requirements(
        [EventType.REVIEW_PLAN_CREATED],
        {"blocking_checks": [{"check_id": "review.cia_upstream_scan", "status": "failed"}]},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_failed"


# ── M-2.1 §4.1 caller-4 — fix.cia_cascade_scan required on FixCompleted ──

_CASCADE_PASS = {"check_id": "fix.cia_cascade_scan", "status": "passed"}


def test_fix_completed_is_registered() -> None:
    # M-2.1 §4.1 caller-4: the CIA cascade (forward) scan is a required completeness check on
    # FixCompleted — required regardless of outcome (resolved / partially_resolved / needs_further).
    required = REQUIRED_COMPLETENESS_CHECKS[EventType.FIX_COMPLETED]
    assert "fix.cia_cascade_scan" in required


def test_fix_completed_with_cascade_scan_passes() -> None:
    res = check_completeness_requirements(
        [EventType.FIX_COMPLETED],
        {"blocking_checks": [_CASCADE_PASS]},
    )
    assert res.passed


def test_fix_completed_missing_cascade_scan_rejected() -> None:
    # caller-4 enforcement: a FixCompleted reaching the gate WITHOUT fix.cia_cascade_scan (e.g. a
    # direct emit bypassing the CLI) is rejected — un-bypassable at the M-0.5 总闸.
    res = check_completeness_requirements(
        [EventType.FIX_COMPLETED],
        {"blocking_checks": []},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_missing"
    assert res.failure_evidence == {"check_id": "fix.cia_cascade_scan"}


def test_fix_completed_failed_cascade_scan_rejected() -> None:
    res = check_completeness_requirements(
        [EventType.FIX_COMPLETED],
        {"blocking_checks": [{"check_id": "fix.cia_cascade_scan", "status": "failed"}]},
    )
    assert not res.passed
    assert res.failure_reason == "completeness_check_failed"
