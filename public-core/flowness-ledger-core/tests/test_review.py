from __future__ import annotations

import pytest

from flowness_ledger_core import Ledger, LedgerError, build_review_verdict


def test_review_verdict_preserves_terminal_accept_and_reject_meaning(tmp_path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("a", {})
    ledger.append_proposed("a", [{"type": "reviewed"}])
    ledger.decide("a", "accepted", {})
    assert build_review_verdict(ledger, "a")["verdict"] == "accepted_committed"
    ledger.begin_proposal("r", {})
    ledger.decide("r", "rejected", {})
    assert build_review_verdict(ledger, "r")["verdict"] == "rejected_not_committed"


def test_review_verdict_refuses_pending_proposal(tmp_path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("pending", {})
    with pytest.raises(LedgerError, match="terminal"):
        build_review_verdict(ledger, "pending")
