"""Immutable, read-only impact review plans for private Content Graphs.

The older impact receipt accepts an explicitly supplied change-set.  This
module instead compares two exact graph revisions and derives the change-set
itself.  It therefore cannot be used to smuggle an arbitrary label into a
review plan.  The sole write is an immutable local plan; there is no channel,
publication, scheduling, or source-mutation capability here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .content_graph import affected_artifacts, validate_graph
from .content_graph_v3 import affected_artifacts_v3, validate_content_graph_v3
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


SCHEMA = SCHEMAS_ROOT / "content-impact-review-plan.schema.json"
NO_PUBLISH_BOUNDARY = (
    "local review planning only; this plan cannot publish, unpublish, schedule, "
    "send, approve, or mutate source evidence or channel material."
)


@dataclass(frozen=True)
class _PreparedGraph:
    graph: dict[str, Any]
    source_graph: dict[str, Any]
    path: str
    identity: dict[str, str]

    @property
    def is_v3(self) -> bool:
        return self.graph["schema_version"] == "content-graph/v3"

    @property
    def nodes(self) -> dict[str, dict[str, Any]]:
        nodes = {node["node_id"]: node for node in self.source_graph["nodes"]}
        if self.is_v3:
            for node in self.graph["nodes"]:
                if node["node_id"] in nodes:
                    raise ValidationError("CONTENT-IMPACT-PLAN-NODE-LAYER-COLLISION")
                nodes[node["node_id"]] = node
        return nodes

    @property
    def asset_relations(self) -> dict[str, dict[str, Any]]:
        if not self.is_v3:
            return {}
        return {item["asset_id"]: item for item in self.graph["asset_relations"]}


def _safe_root(root: Path | str) -> Path:
    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir():
        raise ValidationError("CONTENT-IMPACT-PLAN-ROOT-UNSAFE")
    return base


def _safe_relative_file(root: Path, value: Path | str, *, error: str) -> tuple[Path, str]:
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError(error) from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError(error)
    stable = root / relative
    if stable.is_symlink() or not stable.is_file():
        raise ValidationError(error)
    return stable, relative.as_posix()


def _read_object(path: Path, *, error: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(error) from exc
    if not isinstance(payload, dict):
        raise ValidationError(error)
    return payload


def _single_snapshot(graph: dict[str, Any]) -> str:
    snapshots = {node["snapshot_id"] for node in graph["nodes"]}
    if len(snapshots) != 1:
        raise ValidationError("CONTENT-IMPACT-PLAN-V2-SNAPSHOT-MISMATCH")
    return next(iter(snapshots))


def _prepare_graph(root: Path, supplied_path: Path | str) -> _PreparedGraph:
    path, path_ref = _safe_relative_file(root, supplied_path, error="CONTENT-IMPACT-PLAN-GRAPH-PATH-INVALID")
    graph = _read_object(path, error="CONTENT-IMPACT-PLAN-GRAPH-INVALID")
    graph_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    version = graph.get("schema_version")
    if version == "content-graph/v2":
        validate_graph(graph)
        snapshot_id = _single_snapshot(graph)
        identity = {
            "graph_path": path_ref,
            "graph_schema_version": version,
            "graph_id": f"v2:{snapshot_id}",
            "candidate_id": f"v2-migrated:{snapshot_id}",
            "snapshot_id": snapshot_id,
            "version_id": "v2-migrated",
            "graph_hash": graph_hash,
            "source_graph_hash": graph_hash,
        }
        return _PreparedGraph(graph, graph, path_ref, identity)
    if version != "content-graph/v3":
        raise ValidationError("CONTENT-IMPACT-PLAN-GRAPH-VERSION-INVALID")
    source_ref = graph.get("source_graph", {}).get("source_path")
    if not isinstance(source_ref, str) or not source_ref:
        raise ValidationError("CONTENT-IMPACT-PLAN-SOURCE-PATH-INVALID")
    source_path, _ = _safe_relative_file(root, source_ref, error="CONTENT-IMPACT-PLAN-SOURCE-PATH-INVALID")
    source_graph = _read_object(source_path, error="CONTENT-IMPACT-PLAN-SOURCE-INVALID")
    source_hash = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    validate_content_graph_v3(graph, source_graph=source_graph, source_hash=source_hash)
    candidate = graph["candidate"]
    identity = {
        "graph_path": path_ref,
        "graph_schema_version": version,
        "graph_id": graph["graph_id"],
        "candidate_id": candidate["candidate_id"],
        "snapshot_id": candidate["snapshot_id"],
        "version_id": candidate["version_id"],
        "graph_hash": graph_hash,
        "source_graph_hash": source_hash,
    }
    return _PreparedGraph(graph, source_graph, path_ref, identity)


def _require_same_candidate(predecessor: _PreparedGraph, current: _PreparedGraph) -> None:
    # A V2 graph can be compared only to another V2 graph from the same
    # snapshot-derived migration identity.  A V3 relation overlay is a
    # separate contract and must not be silently matched by node spelling.
    if predecessor.is_v3 != current.is_v3:
        raise ValidationError("CONTENT-IMPACT-PLAN-CROSS-CANDIDATE-MISMATCH")
    keys = ("candidate_id", "snapshot_id", "version_id")
    if any(predecessor.identity[key] != current.identity[key] for key in keys):
        raise ValidationError("CONTENT-IMPACT-PLAN-CROSS-CANDIDATE-MISMATCH")


def _node_fingerprint(node: dict[str, Any]) -> str:
    return canonical_hash(node)


def _changes(predecessor: _PreparedGraph, current: _PreparedGraph) -> dict[str, list[str]]:
    old_nodes, new_nodes = predecessor.nodes, current.nodes
    old_ids, new_ids = set(old_nodes), set(new_nodes)
    changed = sorted(
        node_id for node_id in old_ids & new_ids
        if _node_fingerprint(old_nodes[node_id]) != _node_fingerprint(new_nodes[node_id])
    )
    old_relations, new_relations = predecessor.asset_relations, current.asset_relations
    relation_ids = set(old_relations) | set(new_relations)
    changed_relations = sorted(
        asset_id for asset_id in relation_ids
        if old_relations.get(asset_id) != new_relations.get(asset_id)
    )
    return {
        "added_node_ids": sorted(new_ids - old_ids),
        "removed_node_ids": sorted(old_ids - new_ids),
        "changed_node_ids": changed,
        "changed_asset_relation_ids": changed_relations,
    }


def _impact(prepared: _PreparedGraph, changed_ids: list[str]) -> set[str]:
    if not changed_ids:
        return set()
    if prepared.is_v3:
        return set(
            affected_artifacts_v3(
                prepared.graph, source_graph=prepared.source_graph, changed_node_ids=changed_ids
            )["affected_node_ids"]
        )
    return set(affected_artifacts(prepared.source_graph, changed_ids)["affected_node_ids"])


def _plan_id(unsigned: dict[str, Any]) -> str:
    identity = {
        "authorization": unsigned["authorization"],
        "predecessor": unsigned["predecessor"],
        "current": unsigned["current"],
        "changes": unsigned["changes"],
        "ripple": unsigned["ripple"],
        "review_obligations": unsigned["review_obligations"],
        "boundary": unsigned["boundary"],
    }
    return "content-impact-review-" + canonical_hash(identity).removeprefix("sha256:")[:24]


def _channel_ids(prepared: _PreparedGraph, asset_ids: set[str], affected_nodes: set[str]) -> set[str]:
    result = {
        node_id for node_id in affected_nodes
        if prepared.nodes.get(node_id, {}).get("type") in {"channel_package", "channel"}
    }
    if prepared.is_v3:
        for asset_id, relation in prepared.asset_relations.items():
            if asset_id in asset_ids:
                result.update(relation["channel_ids"])
    return result


def _obligations(
    *, current: _PreparedGraph, review_assets: set[str], current_affected: set[str],
    predecessor_affected: set[str], channels: set[str], triggers: list[str],
) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    for node_id in sorted(current_affected):
        if current.nodes[node_id]["type"] == "claim":
            obligations.append({
                "obligation_id": f"revalidate-claim:{node_id}", "kind": "revalidate_claim",
                "target_id": node_id, "trigger_ids": triggers, "required_state": "evidence_bound",
            })
    for asset_id in sorted(review_assets):
        obligations.append({
            "obligation_id": f"review-asset:{asset_id}", "kind": "review_asset",
            "target_id": asset_id, "trigger_ids": triggers, "required_state": "evidence_bound",
        })
    for channel_id in sorted(channels):
        obligations.append({
            "obligation_id": f"reassemble-channel:{channel_id}", "kind": "reassemble_channel_package",
            "target_id": channel_id, "trigger_ids": triggers, "required_state": "evidence_bound",
        })
    for node_id in sorted(predecessor_affected - set(current.nodes)):
        obligations.append({
            "obligation_id": f"review-predecessor-removal:{node_id}", "kind": "review_predecessor_removal",
            "target_id": node_id, "trigger_ids": triggers, "required_state": "evidence_bound",
        })
    return obligations


def build_content_impact_review_plan(
    *, graph_root: Path | str, predecessor_graph_path: Path | str, current_graph_path: Path | str,
) -> dict[str, Any]:
    """Derive one deterministic review plan from two compatible graph revisions.

    Inputs are graph paths, never a caller-selected changed-node list.  This
    makes changed README, evidence, mechanism, limitation and relationship
    records reviewable only when their canonical graph bytes differ.
    """

    root = _safe_root(graph_root)
    predecessor = _prepare_graph(root, predecessor_graph_path)
    current = _prepare_graph(root, current_graph_path)
    _require_same_candidate(predecessor, current)
    changes = _changes(predecessor, current)
    current_changed = changes["added_node_ids"] + changes["changed_node_ids"]
    predecessor_changed = changes["removed_node_ids"] + changes["changed_node_ids"]
    current_affected = _impact(current, current_changed)
    predecessor_affected = _impact(predecessor, predecessor_changed)
    current_assets = {
        node_id for node_id in current_affected
        if current.nodes[node_id]["type"] == "asset"
    }
    current_assets.update(
        node_id for node_id in current_changed
        if current.nodes[node_id]["type"] == "asset"
    )
    current_assets.update(
        asset_id for asset_id in changes["changed_asset_relation_ids"]
        if asset_id in current.nodes and current.nodes[asset_id]["type"] == "asset"
    )
    predecessor_assets = {
        node_id for node_id in predecessor_affected
        if predecessor.nodes[node_id]["type"] == "asset"
    }
    # Include changed channel nodes themselves as an explicit ripple source.
    # `affected_artifacts_v3` deliberately returns downstream artifacts, so
    # without this union a channel-only graph edit could be visually present
    # in the change set yet absent from the package action bindings.
    channels = _channel_ids(current, current_assets, current_affected | set(current_changed))
    channels.update(_channel_ids(predecessor, predecessor_assets, predecessor_affected | set(predecessor_changed)))
    triggers = sorted(
        current_changed + changes["removed_node_ids"]
        + [f"asset_relation:{item}" for item in changes["changed_asset_relation_ids"]]
    )
    ripple = {
        "current_affected_node_ids": sorted(current_affected),
        "predecessor_affected_node_ids": sorted(predecessor_affected),
        "review_asset_ids": sorted(current_assets),
        "invalidated_channel_package_ids": sorted(channels),
    }
    unsigned = {
        "schema_version": "content-impact-review-plan/v1",
        "plan_id": "",
        "authorization": "local_review_only",
        "predecessor": predecessor.identity,
        "current": current.identity,
        "changes": changes,
        "ripple": ripple,
        "review_obligations": _obligations(
            current=current, review_assets=current_assets, current_affected=current_affected,
            predecessor_affected=predecessor_affected, channels=channels, triggers=triggers,
        ),
        "boundary": NO_PUBLISH_BOUNDARY,
    }
    unsigned["plan_id"] = _plan_id(unsigned)
    plan = {**unsigned, "plan_hash": canonical_hash(unsigned)}
    validate_payload(plan, SCHEMA, "content impact review plan")
    return plan


def create_content_impact_review_plan(
    *, graph_root: Path | str, predecessor_graph_path: Path | str, current_graph_path: Path | str,
    output: Path | str,
) -> dict[str, Any]:
    """Atomically seal a plan without altering either graph or any channel asset."""

    plan = build_content_impact_review_plan(
        graph_root=graph_root, predecessor_graph_path=predecessor_graph_path,
        current_graph_path=current_graph_path,
    )
    target = Path(output)
    if target.is_symlink():
        raise ValidationError("CONTENT-IMPACT-PLAN-PATH-UNSAFE")
    atomic_create_json(target, plan)
    return plan


def verify_content_impact_review_plan(
    plan_path: Path | str, *, graph_root: Path | str,
) -> dict[str, Any]:
    """Fail closed unless current graph bytes reproduce the exact sealed plan."""

    target = Path(plan_path)
    if target.is_symlink() or not target.is_file():
        raise ValidationError("CONTENT-IMPACT-PLAN-PATH-UNSAFE")
    plan = load_validated_json(target, SCHEMA, "content impact review plan")
    verify_self_hash(plan, "plan_hash")
    unsigned = {key: value for key, value in plan.items() if key != "plan_hash"}
    if plan["plan_id"] != _plan_id(unsigned):
        raise ValidationError("CONTENT-IMPACT-PLAN-ID-MISMATCH")
    if plan["authorization"] != "local_review_only" or plan["boundary"] != NO_PUBLISH_BOUNDARY:
        raise ValidationError("CONTENT-IMPACT-PLAN-PUBLISH-BOUNDARY-INVALID")
    rebuilt = build_content_impact_review_plan(
        graph_root=graph_root,
        predecessor_graph_path=plan["predecessor"]["graph_path"],
        current_graph_path=plan["current"]["graph_path"],
    )
    if rebuilt != plan:
        raise ValidationError("CONTENT-IMPACT-PLAN-RECOMPUTATION-MISMATCH")
    return plan
