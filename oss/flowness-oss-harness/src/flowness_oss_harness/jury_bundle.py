"""Immutable evidence-only jury bundles.

This module deliberately does *not* call the release evaluator.  A bundle
freezes the inputs a later evaluator may inspect; it neither clears a blocker
nor authorizes Candidate A (or any successor) for release.
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
from .policy import (
    APPROVED_POLICY_PATH,
    APPROVED_POLICY_SCHEMA,
    load_approved_policy,
)
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


JURY_BUNDLE_SCHEMA = SCHEMAS_ROOT / "jury-bundle.schema.json"
CANDIDATE_SCHEMA = SCHEMAS_ROOT / "release-candidate.schema.json"
REPORT_SCHEMA = SCHEMAS_ROOT / "jury-report.schema.json"
BLOCKER_CASE_SCHEMA = SCHEMAS_ROOT / "blocker-case.schema.json"
REWORK_MANIFEST_SCHEMA = SCHEMAS_ROOT / "rework-manifest.schema.json"
SUCCESSOR_ATTESTATION_SCHEMA = SCHEMAS_ROOT / "successor-retest-attestation.schema.json"


def _file_hash(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"{label} must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _load_candidate(path: Path, label: str) -> dict[str, Any]:
    candidate = load_validated_json(path, CANDIDATE_SCHEMA, label)
    expected = "candidate-" + canonical_hash(
        {key: value for key, value in candidate.items() if key != "candidate_id"}
    ).removeprefix("sha256:")[:24]
    if candidate.get("candidate_id") != expected:
        raise ValidationError(f"{label} candidate_id does not bind its contents")
    return candidate


def _load_report(path: Path, candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    report = load_validated_json(path, REPORT_SCHEMA, "jury report")
    verify_self_hash(report, "signature")
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    if (
        report["candidate_id"] != candidate["candidate_id"]
        or report["snapshot_id"] != snapshot_id
        or report["policy_version"] != policy["policy_version"]
    ):
        raise ValidationError("JURY-BUNDLE-REPORT-BINDING-MISMATCH")
    return report


def _relative_file(root: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError(f"{label} path escapes bundle")
    result = root / relative
    if result.is_symlink() or not result.is_file():
        raise ValidationError(f"{label} file is missing or unsafe")
    return result


def _lineage_input(
    blocker_case_path: Path | None,
    rework_manifest_path: Path | None,
    successor_attestation_path: Path | None,
    candidate: dict[str, Any],
    reports: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[tuple[str, Path, dict[str, Any], str]]]:
    supplied = (blocker_case_path, rework_manifest_path, successor_attestation_path)
    if not any(supplied):
        return (
            {"kind": "initial_jury", "origin_candidate_id": candidate["candidate_id"], "origin_snapshot_id": candidate["snapshot"]["snapshot_id"], "artifacts": []},
            [],
        )
    if not all(supplied):
        raise ValidationError("JURY-BUNDLE-LINEAGE-MUST-BE-COMPLETE")
    assert blocker_case_path is not None
    assert rework_manifest_path is not None
    assert successor_attestation_path is not None
    case = load_validated_json(blocker_case_path, BLOCKER_CASE_SCHEMA, "blocker case")
    verify_self_hash(case, "case_hash")
    manifest = load_validated_json(rework_manifest_path, REWORK_MANIFEST_SCHEMA, "rework manifest")
    verify_self_hash(manifest, "manifest_hash")
    attestation = load_validated_json(successor_attestation_path, SUCCESSOR_ATTESTATION_SCHEMA, "successor retest attestation")
    verify_self_hash(attestation, "attestation_hash")
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    if (
        manifest["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or manifest["blocker_case"]["case_hash"] != case["case_hash"]
        or manifest["successor"]["candidate_id"] != candidate["candidate_id"]
        or manifest["successor"]["snapshot_id"] != snapshot_id
        or attestation["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or attestation["blocker_case"]["case_hash"] != case["case_hash"]
        or attestation["rework_manifest"]["rework_manifest_id"] != manifest["rework_manifest_id"]
        or attestation["rework_manifest"]["manifest_hash"] != manifest["manifest_hash"]
        or attestation["successor"]["candidate_id"] != candidate["candidate_id"]
        or attestation["successor"]["snapshot_id"] != snapshot_id
    ):
        raise ValidationError("JURY-BUNDLE-LINEAGE-BINDING-MISMATCH")
    artifacts = [
        ("blocker_case", blocker_case_path, case, "case_hash"),
        ("rework_manifest", rework_manifest_path, manifest, "manifest_hash"),
        ("successor_retest_attestation", successor_attestation_path, attestation, "attestation_hash"),
    ]
    return (
        {"kind": "successor_retest", "origin_candidate_id": case["original"]["candidate_id"], "origin_snapshot_id": case["original"]["snapshot_id"], "artifacts": []},
        artifacts,
    )


def _copy_regular(source: Path, target: Path, label: str) -> str:
    digest = _file_hash(source, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)
    if _file_hash(target, f"copied {label}") != digest:
        raise ValidationError(f"{label} changed while being bundled")
    return digest


def _report_relative_path(report: dict[str, Any]) -> str:
    """Use content address rather than report_id as a filesystem filename.

    ``report_id`` deliberately admits ``:`` for protocol identities; it is not
    a portable filename contract.  The ID remains inside both the copied report
    and its immutable manifest reference.
    """

    return "reports/" + canonical_hash(report).removeprefix("sha256:") + ".json"


def create_jury_bundle(
    *,
    candidate_path: Path,
    report_paths: list[Path],
    output_dir: Path,
    blocker_case_path: Path | None = None,
    rework_manifest_path: Path | None = None,
    successor_attestation_path: Path | None = None,
) -> dict[str, Any]:
    """Seal candidate + exact reports, optionally with complete B rework lineage."""

    if output_dir.exists() or output_dir.is_symlink():
        raise ValidationError("jury bundle output already exists")
    if not report_paths:
        raise ValidationError("jury bundle requires at least one report")
    policy, policy_hash = load_approved_policy()
    candidate = _load_candidate(candidate_path, "release candidate")
    reports = [_load_report(path, candidate, policy) for path in report_paths]
    report_ids = [report["report_id"] for report in reports]
    if len(report_ids) != len(set(report_ids)):
        raise ValidationError("JURY-BUNDLE-DUPLICATE-REPORT-ID")
    lineage, lineage_inputs = _lineage_input(
        blocker_case_path, rework_manifest_path, successor_attestation_path, candidate, reports
    )
    if lineage_inputs:
        source_by_id = {report["report_id"]: path for report, path in zip(reports, report_paths)}
        attestation = lineage_inputs[-1][2]
        for ref in attestation["successor_reports"]:
            source = source_by_id.get(ref["report_id"])
            if source is None or ref["sha256"] != _file_hash(source, "successor jury report"):
                raise ValidationError("JURY-BUNDLE-SUCCESSOR-REPORT-MISSING")

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    try:
        candidate_digest = _copy_regular(candidate_path, staging / "artifacts/candidate.json", "release candidate")
        policy_digest = _copy_regular(APPROVED_POLICY_PATH, staging / "artifacts/governance-policy.json", "governance policy")
        report_refs: list[dict[str, str]] = []
        for report, source in sorted(zip(reports, report_paths), key=lambda item: item[0]["report_id"]):
            relative = _report_relative_path(report)
            report_refs.append({"report_id": report["report_id"], "candidate_id": report["candidate_id"], "snapshot_id": report["snapshot_id"], "policy_version": report["policy_version"], "path": relative, "sha256": _copy_regular(source, staging / relative, "jury report"), "signature": report["signature"]})
        for artifact_type, source, payload, self_field in lineage_inputs:
            artifact_id = payload[{"blocker_case": "blocker_case_id", "rework_manifest": "rework_manifest_id", "successor_retest_attestation": "attestation_id"}[artifact_type]]
            relative = f"lineage/{artifact_type}.json"
            lineage["artifacts"].append({"artifact_type": artifact_type, "artifact_id": artifact_id, "path": relative, "sha256": _copy_regular(source, staging / relative, artifact_type), "self_hash": payload[self_field]})
        unsigned = {"schema_version": "jury-bundle/v1", "bundle_id": "jury-bundle-" + canonical_hash({"candidate_sha256": candidate_digest, "policy_sha256": policy_hash, "report_sha256": [item["sha256"] for item in report_refs], "lineage": lineage}).removeprefix("sha256:")[:24], "authorization": "jury_evidence_only", "candidate": {"candidate_id": candidate["candidate_id"], "snapshot_id": candidate["snapshot"]["snapshot_id"], "path": "artifacts/candidate.json", "sha256": candidate_digest}, "policy": {"policy_version": policy["policy_version"], "path": "artifacts/governance-policy.json", "sha256": policy_digest}, "reports": report_refs, "lineage": lineage}
        bundle = {**unsigned, "bundle_hash": canonical_hash(unsigned)}
        validate_payload(bundle, JURY_BUNDLE_SCHEMA, "jury bundle")
        atomic_create_json(staging / "jury-bundle.json", bundle)
        os.replace(staging, output_dir)
        return bundle
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_jury_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Fail closed if any copied jury input no longer matches its immutable binding."""

    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ValidationError("jury bundle directory is missing or unsafe")
    bundle = load_validated_json(bundle_dir / "jury-bundle.json", JURY_BUNDLE_SCHEMA, "jury bundle")
    verify_self_hash(bundle, "bundle_hash")
    policy, policy_hash = load_approved_policy()
    candidate_ref = bundle["candidate"]
    candidate_path = _relative_file(bundle_dir, candidate_ref["path"], "candidate")
    if _file_hash(candidate_path, "candidate") != candidate_ref["sha256"]:
        raise ValidationError("JURY-BUNDLE-CANDIDATE-HASH-MISMATCH")
    candidate = _load_candidate(candidate_path, "bundled candidate")
    if candidate["candidate_id"] != candidate_ref["candidate_id"] or candidate["snapshot"]["snapshot_id"] != candidate_ref["snapshot_id"]:
        raise ValidationError("JURY-BUNDLE-CANDIDATE-BINDING-MISMATCH")
    policy_ref = bundle["policy"]
    policy_path = _relative_file(bundle_dir, policy_ref["path"], "policy")
    if _file_hash(policy_path, "policy") != policy_ref["sha256"] or policy_ref["sha256"] != policy_hash or policy_ref["policy_version"] != policy["policy_version"]:
        raise ValidationError("JURY-BUNDLE-POLICY-HASH-MISMATCH")
    try:
        copied_policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("JURY-BUNDLE-POLICY-INVALID") from exc
    if copied_policy != policy:
        raise ValidationError("JURY-BUNDLE-POLICY-CONTENT-MISMATCH")
    seen_ids: set[str] = set()
    for ref in bundle["reports"]:
        if ref["report_id"] in seen_ids:
            raise ValidationError("JURY-BUNDLE-DUPLICATE-REPORT-ID")
        seen_ids.add(ref["report_id"])
        path = _relative_file(bundle_dir, ref["path"], "jury report")
        if _file_hash(path, "jury report") != ref["sha256"]:
            raise ValidationError("JURY-BUNDLE-REPORT-HASH-MISMATCH")
        report = _load_report(path, candidate, policy)
        if any(report[key] != ref[key] for key in ("report_id", "candidate_id", "snapshot_id", "policy_version", "signature")):
            raise ValidationError("JURY-BUNDLE-REPORT-REF-MISMATCH")
    _verify_lineage(bundle_dir, bundle, candidate)
    return bundle


def load_verified_jury_bundle_inputs(
    bundle_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return only verified, copied inputs for bundle-only release evaluation.

    This deliberately does not offer the caller a way to substitute an
    arbitrary candidate, report, or policy path after the bundle has been
    verified.  The built-in policy comparison remains part of verification;
    the returned policy is nevertheless parsed from the immutable bundle copy.
    """

    bundle = verify_jury_bundle(bundle_dir)
    candidate = _load_candidate(
        _relative_file(bundle_dir, bundle["candidate"]["path"], "candidate"),
        "bundled candidate",
    )
    policy = load_validated_json(
        _relative_file(bundle_dir, bundle["policy"]["path"], "policy"),
        APPROVED_POLICY_SCHEMA,
        "bundled governance policy",
    )
    reports = [
        _load_report(
            _relative_file(bundle_dir, ref["path"], "jury report"),
            candidate,
            policy,
        )
        for ref in bundle["reports"]
    ]
    return bundle, candidate, reports, policy


def _verify_lineage(bundle_dir: Path, bundle: dict[str, Any], candidate: dict[str, Any]) -> None:
    lineage = bundle["lineage"]
    if lineage["kind"] == "initial_jury":
        if lineage["origin_candidate_id"] != candidate["candidate_id"] or lineage["origin_snapshot_id"] != candidate["snapshot"]["snapshot_id"]:
            raise ValidationError("JURY-BUNDLE-INITIAL-LINEAGE-MISMATCH")
        return
    expected = {
        "blocker_case": (BLOCKER_CASE_SCHEMA, "case_hash", "blocker_case_id"),
        "rework_manifest": (REWORK_MANIFEST_SCHEMA, "manifest_hash", "rework_manifest_id"),
        "successor_retest_attestation": (SUCCESSOR_ATTESTATION_SCHEMA, "attestation_hash", "attestation_id"),
    }
    refs = {item["artifact_type"]: item for item in lineage["artifacts"]}
    if set(refs) != set(expected):
        raise ValidationError("JURY-BUNDLE-LINEAGE-ARTIFACTS-MISMATCH")
    loaded: dict[str, dict[str, Any]] = {}
    for artifact_type, (schema, self_field, id_field) in expected.items():
        ref = refs[artifact_type]
        path = _relative_file(bundle_dir, ref["path"], artifact_type)
        if _file_hash(path, artifact_type) != ref["sha256"]:
            raise ValidationError("JURY-BUNDLE-LINEAGE-HASH-MISMATCH")
        payload = load_validated_json(path, schema, artifact_type)
        verify_self_hash(payload, self_field)
        if payload[id_field] != ref["artifact_id"] or payload[self_field] != ref["self_hash"]:
            raise ValidationError("JURY-BUNDLE-LINEAGE-REF-MISMATCH")
        loaded[artifact_type] = payload
    case, manifest, attestation = (loaded["blocker_case"], loaded["rework_manifest"], loaded["successor_retest_attestation"])
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    report_refs = {item["report_id"]: item for item in bundle["reports"]}
    if (
        (lineage["origin_candidate_id"], lineage["origin_snapshot_id"])
        != (case["original"]["candidate_id"], case["original"]["snapshot_id"])
        or manifest["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or manifest["blocker_case"]["case_hash"] != case["case_hash"]
        or manifest["successor"]["candidate_id"] != candidate["candidate_id"]
        or manifest["successor"]["snapshot_id"] != snapshot_id
        or attestation["blocker_case"]["blocker_case_id"] != case["blocker_case_id"]
        or attestation["blocker_case"]["case_hash"] != case["case_hash"]
        or attestation["rework_manifest"]["rework_manifest_id"] != manifest["rework_manifest_id"]
        or attestation["rework_manifest"]["manifest_hash"] != manifest["manifest_hash"]
        or attestation["successor"]["candidate_id"] != candidate["candidate_id"]
        or attestation["successor"]["snapshot_id"] != snapshot_id
        or any(
            ref["report_id"] not in report_refs
            or report_refs[ref["report_id"]]["sha256"] != ref["sha256"]
            for ref in attestation["successor_reports"]
        )
    ):
        raise ValidationError("JURY-BUNDLE-LINEAGE-BINDING-MISMATCH")
