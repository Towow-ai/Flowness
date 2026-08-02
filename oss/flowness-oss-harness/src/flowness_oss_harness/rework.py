"""Immutable blocker and rework bindings for the private OSS staging harness.

The release evaluator deliberately decides only whether a jury result blocks a
candidate.  This module records the *next* causal unit without pretending that
the blocker has been cleared: a Blocker Case binds the failed check to the exact
candidate/snapshot/report that produced it, and a Rework Manifest binds a new
candidate/snapshot to that same blocker plus every affected downstream target.

Neither object authorizes a run, a retest, a release, or a publication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .candidate import DEFAULT_CANDIDATE_SCHEMA
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


BLOCKER_CASE_SCHEMA = SCHEMAS_ROOT / "blocker-case.schema.json"
REWORK_MANIFEST_SCHEMA = SCHEMAS_ROOT / "rework-manifest.schema.json"
JURY_REPORT_SCHEMA = SCHEMAS_ROOT / "jury-report.schema.json"
SUCCESSOR_RETEST_ATTESTATION_SCHEMA = SCHEMAS_ROOT / "successor-retest-attestation.schema.json"


def _file_hash(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return payload


def _artifact(path: Path, artifact_id: str, label: str) -> dict[str, str]:
    return {
        "artifact_id": artifact_id,
        "sha256": _file_hash(path, label),
    }


def _load_candidate(path: Path, label: str) -> dict[str, Any]:
    candidate = load_validated_json(path, DEFAULT_CANDIDATE_SCHEMA, label)
    expected_id = "candidate-" + canonical_hash(
        {key: value for key, value in candidate.items() if key != "candidate_id"}
    ).removeprefix("sha256:")[:24]
    if candidate.get("candidate_id") != expected_id:
        raise ValidationError(f"{label} candidate_id does not bind its contents")
    return candidate


def _load_snapshot(path: Path, expected_snapshot_id: str, label: str) -> dict[str, Any]:
    snapshot = _json(path, label)
    if snapshot.get("schema_version") != "evidence-snapshot/v1":
        raise ValidationError(f"{label} has unsupported schema_version")
    if snapshot.get("snapshot_id") != expected_snapshot_id:
        raise ValidationError(f"{label} does not match candidate snapshot_id")
    return snapshot


def _blocking_check(
    report: dict[str, Any],
    *,
    check_id: str,
    blocker_id: str,
) -> dict[str, Any]:
    matches = [item for item in report.get("checks", []) if item.get("check_id") == check_id]
    if len(matches) != 1:
        raise ValidationError("report must contain exactly one named check_id")
    check = matches[0]
    if check.get("blocker_id") != blocker_id:
        raise ValidationError("check blocker_id does not match blocker case")
    verdict = check.get("verdict")
    if verdict == "fail":
        reproduction = check.get("reproduction")
        if not isinstance(reproduction, dict):
            raise ValidationError("failed check lacks a reproduction record")
    elif verdict == "unknown" and check.get("critical") is True:
        if not check.get("missing_evidence"):
            raise ValidationError("critical unknown lacks missing evidence")
    else:
        raise ValidationError("blocker case requires a failed or critical unknown check")
    return check


def _case_retest_contract(check: dict[str, Any]) -> dict[str, Any]:
    """Preserve the different repair obligations of failure and unknown.

    A failure has a reproducible observation.  A critical unknown instead has
    an acquisition obligation; inventing a reproduction record for it would
    turn an evidence gap into a false runtime claim.
    """

    if check["verdict"] == "fail":
        return {
            "reproduction": {
                "command_template": check["reproduction"]["command_template"],
                "environment_hash": check["reproduction"]["environment_hash"],
                "attempts": check["reproduction"]["attempts"],
                "expected": check["expected"],
                "observed": check["observed"],
                "retest_condition": check["retest_condition"],
            }
        }
    return {
        "evidence_acquisition": {
            "missing_evidence": check["missing_evidence"],
            "expected": check["expected"],
            "observed": check["observed"],
            "remediation": check["remediation"],
            "retest_condition": check["retest_condition"],
        }
    }


def _validate_ripple_targets(targets: list[dict[str, Any]]) -> None:
    identities = [(item.get("target_type"), item.get("target_id")) for item in targets]
    if len(identities) != len(set(identities)):
        raise ValidationError("ripple targets must be unique by type and id")


def bind_rework_terminal_attempt(case: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Return only a completed, case-bound terminal receipt for a manifest."""

    admission = receipt.get("admission", {})
    terminal = receipt.get("terminal_event", {})
    binding = admission.get("card", {}).get("rework_binding", {})
    expected = case["blocker_case_id"]
    if (
        receipt.get("state") != "completed"
        or binding.get("blocker_case_id") != expected
        or binding.get("blocker_id") != case["blocker_id"]
        or binding.get("case_hash") != case["case_hash"]
        or terminal.get("event_type") != "terminal"
        or not isinstance(terminal.get("payload", {}).get("execution_record"), dict)
        or terminal.get("payload", {}).get("retest_required") is not True
    ):
        raise ValidationError("REWORK-TERMINAL-BINDING-INVALID")
    return {
        "admission_id": admission["admission_id"],
        "admission_entry_hash": admission["entry_hash"],
        "terminal_event_hash": terminal["event_hash"],
        "execution_record": terminal["payload"]["execution_record"],
    }


def create_blocker_case(
    *,
    candidate_path: Path,
    snapshot_path: Path,
    report_path: Path,
    blocker_id: str,
    check_id: str,
    ripple_targets: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    """Create an immutable, blocking-check-bound Blocker Case.

    ``report_path`` is schema-validated and self-hash checked before the failed
    check is copied.  The reproduction in the result is derived from that check,
    never accepted from the caller as a free-form replacement.
    """

    if output.exists() or output.is_symlink():
        raise ValidationError("blocker case output already exists")
    candidate = _load_candidate(candidate_path, "original release candidate")
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    _load_snapshot(snapshot_path, snapshot_id, "original evidence snapshot")
    report = load_validated_json(report_path, JURY_REPORT_SCHEMA, "jury report")
    verify_self_hash(report, "signature")
    if report.get("candidate_id") != candidate["candidate_id"]:
        raise ValidationError("jury report candidate_id does not match original candidate")
    if report.get("snapshot_id") != snapshot_id:
        raise ValidationError("jury report snapshot_id does not match original snapshot")
    check = _blocking_check(report, check_id=check_id, blocker_id=blocker_id)
    _validate_ripple_targets(ripple_targets)

    unsigned = {
        "schema_version": "blocker-case/v2",
        "blocker_case_id": "case-" + canonical_hash(
            {
                "blocker_id": blocker_id,
                "candidate_id": candidate["candidate_id"],
                "snapshot_id": snapshot_id,
                "report_id": report["report_id"],
                "check_id": check_id,
            }
        ).removeprefix("sha256:")[:24],
        "blocker_id": blocker_id,
        "blocking_verdict": check["verdict"],
        "original": {
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": snapshot_id,
            "report_id": report["report_id"],
            "check_id": check_id,
            "candidate": _artifact(candidate_path, f"candidate:{candidate['candidate_id']}", "original release candidate"),
            "snapshot": _artifact(snapshot_path, f"snapshot:{snapshot_id}", "original evidence snapshot"),
            "report": _artifact(report_path, f"jury-report:{report['report_id']}", "jury report"),
            "check_hash": canonical_hash(check),
        },
        **_case_retest_contract(check),
        "ripple_targets": ripple_targets,
    }
    payload = {**unsigned, "case_hash": canonical_hash(unsigned)}
    validate_payload(payload, BLOCKER_CASE_SCHEMA, "blocker case")
    atomic_create_json(output, payload)
    return payload


def load_blocker_case(path: Path) -> dict[str, Any]:
    payload = load_validated_json(path, BLOCKER_CASE_SCHEMA, "blocker case")
    verify_self_hash(payload, "case_hash")
    _validate_ripple_targets(payload["ripple_targets"])
    return payload


def load_rework_manifest(path: Path) -> dict[str, Any]:
    payload = load_validated_json(path, REWORK_MANIFEST_SCHEMA, "rework manifest")
    verify_self_hash(payload, "manifest_hash")
    return payload


def _report_check(report: dict[str, Any], check_id: str) -> dict[str, Any]:
    checks = [check for check in report.get("checks", []) if check.get("check_id") == check_id]
    if len(checks) != 1:
        raise ValidationError("SUCCESSOR-RETEST-CHECK-MISMATCH")
    return checks[0]


def create_successor_retest_attestation(
    *,
    blocker_case_path: Path,
    rework_manifest_path: Path,
    original_report_path: Path,
    successor_candidate_path: Path,
    successor_snapshot_path: Path,
    successor_report_paths: list[Path],
    missing_evidence_resolution: list[dict[str, Any]] | None = None,
    output: Path,
) -> dict[str, Any]:
    """Seal the narrowly targeted A→B retest evidence without authorizing release.

    This intentionally requires B's reports to be fresh ``first_pass`` reports.
    The attestation proves the old blocker was specifically re-examined; it
    does not replace B's complete policy jury or an owner approval.  A future
    Jury Bundle can consume this narrow record, but this object itself cannot
    clear Candidate A or authorize Candidate B.
    """

    if output.exists() or output.is_symlink():
        raise ValidationError("successor retest attestation output already exists")
    case = load_blocker_case(blocker_case_path)
    manifest = load_rework_manifest(rework_manifest_path)
    original = load_validated_json(original_report_path, JURY_REPORT_SCHEMA, "original jury report")
    verify_self_hash(original, "signature")
    if (
        _file_hash(original_report_path, "original jury report") != case["original"]["report"]["sha256"]
        or original.get("report_id") != case["original"]["report_id"]
        or manifest["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or manifest["blocker_case"]["case_hash"] != case["case_hash"]
        or manifest["blocker_case"]["artifact"]["sha256"] != _file_hash(blocker_case_path, "blocker case")
    ):
        raise ValidationError("SUCCESSOR-RETEST-CASE-MANIFEST-MISMATCH")
    original_check = _report_check(original, case["original"]["check_id"])
    if canonical_hash(original_check) != case["original"]["check_hash"]:
        raise ValidationError("SUCCESSOR-RETEST-ORIGINAL-CHECK-MISMATCH")

    successor = _load_candidate(successor_candidate_path, "successor release candidate")
    snapshot_id = successor["snapshot"]["snapshot_id"]
    _load_snapshot(successor_snapshot_path, snapshot_id, "successor evidence snapshot")
    if (
        successor["candidate_id"] != manifest["successor"]["candidate_id"]
        or snapshot_id != manifest["successor"]["snapshot_id"]
        or manifest["successor"]["candidate"]["sha256"] != _file_hash(successor_candidate_path, "successor release candidate")
        or manifest["successor"]["snapshot"]["sha256"] != _file_hash(successor_snapshot_path, "successor evidence snapshot")
    ):
        raise ValidationError("SUCCESSOR-RETEST-SUCCESSOR-MISMATCH")

    expected = {
        "check_id": case["original"]["check_id"],
        "gate_id": original_check["gate_id"],
        "dimension": original_check["dimension"],
        "critical": original_check["critical"],
    }
    original_agent = original["judge"]["agent_instance_id"]
    seen_roles: set[str] = set()
    seen_agents: set[str] = set()
    report_refs: list[dict[str, str]] = []
    for report_path in successor_report_paths:
        report = load_validated_json(report_path, JURY_REPORT_SCHEMA, "successor jury report")
        verify_self_hash(report, "signature")
        check = _report_check(report, expected["check_id"])
        judge = report.get("judge", {})
        if (
            report.get("phase") != "first_pass"
            or report.get("candidate_id") != successor["candidate_id"]
            or report.get("snapshot_id") != snapshot_id
            or judge.get("organization") != "independent"
            or judge.get("conflicts") != []
            or judge.get("agent_instance_id") == original_agent
            or judge.get("role_id") in seen_roles
            or judge.get("agent_instance_id") in seen_agents
            or check.get("verdict") != "pass"
            or check.get("blocker_id") is not None
            or any(check.get(key) != value for key, value in expected.items())
        ):
            raise ValidationError("SUCCESSOR-RETEST-REPORT-INVALID")
        seen_roles.add(judge["role_id"])
        seen_agents.add(judge["agent_instance_id"])
        report_refs.append({
            "report_id": report["report_id"],
            "sha256": _file_hash(report_path, "successor jury report"),
            "role_id": judge["role_id"],
            "agent_instance_id": judge["agent_instance_id"],
        })
    if len(report_refs) < 2:
        raise ValidationError("SUCCESSOR-RETEST-INSUFFICIENT-INDEPENDENT-JUDGES")

    resolution = missing_evidence_resolution or []
    if case.get("blocking_verdict", "fail") == "unknown":
        missing = set(case["evidence_acquisition"]["missing_evidence"])
        resolved = {item.get("missing_evidence") for item in resolution}
        candidate_evidence = {item.get("evidence_id") for item in successor.get("evidence", [])}
        if (
            resolved != missing
            or any(
                not isinstance(item.get("evidence_ids"), list)
                or not item["evidence_ids"]
                or not set(item["evidence_ids"]).issubset(candidate_evidence)
                for item in resolution
            )
        ):
            raise ValidationError("SUCCESSOR-RETEST-MISSING-EVIDENCE-UNRESOLVED")
    elif resolution:
        raise ValidationError("SUCCESSOR-RETEST-UNEXPECTED-EVIDENCE-RESOLUTION")

    unsigned = {
        "schema_version": "successor-retest-attestation/v1",
        "attestation_id": "successor-retest-" + canonical_hash({
            "case_hash": case["case_hash"],
            "manifest_hash": manifest["manifest_hash"],
            "successor_candidate_id": successor["candidate_id"],
            "reports": report_refs,
        }).removeprefix("sha256:")[:24],
        "blocker_case": {
            "blocker_case_id": case["blocker_case_id"], "case_hash": case["case_hash"],
            "artifact": _artifact(blocker_case_path, f"blocker-case:{case['blocker_case_id']}", "blocker case"),
        },
        "rework_manifest": {
            "rework_manifest_id": manifest["rework_manifest_id"], "manifest_hash": manifest["manifest_hash"],
            "artifact": _artifact(rework_manifest_path, f"rework-manifest:{manifest['rework_manifest_id']}", "rework manifest"),
        },
        "original": {
            "candidate_id": case["original"]["candidate_id"], "snapshot_id": case["original"]["snapshot_id"],
            "report_id": original["report_id"], "check_id": expected["check_id"], "check_hash": case["original"]["check_hash"],
        },
        "successor": {
            "candidate_id": successor["candidate_id"], "snapshot_id": snapshot_id,
            "candidate": _artifact(successor_candidate_path, f"candidate:{successor['candidate_id']}", "successor release candidate"),
            "snapshot": _artifact(successor_snapshot_path, f"snapshot:{snapshot_id}", "successor evidence snapshot"),
        },
        "targeted_check": {**expected, "blocker_id": case["blocker_id"]},
        "successor_reports": report_refs,
        "missing_evidence_resolution": resolution,
        "attestation_hash": "",
    }
    unsigned.pop("attestation_hash")
    payload = {**unsigned, "attestation_hash": canonical_hash(unsigned)}
    validate_payload(payload, SUCCESSOR_RETEST_ATTESTATION_SCHEMA, "successor retest attestation")
    atomic_create_json(output, payload)
    return payload


def create_rework_manifest(
    *,
    blocker_case_path: Path,
    successor_candidate_path: Path,
    successor_snapshot_path: Path,
    rework_evidence: list[dict[str, str]],
    ripple_invalidations: list[dict[str, Any]],
    rework_receipt: dict[str, Any] | None = None,
    output: Path,
) -> dict[str, Any]:
    """Bind a successor candidate to one immutable blocker without clearing it."""

    if output.exists() or output.is_symlink():
        raise ValidationError("rework manifest output already exists")
    case = load_blocker_case(blocker_case_path)
    rework_attempt = (
        bind_rework_terminal_attempt(case, rework_receipt)
        if rework_receipt is not None
        else None
    )
    successor = _load_candidate(successor_candidate_path, "successor release candidate")
    successor_snapshot_id = successor["snapshot"]["snapshot_id"]
    _load_snapshot(successor_snapshot_path, successor_snapshot_id, "successor evidence snapshot")
    if successor_snapshot_id == case["original"]["snapshot_id"]:
        raise ValidationError("successor must bind a new snapshot")

    target_keys = {
        (target["target_type"], target["target_id"])
        for target in case["ripple_targets"]
    }
    invalidation_keys = {
        (item.get("target_type"), item.get("target_id"))
        for item in ripple_invalidations
    }
    if invalidation_keys != target_keys:
        raise ValidationError(
            "ripple invalidations must cover exactly the blocker case ripple targets"
        )
    if not rework_evidence:
        raise ValidationError("rework manifest requires at least one rework evidence artifact")

    unsigned = {
        "schema_version": "rework-manifest/v2",
        "rework_manifest_id": "rework-" + canonical_hash(
            {
                "blocker_case_id": case["blocker_case_id"],
                "successor_candidate_id": successor["candidate_id"],
                "successor_snapshot_id": successor_snapshot_id,
            }
        ).removeprefix("sha256:")[:24],
        "blocker_case": {
            "blocker_case_id": case["blocker_case_id"],
            "blocker_id": case["blocker_id"],
            "case_hash": case["case_hash"],
            "artifact": _artifact(blocker_case_path, f"blocker-case:{case['blocker_case_id']}", "blocker case"),
        },
        "successor": {
            "candidate_id": successor["candidate_id"],
            "snapshot_id": successor_snapshot_id,
            "candidate": _artifact(successor_candidate_path, f"candidate:{successor['candidate_id']}", "successor release candidate"),
            "snapshot": _artifact(successor_snapshot_path, f"snapshot:{successor_snapshot_id}", "successor evidence snapshot"),
        },
        "rework_evidence": rework_evidence,
        "ripple_invalidations": ripple_invalidations,
        "retest_requirement": {
            "blocker_id": case["blocker_id"],
            "original_check_id": case["original"]["check_id"],
            "blocking_verdict": case.get("blocking_verdict", "fail"),
            **(
                {"reproduction": case["reproduction"]}
                if case.get("blocking_verdict", "fail") == "fail"
                else {"evidence_acquisition": case["evidence_acquisition"]}
            ),
            "fresh_independent_jury_required": True,
        },
    }
    if rework_attempt is not None:
        unsigned["rework_attempt"] = rework_attempt
    payload = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    validate_payload(payload, REWORK_MANIFEST_SCHEMA, "rework manifest")
    atomic_create_json(output, payload)
    return payload
