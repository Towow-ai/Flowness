from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from .controller import (
    _hash_file,
    _load_run_manifest,
    _load_roles,
    _prompt_for,
    _write_run_manifest,
    coverage_prompt_template_hash,
    create_run,
)
from .coverage import validate_inventory_v2
from .integrity import canonical_hash
from .reconciliation import SourceResolver, _source_repository
from .registry import ValidationError, atomic_create_json
from .resources import CONFIG_ROOT, PACKAGE_ROOT, SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload

SAMPLE_SCHEMA = SCHEMAS_ROOT / "coverage-sample-manifest.schema.json"
TRACE_SCHEMA = SCHEMAS_ROOT / "coverage-trace.schema.json"
VERDICT_SCHEMA = SCHEMAS_ROOT / "coverage-verdict.schema.json"
TRUSTED_ROLES = CONFIG_ROOT / "roles.json"
ALGORITHM_VERSION = "coverage-sample/stratified-sha256-rank-v2"
TRACE_ROLE_IDS = {"challenge.coverage", "challenge.consumer-chain"}
COMPARATOR_ROLE_IDS = {
    "challenge.coverage-comparator",
    "challenge.coverage-adjudicator",
}
REQUIRED_STRATEGIES = {"required_mechanism", "required_registration"}
RUNTIME_ABSENT = {
    "state": "absent",
    "protocol": "unsupported",
    "filename": None,
    "file_sha256": None,
    "content_hash": None,
}


def _sha_file(path: Path) -> str:
    return "sha256:" + _hash_file(path.resolve())


def _sha_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _artifact_subject(path: Path) -> str:
    try:
        return _sha_file(path)
    except (OSError, ValidationError):
        return f"artifact:{path.name}"


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return payload


def _load_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path.name}") from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise ValidationError(f"{label} must be an array of objects")
    return payload


def _load_controller_trace_execution(path: Path) -> tuple[Path, dict[str, Any]]:
    if path.name != "result.json" or path.is_symlink() or not path.is_file():
        raise ValidationError(
            "coverage trace must be the controller-sealed canonical result.json"
        )
    resolved = path.resolve()
    execution_path = path.parent / "execution.json"
    if execution_path.is_symlink() or not execution_path.is_file():
        raise ValidationError("coverage trace has no regular controller execution.json")
    execution = _load_object(execution_path, "coverage trace execution")
    normalized = execution.get("normalized_result_artifact")
    if (
        execution.get("state") != "awaiting-verification"
        or execution.get("output") != str(resolved)
        or not isinstance(normalized, dict)
        or normalized.get("producer") != "flowness_controller"
        or normalized.get("authoritative") is not True
        or normalized.get("path") != str(resolved)
        or normalized.get("sha256") != _hash_file(resolved)
    ):
        raise ValidationError(
            "coverage trace execution does not bind an authoritative normalized result"
        )
    return resolved, execution


def _stable_blocker(
    code: str,
    detail: str,
    subject: str = "protocol",
    inventory_item_id: str = "",
) -> dict[str, str]:
    material = f"{code}\0{subject}".encode("utf-8")
    return {
        "blocker_id": "COV-" + hashlib.sha256(material).hexdigest()[:20].upper(),
        "code": code,
        "inventory_item_id": inventory_item_id,
        "detail": detail,
    }


def _manifest_blocker(code: str, detail: str, subject: str) -> dict[str, str]:
    blocker = _stable_blocker(code, detail, subject)
    return {
        "blocker_id": blocker["blocker_id"],
        "code": code,
        "detail": detail,
    }


def _verify_self_hash(payload: dict[str, Any], field: str, label: str) -> None:
    unsigned = {key: value for key, value in payload.items() if key != field}
    if payload.get(field) != canonical_hash(unsigned):
        raise ValidationError(f"invalid {label} {field}")


def _registry_semantic_hash(registries: dict[str, list[dict[str, Any]]]) -> str:
    return canonical_hash(
        {
            name: sorted(canonical_hash(item) for item in payload)
            for name, payload in sorted(registries.items())
        }
    )


def _trusted_role(roles_path: Path, role_id: str, output_schema: str):
    supplied = {role.role_id: role for role in _load_roles(roles_path.resolve())}
    trusted = {role.role_id: role for role in _load_roles(TRUSTED_ROLES)}
    role = supplied.get(role_id)
    if (
        role is None
        or trusted.get(role_id) != role
        or role.output_schema != output_schema
    ):
        raise ValidationError(f"role differs from trusted package role: {role_id}")
    return role


def _role_subset(
    workspace: Path,
    roles_path: Path,
    role_id: str,
    expected_schema: str,
) -> Path:
    role = _trusted_role(roles_path, role_id, expected_schema)
    subset = {
        "schema_version": "agent-role-registry/v1",
        "roles": [
            {
                "role_id": role.role_id,
                "wave": role.wave,
                "kind": role.kind,
                "dimension": role.dimension,
                "mission": role.mission,
                "output_schema": role.output_schema,
                "sandbox": role.sandbox,
            }
        ],
    }
    digest = canonical_hash(subset).removeprefix("sha256:")[:20]
    target = workspace.resolve() / "coverage-role-configs" / f"{role_id}-{digest}.json"
    if target.exists():
        if _load_object(target, "coverage role subset") != subset:
            raise ValidationError(f"coverage role subset changed: {target.name}")
    else:
        atomic_create_json(target, subset)
    return target


def _merge_binding_blockers(
    merge_report: dict[str, Any],
    merge_report_path: Path,
    registry_paths: dict[str, Path],
    registries: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, str]], str]:
    blockers: list[dict[str, str]] = []
    report_subject = _artifact_subject(merge_report_path)
    try:
        _verify_self_hash(merge_report, "report_hash", "merge report")
    except ValidationError as exc:
        blockers.append(_stable_blocker("MERGE-REPORT-HASH", str(exc), report_subject))
    if merge_report.get("state") != "merged" or merge_report.get("blockers") != []:
        blockers.append(
            _stable_blocker(
                "MERGE-NOT-PASSED",
                "coverage requires state=merged and blockers=[]",
                report_subject,
            )
        )
    bindings = merge_report.get("outputs")
    if not isinstance(bindings, dict):
        blockers.append(
            _stable_blocker(
                "MERGE-OUTPUT-BINDING-MISSING",
                "merge report has no sealed output bindings",
                report_subject,
            )
        )
        bindings = {}
    for name, path in registry_paths.items():
        payload = registries[name]
        actual = {
            "filename": path.name,
            "count": len(payload),
            "content_hash": canonical_hash(payload),
            "file_sha256": _sha_file(path),
        }
        if bindings.get(name) != actual:
            blockers.append(
                _stable_blocker(
                    "MERGE-OUTPUT-BINDING-MISMATCH",
                    f"combined {name} registry differs from merge report binding",
                    actual["file_sha256"],
                )
            )
    return blockers, _registry_semantic_hash(registries)


def _upstream_sealed_hashes(
    merge_report: dict[str, Any],
) -> tuple[set[str], set[str], list[dict[str, str]]]:
    source_hashes: set[str] = set()
    snapshot_hashes: set[str] = set()
    failures: list[dict[str, str]] = []
    groups = merge_report.get("inputs", {}).get("groups", [])
    if not isinstance(groups, list):
        return source_hashes, snapshot_hashes, [
            _manifest_blocker(
                "MERGE-UPSTREAM-MISSING",
                "merge report has no upstream reconciliation groups",
                str(merge_report.get("report_hash", "merge-report")),
            )
        ]
    for index, group in enumerate(groups):
        report_binding = group.get("report") if isinstance(group, dict) else None
        subject = (
            str(report_binding.get("sha256"))
            if isinstance(report_binding, dict)
            else f"group:{index}"
        )
        if not isinstance(report_binding, dict) or not isinstance(
            report_binding.get("path"), str
        ):
            failures.append(
                _manifest_blocker(
                    "MERGE-UPSTREAM-MISSING",
                    "merge group has no reconciliation report binding",
                    subject,
                )
            )
            continue
        path = Path(report_binding["path"]).resolve()
        try:
            report = _load_object(path, "upstream reconciliation report")
            if _sha_file(path) != report_binding.get("sha256"):
                raise ValidationError("upstream reconciliation file hash differs")
            _verify_self_hash(report, "report_hash", "reconciliation report")
        except ValidationError as exc:
            failures.append(_manifest_blocker("MERGE-UPSTREAM-HASH", str(exc), subject))
            continue
        source = report.get("inputs", {}).get("source", {})
        if isinstance(source, dict) and isinstance(source.get("sha256"), str):
            source_hashes.add(source["sha256"])
        snapshots = report.get("inputs", {}).get("snapshots", [])
        if isinstance(snapshots, list):
            snapshot_hashes.update(
                item["sha256"]
                for item in snapshots
                if isinstance(item, dict) and isinstance(item.get("sha256"), str)
            )
    return source_hashes, snapshot_hashes, failures


def _reject_runtime(path: Path | None) -> dict[str, Any]:
    if path is not None:
        raise ValidationError(
            "runtime evidence is unsupported until a sealed runtime protocol exists"
        )
    return dict(RUNTIME_ABSENT)


def _inventory_items(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    return validate_inventory_v2(inventory)


def _ranked_samples(
    items: list[dict[str, Any]],
    source_hash: str,
    combined_registry_hash: str,
    role_id: str,
    algorithm_version: str,
    search_seed_quota: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    seed = "sha256:" + hashlib.sha256(
        (source_hash + combined_registry_hash + role_id + algorithm_version).encode(
            "utf-8"
        )
    ).hexdigest()
    ranked: list[tuple[str, str, dict[str, Any]]] = []
    for item in items:
        selection_hash = "sha256:" + hashlib.sha256(
            (seed + canonical_hash(item)).encode("utf-8")
        ).hexdigest()
        ranked.append((selection_hash, item["item_id"], item))
    ranked.sort(key=lambda value: (value[0], value[1]))
    required = [entry for entry in ranked if entry[2]["coverage_strategy"] in REQUIRED_STRATEGIES]
    search = [entry for entry in ranked if entry[2]["coverage_strategy"] == "search_seed"]
    selected = [*required, *search[: min(search_seed_quota, len(search))]]
    selected.sort(
        key=lambda value: (
            0 if value[2]["coverage_strategy"] in REQUIRED_STRATEGIES else 1,
            value[0],
            value[1],
        )
    )
    samples = [
        {
            "sample_id": "sample-"
            + hashlib.sha256((seed + item_id).encode("utf-8")).hexdigest()[:20],
            "inventory_item_id": item_id,
            "kind": str(item.get("kind", "unknown")),
            "locator": str(item.get("locator", "unknown")),
            "criticality": item["criticality"],
            "coverage_strategy": item["coverage_strategy"],
            "blocking_reason": item["blocking_reason"],
            "selection_hash": selection_hash,
        }
        for selection_hash, item_id, item in selected
    ]
    algorithm = {
        "version": algorithm_version,
        "seed": seed,
        "search_seed_quota": search_seed_quota,
        "sample_size": len(samples),
        "population_size": len(items),
        "required_selected": len(required),
        "search_seed_selected": min(search_seed_quota, len(search)),
        "ordering": (
            "all required_mechanism/required_registration, then "
            "sha256(seed+canonical_item_hash), then item_id"
        ),
        "shard": {"mode": "all_required", "index": 0, "count": 1},
    }
    return seed, samples, algorithm


def create_coverage_sample_manifest(
    source_snapshot: Path,
    inventory_path: Path,
    merge_report_path: Path,
    mechanisms_path: Path,
    unknowns_path: Path,
    drift_path: Path,
    roles_path: Path,
    role_id: str,
    agent_instance_id: str,
    output_path: Path,
    sample_size: int = 20,
    runtime_evidence_index: Path | None = None,
    algorithm_version: str = ALGORITHM_VERSION,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValidationError(f"refusing to overwrite sample manifest: {output_path.name}")
    if sample_size < 0:
        raise ValidationError("sample_size must be non-negative")
    runtime_binding = _reject_runtime(runtime_evidence_index)
    if role_id not in TRACE_ROLE_IDS or not agent_instance_id:
        raise ValidationError("authorized trace role and agent_instance_id required")
    role = _trusted_role(roles_path, role_id, "coverage-trace.schema.json")
    if role.sandbox != "danger-full-access":
        raise ValidationError(
            "coverage trace role must defer sandboxing to the external host launcher"
        )

    source_snapshot = source_snapshot.resolve()
    inventory_path = inventory_path.resolve()
    merge_report_path = merge_report_path.resolve()
    roles_path = roles_path.resolve()
    registry_paths = {
        "mechanisms": mechanisms_path.resolve(),
        "unknowns": unknowns_path.resolve(),
        "drift_findings": drift_path.resolve(),
    }
    registries = {
        name: _load_array(path, f"combined {name} registry")
        for name, path in registry_paths.items()
    }
    merge_report = _load_object(merge_report_path, "merge report")
    blockers, combined_registry_hash = _merge_binding_blockers(
        merge_report, merge_report_path, registry_paths, registries
    )
    source_hashes, snapshot_hashes, upstream_failures = _upstream_sealed_hashes(
        merge_report
    )
    blockers.extend(upstream_failures)
    source_hash = _sha_file(source_snapshot)
    inventory_hash = _sha_file(inventory_path)
    if source_hash not in source_hashes:
        blockers.append(
            _stable_blocker(
                "SOURCE-NOT-SEALED",
                "source archive hash is absent from upstream reconciliations",
                source_hash,
            )
        )
    if inventory_hash not in snapshot_hashes:
        blockers.append(
            _stable_blocker(
                "INVENTORY-NOT-SEALED",
                "inventory file hash is absent from upstream reconciliations",
                inventory_hash,
            )
        )

    inventory = _load_object(inventory_path, "inventory")
    items = _inventory_items(inventory)
    _, samples, algorithm = _ranked_samples(
        items,
        source_hash,
        combined_registry_hash,
        role_id,
        algorithm_version,
        sample_size,
    )
    if not samples:
        blockers.append(
            _stable_blocker(
                "EMPTY-SAMPLE-SELECTION",
                "coverage trace requires at least one deterministic sample",
                inventory_hash,
            )
        )
    blockers = sorted(
        {item["blocker_id"]: item for item in blockers}.values(),
        key=lambda item: item["blocker_id"],
    )
    unsigned = {
        "schema_version": "coverage-sample-manifest/v2",
        "phase": "trace_sampling",
        "state": "blocked" if blockers else "ready",
        "sealed_inputs": {
            "source_snapshot": {
                "filename": source_snapshot.name,
                "file_sha256": source_hash,
            },
            "inventory": {
                "filename": inventory_path.name,
                "file_sha256": inventory_hash,
                "repository_content_hash": str(
                    inventory.get("repository_content_hash", "unknown")
                ),
                "schema_version": str(inventory.get("schema_version", "unknown")),
            },
            "merge_report": {
                "filename": merge_report_path.name,
                "file_sha256": _sha_file(merge_report_path),
                "report_hash": str(merge_report.get("report_hash", "sha256:" + "0" * 64)),
            },
            "registries": {
                "combined_registry_hash": combined_registry_hash,
                "mechanisms_hash": canonical_hash(registries["mechanisms"]),
                "unknowns_hash": canonical_hash(registries["unknowns"]),
                "drift_findings_hash": canonical_hash(registries["drift_findings"]),
            },
            "runtime_evidence": runtime_binding,
            "roles": {"filename": roles_path.name, "file_sha256": _sha_file(roles_path)},
            "prompt": {
                "template_id": f"coverage-prompt/{role_id}/v1",
                "template_hash": coverage_prompt_template_hash(role_id),
            },
        },
        "algorithm": algorithm,
        "agent": {"role_id": role_id, "agent_instance_id": agent_instance_id},
        "samples": [] if blockers else samples,
        "blockers": [
            {
                "blocker_id": item["blocker_id"],
                "code": item["code"],
                "detail": item["detail"],
            }
            for item in blockers
        ],
    }
    manifest = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    validate_payload(manifest, SAMPLE_SCHEMA, "coverage sample manifest")
    atomic_create_json(output_path.resolve(), manifest)
    return manifest


def _verify_deterministic_manifest(
    manifest: dict[str, Any],
    source_snapshot: Path,
    inventory_path: Path,
    roles_path: Path,
    combined_registry_hash: str | None = None,
) -> None:
    _verify_self_hash(manifest, "manifest_hash", "coverage sample manifest")
    if manifest.get("state") != "ready" or manifest.get("blockers") != []:
        raise ValidationError("blocked sample manifest cannot create a trace run")
    source_hash = _sha_file(source_snapshot)
    inventory_hash = _sha_file(inventory_path)
    roles_hash = _sha_file(roles_path)
    sealed = manifest["sealed_inputs"]
    if sealed["source_snapshot"] != {
        "filename": source_snapshot.name,
        "file_sha256": source_hash,
    }:
        raise ValidationError("trace source differs from sample manifest binding")
    inventory = _load_object(inventory_path, "inventory")
    if sealed["inventory"] != {
        "filename": inventory_path.name,
        "file_sha256": inventory_hash,
        "repository_content_hash": str(inventory.get("repository_content_hash", "unknown")),
        "schema_version": str(inventory.get("schema_version", "unknown")),
    }:
        raise ValidationError("trace inventory differs from sample manifest binding")
    if sealed["roles"] != {"filename": roles_path.name, "file_sha256": roles_hash}:
        raise ValidationError("trace roles differ from sample manifest binding")
    role_id = manifest["agent"]["role_id"]
    if sealed["prompt"] != {
        "template_id": f"coverage-prompt/{role_id}/v1",
        "template_hash": coverage_prompt_template_hash(role_id),
    }:
        raise ValidationError("trace prompt template binding differs")
    if sealed["runtime_evidence"] != RUNTIME_ABSENT:
        raise ValidationError("runtime evidence protocol is unsupported")
    if (
        combined_registry_hash is not None
        and sealed["registries"]["combined_registry_hash"]
        != combined_registry_hash
    ):
        raise ValidationError("sample manifest registry digest differs")
    _, expected_samples, expected_algorithm = _ranked_samples(
        _inventory_items(inventory),
        source_hash,
        sealed["registries"]["combined_registry_hash"],
        role_id,
        manifest["algorithm"]["version"],
        manifest["algorithm"]["search_seed_quota"],
    )
    if (
        not expected_samples
        or manifest["algorithm"] != expected_algorithm
        or manifest["samples"] != expected_samples
    ):
        raise ValidationError("sample manifest selection differs from deterministic ranking")


def _verify_trace_run_manifest(
    manifest: dict[str, Any],
    source_snapshot: Path,
    inventory_path: Path,
    roles_path: Path,
) -> None:
    _verify_deterministic_manifest(
        manifest,
        source_snapshot,
        inventory_path,
        roles_path,
    )


def _bind_coverage_prompt(run_root: Path, role_id: str) -> None:
    run_payload = _load_run_manifest(run_root / "run.json")
    role = _load_roles(Path(run_payload["roles_config"]["path"]))[0]
    if role.role_id != role_id:
        raise ValidationError("coverage run role differs")
    prompt = _prompt_for(role, run_payload)
    run_payload["coverage_phase"]["generated_prompt_sha256"] = _sha_bytes(
        prompt.encode("utf-8")
    )
    _write_run_manifest(run_root / "run.json", run_payload)


def create_coverage_trace_run(
    workspace: Path,
    run_id: str,
    source_snapshot: Path,
    inventory_path: Path,
    sample_manifest_path: Path,
    roles_path: Path,
) -> dict[str, str]:
    sample = load_validated_json(
        sample_manifest_path.resolve(), SAMPLE_SCHEMA, "coverage sample manifest"
    )
    _verify_trace_run_manifest(
        sample,
        source_snapshot.resolve(),
        inventory_path.resolve(),
        roles_path.resolve(),
    )
    role_id = sample["agent"]["role_id"]
    subset_path = _role_subset(
        workspace, roles_path, role_id, "coverage-trace.schema.json"
    )
    run_root = create_run(
        workspace,
        run_id,
        [source_snapshot.resolve(), sample_manifest_path.resolve()],
        subset_path,
    )
    run_payload = _load_run_manifest(run_root / "run.json")
    run_payload["coverage_phase"] = {
        "phase": "blind_source_trace",
        "role_id": role_id,
        "allowed_inputs": ["source_snapshot", "sample_manifest"],
        "controller_only_inputs": {
            "inventory": sample["sealed_inputs"]["inventory"],
        },
        "registry_access": "absent",
        "prompt_source": "trusted_package_template",
        "prompt_template_hash": sample["sealed_inputs"]["prompt"]["template_hash"],
        "requires_host_isolation": True,
        "deterministic_bindings": {
            "manifest_hash": sample["manifest_hash"],
            "agent_instance_id": sample["agent"]["agent_instance_id"],
            "algorithm_version": sample["algorithm"]["version"],
            "seed": sample["algorithm"]["seed"],
            "source_snapshot_hash": sample["sealed_inputs"]["source_snapshot"][
                "file_sha256"
            ],
            "roles_file_sha256": sample["sealed_inputs"]["roles"]["file_sha256"],
            "samples": [
                {
                    "sample_id": item["sample_id"],
                    "inventory_item_id": item["inventory_item_id"],
                }
                for item in sample["samples"]
            ],
        },
    }
    _write_run_manifest(run_root / "run.json", run_payload)
    _bind_coverage_prompt(run_root, role_id)
    return {"run_root": str(run_root), "roles": str(subset_path), "role_id": role_id}


def _verify_source_ref(
    resolver: SourceResolver,
    ref: dict[str, Any],
    source_hash: str,
) -> None:
    if ref["source_snapshot_hash"] != source_hash:
        raise ValidationError("source evidence snapshot hash differs")
    if ref["hash_scope"] != "exact_excerpt_utf8":
        raise ValidationError("source evidence hash_scope is unsupported")
    path, canonical, start, end = resolver.resolve(ref["locator"])
    if start is None or end is None or ref["locator"].replace("\\", "/") != canonical:
        raise ValidationError("source evidence locator must use an exact canonical range")
    excerpt = resolver.actual_excerpt(path, start, end)
    if ref["excerpt"] != excerpt:
        raise ValidationError("source evidence excerpt differs from source archive")
    if ref["content_hash"] != _sha_bytes(excerpt.encode("utf-8")):
        raise ValidationError("source evidence content hash differs")


def _definition_binds_sample(
    resolver: SourceResolver,
    sample_locator: str,
    definition_ref: dict[str, Any],
) -> bool:
    raw_sample_path, sample_start, _ = SourceResolver.split_locator(sample_locator)
    if sample_start is None and ":[" in raw_sample_path:
        raw_sample_path = raw_sample_path.split(":[", 1)[0]
    try:
        sample_path, _, _, _ = resolver.resolve(raw_sample_path)
        definition_path, _, definition_start, definition_end = resolver.resolve(
            definition_ref["locator"]
        )
    except ValidationError:
        return False
    if sample_path != definition_path:
        return False
    if sample_start is None:
        return True
    return (
        definition_start is not None
        and definition_end is not None
        and definition_start <= sample_start <= definition_end
    )


def _load_protocol_inputs(
    source_snapshot: Path,
    inventory_path: Path,
    sample_manifest_paths: Sequence[Path],
    trace_paths: Sequence[Path],
    merge_report_path: Path,
    mechanisms_path: Path,
    unknowns_path: Path,
    drift_path: Path,
    roles_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, Any],
    str,
    list[dict[str, str]],
]:
    source_snapshot = source_snapshot.resolve()
    inventory_path = inventory_path.resolve()
    merge_report_path = merge_report_path.resolve()
    roles_path = roles_path.resolve()
    source_hash = _sha_file(source_snapshot)
    inventory = _load_object(inventory_path, "inventory")
    _inventory_items(inventory)
    registry_paths = {
        "mechanisms": mechanisms_path.resolve(),
        "unknowns": unknowns_path.resolve(),
        "drift_findings": drift_path.resolve(),
    }
    registries = {
        name: _load_array(path, f"combined {name} registry")
        for name, path in registry_paths.items()
    }
    merge_report = _load_object(merge_report_path, "merge report")
    blockers, combined_registry_hash = _merge_binding_blockers(
        merge_report, merge_report_path, registry_paths, registries
    )
    roles_hash = _sha_file(roles_path)
    merge_binding = {
        "filename": merge_report_path.name,
        "file_sha256": _sha_file(merge_report_path),
        "report_hash": str(merge_report.get("report_hash", "sha256:" + "0" * 64)),
    }
    registry_binding = {
        "combined_registry_hash": combined_registry_hash,
        "mechanisms_hash": canonical_hash(registries["mechanisms"]),
        "unknowns_hash": canonical_hash(registries["unknowns"]),
        "drift_findings_hash": canonical_hash(registries["drift_findings"]),
    }

    manifests: dict[str, dict[str, Any]] = {}
    sample_records: dict[str, dict[str, Any]] = {}
    for path in sample_manifest_paths:
        subject = _artifact_subject(path)
        try:
            manifest = load_validated_json(
                path.resolve(), SAMPLE_SCHEMA, "coverage sample manifest"
            )
            _verify_self_hash(manifest, "manifest_hash", "coverage sample manifest")
        except ValidationError as exc:
            blockers.append(_stable_blocker("SAMPLE-MANIFEST-HASH", str(exc), subject))
            continue
        manifest_hash = manifest["manifest_hash"]
        if manifest_hash in manifests:
            blockers.append(
                _stable_blocker(
                    "DUPLICATE-SAMPLE-MANIFEST",
                    "sample manifest supplied more than once",
                    manifest_hash,
                )
            )
            continue
        manifests[manifest_hash] = manifest
        role_id = manifest["agent"]["role_id"]
        try:
            _trusted_role(roles_path, role_id, "coverage-trace.schema.json")
            prompt_binding = {
                "template_id": f"coverage-prompt/{role_id}/v1",
                "template_hash": coverage_prompt_template_hash(role_id),
            }
        except ValidationError as exc:
            blockers.append(_stable_blocker("TRACE-ROLE-NOT-AUTHORIZED", str(exc), role_id))
            prompt_binding = None
        sealed = manifest["sealed_inputs"]
        if (
            manifest["state"] != "ready"
            or manifest["blockers"] != []
            or sealed["source_snapshot"]
            != {"filename": source_snapshot.name, "file_sha256": source_hash}
            or sealed["merge_report"] != merge_binding
            or sealed["registries"] != registry_binding
            or sealed["runtime_evidence"] != RUNTIME_ABSENT
            or sealed["roles"]
            != {"filename": roles_path.name, "file_sha256": roles_hash}
            or sealed["prompt"] != prompt_binding
        ):
            blockers.append(
                _stable_blocker(
                    "SAMPLE-BINDING-MISMATCH",
                    "sample manifest differs from sealed protocol inputs",
                    manifest_hash,
                )
            )
        try:
            _verify_deterministic_manifest(
                manifest,
                source_snapshot,
                inventory_path,
                roles_path,
                combined_registry_hash,
            )
        except ValidationError as exc:
            blockers.append(
                _stable_blocker(
                    "SAMPLE-DETERMINISTIC-BINDING-MISMATCH",
                    str(exc),
                    manifest_hash,
                )
            )
        for sample in manifest["samples"]:
            if sample["sample_id"] in sample_records:
                blockers.append(
                    _stable_blocker(
                        "SAMPLE-ID-CONFLICT",
                        "sample_id occurs more than once",
                        sample["sample_id"],
                    )
                )
            sample_records[sample["sample_id"]] = sample

    manifests_by_role: dict[str, list[dict[str, Any]]] = {}
    for manifest in manifests.values():
        manifests_by_role.setdefault(manifest["agent"]["role_id"], []).append(manifest)
    if (
        set(manifests_by_role) != TRACE_ROLE_IDS
        or any(len(manifests_by_role.get(role_id, [])) != 1 for role_id in TRACE_ROLE_IDS)
    ):
        blockers.append(
            _stable_blocker(
                "TRACE-ROLE-QUORUM-MISMATCH",
                "coverage requires exactly one manifest for each blind trace role",
                canonical_hash(sorted(manifests)),
            )
        )
    agent_instances = {
        manifest["agent"]["agent_instance_id"] for manifest in manifests.values()
    }
    if len(agent_instances) != len(manifests):
        blockers.append(
            _stable_blocker(
                "TRACE-AGENT-QUORUM-MISMATCH",
                "blind trace roles require distinct agent_instance_id values",
                canonical_hash(sorted(agent_instances)),
            )
        )

    traces: dict[str, dict[str, Any]] = {}
    traced_manifests: set[str] = set()
    with _source_repository(source_snapshot) as (repository_root, _):
        resolver = SourceResolver(repository_root)
        for path in trace_paths:
            subject = _artifact_subject(path)
            try:
                resolved_trace_path, execution = _load_controller_trace_execution(path)
            except ValidationError as exc:
                blockers.append(
                    _stable_blocker("TRACE-CONTROLLER-SEAL", str(exc), subject)
                )
                continue
            try:
                trace = load_validated_json(
                    resolved_trace_path, TRACE_SCHEMA, "coverage trace"
                )
                _verify_self_hash(trace, "trace_hash", "coverage trace")
            except ValidationError as exc:
                blockers.append(_stable_blocker("TRACE-HASH", str(exc), subject))
                continue
            trace_hash = trace["trace_hash"]
            if (
                resolved_trace_path.parent.name != trace["role_id"]
                or execution.get("role_id") != trace["role_id"]
            ):
                blockers.append(
                    _stable_blocker(
                        "TRACE-CONTROLLER-SEAL",
                        "coverage trace role differs from its canonical role directory",
                        trace_hash,
                    )
                )
                continue
            manifest = manifests.get(trace["manifest_hash"])
            if manifest is None:
                blockers.append(
                    _stable_blocker(
                        "TRACE-MANIFEST-UNKNOWN",
                        "trace references an unknown sample manifest",
                        trace_hash,
                    )
                )
                continue
            if trace["manifest_hash"] in traced_manifests:
                blockers.append(
                    _stable_blocker(
                        "DUPLICATE-TRACE-FOR-MANIFEST",
                        "one trace is allowed per role-bound sample manifest",
                        trace["manifest_hash"],
                    )
                )
            traced_manifests.add(trace["manifest_hash"])
            expected_samples = {
                item["sample_id"]: item["inventory_item_id"]
                for item in manifest["samples"]
            }
            actual_pairs = [
                (item["sample_id"], item["inventory_item_id"])
                for item in trace["traces"]
            ]
            actual_samples = dict(actual_pairs)
            binding_match = (
                len(actual_pairs) == len(actual_samples)
                and actual_samples == expected_samples
                and trace["role_id"] == manifest["agent"]["role_id"]
                and trace["agent_instance_id"]
                == manifest["agent"]["agent_instance_id"]
                and trace["algorithm_version"] == manifest["algorithm"]["version"]
                and trace["seed"] == manifest["algorithm"]["seed"]
                and trace["source_snapshot_hash"] == source_hash
                and trace["roles_file_sha256"] == roles_hash
                and trace["prompt_template_hash"]
                == manifest["sealed_inputs"]["prompt"]["template_hash"]
            )
            if not binding_match:
                blockers.append(
                    _stable_blocker(
                        "TRACE-BINDING-MISMATCH",
                        "trace identity, source, prompt, or exact sample set differs",
                        trace_hash,
                    )
                )
            evidence_valid = True
            try:
                for item in trace["traces"]:
                    chain = item["source_chain"]
                    chain_is_empty = all(not refs for refs in chain.values())
                    if item["assessment"] == "unresolved":
                        if not chain_is_empty:
                            raise ValidationError(
                                "unresolved trace must have an empty source chain"
                            )
                        continue
                    for refs in item["source_chain"].values():
                        for ref in refs:
                            _verify_source_ref(resolver, ref, source_hash)
                    sample = sample_records.get(item["sample_id"])
                    definitions = item["source_chain"]["definitions"]
                    if (
                        sample is None
                        or not definitions
                        or not any(
                            _definition_binds_sample(
                                resolver,
                                sample["locator"],
                                definition,
                            )
                            for definition in definitions
                        )
                    ):
                        raise ValidationError(
                            "trace definition does not bind the sampled locator"
                        )
            except ValidationError:
                evidence_valid = False
                blockers.append(
                    _stable_blocker(
                        "TRACE-SOURCE-EVIDENCE-INVALID",
                        "trace source evidence does not match the sealed archive",
                        trace_hash,
                    )
                )
            trace["_source_evidence_valid"] = evidence_valid
            traces[trace_hash] = trace
    for manifest_hash in manifests:
        if manifest_hash not in traced_manifests:
            blockers.append(
                _stable_blocker(
                    "TRACE-MISSING",
                    "sample manifest has no bound source trace",
                    manifest_hash,
                )
            )
    blockers = sorted(
        {item["blocker_id"]: item for item in blockers}.values(),
        key=lambda item: item["blocker_id"],
    )
    return manifests, traces, registries, merge_report, combined_registry_hash, blockers


def create_coverage_compare_run(
    workspace: Path,
    run_id: str,
    source_snapshot: Path,
    inventory_path: Path,
    sample_manifests: Sequence[Path],
    traces: Sequence[Path],
    merge_report: Path,
    mechanisms: Path,
    unknowns: Path,
    drift: Path,
    roles_path: Path,
    role_id: str,
    runtime_evidence_index: Path | None = None,
) -> dict[str, str]:
    if not sample_manifests or not traces:
        raise ValidationError("compare run requires samples and traces")
    _reject_runtime(runtime_evidence_index)
    if role_id not in COMPARATOR_ROLE_IDS:
        raise ValidationError("unauthorized coverage comparator role")
    _trusted_role(roles_path, role_id, "coverage-verdict.schema.json")
    (
        manifests,
        verified_traces,
        _,
        verified_merge_report,
        combined_registry_hash,
        blockers,
    ) = _load_protocol_inputs(
        source_snapshot,
        inventory_path,
        sample_manifests,
        traces,
        merge_report,
        mechanisms,
        unknowns,
        drift,
        roles_path,
    )
    if blockers:
        codes = ", ".join(sorted({item["code"] for item in blockers}))
        raise ValidationError(f"coverage compare preflight blocked: {codes}")
    subset_path = _role_subset(
        workspace, roles_path, role_id, "coverage-verdict.schema.json"
    )
    snapshots = [
        *(path.resolve() for path in sample_manifests),
        *(path.resolve() for path in traces),
        merge_report.resolve(),
        mechanisms.resolve(),
        unknowns.resolve(),
        drift.resolve(),
    ]
    run_root = create_run(workspace, run_id, snapshots, subset_path)
    run_payload = _load_run_manifest(run_root / "run.json")
    comparator_agent_id = "agent-" + hashlib.sha256(
        f"{run_id}\0{role_id}".encode("utf-8")
    ).hexdigest()[:24]
    output_bindings = {
        "source_snapshot_hash": _sha_file(source_snapshot.resolve()),
        "sample_manifest_hashes": sorted(manifests),
        "trace_hashes": sorted(verified_traces),
        "merge_report_hash": verified_merge_report["report_hash"],
        "combined_registry_hash": combined_registry_hash,
        "runtime_evidence": "absent",
        "roles_file_sha256": _sha_file(roles_path.resolve()),
        "prompt_template_hashes": sorted(
            {
                coverage_prompt_template_hash(role_id),
                *(
                    manifest["sealed_inputs"]["prompt"]["template_hash"]
                    for manifest in manifests.values()
                ),
            }
        ),
    }
    run_payload["coverage_phase"] = {
        "phase": "registry_comparison",
        "role_id": role_id,
        "allowed_inputs": [
            "sample_manifests",
            "source_traces",
            "merge_report",
            "combined_registries",
        ],
        "controller_only_inputs": {
            "source_snapshot": {
                "filename": source_snapshot.name,
                "file_sha256": _sha_file(source_snapshot),
            },
            "inventory": {
                "filename": inventory_path.name,
                "file_sha256": _sha_file(inventory_path),
            },
        },
        "source_archive_access": "absent",
        "runtime_evidence": "unsupported",
        "prompt_source": "trusted_package_template",
        "prompt_template_hash": coverage_prompt_template_hash(role_id),
        "requires_host_isolation": True,
        "deterministic_bindings": {
            "agent_instance_id": comparator_agent_id,
            "output_bindings": output_bindings,
        },
    }
    _write_run_manifest(run_root / "run.json", run_payload)
    _bind_coverage_prompt(run_root, role_id)
    return {"run_root": str(run_root), "roles": str(subset_path), "role_id": role_id}


def evaluate_coverage_challenge(
    source_snapshot: Path,
    inventory_path: Path,
    sample_manifest_paths: Sequence[Path],
    trace_paths: Sequence[Path],
    merge_report_path: Path,
    mechanisms_path: Path,
    unknowns_path: Path,
    drift_path: Path,
    roles_path: Path,
    role_id: str,
    agent_instance_id: str,
    output_path: Path,
    runtime_evidence_index: Path | None = None,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValidationError(f"refusing to overwrite coverage verdict: {output_path.name}")
    _reject_runtime(runtime_evidence_index)
    if role_id not in COMPARATOR_ROLE_IDS or not agent_instance_id:
        raise ValidationError("authorized comparator role and agent_instance_id required")
    _trusted_role(roles_path, role_id, "coverage-verdict.schema.json")
    (
        manifests,
        traces,
        registries,
        merge_report,
        combined_registry_hash,
        protocol_blockers,
    ) = _load_protocol_inputs(
        source_snapshot,
        inventory_path,
        sample_manifest_paths,
        trace_paths,
        merge_report_path,
        mechanisms_path,
        unknowns_path,
        drift_path,
        roles_path,
    )

    sample_records = {
        sample["sample_id"]: sample
        for manifest in manifests.values()
        for sample in manifest["samples"]
    }
    trace_records: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for trace_hash, trace in traces.items():
        if not trace.get("_source_evidence_valid"):
            continue
        for item in trace["traces"]:
            trace_records.setdefault(item["sample_id"], []).append((trace_hash, item))
    mechanism_map: dict[str, list[dict[str, Any]]] = {}
    for mechanism in registries["mechanisms"]:
        references = mechanism.get("inventory_item_ids", [])
        if isinstance(references, list):
            for inventory_item_id in references:
                if isinstance(inventory_item_id, str):
                    mechanism_map.setdefault(inventory_item_id, []).append(mechanism)

    rows: list[dict[str, Any]] = []
    coverage_blockers: list[dict[str, str]] = [
        _stable_blocker(
            "SEALED-SOURCE-LINK-PROTOCOL-UNAVAILABLE",
            "exact source excerpts prove candidate links only; this protocol does "
            "not verify producer, consumer, failure, or recovery semantics",
        )
    ]
    if not protocol_blockers:
        for sample_id, sample in sorted(sample_records.items()):
            mapped = sorted(
                mechanism_map.get(sample["inventory_item_id"], []),
                key=lambda item: item.get("mechanism_id", ""),
            )
            candidate_traces = [
                trace_hash
                for trace_hash, trace in trace_records.get(sample_id, [])
                if trace["assessment"] == "candidate_source_chain"
                and all(trace["source_chain"][field] for field in (
                    "definitions",
                    "producers_or_callers",
                    "consumers",
                    "failure_paths",
                    "recovery_paths",
                ))
            ]
            if not mapped:
                coverage_state = "unmapped"
            elif not candidate_traces:
                coverage_state = "declared_only"
            else:
                coverage_state = "candidate_mapped"
            blocking = (
                sample["coverage_strategy"] in REQUIRED_STRATEGIES
                and coverage_state != "source_chain_verified"
            )
            if blocking:
                coverage_blockers.append(
                    _stable_blocker(
                        f"COVERAGE-{coverage_state.upper()}",
                        sample["blocking_reason"]
                        or f"required inventory object is only {coverage_state}",
                        sample["inventory_item_id"],
                        sample["inventory_item_id"],
                    )
                )
            rows.append(
                {
                    "sample_id": sample_id,
                    "inventory_item_id": sample["inventory_item_id"],
                    "kind": sample["kind"],
                    "criticality": sample["criticality"],
                    "coverage_strategy": sample["coverage_strategy"],
                    "blocking_reason": sample["blocking_reason"],
                    "coverage_state": coverage_state,
                    "mechanism_ids": [item["mechanism_id"] for item in mapped],
                    "trace_hashes": sorted(set(candidate_traces)),
                    "runtime_evidence_ids": [],
                    "blocking": blocking,
                }
            )
    all_blockers = sorted(
        {
            item["blocker_id"]: item
            for item in [*protocol_blockers, *coverage_blockers]
        }.values(),
        key=lambda item: item["blocker_id"],
    )
    state_names = (
        "unmapped",
        "declared_only",
        "candidate_mapped",
        "source_chain_verified",
        "runtime_verified",
    )
    unsigned = {
        "schema_version": "coverage-verdict/v2",
        "phase": "registry_comparison",
        "state": "blocked" if all_blockers else "evaluated",
        "agent": {"role_id": role_id, "agent_instance_id": agent_instance_id},
        "bindings": {
            "source_snapshot_hash": _sha_file(source_snapshot.resolve()),
            "sample_manifest_hashes": sorted(manifests),
            "trace_hashes": sorted(traces),
            "merge_report_hash": str(
                merge_report.get("report_hash", "sha256:" + "0" * 64)
            ),
            "combined_registry_hash": combined_registry_hash,
            "runtime_evidence": "absent",
            "roles_file_sha256": _sha_file(roles_path.resolve()),
            "prompt_template_hashes": sorted(
                {
                    coverage_prompt_template_hash(role_id),
                    *(manifest["sealed_inputs"]["prompt"]["template_hash"] for manifest in manifests.values()),
                }
            ),
        },
        "rows": [] if protocol_blockers else rows,
        "blockers": all_blockers,
        "counts": {
            "samples": 0 if protocol_blockers else len(rows),
            **{
                state: 0
                if protocol_blockers
                else sum(item["coverage_state"] == state for item in rows)
                for state in state_names
            },
            "blockers": len(all_blockers),
        },
    }
    verdict = {**unsigned, "verdict_hash": canonical_hash(unsigned)}
    validate_payload(verdict, VERDICT_SCHEMA, "coverage verdict")
    atomic_create_json(output_path.resolve(), verdict)
    return verdict
