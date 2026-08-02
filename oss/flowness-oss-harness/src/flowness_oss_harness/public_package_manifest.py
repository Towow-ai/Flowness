from __future__ import annotations

"""Seal and verify private public-package evidence declarations.

The declaration is deliberately *not* a release artifact.  It turns an
already sealed Candidate B assembly plus exact, rooted evidence bytes into one
``public-package-artifacts/v1`` object that the preflight can consume.  It
does not write LICENSE/NOTICE/SBOM material, export source, or authorize a
release.  In particular, a rights review can cover a candidate without being
an owner release decision.
"""

import hashlib
from pathlib import Path
from typing import Any

from .candidate_b_assembly import verify_candidate_b_assembly
from .integrity import canonical_hash, verify_self_hash
from .public_package_preflight import ARTIFACT_SCHEMA_VERSION, _FILE_REQUIREMENT_IDS, _safe_artifact_path
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


INPUT_SCHEMA = SCHEMAS_ROOT / "public-package-artifact-input.schema.json"
MANIFEST_SCHEMA = SCHEMAS_ROOT / "public-package-artifacts.schema.json"

_RIGHTS_SCHEMA_VERSION = "sealed-export-rights-evidence/v1"
_AUTHORIZATION = "private_staging_only"
_BOUNDARY = (
    "private package-evidence manifest only; it does not generate legal content, grant distribution rights, "
    "seal/export source, authorize a release, publish a package, send a channel post, or replace independent clean-room verification"
)


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-NOT-REGULAR-FILE")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-ROOT-UNSAFE")
    return path.resolve(strict=True)


def _candidate_binding(assembly_dir: Path) -> dict[str, str]:
    """Return an exact Candidate B identity after verifying every copied byte."""

    assembly = verify_candidate_b_assembly(assembly_dir)
    assembly_file = assembly_dir / "candidate-b-assembly.json"
    return {
        "assembly_id": assembly["assembly_id"],
        "assembly_hash": assembly["assembly_hash"],
        "assembly_file_sha256": _sha256(assembly_file),
        "candidate_id": assembly["candidate"]["candidate_id"],
        "snapshot_id": assembly["candidate"]["snapshot_id"],
    }


def _assert_declared_binding(value: dict[str, Any], expected: dict[str, str], label: str) -> None:
    if value != expected:
        raise ValidationError(f"PUBLIC-PACKAGE-MANIFEST-{label}-CROSS-CANDIDATE")


def _read_input(path: Path) -> dict[str, Any]:
    return load_validated_json(path, INPUT_SCHEMA, "Public package artifact input")


def _validated_slots(
    root: Path, slots: list[dict[str, Any]], binding: dict[str, str],
) -> dict[str, dict[str, str]]:
    seen_slots: set[str] = set()
    seen_paths: set[str] = set()
    result: dict[str, dict[str, str]] = {}
    for item in slots:
        slot = item["slot"]
        if slot in seen_slots:
            raise ValidationError("PUBLIC-PACKAGE-MANIFEST-DUPLICATE-SLOT")
        seen_slots.add(slot)
        _assert_declared_binding(item["candidate_binding"], binding, f"SLOT-{slot}")
        path, relative = _safe_artifact_path(root, item["path"])
        if relative in seen_paths:
            raise ValidationError("PUBLIC-PACKAGE-MANIFEST-DUPLICATE-ARTIFACT-PATH")
        seen_paths.add(relative)
        observed = _sha256(path)
        if item["sha256"] != observed:
            raise ValidationError(f"PUBLIC-PACKAGE-MANIFEST-SLOT-HASH-STALE:{slot}")
        result[slot] = {"path": relative, "sha256": observed}
    if set(result) != _FILE_REQUIREMENT_IDS:
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-SLOTS-MISSING-OR-UNKNOWN")
    return {slot: result[slot] for slot in sorted(result)}


def _rights_evidence(root: Path, value: dict[str, Any], binding: dict[str, str]) -> dict[str, Any]:
    _assert_declared_binding(value["candidate_binding"], binding, "RIGHTS")
    path, relative = _safe_artifact_path(root, value["path"])
    observed = _sha256(path)
    if value["sha256"] != observed:
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-RIGHTS-HASH-STALE")
    evidence = load_validated_json(path, SCHEMAS_ROOT / "sealed-export-rights-evidence.schema.json", "Sealed export/rights evidence")
    verify_self_hash(evidence, "evidence_hash")
    if (
        evidence["schema_version"] != _RIGHTS_SCHEMA_VERSION
        or evidence["candidate_binding"] != binding
        or evidence["rights_review_state"] != "approved"
        or evidence["authorization"] != _AUTHORIZATION
    ):
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-RIGHTS-UNAUTHORIZED")
    return {
        "path": relative,
        "sha256": observed,
        "evidence_id": evidence["evidence_id"],
        "evidence_hash": evidence["evidence_hash"],
    }


def create_public_package_artifact_manifest(
    *,
    candidate_assembly_dir: Path,
    candidate_package_root: Path,
    artifact_input_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Create a non-overwritable, hash-bound private staging declaration."""

    binding = _candidate_binding(candidate_assembly_dir)
    root = _root(candidate_package_root)
    supplied = _read_input(artifact_input_path)
    _assert_declared_binding(supplied["candidate_binding"], binding, "INPUT")
    artifacts = _validated_slots(root, supplied["artifact_slots"], binding)
    rights = _rights_evidence(root, supplied["rights_evidence"], binding)
    unsigned = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "manifest_id": "public-package-artifacts-" + canonical_hash(
            {"candidate_binding": binding, "artifacts": artifacts, "rights_evidence": rights}
        ).removeprefix("sha256:")[:24],
        "candidate_binding": binding,
        "artifacts": artifacts,
        "rights_evidence": rights,
        "authorization": _AUTHORIZATION,
        "boundary": _BOUNDARY,
    }
    manifest = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    validate_payload(manifest, MANIFEST_SCHEMA, "Public package artifacts")
    atomic_create_json(output, manifest)
    return manifest


def verify_public_package_artifact_manifest(
    *,
    candidate_assembly_dir: Path,
    candidate_package_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Fail closed on stale bytes, duplicate slots, or a different candidate."""

    binding = _candidate_binding(candidate_assembly_dir)
    root = _root(candidate_package_root)
    manifest = load_validated_json(manifest_path, MANIFEST_SCHEMA, "Public package artifacts")
    verify_self_hash(manifest, "manifest_hash")
    if (
        manifest["schema_version"] != ARTIFACT_SCHEMA_VERSION
        or manifest["candidate_binding"] != binding
        or manifest["authorization"] != _AUTHORIZATION
        or manifest["boundary"] != _BOUNDARY
    ):
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-IDENTITY-INVALID")
    artifacts = manifest["artifacts"]
    if set(artifacts) != _FILE_REQUIREMENT_IDS:
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-SLOTS-MISSING-OR-UNKNOWN")
    seen_paths: set[str] = set()
    for slot, item in artifacts.items():
        path, relative = _safe_artifact_path(root, item["path"])
        if relative in seen_paths:
            raise ValidationError("PUBLIC-PACKAGE-MANIFEST-DUPLICATE-ARTIFACT-PATH")
        seen_paths.add(relative)
        if _sha256(path) != item["sha256"]:
            raise ValidationError(f"PUBLIC-PACKAGE-MANIFEST-SLOT-HASH-STALE:{slot}")
    rights = manifest["rights_evidence"]
    rights_value = {
        "path": rights["path"],
        "sha256": rights["sha256"],
        "candidate_binding": binding,
    }
    verified_rights = _rights_evidence(root, rights_value, binding)
    if verified_rights != rights:
        raise ValidationError("PUBLIC-PACKAGE-MANIFEST-RIGHTS-IDENTITY-INVALID")
    return manifest
