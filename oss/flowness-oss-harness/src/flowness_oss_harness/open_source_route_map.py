"""Fail-closed, evidence-bound answers to "when can this be open sourced?".

The readiness ledger is deliberately narrow: it evaluates one candidate module.
The owner dossier compares five release positions, but it does not provide a
machine-readable disposition for every mechanism family.  This module joins
those two views without promoting anything: every route names its exact option,
inherits that option's complete gap set, and binds every narrative input by
hash.  A missing or changed source therefore makes the route map unusable
rather than silently optimistic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .owner_decision_dossier import evaluate_owner_decision_dossier
from .registry import ValidationError


SCHEMA_VERSION = "flowness-open-source-route-map/v2"
RESULT_SCHEMA_VERSION = "flowness-open-source-route-map-result/v2"
SCOPE = "private_staging_only"
SOURCE_IDS = (
    "owner_dossier",
    "module_route",
    "runtime_discovery",
    "runtime_field_availability",
    "rights_contract",
    "wow_continuity_observation",
)
ROUTE_KINDS = {
    "private_research_only",
    "flowness_open_alpha",
    "flowness_verified_alpha",
    "flowness_beta",
    "broad_channel_launch",
    "hosted_or_enterprise_default",
    "exclude_until_rights",
}
OPTION_BY_ROUTE_KIND = {
    "private_research_only": "stop_now_private_offer",
    "flowness_open_alpha": "flowness_open_alpha",
    "flowness_verified_alpha": "flowness_verified_alpha",
    "flowness_beta": "flowness_beta",
    "broad_channel_launch": "broad_channel_launch",
}
NO_PUBLIC_ROUTE_KINDS = {"hosted_or_enterprise_default", "exclude_until_rights"}
GAP_GROUPS = ("product", "evidence", "expression", "authority")
MATURITY_LABELS = {
    "current_verified",
    "experimental",
    "designed_target",
    "written_only",
    "unknown",
}
OPEN_ALPHA_FORBIDDEN_GATE_FRAGMENTS = ("runtime-evidence", "clean-room", "external-use-case")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_file(root: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
        raise ValidationError("OSS-ROUTE-MAP-SOURCE-PATH-INVALID")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError("OSS-ROUTE-MAP-SOURCE-PATH-ESCAPES-ROOT") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError("OSS-ROUTE-MAP-SOURCE-UNSAFE")
    return candidate


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(code)
    return value


def _text_list(value: Any, code: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(code)
    if any(not isinstance(item, str) or not item.strip() for item in value) or len(value) != len(set(value)):
        raise ValidationError(code)
    return value


def _load_sources(payload: dict[str, Any], root: Path) -> dict[str, dict[str, str]]:
    raw = payload.get("bound_sources")
    if not isinstance(raw, list) or [item.get("source_id") if isinstance(item, dict) else None for item in raw] != list(SOURCE_IDS):
        raise ValidationError("OSS-ROUTE-MAP-SOURCES-INVALID")
    result: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"source_id", "path", "sha256", "required_markers"}:
            raise ValidationError("OSS-ROUTE-MAP-SOURCE-INVALID")
        source_id = item["source_id"]
        path = _safe_file(root, item["path"])
        expected_hash = item["sha256"]
        if not isinstance(expected_hash, str) or expected_hash != _sha256(path):
            raise ValidationError(f"OSS-ROUTE-MAP-SOURCE-HASH-MISMATCH:{source_id}")
        markers = _text_list(item["required_markers"], "OSS-ROUTE-MAP-SOURCE-MARKERS-INVALID")
        body = path.read_text(encoding="utf-8", errors="replace")
        missing = next((marker for marker in markers if marker not in body), None)
        if missing is not None:
            raise ValidationError(f"OSS-ROUTE-MAP-SOURCE-MARKER-MISSING:{source_id}:{missing}")
        result[source_id] = {"source_id": source_id, "path": item["path"], "sha256": expected_hash}
    return result


def _load_dossier(source: dict[str, str], root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_safe_file(root, source["path"]).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("OSS-ROUTE-MAP-DOSSIER-INVALID") from exc
    return evaluate_owner_decision_dossier(payload, root)


def _condition_ids(option: dict[str, Any]) -> list[str]:
    return [
        condition["condition_id"]
        for group in GAP_GROUPS
        for condition in option["delta_by_group"][group]
    ]


def _validate_route(route: Any, options: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = {
        "route_id", "mechanism_family", "current_disposition", "now_result",
        "next_route_kind", "incremental_value", "retained_boundary", "required_condition_ids",
        "maturity_label", "source_publication_policy", "claim_policy",
    }
    if not isinstance(route, dict) or set(route) != required:
        raise ValidationError("OSS-ROUTE-MAP-ROUTE-INVALID")
    for field in ("route_id", "mechanism_family", "now_result", "incremental_value", "retained_boundary"):
        _text(route[field], "OSS-ROUTE-MAP-ROUTE-INVALID")
    if route["current_disposition"] not in {"candidate_pending_minimum_gate", "excluded_from_open_alpha"}:
        raise ValidationError("OSS-ROUTE-MAP-DISPOSITION-INVALID")
    if route["maturity_label"] not in MATURITY_LABELS:
        raise ValidationError("OSS-ROUTE-MAP-MATURITY-LABEL-INVALID")
    _text(route["source_publication_policy"], "OSS-ROUTE-MAP-SOURCE-PUBLICATION-POLICY-INVALID")
    _text(route["claim_policy"], "OSS-ROUTE-MAP-CLAIM-POLICY-INVALID")
    route_kind = route["next_route_kind"]
    if route_kind not in ROUTE_KINDS:
        raise ValidationError("OSS-ROUTE-MAP-ROUTE-KIND-INVALID")
    supplied_conditions = _text_list(route["required_condition_ids"], "OSS-ROUTE-MAP-CONDITION-IDS-INVALID", allow_empty=True)
    if route_kind in NO_PUBLIC_ROUTE_KINDS:
        if route["current_disposition"] != "excluded_from_open_alpha":
            raise ValidationError("OSS-ROUTE-MAP-EXCLUDED-ROUTE-DISPOSITION-INVALID")
        if supplied_conditions:
            raise ValidationError("OSS-ROUTE-MAP-NO-PUBLIC-ROUTE-HAS-GATES")
        return {
            **route,
            "current_status": "excluded_from_open_alpha",
            "target_status": "no_public_route_declared",
            "outstanding_by_group": {group: [] for group in GAP_GROUPS},
        }
    option_id = OPTION_BY_ROUTE_KIND[route_kind]
    option = options[option_id]
    expected_conditions = _condition_ids(option)
    if supplied_conditions != expected_conditions:
        raise ValidationError(f"OSS-ROUTE-MAP-CONDITIONS-STALE:{route['route_id']}")
    if route_kind == "flowness_open_alpha":
        if route["source_publication_policy"] != "publish_source_after_minimum_gate":
            raise ValidationError("OSS-ROUTE-MAP-OPEN-ALPHA-SOURCE-POLICY-INVALID")
        if route["claim_policy"] != "publish_with_maturity_label_no_runtime_claim_without_evidence":
            raise ValidationError("OSS-ROUTE-MAP-OPEN-ALPHA-CLAIM-POLICY-INVALID")
        if any(fragment in condition_id for condition_id in supplied_conditions for fragment in OPEN_ALPHA_FORBIDDEN_GATE_FRAGMENTS):
            raise ValidationError("OSS-ROUTE-MAP-OPEN-ALPHA-RUNTIME-GATE-FORBIDDEN")
    return {
        **route,
        "current_status": option["current_state"],
        "target_status": "owner_gate_only_after_all_conditions_are_independently_evidenced",
        "outstanding_by_group": option["delta_by_group"],
    }


def evaluate_open_source_route_map(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    """Return a source-bound, non-authorizing route map for the owner."""

    required = {"schema_version", "map_id", "scope", "bound_sources", "routes", "boundary"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("OSS-ROUTE-MAP-INVALID")
    _text(payload.get("map_id"), "OSS-ROUTE-MAP-INVALID")
    if payload.get("scope") != SCOPE:
        raise ValidationError("OSS-ROUTE-MAP-SCOPE-INVALID")
    _text(payload.get("boundary"), "OSS-ROUTE-MAP-INVALID")
    sources = _load_sources(payload, root)
    dossier = _load_dossier(sources["owner_dossier"], root)
    options = {option["option_id"]: option for option in dossier["options"]}
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValidationError("OSS-ROUTE-MAP-ROUTES-INVALID")
    route_ids = [route.get("route_id") if isinstance(route, dict) else None for route in routes]
    if any(not route_id for route_id in route_ids) or len(route_ids) != len(set(route_ids)):
        raise ValidationError("OSS-ROUTE-MAP-ROUTES-INVALID")
    rendered_routes = [_validate_route(route, options) for route in routes]
    if not any(route["next_route_kind"] == "flowness_open_alpha" for route in rendered_routes):
        raise ValidationError("OSS-ROUTE-MAP-OPEN-ALPHA-MISSING")
    if not any(route["next_route_kind"] == "broad_channel_launch" for route in rendered_routes):
        raise ValidationError("OSS-ROUTE-MAP-BROAD-ROUTE-MISSING")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "map_id": payload["map_id"],
        "scope": SCOPE,
        "current_honest_offer": dossier["current_offer"],
        "bound_sources": list(sources.values()),
        "routes": rendered_routes,
        "release_positions": [
            {
                "option_id": option["option_id"],
                "title": option["title"],
                "current_state": option["current_state"],
                "offer_if_reached": option["offer_if_reached"] or " ".join(option["user_value"]),
                "delta_by_group": option["delta_by_group"],
                "predecessor_option_ids": option["predecessor_option_ids"],
                "carried_delta_by_group": option["carried_delta_by_group"],
                "incremental_delta_by_group": option["incremental_delta_by_group"],
                "excluded": option["excluded"],
            }
            for option in dossier["options"]
        ],
        "boundary": payload["boundary"],
    }


def render_open_source_route_map(result: dict[str, Any]) -> str:
    """Render the verified machine result as a stable owner-facing Markdown map."""

    if not isinstance(result, dict) or result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValidationError("OSS-ROUTE-MAP-RESULT-INVALID")
    lines = [
        "# Flowness Open-Source Route Map — v1",
        "",
        "Status: **private-staging decision aid. The next release target is a broad Flowness Open Alpha; this map does not itself authorize export, repository mutation, release or publication.**",
        "",
        "## What exists now",
        "",
        result["current_honest_offer"],
        "",
        "Experimental source is publishable after the minimum Open Alpha gate. Missing runtime evidence limits the maturity label and public claim; it does not by itself embargo source. Rights/secret/PII/IP hygiene, one runnable E2E, honest labels, license/community basics and the owner gate remain non-negotiable.",
        "",
        "## Module opening map",
        "",
        "| Mechanism family | Maturity | Source policy | Claim policy | Next honest route | Boundary retained |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for route in result["routes"]:
        lines.append(
            f"| {route['mechanism_family']} | `{route['maturity_label']}` | `{route['source_publication_policy']}` | `{route['claim_policy']}` | `{route['next_route_kind']}` | {route['retained_boundary']} |"
        )
    lines += ["", "## Exact gates by release position", ""]
    for position in result["release_positions"]:
        lines += [
            f"### {position['title']}",
            "",
            f"**Current state:** `{position['current_state']}`",
            "",
            "**What this adds if reached:** " + (position["offer_if_reached"] or "No release position: keep the private offer."),
            "",
            "**Still excluded:**",
            "",
        ]
        lines.extend(f"- {item}" for item in position["excluded"])
        lines += ["", "**Outstanding conditions (all required):**", ""]
        nonempty = False
        for group in GAP_GROUPS:
            conditions = position["delta_by_group"][group]
            if conditions:
                nonempty = True
                lines.append(f"- {group}:")
                lines.extend(f"  - `{item['condition_id']}` ({item['state']}): {item['detail']}" for item in conditions)
        if not nonempty:
            lines.append("- None: this is the private stop-now position, not an opening route.")
        lines.append("")
    lines += [
        "## What completing the next increment changes",
        "",
        "This is a planning comparison, not a partial-pass score. Each row needs all carried and new gates; completing one named condition merely removes one blocker.",
        "",
    ]
    for position in result["release_positions"]:
        if position["option_id"] == "stop_now_private_offer":
            continue
        carried = [
            condition
            for group in GAP_GROUPS
            for condition in position["carried_delta_by_group"][group]
        ]
        incremental = [
            condition
            for group in GAP_GROUPS
            for condition in position["incremental_delta_by_group"][group]
        ]
        lines += [
            f"### {position['title']}",
            "",
            "**Carries prior gates:** " + (", ".join(f"`{item['condition_id']}`" for item in carried) if carried else "None."),
            "",
            "**New gates for this step:** " + ", ".join(f"`{item['condition_id']}`" for item in incremental),
            "",
            "**If the complete route passes its separate owner gate, it adds:** " + position["offer_if_reached"],
            "",
        ]
    lines += [
        "## How to use this map",
        "",
        "1. The first target is `Flowness Open Alpha`: publish the useful Harness source surface together, including experimental modules, with per-module maturity labels.",
        "2. Close the exact conditions in all four categories with evidence tied to the candidate bytes. A planned condition, local demo or static chain does not count as a release pass.",
        "3. Re-evaluate this map after the evidence changes. Its hash bindings deliberately fail if a source moves without a new decision map.",
        "4. Only then does the named row become eligible for a separate owner gate; the map itself never performs that gate.",
        "",
        "## Bound evidence sources",
        "",
    ]
    lines.extend(f"- `{item['path']}` ({item['sha256']})" for item in result["bound_sources"])
    lines += ["", "## Boundary", "", result["boundary"], ""]
    return "\n".join(lines)
