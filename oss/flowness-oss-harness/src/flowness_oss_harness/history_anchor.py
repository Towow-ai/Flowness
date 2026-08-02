"""Fail-closed local Git history links to present static mechanism nodes.

The verifier deliberately proves only a narrow, inspectable relation: an
immutable local Git patch is declared against a hash-checked *current static
node*, or the registry says that old implementation has been superseded.  It
does not establish runtime behavior, semantic equivalence, deployment, or
publication rights.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .registry import ValidationError
from .static_chain import verify_static_chain_candidate


BOUNDARY = "LOCAL-GIT-HISTORY-ONLY;DOES-NOT-PROVE-CURRENT-RUNTIME-OR-RIGHTS"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_STATUSES = {"anchored_local_only", "declared_unknown"}
_RELATIONS = {"introduced", "changed", "superseded", "removed"}
UNKNOWN_BOUNDARY = "LOCAL-GIT-HISTORY-UNKNOWN;NO-HISTORICAL-EVIDENCE-ASSERTED"


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise ValidationError("HISTORY-ANCHOR-GIT-COMMAND-FAILED")
    return completed.stdout


def _repository_root(root: Path | str) -> Path:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValidationError("HISTORY-ANCHOR-ROOT-INVALID")
    repository_root = Path(_git(base, "rev-parse", "--show-toplevel").strip()).resolve()
    if repository_root != base:
        raise ValidationError("HISTORY-ANCHOR-ROOT-MUST-BE-REPOSITORY")
    return base


def _valid_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/"):
        return False
    candidate = Path(path)
    return ".." not in candidate.parts and str(candidate) == path


def _read_json(root: Path, relative: str, error: str) -> dict[str, Any]:
    if not _valid_path(relative):
        raise ValidationError(error)
    path = (root / relative).resolve()
    if path.is_symlink() or not path.is_file() or root not in path.parents:
        raise ValidationError(error)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(error) from exc
    if not isinstance(payload, dict):
        raise ValidationError(error)
    return payload


def _static_node(root: Path, reference: dict[str, Any], mechanism_id: str) -> dict[str, Any]:
    if set(reference) != {"manifest_path", "mechanism_id", "role", "ordinal"}:
        raise ValidationError("HISTORY-ANCHOR-STATIC-REF-INVALID")
    if reference.get("mechanism_id") != mechanism_id or reference.get("role") not in {
        "definition", "caller", "consumer", "test", "failure", "recovery"
    } or not isinstance(reference.get("ordinal"), int) or reference["ordinal"] < 0:
        raise ValidationError("HISTORY-ANCHOR-STATIC-REF-INVALID")
    manifest_path = reference.get("manifest_path")
    if not _valid_path(manifest_path):
        raise ValidationError("HISTORY-ANCHOR-STATIC-REF-INVALID")
    manifest = _read_json(root, manifest_path, "HISTORY-ANCHOR-STATIC-MANIFEST-INVALID")
    # Re-use the canonical static verifier so the history layer cannot bless a
    # stale or unhashed current excerpt.
    verify_static_chain_candidate(manifest, root)
    matches = [
        node
        for chain in manifest["chains"]
        if chain["mechanism_id"] == mechanism_id
        for node in chain.get("nodes", [])
        if node["role"] == reference["role"]
    ]
    if reference["ordinal"] >= len(matches):
        raise ValidationError(f"HISTORY-ANCHOR-CURRENT-NODE-MISSING:{mechanism_id}")
    return matches[reference["ordinal"]]


def _node_key(reference: dict[str, Any]) -> tuple[str, str, str, int]:
    """Return the canonical identity of one current static-chain node."""

    return (
        reference["manifest_path"],
        reference["mechanism_id"],
        reference["role"],
        reference["ordinal"],
    )


def _current_static_nodes(root: Path) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    """Enumerate every current static node, not merely registry references."""

    registry_dir = root / "oss/flowness-oss-harness/registries"
    paths = sorted(registry_dir.glob("static-chain-*-candidate-v0.json"))
    if not paths:
        raise ValidationError("HISTORY-ANCHOR-CURRENT-STATIC-CATALOG-MISSING")
    nodes: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValidationError("HISTORY-ANCHOR-CURRENT-STATIC-CATALOG-INVALID")
        relative = path.relative_to(root).as_posix()
        manifest = _read_json(root, relative, "HISTORY-ANCHOR-CURRENT-STATIC-CATALOG-INVALID")
        verify_static_chain_candidate(manifest, root)
        for chain in manifest["chains"]:
            ordinals: dict[str, int] = {}
            for node in chain["nodes"]:
                role = node["role"]
                ordinal = ordinals.get(role, 0)
                ordinals[role] = ordinal + 1
                reference = {
                    "manifest_path": relative,
                    "mechanism_id": chain["mechanism_id"],
                    "role": role,
                    "ordinal": ordinal,
                }
                key = _node_key(reference)
                if key in nodes:
                    raise ValidationError("HISTORY-ANCHOR-CURRENT-STATIC-CATALOG-DUPLICATE")
                nodes[key] = {"reference": reference, "node": node}
    return nodes


def _excerpt(root: Path, node: dict[str, Any]) -> str:
    path = (root / node["path"]).resolve()
    if path.is_symlink() or not path.is_file() or root not in path.parents:
        raise ValidationError("HISTORY-ANCHOR-CURRENT-NODE-UNSAFE")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[node["start_line"] - 1 : node["end_line"]])


def _patch_for_path(root: Path, commit: str, path: str) -> str:
    # A fixed patch hash makes an accidental same-path but unrelated hunk fail.
    return _git(root, "show", "--format=", "--no-ext-diff", "--unified=0", commit, "--", path)


def _patch_hash(patch: str) -> str:
    return "sha256:" + hashlib.sha256(patch.encode()).hexdigest()


def _verify_anchor(root: Path, mechanism_id: str, anchor: dict[str, Any]) -> str:
    if not isinstance(anchor, dict) or set(anchor) != {
        "commit", "reason", "relation_kind", "static_node_ref", "current", "evolution"
    }:
        raise ValidationError("HISTORY-ANCHOR-INVALID")
    commit = anchor.get("commit")
    relation = anchor.get("relation_kind")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValidationError("HISTORY-ANCHOR-COMMIT-INVALID")
    if relation not in _RELATIONS:
        raise ValidationError("HISTORY-ANCHOR-RELATION-INVALID")
    if not isinstance(anchor.get("reason"), str) or not anchor["reason"].strip():
        raise ValidationError("HISTORY-ANCHOR-REASON-REQUIRED")
    actual = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    if actual != commit:
        raise ValidationError(f"HISTORY-ANCHOR-COMMIT-MISSING:{mechanism_id}")

    node = _static_node(root, anchor.get("static_node_ref"), mechanism_id)
    current = anchor.get("current")
    if not isinstance(current, dict) or set(current) != {"path", "excerpt_sha256", "symbol"}:
        raise ValidationError("HISTORY-ANCHOR-CURRENT-INVALID")
    if (
        current.get("path") != node["path"]
        or current.get("excerpt_sha256") != node["excerpt_sha256"]
        or not isinstance(current.get("symbol"), str)
        or not current["symbol"].strip()
    ):
        raise ValidationError(f"HISTORY-ANCHOR-CURRENT-NODE-MISMATCH:{mechanism_id}")
    excerpt = _excerpt(root, node)
    actual_hash = "sha256:" + hashlib.sha256(excerpt.encode()).hexdigest()
    if actual_hash != current["excerpt_sha256"] or current["symbol"] not in excerpt:
        raise ValidationError(f"HISTORY-ANCHOR-CURRENT-NODE-REPLACED:{mechanism_id}")

    evolution = anchor.get("evolution")
    if not isinstance(evolution, dict) or set(evolution) != {"path", "symbol", "patch_sha256"}:
        raise ValidationError("HISTORY-ANCHOR-EVOLUTION-INVALID")
    if (
        not _valid_path(evolution.get("path"))
        or not isinstance(evolution.get("symbol"), str)
        or not evolution["symbol"].strip()
        or not isinstance(evolution.get("patch_sha256"), str)
        or not _SHA256.fullmatch(evolution["patch_sha256"])
    ):
        raise ValidationError("HISTORY-ANCHOR-EVOLUTION-INVALID")
    patch = _patch_for_path(root, commit, evolution["path"])
    if not patch or _patch_hash(patch) != evolution["patch_sha256"]:
        raise ValidationError(f"HISTORY-ANCHOR-EVOLUTION-PATCH-MISMATCH:{mechanism_id}")
    if evolution["symbol"] not in patch:
        raise ValidationError(f"HISTORY-ANCHOR-EVOLUTION-SYMBOL-MISSING:{mechanism_id}")
    if relation in {"introduced", "changed"} and (
        evolution["path"] != current["path"] or evolution["symbol"] != current["symbol"]
    ):
        raise ValidationError(f"HISTORY-ANCHOR-DIRECT-RELATION-MISMATCH:{mechanism_id}")
    return relation


def verify_local_history_anchors(registry: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Verify local evolution links without promoting them into product claims."""

    if set(registry) != {"schema_version", "boundary", "mechanisms", "node_unknowns"}:
        raise ValidationError("HISTORY-ANCHOR-REGISTRY-INVALID")
    if registry.get("schema_version") != "local-history-anchor-registry/v3":
        raise ValidationError("HISTORY-ANCHOR-REGISTRY-INVALID")
    if (
        registry.get("boundary") != BOUNDARY
        or not isinstance(registry.get("mechanisms"), list)
        or not isinstance(registry.get("node_unknowns"), list)
    ):
        raise ValidationError("HISTORY-ANCHOR-BOUNDARY-INVALID")
    if not registry["mechanisms"]:
        raise ValidationError("HISTORY-ANCHOR-REGISTRY-EMPTY")

    repository_root = _repository_root(root)
    current_nodes = _current_static_nodes(repository_root)
    mechanism_ids: set[str] = set()
    verified: list[str] = []
    declared_unknown: list[str] = []
    relations: list[dict[str, Any]] = []
    relation_nodes: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    anchor_count = 0
    for mechanism in registry["mechanisms"]:
        if not isinstance(mechanism, dict) or set(mechanism) != {
            "mechanism_id", "status", "anchors", "unknowns"
        }:
            raise ValidationError("HISTORY-ANCHOR-MECHANISM-INVALID")
        mechanism_id = mechanism.get("mechanism_id")
        if not isinstance(mechanism_id, str) or not mechanism_id or mechanism_id in mechanism_ids:
            raise ValidationError("HISTORY-ANCHOR-MECHANISM-INVALID")
        mechanism_ids.add(mechanism_id)
        status, anchors, unknowns = mechanism.get("status"), mechanism.get("anchors"), mechanism.get("unknowns")
        if status not in _STATUSES or not isinstance(anchors, list) or not isinstance(unknowns, list):
            raise ValidationError("HISTORY-ANCHOR-MECHANISM-INVALID")
        if not unknowns or any(not isinstance(item, str) or not item.strip() for item in unknowns):
            raise ValidationError("HISTORY-ANCHOR-UNKNOWN-REQUIRED")
        if status == "declared_unknown":
            if anchors:
                raise ValidationError("HISTORY-ANCHOR-DECLARED-UNKNOWN-HAS-ANCHOR")
            declared_unknown.append(mechanism_id)
            continue
        if not anchors:
            raise ValidationError("HISTORY-ANCHOR-MISSING-ANCHOR")
        for anchor in anchors:
            relation = _verify_anchor(repository_root, mechanism_id, anchor)
            node_key = _node_key(anchor["static_node_ref"])
            if node_key not in current_nodes:
                raise ValidationError("HISTORY-ANCHOR-CURRENT-NODE-MISSING:" + mechanism_id)
            relation_identity = (anchor["commit"], relation)
            if any(
                (existing["commit"], existing["relation_kind"]) == relation_identity
                for existing in relation_nodes.get(node_key, [])
            ):
                raise ValidationError("HISTORY-ANCHOR-DUPLICATE-RELATION:" + mechanism_id)
            relations.append(
                {
                    "mechanism_id": mechanism_id,
                    "commit": anchor["commit"],
                    "relation_kind": relation,
                    "static_node_ref": anchor["static_node_ref"],
                    "current": anchor["current"],
                }
            )
            relation_nodes.setdefault(node_key, []).append(relations[-1])
            anchor_count += 1
        verified.append(mechanism_id)

    unknown_nodes: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    unknown_ids: set[str] = set()
    for unknown in registry["node_unknowns"]:
        if not isinstance(unknown, dict) or set(unknown) != {
            "unknown_id", "static_node_ref", "question", "next_evidence", "boundary"
        }:
            raise ValidationError("HISTORY-ANCHOR-NODE-UNKNOWN-INVALID")
        unknown_id = unknown.get("unknown_id")
        if not isinstance(unknown_id, str) or not unknown_id.strip() or unknown_id in unknown_ids:
            raise ValidationError("HISTORY-ANCHOR-NODE-UNKNOWN-INVALID")
        unknown_ids.add(unknown_id)
        reference = unknown.get("static_node_ref")
        if not isinstance(reference, dict):
            raise ValidationError("HISTORY-ANCHOR-NODE-UNKNOWN-INVALID")
        # Resolve the reference through the same static verifier; an Unknown is
        # a disposition of a real current node, never a free-floating excuse.
        _static_node(repository_root, reference, reference.get("mechanism_id"))
        key = _node_key(reference)
        if key not in current_nodes:
            raise ValidationError("HISTORY-ANCHOR-NODE-UNKNOWN-CURRENT-NODE-MISSING")
        if key in unknown_nodes:
            raise ValidationError("HISTORY-ANCHOR-DUPLICATE-NODE-UNKNOWN")
        if key in relation_nodes:
            raise ValidationError("HISTORY-ANCHOR-RELATION-UNKNOWN-CONFLICT")
        if (
            unknown.get("boundary") != UNKNOWN_BOUNDARY
            or not isinstance(unknown.get("question"), str)
            or not unknown["question"].strip()
            or not isinstance(unknown.get("next_evidence"), str)
            or not unknown["next_evidence"].strip()
        ):
            raise ValidationError("HISTORY-ANCHOR-NODE-UNKNOWN-INVALID")
        unknown_nodes[key] = unknown

    uncovered = set(current_nodes) - set(relation_nodes) - set(unknown_nodes)
    if uncovered:
        raise ValidationError("HISTORY-ANCHOR-CURRENT-NODE-UNCOVERED")
    extra = (set(relation_nodes) | set(unknown_nodes)) - set(current_nodes)
    if extra:
        raise ValidationError("HISTORY-ANCHOR-CURRENT-NODE-COVERAGE-INVALID")

    return {
        "schema_version": "local-history-anchor-verification/v3",
        "boundary": BOUNDARY,
        "ceiling": "local_history_evolution_only",
        "verified_mechanism_ids": sorted(verified),
        "declared_unknown_ids": sorted(declared_unknown),
        "verified_anchor_count": anchor_count,
        "current_static_node_count": len(current_nodes),
        "anchored_static_node_count": len(relation_nodes),
        "declared_unknown_node_count": len(unknown_nodes),
        "declared_unknown_nodes": [
            {"unknown_id": unknown["unknown_id"], "static_node_ref": unknown["static_node_ref"]}
            for _, unknown in sorted(unknown_nodes.items())
        ],
        "relations": sorted(relations, key=lambda item: (item["mechanism_id"], item["commit"])),
    }
