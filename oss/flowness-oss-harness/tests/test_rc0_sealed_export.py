from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from flowness_oss_harness.rc0_sealed_export import (
    EXPORT_MANIFEST_NAME,
    FREEZE_RECORD_NAME,
    RIGHTS_POLICY_NAME,
    _load_rights_policy,
    build_rc0_sealed_export,
    verify_rc0_sealed_export,
)
from flowness_oss_harness.registry import ValidationError
from flowness_oss_harness.integrity import canonical_hash


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "RC0 Test")
    _write(repo, "src/tool.py", "print('included')\n")
    _write(repo, "src/runner.py", "print('experimental')\n")
    _write(repo, "docs/design.md", "target\n")
    _write(repo, "private/secret.txt", "excluded fixture\n")
    _write(repo, "held/later.txt", "held\n")
    os.chmod(repo / "src/tool.py", 0o755)
    policy = {
        "schema_version": "open-alpha-package-scope-policy/v1",
        "package_id": "fixture-rc0",
        "scope_roots": ["src", "docs", "private", "held", "scope.json", "rights.json"],
        "ignored_generated_paths": [],
        "rules": [
            {"rule_id": "runner", "patterns": ["src/runner.py"], "maturity": "experimental", "disposition": "include", "component": "multi_agent_harness", "reason": "runner", "claim_boundary": "local"},
            {"rule_id": "code", "patterns": ["src/**"], "maturity": "stable", "disposition": "include", "component": "multi_agent_harness", "reason": "code", "claim_boundary": "local"},
            {"rule_id": "docs", "patterns": ["docs/**"], "maturity": "design_target", "disposition": "include", "component": "architecture_d0_d9", "reason": "docs", "claim_boundary": "target"},
            {"rule_id": "private", "patterns": ["private/**"], "maturity": "private_excluded", "disposition": "exclude", "component": "private_boundary", "reason": "private", "claim_boundary": "never"},
            {"rule_id": "hold", "patterns": ["held/**", "scope.json", "rights.json"], "maturity": "experimental", "disposition": "hold", "component": "open_alpha_packaging", "reason": "later", "claim_boundary": "held"},
        ],
        "required_include_components": ["multi_agent_harness", "architecture_d0_d9"],
        "required_include_paths": ["src/tool.py", "src/runner.py", "docs/design.md"],
        "required_exclude_paths": ["private/secret.txt"],
        "global_boundary": "candidate only",
    }
    rights = {
        "schema_version": "fixture-rights-policy/v1",
        "rights_groups": [
            {"group_id": "code", "patterns": ["src/**"], "license_expression": "Apache-2.0", "origin_class": "owner", "rights_state": "owner_attestation_pending", "ip_review_state": "source_review_pending", "provenance_state": "repo", "evidence_refs": []},
            {"group_id": "docs", "patterns": ["docs/**"], "license_expression": "CC-BY-4.0", "origin_class": "owner", "rights_state": "blocked", "ip_review_state": "blocked", "provenance_state": "review", "evidence_refs": []},
        ]
    }
    _write(repo, "scope.json", json.dumps(policy))
    _write(repo, "rights.json", json.dumps(rights))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, repo / "scope.json", repo / "rights.json"


def test_current_release_policy_uses_rc0_valid_structured_rights_evidence() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "config/open-alpha-release-audit.json"
    )
    payload = _load_rights_policy(policy_path.read_bytes())

    assert payload["rights_groups"]
    assert all(group["evidence_refs"] for group in payload["rights_groups"])
    assert all(
        set(evidence_ref) == {"evidence_type", "path", "sha256"}
        for group in payload["rights_groups"]
        for evidence_ref in group["evidence_refs"]
    )


def test_rc0_rights_policy_rejects_opaque_or_missing_cleared_evidence() -> None:
    base_group = {
        "group_id": "code",
        "patterns": ["src/**"],
        "license_expression": "Apache-2.0",
        "origin_class": "owner",
        "rights_state": "cleared",
        "ip_review_state": "cleared",
        "provenance_state": "owner_attested",
        "evidence_refs": [],
    }
    missing = {
        "schema_version": "fixture-rights-policy/v1",
        "rights_groups": [base_group],
    }
    with pytest.raises(ValidationError, match="schema violation"):
        _load_rights_policy(json.dumps(missing).encode("utf-8"))

    opaque = json.loads(json.dumps(missing))
    opaque["rights_groups"][0]["evidence_refs"] = ["opaque:jury-pass"]
    with pytest.raises(ValidationError, match="schema violation"):
        _load_rights_policy(json.dumps(opaque).encode("utf-8"))


def test_rc0_rights_policy_rejects_unsafe_structured_evidence_path() -> None:
    payload = {
        "schema_version": "fixture-rights-policy/v1",
        "rights_groups": [
            {
                "group_id": "code",
                "patterns": ["src/**"],
                "license_expression": "Apache-2.0",
                "origin_class": "owner",
                "rights_state": "cleared",
                "ip_review_state": "cleared",
                "provenance_state": "owner_attested",
                "evidence_refs": [
                    {
                        "evidence_type": "owner_rights_attestation",
                        "path": "../outside.json",
                        "sha256": "sha256:" + "0" * 64,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError, match="RC0-EXPORT-PATH-INVALID"):
        _load_rights_policy(json.dumps(payload).encode("utf-8"))


def test_build_copies_only_included_head_blobs_and_verify_is_repository_independent(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    export = tmp_path / "export"
    manifest, freeze = build_rc0_sealed_export(
        repo=repo,
        scope_policy_path=scope,
        rights_policy_path=rights,
        export_root=export,
    )

    assert (export / "src/tool.py").read_text() == "print('included')\n"
    assert (export / "docs/design.md").read_text() == "target\n"
    assert not (export / "private/secret.txt").exists()
    assert not (export / "held/later.txt").exists()
    assert (export / "src/tool.py").stat().st_mode & stat_exec_bit()
    assert manifest["payload"]["files"] == 3
    assert {row["license_expression"] for row in manifest["files"]} == {"Apache-2.0", "CC-BY-4.0"}
    assert manifest["release_authorized"] is False
    assert freeze["release_authorized"] is False
    assert verify_rc0_sealed_export(export_root=export) == (manifest, freeze)


def stat_exec_bit() -> int:
    return 0o111


def test_build_rejects_dirty_repository_existing_target_and_ambiguous_rights(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    (repo / "src/tool.py").write_text("dirty\n")
    with pytest.raises(ValidationError, match="REPOSITORY-DIRTY"):
        build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=tmp_path / "dirty")
    _git(repo, "restore", "--", "src/tool.py")

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValidationError, match="TARGET-MUST-NOT-EXIST"):
        build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=existing)

    payload = json.loads(rights.read_text())
    payload["rights_groups"].append(payload["rights_groups"][0])
    rights.write_text(json.dumps(payload))
    _git(repo, "add", "rights.json")
    _git(repo, "commit", "-qm", "ambiguous rights")
    with pytest.raises(ValidationError, match="RIGHTS-MAPPING-NOT-EXACT"):
        build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=tmp_path / "ambiguous")


def test_build_rejects_included_consumer_link_to_held_artifact(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    (repo / "docs/design.md").write_text("[held](../held/later.txt)\n")
    _git(repo, "add", "docs/design.md")
    _git(repo, "commit", "-qm", "add broken public consumer edge")

    with pytest.raises(ValidationError, match="CONSUMER-CLOSURE-BLOCKED"):
        build_rc0_sealed_export(
            repo=repo,
            scope_policy_path=scope,
            rights_policy_path=rights,
            export_root=tmp_path / "consumer-blocked",
        )


def test_verify_rejects_tamper_extra_file_and_authorization_forgery(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    export = tmp_path / "export"
    build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=export)

    (export / "src/tool.py").write_text("tampered\n")
    with pytest.raises(ValidationError, match="BYTE-MISMATCH"):
        verify_rc0_sealed_export(export_root=export)
    (export / "src/tool.py").write_text("print('included')\n")

    (export / "extra.txt").write_text("extra\n")
    with pytest.raises(ValidationError, match="FILESET-MISMATCH"):
        verify_rc0_sealed_export(export_root=export)
    (export / "extra.txt").unlink()

    freeze_path = export / FREEZE_RECORD_NAME
    freeze = json.loads(freeze_path.read_text())
    freeze["release_authorized"] = True
    freeze_path.write_text(json.dumps(freeze))
    with pytest.raises(ValidationError):
        verify_rc0_sealed_export(export_root=export)


def test_verify_rejects_payload_symlink(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    export = tmp_path / "export"
    build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=export)
    payload = export / "src/tool.py"
    payload.unlink()
    payload.symlink_to(export / "docs/design.md")
    with pytest.raises(ValidationError, match="FILE-UNSAFE"):
        verify_rc0_sealed_export(export_root=export)


def test_metadata_files_are_present_and_self_contained(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    export = tmp_path / "export"
    build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=export)
    assert (export / EXPORT_MANIFEST_NAME).is_file()
    assert (export / FREEZE_RECORD_NAME).is_file()
    assert (export / RIGHTS_POLICY_NAME).is_file()


def test_verify_recomputes_rights_after_attacker_rehashes_all_records(tmp_path: Path) -> None:
    repo, scope, rights = _fixture(tmp_path)
    export = tmp_path / "export"
    build_rc0_sealed_export(repo=repo, scope_policy_path=scope, rights_policy_path=rights, export_root=export)

    manifest_path = export / EXPORT_MANIFEST_NAME
    freeze_path = export / FREEZE_RECORD_NAME
    manifest = json.loads(manifest_path.read_text())
    freeze = json.loads(freeze_path.read_text())
    target = manifest["files"][0]
    target.update(
        {
            "rights_group": "forged-group",
            "license_expression": "LicenseRef-Forged",
            "rights_state": "cleared",
            "ip_review_state": "cleared",
        }
    )
    aggregate = canonical_hash({"files": manifest["files"]})
    manifest["payload"]["aggregate_hash"] = aggregate
    manifest["export_id"] = "flowness-open-alpha-rc0-" + aggregate.removeprefix("sha256:")[:24]
    manifest_unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    manifest["manifest_hash"] = canonical_hash(manifest_unsigned)
    freeze["export_manifest_hash"] = manifest["manifest_hash"]
    freeze["payload_aggregate_hash"] = aggregate
    freeze["freeze_id"] = "rc0-" + canonical_hash(
        {"commit": freeze["source_commit"], "export_manifest_hash": manifest["manifest_hash"]}
    ).removeprefix("sha256:")[:24]
    freeze_unsigned = {key: value for key, value in freeze.items() if key != "record_hash"}
    freeze["record_hash"] = canonical_hash(freeze_unsigned)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    freeze_path.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValidationError, match="RIGHTS-BINDING-MISMATCH"):
        verify_rc0_sealed_export(export_root=export)
