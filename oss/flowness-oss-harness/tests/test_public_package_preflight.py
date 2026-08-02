from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flowness_oss_harness.cli import main
from flowness_oss_harness.integrity import canonical_hash
from flowness_oss_harness.public_package_preflight import (
    ARTIFACT_SCHEMA_VERSION,
    RIGHTS_SCHEMA_VERSION,
    evaluate_public_package_preflight,
)


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _binding() -> dict[str, str]:
    return {
        "assembly_id": "assembly-001",
        "assembly_hash": "sha256:" + "a" * 64,
        "assembly_file_sha256": "sha256:" + "b" * 64,
        "candidate_id": "candidate-001",
        "snapshot_id": "sha256:" + "c" * 64,
    }


def _rights(binding: dict[str, str]) -> dict:
    body = {
        "schema_version": RIGHTS_SCHEMA_VERSION,
        "evidence_id": "rights-001",
        "candidate_binding": binding,
        "rights_review_id": "review-001",
        "rights_review_state": "approved",
        "authorization": "private_staging_only",
        "boundary": "staging evidence only; no release approval",
    }
    return {**body, "evidence_hash": canonical_hash(body)}


def _artifacts(root: Path, *, binding: dict[str, str] | None = None, rights: dict | None = None) -> dict:
    paths = {
        "license-matrix": "LICENSES.md",
        "notice": "NOTICE",
        "sbom-spdx": "sbom.spdx.json",
        "security": "SECURITY.md",
        "contributing": "CONTRIBUTING.md",
        "support": "SUPPORT.md",
        "migration-guide": "docs/MIGRATION.md",
        "release-notes": "docs/RELEASE_NOTES.md",
        "source-allowlist": "public-export-allowlist.json",
        "cleanroom": "evidence/cleanroom.json",
    }
    result: dict[str, dict[str, str]] = {}
    for requirement_id, relative in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(requirement_id + "\n", encoding="utf-8")
        result[requirement_id] = {"path": relative, "sha256": _hash(path)}
    if binding is None or rights is None:
        return {"schema_version": ARTIFACT_SCHEMA_VERSION, "artifacts": result}
    rights_path = root / "evidence" / "rights.json"
    rights_path.parent.mkdir(parents=True, exist_ok=True)
    rights_path.write_text(json.dumps(rights, sort_keys=True), encoding="utf-8")
    body = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "manifest_id": "public-package-artifacts-001",
        "candidate_binding": binding,
        "artifacts": result,
        "rights_evidence": {
            "path": rights_path.relative_to(root).as_posix(),
            "sha256": _hash(rights_path),
            "evidence_id": rights["evidence_id"],
            "evidence_hash": rights["evidence_hash"],
        },
        "authorization": "private_staging_only",
        "boundary": "private evidence declaration only",
    }
    return {**body, "manifest_hash": canonical_hash(body)}


def test_empty_rights_and_artifact_inputs_fail_closed_without_scaffolding(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    result = evaluate_public_package_preflight(
        root,
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "artifacts": {}},
        None,
    )

    assert result["verdict"] == "blocked"
    assert {item["requirement_id"] for item in result["missing"]} == {
        "license-matrix", "notice", "sbom-spdx", "security", "contributing",
        "support", "migration-guide", "release-notes", "source-allowlist", "cleanroom",
    }
    assert result["blocked"] == [{
        "requirement_id": "sealed-export-rights",
        "group": "authority",
        "state": "blocked",
        "detail": "no sealed export/rights evidence was supplied",
    }]
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


def test_complete_fixture_requires_hash_bound_artifacts_and_still_only_reaches_owner_gate(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    binding = _binding()
    rights = _rights(binding)

    result = evaluate_public_package_preflight(
        root,
        _artifacts(root, binding=binding, rights=rights),
        rights,
    )

    assert result["verdict"] == "preflight_complete_for_owner_gate"
    assert not result["missing"]
    assert not result["blocked"]
    assert result["coverage"]["authority"][0]["requirement_id"] == "sealed-export-rights"
    assert "does not grant rights" in result["boundary"]


def test_tampered_or_escaping_artifact_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    # The compact declaration is intentionally retained for a diagnostic
    # report of missing/stale file slots. It cannot cover the rights gate.
    artifacts = _artifacts(root)
    (root / "NOTICE").write_text("changed\n", encoding="utf-8")
    artifacts["artifacts"]["security"]["path"] = "../outside.md"

    result = evaluate_public_package_preflight(root, artifacts, None)

    blocked = {item["requirement_id"]: item["detail"] for item in result["blocked"]}
    assert blocked["notice"] == "declared artifact hash does not match current bytes"
    assert blocked["security"] == "PUBLIC-PACKAGE-ARTIFACT-PATH-ESCAPES-ROOT"


def test_flat_rights_alias_cannot_cover_the_authority_requirement(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    binding = _binding()
    rights = _rights(binding)
    artifacts = _artifacts(root, binding=binding, rights=rights)
    flat_alias = {
        "schema_version": RIGHTS_SCHEMA_VERSION,
        "evidence_id": "rights-001",
        "sealed_export_manifest_sha256": "sha256:" + "d" * 64,
        "rights_review_id": "review-001",
        "rights_review_state": "approved",
        "boundary": "old flat shape",
    }

    result = evaluate_public_package_preflight(root, artifacts, flat_alias)

    assert result["verdict"] == "blocked"
    assert result["coverage"]["authority"] == [{
        "requirement_id": "sealed-export-rights",
        "group": "authority",
        "state": "blocked",
        "detail": "sealed export/rights evidence is schema-invalid or stale: Sealed export/rights evidence schema violation at <root>: Additional properties are not allowed ('sealed_export_manifest_sha256' was unexpected)",
    }]


def test_rights_evidence_must_be_self_hashed_and_cross_bound_to_the_artifact(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    binding = _binding()
    rights = _rights(binding)
    artifacts = _artifacts(root, binding=binding, rights=rights)

    tampered = dict(rights)
    tampered["candidate_binding"] = {**binding, "candidate_id": "other-candidate"}
    tampered["evidence_hash"] = canonical_hash({key: value for key, value in tampered.items() if key != "evidence_hash"})
    result = evaluate_public_package_preflight(root, artifacts, tampered)

    assert result["verdict"] == "blocked"
    assert result["coverage"]["authority"][0]["detail"] == "sealed export/rights evidence is incomplete, unauthorized, or bound to a different candidate"

    stale = dict(rights)
    stale["rights_review_id"] = "modified-without-rehash"
    result = evaluate_public_package_preflight(root, artifacts, stale)
    assert result["coverage"]["authority"][0]["detail"].startswith(
        "sealed export/rights evidence is schema-invalid or stale: invalid immutable evidence_hash"
    )


def test_cli_writes_an_immutable_report_not_public_material(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    artifacts_path = tmp_path / "artifacts.json"
    artifacts_path.write_text(json.dumps({"schema_version": ARTIFACT_SCHEMA_VERSION, "artifacts": {}}), encoding="utf-8")
    output = tmp_path / "preflight.json"

    assert main([
        "public-package-preflight",
        "--candidate-package-root", str(root),
        "--required-public-artifacts", str(artifacts_path),
        "--output", str(output),
    ]) == 0

    assert json.loads(capsys.readouterr().out)["verdict"] == "blocked"
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "public-package-preflight-report/v1"
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == []
    assert main([
        "public-package-preflight",
        "--candidate-package-root", str(root),
        "--required-public-artifacts", str(artifacts_path),
        "--output", str(output),
    ]) == 2
