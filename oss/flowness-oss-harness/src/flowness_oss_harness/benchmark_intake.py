"""Fail-closed validation for the private comparator-intake queue.

An intake candidate is not a benchmark claim.  In particular, a familiar
repository URL or a previously seen star count cannot promote an entry until a
primary source is fetched and pinned.
"""

from __future__ import annotations

from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "benchmark-intake.schema.json"


def evaluate_benchmark_intake(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate that an unfetched intake cannot yield an external comparison."""

    validate_payload(payload, SCHEMA, "benchmark intake")
    verify_self_hash(payload, "intake_hash")
    if payload["intake_state"] != "blocked_external_research_credits":
        raise ValidationError("BENCHMARK-INTAKE-MUST-REMAIN-CREDIT-BLOCKED")
    credits = payload["credit_observation"]
    if credits["remaining_credits"] >= credits["minimum_discovered_primary_search_credits"]:
        raise ValidationError("BENCHMARK-INTAKE-CREDIT-BLOCKER-INVALID")

    projects = payload["projects"]
    for project in projects:
        if project["star_status"] != "unobserved_not_eligible_for_high_star_claim":
            raise ValidationError("BENCHMARK-INTAKE-STAR-CLAIM-NOT-PERMITTED")
        if project["comparison_state"] != "pending_primary_evidence":
            raise ValidationError("BENCHMARK-INTAKE-COMPARISON-PROMOTION-NOT-PERMITTED")
        if any(
            source["fetch_state"] != "not_fetched_credit_blocked"
            or source["fetched_at"] is not None
            or source["immutable_version"] is not None
            for source in project["source_plan"]
        ):
            raise ValidationError("BENCHMARK-INTAKE-UNFETCHED-SOURCE-INVALID")

    boundary = payload["comparison_boundary"]
    if any(
        boundary[key]
        for key in (
            "external_claims_permitted",
            "comparative_verdict_permitted",
            "star_ranking_permitted",
        )
    ):
        raise ValidationError("BENCHMARK-INTAKE-EXTERNAL-CLAIM-NOT-PERMITTED")

    report = {
        "schema_version": "benchmark-intake-report/v1",
        "intake_id": payload["intake_id"],
        "scope": payload["scope"],
        "state": "blocked_external_research_credits",
        "candidate_count": len(projects),
        "primary_evidence_count": 0,
        "high_star_eligible_candidate_count": 0,
        "comparative_verdict_permitted": False,
        "next_condition": "obtain sufficient research credits then collect and pin primary-source evidence",
    }
    return {**report, "report_hash": canonical_hash(report)}
