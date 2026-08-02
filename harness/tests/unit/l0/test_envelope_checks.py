"""M-0.5 §3.0 EnvelopeIntegrity + §3.6 ClaimsBoundary checks (debt-payoff block 1)."""

from __future__ import annotations

from typing import Any

from towow.l0.envelope.checks import check_claims_boundary, check_envelope_integrity


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "envelope_id": "env-1",
        "task_id": "t-1",
        "run_id": "r-1",
        "fork_id": "fork-1",
        "capsule_compiled_event_id": "evt-cap",
        "schema_version": "1.0.0",
        "read_set": [{"entity_type": "task", "entity_id": "t-1", "version": "v1"}],
        "write_set": [{"entity_type": "concept", "entity_id": "c-1", "change_type": "created"}],
        "claims": [],
        "active_obligations_declared": [],
        "self_check": {"passed": True, "checks_run": []},
    }
    base.update(overrides)
    return base


# ── EnvelopeIntegrity §3.0 ──────────────────────────────────────────────────


def test_integrity_passes_for_complete_envelope() -> None:
    assert check_envelope_integrity(_valid_payload()).passed


def test_integrity_rejects_empty_read_set() -> None:
    res = check_envelope_integrity(_valid_payload(read_set=[]))
    assert not res.passed
    assert res.failure_reason == "envelope_malformed"
    assert res.evidence["field"] == "read_set"


def test_integrity_rejects_empty_write_set() -> None:
    res = check_envelope_integrity(_valid_payload(write_set=[]))
    assert not res.passed
    assert res.evidence["field"] == "write_set"


def test_integrity_rejects_missing_task_id() -> None:
    payload = _valid_payload()
    del payload["task_id"]
    res = check_envelope_integrity(payload)
    assert not res.passed
    assert res.evidence["field"] == "task_id"


def test_integrity_rejects_unknown_schema_version() -> None:
    res = check_envelope_integrity(_valid_payload(schema_version="9.9.9"))
    assert not res.passed
    assert res.failure_reason == "schema_version_unsupported"


def test_integrity_rejects_illegal_entity_type() -> None:
    res = check_envelope_integrity(
        _valid_payload(write_set=[{"entity_type": "finding", "entity_id": "f-1", "change_type": "created"}])
    )
    assert not res.passed
    assert "write_set.entity_type" in res.evidence


def test_integrity_rejects_incomplete_supersede_patch() -> None:
    res = check_envelope_integrity(
        _valid_payload(
            patches=[
                {
                    "patch_id": "p1",
                    "patch_type": "concept_event",
                    "target": "c",
                    "summary": "s",
                    "is_supersede": True,
                }
            ]
        )
    )
    assert not res.passed
    assert res.failure_reason == "supersede_declaration_incomplete"


def test_integrity_rejects_unknown_patch_type() -> None:
    res = check_envelope_integrity(
        _valid_payload(patches=[{"patch_id": "p1", "patch_type": "bogus", "target": "c", "summary": "s"}])
    )
    assert not res.passed
    assert res.evidence["patch_type"] == "bogus"


# ── ClaimsBoundary §3.6 ─────────────────────────────────────────────────────


def test_claims_absent_skips_with_notice() -> None:
    res = check_claims_boundary(_valid_payload(claims=[]))
    assert res.passed
    assert res.claims_absent
    assert "claims_absent" in (res.notice or "")


def test_claims_in_bounds_passes() -> None:
    payload = _valid_payload(
        write_set=[{"entity_type": "concept", "entity_id": "c-1", "change_type": "created"}],
        read_set=[{"entity_type": "task", "entity_id": "t-1", "version": "v1"}],
        claims=[
            {"entity_type": "concept", "entity_id": "c-1", "claim_type": "write_claim"},
            {"entity_type": "task", "entity_id": "t-1", "claim_type": "read_claim"},
        ],
    )
    assert check_claims_boundary(payload).passed


def test_write_set_exceeds_claims_rejected() -> None:
    payload = _valid_payload(
        write_set=[
            {"entity_type": "concept", "entity_id": "c-1", "change_type": "created"},
            {"entity_type": "concept", "entity_id": "c-UNCLAIMED", "change_type": "created"},
        ],
        read_set=[{"entity_type": "task", "entity_id": "t-1", "version": "v1"}],
        claims=[
            {"entity_type": "concept", "entity_id": "c-1", "claim_type": "write_claim"},
            {"entity_type": "task", "entity_id": "t-1", "claim_type": "read_claim"},
        ],
    )
    res = check_claims_boundary(payload)
    assert not res.passed
    assert res.failure_reason == "write_set_exceeds_claims"
    assert ("concept", "c-UNCLAIMED") in res.exceeded


def test_read_set_exceeds_claims_rejected() -> None:
    payload = _valid_payload(
        write_set=[{"entity_type": "concept", "entity_id": "c-1", "change_type": "created"}],
        read_set=[{"entity_type": "file", "entity_id": "src/secret.py", "version": "v1"}],
        claims=[{"entity_type": "concept", "entity_id": "c-1", "claim_type": "write_claim"}],
    )
    res = check_claims_boundary(payload)
    assert not res.passed
    assert res.failure_reason == "read_set_exceeds_claims"
    assert ("file", "src/secret.py") in res.exceeded
