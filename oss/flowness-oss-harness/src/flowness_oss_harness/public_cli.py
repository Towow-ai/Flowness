"""Narrow console surface for the Flowness Open Alpha package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .open_alpha_demo import inspect_open_alpha_demo, run_open_alpha_demo
from .registry import ValidationError


PUBLIC_COMMANDS = {
    "open-alpha-demo": "Run the deterministic offline multi-agent/jury/rework demonstration.",
    "open-alpha-demo-inspect": "Verify a previously generated demo trace and its bindings.",
    "commands": "Print the exact public Open Alpha command surface.",
}
BOUNDARY = (
    "Open Alpha exposes only the deterministic demo and its read-only verifier; "
    "private controller, fleet, publication, and execution-policy commands are not public console APIs."
)


class _PublicParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n{BOUNDARY}\n")


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = _PublicParser(prog="flowness-oss", description=BOUNDARY)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("commands", help=PUBLIC_COMMANDS["commands"])
    demo = commands.add_parser("open-alpha-demo", help=PUBLIC_COMMANDS["open-alpha-demo"])
    demo.add_argument("--output", required=True, type=_path)
    inspect = commands.add_parser(
        "open-alpha-demo-inspect", help=PUBLIC_COMMANDS["open-alpha-demo-inspect"]
    )
    inspect.add_argument("--run-root", required=True, type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "commands":
            result = {"commands": PUBLIC_COMMANDS, "boundary": BOUNDARY}
        elif args.command == "open-alpha-demo":
            result = run_open_alpha_demo(args.output, runner="fixture")
        else:
            result = inspect_open_alpha_demo(args.run_root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
