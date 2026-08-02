from __future__ import annotations

import json
import hashlib

import pytest

from flowness_ledger_core.experiments import run_semantic_trials
from flowness_ledger_core.ledger import LedgerError
from flowness_ledger_core.measurements import (
    LEGACY_MEASUREMENT_SCHEMA,
    MEASUREMENT_SCHEMA,
    run_raw_local_measurements,
    summarize_raw_local_measurements,
    verify_raw_local_measurements,
)


def _rehash_receipt(path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in payload.items() if key != "measurements_sha256"}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["measurements_sha256"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_semantic_trials_preserve_raw_verified_trial_records(tmp_path) -> None:
    result = run_semantic_trials(tmp_path / "trials", trials=2)
    assert result["all_passed"] is True
    assert [row["verdict"] for row in result["trials"]] == ["pass", "pass"]
    assert json.loads((tmp_path / "trials" / "semantic-trials.json").read_text()) == result


def test_raw_measurements_bind_each_fixture_result_environment_and_elapsed(tmp_path) -> None:
    root = tmp_path / "measurements"
    result = run_raw_local_measurements(root, trials=2)

    assert result["all_passed"] is True
    assert [row["verdict"] for row in result["trials"]] == ["pass", "pass"]
    assert all(isinstance(row["monotonic_elapsed_ns"], int) for row in result["trials"])
    assert all(row["fixture"]["sha256"].startswith("sha256:") for row in result["trials"])
    assert all(row["result"]["sha256"].startswith("sha256:") for row in result["trials"])
    assert all(row["environment"] == result["environment"] for row in result["trials"])
    assert verify_raw_local_measurements(root) == result


def test_raw_measurements_preserve_failed_trials_and_verify_them(tmp_path) -> None:
    def failing_runner(_run_dir):
        raise RuntimeError("fixture deliberately failed")

    root = tmp_path / "failed-measurements"
    result = run_raw_local_measurements(root, trials=2, trial_runner=failing_runner)

    assert result["all_passed"] is False
    assert [row["verdict"] for row in result["trials"]] == ["fail", "fail"]
    assert all("fixture deliberately failed" in row["failure"] for row in result["trials"])
    assert all((root / row["result"]["path"]).is_file() for row in result["trials"])
    assert verify_raw_local_measurements(root) == result


def test_raw_measurements_reject_tampered_trial_result(tmp_path) -> None:
    root = tmp_path / "tampered-measurements"
    run_raw_local_measurements(root, trials=1)
    (root / "trial-001" / "result.json").write_text('{"tampered":true}\n')

    with pytest.raises(LedgerError, match="hash does not match"):
        verify_raw_local_measurements(root)


def test_measurement_summary_verifies_receipts_and_keeps_failure_samples(tmp_path) -> None:
    def failing_runner(_run_dir):
        raise RuntimeError("kept in summary")

    passed = tmp_path / "passed"
    failed = tmp_path / "failed"
    run_raw_local_measurements(passed, trials=2)
    run_raw_local_measurements(failed, trials=1, trial_runner=failing_runner)

    summary = summarize_raw_local_measurements([passed, failed / "raw-local-measurements.json"])

    aggregate = summary["raw_aggregate"]
    assert summary["candidate"]["schema_version"] == MEASUREMENT_SCHEMA
    assert aggregate["attempts"] == 3
    assert aggregate["passed"] == 2
    assert aggregate["failed"] == 1
    assert aggregate["all_passed"] is False
    assert len(aggregate["elapsed_samples"]) == 3
    assert len(aggregate["failed_samples"]) == 1
    assert "kept in summary" in aggregate["failed_samples"][0]["failure"]
    elapsed = aggregate["elapsed_ns"]
    assert elapsed["valid_sample_count"] == 3
    assert elapsed["min"] <= elapsed["median"] <= elapsed["max"]
    assert all("benchmark" not in limitation.lower() or "not a benchmark" in limitation.lower() for limitation in summary["limitations"])


@pytest.mark.parametrize("kind", ["candidate_id", "environment", "schema"])
def test_measurement_summary_refuses_mixed_candidate_environment_or_schema(tmp_path, kind: str) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    run_raw_local_measurements(first, trials=1)
    run_raw_local_measurements(second, trials=1)
    receipt = second / "raw-local-measurements.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))

    if kind == "candidate_id":
        payload["candidate_id"] = "another-private-candidate"
        expected = "mixed candidate_id"
    elif kind == "environment":
        payload["environment"]["platform"] = "other-platform"
        payload["trials"][0]["environment"]["platform"] = "other-platform"
        expected = "mixed environment"
    else:
        payload["schema_version"] = LEGACY_MEASUREMENT_SCHEMA
        del payload["candidate_id"]
        expected = "requires current receipts"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    _rehash_receipt(receipt)

    with pytest.raises(LedgerError, match=expected):
        summarize_raw_local_measurements([first, second])


def test_measurement_summary_refuses_duplicate_or_invalid_receipt(tmp_path) -> None:
    root = tmp_path / "receipt"
    run_raw_local_measurements(root, trials=1)
    with pytest.raises(LedgerError, match="duplicate"):
        summarize_raw_local_measurements([root, root / "raw-local-measurements.json"])
    (root / "trial-001" / "result.json").write_text('{"tampered":true}\n')
    with pytest.raises(LedgerError, match="hash does not match"):
        summarize_raw_local_measurements([root])
