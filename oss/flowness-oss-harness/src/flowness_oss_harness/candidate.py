from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload

DEFAULT_CANDIDATE_SCHEMA = SCHEMAS_ROOT / "release-candidate.schema.json"


def _load(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be JSON") from exc


def _rows(payload: Any, key: str, label: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get(key)
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValidationError(f"{label} must contain a {key} array")
    return payload


def _unique(rows: list[dict[str, Any]], key: str, label: str) -> None:
    values = [row.get(key) for row in rows]
    if any(not value for value in values) or len(set(values)) != len(values):
        raise ValidationError(f"{label} {key} values must be present and unique")


def assemble_candidate(
    sealed_manifest_path: Path,
    modules_registry_path: Path,
    claims_registry_path: Path,
    benchmarks_path: Path,
    evidence_path: Path,
    output: Path,
    schema_path: Path = DEFAULT_CANDIDATE_SCHEMA,
) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValidationError("candidate output already exists")
    manifest = _load(sealed_manifest_path, "sealed assembly manifest")
    if not isinstance(manifest, dict):
        raise ValidationError("sealed assembly manifest must be an object")
    if manifest.get("schema_version") != "candidate-assembly-manifest/v1":
        raise ValidationError("unsupported candidate assembly manifest")
    verify_self_hash(manifest, "manifest_hash")
    if set(manifest) != {
        "schema_version",
        "snapshot",
        "target_stage",
        "created_at",
        "manifest_hash",
    }:
        raise ValidationError("sealed assembly manifest has unexpected fields")

    modules = _rows(_load(modules_registry_path, "modules registry"), "modules", "modules registry")
    claims = _rows(_load(claims_registry_path, "claims registry"), "claims", "claims registry")
    benchmarks = _rows(_load(benchmarks_path, "benchmarks"), "benchmarks", "benchmarks")
    evidence = _rows(_load(evidence_path, "evidence"), "evidence", "evidence")
    _unique(modules, "module_id", "modules")
    _unique(claims, "claim_id", "claims")
    _unique(benchmarks, "benchmark_id", "benchmarks")
    _unique(evidence, "evidence_id", "evidence")

    modules = sorted(modules, key=lambda item: item["module_id"])
    claims = sorted(claims, key=lambda item: item["claim_id"])
    benchmarks = sorted(benchmarks, key=lambda item: item["benchmark_id"])
    evidence = sorted(evidence, key=lambda item: item["evidence_id"])
    evidence_ids = {item["evidence_id"] for item in evidence}
    claim_ids = {item["claim_id"] for item in claims}
    snapshot_id = manifest.get("snapshot", {}).get("snapshot_id")
    for module in modules:
        if not set(module.get("evidence_ids", [])).issubset(evidence_ids):
            raise ValidationError(f"module has dangling evidence: {module['module_id']}")
    for claim in claims:
        if not set(claim.get("evidence_ids", [])).issubset(evidence_ids):
            raise ValidationError(f"claim has dangling evidence: {claim['claim_id']}")
    for benchmark in benchmarks:
        if not set(benchmark.get("claim_ids", [])).issubset(claim_ids):
            raise ValidationError(
                f"benchmark has dangling claims: {benchmark['benchmark_id']}"
            )
        if benchmark.get("raw_result_evidence_id") not in evidence_ids:
            raise ValidationError(
                f"benchmark has dangling raw evidence: {benchmark['benchmark_id']}"
            )
    if any(item.get("snapshot_id") != snapshot_id for item in evidence):
        raise ValidationError("candidate evidence must match the sealed snapshot")

    body = {
        "schema_version": "1.0",
        "snapshot": manifest["snapshot"],
        "target_stage": manifest["target_stage"],
        "created_at": manifest["created_at"],
        "modules": modules,
        "claims": claims,
        "benchmarks": benchmarks,
        "evidence": evidence,
    }
    candidate_id = "candidate-" + canonical_hash(body).removeprefix("sha256:")[:24]
    candidate = {"candidate_id": candidate_id, **body}
    validate_payload(candidate, schema_path, "release candidate")
    atomic_create_json(output, candidate)
    return candidate
