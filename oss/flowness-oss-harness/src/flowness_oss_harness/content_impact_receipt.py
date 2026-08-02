"""Immutable, local-only review receipts for Content Graph invalidation.

The receipt proves a particular Content Graph and change-set implied a bounded
review set at the time it was created.  It is intentionally *not* a content
state transition and has no publishing capability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .content_graph import affected_artifacts, validate_graph
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


CONTENT_IMPACT_RECEIPT_SCHEMA = SCHEMAS_ROOT / "content-impact-receipt.schema.json"
NO_PUBLISH_BOUNDARY = (
    "local impact review only; this receipt cannot publish, unpublish, schedule, "
    "or mutate source evidence."
)


def _safe_root(root: Path | str) -> Path:
    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir():
        raise ValidationError("CONTENT-IMPACT-ROOT-UNSAFE")
    return base


def _safe_graph_path(root: Path, source_path: Path | str) -> tuple[Path, str]:
    """Return a regular graph file and the only portable reference to it."""

    supplied = Path(source_path)
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError("CONTENT-IMPACT-GRAPH-PATH-ESCAPES-ROOT") from exc
    # Resolve from the declared root again so a symlink supplied outside the
    # root cannot quietly become the receipt's apparent in-root source.
    stable = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError("CONTENT-IMPACT-GRAPH-PATH-UNSAFE")
    if stable.is_symlink() or not stable.is_file():
        raise ValidationError("CONTENT-IMPACT-GRAPH-PATH-UNSAFE")
    return stable, relative.as_posix()


def _load_graph(path: Path) -> dict[str, Any]:
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("CONTENT-IMPACT-GRAPH-INVALID") from exc
    if not isinstance(graph, dict):
        raise ValidationError("CONTENT-IMPACT-GRAPH-INVALID")
    validate_graph(graph)
    return graph


def _receipt_id(unsigned: dict[str, Any]) -> str:
    identity = {
        "authorization": unsigned["authorization"],
        "graph": unsigned["graph"],
        "changed_node_ids": unsigned["changed_node_ids"],
        "affected_node_ids": unsigned["affected_node_ids"],
        "required_state": unsigned["required_state"],
        "boundary": unsigned["boundary"],
    }
    return "content-impact-" + canonical_hash(identity).removeprefix("sha256:")[:24]


def create_content_impact_receipt(
    *,
    graph_root: Path | str,
    graph_path: Path | str,
    changed_node_ids: list[str],
    output: Path | str,
) -> dict[str, Any]:
    """Create one non-overwritable, review-only receipt from a graph change.

    The caller supplies a root to make the graph reference portable and to
    reject a graph file outside the reviewed tree.  This function performs no
    channel operation and leaves graph nodes and source assets untouched.
    """

    root = _safe_root(graph_root)
    source, source_ref = _safe_graph_path(root, graph_path)
    graph = _load_graph(source)
    impact = affected_artifacts(graph, changed_node_ids)
    unsigned = {
        "schema_version": "content-impact-receipt/v1",
        "receipt_id": "",
        "authorization": "local_review_only",
        "graph": {
            "source_path": source_ref,
            "graph_hash": canonical_hash(graph),
        },
        "changed_node_ids": impact["changed_node_ids"],
        "affected_node_ids": impact["affected_node_ids"],
        "required_state": impact["required_state"],
        "boundary": NO_PUBLISH_BOUNDARY,
    }
    unsigned["receipt_id"] = _receipt_id(unsigned)
    receipt = {**unsigned, "receipt_hash": canonical_hash(unsigned)}
    validate_payload(receipt, CONTENT_IMPACT_RECEIPT_SCHEMA, "content impact receipt")
    target = Path(output)
    if target.is_symlink():
        raise ValidationError("CONTENT-IMPACT-RECEIPT-PATH-UNSAFE")
    atomic_create_json(target, receipt)
    return receipt


def verify_content_impact_receipt(
    receipt_path: Path | str,
    *,
    graph_root: Path | str,
) -> dict[str, Any]:
    """Fail closed unless graph bytes still imply the sealed review set."""

    target = Path(receipt_path)
    if target.is_symlink() or not target.is_file():
        raise ValidationError("CONTENT-IMPACT-RECEIPT-PATH-UNSAFE")
    receipt = load_validated_json(target, CONTENT_IMPACT_RECEIPT_SCHEMA, "content impact receipt")
    verify_self_hash(receipt, "receipt_hash")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if receipt["receipt_id"] != _receipt_id(unsigned):
        raise ValidationError("CONTENT-IMPACT-RECEIPT-ID-MISMATCH")
    if receipt["authorization"] != "local_review_only" or receipt["boundary"] != NO_PUBLISH_BOUNDARY:
        raise ValidationError("CONTENT-IMPACT-PUBLISH-BOUNDARY-INVALID")

    root = _safe_root(graph_root)
    source, source_ref = _safe_graph_path(root, receipt["graph"]["source_path"])
    if source_ref != receipt["graph"]["source_path"]:
        raise ValidationError("CONTENT-IMPACT-GRAPH-PATH-MISMATCH")
    graph = _load_graph(source)
    if canonical_hash(graph) != receipt["graph"]["graph_hash"]:
        raise ValidationError("CONTENT-IMPACT-GRAPH-HASH-MISMATCH")
    recomputed = affected_artifacts(graph, receipt["changed_node_ids"])
    if (
        recomputed["affected_node_ids"] != receipt["affected_node_ids"]
        or recomputed["required_state"] != receipt["required_state"]
    ):
        raise ValidationError("CONTENT-IMPACT-RECOMPUTATION-MISMATCH")
    return receipt
