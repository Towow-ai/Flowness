#!/usr/bin/env python3
"""Record and verify one local, offline wheel-install observation.

The receipt deliberately describes a *local staging observation*.  It records
that a given wheel was installed into a nominated Python environment, that the
installed console script created and then verified the candidate demo, and
that the installed module resolves under that environment's prefix.  It is not
an external clean-room attestation, a signed release artifact, or a license
grant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "flowness-ledger-core-local-wheel-install-receipt/v1"
BOUNDARY = "public_open_alpha_local_wheel_observation_not_clean_room_receipt"
DIST_NAME = "flowness-ledger-core"
EXPECTED_VERSION = "1.0.0a1"


class ReceiptError(RuntimeError):
    """A local install observation cannot be safely recorded or replayed."""


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ReceiptError(f"{label} must be a regular file")
    return path.resolve(strict=True)


def _executable_file(path: Path, label: str) -> Path:
    """Accept the normal venv-python symlink while preserving its entry path.

    A virtual environment's ``bin/python`` is commonly a symlink to a shared
    interpreter.  The path itself is what a caller executes and must remain
    under the nominated venv; resolving it before storing would incorrectly
    turn it into the host interpreter path.
    """

    if not path.is_file():
        raise ReceiptError(f"{label} must name an executable file")
    return path.absolute()


def _new_receipt_path(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise ReceiptError("receipt path must not already exist")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ReceiptError("receipt parent must be an existing regular directory")
    return path.resolve()


def _run(command: list[str], label: str) -> str:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReceiptError(f"{label} failed: {detail}")
    return completed.stdout


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _installed_observation(python: Path, console_script: Path) -> dict[str, str]:
    source = (
        "import importlib.metadata,json,sys,flowness_ledger_core;"
        "print(json.dumps({'prefix':sys.prefix,'package_file':flowness_ledger_core.__file__,"
        "'version':importlib.metadata.version('flowness-ledger-core')}))"
    )
    raw = _run([str(python), "-c", source], "installed module inspection")
    try:
        observation = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReceiptError("installed module inspection did not produce JSON") from exc
    if set(observation) != {"prefix", "package_file", "version"} or not all(
        isinstance(value, str) and value for value in observation.values()
    ):
        raise ReceiptError("installed module inspection produced an invalid observation")
    prefix = Path(observation["prefix"]).resolve(strict=True)
    package_file = _regular_file(Path(observation["package_file"]), "installed package file")
    script = _regular_file(console_script, "installed console script")
    if not _inside(package_file, prefix):
        raise ReceiptError("installed package does not resolve under the nominated environment")
    if not _inside(script, prefix):
        raise ReceiptError("installed console script does not resolve under the nominated environment")
    if observation["version"] != EXPECTED_VERSION:
        raise ReceiptError("installed candidate version is not the expected staging version")
    return {
        "python": str(python),
        "prefix": str(prefix),
        "package_file": str(package_file),
        "package_sha256": _sha256(package_file),
        "version": observation["version"],
        "console_script": str(script),
        "console_script_sha256": _sha256(script),
    }


def _parse_demo_manifest(raw: str, label: str) -> dict[str, Any]:
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"{label} did not produce a JSON demo manifest") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("manifest_sha256"), str):
        raise ReceiptError(f"{label} produced an invalid demo manifest")
    return manifest


def create_receipt(
    wheel: Path, python: Path, console_script: Path, demo_dir: Path, receipt_path: Path
) -> dict[str, Any]:
    wheel = _regular_file(wheel, "wheel")
    if wheel.suffix != ".whl":
        raise ReceiptError("wheel must have a .whl suffix")
    python = _executable_file(python, "environment python")
    receipt_path = _new_receipt_path(receipt_path)
    environment = _installed_observation(python, console_script)
    demo_output = _run([environment["console_script"], "--demo-dir", str(demo_dir)], "installed demo")
    demo_manifest = _parse_demo_manifest(demo_output, "installed demo")
    verify_output = _run(
        [environment["console_script"], "--verify-demo-dir", str(demo_dir)],
        "installed demo verifier",
    )
    verified_manifest = _parse_demo_manifest(verify_output, "installed demo verifier")
    if verified_manifest != demo_manifest:
        raise ReceiptError("installed demo verifier returned a different manifest")
    receipt = {
        "schema_version": SCHEMA,
        "boundary": BOUNDARY,
        "wheel": {"path": str(wheel), "sha256": _sha256(wheel), "filename": wheel.name},
        "environment": environment,
        "demo": {
            "path": str(Path(demo_dir).resolve()),
            "manifest_sha256": demo_manifest["manifest_sha256"],
        },
        "not_proven": [
            "this local receipt is not an independent clean-room acceptance receipt",
            "this local receipt is not the external sealed-export or license-policy record",
            "this local receipt is not a signed release artifact",
            "cross-platform compatibility or production reliability",
        ],
    }
    receipt["receipt_sha256"] = "sha256:" + hashlib.sha256(_canonical(receipt)).hexdigest()
    receipt_path.write_bytes(_canonical(receipt) + b"\n")
    return receipt


def verify_receipt(receipt_path: Path) -> dict[str, Any]:
    receipt_path = _regular_file(receipt_path, "receipt")
    try:
        receipt = json.loads(receipt_path.read_bytes())
    except json.JSONDecodeError as exc:
        raise ReceiptError("receipt is not valid JSON") from exc
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt is not an object")
    expected_keys = {"schema_version", "boundary", "wheel", "environment", "demo", "not_proven", "receipt_sha256"}
    if set(receipt) != expected_keys:
        raise ReceiptError("receipt has an invalid shape")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    expected_hash = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    if (
        receipt["schema_version"] != SCHEMA
        or receipt["boundary"] != BOUNDARY
        or receipt["receipt_sha256"] != expected_hash
    ):
        raise ReceiptError("receipt header or self hash is invalid")
    wheel = receipt["wheel"]
    environment = receipt["environment"]
    demo = receipt["demo"]
    if not isinstance(wheel, dict) or not isinstance(environment, dict) or not isinstance(demo, dict):
        raise ReceiptError("receipt has invalid sections")
    wheel_path = _regular_file(Path(wheel.get("path", "")), "receipt wheel")
    if wheel.get("filename") != wheel_path.name or wheel.get("sha256") != _sha256(wheel_path):
        raise ReceiptError("receipt wheel hash does not match")
    python = _executable_file(Path(environment.get("python", "")), "receipt environment python")
    observed = _installed_observation(python, Path(environment.get("console_script", "")))
    if observed != environment:
        raise ReceiptError("installed environment no longer matches receipt")
    demo_dir = Path(demo.get("path", ""))
    verified_manifest = _parse_demo_manifest(
        _run([observed["console_script"], "--verify-demo-dir", str(demo_dir)], "receipt demo verifier"),
        "receipt demo verifier",
    )
    if demo.get("manifest_sha256") != verified_manifest.get("manifest_sha256"):
        raise ReceiptError("receipt demo manifest does not match")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record or verify a local candidate wheel-install receipt")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--console-script", type=Path)
    parser.add_argument("--demo-dir", type=Path)
    args = parser.parse_args(argv)
    if args.create:
        required = {"--wheel": args.wheel, "--python": args.python, "--console-script": args.console_script, "--demo-dir": args.demo_dir}
        missing = [flag for flag, value in required.items() if value is None]
        if missing:
            parser.error("--create requires " + ", ".join(missing))
        result = create_receipt(args.wheel, args.python, args.console_script, args.demo_dir, args.receipt)
    else:
        result = verify_receipt(args.receipt)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReceiptError as exc:
        print(f"receipt verification failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
