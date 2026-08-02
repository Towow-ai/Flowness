import json
import re
from pathlib import Path

from flowness_oss_harness.open_alpha_package_scope import (
    build_open_alpha_package_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
README = ROOT / "README.md"


def _public_atlas_mermaid_blocks() -> dict[str, str]:
    atlas = (ROOT / "docs/architecture-atlas.md").read_text(encoding="utf-8")
    rendered = atlas.split("## Rendered progressive views", 1)[1]
    sections = re.split(r"(?m)^### (D[0-9])\b[^\n]*$", rendered)
    blocks: dict[str, str] = {}
    for index in range(1, len(sections), 2):
        diagram_id = sections[index]
        mermaid = re.findall(r"```mermaid\n(.*?)\n```", sections[index + 1], re.DOTALL)
        assert len(mermaid) == 1, diagram_id
        blocks[diagram_id] = mermaid[0]
    return blocks


def test_public_readme_identifies_the_harness_and_truth_boundary() -> None:
    text = README.read_text(encoding="utf-8")

    required = (
        "# Flowness OSS Harness",
        "public Open Alpha coordination and acceptance layer",
        "## Run the acceptance loop",
        "from a Flowness **Git checkout**",
        "never create `.venv`",
        ".venv/bin/pip install -e './oss/flowness-oss-harness[test]'",
        "Linux aarch64 with CPython",
        "## Open Alpha surface",
        "## Maturity and boundaries",
        "docs/open-alpha-demo.md",
        "## From Wow-Harness",
        "## Publication boundary",
    )
    for marker in required:
        assert marker in text

    forbidden_claims = (
        "production-ready",
        "battle-tested",
        "industry-leading",
        "proven at scale",
    )
    lowered = text.lower()
    for claim in forbidden_claims:
        assert claim not in lowered


def test_public_readme_existing_local_links_resolve() -> None:
    targets = (
        "../../public-core/flowness-ledger-core/README.md",
        "../../public-core/flowness-ledger-core/docs/ALPHA_QUICKSTART_CANDIDATE.md",
        "docs/architecture-atlas.md",
        "docs/gate-rules.md",
        "docs/mechanism-excavation-seed-map.md",
        "docs/drift-atlas-seed-v0.md",
        "docs/content-graph.md",
        "docs/rework-ledger.md",
        "docs/benchmark-protocol.md",
        "docs/open-alpha-demo.md",
        "docs/open-alpha-package-scope-v0.md",
        "docs/source-policy.md",
    )
    for target in targets:
        assert (ROOT / target).is_file(), target


def test_root_relative_quickstart_points_to_the_public_package() -> None:
    text = README.read_text(encoding="utf-8")
    assert "from a Flowness **Git checkout**" in text
    assert "immutable root of a bare sealed export" in text
    assert ".venv/bin/pip install -e './oss/flowness-oss-harness[test]'" in text
    assert ".venv/bin/flowness-oss open-alpha-demo" in text


def test_public_payload_has_no_internal_runner_or_owner_home_coordinate() -> None:
    manifest = build_open_alpha_package_manifest(
        repo=REPO,
        policy_path=ROOT / "config/open-alpha-package-scope.json",
    )
    assert manifest["dependency_closure"]["status"] == "closed"
    assert manifest["consumer_closure"]["status"] == "closed"
    forbidden = (
        b"hs-" + b"ts",
        b"/Users/" + b"nature",
        b"/home/" + b"nature",
    )
    findings: list[tuple[str, str]] = []
    for record in manifest["records"]:
        if record["disposition"] != "include":
            continue
        raw = (REPO / record["path"]).read_bytes()
        for token in forbidden:
            if token in raw:
                findings.append((record["path"], token.decode()))
    assert findings == []


def test_public_architecture_atlas_is_mixed_truth_not_private_staging() -> None:
    atlas = (ROOT / "docs/architecture-atlas.md").read_text(encoding="utf-8")
    blocks = _public_atlas_mermaid_blocks()
    assert "public Open Alpha Atlas index" in atlas
    assert "mixed-truth map" in atlas
    assert "`experimental`" in atlas
    assert "`designed_target`" in atlas
    assert "private_local_candidate" not in atlas
    assert "not a release asset" not in atlas

    rendered = atlas.split("## Rendered progressive views", 1)[1]
    assert rendered.count("```mermaid") == 10
    assert rendered.count("**Proof ceiling:**") == 10
    for level in range(10):
        assert len(re.findall(rf"^### D{level}\b", rendered, re.MULTILINE)) == 1
        source = ROOT / f"assets/architecture-atlas/open-alpha-v1/D{level}.mmd"
        rendered_svg = ROOT / f"assets/architecture-atlas/open-alpha-v1/D{level}.svg"
        assert source.is_file()
        assert rendered_svg.read_text(encoding="utf-8").lstrip().startswith("<svg")
        assert source.read_text(encoding="utf-8").strip() == blocks[f"D{level}"].strip()
        assert f"[D{level}.mmd](../assets/architecture-atlas/open-alpha-v1/D{level}.mmd)" in atlas
        assert f"[D{level}.svg](../assets/architecture-atlas/open-alpha-v1/D{level}.svg)" in atlas
    for state in (
        "CURRENT_VERIFIED",
        "EXPERIMENTAL",
        "DESIGNED_TARGET",
        "UNKNOWN",
        "EXTERNAL",
    ):
        assert state in rendered


def test_public_architecture_atlas_matches_stable_node_and_edge_contracts() -> None:
    blocks = _public_atlas_mermaid_blocks()
    contract = json.loads(
        (ROOT / "config/architecture-atlas.json").read_text(encoding="utf-8")
    )
    edge_registry = json.loads(
        (
            ROOT
            / "registries/architecture-cross-layer-edges-local-v0.json"
        ).read_text(encoding="utf-8")
    )

    assert set(blocks) == {f"D{level}" for level in range(10)}
    for view in contract["views"]:
        diagram_id = view["diagram_id"]
        block = blocks[diagram_id]
        declared_nodes = set(
            re.findall(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\[", block)
        )
        required_nodes = {"H"}
        required_edges: set[str] = set()
        for element in view["element_contract"]:
            required_nodes.update(element["node_ids"])
            required_edges.update(element["edge_ids"])

        assert required_nodes <= declared_nodes, (
            diagram_id,
            sorted(required_nodes - declared_nodes),
        )
        for edge_id in required_edges:
            assert edge_id in block, (diagram_id, edge_id)

        for marker in (
            "CURRENT_VERIFIED",
            "EXPERIMENTAL",
            "UNKNOWN",
            "PROOF CEILING",
        ):
            assert marker in block, (diagram_id, marker)
        assert "DESIGNED TARGET" in block or "DESIGNED_TARGET" in block
        assert re.search(r"\bFAIL(?:URE)?\b|\bFailure\b", block)
        assert re.search(r"\bRecovery\b|\brecovery\b", block)

    for edge in edge_registry["edges"]:
        diagram_id = edge["diagram_id"]
        if diagram_id not in blocks:
            continue
        pattern = re.compile(
            rf"(?m)^\s*{re.escape(edge['rendered_source_id'])}"
            rf"(?:\[[^\]]*\])?\s+"
            rf"[-.]+>\|[^|\n]*{re.escape(edge['edge_id'])}[^|\n]*\|\s*"
            rf"{re.escape(edge['rendered_target_id'])}(?:\[|\s|$)"
        )
        assert pattern.search(blocks[diagram_id]), (
            diagram_id,
            edge["edge_id"],
            edge["rendered_source_id"],
            edge["rendered_target_id"],
        )
