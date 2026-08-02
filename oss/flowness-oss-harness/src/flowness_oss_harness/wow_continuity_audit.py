"""Validate a bounded, read-only Wow-Harness continuity observation.

This is deliberately not a replacement for ``wow_migration_preflight``.  The
observation records exactly which remote facts were seen and which required
preflight inputs remain unknown.  It neither synthesizes a Git head/ref, nor
authorizes a repository mutation.
"""

from __future__ import annotations

from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "wow-continuity-audit-observation.schema.json"


def evaluate_wow_continuity_audit_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an immutable, fail-closed summary for the preflight consumer.

    The current preflight intentionally requires a fully verified legacy audit.
    This function makes that incompatibility visible instead of allowing partial
    GitHub metadata to masquerade as head/ref, attribution, or rights evidence.
    """

    validate_payload(payload, SCHEMA, "Wow continuity audit observation")
    verify_self_hash(payload, "audit_hash")
    binding = payload["preflight_binding"]
    if binding["consumer"] != "wow_migration_preflight":
        raise ValidationError("WOW-CONTINUITY-AUDIT-CONSUMER-INVALID")
    if binding["state"] != "blocked_missing_required_observations":
        raise ValidationError("WOW-CONTINUITY-AUDIT-MUST-REMAIN-BLOCKED")
    if binding["verified_bindings"] != [
        "legacy_audit.repository",
        "legacy_audit.default_branch.name",
    ]:
        raise ValidationError("WOW-CONTINUITY-AUDIT-VERIFIED-BINDINGS-INVALID")
    required_unknowns = binding["required_unknowns"]
    if len(required_unknowns) != len(set(required_unknowns)):
        raise ValidationError("WOW-CONTINUITY-AUDIT-UNKNOWN-REQUIREMENTS-DUPLICATE")

    repository = payload["github_repository"]
    report = {
        "schema_version": "wow-continuity-audit-report/v1",
        "audit_id": payload["audit_id"],
        "scope": "private_staging_only",
        "state": "partial_observed_not_preflight_eligible",
        "observation": {
            "observed_at": payload["provenance"]["observed_at"],
            "repository": repository["name_with_owner"],
            "repository_node_id": repository["node_id"],
            "default_branch": repository["default_branch"],
            "license_spdx": repository["license_spdx"],
            "open_issues_count": repository["open_issues_count"],
            "issue_or_pr_item_inventory_observed": False,
        },
        "preflight_consumer": {
            "consumer": binding["consumer"],
            "state": binding["state"],
            "verified_bindings": binding["verified_bindings"],
            "required_unknowns": required_unknowns,
        },
        "not_authorized": [
            "create legacy branch or tag",
            "classify issues or pull requests",
            "make replacement commit",
            "transfer or rename repository",
            "publish release",
        ],
        "boundary": (
            "Read-only GitHub metadata is an audit input only. It does not prove "
            "a remote head, historical refs/tags, item-level attribution, rights, "
            "or owner authorization."
        ),
    }
    return {**report, "report_hash": canonical_hash(report)}
