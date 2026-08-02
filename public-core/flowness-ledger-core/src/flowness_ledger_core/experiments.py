from __future__ import annotations

"""Raw semantic-trial records for the private ledger candidate.

This is deliberately not a performance benchmark. It records repeated,
independently verified invariant trials so later evaluators can inspect success
and failure samples instead of receiving an aggregate claim.
"""

import hashlib
import json
import platform
from pathlib import Path
from typing import Any

from .demo import run_change_evidence_demo, verify_change_evidence_demo
from .ledger import LedgerError


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def run_semantic_trials(root: Path | str, trials: int = 3) -> dict[str, Any]:
    if trials < 1:
        raise LedgerError("trials must be at least one")
    directory = Path(root)
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir() or any(directory.iterdir()):
            raise LedgerError("trial directory must be new and empty")
    else:
        directory.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    for index in range(1, trials + 1):
        trial_dir = directory / f"trial-{index:03d}"
        try:
            produced = run_change_evidence_demo(trial_dir)
            verified = verify_change_evidence_demo(trial_dir)
            if produced != verified:
                raise LedgerError("demo verification returned different manifest")
            rows.append({"trial_id": f"trial-{index:03d}", "verdict": "pass", "manifest_sha256": produced["manifest_sha256"], "failure": None})
        except Exception as exc:  # preserve failures as data, then stop fail-closed
            rows.append({"trial_id": f"trial-{index:03d}", "verdict": "fail", "manifest_sha256": None, "failure": f"{type(exc).__name__}: {exc}"})
            break
    unsigned = {
        "schema_version": "flowness-ledger-core-semantic-trials/v1",
        "candidate_boundary": "local_private_candidate_not_performance_benchmark",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "requested_trials": trials,
        "completed_trials": len(rows),
        "trials": rows,
        "all_passed": len(rows) == trials and all(row["verdict"] == "pass" for row in rows),
        "not_proven": ["performance", "external clean room", "production reliability", "public release"],
    }
    payload = {**unsigned, "trials_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}
    (directory / "semantic-trials.json").write_bytes(_canonical(payload) + b"\n")
    return payload
