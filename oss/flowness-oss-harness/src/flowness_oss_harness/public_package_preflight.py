from __future__ import annotations

"""Fail-closed, private-staging preflight for a public package.

This module deliberately does *not* scaffold or amend public-facing material.
It only reports whether named bytes and supplied authority evidence cover the
minimum Alpha package surface.  A passing result is still merely an owner-gate
input: it neither grants rights nor publishes anything.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .registry import ValidationError, atomic_create_json
from .integrity import verify_self_hash
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA_VERSION = "public-package-preflight-report/v1"
ARTIFACT_SCHEMA_VERSION = "public-package-artifacts/v1"
RIGHTS_SCHEMA_VERSION = "sealed-export-rights-evidence/v1"

# The grouping is intentional.  A document can explain a commitment, evidence
# can bind bytes, and only a supplied authority record can speak to permission.
ALPHA_REQUIREMENTS: tuple[dict[str, str], ...] = (
    {"requirement_id": "candidate-package-root", "group": "product", "label": "candidate package root"},
    {"requirement_id": "license-matrix", "group": "expression", "label": "license matrix"},
    {"requirement_id": "notice", "group": "expression", "label": "NOTICE"},
    {"requirement_id": "sbom-spdx", "group": "evidence", "label": "SBOM/SPDX"},
    {"requirement_id": "security", "group": "expression", "label": "SECURITY"},
    {"requirement_id": "contributing", "group": "expression", "label": "CONTRIBUTING"},
    {"requirement_id": "support", "group": "expression", "label": "SUPPORT"},
    {"requirement_id": "migration-guide", "group": "expression", "label": "migration guide"},
    {"requirement_id": "release-notes", "group": "expression", "label": "release notes"},
    {"requirement_id": "source-allowlist", "group": "evidence", "label": "source allowlist"},
    {"requirement_id": "cleanroom", "group": "product", "label": "clean-room installation evidence"},
    {"requirement_id": "sealed-export-rights", "group": "authority", "label": "sealed export and rights evidence"},
)

_FILE_REQUIREMENT_IDS = {
    item["requirement_id"]
    for item in ALPHA_REQUIREMENTS
    if item["requirement_id"] not in {"candidate-package-root", "sealed-export-rights"}
}
_GROUPS = ("product", "evidence", "expression", "authority")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_artifact_path(root: Path, value: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value:
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-PATH-INVALID")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or str(pure) in {".", ""}:
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-PATH-ESCAPES-ROOT")
    candidate = root.joinpath(*pure.parts)
    # Do not allow an apparently in-root file to resolve through a symlink.
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-SYMLINK-REFUSED")
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-PATH-ESCAPES-ROOT")
    return candidate, pure.as_posix()


def _result(requirement: dict[str, str], state: str, detail: str, **extra: str) -> dict[str, str]:
    return {
        "requirement_id": requirement["requirement_id"],
        "group": requirement["group"],
        "state": state,
        "detail": detail,
        **extra,
    }


def _validate_artifacts(
    value: Any,
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, str] | None,
    dict[str, str] | None,
]:
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), dict):
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACTS-INVALID")
    if value.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACTS-INVALID")
    # A two-field declaration remains useful for reporting missing package
    # slots.  It cannot, however, establish a candidate identity to which a
    # rights artifact could be bound, so it can never cover that requirement.
    allowed_shapes = {
        frozenset({"schema_version", "artifacts"}),
        frozenset({
            "schema_version", "manifest_id", "candidate_binding", "artifacts",
            "rights_evidence", "authorization", "boundary", "manifest_hash",
        }),
    }
    if frozenset(value) not in allowed_shapes:
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACTS-INVALID")
    candidate_binding: dict[str, str] | None = None
    rights_pointer: dict[str, str] | None = None
    if len(value) > 2:
        validate_payload(
            value,
            SCHEMAS_ROOT / "public-package-artifacts.schema.json",
            "Public package artifacts",
        )
        verify_self_hash(value, "manifest_hash")
        if value["authorization"] != "private_staging_only":
            raise ValidationError("PUBLIC-PACKAGE-ARTIFACTS-UNAUTHORIZED")
        candidate_binding = value["candidate_binding"]
        rights_pointer = value["rights_evidence"]
    artifacts = value["artifacts"]
    if not set(artifacts).issubset(_FILE_REQUIREMENT_IDS):
        raise ValidationError("PUBLIC-PACKAGE-ARTIFACTS-UNKNOWN-REQUIREMENT")
    normalized: dict[str, dict[str, str]] = {}
    for requirement_id, item in artifacts.items():
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-INVALID")
        path, expected_hash = item["path"], item["sha256"]
        if (
            not isinstance(path, str)
            or not isinstance(expected_hash, str)
            or not expected_hash.startswith("sha256:")
            or len(expected_hash) != 71
            or any(char not in "0123456789abcdef" for char in expected_hash[7:])
        ):
            raise ValidationError("PUBLIC-PACKAGE-ARTIFACT-INVALID")
        normalized[requirement_id] = {"path": path, "sha256": expected_hash}
    return normalized, candidate_binding, rights_pointer


def _validate_rights_evidence(
    value: Any,
    *,
    candidate_package_root: Path,
    candidate_binding: dict[str, str] | None,
    rights_pointer: dict[str, str] | None,
) -> str | None:
    if value is None:
        return "no sealed export/rights evidence was supplied"
    if candidate_binding is None or rights_pointer is None:
        return "sealed export/rights evidence cannot be bound to an unsealed artifact declaration"
    try:
        validate_payload(
            value,
            SCHEMAS_ROOT / "sealed-export-rights-evidence.schema.json",
            "Sealed export/rights evidence",
        )
        verify_self_hash(value, "evidence_hash")
    except ValidationError as exc:
        return f"sealed export/rights evidence is schema-invalid or stale: {exc}"
    if (
        value["schema_version"] != RIGHTS_SCHEMA_VERSION
        or value["candidate_binding"] != candidate_binding
        or value["rights_review_state"] != "approved"
        or value["authorization"] != "private_staging_only"
    ):
        return "sealed export/rights evidence is incomplete, unauthorized, or bound to a different candidate"
    if (
        value["evidence_id"] != rights_pointer["evidence_id"]
        or value["evidence_hash"] != rights_pointer["evidence_hash"]
    ):
        return "sealed export/rights evidence does not match the artifact declaration"
    try:
        path, _ = _safe_artifact_path(candidate_package_root, rights_pointer["path"])
    except ValidationError as exc:
        return str(exc)
    if not path.is_file() or path.is_symlink():
        return "declared sealed export/rights evidence is not a regular file"
    if _sha256(path) != rights_pointer["sha256"]:
        return "declared sealed export/rights evidence hash does not match current bytes"
    try:
        on_disk_value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "declared sealed export/rights evidence cannot be read as JSON"
    if on_disk_value != value:
        return "supplied sealed export/rights evidence is not the declared candidate artifact"
    return None


def evaluate_public_package_preflight(
    candidate_package_root: Path,
    required_public_artifacts: dict[str, Any],
    sealed_export_rights_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess a named candidate package without authoring public material.

    ``required_public_artifacts`` is a ``public-package-artifacts/v1`` object.
    Its artifact paths are relative to ``candidate_package_root`` and each must
    carry the exact SHA-256 of the bytes being claimed.  It may intentionally
    omit items: omissions are reported as missing and block the preflight.
    """

    root = candidate_package_root.expanduser()
    results: list[dict[str, str]] = []
    root_requirement = ALPHA_REQUIREMENTS[0]
    if root.is_symlink() or not root.is_dir():
        results.append(_result(root_requirement, "blocked", "candidate package root is not a regular directory"))
        root_ready = False
    else:
        root = root.resolve()
        results.append(_result(root_requirement, "covered", "candidate package root is a regular directory"))
        root_ready = True

    artifacts, candidate_binding, rights_pointer = _validate_artifacts(required_public_artifacts)
    requirement_by_id = {item["requirement_id"]: item for item in ALPHA_REQUIREMENTS}
    for requirement_id in sorted(_FILE_REQUIREMENT_IDS):
        requirement = requirement_by_id[requirement_id]
        supplied = artifacts.get(requirement_id)
        if supplied is None:
            results.append(_result(requirement, "missing", "no artifact path and hash were supplied"))
            continue
        if not root_ready:
            results.append(_result(requirement, "blocked", "candidate package root is unavailable"))
            continue
        try:
            path, path_ref = _safe_artifact_path(root, supplied["path"])
        except ValidationError as exc:
            results.append(_result(requirement, "blocked", str(exc)))
            continue
        if not path.is_file() or path.is_symlink():
            results.append(_result(requirement, "missing", "declared artifact is not a regular file", path=path_ref))
            continue
        observed_hash = _sha256(path)
        if observed_hash != supplied["sha256"]:
            results.append(_result(requirement, "blocked", "declared artifact hash does not match current bytes", path=path_ref, observed_sha256=observed_hash))
            continue
        results.append(_result(requirement, "covered", "regular artifact exists and matches its declared hash", path=path_ref, sha256=observed_hash))

    rights_requirement = requirement_by_id["sealed-export-rights"]
    rights_error = _validate_rights_evidence(
        sealed_export_rights_evidence,
        candidate_package_root=root,
        candidate_binding=candidate_binding,
        rights_pointer=rights_pointer,
    )
    if rights_error:
        results.append(_result(rights_requirement, "blocked", rights_error))
    else:
        results.append(_result(rights_requirement, "covered", "sealed export/rights evidence has the required staging shape"))

    coverage = {group: [] for group in _GROUPS}
    missing: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    for item in results:
        coverage[item["group"]].append(item)
        if item["state"] == "missing":
            missing.append(item)
        elif item["state"] == "blocked":
            blocked.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "alpha",
        "candidate_package_root": root.name if root_ready else str(candidate_package_root),
        "verdict": "preflight_complete_for_owner_gate" if not missing and not blocked else "blocked",
        "coverage": coverage,
        "missing": missing,
        "blocked": blocked,
        "boundary": (
            "This private-staging report does not generate LICENSE, NOTICE, SECURITY, or any public commitment; "
            "it does not grant rights, authorize release, publish a package, or replace independent clean-room verification."
        ),
    }


def write_public_package_preflight(
    candidate_package_root: Path,
    required_public_artifacts: dict[str, Any],
    output: Path,
    sealed_export_rights_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a new immutable staging report; refuse to replace an old report."""

    result = evaluate_public_package_preflight(
        candidate_package_root,
        required_public_artifacts,
        sealed_export_rights_evidence,
    )
    atomic_create_json(output, result)
    return result
