from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from .approval import DEFAULT_TRUSTED_KEYS_PATH
from .assets import scaffold_assets
from .candidate import assemble_candidate
from .cleanroom_challenger import write_cleanroom_challenger_preflight
from .candidate_export_scope_registry import (
    verify_candidate_export_scope_registry,
    write_candidate_export_scope_registry,
)
from .coverage import write_coverage
from .coverage_inventory import write_coverage_inventory
from .control_ledger import admit_work, inspect_nonterminal_attempts, recover_attempt
from .evidence_pack import build_agent_evidence_pack, verify_agent_evidence_pack
from .execution_policy import require_command_execution_allowed
from .coverage_challenge import (
    create_coverage_compare_run,
    create_coverage_sample_manifest,
    create_coverage_trace_run,
    evaluate_coverage_challenge,
)
from .controller import (
    create_jury_run,
    create_run,
    initialize_workspace,
    run_wave,
    seal_candidate,
)
from .inventory import write_inventory
from .ledger_alpha_evaluation import (
    create_ledger_alpha_evaluation_handoff,
    run_ledger_alpha_evaluation,
)
from .open_alpha_demo import inspect_open_alpha_demo, run_open_alpha_demo
from .public_export import seal_public_export
from .public_package_preflight import write_public_package_preflight
from .public_package_manifest import (
    create_public_package_artifact_manifest,
    verify_public_package_artifact_manifest,
)
from .reconciliation import reconcile_excavation
from .registry_merge import merge_reconciled_registries
from .release import evaluate_release_files
from .registry import ValidationError
from .resources import CONFIG_ROOT as DEFAULT_CONFIG
from .resources import PACKAGE_ROOT, SCHEMAS_ROOT as DEFAULT_SCHEMAS
from .snapshot import seal_repository_snapshot
from .static_chain_catalog import write_static_chain_catalog
from .transcript import extract_transcripts, write_excerpts


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flowness-oss")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--workspace", required=True, type=_path)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--repo", required=True, type=_path)
    inventory.add_argument("--output", required=True, type=_path)

    snapshot = subparsers.add_parser("snapshot-seal")
    snapshot.add_argument("--repo", required=True, type=_path)
    snapshot.add_argument("--output", required=True, type=_path)

    export = subparsers.add_parser("export-seal")
    export.add_argument("--source-repo", required=True, type=_path)
    export.add_argument("--source-ref", required=True)
    export.add_argument("--allowlist", required=True, type=_path)
    export.add_argument("--target", required=True, type=_path)

    evidence_build = subparsers.add_parser("evidence-pack-build")
    evidence_build.add_argument("--input", required=True, type=_path)
    evidence_build.add_argument("--mechanism-registry", required=True, type=_path)
    evidence_build.add_argument("--target", required=True, type=_path)

    evidence_verify = subparsers.add_parser("evidence-pack-verify")
    evidence_verify.add_argument("--target", required=True, type=_path)
    evidence_verify.add_argument("--mechanism-registry", required=True, type=_path)

    coverage = subparsers.add_parser("coverage")
    coverage.add_argument("--inventory", required=True, type=_path)
    coverage.add_argument("--mechanisms", required=True, type=_path)
    coverage.add_argument("--output", required=True, type=_path)

    coverage_inventory = subparsers.add_parser("coverage-inventory-build")
    coverage_inventory.add_argument("--root", required=True, type=_path)
    coverage_inventory.add_argument("--scope-config", required=True, type=_path)
    coverage_inventory.add_argument("--seed", required=True, type=_path)
    coverage_inventory.add_argument("--output", required=True, type=_path)

    static_catalog = subparsers.add_parser("static-chain-catalog")
    static_catalog.add_argument("--seed", required=True, type=_path)
    static_catalog.add_argument("--manifest", action="append", required=True, type=_path)
    static_catalog.add_argument("--source-root", required=True, type=_path)
    static_catalog.add_argument("--output", required=True, type=_path)

    coverage_sample = subparsers.add_parser("coverage-sample-create")
    coverage_sample.add_argument("--source-snapshot", required=True, type=_path)
    coverage_sample.add_argument("--inventory", required=True, type=_path)
    coverage_sample.add_argument("--merge-report", required=True, type=_path)
    coverage_sample.add_argument("--mechanisms", required=True, type=_path)
    coverage_sample.add_argument("--unknowns", required=True, type=_path)
    coverage_sample.add_argument("--drift", required=True, type=_path)
    coverage_sample.add_argument("--roles", required=True, type=_path)
    coverage_sample.add_argument("--role-id", required=True)
    coverage_sample.add_argument("--agent-instance-id", required=True)
    coverage_sample.add_argument("--sample-size", type=int, default=20)
    coverage_sample.add_argument("--output", required=True, type=_path)

    trace_run = subparsers.add_parser("coverage-trace-run-create")
    trace_run.add_argument("--workspace", required=True, type=_path)
    trace_run.add_argument("--run-id", required=True)
    trace_run.add_argument("--source-snapshot", required=True, type=_path)
    trace_run.add_argument("--inventory", required=True, type=_path)
    trace_run.add_argument("--sample", required=True, type=_path)
    trace_run.add_argument("--roles", required=True, type=_path)

    compare_run = subparsers.add_parser("coverage-compare-run-create")
    compare_run.add_argument("--workspace", required=True, type=_path)
    compare_run.add_argument("--run-id", required=True)
    compare_run.add_argument("--source-snapshot", required=True, type=_path)
    compare_run.add_argument("--inventory", required=True, type=_path)
    compare_run.add_argument("--sample", action="append", required=True, type=_path)
    compare_run.add_argument("--trace", action="append", required=True, type=_path)
    compare_run.add_argument("--merge-report", required=True, type=_path)
    compare_run.add_argument("--mechanisms", required=True, type=_path)
    compare_run.add_argument("--unknowns", required=True, type=_path)
    compare_run.add_argument("--drift", required=True, type=_path)
    compare_run.add_argument("--roles", required=True, type=_path)
    compare_run.add_argument("--role-id", required=True)

    coverage_evaluate = subparsers.add_parser("coverage-challenge-evaluate")
    coverage_evaluate.add_argument("--source-snapshot", required=True, type=_path)
    coverage_evaluate.add_argument("--inventory", required=True, type=_path)
    coverage_evaluate.add_argument(
        "--sample", action="append", required=True, type=_path
    )
    coverage_evaluate.add_argument(
        "--trace", action="append", required=True, type=_path
    )
    coverage_evaluate.add_argument("--merge-report", required=True, type=_path)
    coverage_evaluate.add_argument("--mechanisms", required=True, type=_path)
    coverage_evaluate.add_argument("--unknowns", required=True, type=_path)
    coverage_evaluate.add_argument("--drift", required=True, type=_path)
    coverage_evaluate.add_argument("--roles", required=True, type=_path)
    coverage_evaluate.add_argument("--role-id", required=True)
    coverage_evaluate.add_argument("--agent-instance-id", required=True)
    coverage_evaluate.add_argument("--output", required=True, type=_path)

    assets = subparsers.add_parser("assets-scaffold")
    assets.add_argument("--workspace", required=True, type=_path)

    transcript = subparsers.add_parser("transcript-extract")
    transcript.add_argument("--projects-root", required=True, type=_path)
    transcript.add_argument("--namespace", required=True)
    transcript.add_argument("--snapshot-id", default="local-transcript-snapshot")
    transcript.add_argument("--include-probable", action="store_true")
    transcript.add_argument("--output", required=True, type=_path)

    run_create = subparsers.add_parser("run-create")
    run_create.add_argument("--workspace", required=True, type=_path)
    run_create.add_argument("--run-id", required=True)
    run_create.add_argument("--snapshot", action="append", required=True, type=_path)
    run_create.add_argument(
        "--roles", type=_path, default=DEFAULT_CONFIG / "roles.json"
    )

    jury_create = subparsers.add_parser("jury-run-create")
    jury_create.add_argument("--workspace", required=True, type=_path)
    jury_create.add_argument("--run-id", required=True)
    jury_create.add_argument("--candidate", required=True, type=_path)
    jury_create.add_argument(
        "--roles",
        type=_path,
        default=DEFAULT_CONFIG / "roles.json",
    )

    wave = subparsers.add_parser("run-wave")
    wave.add_argument("--workspace", required=True, type=_path)
    wave.add_argument("--run-id", required=True)
    wave.add_argument("--wave", required=True)
    wave.add_argument("--roles", type=_path, default=DEFAULT_CONFIG / "roles.json")
    wave.add_argument("--schemas-root", type=_path, default=DEFAULT_SCHEMAS)
    wave.add_argument("--codex-bin", default="codex")
    wave.add_argument("--dry-run", action="store_true")
    wave.add_argument("--max-parallel", type=int, default=1)
    wave.add_argument(
        "--launcher-command",
        help="External host wrapper command parsed without a shell",
    )
    wave.add_argument(
        "--admission-id",
        action="append",
        help="One previously admitted permit per role in this wave",
    )

    admit = subparsers.add_parser("admit-work")
    admit.add_argument("--workspace", required=True, type=_path)
    admit.add_argument("--card", required=True, type=_path)

    attempt_inspect = subparsers.add_parser("attempt-inspect")
    attempt_inspect.add_argument("--workspace", required=True, type=_path)

    attempt_recover = subparsers.add_parser("attempt-recover")
    attempt_recover.add_argument("--workspace", required=True, type=_path)
    attempt_recover.add_argument("--admission-id", required=True)
    attempt_recover.add_argument("--started-event-hash")
    attempt_recover.add_argument("--reason", required=True)
    attempt_recover.add_argument("--evidence", required=True, type=_path)
    attempt_recover.add_argument("--outcome", choices=("failed", "stopped"), default="failed")

    seal = subparsers.add_parser("candidate-seal")
    seal.add_argument("--run-root", required=True, type=_path)
    seal.add_argument(
        "--target-stage",
        required=True,
        choices=("alpha", "beta", "1.0"),
    )

    assemble = subparsers.add_parser("candidate-assemble")
    assemble.add_argument("--sealed-manifest", required=True, type=_path)
    assemble.add_argument("--modules-registry", required=True, type=_path)
    assemble.add_argument("--claims-registry", required=True, type=_path)
    assemble.add_argument("--benchmarks", required=True, type=_path)
    assemble.add_argument("--evidence", required=True, type=_path)
    assemble.add_argument("--output", required=True, type=_path)

    reconcile = subparsers.add_parser("reconcile-excavation")
    reconcile.add_argument("--run", required=True, type=_path)
    reconcile.add_argument("--result", action="append", required=True, type=_path)
    reconcile.add_argument("--source", required=True, type=_path)
    reconcile.add_argument(
        "--schema",
        type=_path,
        default=DEFAULT_SCHEMAS / "mechanism-batch.schema.json",
    )
    reconcile.add_argument("--output-dir", required=True, type=_path)

    merge_registries = subparsers.add_parser("merge-reconciled-registries")
    merge_registries.add_argument(
        "--group",
        action="append",
        nargs=4,
        required=True,
        type=_path,
        metavar=("REPORT", "MECHANISMS", "UNKNOWNS", "DRIFT"),
    )
    merge_registries.add_argument("--output-dir", required=True, type=_path)

    release = subparsers.add_parser("release-evaluate")
    release.add_argument("--candidate", required=True, type=_path)
    release.add_argument("--report", action="append", default=[], type=_path)
    release.add_argument("--approval", type=_path)
    release.add_argument(
        "--trusted-owner-keys",
        type=_path,
        default=DEFAULT_TRUSTED_KEYS_PATH,
    )
    release.add_argument("--output", required=True, type=_path)

    public_package = subparsers.add_parser("public-package-preflight")
    public_package.add_argument("--candidate-package-root", required=True, type=_path)
    public_package.add_argument("--required-public-artifacts", required=True, type=_path)
    public_package.add_argument("--sealed-export-rights-evidence", type=_path)
    public_package.add_argument("--output", required=True, type=_path)

    public_package_seal = subparsers.add_parser("public-package-artifacts-seal")
    public_package_seal.add_argument("--candidate-assembly-dir", required=True, type=_path)
    public_package_seal.add_argument("--candidate-package-root", required=True, type=_path)
    public_package_seal.add_argument("--artifact-input", required=True, type=_path)
    public_package_seal.add_argument("--output", required=True, type=_path)

    public_package_verify = subparsers.add_parser("public-package-artifacts-verify")
    public_package_verify.add_argument("--candidate-assembly-dir", required=True, type=_path)
    public_package_verify.add_argument("--candidate-package-root", required=True, type=_path)
    public_package_verify.add_argument("--manifest", required=True, type=_path)

    export_scope_build = subparsers.add_parser("candidate-export-scope-registry-build")
    export_scope_build.add_argument("--repo", required=True, type=_path)
    export_scope_build.add_argument("--scope-root", required=True, type=_path)
    export_scope_build.add_argument("--output", required=True, type=_path)

    export_scope_verify = subparsers.add_parser("candidate-export-scope-registry-verify")
    export_scope_verify.add_argument("--repo", required=True, type=_path)
    export_scope_verify.add_argument("--scope-root", required=True, type=_path)
    export_scope_verify.add_argument("--registry", required=True, type=_path)

    ledger_eval_handoff = subparsers.add_parser("ledger-alpha-evaluation-handoff")
    ledger_eval_handoff.add_argument("--input", required=True, type=_path)
    ledger_eval_handoff.add_argument("--output", required=True, type=_path)

    ledger_eval_run = subparsers.add_parser("ledger-alpha-evaluation-run")
    ledger_eval_run.add_argument("--handoff", required=True, type=_path)
    ledger_eval_run.add_argument("--input", required=True, type=_path)
    ledger_eval_run.add_argument("--candidate-python", required=True, type=_path)
    ledger_eval_run.add_argument("--candidate-pythonpath", type=_path)
    ledger_eval_run.add_argument("--output", required=True, type=_path)

    cleanroom_challenger = subparsers.add_parser("cleanroom-challenger-preflight")
    cleanroom_challenger.add_argument("--input", required=True, type=_path)
    cleanroom_challenger.add_argument("--output", required=True, type=_path)

    open_alpha_demo = subparsers.add_parser("open-alpha-demo")
    open_alpha_demo.add_argument("--output", required=True, type=_path)
    open_alpha_demo.add_argument(
        "--runner", choices=("fixture", "codex"), default="fixture"
    )
    open_alpha_demo.add_argument("--codex-bin", default="codex")

    open_alpha_inspect = subparsers.add_parser("open-alpha-demo-inspect")
    open_alpha_inspect.add_argument("--run-root", required=True, type=_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        # Freeze blocks new execution, not a read-only inspection or a
        # evidence-bound terminalization of work that was already admitted.
        if args.command not in {
            "attempt-inspect",
            "attempt-recover",
            "open-alpha-demo",
            "open-alpha-demo-inspect",
        }:
            require_command_execution_allowed(args.command)
        if args.command == "init":
            initialize_workspace(args.workspace)
            print(args.workspace)
        elif args.command == "inventory":
            write_inventory(args.repo, args.output)
            print(args.output)
        elif args.command == "snapshot-seal":
            payload = seal_repository_snapshot(args.repo, args.output)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "export-seal":
            payload = seal_public_export(
                args.source_repo,
                args.source_ref,
                args.allowlist,
                args.target,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "evidence-pack-build":
            source = json.loads(args.input.read_text(encoding="utf-8"))
            if (
                not isinstance(source, dict)
                or set(source) != {"schema_version", "pack_id", "cutoff", "records"}
                or source.get("schema_version") != "agent-safe-evidence-input/v1"
                or not isinstance(source.get("records"), list)
            ):
                raise ValidationError("AGENT-PACK-INPUT-SHAPE-INVALID")
            mechanism_registry = json.loads(
                args.mechanism_registry.read_text(encoding="utf-8")
            )
            mechanisms = mechanism_registry.get("mechanisms", [])
            known_ids = {
                item.get("mechanism_id")
                for item in mechanisms
                if isinstance(item, dict) and isinstance(item.get("mechanism_id"), str)
            }
            manifest = build_agent_evidence_pack(
                source["records"],
                pack_id=source["pack_id"],
                cutoff=source["cutoff"],
                known_mechanism_ids=known_ids,
                target=args.target,
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif args.command == "evidence-pack-verify":
            mechanism_registry = json.loads(
                args.mechanism_registry.read_text(encoding="utf-8")
            )
            mechanisms = mechanism_registry.get("mechanisms", [])
            known_ids = {
                item.get("mechanism_id")
                for item in mechanisms
                if isinstance(item, dict) and isinstance(item.get("mechanism_id"), str)
            }
            manifest = verify_agent_evidence_pack(
                args.target, known_mechanism_ids=known_ids
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif args.command == "coverage":
            write_coverage(args.inventory, args.mechanisms, args.output)
            print(args.output)
        elif args.command == "coverage-inventory-build":
            payload = write_coverage_inventory(
                args.root, args.scope_config, args.seed, args.output
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "static-chain-catalog":
            payload = write_static_chain_catalog(
                args.seed, args.manifest, args.source_root, args.output
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "coverage-sample-create":
            manifest = create_coverage_sample_manifest(
                args.source_snapshot,
                args.inventory,
                args.merge_report,
                args.mechanisms,
                args.unknowns,
                args.drift,
                args.roles,
                args.role_id,
                args.agent_instance_id,
                args.output,
                args.sample_size,
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            if manifest["state"] == "blocked":
                return 2
        elif args.command == "coverage-trace-run-create":
            result = create_coverage_trace_run(
                args.workspace,
                args.run_id,
                args.source_snapshot,
                args.inventory,
                args.sample,
                args.roles,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "coverage-compare-run-create":
            result = create_coverage_compare_run(
                args.workspace,
                args.run_id,
                args.source_snapshot,
                args.inventory,
                args.sample,
                args.trace,
                args.merge_report,
                args.mechanisms,
                args.unknowns,
                args.drift,
                args.roles,
                args.role_id,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "coverage-challenge-evaluate":
            verdict = evaluate_coverage_challenge(
                args.source_snapshot,
                args.inventory,
                args.sample,
                args.trace,
                args.merge_report,
                args.mechanisms,
                args.unknowns,
                args.drift,
                args.roles,
                args.role_id,
                args.agent_instance_id,
                args.output,
            )
            print(json.dumps(verdict, ensure_ascii=False, indent=2))
            if verdict["state"] == "blocked":
                return 2
        elif args.command == "assets-scaffold":
            payload = scaffold_assets(args.workspace)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.command == "transcript-extract":
            excerpts = extract_transcripts(
                args.projects_root,
                args.namespace,
                args.snapshot_id,
                args.include_probable,
            )
            write_excerpts(args.output, excerpts)
            print(json.dumps({"output": str(args.output), "excerpts": len(excerpts)}))
        elif args.command == "run-create":
            path = create_run(args.workspace, args.run_id, args.snapshot, args.roles)
            print(path)
        elif args.command == "jury-run-create":
            path = create_jury_run(
                args.workspace,
                args.run_id,
                args.candidate,
                args.roles,
            )
            print(path)
        elif args.command == "run-wave":
            records = run_wave(
                args.workspace,
                args.run_id,
                args.wave,
                args.roles,
                args.schemas_root,
                args.codex_bin,
                args.dry_run,
                args.max_parallel,
                (
                    shlex.split(args.launcher_command)
                    if args.launcher_command is not None
                    else None
                ),
                args.admission_id,
            )
            print(json.dumps(records, ensure_ascii=False, indent=2))
            if any(
                record.get("state") == "failed"
                or record.get("returncode", 0) != 0
                for record in records
            ):
                return 2
        elif args.command == "admit-work":
            card = json.loads(args.card.read_text(encoding="utf-8"))
            result = admit_work(args.workspace, card)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "attempt-inspect":
            print(
                json.dumps(
                    inspect_nonterminal_attempts(args.workspace),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "attempt-recover":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            if not isinstance(evidence, list):
                raise ValidationError("ATTEMPT-RECOVERY-EVIDENCE-INVALID")
            result = recover_attempt(
                args.workspace,
                args.admission_id,
                started_event_hash=args.started_event_hash,
                reason_code=args.reason,
                evidence=evidence,
                outcome=args.outcome,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "candidate-seal":
            manifest = seal_candidate(args.run_root, args.target_stage)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        elif args.command == "candidate-assemble":
            candidate = assemble_candidate(
                args.sealed_manifest,
                args.modules_registry,
                args.claims_registry,
                args.benchmarks,
                args.evidence,
                args.output,
            )
            print(json.dumps(candidate, ensure_ascii=False, indent=2))
        elif args.command == "reconcile-excavation":
            report = reconcile_excavation(
                args.run,
                args.result,
                args.source,
                args.schema,
                args.output_dir,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report["state"] == "blocked":
                return 2
        elif args.command == "merge-reconciled-registries":
            report = merge_reconciled_registries(
                [tuple(group) for group in args.group],
                args.output_dir,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if report["state"] == "blocked":
                return 2
        elif args.command == "release-evaluate":
            result = evaluate_release_files(
                args.candidate,
                args.report,
                args.output,
                args.approval,
                args.trusted_owner_keys,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "public-package-preflight":
            artifacts = json.loads(args.required_public_artifacts.read_text(encoding="utf-8"))
            rights = (
                json.loads(args.sealed_export_rights_evidence.read_text(encoding="utf-8"))
                if args.sealed_export_rights_evidence is not None
                else None
            )
            result = write_public_package_preflight(
                args.candidate_package_root,
                artifacts,
                args.output,
                rights,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "public-package-artifacts-seal":
            result = create_public_package_artifact_manifest(
                candidate_assembly_dir=args.candidate_assembly_dir,
                candidate_package_root=args.candidate_package_root,
                artifact_input_path=args.artifact_input,
                output=args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "public-package-artifacts-verify":
            result = verify_public_package_artifact_manifest(
                candidate_assembly_dir=args.candidate_assembly_dir,
                candidate_package_root=args.candidate_package_root,
                manifest_path=args.manifest,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "candidate-export-scope-registry-build":
            result = write_candidate_export_scope_registry(
                repo=args.repo,
                scope_root=args.scope_root,
                output=args.output,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "candidate-export-scope-registry-verify":
            result = verify_candidate_export_scope_registry(
                repo=args.repo,
                scope_root=args.scope_root,
                registry_path=args.registry,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "ledger-alpha-evaluation-handoff":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = create_ledger_alpha_evaluation_handoff(payload, args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "ledger-alpha-evaluation-run":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = run_ledger_alpha_evaluation(
                args.handoff,
                payload,
                args.candidate_python,
                args.output,
                candidate_pythonpath=args.candidate_pythonpath,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "cleanroom-challenger-preflight":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = write_cleanroom_challenger_preflight(payload, args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "open-alpha-demo":
            result = run_open_alpha_demo(
                args.output,
                runner=args.runner,
                codex_bin=args.codex_bin,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "open-alpha-demo-inspect":
            result = inspect_open_alpha_demo(args.run_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    print(
        "Open Alpha boundary: use `flowness-oss commands`; legacy orchestration commands are not public APIs.",
        file=sys.stderr,
    )
    raise SystemExit(2)
