"""Immutable private manual-publish packages derived from Content Graph v3.

This module intentionally has no publisher, scheduler, network client, or
channel credential surface.  A valid package is a reviewable copy/paste pack,
not permission to send it anywhere.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .approval import validate_owner_approval
from .content_graph import verify_file_backed_assets
from .content_graph_v3 import verify_content_graph_v3_files
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


PACKAGE_SCHEMA = SCHEMAS_ROOT / "channel-package.schema.json"
EXTERNAL_SEND_APPROVAL_SCHEMA = SCHEMAS_ROOT / "channel-package-external-send-approval.schema.json"
NO_SEND_BOUNDARY = (
    "private manual package only; no publication, scheduling, network dispatch, "
    "credential use, or external send is implemented or authorized."
)


def _hash_bytes(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_in_root(root: Path, value: str, error: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value:
        raise ValidationError(error)
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError(error) from exc
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValidationError(error)
    if resolved.is_symlink() or not resolved.is_file():
        raise ValidationError(error)
    return resolved, relative.as_posix()


def _load_json(path: Path, error: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(error) from exc
    if not isinstance(payload, dict):
        raise ValidationError(error)
    return payload


def _package_identity(unsigned: dict[str, Any]) -> str:
    # The identifier is derived before its own field is filled; the immutable
    # package hash below binds the final identifier and every other byte.
    seed = {**unsigned, "package_id": ""}
    return "channel-package-" + canonical_hash(seed).removeprefix("sha256:")[:24]


def _package_unsigned(package: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in package.items() if key != "package_hash"}


def _validate_immutable_package_shape(package: dict[str, Any]) -> None:
    validate_payload(package, PACKAGE_SCHEMA, "channel package")
    verify_self_hash(package, "package_hash")
    unsigned = _package_unsigned(package)
    if package["package_id"] != _package_identity(unsigned):
        raise ValidationError("CHANNEL-PACKAGE-ID-MISMATCH")
    if package["external_send"] != {
        "authorization_required": True,
        "approval_present": False,
        "authorized": False,
        "operation": "not_performed",
    }:
        raise ValidationError("CHANNEL-PACKAGE-EXTERNAL-SEND-STATE-INVALID")


def _load_graphs(root: Path, source_graph: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    graph_path, graph_ref = _safe_in_root(root, source_graph["source_path"], "CHANNEL-PACKAGE-GRAPH-PATH-INVALID")
    graph_raw_hash = _hash_bytes(graph_path)
    if graph_ref != source_graph["source_path"] or graph_raw_hash != source_graph["content_hash"]:
        raise ValidationError("CHANNEL-PACKAGE-GRAPH-HASH-MISMATCH")
    verify_content_graph_v3_files(graph_path, root=root)
    graph = _load_json(graph_path, "CHANNEL-PACKAGE-GRAPH-INVALID")
    if graph["graph_id"] != source_graph["graph_id"] or graph["candidate"] != {
        "candidate_id": source_graph["candidate_id"],
        "snapshot_id": source_graph["snapshot_id"],
        "version_id": source_graph["version_id"],
        "semantic_version": graph["candidate"]["semantic_version"],
        "capability": "draft",
    }:
        raise ValidationError("CHANNEL-PACKAGE-GRAPH-IDENTITY-MISMATCH")
    source_v2_path, _ = _safe_in_root(root, graph["source_graph"]["source_path"], "CHANNEL-PACKAGE-SOURCE-GRAPH-INVALID")
    source_v2 = _load_json(source_v2_path, "CHANNEL-PACKAGE-SOURCE-GRAPH-INVALID")
    verify_file_backed_assets(source_v2, root)
    return graph, source_v2, graph_raw_hash


def _relation_allows_private_draft(
    relation: dict[str, Any], requested_channel_id: str, known_channels: dict[str, dict[str, Any]]
) -> bool:
    """Allow an S0 copy pack to reuse a private-workspace source asset.

    This is deliberately not a promotion rule: the graph's private-workspace
    relation means the source can be *reviewed* through a named draft adapter.
    It does not make that asset eligible for the named external channel, and
    the package still has no send surface or authorization.
    """

    return requested_channel_id in relation["channel_ids"] or (
        requested_channel_id in known_channels
        and "channel.private-workspace" in relation["channel_ids"]
    )


def validate_channel_package(
    package: dict[str, Any], *, root: Path | str
) -> dict[str, Any]:
    """Verify one immutable private manual pack and all of its bound inputs."""

    _validate_immutable_package_shape(package)
    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir():
        raise ValidationError("CHANNEL-PACKAGE-ROOT-UNSAFE")
    graph, source_v2, graph_hash = _load_graphs(base, package["source_graph"])
    candidate = graph["candidate"]
    if any(package["source_graph"][field] != candidate[field] for field in ("candidate_id", "snapshot_id", "version_id")):
        raise ValidationError("CHANNEL-PACKAGE-CANDIDATE-MISMATCH")
    if package["channel"]["release_stage"] != "S0-private-research":
        raise ValidationError("CHANNEL-PACKAGE-STAGE-INVALID")
    if package["channel"]["adapter"]["operation_mode"] != "manual_only":
        raise ValidationError("CHANNEL-PACKAGE-ADAPTER-INVALID")
    if package["capability"] not in {"draft", "manual_publish_pack", "analytics_only"}:
        raise ValidationError("CHANNEL-PACKAGE-CAPABILITY-INVALID")

    v3_channels = {node["node_id"]: node for node in graph["nodes"] if node["type"] == "channel"}
    channel = v3_channels.get(package["channel"]["channel_id"])
    if channel is None or channel["capability"] != "draft":
        raise ValidationError("CHANNEL-PACKAGE-CHANNEL-INVALID")
    relations = {item["asset_id"]: item for item in graph["asset_relations"]}
    v2_assets = {node["node_id"]: node for node in source_v2["nodes"] if node["type"] == "asset"}
    seen_assets: set[str] = set()
    all_limits: set[str] = set()
    all_audiences: set[str] = set()
    all_locales: set[str] = set()
    all_claims: set[str] = set()
    for asset in package["assets"]:
        asset_id = asset["asset_id"]
        if asset_id in seen_assets or asset_id not in relations or asset_id not in v2_assets:
            raise ValidationError("CHANNEL-PACKAGE-ASSET-INVALID")
        seen_assets.add(asset_id)
        relation, v2_asset = relations[asset_id], v2_assets[asset_id]
        if not _relation_allows_private_draft(relation, package["channel"]["channel_id"], v3_channels):
            raise ValidationError("CHANNEL-PACKAGE-ASSET-CHANNEL-MISMATCH")
        expected = {
            "source_path": v2_asset.get("source_path"), "content_hash": v2_asset["content_hash"],
            "locale": relation["locale"], "audience_ids": sorted(relation["audience_ids"]),
            "limitation_ids": sorted(relation["boundary_ids"]),
        }
        actual = {key: asset[key] for key in expected}
        if actual != expected:
            raise ValidationError("CHANNEL-PACKAGE-ASSET-BINDING-MISMATCH")
        asset_path, asset_ref = _safe_in_root(base, asset["source_path"], "CHANNEL-PACKAGE-ASSET-PATH-INVALID")
        if asset_ref != asset["source_path"] or _hash_bytes(asset_path) != asset["content_hash"]:
            raise ValidationError("CHANNEL-PACKAGE-ASSET-HASH-MISMATCH")
        all_limits.update(asset["limitation_ids"])
        all_audiences.update(asset["audience_ids"])
        all_locales.add(asset["locale"])
        all_claims.update(relation["scope"]["claim_ids"])

    # A track is a small audience-specific route through source assets already
    # bound by the V3 graph.  It is navigation, not a free-text source claim:
    # every referenced asset must be valid for this exact draft channel and
    # contribute its inherited limitations and claim scope to the package.
    tracks = package.get("audience_tracks")
    if tracks is not None:
        track_audiences: set[str] = set()
        for track in tracks:
            audience_id = track["audience_id"]
            if audience_id in track_audiences:
                raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-TRACK-DUPLICATE")
            track_audiences.add(audience_id)
            for asset_id in track["entry_asset_ids"]:
                relation = relations.get(asset_id)
                if relation is None or asset_id not in v2_assets:
                    raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-TRACK-ASSET-INVALID")
                if not _relation_allows_private_draft(relation, package["channel"]["channel_id"], v3_channels):
                    raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-TRACK-CHANNEL-MISMATCH")
                if audience_id not in relation["audience_ids"]:
                    raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-TRACK-AUDIENCE-MISMATCH")
                all_limits.update(relation["boundary_ids"])
                all_claims.update(relation["scope"]["claim_ids"])
        if track_audiences != set(package["channel"]["audience_ids"]):
            raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-TRACK-COVERAGE-MISMATCH")
        all_audiences.update(track_audiences)
    if package["limitation_ids"] != sorted(all_limits):
        raise ValidationError("CHANNEL-PACKAGE-LIMITATION-OMITTED")
    if package["channel"]["audience_ids"] != sorted(all_audiences):
        raise ValidationError("CHANNEL-PACKAGE-AUDIENCE-MISMATCH")
    if len(all_locales) != 1 or package["channel"]["locale"] not in all_locales:
        raise ValidationError("CHANNEL-PACKAGE-LOCALE-MISMATCH")
    if "claim_ids" in package and package["claim_ids"] != sorted(all_claims):
        raise ValidationError("CHANNEL-PACKAGE-CLAIM-OMITTED")

    template_path, template_ref = _safe_in_root(base, package["template"]["source_path"], "CHANNEL-PACKAGE-TEMPLATE-PATH-INVALID")
    if template_ref != package["template"]["source_path"] or _hash_bytes(template_path) != package["template"]["content_hash"]:
        raise ValidationError("CHANNEL-PACKAGE-TEMPLATE-HASH-MISMATCH")
    return {
        "schema_version": "channel-package-verification/v1",
        "package_id": package["package_id"],
        "package_hash": package["package_hash"],
        "graph_hash": graph_hash,
        "authorized": False,
        "boundary": NO_SEND_BOUNDARY,
    }


def verify_channel_package_files(package_path: Path | str, *, root: Path | str) -> dict[str, Any]:
    base = Path(root).resolve(strict=True)
    path, _ = _safe_in_root(base, str(package_path), "CHANNEL-PACKAGE-PATH-INVALID")
    return validate_channel_package(_load_json(path, "CHANNEL-PACKAGE-INVALID"), root=base)


def _approval_candidate(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel_package_id": package["package_id"], "channel_package_hash": package["package_hash"],
        "graph_hash": package["source_graph"]["content_hash"], "snapshot": {"snapshot_id": package["source_graph"]["snapshot_id"]},
    }


def _approval_policy(package: dict[str, Any]) -> dict[str, Any]:
    return {"policy": "channel-package-external-send/v1", "package_id": package["package_id"], "operation": "external_send"}


def _approval_decision(package: dict[str, Any]) -> dict[str, Any]:
    return {"decision": "approve_external_send", "package_id": package["package_id"], "package_hash": package["package_hash"], "graph_hash": package["source_graph"]["content_hash"]}


def validate_external_send_owner_approval(
    approval: dict[str, Any] | None, package: dict[str, Any], *, trusted_keys: dict[str, Any] | None, now: Any = None
) -> dict[str, Any]:
    """Require a dedicated signed approval before any hypothetical external send.

    This is an authorization verifier only.  Even a valid result does not
    perform an external action because this module contains no send operation.
    """

    _validate_immutable_package_shape(package)
    if approval is None:
        return {"authorization_required": True, "authorized": False, "boundary": NO_SEND_BOUNDARY}
    validate_payload(approval, EXTERNAL_SEND_APPROVAL_SCHEMA, "channel package external-send approval")
    expected = {
        "package_id": package["package_id"], "package_hash": package["package_hash"],
        "graph_hash": package["source_graph"]["content_hash"], "candidate_id": package["source_graph"]["candidate_id"],
        "snapshot_id": package["source_graph"]["snapshot_id"],
    }
    if any(approval[key] != value for key, value in expected.items()):
        raise ValidationError("CHANNEL-PACKAGE-APPROVAL-BINDING-MISMATCH")
    identity = {key: value for key, value in approval.items() if key != "owner_approval"}
    identity["approval_id"] = ""
    expected_id = "channel-send-approval-" + canonical_hash(identity).removeprefix("sha256:")[:24]
    if approval["approval_id"] != expected_id:
        raise ValidationError("CHANNEL-PACKAGE-APPROVAL-ID-MISMATCH")
    if not package["source_graph"]["snapshot_id"].startswith("sha256:"):
        raise ValidationError("CHANNEL-PACKAGE-APPROVAL-REQUIRES-SEALED-SNAPSHOT")
    decision = validate_owner_approval(
        approval["owner_approval"], _approval_candidate(package), canonical_hash(_approval_policy(package)),
        canonical_hash(_approval_decision(package)), trusted_keys, now,
    )
    if decision != "approve":
        raise ValidationError("CHANNEL-PACKAGE-APPROVAL-NOT-APPROVED")
    return {"authorization_required": True, "authorized": True, "boundary": NO_SEND_BOUNDARY}
