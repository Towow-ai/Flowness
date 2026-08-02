from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .integrity import canonical_hash, verify_self_hash
from .control_ledger import (
    claim_attempt,
    initialize_control_ledger,
    load_admitted_work,
    settle_attempt,
)
from .content_roles import validate_content_role_contract
from .execution_policy import require_agent_execution_allowed
from .models import utc_now
from .policy import load_approved_policy
from .registry import ValidationError, atomic_create_json, atomic_write_json
from .schema_validation import (
    load_validated_json,
    validate_openai_response_format_schema,
    validate_payload,
)

SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_ROLE_KINDS = {"producer", "judge"}
ALLOWED_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}

COVERAGE_PROMPT_TEMPLATES = {
    "challenge.coverage": (
        "Object-first blind trace. Inspect only the sealed source archive and the "
        "bound sample manifest. Return sparse trace rows only for samples whose "
        "inspection you actually completed, with exact candidate source "
        "references for definitions, callers/producers, consumers, failure paths, "
        "and recovery paths. Exact excerpts prove only that text was read, not the "
        "semantic relationship among those references. Use candidate_source_chain "
        "only when all five groups are present. You may omit samples you could not "
        "trace; the controller will mark only those omitted sealed samples unresolved. "
        "Do not spend work computing trace_hash: if the schema requires it, use a "
        "sha256 zero placeholder because the controller seals the authoritative hash. "
        "Never inspect or infer registry content."
    ),
    "challenge.consumer-chain": (
        "Failure-consumer blind trace. Inspect only the sealed source archive and "
        "the bound sample manifest. Copy all sealed binding fields and hashes from "
        "that manifest into the matching top-level output fields exactly; do not "
        "replace or recompute any of them. Only trace_hash may use a schema-valid "
        "sha256 zero placeholder before controller normalization. Return sparse "
        "trace rows only for samples whose "
        "inspection you actually completed, with exact candidate source "
        "references for definitions, callers/producers, consumers, failure paths, "
        "and recovery paths. Exact excerpts prove only that text was read, not the "
        "semantic relationship among those references. Use candidate_source_chain "
        "only when all five groups are present. You may omit samples you could not "
        "trace; the controller will mark only those omitted sealed samples unresolved. "
        "Do not spend work computing trace_hash because the controller seals the "
        "authoritative hash. "
        "Never inspect or infer registry content."
    ),
    "challenge.coverage-comparator": (
        "Compare the sealed blind traces with the sealed combined registries. "
        "Treat exact five-part source evidence as candidate_mapped at most. Never "
        "emit source_chain_verified or runtime_verified under this protocol."
    ),
    "challenge.coverage-adjudicator": (
        "Adjudicate only conflicts present in the sealed sample manifests, traces, "
        "and registries. Do not add unsampled claims, promote mechanism status, or "
        "treat exact excerpts as semantic chain verification."
    ),
}
COVERAGE_TRACE_ROLES = {"challenge.coverage", "challenge.consumer-chain"}
COVERAGE_VERDICT_ROLES = {
    "challenge.coverage-comparator",
    "challenge.coverage-adjudicator",
}
COVERAGE_OUTPUT_SCHEMAS = {
    "coverage-trace.schema.json",
    "coverage-verdict.schema.json",
}
COVERAGE_UNRESOLVED_NOTE = (
    "Controller normalization: the model omitted this sealed sample; no candidate "
    "source chain was returned."
)


def coverage_prompt_template_hash(role_id: str) -> str:
    try:
        template = COVERAGE_PROMPT_TEMPLATES[role_id]
    except KeyError as exc:
        raise ValidationError(f"no trusted coverage prompt for role: {role_id}") from exc
    return "sha256:" + hashlib.sha256(template.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentRole:
    role_id: str
    wave: str
    kind: str
    dimension: str
    mission: str
    output_schema: str
    sandbox: str = "danger-full-access"
    checks: tuple[str, ...] = ()
    content_contract: dict[str, Any] | None = None


def _load_roles(config_path: Path) -> list[AgentRole]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), list):
        raise ValidationError("roles config must contain a roles array")
    try:
        roles = [
            AgentRole(
                **{
                    **{key: value for key, value in item.items() if key != "content_contract"},
                    "checks": tuple(item.get("checks", [])),
                    "content_contract": item.get("content_contract"),
                }
            )
            for item in payload["roles"]
        ]
    except (TypeError, AttributeError) as exc:
        raise ValidationError(f"invalid role entry: {exc}") from exc
    seen: set[str] = set()
    for role in roles:
        if not SAFE_COMPONENT.fullmatch(role.role_id):
            raise ValidationError(f"unsafe role_id: {role.role_id!r}")
        if role.role_id in seen:
            raise ValidationError(f"duplicate role_id: {role.role_id}")
        seen.add(role.role_id)
        if not SAFE_COMPONENT.fullmatch(role.wave):
            raise ValidationError(f"unsafe wave: {role.wave!r}")
        if role.kind not in ALLOWED_ROLE_KINDS:
            raise ValidationError(f"unsupported role kind: {role.kind}")
        if role.sandbox not in ALLOWED_SANDBOXES:
            raise ValidationError(f"unsupported sandbox: {role.sandbox}")
        if not role.output_schema or Path(role.output_schema).is_absolute():
            raise ValidationError(f"unsafe output schema: {role.output_schema!r}")
        if role.kind == "judge" and role.output_schema != "jury-report.schema.json":
            raise ValidationError(
                f"judge role must emit jury-report.schema.json: {role.role_id}"
            )
        if role.kind == "judge" and (
            not role.checks
            or len(set(role.checks)) != len(role.checks)
            or any(
                not re.fullmatch(r"G[0-5]\.[A-Za-z0-9._-]+", check_id)
                for check_id in role.checks
            )
        ):
            raise ValidationError(
                f"judge role must declare unique authorized checks: {role.role_id}"
            )
        validate_content_role_contract(
            role_id=role.role_id,
            kind=role.kind,
            output_schema=role.output_schema,
            contract=role.content_contract,
        )
    return roles


def initialize_workspace(workspace: Path) -> None:
    for relative in (
        "runs",
        "snapshots",
        "registries",
        "candidates",
        "channel-staging",
    ):
        (workspace / relative).mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        workspace / "workspace.json",
        {
            "schema_version": "oss-workspace/v1",
            "created_at": utc_now(),
            "truth_boundary": (
                "This workspace is independent from .towow and may only consume "
                "sealed snapshots."
            ),
        },
    )
    initialize_control_ledger(workspace)


def _hash_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"expected regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seal_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in payload.items() if key != "run_hash"}
    return {**unsigned, "run_hash": canonical_hash(unsigned)}


def _verify_run_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "oss-run/v1":
        raise ValidationError("unsupported run manifest schema_version")
    verify_self_hash(payload, "run_hash")


def _load_run_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load run manifest: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValidationError("run manifest must be a JSON object")
    _verify_run_payload(payload)
    return payload


def _write_run_manifest(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, _seal_run_payload(payload))


def create_run(
    workspace: Path, run_id: str, snapshots: list[Path], roles_path: Path
) -> Path:
    if not SAFE_COMPONENT.fullmatch(run_id):
        raise ValidationError(f"unsafe run_id: {run_id!r}")
    if not snapshots:
        raise ValidationError("at least one snapshot is required")
    workspace = workspace.resolve()
    run_root = workspace / "runs" / run_id
    if run_root.exists():
        raise ValidationError(f"run already exists: {run_id}")
    roles_path = roles_path.resolve()
    _load_roles(roles_path)
    snapshot_records = []
    seen_snapshots: set[Path] = set()
    for path in snapshots:
        resolved = path.resolve()
        if resolved in seen_snapshots:
            raise ValidationError(f"duplicate snapshot: {resolved}")
        seen_snapshots.add(resolved)
        snapshot_records.append(
            {"path": str(resolved), "sha256": _hash_file(resolved)}
        )
    for relative in ("controller", "producers", "judges", "candidate", "logs"):
        (run_root / relative).mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "oss-run/v1",
        "run_id": run_id,
        "created_at": utc_now(),
        "state": "created",
        "snapshots": snapshot_records,
        "roles_config": {
            "path": str(roles_path.resolve()),
            "sha256": _hash_file(roles_path),
        },
    }
    _write_run_manifest(run_root / "run.json", manifest)
    return run_root


def seal_candidate(
    run_root: Path,
    target_stage: str,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    manifest_path = run_root / "candidate" / "assembly-manifest.json"
    if manifest_path.exists():
        raise ValidationError("candidate is already sealed")
    if target_stage not in {"alpha", "beta", "1.0"}:
        raise ValidationError("target_stage must be alpha, beta, or 1.0")
    run_payload = _load_run_manifest(run_root / "run.json")
    snapshots = run_payload.get("snapshots", [])
    if len(snapshots) != 1:
        raise ValidationError(
            "candidate assembly requires exactly one sealed repository snapshot"
        )
    snapshot_record = snapshots[0]
    snapshot_path = Path(snapshot_record["path"])
    if _hash_file(snapshot_path) != snapshot_record.get("sha256"):
        raise ValidationError("sealed repository snapshot changed")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if (
        snapshot.get("dirty") is not False
        or snapshot.get("candidate_assembly_eligible") is not True
    ):
        raise ValidationError("snapshot is not eligible to seed candidate assembly")
    repository = snapshot.get("repository")
    if not isinstance(repository, str) or not repository:
        raise ValidationError("snapshot repository is required")
    repo_uri = (
        repository
        if urlparse(repository).scheme
        else Path(repository).resolve().as_uri()
    )
    unsigned = {
        "schema_version": "candidate-assembly-manifest/v1",
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "repo": repo_uri,
            "commit_sha": snapshot.get("commit_sha"),
            "tree_sha": snapshot.get("tree_sha"),
            "dirty": False,
            "built_at": snapshot.get("captured_at"),
        },
        "target_stage": target_stage,
        "created_at": snapshot.get("captured_at"),
    }
    manifest = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    atomic_create_json(manifest_path, manifest)
    return manifest


def _jury_identity(run_id: str, role_id: str, candidate_id: str) -> dict[str, str]:
    material = f"{run_id}\0{role_id}\0{candidate_id}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return {
        "agent_instance_id": f"agent-{digest[:24]}",
        "report_id": f"report-{digest[24:48]}",
    }


def create_jury_run(
    workspace: Path,
    run_id: str,
    candidate_path: Path,
    roles_path: Path,
) -> Path:
    from .candidate import DEFAULT_CANDIDATE_SCHEMA

    candidate_path = candidate_path.resolve()
    candidate = load_validated_json(
        candidate_path,
        DEFAULT_CANDIDATE_SCHEMA,
        "release candidate",
    )
    policy, policy_hash = load_approved_policy()
    policy_roles = {
        item["role_id"]: item
        for item in policy.get("roles", [])
        if item.get("kind") == "judge"
    }
    for role in _load_roles(roles_path.resolve()):
        if role.kind != "judge":
            continue
        policy_role = policy_roles.get(role.role_id)
        if (
            policy_role is None
            or role.dimension not in policy_role.get("dimensions", [])
            or set(role.checks) != set(policy_role.get("checks", []))
            or role.wave != "jury"
        ):
            raise ValidationError(
                f"jury role differs from approved policy: {role.role_id}"
            )
    run_root = create_run(
        workspace,
        run_id,
        [candidate_path],
        roles_path,
    )
    run_payload = _load_run_manifest(run_root / "run.json")
    run_payload["state"] = "jury-created"
    run_payload["jury"] = {
        "candidate": {
            "path": str(candidate_path),
            "sha256": _hash_file(candidate_path),
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
        },
        "policy": {
            "policy_version": policy["policy_version"],
            "policy_hash": policy_hash,
        },
        "report_identity_rule": (
            "agent/report ids are deterministic sha256(run_id, role_id, "
            "candidate_id) projections and are bound in each prompt"
        ),
    }
    _write_run_manifest(run_root / "run.json", run_payload)
    return run_root


def _prompt_for(
    role: AgentRole,
    run_payload: dict[str, Any],
    jury_identity: dict[str, str] | None = None,
) -> str:
    snapshots = "\n".join(
        f"- {item['path']} (sha256={item['sha256']})"
        for item in run_payload["snapshots"]
    )
    coverage_phase = run_payload.get("coverage_phase")
    trusted_mission = role.mission
    coverage_binding = ""
    if coverage_phase is not None:
        if not isinstance(coverage_phase, dict):
            raise ValidationError("coverage phase metadata must be an object")
        expected_template_hash = coverage_prompt_template_hash(role.role_id)
        if coverage_phase.get("prompt_template_hash") != expected_template_hash:
            raise ValidationError("coverage prompt template binding differs")
        trusted_mission = COVERAGE_PROMPT_TEMPLATES[role.role_id]
        bindings = coverage_phase.get("deterministic_bindings")
        if not isinstance(bindings, dict):
            raise ValidationError("coverage deterministic bindings are missing")
        coverage_binding = (
            "\nSealed coverage bindings:\n"
            + json.dumps(bindings, ensure_ascii=False, sort_keys=True)
            + "\n"
        )
    blindness = (
        "Do not inspect other producer or judge outputs. Judge the sealed inputs "
        "only." if role.kind == "judge" else
        "Do not inspect judge outputs or other producer drafts."
    )
    authorized_checks = (
        "\n".join(f"- {check_id}" for check_id in role.checks)
        if role.kind == "judge"
        else "- Not applicable to producer roles"
    )
    jury_binding = ""
    if role.kind == "judge":
        jury = run_payload.get("jury")
        if not jury or jury_identity is None:
            raise ValidationError("judge wave requires sealed jury run metadata")
        jury_binding = f"""
Sealed jury binding:
- candidate_id: {jury['candidate']['candidate_id']}
- snapshot_id: {jury['candidate']['snapshot_id']}
- policy_version: {jury['policy']['policy_version']}
- policy_hash: {jury['policy']['policy_hash']}
- agent_instance_id: {jury_identity['agent_instance_id']}
- report_id: {jury_identity['report_id']}
- signature: sha256 canonical JSON of the report excluding signature
"""
    excavation_rules = ""
    if (
        role.kind == "producer"
        and role.output_schema == "mechanism-batch.schema.json"
    ):
        excavation_rules = """
Mechanism excavation procedure (mandatory):
- Treat each repository inventory as a discovery map only. It may point you to
  candidates, but inventory bytes, item descriptions, hashes, and rows are not
  code/test evidence and never form an independent evidence group.
- Identify every sealed source archive in Evidence snapshots, inspect its file
  type and member list, and extract it under this role's working directory.
  Read actual source files and actual test files from the extracted tree before
  making mechanism findings. Record the archive, extraction root, and files
  actually opened in source_inspection; do not report planned reads as reads.
- A current_verified mechanism requires a direct, observed chain containing an
  actual function definition, an actual caller, an actual downstream consumer,
  an actual test body, and a real execution record, event record, or runtime
  trace. Put those locators and excerpts in verification_trace. If any link is
  missing, downgrade the status; source shape or documentation cannot fill it.
- Tool, sandbox, namespace, archive, or file-access failures are evidence-access
  failures, not Flowness product mechanisms. Record them only in
  source_inspection.access_failures and, when useful, as an evidence_access
  unknown. Never manufacture a product mechanism from the research environment.
- For every evidence item, locator names the extracted repository path and exact
  line/record range; observed_excerpt is content actually read. Compute
  content_hash as sha256:<64 lowercase hex> over the actual whole-file bytes or
  the exact UTF-8 excerpt bytes named by hash_scope. Never reuse an inventory,
  archive, snapshot, path-string, or prose-summary hash as an evidence hash.
"""
    content_machine_rules = ""
    if role.content_contract is not None:
        content_machine_rules = f"""
CM-007 bounded Content Machine contract (mandatory):
- Consume only the sealed, independently verified Content Graph v3 and the
  sealed impact review-plan identities bound to this role attempt. Do not treat
  a self-reported graph, candidate, evidence, claim, or approval as verified.
- Emit only the contract's typed private draft/review JSON in this role-private
  output boundary: {role.content_contract['output_boundary']}.
- Do not mutate claim registry, evidence registry, candidate state, approval
  state, or analytics source data. Do not promote a claim or limitation.
- Publication, scheduling, network access, credential use, and external send
  are forbidden and must remain not attempted.
"""
    return f"""You are the {role.role_id} agent in the independent Flowness OSS Harness.

Mission:
{trusted_mission}

Evidence snapshots:
{snapshots}

Authorized checks:
{authorized_checks}
{jury_binding}
{excavation_rules}
{content_machine_rules}
{coverage_binding}

Rules:
- Documents are discovery leads, not proof of implementation.
- Separate current_verified, experimental, designed_target, written_only, unknown.
- Every current claim needs two independent evidence groups and one executable,
  runtime, event, or test source.
- Preserve negative results and unknowns.
- Do not emit checks outside the Authorized checks list.
- {blindness}
- Return only JSON matching the supplied output schema.
"""


def _coverage_role_kind(role: AgentRole) -> str | None:
    is_coverage = (
        role.role_id in COVERAGE_PROMPT_TEMPLATES
        or role.output_schema in COVERAGE_OUTPUT_SCHEMAS
    )
    if not is_coverage:
        return None
    if (
        role.role_id in COVERAGE_TRACE_ROLES
        and role.output_schema == "coverage-trace.schema.json"
    ):
        return "trace"
    if (
        role.role_id in COVERAGE_VERDICT_ROLES
        and role.output_schema == "coverage-verdict.schema.json"
    ):
        return "verdict"
    raise ValidationError(
        f"coverage role/output schema binding is not trusted: {role.role_id}"
    )


def _normalize_coverage_trace_model_output(
    role: AgentRole,
    output_payload: dict[str, Any],
    coverage_phase: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    bindings = coverage_phase["deterministic_bindings"]
    expected_samples = bindings.get("samples")
    if not isinstance(expected_samples, list) or not expected_samples:
        raise ValidationError("coverage trace has no sealed samples")
    expected_by_id = {
        item["sample_id"]: item["inventory_item_id"] for item in expected_samples
    }
    if len(expected_by_id) != len(expected_samples):
        raise ValidationError("coverage trace sealed sample ids are not unique")
    immutable_bindings = {
        "role_id": role.role_id,
        "agent_instance_id": bindings.get("agent_instance_id"),
        "manifest_hash": bindings.get("manifest_hash"),
        "algorithm_version": bindings.get("algorithm_version"),
        "seed": bindings.get("seed"),
        "source_snapshot_hash": bindings.get("source_snapshot_hash"),
        "roles_file_sha256": bindings.get("roles_file_sha256"),
        "prompt_template_hash": coverage_phase.get("prompt_template_hash"),
    }
    if any(
        output_payload.get(field) != expected
        for field, expected in immutable_bindings.items()
    ):
        raise ValidationError(
            "coverage trace identity, source, seed, role, or prompt binding differs"
        )

    returned_by_id: dict[str, dict[str, Any]] = {}
    for item in output_payload.get("traces", []):
        sample_id = item.get("sample_id")
        inventory_item_id = item.get("inventory_item_id")
        if sample_id in returned_by_id:
            raise ValidationError(f"coverage trace duplicate sample: {sample_id}")
        if sample_id not in expected_by_id:
            raise ValidationError(f"coverage trace unknown sample: {sample_id}")
        if inventory_item_id != expected_by_id[sample_id]:
            raise ValidationError(
                f"coverage trace inventory item differs for sample: {sample_id}"
            )
        returned_by_id[sample_id] = item

    filled = 0
    normalized_traces: list[dict[str, Any]] = []
    for sample in expected_samples:
        sample_id = sample["sample_id"]
        returned = returned_by_id.get(sample_id)
        if returned is not None:
            normalized_traces.append(returned)
            continue
        filled += 1
        normalized_traces.append(
            {
                "sample_id": sample_id,
                "inventory_item_id": sample["inventory_item_id"],
                "source_chain": {
                    "definitions": [],
                    "producers_or_callers": [],
                    "consumers": [],
                    "failure_paths": [],
                    "recovery_paths": [],
                },
                "runtime_evidence_ids": [],
                "assessment": "unresolved",
                "notes": [COVERAGE_UNRESOLVED_NOTE],
            }
        )
    unsigned = {
        key: value for key, value in output_payload.items() if key != "trace_hash"
    }
    unsigned["traces"] = normalized_traces
    normalized = {**unsigned, "trace_hash": canonical_hash(unsigned)}
    return normalized, filled


def _validate_coverage_model_output(
    role: AgentRole,
    output_payload: dict[str, Any],
    coverage_phase: dict[str, Any],
) -> None:
    kind = _coverage_role_kind(role)
    if kind is None:
        return
    if kind == "trace":
        raise ValidationError("coverage trace must use controller normalization")

    bindings = coverage_phase["deterministic_bindings"]
    verify_self_hash(output_payload, "verdict_hash")
    rows = output_payload.get("rows", [])
    counts = output_payload.get("counts", {})
    output_bindings = output_payload.get("bindings", {})
    blocker_codes = {
        blocker.get("code")
        for blocker in output_payload.get("blockers", [])
        if isinstance(blocker, dict)
    }
    state_counts = sum(
        counts.get(state, -1)
        for state in (
            "unmapped",
            "declared_only",
            "candidate_mapped",
            "source_chain_verified",
            "runtime_verified",
        )
    )
    expected_output_bindings = bindings.get("output_bindings")
    if (
        not isinstance(expected_output_bindings, dict)
        or not rows
        or counts.get("samples") != len(rows)
        or state_counts != len(rows)
        or counts.get("blockers") != len(output_payload.get("blockers", []))
        or counts.get("source_chain_verified") != 0
        or counts.get("runtime_verified") != 0
        or "SEALED-SOURCE-LINK-PROTOCOL-UNAVAILABLE" not in blocker_codes
        or output_payload.get("agent", {}).get("role_id") != role.role_id
        or output_payload.get("agent", {}).get("agent_instance_id")
        != bindings.get("agent_instance_id")
        or output_bindings != expected_output_bindings
    ):
        raise ValidationError("coverage verdict output violates sealed bindings")


def run_wave(
    workspace: Path,
    run_id: str,
    wave: str,
    roles_path: Path,
    schemas_root: Path,
    codex_bin: str = "codex",
    dry_run: bool = False,
    max_parallel: int = 1,
    launcher_command: list[str] | None = None,
    admission_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    execution_policy, execution_policy_hash = require_agent_execution_allowed("run-wave")
    if not SAFE_COMPONENT.fullmatch(run_id):
        raise ValidationError(f"unsafe run_id: {run_id!r}")
    if not SAFE_COMPONENT.fullmatch(wave):
        raise ValidationError(f"unsafe wave: {wave!r}")
    workspace = workspace.resolve()
    runs_root = (workspace / "runs").resolve()
    unresolved_run_root = runs_root / run_id
    if unresolved_run_root.is_symlink():
        raise ValidationError("run root cannot be a symlink")
    run_root = unresolved_run_root.resolve()
    if runs_root not in run_root.parents:
        raise ValidationError("run root escapes workspace")
    run_payload = _load_run_manifest(run_root / "run.json")
    if run_payload.get("run_id") != run_id:
        raise ValidationError("run manifest id does not match requested run")
    roles_path = roles_path.resolve()
    expected_roles = run_payload.get("roles_config", {})
    if (
        expected_roles.get("path") != str(roles_path)
        or expected_roles.get("sha256") != _hash_file(roles_path)
    ):
        raise ValidationError("roles config differs from the sealed run manifest")
    for snapshot in run_payload.get("snapshots", []):
        snapshot_path = Path(snapshot.get("path", "")).resolve()
        if (
            not snapshot.get("sha256")
            or _hash_file(snapshot_path) != snapshot["sha256"]
        ):
            raise ValidationError(f"sealed snapshot changed: {snapshot_path}")
    roles = [role for role in _load_roles(roles_path) if role.wave == wave]
    if not roles:
        raise ValidationError(f"no roles registered for wave {wave}")
    if max_parallel < 1 or max_parallel > 8:
        raise ValidationError("max_parallel must be between 1 and 8")
    if launcher_command is not None and (
        not launcher_command
        or any(
            not isinstance(item, str) or not item or "\x00" in item
            for item in launcher_command
        )
    ):
        raise ValidationError("launcher_command must be a non-empty argument list")
    coverage_kinds = [_coverage_role_kind(role) for role in roles]
    coverage_roles = [
        (role, kind)
        for role, kind in zip(roles, coverage_kinds, strict=True)
        if kind is not None
    ]
    coverage_phase = run_payload.get("coverage_phase")
    if coverage_roles:
        if len(roles) != 1 or len(coverage_roles) != 1:
            raise ValidationError("coverage waves require one sealed role per run")
        role, coverage_kind = coverage_roles[0]
        expected_phase = (
            "blind_source_trace" if coverage_kind == "trace" else "registry_comparison"
        )
        if (
            not isinstance(coverage_phase, dict)
            or coverage_phase.get("phase") != expected_phase
            or coverage_phase.get("role_id") != role.role_id
            or coverage_phase.get("prompt_source") != "trusted_package_template"
            or coverage_phase.get("prompt_template_hash")
            != coverage_prompt_template_hash(role.role_id)
            or not isinstance(coverage_phase.get("deterministic_bindings"), dict)
        ):
            raise ValidationError("coverage phase/trusted prompt binding is missing")
        if role.sandbox != "danger-full-access":
            raise ValidationError(
                "coverage roles must defer sandboxing to the external host launcher"
            )
        if coverage_phase.get("requires_host_isolation") is not True:
            raise ValidationError("coverage run must require host isolation")
        if not dry_run and launcher_command is None:
            raise ValidationError(
                "coverage execution requires an external host-isolation launcher"
            )
    elif coverage_phase is not None:
        raise ValidationError("non-coverage role cannot consume coverage phase metadata")
    if any(role.kind == "judge" for role in roles):
        jury = run_payload.get("jury")
        if not jury:
            raise ValidationError("judge roles require jury-run-create")
        policy, policy_hash = load_approved_policy()
        candidate_record = jury.get("candidate", {})
        candidate_path = Path(candidate_record.get("path", ""))
        if (
            _hash_file(candidate_path) != candidate_record.get("sha256")
            or policy.get("policy_version")
            != jury.get("policy", {}).get("policy_version")
            or policy_hash != jury.get("policy", {}).get("policy_hash")
        ):
            raise ValidationError("sealed jury candidate or policy changed")
    admission_by_role: dict[str, str] = {}
    if not execution_policy.get("test_only"):
        if admission_ids is None or len(admission_ids) != len(roles):
            raise ValidationError("RUN-ADMISSION-ROLE-CARD-REQUIRED")
        admissions = load_admitted_work(workspace, admission_ids, execution_policy_hash)
        run_binding = {"artifact_id": f"oss-run:{run_id}", "sha256": run_payload["run_hash"]}
        unmatched_roles = {role.role_id for role in roles}
        for admission in admissions:
            if (
                admission["wave_id"] != wave
                or admission["role_id"] not in unmatched_roles
                or run_binding not in admission["card"]["immutable_inputs"]
            ):
                raise ValidationError("RUN-ADMISSION-BINDING-MISMATCH")
            unmatched_roles.remove(admission["role_id"])
            admission_by_role[admission["role_id"]] = admission["admission_id"]
        if unmatched_roles:
            raise ValidationError("RUN-ADMISSION-ROLE-CARD-REQUIRED")

    def execute(role: AgentRole) -> dict[str, Any]:
        output_root = (
            run_root / ("judges" if role.kind == "judge" else "producers") / role.role_id
        )
        output_root.mkdir(parents=True, exist_ok=True)
        resolved_output_root = output_root.resolve()
        if run_root not in resolved_output_root.parents:
            raise ValidationError(f"role output root escapes run: {role.role_id}")
        output_root = resolved_output_root
        identity = (
            _jury_identity(
                run_id,
                role.role_id,
                run_payload["jury"]["candidate"]["candidate_id"],
            )
            if role.kind == "judge"
            else None
        )
        output_path = output_root / "result.json"
        raw_model_output_path = output_root / "model-result.raw.json"
        execution_path = output_root / "execution.json"
        coverage_kind = _coverage_role_kind(role)
        model_output_path = (
            raw_model_output_path if coverage_kind == "trace" else output_path
        )
        existing_artifacts = [
            path.name
            for path in (output_path, raw_model_output_path, execution_path)
            if path.exists() or path.is_symlink()
        ]
        if existing_artifacts:
            raise ValidationError(
                "role attempt output already exists; create a new run_id: "
                + ", ".join(existing_artifacts)
            )
        prompt = _prompt_for(role, run_payload, identity)
        if isinstance(coverage_phase, dict):
            expected_prompt_hash = coverage_phase.get("generated_prompt_sha256")
            actual_prompt_hash = "sha256:" + hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest()
            if expected_prompt_hash != actual_prompt_hash:
                raise ValidationError("coverage generated prompt binding differs")
        prompt_path = output_root / "prompt.txt"
        # Validate the entire static launch contract before claiming a permit.
        # Once claimed, every recoverable launch failure must receive a terminal
        # execution receipt rather than leave the permit in_flight.
        schemas_root_resolved = schemas_root.resolve()
        schema_candidate = schemas_root_resolved / role.output_schema
        schema_path = schema_candidate.resolve()
        if (
            schema_path != schemas_root_resolved
            and schemas_root_resolved not in schema_path.parents
        ):
            raise ValidationError(
                f"output schema escapes schemas root: {role.output_schema}"
            )
        if schema_candidate.is_symlink() or not schema_path.is_file():
            raise ValidationError(f"output schema is not a regular file: {schema_path}")
        validate_openai_response_format_schema(
            schema_path,
            f"{role.role_id} output",
        )
        codex_command = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            role.sandbox,
            "--cd",
            str(output_root),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "-",
        ]
        command = codex_command
        if launcher_command is not None:
            command = [
                *launcher_command,
                "--role-id",
                role.role_id,
                "--workdir",
                str(output_root),
                "--",
                *codex_command,
            ]
        record = {
            "role_id": role.role_id,
            "wave": wave,
            "command": command,
            "codex_command": codex_command,
            "launcher": launcher_command,
            "jury_identity": identity,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "output": str(output_path),
            "raw_model_output": (
                str(model_output_path) if coverage_kind == "trace" else None
            ),
            "dry_run": dry_run,
            "isolation_assurance": (
                "dedicated working/output root, role-private ephemeral CODEX_HOME, "
                "and Codex sandbox; host-level read isolation must still be "
                "supplied by the deployment boundary"
            ),
        }

        attempt_admission_id = admission_by_role.get(role.role_id)
        attempt_binding = {
            "run_id": run_id,
            "run_hash": run_payload["run_hash"],
            "wave_id": wave,
            "role_id": role.role_id,
        }

        def settle_written_execution() -> None:
            if attempt_admission_id is None:
                return
            execution_record = {
                "artifact_id": f"oss-execution:{run_id}:{role.role_id}",
                "sha256": "sha256:" + _hash_file(execution_path),
            }
            if dry_run:
                # A dry run does create a controller plan artifact, but it has
                # not invoked the agent.  Closing the claimed permit as stopped
                # keeps the append-only ledger finite without representing a
                # plan as a successful execution.
                settle_attempt(
                    workspace,
                    attempt_admission_id,
                    "stopped",
                    "dry_run_not_executed",
                    execution_record,
                    retest_required=True,
                )
            elif record["state"] == "failed":
                settle_attempt(
                    workspace,
                    attempt_admission_id,
                    "failed",
                    "role_execution_failed",
                    execution_record,
                    retest_required=True,
                )
            else:
                settle_attempt(
                    workspace,
                    attempt_admission_id,
                    "completed",
                    "role_execution_completed",
                    execution_record,
                )

        def persist_execution_and_settle() -> bool:
            """Close a claimed permit even when its role receipt cannot persist.

            The attempt ledger is the authoritative finite-work accounting
            surface.  ``execution.json`` is still required for ordinary
            success/failure evidence, but a local receipt-write failure must
            not silently strand a claimed admission in ``in_flight``.
            A second ledger-write failure remains an explicit physical-recovery
            case; we never pretend it settled.
            """

            try:
                atomic_write_json(execution_path, record)
            except (OSError, ValidationError) as exc:
                record["state"] = "failed"
                record["execution_receipt_error"] = str(exc)
                if attempt_admission_id is not None:
                    settle_attempt(
                        workspace,
                        attempt_admission_id,
                        "failed",
                        "execution_receipt_write_failed",
                        None,
                        retest_required=True,
                    )
                return False
            settle_written_execution()
            return True

        if attempt_admission_id is not None:
            # This is intentionally the last action before the role-local
            # prompt artifact.  A permit is never reused for an unrecorded
            # prompt/agent attempt.
            claim_attempt(workspace, attempt_admission_id, attempt_binding)
        try:
            prompt_path.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            record["state"] = "failed"
            record["launch_error"] = f"prompt_write_failed: {exc}"
            persist_execution_and_settle()
            return record
        if not dry_run:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY",
                    "GITHUB_TOKEN",
                    "GH_TOKEN",
                }
            }
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
            except OSError as exc:
                record["state"] = "failed"
                record["launch_error"] = f"subprocess_start_failed: {exc}"
                completed = subprocess.CompletedProcess(
                    command, 127, stdout="", stderr=str(exc)
                )
            try:
                (output_root / "stdout.log").write_text(
                    completed.stdout, encoding="utf-8"
                )
                (output_root / "stderr.log").write_text(
                    completed.stderr, encoding="utf-8"
                )
            except OSError as exc:
                record["state"] = "failed"
                record["launch_error"] = f"execution_log_write_failed: {exc}"
            record["returncode"] = completed.returncode
            if coverage_kind == "trace" and output_path.exists():
                try:
                    if output_path.is_symlink() or not output_path.is_file():
                        raise ValidationError(
                            "coverage trace model output is not a regular file"
                        )
                    if model_output_path.exists():
                        raise ValidationError(
                            "refusing to overwrite preserved raw model output"
                        )
                    os.replace(output_path, model_output_path)
                    directory_fd = os.open(output_root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    record["raw_model_artifact"] = {
                        "path": str(model_output_path),
                        "sha256": _hash_file(model_output_path),
                        "producer": "codex_model_output",
                        "preservation": "atomic_rename_before_validation",
                        "authoritative": False,
                    }
                except (OSError, ValidationError) as exc:
                    record["state"] = "failed"
                    record["validation_error"] = str(exc)
            if completed.returncode != 0:
                record["state"] = "failed"
            elif record.get("state") != "failed":
                try:
                    output_payload = json.loads(
                        model_output_path.read_text(encoding="utf-8")
                    )
                    validate_payload(
                        output_payload,
                        schema_path,
                        f"{role.role_id} output",
                    )
                    if role.kind == "judge":
                        jury = run_payload["jury"]
                        if (
                            output_payload.get("candidate_id")
                            != jury["candidate"]["candidate_id"]
                            or output_payload.get("snapshot_id")
                            != jury["candidate"]["snapshot_id"]
                            or output_payload.get("policy_version")
                            != jury["policy"]["policy_version"]
                            or output_payload.get("phase") != "first_pass"
                            or output_payload.get("report_id")
                            != identity["report_id"]
                            or output_payload.get("judge", {}).get("role_id")
                            != role.role_id
                            or output_payload.get("judge", {}).get(
                                "agent_instance_id"
                            )
                            != identity["agent_instance_id"]
                            or any(
                                check.get("check_id") not in role.checks
                                or check.get("dimension") != role.dimension
                                for check in output_payload.get("checks", [])
                            )
                        ):
                            raise ValidationError(
                                f"{role.role_id} output violates sealed jury binding"
                            )
                        verify_self_hash(output_payload)
                    if coverage_kind == "trace":
                        normalized, filled = _normalize_coverage_trace_model_output(
                            role,
                            output_payload,
                            coverage_phase,
                        )
                        validate_payload(
                            normalized,
                            schema_path,
                            f"{role.role_id} normalized output",
                        )
                        atomic_write_json(output_path, normalized)
                        record["normalization"] = {
                            "returned_samples": len(output_payload["traces"]),
                            "filled_unresolved_samples": filled,
                            "authoritative_trace_hash": normalized["trace_hash"],
                        }
                        record["normalized_result_artifact"] = {
                            "path": str(output_path),
                            "sha256": _hash_file(output_path),
                            "producer": "flowness_controller",
                            "authoritative": True,
                        }
                    elif coverage_kind is not None:
                        _validate_coverage_model_output(
                            role,
                            output_payload,
                            coverage_phase,
                        )
                except (OSError, json.JSONDecodeError, ValidationError) as exc:
                    record["state"] = "failed"
                    record["validation_error"] = str(exc)
                else:
                    record["state"] = (
                        "awaiting-verification"
                        if _coverage_role_kind(role) is not None
                        else "completed"
                    )
        else:
            record["state"] = "planned"
        persist_execution_and_settle()
        return record

    if max_parallel == 1:
        records = [execute(role) for role in roles]
    else:
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            records = list(executor.map(execute, roles))
    return sorted(records, key=lambda item: item["role_id"])
