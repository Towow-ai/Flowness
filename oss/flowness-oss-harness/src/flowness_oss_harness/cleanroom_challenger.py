from __future__ import annotations

"""Fail-closed, local-only preflight for a future clean-room challenger.

This is deliberately *not* an installer.  It reads four sealed candidate
artifacts, challenges the declared install plan, and writes a redacted local
receipt.  It never creates an environment, imports the candidate, inherits an
environment variable, resolves a dependency, or touches the network.  A pass
therefore says only that the supplied plan is shaped for a future isolated
challenge; it is not an independent clean-room result.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


INPUT_SCHEMA = SCHEMAS_ROOT / "cleanroom-challenger-preflight-input.schema.json"
PLAN_SCHEMA = SCHEMAS_ROOT / "cleanroom-challenger-plan.schema.json"
RECEIPT_SCHEMA = SCHEMAS_ROOT / "cleanroom-challenger-preflight-receipt.schema.json"

SLOTS = (
    "sealed_candidate_artifact",
    "sealed_candidate_artifact_manifest",
    "sealed_candidate_export_manifest",
    "challenge_plan",
)
CHECK_IDS = (
    "hash_bound_candidate_artifacts",
    "source_checkout_refusal",
    "editable_or_local_absolute_refusal",
    "private_environment_refusal",
    "network_dependency_refusal",
)
_OPAQUE_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NO_AUTHORITY_BOUNDARY = (
    "local private-staging preflight only; it does not create or inspect a clean-room "
    "environment, install or execute a candidate, establish evaluator independence, "
    "grant rights, authorize release, or publish anything"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _regular_file(path: Path, slot: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"CLEANROOM-CHALLENGER-{slot.upper()}-NOT-REGULAR-FILE")
    return path.resolve(strict=True)


def _load_input(payload: dict[str, Any]) -> dict[str, Any]:
    validate_payload(payload, INPUT_SCHEMA, "Clean-room challenger preflight input")
    if tuple(sorted(payload["input_slots"])) != tuple(sorted(SLOTS)):
        raise ValidationError("CLEANROOM-CHALLENGER-INPUT-SLOTS-MISMATCH")
    return payload


def _bound_inputs(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    """Bind regular local files without copying their locations into a receipt."""

    payload = _load_input(payload)
    binding: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for slot in SLOTS:
        declared = payload["input_slots"][slot]
        path = _regular_file(Path(declared["path"]), slot)
        observed = _sha256_file(path)
        if declared["sha256"] != observed:
            raise ValidationError(f"CLEANROOM-CHALLENGER-{slot.upper()}-HASH-MISMATCH")
        paths[slot] = path
        binding[slot] = {
            "file_name": path.name,
            "bytes": path.stat().st_size,
            "sha256": observed,
        }
    return binding, paths


def _plan_violations(plan: dict[str, Any], artifact_hash: str) -> set[str]:
    """Return policy violations without running the candidate or a package tool."""

    violations: set[str] = set()
    candidate = plan["candidate_install"]
    reference = candidate["reference"]
    if candidate["artifact_sha256"] != artifact_hash:
        violations.add("CLEANROOM-CHALLENGER-CANDIDATE-HASH-UNBOUND")
    if candidate["mode"] == "source_checkout" or candidate["reference_kind"] == "source_checkout":
        violations.add("CLEANROOM-CHALLENGER-SOURCE-CHECKOUT-REFUSED")
    if candidate["mode"] == "editable" or candidate["reference_kind"] == "editable_install":
        violations.add("CLEANROOM-CHALLENGER-EDITABLE-REFUSED")
    if (
        candidate["mode"] == "local_absolute_path"
        or candidate["reference_kind"] == "local_absolute_path"
        or reference.startswith(("/", "file://", "file:"))
    ):
        violations.add("CLEANROOM-CHALLENGER-LOCAL-ABSOLUTE-PATH-REFUSED")
    # A sealed candidate may only be named by an opaque local identifier.  A
    # URL, package index coordinate, VCS address, or any other structured
    # locator is not evidence that a later challenger can remain offline.
    if not _OPAQUE_ARTIFACT_ID.fullmatch(reference):
        violations.add("CLEANROOM-CHALLENGER-NONOPAQUE-REFERENCE-REFUSED")
    environment = plan["environment"]
    if environment["inherit_parent"] or environment["requested_variables"]:
        violations.add("CLEANROOM-CHALLENGER-PRIVATE-ENVIRONMENT-REFUSED")
    dependency_policy = plan["dependency_policy"]
    if dependency_policy["resolution"] != "offline_no_network" or dependency_policy["dependencies"]:
        violations.add("CLEANROOM-CHALLENGER-NETWORK-DEPENDENCY-REFUSED")
    return violations


def _check(check_id: str, violations: set[str], *codes: str) -> dict[str, Any]:
    matched = sorted(set(codes).intersection(violations))
    return {
        "check_id": check_id,
        "outcome": "blocked" if matched else "passed",
        "codes": matched or ["CLEANROOM-CHALLENGER-CHECK-PASSED"],
    }


def _receipt_id(unsigned: dict[str, Any]) -> str:
    return "cleanroom-challenger-" + canonical_hash(unsigned)[7:23]


def evaluate_cleanroom_challenger_preflight(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate sealed bytes and an install-plan declaration without installing.

    The caller may use local absolute paths *only to supply the four evidence
    files to this pure local checker*.  Those paths are never interpreted as a
    candidate-install route and are never emitted.  Candidate plans using a
    source checkout, editable install, local absolute/file URL, inherited or
    requested environment variable, or dependency resolution are blocked.
    """

    binding, paths = _bound_inputs(input_payload)
    try:
        plan = json.loads(paths["challenge_plan"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("CLEANROOM-CHALLENGER-PLAN-INVALID") from exc
    validate_payload(plan, PLAN_SCHEMA, "Clean-room challenger plan")

    violations = _plan_violations(plan, binding["sealed_candidate_artifact"]["sha256"])
    checks = [
        _check(
            "hash_bound_candidate_artifacts",
            violations,
            "CLEANROOM-CHALLENGER-CANDIDATE-HASH-UNBOUND",
        ),
        _check("source_checkout_refusal", violations, "CLEANROOM-CHALLENGER-SOURCE-CHECKOUT-REFUSED"),
        _check(
            "editable_or_local_absolute_refusal",
            violations,
            "CLEANROOM-CHALLENGER-EDITABLE-REFUSED",
            "CLEANROOM-CHALLENGER-LOCAL-ABSOLUTE-PATH-REFUSED",
        ),
        _check("private_environment_refusal", violations, "CLEANROOM-CHALLENGER-PRIVATE-ENVIRONMENT-REFUSED"),
        _check(
            "network_dependency_refusal",
            violations,
            "CLEANROOM-CHALLENGER-NETWORK-DEPENDENCY-REFUSED",
            "CLEANROOM-CHALLENGER-NONOPAQUE-REFERENCE-REFUSED",
        ),
    ]
    passed = not violations
    unsigned = {
        "schema_version": "cleanroom-challenger-preflight-receipt/v1",
        "receipt_id": "",
        "scope": "private_staging_cleanroom_challenger_preflight",
        "authorization": "not_authorized",
        "input_binding": binding,
        "challenge_plan": {
            "file_name": binding["challenge_plan"]["file_name"],
            "sha256": binding["challenge_plan"]["sha256"],
        },
        "checks": checks,
        "preflight_state": "local_preflight_passed" if passed else "local_preflight_blocked",
        "not_proven": [
            "an independently controlled clean-room environment",
            "an actual candidate installation, import, command execution, or result",
            "absence of ambient credentials or network reachability outside this declared plan",
            "evaluator identity or independence",
            "artifact rights, release readiness, release, or publication authorization",
        ],
        "boundary": NO_AUTHORITY_BOUNDARY,
    }
    unsigned["receipt_id"] = _receipt_id(unsigned)
    receipt = {**unsigned, "receipt_hash": canonical_hash(unsigned)}
    validate_payload(receipt, RECEIPT_SCHEMA, "Clean-room challenger preflight receipt")
    return receipt


def write_cleanroom_challenger_preflight(
    input_payload: dict[str, Any], output: Path,
) -> dict[str, Any]:
    """Write one immutable local preflight receipt and make no other mutation."""

    receipt = evaluate_cleanroom_challenger_preflight(input_payload)
    atomic_create_json(output, receipt)
    return receipt


def verify_cleanroom_challenger_preflight(receipt_path: Path) -> dict[str, Any]:
    """Validate receipt integrity; this cannot promote it to clean-room evidence."""

    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("CLEANROOM-CHALLENGER-RECEIPT-INVALID") from exc
    validate_payload(payload, RECEIPT_SCHEMA, "Clean-room challenger preflight receipt")
    verify_self_hash(payload, "receipt_hash")
    if (
        payload["scope"] != "private_staging_cleanroom_challenger_preflight"
        or payload["authorization"] != "not_authorized"
        or payload["boundary"] != NO_AUTHORITY_BOUNDARY
    ):
        raise ValidationError("CLEANROOM-CHALLENGER-RECEIPT-IDENTITY-INVALID")
    return payload
