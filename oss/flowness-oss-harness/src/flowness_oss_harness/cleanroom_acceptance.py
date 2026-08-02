from __future__ import annotations

"""Offline clean-room acceptance for a frozen Flowness Open Alpha export.

The runner intentionally has no dependency on the source repository or on the
installed Flowness packages.  It consumes only an RC0 export, copies the three
reviewed public packages into scratch space, creates a fresh Python environment
and records content-addressed observations.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import unquote, urlparse


EXPORT_MANIFEST = "OPEN_ALPHA_EXPORT_MANIFEST.json"
FREEZE_RECORD = "RC0_FREEZE_RECORD.json"
SCHEMA_VERSION = "flowness-open-alpha-cleanroom-receipt/v1"
DEPENDENCY_BLOCKER = "OA-CLEANROOM-OFFLINE-DEPS-001"
EXECUTION_BLOCKER = "OA-CLEANROOM-E2E-001"

_CANONICAL_TESTS = (
    "harness/tests/unit/l0/test_event_log.py",
    "harness/tests/unit/l0/test_envelope_checks.py",
    "harness/tests/unit/l0/test_commit_gate_completeness.py",
    "harness/tests/unit/l0/test_projection_batch_coalesce.py",
    "harness/tests/unit/l1/test_review_aggregation.py",
    "harness/tests/unit/l2/test_portable_runtime.py",
    "harness/tests/unit/l2/test_reflow_commit_gate.py",
    "harness/tests/unit/l2/test_reflow_sentinel_reconciliation_v2.py",
)


class CleanroomError(RuntimeError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _verify_self_hash(payload: dict[str, Any], field: str) -> None:
    claimed = payload.get(field)
    unsigned = {key: value for key, value in payload.items() if key != field}
    if claimed != _hash_bytes(_canonical_json(unsigned)):
        raise CleanroomError(f"invalid {field}")


def verify_export(export_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the complete export boundary without importing project code."""

    if export_root.is_symlink() or not export_root.is_dir():
        raise CleanroomError("export root must be a non-symlink directory")
    root = export_root.resolve(strict=True)
    manifest_path = root / EXPORT_MANIFEST
    freeze_path = root / FREEZE_RECORD
    for path in (manifest_path, freeze_path):
        if path.is_symlink() or not path.is_file():
            raise CleanroomError(f"missing safe metadata file: {path.name}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanroomError("export metadata must be UTF-8 JSON") from exc
    if manifest.get("schema_version") != "flowness-rc0-export-manifest/v1":
        raise CleanroomError("unsupported export manifest schema")
    if freeze.get("schema_version") != "flowness-rc0-freeze-record/v1":
        raise CleanroomError("unsupported freeze record schema")
    _verify_self_hash(manifest, "manifest_hash")
    _verify_self_hash(freeze, "record_hash")
    if manifest.get("release_authorized") is not False:
        raise CleanroomError("clean-room input cannot be release-authorized")
    if freeze.get("release_authorized") is not False:
        raise CleanroomError("freeze record cannot authorize release")
    if freeze.get("export_manifest_hash") != manifest["manifest_hash"]:
        raise CleanroomError("freeze record does not bind export manifest")

    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise CleanroomError("export manifest has no files")
    expected = {EXPORT_MANIFEST, FREEZE_RECORD}
    rights = manifest.get("rights_policy")
    if isinstance(rights, dict) and isinstance(rights.get("metadata_path"), str):
        rights_relative = rights["metadata_path"]
        rights_pure = PurePosixPath(rights_relative)
        if rights_pure.is_absolute() or ".." in rights_pure.parts:
            raise CleanroomError("unsafe rights policy metadata path")
        rights_path = root.joinpath(*rights_pure.parts)
        if rights_path.is_symlink() or not rights_path.is_file():
            raise CleanroomError("missing sealed rights policy metadata")
        if _hash_file(rights_path) != rights.get("sha256"):
            raise CleanroomError("sealed rights policy byte mismatch")
        expected.add(rights_relative)
    paths: list[str] = []
    for record in records:
        relative = record.get("path") if isinstance(record, dict) else None
        if not isinstance(relative, str):
            raise CleanroomError("manifest record path is invalid")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in expected:
            raise CleanroomError(f"unsafe export path: {relative}")
        path = root.joinpath(*pure.parts)
        if path.is_symlink() or not path.is_file():
            raise CleanroomError(f"missing safe export file: {relative}")
        try:
            path.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise CleanroomError(f"export path escaped root: {relative}") from exc
        if _hash_file(path) != record.get("sha256"):
            raise CleanroomError(f"export byte mismatch: {relative}")
        if path.stat().st_size != record.get("bytes"):
            raise CleanroomError(f"export size mismatch: {relative}")
        expected.add(relative)
        paths.append(relative)
    if len(paths) != len(set(paths)):
        raise CleanroomError("duplicate export path")
    observed = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() or item.is_symlink()
    }
    if observed != expected:
        raise CleanroomError("export contains unmanifested or missing files")
    if manifest["payload"].get("aggregate_hash") != _hash_bytes(
        _canonical_json({"files": records})
    ):
        raise CleanroomError("payload aggregate hash mismatch")
    if freeze.get("payload_aggregate_hash") != manifest["payload"]["aggregate_hash"]:
        raise CleanroomError("freeze payload hash mismatch")
    return manifest, freeze


def _write_scratch_packages(
    export_root: Path, scratch: Path
) -> tuple[Path, Path, Path]:
    ledger = scratch / "flowness-ledger-core"
    shutil.copytree(export_root / "public-core/flowness-ledger-core", ledger)
    harness = scratch / "flowness-harness"
    shutil.copytree(export_root / "harness", harness)
    oss_harness = scratch / "flowness-oss-harness"
    shutil.copytree(export_root / "oss/flowness-oss-harness", oss_harness)
    for package in (ledger, harness, oss_harness):
        metadata = package / "pyproject.toml"
        if metadata.is_symlink() or not metadata.is_file():
            raise CleanroomError(
                f"reviewed public package metadata is missing: {package.name}/pyproject.toml"
            )
    dependency_lock = harness / "open-alpha-requirements.lock"
    if dependency_lock.is_symlink() or not dependency_lock.is_file():
        raise CleanroomError("reviewed Open Alpha dependency lock is missing")
    return ledger, harness, oss_harness


def _copy_canonical_tests(export_root: Path, scratch: Path) -> list[Path]:
    """Copy selected tests and any exported ancestor conftests out of RC0."""

    test_root = scratch / "canonical-tests"
    selected: list[Path] = []
    conftests: set[Path] = set()
    for relative in _CANONICAL_TESTS:
        source = export_root / relative
        if not source.is_file() or source.is_symlink():
            raise CleanroomError(f"selected canonical test is missing: {relative}")
        destination = test_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        selected.append(destination)
        parent = source.parent
        boundary = export_root / "harness/tests"
        while parent == boundary or boundary in parent.parents:
            candidate = parent / "conftest.py"
            if candidate.is_file() and not candidate.is_symlink():
                conftests.add(candidate)
            if parent == boundary:
                break
            parent = parent.parent
    for source in conftests:
        relative = source.relative_to(export_root)
        destination = test_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return selected


def _safe_env(clean_root: Path, uv_cache: Path | None) -> dict[str, str]:
    home = clean_root / "home"
    tmp = clean_root / "tmp"
    cache = clean_root / "uv-cache"
    home.mkdir()
    tmp.mkdir()
    cache.mkdir()
    keep = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}}
    keep.update(
        {
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "UV_OFFLINE": "1",
            "UV_NO_CONFIG": "1",
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    keep.pop("PYTHONPATH", None)
    keep.pop("VIRTUAL_ENV", None)
    keep["UV_CACHE_DIR"] = str(uv_cache.resolve()) if uv_cache else str(cache)
    return keep


def _acceptance_cache_evidence(
    wheelhouse: Path, dependency_report: Path
) -> dict[str, Any]:
    """Bind a host-specific external wheel cache to its resolver URLs/hashes."""

    root = wheelhouse.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise CleanroomError("acceptance wheelhouse must be a non-symlink directory")
    report_path = dependency_report.resolve(strict=True)
    if report_path.is_symlink() or not report_path.is_file():
        raise CleanroomError("dependency report must be a non-symlink file")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanroomError("dependency report must be UTF-8 JSON") from exc
    installs = report.get("install")
    environment = report.get("environment")
    if not isinstance(installs, list) or not isinstance(environment, dict):
        raise CleanroomError("dependency report is missing install/environment evidence")

    files: list[dict[str, Any]] = []
    expected_names: set[str] = set()
    for item in installs:
        download = item.get("download_info") if isinstance(item, dict) else None
        archive = download.get("archive_info") if isinstance(download, dict) else None
        hashes = archive.get("hashes") if isinstance(archive, dict) else None
        url = download.get("url") if isinstance(download, dict) else None
        expected_sha = hashes.get("sha256") if isinstance(hashes, dict) else None
        if not isinstance(url, str) or not isinstance(expected_sha, str):
            raise CleanroomError("dependency report entry lacks URL or sha256")
        filename = unquote(Path(urlparse(url).path).name)
        if not filename.endswith(".whl") or filename in expected_names:
            raise CleanroomError("dependency report has an unsafe or duplicate wheel filename")
        wheel = root / filename
        if wheel.is_symlink() or not wheel.is_file():
            raise CleanroomError(f"acceptance wheel is missing: {filename}")
        observed_sha = _hash_file(wheel).removeprefix("sha256:")
        if observed_sha != expected_sha:
            raise CleanroomError(f"acceptance wheel hash mismatch: {filename}")
        expected_names.add(filename)
        files.append(
            {
                "filename": filename,
                "bytes": wheel.stat().st_size,
                "sha256": "sha256:" + observed_sha,
                "source_url": url,
            }
        )
    observed_names = {path.name for path in root.glob("*.whl") if path.is_file()}
    if observed_names != expected_names:
        raise CleanroomError("acceptance wheelhouse and resolver report differ")
    host_keys = (
        "implementation_name",
        "implementation_version",
        "platform_machine",
        "platform_system",
        "python_full_version",
        "sys_platform",
    )
    return {
        "cache_not_part_of_export": True,
        "single_host_only": True,
        "sealed_cross_platform_wheelhouse": False,
        "resolver_report_sha256": _hash_file(report_path),
        "host_matrix": {key: environment.get(key) for key in host_keys},
        "files": sorted(files, key=lambda item: item["filename"]),
    }


def _run_stage(
    stage_id: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        list(command), cwd=cwd, env=env, capture_output=True, check=False
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    return {
        "stage_id": stage_id,
        "state": "pass" if completed.returncode == 0 else "fail",
        "command": [Path(part).name if index == 0 else part for index, part in enumerate(command)],
        "exit_code": completed.returncode,
        "duration_ms": elapsed_ms,
        "stdout_bytes": len(completed.stdout),
        "stdout_sha256": _hash_bytes(completed.stdout),
        "stderr_bytes": len(completed.stderr),
        "stderr_sha256": _hash_bytes(completed.stderr),
    }


def _python_probe(venv_python: Path) -> list[str]:
    return [
        str(venv_python),
        "-c",
        (
            "from pathlib import Path; "
            "from towow.l0.event_log import EventLog; "
            "from towow.l0.commit_gate.gate import CommitGate; "
            "from towow.l0.projection.projection import ProjectionStore; "
            "from towow.l2 import portable_runtime as p; "
            "d=Path('canonical-smoke'); d.mkdir(); t=d/'.towow'; t.mkdir(); "
            "log=EventLog(t/'events.log'); CommitGate(log); ProjectionStore(t/'graph'); "
            "r=p.spawn_bg_session('bounded', d, 'parent', method=p.SpawnMethod.MOCK); "
            "assert r.launched is False; "
            "\ntry:\n p.spawn_bg_session('real', d, 'parent', method=p.SpawnMethod.CLAUDE_BG)\n"
            "except RuntimeError as e:\n assert 'not part of Flowness Open Alpha' in str(e)\n"
            "else:\n raise AssertionError('real spawn did not fail closed')\n"
            "print('canonical-import-mock-failclosed-ok')"
        ),
    ]


def run_cleanroom_acceptance(
    *,
    export_root: Path,
    receipt_path: Path,
    python_bin: str,
    uv_bin: str,
    uv_cache: Path | None = None,
    wheelhouse: Path | None = None,
    dependency_report: Path | None = None,
    keep_workdir: bool = False,
) -> dict[str, Any]:
    manifest, freeze = verify_export(export_root)
    export_root = export_root.resolve(strict=True)
    if Path(receipt_path).resolve(strict=False).is_relative_to(export_root):
        raise CleanroomError("receipt must be written outside the immutable export")
    if (wheelhouse is None) != (dependency_report is None):
        raise CleanroomError("wheelhouse and dependency report must be supplied together")
    acceptance_cache = (
        _acceptance_cache_evidence(wheelhouse, dependency_report)
        if wheelhouse is not None and dependency_report is not None
        else None
    )
    selected_python = subprocess.run(
        [python_bin, "-c", "import json,sys; print(json.dumps({'version':list(sys.version_info[:3]),'executable':sys.executable}))"],
        capture_output=True,
        text=True,
        check=False,
    )
    if selected_python.returncode:
        raise CleanroomError("selected Python is not executable")
    python_info = json.loads(selected_python.stdout)
    if tuple(python_info["version"]) < (3, 12, 0):
        raise CleanroomError("Python 3.12 or newer is required for canonical Harness")

    clean_root = Path(tempfile.mkdtemp(prefix="flowness-open-alpha-cleanroom-"))
    env = _safe_env(clean_root, uv_cache)
    venv = clean_root / "venv"
    scratch_ledger, scratch_harness, scratch_oss_harness = _write_scratch_packages(
        export_root, clean_root
    )
    try:
        scratch_tests = _copy_canonical_tests(export_root, clean_root)
    except CleanroomError:
        scratch_tests = []
    stages: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []

    def stage(stage_id: str, command: Sequence[str], cwd: Path = clean_root) -> bool:
        result = _run_stage(stage_id, command, cwd=cwd, env=env)
        stages.append(result)
        return result["state"] == "pass"

    try:
        ok = stage("create-fresh-venv", [uv_bin, "venv", "--python", python_bin, str(venv)])
        vpython = venv / "bin/python"
        if ok:
            dependency_command = [
                uv_bin,
                "pip",
                "install",
                "--offline",
                "--python",
                str(vpython),
            ]
            if wheelhouse is not None:
                dependency_command.extend(["--find-links", str(wheelhouse.resolve())])
            dependency_command.extend(
                ["--requirement", str(scratch_harness / "open-alpha-requirements.lock")]
            )
            ok = stage(
                "install-offline-dependencies",
                dependency_command,
            )
        if not ok:
            blockers.append(
                {
                    "blocker_id": DEPENDENCY_BLOCKER,
                    "detail": "A fresh environment could not resolve the pinned test dependencies from the declared offline cache.",
                    "retest_condition": "Provide a sealed cross-platform wheelhouse or an explicit offline cache containing every pinned dependency and rerun.",
                }
            )
        else:
            install_prefix = [uv_bin, "pip", "install", "--offline", "--no-deps", "--no-build-isolation", "--python", str(vpython)]
            ok = stage("install-ledger-package", [*install_prefix, str(scratch_ledger)])
            ok = stage(
                "install-public-harness-package",
                [*install_prefix, str(scratch_harness)],
            ) and ok
            ok = stage(
                "install-public-oss-package",
                [*install_prefix, str(scratch_oss_harness)],
            ) and ok

        if ok:
            demo = clean_root / "ledger-demo"
            ok = stage("ledger-cli-run", [str(venv / "bin/flowness-ledger-demo"), "--demo-dir", str(demo)])
            ok = stage("ledger-cli-verify", [str(venv / "bin/flowness-ledger-demo"), "--verify-demo-dir", str(demo)]) and ok
            ok = stage(
                "harness-cli-inspect",
                [str(venv / "bin/flowness-harness"), "--json"],
            ) and ok
            ok = stage("canonical-import-mock-realspawn", _python_probe(vpython)) and ok

            harness_demo = clean_root / "harness-demo"
            ok = stage(
                "open-alpha-demo",
                [
                    str(venv / "bin/flowness-oss"),
                    "open-alpha-demo",
                    "--output",
                    str(harness_demo),
                ],
            ) and ok
            ok = stage(
                "open-alpha-demo-inspect",
                [
                    str(venv / "bin/flowness-oss"),
                    "open-alpha-demo-inspect",
                    "--run-root",
                    str(harness_demo),
                ],
            ) and ok

            if len(scratch_tests) != len(_CANONICAL_TESTS):
                ok = False
                stages.append(
                    {
                        "stage_id": "canonical-representative-tests",
                        "state": "fail",
                        "command": ["python", "-m", "pytest", "<manifest-selected-tests>"],
                        "exit_code": 2,
                        "duration_ms": 0,
                        "stdout_bytes": 0,
                        "stdout_sha256": _hash_bytes(b""),
                        "stderr_bytes": 0,
                        "stderr_sha256": _hash_bytes(b""),
                    }
                )
            else:
                ok = stage(
                    "canonical-representative-tests",
                    [
                        str(vpython),
                        "-m",
                        "pytest",
                        "-q",
                        "--disable-warnings",
                        *(str(path) for path in scratch_tests),
                    ],
                ) and ok
        try:
            post_manifest, post_freeze = verify_export(export_root)
            post_unchanged = (
                post_manifest["manifest_hash"] == manifest["manifest_hash"]
                and post_freeze["record_hash"] == freeze["record_hash"]
            )
        except CleanroomError:
            post_unchanged = False
        stages.append(
            {
                "stage_id": "sealed-export-post-verify",
                "state": "pass" if post_unchanged else "fail",
                "command": ["internal", "verify-export-byte-for-byte"],
                "exit_code": 0 if post_unchanged else 1,
                "duration_ms": 0,
                "stdout_bytes": 0,
                "stdout_sha256": _hash_bytes(b""),
                "stderr_bytes": 0,
                "stderr_sha256": _hash_bytes(b""),
            }
        )
        ok = ok and post_unchanged
        if not ok and not any(item["blocker_id"] == EXECUTION_BLOCKER for item in blockers):
            failed_stage_ids = [
                item["stage_id"] for item in stages if item["state"] == "fail"
            ]
            blockers.append(
                {
                    "blocker_id": EXECUTION_BLOCKER,
                    "detail": "Failed clean-room stages: " + ", ".join(failed_stage_ids) + ". Command output is retained only by byte count and hash.",
                    "retest_condition": "Reproduce the failed stage from this receipt against a successor sealed export and obtain exit code 0.",
                }
            )

        distributions: list[dict[str, str]] = []
        if vpython.is_file():
            probe = subprocess.run(
                [str(vpython), "-c", "import importlib.metadata as m,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in m.distributions())))"],
                cwd=clean_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode == 0:
                distributions = [
                    {"name": name, "version": version}
                    for name, version in json.loads(probe.stdout)
                ]

        unsigned: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "receipt_id": "cleanroom-" + freeze["payload_aggregate_hash"].removeprefix("sha256:")[:24],
            "state": (
                "pass"
                if all(item["state"] == "pass" for item in stages) and not blockers
                else "pass_with_blockers"
                if all(item["state"] == "pass" for item in stages)
                else "failed"
            ),
            "source": {
                "source_commit": freeze["source_commit"],
                "export_id": manifest["export_id"],
                "export_manifest_hash": manifest["manifest_hash"],
                "payload_aggregate_hash": freeze["payload_aggregate_hash"],
                "files": manifest["payload"]["files"],
                "bytes": manifest["payload"]["bytes"],
            },
            "environment": {
                "python_version": ".".join(map(str, python_info["version"])),
                "platform": platform.platform(),
                "support_coordinate": {
                    "python": ".".join(map(str, python_info["version"][:2])),
                    "system": platform.system(),
                    "machine": platform.machine(),
                },
                "fresh_venv": True,
                "network_policy": "disabled_uv_offline_and_pip_no_index",
                "dependency_source": (
                    "external_host_acceptance_wheelhouse"
                    if acceptance_cache is not None
                    else "explicit_host_uv_cache"
                    if uv_cache
                    else "new_empty_cache"
                ),
                "acceptance_cache": acceptance_cache,
                "cache_not_part_of_export": acceptance_cache is not None,
                "single_host_only": acceptance_cache is not None,
                "existing_venv_inherited": False,
                "pythonpath_inherited": False,
                "home_isolated": True,
                "source_repository_referenced": False,
            },
            "dependencies": distributions,
            "stages": stages,
            "blockers": blockers,
            "release_ready": False,
            "boundary": "This receipt proves only the recorded local sealed-export installation and E2E observations. It cannot grant rights, owner approval, public-release readiness, external reproducibility, or production reliability.",
        }
        receipt = {**unsigned, "receipt_hash": _hash_bytes(_canonical_json(unsigned))}
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return receipt
    finally:
        if not keep_workdir:
            shutil.rmtree(clean_root, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-root", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable, dest="python_bin")
    parser.add_argument("--uv", default="uv", dest="uv_bin")
    parser.add_argument("--uv-cache", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--dependency-report", type=Path)
    parser.add_argument("--keep-workdir", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = run_cleanroom_acceptance(
            export_root=args.export_root,
            receipt_path=args.receipt,
            python_bin=args.python_bin,
            uv_bin=args.uv_bin,
            uv_cache=args.uv_cache,
            wheelhouse=args.wheelhouse,
            dependency_report=args.dependency_report,
            keep_workdir=args.keep_workdir,
        )
    except (CleanroomError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({"state": receipt["state"], "receipt": str(args.receipt), "blockers": [item["blocker_id"] for item in receipt["blockers"]]}))
    return 0 if receipt["state"] in {"pass", "pass_with_blockers"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
