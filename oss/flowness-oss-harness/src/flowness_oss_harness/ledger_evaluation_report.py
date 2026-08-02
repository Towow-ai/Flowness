"""Compile local Ledger observations into a reproducible private report.

This compiler has a deliberately narrow job: preserve and cross-check the
already-produced semantic-trial, raw-measurement, and evaluator-kit receipts.
It does not create observations, select only successful rows, infer a clean
room, or turn elapsed values into a benchmark.  In particular, the compiler
will refuse to combine records from different declared environments.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .ledger_alpha_evaluation import RECEIPT_SCHEMA
from .registry import ValidationError, atomic_create_json
from .schema_validation import load_validated_json, validate_payload
from .resources import SCHEMAS_ROOT


REPORT_SCHEMA = SCHEMAS_ROOT / "ledger-local-evaluation-report.schema.json"
SEMANTIC_SCHEMA = "flowness-ledger-core-semantic-trials/v1"
MEASUREMENT_SCHEMA = "flowness-ledger-core-raw-local-measurements/v2"
MEASUREMENT_BOUNDARY = "local_private_candidate_raw_observation_not_benchmark"
SEMANTIC_BOUNDARY = "local_private_candidate_not_performance_benchmark"
REPORT_SCHEMA_VERSION = "ledger-local-evaluation-report/v1"
BOUNDARY = (
    "private local reproducibility report only; it cannot establish performance, "
    "a comparison, external adoption, independent clean-room status, rights, "
    "release readiness, publication, or owner authorization"
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdef" for char in value[7:]
    )


def _regular_file(path: Path, code: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(code)
    return path.resolve(strict=True)


def _receipt_file(value: Path | str, filename: str, code: str) -> Path:
    supplied = Path(value)
    if supplied.is_symlink():
        raise ValidationError(code)
    path = supplied / filename if supplied.is_dir() else supplied
    if path.name != filename:
        raise ValidationError(code)
    return _regular_file(path, code)


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(code) from exc
    if not isinstance(value, dict):
        raise ValidationError(code)
    return value


def _environment(value: Any, code: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"python", "platform"} or not all(
        isinstance(item, str) and item for item in value.values()
    ):
        raise ValidationError(code)
    return {"python": value["python"], "platform": value["platform"]}


def _same_platform_family(local_platform: str, evaluator_platform: str) -> bool:
    """Compare platform.platform() with the kit's platform.system() spelling."""

    local = local_platform.casefold()
    evaluator = evaluator_platform.casefold()
    if evaluator in local:
        return True
    # macOS commonly appears as ``macOS-*`` in platform.platform() but as
    # ``Darwin`` in platform.system().  This is a spelling normalization, not
    # permission to mix two OS families.
    return ("macos" in local or "darwin" in local) and evaluator == "darwin"


def _verify_semantic_trials(value: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _receipt_file(value, "semantic-trials.json", "EVALUATION-REPORT-SEMANTIC-PATH-INVALID")
    payload = _read_object(path, "EVALUATION-REPORT-SEMANTIC-INVALID")
    unsigned = {key: item for key, item in payload.items() if key != "trials_hash"}
    required = {
        "schema_version", "candidate_boundary", "environment", "requested_trials", "completed_trials",
        "trials", "all_passed", "not_proven", "trials_hash",
    }
    if (
        set(payload) != required
        or payload.get("schema_version") != SEMANTIC_SCHEMA
        or payload.get("candidate_boundary") != SEMANTIC_BOUNDARY
        or not _is_sha256(payload.get("trials_hash"))
        or payload["trials_hash"] != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise ValidationError("EVALUATION-REPORT-SEMANTIC-HASH-INVALID")
    environment = _environment(payload.get("environment"), "EVALUATION-REPORT-SEMANTIC-ENVIRONMENT-INVALID")
    requested = payload.get("requested_trials")
    rows = payload.get("trials")
    if (
        isinstance(requested, bool) or not isinstance(requested, int) or requested < 1
        or not isinstance(rows, list) or not rows or len(rows) > requested
        or payload.get("completed_trials") != len(rows) or not isinstance(payload.get("all_passed"), bool)
        or not isinstance(payload.get("not_proven"), list)
    ):
        raise ValidationError("EVALUATION-REPORT-SEMANTIC-SHAPE-INVALID")
    for index, row in enumerate(rows, start=1):
        expected_id = f"trial-{index:03d}"
        if not isinstance(row, dict) or set(row) != {"trial_id", "verdict", "manifest_sha256", "failure"}:
            raise ValidationError("EVALUATION-REPORT-SEMANTIC-ROW-INVALID")
        if row.get("trial_id") != expected_id or row.get("verdict") not in {"pass", "fail"}:
            raise ValidationError("EVALUATION-REPORT-SEMANTIC-ROW-INVALID")
        if row["verdict"] == "pass" and (not _is_sha256(row.get("manifest_sha256")) or row.get("failure") is not None):
            raise ValidationError("EVALUATION-REPORT-SEMANTIC-ROW-INVALID")
        if row["verdict"] == "fail" and (row.get("manifest_sha256") is not None or not isinstance(row.get("failure"), str) or not row["failure"]):
            raise ValidationError("EVALUATION-REPORT-SEMANTIC-ROW-INVALID")
        if row["verdict"] == "fail" and index != len(rows):
            raise ValidationError("EVALUATION-REPORT-SEMANTIC-FAILURE-NOT-TERMINAL")
    computed_all_passed = len(rows) == requested and all(row["verdict"] == "pass" for row in rows)
    if payload["all_passed"] != computed_all_passed:
        raise ValidationError("EVALUATION-REPORT-SEMANTIC-VERDICT-INVALID")
    return payload, {"file_name": path.name, "sha256": _sha256_file(path)}


def _verify_measurement(value: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _receipt_file(value, "raw-local-measurements.json", "EVALUATION-REPORT-MEASUREMENT-PATH-INVALID")
    root = path.parent.resolve(strict=True)
    payload = _read_object(path, "EVALUATION-REPORT-MEASUREMENT-INVALID")
    unsigned = {key: item for key, item in payload.items() if key != "measurements_sha256"}
    required = {
        "schema_version", "candidate_id", "candidate_boundary", "environment", "requested_trials",
        "completed_trials", "trials", "all_passed", "not_proven", "measurements_sha256",
    }
    if (
        set(payload) != required or payload.get("schema_version") != MEASUREMENT_SCHEMA
        or payload.get("candidate_boundary") != MEASUREMENT_BOUNDARY
        or not isinstance(payload.get("candidate_id"), str) or not payload["candidate_id"].strip()
        or not _is_sha256(payload.get("measurements_sha256"))
        or payload["measurements_sha256"] != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise ValidationError("EVALUATION-REPORT-MEASUREMENT-HASH-INVALID")
    environment = _environment(payload.get("environment"), "EVALUATION-REPORT-MEASUREMENT-ENVIRONMENT-INVALID")
    requested, rows = payload.get("requested_trials"), payload.get("trials")
    if (
        isinstance(requested, bool) or not isinstance(requested, int) or requested < 1
        or not isinstance(rows, list) or len(rows) != requested or payload.get("completed_trials") != len(rows)
        or not isinstance(payload.get("all_passed"), bool) or not isinstance(payload.get("not_proven"), list)
    ):
        raise ValidationError("EVALUATION-REPORT-MEASUREMENT-SHAPE-INVALID")
    for index, row in enumerate(rows, start=1):
        trial_id = f"trial-{index:03d}"
        if not isinstance(row, dict) or set(row) != {"trial_id", "verdict", "monotonic_elapsed_ns", "environment", "fixture", "result", "failure"}:
            raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ROW-INVALID")
        if row.get("trial_id") != trial_id or row.get("verdict") not in {"pass", "fail"} or row.get("environment") != environment:
            raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ROW-INVALID")
        elapsed = row.get("monotonic_elapsed_ns")
        if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
            raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ROW-INVALID")
        for artifact, schema in (("fixture", "flowness-ledger-core-raw-local-fixture/v1"), ("result", "flowness-ledger-core-raw-local-result/v1")):
            descriptor = row.get(artifact)
            expected_rel = f"{trial_id}/{artifact}.json"
            if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"} or descriptor.get("path") != expected_rel or not _is_sha256(descriptor.get("sha256")):
                raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ARTIFACT-INVALID")
            artifact_path = root / expected_rel
            if artifact_path.is_symlink() or not artifact_path.is_file() or _sha256_file(artifact_path) != descriptor["sha256"]:
                raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ARTIFACT-HASH-MISMATCH")
            record = _read_object(artifact_path, "EVALUATION-REPORT-MEASUREMENT-ARTIFACT-INVALID")
            if record.get("schema_version") != schema or record.get("trial_id") != trial_id or record.get("candidate_boundary") != MEASUREMENT_BOUNDARY:
                raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ARTIFACT-INVALID")
            if artifact == "result" and record.get("verdict") != row["verdict"]:
                raise ValidationError("EVALUATION-REPORT-MEASUREMENT-RESULT-VERDICT-INVALID")
        if row["verdict"] == "pass" and row.get("failure") is not None:
            raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ROW-INVALID")
        if row["verdict"] == "fail" and (not isinstance(row.get("failure"), str) or not row["failure"]):
            raise ValidationError("EVALUATION-REPORT-MEASUREMENT-ROW-INVALID")
    if payload["all_passed"] != all(row["verdict"] == "pass" for row in rows):
        raise ValidationError("EVALUATION-REPORT-MEASUREMENT-VERDICT-INVALID")
    return payload, {"file_name": path.name, "sha256": _sha256_file(path)}


def _verify_evaluation(value: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _regular_file(Path(value), "EVALUATION-REPORT-EVALUATOR-PATH-INVALID")
    payload = load_validated_json(path, RECEIPT_SCHEMA, "Ledger evaluator receipt")
    verify_self_hash(payload, "receipt_hash")
    passed = all(row["outcome"] == "passed" for row in payload["assertions"])
    if payload["all_required_assertions_passed"] != passed or payload["evaluation_state"] != ("all_assertions_passed" if passed else "assertions_failed"):
        raise ValidationError("EVALUATION-REPORT-EVALUATOR-VERDICT-INVALID")
    return payload, {"file_name": path.name, "sha256": _sha256_file(path)}


def _unique_paths(items: list[Path | str], kind: str, filename: str | None = None) -> None:
    if not items:
        raise ValidationError(f"EVALUATION-REPORT-{kind}-REQUIRED")
    seen: set[Path] = set()
    for item in items:
        supplied = Path(item)
        resolved = (supplied / filename if filename is not None and supplied.is_dir() else supplied).resolve(strict=True)
        if resolved in seen:
            raise ValidationError(f"EVALUATION-REPORT-DUPLICATE-{kind}")
        seen.add(resolved)


def _aggregate_trials(receipts: list[dict[str, Any]], field: str) -> dict[str, Any]:
    rows = [row for receipt in receipts for row in receipt[field]]
    passed = sum(row["verdict"] == "pass" for row in rows)
    return {"attempts": len(rows), "passed": passed, "failed": len(rows) - passed, "all_passed": passed == len(rows)}


def _render(report: dict[str, Any]) -> str:
    aggregates = report["aggregates"]
    lines = [
        "# Ledger Local Evaluation Report — private staging",
        "",
        "**Status:** local, reproducible observation compilation only. It is not a benchmark, performance report, comparison, clean-room attestation, adoption result, release decision, or publication authorization.",
        "",
        f"Report ID: `{report['report_id']}`",
        "",
        "## Declared environment",
        "",
        f"- Python: `{report['environment']['python']}`",
        f"- Platform: `{report['environment']['platform']}`",
        f"- Evaluator implementation: `{report['environment']['evaluator_implementation']}`",
        f"- Evaluator candidate-source match: `{report['environment']['evaluator_candidate_source_match']}`",
        "",
        "## Complete local observation counts",
        "",
        f"- Semantic trials: {aggregates['semantic_trials']['attempts']} attempts; {aggregates['semantic_trials']['passed']} pass; {aggregates['semantic_trials']['failed']} fail.",
        f"- Raw measurement trials: {aggregates['raw_measurements']['attempts']} attempts; {aggregates['raw_measurements']['passed']} pass; {aggregates['raw_measurements']['failed']} fail.",
        f"- Evaluator assertions: {aggregates['evaluator_assertions']['attempts']} attempts; {aggregates['evaluator_assertions']['passed']} passed; {aggregates['evaluator_assertions']['failed']} not passed.",
        "",
        "## Raw elapsed observations",
        "",
        "These values include every local pass and failure, but are not latency, throughput, efficiency, or performance results.",
        "",
        f"- Samples: {aggregates['raw_measurements']['elapsed_ns']['valid_sample_count']}; min: {aggregates['raw_measurements']['elapsed_ns']['min']}; median: {aggregates['raw_measurements']['elapsed_ns']['median']}; max: {aggregates['raw_measurements']['elapsed_ns']['max']} nanoseconds.",
        "",
        "## Retained failures and non-passes",
        "",
    ]
    failures = report["failure_records"]
    if not failures:
        lines.append("- None in the supplied local receipts. This does not establish reliability beyond those records.")
    else:
        for item in failures:
            detail = item.get("failure") or item.get("code")
            lines.append(f"- `{item['surface']}` / `{item['record_id']}`: {detail}")
    lines += [
        "",
        "## Bound input files",
        "",
    ]
    for surface, sources in report["input_sources"].items():
        for item in sources:
            lines.append(f"- `{surface}`: `{item['file_name']}` ({item['sha256']})")
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["not_proven"]]
    lines += ["", "## Boundary", "", report["boundary"], ""]
    return "\n".join(lines)


def _atomic_create_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValidationError(f"refusing to overwrite existing file: {path}") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def compile_ledger_local_evaluation_report(
    semantic_trial_paths: list[Path | str],
    measurement_paths: list[Path | str],
    evaluator_receipt_paths: list[Path | str],
    output_json: Path,
    output_markdown: Path,
) -> dict[str, Any]:
    """Create one immutable report from homogeneous, complete local receipts.

    All receipt rows are copied into the report after integrity validation.  A
    report therefore remains useful for review even when some supplied trials
    or evaluator assertions failed; callers must not pre-filter failures.
    """

    _unique_paths(semantic_trial_paths, "SEMANTIC", "semantic-trials.json")
    _unique_paths(measurement_paths, "MEASUREMENT", "raw-local-measurements.json")
    _unique_paths(evaluator_receipt_paths, "EVALUATOR")
    if output_json.suffix != ".json" or output_markdown.suffix != ".md" or output_json.exists() or output_markdown.exists():
        raise ValidationError("EVALUATION-REPORT-OUTPUT-INVALID")

    semantic = [_verify_semantic_trials(item) for item in semantic_trial_paths]
    measurements = [_verify_measurement(item) for item in measurement_paths]
    evaluations = [_verify_evaluation(item) for item in evaluator_receipt_paths]
    semantic_receipts, semantic_sources = zip(*semantic)
    measurement_receipts, measurement_sources = zip(*measurements)
    evaluation_receipts, evaluation_sources = zip(*evaluations)

    baseline_environment = semantic_receipts[0]["environment"]
    if any(receipt["environment"] != baseline_environment for receipt in semantic_receipts + measurement_receipts):
        raise ValidationError("EVALUATION-REPORT-MIXED-LOCAL-ENVIRONMENT")
    candidate_id = measurement_receipts[0]["candidate_id"]
    if any(receipt["candidate_id"] != candidate_id for receipt in measurement_receipts):
        raise ValidationError("EVALUATION-REPORT-MIXED-MEASUREMENT-CANDIDATE")

    evaluation_environment = evaluation_receipts[0]["environment"]
    evaluator_identity = {
        "environment": evaluation_environment,
        "candidate_source_inventory_sha256": evaluation_receipts[0]["input_binding"]["candidate_source_inventory"]["sha256"],
    }
    if any(
        receipt["environment"] != evaluator_identity["environment"]
        or receipt["input_binding"]["candidate_source_inventory"]["sha256"] != evaluator_identity["candidate_source_inventory_sha256"]
        for receipt in evaluation_receipts
    ):
        raise ValidationError("EVALUATION-REPORT-MIXED-EVALUATOR-IDENTITY")
    if (
        not _same_platform_family(baseline_environment["platform"], evaluation_environment["platform"])
        or evaluation_environment["python_version"] != baseline_environment["python"]
    ):
        raise ValidationError("EVALUATION-REPORT-MIXED-CROSS-SURFACE-ENVIRONMENT")

    semantic_aggregate = _aggregate_trials(list(semantic_receipts), "trials")
    measurement_aggregate = _aggregate_trials(list(measurement_receipts), "trials")
    elapsed = [row["monotonic_elapsed_ns"] for receipt in measurement_receipts for row in receipt["trials"]]
    measurement_aggregate["elapsed_ns"] = {
        "valid_sample_count": len(elapsed), "min": min(elapsed), "median": statistics.median(elapsed), "max": max(elapsed),
    }
    assertions = [row for receipt in evaluation_receipts for row in receipt["assertions"]]
    evaluator_aggregate = {
        "attempts": len(assertions),
        "passed": sum(row["outcome"] == "passed" for row in assertions),
        "failed": sum(row["outcome"] != "passed" for row in assertions),
        "all_passed": all(row["outcome"] == "passed" for row in assertions),
    }
    failures = [
        {"surface": "semantic_trial", "record_id": row["trial_id"], "failure": row["failure"]}
        for receipt in semantic_receipts for row in receipt["trials"] if row["verdict"] == "fail"
    ] + [
        {"surface": "raw_measurement", "record_id": row["trial_id"], "failure": row["failure"]}
        for receipt in measurement_receipts for row in receipt["trials"] if row["verdict"] == "fail"
    ] + [
        {"surface": "evaluator_assertion", "record_id": row["assertion_id"], "code": row["code"]}
        for receipt in evaluation_receipts for row in receipt["assertions"] if row["outcome"] != "passed"
    ]
    source_hashes = {
        "semantic": list(semantic_sources), "measurements": list(measurement_sources), "evaluator": list(evaluation_sources),
    }
    report_id = "ledger-local-evaluation-" + canonical_hash(source_hashes)[7:23]
    unsigned = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "scope": "private_staging_local_reproducibility_report",
        "authorization": "not_authorized",
        "environment": {
            **baseline_environment,
            "evaluator_implementation": evaluation_environment["implementation"],
            "evaluator_candidate_source_match": evaluation_environment["candidate_source_match"],
        },
        "candidate": {
            "measurement_candidate_id": candidate_id,
            "evaluator_candidate_source_inventory_sha256": evaluator_identity["candidate_source_inventory_sha256"],
            "cross_surface_identity_status": "not_established_by_this_report",
        },
        "input_sources": source_hashes,
        "records": {
            "semantic_trial_receipts": list(semantic_receipts),
            "raw_measurement_receipts": list(measurement_receipts),
            "evaluator_receipts": list(evaluation_receipts),
        },
        "aggregates": {
            "semantic_trials": semantic_aggregate,
            "raw_measurements": measurement_aggregate,
            "evaluator_assertions": evaluator_aggregate,
        },
        "failure_records": failures,
        "all_supplied_observations_passed": semantic_aggregate["all_passed"] and measurement_aggregate["all_passed"] and evaluator_aggregate["all_passed"],
        "not_proven": [
            "performance, efficiency, throughput, latency, or comparative benchmark results",
            "external adoption, customer value, or independent clean-room installation",
            "production reliability, deployment, incident response, or operational availability",
            "sealed-export rights, license, SBOM completeness, public release readiness, or owner authorization",
            "a common candidate identity across semantic trials, measurements, and evaluator-kit receipts beyond the retained declarations",
        ],
        "boundary": BOUNDARY,
    }
    report = {**unsigned, "report_hash": canonical_hash(unsigned)}
    validate_payload(report, REPORT_SCHEMA, "Ledger local evaluation report")
    atomic_create_json(output_json, report)
    _atomic_create_text(output_markdown, _render(report))
    return report


def verify_ledger_local_evaluation_report(path: Path) -> dict[str, Any]:
    """Verify only the immutable compiled report, not unavailable original paths."""

    report = load_validated_json(path, REPORT_SCHEMA, "Ledger local evaluation report")
    verify_self_hash(report, "report_hash")
    if report["schema_version"] != REPORT_SCHEMA_VERSION or report["authorization"] != "not_authorized" or report["boundary"] != BOUNDARY:
        raise ValidationError("EVALUATION-REPORT-IDENTITY-INVALID")
    return report
