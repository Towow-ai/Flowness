"""Validate and render the non-publishing Open Alpha discovery pack.

The pack deliberately separates local Flowness claims from an external source
queue.  Search seeds are useful discovery inputs, but they remain ``unknown``
until source, release, tests, and a runnable path have been inspected.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .content_graph import validate_graph, verify_file_backed_assets
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .schema_validation import validate_payload


SCHEMA_VERSION = "flowness-open-alpha-discovery-launch-pack/v1"
FORBIDDEN_HYPE = (
    "production-ready",
    "battle-tested",
    "industry-leading",
    "proven at scale",
    "best harness",
    "领先所有",
    "生产就绪",
)
MATURITY_STATES = {
    "current_verified",
    "experimental",
    "designed_target",
    "unknown",
}

LAUNCH_SNAPSHOT = "open-alpha-launch-successor-v1"
SELECTOR_DOCUMENT = "docs/open-alpha-selector-packet-v0.md"
LAUNCH_DOCUMENT = "docs/open-alpha-discovery-launch-pack-v0.md"
README_DOCUMENT = "README.md"
MECHANISM_CARD_REGISTRY = "registries/mechanism-cards-v0.json"
SEMANTIC_SCOUT = "docs/semantic-chain-evidence-scout-v0.md"
READYSET_MECHANISM_ID = "M-ORCH-READYSET-EVENT-FANOUT"


_MECHANISM_REGISTRY_HASH_DECLARATION = re.compile(
    r"Mechanism Registry\s+`registry_hash`\s+`?(sha256:[0-9a-f]{64})`?",
    re.IGNORECASE,
)


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_local_file(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValidationError(f"DISCOVERY-PACK-LOCAL-PATH-INVALID:{relative}")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError(f"DISCOVERY-PACK-LOCAL-PATH-ESCAPES:{relative}") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ValidationError(f"DISCOVERY-PACK-LOCAL-LINK-MISSING:{relative}")
    return candidate


def _readyset_card(registry: dict[str, Any]) -> dict[str, Any]:
    cards = registry.get("cards") if isinstance(registry, dict) else None
    matches = [
        card
        for card in cards or []
        if isinstance(card, dict) and card.get("mechanism_id") == READYSET_MECHANISM_ID
    ]
    if len(matches) != 1:
        raise ValidationError("MECHANISM-SCOUT-CANONICAL-CARD-MISSING")
    return matches[0]


def _scout_coordinate(card: dict[str, Any], role: str, ordinal: int) -> dict[str, Any]:
    try:
        coordinate = card["static_coordinates"][role][ordinal - 1]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValidationError("MECHANISM-SCOUT-CANONICAL-COORDINATE-MISSING") from exc
    if set(coordinate) != {"path", "start_line", "end_line", "excerpt_sha256"}:
        raise ValidationError("MECHANISM-SCOUT-CANONICAL-COORDINATE-INVALID")
    return coordinate


def _scout_locator(coordinate: dict[str, Any]) -> str:
    return (
        f"`{coordinate['path']}:{coordinate['start_line']}-{coordinate['end_line']}` "
        f"(`{coordinate['excerpt_sha256']}`)"
    )


def render_readyset_scout_row(registry: dict[str, Any]) -> str:
    """Render the public scout row from the canonical hash-bound card."""

    card = _readyset_card(registry)
    caller = _scout_coordinate(card, "caller", 1)
    definition = _scout_coordinate(card, "definition", 1)
    collector = _scout_coordinate(card, "consumer", 1)
    consumer = _scout_coordinate(card, "consumer", 2)
    failure = _scout_coordinate(card, "failure", 1)
    recovery = _scout_coordinate(card, "recovery", 1)
    dispatch = _scout_coordinate(card, "recovery", 2)
    test = _scout_coordinate(card, "test", 1)
    return (
        f"| `{READYSET_MECHANISM_ID}` | `OrchestratorDaemon` dispatch calls "
        f"`self._ready_execution_decisions(...)` within the hash-bound caller excerpt "
        f"{_scout_locator(caller)}; the definition is "
        f"{_scout_locator(definition)}. The ready-decision collector is {_scout_locator(collector)} "
        f"and polling batch consumer is {_scout_locator(consumer)}. Failure/no-stamp handling is "
        f"{_scout_locator(failure)}; recovery/backlog handling, including backlog recomputation, is "
        f"{_scout_locator(recovery)}, and the downstream dispatcher is {_scout_locator(dispatch)}. | "
        f"`static+test`: {_scout_locator(test)} anchors "
        "cached round-event behavior. | A source-level method and recovery chain is real, but it does "
        "not prove the polling loop is enabled or every task type reaches the branch. No live event -> "
        "ready-set -> spawned-session trace, deployed authority, or runtime causal chain is proved. |"
    )


def verify_readyset_scout_row(registry: dict[str, Any], root: Path | str) -> None:
    """Fail closed when the public scout row drifts from canonical coordinates."""

    base = Path(root).resolve(strict=True)
    text = _safe_local_file(base, SEMANTIC_SCOUT).read_text(encoding="utf-8")
    prefix = f"| `{READYSET_MECHANISM_ID}` |"
    rows = [line for line in text.splitlines() if line.startswith(prefix)]
    if rows != [render_readyset_scout_row(registry)]:
        raise ValidationError("MECHANISM-SCOUT-COORDINATE-DRIFT")


def _all_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _all_text(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_text(item)]
    return []


def build_launch_content_graph(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    """Build the exact v2 graph expected for this launch candidate.

    The stored graph is intentionally derived from claim bytes, file hashes,
    mechanism entries and the Drift Atlas.  Validation compares it byte-for-
    byte with this result, so an edited evidence file or launch asset cannot
    remain silently bound to the prior candidate.
    """

    binding = payload["content_graph_binding"]
    snapshot_id = binding["snapshot_id"]
    mechanism_path = _safe_local_file(root, binding["mechanism_registry_path"])
    drift_path = _safe_local_file(root, binding["drift_atlas_path"])
    mechanism_registry = json.loads(mechanism_path.read_text(encoding="utf-8"))
    mechanism_index = {
        item["mechanism_id"]: item for item in mechanism_registry["mechanisms"]
    }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    used_mechanisms = sorted(
        {mechanism_id for claim in payload["claims"] for mechanism_id in claim["mechanism_ids"]}
    )
    for mechanism_id in used_mechanisms:
        if mechanism_id not in mechanism_index:
            raise ValidationError(f"DISCOVERY-PACK-MECHANISM-MISSING:{mechanism_id}")
        nodes.append(
            {
                "node_id": f"mechanism.launch.{mechanism_id}",
                "type": "mechanism",
                "state": "evidence_bound",
                "snapshot_id": snapshot_id,
                "content_hash": canonical_hash(mechanism_index[mechanism_id]),
            }
        )

    drift_ids = sorted({drift_id for claim in payload["claims"] for drift_id in claim["drift_ids"]})
    drift_text = drift_path.read_text(encoding="utf-8")
    for drift_id in drift_ids:
        if drift_id not in drift_text:
            raise ValidationError(f"DISCOVERY-PACK-DRIFT-MISSING:{drift_id}")
    if drift_ids:
        nodes.append(
            {
                "node_id": "evidence.launch.drift-atlas",
                "type": "evidence",
                "state": "evidence_bound",
                "snapshot_id": snapshot_id,
                "content_hash": _file_hash(drift_path),
            }
        )

    claim_node_ids = binding["claim_node_ids"]
    for claim in payload["claims"]:
        claim_id = claim["claim_id"]
        node_id = claim_node_ids.get(claim_id)
        if not node_id:
            raise ValidationError(f"DISCOVERY-PACK-CONTENT-GRAPH-CLAIM-MISSING:{claim_id}")
        if claim["evidence_bindings"]:
            evidence_node = f"evidence.launch.{claim_id.lower()}"
            nodes.append(
                {
                    "node_id": evidence_node,
                    "type": "evidence",
                    "state": "evidence_bound",
                    "snapshot_id": snapshot_id,
                    "content_hash": canonical_hash(claim["evidence_bindings"]),
                }
            )
            edges.append({"from_id": evidence_node, "to_id": node_id, "type": "supports"})
        nodes.append(
            {
                "node_id": node_id,
                "type": "claim",
                "state": "draft" if claim["state"] == "unknown" else "evidence_bound",
                "snapshot_id": snapshot_id,
                "content_hash": canonical_hash(claim),
            }
        )
        for mechanism_id in claim["mechanism_ids"]:
            edges.append(
                {
                    "from_id": f"mechanism.launch.{mechanism_id}",
                    "to_id": node_id,
                    "type": "implements",
                }
            )
        if claim["drift_ids"]:
            edges.append(
                {
                    "from_id": "evidence.launch.drift-atlas",
                    "to_id": node_id,
                    "type": "limits",
                }
            )

    asset_specs = {
        "asset.launch.discovery-pack": (LAUNCH_DOCUMENT, [claim["claim_id"] for claim in payload["claims"]]),
        "asset.launch.selector-packet": (SELECTOR_DOCUMENT, payload["selector_one_pager"]["claim_ids"]),
        "asset.launch.readme": (
            README_DOCUMENT,
            [claim["claim_id"] for claim in payload["claims"] if not claim["external_subject"]],
        ),
    }
    if sorted(asset_specs) != sorted(binding["asset_node_ids"]):
        raise ValidationError("DISCOVERY-PACK-CONTENT-GRAPH-ASSET-SET-DRIFT")
    for asset_id, (relative, claim_ids) in asset_specs.items():
        path = _safe_local_file(root, relative)
        nodes.append(
            {
                "node_id": asset_id,
                "type": "asset",
                "state": "draft",
                "snapshot_id": snapshot_id,
                "content_hash": _file_hash(path),
                "source_path": relative,
            }
        )
        for claim_id in claim_ids:
            edges.append(
                {
                    "from_id": asset_id,
                    "to_id": claim_node_ids[claim_id],
                    "type": "derived_from",
                }
            )

    channel_ids: list[str] = []
    for index, draft in enumerate(payload["channel_drafts"], 1):
        node_id = f"channel.launch.draft-{index}"
        channel_ids.append(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "type": "channel_package",
                "state": "draft",
                "snapshot_id": snapshot_id,
                "content_hash": canonical_hash(draft),
            }
        )
        for claim_id in draft["claim_ids"]:
            edges.append(
                {
                    "from_id": node_id,
                    "to_id": claim_node_ids[claim_id],
                    "type": "derived_from",
                }
            )
    if channel_ids != binding["channel_node_ids"]:
        raise ValidationError("DISCOVERY-PACK-CONTENT-GRAPH-CHANNEL-SET-DRIFT")

    graph = {
        "schema_version": "content-graph/v2",
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(edges, key=lambda item: (item["from_id"], item["to_id"], item["type"])),
    }
    validate_graph(graph)
    return graph


def validate_discovery_launch_pack(
    payload: dict[str, Any], root: Path, schema_path: Path, *, verify_graph: bool = True
) -> dict[str, Any]:
    """Fail closed on claim promotion, byte drift, and seed laundering."""

    validate_payload(payload, schema_path, "Open Alpha discovery launch pack")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("DISCOVERY-PACK-SCHEMA-VERSION-INVALID")
    if (
        payload["status"] != "open_alpha_release_material"
        or payload["external_action"] != "external_release_record"
    ):
        raise ValidationError("DISCOVERY-PACK-RELEASE-TRUTH-SOURCE-INVALID")

    binding = payload["content_graph_binding"]
    if binding["snapshot_id"] != LAUNCH_SNAPSHOT:
        raise ValidationError("DISCOVERY-PACK-CONTENT-GRAPH-SNAPSHOT-INVALID")
    scope_identity = payload["selector_one_pager"]["identity"]
    scope_path = _safe_local_file(root, scope_identity["scope_policy_path"])
    if _file_hash(scope_path) != scope_identity["scope_policy_sha256"]:
        raise ValidationError("DISCOVERY-PACK-SCOPE-POLICY-HASH-MISMATCH")
    if (
        scope_identity["source_commit"] != "EXTERNAL_RELEASE_RECORD"
        or scope_identity["sealed_export_manifest_hash"] != "EXTERNAL_RELEASE_RECORD"
    ):
        raise ValidationError("DISCOVERY-PACK-IDENTITY-MUST-REMAIN-EXTERNAL")

    mechanism_path = _safe_local_file(root, binding["mechanism_registry_path"])
    mechanism_registry = json.loads(mechanism_path.read_text(encoding="utf-8"))
    mechanism_index = {
        item["mechanism_id"]: item for item in mechanism_registry["mechanisms"]
    }
    mechanism_card_path = _safe_local_file(root, MECHANISM_CARD_REGISTRY)
    mechanism_card_registry = json.loads(
        mechanism_card_path.read_text(encoding="utf-8")
    )
    try:
        verify_self_hash(mechanism_card_registry, "registry_hash")
    except ValidationError as exc:
        raise ValidationError(
            "DISCOVERY-PACK-MECHANISM-REGISTRY-SELF-HASH-INVALID"
        ) from exc
    actual_mechanism_registry_hash = mechanism_card_registry["registry_hash"]
    declared_mechanism_registry_hashes = set(
        _MECHANISM_REGISTRY_HASH_DECLARATION.findall("\n".join(_all_text(payload)))
    )
    if declared_mechanism_registry_hashes != {
        actual_mechanism_registry_hash
    }:
        raise ValidationError(
            "DISCOVERY-PACK-MECHANISM-REGISTRY-DECLARED-HASH-MISMATCH"
        )
    drift_text = _safe_local_file(root, binding["drift_atlas_path"]).read_text(encoding="utf-8")

    claim_ids: set[str] = set()
    for claim in payload["claims"]:
        claim_id = claim["claim_id"]
        if claim_id in claim_ids:
            raise ValidationError(f"DISCOVERY-PACK-CLAIM-DUPLICATE:{claim_id}")
        claim_ids.add(claim_id)
        if claim["state"] not in MATURITY_STATES:
            raise ValidationError(f"DISCOVERY-PACK-CLAIM-STATE-INVALID:{claim_id}")
        if claim["external_subject"]:
            if (
                claim["state"] != "unknown"
                or claim["evidence_bindings"]
                or claim["mechanism_ids"]
                or claim["drift_ids"]
            ):
                raise ValidationError(f"DISCOVERY-PACK-EXTERNAL-CLAIM-PROMOTED:{claim_id}")
        else:
            if not claim["evidence_bindings"] or not claim["mechanism_ids"]:
                raise ValidationError(f"DISCOVERY-PACK-INTERNAL-CLAIM-UNBOUND:{claim_id}")
            for evidence in claim["evidence_bindings"]:
                evidence_path = _safe_local_file(root, evidence["path"])
                if _file_hash(evidence_path) != evidence["sha256"]:
                    raise ValidationError(
                        f"DISCOVERY-PACK-EVIDENCE-HASH-MISMATCH:{claim_id}:{evidence['path']}"
                    )
            for mechanism_id in claim["mechanism_ids"]:
                mechanism = mechanism_index.get(mechanism_id)
                if mechanism is None:
                    raise ValidationError(f"DISCOVERY-PACK-MECHANISM-MISSING:{mechanism_id}")
                if claim["state"] == "current_verified" and mechanism.get("proposed_status") != "current_verified":
                    raise ValidationError(
                        f"DISCOVERY-PACK-CLAIM-OUTRUNS-MECHANISM:{claim_id}:{mechanism_id}"
                    )
            for drift_id in claim["drift_ids"]:
                if drift_id not in drift_text:
                    raise ValidationError(f"DISCOVERY-PACK-DRIFT-MISSING:{drift_id}")

    source_ids: set[str] = set()
    source_urls: set[str] = set()
    for source in payload["source_queue"]:
        source_id = source["source_queue_id"]
        if source_id in source_ids:
            raise ValidationError(f"DISCOVERY-PACK-SOURCE-DUPLICATE:{source_id}")
        source_ids.add(source_id)
        if source["verification_state"] != "unknown" or source["claims_allowed"]:
            raise ValidationError(f"DISCOVERY-PACK-SOURCE-SEED-LAUNDERED:{source_id}")
        if source["url"] is not None:
            parsed = urlparse(source["url"])
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValidationError(f"DISCOVERY-PACK-SOURCE-URL-INVALID:{source_id}")
            source_urls.add(source["url"])

    dimensions = {item["dimension_id"] for item in payload["comparison_matrix"]["dimensions"]}
    if len(dimensions) != len(payload["comparison_matrix"]["dimensions"]):
        raise ValidationError("DISCOVERY-PACK-COMPARISON-DIMENSION-DUPLICATE")
    for row in payload["comparison_matrix"]["rows"]:
        cell_dimensions = {cell["dimension_id"] for cell in row["cells"]}
        if cell_dimensions != dimensions or len(cell_dimensions) != len(row["cells"]):
            raise ValidationError(f"DISCOVERY-PACK-COMPARISON-COVERAGE:{row['project_name']}")
        if set(row["claims_allowed"]) - claim_ids:
            raise ValidationError(f"DISCOVERY-PACK-COMPARISON-CLAIM-MISSING:{row['project_name']}")
        if row["row_type"] == "external_seed":
            if row["source_queue_id"] not in source_ids:
                raise ValidationError(f"DISCOVERY-PACK-COMPARISON-SOURCE-MISSING:{row['project_name']}")
            if row["verification_state"] != "unknown" or row["claims_allowed"]:
                raise ValidationError(f"DISCOVERY-PACK-COMPARISON-PROMOTED:{row['project_name']}")
            if any(cell["state"] != "unknown" or cell["evidence"] for cell in row["cells"]):
                raise ValidationError(f"DISCOVERY-PACK-COMPARISON-CELL-PROMOTED:{row['project_name']}")
        else:
            if row["source_queue_id"] is not None or row["verification_state"] != "local_evidence_available":
                raise ValidationError(f"DISCOVERY-PACK-COMPARISON-LOCAL-ROW-INVALID:{row['project_name']}")
            for cell in row["cells"]:
                if not cell["evidence"]:
                    raise ValidationError(f"DISCOVERY-PACK-COMPARISON-LOCAL-CELL-UNBOUND:{row['project_name']}:{cell['dimension_id']}")
                for relative in cell["evidence"]:
                    _safe_local_file(root, relative)

    for relative in payload["link_policy"]["internal_paths"]:
        _safe_local_file(root, relative)
    declared_external = set(payload["link_policy"]["external_urls_from_queue_only"])
    if declared_external != source_urls:
        raise ValidationError("DISCOVERY-PACK-EXTERNAL-LINK-SET-DRIFT")
    payload_urls = {
        match.rstrip(".,);]")
        for value in _all_text(payload)
        for match in re.findall(r"https://[^\s<>\"]+", value)
    }
    if payload_urls != source_urls:
        raise ValidationError("DISCOVERY-PACK-UNREGISTERED-EXTERNAL-LINK")

    for section in (
        payload["introductions"],
        [payload["selector_one_pager"]],
        payload["release_draft"]["body_sections"],
        payload["channel_drafts"],
    ):
        for item in section:
            unknown_claim_ids = set(item.get("claim_ids", ())) - claim_ids
            if unknown_claim_ids:
                raise ValidationError(
                    "DISCOVERY-PACK-CLAIM-REFERENCE-MISSING:"
                    + ",".join(sorted(unknown_claim_ids))
                )
            externally_unknown = {
                claim["claim_id"]
                for claim in payload["claims"]
                if claim["external_subject"] and claim["state"] == "unknown"
            }
            referenced_unknown = set(item.get("claim_ids", ())) & externally_unknown
            if referenced_unknown:
                raise ValidationError(
                    "DISCOVERY-PACK-OUTWARD-ASSET-REFERENCES-UNKNOWN-EXTERNAL-CLAIM:"
                    + ",".join(sorted(referenced_unknown))
                )

    joined = "\n".join(_all_text(payload)).lower()
    for phrase in FORBIDDEN_HYPE:
        if phrase in joined:
            raise ValidationError(f"DISCOVERY-PACK-FORBIDDEN-HYPE:{phrase}")
    if re.search(r"\b\d[\d,.]*\s*(?:github\s+)?stars?\b", joined, re.IGNORECASE):
        raise ValidationError("DISCOVERY-PACK-NUMERIC-STAR-VALIDATION-FORBIDDEN")
    if "old wow-harness stars do not validate flowness" not in joined:
        raise ValidationError("DISCOVERY-PACK-WOW-BOUNDARY-MISSING")
    if "deepseek is currently selecting" in joined:
        raise ValidationError("DISCOVERY-PACK-DEEPSEEK-ASSERTION-FORBIDDEN")
    if "user-supplied live-search seed" in joined:
        raise ValidationError("DISCOVERY-PACK-SEARCH-SEED-PROVENANCE-INVALID")
    if verify_graph:
        graph_path = _safe_local_file(root, binding["graph_path"])
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        validate_graph(graph)
        verify_file_backed_assets(graph, root)
        expected_graph = build_launch_content_graph(payload, root)
        if graph != expected_graph:
            raise ValidationError("DISCOVERY-PACK-CONTENT-GRAPH-DRIFT")
    return payload


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_discovery_launch_pack(payload: dict[str, Any]) -> str:
    lines = [
        "# Flowness Open Alpha — Discovery & Launch Pack",
        "",
        "> Status: **Open Alpha pre-release material; independent clean-room, fresh jury, and exact release records are still required.**",
        "",
        payload["scope"],
        "",
        "**Start here:** [project story](../README.md) · [10-minute Harness demo](open-alpha-demo.md) · [D0–D9 Architecture Atlas](architecture-atlas.md) · [Mechanism Registry](../registries/mechanism-registry-seed-v0.json) · [Open Alpha scope](open-alpha-package-scope-v0.md)",
        "",
        "## GitHub metadata",
        "",
        f"**Description:** {payload['github']['description']}",
        "",
        "**Topics:** " + ", ".join(f"`{topic}`" for topic in payload["github"]["topics"]),
        "",
        "### Social preview copy",
        "",
        f"- Headline: {payload['github']['social_preview_text']['headline']}",
        f"- Subhead: {payload['github']['social_preview_text']['subhead']}",
        f"- Footer: {payload['github']['social_preview_text']['footer']}",
        "",
        "## Layered introductions",
        "",
    ]
    for intro in payload["introductions"]:
        lines += [f"### {intro['duration']} — {intro['title']}", "", intro["copy"], "", f"**CTA:** {intro['cta']}", ""]

    selector = payload["selector_one_pager"]
    lines += [f"## {selector['title']}", "", selector["problem"], "", selector["offer"], "", "### What to inspect", ""]
    lines += [f"- {item}" for item in selector["what_to_inspect"]]
    lines += ["", "### Maturity at a glance", ""]
    lines += [f"- **{item['layer']} — `{item['state']}`:** {item['boundary']}" for item in selector["maturity"]]
    lines += ["", "### Decision questions", ""]
    lines += [f"- {item}" for item in selector["decision_questions"]]
    lines += ["", "### Boundaries", ""]
    lines += [f"- {item}" for item in selector["boundaries"]]
    lines += ["", f"Standalone selector packet: [{SELECTOR_DOCUMENT.split('/')[-1]}]({SELECTOR_DOCUMENT.split('/')[-1]})"]

    matrix = payload["comparison_matrix"]
    dimensions = matrix["dimensions"]
    lines += ["", "## Comparison matrix — verification queue, not a leaderboard", "", matrix["purpose"], ""]
    header = "| Project | Verification | " + " | ".join(item["label"] for item in dimensions) + " |"
    rule = "| --- | --- | " + " | ".join("---" for _ in dimensions) + " |"
    lines += [header, rule]
    for row in matrix["rows"]:
        cells = {cell["dimension_id"]: cell["value"] for cell in row["cells"]}
        lines.append(
            "| " + _table_cell(row["project_name"]) + " | `" + row["verification_state"] + "` | "
            + " | ".join(_table_cell(cells[item["dimension_id"]]) for item in dimensions) + " |"
        )

    lines += ["", "### Dimension verification method", ""]
    lines += [f"- **{item['label']}:** {item['verification_method']}" for item in dimensions]

    lines += ["", "## External source queue", "", "Every entry below is an unverified search seed collected on 2026-08-02. No source, release, test, star count, or mechanism claim has been independently verified in this offline pass.", ""]
    for source in payload["source_queue"]:
        label = f"[{source['name']}]({source['url']})" if source["url"] else source["name"]
        lines += [f"### {label}", "", f"- State: `{source['verification_state']}`", f"- Seed basis: {source['seed_basis']}", "- Next verification:"]
        lines += [f"  - {item}" for item in source["next_verification"]]
        lines.append("")

    lines += ["## Launch checklist", "", "| Gate | State | Blocking | Evidence or next action |", "| --- | --- | --- | --- |"]
    for item in payload["launch_checklist"]:
        lines.append(f"| {item['label']} | `{item['state']}` | `{str(item['blocking']).lower()}` | {_table_cell(item['evidence_or_action'])} |")

    release = payload["release_draft"]
    lines += ["", "## GitHub Release draft", "", f"**Tag candidate:** `{release['tag']}`", "", f"**Title:** {release['title']}", "", f"**Pre-release:** `{str(release['prerelease']).lower()}`", ""]
    for section in release["body_sections"]:
        lines += [f"### {section['heading']}", "", section["body"], ""]
    lines += ["### Do not publish until", ""]
    lines += [f"- {item}" for item in release["do_not_publish_until"]]

    lines += ["", "## 中文渠道短稿", ""]
    for draft in payload["channel_drafts"]:
        lines += [f"### {draft['channel']}｜{draft['title']}", "", draft["body"], "", f"**候选 CTA：** {draft['cta']}", "", f"发布状态：`{draft['publication_state']}`", ""]

    lines += ["## Claim ledger", "", "| Claim | State | Evidence | Limitation |", "| --- | --- | --- | --- |"]
    for claim in payload["claims"]:
        evidence = "<br>".join(item["path"] for item in claim["evidence_bindings"]) or "None — external hypothesis remains Unknown"
        limitations = "<br>".join(claim["limitations"])
        lines.append(f"| `{claim['claim_id']}` { _table_cell(claim['text']) } | `{claim['state']}` | {_table_cell(evidence)} | {_table_cell(limitations)} |")
    lines += ["", "## Static boundary", "", payload["truth_policy"]["wow_history_boundary"], "", payload["truth_policy"]["source_seed_rule"], ""]
    return "\n".join(lines)


def render_selector_packet(payload: dict[str, Any]) -> str:
    """Render the standalone, copyable selector packet."""

    selector = payload["selector_one_pager"]
    identity = selector["identity"]
    lines = [
        "# Flowness Open Alpha — selector packet",
        "",
        "> Open Alpha pre-release material. Independent clean-room, fresh jury, and exact release records are still required.",
        "",
        "## 30-second fit",
        "",
        selector["offer"],
        "",
        "The runnable proof today uses deterministic producer and judge fixtures. Optional Codex mode replaces only the producers with real Codex processes; judgment remains a deterministic policy probe. This proves the local orchestration and acceptance semantics, not model quality, production reliability, scale, security, or adoption.",
        "",
        "## Exact candidate identity",
        "",
        f"- Package version: `{identity['candidate_version']}`",
        f"- Tag candidate: `{identity['release_tag_candidate']}`",
        f"- Exact release commit: `{identity['source_commit']}`",
        f"- Exact sealed export manifest hash: `{identity['sealed_export_manifest_hash']}`",
        f"- Scope policy: `{identity['scope_policy_path']}`",
        f"- Scope policy SHA-256: `{identity['scope_policy_sha256']}`",
        "",
        "`EXTERNAL_RELEASE_RECORD` is a required future binding for the exact commit, tree, export, wheel, non-author clean-room result, fresh jury decision, and authorization. This mutable packet does not assert that record already exists.",
        "",
        "## Run the smallest proof",
        "",
        "From `oss/flowness-oss-harness` in a Git checkout, or from a scratch copy outside an immutable sealed export:",
        "",
        "```bash",
        *selector["quickstart_commands"],
        "```",
        "",
        f"Expected terminal summary: `{selector['expected_result']}`",
        "",
        "## Available now",
        "",
        *[f"- {item}" for item in selector["available_now"]],
        "",
        "## Not available or not proven",
        "",
        *[f"- {item}" for item in selector["not_available"]],
        "",
        "## Evidence to inspect",
        "",
        *[f"- {item}" for item in selector["what_to_inspect"]],
        "",
        "## Maturity",
        "",
        *[f"- **{item['layer']} — `{item['state']}`:** {item['boundary']}" for item in selector["maturity"]],
        "",
        "## Selection boundaries",
        "",
        *[f"- {item}" for item in selector["boundaries"]],
        "",
        "## License and contact",
        "",
        "- Code: Apache-2.0; public documentation/media: CC-BY-4.0 unless a file-level notice says otherwise.",
        "- Questions, defects, and evidence challenges: use the current repository issue tracker; report vulnerabilities through the route in the root SECURITY.md.",
        "- Start with `docs/open-alpha-demo.md`, then inspect `trace.json` and the Claim ledger.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    source = root / "registries/open-alpha-discovery-launch-pack-v0.json"
    schema = root / "schemas/open-alpha-discovery-launch-pack.schema.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if len(sys.argv) == 2 and sys.argv[1] == "--write":
        validate_discovery_launch_pack(payload, root, schema, verify_graph=False)
        (root / LAUNCH_DOCUMENT).write_text(render_discovery_launch_pack(payload), encoding="utf-8")
        (root / SELECTOR_DOCUMENT).write_text(render_selector_packet(payload), encoding="utf-8")
        graph = build_launch_content_graph(payload, root)
        (root / payload["content_graph_binding"]["graph_path"]).write_text(
            json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validate_discovery_launch_pack(payload, root, schema)
    elif len(sys.argv) == 1:
        validate_discovery_launch_pack(payload, root, schema)
        print(render_discovery_launch_pack(payload), end="")
    else:
        raise SystemExit("usage: python -m flowness_oss_harness.discovery_launch_pack [--write]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
