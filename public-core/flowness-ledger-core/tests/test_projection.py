from __future__ import annotations

import pytest

from flowness_ledger_core import Ledger, LedgerError, read_fresh_type_projection, rebuild_type_projection


def test_projection_uses_committed_view_and_refuses_stale_watermark(tmp_path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("accepted", {})
    ledger.append_proposed("accepted", [{"type": "accepted.type"}])
    ledger.decide("accepted", "accepted", {})
    ledger.begin_proposal("rejected", {})
    ledger.append_proposed("rejected", [{"type": "rejected.type"}])
    ledger.decide("rejected", "rejected", {})

    first = rebuild_type_projection(ledger)
    assert first["committed_type_counts"] == {"accepted.type": 1}
    assert read_fresh_type_projection(ledger) == first
    ledger.begin_proposal("pending", {})
    with pytest.raises(LedgerError, match="stale"):
        read_fresh_type_projection(ledger)
    rebuilt = rebuild_type_projection(ledger)
    assert rebuilt["committed_type_counts"] == first["committed_type_counts"]
    assert rebuilt["watermark"] != first["watermark"]


def test_projection_tampering_fails_closed(tmp_path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    rebuild_type_projection(ledger)
    path = ledger.directory / "projections" / "committed-types.json"
    path.write_text('{"bad":true}\n', encoding="utf-8")
    with pytest.raises(LedgerError, match="hash"):
        read_fresh_type_projection(ledger)
