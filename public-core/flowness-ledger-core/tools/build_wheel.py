#!/usr/bin/env python3
"""Build the public Open Alpha wheel without downloading a build backend.

This intentionally narrow builder exists because Ledger Core has no runtime
dependencies and supports a local offline artifact-install walkthrough. It is
not a substitute for the external sealed-release identity or release record.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import zipfile
from pathlib import Path

NAME = "flowness_ledger_core"
VERSION = "1.0.0a1"
DIST_INFO = f"{NAME}-{VERSION}.dist-info"


def _record_hash(content: bytes) -> str:
    digest = hashlib.sha256(content).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    package = root / "src" / "flowness_ledger_core"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / f"{NAME}-{VERSION}-py3-none-any.whl"
    if wheel.exists():
        wheel.unlink()

    contents: dict[str, bytes] = {}
    for path in sorted(package.rglob("*.py")):
        contents[str(path.relative_to(root / "src"))] = path.read_bytes()
    contents[f"{DIST_INFO}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        "Name: flowness-ledger-core\n"
        "Version: 1.0.0a1\n"
        "Summary: Recoverable decision ledger for evidence-driven multi-agent workflows.\n"
        "License-Expression: Apache-2.0\n"
        "Requires-Python: >=3.11\n"
    ).encode()
    contents[f"{DIST_INFO}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: flowness-ledger-core/tools/build_wheel.py\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode()
    contents[f"{DIST_INFO}/entry_points.txt"] = (
        "[console_scripts]\nflowness-ledger-demo = flowness_ledger_core.cli:main\n"
    ).encode()
    records = [f"{name},{_record_hash(data)},{len(data)}" for name, data in contents.items()]
    contents[f"{DIST_INFO}/RECORD"] = ("\n".join(records + [f"{DIST_INFO}/RECORD,,"]) + "\n").encode()
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            archive.writestr(name, data)
    print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
