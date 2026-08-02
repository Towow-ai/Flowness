from __future__ import annotations

"""Raw, locally reproducible measurement receipts for the ledger candidate.

The receipt deliberately records observations rather than deriving a benchmark:
each attempted fixture has its own result, monotonic elapsed time, environment
and byte hashes.  It retains failures as first-class data.  A receipt therefore
can help a reviewer reproduce a local candidate run, but cannot establish
throughput, latency, production reliability, a comparison, or an external
clean-room result.
"""

import hashlib
import json
import platform
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .demo import run_change_evidence_demo, verify_change_evidence_demo
from .ledger import LedgerError


LEGACY_MEASUREMENT_SCHEMA = "flowness-ledger-core-raw-local-measurements/v1"
MEASUREMENT_SCHEMA = "flowness-ledger-core-raw-local-measurements/v2"
MEASUREMENT_SUMMARY_SCHEMA = "flowness-ledger-core-raw-local-measurement-summary/v1"
TRIAL_FIXTURE_SCHEMA = "flowness-ledger-core-raw-local-fixture/v1"
TRIAL_RESULT_SCHEMA = "flowness-ledger-core-raw-local-result/v1"
BOUNDARY = "local_private_candidate_raw_observation_not_benchmark"
CANDIDATE_ID = "flowness-ledger-core@1.0.0a1-open-alpha-candidate"

TrialRunner = Callable[[Path], dict[str, Any]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: dict[str, Any]) -> str:
    encoded = _canonical(value) + b"\n"
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LedgerError(f"measurement {label} artifact is unsafe")
    try:
        value = json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        raise LedgerError(f"measurement {label} artifact is not valid JSON") from exc
    if not isinstance(value, dict):
        raise LedgerError(f"measurement {label} artifact is not an object")
    return value


def _environment() -> dict[str, str]:
    return {"python": platform.python_version(), "platform": platform.platform()}


def _new_empty_directory(root: Path | str) -> Path:
    directory = Path(root)
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir() or any(directory.iterdir()):
            raise LedgerError("measurement directory must be new and empty")
    else:
        directory.mkdir(parents=True)
    return directory.resolve(strict=True)


def _default_trial_runner(run_dir: Path) -> dict[str, Any]:
    produced = run_change_evidence_demo(run_dir)
    verified = verify_change_evidence_demo(run_dir)
    if produced != verified:
        raise LedgerError("demo verification returned different manifest")
    return {
        "demo_manifest_sha256": produced["manifest_sha256"],
        "verified_manifest_sha256": verified["manifest_sha256"],
    }


def run_raw_local_measurements(
    root: Path | str, trials: int = 3, *, trial_runner: TrialRunner | None = None
) -> dict[str, Any]:
    """Write one tamper-evident raw local measurement receipt.

    Every requested trial receives a canonical fixture and a canonical result
    artifact.  The result artifact exists even for an exception, which keeps
    failure observations from being silently discarded.  ``trial_runner`` is a
    test seam; production callers use the candidate demo/verifier runner.
    """

    if trials < 1:
        raise LedgerError("trials must be at least one")
    directory = _new_empty_directory(root)
    environment = _environment()
    runner = trial_runner or _default_trial_runner
    rows: list[dict[str, Any]] = []

    for index in range(1, trials + 1):
        trial_id = f"trial-{index:03d}"
        trial_dir = directory / trial_id
        trial_dir.mkdir()
        fixture = {
            "schema_version": TRIAL_FIXTURE_SCHEMA,
            "candidate_boundary": BOUNDARY,
            "trial_id": trial_id,
            "scenario": "change_evidence_demo_and_independent_verifier",
            "expected_observation": "verified_candidate_demo_manifest",
        }
        fixture_path = trial_dir / "fixture.json"
        fixture_sha256 = _write_json(fixture_path, fixture)

        started_ns = time.monotonic_ns()
        try:
            observation = runner(trial_dir / "run")
            if not isinstance(observation, dict):
                raise LedgerError("trial runner did not return an object")
            if observation.get("demo_manifest_sha256") != observation.get(
                "verified_manifest_sha256"
            ):
                raise LedgerError("trial runner did not verify the produced manifest")
            result = {
                "schema_version": TRIAL_RESULT_SCHEMA,
                "candidate_boundary": BOUNDARY,
                "trial_id": trial_id,
                "verdict": "pass",
                "observation": observation,
            }
            failure: str | None = None
        except Exception as exc:  # failures are evidence, not a reason to hide an attempt
            failure = f"{type(exc).__name__}: {exc}"
            result = {
                "schema_version": TRIAL_RESULT_SCHEMA,
                "candidate_boundary": BOUNDARY,
                "trial_id": trial_id,
                "verdict": "fail",
                "failure": failure,
            }
        elapsed_ns = time.monotonic_ns() - started_ns
        result_path = trial_dir / "result.json"
        result_sha256 = _write_json(result_path, result)
        rows.append(
            {
                "trial_id": trial_id,
                "verdict": result["verdict"],
                "monotonic_elapsed_ns": elapsed_ns,
                "environment": environment,
                "fixture": {"path": f"{trial_id}/fixture.json", "sha256": fixture_sha256},
                "result": {"path": f"{trial_id}/result.json", "sha256": result_sha256},
                "failure": failure,
            }
        )

    unsigned = {
        "schema_version": MEASUREMENT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "candidate_boundary": BOUNDARY,
        "environment": environment,
        "requested_trials": trials,
        "completed_trials": len(rows),
        "trials": rows,
        "all_passed": all(row["verdict"] == "pass" for row in rows),
        "not_proven": [
            "throughput or latency",
            "performance comparison or benchmark result",
            "external clean-room installation",
            "production reliability or incident handling",
            "public release readiness",
        ],
    }
    payload = {
        **unsigned,
        "measurements_sha256": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest(),
    }
    _write_json(directory / "raw-local-measurements.json", payload)
    return payload


def _expected_path(trial_id: str, artifact: str) -> str:
    return f"{trial_id}/{artifact}.json"


def _verify_trial(root: Path, index: int, row: Any, environment: dict[str, str]) -> None:
    trial_id = f"trial-{index:03d}"
    if not isinstance(row, dict) or row.get("trial_id") != trial_id:
        raise LedgerError("measurement trial identity is invalid")
    if row.get("verdict") not in {"pass", "fail"}:
        raise LedgerError("measurement trial verdict is invalid")
    elapsed = row.get("monotonic_elapsed_ns")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
        raise LedgerError("measurement monotonic elapsed value is invalid")
    if row.get("environment") != environment:
        raise LedgerError("measurement trial environment does not match receipt")

    trial_dir = root / trial_id
    if trial_dir.is_symlink() or not trial_dir.is_dir():
        raise LedgerError("measurement trial directory is unsafe")
    for artifact_name, expected_schema in (("fixture", TRIAL_FIXTURE_SCHEMA), ("result", TRIAL_RESULT_SCHEMA)):
        descriptor = row.get(artifact_name)
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("path") != _expected_path(trial_id, artifact_name)
            or not isinstance(descriptor.get("sha256"), str)
        ):
            raise LedgerError(f"measurement {artifact_name} descriptor is invalid")
        path = trial_dir / f"{artifact_name}.json"
        value = _read_json(path, artifact_name)
        if descriptor["sha256"] != _sha256_file(path):
            raise LedgerError(f"measurement {artifact_name} artifact hash does not match")
        if value.get("schema_version") != expected_schema or value.get("trial_id") != trial_id:
            raise LedgerError(f"measurement {artifact_name} artifact identity is invalid")
        if value.get("candidate_boundary") != BOUNDARY:
            raise LedgerError(f"measurement {artifact_name} artifact boundary is invalid")
        if artifact_name == "fixture" and (
            value.get("scenario") != "change_evidence_demo_and_independent_verifier"
            or value.get("expected_observation") != "verified_candidate_demo_manifest"
        ):
            raise LedgerError("measurement fixture is not the declared scenario")
        if artifact_name == "result":
            if value.get("verdict") != row["verdict"]:
                raise LedgerError("measurement result verdict does not match receipt")
            if row["verdict"] == "pass":
                observation = value.get("observation")
                if (
                    not isinstance(observation, dict)
                    or not isinstance(observation.get("demo_manifest_sha256"), str)
                    or observation.get("demo_manifest_sha256")
                    != observation.get("verified_manifest_sha256")
                    or row.get("failure") is not None
                ):
                    raise LedgerError("measurement pass result is incomplete")
            elif value.get("failure") != row.get("failure") or not isinstance(row.get("failure"), str):
                raise LedgerError("measurement failure result is incomplete")


def verify_raw_local_measurements(root: Path | str) -> dict[str, Any]:
    """Reject an altered or internally inconsistent raw local measurement receipt."""

    directory = Path(root)
    if directory.is_symlink() or not directory.is_dir():
        raise LedgerError("measurement directory is unsafe")
    directory = directory.resolve(strict=True)
    payload = _read_json(directory / "raw-local-measurements.json", "receipt")
    receipt_hash = payload.get("measurements_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "measurements_sha256"}
    schema_version = payload.get("schema_version")
    if (
        schema_version not in {LEGACY_MEASUREMENT_SCHEMA, MEASUREMENT_SCHEMA}
        or payload.get("candidate_boundary") != BOUNDARY
        or not isinstance(receipt_hash, str)
        or receipt_hash != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise LedgerError("invalid self-hashed raw local measurement receipt")
    if (
        schema_version == MEASUREMENT_SCHEMA
        and (
            not isinstance(payload.get("candidate_id"), str)
            or not payload["candidate_id"].strip()
        )
    ):
        raise LedgerError("measurement receipt candidate identity is invalid")
    environment = payload.get("environment")
    if (
        not isinstance(environment, dict)
        or set(environment) != {"python", "platform"}
        or not all(isinstance(value, str) for value in environment.values())
    ):
        raise LedgerError("measurement receipt environment is invalid")
    requested = payload.get("requested_trials")
    rows = payload.get("trials")
    if (
        isinstance(requested, bool)
        or not isinstance(requested, int)
        or requested < 1
        or not isinstance(rows, list)
        or payload.get("completed_trials") != len(rows)
        or len(rows) != requested
    ):
        raise LedgerError("measurement receipt trial count is invalid")
    for index, row in enumerate(rows, start=1):
        _verify_trial(directory, index, row, environment)
    if payload.get("all_passed") != all(row["verdict"] == "pass" for row in rows):
        raise LedgerError("measurement receipt all_passed value is invalid")
    if not isinstance(payload.get("not_proven"), list):
        raise LedgerError("measurement receipt boundaries are invalid")
    return payload


def _receipt_directory(receipt_path: Path | str) -> Path:
    """Resolve a receipt directory without accepting a symlinked indirection."""

    supplied = Path(receipt_path)
    if supplied.is_symlink():
        raise LedgerError("measurement receipt path is unsafe")
    if supplied.is_dir():
        return supplied
    if supplied.name == "raw-local-measurements.json" and supplied.is_file():
        return supplied.parent
    raise LedgerError("measurement receipt path must be a receipt directory or raw-local-measurements.json")


def summarize_raw_local_measurements(receipt_paths: list[Path | str]) -> dict[str, Any]:
    """Verify and aggregate homogeneous raw-local measurement receipts.

    This intentionally returns observations only.  It refuses legacy receipts
    that have no candidate identity, mixed schemas/candidates/environments,
    duplicate paths, and every invalid receipt.  Failed trials are kept both in
    ``elapsed_samples`` and ``failed_samples``; they are never filtered before
    deriving counts or elapsed extrema.
    """

    if not receipt_paths:
        raise LedgerError("at least one measurement receipt path is required")

    verified: list[tuple[Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for supplied in receipt_paths:
        directory = _receipt_directory(supplied)
        resolved = directory.resolve(strict=True)
        if resolved in seen:
            raise LedgerError("duplicate measurement receipt path")
        seen.add(resolved)
        receipt = verify_raw_local_measurements(resolved)
        if receipt.get("schema_version") != MEASUREMENT_SCHEMA:
            raise LedgerError("measurement summary requires current receipts with candidate identity")
        verified.append((resolved, receipt))

    first = verified[0][1]
    identity = {
        "schema_version": first["schema_version"],
        "candidate_id": first["candidate_id"],
        "candidate_boundary": first["candidate_boundary"],
        "environment": first["environment"],
    }
    for _directory, receipt in verified[1:]:
        for key, expected in identity.items():
            if receipt.get(key) != expected:
                raise LedgerError(f"measurement summary refuses mixed {key}")

    samples: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    receipt_sources: list[dict[str, str]] = []
    for directory, receipt in verified:
        receipt_sources.append(
            {
                "path": str(directory / "raw-local-measurements.json"),
                "measurements_sha256": receipt["measurements_sha256"],
            }
        )
        for row in receipt["trials"]:
            sample = {
                "receipt_measurements_sha256": receipt["measurements_sha256"],
                "trial_id": row["trial_id"],
                "verdict": row["verdict"],
                "monotonic_elapsed_ns": row["monotonic_elapsed_ns"],
                "failure": row["failure"],
            }
            samples.append(sample)
            if row["verdict"] == "fail":
                failures.append(sample)

    elapsed = [sample["monotonic_elapsed_ns"] for sample in samples]
    passed = sum(sample["verdict"] == "pass" for sample in samples)
    failed = len(samples) - passed
    return {
        "schema_version": MEASUREMENT_SUMMARY_SCHEMA,
        "candidate": identity,
        "receipt_sources": receipt_sources,
        "raw_aggregate": {
            "receipt_count": len(verified),
            "attempts": len(samples),
            "passed": passed,
            "failed": failed,
            "all_passed": failed == 0,
            "elapsed_samples": samples,
            "elapsed_ns": {
                "valid_sample_count": len(elapsed),
                "min": min(elapsed),
                "median": statistics.median(elapsed),
                "max": max(elapsed),
            },
            "failed_samples": failures,
        },
        "limitations": [
            "Raw local observations only; not a benchmark, efficiency result, or comparison.",
            "Elapsed values include every verified pass and fail attempt, but are local monotonic observations rather than latency or throughput claims.",
            "This summary does not establish external clean-room installation, production reliability, public release readiness, or any public claim.",
        ],
    }
