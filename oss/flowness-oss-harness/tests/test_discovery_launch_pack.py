from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from flowness_oss_harness.architecture_edges import verify_architecture_edge_registry
from flowness_oss_harness.discovery_launch_pack import (
    build_launch_content_graph,
    render_discovery_launch_pack,
    render_readyset_scout_row,
    render_selector_packet,
    validate_discovery_launch_pack,
    verify_readyset_scout_row,
)
from flowness_oss_harness.registry import ValidationError


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "registries/open-alpha-discovery-launch-pack-v0.json"
SCHEMA = ROOT / "schemas/open-alpha-discovery-launch-pack.schema.json"
DOCUMENT = ROOT / "docs/open-alpha-discovery-launch-pack-v0.md"
SELECTOR_DOCUMENT = ROOT / "docs/open-alpha-selector-packet-v0.md"


def _source() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _copy_public_file(export_root: Path, relative: str) -> Path:
    target = export_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / relative, target)
    return target


def test_discovery_pack_is_valid_and_document_is_deterministic() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)
    assert DOCUMENT.read_text(encoding="utf-8") == render_discovery_launch_pack(payload)
    assert SELECTOR_DOCUMENT.read_text(encoding="utf-8") == render_selector_packet(payload)
    graph_path = ROOT / payload["content_graph_binding"]["graph_path"]
    assert json.loads(graph_path.read_text(encoding="utf-8")) == build_launch_content_graph(payload, ROOT)


def test_discovery_pack_contains_every_requested_launch_surface() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)

    assert len(payload["introductions"]) == 3
    assert {item["duration"] for item in payload["introductions"]} == {
        "30 seconds",
        "3 minutes",
        "10 minutes",
    }
    assert "DeepSeek" in payload["selector_one_pager"]["title"]
    assert len(payload["comparison_matrix"]["dimensions"]) >= 7
    assert len(payload["source_queue"]) == 7
    assert payload["release_draft"]["prerelease"] is True
    assert len(payload["channel_drafts"]) >= 4
    assert payload["github"]["description"]
    assert "ai-agent-harness" in payload["github"]["topics"]
    assert payload["github"]["social_preview_text"]["headline"] == "Flowness"
    selector = payload["selector_one_pager"]
    assert selector["identity"]["candidate_version"] == "1.0.0a1"
    assert selector["identity"]["release_tag_candidate"] == "v1.0.0-alpha"
    assert selector["identity"]["source_commit"] == "db9cda3f82cea192c92f30ccca6ff9f12d5a1d31"
    assert selector["identity"]["sealed_export_manifest_hash"].startswith("sha256:")
    assert len(selector["quickstart_commands"]) >= 2
    assert selector["available_now"] and selector["not_available"]


def test_public_launch_uses_current_included_ledger_and_content_graph_evidence() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)
    ledger = next(claim for claim in payload["claims"] if claim["claim_id"] == "CLM-FLOW-LEDGER")
    ledger_paths = {item["path"] for item in ledger["evidence_bindings"]}
    architecture = next(
        claim for claim in payload["claims"] if claim["claim_id"] == "CLM-FLOW-ARCH"
    )
    architecture_paths = {item["path"] for item in architecture["evidence_bindings"]}

    assert "registries/mechanism-cards-v0.json" in ledger_paths
    assert "registries/ledger-candidate-package-surface-v0.json" not in ledger_paths
    assert "registries/system-narrative-trace-map-local-v0.json" not in ledger_paths
    assert "registries/system-narrative-trace-map-local-v0.json" not in architecture_paths
    assert "registries/architecture-visual-atlas-local-v0.json" not in architecture_paths
    assert {
        "docs/architecture-atlas.md",
        "config/architecture-atlas.json",
        "registries/architecture-cross-layer-edges-local-v0.json",
        "registries/mechanism-cards-v0.json",
        *{
            f"assets/architecture-atlas/open-alpha-v1/D{level}.{suffix}"
            for level in range(10)
            for suffix in ("mmd", "svg")
        },
    } <= architecture_paths

    content_doc = (ROOT / "docs/content-graph.md").read_text(encoding="utf-8")
    assert "registries/content-graph-open-alpha-launch-v0.json" in content_doc
    assert "excluded from the Open Alpha export" in content_doc
    assert "is not the current public graph" in content_doc


def test_declared_mechanism_registry_self_hash_cannot_stay_stale() -> None:
    payload = _source()
    mechanism_registry = json.loads(
        (ROOT / "registries/mechanism-cards-v0.json").read_text(encoding="utf-8")
    )
    registry_hash = mechanism_registry["registry_hash"]
    architecture = next(
        claim for claim in payload["claims"] if claim["claim_id"] == "CLM-FLOW-ARCH"
    )
    assert registry_hash in architecture["limitations"][0]

    architecture["limitations"][0] = architecture["limitations"][0].replace(
        registry_hash,
        "sha256:" + "0" * 64,
    )
    with pytest.raises(
        ValidationError,
        match="MECHANISM-REGISTRY-DECLARED-HASH-MISMATCH",
    ):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA, verify_graph=False)

    payload = _source()
    architecture = next(
        claim for claim in payload["claims"] if claim["claim_id"] == "CLM-FLOW-ARCH"
    )
    architecture["limitations"][0] = re.sub(
        r"Mechanism Registry\s+`registry_hash`\s+`?sha256:[0-9a-f]{64}`?",
        "Mechanism Registry declaration omitted",
        architecture["limitations"][0],
    )
    with pytest.raises(
        ValidationError,
        match="MECHANISM-REGISTRY-DECLARED-HASH-MISMATCH",
    ):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA, verify_graph=False)


def test_public_ci_executes_architecture_and_scout_drift_guards(tmp_path: Path) -> None:
    export_root = tmp_path / "public-export"
    architecture_relative = (
        "oss/flowness-oss-harness/registries/"
        "architecture-cross-layer-edges-local-v0.json"
    )
    architecture_path = _copy_public_file(export_root, architecture_relative)
    architecture = json.loads(architecture_path.read_text(encoding="utf-8"))
    seed = _copy_public_file(
        export_root,
        "oss/flowness-oss-harness/registries/mechanism-registry-seed-v0.json",
    )
    for relative in architecture["artifacts"][0]["paths"].values():
        _copy_public_file(export_root, relative)
    _copy_public_file(
        export_root,
        "oss/flowness-oss-harness/assets/architecture-atlas/open-alpha-v1/D9.mmd",
    )
    narrative = _copy_public_file(
        export_root,
        "oss/flowness-oss-harness/docs/rework-ledger.md",
    )

    architecture_result = verify_architecture_edge_registry(
        architecture, export_root, seed
    )
    edge_count = len(architecture_result["verified_edge_ids"])
    narrative.write_text(
        narrative.read_text(encoding="utf-8").replace(
            f"now binds all {edge_count}\nsemantic D1/D2/D5 arrows",
            f"now binds all {edge_count - 1}\nsemantic D1/D2/D5 arrows",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="PUBLIC-NARRATIVE-COUNT-MISMATCH"):
        verify_architecture_edge_registry(architecture, export_root, seed)

    package_root = export_root / "oss/flowness-oss-harness"
    cards_path = _copy_public_file(
        export_root,
        "oss/flowness-oss-harness/registries/mechanism-cards-v0.json",
    )
    scout = _copy_public_file(
        export_root,
        "oss/flowness-oss-harness/docs/semantic-chain-evidence-scout-v0.md",
    )
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    rendered_scout_row = render_readyset_scout_row(cards)
    canonical_coordinates = [
        coordinate
        for card in cards["cards"]
        if card["mechanism_id"] == "M-ORCH-READYSET-EVENT-FANOUT"
        for coordinates in card["static_coordinates"].values()
        for coordinate in coordinates
        if "path" in coordinate
    ]
    canonical_locators = {
        f"{coordinate['path']}:{coordinate['start_line']}-{coordinate['end_line']}"
        for coordinate in canonical_coordinates
    }
    canonical_hashes = {
        coordinate["excerpt_sha256"] for coordinate in canonical_coordinates
    }
    rendered_locators = set(
        re.findall(r"`([^`]+:\d+(?:-\d+)?)`", rendered_scout_row)
    )
    rendered_hashes = set(
        re.findall(r"`(sha256:[0-9a-f]{64})`", rendered_scout_row)
    )
    assert rendered_locators and rendered_locators <= canonical_locators
    assert rendered_hashes and rendered_hashes <= canonical_hashes
    verify_readyset_scout_row(cards, package_root)
    readyset = next(
        card
        for card in cards["cards"]
        if card["mechanism_id"] == "M-ORCH-READYSET-EVENT-FANOUT"
    )
    caller_hash = readyset["static_coordinates"]["caller"][0]["excerpt_sha256"]
    scout.write_text(
        scout.read_text(encoding="utf-8").replace(
            caller_hash,
            "sha256:" + "0" * 64,
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="MECHANISM-SCOUT-COORDINATE-DRIFT"):
        verify_readyset_scout_row(cards, package_root)


def test_every_external_seed_and_comparison_cell_stays_unknown() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)

    assert all(item["verification_state"] == "unknown" for item in payload["source_queue"])
    assert all(item["claims_allowed"] == [] for item in payload["source_queue"])
    external_rows = [
        row
        for row in payload["comparison_matrix"]["rows"]
        if row["row_type"] == "external_seed"
    ]
    assert external_rows
    for row in external_rows:
        assert row["verification_state"] == "unknown"
        assert row["claims_allowed"] == []
        assert all(cell["state"] == "unknown" and cell["evidence"] == [] for cell in row["cells"])


def test_external_seed_cannot_be_laundered_into_a_claim_or_matrix_result() -> None:
    payload = _source()
    payload["source_queue"][0]["claims_allowed"] = ["popular"]
    with pytest.raises(ValidationError):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = _source()
    external = next(
        row for row in payload["comparison_matrix"]["rows"] if row["row_type"] == "external_seed"
    )
    external["cells"][0]["state"] = "experimental"
    external["cells"][0]["evidence"] = ["README.md"]
    with pytest.raises(ValidationError, match="COMPARISON-CELL-PROMOTED"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = _source()
    external_claim = next(claim for claim in payload["claims"] if claim["external_subject"])
    external_claim["state"] = "current_verified"
    external_claim["evidence_bindings"] = [
        {"path": "README.md", "sha256": "sha256:" + "0" * 64}
    ]
    with pytest.raises(ValidationError, match="EXTERNAL-CLAIM-PROMOTED"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)


def test_broken_local_evidence_and_unregistered_external_links_fail_closed() -> None:
    payload = _source()
    payload["claims"][0]["evidence_bindings"][0]["path"] = "docs/does-not-exist.md"
    with pytest.raises(ValidationError, match="LOCAL-LINK-MISSING"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = _source()
    payload["github"]["description"] += " https://example.invalid/unregistered"
    with pytest.raises(ValidationError, match="UNREGISTERED-EXTERNAL-LINK"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)


def test_evidence_bytes_mechanism_ceiling_and_graph_hash_are_fail_closed(tmp_path: Path) -> None:
    copied = tmp_path / "candidate"
    shutil.copytree(
        ROOT,
        copied,
        ignore=shutil.ignore_patterns(".venv", "build", "dist", "*.egg-info", "__pycache__"),
    )
    copied_source = copied / "registries/open-alpha-discovery-launch-pack-v0.json"
    copied_schema = copied / "schemas/open-alpha-discovery-launch-pack.schema.json"
    payload = json.loads(copied_source.read_text(encoding="utf-8"))
    validate_discovery_launch_pack(payload, copied, copied_schema)

    (copied / "docs/open-alpha-demo.md").write_text("tampered evidence\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="EVIDENCE-HASH-MISMATCH"):
        validate_discovery_launch_pack(payload, copied, copied_schema)
    shutil.copy2(ROOT / "docs/open-alpha-demo.md", copied / "docs/open-alpha-demo.md")

    graph_path = copied / payload["content_graph_binding"]["graph_path"]
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    claim_node = next(node for node in graph["nodes"] if node["node_id"] == "claim.launch.flow-loop")
    claim_node["content_hash"] = "sha256:" + "f" * 64
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(ValidationError, match="CONTENT-GRAPH-DRIFT"):
        validate_discovery_launch_pack(payload, copied, copied_schema)

    payload = _source()
    ledger = next(claim for claim in payload["claims"] if claim["claim_id"] == "CLM-FLOW-LEDGER")
    ledger["state"] = "current_verified"
    with pytest.raises(ValidationError, match="CLAIM-OUTRUNS-MECHANISM"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)


def test_deepseek_hypothesis_cannot_be_laundered_into_outward_copy() -> None:
    payload = _source()
    external = next(claim for claim in payload["claims"] if claim["external_subject"])
    assert external["state"] == "unknown"
    assert external["text"].startswith("The owner supplied an unverified hypothesis")
    assert all(
        external["claim_id"] not in item["claim_ids"]
        for item in [
            *payload["introductions"],
            payload["selector_one_pager"],
            *payload["release_draft"]["body_sections"],
            *payload["channel_drafts"],
        ]
    )

    payload["channel_drafts"][0]["claim_ids"].append(external["claim_id"])
    with pytest.raises(ValidationError, match="OUTWARD-ASSET-REFERENCES-UNKNOWN"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = _source()
    payload["claims"][-1]["text"] = "DeepSeek is currently selecting open-source Harness projects."
    with pytest.raises(ValidationError, match="DEEPSEEK-ASSERTION-FORBIDDEN"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)


def test_thirty_second_and_selector_copy_name_fixture_proof_ceiling() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)
    thirty = next(item for item in payload["introductions"] if item["duration"] == "30 seconds")["copy"]
    assert "clarify the goal" in thirty
    assert "engineering contracts" in thirty
    assert "public Alpha currently lets you run and inspect" in thirty

    selector = SELECTOR_DOCUMENT.read_text(encoding="utf-8")
    assert "deterministic producer and judge fixtures" in selector
    assert "replaces only the producers" in selector
    assert "not model quality" in selector
    for required in (
        "Run the smallest proof",
        "flowness-oss open-alpha-demo",
        "flowness-oss open-alpha-demo-inspect",
        "Available now",
        "Not available or not proven",
        "Exact release commit: `db9cda3f82cea192c92f30ccca6ff9f12d5a1d31`",
        "Exact sealed export manifest hash: `sha256:",
        "Apache-2.0",
    ):
        assert required in selector


def test_released_acceptance_gates_are_historical_and_successor_checks_remain_explicit() -> None:
    payload = validate_discovery_launch_pack(_source(), ROOT, SCHEMA)
    gates = {item["gate_id"]: item for item in payload["launch_checklist"]}

    for gate_id in (
        "LCH-SCOPE",
        "LCH-RIGHTS",
        "LCH-LICENSE",
        "LCH-E2E",
        "LCH-EXPORT",
        "LCH-INSTALL",
        "LCH-JURY",
        "LCH-OWNER",
    ):
        assert gates[gate_id]["state"] == "passed_v1_0_0_alpha"
        assert gates[gate_id]["blocking"] is False
    assert gates["LCH-CLAIMS"]["state"] == "successor_revalidation_required"
    assert gates["LCH-DISCOVERY"]["state"] == "released_refresh_in_progress"
    assert gates["LCH-GITHUB"]["state"] == "passed_v1_0_0_alpha"
    assert gates["LCH-GITHUB"]["blocking"] is False

    selector = SELECTOR_DOCUMENT.read_text(encoding="utf-8")
    launch = DOCUMENT.read_text(encoding="utf-8")
    for text in (selector, launch):
        lowered = text.lower()
        assert "successor" in lowered
        assert "v1.0.0-alpha" in lowered
        assert "passed_external_evidence" not in lowered
        assert "independently reproduced" not in lowered


def test_hype_numeric_stars_and_external_actions_are_rejected() -> None:
    payload = _source()
    payload["channel_drafts"][0]["body"] += " Industry-leading."
    with pytest.raises(ValidationError, match="FORBIDDEN-HYPE"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = _source()
    payload["truth_policy"]["wow_history_boundary"] += " It had 100 stars."
    with pytest.raises(ValidationError, match="NUMERIC-STAR"):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)

    payload = copy.deepcopy(_source())
    payload["external_action"] = "published"
    with pytest.raises(ValidationError):
        validate_discovery_launch_pack(payload, ROOT, SCHEMA)
