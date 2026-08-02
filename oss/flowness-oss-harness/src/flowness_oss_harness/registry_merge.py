from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from .controller import SAFE_COMPONENT, _hash_file
from .integrity import canonical_hash
from .registry import ValidationError, atomic_create_json

MERGE_OUTPUT_FILES = {
    "mechanisms": "combined-mechanisms-registry.json",
    "unknowns": "combined-unknown-registry.json",
    "drift_findings": "combined-drift-registry.json",
    "report": "merge-report.json",
}

RegistryGroup = tuple[Path, Path, Path, Path]


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object: {path}")
    return payload


def _load_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path}") from exc
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise ValidationError(f"{label} must be an array of objects: {path}")
    return payload


def _add_blocker(
    blockers: list[dict[str, str]],
    code: str,
    detail: str,
    run_id: str = "",
    object_id: str = "",
) -> None:
    blockers.append(
        {
            "code": code,
            "run_id": run_id,
            "object_id": object_id,
            "detail": detail,
        }
    )


def _prefixed_id(run_id: str, value: str) -> str:
    return f"{run_id}::{value}"


def _evidence_definition(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in evidence.items()
        if key
        not in {
            "source_run_id",
            "reconciled_evidence_id",
        }
    }


def _rewrite_evidence(evidence: dict[str, Any], run_id: str) -> dict[str, Any]:
    original_id = evidence.get("evidence_id")
    if not isinstance(original_id, str) or not original_id:
        raise ValidationError("reconciled evidence requires evidence_id")
    return {
        **deepcopy(evidence),
        "evidence_id": _prefixed_id(run_id, original_id),
        "reconciled_evidence_id": original_id,
        "source_run_id": run_id,
    }


def _mechanism_semantics(mechanism: dict[str, Any]) -> dict[str, Any]:
    semantic = deepcopy(mechanism)
    for field in (
        "evidence",
        "known_drift",
        "source_roles",
        "source_runs",
        "source_provenance",
    ):
        semantic.pop(field, None)
    return semantic


def _deduplicate_by_id(
    items: list[dict[str, Any]],
    id_field: str,
) -> list[dict[str, Any]]:
    return sorted(
        {item[id_field]: item for item in items}.values(),
        key=lambda item: item[id_field],
    )


def merge_reconciled_registries(
    groups: Sequence[RegistryGroup],
    output_dir: Path,
) -> dict[str, Any]:
    if not groups:
        raise ValidationError("at least one reconciled registry group is required")
    output_dir = output_dir.resolve()
    targets = {
        key: output_dir / filename
        for key, filename in MERGE_OUTPUT_FILES.items()
    }
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise ValidationError(
            "refusing to overwrite registry merge output: " + ", ".join(existing)
        )

    loaded_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(groups, 1):
        if len(raw_group) != 4:
            raise ValidationError(
                "each registry group requires report, mechanisms, unknowns, drift"
            )
        report_path, mechanisms_path, unknowns_path, drift_path = (
            Path(item).resolve() for item in raw_group
        )
        report = _load_object(report_path, f"group {index} reconciliation report")
        mechanisms = _load_array(
            mechanisms_path, f"group {index} mechanisms registry"
        )
        unknowns = _load_array(unknowns_path, f"group {index} unknown registry")
        drift_findings = _load_array(
            drift_path, f"group {index} drift registry"
        )
        run_id = report.get("run_id")
        if not isinstance(run_id, str) or not SAFE_COMPONENT.fullmatch(run_id):
            raise ValidationError(
                f"group {index} reconciliation report has unsafe run_id"
            )
        loaded_groups.append(
            {
                "run_id": run_id,
                "report_path": report_path,
                "mechanisms_path": mechanisms_path,
                "unknowns_path": unknowns_path,
                "drift_path": drift_path,
                "report": report,
                "mechanisms": mechanisms,
                "unknowns": unknowns,
                "drift_findings": drift_findings,
            }
        )
    loaded_groups.sort(
        key=lambda item: (
            item["run_id"],
            _hash_file(item["report_path"]),
            str(item["report_path"]),
        )
    )

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    input_records: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    evidence_definitions: dict[str, tuple[str, dict[str, Any]]] = {}
    mechanism_definitions: dict[str, dict[str, Any]] = {}
    mechanisms: dict[str, dict[str, Any]] = {}
    unknowns: list[dict[str, Any]] = []
    drift_findings: list[dict[str, Any]] = []

    for group in loaded_groups:
        run_id = group["run_id"]
        report = group["report"]
        if run_id in seen_runs:
            _add_blocker(
                blockers,
                "duplicate_run_id",
                "the same run_id was supplied more than once",
                run_id,
            )
        seen_runs.add(run_id)

        unsigned_report = {
            key: value for key, value in report.items() if key != "report_hash"
        }
        if report.get("report_hash") != canonical_hash(unsigned_report):
            _add_blocker(
                blockers,
                "invalid_reconciliation_report_hash",
                "reconciliation report self-hash does not match",
                run_id,
            )
        if report.get("state") != "reconciled":
            _add_blocker(
                blockers,
                "reconciliation_not_passed",
                f"expected report state reconciled, got {report.get('state')!r}",
                run_id,
            )
        if report.get("blockers") != []:
            _add_blocker(
                blockers,
                "reconciliation_has_blockers",
                "input reconciliation report must have blockers=[]",
                run_id,
            )

        output_bindings = report.get("outputs")
        registry_inputs = {
            "mechanisms": (
                group["mechanisms_path"],
                group["mechanisms"],
            ),
            "unknowns": (
                group["unknowns_path"],
                group["unknowns"],
            ),
            "drift_findings": (
                group["drift_path"],
                group["drift_findings"],
            ),
        }
        if not isinstance(output_bindings, dict):
            _add_blocker(
                blockers,
                "reconciliation_registry_binding_missing",
                "reconciliation report has no outputs registry bindings",
                run_id,
            )
        for registry_name, (registry_path, registry_payload) in registry_inputs.items():
            binding = (
                output_bindings.get(registry_name)
                if isinstance(output_bindings, dict)
                else None
            )
            if not isinstance(binding, dict):
                _add_blocker(
                    blockers,
                    "reconciliation_registry_binding_missing",
                    f"missing output binding for {registry_name}",
                    run_id,
                    registry_name,
                )
                continue
            actual_binding = {
                "filename": registry_path.name,
                "count": len(registry_payload),
                "content_hash": canonical_hash(registry_payload),
                "file_sha256": "sha256:" + _hash_file(registry_path),
            }
            if binding != actual_binding:
                _add_blocker(
                    blockers,
                    "reconciliation_registry_binding_mismatch",
                    (
                        f"sealed binding differs for {registry_name}: "
                        f"expected {binding}, actual {actual_binding}"
                    ),
                    run_id,
                    registry_name,
                )

        report_blockers = report.get("blockers")
        report_warnings = report.get("warnings")
        report_roles = report.get("roles")
        expected_counts = {
            "mechanisms": len(group["mechanisms"]),
            "unknowns": len(group["unknowns"]),
            "drift_findings": len(group["drift_findings"]),
            "blockers": (
                len(report_blockers) if isinstance(report_blockers, list) else -1
            ),
            "warnings": (
                len(report_warnings) if isinstance(report_warnings, list) else -1
            ),
            "roles": len(report_roles) if isinstance(report_roles, list) else -1,
        }
        report_counts = report.get("counts")
        if not isinstance(report_counts, dict) or any(
            report_counts.get(key) != value
            for key, value in expected_counts.items()
        ):
            _add_blocker(
                blockers,
                "reconciliation_count_mismatch",
                f"report counts do not match registries: expected {expected_counts}",
                run_id,
            )

        input_records.append(
            {
                "run_id": run_id,
                "report": {
                    "path": str(group["report_path"]),
                    "sha256": "sha256:" + _hash_file(group["report_path"]),
                },
                "mechanisms": {
                    "path": str(group["mechanisms_path"]),
                    "sha256": "sha256:" + _hash_file(group["mechanisms_path"]),
                },
                "unknowns": {
                    "path": str(group["unknowns_path"]),
                    "sha256": "sha256:" + _hash_file(group["unknowns_path"]),
                },
                "drift_findings": {
                    "path": str(group["drift_path"]),
                    "sha256": "sha256:" + _hash_file(group["drift_path"]),
                },
            }
        )

        group_drift_ids: set[str] = set()
        for finding in group["drift_findings"]:
            drift_id = finding.get("drift_id")
            if not isinstance(drift_id, str) or not drift_id:
                _add_blocker(
                    blockers,
                    "invalid_drift_id",
                    "drift registry item requires drift_id",
                    run_id,
                )
                continue
            if drift_id in group_drift_ids:
                _add_blocker(
                    blockers,
                    "duplicate_drift_id_in_run",
                    "drift_id occurs more than once in one reconciled registry",
                    run_id,
                    drift_id,
                )
                continue
            group_drift_ids.add(drift_id)

        def track_and_rewrite_evidence(
            items: Any, owner_id: str
        ) -> list[dict[str, Any]]:
            if not isinstance(items, list) or any(
                not isinstance(item, dict) for item in items
            ):
                _add_blocker(
                    blockers,
                    "invalid_evidence_collection",
                    "evidence must be an array of objects",
                    run_id,
                    owner_id,
                )
                return []
            rewritten: list[dict[str, Any]] = []
            for evidence in items:
                evidence_id = evidence.get("evidence_id")
                if not isinstance(evidence_id, str) or not evidence_id:
                    _add_blocker(
                        blockers,
                        "invalid_evidence_id",
                        "evidence item requires evidence_id",
                        run_id,
                        owner_id,
                    )
                    continue
                definition = _evidence_definition(evidence)
                previous = evidence_definitions.get(evidence_id)
                if previous is not None and previous[1] != definition:
                    _add_blocker(
                        blockers,
                        "conflicting_cross_run_evidence_definition",
                        f"evidence_id differs from definition in run {previous[0]}",
                        run_id,
                        evidence_id,
                    )
                else:
                    evidence_definitions[evidence_id] = (run_id, definition)
                rewritten.append(_rewrite_evidence(evidence, run_id))
            return rewritten

        group_unknown_ids: set[str] = set()
        for record in group["unknowns"]:
            unknown_id = record.get("unknown_id")
            if not isinstance(unknown_id, str) or not unknown_id:
                _add_blocker(
                    blockers,
                    "invalid_unknown_id",
                    "unknown registry item requires unknown_id",
                    run_id,
                )
                continue
            if unknown_id in group_unknown_ids:
                _add_blocker(
                    blockers,
                    "duplicate_unknown_id_in_run",
                    "unknown_id occurs more than once in one reconciled registry",
                    run_id,
                    unknown_id,
                )
                continue
            group_unknown_ids.add(unknown_id)
            unknowns.append(
                {
                    **deepcopy(record),
                    "unknown_id": _prefixed_id(run_id, unknown_id),
                    "reconciled_unknown_id": unknown_id,
                    "source_run_id": run_id,
                    "evidence": track_and_rewrite_evidence(
                        record.get("evidence"), unknown_id
                    ),
                }
            )

        for finding in group["drift_findings"]:
            drift_id = finding.get("drift_id")
            if not isinstance(drift_id, str) or drift_id not in group_drift_ids:
                continue
            drift_findings.append(
                {
                    **deepcopy(finding),
                    "drift_id": _prefixed_id(run_id, drift_id),
                    "reconciled_drift_id": drift_id,
                    "source_run_id": run_id,
                    "evidence": track_and_rewrite_evidence(
                        finding.get("evidence"), drift_id
                    ),
                }
            )

        group_mechanism_ids: set[str] = set()
        for mechanism in group["mechanisms"]:
            mechanism_id = mechanism.get("mechanism_id")
            if not isinstance(mechanism_id, str) or not mechanism_id:
                _add_blocker(
                    blockers,
                    "invalid_mechanism_id",
                    "mechanism registry item requires mechanism_id",
                    run_id,
                )
                continue
            if mechanism_id in group_mechanism_ids:
                _add_blocker(
                    blockers,
                    "duplicate_mechanism_id_in_run",
                    "mechanism_id occurs more than once in one reconciled registry",
                    run_id,
                    mechanism_id,
                )
                continue
            group_mechanism_ids.add(mechanism_id)
            known_drift = mechanism.get("known_drift")
            if not isinstance(known_drift, list) or any(
                not isinstance(item, str) or not item for item in known_drift
            ):
                _add_blocker(
                    blockers,
                    "invalid_known_drift",
                    "mechanism known_drift must be an array of strings",
                    run_id,
                    mechanism_id,
                )
                known_drift = []
            rewritten_known_drift: list[str] = []
            for reference in known_drift:
                if reference in group_drift_ids:
                    rewritten_known_drift.append(_prefixed_id(run_id, reference))
                elif "::" in reference and " " not in reference:
                    _add_blocker(
                        blockers,
                        "dangling_known_drift_reference",
                        "mechanism references a drift_id absent from its run registry",
                        run_id,
                        reference,
                    )
                else:
                    rewritten_known_drift.append(reference)

            semantic_definition = _mechanism_semantics(mechanism)
            previous_definition = mechanism_definitions.get(mechanism_id)
            if (
                previous_definition is not None
                and previous_definition != semantic_definition
            ):
                _add_blocker(
                    blockers,
                    "conflicting_cross_run_mechanism_definition",
                    "same mechanism_id has different semantic definitions",
                    run_id,
                    mechanism_id,
                )
                continue
            rewritten_evidence = track_and_rewrite_evidence(
                mechanism.get("evidence"), mechanism_id
            )
            source_roles = mechanism.get("source_roles")
            if not isinstance(source_roles, list) or any(
                not isinstance(item, str) or not item for item in source_roles
            ):
                _add_blocker(
                    blockers,
                    "invalid_mechanism_source_roles",
                    "mechanism source_roles must be an array of strings",
                    run_id,
                    mechanism_id,
                )
                source_roles = []
            provenance = {
                "run_id": run_id,
                "source_roles": sorted(set(source_roles)),
            }
            if previous_definition is None:
                mechanism_definitions[mechanism_id] = semantic_definition
                mechanisms[mechanism_id] = {
                    **deepcopy(mechanism),
                    "known_drift": rewritten_known_drift,
                    "evidence": rewritten_evidence,
                    "source_roles": sorted(set(source_roles)),
                    "source_runs": [run_id],
                    "source_provenance": [provenance],
                }
            else:
                existing = mechanisms[mechanism_id]
                existing["known_drift"].extend(rewritten_known_drift)
                existing["evidence"].extend(rewritten_evidence)
                existing["source_roles"].extend(source_roles)
                existing["source_runs"].append(run_id)
                existing["source_provenance"].append(provenance)

    combined_mechanisms = sorted(
        mechanisms.values(), key=lambda item: item["mechanism_id"]
    )
    for mechanism in combined_mechanisms:
        mechanism["known_drift"] = sorted(set(mechanism["known_drift"]))
        mechanism["source_roles"] = sorted(set(mechanism["source_roles"]))
        mechanism["source_runs"] = sorted(set(mechanism["source_runs"]))
        mechanism["source_provenance"] = sorted(
            mechanism["source_provenance"], key=lambda item: item["run_id"]
        )
        mechanism["evidence"] = _deduplicate_by_id(
            mechanism["evidence"], "evidence_id"
        )
    combined_unknowns = sorted(unknowns, key=lambda item: item["unknown_id"])
    combined_drift = sorted(
        drift_findings, key=lambda item: item["drift_id"]
    )
    blockers = sorted(
        {json.dumps(item, sort_keys=True): item for item in blockers}.values(),
        key=lambda item: (
            item["code"],
            item["run_id"],
            item["object_id"],
            item["detail"],
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bindings: dict[str, dict[str, Any]] = {}
    if not blockers:
        combined_payloads = {
            "mechanisms": combined_mechanisms,
            "unknowns": combined_unknowns,
            "drift_findings": combined_drift,
        }
        for registry_name, payload in combined_payloads.items():
            atomic_create_json(targets[registry_name], payload)
            output_bindings[registry_name] = {
                "filename": targets[registry_name].name,
                "count": len(payload),
                "content_hash": canonical_hash(payload),
                "file_sha256": "sha256:" + _hash_file(targets[registry_name]),
            }

    unsigned_report = {
        "schema_version": "registry-merge-report/v1",
        "state": "blocked" if blockers else "merged",
        "inputs": {"groups": input_records},
        "outputs": output_bindings,
        "rules": {
            "id_namespace": "run_id::reconciled_id",
            "mechanism_status": "preserve_without_promotion",
            "mechanism_conflict": "fail_closed_without_guessing",
            "evidence_conflict": "same_reconciled_id_requires_exact_definition",
        },
        "blockers": blockers,
        "warnings": warnings,
        "counts": {
            "groups": len(loaded_groups),
            "source_runs": len(seen_runs),
            "mechanisms": len(combined_mechanisms),
            "unknowns": len(combined_unknowns),
            "drift_findings": len(combined_drift),
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
    }
    report = {**unsigned_report, "report_hash": canonical_hash(unsigned_report)}
    atomic_create_json(targets["report"], report)
    return report
