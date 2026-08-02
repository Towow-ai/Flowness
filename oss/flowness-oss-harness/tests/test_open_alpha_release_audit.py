from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from flowness_oss_harness.integrity import canonical_hash
from flowness_oss_harness.open_alpha_release_audit import (
    _line_allowances,
    audit_open_alpha_release,
)
from flowness_oss_harness.registry import ValidationError
from flowness_oss_harness.resources import PACKAGE_ROOT


SCHEMA = PACKAGE_ROOT / "schemas" / "open-alpha-release-audit.schema.json"
ROOT = PACKAGE_ROOT.parents[1]
FIXTURE_LOCK_DEPENDENCIES = {
    f"dependency-{index:02d}": "1.0" for index in range(21)
}


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write(root: Path, relative: str, value: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, dict, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    files = {
        "oss/flowness-oss-harness/CONTRIBUTING.md": "contribute\n",
        "oss/flowness-oss-harness/CODE_OF_CONDUCT.md": "conduct\n",
        "oss/flowness-oss-harness/SECURITY.md": "security\n",
        "oss/flowness-oss-harness/LICENSE-POLICY-CANDIDATE.md": "Apache-2.0 CC-BY-4.0\n",
        "oss/flowness-oss-harness/NOTICE-CANDIDATE.md": "notice\n",
        "oss/flowness-oss-harness/THIRD_PARTY-CANDIDATE.md": "third party\n",
        "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py": "source\n",
        "oss/flowness-oss-harness/fixture-requirements.lock": "".join(
            f"{name}=={version}\n"
            for name, version in FIXTURE_LOCK_DEPENDENCIES.items()
        ),
        "public-core/flowness-ledger-core/LICENSE": "apache license fixture\n",
        "public-core/flowness-ledger-core/LICENSES/CC-BY-4.0.txt": "cc by fixture\n",
        "public-core/flowness-ledger-core/open-alpha-public-package-metadata.json": "{}\n",
        "public-core/flowness-ledger-core/sbom.cdx.json": "{}\n",
        "public-core/flowness-ledger-core/pyproject.toml": '[project]\nname="flowness-ledger-core"\nversion="1.0.0a1"\nlicense={text="Apache-2.0"}\n',
        "public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py": "source\n",
    }
    files["oss/flowness-oss-harness/schemas/open-alpha-cleanroom-receipt.schema.json"] = (
        SCHEMA.with_name("open-alpha-cleanroom-receipt.schema.json").read_text(encoding="utf-8")
    )
    records = []
    for relative, value in files.items():
        _write(root, relative, value)
        raw = value.encode()
        records.append({"path": relative, "disposition": "include", "sha256": _sha(raw), "bytes": len(raw)})
    unsigned = {
        "repository": {"head": "a" * 40, "tree": "b" * 40},
        "records": records,
        "release_authorized": False,
    }
    manifest = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    policy = {
        "schema_version": "open-alpha-release-audit-policy/v2",
        "policy_id": "fixture",
        "license_plan": {
            "state": "active_candidate_license_selection",
            "code_spdx": "Apache-2.0",
            "documentation_spdx": "CC-BY-4.0",
            "legacy_spdx": "MIT",
            "proprietary_boundary": "private surfaces excluded",
            "candidate_files": [
                "oss/flowness-oss-harness/LICENSE-POLICY-CANDIDATE.md",
                "oss/flowness-oss-harness/NOTICE-CANDIDATE.md",
                "oss/flowness-oss-harness/THIRD_PARTY-CANDIDATE.md",
                "public-core/flowness-ledger-core/LICENSE",
                "public-core/flowness-ledger-core/LICENSES/CC-BY-4.0.txt",
            ],
            "full_license_texts_present": True,
            "license_texts": [
                {"path": "public-core/flowness-ledger-core/LICENSE", "spdx": "Apache-2.0", "source_url": "https://www.apache.org/licenses/LICENSE-2.0.txt", "source_sha256": _sha(b"apache license fixture\n")},
                {"path": "public-core/flowness-ledger-core/LICENSES/CC-BY-4.0.txt", "spdx": "CC-BY-4.0", "source_url": "https://creativecommons.org/licenses/by/4.0/legalcode.txt", "source_sha256": _sha(b"cc by fixture\n")},
            ],
            "spdx_header_policy": "fixture",
        },
        "package_metadata": [
            {"path": "public-core/flowness-ledger-core/pyproject.toml", "expected_name": "flowness-ledger-core", "expected_version": "1.0.0a1", "expected_spdx": "Apache-2.0"},
        ],
        "public_package_assembly": {
            "contract_path": "public-core/flowness-ledger-core/open-alpha-public-package-metadata.json",
            "state": "portable_contract_present_harness_metadata_pending_assembly",
            "canonical_harness_pyproject": "harness/pyproject.toml",
            "canonical_harness_metadata_disposition": "hold",
            "reason": "fixture pending assembly",
            "owner_exact_export_approval": False,
        },
        "community_files": [
            "oss/flowness-oss-harness/CONTRIBUTING.md",
            "oss/flowness-oss-harness/CODE_OF_CONDUCT.md",
            "oss/flowness-oss-harness/SECURITY.md",
        ],
        "security_contact": {"state": "pending", "evidence_refs": []},
        "rights_groups": [
            {
                "group_id": "harness",
                "patterns": ["oss/flowness-oss-harness/**"],
                "license_expression": "Apache-2.0",
                "origin_class": "owner_repository_source",
                "rights_state": "owner_attestation_pending",
                "ip_review_state": "source_review_pending",
                "provenance_state": "repository_path_only_not_legal_proof",
                "evidence_refs": [],
            },
            {
                "group_id": "ledger",
                "patterns": ["public-core/flowness-ledger-core/**"],
                "license_expression": "Apache-2.0",
                "origin_class": "owner_repository_source",
                "rights_state": "owner_attestation_pending",
                "ip_review_state": "source_review_pending",
                "provenance_state": "repository_path_only_not_legal_proof",
                "evidence_refs": [],
            },
        ],
        "supply_chain": {
            "candidate_sbom_path": "public-core/flowness-ledger-core/sbom.cdx.json",
            "state": "candidate_unlocked_direct_dependencies_only",
            "unified_lock_path": "oss/flowness-oss-harness/fixture-requirements.lock",
            "final_requirement": "locked sealed build",
            "third_party_notice": "oss/flowness-oss-harness/THIRD_PARTY-CANDIDATE.md",
        },
        "cleanroom_acceptance": {
            "receipt_schema_path": "oss/flowness-oss-harness/schemas/open-alpha-cleanroom-receipt.schema.json",
            "support_matrix": [
                {
                    "python": "3.12",
                    "system": "Linux",
                    "machine": "aarch64",
                    "platform_pattern": "^Linux-.+-aarch64-with-glibc[0-9]+(?:\\.[0-9]+)*$",
                }
            ],
            "required_stage_ids": ["prepare", "canonical-e2e"],
            "required_stage_commands": [
                {
                    "stage_id": "prepare",
                    "expected_executable": "uv",
                    "required_argv_tokens": ["venv"],
                },
                {
                    "stage_id": "canonical-e2e",
                    "expected_executable": "python",
                    "required_argv_tokens": ["-m", "pytest"],
                }
            ],
            "acceptance_cache": {
                "required_wheel_count": 21,
                "source_url_pattern": "^https://files\\.pythonhosted\\.org/.+\\.whl$",
            },
        },
        "sensitive_rules": [
            {"rule_id": "openai-secret", "pattern": "\\bsk-[A-Za-z0-9_-]{20,}\\b", "flags": ""},
            {"rule_id": "email-address", "pattern": "\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b", "flags": "i"},
            {"rule_id": "owner-local-path", "pattern": "/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\\b)", "flags": ""},
            {"rule_id": "private-transcript-coordinate", "pattern": "\\b[0-9a-f]{8}-[0-9a-f-]{27,}\\.jsonl\\b", "flags": "i"},
            {"rule_id": "owner-verbatim-marker", "pattern": "(?:Nature|owner|用户)\\s*(?:原话|澄清)\\s*[:：]?\\s*[\\\"“『]", "flags": "i"},
        ],
        "line_allowances": [],
        "owner_authorization": {"state": "not_present", "evidence_refs": []},
    }
    policy_path = root / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return root, manifest, policy_path


def _bind_owner_rights_attestation(
    root: Path,
    manifest: dict,
    policy_path: Path,
    *,
    include_evidence: bool = True,
) -> tuple[dict, str, str]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    group_ids = [item["group_id"] for item in policy["rights_groups"]]
    unsigned = {
        "schema_version": "open-alpha-owner-rights-attestation/v1",
        "attestation_id": "fixture-owner-rights-attestation",
        "attested_on": "2026-08-02",
        "owner_role": "repository_owner",
        "scope": {
            "policy_path": "oss/flowness-oss-harness/config/open-alpha-release-audit.json",
            "covered_rights_group_ids": group_ids,
        },
        "rights_review_state": "owner_attested",
        "ip_review_state": "owner_attested",
        "attestations": [
            {
                "statement_id": "distribution-rights",
                "statement": "Fixture owner rights statement for the exact named scope.",
            },
            {
                "statement_id": "source-ip-review",
                "statement": "Fixture owner source and IP review statement.",
            },
        ],
        "cryptographic_signature": {"present": False},
        "publication_authorization": False,
        "boundary": "Fixture only; not a signature or publication authorization.",
    }
    attestation = {**unsigned, "attestation_hash": canonical_hash(unsigned)}
    attestation_path = (
        "oss/flowness-oss-harness/registries/"
        "open-alpha-owner-rights-attestation-v1.json"
    )
    schema_path = (
        "oss/flowness-oss-harness/schemas/"
        "open-alpha-owner-rights-attestation.schema.json"
    )
    raw = (json.dumps(attestation, indent=2) + "\n").encode("utf-8")
    _write(root, attestation_path, raw.decode("utf-8"))
    schema_raw = PACKAGE_ROOT.joinpath(
        "schemas/open-alpha-owner-rights-attestation.schema.json"
    ).read_bytes()
    schema_target = root / schema_path
    schema_target.parent.mkdir(parents=True, exist_ok=True)
    schema_target.write_bytes(schema_raw)
    if include_evidence:
        manifest["records"].extend(
            [
                {
                    "path": attestation_path,
                    "disposition": "include",
                    "sha256": _sha(raw),
                    "bytes": len(raw),
                },
                {
                    "path": schema_path,
                    "disposition": "include",
                    "sha256": _sha(schema_raw),
                    "bytes": len(schema_raw),
                },
            ]
        )
    evidence_ref = {
        "evidence_type": "owner_rights_attestation",
        "path": attestation_path,
        "sha256": _sha(raw),
    }
    for group in policy["rights_groups"]:
        group["rights_state"] = "cleared"
        group["ip_review_state"] = "cleared"
        group["evidence_refs"] = [copy.deepcopy(evidence_ref)]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    return evidence_ref, attestation_path, schema_path


def test_current_candidate_passes_community_files_but_keeps_release_blocked(tmp_path: Path) -> None:
    root, manifest, policy = _fixture(tmp_path)
    report = audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy, schema_path=SCHEMA)
    blockers = {item["blocker_id"] for item in report["blockers"]}
    checks = {item["check_id"]: item["state"] for item in report["checks"]}

    assert checks["community-files"] == "pass"
    assert checks["secret-pii-sensitive-content"] == "pass"
    assert checks["license-and-package-metadata"] == "pass"
    assert checks["path-level-spdx-mapping"] == "pass"
    assert checks["alpha-independent-cleanroom"] == "unknown"
    assert checks["beta-cross-platform-offline-wheelhouse"] == "unknown"
    assert {"OA-PACKAGE-METADATA-001", "OA-RIGHTS-001", "OA-IP-001", "OA-OWNER-001"} <= blockers
    assert "OA-CLEANROOM-001" in blockers
    assert "OA-OFFLINE-WHEELHOUSE-001" not in blockers
    assert report["release_ready"] is False
    assert report["owner_authorized"] is False
    assert report["report_hash"] == canonical_hash({key: value for key, value in report.items() if key != "report_hash"})


def _acceptance_cache_fixture(tmp_path: Path) -> tuple[dict, Path, Path]:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    files = []
    installs = []
    for index, (name, version) in enumerate(FIXTURE_LOCK_DEPENDENCIES.items()):
        filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        raw = f"fixture-wheel-{index}\n".encode()
        wheel = wheelhouse / filename
        wheel.write_bytes(raw)
        url = f"https://files.pythonhosted.org/packages/fixture/{filename}"
        digest = hashlib.sha256(raw).hexdigest()
        files.append(
            {
                "filename": filename,
                "bytes": len(raw),
                "sha256": "sha256:" + digest,
                "source_url": url,
            }
        )
        installs.append(
            {
                "download_info": {
                    "url": url,
                    "archive_info": {"hashes": {"sha256": digest}},
                },
                "metadata": {"name": name, "version": version},
            }
        )
    dependency_report = tmp_path / "pip-resolution-report.json"
    report_raw = json.dumps(
        {"version": "1", "install": installs, "environment": {}},
        sort_keys=True,
    ).encode()
    dependency_report.write_bytes(report_raw)
    cache = {
        "cache_not_part_of_export": True,
        "single_host_only": True,
        "sealed_cross_platform_wheelhouse": False,
        "resolver_report_sha256": _sha(report_raw),
        "host_matrix": {
            "implementation_name": "cpython",
            "implementation_version": "3.12.12",
            "platform_machine": "aarch64",
            "platform_system": "Linux",
            "python_full_version": "3.12.12",
            "sys_platform": "linux",
        },
        "files": files,
    }
    return cache, wheelhouse, dependency_report


def test_cleanroom_receipt_must_bind_exact_scope_support_isolation_and_e2e(tmp_path: Path) -> None:
    root, manifest, policy = _fixture(tmp_path)
    acceptance_cache, wheelhouse, dependency_report = _acceptance_cache_fixture(tmp_path)
    export_files = [
        {"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
        for item in manifest["records"]
    ]
    aggregate = canonical_hash({"files": export_files})
    export_unsigned = {
        "schema_version": "flowness-rc0-export-manifest/v1",
        "export_id": "flowness-open-alpha-rc0-fixture",
        "source_repository": {
            "commit": manifest["repository"]["head"],
            "tree": manifest["repository"]["tree"],
        },
        "scope": {"manifest_hash": manifest["manifest_hash"]},
        "payload": {
            "files": len(export_files),
            "bytes": sum(item["bytes"] for item in export_files),
            "aggregate_hash": aggregate,
        },
        "files": export_files,
        "release_authorized": False,
    }
    export_manifest = {**export_unsigned, "manifest_hash": canonical_hash(export_unsigned)}
    export_manifest_path = tmp_path / "OPEN_ALPHA_EXPORT_MANIFEST.json"
    export_manifest_path.write_text(json.dumps(export_manifest), encoding="utf-8")
    unsigned = {
        "schema_version": "flowness-open-alpha-cleanroom-receipt/v1",
        "receipt_id": "cleanroom-fixture",
        "state": "pass",
        "source": {
            "export_id": export_manifest["export_id"],
            "export_manifest_hash": export_manifest["manifest_hash"],
            "payload_aggregate_hash": aggregate,
            "files": len(export_files),
            "bytes": sum(item["bytes"] for item in export_files),
            "source_commit": manifest["repository"]["head"],
        },
        "environment": {
            "fresh_venv": True,
            "existing_venv_inherited": False,
            "pythonpath_inherited": False,
            "home_isolated": True,
            "source_repository_referenced": False,
            "network_policy": "disabled_uv_offline_and_pip_no_index",
            "dependency_source": "external_host_acceptance_wheelhouse",
            "cache_not_part_of_export": True,
            "single_host_only": True,
            "python_version": "3.12.12",
            "platform": "Linux-6.8.0-90-generic-aarch64-with-glibc2.39",
            "support_coordinate": {
                "python": "3.12",
                "system": "Linux",
                "machine": "aarch64",
            },
            "acceptance_cache": acceptance_cache,
        },
        "dependencies": [
            {"name": name, "version": version}
            for name, version in {
                **FIXTURE_LOCK_DEPENDENCIES,
                "flowness-ledger-core": "1.0.0a1",
            }.items()
        ],
        "stages": [
            {
                "stage_id": "prepare",
                "state": "pass",
                "exit_code": 0,
                "command": ["uv", "venv"],
                "duration_ms": 1,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "stdout_sha256": _sha(b""),
                "stderr_sha256": _sha(b""),
            },
            {
                "stage_id": "canonical-e2e",
                "state": "pass",
                "exit_code": 0,
                "command": ["python", "-m", "pytest"],
                "duration_ms": 1,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "stdout_sha256": _sha(b""),
                "stderr_sha256": _sha(b""),
            }
        ],
        "blockers": [],
        "release_ready": False,
        "boundary": "fixture independent clean-room receipt",
    }
    receipt = {**unsigned, "receipt_hash": canonical_hash(unsigned)}
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = audit_open_alpha_release(
        repo=root,
        package_manifest=manifest,
        policy_path=policy,
        schema_path=SCHEMA,
        cleanroom_receipt_path=receipt_path,
        cleanroom_export_manifest_path=export_manifest_path,
        cleanroom_wheelhouse_path=wheelhouse,
        cleanroom_dependency_report_path=dependency_report,
    )
    checks = {item["check_id"]: item["state"] for item in report["checks"]}
    assert checks["alpha-independent-cleanroom"] == "pass"
    assert "OA-CLEANROOM-001" not in {item["blocker_id"] for item in report["blockers"]}

    pristine = copy.deepcopy(receipt)

    def assert_rehashed_receipt_rejected(candidate: dict) -> None:
        candidate["receipt_hash"] = canonical_hash(
            {key: value for key, value in candidate.items() if key != "receipt_hash"}
        )
        receipt_path.write_text(json.dumps(candidate), encoding="utf-8")
        rejected = audit_open_alpha_release(
            repo=root,
            package_manifest=manifest,
            policy_path=policy,
            schema_path=SCHEMA,
            cleanroom_receipt_path=receipt_path,
            cleanroom_export_manifest_path=export_manifest_path,
            cleanroom_wheelhouse_path=wheelhouse,
            cleanroom_dependency_report_path=dependency_report,
        )
        assert "OA-CLEANROOM-001" in {
            item["blocker_id"] for item in rejected["blockers"]
        }

    forged = copy.deepcopy(pristine)
    forged["source"]["export_id"] = "flowness-open-alpha-rc0-other"
    assert_rehashed_receipt_rejected(forged)

    forged = copy.deepcopy(pristine)
    forged["stages"][0]["exit_code"] = 1
    assert_rehashed_receipt_rejected(forged)
    forged = copy.deepcopy(pristine)
    forged["stages"][0]["command"] = ["false"]
    assert_rehashed_receipt_rejected(forged)
    for forged_stages in (
        list(reversed(pristine["stages"])),
        pristine["stages"][:-1],
        pristine["stages"] + [copy.deepcopy(pristine["stages"][-1])],
    ):
        forged = copy.deepcopy(pristine)
        forged["stages"] = copy.deepcopy(forged_stages)
        assert_rehashed_receipt_rejected(forged)

    forged = copy.deepcopy(pristine)
    forged["dependencies"] = forged["dependencies"][:-1]
    assert_rehashed_receipt_rejected(forged)

    forged = copy.deepcopy(pristine)
    forged["environment"]["acceptance_cache"]["host_matrix"][
        "platform_machine"
    ] = "x86_64"
    assert_rehashed_receipt_rejected(forged)

    forged = copy.deepcopy(pristine)
    forged["environment"]["acceptance_cache"]["files"][0]["sha256"] = (
        "sha256:" + "0" * 64
    )
    forged["environment"]["acceptance_cache"]["files"][0]["source_url"] = (
        "https://example.invalid/forged.whl"
    )
    assert_rehashed_receipt_rejected(forged)

    forged = copy.deepcopy(pristine)
    forged["environment"]["acceptance_cache"] = None
    forged["environment"]["dependency_source"] = "new_empty_cache"
    forged["environment"]["cache_not_part_of_export"] = False
    forged["environment"]["single_host_only"] = False
    assert_rehashed_receipt_rejected(forged)

    pristine["receipt_hash"] = canonical_hash(
        {key: value for key, value in pristine.items() if key != "receipt_hash"}
    )
    receipt_path.write_text(json.dumps(pristine), encoding="utf-8")
    missing_artifacts = audit_open_alpha_release(
        repo=root,
        package_manifest=manifest,
        policy_path=policy,
        schema_path=SCHEMA,
        cleanroom_receipt_path=receipt_path,
        cleanroom_export_manifest_path=export_manifest_path,
    )
    assert "OA-CLEANROOM-001" in {
        item["blocker_id"] for item in missing_artifacts["blockers"]
    }

    forged_export = copy.deepcopy(export_manifest)
    forged_export["source_repository"]["tree"] = "c" * 40
    forged_export["manifest_hash"] = canonical_hash(
        {
            key: value
            for key, value in forged_export.items()
            if key != "manifest_hash"
        }
    )
    export_manifest_path.write_text(json.dumps(forged_export), encoding="utf-8")
    forged = copy.deepcopy(pristine)
    forged["source"]["export_manifest_hash"] = forged_export["manifest_hash"]
    assert_rehashed_receipt_rejected(forged)
    export_manifest_path.write_text(json.dumps(export_manifest), encoding="utf-8")

    first_wheel = next(wheelhouse.glob("*.whl"))
    original_wheel = first_wheel.read_bytes()
    first_wheel.write_bytes(original_wheel + b"tampered")
    assert_rehashed_receipt_rejected(copy.deepcopy(pristine))
    first_wheel.write_bytes(original_wheel)


def test_sensitive_finding_is_hashed_not_echoed(tmp_path: Path) -> None:
    root, manifest, policy = _fixture(tmp_path)
    target = root / "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py"
    secret = "sk-this_value_must_never_appear_in_the_report"
    target.write_text(secret + "\n", encoding="utf-8")
    for record in manifest["records"]:
        if record["path"].endswith("controller.py"):
            record["sha256"] = _sha((secret + "\n").encode())
            record["bytes"] = len((secret + "\n").encode())
    manifest["manifest_hash"] = canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})

    report = audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy, schema_path=SCHEMA)
    rendered = json.dumps(report)

    assert report["sensitive_findings"][0]["rule_id"] == "openai-secret"
    assert secret not in rendered
    assert "OA-SENSITIVE-001" in {item["blocker_id"] for item in report["blockers"]}


@pytest.mark.parametrize(
    ("sensitive_value", "rule_id"),
    [
        ("sk-" + "productiontokenvalue0123456789", "openai-secret"),
        ("owner" + "@company.example", "email-address"),
        ("/Users/" + "sample-user/private-project", "owner-local-path"),
        ("/home/" + "another-user/private-project", "owner-local-path"),
        (
            "62abf2f9" + "-290d-48a2-8e5e-39bdcc5f6f60.jsonl",
            "private-transcript-coordinate",
        ),
        ("Nature " + "原话：" + "“private wording”", "owner-verbatim-marker"),
    ],
)
def test_sensitive_values_in_production_source_still_fail(
    tmp_path: Path, sensitive_value: str, rule_id: str
) -> None:
    root, manifest, policy = _fixture(tmp_path)
    target = root / "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py"
    raw = (sensitive_value + "\n").encode()
    target.write_bytes(raw)
    for record in manifest["records"]:
        if record["path"].endswith("controller.py"):
            record["sha256"] = _sha(raw)
            record["bytes"] = len(raw)
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )

    report = audit_open_alpha_release(
        repo=root, package_manifest=manifest, policy_path=policy, schema_path=SCHEMA
    )

    assert rule_id in {finding["rule_id"] for finding in report["sensitive_findings"]}
    assert "OA-SENSITIVE-001" in {item["blocker_id"] for item in report["blockers"]}


def test_fixture_allowance_is_exact_and_cannot_cover_production_source(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    test_path = "oss/flowness-oss-harness/tests/test_sensitive_fixture.py"
    sensitive_line = 'OWNER_FIXTURE = "' + "/Users/" + 'sample-user/private-project"'
    _write(root, test_path, sensitive_line + "\n")
    raw = (sensitive_line + "\n").encode()
    manifest["records"].append(
        {"path": test_path, "disposition": "include", "sha256": _sha(raw), "bytes": len(raw)}
    )
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    policy = json.loads(policy_path.read_text())
    policy["line_allowances"] = [
        {
            "path": test_path,
            "rule_id": "owner-local-path",
            "line_sha256": _sha(sensitive_line.encode()),
            "reason": "Exact negative fixture for local-path detection.",
        }
    ]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    allowed = audit_open_alpha_release(
        repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA
    )
    assert not allowed["sensitive_findings"]

    production_path = root / "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py"
    production_path.write_bytes(raw)
    for record in manifest["records"]:
        if record["path"].endswith("controller.py"):
            record["sha256"] = _sha(raw)
            record["bytes"] = len(raw)
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    report = audit_open_alpha_release(
        repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA
    )
    assert {finding["path"] for finding in report["sensitive_findings"]} == {
        "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py"
    }


def test_synthetic_git_email_allowance_is_byte_exact(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    test_path = "oss/flowness-oss-harness/tests/test_git_fixture.py"
    fixture_line = '    _git(repo, "config", "user.email", "test@example.invalid")'
    _write(root, test_path, fixture_line + "\n")
    raw = (fixture_line + "\n").encode()
    manifest["records"].append(
        {"path": test_path, "disposition": "include", "sha256": _sha(raw), "bytes": len(raw)}
    )
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    policy = json.loads(policy_path.read_text())
    policy["line_allowances"] = [
        {
            "path": test_path,
            "rule_id": "email-address",
            "line_sha256": _sha(fixture_line.encode()),
            "reason": "Synthetic Git identity in an isolated repository fixture.",
        }
    ]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    allowed = audit_open_alpha_release(
        repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA
    )
    assert not allowed["sensitive_findings"]

    changed_line = fixture_line.replace("test@example.invalid", "other@example.invalid")
    changed = (changed_line + "\n").encode()
    _write(root, test_path, changed_line + "\n")
    manifest["records"][-1].update({"sha256": _sha(changed), "bytes": len(changed)})
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    rejected = audit_open_alpha_release(
        repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA
    )
    assert {item["rule_id"] for item in rejected["sensitive_findings"]} == {
        "email-address", "stale-sensitive-allowance"
    }


def test_production_source_cannot_receive_sensitive_allowance(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    policy = json.loads(policy_path.read_text())
    policy["line_allowances"] = [
        {
            "path": "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py",
            "rule_id": "owner-local-path",
            "line_sha256": _sha(b"source"),
            "reason": "Invalid attempt to exempt production source.",
        }
    ]
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValidationError, match="ALLOWANCE-NOT-FIXTURE"):
        audit_open_alpha_release(
            repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA
        )


def test_policy_cannot_self_authorize_or_clear_rights_without_evidence(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    payload = json.loads(policy_path.read_text())
    payload["owner_authorization"] = {"state": "authorized", "evidence_refs": ["owner.json"]}
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="CANNOT-SELF-AUTHORIZE"):
        audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA)

    payload["owner_authorization"] = {"state": "not_present", "evidence_refs": []}
    payload["rights_groups"][0]["rights_state"] = "cleared"
    payload["rights_groups"][0]["evidence_refs"] = []
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="RIGHTS-CLEAR-WITHOUT-EVIDENCE"):
        audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA)


def test_cleared_rights_bind_included_self_hashed_owner_attestation(
    tmp_path: Path,
) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    evidence_ref, _, _ = _bind_owner_rights_attestation(
        root, manifest, policy_path
    )

    report = audit_open_alpha_release(
        repo=root,
        package_manifest=manifest,
        policy_path=policy_path,
        schema_path=SCHEMA,
    )
    checks = {item["check_id"]: item["state"] for item in report["checks"]}
    blockers = {item["blocker_id"] for item in report["blockers"]}

    assert checks["file-origin-and-rights"] == "pass"
    assert checks["ip-and-source-review"] == "pass"
    assert "OA-RIGHTS-001" not in blockers
    assert "OA-IP-001" not in blockers
    assert all(item["evidence_refs"] == [evidence_ref] for item in report["rights_groups"])
    assert report["owner_authorized"] is False
    assert "OA-OWNER-001" in blockers


def test_opaque_rights_evidence_ref_cannot_clear_a_group(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["rights_groups"][0]["rights_state"] = "cleared"
    payload["rights_groups"][0]["ip_review_state"] = "cleared"
    payload["rights_groups"][0]["evidence_refs"] = [
        "owner-attestation:opaque-jury-pass"
    ]
    policy_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="RIGHTS-EVIDENCE-REF-INVALID"):
        audit_open_alpha_release(
            repo=root,
            package_manifest=manifest,
            policy_path=policy_path,
            schema_path=SCHEMA,
        )


def test_rights_evidence_outside_included_scope_cannot_clear_groups(
    tmp_path: Path,
) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    _bind_owner_rights_attestation(
        root, manifest, policy_path, include_evidence=False
    )

    with pytest.raises(ValidationError, match="RIGHTS-EVIDENCE-OUTSIDE-SCOPE"):
        audit_open_alpha_release(
            repo=root,
            package_manifest=manifest,
            policy_path=policy_path,
            schema_path=SCHEMA,
        )


def test_rights_evidence_policy_digest_drift_fails_closed(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    _bind_owner_rights_attestation(root, manifest, policy_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["rights_groups"][0]["evidence_refs"][0]["sha256"] = (
        "sha256:" + "0" * 64
    )
    policy_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="RIGHTS-EVIDENCE-HASH-DRIFT"):
        audit_open_alpha_release(
            repo=root,
            package_manifest=manifest,
            policy_path=policy_path,
            schema_path=SCHEMA,
        )


def test_rehashed_file_with_invalid_attestation_self_hash_fails_closed(
    tmp_path: Path,
) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    _, attestation_path, _ = _bind_owner_rights_attestation(
        root, manifest, policy_path
    )
    path = root / attestation_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["boundary"] = "Tampered after attestation."
    raw = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    for record in manifest["records"]:
        if record["path"] == attestation_path:
            record.update({"sha256": _sha(raw), "bytes": len(raw)})
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    for group in policy["rights_groups"]:
        group["evidence_refs"][0]["sha256"] = _sha(raw)
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValidationError, match="invalid immutable attestation_hash"):
        audit_open_alpha_release(
            repo=root,
            package_manifest=manifest,
            policy_path=policy_path,
            schema_path=SCHEMA,
        )


def test_byte_drift_and_overlapping_rights_groups_fail_closed(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    target = root / "oss/flowness-oss-harness/CONTRIBUTING.md"
    target.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="MANIFEST-BYTE-DRIFT"):
        audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA)

    target.write_text("contribute\n", encoding="utf-8")
    payload = json.loads(policy_path.read_text())
    payload["rights_groups"].append(copy.deepcopy(payload["rights_groups"][0]))
    payload["rights_groups"][-1]["group_id"] = "overlap"
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="RIGHTS-GROUP-EMPTY"):
        audit_open_alpha_release(repo=root, package_manifest=manifest, policy_path=policy_path, schema_path=SCHEMA)


def test_byte_identical_assets_cannot_receive_conflicting_licenses(tmp_path: Path) -> None:
    root, manifest, policy_path = _fixture(tmp_path)
    payload = json.loads(policy_path.read_text())
    payload["rights_groups"][1]["license_expression"] = "CC-BY-4.0"
    policy_path.write_text(json.dumps(payload), encoding="utf-8")

    report = audit_open_alpha_release(
        repo=root,
        package_manifest=manifest,
        policy_path=policy_path,
        schema_path=SCHEMA,
    )
    checks = {item["check_id"]: item["state"] for item in report["checks"]}
    blockers = {item["blocker_id"] for item in report["blockers"]}

    assert checks["byte-identical-license-consistency"] == "fail"
    assert "OA-LICENSE-DUPLICATE-001" in blockers


def test_identical_unified_sbom_paths_share_apache_mapping() -> None:
    policy = json.loads(
        (PACKAGE_ROOT / "config/open-alpha-release-audit.json").read_text(
            encoding="utf-8"
        )
    )
    paths = {
        "harness/sbom.cdx.json",
        "public-core/flowness-ledger-core/sbom.cdx.json",
    }
    mappings: dict[str, tuple[str, str]] = {}
    for path in paths:
        matches = [
            group
            for group in policy["rights_groups"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in group["patterns"])
        ]
        assert len(matches) == 1, path
        mappings[path] = (
            matches[0]["group_id"],
            matches[0]["license_expression"],
        )

    assert {license_expression for _, license_expression in mappings.values()} == {
        "Apache-2.0"
    }
    assert {group_id for group_id, _ in mappings.values()} == {
        "ledger-generated-machine-readable-assets"
    }


def test_root_release_layout_has_one_file_exact_rights_mapping() -> None:
    policy = json.loads(
        (PACKAGE_ROOT / "config/open-alpha-release-audit.json").read_text(encoding="utf-8")
    )
    expected = {
        "README.md": "CC-BY-4.0",
        "LICENSE": "NOASSERTION",
        "NOTICE": "CC-BY-4.0",
        "CONTRIBUTING.md": "CC-BY-4.0",
        "SECURITY.md": "CC-BY-4.0",
        "CODE_OF_CONDUCT.md": "CC-BY-4.0",
        "MIGRATION.md": "CC-BY-4.0",
        ".github/workflows/ci.yml": "Apache-2.0",
    }

    for path, license_expression in expected.items():
        matches = [
            group
            for group in policy["rights_groups"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in group["patterns"])
        ]
        assert len(matches) == 1
        assert matches[0]["license_expression"] == license_expression

    assert {"CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md"} <= set(
        policy["community_files"]
    )
    assert policy["license_plan"]["candidate_files"] == [
        "LICENSE",
        "LICENSE-MATRIX.md",
        "NOTICE",
        "harness/THIRD_PARTY.md",
    ]


def test_current_policy_allowances_fit_exact_successor_include_set() -> None:
    audit_policy = json.loads(
        (PACKAGE_ROOT / "config/open-alpha-release-audit.json").read_text(encoding="utf-8")
    )
    scope_policy = json.loads(
        (PACKAGE_ROOT / "config/open-alpha-package-scope.json").read_text(encoding="utf-8")
    )
    tracked = set(
        subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--", *scope_policy["scope_roots"]],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    ) - set(scope_policy["ignored_generated_paths"])
    included_paths = {
        path
        for path in tracked
        if next(
            rule
            for rule in scope_policy["rules"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["patterns"])
        )["disposition"]
        == "include"
    }

    allowances = _line_allowances(audit_policy, included_paths)

    assert allowances
    assert {item["path"] for item in audit_policy["line_allowances"]} <= included_paths
