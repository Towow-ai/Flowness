from __future__ import annotations

"""Private, hash-bound handoff for Ledger candidate evaluation.

This module deliberately prepares and records an evaluator exercise.  It does
not say who the evaluator is, that the environment is clean-room, that the
candidate may be released, or that anybody has rights to distribute it.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, canonical_json_bytes, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


KIT_ID = "ledger-alpha-independent-evaluation-kit/v0"
INPUT_SCHEMA = SCHEMAS_ROOT / "ledger-alpha-independent-evaluation-input.schema.json"
HANDOFF_SCHEMA = SCHEMAS_ROOT / "ledger-alpha-independent-evaluation-handoff.schema.json"
RECEIPT_SCHEMA = SCHEMAS_ROOT / "ledger-alpha-independent-evaluation-receipt.schema.json"
RUNNER_PATH = Path(__file__).resolve().parents[2] / "tools" / "ledger_alpha_evaluation_runner.py"
SLOTS = (
    "sealed_candidate_artifact",
    "sealed_candidate_artifact_manifest",
    "sealed_candidate_export_manifest",
    "candidate_source_inventory",
)
ASSERTIONS = (
    "positive_change_evidence_e2e",
    "pending_invisibility",
    "corrupt_tail_refusal_and_recovery",
    "conflicting_decision_refusal",
    "unresolved_major_verdict_refusal",
)
NO_AUTHORITY_BOUNDARY = (
    "private evaluator handoff only; no evaluator identity, clean-room status, "
    "rights, Alpha readiness, release, publication, network, checkout, or credential authority is asserted"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"LEDGER-EVALUATION-{label}-NOT-REGULAR-FILE")
    return path.resolve(strict=True)


def _hash_text(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _load_input(payload: dict[str, Any]) -> dict[str, Any]:
    validate_payload(payload, INPUT_SCHEMA, "Ledger evaluator input")
    if tuple(sorted(payload["input_slots"])) != tuple(sorted(SLOTS)):
        raise ValidationError("LEDGER-EVALUATION-INPUT-SLOTS-MISMATCH")
    return payload


def _bound_inputs(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    """Resolve all required private inputs and bind every observed byte string."""

    payload = _load_input(payload)
    redacted: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for slot in SLOTS:
        declared = payload["input_slots"][slot]
        path = _regular_file(Path(declared["path"]), slot.upper())
        observed = _sha256_file(path)
        if declared["sha256"] != observed:
            raise ValidationError(f"LEDGER-EVALUATION-{slot.upper()}-HASH-MISMATCH")
        paths[slot] = path
        # Only a basename and byte identity leave the private handoff; no local
        # directory, user name, command line, or credential can reach a receipt.
        redacted[slot] = {
            "file_name": path.name,
            "bytes": path.stat().st_size,
            "sha256": observed,
        }
    return redacted, paths


def _runner_descriptor() -> dict[str, str]:
    runner = _regular_file(RUNNER_PATH, "RUNNER")
    return {"file_name": runner.name, "sha256": _sha256_file(runner)}


def create_ledger_alpha_evaluation_handoff(
    input_payload: dict[str, Any], output: Path,
) -> dict[str, Any]:
    """Create a non-overwritable, redacted evaluator handoff.

    The caller retains the input paths locally.  The handoff only carries the
    exact bytes and required checks, so it is safe to pass between private
    evaluation workspaces without leaking source locations.
    """

    inputs, _ = _bound_inputs(input_payload)
    unsigned = {
        "schema_version": "ledger-alpha-independent-evaluation-handoff/v1",
        "kit_id": KIT_ID,
        "scope": "private_staging_evaluator_handoff",
        "authorization": "not_authorized",
        "input_binding": inputs,
        "runner": _runner_descriptor(),
        "required_assertion_ids": list(ASSERTIONS),
        "receipt_contract": {
            "schema_version": "ledger-alpha-independent-evaluation-receipt/v1",
            "redacted": True,
            "retains_every_failure": True,
            "preflight_artifact_requirement": "cleanroom",
            "preflight_compatibility_boundary": (
                "A receipt can be declared as a hash-bound cleanroom artifact input later, "
                "but this kit never establishes independent-cleanroom status by itself."
            ),
        },
        "boundary": NO_AUTHORITY_BOUNDARY,
    }
    handoff = {**unsigned, "handoff_hash": canonical_hash(unsigned)}
    validate_payload(handoff, HANDOFF_SCHEMA, "Ledger evaluator handoff")
    atomic_create_json(output, handoff)
    return handoff


def verify_ledger_alpha_evaluation_handoff(handoff_path: Path) -> dict[str, Any]:
    handoff = load_validated_json(handoff_path, HANDOFF_SCHEMA, "Ledger evaluator handoff")
    verify_self_hash(handoff, "handoff_hash")
    if (
        handoff["kit_id"] != KIT_ID
        or handoff["scope"] != "private_staging_evaluator_handoff"
        or handoff["authorization"] != "not_authorized"
        or handoff["required_assertion_ids"] != list(ASSERTIONS)
        or handoff["runner"] != _runner_descriptor()
        or handoff["boundary"] != NO_AUTHORITY_BOUNDARY
    ):
        raise ValidationError("LEDGER-EVALUATION-HANDOFF-IDENTITY-INVALID")
    return handoff


def _failed_assertions(code: str) -> list[dict[str, str]]:
    return [
        {"assertion_id": assertion, "outcome": "error", "code": code}
        for assertion in ASSERTIONS
    ]


def _read_runner_observations(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _failed_assertions("RUNNER-OUTPUT-INVALID"), {}
    assertions = payload.get("assertions") if isinstance(payload, dict) else None
    environment = payload.get("environment") if isinstance(payload, dict) else None
    if (
        not isinstance(assertions, list)
        or {item.get("assertion_id") for item in assertions if isinstance(item, dict)} != set(ASSERTIONS)
        or len(assertions) != len(ASSERTIONS)
        or any(
            not isinstance(item, dict)
            or set(item) != {"assertion_id", "outcome", "code"}
            or item["outcome"] not in {"passed", "failed", "error"}
            or not isinstance(item["code"], str)
            for item in assertions
        )
        or not isinstance(environment, dict)
        or set(environment) != {"implementation", "platform", "python_version", "candidate_source_match"}
        or not all(isinstance(value, str) for value in environment.values())
    ):
        return _failed_assertions("RUNNER-OUTPUT-INVALID"), {}
    return sorted(assertions, key=lambda item: item["assertion_id"]), environment


def _receipt_id(unsigned: dict[str, Any]) -> str:
    return "ledger-eval-" + canonical_hash(unsigned)[7:23]


def run_ledger_alpha_evaluation(
    handoff_path: Path,
    input_payload: dict[str, Any],
    candidate_python: Path,
    output: Path,
    *,
    candidate_pythonpath: Path | None = None,
) -> dict[str, Any]:
    """Run every required assertion and write one immutable redacted receipt.

    Failure is a result, not an exception path: a candidate import failure,
    runner failure, or failing invariant still creates a receipt with all five
    required assertion rows.  The optional import root exists only for private
    source-fixture testing; it is not evidence of an installed clean room.
    """

    handoff = verify_ledger_alpha_evaluation_handoff(handoff_path)
    inputs, paths = _bound_inputs(input_payload)
    if inputs != handoff["input_binding"]:
        raise ValidationError("LEDGER-EVALUATION-INPUT-BINDING-DOES-NOT-MATCH-HANDOFF")
    python = _regular_file(candidate_python, "CANDIDATE-PYTHON")
    env = {"PATH": os.environ.get("PATH", "")}
    if candidate_pythonpath is not None:
        candidate_root = Path(candidate_pythonpath).resolve(strict=True)
        env["PYTHONPATH"] = str(candidate_root)

    with tempfile.TemporaryDirectory(prefix="ledger-alpha-eval-") as temporary:
        work_dir = Path(temporary) / "work"
        runner_output = Path(temporary) / "runner-observations.json"
        command = [
            str(python), str(RUNNER_PATH), "--work-dir", str(work_dir),
            "--source-inventory", str(paths["candidate_source_inventory"]),
            "--output", str(runner_output),
        ]
        try:
            completed = subprocess.run(
                command, env=env, text=True, capture_output=True, check=False, timeout=60
            )
            assertions, environment = _read_runner_observations(runner_output)
            if completed.returncode != 0 and all(item["outcome"] == "passed" for item in assertions):
                assertions = _failed_assertions("RUNNER-NONZERO-WITHOUT-FAILURE-RECORD")
        except (OSError, subprocess.TimeoutExpired):
            assertions, environment = _failed_assertions("RUNNER-UNAVAILABLE"), {}

    all_passed = all(item["outcome"] == "passed" for item in assertions)
    unsigned = {
        "schema_version": "ledger-alpha-independent-evaluation-receipt/v1",
        "receipt_id": "",
        "kit_id": KIT_ID,
        "scope": "private_staging_evaluator_result",
        "authorization": "not_authorized",
        "handoff": {"handoff_hash": handoff["handoff_hash"]},
        "input_binding": inputs,
        "executor": {
            "python_file_name": python.name,
            "python_sha256": _sha256_file(python),
            "runner_sha256": _runner_descriptor()["sha256"],
        },
        "environment": environment or {
            "implementation": "unavailable",
            "platform": "unavailable",
            "python_version": "unavailable",
            "candidate_source_match": "unavailable",
        },
        "assertions": assertions,
        "all_required_assertions_passed": all_passed,
        "evaluation_state": "all_assertions_passed" if all_passed else "assertions_failed",
        "not_proven": [
            "evaluator identity or independence",
            "independent clean-room installation",
            "sealed artifact was the installed executable",
            "rights, license, SBOM, Alpha readiness, release, or publication authorization",
            "network, checkout, credential, production, benchmark, or performance evidence",
        ],
        "boundary": NO_AUTHORITY_BOUNDARY,
    }
    unsigned["receipt_id"] = _receipt_id(unsigned)
    receipt = {**unsigned, "receipt_hash": canonical_hash(unsigned)}
    validate_payload(receipt, RECEIPT_SCHEMA, "Ledger evaluator receipt")
    atomic_create_json(output, receipt)
    return receipt


def evaluation_receipt_preflight_artifact(receipt_path: Path, relative_path: str) -> dict[str, Any]:
    """Return a hash-bound `cleanroom` artifact declaration for later preflight.

    This is format-compatible with ``public-package-artifacts/v1`` only.  It
    deliberately does not promote the receipt to independent-cleanroom proof.
    """

    receipt = load_validated_json(receipt_path, RECEIPT_SCHEMA, "Ledger evaluator receipt")
    verify_self_hash(receipt, "receipt_hash")
    if receipt["kit_id"] != KIT_ID or receipt["authorization"] != "not_authorized":
        raise ValidationError("LEDGER-EVALUATION-RECEIPT-IDENTITY-INVALID")
    if not relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
        raise ValidationError("LEDGER-EVALUATION-PREFLIGHT-PATH-INVALID")
    return {
        "schema_version": "public-package-artifacts/v1",
        "artifacts": {
            "cleanroom": {"path": relative_path, "sha256": _sha256_file(receipt_path)}
        },
    }
