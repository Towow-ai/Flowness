from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from flowness_ledger_core import Ledger, LedgerError


def test_pending_records_are_invisible_until_accepted(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("P-1", {"case": "pending"})
    ledger.append_proposed("P-1", [{"type": "change.requested"}, {"type": "artifact.checked"}])

    assert ledger.read("committed") == []
    assert len(ledger.read("audit")) == 3
    ledger.decide("P-1", "accepted", {"reason": "checked"})
    assert [record["payload"]["type"] for record in ledger.read()] == ["change.requested", "artifact.checked"]
    assert Ledger.open(str(tmp_path)).read() == ledger.read()


def test_rejected_and_conflicting_decisions_never_enter_committed_view(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("P-2", {})
    ledger.append_proposed("P-2", [{"type": "change.requested"}])
    first = ledger.decide("P-2", "rejected", {"reason": "insufficient"})

    assert ledger.decide("P-2", "rejected", {"reason": "insufficient"}) == first
    with pytest.raises(LedgerError, match="conflicting"):
        ledger.decide("P-2", "accepted", {"reason": "later"})
    assert ledger.read() == []


def test_recovery_truncates_only_an_incomplete_final_jsonl_tail(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("P-3", {})
    ledger.append_proposed("P-3", [{"type": "artifact.checked"}])
    baseline = ledger.path.read_bytes()
    with ledger.path.open("ab") as handle:
        handle.write(b'{"format":"half')

    with pytest.raises(LedgerError, match="incomplete tail"):
        ledger.read()
    report = ledger.recover(persist_report=True)

    assert report.action == "truncated_incomplete_tail"
    assert report.pending_proposals == ("P-3",)
    assert ledger.path.read_bytes() == baseline
    assert ledger.read() == []
    persisted = ledger.recovery_report_path(report)
    assert persisted.is_file()
    verified = ledger.verify_recovery_report(persisted)
    assert verified["report_hash"] == report.to_dict()["report_hash"]
    assert verified["ledger_head_sequence"] == 2


def test_recovery_report_rejects_tampered_state_summary(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path, create=True)
    ledger.begin_proposal("P-4", {})
    report = ledger.recover(persist_report=True)
    persisted = ledger.recovery_report_path(report)

    payload = persisted.read_text(encoding="utf-8")
    persisted.write_text(payload.replace('"committed_watermark":0', '"committed_watermark":9'), encoding="utf-8")

    with pytest.raises(LedgerError, match="self-hashed"):
        ledger.verify_recovery_report(persisted)


def test_zero_dependency_builder_emits_a_valid_wheel(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    built = subprocess.run(
        [sys.executable, "tools/build_wheel.py", "--output", str(tmp_path)],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    wheel = Path(built.stdout.strip())

    assert wheel.is_file()
    assert wheel.name.endswith("-py3-none-any.whl")
