from __future__ import annotations

from pathlib import Path


def test_candidate_casebook_preserves_three_evidence_bounded_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    casebook = (root / "docs" / "LEDGER_CANDIDATE_CASEBOOK.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "## Case 1 — an accepted change becomes visible as one committed result",
        "## Case 2 — a rejected proposal and later conflicting decision do not rewrite history",
        "## Case 3 — an interrupted final write blocks reads until bounded tail recovery",
    ):
        assert heading in casebook
    assert casebook.count("**Status:** `experimental_open_alpha_local`") == 3
    assert casebook.count("### Observable evidence") == 3
    assert casebook.count("### Failure and recovery boundary") == 3
    assert casebook.count("### Cannot prove") == 3
    for source in (
        "../src/flowness_ledger_core/demo.py",
        "../src/flowness_ledger_core/ledger.py",
        "../tests/test_ledger.py",
        "LEDGER_CANDIDATE_TECHNICAL_REPORT.md",
    ):
        assert source in casebook
