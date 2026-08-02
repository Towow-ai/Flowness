from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .approval import load_trusted_owner_keys, validate_owner_approval
from .integrity import canonical_hash, verify_self_hash
from .models import utc_now
from .policy import load_approved_policy
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload

DEFAULT_CANDIDATE_SCHEMA = SCHEMAS_ROOT / "release-candidate.schema.json"
DEFAULT_REPORT_SCHEMA = SCHEMAS_ROOT / "jury-report.schema.json"
DEFAULT_DECISION_SCHEMA = SCHEMAS_ROOT / "release-decision.schema.json"


def _system_blocker(blockers: list[str], suffix: str) -> None:
    blocker = f"SYSTEM-{suffix}"
    if blocker not in blockers:
        blockers.append(blocker)


def _index_unique(
    rows: list[dict[str, Any]],
    key: str,
    blockers: list[str],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if not value or value in index:
            _system_blocker(blockers, f"INVALID-{key.upper()}")
            continue
        index[value] = row
    return index


def _highest_maturity(
    evidence_types: set[str],
    levels: dict[str, list[str]],
) -> int:
    highest = 0
    accumulated: set[str] = set()
    for raw_level in sorted(levels, key=int):
        accumulated.update(levels[raw_level])
        if accumulated.issubset(evidence_types):
            highest = int(raw_level)
        else:
            break
    return highest


def _role_requirements(value: Any) -> dict[str, int]:
    if not isinstance(value, list):
        return {}
    requirements: dict[str, int] = {}
    for item in value:
        if isinstance(item, str):
            role_id = item
            count = 1
        elif isinstance(item, dict):
            role_id = item.get("role_id")
            count = item.get("min_count", 1)
        else:
            return {}
        if (
            not isinstance(role_id, str)
            or not role_id
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or role_id in requirements
        ):
            return {}
        requirements[role_id] = count
    return requirements


def _check_has_trusted_evidence(
    check: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    snapshot_id: str | None,
) -> bool:
    evidence_ids = check.get("evidence_ids")
    return bool(
        isinstance(evidence_ids, list)
        and evidence_ids
        and all(
            evidence_id in evidence
            and evidence[evidence_id].get("snapshot_id") == snapshot_id
            for evidence_id in evidence_ids
        )
    )


def _trusted_fail(
    check: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    snapshot_id: str | None,
) -> bool:
    return bool(
        check.get("verdict") == "fail"
        and check.get("blocker_id")
        and _check_has_trusted_evidence(check, evidence, snapshot_id)
        and check.get("observed")
        and check.get("reproduction")
        and check.get("retest_condition")
    )


def _blocking_check(check: dict[str, Any]) -> bool:
    return bool(
        check.get("verdict") == "fail"
        or (
            check.get("verdict") == "unknown"
            and check.get("critical") is True
        )
    )


def _latest_timestamp(values: list[str]) -> str:
    parsed: list[datetime] = []
    for value in values:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is not None:
            parsed.append(timestamp.astimezone(UTC))
    return max(parsed).isoformat() if parsed else utc_now()


def _report_is_structurally_valid(
    report: dict[str, Any],
    candidate: dict[str, Any],
    policy: dict[str, Any],
    roles: dict[str, dict[str, Any]],
) -> bool:
    judge = report.get("judge", {})
    role_id = judge.get("role_id")
    role = roles.get(role_id)
    phase = report.get("phase")
    checks = report.get("checks")
    check_ids = (
        [check.get("check_id") for check in checks if isinstance(check, dict)]
        if isinstance(checks, list)
        else []
    )
    if (
        not report.get("report_id")
        or report.get("candidate_id") != candidate.get("candidate_id")
        or report.get("snapshot_id")
        != candidate.get("snapshot", {}).get("snapshot_id")
        or report.get("policy_version") != policy.get("policy_version")
        or phase not in {"first_pass", "retest", "adjudication"}
        or role is None
        or not judge.get("agent_instance_id")
        or judge.get("organization") != "independent"
        or judge.get("conflicts") != []
        or not isinstance(checks, list)
        or len(check_ids) != len(checks)
        or any(not check_id for check_id in check_ids)
        or len(set(check_ids)) != len(check_ids)
    ):
        return False
    if phase == "adjudication":
        if role.get("kind") != "adjudicator" or not report.get("adjudication"):
            return False
    elif role.get("kind") != "judge":
        return False
    if phase == "first_pass":
        attestations = judge.get("attestations", {})
        if not all(
            attestations.get(key) is True
            for key in (
                "blind_first_pass",
                "no_shared_verdicts",
                "not_candidate_author",
            )
        ):
            return False
    role_dimensions = set(role.get("dimensions", []))
    role_gates = set(role.get("gates", []))
    role_checks = set(role.get("checks", []))
    for check in checks:
        if check.get("verdict") not in {"pass", "fail", "unknown", "na"}:
            return False
        if phase == "adjudication":
            if check.get("gate_id") not in role_gates:
                return False
        elif (
            check.get("dimension") not in role_dimensions
            or check.get("gate_id") not in role_gates
            or check.get("check_id") not in role_checks
        ):
            return False
    return True


def _validate_lifecycle(
    reports: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    snapshot_id: str | None,
    blockers: list[str],
) -> dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]]:
    report_by_id = {report["report_id"]: report for report in reports}
    judge_agents = {
        report["judge"]["agent_instance_id"]
        for report in reports
        if report["phase"] in {"first_pass", "retest"}
    }
    decisions: dict[tuple[str, str], str] = {}
    cleared: dict[
        tuple[str, str],
        tuple[dict[str, Any], dict[str, Any]],
    ] = {}
    for adjudicator in (
        report for report in reports if report["phase"] == "adjudication"
    ):
        report_id = adjudicator["report_id"]
        adjudication = adjudicator.get("adjudication", {})
        resolution = adjudication.get("resolution")
        disputed_ids = adjudication.get("disputed_report_ids")
        if (
            adjudicator["judge"]["agent_instance_id"] in judge_agents
            or resolution not in {"uphold_fail", "clear_after_retest"}
            or not isinstance(disputed_ids, list)
            or not disputed_ids
        ):
            _system_blocker(blockers, f"INVALID-ADJUDICATION-{report_id}")
            continue
        disputed_reports = [report_by_id.get(item) for item in disputed_ids]
        if any(
            report is None or report.get("phase") != "first_pass"
            for report in disputed_reports
        ):
            _system_blocker(blockers, f"INVALID-ADJUDICATION-{report_id}")
            continue
        original_checks: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for report in disputed_reports:
            assert report is not None
            for check in report["checks"]:
                if _blocking_check(check):
                    original_checks.append((report, check))
        if not original_checks:
            _system_blocker(blockers, f"INVALID-ADJUDICATION-{report_id}")
            continue

        # This evaluator receives one candidate and one snapshot.  A retest in
        # that same input set may help diagnosis, but it cannot rewrite the
        # historical first-pass failure into a release pass.  The only valid
        # repair route is a separately bound successor candidate bundle.
        if resolution == "clear_after_retest":
            _system_blocker(
                blockers,
                f"SAME-CANDIDATE-RETEST-FORBIDDEN-{report_id}",
            )
            continue

        retest = report_by_id.get(adjudication.get("retest_report_id"))
        adjudication_checks = {
            check["check_id"]: check for check in adjudicator["checks"]
        }
        valid_clear = resolution == "clear_after_retest"
        if valid_clear and (
            retest is None
            or retest.get("phase") != "retest"
            or retest["judge"]["agent_instance_id"]
            == adjudicator["judge"]["agent_instance_id"]
        ):
            valid_clear = False
        retest_checks = (
            {check["check_id"]: check for check in retest["checks"]}
            if retest is not None and retest.get("phase") == "retest"
            else {}
        )
        for original_report, original_check in original_checks:
            key = (original_report["report_id"], original_check["check_id"])
            prior = decisions.get(key)
            if prior is not None:
                suffix = (
                    "CONFLICTING-ADJUDICATION"
                    if prior != resolution
                    else "DUPLICATE-ADJUDICATION"
                )
                _system_blocker(blockers, f"{suffix}-{report_id}")
                valid_clear = False
                continue
            decisions[key] = resolution
            if resolution != "clear_after_retest":
                continue
            retest_check = retest_checks.get(original_check["check_id"])
            adjudication_check = adjudication_checks.get(original_check["check_id"])
            if (
                retest is None
                or retest["judge"]["role_id"]
                != original_report["judge"]["role_id"]
                or retest_check is None
                or adjudication_check is None
                or retest_check.get("verdict") != "pass"
                or adjudication_check.get("verdict") != "pass"
                or retest_check.get("gate_id") != original_check.get("gate_id")
                or retest_check.get("dimension") != original_check.get("dimension")
                or not _check_has_trusted_evidence(
                    retest_check,
                    evidence,
                    snapshot_id,
                )
            ):
                valid_clear = False
                continue
            cleared[key] = (retest, retest_check)
        if resolution == "clear_after_retest" and not valid_clear:
            _system_blocker(blockers, f"INVALID-ADJUDICATION-{report_id}")
            for original_report, original_check in original_checks:
                cleared.pop(
                    (original_report["report_id"], original_check["check_id"]),
                    None,
                )
    return cleared


def _module_results(
    candidate: dict[str, Any],
    policy: dict[str, Any],
    profile: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    modules: dict[str, dict[str, Any]],
    blockers: list[str],
) -> list[dict[str, Any]]:
    snapshot_id = candidate.get("snapshot", {}).get("snapshot_id")
    evidence_by_module: dict[str, set[str]] = {}
    for module_id, module in modules.items():
        referenced = module.get("evidence_ids", [])
        evidence_by_module[module_id] = {
            evidence[evidence_id].get("type")
            for evidence_id in referenced
            if evidence_id in evidence
            and evidence[evidence_id].get("snapshot_id") == snapshot_id
        }
        if any(evidence_id not in evidence for evidence_id in referenced):
            _system_blocker(blockers, f"DANGLING-EVIDENCE-{module_id}")
        if any(
            evidence_id in evidence
            and evidence[evidence_id].get("snapshot_id") != snapshot_id
            for evidence_id in referenced
        ):
            _system_blocker(blockers, f"STALE-EVIDENCE-{module_id}")

    requirements: dict[str, int] = {}
    for requirement in profile.get("module_requirements", []):
        if "module_id" in requirement:
            requirements[requirement["module_id"]] = requirement["min_maturity"]
            continue
        selector = requirement.get("selector", {})
        for module_id, module in modules.items():
            layer_match = (
                not selector.get("layer")
                or module.get("layer") in selector["layer"]
            )
            critical_match = (
                "critical" not in selector
                or module.get("critical") is selector["critical"]
            )
            if layer_match and critical_match:
                requirements[module_id] = max(
                    requirements.get(module_id, 0),
                    requirement["min_maturity"],
                )

    levels = policy.get("maturity_levels", {})
    effective_levels = {
        module_id: min(
            int(module.get("declared_maturity", 0)),
            _highest_maturity(evidence_by_module[module_id], levels),
        )
        for module_id, module in modules.items()
    }
    results: list[dict[str, Any]] = []
    for module_id, module in modules.items():
        dependency_pass = True
        missing_evidence: list[str] = []
        for dependency in module.get("dependencies", []):
            dependency_id = dependency.get("module_id")
            if dependency_id not in modules:
                dependency_pass = False
                missing_evidence.append(f"missing dependency {dependency_id}")
            elif effective_levels.get(dependency_id, 0) < int(
                dependency.get("min_maturity", 0)
            ):
                dependency_pass = False
                missing_evidence.append(f"dependency maturity {dependency_id}")
        required = requirements.get(module_id, 0)
        effective = effective_levels[module_id]
        passed = effective >= required and dependency_pass
        if required and not passed:
            blockers.append(f"MATURITY-{module_id}")
        results.append(
            {
                "module_id": module_id,
                "declared_maturity": int(module.get("declared_maturity", 0)),
                "effective_maturity": effective,
                "required_maturity": required,
                "dependency_pass": dependency_pass,
                "missing_evidence": missing_evidence,
                "effective_verdict": "pass" if passed else "blocked",
            }
        )
    for required_module in requirements:
        if required_module not in modules:
            blockers.append(f"MISSING-MODULE-{required_module}")
    return sorted(results, key=lambda item: item["module_id"])


def evaluate_release(
    candidate: dict[str, Any],
    reports: list[dict[str, Any]],
    policy: dict[str, Any],
    owner_approval: dict[str, Any] | None = None,
    policy_hash: str | None = None,
    trusted_owner_keys: dict[str, Any] | None = None,
    jury_bundle: dict[str, str] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    snapshot_id = candidate.get("snapshot", {}).get("snapshot_id")
    if not snapshot_id or candidate.get("snapshot", {}).get("dirty") is not False:
        _system_blocker(blockers, "UNSEALED-SNAPSHOT")
    if policy.get("policy_version") is None:
        _system_blocker(blockers, "MISSING-POLICY-VERSION")
    effective_policy_hash = policy_hash or canonical_hash(policy)

    evidence = _index_unique(candidate.get("evidence", []), "evidence_id", blockers)
    modules = _index_unique(candidate.get("modules", []), "module_id", blockers)
    claims = _index_unique(candidate.get("claims", []), "claim_id", blockers)
    for claim_id, claim in claims.items():
        referenced = claim.get("evidence_ids", [])
        if any(evidence_id not in evidence for evidence_id in referenced):
            _system_blocker(blockers, f"DANGLING-CLAIM-EVIDENCE-{claim_id}")
        if any(
            evidence_id in evidence
            and evidence[evidence_id].get("snapshot_id") != snapshot_id
            for evidence_id in referenced
        ):
            _system_blocker(blockers, f"STALE-CLAIM-EVIDENCE-{claim_id}")

    stage = candidate.get("target_stage")
    profile = policy.get("stage_profiles", {}).get(stage)
    if not profile:
        _system_blocker(blockers, "UNKNOWN-STAGE")
        profile = {"module_requirements": [], "mandatory_gates": []}
    module_results = _module_results(
        candidate,
        policy,
        profile,
        evidence,
        modules,
        blockers,
    )

    roles = _index_unique(policy.get("roles", []), "role_id", blockers)
    policy_gates = _index_unique(policy.get("gates", []), "gate_id", blockers)
    valid_reports: list[dict[str, Any]] = []
    seen_report_ids: set[str] = set()
    first_pass_agents: dict[str, str] = {}
    first_pass_role_agents: set[tuple[str, str]] = set()
    for report in reports:
        report_id = report.get("report_id")
        if (
            report_id in seen_report_ids
            or not _report_is_structurally_valid(
                report,
                candidate,
                policy,
                roles,
            )
        ):
            _system_blocker(
                blockers,
                f"INVALID-REPORT-{report_id or 'unknown'}",
            )
            continue
        seen_report_ids.add(report_id)
        if report["phase"] == "first_pass":
            role_id = report["judge"]["role_id"]
            agent_id = report["judge"]["agent_instance_id"]
            if (
                (role_id, agent_id) in first_pass_role_agents
                or (
                    agent_id in first_pass_agents
                    and first_pass_agents[agent_id] != role_id
                )
            ):
                _system_blocker(blockers, f"NONINDEPENDENT-JUDGE-{role_id}")
                continue
            first_pass_role_agents.add((role_id, agent_id))
            first_pass_agents[agent_id] = role_id
        valid_reports.append(report)

    cleared = _validate_lifecycle(
        valid_reports,
        evidence,
        snapshot_id,
        blockers,
    )
    first_pass_checks: dict[
        str,
        list[tuple[dict[str, Any], dict[str, Any]]],
    ] = defaultdict(list)
    for report in valid_reports:
        if report["phase"] != "first_pass":
            continue
        for check in report["checks"]:
            replacement = cleared.get((report["report_id"], check["check_id"]))
            first_pass_checks[check["check_id"]].append(
                replacement if replacement is not None else (report, check)
            )

    min_judges = int(
        policy.get("independence_rules", {}).get("judges_per_check", 2)
    )
    if min_judges < 2:
        _system_blocker(blockers, "INVALID-JUDGES-PER-CHECK")
        min_judges = 2
    gate_results: list[dict[str, Any]] = []
    for gate_id in profile.get("mandatory_gates", []):
        gate = policy_gates.get(gate_id)
        if not gate:
            blockers.append(f"MISSING-GATE-{gate_id}")
            continue
        gate_reasons: list[str] = []
        check_results: list[dict[str, Any]] = []
        gate_role_agents: dict[str, set[str]] = defaultdict(set)
        for required_check in gate.get("required_checks", []):
            check_id = required_check.get("check_id")
            dimension = required_check.get("dimension")
            required_roles = _role_requirements(
                required_check.get("required_roles")
            )
            allowed_roles = set(required_check.get("allowed_roles", []))
            if (
                not check_id
                or not dimension
                or len(required_roles) != 2
                or set(required_roles) != allowed_roles
                or any(role_id not in roles for role_id in allowed_roles)
                or any(
                    roles[role_id].get("kind") != "judge"
                    or dimension not in roles[role_id].get("dimensions", [])
                    or gate_id not in roles[role_id].get("gates", [])
                    or check_id not in roles[role_id].get("checks", [])
                    for role_id in allowed_roles
                )
            ):
                _system_blocker(blockers, f"INVALID-POLICY-CHECK-{check_id}")
                gate_reasons.append(f"{check_id}: invalid policy check")
                continue

            all_entries = first_pass_checks.get(check_id, [])
            entries = [
                (report, check)
                for report, check in all_entries
                if report["judge"]["role_id"] in allowed_roles
                and check.get("gate_id") == gate_id
                and check.get("dimension") == dimension
                and check.get("critical") is required_check.get("critical")
            ]
            if len(entries) != len(all_entries):
                _system_blocker(blockers, f"UNAUTHORIZED-CHECK-{check_id}")
            states = [check.get("verdict") for _, check in entries]
            role_coverage = {
                role_id: len(
                    {
                        report["judge"]["agent_instance_id"]
                        for report, _ in entries
                        if report["judge"]["role_id"] == role_id
                    }
                )
                for role_id in required_roles
            }
            for report, _ in entries:
                gate_role_agents[report["judge"]["role_id"]].add(
                    report["judge"]["agent_instance_id"]
                )
            trusted_fail = any(
                _trusted_fail(check, evidence, snapshot_id)
                for _, check in entries
            )
            untrusted_fail = any(
                check.get("verdict") == "fail"
                and not _trusted_fail(check, evidence, snapshot_id)
                for _, check in entries
            )
            for _, check in entries:
                if _trusted_fail(check, evidence, snapshot_id):
                    blockers.append(check["blocker_id"])
            missing_roles = any(
                role_coverage[role_id] < count
                for role_id, count in required_roles.items()
            )
            unique_agents = {
                report["judge"]["agent_instance_id"] for report, _ in entries
            }
            if trusted_fail:
                verdict = "fail"
            elif required_check.get("critical") and "unknown" in states:
                verdict = "blocked_missing_evidence"
                unknown_blockers = {
                    check.get("blocker_id")
                    for _, check in entries
                    if check.get("verdict") == "unknown"
                    and isinstance(check.get("blocker_id"), str)
                    and check["blocker_id"]
                }
                if not unknown_blockers:
                    _system_blocker(
                        blockers,
                        f"MISSING-CRITICAL-UNKNOWN-BLOCKER-{check_id}",
                    )
                else:
                    blockers.extend(sorted(unknown_blockers))
            elif untrusted_fail:
                verdict = "pending"
                _system_blocker(blockers, f"UNTRUSTED-FAIL-{check_id}")
            elif len(unique_agents) < min_judges or missing_roles:
                verdict = "pending"
                blockers.append(f"JURY-COVERAGE-{check_id}")
            elif states and all(state == "pass" for state in states):
                verdict = "pass"
            elif (
                required_check.get("allow_na")
                and states
                and all(state == "na" for state in states)
            ):
                verdict = "pass"
            else:
                verdict = "pending"
                blockers.append(f"CHECK-{check_id}")
            check_results.append(
                {
                    "check_id": check_id,
                    "verdict": verdict,
                    "judge_states": states or ["unknown"],
                }
            )
            if verdict != "pass":
                gate_reasons.append(f"{check_id}: {verdict}")
        gate_results.append(
            {
                "gate_id": gate_id,
                "mandatory": True,
                "check_results": check_results,
                "role_coverage": {
                    role_id: len(agent_ids)
                    for role_id, agent_ids in sorted(gate_role_agents.items())
                },
                "verdict": "pass" if not gate_reasons else "blocked",
                "reasons": gate_reasons,
            }
        )

    blockers = sorted(set(blockers))
    engine_verdict = "blocked" if blockers else "pass"
    timestamps = [
        value
        for value in (
            candidate.get("created_at"),
            *(report.get("finished_at") for report in valid_reports),
        )
        if isinstance(value, str) and value
    ]
    computed_at = _latest_timestamp(timestamps)
    engine_result = {
        "candidate_id": candidate.get("candidate_id"),
        "snapshot_id": snapshot_id,
        "policy_version": policy.get("policy_version"),
        "target_stage": stage,
        "computed_at": computed_at,
        "module_results": module_results,
        "gate_results": gate_results,
        "open_blockers": blockers,
        "engine_verdict": engine_verdict,
    }
    if jury_bundle is not None:
        engine_result["jury_bundle"] = jury_bundle
    decision_hash = canonical_hash(engine_result)
    owner_decision = validate_owner_approval(
        owner_approval,
        candidate,
        effective_policy_hash,
        decision_hash,
        trusted_owner_keys,
    )
    if engine_verdict != "pass" and owner_decision == "approve":
        raise ValidationError("owner approval cannot override release blockers")
    result = {
        **engine_result,
        "owner_decision": owner_decision,
        "release_authorized": (
            engine_verdict == "pass" and owner_decision == "approve"
        ),
        "decision_hash": decision_hash,
    }
    return result


def evaluate_release_bundle(
    bundle_dir: Path,
    owner_approval: dict[str, Any] | None = None,
    trusted_owner_keys: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate exactly one verified ``jury-bundle/v1`` and nothing else.

    A result is still only a decision.  It performs no repository change,
    release, transfer, or publication.  An owner approval remains necessary
    before ``release_authorized`` can become true.
    """

    from .jury_bundle import load_verified_jury_bundle_inputs

    bundle, candidate, reports, policy = load_verified_jury_bundle_inputs(
        bundle_dir
    )
    return evaluate_release(
        candidate,
        reports,
        policy,
        owner_approval,
        bundle["policy"]["sha256"],
        trusted_owner_keys,
        {
            "bundle_id": bundle["bundle_id"],
            "bundle_hash": bundle["bundle_hash"],
        },
    )


def evaluate_release_files(
    candidate_path: Path,
    report_paths: list[Path],
    output: Path,
    approval_path: Path | None = None,
    trusted_keys_path: Path | None = None,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    candidate = load_validated_json(
        candidate_path,
        DEFAULT_CANDIDATE_SCHEMA,
        "release candidate",
    )
    policy, policy_hash = load_approved_policy(policy_path)
    reports = [
        load_validated_json(path, DEFAULT_REPORT_SCHEMA, "jury report")
        for path in report_paths
    ]
    for report in reports:
        verify_self_hash(report)
    approval = None
    trusted_keys = None
    if approval_path is not None:
        from .approval import OWNER_APPROVAL_SCHEMA

        approval = load_validated_json(
            approval_path,
            OWNER_APPROVAL_SCHEMA,
            "owner approval",
        )
        if trusted_keys_path is None:
            raise ValidationError(
                "owner approval requires --trusted-owner-keys"
            )
        trusted_keys = load_trusted_owner_keys(trusted_keys_path)
    result = evaluate_release(
        candidate,
        reports,
        policy,
        approval,
        policy_hash,
        trusted_keys,
    )
    validate_payload(result, DEFAULT_DECISION_SCHEMA, "release decision")
    if output.exists() or output.is_symlink():
        raise ValidationError("release decision output already exists")
    atomic_create_json(output, result)
    return result


def evaluate_release_bundle_files(
    bundle_dir: Path,
    output: Path,
    approval_path: Path | None = None,
    trusted_keys_path: Path | None = None,
) -> dict[str, Any]:
    """Write a decision sourced solely from one verified jury bundle."""

    approval = None
    trusted_keys = None
    if approval_path is not None:
        from .approval import OWNER_APPROVAL_SCHEMA

        approval = load_validated_json(
            approval_path,
            OWNER_APPROVAL_SCHEMA,
            "owner approval",
        )
        if trusted_keys_path is None:
            raise ValidationError(
                "owner approval requires --trusted-owner-keys"
            )
        trusted_keys = load_trusted_owner_keys(trusted_keys_path)
    result = evaluate_release_bundle(bundle_dir, approval, trusted_keys)
    validate_payload(result, DEFAULT_DECISION_SCHEMA, "release decision")
    if output.exists() or output.is_symlink():
        raise ValidationError("release decision output already exists")
    atomic_create_json(output, result)
    return result
