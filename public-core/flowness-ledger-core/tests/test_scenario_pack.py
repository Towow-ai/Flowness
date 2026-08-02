from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowness_ledger_core.demo import run_change_evidence_demo
from flowness_ledger_core.cli import main
from flowness_ledger_core.ledger import LedgerError
from flowness_ledger_core.scenario_pack import (
    MARKDOWN_FILE,
    MERMAID_FILE,
    PACK_FILE,
    TIMELINE_FILE,
    create_demo_scenario_pack,
    verify_demo_scenario_pack,
)


def test_scenario_pack_derives_auditable_explanation_from_verified_demo(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    run_change_evidence_demo(demo)

    pack = create_demo_scenario_pack(demo, tmp_path / "pack")

    assert pack == verify_demo_scenario_pack(demo, tmp_path / "pack")
    timeline = json.loads((tmp_path / "pack" / TIMELINE_FILE).read_text(encoding="utf-8"))
    assert timeline["candidate_boundary"] == "public_open_alpha_local_explanation_not_production"
    assert timeline["accepted_path"]["committed_types"] == ["change.requested", "artifact.checked"]
    assert timeline["rejected_and_conflict_path"]["review_verdict"]["verdict"] == "rejected_not_committed"
    assert timeline["recovery_path"]["recovery_action"] == "truncated_incomplete_tail"
    assert timeline["projection_path"]["stale_read_refused_after_pending_event"] is True
    assert timeline["major_verdict_negative_path"]["pending_verdict_refused"] is True
    markdown = (tmp_path / "pack" / MARKDOWN_FILE).read_text(encoding="utf-8")
    assert "not a runtime trace," in markdown
    assert "performance or efficiency benchmark" in markdown
    assert "MECH-EVT-001" in markdown and "MECH-PROJ-001" in markdown and "MECH-REVIEW-001" in markdown
    assert "sequenceDiagram" in (tmp_path / "pack" / MERMAID_FILE).read_text(encoding="utf-8")


def test_scenario_pack_is_deterministic_for_same_verified_input(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    run_change_evidence_demo(demo)
    create_demo_scenario_pack(demo, tmp_path / "first")
    create_demo_scenario_pack(demo, tmp_path / "second")

    for name in (PACK_FILE, TIMELINE_FILE, MARKDOWN_FILE, MERMAID_FILE):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_scenario_pack_verifier_rejects_tampered_input_or_output(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    result = run_change_evidence_demo(demo)
    pack = tmp_path / "pack"
    create_demo_scenario_pack(demo, pack)
    (pack / MARKDOWN_FILE).write_text("tampered\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="timeline.md hash does not match"):
        verify_demo_scenario_pack(demo, pack)

    pack = tmp_path / "second-pack"
    create_demo_scenario_pack(demo, pack)
    ledger = demo / result["artifacts"]["ledger"]["path"]
    ledger.write_bytes(ledger.read_bytes() + b"changed")
    with pytest.raises(LedgerError, match="artifact hash does not match"):
        verify_demo_scenario_pack(demo, pack)


def test_scenario_pack_refuses_nonempty_destination(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    run_change_evidence_demo(demo)
    destination = tmp_path / "pack"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(LedgerError, match="new empty regular directory"):
        create_demo_scenario_pack(demo, destination)


def test_scenario_pack_cli_derives_and_reverifies_from_the_same_demo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo = tmp_path / "demo"
    run_change_evidence_demo(demo)
    scenario = tmp_path / "scenario"

    assert main(["--scenario-pack-from-demo", str(demo), "--scenario-pack-dir", str(scenario)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["candidate_boundary"] == "public_open_alpha_local_explanation_not_production"
    assert main(["--verify-scenario-pack-from-demo", str(demo), "--scenario-pack-dir", str(scenario)]) == 0
    assert json.loads(capsys.readouterr().out) == created
