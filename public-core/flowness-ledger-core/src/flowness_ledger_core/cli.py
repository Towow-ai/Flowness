from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo import run_change_evidence_demo, verify_change_evidence_demo
from .experiments import run_semantic_trials
from .measurements import (
    run_raw_local_measurements,
    summarize_raw_local_measurements,
    verify_raw_local_measurements,
)
from .scenario_pack import create_demo_scenario_pack, verify_demo_scenario_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the public Flowness Ledger Core Open Alpha demo"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo-dir", type=Path, help="create a new candidate demo")
    mode.add_argument(
        "--verify-demo-dir", type=Path, help="verify a completed candidate demo"
    )
    mode.add_argument(
        "--semantic-trials-dir", type=Path, help="run repeated local semantic trials"
    )
    mode.add_argument(
        "--raw-measurements-dir",
        type=Path,
        help="write raw local observations; this is not a benchmark",
    )
    mode.add_argument(
        "--verify-raw-measurements-dir",
        type=Path,
        help="verify a completed raw local measurement receipt",
    )
    mode.add_argument(
        "--summarize-raw-measurements",
        type=Path,
        nargs="+",
        metavar="RECEIPT_PATH",
        help="verify and aggregate homogeneous local receipts; not a benchmark",
    )
    mode.add_argument(
        "--scenario-pack-from-demo",
        type=Path,
        help="derive a public evidence-bounded scenario pack from a verified demo",
    )
    mode.add_argument(
        "--verify-scenario-pack-from-demo",
        type=Path,
        help="verify a scenario pack against a verified local demo",
    )
    parser.add_argument(
        "--scenario-pack-dir",
        type=Path,
        help="new scenario output directory (required for scenario pack modes)",
    )
    parser.add_argument("--trials", type=int, default=3, help="semantic trial count")
    args = parser.parse_args(argv)
    if (args.scenario_pack_from_demo is not None or args.verify_scenario_pack_from_demo is not None) and args.scenario_pack_dir is None:
        parser.error("--scenario-pack-dir is required for scenario pack modes")
    result = (
        run_change_evidence_demo(args.demo_dir)
        if args.demo_dir is not None
        else verify_change_evidence_demo(args.verify_demo_dir)
        if args.verify_demo_dir is not None
        else run_semantic_trials(args.semantic_trials_dir, args.trials)
        if args.semantic_trials_dir is not None
        else run_raw_local_measurements(args.raw_measurements_dir, args.trials)
        if args.raw_measurements_dir is not None
        else verify_raw_local_measurements(args.verify_raw_measurements_dir)
        if args.verify_raw_measurements_dir is not None
        else summarize_raw_local_measurements(args.summarize_raw_measurements)
        if args.summarize_raw_measurements is not None
        else create_demo_scenario_pack(args.scenario_pack_from_demo, args.scenario_pack_dir)
        if args.scenario_pack_from_demo is not None
        else verify_demo_scenario_pack(args.verify_scenario_pack_from_demo, args.scenario_pack_dir)
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
