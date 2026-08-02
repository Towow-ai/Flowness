from __future__ import annotations

"""A self-contained, inspectable demonstration of the candidate contract."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .ledger import Ledger, LedgerError


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(_canonical(payload) + b"\n")


def _safe_relative(root: Path, candidate: str) -> Path:
    path = (root / candidate).resolve(strict=True)
    try:
        path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise LedgerError("demo artifact path escapes the demo directory") from exc
    if path.is_symlink() or not path.is_file():
        raise LedgerError("demo artifact path is unsafe")
    return path


def run_change_evidence_demo(demo_dir: Path | str) -> dict[str, Any]:
    """Create one new-directory demo with both success and failure evidence.

    It is intentionally a candidate demonstration, not a benchmark or an
    installation attestation.  The returned manifest carries only relative
    artifact paths so a later clean-room verifier can consume the same shape.
    """

    root = Path(demo_dir)
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise LedgerError("demo directory must be a new empty regular directory")
    else:
        root.mkdir(parents=True)
    # Ledger.open canonicalizes its directory.  Match that canonical root here
    # before deriving manifest-relative artifacts, including when an ancestor
    # such as /var is a symlink to /private/var.
    root = root.resolve(strict=True)
    ledger = Ledger.open(root / "ledger", create=True)

    accepted = "P-accepted"
    ledger.begin_proposal(accepted, {"purpose": "change evidence demo"})
    ledger.append_proposed(
        accepted,
        [{"type": "change.requested"}, {"type": "artifact.checked"}],
    )
    pending_visible = ledger.read("committed")
    ledger.decide(accepted, "accepted", {"review": "demo"})
    committed_after_accept = ledger.read("committed")

    rejected = "P-rejected"
    ledger.begin_proposal(rejected, {"purpose": "negative decision demo"})
    ledger.append_proposed(rejected, [{"type": "change.rejected"}])
    ledger.decide(rejected, "rejected", {"review": "insufficient evidence"})
    committed_after_reject = ledger.read("committed")
    try:
        ledger.decide(rejected, "accepted", {"review": "later override"})
    except LedgerError as exc:
        conflict_error = str(exc)
    else:  # pragma: no cover - contract guard, not an expected branch
        raise LedgerError("candidate accepted a conflicting immutable decision")

    with ledger.path.open("ab") as handle:
        handle.write(b'{"format":"incomplete-tail')
    try:
        ledger.read("committed")
    except LedgerError as exc:
        interrupted_read_error = str(exc)
    else:  # pragma: no cover - contract guard, not an expected branch
        raise LedgerError("candidate read an incomplete JSONL tail")
    # Convert the dataclass's tuple fields to JSON-native arrays once, so the
    # returned object and the persisted report are byte-for-byte equivalent.
    recovered_report = ledger.recover(persist_report=True)
    recovery = json.loads(_canonical(recovered_report.to_dict()))
    recovery_path = ledger.recovery_report_path(recovered_report)
    if not recovery_path.is_file():  # pragma: no cover - persistence guard
        raise LedgerError("persisted recovery report is missing")

    manifest = {
        "schema_version": "flowness-ledger-core-change-evidence-demo/v1",
        "candidate_boundary": "public_open_alpha_local_demo_not_production",
        "ledger_format": Ledger.FORMAT,
        "artifacts": {
            "ledger": {"path": "ledger/ledger.jsonl", "sha256": _hash_file(ledger.path)},
            "recovery_report": {
                "path": str(recovery_path.relative_to(root)),
                "sha256": _hash_file(recovery_path),
                "self_hash": recovery["report_hash"],
            },
        },
        "observations": {
            "pending_committed_view": pending_visible,
            "accepted_committed_types": [
                item["payload"]["type"] for item in committed_after_accept
            ],
            "rejected_committed_types": [
                item["payload"]["type"] for item in committed_after_reject
            ],
            "conflicting_decision_error": conflict_error,
            "interrupted_read_error": interrupted_read_error,
            "recovery": recovery,
        },
        "invariants": {
            "pending_is_invisible": pending_visible == [],
            "accepted_records_appear_together": [
                item["payload"]["type"] for item in committed_after_accept
            ]
            == ["change.requested", "artifact.checked"],
            "rejected_record_is_invisible": [
                item["payload"]["type"] for item in committed_after_reject
            ]
            == ["change.requested", "artifact.checked"],
            "conflict_is_rejected": "conflicting immutable decision" in conflict_error,
            "tail_requires_recovery": "incomplete tail" in interrupted_read_error,
            "recovery_truncated_only_tail": recovery["action"] == "truncated_incomplete_tail",
        },
        "not_proven": [
            "this local run is not the external sealed-export or license-policy record",
            "this local run is not an independent clean-room acceptance receipt",
            "distributed concurrency or exactly-once delivery",
            "projection/watermark or review-verdict behavior",
            "runtime performance or production incident handling",
        ],
    }
    manifest["manifest_sha256"] = "sha256:" + hashlib.sha256(
        _canonical(manifest)
    ).hexdigest()
    _write_json(root / "demo-run.json", manifest)
    return manifest


def verify_change_evidence_demo(demo_dir: Path | str) -> dict[str, Any]:
    """Independently check a completed demo's manifest, receipt and negatives."""

    root = Path(demo_dir).resolve(strict=True)
    manifest_path = _safe_relative(root, "demo-run.json")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except json.JSONDecodeError as exc:
        raise LedgerError("demo manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise LedgerError("demo manifest is not an object")
    manifest_hash = manifest.get("manifest_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if (
        manifest.get("schema_version") != "flowness-ledger-core-change-evidence-demo/v1"
        or manifest.get("candidate_boundary") != "public_open_alpha_local_demo_not_production"
        or not isinstance(manifest_hash, str)
        or manifest_hash != "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    ):
        raise LedgerError("invalid self-hashed demo manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise LedgerError("demo manifest has no artifacts")
    for name in ("ledger", "recovery_report"):
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise LedgerError(f"demo manifest has invalid {name} artifact")
        path = _safe_relative(root, artifact["path"])
        if artifact.get("sha256") != _hash_file(path):
            raise LedgerError(f"demo {name} artifact hash does not match")
    ledger = Ledger.open(root / "ledger")
    recovery_artifact = artifacts["recovery_report"]
    recovery = ledger.verify_recovery_report(root / recovery_artifact["path"])
    if recovery_artifact.get("self_hash") != recovery["report_hash"]:
        raise LedgerError("demo recovery report self hash does not match")
    invariants = manifest.get("invariants")
    observations = manifest.get("observations")
    if not isinstance(invariants, dict) or not isinstance(observations, dict):
        raise LedgerError("demo manifest has invalid observations or invariants")
    required_negative_checks = {
        "rejected_record_is_invisible",
        "conflict_is_rejected",
        "tail_requires_recovery",
        "recovery_truncated_only_tail",
    }
    if not all(invariants.get(name) is True for name in required_negative_checks):
        raise LedgerError("demo negative E2E invariant is missing or false")
    if observations.get("recovery") != recovery:
        raise LedgerError("demo recovery observation does not match persisted receipt")
    return manifest
