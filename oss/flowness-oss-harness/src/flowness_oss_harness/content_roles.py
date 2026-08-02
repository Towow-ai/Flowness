"""Static CM-007 contracts for bounded content-machine roles.

These contracts are deliberately narrower than a general agent permission
model.  They describe only private-staging *draft* and *review* work.  They
do not start agents, mutate a Content Graph, publish, schedule, use network
credentials, or interpret a draft as an approval.  Runtime execution remains
subject to the separately frozen execution policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


CONTRACT_ID = "content-machine/CM-007/v1"
CONTENT_OUTPUT_SCHEMA = "content-role-output.schema.json"
CONTENT_PRODUCER_OUTPUTS = {
    "content.compiler": "content_draft",
    "visual_demo.compiler": "visual_demo_draft",
    "channel.adapter": "channel_adapter_draft",
    "publisher.stager": "package_review",
    "analytics.interpreter": "analytics_interpretation",
}
CONTENT_PRODUCER_ARTIFACT_TYPES = {
    "content.compiler": {"article"},
    "visual_demo.compiler": {"diagram_brief", "demo_brief"},
    "channel.adapter": {"channel_copy"},
    "publisher.stager": {"manual_package_review"},
    "analytics.interpreter": {"analytics_note"},
}
CHANNEL_JUDGES = {
    "judge.channel-distribution-a",
    "judge.channel-distribution-b",
}
FORBIDDEN_MUTATIONS = [
    "claim_registry",
    "evidence_registry",
    "candidate_state",
    "approval_state",
    "analytics_source_data",
]
FORBIDDEN_CAPABILITIES = [
    "publish",
    "network",
    "credential_use",
    "external_send",
    "schedule",
]
OUTPUT_SCHEMA = SCHEMAS_ROOT / CONTENT_OUTPUT_SCHEMA


def is_content_machine_role(role_id: str) -> bool:
    return role_id in CONTENT_PRODUCER_OUTPUTS or role_id in CHANNEL_JUDGES


def _require_exact(value: Any, expected: Any, error: str) -> None:
    if value != expected:
        raise ValidationError(error)


def validate_content_role_contract(
    *, role_id: str, kind: str, output_schema: str, contract: Any
) -> None:
    """Reject any CM-007 role whose authority or output shape drifts.

    This is intentionally a registry-level check, so it can be applied before
    a controller creates role directories or constructs a command line.
    """

    if not is_content_machine_role(role_id):
        if contract is not None:
            raise ValidationError("CONTENT-ROLE-CONTRACT-UNEXPECTED")
        return
    if not isinstance(contract, dict):
        raise ValidationError("CONTENT-ROLE-CONTRACT-MISSING")
    expected_common = {
        "contract_id": CONTRACT_ID,
        "forbidden_mutations": FORBIDDEN_MUTATIONS,
        "forbidden_capabilities": FORBIDDEN_CAPABILITIES,
        "external_effects": "forbidden",
    }
    for key, expected in expected_common.items():
        _require_exact(contract.get(key), expected, "CONTENT-ROLE-CONTRACT-BOUNDARY-INVALID")
    boundary = contract.get("output_boundary")
    if boundary != f"role-private/{role_id}":
        raise ValidationError("CONTENT-ROLE-CONTRACT-OUTPUT-BOUNDARY-INVALID")
    if role_id in CONTENT_PRODUCER_OUTPUTS:
        _require_exact(kind, "producer", "CONTENT-ROLE-CONTRACT-KIND-INVALID")
        _require_exact(output_schema, CONTENT_OUTPUT_SCHEMA, "CONTENT-ROLE-CONTRACT-SCHEMA-INVALID")
        _require_exact(
            contract.get("input_contract"),
            "sealed_verified_content_graph_v3_and_impact_review_plan",
            "CONTENT-ROLE-CONTRACT-INPUT-INVALID",
        )
        _require_exact(
            contract.get("output_kind"),
            CONTENT_PRODUCER_OUTPUTS[role_id],
            "CONTENT-ROLE-CONTRACT-OUTPUT-KIND-INVALID",
        )
        _require_exact(contract.get("blindness_required"), True, "CONTENT-ROLE-CONTRACT-BLINDNESS-INVALID")
        return
    _require_exact(kind, "judge", "CONTENT-ROLE-CONTRACT-KIND-INVALID")
    _require_exact(output_schema, "jury-report.schema.json", "CONTENT-ROLE-CONTRACT-SCHEMA-INVALID")
    _require_exact(
        contract.get("input_contract"),
        "blind_sealed_channel_package_review_only",
        "CONTENT-ROLE-CONTRACT-INPUT-INVALID",
    )
    _require_exact(contract.get("output_kind"), "jury_report", "CONTENT-ROLE-CONTRACT-OUTPUT-KIND-INVALID")
    _require_exact(contract.get("blindness_required"), True, "CONTENT-ROLE-CONTRACT-BLINDNESS-INVALID")


def validate_content_role_output(
    payload: Any,
    *,
    role_id: str,
    expected_graph: Mapping[str, str],
    expected_review_plan: Mapping[str, str],
) -> dict[str, Any]:
    """Validate a typed draft against already-verified graph/plan identities.

    The caller supplies identities returned by independent Content Graph v3 and
    impact-plan verification.  This avoids treating an agent's self-described
    path, candidate, claim, evidence, or approval as trusted input.
    """

    if role_id not in CONTENT_PRODUCER_OUTPUTS:
        raise ValidationError("CONTENT-ROLE-OUTPUT-ROLE-INVALID")
    validate_payload(payload, OUTPUT_SCHEMA, "content role output")
    _require_exact(payload["role_id"], role_id, "CONTENT-ROLE-OUTPUT-ROLE-INVALID")
    _require_exact(payload["contract_id"], CONTRACT_ID, "CONTENT-ROLE-OUTPUT-CONTRACT-INVALID")
    _require_exact(
        payload["output_kind"], CONTENT_PRODUCER_OUTPUTS[role_id], "CONTENT-ROLE-OUTPUT-KIND-INVALID"
    )
    allowed_artifacts = CONTENT_PRODUCER_ARTIFACT_TYPES[role_id]
    if any(draft["artifact_type"] not in allowed_artifacts for draft in payload["drafts"]):
        raise ValidationError("CONTENT-ROLE-OUTPUT-ARTIFACT-TYPE-INVALID")
    actual_graph = payload["source_graph"]
    if dict(actual_graph) != dict(expected_graph):
        raise ValidationError("CONTENT-ROLE-OUTPUT-GRAPH-BINDING-MISMATCH")
    actual_plan = payload["impact_review_plan"]
    if dict(actual_plan) != dict(expected_review_plan):
        raise ValidationError("CONTENT-ROLE-OUTPUT-PLAN-BINDING-MISMATCH")
    _require_exact(payload["mutation_attestation"], {
        "claim_registry": "not_mutated",
        "evidence_registry": "not_mutated",
        "candidate_state": "not_mutated",
        "approval_state": "not_mutated",
        "analytics_source_data": "not_mutated",
    }, "CONTENT-ROLE-OUTPUT-MUTATION-ATTESTATION-INVALID")
    _require_exact(payload["effect_attestation"], {
        "publish": "not_attempted",
        "network": "not_attempted",
        "credential_use": "not_attempted",
        "external_send": "not_attempted",
        "schedule": "not_attempted",
    }, "CONTENT-ROLE-OUTPUT-EFFECT-ATTESTATION-INVALID")
    return {
        "schema_version": "content-role-output-verification/v1",
        "role_id": role_id,
        "output_kind": payload["output_kind"],
        "state": "private_draft_only",
        "boundary": "verified input identities; typed output only; no mutation or external effect authorized.",
    }
