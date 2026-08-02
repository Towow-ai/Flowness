from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flowness_oss_harness.cleanroom_challenger import (
    NO_AUTHORITY_BOUNDARY,
    evaluate_cleanroom_challenger_preflight,
    verify_cleanroom_challenger_preflight,
    write_cleanroom_challenger_preflight,
)
from flowness_oss_harness.cli import main
from flowness_oss_harness.registry import ValidationError


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _input(tmp_path: Path, *, plan_override: dict | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"not-a-real-wheel; challenger never imports it\n")
    artifact_manifest = tmp_path / "artifact-manifest.json"
    artifact_manifest.write_text('{"sealed": true}\n', encoding="utf-8")
    export_manifest = tmp_path / "export-manifest.json"
    export_manifest.write_text('{"export": "candidate"}\n', encoding="utf-8")
    plan_path = tmp_path / "challenge-plan.json"
    plan = {
        "schema_version": "cleanroom-challenger-plan/v1",
        "candidate_install": {
            "mode": "sealed_artifact",
            "reference_kind": "opaque_artifact_id",
            "reference": "sealed-candidate-artifact",
            "artifact_sha256": _hash(artifact),
        },
        "dependency_policy": {"resolution": "offline_no_network", "dependencies": []},
        "environment": {"inherit_parent": False, "requested_variables": []},
    }
    if plan_override:
        for key, value in plan_override.items():
            plan[key] = value
    plan_path.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    slots = {
        "sealed_candidate_artifact": artifact,
        "sealed_candidate_artifact_manifest": artifact_manifest,
        "sealed_candidate_export_manifest": export_manifest,
        "challenge_plan": plan_path,
    }
    return {
        "schema_version": "cleanroom-challenger-preflight-input/v1",
        "input_slots": {
            slot: {"path": str(path), "sha256": _hash(path)}
            for slot, path in slots.items()
        },
    }


def _codes(receipt: dict) -> set[str]:
    return {code for check in receipt["checks"] for code in check["codes"]}


def test_local_preflight_binds_only_four_files_and_does_not_claim_cleanroom(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    receipt_path = tmp_path / "receipt.json"

    receipt = write_cleanroom_challenger_preflight(payload, receipt_path)

    assert receipt["preflight_state"] == "local_preflight_passed"
    assert [item["check_id"] for item in receipt["checks"]] == [
        "hash_bound_candidate_artifacts",
        "source_checkout_refusal",
        "editable_or_local_absolute_refusal",
        "private_environment_refusal",
        "network_dependency_refusal",
    ]
    assert str(tmp_path) not in receipt_path.read_text(encoding="utf-8")
    assert "an independently controlled clean-room environment" in receipt["not_proven"]
    assert receipt["boundary"] == NO_AUTHORITY_BOUNDARY
    assert verify_cleanroom_challenger_preflight(receipt_path)["receipt_hash"] == receipt["receipt_hash"]
    with pytest.raises(ValidationError, match="refusing to overwrite"):
        write_cleanroom_challenger_preflight(payload, receipt_path)


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {
                "candidate_install": {
                    "mode": "source_checkout",
                    "reference_kind": "source_checkout",
                    "reference": "source-tree",
                    "artifact_sha256": "sha256:" + "0" * 64,
                }
            },
            "CLEANROOM-CHALLENGER-SOURCE-CHECKOUT-REFUSED",
        ),
        (
            {
                "candidate_install": {
                    "mode": "editable",
                    "reference_kind": "editable_install",
                    "reference": "editable-source",
                    "artifact_sha256": "sha256:" + "0" * 64,
                }
            },
            "CLEANROOM-CHALLENGER-EDITABLE-REFUSED",
        ),
        (
            {
                "candidate_install": {
                    "mode": "sealed_artifact",
                    "reference_kind": "opaque_artifact_id",
                    "reference": "file:///private/candidate.whl",
                    "artifact_sha256": "sha256:" + "0" * 64,
                }
            },
            "CLEANROOM-CHALLENGER-LOCAL-ABSOLUTE-PATH-REFUSED",
        ),
        (
            {"environment": {"inherit_parent": True, "requested_variables": ["PRIVATE_TOKEN"]}},
            "CLEANROOM-CHALLENGER-PRIVATE-ENVIRONMENT-REFUSED",
        ),
        (
            {"dependency_policy": {"resolution": "network_required", "dependencies": ["dependency"]}},
            "CLEANROOM-CHALLENGER-NETWORK-DEPENDENCY-REFUSED",
        ),
        (
            {
                "candidate_install": {
                    "mode": "sealed_artifact",
                    "reference_kind": "opaque_artifact_id",
                    "reference": "https://example.invalid/candidate.whl",
                    "artifact_sha256": "sha256:" + "0" * 64,
                }
            },
            "CLEANROOM-CHALLENGER-NONOPAQUE-REFERENCE-REFUSED",
        ),
    ],
)
def test_attack_plans_are_recorded_as_blocked_without_installing(
    tmp_path: Path, override: dict, expected: str,
) -> None:
    receipt = evaluate_cleanroom_challenger_preflight(_input(tmp_path, plan_override=override))

    assert receipt["preflight_state"] == "local_preflight_blocked"
    assert expected in _codes(receipt)
    # The candidate fixture is intentionally not importable.  Its bytes are
    # bound, but this preflight never runs an installer or an import.
    assert (tmp_path / "candidate.whl").read_bytes().startswith(b"not-a-real-wheel")


def test_tampered_input_or_symlink_is_refused_before_a_receipt(tmp_path: Path) -> None:
    payload = _input(tmp_path)
    Path(payload["input_slots"]["sealed_candidate_artifact"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValidationError, match="SEALED_CANDIDATE_ARTIFACT-HASH-MISMATCH"):
        evaluate_cleanroom_challenger_preflight(payload)

    payload = _input(tmp_path / "symlink-case")
    source = Path(payload["input_slots"]["challenge_plan"]["path"])
    alias = source.parent / "plan-alias.json"
    alias.symlink_to(source)
    payload["input_slots"]["challenge_plan"]["path"] = str(alias)
    payload["input_slots"]["challenge_plan"]["sha256"] = _hash(source)
    with pytest.raises(ValidationError, match="CHALLENGE_PLAN-NOT-REGULAR-FILE"):
        evaluate_cleanroom_challenger_preflight(payload)


def test_cli_only_writes_local_receipt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(_input(tmp_path / "files")), encoding="utf-8")
    output = tmp_path / "local-receipt.json"

    assert main(["cleanroom-challenger-preflight", "--input", str(payload_path), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preflight_state"] == "local_preflight_passed"
    assert json.loads(output.read_text(encoding="utf-8"))["authorization"] == "not_authorized"
