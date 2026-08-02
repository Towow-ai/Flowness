"""Small, dependency-light public CLI for the Flowness Harness kernel."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Sequence


def _package_version() -> str:
    try:
        return version("flowness-harness")
    except PackageNotFoundError:
        return "source-checkout"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flowness-harness",
        description="Inspect the portable Flowness Open Alpha kernel.",
    )
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = {
        "name": "flowness-harness",
        "version": _package_version(),
        "maturity": "open-alpha",
        "real_agent_spawn": "adapter-required",
    }
    if args.version:
        print(payload["version"])
    elif args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("Flowness Harness Open Alpha")
        print("real agent spawning requires a separately authorized adapter")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
