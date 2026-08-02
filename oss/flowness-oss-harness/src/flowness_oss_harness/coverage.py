from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .registry import STRONG_EVIDENCE, ValidationError, atomic_write_json

REQUIRED_STRATEGIES = {"required_mechanism", "required_registration"}
CRITICALITY_STRATEGY = {
    "critical": "required_mechanism",
    "registration_surface": "required_registration",
    "search_seed": "search_seed",
}


def validate_inventory_v2(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    if inventory.get("schema_version") != "repository-inventory/v2":
        raise ValidationError("repository-inventory/v2 is required")
    items = inventory.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValidationError("inventory v2 must contain an items array")
    seen: set[str] = set()
    for item in items:
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id or item_id in seen:
            raise ValidationError("inventory item_id values must be unique strings")
        seen.add(item_id)
        criticality = item.get("criticality")
        strategy = item.get("coverage_strategy")
        reason = item.get("blocking_reason")
        if criticality not in CRITICALITY_STRATEGY:
            raise ValidationError(
                f"unsupported inventory criticality for {item_id}: {criticality}"
            )
        if strategy != CRITICALITY_STRATEGY[criticality]:
            raise ValidationError(
                f"criticality/coverage_strategy mismatch for {item_id}"
            )
        if criticality == "search_seed":
            if reason is not None:
                raise ValidationError(
                    f"search_seed blocking_reason must be null for {item_id}"
                )
        elif not isinstance(reason, str) or not reason.strip():
            raise ValidationError(
                f"required inventory blocking_reason must be non-empty for {item_id}"
            )
    return items


def build_coverage(
    inventory_path: Path, mechanisms_path: Path
) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    mechanisms = json.loads(mechanisms_path.read_text(encoding="utf-8"))
    items = validate_inventory_v2(inventory)
    if not isinstance(mechanisms, list):
        raise ValidationError("mechanisms registry must be an array")
    item_ids = [item.get("item_id") for item in items]
    if any(not item_id for item_id in item_ids) or len(set(item_ids)) != len(item_ids):
        raise ValidationError("inventory item_id values must be present and unique")
    mechanism_ids = [card.get("mechanism_id") for card in mechanisms]
    if (
        any(not mechanism_id for mechanism_id in mechanism_ids)
        or len(set(mechanism_ids)) != len(mechanism_ids)
    ):
        raise ValidationError("mechanism_id values must be present and unique")

    declared_by: dict[str, list[str]] = {}
    covered_by: dict[str, list[str]] = {}
    coverage_states_by: dict[str, list[tuple[str, str]]] = {}
    invalid_verified: list[str] = []
    for card in mechanisms:
        mechanism_id = card["mechanism_id"]
        references = card.get("inventory_item_ids", [])
        if not isinstance(references, list):
            raise ValidationError(
                f"mechanism {mechanism_id} inventory_item_ids must be an array"
            )
        evidence = card.get("evidence", [])
        independent_groups = {
            item.get("independent_group")
            for item in evidence
            if isinstance(item, dict) and item.get("independent_group")
        }
        has_strong_evidence = any(
            isinstance(item, dict) and item.get("kind") in STRONG_EVIDENCE
            for item in evidence
        )
        verified = (
            card.get("status") == "current_verified"
            and len(independent_groups) >= 2
            and has_strong_evidence
            and bool(card.get("failure_modes"))
        )
        verification_trace = card.get("verification_trace", {})
        source_chain_complete = (
            isinstance(verification_trace, dict)
            and bool(verification_trace.get("function_definitions"))
            and bool(verification_trace.get("callers"))
            and bool(verification_trace.get("consumers"))
            and bool(card.get("failure_modes"))
            and bool(card.get("recovery_and_rollback"))
        )
        if not source_chain_complete:
            coverage_state = "declared_only"
        elif card.get("status") in {"experimental", "current_verified"}:
            coverage_state = "candidate_mapped"
        else:
            coverage_state = "declared_only"
        if card.get("status") == "current_verified" and not verified:
            invalid_verified.append(mechanism_id)
        for item_id in card.get("inventory_item_ids", []):
            declared_by.setdefault(item_id, []).append(mechanism_id)
            coverage_states_by.setdefault(item_id, []).append(
                (mechanism_id, coverage_state)
            )
            if verified:
                covered_by.setdefault(item_id, []).append(mechanism_id)

    rows: list[dict[str, Any]] = []
    blockers: list[str] = [
        "SEALED-RUNTIME-PROTOCOL-UNAVAILABLE",
        "SEALED-SOURCE-LINK-PROTOCOL-UNAVAILABLE",
    ]
    known_ids = set(item_ids)
    dangling = sorted(
        item_id for item_id in declared_by if item_id not in known_ids
    )
    blockers.extend(f"INVALID-MECHANISM-{item}" for item in invalid_verified)
    blockers.extend(f"DANGLING-COVERAGE-{item}" for item in dangling)
    for item in items:
        effective_mechanism_ids = sorted(set(covered_by.get(item["item_id"], [])))
        declared_mechanism_ids = sorted(set(declared_by.get(item["item_id"], [])))
        strategy = item["coverage_strategy"]
        state_rank = {
            "declared_only": 1,
            "candidate_mapped": 2,
            "source_chain_verified": 3,
            "runtime_verified": 4,
        }
        candidate_states = coverage_states_by.get(item["item_id"], [])
        coverage_state = (
            max(candidate_states, key=lambda pair: state_rank[pair[1]])[1]
            if candidate_states
            else "unmapped"
        )
        blocking = (
            strategy in REQUIRED_STRATEGIES
            and coverage_state != "source_chain_verified"
        )
        if blocking:
            blockers.append(f"COVERAGE-{item['item_id']}")
        rows.append(
            {
                "inventory_item_id": item["item_id"],
                "kind": item["kind"],
                "locator": item["locator"],
                "coverage_strategy": strategy,
                "blocking_reason": item.get("blocking_reason"),
                "mechanism_ids": effective_mechanism_ids,
                "declared_mechanism_ids": declared_mechanism_ids,
                "state": "covered" if effective_mechanism_ids else "unknown",
                "coverage_state": coverage_state,
                "blocking": blocking,
            }
        )
    return {
        "schema_version": "mechanism-coverage/v1",
        "inventory_hash": inventory["repository_content_hash"],
        "rows": rows,
        "dangling_inventory_references": dangling,
        "blockers": sorted(set(blockers)),
        "counts": {
            "inventory_items": len(rows),
            "covered": sum(row["state"] == "covered" for row in rows),
            "blocking_unknowns": sum(row["blocking"] for row in rows),
            "dangling_references": len(dangling),
            "invalid_verified_mechanisms": len(invalid_verified),
            "unmapped": sum(row["coverage_state"] == "unmapped" for row in rows),
            "declared_only": sum(
                row["coverage_state"] == "declared_only" for row in rows
            ),
            "candidate_mapped": sum(
                row["coverage_state"] == "candidate_mapped" for row in rows
            ),
            "source_chain_verified": sum(
                row["coverage_state"] == "source_chain_verified" for row in rows
            ),
            "runtime_verified": sum(
                row["coverage_state"] == "runtime_verified" for row in rows
            ),
        },
        "release_eligible": False,
    }


def write_coverage(
    inventory_path: Path, mechanisms_path: Path, output: Path
) -> None:
    atomic_write_json(output, build_coverage(inventory_path, mechanisms_path))
