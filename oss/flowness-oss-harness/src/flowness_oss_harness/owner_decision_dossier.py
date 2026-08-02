"""Hash-bound, owner-facing OSS decision dossiers.

This deliberately turns the existing readiness and review evidence into a
comparison aid.  It neither fills an evidence gap nor turns an owner action
into an authorization.  The input records the comparison wording, while the
evaluator verifies that every cited source is still the exact source bytes and
that the roadmap-derived gates have not been silently edited out.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .readiness_roadmap import evaluate_readiness_roadmap
from .registry import ValidationError, atomic_create_json


SCHEMA_VERSION = "owner-oss-decision-dossier-source/v2"
RESULT_SCHEMA_VERSION = "owner-oss-decision-dossier-result/v2"
OPTION_IDS = (
    "stop_now_private_offer",
    "flowness_open_alpha",
    "flowness_verified_alpha",
    "flowness_beta",
    "broad_channel_launch",
)
GROUPS = ("product", "evidence", "expression", "authority")

# This is a comparison topology, not a release topology.  It tells the owner
# which earlier route's gates are deliberately carried into a later *choice*,
# so the rendered dossier can distinguish a route's new work from the work it
# inherits.  A selected system-module Alpha is a separate branch from the
# Open Alpha is a source-distribution frontier.  Runtime evidence is a later
# claim/promotion frontier: experimental code may be public when it is labelled
# honestly, but it may not be described as runtime-verified.  This distinction
# prevents an evidence gap from silently becoming an embargo on inspectable
# source code.
OPTION_PREDECESSORS: dict[str, tuple[str, ...]] = {
    "stop_now_private_offer": (),
    "flowness_open_alpha": ("stop_now_private_offer",),
    "flowness_verified_alpha": ("flowness_open_alpha",),
    "flowness_beta": ("flowness_verified_alpha",),
    "broad_channel_launch": ("flowness_beta",),
}

OPEN_ALPHA_MINIMUM_CONDITION_IDS = {
    "open-alpha-complete-public-scope",
    "open-alpha-canonical-e2e",
    "open-alpha-rights-secret-pii-ip",
    "open-alpha-license-community-basics",
    "open-alpha-maturity-labels",
    "open-alpha-owner-gate",
}
OPEN_ALPHA_FORBIDDEN_CONDITION_IDS = {
    "system-module-runtime-evidence-seal",
    "system-module-independent-cleanroom",
    "independent-clean-room",
    "external-clean-rooms",
    "external-use-case",
}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_file(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ValidationError("OWNER-DOSSIER-SOURCE-PATH-INVALID")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError("OWNER-DOSSIER-SOURCE-PATH-ESCAPES-ROOT") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("OWNER-DOSSIER-SOURCE-UNSAFE")
    return candidate


def _text_list(value: Any, code: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(code)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError(code)
    if len(value) != len(set(value)):
        raise ValidationError(code)
    return value


def _load_sources(payload: dict[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValidationError("OWNER-DOSSIER-SOURCES-INVALID")
    result: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"source_id", "path", "sha256", "required_markers"}:
            raise ValidationError("OWNER-DOSSIER-SOURCE-INVALID")
        source_id, path_ref, expected_hash = source["source_id"], source["path"], source["sha256"]
        if not isinstance(source_id, str) or not source_id or source_id in result:
            raise ValidationError("OWNER-DOSSIER-SOURCE-INVALID")
        if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:") or len(expected_hash) != 71:
            raise ValidationError("OWNER-DOSSIER-SOURCE-INVALID")
        markers = _text_list(source["required_markers"], "OWNER-DOSSIER-SOURCE-MARKERS-INVALID")
        path = _safe_file(root, path_ref)
        if _sha256(path) != expected_hash:
            raise ValidationError(f"OWNER-DOSSIER-SOURCE-HASH-MISMATCH:{source_id}")
        text = path.read_text(encoding="utf-8", errors="replace")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise ValidationError(f"OWNER-DOSSIER-SOURCE-MARKER-MISSING:{source_id}:{missing[0]}")
        result[source_id] = {
            "source_id": source_id,
            "path": path_ref,
            "sha256": expected_hash,
        }
    return result


def _condition_groups(conditions: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {group: [] for group in GROUPS}
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {"condition_id", "kind", "state", "detail"}:
            raise ValidationError("OWNER-DOSSIER-CONDITION-INVALID")
        if condition["kind"] not in GROUPS or not isinstance(condition["condition_id"], str) or not condition["condition_id"]:
            raise ValidationError("OWNER-DOSSIER-CONDITION-INVALID")
        if not isinstance(condition["state"], str) or not condition["state"] or not isinstance(condition["detail"], str) or not condition["detail"]:
            raise ValidationError("OWNER-DOSSIER-CONDITION-INVALID")
        grouped[condition["kind"]].append({
            "condition_id": condition["condition_id"],
            "state": condition["state"],
            "detail": condition["detail"],
        })
    return grouped


def _condition_ids(grouped: dict[str, list[dict[str, str]]]) -> set[str]:
    return {
        condition["condition_id"]
        for group in GROUPS
        for condition in grouped[group]
    }


def _without_condition_ids(
    grouped: dict[str, list[dict[str, str]]], excluded_ids: set[str]
) -> dict[str, list[dict[str, str]]]:
    return {
        group: [
            condition for condition in grouped[group]
            if condition["condition_id"] not in excluded_ids
        ]
        for group in GROUPS
    }


def _validate_option(option: Any, source_ids: set[str]) -> None:
    required = {
        "option_id", "title", "roadmap_milestone", "user_value", "included", "excluded",
        "supporting_sources", "static_delta_conditions", "named_blockers", "irreversible_owner_actions", "no_go_conditions",
    }
    if not isinstance(option, dict) or set(option) != required:
        raise ValidationError("OWNER-DOSSIER-OPTION-INVALID")
    if not isinstance(option["option_id"], str) or not isinstance(option["title"], str) or not option["title"].strip():
        raise ValidationError("OWNER-DOSSIER-OPTION-INVALID")
    milestone = option["roadmap_milestone"]
    if milestone is not None and not isinstance(milestone, str):
        raise ValidationError("OWNER-DOSSIER-OPTION-INVALID")
    for key in ("user_value", "included", "excluded", "named_blockers", "irreversible_owner_actions", "no_go_conditions"):
        _text_list(option[key], f"OWNER-DOSSIER-{key.upper()}-INVALID")
    supporting = _text_list(option["supporting_sources"], "OWNER-DOSSIER-SUPPORTING-SOURCES-INVALID")
    if not set(supporting) <= source_ids:
        raise ValidationError("OWNER-DOSSIER-SUPPORTING-SOURCE-UNKNOWN")
    static = option["static_delta_conditions"]
    if not isinstance(static, list):
        raise ValidationError("OWNER-DOSSIER-STATIC-DELTA-INVALID")
    _condition_groups(static)


def evaluate_owner_decision_dossier(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    """Verify a dossier source and derive an exact, non-authorizing matrix."""

    required = {"schema_version", "dossier_id", "scope", "sources", "options", "boundary"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("OWNER-DOSSIER-INVALID")
    if not isinstance(payload["dossier_id"], str) or not payload["dossier_id"] or payload["scope"] != "private_staging_only":
        raise ValidationError("OWNER-DOSSIER-INVALID")
    if not isinstance(payload["boundary"], str) or not payload["boundary"].strip():
        raise ValidationError("OWNER-DOSSIER-INVALID")
    sources = _load_sources(payload, root)
    options = payload["options"]
    if not isinstance(options, list) or [item.get("option_id") if isinstance(item, dict) else None for item in options] != list(OPTION_IDS):
        raise ValidationError("OWNER-DOSSIER-OPTIONS-INVALID")
    roadmap_source = sources.get("readiness_roadmap")
    if roadmap_source is None:
        raise ValidationError("OWNER-DOSSIER-ROADMAP-SOURCE-MISSING")
    try:
        roadmap_payload = json.loads(_safe_file(root, roadmap_source["path"]).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("OWNER-DOSSIER-ROADMAP-INVALID") from exc
    roadmap = evaluate_readiness_roadmap(roadmap_payload, root)
    milestones = {item["milestone_id"]: item for item in roadmap["milestones"]}

    results: list[dict[str, Any]] = []
    for option in options:
        _validate_option(option, set(sources))
        milestone_id = option["roadmap_milestone"]
        if milestone_id is None:
            roadmap_delta: list[dict[str, Any]] = []
            current_state = "not_ready"
            offer = None
        else:
            if milestone_id not in milestones:
                raise ValidationError(f"OWNER-DOSSIER-ROADMAP-MILESTONE-UNKNOWN:{milestone_id}")
            milestone = milestones[milestone_id]
            roadmap_delta = milestone["outstanding_conditions"]
            current_state = milestone["current_status"]
            offer = milestone["offer_if_reached"]
        static_delta = option["static_delta_conditions"]
        if milestone_id == "now" and (roadmap_delta or static_delta):
            raise ValidationError("OWNER-DOSSIER-NOW-MUST-HAVE-NO-DELTA")
        conditions = roadmap_delta + static_delta
        if len({item["condition_id"] for item in conditions}) != len(conditions):
            raise ValidationError("OWNER-DOSSIER-DUPLICATE-CONDITION")
        results.append({
            "option_id": option["option_id"],
            "title": option["title"],
            "current_state": current_state,
            "offer_if_reached": offer,
            "user_value": option["user_value"],
            "included": option["included"],
            "excluded": option["excluded"],
            "direct_delta_by_group": _condition_groups(conditions),
            "named_blockers": option["named_blockers"],
            "supporting_sources": [sources[item] for item in option["supporting_sources"]],
            "irreversible_owner_actions": option["irreversible_owner_actions"],
            "no_go_conditions": option["no_go_conditions"],
        })

    open_alpha = next(item for item in results if item["option_id"] == "flowness_open_alpha")
    open_alpha_ids = _condition_ids(open_alpha["direct_delta_by_group"])
    if open_alpha_ids != OPEN_ALPHA_MINIMUM_CONDITION_IDS:
        raise ValidationError("OWNER-DOSSIER-OPEN-ALPHA-MINIMUM-GATES-INVALID")
    if open_alpha_ids & OPEN_ALPHA_FORBIDDEN_CONDITION_IDS:
        raise ValidationError("OWNER-DOSSIER-OPEN-ALPHA-RUNTIME-GATE-FORBIDDEN")

    # Do this from the verified results rather than from prose in the source.
    # That way an edited option cannot silently rewrite which gates it inherits
    # or what becomes newly necessary for the next useful opening position.
    by_option_id = {item["option_id"]: item for item in results}
    if tuple(by_option_id) != OPTION_IDS:
        raise ValidationError("OWNER-DOSSIER-OPTION-RESULTS-INVALID")
    for option_id in OPTION_IDS:
        option = by_option_id[option_id]
        predecessors = OPTION_PREDECESSORS[option_id]
        inherited: dict[str, list[dict[str, str]]] = {group: [] for group in GROUPS}
        inherited_ids: set[str] = set()
        for predecessor_id in predecessors:
            predecessor = by_option_id.get(predecessor_id)
            if predecessor is None:
                raise ValidationError(f"OWNER-DOSSIER-PREDECESSOR-UNKNOWN:{predecessor_id}")
            if "delta_by_group" not in predecessor:
                raise ValidationError(f"OWNER-DOSSIER-PREDECESSOR-ORDER-INVALID:{option_id}:{predecessor_id}")
            for group in GROUPS:
                for condition in predecessor["delta_by_group"][group]:
                    if condition["condition_id"] not in inherited_ids:
                        inherited[group].append(condition)
                        inherited_ids.add(condition["condition_id"])
        direct_ids = _condition_ids(option["direct_delta_by_group"])
        if direct_ids & inherited_ids:
            raise ValidationError(f"OWNER-DOSSIER-DUPLICATE-CARRIED-CONDITION:{option_id}")
        option["delta_by_group"] = {
            group: inherited[group] + option["direct_delta_by_group"][group]
            for group in GROUPS
        }
        option["predecessor_option_ids"] = list(predecessors)
        option["carried_delta_by_group"] = inherited
        option["incremental_delta_by_group"] = option.pop("direct_delta_by_group")

        # A private stop-now row is intentionally not a release frontier.  All
        # other rows must add at least one new, non-averagable gate; otherwise
        # the comparison wording would falsely imply a new capability without
        # any new proof or product work.
        if option_id != "stop_now_private_offer" and not any(option["incremental_delta_by_group"].values()):
            raise ValidationError(f"OWNER-DOSSIER-NO-INCREMENTAL-FRONTIER:{option_id}")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "dossier_id": payload["dossier_id"],
        "scope": payload["scope"],
        "current_offer": roadmap["current_offer"],
        "sources": list(sources.values()),
        "options": results,
        "boundary": payload["boundary"],
    }


def _lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _atomic_create_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValidationError(f"refusing to overwrite existing file: {path}") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_owner_decision_dossier(result: dict[str, Any]) -> str:
    """Render a stable Markdown view of an already verified dossier result."""

    if not isinstance(result, dict) or result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValidationError("OWNER-DOSSIER-RESULT-INVALID")
    lines = [
        "# Flowness OSS Owner Decision Dossier — v0",
        "",
        "Status: **private-staging decision aid; no option authorizes export, release, repository mutation or channel publication.**",
        "",
        "## Current honest offer",
        "",
        result["current_offer"],
        "",
        "## Options at this evidence snapshot",
        "",
    ]
    for option in result["options"]:
        lines += [
            f"### {option['title']}",
            "",
            f"**Current state:** `{option['current_state']}`",
            "",
            "**User value:** " + " ".join(option["user_value"]),
            "",
            "**Included**",
            _lines(option["included"]),
            "",
            "**Excluded**",
            _lines(option["excluded"]),
            "",
            "**Delta before this option can reach its owner gate**",
        ]
        nonempty = False
        for group in GROUPS:
            conditions = option["delta_by_group"][group]
            if conditions:
                nonempty = True
                lines.append(f"- {group}:")
                lines.extend(f"  - `{item['condition_id']}` ({item['state']}): {item['detail']}" for item in conditions)
        if not nonempty:
            lines.append("- None. This is the stop-now private offer, not a release route.")
        lines += [
            "",
            "**Named blockers / evidence artifacts**",
            _lines(option["named_blockers"] + [f"{item['path']} ({item['sha256']})" for item in option["supporting_sources"]]),
            "",
            "**Irreversible owner actions (only after every applicable gate passes)**",
            _lines(option["irreversible_owner_actions"]),
            "",
            "**No-go conditions**",
            _lines(option["no_go_conditions"]),
            "",
        ]
    lines += [
        "## Incremental decision frontier",
        "",
        "Each row below separates gates inherited from an earlier route from the new gates that make this route materially different. **Closing one condition alone does not unlock a route**: every carried and new condition must be independently evidenced against the same candidate bytes, then pass its separate owner gate.",
        "",
    ]
    for option in result["options"]:
        if option["option_id"] == "stop_now_private_offer":
            continue
        predecessor_titles = [
            next(item["title"] for item in result["options"] if item["option_id"] == predecessor_id)
            for predecessor_id in option["predecessor_option_ids"]
        ]
        lines += [
            f"### {option['title']}",
            "",
            "**Carries forward:** " + ("; ".join(predecessor_titles) if predecessor_titles else "No earlier public route."),
            "",
            "**New gates for this step (all required together):**",
            "",
        ]
        for group in GROUPS:
            conditions = option["incremental_delta_by_group"][group]
            if conditions:
                lines.append(f"- {group}:")
                lines.extend(f"  - `{item['condition_id']}` ({item['state']}): {item['detail']}" for item in conditions)
        lines += [
            "",
            "**What the whole step adds if it reaches its owner gate:** " + (option["offer_if_reached"] or " ".join(option["user_value"])),
            "",
            "**It still does not add:** " + " ".join(option["excluded"]),
            "",
        ]
    lines += ["## Boundary", "", result["boundary"], ""]
    return "\n".join(lines)


def write_owner_decision_dossier(payload: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    """Write only a newly derived private Markdown dossier and adjacent JSON."""

    result = evaluate_owner_decision_dossier(payload, root)
    if output.suffix != ".md":
        raise ValidationError("OWNER-DOSSIER-OUTPUT-MUST-BE-MARKDOWN")
    if output.exists() or output.with_suffix(".json").exists():
        raise ValidationError("OWNER-DOSSIER-OUTPUT-EXISTS")
    atomic_create_json(output.with_suffix(".json"), result)
    _atomic_create_text(output, render_owner_decision_dossier(result))
    return result
