"""Fail-closed registry for semantic arrows in the public Open Alpha Atlas.

The Atlas is deliberately a static public candidate.  This verifier therefore does
not turn an arrow into a runtime trace.  It only makes every non-decorative
edge in D1, D2 and D5 inspectable: its rendered endpoints, mechanism/plane
crossing, authority, state, provenance, failure/recovery owner and the exact
evidence boundary must be declared together.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .registry import ValidationError


SCHEMA_VERSION = "architecture-edge-registry/v1"
BOUNDARY = "UNSEALED-LOCAL-SOURCE;STATIC-ARCHITECTURE-EDGES;RUNTIME-UNAVAILABLE"
_REQUIRED_EDGE_FIELDS = {
    "edge_id",
    "artifact_id",
    "diagram_id",
    "rendered_source_id",
    "rendered_target_id",
    "source",
    "target",
    "producer_output",
    "consumer_input",
    "authoritative_state",
    "schema_version_or_unknown",
    "authority",
    "correlation_provenance",
    "failure_owner",
    "recovery_owner",
    "evidence_locator",
    "boundary_status",
    "does_not_prove",
}
_REQUIRED_ENDPOINT_FIELDS = {"mechanism_id", "plane"}
_PLANES = {"control", "execution", "evidence", "recovery", "external", "terminal"}
_STATUSES = {"candidate_static", "unknown"}
_PUBLIC_ARTIFACT_ID = "ARCH-ATLAS-OPEN-ALPHA-V1"
_PUBLIC_PATHS = {
    diagram_id: f"oss/flowness-oss-harness/assets/architecture-atlas/open-alpha-v1/{diagram_id}.mmd"
    for diagram_id in ("D1", "D2", "D5")
}
_PUBLIC_D9 = "oss/flowness-oss-harness/assets/architecture-atlas/open-alpha-v1/D9.mmd"
_PUBLIC_REWORK_LEDGER = "oss/flowness-oss-harness/docs/rework-ledger.md"
_TARGET_STYLE_EDGES = {
    "D1": (("Target", "accepted"), ("PublishBoundary", "Published")),
    "D2": (("Target", "Closed"),),
    "D5": (("Target", "C"),),
    "D9": (("Alpha", "Beta"), ("Beta", "Public")),
}


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(code) from exc
    if not isinstance(payload, dict):
        raise ValidationError(code)
    return payload


def _seed_ids(seed_path: Path) -> set[str]:
    seed = _load_json(seed_path, "ARCH-EDGE-SEED-INVALID")
    mechanisms = seed.get("mechanisms")
    if seed.get("schema_version") != "mechanism-registry-seed/v0" or not isinstance(mechanisms, list):
        raise ValidationError("ARCH-EDGE-SEED-INVALID")
    ids = [row.get("mechanism_id") for row in mechanisms if isinstance(row, dict)]
    if len(ids) != len(mechanisms) or any(not isinstance(value, str) or not value for value in ids):
        raise ValidationError("ARCH-EDGE-SEED-INVALID")
    return set(ids)


def _diagram_region(document: str, diagram_id: str, *, is_mermaid_source: bool = False) -> str:
    if is_mermaid_source:
        return document
    heading = re.compile(rf"^## {re.escape(diagram_id)}(?:\s|—)", re.MULTILINE)
    match = heading.search(document)
    if not match:
        raise ValidationError(f"ARCH-EDGE-DIAGRAM-MISSING:{diagram_id}")
    next_heading = re.compile(r"^## ", re.MULTILINE).search(document, match.end())
    return document[match.start() : next_heading.start() if next_heading else len(document)]


def _declared_ids(region: str, diagram_id: str) -> set[str]:
    marker = re.search(
        rf"(?:<!--\s*ARCH-EDGE-IDS:{re.escape(diagram_id)}:\s*([^>]+?)\s*-->|%%\s*ARCH-EDGE-IDS:{re.escape(diagram_id)}:\s*(.+))",
        region,
    )
    if not marker:
        raise ValidationError(f"ARCH-EDGE-DOCUMENT-MARKER-MISSING:{diagram_id}")
    marker_value = marker.group(1) if marker.group(1) is not None else marker.group(2)
    ids = [item.strip() for item in marker_value.split(",")]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValidationError(f"ARCH-EDGE-DOCUMENT-MARKER-INVALID:{diagram_id}")
    return set(ids)


def _mermaid_node_ids(region: str) -> set[str]:
    """Extract stable flowchart IDs without needing a Mermaid renderer.

    This is intentionally not a syntax/visual-rendering assertion.  The
    release renderer remains a separate Unknown.  It is only the provenance
    join for authored D1/D2/D5 graph source.
    """

    return set(re.findall(r"\b([A-Za-z][A-Za-z0-9_-]*)\s*(?:\[|\{)", region))


def _semantic_mermaid_edges(region: str) -> dict[str, tuple[str, str, str]]:
    """Extract explicitly labelled semantic arrows from the source.

    A semantic arrow is required to carry its registry `EDGE-*` ID in the
    Mermaid label.  Dashed target/evidence boundary arrows are deliberately
    not semantic mechanism arrows and remain bounded by their adjacent labels.
    """

    pattern = re.compile(
        r"(?m)^\s*([A-Za-z][A-Za-z0-9_-]*)(?:\[[^\n]*?\])?\s*"
        r"(-->|-\.->)\s*\|([^|\n]*?(EDGE-D[125]-\d{3})[^|\n]*?)\|\s*"
        r"([A-Za-z][A-Za-z0-9_-]*)\b"
    )
    result: dict[str, tuple[str, str, str]] = {}
    for source, arrow, _label, edge_id, target in pattern.findall(region):
        if edge_id in result:
            raise ValidationError(f"ARCH-EDGE-RENDERED-EDGE-DUPLICATE:{edge_id}")
        result[edge_id] = (source, target, arrow)
    return result


def _unlabelled_solid_arrows(region: str) -> list[str]:
    """Return solid mechanism-like arrows that would otherwise evade a card."""

    rows: list[str] = []
    for line in region.splitlines():
        stripped = line.strip()
        if "-->" in stripped and "EDGE-D" not in stripped:
            rows.append(stripped)
    return rows


def _edge_arrow(region: str, source_id: str, target_id: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(source_id)}\b(?:\[[^\n]*?\])?[ \t]*"
        rf"(-->|-\.->)(?:\|[^|\n]*\|)?[ \t]*{re.escape(target_id)}\b",
        region,
    )
    return match.group(1) if match else None


def _public_target_style_documents(root: Path, documents: dict[str, str]) -> dict[str, str]:
    d9 = (root / _PUBLIC_D9).resolve()
    if root.resolve() not in d9.parents or not d9.is_file() or d9.is_symlink():
        raise ValidationError("ARCH-EDGE-PUBLIC-TARGET-SOURCE-MISSING:D9")
    return {**documents, "D9": d9.read_text(encoding="utf-8")}


def _verify_public_target_styles(documents: dict[str, str]) -> None:
    for diagram_id, pairs in _TARGET_STYLE_EDGES.items():
        for source_id, target_id in pairs:
            if _edge_arrow(documents[diagram_id], source_id, target_id) != "-.->":
                raise ValidationError(
                    f"ARCH-EDGE-TARGET-STYLE-MISMATCH:{diagram_id}:{source_id}:{target_id}"
                )


def _verify_public_narrative_edge_count(root: Path, expected_count: int) -> None:
    narrative = (root / _PUBLIC_REWORK_LEDGER).resolve()
    if root.resolve() not in narrative.parents or not narrative.is_file() or narrative.is_symlink():
        raise ValidationError("ARCH-EDGE-PUBLIC-NARRATIVE-MISSING")
    matches = re.findall(
        r"architecture-cross-layer-edges-local-v0\.json` now binds all (\d+)\s+semantic D1/D2/D5 arrows",
        narrative.read_text(encoding="utf-8"),
    )
    if matches != [str(expected_count)]:
        raise ValidationError("ARCH-EDGE-PUBLIC-NARRATIVE-COUNT-MISMATCH")


def _validate_endpoint(endpoint: Any, known_mechanisms: set[str], unknown_ids: set[str], edge_id: str) -> None:
    if not isinstance(endpoint, dict) or set(endpoint) != _REQUIRED_ENDPOINT_FIELDS:
        raise ValidationError(f"ARCH-EDGE-ENDPOINT-INVALID:{edge_id}")
    mechanism_id = endpoint["mechanism_id"]
    plane = endpoint["plane"]
    if not isinstance(mechanism_id, str) or not mechanism_id:
        raise ValidationError(f"ARCH-EDGE-ENDPOINT-INVALID:{edge_id}")
    if not isinstance(plane, str) or plane not in _PLANES:
        raise ValidationError(f"ARCH-EDGE-PLANE-INVALID:{edge_id}")
    if mechanism_id not in known_mechanisms and mechanism_id not in unknown_ids:
        raise ValidationError(f"ARCH-EDGE-MECHANISM-UNKNOWN:{edge_id}:{mechanism_id}")


def verify_architecture_edge_registry(
    registry: dict[str, Any], root: Path, seed_path: Path
) -> dict[str, Any]:
    """Verify that Atlas markers and semantic edge contracts agree exactly."""

    required = {"schema_version", "boundary", "artifacts", "unknowns", "edges"}
    if not isinstance(registry, dict) or set(registry) != required:
        raise ValidationError("ARCH-EDGE-REGISTRY-INVALID")
    if registry.get("schema_version") != SCHEMA_VERSION or registry.get("boundary") != BOUNDARY:
        raise ValidationError("ARCH-EDGE-REGISTRY-INVALID")
    known_mechanisms = _seed_ids(seed_path)
    artifacts = registry["artifacts"]
    unknowns = registry["unknowns"]
    edges = registry["edges"]
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(unknowns, list) or not isinstance(edges, list) or not edges:
        raise ValidationError("ARCH-EDGE-REGISTRY-INVALID")

    unknown_ids: set[str] = set()
    for row in unknowns:
        if not isinstance(row, dict) or set(row) != {"unknown_id", "question", "next_check", "blocking"}:
            raise ValidationError("ARCH-EDGE-UNKNOWN-INVALID")
        unknown_id = row["unknown_id"]
        if (
            not isinstance(unknown_id, str)
            or not unknown_id.startswith("UNKNOWN-ARCH-EDGE-")
            or unknown_id in unknown_ids
            or not isinstance(row["question"], str)
            or not row["question"].strip()
            or not isinstance(row["next_check"], str)
            or not row["next_check"].strip()
            or row["blocking"] is not True
        ):
            raise ValidationError("ARCH-EDGE-UNKNOWN-INVALID")
        unknown_ids.add(unknown_id)

    artifact_by_id: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"artifact_id", "paths", "diagram_ids"}:
            raise ValidationError("ARCH-EDGE-ARTIFACT-INVALID")
        artifact_id = artifact["artifact_id"]
        paths = artifact["paths"]
        diagram_ids = artifact["diagram_ids"]
        if (
            not isinstance(artifact_id, str)
            or artifact_id != _PUBLIC_ARTIFACT_ID
            or artifact_id in artifact_by_id
            or not isinstance(diagram_ids, list)
            or not diagram_ids
            or any(value not in {"D1", "D2", "D5"} for value in diagram_ids)
            or len(diagram_ids) != len(set(diagram_ids))
            or not isinstance(paths, dict)
            or paths != _PUBLIC_PATHS
            or any(not isinstance(path, str) or path.startswith("/") or ".." in Path(path).parts for path in paths.values())
        ):
            raise ValidationError("ARCH-EDGE-ARTIFACT-INVALID")
        documents: dict[str, str] = {}
        for diagram_id, path in paths.items():
            target = (root / path).resolve()
            if root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
                raise ValidationError(f"ARCH-EDGE-ARTIFACT-MISSING:{artifact_id}:{diagram_id}")
            documents[diagram_id] = target.read_text(encoding="utf-8")
        artifact_by_id[artifact_id] = (artifact, documents)

    public_documents = _public_target_style_documents(
        root, artifact_by_id[_PUBLIC_ARTIFACT_ID][1]
    )
    _verify_public_target_styles(public_documents)

    seen_edge_ids: set[str] = set()
    declared_by_diagram: dict[tuple[str, str], set[str]] = {}
    registered_by_diagram: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != _REQUIRED_EDGE_FIELDS:
            raise ValidationError("ARCH-EDGE-RECORD-INVALID")
        edge_id = edge["edge_id"]
        artifact_id = edge["artifact_id"]
        diagram_id = edge["diagram_id"]
        if (
            not isinstance(edge_id, str)
            or not edge_id
            or edge_id in seen_edge_ids
            or artifact_id not in artifact_by_id
            or diagram_id not in artifact_by_id[artifact_id][0]["diagram_ids"]
            or not isinstance(edge["rendered_source_id"], str)
            or not isinstance(edge["rendered_target_id"], str)
            or not edge["rendered_source_id"]
            or not edge["rendered_target_id"]
            or edge["boundary_status"] not in _STATUSES
        ):
            raise ValidationError(f"ARCH-EDGE-RECORD-INVALID:{edge_id}")
        seen_edge_ids.add(edge_id)
        for field in _REQUIRED_EDGE_FIELDS - {"edge_id", "artifact_id", "diagram_id", "rendered_source_id", "rendered_target_id", "source", "target", "boundary_status"}:
            if not isinstance(edge[field], str) or not edge[field].strip():
                raise ValidationError(f"ARCH-EDGE-FIELD-INVALID:{edge_id}:{field}")
        _validate_endpoint(edge["source"], known_mechanisms, unknown_ids, edge_id)
        _validate_endpoint(edge["target"], known_mechanisms, unknown_ids, edge_id)
        endpoint_ids = {edge["source"]["mechanism_id"], edge["target"]["mechanism_id"]}
        unknown_endpoint_ids = endpoint_ids & unknown_ids
        if edge["boundary_status"] == "unknown" and not unknown_endpoint_ids:
            raise ValidationError(f"ARCH-EDGE-UNKNOWN-BOUNDARY-MISSING:{edge_id}")
        if edge["boundary_status"] == "candidate_static" and unknown_endpoint_ids:
            raise ValidationError(f"ARCH-EDGE-UNKNOWN-HIDDEN:{edge_id}")
        artifact, documents = artifact_by_id[artifact_id]
        document = documents[diagram_id]
        is_mermaid_source = Path(artifact["paths"][diagram_id]).suffix == ".mmd"
        region = _diagram_region(document, diagram_id, is_mermaid_source=is_mermaid_source)
        key = (artifact_id, diagram_id)
        declared_by_diagram.setdefault(key, _declared_ids(region, diagram_id))
        registered_by_diagram.setdefault(key, set()).add(edge_id)
        semantic_edges = _semantic_mermaid_edges(region)
        rendered_edge = semantic_edges.get(edge_id)
        if rendered_edge is None or rendered_edge[:2] != (
            edge["rendered_source_id"], edge["rendered_target_id"]
        ):
            raise ValidationError(f"ARCH-EDGE-RENDERED-ENDPOINT-MISMATCH:{edge_id}")
        if edge["boundary_status"] == "unknown" and rendered_edge[2] != "-.->":
            raise ValidationError(f"ARCH-EDGE-UNKNOWN-STYLE-MISMATCH:{edge_id}")
        nodes = _mermaid_node_ids(region)
        if edge["rendered_source_id"] not in nodes or edge["rendered_target_id"] not in nodes:
            raise ValidationError(f"ARCH-EDGE-RENDERED-NODE-MISSING:{edge_id}")

    for artifact_id, (artifact, documents) in artifact_by_id.items():
        for diagram_id in artifact["diagram_ids"]:
            key = (artifact_id, diagram_id)
            if key not in registered_by_diagram:
                raise ValidationError(f"ARCH-EDGE-DIAGRAM-UNREGISTERED:{artifact_id}:{diagram_id}")
            document = documents[diagram_id]
            region = _diagram_region(document, diagram_id, is_mermaid_source=Path(artifact["paths"][diagram_id]).suffix == ".mmd")
            declared = declared_by_diagram.get(key) or _declared_ids(region, diagram_id)
            if declared != registered_by_diagram[key]:
                raise ValidationError(f"ARCH-EDGE-DOCUMENT-REGISTRY-MISMATCH:{artifact_id}:{diagram_id}")
            semantic_edges = _semantic_mermaid_edges(region)
            if set(semantic_edges) != declared:
                raise ValidationError(f"ARCH-EDGE-RENDERED-EDGE-SET-MISMATCH:{artifact_id}:{diagram_id}")
            if _unlabelled_solid_arrows(region):
                raise ValidationError(f"ARCH-EDGE-UNBOUND-RENDERED-ARROW:{artifact_id}:{diagram_id}")
    _verify_public_narrative_edge_count(root, len(seen_edge_ids))
    return {
        "schema_version": "architecture-edge-verification/v1",
        "boundary": BOUNDARY,
        "ceiling": "local_static_edge_contract_only",
        "verified_edge_ids": sorted(seen_edge_ids),
        "unknown_edge_endpoint_ids": sorted(
            {
                endpoint["mechanism_id"]
                for edge in edges
                for endpoint in (edge["source"], edge["target"])
                if endpoint["mechanism_id"] in unknown_ids
            }
        ),
    }
