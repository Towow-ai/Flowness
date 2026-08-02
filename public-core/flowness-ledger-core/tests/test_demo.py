from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowness_ledger_core.demo import run_change_evidence_demo, verify_change_evidence_demo
from flowness_ledger_core.ledger import LedgerError


def test_change_evidence_demo_produces_auditable_success_and_negative_cases(
    tmp_path: Path,
) -> None:
    result = run_change_evidence_demo(tmp_path / "demo")

    assert all(result["invariants"].values())
    assert result["candidate_boundary"] == "public_open_alpha_local_demo_not_production"
    saved = json.loads((tmp_path / "demo" / "demo-run.json").read_text())
    assert saved == result
    verified = verify_change_evidence_demo(tmp_path / "demo")
    assert verified == result
    recovery_path = tmp_path / "demo" / saved["artifacts"]["recovery_report"]["path"]
    assert recovery_path.is_file()
    assert saved["observations"]["recovery"]["action"] == "truncated_incomplete_tail"


def test_demo_verifier_rejects_tampered_persisted_recovery_receipt(tmp_path: Path) -> None:
    result = run_change_evidence_demo(tmp_path / "demo")
    receipt = tmp_path / "demo" / result["artifacts"]["recovery_report"]["path"]
    receipt.write_text('{"not":"a valid recovery receipt"}\n', encoding="utf-8")

    with pytest.raises(LedgerError, match="artifact hash does not match"):
        verify_change_evidence_demo(tmp_path / "demo")


def test_change_evidence_demo_refuses_to_overwrite_existing_material(
    tmp_path: Path,
) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "existing.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(LedgerError, match="new empty regular directory"):
        run_change_evidence_demo(demo)


def test_change_evidence_demo_canonicalizes_a_symlinked_ancestor(tmp_path: Path) -> None:
    """A caller may enter a real new directory through a symlinked ancestor."""

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    result = run_change_evidence_demo(linked_parent / "demo")

    assert verify_change_evidence_demo(real_parent / "demo") == result
