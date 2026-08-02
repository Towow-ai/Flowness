from __future__ import annotations

from pathlib import Path


def test_candidate_d0_d2_architecture_keeps_three_evidence_bounded_layers() -> None:
    root = Path(__file__).resolve().parents[1]
    document = (root / "docs" / "LEDGER_CANDIDATE_ARCHITECTURE_D0_D2.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "## D0 — why a durable draft must not look like a decision",
        "## D1 — the local proposal-to-verdict path",
        "## D2 — candidate states, failures, and recovery limits",
    ):
        assert heading in document
    assert document.count("```mermaid") == 3
    assert document.count("**Status:** `current Open Alpha local slice`.") == 3
    assert document.count("### Evidence pointers") == 3
    assert document.count("### Cannot prove") == 3
    for boundary in (
        "not a runtime topology",
        "not a live service state machine",
        "agent runtime",
        "does not assert that every recovered prefix has that shape",
    ):
        assert boundary in document
    for source in (
        "../src/flowness_ledger_core/ledger.py",
        "../src/flowness_ledger_core/review.py",
        "../src/flowness_ledger_core/demo.py",
        "../tests/test_ledger.py",
        "LEDGER_CANDIDATE_CASEBOOK.md",
        "LEDGER_CANDIDATE_TECHNICAL_REPORT.md",
    ):
        assert source in document
