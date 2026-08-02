from __future__ import annotations

"""Executable, fail-closed OSS module readiness summaries.

This is intentionally a decision aid, not a release switch: a passing row only
means the supplied, explicit requirements are satisfied. Owner approval,
rights, public export and external state remain separate authorities.
"""

from typing import Any

from .registry import ValidationError


GAP_KINDS = {"product", "evidence", "expression"}
GAP_STATES = {"verified", "missing", "blocked", "unknown"}


def evaluate_module_readiness(record: dict[str, Any]) -> dict[str, Any]:
    required = {"module_id", "current_offer", "stages"}
    if not isinstance(record, dict) or set(record) != required:
        raise ValidationError("OSS-READINESS-RECORD-INVALID")
    if not isinstance(record["module_id"], str) or not record["module_id"]:
        raise ValidationError("OSS-READINESS-RECORD-INVALID")
    if not isinstance(record["current_offer"], str) or not isinstance(record["stages"], list):
        raise ValidationError("OSS-READINESS-RECORD-INVALID")
    results: list[dict[str, Any]] = []
    for stage in record["stages"]:
        if not isinstance(stage, dict) or set(stage) != {"stage", "requirements"}:
            raise ValidationError("OSS-READINESS-STAGE-INVALID")
        if not isinstance(stage["stage"], str) or not stage["stage"] or not isinstance(stage["requirements"], list):
            raise ValidationError("OSS-READINESS-STAGE-INVALID")
        gaps: dict[str, list[dict[str, str]]] = {kind: [] for kind in sorted(GAP_KINDS)}
        seen: set[str] = set()
        for item in stage["requirements"]:
            if not isinstance(item, dict) or set(item) != {"requirement_id", "kind", "state", "detail"}:
                raise ValidationError("OSS-READINESS-REQUIREMENT-INVALID")
            requirement_id = item["requirement_id"]
            if (
                not isinstance(requirement_id, str) or not requirement_id or requirement_id in seen
                or item["kind"] not in GAP_KINDS or item["state"] not in GAP_STATES
                or not isinstance(item["detail"], str) or not item["detail"]
            ):
                raise ValidationError("OSS-READINESS-REQUIREMENT-INVALID")
            seen.add(requirement_id)
            if item["state"] != "verified":
                gaps[item["kind"]].append({"requirement_id": requirement_id, "state": item["state"], "detail": item["detail"]})
        results.append({
            "stage": stage["stage"],
            "verdict": "ready_for_owner_gate" if not any(gaps.values()) else "not_ready",
            "gaps": gaps,
        })
    return {
        "schema_version": "oss-module-readiness-result/v1",
        "module_id": record["module_id"],
        "current_offer": record["current_offer"],
        "stages": results,
        "boundary": "A ready_for_owner_gate result is not a publication, export-rights, license, security, or owner-approval decision.",
    }
