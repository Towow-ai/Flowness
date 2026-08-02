from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from flowness_oss_harness.cleanroom_acceptance import (
    CleanroomError,
    _CANONICAL_TESTS,
    _acceptance_cache_evidence,
    _copy_canonical_tests,
    _write_scratch_packages,
    verify_export,
)


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "export"
    target = root / "src/example.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")
    record = {
        "path": "src/example.py",
        "sha256": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
        "bytes": target.stat().st_size,
    }
    unsigned_manifest = {
        "schema_version": "flowness-rc0-export-manifest/v1",
        "export_id": "fixture",
        "source_repository": {"commit": "a" * 40, "tree": "b" * 40},
        "payload": {"files": 1, "bytes": target.stat().st_size, "aggregate_hash": _hash({"files": [record]})},
        "files": [record],
        "release_authorized": False,
    }
    manifest = {**unsigned_manifest, "manifest_hash": _hash(unsigned_manifest)}
    unsigned_freeze = {
        "schema_version": "flowness-rc0-freeze-record/v1",
        "source_commit": "a" * 40,
        "export_manifest_hash": manifest["manifest_hash"],
        "payload_aggregate_hash": unsigned_manifest["payload"]["aggregate_hash"],
        "release_authorized": False,
    }
    freeze = {**unsigned_freeze, "record_hash": _hash(unsigned_freeze)}
    (root / "OPEN_ALPHA_EXPORT_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "RC0_FREEZE_RECORD.json").write_text(json.dumps(freeze), encoding="utf-8")
    return root


def test_verify_export_accepts_only_complete_hash_bound_tree(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    manifest, freeze = verify_export(root)
    assert manifest["export_id"] == "fixture"
    assert freeze["release_authorized"] is False


def test_verify_export_rejects_payload_tampering(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "src/example.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(CleanroomError, match="byte mismatch"):
        verify_export(root)


def test_verify_export_rejects_unmanifested_file(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / "extra.txt").write_text("not sealed\n", encoding="utf-8")
    with pytest.raises(CleanroomError, match="unmanifested"):
        verify_export(root)


def test_package_assembly_copies_sources_out_of_immutable_export(tmp_path: Path) -> None:
    export = tmp_path / "export"
    ledger = export / "public-core/flowness-ledger-core"
    harness = export / "harness/src/towow"
    oss = export / "oss/flowness-oss-harness"
    (ledger / "src/flowness_ledger_core").mkdir(parents=True)
    (ledger / "src/flowness_ledger_core/__init__.py").write_text("\n")
    (ledger / "pyproject.toml").write_text("[build-system]\n")
    harness.mkdir(parents=True)
    (harness / "__init__.py").write_text("\n")
    (oss / "src/flowness_oss_harness").mkdir(parents=True)
    (oss / "src/flowness_oss_harness/__init__.py").write_text("\n")
    (oss / "schemas").mkdir()
    (oss / "pyproject.toml").write_text("[build-system]\n")
    (export / "harness/pyproject.toml").write_text("[build-system]\n")
    (export / "harness/open-alpha-requirements.lock").write_text("pytest==9.0.3\n")
    before = sorted(path.relative_to(export).as_posix() for path in export.rglob("*") if path.is_file())

    scratch_ledger, scratch_harness, scratch_oss = _write_scratch_packages(
        export, tmp_path / "scratch"
    )

    after = sorted(path.relative_to(export).as_posix() for path in export.rglob("*") if path.is_file())
    assert before == after
    assert scratch_ledger != ledger
    assert (scratch_harness / "pyproject.toml").is_file()
    assert (scratch_harness / "open-alpha-requirements.lock").is_file()
    assert (scratch_oss / "pyproject.toml").is_file()


def test_selected_tests_run_only_from_scratch_copy(tmp_path: Path) -> None:
    export = tmp_path / "export"
    for relative in _CANONICAL_TESTS:
        path = export / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_fixture():\n    assert True\n", encoding="utf-8")
    conftest = export / "harness/tests/conftest.py"
    conftest.write_text("# exported fixture boundary\n", encoding="utf-8")
    before = sorted(path.relative_to(export).as_posix() for path in export.rglob("*") if path.is_file())

    copied = _copy_canonical_tests(export, tmp_path / "scratch")

    after = sorted(path.relative_to(export).as_posix() for path in export.rglob("*") if path.is_file())
    assert before == after
    assert len(copied) == len(_CANONICAL_TESTS)
    assert all(export not in path.parents for path in copied)
    assert (tmp_path / "scratch/canonical-tests/harness/tests/conftest.py").is_file()


def test_acceptance_cache_binds_wheel_url_hash_and_host(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "fixture-1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture-wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    report = wheelhouse / "pip-resolution-report.json"
    report.write_text(
        json.dumps(
            {
                "install": [
                    {
                        "download_info": {
                            "url": f"https://files.pythonhosted.org/packages/x/{wheel.name}",
                            "archive_info": {"hashes": {"sha256": digest}},
                        }
                    }
                ],
                "environment": {
                    "implementation_name": "cpython",
                    "implementation_version": "3.12.12",
                    "platform_machine": "arm64",
                    "platform_system": "Darwin",
                    "python_full_version": "3.12.12",
                    "sys_platform": "darwin",
                },
            }
        ),
        encoding="utf-8",
    )

    evidence = _acceptance_cache_evidence(wheelhouse, report)

    assert evidence["cache_not_part_of_export"] is True
    assert evidence["single_host_only"] is True
    assert evidence["files"] == [
        {
            "filename": wheel.name,
            "bytes": len(b"fixture-wheel"),
            "sha256": "sha256:" + digest,
            "source_url": f"https://files.pythonhosted.org/packages/x/{wheel.name}",
        }
    ]


def test_export_runner_wrapper_never_writes_bytecode_before_preverify(
    tmp_path: Path,
) -> None:
    package = tmp_path / "export/oss/flowness-oss-harness"
    tools = package / "tools"
    source = package / "src/flowness_oss_harness"
    tools.mkdir(parents=True)
    source.mkdir(parents=True)
    repository_package = Path(__file__).resolve().parents[1]
    shutil.copy2(
        repository_package / "tools/run_cleanroom_acceptance.py",
        tools / "run_cleanroom_acceptance.py",
    )
    shutil.copy2(
        repository_package / "src/flowness_oss_harness/__init__.py",
        source / "__init__.py",
    )
    shutil.copy2(
        repository_package / "src/flowness_oss_harness/cleanroom_acceptance.py",
        source / "cleanroom_acceptance.py",
    )
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)

    completed = subprocess.run(
        [sys.executable, str(tools / "run_cleanroom_acceptance.py"), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert not list((tmp_path / "export").rglob("__pycache__"))
    assert not list((tmp_path / "export").rglob("*.pyc"))
