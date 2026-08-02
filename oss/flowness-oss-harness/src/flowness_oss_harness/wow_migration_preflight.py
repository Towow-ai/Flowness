"""Pure-local, fail-closed preflight for a planned Wow-Harness upgrade.

It deliberately consumes an explicit audit record only.  It does not invoke
Git, access a remote, create refs, transfer/rename a repository, or publish.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


SCHEMA = SCHEMAS_ROOT / "wow-migration-preflight.schema.json"
_ACTIONS = (
    "seal_public_export", "create_legacy_branch", "create_legacy_tag",
    "write_migration_guide", "classify_legacy_issues_and_prs",
    "create_replacement_commit", "verify_local_history_license_and_clean_install",
    "owner_gate_before_external_mutation",
)
_EXTERNAL = ("transfer_repository", "rename_repository", "publish_release")


def _verified(item: Any, code: str, *, absent: bool = False) -> None:
    if not isinstance(item, dict) or item.get("state") != ("verified_absent" if absent else "verified"):
        raise ValidationError(code)
    if not isinstance(item.get("evidence_ref"), str) or not item["evidence_ref"].strip():
        raise ValidationError(code)


def evaluate_wow_migration_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate audit bindings and return a report that cannot authorize release."""

    validate_payload(payload, SCHEMA, "Wow migration preflight")
    verify_self_hash(payload, "input_hash")
    audit = payload["legacy_audit"]
    _verified(audit["remote"], "WOW-MIGRATION-LEGACY-REMOTE-NOT-VERIFIED")
    _verified(audit["default_branch"], "WOW-MIGRATION-DEFAULT-BRANCH-NOT-VERIFIED")
    for name, code in (("legacy_branch", "WOW-MIGRATION-LEGACY-BRANCH-NOT-VERIFIED"), ("legacy_tag", "WOW-MIGRATION-LEGACY-TAG-NOT-VERIFIED")):
        _verified(audit[name], code, absent=True)
        if audit[name]["target_commit_sha"] != audit["remote"]["head_commit_sha"]:
            raise ValidationError(code + "-TARGET-MISMATCH")
    _verified(audit["rights"], "WOW-MIGRATION-RIGHTS-NOT-VERIFIED")
    _verified(audit["issue_attribution"], "WOW-MIGRATION-ISSUE-ATTRIBUTION-NOT-VERIFIED")
    if audit["issue_attribution"]["classification_state"] != "verified_for_preflight":
        raise ValidationError("WOW-MIGRATION-ISSUE-ATTRIBUTION-CLASSIFICATION-NOT-VERIFIED")
    if not audit["rights"]["approved_scope"].strip() or not audit["issue_attribution"]["classification_ref"].strip():
        raise ValidationError("WOW-MIGRATION-RIGHTS-OR-ATTRIBUTION-EVIDENCE-EMPTY")
    candidate = payload["candidate_identity"]
    if candidate["export_state"] != "sealed_export_verified" or not candidate["sealed_export_ref"].strip():
        raise ValidationError("WOW-MIGRATION-CANDIDATE-EXPORT-NOT-VERIFIED")
    plan = payload["migration_plan"]
    if tuple(step["action"] for step in plan["planned_steps"]) != _ACTIONS:
        raise ValidationError("WOW-MIGRATION-PLAN-ORDER-OR-COVERAGE-INVALID")
    if any(step["mode"] != "planned_only" for step in plan["planned_steps"]):
        raise ValidationError("WOW-MIGRATION-PLAN-MUST-REMAIN-PLANNED-ONLY")
    if tuple(plan["external_actions_after_owner_gate"]) != _EXTERNAL:
        raise ValidationError("WOW-MIGRATION-EXTERNAL-ACTIONS-INVALID")
    report = {
        "schema_version": "wow-migration-preflight-report/v1",
        "report_id": "wow-migration-preflight-" + canonical_hash({"id": payload["preflight_id"], "input": payload["input_hash"]}).removeprefix("sha256:")[:24],
        "scope": "private_staging_only",
        "state": "owner_gated_not_authorized",
        "input": {"preflight_id": payload["preflight_id"], "input_hash": payload["input_hash"], "legacy_audit_id": audit["audit_id"], "candidate_id": candidate["candidate_id"]},
        "verified_checks": ["remote/default branch", "legacy branch/tag availability", "rights", "issue/PR attribution", "sealed candidate export", "planned-only local sequence"],
        "external_actions_not_authorized": list(_EXTERNAL),
        "boundary": "Private staging evidence only; cannot create refs, transfer/rename a repository, publish, or replace owner approval.",
    }
    return {**report, "report_hash": canonical_hash(report)}


def evaluate_wow_migration_preflight_with_live_classification(
    payload: dict[str, Any],
    live_snapshot: dict[str, Any],
    legacy_classification: dict[str, Any],
) -> dict[str, Any]:
    """Bind the attribution preflight input to one exact read-only snapshot.

    The base preflight is deliberately file/reference oriented for a future
    sealed staging bundle.  This stricter adapter is the intake path for the
    first live audit: it refuses a hand-copied attribution summary unless the
    snapshot, its complete classification, remote head/default branch and the
    planned legacy-ref absence all agree.
    """

    from .wow_legacy_classification import evaluate_wow_legacy_classification

    classification_report = evaluate_wow_legacy_classification(
        live_snapshot, legacy_classification
    )
    expected = classification_report["preflight_issue_attribution_binding"]
    audit = payload["legacy_audit"]
    if audit["issue_attribution"] != expected:
        raise ValidationError("WOW-MIGRATION-LIVE-CLASSIFICATION-BINDING-MISMATCH")
    if audit["repository"] != live_snapshot["repository"]["name_with_owner"]:
        raise ValidationError("WOW-MIGRATION-LIVE-SNAPSHOT-REPOSITORY-MISMATCH")
    if audit["remote"]["head_commit_sha"] != live_snapshot["remote"]["head_commit_sha"]:
        raise ValidationError("WOW-MIGRATION-LIVE-SNAPSHOT-HEAD-MISMATCH")
    if audit["default_branch"]["name"] != live_snapshot["repository"]["default_branch"]:
        raise ValidationError("WOW-MIGRATION-LIVE-SNAPSHOT-DEFAULT-BRANCH-MISMATCH")
    ref_names = {row["name"] for row in live_snapshot["refs"]}
    for name in ("legacy_branch", "legacy_tag"):
        ref = audit[name]
        if ref["name"] in ref_names:
            raise ValidationError("WOW-MIGRATION-LIVE-SNAPSHOT-LEGACY-REF-PRESENT")
    return evaluate_wow_migration_preflight(payload)


def write_wow_migration_preflight_report(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Create-only local report writer; intentionally no Git or network calls."""

    payload = load_validated_json(input_path, SCHEMA, "Wow migration preflight")
    report = evaluate_wow_migration_preflight(payload)
    atomic_create_json(output_path, report)
    return report
