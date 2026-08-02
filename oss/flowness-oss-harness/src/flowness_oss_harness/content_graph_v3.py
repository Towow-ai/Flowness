"""Fail-closed private-staging contract for Content Graph v3.

V2 remains the byte-bound source graph for the existing Ledger candidate.  V3
is deliberately a migration overlay rather than a silent re-description: it
binds that exact V2 graph, adds the missing limitation/audience/channel/version
relationships, and never grants publication capability.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any

from .content_graph import affected_artifacts, validate_graph
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "content-graph-v3.schema.json"
NOT_APPLICABLE = "NotApplicable"
V3_VERSION = "content-graph/v3"
_DERIVATION_EDGE_TYPES = {"derived_from", "explains", "implements"}
_ALLOWED_CAPABILITIES = {"draft", "manual_publish_pack", "analytics_only", NOT_APPLICABLE}
_ALLOWED_LOCALES = {"en", "zh-CN", NOT_APPLICABLE}


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["node_id"]: node for node in graph["nodes"]}


def _as_nonempty_unique_strings(value: Any, error: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(error)
    if len(set(value)) != len(value):
        raise ValidationError(error)
    return value


def _assert_not_applicable_or_known(
    values: Any, known: set[str], error: str
) -> list[str]:
    result = _as_nonempty_unique_strings(values, error)
    if result == [NOT_APPLICABLE]:
        return result
    if NOT_APPLICABLE in result or not set(result).issubset(known):
        raise ValidationError(error)
    return result


def _v2_asset_sources(v2: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for edge in v2["edges"]:
        if edge["type"] not in _DERIVATION_EDGE_TYPES:
            continue
        result.setdefault(edge["from_id"], []).append(edge["to_id"])
    return {node_id: sorted(values) for node_id, values in result.items()}


def _reachable_claims(v2: dict[str, Any], source_ids: list[str]) -> set[str]:
    """Trace source nodes to claims without treating the v2 edge direction as prose.

    The V2 graph stores both authoring edges (asset -> section) and evidence
    edges (evidence -> claim).  This function follows the semantic source
    direction only and is intentionally limited to the edges which can make a
    claim part of an asset's scope.
    """

    next_nodes: dict[str, set[str]] = {}
    for edge in v2["edges"]:
        if edge["type"] in {
            "derived_from", "explains", "implements", "supports", "disproves",
            "limits", "tests", "replays", "exercises",
        }:
            next_nodes.setdefault(edge["from_id"], set()).add(edge["to_id"])
    nodes = _node_map(v2)
    queue = deque(source_ids)
    seen = set(source_ids)
    claims: set[str] = set()
    while queue:
        current = queue.popleft()
        if nodes[current]["type"] == "claim":
            claims.add(current)
        for target in sorted(next_nodes.get(current, ())):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return claims


def _check_source_graph_binding(v3: dict[str, Any], v2: dict[str, Any], *, source_hash: str | None) -> None:
    source = v3["source_graph"]
    if source["schema_version"] != v2["schema_version"]:
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-VERSION-MISMATCH")
    if source_hash is not None and source["content_hash"] != source_hash:
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-HASH-MISMATCH")
    if v2["schema_version"] != "content-graph/v2":
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-REQUIRES-V2")


def validate_content_graph_v3(
    graph: dict[str, Any], *, source_graph: dict[str, Any], source_hash: str | None = None
) -> dict[str, Any]:
    """Validate V3 relations against one exact V2 candidate graph.

    The returned summary is local review evidence only.  Validation has no
    state-transition, send, scheduling, or publication effect.
    """

    validate_payload(graph, SCHEMA, "content graph v3")
    validate_graph(source_graph)
    _check_source_graph_binding(graph, source_graph, source_hash=source_hash)

    candidate = graph["candidate"]
    if candidate["capability"] != "draft":
        raise ValidationError("CONTENT-GRAPH-V3-CANDIDATE-CAPABILITY-INVALID")

    v2_nodes = _node_map(source_graph)
    v2_assets = {node_id for node_id, node in v2_nodes.items() if node["type"] == "asset"}
    v2_claims = {node_id for node_id, node in v2_nodes.items() if node["type"] == "claim"}
    source_snapshot = candidate["snapshot_id"]
    if any(node["snapshot_id"] != source_snapshot for node in v2_nodes.values()):
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-SNAPSHOT-MISMATCH")

    v3_nodes = {node["node_id"]: node for node in graph["nodes"]}
    if len(v3_nodes) != len(graph["nodes"]):
        raise ValidationError("CONTENT-GRAPH-V3-DUPLICATE-NODE")
    if any(
        node["candidate_id"] != candidate["candidate_id"]
        or node["snapshot_id"] != source_snapshot
        or node["version_id"] != candidate["version_id"]
        for node in v3_nodes.values()
    ):
        raise ValidationError("CONTENT-GRAPH-V3-NODE-CANDIDATE-MISMATCH")

    limitations = {node_id for node_id, node in v3_nodes.items() if node["type"] == "limitation"}
    audiences = {node_id for node_id, node in v3_nodes.items() if node["type"] == "audience_profile"}
    channels = {node_id for node_id, node in v3_nodes.items() if node["type"] == "channel"}
    versions = {node_id for node_id, node in v3_nodes.items() if node["type"] == "version"}
    if versions != {candidate["version_id"]} or not limitations or not audiences or not channels:
        raise ValidationError("CONTENT-GRAPH-V3-RELATION-NODES-INCOMPLETE")
    for node in v3_nodes.values():
        if node["type"] == "channel" and node["capability"] != "draft":
            raise ValidationError("CONTENT-GRAPH-V3-CHANNEL-CAPABILITY-INVALID")

    claim_limitations: dict[str, list[str]] = {}
    for item in graph["claim_limitations"]:
        claim_id = item["claim_id"]
        if claim_id in claim_limitations or claim_id not in v2_claims:
            raise ValidationError("CONTENT-GRAPH-V3-CLAIM-LIMITATION-INVALID")
        claim_limitations[claim_id] = _assert_not_applicable_or_known(
            item["limitation_ids"], limitations, "CONTENT-GRAPH-V3-CLAIM-LIMITATION-INVALID"
        )
        if claim_limitations[claim_id] == [NOT_APPLICABLE]:
            raise ValidationError("CONTENT-GRAPH-V3-CLAIM-LIMITATION-INVALID")
    if set(claim_limitations) != v2_claims:
        raise ValidationError("CONTENT-GRAPH-V3-CLAIM-LIMITATION-INVALID")

    expected_sources = _v2_asset_sources(source_graph)
    asset_relations: dict[str, dict[str, Any]] = {}
    for relation in graph["asset_relations"]:
        asset_id = relation["asset_id"]
        if asset_id in asset_relations or asset_id not in v2_assets:
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-RELATION-INVALID")
        if relation["version_id"] != candidate["version_id"] or relation["capability"] not in _ALLOWED_CAPABILITIES:
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-RELATION-INVALID")
        if relation["locale"] not in _ALLOWED_LOCALES:
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-RELATION-INVALID")
        _assert_not_applicable_or_known(relation["audience_ids"], audiences, "CONTENT-GRAPH-V3-ASSET-AUDIENCE-INVALID")
        _assert_not_applicable_or_known(relation["channel_ids"], channels, "CONTENT-GRAPH-V3-ASSET-CHANNEL-INVALID")
        boundaries = _assert_not_applicable_or_known(relation["boundary_ids"], limitations, "CONTENT-GRAPH-V3-ASSET-BOUNDARY-INVALID")
        sources = _as_nonempty_unique_strings(relation["source_node_ids"], "CONTENT-GRAPH-V3-ASSET-SOURCE-INVALID")
        if sources != expected_sources.get(asset_id, []):
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-SOURCE-INVALID")
        source_hashes = relation["source_node_hashes"]
        if not isinstance(source_hashes, dict) or set(source_hashes) != set(sources):
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-SOURCE-HASH-INVALID")
        if any(source_hashes[node_id] != v2_nodes[node_id]["content_hash"] for node_id in sources):
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-SOURCE-HASH-INVALID")
        transform = relation["transform"]
        if transform["producer_id"] not in graph["policy"]["allowed_transform_producers"]:
            raise ValidationError("CONTENT-GRAPH-V3-TRANSFORM-PRODUCER-INVALID")
        if not transform["template_version"] or not transform["model_id"]:
            raise ValidationError("CONTENT-GRAPH-V3-TRANSFORM-INVALID")
        review_ids = _as_nonempty_unique_strings(relation["review_ids"], "CONTENT-GRAPH-V3-REVIEW-INVALID")
        if "review.local-content-boundary-v1" not in review_ids:
            raise ValidationError("CONTENT-GRAPH-V3-REVIEW-INVALID")

        reachable = _reachable_claims(source_graph, sources)
        declared_claims = _assert_not_applicable_or_known(
            relation["scope"]["claim_ids"], v2_claims, "CONTENT-GRAPH-V3-SCOPE-INVALID"
        )
        if declared_claims == [NOT_APPLICABLE]:
            if reachable:
                raise ValidationError("CONTENT-GRAPH-V3-SCOPE-INVALID")
        elif set(declared_claims) != reachable:
            raise ValidationError("CONTENT-GRAPH-V3-SCOPE-INVALID")
        inherited = set().union(*(set(claim_limitations[claim]) for claim in reachable)) if reachable else set()
        if inherited and set(boundaries) != inherited:
            raise ValidationError("CONTENT-GRAPH-V3-LIMITATION-OMITTED")
        if not inherited and boundaries == [NOT_APPLICABLE]:
            raise ValidationError("CONTENT-GRAPH-V3-ASSET-BOUNDARY-INVALID")
        asset_relations[asset_id] = relation
    if set(asset_relations) != v2_assets:
        raise ValidationError("CONTENT-GRAPH-V3-ASSET-RELATION-INCOMPLETE")

    pairs = {pair["pair_id"]: pair for pair in graph["localization_pairs"]}
    if len(pairs) != len(graph["localization_pairs"]):
        raise ValidationError("CONTENT-GRAPH-V3-LOCALIZATION-DUPLICATE")
    seen_pair_assets: set[str] = set()
    for pair in pairs.values():
        assets = pair["asset_ids"]
        if any(asset not in v2_assets for asset in assets) or len(set(assets)) != 2:
            raise ValidationError("CONTENT-GRAPH-V3-LOCALIZATION-INVALID")
        if set(assets) & seen_pair_assets:
            raise ValidationError("CONTENT-GRAPH-V3-LOCALIZATION-INVALID")
        seen_pair_assets.update(assets)
        if pair["status"] == "reviewed" and not pair["review_ids"]:
            raise ValidationError("CONTENT-GRAPH-V3-PARITY-REVIEW-INVALID")
        if pair["status"] == "unreviewed" and pair["review_ids"]:
            raise ValidationError("CONTENT-GRAPH-V3-PARITY-REVIEW-INVALID")

    return {
        "schema_version": "content-graph-v3-verification/v1",
        "candidate_id": candidate["candidate_id"],
        "version_id": candidate["version_id"],
        "asset_relation_count": len(asset_relations),
        "claim_limitation_count": len(claim_limitations),
        "localization_pair_count": len(pairs),
        "boundary": "private staging verification only; this result cannot publish, approve, or authorize a channel package.",
    }


def verify_content_graph_v3_files(
    graph_path: Path | str, *, root: Path | str
) -> dict[str, Any]:
    """Load a V3 migration graph and the exact V2 source bytes it names."""

    base = Path(root).resolve(strict=True)
    path = Path(graph_path).resolve(strict=True)
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValidationError("CONTENT-GRAPH-V3-PATH-ESCAPES-ROOT") from exc
    if path.is_symlink() or not path.is_file():
        raise ValidationError("CONTENT-GRAPH-V3-PATH-UNSAFE")
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("CONTENT-GRAPH-V3-INVALID") from exc
    source_value = graph.get("source_graph", {}).get("source_path") if isinstance(graph, dict) else None
    if not isinstance(source_value, str) or not source_value:
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-PATH-INVALID")
    source = (base / source_value).resolve(strict=True)
    try:
        source.relative_to(base)
    except ValueError as exc:
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-PATH-INVALID") from exc
    if source.is_symlink() or not source.is_file():
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-PATH-INVALID")
    raw_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        v2 = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-INVALID") from exc
    if not isinstance(v2, dict):
        raise ValidationError("CONTENT-GRAPH-V3-SOURCE-INVALID")
    return validate_content_graph_v3(graph, source_graph=v2, source_hash=raw_hash)


def affected_artifacts_v3(
    graph: dict[str, Any], *, source_graph: dict[str, Any], changed_node_ids: list[str]
) -> dict[str, Any]:
    """Extend V2 ripple analysis with limitation-to-asset invalidation.

    A limitation is a first-class V3 source.  Changing it invalidates every
    asset that retained it, plus any asset which is reached through the V2
    evidence/claim graph.  The result is review work only.
    """

    validate_content_graph_v3(graph, source_graph=source_graph)
    v2_nodes = _node_map(source_graph)
    v3_ids = {node["node_id"] for node in graph["nodes"]}
    if not changed_node_ids or any(node_id not in v2_nodes and node_id not in v3_ids for node_id in changed_node_ids):
        raise ValidationError("CONTENT-GRAPH-V3-CHANGE-SET-INVALID")
    changed_v2 = sorted(node_id for node_id in changed_node_ids if node_id in v2_nodes)
    affected: set[str] = set()
    if changed_v2:
        affected.update(affected_artifacts(source_graph, changed_v2)["affected_node_ids"])
    limitation_to_claims = {
        limitation: {
            item["claim_id"] for item in graph["claim_limitations"] if limitation in item["limitation_ids"]
        }
        for limitation in {node["node_id"] for node in graph["nodes"] if node["type"] == "limitation"}
    }
    for limitation in (node_id for node_id in changed_node_ids if node_id in limitation_to_claims):
        affected.update(limitation_to_claims[limitation])
        affected.update(
            relation["asset_id"] for relation in graph["asset_relations"]
            if limitation in relation["boundary_ids"]
        )
        for claim in limitation_to_claims[limitation]:
            affected.update(affected_artifacts(source_graph, [claim])["affected_node_ids"])
    # A channel is not merely a presentation label.  Every package instance is
    # bound to one, via its asset relations.  Therefore a direct change to a
    # V3 channel node invalidates those assets as well; callers can then route
    # the matching immutable instances to an explicit rebuild/withdrawal plan.
    channels = {node["node_id"] for node in graph["nodes"] if node["type"] == "channel"}
    for channel in (node_id for node_id in changed_node_ids if node_id in channels):
        affected.update(
            relation["asset_id"] for relation in graph["asset_relations"]
            if channel in relation["channel_ids"]
        )
    return {
        "schema_version": "content-impact/v3",
        "changed_node_ids": sorted(set(changed_node_ids)),
        "affected_node_ids": sorted(affected - set(changed_node_ids)),
        "required_state": "evidence_bound",
        "boundary": "private impact analysis only; it cannot publish, approve, send, or mutate any source asset.",
    }


def validate_channel_package_v3(
    package: dict[str, Any], *, graph: dict[str, Any], source_graph: dict[str, Any]
) -> dict[str, Any]:
    """Validate a draft-only package without creating or sending one.

    This is intentionally narrower than a publisher.  It proves a prospective
    package preserves its source candidate, source hashes and every retained
    limitation.  `published` is not a legal capability in this contract.
    """

    validate_content_graph_v3(graph, source_graph=source_graph)
    required = {
        "schema_version", "package_id", "candidate_id", "snapshot_id", "version_id",
        "channel_id", "asset_ids", "source_node_ids", "source_node_hashes",
        "limitation_ids", "locales", "capability", "parity_review_ids",
    }
    if not isinstance(package, dict) or set(package) != required or package.get("schema_version") != "channel-package/v1":
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-SHAPE-INVALID")
    candidate = graph["candidate"]
    if (
        package["candidate_id"] != candidate["candidate_id"]
        or package["snapshot_id"] != candidate["snapshot_id"]
        or package["version_id"] != candidate["version_id"]
        or package["capability"] != "draft"
    ):
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-CANDIDATE-MISMATCH")
    channels = {node["node_id"]: node for node in graph["nodes"] if node["type"] == "channel"}
    if package["channel_id"] not in channels or channels[package["channel_id"]]["capability"] != "draft":
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-CHANNEL-INVALID")
    relations = {item["asset_id"]: item for item in graph["asset_relations"]}
    asset_ids = _as_nonempty_unique_strings(package["asset_ids"], "CONTENT-GRAPH-V3-PACKAGE-ASSET-INVALID")
    if not set(asset_ids).issubset(relations) or any(package["channel_id"] not in relations[item]["channel_ids"] for item in asset_ids):
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-ASSET-INVALID")
    expected_sources = sorted({source for asset_id in asset_ids for source in relations[asset_id]["source_node_ids"]})
    package_sources = _as_nonempty_unique_strings(package["source_node_ids"], "CONTENT-GRAPH-V3-PACKAGE-SOURCE-INVALID")
    if package_sources != expected_sources:
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-SOURCE-INVALID")
    v2_nodes = _node_map(source_graph)
    expected_hashes = {source: v2_nodes[source]["content_hash"] for source in expected_sources}
    if package["source_node_hashes"] != expected_hashes:
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-SOURCE-HASH-INVALID")
    expected_limits = sorted({limit for asset_id in asset_ids for limit in relations[asset_id]["boundary_ids"]})
    if _as_nonempty_unique_strings(package["limitation_ids"], "CONTENT-GRAPH-V3-PACKAGE-LIMITATION-INVALID") != expected_limits:
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-LIMITATION-OMITTED")
    locales = _as_nonempty_unique_strings(package["locales"], "CONTENT-GRAPH-V3-PACKAGE-LOCALE-INVALID")
    if any(locale not in _ALLOWED_LOCALES - {NOT_APPLICABLE} for locale in locales):
        raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-LOCALE-INVALID")
    pairs = graph["localization_pairs"]
    package_assets = set(asset_ids)
    for pair in pairs:
        if package_assets.intersection(pair["asset_ids"]) and set(locales) == {"en", "zh-CN"}:
            if pair["status"] != "reviewed" or not set(pair["review_ids"]).issubset(set(package["parity_review_ids"])):
                raise ValidationError("CONTENT-GRAPH-V3-PACKAGE-PARITY-REVIEW-INVALID")
    return {
        "schema_version": "channel-package-v3-verification/v1",
        "package_id": package["package_id"],
        "state": "draft_validated",
        "boundary": "private draft validation only; it cannot publish, schedule, send, or grant owner authorization.",
    }
