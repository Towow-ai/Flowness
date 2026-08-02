from __future__ import annotations

from pathlib import Path


def test_candidate_d3_d5_architecture_keeps_local_and_authority_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    document = (root / "docs" / "LEDGER_CANDIDATE_ARCHITECTURE_D3_D5.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(document.split())

    for heading in (
        "## D3 — local control, data execution, and evidence planes",
        "## D4 — event, state, and authority boundary",
        "## D5 — local demo, recovery, projection, and review sequences",
    ):
        assert heading in document
    assert document.count("```mermaid") == 3
    assert document.count("**Status:** `current Open Alpha local slice`.") == 3
    assert document.count("### Evidence pointers") == 3
    assert document.count("### Cannot prove") == 3

    for source in (
        "../src/flowness_ledger_core/ledger.py",
        "../src/flowness_ledger_core/projection.py",
        "../src/flowness_ledger_core/review.py",
        "../src/flowness_ledger_core/demo.py",
        "../tests/test_ledger.py",
        "../tests/test_projection.py",
        "../tests/test_review.py",
        "../tests/test_demo.py",
        "LEDGER_CANDIDATE_ARCHITECTURE_D0_D2.md",
        "LEDGER_CANDIDATE_TECHNICAL_REPORT.md",
    ):
        assert source in document

    for boundary in (
        "do not turn the package into a deployment, an RBAC system, a production runtime",
        "does not imply independently deployed services",
        "means that authorization is",
        "outside this code",
        "not a claim that the demo automatically invokes projection or review",
        "not an end-to-end multi-agent workflow",
        "remain unknown or outside this package",
    ):
        assert boundary in normalized
