"""Fail-closed, private Candidate B assembly evidence.

This is deliberately a *staging* object, not a release package.  It copies and
binds the exact successor candidate, clean source snapshot, candidate assembly
manifest, built-in governance policy, every candidate evidence byte, and the
original blocker/rework lineage.  Nothing in this module evaluates a release,
starts an agent, installs a package, or performs network I/O.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .policy import APPROVED_POLICY_PATH, load_approved_policy
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


CANDIDATE_SCHEMA = SCHEMAS_ROOT / "release-candidate.schema.json"
BLOCKER_CASE_SCHEMA = SCHEMAS_ROOT / "blocker-case.schema.json"
REWORK_MANIFEST_SCHEMA = SCHEMAS_ROOT / "rework-manifest.schema.json"
CANDIDATE_B_ASSEMBLY_SCHEMA = SCHEMAS_ROOT / "candidate-b-assembly.schema.json"


def _file_hash(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return payload


def _load_candidate(path: Path) -> dict[str, Any]:
    candidate = load_validated_json(path, CANDIDATE_SCHEMA, "successor candidate")
    expected_id = "candidate-" + canonical_hash(
        {key: value for key, value in candidate.items() if key != "candidate_id"}
    ).removeprefix("sha256:")[:24]
    if candidate["candidate_id"] != expected_id:
        raise ValidationError("CANDIDATE-B-CANDIDATE-ID-MISMATCH")
    evidence_ids = [item["evidence_id"] for item in candidate["evidence"]]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValidationError("CANDIDATE-B-DUPLICATE-CANDIDATE-EVIDENCE")
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    if any(item["snapshot_id"] != snapshot_id for item in candidate["evidence"]):
        raise ValidationError("CANDIDATE-B-EVIDENCE-CROSS-SNAPSHOT")
    return candidate


def _load_source_snapshot(path: Path, expected_snapshot_id: str) -> dict[str, Any]:
    snapshot = _read_json(path, "successor source snapshot")
    if (
        snapshot.get("schema_version") != "evidence-snapshot/v1"
        or snapshot.get("snapshot_id") != expected_snapshot_id
        or snapshot.get("dirty") is not False
        or snapshot.get("candidate_assembly_eligible") is not True
        or snapshot.get("release_eligible") is not False
    ):
        raise ValidationError("CANDIDATE-B-SOURCE-SNAPSHOT-INELIGIBLE")
    return snapshot


def _load_assembly_manifest(path: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    manifest = _read_json(path, "successor candidate assembly manifest")
    if manifest.get("schema_version") != "candidate-assembly-manifest/v1":
        raise ValidationError("CANDIDATE-B-ASSEMBLY-MANIFEST-VERSION")
    verify_self_hash(manifest, "manifest_hash")
    expected = {
        "schema_version", "snapshot", "target_stage", "created_at", "manifest_hash"
    }
    if set(manifest) != expected:
        raise ValidationError("CANDIDATE-B-ASSEMBLY-MANIFEST-FIELDS")
    if (
        manifest["snapshot"] != candidate["snapshot"]
        or manifest["target_stage"] != candidate["target_stage"]
        or manifest["created_at"] != candidate["created_at"]
    ):
        raise ValidationError("CANDIDATE-B-ASSEMBLY-MANIFEST-BINDING-MISMATCH")
    return manifest


def _load_lineage(
    blocker_case_path: Path,
    rework_manifest_path: Path,
    candidate_path: Path,
    snapshot_path: Path,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = load_validated_json(blocker_case_path, BLOCKER_CASE_SCHEMA, "blocker case")
    verify_self_hash(case, "case_hash")
    rework = load_validated_json(rework_manifest_path, REWORK_MANIFEST_SCHEMA, "rework manifest")
    verify_self_hash(rework, "manifest_hash")
    candidate_hash = _file_hash(candidate_path, "successor candidate")
    snapshot_hash = _file_hash(snapshot_path, "successor source snapshot")
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    if (
        case["original"]["candidate_id"] == candidate["candidate_id"]
        or case["original"]["snapshot_id"] == snapshot_id
        or rework["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or rework["blocker_case"]["blocker_id"] != case["blocker_id"]
        or rework["blocker_case"]["case_hash"] != case["case_hash"]
        or rework["blocker_case"]["artifact"]["sha256"]
        != _file_hash(blocker_case_path, "blocker case")
        or rework["successor"]["candidate_id"] != candidate["candidate_id"]
        or rework["successor"]["snapshot_id"] != snapshot_id
        or rework["successor"]["candidate"]["sha256"] != candidate_hash
        or rework["successor"]["snapshot"]["sha256"] != snapshot_hash
    ):
        raise ValidationError("CANDIDATE-B-LINEAGE-BINDING-MISMATCH")
    return case, rework


def _evidence_paths(
    candidate: dict[str, Any], evidence_paths: list[tuple[str, Path]]
) -> list[tuple[str, Path]]:
    supplied_ids = [evidence_id for evidence_id, _ in evidence_paths]
    expected = {item["evidence_id"]: item["sha256"] for item in candidate["evidence"]}
    if len(supplied_ids) != len(set(supplied_ids)):
        raise ValidationError("CANDIDATE-B-DUPLICATE-EVIDENCE-BINDING")
    if set(supplied_ids) != set(expected):
        raise ValidationError("CANDIDATE-B-DANGLING-OR-MISSING-EVIDENCE")
    ordered = sorted(evidence_paths, key=lambda item: item[0])
    for evidence_id, path in ordered:
        if _file_hash(path, f"evidence {evidence_id}") != expected[evidence_id]:
            raise ValidationError("CANDIDATE-B-EVIDENCE-HASH-MISMATCH")
    return ordered


def _copy_regular(source: Path, target: Path, label: str) -> str:
    digest = _file_hash(source, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)
    if _file_hash(target, f"copied {label}") != digest:
        raise ValidationError(f"{label} changed while being assembled")
    return digest


def _relative_file(root: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError(f"{label} path escapes assembly")
    result = root / relative
    if result.is_symlink() or not result.is_file():
        raise ValidationError(f"{label} file is missing or unsafe")
    return result


def create_candidate_b_assembly(
    *,
    candidate_path: Path,
    source_snapshot_path: Path,
    assembly_manifest_path: Path,
    blocker_case_path: Path,
    rework_manifest_path: Path,
    evidence_paths: list[tuple[str, Path]],
    output_dir: Path,
) -> dict[str, Any]:
    """Create a private, immutable successor-assembly record.

    The function accepts no policy override and has no release/publication path.
    It is intentionally library-only: the frozen command surface cannot be used
    as a back door to create or publish this record.
    """

    if output_dir.exists() or output_dir.is_symlink():
        raise ValidationError("candidate B assembly output already exists")
    candidate = _load_candidate(candidate_path)
    _load_source_snapshot(source_snapshot_path, candidate["snapshot"]["snapshot_id"])
    _load_assembly_manifest(assembly_manifest_path, candidate)
    case, rework = _load_lineage(
        blocker_case_path,
        rework_manifest_path,
        candidate_path,
        source_snapshot_path,
        candidate,
    )
    evidence = _evidence_paths(candidate, evidence_paths)
    policy, policy_hash = load_approved_policy()

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        candidate_ref = {
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
            "artifact": {"path": "artifacts/candidate-b.json", "sha256": _copy_regular(candidate_path, staging / "artifacts/candidate-b.json", "successor candidate")},
        }
        source = {
            "snapshot": {"path": "artifacts/source-snapshot.json", "sha256": _copy_regular(source_snapshot_path, staging / "artifacts/source-snapshot.json", "successor source snapshot")},
            "assembly_manifest": {"path": "artifacts/candidate-assembly-manifest.json", "sha256": _copy_regular(assembly_manifest_path, staging / "artifacts/candidate-assembly-manifest.json", "successor candidate assembly manifest")},
        }
        policy_ref = {
            "policy_version": policy["policy_version"],
            "artifact": {"path": "artifacts/governance-policy.json", "sha256": _copy_regular(APPROVED_POLICY_PATH, staging / "artifacts/governance-policy.json", "built-in governance policy")},
        }
        if policy_ref["artifact"]["sha256"] != policy_hash:
            raise ValidationError("CANDIDATE-B-POLICY-HASH-MISMATCH")
        evidence_refs = [
            {
                "evidence_id": evidence_id,
                "artifact": {
                    "path": f"evidence/{index:03d}.bin",
                    "sha256": _copy_regular(path, staging / f"evidence/{index:03d}.bin", f"evidence {evidence_id}"),
                },
            }
            for index, (evidence_id, path) in enumerate(evidence, start=1)
        ]
        lineage = {
            "blocker_case_id": case["blocker_case_id"],
            "blocker_id": case["blocker_id"],
            "origin_candidate_id": case["original"]["candidate_id"],
            "origin_snapshot_id": case["original"]["snapshot_id"],
            "blocker_case": {"path": "lineage/blocker-case.json", "sha256": _copy_regular(blocker_case_path, staging / "lineage/blocker-case.json", "blocker case")},
            "rework_manifest": {"path": "lineage/rework-manifest.json", "sha256": _copy_regular(rework_manifest_path, staging / "lineage/rework-manifest.json", "rework manifest")},
        }
        unsigned = {
            "schema_version": "candidate-b-assembly/v1",
            "assembly_id": "candidate-b-assembly-" + canonical_hash({"candidate": candidate_ref, "source": source, "policy": policy_ref, "evidence": evidence_refs, "lineage": lineage}).removeprefix("sha256:")[:24],
            "authorization": "private_candidate_assembly_only",
            "candidate": candidate_ref,
            "source": source,
            "policy": policy_ref,
            "evidence": evidence_refs,
            "lineage": lineage,
        }
        payload = {**unsigned, "assembly_hash": canonical_hash(unsigned)}
        validate_payload(payload, CANDIDATE_B_ASSEMBLY_SCHEMA, "candidate B assembly")
        atomic_create_json(staging / "candidate-b-assembly.json", payload)
        os.replace(staging, output_dir)
        return payload
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_candidate_b_assembly(output_dir: Path) -> dict[str, Any]:
    """Verify only the immutable private assembly copies; never release them."""

    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValidationError("candidate B assembly directory is missing or unsafe")
    payload = load_validated_json(
        output_dir / "candidate-b-assembly.json",
        CANDIDATE_B_ASSEMBLY_SCHEMA,
        "candidate B assembly",
    )
    verify_self_hash(payload, "assembly_hash")
    candidate_path = _relative_file(output_dir, payload["candidate"]["artifact"]["path"], "candidate")
    snapshot_path = _relative_file(output_dir, payload["source"]["snapshot"]["path"], "source snapshot")
    manifest_path = _relative_file(output_dir, payload["source"]["assembly_manifest"]["path"], "assembly manifest")
    case_path = _relative_file(output_dir, payload["lineage"]["blocker_case"]["path"], "blocker case")
    rework_path = _relative_file(output_dir, payload["lineage"]["rework_manifest"]["path"], "rework manifest")
    policy_path = _relative_file(output_dir, payload["policy"]["artifact"]["path"], "policy")
    all_refs = [
        (candidate_path, payload["candidate"]["artifact"]["sha256"], "candidate"),
        (snapshot_path, payload["source"]["snapshot"]["sha256"], "source snapshot"),
        (manifest_path, payload["source"]["assembly_manifest"]["sha256"], "assembly manifest"),
        (case_path, payload["lineage"]["blocker_case"]["sha256"], "blocker case"),
        (rework_path, payload["lineage"]["rework_manifest"]["sha256"], "rework manifest"),
        (policy_path, payload["policy"]["artifact"]["sha256"], "policy"),
    ]
    evidence_paths: list[tuple[str, Path]] = []
    for evidence in payload["evidence"]:
        path = _relative_file(output_dir, evidence["artifact"]["path"], f"evidence {evidence['evidence_id']}")
        all_refs.append((path, evidence["artifact"]["sha256"], f"evidence {evidence['evidence_id']}"))
        evidence_paths.append((evidence["evidence_id"], path))
    for path, expected_hash, label in all_refs:
        if _file_hash(path, label) != expected_hash:
            raise ValidationError("CANDIDATE-B-ASSEMBLY-HASH-MISMATCH")
    policy, policy_hash = load_approved_policy()
    if _file_hash(policy_path, "policy") != policy_hash:
        raise ValidationError("CANDIDATE-B-POLICY-HASH-MISMATCH")
    candidate = _load_candidate(candidate_path)
    _load_source_snapshot(snapshot_path, candidate["snapshot"]["snapshot_id"])
    _load_assembly_manifest(manifest_path, candidate)
    case, rework = _load_lineage(case_path, rework_path, candidate_path, snapshot_path, candidate)
    _evidence_paths(candidate, evidence_paths)
    if (
        payload["candidate"]["candidate_id"] != candidate["candidate_id"]
        or payload["candidate"]["snapshot_id"] != candidate["snapshot"]["snapshot_id"]
        or payload["policy"]["policy_version"] != policy["policy_version"]
        or payload["lineage"]["blocker_case_id"] != case["blocker_case_id"]
        or payload["lineage"]["blocker_id"] != case["blocker_id"]
        or payload["lineage"]["origin_candidate_id"] != case["original"]["candidate_id"]
        or payload["lineage"]["origin_snapshot_id"] != case["original"]["snapshot_id"]
        or rework["successor"]["candidate_id"] != candidate["candidate_id"]
    ):
        raise ValidationError("CANDIDATE-B-ASSEMBLY-BINDING-MISMATCH")
    return payload
