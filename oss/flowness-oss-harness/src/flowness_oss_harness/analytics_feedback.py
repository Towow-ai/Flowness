"""CM-008 private, aggregate-only analytics feedback contracts.

This module deliberately does not talk to any analytics provider, browser,
channel API, credential store, scheduler, or publisher.  Its inputs are
already-suppressed aggregate observations.  Its only output is a typed request
to review context under obligations which a separate Content Graph review plan
already created.  Attention or installation counts are therefore neither
claim evidence nor a product-value verdict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


OBSERVATIONS_SCHEMA = SCHEMAS_ROOT / "analytics-feedback-observations.schema.json"
INTERPRETATION_SCHEMA = SCHEMAS_ROOT / "analytics-feedback-interpretation.schema.json"
CONTRACT_ID = "content-machine/CM-008/v1"
OBSERVATION_BOUNDARY = (
    "private aggregate analytics observations only; no raw personal data, raw event records, "
    "claim mutation, product-value inference, publication, network collection, credential use, "
    "scheduling, or external send."
)
INTERPRETATION_BOUNDARY = (
    "private analytics review output only; it may request context review against existing Content Graph "
    "obligations but cannot mutate claims, evidence, candidate state, approvals, source analytics, publish, "
    "collect network data, use credentials, schedule, or send externally."
)
_MUTATION_ATTESTATION = {
    "claim_registry": "not_mutated",
    "evidence_registry": "not_mutated",
    "candidate_state": "not_mutated",
    "approval_state": "not_mutated",
    "analytics_source_data": "not_mutated",
}
_EFFECT_ATTESTATION = {
    "publish": "not_attempted",
    "network": "not_attempted",
    "credential_use": "not_attempted",
    "external_send": "not_attempted",
    "schedule": "not_attempted",
}
_INFERENCE_TERMS = (
    "product value", "business value", "proves", "proven", "validates", "validated",
    "effective", "effectiveness", "better", "outperforms", "success metric",
)


def _parse_instant(value: str, error: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValidationError(error) from exc
    if parsed.tzinfo is None:
        raise ValidationError(error)
    return parsed.astimezone(timezone.utc)


def _bundle_unsigned(bundle: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bundle.items() if key != "bundle_hash"}


def _bundle_id(unsigned: Mapping[str, Any]) -> str:
    seed = dict(unsigned)
    seed["bundle_id"] = ""
    return "analytics-feedback-" + canonical_hash(seed).removeprefix("sha256:")[:24]


def _interpretation_unsigned(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "interpretation_hash"}


def _interpretation_id(unsigned: Mapping[str, Any]) -> str:
    seed = dict(unsigned)
    seed["interpretation_id"] = ""
    return "analytics-interpretation-" + canonical_hash(seed).removeprefix("sha256:")[:24]


def feedback_identity(bundle: Mapping[str, Any]) -> dict[str, str]:
    """Return the narrow identity an interpreter may consume after validation."""

    return {
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle["bundle_hash"],
        "verification": "verified_private_aggregate_analytics",
    }


def _verified_feedback_identity(verification: Mapping[str, Any]) -> dict[str, str]:
    """Narrow a verifier result before comparing it to an agent output."""

    try:
        return {
            "bundle_id": verification["bundle_id"],
            "bundle_hash": verification["bundle_hash"],
            "verification": verification["verification"],
        }
    except KeyError as exc:
        raise ValidationError("ANALYTICS-INTERPRETATION-FEEDBACK-INVALID") from exc


def _require_exact(value: Any, expected: Any, error: str) -> None:
    if value != expected:
        raise ValidationError(error)


def _require_graph_identity(bundle: Mapping[str, Any], expected_graph: Mapping[str, str]) -> None:
    _require_exact(bundle["source_graph"], dict(expected_graph), "ANALYTICS-FEEDBACK-GRAPH-BINDING-MISMATCH")


def validate_analytics_feedback_observations(
    bundle: Any,
    *,
    expected_graph: Mapping[str, str],
    expected_channel_package: Mapping[str, str],
) -> dict[str, Any]:
    """Validate a sealed aggregate-only input without collecting any data.

    ``expected_graph`` and ``expected_channel_package`` must come from
    independently verified Content Graph v3 and manual channel-package inputs.
    This means a collector cannot make an unrelated package look attributable
    merely by self-reporting its identifiers.
    """

    validate_payload(bundle, OBSERVATIONS_SCHEMA, "analytics feedback observations")
    verify_self_hash(bundle, "bundle_hash")
    unsigned = _bundle_unsigned(bundle)
    if bundle["bundle_id"] != _bundle_id(unsigned):
        raise ValidationError("ANALYTICS-FEEDBACK-BUNDLE-ID-MISMATCH")
    _require_exact(bundle["boundary"], OBSERVATION_BOUNDARY, "ANALYTICS-FEEDBACK-BOUNDARY-INVALID")
    _require_graph_identity(bundle, expected_graph)
    _require_exact(
        bundle["channel_package"], dict(expected_channel_package),
        "ANALYTICS-FEEDBACK-PACKAGE-BINDING-MISMATCH",
    )

    provenance = bundle["provenance"]
    expected_method = {
        "synthetic_fixture": "fixture_generated",
        "private_aggregate_export": "manual_private_aggregate_export",
    }[provenance["source_class"]]
    _require_exact(provenance["collection_method"], expected_method, "ANALYTICS-FEEDBACK-PROVENANCE-INVALID")
    captured_at = _parse_instant(provenance["captured_at"], "ANALYTICS-FEEDBACK-TIME-INVALID")
    window = bundle["observation_window"]
    start = _parse_instant(window["start"], "ANALYTICS-FEEDBACK-WINDOW-INVALID")
    end = _parse_instant(window["end"], "ANALYTICS-FEEDBACK-WINDOW-INVALID")
    if start >= end or captured_at < end:
        raise ValidationError("ANALYTICS-FEEDBACK-WINDOW-INVALID")

    privacy = bundle["privacy"]
    minimum_group_size = privacy["minimum_group_size"]
    seen_ids: set[str] = set()
    seen_metric_shapes: set[tuple[str, str, str]] = set()
    for observation in bundle["observations"]:
        observation_id = observation["observation_id"]
        if observation_id in seen_ids:
            raise ValidationError("ANALYTICS-FEEDBACK-OBSERVATION-DUPLICATE")
        seen_ids.add(observation_id)
        shape = (observation["metric"], observation["aggregation"], observation["unit"])
        if shape in seen_metric_shapes:
            raise ValidationError("ANALYTICS-FEEDBACK-OBSERVATION-DUPLICATE")
        seen_metric_shapes.add(shape)
        if observation["aggregation"] == "event_count":
            if observation["denominator"] != 0 or observation["count"] < minimum_group_size:
                raise ValidationError("ANALYTICS-FEEDBACK-SMALL-GROUP-OR-COUNT-INVALID")
        elif (
            observation["denominator"] < minimum_group_size
            or observation["count"] > observation["denominator"]
        ):
            raise ValidationError("ANALYTICS-FEEDBACK-SMALL-GROUP-OR-RATE-INVALID")
    return {
        "schema_version": "analytics-feedback-observations-verification/v1",
        **feedback_identity(bundle),
        "candidate_id": bundle["source_graph"]["candidate_id"],
        "channel_id": bundle["channel_package"]["channel_id"],
        "metric_ids": sorted(observation["observation_id"] for observation in bundle["observations"]),
        "state": "private_descriptive_aggregate_only",
        "boundary": OBSERVATION_BOUNDARY,
    }


def _plan_identity(plan: Mapping[str, Any]) -> dict[str, str]:
    return {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "verification": "verified_content_impact_review_plan",
    }


def _assert_plan_graph(plan: Mapping[str, Any], expected_graph: Mapping[str, str]) -> None:
    current = plan.get("current")
    if not isinstance(current, Mapping):
        raise ValidationError("ANALYTICS-INTERPRETATION-PLAN-INVALID")
    for key in ("graph_id", "graph_hash", "candidate_id", "snapshot_id", "version_id"):
        if current.get(key) != expected_graph.get(key):
            raise ValidationError("ANALYTICS-INTERPRETATION-PLAN-GRAPH-MISMATCH")


def validate_analytics_interpretation(
    payload: Any,
    *,
    expected_graph: Mapping[str, str],
    verified_review_plan: Mapping[str, Any],
    verified_feedback: Mapping[str, str],
) -> dict[str, Any]:
    """Validate a descriptive-only interpreter output against a fixed plan.

    This is intentionally an interpreter *review* contract, not a feedback
    loop which can change a claim, invent an affected asset, or publish a
    channel package.  The caller must pass an independently verified review
    plan and feedback identity rather than trusting any agent-described input.
    """

    validate_payload(payload, INTERPRETATION_SCHEMA, "analytics feedback interpretation")
    verify_self_hash(payload, "interpretation_hash")
    unsigned = _interpretation_unsigned(payload)
    if payload["interpretation_id"] != _interpretation_id(unsigned):
        raise ValidationError("ANALYTICS-INTERPRETATION-ID-MISMATCH")
    _require_exact(payload["contract_id"], CONTRACT_ID, "ANALYTICS-INTERPRETATION-CONTRACT-INVALID")
    _require_exact(payload["boundary"], INTERPRETATION_BOUNDARY, "ANALYTICS-INTERPRETATION-BOUNDARY-INVALID")
    _require_exact(payload["source_graph"], dict(expected_graph), "ANALYTICS-INTERPRETATION-GRAPH-BINDING-MISMATCH")
    _require_exact(payload["impact_review_plan"], _plan_identity(verified_review_plan), "ANALYTICS-INTERPRETATION-PLAN-BINDING-MISMATCH")
    _require_exact(
        payload["input_bundle"], _verified_feedback_identity(verified_feedback),
        "ANALYTICS-INTERPRETATION-BUNDLE-BINDING-MISMATCH",
    )
    _require_exact(payload["mutation_attestation"], _MUTATION_ATTESTATION, "ANALYTICS-INTERPRETATION-MUTATION-INVALID")
    _require_exact(payload["effect_attestation"], _EFFECT_ATTESTATION, "ANALYTICS-INTERPRETATION-EFFECT-INVALID")
    _assert_plan_graph(verified_review_plan, expected_graph)

    known_observations = set(verified_feedback.get("metric_ids", ()))
    seen_findings: set[str] = set()
    for finding in payload["findings"]:
        if finding["finding_id"] in seen_findings:
            raise ValidationError("ANALYTICS-INTERPRETATION-FINDING-DUPLICATE")
        seen_findings.add(finding["finding_id"])
        if not set(finding["observation_ids"]).issubset(known_observations):
            raise ValidationError("ANALYTICS-INTERPRETATION-OBSERVATION-UNKNOWN")
        statement = finding["statement"].lower()
        if any(term in statement for term in _INFERENCE_TERMS):
            raise ValidationError("ANALYTICS-INTERPRETATION-PRODUCT-VALUE-INFERENCE")

    obligations = {
        item["obligation_id"]: item["target_id"]
        for item in verified_review_plan.get("review_obligations", ())
        if isinstance(item, Mapping)
        and isinstance(item.get("obligation_id"), str)
        and isinstance(item.get("target_id"), str)
    }
    seen_links: set[str] = set()
    for link in payload["review_links"]:
        if link["link_id"] in seen_links:
            raise ValidationError("ANALYTICS-INTERPRETATION-LINK-DUPLICATE")
        seen_links.add(link["link_id"])
        if not set(link["finding_ids"]).issubset(seen_findings):
            raise ValidationError("ANALYTICS-INTERPRETATION-FINDING-UNKNOWN")
        if not set(link["plan_obligation_ids"]).issubset(obligations):
            raise ValidationError("ANALYTICS-INTERPRETATION-OBLIGATION-UNKNOWN")
        expected_targets = {obligations[obligation_id] for obligation_id in link["plan_obligation_ids"]}
        if set(link["target_ids"]) != expected_targets:
            raise ValidationError("ANALYTICS-INTERPRETATION-TARGET-MISMATCH")
    return {
        "schema_version": "analytics-feedback-interpretation-verification/v1",
        "interpretation_id": payload["interpretation_id"],
        "interpretation_hash": payload["interpretation_hash"],
        "input_bundle": dict(verified_feedback),
        "review_link_count": len(payload["review_links"]),
        "state": "private_context_review_only",
        "boundary": INTERPRETATION_BOUNDARY,
    }
