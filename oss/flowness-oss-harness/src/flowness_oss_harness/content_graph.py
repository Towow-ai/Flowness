from __future__ import annotations

"""Minimal, executable invalidation engine for evidence-bound communications."""

from collections import deque
import hashlib
import json
from pathlib import Path
from typing import Any

from .registry import ValidationError

NODE_TYPES = {
    "claim", "mechanism", "evidence", "module", "diagram", "case",
    "benchmark", "demo", "section", "asset", "channel_package",
}
NODE_STATES = {
    "draft", "evidence_bound", "jury_accepted", "staged", "owner_approved",
    "published", "withdrawn", "superseded",
}
EDGE_TYPES = {
    "supports", "disproves", "limits", "implements", "depends_on", "depicts",
    "exercises", "tests", "replays", "explains", "derived_from", "contains",
    "localized_as", "supersedes",
}

# For an edge A --type--> B, this says a change in the first named node must
# invalidate the second. Narrative/packaging edges deliberately reverse the
# authoring direction: an Asset derived_from a Section depends on that Section.
FORWARD = {
    "supports", "disproves", "limits", "implements", "depends_on",
    "exercises", "tests", "replays", "supersedes",
}
REVERSE = {"depicts", "explains", "derived_from", "contains", "localized_as"}


def validate_graph(graph: dict[str, Any]) -> None:
    if set(graph) != {"schema_version", "nodes", "edges"}:
        raise ValidationError("CONTENT-GRAPH-SHAPE-INVALID")
    if graph["schema_version"] not in {"content-graph/v1", "content-graph/v2"}:
        raise ValidationError("CONTENT-GRAPH-VERSION-INVALID")
    if not isinstance(graph["nodes"], list) or not isinstance(graph["edges"], list):
        raise ValidationError("CONTENT-GRAPH-SHAPE-INVALID")
    nodes: dict[str, dict[str, Any]] = {}
    for node in graph["nodes"]:
        expected_keys = {"node_id", "type", "state", "snapshot_id", "content_hash"}
        if not isinstance(node, dict):
            raise ValidationError("CONTENT-GRAPH-NODE-INVALID")
        if graph["schema_version"] == "content-graph/v2" and node.get("type") == "asset":
            expected_keys.add("source_path")
        if set(node) != expected_keys:
            raise ValidationError("CONTENT-GRAPH-NODE-INVALID")
        node_id = node["node_id"]
        if not isinstance(node_id, str) or not node_id or node_id in nodes:
            raise ValidationError("CONTENT-GRAPH-NODE-ID-INVALID")
        if node["type"] not in NODE_TYPES or node["state"] not in NODE_STATES:
            raise ValidationError("CONTENT-GRAPH-NODE-INVALID")
        if not isinstance(node["snapshot_id"], str) or not isinstance(node["content_hash"], str):
            raise ValidationError("CONTENT-GRAPH-NODE-INVALID")
        if "source_path" in node and (not isinstance(node["source_path"], str) or not node["source_path"]):
            raise ValidationError("CONTENT-GRAPH-NODE-INVALID")
        nodes[node_id] = node
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in graph["edges"]:
        if not isinstance(edge, dict) or set(edge) != {"from_id", "to_id", "type"}:
            raise ValidationError("CONTENT-GRAPH-EDGE-INVALID")
        key = (edge["from_id"], edge["to_id"], edge["type"])
        if (
            edge["type"] not in EDGE_TYPES
            or edge["from_id"] not in nodes
            or edge["to_id"] not in nodes
            or edge["from_id"] == edge["to_id"]
            or key in seen_edges
        ):
            raise ValidationError("CONTENT-GRAPH-EDGE-INVALID")
        seen_edges.add(key)
    # A promotable claim needs an evidence relationship. This is intentionally
    # weaker than release acceptance, but prevents an asset graph from creating
    # free-floating evidence_bound/published claims.
    for node in nodes.values():
        if node["type"] != "claim" or node["state"] in {"draft", "withdrawn", "superseded"}:
            continue
        if not any(
            edge["to_id"] == node["node_id"] and edge["type"] in {"supports", "limits", "disproves"}
            and nodes[edge["from_id"]]["type"] == "evidence"
            for edge in graph["edges"]
        ):
            raise ValidationError("CONTENT-GRAPH-CLAIM-WITHOUT-EVIDENCE")


def verify_file_backed_assets(graph: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Verify that v2 asset nodes name actual immutable bytes under ``root``.

    A graph may preserve evidence provenance but still become stale when a
    writer edits a README or localized FAQ.  This check turns that otherwise
    silent drift into an explicit review input; it never changes source files
    or node state.
    """

    validate_graph(graph)
    if graph["schema_version"] != "content-graph/v2":
        raise ValidationError("CONTENT-GRAPH-FILE-BINDINGS-REQUIRE-V2")
    base = Path(root).resolve(strict=True)
    verified: list[str] = []
    for node in graph["nodes"]:
        if node["type"] != "asset":
            continue
        path = (base / node["source_path"]).resolve(strict=True)
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise ValidationError("CONTENT-GRAPH-ASSET-PATH-ESCAPES-ROOT") from exc
        if path.is_symlink() or not path.is_file():
            raise ValidationError("CONTENT-GRAPH-ASSET-PATH-UNSAFE")
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != node["content_hash"]:
            raise ValidationError(f"CONTENT-GRAPH-ASSET-HASH-MISMATCH:{node['node_id']}")
        verified.append(node["node_id"])
    return {"schema_version": "content-file-verification/v1", "verified_asset_ids": sorted(verified)}


COMPLETENESS_SCHEMA = "content-graph-completeness/v1"


def _safe_relative_path(root: Path, value: Any, *, error: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError(error)
    try:
        candidate = (root / value).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError(error) from exc
    return candidate


def _read_json_object(path: Path, *, error: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(error)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(error) from exc
    if not isinstance(value, dict):
        raise ValidationError(error)
    return value


def verify_content_graph_completeness(
    manifest: dict[str, Any], graph: dict[str, Any], root: Path | str
) -> dict[str, Any]:
    """Fail closed when a scoped candidate-facing asset escapes the graph.

    The manifest is deliberately an explicit local-candidate allowlist, not a
    discovery claim about every file in the repository.  Its scopes discover
    candidate-facing Markdown below the named directories; its explicit paths
    cover singular materials such as the README, the receipt tool, and the
    graph's own explanation.  Thus adding a new candidate document inside a
    scope fails review until it is hash-bound as a graph asset.
    """

    expected_keys = {
        "schema_version", "scope", "boundary", "graph_source_path",
        "mechanism_registry_path", "asset_scopes", "required_source_paths",
        "seed_mechanism_nodes", "unrepresented_public_claim_seed_ids",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
    if manifest["schema_version"] != COMPLETENESS_SCHEMA:
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-VERSION-INVALID")
    if not isinstance(manifest["scope"], str) or not isinstance(manifest["boundary"], str):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
    validate_graph(graph)
    base = Path(root).resolve(strict=True)

    required_paths = manifest["required_source_paths"]
    if not isinstance(required_paths, list) or not all(isinstance(item, str) and item for item in required_paths):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
    if len(set(required_paths)) != len(required_paths):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-DUPLICATE-PATH")
    expected_paths = set(required_paths)
    for relative in required_paths:
        path = _safe_relative_path(base, relative, error="CONTENT-GRAPH-COMPLETENESS-PATH-INVALID")
        if path.is_symlink() or not path.is_file():
            raise ValidationError("CONTENT-GRAPH-COMPLETENESS-PATH-INVALID")

    scopes = manifest["asset_scopes"]
    if not isinstance(scopes, list) or not scopes:
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
    seen_scope_keys: set[tuple[str, str]] = set()
    for scope in scopes:
        if not isinstance(scope, dict) or set(scope) != {"directory", "glob"}:
            raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
        directory_value, glob = scope["directory"], scope["glob"]
        if not isinstance(directory_value, str) or not isinstance(glob, str) or not glob:
            raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
        scope_key = (directory_value, glob)
        if scope_key in seen_scope_keys:
            raise ValidationError("CONTENT-GRAPH-COMPLETENESS-DUPLICATE-SCOPE")
        seen_scope_keys.add(scope_key)
        directory = _safe_relative_path(base, directory_value, error="CONTENT-GRAPH-COMPLETENESS-PATH-INVALID")
        if directory.is_symlink() or not directory.is_dir():
            raise ValidationError("CONTENT-GRAPH-COMPLETENESS-PATH-INVALID")
        for path in directory.glob(glob):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                expected_paths.add(path.resolve(strict=True).relative_to(base).as_posix())
            except (OSError, ValueError) as exc:
                raise ValidationError("CONTENT-GRAPH-COMPLETENESS-PATH-INVALID") from exc

    graph_assets = [node for node in graph["nodes"] if node["type"] == "asset"]
    asset_paths = [node["source_path"] for node in graph_assets]
    if len(set(asset_paths)) != len(asset_paths):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-DUPLICATE-GRAPH-ASSET-PATH")
    if set(asset_paths) != expected_paths:
        missing = sorted(expected_paths - set(asset_paths))
        extra = sorted(set(asset_paths) - expected_paths)
        raise ValidationError(
            "CONTENT-GRAPH-COMPLETENESS-ASSET-SET-MISMATCH:"
            f"missing={','.join(missing)};extra={','.join(extra)}"
        )

    mechanism_registry = _read_json_object(
        _safe_relative_path(base, manifest["mechanism_registry_path"], error="CONTENT-GRAPH-COMPLETENESS-PATH-INVALID"),
        error="CONTENT-GRAPH-COMPLETENESS-MECHANISM-REGISTRY-INVALID",
    )
    seeds = mechanism_registry.get("mechanisms")
    if not isinstance(seeds, list) or any(not isinstance(item, dict) or not isinstance(item.get("mechanism_id"), str) for item in seeds):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-MECHANISM-REGISTRY-INVALID")
    seed_ids = {item["mechanism_id"] for item in seeds}
    seed_nodes = manifest["seed_mechanism_nodes"]
    if not isinstance(seed_nodes, dict) or set(seed_nodes) != seed_ids or not all(isinstance(value, str) and value for value in seed_nodes.values()):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SEED-MAPPING-INVALID")
    graph_nodes = {node["node_id"]: node for node in graph["nodes"]}
    if any(node_id not in graph_nodes or graph_nodes[node_id]["type"] != "mechanism" for node_id in seed_nodes.values()):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-SEED-MAPPING-INVALID")

    no_claims = manifest["unrepresented_public_claim_seed_ids"]
    if not isinstance(no_claims, list) or set(no_claims) != seed_ids or len(no_claims) != len(seed_ids):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-CLAIM-BOUNDARY-INVALID")
    if any(item.get("public_claims") for item in seeds):
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-CLAIM-BOUNDARY-INVALID")

    return {
        "schema_version": "content-graph-completeness-result/v1",
        "scope": manifest["scope"],
        "asset_source_paths": sorted(expected_paths),
        "asset_count": len(expected_paths),
        "seed_mechanism_count": len(seed_ids),
        "unrepresented_public_claim_seed_ids": sorted(no_claims),
        "boundary": manifest["boundary"],
    }


def verify_content_graph_completeness_files(
    manifest_path: Path | str, root: Path | str
) -> dict[str, Any]:
    """Load and verify the graph and completeness manifest from one root."""

    base = Path(root).resolve(strict=True)
    manifest_file = Path(manifest_path).resolve(strict=True)
    try:
        manifest_file.relative_to(base)
    except ValueError as exc:
        raise ValidationError("CONTENT-GRAPH-COMPLETENESS-PATH-INVALID") from exc
    manifest = _read_json_object(manifest_file, error="CONTENT-GRAPH-COMPLETENESS-SHAPE-INVALID")
    graph_path = _safe_relative_path(base, manifest.get("graph_source_path"), error="CONTENT-GRAPH-COMPLETENESS-PATH-INVALID")
    graph = _read_json_object(graph_path, error="CONTENT-GRAPH-COMPLETENESS-GRAPH-INVALID")
    verify_file_backed_assets(graph, base)
    return verify_content_graph_completeness(manifest, graph, base)


def affected_artifacts(graph: dict[str, Any], changed_node_ids: list[str]) -> dict[str, Any]:
    """Return downstream IDs that must return to evidence_bound after a change."""

    validate_graph(graph)
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    if not changed_node_ids or any(node_id not in nodes for node_id in changed_node_ids):
        raise ValidationError("CONTENT-GRAPH-CHANGE-SET-INVALID")
    neighbors: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in graph["edges"]:
        if edge["type"] in FORWARD:
            neighbors[edge["from_id"]].add(edge["to_id"])
        else:
            neighbors[edge["to_id"]].add(edge["from_id"])
    queue = deque(changed_node_ids)
    visited = set(changed_node_ids)
    while queue:
        current = queue.popleft()
        for dependent in sorted(neighbors[current]):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    affected = sorted(
        node_id for node_id in visited - set(changed_node_ids)
        if nodes[node_id]["type"] in {"claim", "diagram", "case", "benchmark", "demo", "section", "asset", "channel_package"}
    )
    return {
        "schema_version": "content-impact/v1",
        "changed_node_ids": sorted(set(changed_node_ids)),
        "affected_node_ids": affected,
        "required_state": "evidence_bound",
        "boundary": "impact analysis identifies review work; it does not publish, unpublish, or alter source evidence.",
    }
