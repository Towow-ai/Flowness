from __future__ import annotations

"""Fail-closed contract checks for a future server-side Evidence Seal.

This module deliberately has no transport, SSH, filesystem-discovery, vault, or
redaction capability.  It only validates three *already prepared* JSON records:
an immutable, field-level collection request; a collector receipt; and an
independent verifier report.  Passing these checks means that a proposed
evidence chain is internally coherent enough for a later registry review.  It
never proves that a server was read, that an upstream redactor was correct, or
that a mechanism is ``current_verified``.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


REQUEST_SCHEMA = SCHEMAS_ROOT / "runtime-evidence-seal-request.schema.json"
RECEIPT_SCHEMA = SCHEMAS_ROOT / "runtime-evidence-seal-collector-receipt.schema.json"
VERIFIER_SCHEMA = SCHEMAS_ROOT / "runtime-evidence-seal-verifier-report.schema.json"

CHAIN_ROLES = (
    "producer",
    "event",
    "projection",
    "consumer",
    "failure_recovery",
    "postcondition",
)
OBJECT_CLASS_BY_ROLE = {
    "source_identity": "source_identity",
    "producer": "producer_observation",
    "event": "event_segment",
    "projection": "projection_state",
    "consumer": "consumer_observation",
    "failure_recovery": "failure_recovery",
    "postcondition": "independent_postcondition",
}
REQUIRED_OBJECT_CLASSES = tuple(OBJECT_CLASS_BY_ROLE.values())
CHECK_IDS = (
    "scope_freeze",
    "source_identity",
    "event_coherence",
    "projection_coherence",
    "consumer_link",
    "failure_recovery",
    "redaction_derivation",
    "independent_postcondition",
)
CHECK_OBJECT_CLASSES = {
    "scope_freeze": set(REQUIRED_OBJECT_CLASSES),
    "source_identity": {"source_identity"},
    "event_coherence": {"event_segment"},
    "projection_coherence": {"projection_state"},
    "consumer_link": {"consumer_observation"},
    "failure_recovery": {"failure_recovery"},
    "redaction_derivation": set(REQUIRED_OBJECT_CLASSES),
    "independent_postcondition": {"independent_postcondition"},
}
MAX_CUTOFF_WINDOW = timedelta(hours=24)
REQUIRED_EXCLUSIONS = {
    "credentials_and_secrets",
    "raw_transcript_prompt_response",
    "customer_content_and_identifiers",
    "host_network_and_private_paths",
    "environment_and_configuration_values",
    "undeclared_objects_or_fields",
}
ALLOWED_FIELDS = {
    "source_identity": {
        "build_identity_hash", "commit_hash", "tree_hash", "clean_state",
    },
    "producer_observation": {
        "subject_token", "producer_kind", "observation_bucket", "source_identity_ref",
    },
    "event_segment": {
        "event_type", "sequence", "sentinel_relation", "transition_code",
    },
    "projection_state": {
        "projection_name", "watermark", "state_hash", "meta_hash",
    },
    "consumer_observation": {
        "consumer_kind", "subject_token", "watermark", "decision_code",
    },
    "failure_recovery": {
        "failure_category", "fingerprint_hash", "recovery_action_code",
        "pre_state_hash", "post_state_hash",
    },
    "independent_postcondition": {
        "postcondition_code", "observation_kind", "state_hash",
    },
}
_DENIED_KEY = re.compile(
    r"(?i)(?:password|passwd|secret|credential|token(?!_policy|_strategy)|"
    r"authorization|api[_-]?key|cookie|command|path|hostname|(?:^|_)ip(?:_|$)|email|"
    r"locator|config(?:uration)?[_-]?value|transcript|prompt|response)"
)
_DENIED_TEXT = (
    re.compile(r"(?:/Users|/home|/srv|/opt|/root)/[^/\s]+/"),
    re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
    re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _screen(value: Any, path: tuple[str, ...] = ()) -> None:
    """Reject raw-ish fields before they can become a protocol artifact."""

    if isinstance(value, str):
        if len(value) > 512 or "\x00" in value or any(pattern.search(value) for pattern in _DENIED_TEXT):
            raise ValidationError("RUNTIME-SEAL-PROTOCOL-UNSAFE-CONTENT")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, list):
        for item in value:
            _screen(item, path)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("RUNTIME-SEAL-PROTOCOL-UNSAFE-FIELD")
            # These protocol names intentionally use ``subject_token`` only in
            # generated evidence, never in the request or an identity field.
            if _DENIED_KEY.search(key) and key not in {
                "subject_token_policy",
                "subject_token_strategy",
                "vault_locator_fence",
                "vault_locator_fence_sha256",
            }:
                raise ValidationError("RUNTIME-SEAL-PROTOCOL-UNSAFE-FIELD")
            _screen(item, (*path, key))
        return
    raise ValidationError("RUNTIME-SEAL-PROTOCOL-INVALID-TYPE")


def _require_exact_ids(values: list[dict[str, Any]], field: str, expected: set[str], code: str) -> None:
    got = {item[field] for item in values}
    if got != expected or len(got) != len(values):
        raise ValidationError(code)


def _validate_capture_window(cutoff: dict[str, Any]) -> None:
    """Reject rolling, reversed, malformed, or overly broad capture windows."""

    try:
        start = datetime.strptime(cutoff["start_bucket"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        end = datetime.strptime(cutoff["end_bucket"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("RUNTIME-SEAL-REQUEST-CUTOFF-WINDOW-INVALID") from exc
    if end <= start or end - start > MAX_CUTOFF_WINDOW:
        raise ValidationError("RUNTIME-SEAL-REQUEST-CUTOFF-WINDOW-INVALID")


def _validate_record_range(record_range: dict[str, Any]) -> None:
    if record_range["end_sequence"] < record_range["start_sequence"]:
        raise ValidationError("RUNTIME-SEAL-REQUEST-RECORD-RANGE-INVALID")


def _object_bindings_hash(
    objects_by_id: dict[str, dict[str, Any]], object_ids: set[str]
) -> str:
    """Hash only the receipt-side audit/derivation handles for named objects."""

    return canonical_hash([
        {
            "object_id": object_id,
            "audit_object_sha256": objects_by_id[object_id]["audit_object_sha256"],
            "derived_object_sha256": objects_by_id[object_id]["derived_object_sha256"],
            "capture_status": objects_by_id[object_id]["capture_status"],
        }
        for object_id in sorted(object_ids)
    ])


def validate_runtime_evidence_seal_request(
    request: dict[str, Any], *, known_mechanism_ids: set[str]
) -> dict[str, Any]:
    """Validate a frozen, data-free request before any collector may read.

    The caller is responsible for writing the immutable request to a private
    audit vault.  This validator has no authority to initiate collection.
    """

    validate_payload(request, REQUEST_SCHEMA, "runtime evidence seal request")
    verify_self_hash(request, "request_sha256")
    _screen(request)
    if not set(request["mechanism_ids"]).issubset(known_mechanism_ids):
        raise ValidationError("RUNTIME-SEAL-REQUEST-UNKNOWN-MECHANISM")
    if request["collector_identity"] == request["independent_verifier_identity"]:
        raise ValidationError("RUNTIME-SEAL-REQUEST-NONINDEPENDENT-ROLES")
    if request["seal_id"] == request["trace_id"]:
        raise ValidationError("RUNTIME-SEAL-REQUEST-SEAL-TRACE-REUSE")
    _validate_capture_window(request["cutoff"])
    _validate_record_range(request["record_range"])
    objects = request["allowlisted_objects"]
    _require_exact_ids(objects, "object_class", set(REQUIRED_OBJECT_CLASSES), "RUNTIME-SEAL-REQUEST-OBJECT-COVERAGE")
    if len({item["object_id"] for item in objects}) != len(objects):
        raise ValidationError("RUNTIME-SEAL-REQUEST-DUPLICATE-OBJECT")
    for item in objects:
        if set(item["allowed_fields"]) != ALLOWED_FIELDS[item["object_class"]]:
            raise ValidationError("RUNTIME-SEAL-REQUEST-FIELD-ALLOWLIST-INVALID")
    bindings = request["mechanism_bindings"]
    _require_exact_ids(bindings, "mechanism_id", set(request["mechanism_ids"]), "RUNTIME-SEAL-REQUEST-BINDING-COVERAGE")
    for binding in bindings:
        if binding["chain_roles"] != list(CHAIN_ROLES):
            raise ValidationError("RUNTIME-SEAL-REQUEST-CHAIN-ORDER-INVALID")
    if set(request["exclusions"]) != REQUIRED_EXCLUSIONS:
        raise ValidationError("RUNTIME-SEAL-REQUEST-EXCLUSIONS-INVALID")
    _require_exact_ids(request["claim_mapping"], "mechanism_id", set(request["mechanism_ids"]), "RUNTIME-SEAL-REQUEST-CLAIM-MAPPING-INVALID")
    return {
        "request_id": request["request_id"],
        "seal_id": request["seal_id"],
        "trace_id": request["trace_id"],
        "request_sha256": request["request_sha256"],
        "mechanism_ids": request["mechanism_ids"],
        "collection_authority": "none_granted_by_protocol",
        "claim_effect": "runtime_evidence_group_only_registry_review_required",
    }


def validate_runtime_evidence_seal_collector_receipt(
    receipt: dict[str, Any], *, request: dict[str, Any], known_mechanism_ids: set[str]
) -> dict[str, Any]:
    """Validate a collector's metadata receipt without reading its source data."""

    request_summary = validate_runtime_evidence_seal_request(request, known_mechanism_ids=known_mechanism_ids)
    validate_payload(receipt, RECEIPT_SCHEMA, "runtime evidence seal collector receipt")
    verify_self_hash(receipt, "receipt_sha256")
    _screen(receipt)
    if receipt["request_id"] != request_summary["request_id"] or receipt["request_sha256"] != request_summary["request_sha256"]:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-REQUEST-MISMATCH")
    if receipt["seal_id"] != request_summary["seal_id"] or receipt["trace_id"] != request_summary["trace_id"]:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-SEAL-TRACE-MISMATCH")
    if receipt["collector_identity"] != request["collector_identity"]:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-COLLECTOR-MISMATCH")
    scope = receipt["scope_binding"]
    if (
        scope["vault_locator_fence_sha256"] != canonical_hash(request["vault_locator_fence"])
        or scope["record_range"] != request["record_range"]
        or scope["cutoff"] != request["cutoff"]
        or scope["subject_token_policy"] != request["subject_token_policy"]
    ):
        raise ValidationError("RUNTIME-SEAL-RECEIPT-SCOPE-BINDING-MISMATCH")
    _validate_capture_window(scope["cutoff"])
    _validate_record_range(scope["record_range"])
    request_objects = {item["object_id"]: item for item in request["allowlisted_objects"]}
    if set(item["object_id"] for item in receipt["objects"]) != set(request_objects):
        raise ValidationError("RUNTIME-SEAL-RECEIPT-OBJECT-MISMATCH")
    for item in receipt["objects"]:
        expected = request_objects[item["object_id"]]
        if item["object_class"] != expected["object_class"] or set(item["included_fields"]) != set(expected["allowed_fields"]):
            raise ValidationError("RUNTIME-SEAL-RECEIPT-ALLOWLIST-ESCAPE")
    receipt_objects = {item["object_id"]: item for item in receipt["objects"]}
    source_binding = receipt["source_identity_binding"]
    source_object = receipt_objects.get(source_binding["source_object_id"])
    if (
        source_object is None
        or source_object["object_class"] != "source_identity"
        or source_binding["source_identity_digest"] != request["source_identity_expectation"]["source_identity_digest"]
        or source_binding["audit_object_sha256"] != source_object["audit_object_sha256"]
    ):
        raise ValidationError("RUNTIME-SEAL-RECEIPT-SOURCE-BINDING-MISMATCH")
    observations = receipt["chain_observations"]
    _require_exact_ids(observations, "role", set(CHAIN_ROLES), "RUNTIME-SEAL-RECEIPT-CHAIN-COVERAGE")
    by_role = {item["role"]: item for item in observations}
    for role, observation in by_role.items():
        object_id = observation["object_id"]
        if request_objects[object_id]["object_class"] != OBJECT_CLASS_BY_ROLE[role]:
            raise ValidationError("RUNTIME-SEAL-RECEIPT-CHAIN-OBJECT-INVALID")
        captured_object = receipt_objects[object_id]
        if (
            observation["capture_status"] != captured_object["capture_status"]
            or observation["audit_object_sha256"] != captured_object["audit_object_sha256"]
            or observation["derived_object_sha256"] != captured_object["derived_object_sha256"]
        ):
            raise ValidationError("RUNTIME-SEAL-RECEIPT-CHAIN-CAPTURE-BINDING-MISMATCH")
    if len({item["subject_link"] for item in observations}) != 1:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-SUBJECT-LINK-INCOHERENT")
    if observations[0]["subject_link"] != scope["subject_link"]:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-SUBJECT-BINDING-MISMATCH")
    captured = receipt["collection_state"] == "captured_ready_for_independent_verification"
    statuses = {item["capture_status"] for item in receipt["objects"]}
    if captured and statuses != {"captured"}:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-CAPTURE-STATE-INCOHERENT")
    if captured and receipt["gaps"]:
        raise ValidationError("RUNTIME-SEAL-RECEIPT-CAPTURE-HAS-GAPS")
    return {
        "request_id": receipt["request_id"],
        "seal_id": receipt["seal_id"],
        "trace_id": receipt["trace_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "scope_binding_sha256": canonical_hash(scope),
        "receipt_object_set_sha256": _object_bindings_hash(receipt_objects, set(receipt_objects)),
        "collection_state": receipt["collection_state"],
        "claim_effect": "no_mechanism_status_change",
    }


def validate_runtime_evidence_seal_verifier_report(
    report: dict[str, Any], *, request: dict[str, Any], receipt: dict[str, Any], known_mechanism_ids: set[str]
) -> dict[str, Any]:
    """Verify independent-review metadata and retain the no-auto-promotion rule."""

    receipt_summary = validate_runtime_evidence_seal_collector_receipt(receipt, request=request, known_mechanism_ids=known_mechanism_ids)
    validate_payload(report, VERIFIER_SCHEMA, "runtime evidence seal verifier report")
    verify_self_hash(report, "report_sha256")
    _screen(report)
    if report["request_id"] != receipt_summary["request_id"] or report["request_sha256"] != request["request_sha256"] or report["receipt_sha256"] != receipt_summary["receipt_sha256"]:
        raise ValidationError("RUNTIME-SEAL-VERIFIER-BINDING-MISMATCH")
    if (
        report["seal_id"] != receipt_summary["seal_id"]
        or report["trace_id"] != receipt_summary["trace_id"]
        or report["scope_binding_sha256"] != receipt_summary["scope_binding_sha256"]
        or report["receipt_object_set_sha256"] != receipt_summary["receipt_object_set_sha256"]
    ):
        raise ValidationError("RUNTIME-SEAL-VERIFIER-SCOPE-BINDING-MISMATCH")
    if report["verifier_identity"] != request["independent_verifier_identity"] or report["verifier_identity"] == receipt["collector_identity"]:
        raise ValidationError("RUNTIME-SEAL-VERIFIER-NONINDEPENDENT")
    _require_exact_ids(report["checks"], "check_id", set(CHECK_IDS), "RUNTIME-SEAL-VERIFIER-CHECK-COVERAGE")
    objects_by_id = {item["object_id"]: item for item in receipt["objects"]}
    for check in report["checks"]:
        if check["review_basis"] != report["verification_basis"]:
            raise ValidationError("RUNTIME-SEAL-VERIFIER-CHECK-BASIS-MISMATCH")
        if check["review_basis"] == "contract_only":
            if check["result"] == "pass" or "derivation_audit_binding" in check:
                raise ValidationError("RUNTIME-SEAL-VERIFIER-CONTRACT-ONLY-PASS")
            continue
        binding = check.get("derivation_audit_binding")
        if binding is None:
            raise ValidationError("RUNTIME-SEAL-VERIFIER-DERIVATION-BINDING-MISSING")
        expected_ids = {
            object_id
            for object_id, item in objects_by_id.items()
            if item["object_class"] in CHECK_OBJECT_CLASSES[check["check_id"]]
        }
        actual_ids = set(binding["object_ids"])
        if actual_ids != expected_ids or binding["object_bindings_sha256"] != _object_bindings_hash(objects_by_id, actual_ids):
            raise ValidationError("RUNTIME-SEAL-VERIFIER-DERIVATION-BINDING-MISMATCH")
    passed = report["verification_state"] == "scope_limited_evidence_ready_for_registry_review"
    if passed:
        if report["verification_basis"] != "receipt_hash_bound_derivation":
            raise ValidationError("RUNTIME-SEAL-VERIFIER-CONTRACT-ONLY-UNREADY")
        if receipt_summary["collection_state"] != "captured_ready_for_independent_verification":
            raise ValidationError("RUNTIME-SEAL-VERIFIER-UNREADY-RECEIPT")
        if any(check["result"] != "pass" for check in report["checks"]) or report["gaps"]:
            raise ValidationError("RUNTIME-SEAL-VERIFIER-INVALID-PASS")
    return {
        "verification_state": report["verification_state"],
        "claim_effect": "scope_limited_runtime_evidence_group_only",
        "status_promotion": "forbidden_without_separate_registry_review",
        "affected_mechanism_ids": request["mechanism_ids"],
    }
