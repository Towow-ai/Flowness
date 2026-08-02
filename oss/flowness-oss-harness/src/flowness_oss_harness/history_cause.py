"""Fail-closed causal-history dispositions for mechanism narration.

An immutable diff can prove that a named source change occurred.  It cannot,
by itself, prove that an incident happened, that a particular alternative was
rejected, or that the present behavior was observed in production.  This
module makes that distinction explicit per mechanism so writers cannot turn a
patch label into an invented origin story.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .history_anchor import BOUNDARY as ANCHOR_BOUNDARY
from .history_anchor import verify_local_history_anchors
from .registry import ValidationError


BOUNDARY = "LOCAL-GIT-HISTORY-CAUSAL-RECORDS;DOES-NOT-PROVE-INCIDENT-OR-RUNTIME"
_CAUSE_UNKNOWN = "historical_cause_unknown"
_PRECONDITION_UNKNOWN = "historical_precondition_unknown"
_POSTCONDITION_UNKNOWN = "historical_postcondition_unknown"
_PATCH_OBSERVED = "patch_observed"


def _read(root: Path, relative: str, error: str) -> dict[str, Any]:
    if not isinstance(relative, str) or not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise ValidationError(error)
    path = (root / relative).resolve()
    if path.is_symlink() or not path.is_file() or root not in path.parents:
        raise ValidationError(error)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(error) from exc
    if not isinstance(value, dict):
        raise ValidationError(error)
    return value


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unknown_field(value: Any, state: str, error: str) -> None:
    if not isinstance(value, dict) or set(value) != {"state", "statement"} or value.get("state") != state or not _nonempty_text(value.get("statement")):
        raise ValidationError(error)


def verify_mechanism_history_causes(registry: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Verify every mechanism has an audit-friendly causal disposition.

    The current evidence set deliberately declares causes Unknown.  Adding a
    positive historical failure claim is rejected until its own evidence type
    and immutable coordinate are added in a later schema, rather than silently
    converting a hunk into an incident claim.
    """

    base = Path(root).resolve(strict=True)
    if set(registry) != {"schema_version", "boundary", "history_anchor_registry", "records"} or registry.get("schema_version") != "mechanism-history-cause-registry/v1" or registry.get("boundary") != BOUNDARY:
        raise ValidationError("HISTORY-CAUSE-REGISTRY-INVALID")
    anchor_relative = registry.get("history_anchor_registry")
    anchors = _read(base, anchor_relative, "HISTORY-CAUSE-ANCHOR-REGISTRY-INVALID")
    anchor_result = verify_local_history_anchors(anchors, base)
    if anchor_result["boundary"] != ANCHOR_BOUNDARY:
        raise ValidationError("HISTORY-CAUSE-ANCHOR-BOUNDARY-INVALID")
    by_mechanism = {item["mechanism_id"]: item for item in anchors["mechanisms"]}
    records = registry.get("records")
    if not isinstance(records, list) or not records:
        raise ValidationError("HISTORY-CAUSE-REGISTRY-INVALID")
    seen: set[str] = set()
    coordinates: list[dict[str, Any]] = []
    for record in records:
        required = {
            "mechanism_id", "anchor_ordinal", "precondition", "failure_or_constraint",
            "evolution_action", "behavioral_postcondition", "what_remains_unknown",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise ValidationError("HISTORY-CAUSE-RECORD-INVALID")
        mechanism_id = record.get("mechanism_id")
        ordinal = record.get("anchor_ordinal")
        if not _nonempty_text(mechanism_id) or mechanism_id in seen or mechanism_id not in by_mechanism or not isinstance(ordinal, int) or ordinal < 0:
            raise ValidationError("HISTORY-CAUSE-RECORD-INVALID")
        seen.add(mechanism_id)
        mechanism = by_mechanism[mechanism_id]
        if mechanism.get("status") != "anchored_local_only" or ordinal >= len(mechanism["anchors"]):
            raise ValidationError("HISTORY-CAUSE-ANCHOR-MISSING")
        _unknown_field(record.get("precondition"), _PRECONDITION_UNKNOWN, "HISTORY-CAUSE-PRECONDITION-INVALID")
        _unknown_field(record.get("failure_or_constraint"), _CAUSE_UNKNOWN, "HISTORY-CAUSE-UNKNOWN-REQUIRED")
        evolution = record.get("evolution_action")
        if not isinstance(evolution, dict) or set(evolution) != {"state", "statement"} or evolution.get("state") != _PATCH_OBSERVED or not _nonempty_text(evolution.get("statement")):
            raise ValidationError("HISTORY-CAUSE-EVOLUTION-INVALID")
        _unknown_field(record.get("behavioral_postcondition"), _POSTCONDITION_UNKNOWN, "HISTORY-CAUSE-POSTCONDITION-INVALID")
        unresolved = record.get("what_remains_unknown")
        if not isinstance(unresolved, list) or not unresolved or any(not _nonempty_text(item) for item in unresolved):
            raise ValidationError("HISTORY-CAUSE-UNKNOWN-REQUIRED")
        anchor = mechanism["anchors"][ordinal]
        coordinates.append({
            "mechanism_id": mechanism_id,
            "anchor_ordinal": ordinal,
            "commit": anchor["commit"],
            "relation_kind": anchor["relation_kind"],
            "evolution": anchor["evolution"],
            "static_node_ref": anchor["static_node_ref"],
        })
    expected_ids = set(by_mechanism)
    if seen != expected_ids:
        raise ValidationError("HISTORY-CAUSE-MECHANISM-COVERAGE-INVALID")
    return {
        "schema_version": "mechanism-history-cause-verification/v1",
        "boundary": BOUNDARY,
        "ceiling": "patch_observed_cause_unknown",
        "mechanism_count": len(seen),
        "historical_cause_unknown_count": len(seen),
        "positive_incident_claim_count": 0,
        "coordinates": sorted(coordinates, key=lambda item: item["mechanism_id"]),
    }
