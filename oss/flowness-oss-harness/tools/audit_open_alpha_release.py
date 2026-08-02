from __future__ import annotations

import argparse
import json
from pathlib import Path

from flowness_oss_harness.open_alpha_package_scope import build_open_alpha_package_manifest
from flowness_oss_harness.open_alpha_release_audit import audit_open_alpha_release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the exact Flowness Open Alpha candidate scope.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--scope-policy", type=Path, required=True)
    parser.add_argument("--audit-policy", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--cleanroom-receipt", type=Path)
    parser.add_argument("--cleanroom-export-manifest", type=Path)
    parser.add_argument("--cleanroom-wheelhouse", type=Path)
    parser.add_argument("--cleanroom-dependency-report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest = build_open_alpha_package_manifest(repo=args.repo, policy_path=args.scope_policy)
    report = audit_open_alpha_release(
        repo=args.repo,
        package_manifest=manifest,
        policy_path=args.audit_policy,
        schema_path=args.schema,
        cleanroom_receipt_path=args.cleanroom_receipt,
        cleanroom_export_manifest_path=args.cleanroom_export_manifest,
        cleanroom_wheelhouse_path=args.cleanroom_wheelhouse,
        cleanroom_dependency_report_path=args.cleanroom_dependency_report,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
