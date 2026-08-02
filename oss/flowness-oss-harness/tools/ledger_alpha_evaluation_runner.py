from __future__ import annotations

"""Runner copied with the private evaluator kit; it never performs network I/O."""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path


ASSERTIONS = (
    "positive_change_evidence_e2e",
    "pending_invisibility",
    "corrupt_tail_refusal_and_recovery",
    "conflicting_decision_refusal",
    "unresolved_major_verdict_refusal",
)


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _environment(source_inventory: Path) -> dict[str, str]:
    import flowness_ledger_core as package

    inventory = json.loads(source_inventory.read_text(encoding="utf-8"))
    package_root = Path(package.__file__).resolve().parent
    matched = True
    for entry in inventory.get("source_files", []):
        relative = Path(entry.get("path", ""))
        parts = relative.parts
        if len(parts) < 3 or parts[:2] != ("src", "flowness_ledger_core"):
            matched = False
            break
        candidate = package_root.joinpath(*parts[2:])
        if not candidate.is_file() or _hash(candidate) != entry.get("sha256"):
            matched = False
            break
    return {
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "python_version": platform.python_version(),
        "candidate_source_match": "matched" if matched else "mismatched",
    }


def _result(assertion_id: str, action) -> dict[str, str]:
    try:
        action()
        return {"assertion_id": assertion_id, "outcome": "passed", "code": "OBSERVED"}
    except Exception as exc:  # evaluator needs every failure, including unexpected ones
        return {
            "assertion_id": assertion_id,
            "outcome": "failed",
            "code": type(exc).__name__.upper(),
        }


def run(work_dir: Path, source_inventory: Path) -> dict[str, object]:
    try:
        environment = _environment(source_inventory)
        from flowness_ledger_core.demo import run_change_evidence_demo, verify_change_evidence_demo
        from flowness_ledger_core.ledger import Ledger, LedgerError
        from flowness_ledger_core.review import build_review_verdict
    except Exception as exc:
        code = "IMPORT_" + type(exc).__name__.upper()
        return {
            "environment": {},
            "assertions": [
                {"assertion_id": assertion, "outcome": "error", "code": code}
                for assertion in ASSERTIONS
            ],
        }

    demo_dir = work_dir / "demo"
    manifest: dict[str, object] = {}

    def positive() -> None:
        nonlocal manifest
        manifest = run_change_evidence_demo(demo_dir)
        verify_change_evidence_demo(demo_dir)
        if environment["candidate_source_match"] != "matched":
            raise AssertionError("candidate source does not match inventory")

    def pending() -> None:
        if manifest.get("invariants", {}).get("pending_is_invisible") is not True:
            raise AssertionError("pending visibility invariant missing")

    def corrupt_tail() -> None:
        invariants = manifest.get("invariants", {})
        if not (
            invariants.get("tail_requires_recovery") is True
            and invariants.get("recovery_truncated_only_tail") is True
        ):
            raise AssertionError("corrupt-tail refusal/recovery invariant missing")

    def conflict() -> None:
        if manifest.get("invariants", {}).get("conflict_is_rejected") is not True:
            raise AssertionError("conflicting-decision refusal invariant missing")

    def unresolved_verdict() -> None:
        ledger = Ledger.open(work_dir / "pending-verdict", create=True)
        ledger.begin_proposal("unresolved-major", {"severity": "major"})
        ledger.append_proposed("unresolved-major", [{"type": "major.finding"}])
        try:
            build_review_verdict(ledger, "unresolved-major")
        except LedgerError as exc:
            if "requires terminal decision" not in str(exc):
                raise AssertionError("wrong unresolved verdict refusal") from exc
            return
        raise AssertionError("unresolved major verdict was accepted")

    return {
        "environment": environment,
        "assertions": [
            _result("positive_change_evidence_e2e", positive),
            _result("pending_invisibility", pending),
            _result("corrupt_tail_refusal_and_recovery", corrupt_tail),
            _result("conflicting_decision_refusal", conflict),
            _result("unresolved_major_verdict_refusal", unresolved_verdict),
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = run(args.work_dir, args.source_inventory)
    args.output.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
