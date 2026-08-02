from __future__ import annotations

"""Private-staging evidence contract for DRIFT-PUBLIC-EXPORT-004.

This module deliberately records an auditable *question set*, not a legal
conclusion.  A contract has no authorizing state and the evaluator always
returns a non-authorizing result.  Owner authorization must remain a separate,
authenticated release decision after an actual sealed export is available.
"""

from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "sealed-export-rights-contract.schema.json"
_ZERO_HASH = "sha256:" + "0" * 64
_PUBLIC_METADATA = ("notice", "spdx", "sbom")


def _append_once(blockers: list[str], value: str) -> None:
    if value not in blockers:
        blockers.append(value)


def evaluate_sealed_export_rights_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a private contract and enumerate why it cannot authorize export.

    The returned report is evidence for targeted rework only.  It intentionally
    cannot be used as ``sealed-export-rights-evidence`` by a release preflight.
    """

    validate_payload(payload, SCHEMA, "Sealed export/rights contract")
    verify_self_hash(payload, "contract_hash")
    blockers: list[str] = []
    binding = payload["candidate_binding"]
    if binding["sealed_export_manifest_sha256"] == _ZERO_HASH:
        _append_once(blockers, "DRIFT-PUBLIC-EXPORT-004:SEALED-EXPORT-UNBOUND")

    seen_ids: set[str] = set()
    for record in payload["records"]:
        record_id = record["record_id"]
        if record_id in seen_ids:
            _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:DUPLICATE-RECORD:{record_id}")
        seen_ids.add(record_id)

        if record["source"]["origin_type"] == "unknown":
            _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:SOURCE-UNKNOWN:{record_id}")
        if not record["evidence"]["source_evidence"]:
            _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:SOURCE-EVIDENCE-MISSING:{record_id}")
        if record["proposed_disposition"] == "include_in_candidate":
            if not record["evidence"]["license_evidence"]:
                _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:LICENSE-EVIDENCE-MISSING:{record_id}")
            if record["review"]["state"] != "recommended_include":
                _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:INCLUSION-REVIEW-UNRESOLVED:{record_id}")
            for metadata_name in _PUBLIC_METADATA:
                metadata = record["public_metadata"][metadata_name]
                if metadata["state"] in {"required", "unknown"} and not metadata["evidence_refs"]:
                    _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:{metadata_name.upper()}-MAPPING-MISSING:{record_id}")
        if record["review"]["state"] in {"unreviewed", "needs_research"}:
            _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:REVIEW-UNRESOLVED:{record_id}")
        if record["reject_reasons"]:
            _append_once(blockers, f"DRIFT-PUBLIC-EXPORT-004:DECLARED-REJECTION:{record_id}")

    _append_once(blockers, "DRIFT-PUBLIC-EXPORT-004:OWNER-AUTHORIZATION-ABSENT")
    report = {
        "schema_version": "sealed-export-rights-contract-report/v1",
        "scope": "private_staging_only",
        "state": "not_authorized",
        "eligible_for_public_export": False,
        "contract": {
            "contract_id": payload["contract_id"],
            "contract_hash": payload["contract_hash"],
            "candidate_id": binding["candidate_id"],
            "sealed_export_manifest_sha256": binding["sealed_export_manifest_sha256"],
        },
        "blockers": blockers,
        "boundary": (
            "This report only validates private-staging evidence coverage. It does not decide ownership or licensing, "
            "grant rights, create an SPDX/SBOM/NOTICE, authorize a sealed export, or authorize release/publishing."
        ),
    }
    return {**report, "report_hash": canonical_hash(report)}


def validate_sealed_export_rights_contract(payload: dict[str, Any]) -> None:
    """Validate structure and immutable identity without making any determination."""

    validate_payload(payload, SCHEMA, "Sealed export/rights contract")
    verify_self_hash(payload, "contract_hash")
