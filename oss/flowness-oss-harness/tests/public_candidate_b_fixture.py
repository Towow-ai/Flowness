from __future__ import annotations

import hashlib
import json
from pathlib import Path

from flowness_oss_harness.candidate_b_assembly import create_candidate_b_assembly
from flowness_oss_harness.integrity import canonical_hash
from flowness_oss_harness.policy import load_approved_policy
from flowness_oss_harness.rework import create_blocker_case, create_rework_manifest


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _snapshot(path: Path, snapshot_id: str, *, dirty: bool = False) -> Path:
    return _write(
        path,
        {
            "schema_version": "evidence-snapshot/v1",
            "snapshot_id": snapshot_id,
            "repository": "file:///private/tmp/candidate-b-fixture",
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "dirty": dirty,
            "repository_content_hash": "3" * 64,
            "captured_at": "2026-08-02T00:00:00Z",
            "dirty_paths": ["fixture.txt"] if dirty else [],
            "inventory": {},
            "candidate_assembly_eligible": not dirty,
            "release_eligible": False,
            "boundary": "fixture; private candidate assembly only",
        },
    )


def _candidate(path: Path, snapshot_id: str, evidence_path: Path) -> Path:
    body = {
        "schema_version": "1.0",
        "snapshot": {
            "snapshot_id": snapshot_id,
            "repo": "file:///private/tmp/candidate-b-fixture",
            "commit_sha": "1" * 40,
            "tree_sha": "2" * 40,
            "dirty": False,
            "built_at": "2026-08-02T00:00:00Z",
        },
        "target_stage": "alpha",
        "created_at": "2026-08-02T00:00:00Z",
        "modules": [
            {
                "module_id": "module-ledger",
                "layer": "L0",
                "name": "Ledger",
                "critical": True,
                "declared_maturity": 1,
                "dependencies": [],
                "evidence_ids": ["evidence-ledger"],
            }
        ],
        "claims": [
            {
                "claim_id": "claim-ledger",
                "text": "Fixture claim",
                "scope": "fixture",
                "baseline": "none",
                "success_criteria": "fixture succeeds",
                "critical": True,
                "evidence_ids": ["evidence-ledger"],
                "limitations": ["fixture"],
                "last_verified_at": "2026-08-02T00:00:00Z",
            }
        ],
        "benchmarks": [],
        "evidence": [
            {
                "evidence_id": "evidence-ledger",
                "type": "passing_test",
                "uri": "fixture/evidence-ledger",
                "sha256": _sha_bytes(evidence_path.read_bytes()),
                "snapshot_id": snapshot_id,
                "observed_at": "2026-08-02T00:00:00Z",
                "producer": "fixture",
                "summary": "fixture evidence",
            }
        ],
    }
    payload = {
        "candidate_id": "candidate-"
        + canonical_hash(body).removeprefix("sha256:")[:24],
        **body,
    }
    return _write(path, payload)


def _assembly_manifest(path: Path, candidate_path: Path) -> Path:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    body = {
        "schema_version": "candidate-assembly-manifest/v1",
        "snapshot": candidate["snapshot"],
        "target_stage": candidate["target_stage"],
        "created_at": candidate["created_at"],
    }
    return _write(path, {**body, "manifest_hash": canonical_hash(body)})


def _failed_report(path: Path, candidate_path: Path) -> Path:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    policy, _ = load_approved_policy()
    body = {
        "schema_version": "1.0",
        "report_id": "report-original",
        "candidate_id": candidate["candidate_id"],
        "snapshot_id": candidate["snapshot"]["snapshot_id"],
        "policy_version": policy["policy_version"],
        "phase": "first_pass",
        "judge": {
            "role_id": "judge.fixture",
            "agent_instance_id": "agent-original",
            "organization": "independent",
            "conflicts": [],
            "attestations": {
                "blind_first_pass": True,
                "no_shared_verdicts": True,
                "not_candidate_author": True,
            },
        },
        "started_at": "2026-08-02T00:01:00Z",
        "finished_at": "2026-08-02T00:02:00Z",
        "checks": [
            {
                "check_id": "G0.fixture",
                "gate_id": "G0",
                "dimension": "truth",
                "critical": True,
                "verdict": "fail",
                "confidence": 0.99,
                "tested_claim_ids": ["claim-ledger"],
                "evidence_ids": ["evidence-ledger"],
                "reproduction": {
                    "command_template": "python -m fixture",
                    "environment_hash": "sha256:" + "f" * 64,
                    "attempts": 1,
                },
                "expected": "sealed",
                "observed": "failed",
                "blocker_id": "BLK-fixture-001",
                "missing_evidence": [],
                "na_rationale": None,
                "remediation": "repair",
                "retest_condition": "new snapshot",
            }
        ],
    }
    return _write(path, {**body, "signature": canonical_hash(body)})


def _fixture(tmp_path: Path) -> dict[str, Path]:
    evidence_a = tmp_path / "evidence-a.bin"
    evidence_a.write_bytes(b"candidate-a-evidence")
    candidate_a = _candidate(
        tmp_path / "candidate-a.json", "sha256:" + "a" * 64, evidence_a
    )
    snapshot_a = _snapshot(tmp_path / "snapshot-a.json", "sha256:" + "a" * 64)
    original_report = _failed_report(tmp_path / "report-a.json", candidate_a)
    case = tmp_path / "case.json"
    create_blocker_case(
        candidate_path=candidate_a,
        snapshot_path=snapshot_a,
        report_path=original_report,
        blocker_id="BLK-fixture-001",
        check_id="G0.fixture",
        ripple_targets=[
            {"target_type": "claim", "target_id": "claim-ledger", "reason": "fixture"}
        ],
        output=case,
    )
    evidence_b = tmp_path / "evidence-b.bin"
    evidence_b.write_bytes(b"candidate-b-evidence")
    candidate_b = _candidate(
        tmp_path / "candidate-b.json", "sha256:" + "b" * 64, evidence_b
    )
    snapshot_b = _snapshot(tmp_path / "snapshot-b.json", "sha256:" + "b" * 64)
    assembly_manifest = _assembly_manifest(
        tmp_path / "assembly-manifest-b.json", candidate_b
    )
    rework = tmp_path / "rework.json"
    create_rework_manifest(
        blocker_case_path=case,
        successor_candidate_path=candidate_b,
        successor_snapshot_path=snapshot_b,
        rework_evidence=[
            {"artifact_id": "evidence:rework", "sha256": "sha256:" + "c" * 64}
        ],
        ripple_invalidations=[
            {
                "target_type": "claim",
                "target_id": "claim-ledger",
                "reason": "fixture",
                "required_revalidation": "fresh independent jury",
            }
        ],
        output=rework,
    )
    return {
        "candidate_b": candidate_b,
        "snapshot_b": snapshot_b,
        "assembly_manifest": assembly_manifest,
        "case": case,
        "rework": rework,
        "evidence_b": evidence_b,
    }


def _assemble(paths: dict[str, Path], output_dir: Path) -> dict:
    return create_candidate_b_assembly(
        candidate_path=paths["candidate_b"],
        source_snapshot_path=paths["snapshot_b"],
        assembly_manifest_path=paths["assembly_manifest"],
        blocker_case_path=paths["case"],
        rework_manifest_path=paths["rework"],
        evidence_paths=[("evidence-ledger", paths["evidence_b"])],
        output_dir=output_dir,
    )
