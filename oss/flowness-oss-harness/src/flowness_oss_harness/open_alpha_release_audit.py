from __future__ import annotations

"""Fail-closed release audit for the broadened Flowness Open Alpha scope.

The audit consumes the file-exact package manifest. It never reads excluded
files, never emits matched secret text, and never converts repository authorship
into a legal-rights conclusion. Owner authorization intentionally remains an
external, irreversible gate.
"""

import fnmatch
import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError
from .schema_validation import validate_payload


SCHEMA_VERSION = "open-alpha-release-audit/v2"
POLICY_VERSION = "open-alpha-release-audit-policy/v2"
RIGHTS_ATTESTATION_SCHEMA_VERSION = "open-alpha-owner-rights-attestation/v1"
RIGHTS_ATTESTATION_SCHEMA_PATH = (
    "oss/flowness-oss-harness/schemas/"
    "open-alpha-owner-rights-attestation.schema.json"
)
RIGHTS_POLICY_PATH = "oss/flowness-oss-harness/config/open-alpha-release-audit.json"
_POLICY_KEYS = {
    "schema_version",
    "policy_id",
    "license_plan",
    "package_metadata",
    "public_package_assembly",
    "community_files",
    "security_contact",
    "rights_groups",
    "supply_chain",
    "cleanroom_acceptance",
    "sensitive_rules",
    "line_allowances",
    "owner_authorization",
}


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or ".." in pure.parts:
        raise ValidationError("OPEN-ALPHA-AUDIT-PATH-INVALID")
    root = root.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValidationError("OPEN-ALPHA-AUDIT-PATH-ESCAPES-ROOT") from exc
    if path.is_symlink() or not path.is_file():
        raise ValidationError("OPEN-ALPHA-AUDIT-FILE-UNSAFE")
    return path


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("OPEN-ALPHA-AUDIT-POLICY-INVALID") from exc
    if not isinstance(payload, dict) or set(payload) != _POLICY_KEYS:
        raise ValidationError("OPEN-ALPHA-AUDIT-POLICY-SHAPE-INVALID")
    if payload["schema_version"] != POLICY_VERSION:
        raise ValidationError("OPEN-ALPHA-AUDIT-POLICY-VERSION-INVALID")
    for key in ("package_metadata", "community_files", "rights_groups", "sensitive_rules", "line_allowances"):
        if not isinstance(payload[key], list):
            raise ValidationError("OPEN-ALPHA-AUDIT-POLICY-SHAPE-INVALID")
    if not payload["rights_groups"] or not payload["sensitive_rules"]:
        raise ValidationError("OPEN-ALPHA-AUDIT-POLICY-COVERAGE-EMPTY")
    license_plan = payload["license_plan"]
    if not isinstance(license_plan, dict) or set(license_plan) != {
        "state", "code_spdx", "documentation_spdx", "legacy_spdx",
        "proprietary_boundary", "candidate_files", "full_license_texts_present",
        "license_texts", "spdx_header_policy",
    }:
        raise ValidationError("OPEN-ALPHA-AUDIT-LICENSE-PLAN-INVALID")
    if license_plan["state"] != "active_candidate_license_selection":
        raise ValidationError("OPEN-ALPHA-AUDIT-LICENSE-STATE-INVALID")
    if not isinstance(license_plan["candidate_files"], list) or not isinstance(
        license_plan["license_texts"], list
    ):
        raise ValidationError("OPEN-ALPHA-AUDIT-LICENSE-FILES-INVALID")
    if license_plan["code_spdx"] != "Apache-2.0" or license_plan["documentation_spdx"] != "CC-BY-4.0" or license_plan["legacy_spdx"] != "MIT":
        raise ValidationError("OPEN-ALPHA-AUDIT-LICENSE-SELECTION-INVALID")
    assembly = payload["public_package_assembly"]
    if not isinstance(assembly, dict) or set(assembly) != {
        "contract_path", "state", "canonical_harness_pyproject",
        "canonical_harness_metadata_disposition", "reason", "owner_exact_export_approval",
    }:
        raise ValidationError("OPEN-ALPHA-AUDIT-ASSEMBLY-CONTRACT-INVALID")
    if assembly["owner_exact_export_approval"] is not False:
        raise ValidationError("OPEN-ALPHA-AUDIT-CANNOT-SELF-AUTHORIZE")
    cleanroom = payload["cleanroom_acceptance"]
    if not isinstance(cleanroom, dict) or set(cleanroom) != {
        "receipt_schema_path",
        "support_matrix",
        "required_stage_ids",
        "required_stage_commands",
        "acceptance_cache",
    }:
        raise ValidationError("OPEN-ALPHA-AUDIT-CLEANROOM-POLICY-INVALID")
    matrix = cleanroom["support_matrix"]
    required_stages = cleanroom["required_stage_ids"]
    stage_commands = cleanroom["required_stage_commands"]
    cache_policy = cleanroom["acceptance_cache"]
    if (
        not isinstance(matrix, list)
        or not matrix
        or any(
            not isinstance(item, dict)
            or set(item) != {"python", "system", "machine", "platform_pattern"}
            or not all(isinstance(value, str) and value for value in item.values())
            for item in matrix
        )
        or len({tuple(sorted(item.items())) for item in matrix}) != len(matrix)
        or not isinstance(required_stages, list)
        or not required_stages
        or len(set(required_stages)) != len(required_stages)
        or not all(isinstance(item, str) and item for item in required_stages)
        or not isinstance(stage_commands, list)
        or len(stage_commands) != len(required_stages)
        or any(
            not isinstance(item, dict)
            or set(item) != {"stage_id", "expected_executable", "required_argv_tokens"}
            or not isinstance(item["stage_id"], str)
            or not item["stage_id"]
            or not isinstance(item["expected_executable"], str)
            or not item["expected_executable"]
            or not isinstance(item["required_argv_tokens"], list)
            or not all(
                isinstance(token, str) and token
                for token in item["required_argv_tokens"]
            )
            or len(set(item["required_argv_tokens"]))
            != len(item["required_argv_tokens"])
            for item in stage_commands
        )
        or {item["stage_id"] for item in stage_commands} != set(required_stages)
        or not isinstance(cache_policy, dict)
        or set(cache_policy) != {"required_wheel_count", "source_url_pattern"}
        or isinstance(cache_policy["required_wheel_count"], bool)
        or not isinstance(cache_policy["required_wheel_count"], int)
        or cache_policy["required_wheel_count"] < 1
        or not isinstance(cache_policy["source_url_pattern"], str)
        or not cache_policy["source_url_pattern"]
    ):
        raise ValidationError("OPEN-ALPHA-AUDIT-CLEANROOM-POLICY-INVALID")
    try:
        for item in matrix:
            re.compile(item["platform_pattern"])
        re.compile(cache_policy["source_url_pattern"])
    except re.error as exc:
        raise ValidationError("OPEN-ALPHA-AUDIT-CLEANROOM-POLICY-INVALID") from exc
    owner = payload["owner_authorization"]
    if owner != {"state": "not_present", "evidence_refs": []}:
        raise ValidationError("OPEN-ALPHA-AUDIT-CANNOT-SELF-AUTHORIZE")
    return payload


def _manifest_includes(manifest: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    verify_self_hash(manifest, "manifest_hash")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValidationError("OPEN-ALPHA-AUDIT-MANIFEST-INVALID")
    included = [record for record in records if record.get("disposition") == "include"]
    if not included:
        raise ValidationError("OPEN-ALPHA-AUDIT-SCOPE-EMPTY")
    seen: set[str] = set()
    for record in included:
        path_ref = record.get("path")
        if not isinstance(path_ref, str) or path_ref in seen:
            raise ValidationError("OPEN-ALPHA-AUDIT-MANIFEST-PATH-INVALID")
        seen.add(path_ref)
        path = _safe_file(root, path_ref)
        raw = path.read_bytes()
        if record.get("sha256") != _sha256_bytes(raw) or record.get("bytes") != len(raw):
            raise ValidationError(f"OPEN-ALPHA-AUDIT-MANIFEST-BYTE-DRIFT:{path_ref}")
    return included


def _line_allowances(policy: dict[str, Any], included_paths: set[str]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for item in policy["line_allowances"]:
        if not isinstance(item, dict) or set(item) != {"path", "rule_id", "line_sha256", "reason"}:
            raise ValidationError("OPEN-ALPHA-AUDIT-ALLOWANCE-INVALID")
        if item["path"] not in included_paths or not item["reason"]:
            raise ValidationError("OPEN-ALPHA-AUDIT-ALLOWANCE-OUTSIDE-SCOPE")
        pure = PurePosixPath(item["path"])
        is_test_fixture = "tests" in pure.parts
        is_scanner_definition = item["path"] == (
            "oss/flowness-oss-harness/config/open-alpha-release-audit.json"
        )
        if not (is_test_fixture or is_scanner_definition):
            raise ValidationError("OPEN-ALPHA-AUDIT-ALLOWANCE-NOT-FIXTURE")
        key = (item["path"], item["rule_id"], item["line_sha256"])
        if key in result:
            raise ValidationError("OPEN-ALPHA-AUDIT-ALLOWANCE-DUPLICATE")
        result.add(key)
    return result


def _scan_sensitive(
    *, root: Path, included: list[dict[str, Any]], policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    rules: list[tuple[str, re.Pattern[str]]] = []
    for item in policy["sensitive_rules"]:
        if not isinstance(item, dict) or set(item) != {"rule_id", "pattern", "flags"}:
            raise ValidationError("OPEN-ALPHA-AUDIT-SENSITIVE-RULE-INVALID")
        flags = re.IGNORECASE if item["flags"] == "i" else 0
        if item["flags"] not in {"", "i"}:
            raise ValidationError("OPEN-ALPHA-AUDIT-SENSITIVE-FLAGS-INVALID")
        try:
            rules.append((item["rule_id"], re.compile(item["pattern"], flags)))
        except re.error as exc:
            raise ValidationError("OPEN-ALPHA-AUDIT-SENSITIVE-REGEX-INVALID") from exc
    included_paths = {item["path"] for item in included}
    allowances = _line_allowances(policy, included_paths)
    used_allowances: set[tuple[str, str, str]] = set()
    findings: list[dict[str, Any]] = []
    scanned = 0
    for record in included:
        path_ref = record["path"]
        raw = _safe_file(root, path_ref).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append({
                "rule_id": "non-utf8-included-file",
                "path": path_ref,
                "line": 1,
                "match_sha256": _sha256_bytes(raw),
            })
            continue
        scanned += 1
        for line_no, line in enumerate(text.splitlines(), 1):
            line_hash = _sha256_bytes(line.encode("utf-8"))
            for rule_id, pattern in rules:
                for match in pattern.finditer(line):
                    allowance = (path_ref, rule_id, line_hash)
                    if allowance in allowances:
                        used_allowances.add(allowance)
                        continue
                    findings.append({
                        "rule_id": rule_id,
                        "path": path_ref,
                        "line": line_no,
                        "match_sha256": _sha256_bytes(match.group(0).encode("utf-8")),
                    })
    stale = allowances - used_allowances
    for path_ref, rule_id, line_hash in sorted(stale):
        findings.append({
            "rule_id": "stale-sensitive-allowance",
            "path": path_ref,
            "line": 1,
            "match_sha256": _sha256_bytes(f"{rule_id}:{line_hash}".encode("utf-8")),
        })
    return sorted(findings, key=lambda item: (item["path"], item["line"], item["rule_id"])), scanned


def _rights_attestation(
    *,
    root: Path,
    included_by_path: dict[str, dict[str, Any]],
    evidence_ref: Any,
    group_id: str,
    cache: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(evidence_ref, dict) or set(evidence_ref) != {
        "evidence_type", "path", "sha256",
    }:
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-REF-INVALID")
    if evidence_ref["evidence_type"] != "owner_rights_attestation":
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-TYPE-INVALID")
    path_ref = evidence_ref["path"]
    digest = evidence_ref["sha256"]
    if not isinstance(path_ref, str) or not isinstance(digest, str):
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-REF-INVALID")
    record = included_by_path.get(path_ref)
    if record is None:
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-OUTSIDE-SCOPE")
    try:
        path = _safe_file(root, path_ref)
    except (OSError, ValidationError) as exc:
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-FILE-MISSING") from exc
    raw = path.read_bytes()
    observed_digest = _sha256_bytes(raw)
    if digest != observed_digest or record.get("sha256") != observed_digest:
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-HASH-DRIFT")
    cache_key = (path_ref, digest)
    attestation = cache.get(cache_key)
    if attestation is None:
        schema_record = included_by_path.get(RIGHTS_ATTESTATION_SCHEMA_PATH)
        if schema_record is None:
            raise ValidationError(
                "OPEN-ALPHA-AUDIT-RIGHTS-ATTESTATION-SCHEMA-OUTSIDE-SCOPE"
            )
        schema_path = _safe_file(root, RIGHTS_ATTESTATION_SCHEMA_PATH)
        if schema_record.get("sha256") != _sha256_bytes(schema_path.read_bytes()):
            raise ValidationError(
                "OPEN-ALPHA-AUDIT-RIGHTS-ATTESTATION-SCHEMA-HASH-DRIFT"
            )
        try:
            attestation = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-ATTESTATION-INVALID") from exc
        validate_payload(
            attestation,
            schema_path,
            "Open Alpha owner rights attestation",
        )
        verify_self_hash(attestation, "attestation_hash")
        if (
            attestation.get("schema_version") != RIGHTS_ATTESTATION_SCHEMA_VERSION
            or attestation.get("owner_role") != "repository_owner"
            or attestation.get("rights_review_state") != "owner_attested"
            or attestation.get("ip_review_state") != "owner_attested"
            or attestation.get("publication_authorization") is not False
            or attestation.get("cryptographic_signature") != {"present": False}
            or attestation.get("scope", {}).get("policy_path") != RIGHTS_POLICY_PATH
        ):
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-ATTESTATION-INVALID")
        cache[cache_key] = attestation
    if group_id not in attestation["scope"]["covered_rights_group_ids"]:
        raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-GROUP-MISMATCH")
    return evidence_ref


def _rights_groups(
    root: Path,
    included: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    groups = policy["rights_groups"]
    included_by_path = {item["path"]: item for item in included}
    attestation_cache: dict[tuple[str, str], dict[str, Any]] = {}
    required = {
        "group_id", "patterns", "license_expression", "origin_class", "rights_state",
        "ip_review_state", "provenance_state", "evidence_refs",
    }
    for group in groups:
        if not isinstance(group, dict) or set(group) != required:
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-GROUP-INVALID")
        if group["rights_state"] not in {"owner_attestation_pending", "blocked", "cleared"}:
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-STATE-INVALID")
        if group["ip_review_state"] not in {"source_review_pending", "blocked", "cleared"}:
            raise ValidationError("OPEN-ALPHA-AUDIT-IP-STATE-INVALID")
        if not isinstance(group["evidence_refs"], list):
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-EVIDENCE-REF-INVALID")
        if group["rights_state"] == "cleared" and not group["evidence_refs"]:
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-CLEAR-WITHOUT-EVIDENCE")
        if group["ip_review_state"] == "cleared" and not group["evidence_refs"]:
            raise ValidationError("OPEN-ALPHA-AUDIT-IP-CLEAR-WITHOUT-EVIDENCE")
        if group["rights_state"] == "cleared" or group["ip_review_state"] == "cleared":
            for evidence_ref in group["evidence_refs"]:
                _rights_attestation(
                    root=root,
                    included_by_path=included_by_path,
                    evidence_ref=evidence_ref,
                    group_id=group["group_id"],
                    cache=attestation_cache,
                )
    counts = {item["group_id"]: 0 for item in groups}
    unclassified: list[str] = []
    for record in included:
        matches = [
            group for group in groups
            if any(fnmatch.fnmatchcase(record["path"], pattern) for pattern in group["patterns"])
        ]
        if len(matches) != 1:
            unclassified.append(record["path"])
            continue
        counts[matches[0]["group_id"]] += 1
    output: list[dict[str, Any]] = []
    for group in groups:
        if counts[group["group_id"]] == 0:
            raise ValidationError("OPEN-ALPHA-AUDIT-RIGHTS-GROUP-EMPTY")
        output.append({
            "group_id": group["group_id"],
            "files": counts[group["group_id"]],
            "license_expression": group["license_expression"],
            "origin_class": group["origin_class"],
            "rights_state": group["rights_state"],
            "ip_review_state": group["ip_review_state"],
            "provenance_state": group["provenance_state"],
            "evidence_refs": group["evidence_refs"],
        })
    return output, sorted(unclassified)


def _cross_license_duplicate_count(
    included: list[dict[str, Any]], policy: dict[str, Any]
) -> int:
    """Count byte-identical included assets assigned incompatible SPDX expressions."""
    licenses_by_hash: dict[str, set[str]] = {}
    for record in included:
        matches = [
            group
            for group in policy["rights_groups"]
            if any(
                fnmatch.fnmatchcase(record["path"], pattern)
                for pattern in group["patterns"]
            )
        ]
        if len(matches) != 1:
            continue
        licenses_by_hash.setdefault(record["sha256"], set()).add(
            matches[0]["license_expression"]
        )
    return sum(len(expressions) > 1 for expressions in licenses_by_hash.values())


def _project_metadata(path: Path) -> tuple[str, str, str]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        project = payload["project"]
        license_value = project["license"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ValidationError("OPEN-ALPHA-AUDIT-PACKAGE-METADATA-INVALID") from exc
    if isinstance(license_value, str):
        license_text = license_value
    elif isinstance(license_value, dict) and isinstance(license_value.get("text"), str):
        license_text = license_value["text"]
    else:
        license_text = ""
    return str(project.get("name", "")), str(project.get("version", "")), license_text


def _cleanroom_stage_evidence_bound(
    stages: Any, cleanroom_policy: dict[str, Any]
) -> bool:
    """Require an exact stage set whose recorded command could perform the stage."""

    if not isinstance(stages, list):
        return False
    required_ids = cleanroom_policy["required_stage_ids"]
    contracts = {
        item["stage_id"]: item for item in cleanroom_policy["required_stage_commands"]
    }
    stage_ids = [
        item.get("stage_id") if isinstance(item, dict) else None for item in stages
    ]
    if stage_ids != required_ids:
        return False
    for stage in stages:
        state = stage.get("state")
        exit_code = stage.get("exit_code")
        command = stage.get("command")
        if (
            state not in {"pass", "fail"}
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or (state == "pass") != (exit_code == 0)
            or not isinstance(command, list)
            or not command
            or not all(isinstance(token, str) and token for token in command)
        ):
            return False
        contract = contracts[stage["stage_id"]]
        if command[0] != contract["expected_executable"]:
            return False
        argv = command[1:]
        if not all(token in argv for token in contract["required_argv_tokens"]):
            return False
    return True


def _expected_cleanroom_dependencies(
    root: Path,
    policy: dict[str, Any],
    support_coordinate: dict[str, str],
    python_version: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return exact project and target-active locked distributions."""

    projects = {
        canonicalize_name(item["expected_name"]): item["expected_version"]
        for item in policy["package_metadata"]
    }
    lock_path = policy["supply_chain"].get("unified_lock_path")
    if not isinstance(lock_path, str):
        raise ValidationError("OPEN-ALPHA-AUDIT-UNIFIED-LOCK-MISSING")
    marker_environment = {
        "implementation_name": "cpython",
        "implementation_version": python_version,
        "os_name": "posix" if support_coordinate["system"] != "Windows" else "nt",
        "platform_machine": support_coordinate["machine"],
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": support_coordinate["system"],
        "platform_version": "",
        "python_full_version": python_version,
        "python_version": ".".join(python_version.split(".")[:2]),
        "sys_platform": support_coordinate["system"].lower(),
    }
    locked: dict[str, str] = {}
    for raw_line in _safe_file(root, lock_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise ValidationError("OPEN-ALPHA-AUDIT-UNIFIED-LOCK-INVALID") from exc
        if requirement.marker is not None and not requirement.marker.evaluate(
            marker_environment
        ):
            continue
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
            raise ValidationError("OPEN-ALPHA-AUDIT-UNIFIED-LOCK-NOT-EXACT")
        name = canonicalize_name(requirement.name)
        if name in locked:
            raise ValidationError("OPEN-ALPHA-AUDIT-UNIFIED-LOCK-DUPLICATE")
        locked[name] = specifiers[0].version
    if not locked:
        raise ValidationError("OPEN-ALPHA-AUDIT-UNIFIED-LOCK-EMPTY")
    return projects, locked


def _receipt_dependencies_bound(
    dependencies: Any, projects: dict[str, str], locked: dict[str, str]
) -> bool:
    if not isinstance(dependencies, list):
        return False
    observed: dict[str, str] = {}
    for item in dependencies:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "version"}
            or not isinstance(item["name"], str)
            or not isinstance(item["version"], str)
        ):
            return False
        name = canonicalize_name(item["name"])
        if name in observed:
            return False
        observed[name] = item["version"]
    return observed == {**locked, **projects}


def _cleanroom_cache_evidence_bound(
    environment: dict[str, Any],
    support_coordinate: dict[str, str],
    python_version: str,
    cleanroom_policy: dict[str, Any],
    locked_dependencies: dict[str, str],
    wheelhouse_path: Path | None,
    dependency_report_path: Path | None,
) -> bool:
    """Recompute optional host cache evidence from the external artifacts."""

    cache = environment.get("acceptance_cache")
    if cache is None:
        return False
    if (
        not isinstance(cache, dict)
        or wheelhouse_path is None
        or dependency_report_path is None
    ):
        return False
    if (
        environment.get("dependency_source")
        != "external_host_acceptance_wheelhouse"
        or environment.get("cache_not_part_of_export") is not True
        or environment.get("single_host_only") is not True
        or cache.get("cache_not_part_of_export") is not True
        or cache.get("single_host_only") is not True
        or cache.get("sealed_cross_platform_wheelhouse") is not False
    ):
        return False
    hash_pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
    claimed_report_hash = cache.get("resolver_report_sha256")
    if (
        not isinstance(claimed_report_hash, str)
        or not hash_pattern.fullmatch(claimed_report_hash)
        or claimed_report_hash == "sha256:" + "0" * 64
    ):
        return False
    host_matrix = cache.get("host_matrix")
    if not isinstance(host_matrix, dict) or set(host_matrix) != {
        "implementation_name",
        "implementation_version",
        "platform_machine",
        "platform_system",
        "python_full_version",
        "sys_platform",
    }:
        return False
    if (
        host_matrix["implementation_name"] != "cpython"
        or host_matrix["implementation_version"] != python_version
        or host_matrix["python_full_version"] != python_version
        or host_matrix["platform_machine"] != support_coordinate["machine"]
        or host_matrix["platform_system"] != support_coordinate["system"]
        or host_matrix["sys_platform"] != support_coordinate["system"].lower()
    ):
        return False

    try:
        wheelhouse = wheelhouse_path.resolve(strict=True)
        report_path = dependency_report_path.resolve(strict=True)
    except OSError:
        return False
    if (
        wheelhouse_path.is_symlink()
        or not wheelhouse.is_dir()
        or dependency_report_path.is_symlink()
        or not report_path.is_file()
        or _sha256_bytes(report_path.read_bytes()) != claimed_report_hash
    ):
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    installs = report.get("install") if isinstance(report, dict) else None
    if not isinstance(installs, list):
        return False
    report_files: dict[str, tuple[str, str, str]] = {}
    for item in installs:
        download = item.get("download_info") if isinstance(item, dict) else None
        archive = download.get("archive_info") if isinstance(download, dict) else None
        hashes = archive.get("hashes") if isinstance(archive, dict) else None
        url = download.get("url") if isinstance(download, dict) else None
        digest = hashes.get("sha256") if isinstance(hashes, dict) else None
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if (
            not isinstance(url, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest == "0" * 64
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("name"), str)
            or not isinstance(metadata.get("version"), str)
        ):
            return False
        filename = unquote(Path(urlparse(url).path).name)
        if not filename.endswith(".whl") or filename in report_files:
            return False
        report_files[filename] = (
            "sha256:" + digest,
            url,
            canonicalize_name(metadata["name"]) + "==" + metadata["version"],
        )

    files = cache.get("files")
    cache_policy = cleanroom_policy["acceptance_cache"]
    if (
        not isinstance(files, list)
        or len(files) != cache_policy["required_wheel_count"]
        or len(files) != len(locked_dependencies)
    ):
        return False
    receipt_files: dict[str, dict[str, Any]] = {}
    source_url_pattern = re.compile(cache_policy["source_url_pattern"])
    wheel_dependencies: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "filename", "bytes", "sha256", "source_url",
        }:
            return False
        filename = item["filename"]
        source_url = item["source_url"]
        if (
            not isinstance(filename, str)
            or not filename.endswith(".whl")
            or "/" in filename
            or "\\" in filename
            or filename in receipt_files
            or isinstance(item["bytes"], bool)
            or not isinstance(item["bytes"], int)
            or item["bytes"] < 1
            or not isinstance(item["sha256"], str)
            or not hash_pattern.fullmatch(item["sha256"])
            or item["sha256"] == "sha256:" + "0" * 64
            or not isinstance(source_url, str)
            or not source_url_pattern.fullmatch(source_url)
        ):
            return False
        try:
            wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
        except InvalidWheelFilename:
            return False
        normalized = canonicalize_name(str(wheel_name))
        if normalized in wheel_dependencies:
            return False
        wheel_dependencies[normalized] = str(wheel_version)
        receipt_files[filename] = item
    observed_names = {
        item.name for item in wheelhouse.iterdir() if item.is_file() and item.suffix == ".whl"
    }
    if (
        wheel_dependencies != locked_dependencies
        or set(receipt_files) != set(report_files)
        or observed_names != set(receipt_files)
    ):
        return False
    for filename, item in receipt_files.items():
        wheel = wheelhouse / filename
        if wheel.is_symlink() or not wheel.is_file():
            return False
        reported_hash, reported_url, reported_dependency = report_files[filename]
        normalized_dependency = (
            canonicalize_name(reported_dependency.split("==", 1)[0])
            + "=="
            + reported_dependency.split("==", 1)[1]
        )
        expected_dependency = canonicalize_name(str(parse_wheel_filename(filename)[0]))
        expected_dependency += "==" + str(parse_wheel_filename(filename)[1])
        if (
            item["sha256"] != _sha256_bytes(wheel.read_bytes())
            or item["bytes"] != wheel.stat().st_size
            or item["sha256"] != reported_hash
            or item["source_url"] != reported_url
            or normalized_dependency != expected_dependency
        ):
            return False
    return True


def audit_open_alpha_release(
    *,
    repo: Path,
    package_manifest: dict[str, Any],
    policy_path: Path,
    schema_path: Path,
    cleanroom_receipt_path: Path | None = None,
    cleanroom_export_manifest_path: Path | None = None,
    cleanroom_wheelhouse_path: Path | None = None,
    cleanroom_dependency_report_path: Path | None = None,
) -> dict[str, Any]:
    root = repo.resolve(strict=True)
    policy = _load_policy(policy_path)
    included = _manifest_includes(package_manifest, root)
    included_paths = {item["path"] for item in included}
    checks: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []

    def block(blocker_id: str, summary: str, retest: str) -> None:
        if not any(item["blocker_id"] == blocker_id for item in blockers):
            blockers.append({"blocker_id": blocker_id, "summary": summary, "retest_condition": retest})

    community_missing = [path for path in policy["community_files"] if path not in included_paths]
    checks.append({
        "check_id": "community-files",
        "state": "pass" if not community_missing else "fail",
        "detail": "All required community entry files are included." if not community_missing else "Missing from included scope: " + ", ".join(community_missing),
    })
    if community_missing:
        block("OA-COMMUNITY-001", "Required contribution, conduct, or security entry is not in the exact export scope.", "Include every configured community file and rerun against the new manifest.")

    contact = policy["security_contact"]
    contact_ready = contact.get("state") == "ready" and bool(contact.get("evidence_refs"))
    checks.append({
        "check_id": "private-security-contact",
        "state": "pass" if contact_ready else "unknown",
        "detail": "Authenticated private reporting path is bound." if contact_ready else "Public repository private-reporting flow and authenticated contact are not yet bound.",
    })
    if not contact_ready:
        block("OA-COMMUNITY-SECURITY-CONTACT-001", "A private vulnerability-reporting path is not yet evidenced.", "Enable the final repository private reporting flow or bind an authenticated security contact and evidence.")

    license_plan = policy["license_plan"]
    license_files_missing = [path for path in license_plan["candidate_files"] if path not in included_paths]
    license_text_mismatch: list[str] = []
    for item in license_plan["license_texts"]:
        if not isinstance(item, dict) or set(item) != {
            "path", "spdx", "source_url", "source_sha256"
        }:
            raise ValidationError("OPEN-ALPHA-AUDIT-LICENSE-TEXT-ENTRY-INVALID")
        if item["path"] not in included_paths:
            license_text_mismatch.append(item["path"] + " (not included)")
            continue
        observed_hash = _sha256_bytes(_safe_file(root, item["path"]).read_bytes())
        if observed_hash != item["source_sha256"]:
            license_text_mismatch.append(item["path"] + " (official-source hash mismatch)")
    metadata_mismatch: list[str] = []
    for item in policy["package_metadata"]:
        if not isinstance(item, dict) or set(item) != {
            "path", "expected_name", "expected_version", "expected_spdx"
        }:
            raise ValidationError("OPEN-ALPHA-AUDIT-PACKAGE-METADATA-POLICY-INVALID")
        if item["path"] not in included_paths:
            metadata_mismatch.append(item["path"] + " (not included)")
            continue
        observed = _project_metadata(_safe_file(root, item["path"]))
        expected = (item["expected_name"], item["expected_version"], item["expected_spdx"])
        if observed != expected:
            metadata_mismatch.append(item["path"] + f" ({observed!r})")
    license_ready = (
        license_plan["state"] == "active_candidate_license_selection"
        and license_plan["full_license_texts_present"] is True
        and not license_files_missing
        and not license_text_mismatch
        and not metadata_mismatch
    )
    checks.append({
        "check_id": "license-and-package-metadata",
        "state": "pass" if license_ready else "fail",
        "detail": "Official-source license texts and Ledger package metadata match the selected SPDX expressions." if license_ready else "License text or package metadata mismatch: " + ", ".join(license_files_missing + license_text_mismatch + metadata_mismatch),
    })
    if not license_ready:
        block("OA-LICENSE-001", "The Apache-2.0/CC-BY-4.0 selection is not hash-bound to complete official texts and matching package metadata.", "Include the official full texts, match their configured hashes, and correct public package metadata.")

    assembly = policy["public_package_assembly"]
    assembly_contract_present = assembly["contract_path"] in included_paths
    assembly_ready = assembly["state"] in {
        "portable_metadata_assembled",
        "exact_installable_harness_metadata_present",
    } and assembly_contract_present
    checks.append({
        "check_id": "portable-harness-package-metadata",
        "state": "pass" if assembly_ready else "unknown",
        "detail": "Exact installable Harness metadata is included in the selected export scope." if assembly_ready else "A portable assembly contract is included, but canonical Harness metadata remains held until dependency closure and clean-room assembly.",
    })
    if not assembly_ready:
        block("OA-PACKAGE-METADATA-001", "The public Harness pyproject is still an assembly contract, not an exact installable metadata file.", "Close dependency scope, generate the minimal public pyproject from the included contract, and pass clean-room installation.")

    supply_chain = policy["supply_chain"]
    supply_paths = [
        supply_chain.get("candidate_sbom_path"),
        supply_chain.get("third_party_notice"),
        supply_chain.get("unified_lock_path"),
        *supply_chain.get("source_lock_paths", []),
    ]
    third_party_ready = (
        supply_chain.get("state") == "locked_transitive_candidate"
        and all(isinstance(path, str) and path in included_paths for path in supply_paths)
    )
    if third_party_ready:
        try:
            sbom = json.loads(
                _safe_file(root, supply_chain["candidate_sbom_path"]).read_text(encoding="utf-8")
            )
            pins = {
                match.group(1).lower().replace("_", "-"): match.group(2)
                for line in _safe_file(root, supply_chain["unified_lock_path"]).read_text(encoding="utf-8").splitlines()
                if (match := re.match(r"^([A-Za-z0-9_.-]+)==([^ ;]+)", line))
            }
            components = {
                str(item.get("name")): str(item.get("version"))
                for item in sbom.get("components", [])
                if isinstance(item, dict)
            }
            properties = {
                item.get("name"): item.get("value")
                for item in sbom.get("metadata", {}).get("properties", [])
                if isinstance(item, dict)
            }
            source_pins: set[tuple[str, str]] = set()
            lock_property_names = {
                "harness/uv.lock": "flowness:harness-uv-lock-sha256",
                "oss/flowness-oss-harness/uv.lock": "flowness:oss-uv-lock-sha256",
                "harness/build-system-requirements.lock": "flowness:build-system-lock-sha256",
            }
            source_locks_hash_bound = True
            for relative in supply_chain.get("source_lock_paths", []):
                source_path = _safe_file(root, relative)
                source_bytes = source_path.read_bytes()
                expected_property = lock_property_names.get(relative)
                if expected_property is None or properties.get(expected_property) != hashlib.sha256(source_bytes).hexdigest():
                    source_locks_hash_bound = False
                if relative.endswith("uv.lock"):
                    source_data = tomllib.loads(source_bytes.decode("utf-8"))
                    source_pins.update(
                        (str(item["name"]).lower().replace("_", "-"), str(item["version"]))
                        for item in source_data.get("package", [])
                    )
                else:
                    source_pins.update(
                        (match.group(1).lower().replace("_", "-"), match.group(2))
                        for line in source_bytes.decode("utf-8").splitlines()
                        if (match := re.match(r"^([A-Za-z0-9_.-]+)==([^ ;]+)", line))
                    )
            pins_have_exact_source = all((name, version) in source_pins for name, version in pins.items())
            third_party_ready = (
                sbom.get("bomFormat") == "CycloneDX"
                and sbom.get("specVersion") == "1.6"
                and pins == components
                and pins_have_exact_source
                and source_locks_hash_bound
                and properties.get("flowness:sbom-state") == "locked-transitive-candidate"
                and properties.get("flowness:release-authorized") == "false"
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            third_party_ready = False
    checks.append({
        "check_id": "third-party-inventory",
        "state": "pass" if third_party_ready else "fail",
        "detail": "CycloneDX components match the unified pins; every pin has an exact source-lock coordinate and every declared source lock is hash-bound." if third_party_ready else "The locked transitive SBOM, unified pins, source-lock provenance/hashes, or dependency notice are missing or inconsistent.",
    })
    if not third_party_ready:
        block("OA-THIRD-PARTY-001", "Dependency material is not a consistent locked transitive SBOM candidate.", "Regenerate the unified lock and CycloneDX document from both exact source locks and reconcile the third-party mapping.")
    wheelhouse_ready = supply_chain.get("offline_wheelhouse_state") == "sealed_cross_platform"
    checks.append({
        "check_id": "beta-cross-platform-offline-wheelhouse",
        "state": "pass" if wheelhouse_ready else "unknown",
        "detail": "A sealed cross-platform wheelhouse is bound for Beta portability." if wheelhouse_ready else "Beta portability remains Unknown because no sealed cross-platform wheelhouse is bound; this is not an Alpha blocker.",
    })

    cleanroom_policy = policy["cleanroom_acceptance"]
    cleanroom_state = "unknown"
    cleanroom_detail = "No independent clean-room receipt was supplied for the exact sealed export."
    if (
        (cleanroom_receipt_path is None) != (cleanroom_export_manifest_path is None)
        or (
            cleanroom_receipt_path is None
            and (
                cleanroom_wheelhouse_path is not None
                or cleanroom_dependency_report_path is not None
            )
        )
    ):
        cleanroom_state = "fail"
        cleanroom_detail = "Both the external clean-room receipt and its exact sealed-export manifest are required."
    elif cleanroom_receipt_path is not None and cleanroom_export_manifest_path is not None:
        cleanroom_state = "fail"
        cleanroom_detail = "The supplied clean-room receipt failed exact export, support, isolation, or E2E binding."
        try:
            receipt_path = cleanroom_receipt_path.resolve(strict=True)
            if cleanroom_receipt_path.is_symlink() or not receipt_path.is_file():
                raise ValidationError("OPEN-ALPHA-CLEANROOM-RECEIPT-UNSAFE")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            export_manifest_path = cleanroom_export_manifest_path.resolve(strict=True)
            if cleanroom_export_manifest_path.is_symlink() or not export_manifest_path.is_file():
                raise ValidationError("OPEN-ALPHA-CLEANROOM-EXPORT-MANIFEST-UNSAFE")
            export_manifest = json.loads(export_manifest_path.read_text(encoding="utf-8"))
            validate_payload(
                receipt,
                _safe_file(root, cleanroom_policy["receipt_schema_path"]),
                "Open Alpha clean-room receipt",
            )
            verify_self_hash(receipt, "receipt_hash")
            verify_self_hash(export_manifest, "manifest_hash")
            if (
                export_manifest.get("schema_version") != "flowness-rc0-export-manifest/v1"
                or export_manifest.get("release_authorized") is not False
                or canonical_hash({"files": export_manifest.get("files")})
                != export_manifest.get("payload", {}).get("aggregate_hash")
            ):
                raise ValidationError("OPEN-ALPHA-CLEANROOM-EXPORT-MANIFEST-INVALID")
            source = receipt["source"]
            environment = receipt["environment"]
            stages = receipt["stages"]
            current_repository = package_manifest.get("repository", {})
            current_head = current_repository.get("head")
            current_tree = current_repository.get("tree")
            export_repository = export_manifest.get("source_repository", {})
            export_files = {
                item.get("path"): (item.get("sha256"), item.get("bytes"))
                for item in export_manifest["files"]
                if isinstance(item, dict)
            }
            included_files = {
                item["path"]: (item["sha256"], item["bytes"])
                for item in included
            }
            source_bound = (
                export_manifest.get("scope", {}).get("manifest_hash") == package_manifest["manifest_hash"]
                and export_files == included_files
                and source.get("export_id") == export_manifest.get("export_id")
                and source.get("export_manifest_hash") == export_manifest["manifest_hash"]
                and source.get("payload_aggregate_hash") == export_manifest["payload"]["aggregate_hash"]
                and source.get("files") == export_manifest["payload"]["files"]
                and source.get("bytes") == export_manifest["payload"]["bytes"]
                and isinstance(current_head, str)
                and isinstance(current_tree, str)
                and export_repository
                == {"commit": current_head, "tree": current_tree}
                and source.get("source_commit") == current_head
            )
            isolation_bound = (
                environment.get("fresh_venv") is True
                and environment.get("existing_venv_inherited") is False
                and environment.get("pythonpath_inherited") is False
                and environment.get("source_repository_referenced") is False
                and environment.get("network_policy") == "disabled_uv_offline_and_pip_no_index"
            )
            support_coordinate = environment.get("support_coordinate")
            matrix_bound = isinstance(support_coordinate, dict) and any(
                environment.get("python_version", "").startswith(item["python"] + ".")
                and support_coordinate
                == {
                    "python": item["python"],
                    "system": item["system"],
                    "machine": item["machine"],
                }
                and re.fullmatch(item["platform_pattern"], environment.get("platform", ""))
                for item in cleanroom_policy["support_matrix"]
            )
            project_dependencies: dict[str, str] = {}
            locked_dependencies: dict[str, str] = {}
            if matrix_bound:
                project_dependencies, locked_dependencies = (
                    _expected_cleanroom_dependencies(
                        root,
                        policy,
                        support_coordinate,
                        environment.get("python_version", ""),
                    )
                )
            dependencies_bound = matrix_bound and _receipt_dependencies_bound(
                receipt.get("dependencies"),
                project_dependencies,
                locked_dependencies,
            )
            cache_bound = (
                matrix_bound
                and _cleanroom_cache_evidence_bound(
                    environment,
                    support_coordinate,
                    environment.get("python_version", ""),
                    cleanroom_policy,
                    locked_dependencies,
                    cleanroom_wheelhouse_path,
                    cleanroom_dependency_report_path,
                )
            )
            e2e_bound = _cleanroom_stage_evidence_bound(stages, cleanroom_policy)
            if (
                receipt.get("state") == "pass"
                and receipt.get("blockers") == []
                and source_bound
                and isolation_bound
                and matrix_bound
                and dependencies_bound
                and cache_bound
                and e2e_bound
            ):
                cleanroom_state = "pass"
                cleanroom_detail = "A self-hashed independent receipt binds this exact scope manifest, a declared support coordinate, fresh offline installation, canonical E2E, and post-run export verification."
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, ValidationError):
            pass
    checks.append({
        "check_id": "alpha-independent-cleanroom",
        "state": cleanroom_state,
        "detail": cleanroom_detail,
    })
    if cleanroom_state != "pass":
        block(
            "OA-CLEANROOM-001",
            "No valid independent receipt binds the exact sealed export to a declared support coordinate, fresh clean install, and canonical E2E pass.",
            "Run clean-room acceptance outside the source workspace, supply its self-hashed receipt, and verify exact scope-manifest, support-coordinate, isolation, and required-stage bindings.",
        )

    rights, unclassified = _rights_groups(root, included, policy)
    rights_clear = not unclassified and all(item["rights_state"] == "cleared" for item in rights)
    ip_clear = not unclassified and all(item["ip_review_state"] == "cleared" for item in rights)
    spdx_covered = not unclassified and all(item["license_expression"] for item in rights)
    checks.append({
        "check_id": "path-level-spdx-mapping",
        "state": "pass" if spdx_covered else "fail",
        "detail": "Every included path has exactly one path/glob-level SPDX assignment." if spdx_covered else f"{len(unclassified)} included paths have zero or overlapping rights/SPDX assignments.",
    })
    if not spdx_covered:
        block("OA-SPDX-COVERAGE-001", "The exact export is not uniquely covered by the path-level SPDX and provenance map.", "Assign every included path to exactly one rights/SPDX group and rerun.")
    cross_license_duplicates = _cross_license_duplicate_count(included, policy)
    checks.append({
        "check_id": "byte-identical-license-consistency",
        "state": "pass" if cross_license_duplicates == 0 else "fail",
        "detail": "Byte-identical included assets use one SPDX expression." if cross_license_duplicates == 0 else f"{cross_license_duplicates} byte-identical content sets are assigned conflicting SPDX expressions.",
    })
    if cross_license_duplicates:
        block(
            "OA-LICENSE-DUPLICATE-001",
            "Byte-identical included assets are assigned conflicting SPDX expressions.",
            "Assign each byte-identical operational source asset one consistent license expression and rerun.",
        )
    checks.append({
        "check_id": "file-origin-and-rights",
        "state": "pass" if rights_clear else "unknown",
        "detail": "Every included file is rights-cleared with evidence." if rights_clear else f"Rights remain pending or blocked; {len(unclassified)} included paths are unclassified or multiply classified.",
    })
    checks.append({
        "check_id": "ip-and-source-review",
        "state": "pass" if ip_clear else "unknown",
        "detail": "Every included file passed source/IP review." if ip_clear else "Code, generated material, transcript-derived wording, and reference-inspired documents still need source/IP review.",
    })
    if not rights_clear:
        block("OA-RIGHTS-001", "Repository authorship and an owner license plan do not prove file-level distribution rights.", "Bind every included path to reviewed origin and license evidence; exclude or replace unresolved files.")
    if not ip_clear:
        block("OA-IP-001", "The exact package lacks a completed source/IP review for copied, adapted, generated, or transcript-derived material.", "Complete group and file-level source review with evidence; exclude or rewrite anything unresolved.")

    sensitive_findings, scanned = _scan_sensitive(root=root, included=included, policy=policy)
    checks.append({
        "check_id": "secret-pii-sensitive-content",
        "state": "pass" if not sensitive_findings else "fail",
        "detail": f"Scanned {scanned} UTF-8 included files; findings expose hashes and coordinates, never matched values.",
    })
    if sensitive_findings:
        block("OA-SENSITIVE-001", "The exact included scope contains a sensitive-pattern finding or a stale exception.", "Remove or replace each finding, or add a byte-exact justified test-fixture allowance and rerun.")

    owner_authorized = False
    checks.append({
        "check_id": "owner-release-authorization",
        "state": "unknown",
        "detail": "Owner authorization is intentionally external and absent from this candidate audit.",
    })
    block("OA-OWNER-001", "No authenticated owner decision authorizes the exact sealed export or public release.", "After all other blockers close, bind a separately authenticated owner approval to the sealed export hash and release identity.")

    identity = {
        "policy_id": policy["policy_id"],
        "policy_sha256": _sha256_bytes(policy_path.read_bytes()),
        "package_manifest_hash": package_manifest["manifest_hash"],
    }
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "audit_id": "open-alpha-release-" + canonical_hash(identity).removeprefix("sha256:")[:24],
        "package_manifest_hash": package_manifest["manifest_hash"],
        "scope": {
            "included_files": len(included),
            "included_bytes": sum(item["bytes"] for item in included),
        },
        "checks": checks,
        "sensitive_findings": sensitive_findings,
        "rights_groups": rights,
        "blockers": sorted(blockers, key=lambda item: item["blocker_id"]),
        "release_ready": False,
        "owner_authorized": owner_authorized,
        "boundary": "Open Alpha candidate audit. PASS means the named static check passed for exact included bytes; it does not prove production behavior or manufacture the external owner authorization and publication record.",
    }
    report = {**unsigned, "report_hash": canonical_hash(unsigned)}
    validate_payload(report, schema_path, "Open Alpha release audit")
    return report
