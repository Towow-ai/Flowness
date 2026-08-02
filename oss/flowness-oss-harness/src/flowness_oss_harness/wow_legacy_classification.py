"""Fail-closed intake and item-complete classification for Wow continuity.

The module is intentionally pure-local.  A future collector can provide a
verified *read-only* live snapshot, after which this code makes the exact ref,
issue, PR and contributor set reviewable.  It never invokes GitHub/Git, creates
refs, changes labels, transfers a repository, or converts a plan into owner
authorization.
"""

from __future__ import annotations

from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SNAPSHOT_SCHEMA = SCHEMAS_ROOT / "wow-live-readonly-snapshot.schema.json"
CLASSIFICATION_SCHEMA = SCHEMAS_ROOT / "wow-legacy-classification.schema.json"


def _ids(rows: list[dict[str, Any]], key: str, code: str) -> set[str]:
    values = [row[key] for row in rows]
    if len(values) != len(set(values)):
        raise ValidationError(code)
    return set(values)


def _snapshot_item_ids(snapshot: dict[str, Any]) -> set[str]:
    refs = _ids(snapshot["refs"], "ref_id", "WOW-LIVE-SNAPSHOT-REF-DUPLICATE")
    issues = _ids(snapshot["issues"], "node_id", "WOW-LIVE-SNAPSHOT-ISSUE-DUPLICATE")
    prs = _ids(snapshot["pull_requests"], "node_id", "WOW-LIVE-SNAPSHOT-PR-DUPLICATE")
    if issues & prs or refs & (issues | prs):
        raise ValidationError("WOW-LIVE-SNAPSHOT-OBJECT-ID-COLLISION")
    return refs | issues | prs


def evaluate_wow_live_readonly_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate a live, independently verified, read-only snapshot.

    This establishes only an intake boundary.  It does not assert rights,
    current Flowness quality, or permission to mutate the Wow repository.
    """

    validate_payload(snapshot, SNAPSHOT_SCHEMA, "Wow live read-only snapshot")
    verify_self_hash(snapshot, "snapshot_hash")
    if snapshot["provenance"]["collector_id"] == snapshot["verification"]["verifier_id"]:
        raise ValidationError("WOW-LIVE-SNAPSHOT-COLLECTOR-VERIFIER-NOT-INDEPENDENT")
    item_ids = _snapshot_item_ids(snapshot)
    contributors = _ids(
        snapshot["contributors"], "node_id", "WOW-LIVE-SNAPSHOT-CONTRIBUTOR-DUPLICATE"
    )
    for contributor in snapshot["contributors"]:
        if not set(contributor["source_object_ids"]).issubset(item_ids):
            raise ValidationError("WOW-LIVE-SNAPSHOT-CONTRIBUTOR-SOURCE-OUTSIDE-INVENTORY")
    for work_item in [*snapshot["issues"], *snapshot["pull_requests"]]:
        if work_item["author_node_id"] not in contributors:
            raise ValidationError("WOW-LIVE-SNAPSHOT-WORK-ITEM-AUTHOR-NOT-ATTRIBUTED")
    report = {
        "schema_version": "wow-live-readonly-snapshot-report/v1",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
        "scope": "private_staging_only",
        "state": "accepted_for_private_legacy_classification_only",
        "repository": snapshot["repository"]["name_with_owner"],
        "inventory": {
            "refs": len(snapshot["refs"]),
            "issues": len(snapshot["issues"]),
            "pull_requests": len(snapshot["pull_requests"]),
            "contributors": len(snapshot["contributors"]),
        },
        "not_proven": [
            "Flowness v1 validation from Wow stars or forks",
            "rights approval",
            "sealed public export",
            "owner authorization for repository mutation",
        ],
    }
    return {**report, "report_hash": canonical_hash(report)}


def build_wow_legacy_classification_template(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Create the only allowable pending template for a snapshot inventory."""

    evaluate_wow_live_readonly_snapshot(snapshot)
    payload = {
        "schema_version": "wow-legacy-classification/v1",
        "classification_id": "wow-legacy-classification-"
        + snapshot["snapshot_hash"].removeprefix("sha256:")[:24],
        "scope": "private_staging_only",
        "snapshot": {
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_hash": snapshot["snapshot_hash"],
        },
        "state": "template_pending",
        "mode": "planned_only",
        "review": {
            "state": "not_reviewed",
            "reviewer_id": "pending-independent-reviewer",
            "evidence_ref": "pending-private-classification-review",
        },
        "refs": [
            {"ref_id": row["ref_id"], "treatment": "needs_owner_resolution"}
            for row in snapshot["refs"]
        ],
        "issues": [
            {"node_id": row["node_id"], "disposition": "needs-owner-resolution"}
            for row in snapshot["issues"]
        ],
        "pull_requests": [
            {"node_id": row["node_id"], "disposition": "needs-owner-resolution"}
            for row in snapshot["pull_requests"]
        ],
        "contributors": [
            {"node_id": row["node_id"], "treatment": "needs-rights-review"}
            for row in snapshot["contributors"]
        ],
    }
    return {**payload, "classification_hash": canonical_hash(payload)}


def _exact_coverage(
    snapshot_rows: list[dict[str, Any]],
    resolution_rows: list[dict[str, Any]],
    key: str,
    code: str,
) -> None:
    if _ids(snapshot_rows, key, code + "-SNAPSHOT-DUPLICATE") != _ids(
        resolution_rows, key, code + "-CLASSIFICATION-DUPLICATE"
    ):
        raise ValidationError(code + "-COVERAGE-MISMATCH")


def evaluate_wow_legacy_classification(
    snapshot: dict[str, Any], classification: dict[str, Any]
) -> dict[str, Any]:
    """Validate exact legacy classifications and return a preflight binding.

    A `template_pending` payload is intentionally a blocker.  Only an
    independently reviewed, all-item classification can become a *preflight
    input*; even that remains planned-only and cannot authorize a mutation.
    """

    evaluate_wow_live_readonly_snapshot(snapshot)
    validate_payload(classification, CLASSIFICATION_SCHEMA, "Wow legacy classification")
    verify_self_hash(classification, "classification_hash")
    if classification["snapshot"] != {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": snapshot["snapshot_hash"],
    }:
        raise ValidationError("WOW-LEGACY-CLASSIFICATION-SNAPSHOT-MISMATCH")
    _exact_coverage(snapshot["refs"], classification["refs"], "ref_id", "WOW-LEGACY-REF")
    _exact_coverage(snapshot["issues"], classification["issues"], "node_id", "WOW-LEGACY-ISSUE")
    _exact_coverage(snapshot["pull_requests"], classification["pull_requests"], "node_id", "WOW-LEGACY-PR")
    _exact_coverage(snapshot["contributors"], classification["contributors"], "node_id", "WOW-LEGACY-CONTRIBUTOR")

    ready = classification["state"] == "verified_for_preflight"
    review = classification["review"]
    if ready:
        if review["state"] != "independently_verified":
            raise ValidationError("WOW-LEGACY-CLASSIFICATION-REVIEW-NOT-VERIFIED")
        if review["reviewer_id"] == snapshot["provenance"]["collector_id"]:
            raise ValidationError("WOW-LEGACY-CLASSIFICATION-REVIEW-NOT-INDEPENDENT")
        unresolved = (
            any(row["treatment"] == "needs_owner_resolution" for row in classification["refs"])
            or any(row["disposition"] == "needs-owner-resolution" for row in [*classification["issues"], *classification["pull_requests"]])
            or any(row["treatment"] == "needs-rights-review" for row in classification["contributors"])
        )
        if unresolved:
            raise ValidationError("WOW-LEGACY-CLASSIFICATION-UNRESOLVED")
    elif review["state"] != "not_reviewed":
        raise ValidationError("WOW-LEGACY-CLASSIFICATION-PENDING-REVIEW-INVALID")

    report = {
        "schema_version": "wow-legacy-classification-report/v1",
        "classification_id": classification["classification_id"],
        "classification_hash": classification["classification_hash"],
        "snapshot": classification["snapshot"],
        "scope": "private_staging_only",
        "state": "verified_preflight_input_not_owner_authorization" if ready else "pending_classification_not_preflight_eligible",
        "counts": {
            "refs": len(classification["refs"]),
            "issues": len(classification["issues"]),
            "pull_requests": len(classification["pull_requests"]),
            "contributors": len(classification["contributors"]),
        },
        "preflight_issue_attribution_binding": {
            "state": "verified" if ready else "unverified",
            "classification_state": classification["state"],
            "classification_hash": classification["classification_hash"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "issue_count": len(classification["issues"]),
            "pull_request_count": len(classification["pull_requests"]),
            "contributor_count": len(classification["contributors"]),
            "evidence_ref": review["evidence_ref"],
            "classification_ref": "private:" + classification["classification_id"],
        },
        "not_authorized": [
            "create or move a ref",
            "apply issue or PR labels",
            "transfer or rename repository",
            "publish a release",
        ],
    }
    return {**report, "report_hash": canonical_hash(report)}
