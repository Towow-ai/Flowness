"""Evidence-bound OSS readiness roadmaps.

The roadmap is intentionally a *promotion candidate* evaluator, never a
release switch.  It resolves each gate from source-bound evidence records and
then carries every predecessor gate into later milestones.  In particular, a
piece of roadmap prose cannot turn ``required`` into ``verified``: verification
must arrive through a hash-bound record, and authority records can only make a
row eligible for a later owner gate -- they never approve publication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .readiness import GAP_KINDS, GAP_STATES, evaluate_module_readiness
from .registry import ValidationError


SCHEMA_VERSION = "oss-readiness-roadmap/v2"
RESULT_SCHEMA_VERSION = "oss-readiness-roadmap-result/v2"
EVIDENCE_LEDGER_SCHEMA_VERSION = "oss-readiness-condition-evidence-ledger/v1"
EVIDENCE_RECORD_SCHEMA_VERSION = "oss-readiness-evidence-record/v1"
AUTHORITY_RECORD_SCHEMA_VERSION = "oss-readiness-authority-record/v1"
CONDITION_KINDS = GAP_KINDS | {"authority"}
CONDITION_STATES = GAP_STATES | {"required"}
MILESTONE_IDS = ("now", "ledger_alpha", "trust_primitive_beta", "broad_channels")


def _safe_file(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ValidationError("OSS-ROADMAP-BOUND-INPUT-INVALID")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError("OSS-ROADMAP-BOUND-INPUT-ESCAPES-ROOT") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("OSS-ROADMAP-BOUND-INPUT-UNSAFE")
    return candidate


def _safe_evidence_file(root: Path, relative_path: str) -> Path:
    """Resolve a record source from the package or its checked-in workspace.

    The roadmap registry lives under ``oss/flowness-oss-harness`` while a
    candidate proof may deliberately live under the sibling ``public-core``.
    Both roots remain finite and checked; this is not a general parent-path
    escape hatch.
    """

    try:
        return _safe_file(root, relative_path)
    except ValidationError as first_error:
        workspace = root.parents[1] if root.name == "flowness-oss-harness" and len(root.parents) > 1 else None
        if workspace is None:
            raise first_error
        candidate = (workspace / relative_path).resolve()
        try:
            candidate.relative_to(workspace.resolve())
        except ValueError as exc:
            raise first_error from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise first_error
        return candidate


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(code) from exc
    if not isinstance(payload, dict):
        raise ValidationError(code)
    return payload


def _load_bound_inputs(
    roadmap: dict[str, Any], root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, str]]]:
    inputs = roadmap.get("bound_inputs")
    expected_ids = ("module_readiness", "module_route", "condition_evidence_ledger")
    if not isinstance(inputs, list) or [item.get("input_id") if isinstance(item, dict) else None for item in inputs] != list(expected_ids):
        raise ValidationError("OSS-ROADMAP-BOUND-INPUTS-INVALID")
    by_id: dict[str, dict[str, str]] = {}
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"input_id", "path", "sha256"}:
            raise ValidationError("OSS-ROADMAP-BOUND-INPUT-INVALID")
        input_id, path, expected_hash = item["input_id"], item["path"], item["sha256"]
        if input_id in by_id:
            raise ValidationError("OSS-ROADMAP-BOUND-INPUT-INVALID")
        source = _safe_file(root, path)
        if not isinstance(expected_hash, str) or expected_hash != _sha256(source):
            raise ValidationError(f"OSS-ROADMAP-BOUND-INPUT-HASH-MISMATCH:{input_id}")
        by_id[input_id] = item
    readiness = _load_json(
        _safe_file(root, by_id["module_readiness"]["path"]), "OSS-ROADMAP-READINESS-SOURCE-INVALID"
    )
    evaluate_module_readiness(readiness)
    ledger = _load_json(
        _safe_file(root, by_id["condition_evidence_ledger"]["path"]),
        "OSS-ROADMAP-EVIDENCE-LEDGER-INVALID",
    )
    return readiness, ledger, by_id


def _validate_condition(item: Any) -> dict[str, str]:
    if not isinstance(item, dict) or set(item) != {"condition_id", "kind", "state", "detail"}:
        raise ValidationError("OSS-ROADMAP-CONDITION-INVALID")
    if (
        not isinstance(item["condition_id"], str) or not item["condition_id"]
        or item["kind"] not in CONDITION_KINDS
        or item["state"] not in CONDITION_STATES
        or not isinstance(item["detail"], str) or not item["detail"]
    ):
        raise ValidationError("OSS-ROADMAP-CONDITION-INVALID")
    return item


def _stage_conditions(readiness: dict[str, Any], stage_id: str) -> list[dict[str, str]]:
    stage = next((item for item in readiness["stages"] if item["stage"] == stage_id), None)
    if stage is None:
        raise ValidationError(f"OSS-ROADMAP-STAGE-MISSING:{stage_id}")
    return [
        {"condition_id": item["requirement_id"], "kind": item["kind"], "state": item["state"], "detail": item["detail"]}
        for item in stage["requirements"]
    ]


def _reference(root: Path, value: Any, code: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "required_markers"}:
        raise ValidationError(code)
    path, expected_hash, markers = value["path"], value["sha256"], value["required_markers"]
    if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:"):
        raise ValidationError(code)
    if not isinstance(markers, list) or not markers or any(not isinstance(marker, str) or not marker for marker in markers):
        raise ValidationError(code)
    source = _safe_evidence_file(root, path)
    if _sha256(source) != expected_hash:
        raise ValidationError(f"{code}-HASH-MISMATCH")
    text = source.read_text(encoding="utf-8", errors="replace")
    missing = next((marker for marker in markers if marker not in text), None)
    if missing is not None:
        raise ValidationError(f"{code}-MARKER-MISSING:{missing}")
    return {"path": path, "sha256": expected_hash, "required_markers": markers}


def _binding(value: Any, code: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"candidate_id", "snapshot_id"}:
        raise ValidationError(code)
    if any(not isinstance(value[key], str) or not value[key] for key in value):
        raise ValidationError(code)
    return value


def _verify_evidence_record(
    root: Path, reference: Any, condition: dict[str, str], expected_binding: dict[str, str]
) -> None:
    ref = _reference(root, reference, "OSS-ROADMAP-EVIDENCE-RECORD")
    payload = _load_json(_safe_evidence_file(root, ref["path"]), "OSS-ROADMAP-EVIDENCE-RECORD-INVALID")
    required = {"schema_version", "record_id", "condition_id", "candidate_binding", "verification_state", "evidence_sources", "boundary"}
    if set(payload) != required or payload.get("schema_version") != EVIDENCE_RECORD_SCHEMA_VERSION:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-RECORD-INVALID")
    if payload.get("condition_id") != condition["condition_id"] or payload.get("verification_state") != "verified":
        raise ValidationError("OSS-ROADMAP-EVIDENCE-RECORD-CONDITION-MISMATCH")
    if _binding(payload.get("candidate_binding"), "OSS-ROADMAP-EVIDENCE-RECORD-BINDING-INVALID") != expected_binding:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-RECORD-BINDING-MISMATCH")
    if not isinstance(payload.get("record_id"), str) or not payload["record_id"] or not isinstance(payload.get("boundary"), str) or not payload["boundary"]:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-RECORD-INVALID")
    sources = payload.get("evidence_sources")
    if not isinstance(sources, list) or not sources:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-RECORD-SOURCES-INVALID")
    for source in sources:
        _reference(root, source, "OSS-ROADMAP-EVIDENCE-SOURCE")


def _verify_authority_record(
    root: Path, reference: Any, condition: dict[str, str], expected_binding: dict[str, str]
) -> None:
    ref = _reference(root, reference, "OSS-ROADMAP-AUTHORITY-RECORD")
    payload = _load_json(_safe_evidence_file(root, ref["path"]), "OSS-ROADMAP-AUTHORITY-RECORD-INVALID")
    required = {
        "schema_version", "record_id", "condition_id", "candidate_binding", "authority_kind",
        "review_state", "authorization", "evidence_sources", "boundary",
    }
    if set(payload) != required or payload.get("schema_version") != AUTHORITY_RECORD_SCHEMA_VERSION:
        raise ValidationError("OSS-ROADMAP-AUTHORITY-RECORD-INVALID")
    if payload.get("condition_id") != condition["condition_id"] or payload.get("review_state") != "verified_for_owner_gate":
        raise ValidationError("OSS-ROADMAP-AUTHORITY-RECORD-CONDITION-MISMATCH")
    if payload.get("authorization") != "not_authorized":
        raise ValidationError("OSS-ROADMAP-AUTHORITY-RECORD-MUST-NOT-AUTHORIZE")
    if _binding(payload.get("candidate_binding"), "OSS-ROADMAP-AUTHORITY-RECORD-BINDING-INVALID") != expected_binding:
        raise ValidationError("OSS-ROADMAP-AUTHORITY-RECORD-BINDING-MISMATCH")
    if (
        not isinstance(payload.get("record_id"), str) or not payload["record_id"]
        or not isinstance(payload.get("authority_kind"), str) or not payload["authority_kind"]
        or not isinstance(payload.get("boundary"), str) or not payload["boundary"]
        or not isinstance(payload.get("evidence_sources"), list) or not payload["evidence_sources"]
    ):
        raise ValidationError("OSS-ROADMAP-AUTHORITY-RECORD-INVALID")
    for source in payload["evidence_sources"]:
        _reference(root, source, "OSS-ROADMAP-AUTHORITY-SOURCE")


def _verify_condition_evidence(
    ledger: dict[str, Any], conditions: list[dict[str, str]], root: Path, module_id: str
) -> None:
    required = {"schema_version", "module_id", "candidate_binding", "conditions", "boundary"}
    if set(ledger) != required or ledger.get("schema_version") != EVIDENCE_LEDGER_SCHEMA_VERSION or ledger.get("module_id") != module_id:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-LEDGER-INVALID")
    binding = _binding(ledger.get("candidate_binding"), "OSS-ROADMAP-EVIDENCE-LEDGER-BINDING-INVALID")
    if not isinstance(ledger.get("boundary"), str) or not ledger["boundary"]:
        raise ValidationError("OSS-ROADMAP-EVIDENCE-LEDGER-INVALID")
    rows = ledger.get("conditions")
    if not isinstance(rows, list):
        raise ValidationError("OSS-ROADMAP-EVIDENCE-LEDGER-INVALID")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"condition_id", "kind", "state", "detail", "evidence_record", "authority_record"}:
            raise ValidationError("OSS-ROADMAP-EVIDENCE-LEDGER-CONDITION-INVALID")
        row_view = _validate_condition({key: row[key] for key in ("condition_id", "kind", "state", "detail")})
        if row_view["condition_id"] in by_id:
            raise ValidationError(f"OSS-ROADMAP-EVIDENCE-LEDGER-DUPLICATE:{row_view['condition_id']}")
        by_id[row_view["condition_id"]] = row
    expected = {condition["condition_id"]: condition for condition in conditions}
    if set(by_id) != set(expected):
        raise ValidationError("OSS-ROADMAP-EVIDENCE-LEDGER-CONDITIONS-STALE")
    for condition_id, condition in expected.items():
        row = by_id[condition_id]
        if any(row[key] != condition[key] for key in ("condition_id", "kind", "state", "detail")):
            raise ValidationError(f"OSS-ROADMAP-EVIDENCE-LEDGER-CONDITION-DRIFT:{condition_id}")
        evidence, authority = row["evidence_record"], row["authority_record"]
        if condition["state"] != "verified":
            if evidence is not None or authority is not None:
                raise ValidationError(f"OSS-ROADMAP-NONVERIFIED-CONDITION-HAS-EVIDENCE:{condition_id}")
            continue
        if condition["kind"] == "authority":
            if evidence is not None or authority is None:
                raise ValidationError(f"OSS-ROADMAP-AUTHORITY-RECORD-REQUIRED:{condition_id}")
            _verify_authority_record(root, authority, condition, binding)
        else:
            if authority is not None or evidence is None:
                raise ValidationError(f"OSS-ROADMAP-EVIDENCE-RECORD-REQUIRED:{condition_id}")
            _verify_evidence_record(root, evidence, condition, binding)


def _closure(
    milestone_id: str, direct: dict[str, list[dict[str, str]]], predecessors: dict[str, list[str]]
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(current: str) -> None:
        for predecessor in predecessors[current]:
            visit(predecessor)
        for condition in direct[current]:
            if condition["state"] != "verified" and condition["condition_id"] not in seen:
                seen.add(condition["condition_id"])
                result.append(condition)

    visit(milestone_id)
    return result


def evaluate_readiness_roadmap(roadmap: dict[str, Any], root: Path) -> dict[str, Any]:
    """Validate a roadmap and calculate only owner-gate *eligibility*."""

    required = {"schema_version", "module_id", "bound_inputs", "milestones"}
    if not isinstance(roadmap, dict) or set(roadmap) != required or roadmap.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("OSS-ROADMAP-INVALID")
    if not isinstance(roadmap["module_id"], str) or not roadmap["module_id"]:
        raise ValidationError("OSS-ROADMAP-INVALID")
    readiness, evidence_ledger, bound_inputs = _load_bound_inputs(roadmap, root)
    if roadmap["module_id"] != readiness["module_id"]:
        raise ValidationError("OSS-ROADMAP-MODULE-MISMATCH")
    milestones = roadmap["milestones"]
    if not isinstance(milestones, list) or [item.get("milestone_id") if isinstance(item, dict) else None for item in milestones] != list(MILESTONE_IDS):
        raise ValidationError("OSS-ROADMAP-MILESTONES-INVALID")

    expected_predecessors = {
        "now": [], "ledger_alpha": ["now"], "trust_primitive_beta": ["ledger_alpha"], "broad_channels": ["trust_primitive_beta"],
    }
    expected_stage = {"now": None, "ledger_alpha": "ledger_alpha", "trust_primitive_beta": "trust_primitive_beta", "broad_channels": None}
    direct: dict[str, list[dict[str, str]]] = {}
    normalised: dict[str, dict[str, Any]] = {}
    all_conditions: list[dict[str, str]] = []
    seen_conditions: set[str] = set()
    for item in milestones:
        expected = {"milestone_id", "depends_on", "readiness_stage", "offer_if_reached", "stage_gap_ids", "additional_conditions"}
        if not isinstance(item, dict) or set(item) != expected:
            raise ValidationError("OSS-ROADMAP-MILESTONE-INVALID")
        milestone_id = item["milestone_id"]
        if item["depends_on"] != expected_predecessors[milestone_id] or item["readiness_stage"] != expected_stage[milestone_id]:
            raise ValidationError(f"OSS-ROADMAP-DEPENDENCY-INVALID:{milestone_id}")
        if not isinstance(item["offer_if_reached"], str) or not item["offer_if_reached"]:
            raise ValidationError("OSS-ROADMAP-MILESTONE-INVALID")
        stage_conditions = [] if item["readiness_stage"] is None else _stage_conditions(readiness, item["readiness_stage"])
        expected_gaps = [condition["condition_id"] for condition in stage_conditions if condition["state"] != "verified"]
        if not isinstance(item["stage_gap_ids"], list) or item["stage_gap_ids"] != expected_gaps:
            raise ValidationError(f"OSS-ROADMAP-STAGE-GAPS-STALE:{milestone_id}")
        if not isinstance(item["additional_conditions"], list):
            raise ValidationError("OSS-ROADMAP-CONDITIONS-INVALID")
        additional = [_validate_condition(condition) for condition in item["additional_conditions"]]
        conditions = stage_conditions + additional
        for condition in conditions:
            condition_id = condition["condition_id"]
            if condition_id in seen_conditions:
                raise ValidationError(f"OSS-ROADMAP-DUPLICATE-CONDITION:{condition_id}")
            seen_conditions.add(condition_id)
            all_conditions.append(condition)
        direct[milestone_id] = conditions
        normalised[milestone_id] = item
    _verify_condition_evidence(evidence_ledger, all_conditions, root, roadmap["module_id"])

    statuses: dict[str, str] = {}
    result_milestones: list[dict[str, Any]] = []
    for milestone_id in MILESTONE_IDS:
        item = normalised[milestone_id]
        direct_outstanding = [condition for condition in direct[milestone_id] if condition["state"] != "verified"]
        inherited = _closure(milestone_id, direct, expected_predecessors)
        if milestone_id == "now":
            status = "candidate_only"
        else:
            dependencies_ready = all(statuses[predecessor] in {"candidate_only", "ready_for_owner_gate"} for predecessor in expected_predecessors[milestone_id])
            status = "ready_for_owner_gate" if not inherited and dependencies_ready else "not_ready"
        statuses[milestone_id] = status
        result_milestones.append({
            "milestone_id": milestone_id,
            "depends_on": item["depends_on"],
            "current_status": status,
            "offer_if_reached": item["offer_if_reached"],
            "direct_outstanding_conditions": direct_outstanding,
            "outstanding_conditions": inherited,
            "transition_rule": (
                "No publication is authorized. A ready_for_owner_gate result means every direct and inherited gate has a source-bound verified record; authority records remain not_authorized and a separate owner decision is still required."
            ),
        })
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "module_id": roadmap["module_id"],
        "bound_inputs": bound_inputs,
        "current_offer": readiness["current_offer"],
        "milestones": result_milestones,
        "boundary": "A roadmap is a decision aid. It does not grant export rights, publish a repository, approve a license, or replace owner authority.",
    }
