from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from flowness_oss_harness.integrity import canonical_hash
from flowness_oss_harness.public_package_manifest import (
    _candidate_binding,
    create_public_package_artifact_manifest,
    verify_public_package_artifact_manifest,
)
from flowness_oss_harness.public_package_preflight import _FILE_REQUIREMENT_IDS
from flowness_oss_harness.registry import ValidationError
from public_candidate_b_fixture import _assemble, _fixture


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _rights(binding: dict[str, str]) -> dict:
    body = {
        "schema_version": "sealed-export-rights-evidence/v1",
        "evidence_id": "rights-evidence-001",
        "candidate_binding": binding,
        "rights_review_id": "rights-review-001",
        "rights_review_state": "approved",
        "authorization": "private_staging_only",
        "boundary": "private review evidence; it cannot authorize export or release",
    }
    return {**body, "evidence_hash": canonical_hash(body)}


def _prepared(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    assembly_dir = tmp_path / "candidate-b-assembly"
    _assemble(_fixture(tmp_path), assembly_dir)
    assembly = _candidate_binding(assembly_dir)
    root = tmp_path / "private-package"
    root.mkdir()
    slots = []
    for index, slot in enumerate(sorted(_FILE_REQUIREMENT_IDS), start=1):
        path = root / "artifacts" / f"{index:02d}-{slot}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(slot + "\n", encoding="utf-8")
        slots.append({
            "slot": slot,
            "path": path.relative_to(root).as_posix(),
            "sha256": _hash(path),
            "candidate_binding": assembly,
        })
    rights_path = root / "evidence" / "rights.json"
    rights_path.parent.mkdir(parents=True)
    rights_path.write_text(json.dumps(_rights(assembly), sort_keys=True), encoding="utf-8")
    supplied = {
        "schema_version": "public-package-artifact-input/v1",
        "candidate_binding": assembly,
        "artifact_slots": slots,
        "rights_evidence": {
            "path": rights_path.relative_to(root).as_posix(),
            "sha256": _hash(rights_path),
            "candidate_binding": assembly,
        },
    }
    input_path = tmp_path / "artifact-input.json"
    input_path.write_text(json.dumps(supplied), encoding="utf-8")
    return assembly_dir, root, input_path, supplied


def test_manifest_binds_every_alpha_slot_to_verified_candidate_b_and_private_rights(tmp_path: Path) -> None:
    assembly_dir, root, input_path, _ = _prepared(tmp_path)
    output = tmp_path / "public-package-artifacts.json"

    manifest = create_public_package_artifact_manifest(
        candidate_assembly_dir=assembly_dir,
        candidate_package_root=root,
        artifact_input_path=input_path,
        output=output,
    )

    assert manifest["schema_version"] == "public-package-artifacts/v1"
    assert set(manifest["artifacts"]) == _FILE_REQUIREMENT_IDS
    assert manifest["authorization"] == "private_staging_only"
    assert "does not generate legal content" in manifest["boundary"]
    assert verify_public_package_artifact_manifest(
        candidate_assembly_dir=assembly_dir,
        candidate_package_root=root,
        manifest_path=output,
    ) == manifest
    with pytest.raises(ValidationError, match="refusing to overwrite"):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir,
            candidate_package_root=root,
            artifact_input_path=input_path,
            output=output,
        )


def test_generator_rejects_missing_duplicate_stale_and_cross_candidate_inputs(tmp_path: Path) -> None:
    assembly_dir, root, input_path, supplied = _prepared(tmp_path)

    missing = copy.deepcopy(supplied)
    missing["artifact_slots"] = missing["artifact_slots"][:-1]
    input_path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(ValidationError, match="SLOTS-MISSING-OR-UNKNOWN"):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir, candidate_package_root=root,
            artifact_input_path=input_path, output=tmp_path / "missing.json",
        )

    duplicate = copy.deepcopy(supplied)
    duplicate["artifact_slots"].append(copy.deepcopy(duplicate["artifact_slots"][0]))
    input_path.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(ValidationError, match="DUPLICATE-SLOT"):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir, candidate_package_root=root,
            artifact_input_path=input_path, output=tmp_path / "duplicate.json",
        )

    stale = copy.deepcopy(supplied)
    stale_path = root / stale["artifact_slots"][0]["path"]
    stale_path.write_text("changed\n", encoding="utf-8")
    input_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ValidationError, match="SLOT-HASH-STALE"):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir, candidate_package_root=root,
            artifact_input_path=input_path, output=tmp_path / "stale.json",
        )

    # Repair the exact bytes then show that a slot cannot be relabelled to a
    # different Candidate B identity.
    stale_path.write_text(stale["artifact_slots"][0]["slot"] + "\n", encoding="utf-8")
    cross = copy.deepcopy(supplied)
    cross["artifact_slots"][0]["candidate_binding"]["candidate_id"] = "other-candidate"
    input_path.write_text(json.dumps(cross), encoding="utf-8")
    with pytest.raises(ValidationError, match="CROSS-CANDIDATE"):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir, candidate_package_root=root,
            artifact_input_path=input_path, output=tmp_path / "cross.json",
        )


def test_generator_and_verifier_reject_unauthorized_or_stale_rights_evidence(tmp_path: Path) -> None:
    assembly_dir, root, input_path, supplied = _prepared(tmp_path)
    rights_path = root / supplied["rights_evidence"]["path"]
    rights = json.loads(rights_path.read_text(encoding="utf-8"))
    rights["authorization"] = "not_authorized"
    rights["evidence_hash"] = canonical_hash({key: value for key, value in rights.items() if key != "evidence_hash"})
    rights_path.write_text(json.dumps(rights), encoding="utf-8")
    supplied["rights_evidence"]["sha256"] = _hash(rights_path)
    input_path.write_text(json.dumps(supplied), encoding="utf-8")
    with pytest.raises(ValidationError):
        create_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir, candidate_package_root=root,
            artifact_input_path=input_path, output=tmp_path / "unauthorized.json",
        )

    # Restore valid rights, create a manifest, then mutate rights evidence so
    # later review does not mistake a stale input for the sealed one.
    binding = supplied["candidate_binding"]
    rights_path.write_text(json.dumps(_rights(binding)), encoding="utf-8")
    supplied["rights_evidence"]["sha256"] = _hash(rights_path)
    input_path.write_text(json.dumps(supplied), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    create_public_package_artifact_manifest(
        candidate_assembly_dir=assembly_dir, candidate_package_root=root,
        artifact_input_path=input_path, output=manifest_path,
    )
    rights_path.write_text("altered\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="RIGHTS-HASH-STALE"):
        verify_public_package_artifact_manifest(
            candidate_assembly_dir=assembly_dir,
            candidate_package_root=root,
            manifest_path=manifest_path,
        )
