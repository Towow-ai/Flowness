from __future__ import annotations

"""Build the file-exact Flowness Open Alpha scope manifest.

The manifest is deliberately upstream of export and release.  It makes the
larger useful surface inspectable -- Ledger plus the experimental Harness,
jury/rework, mechanism/Drift/Content Graph machinery, and D0-D9 design views --
while fail-closing private operational material. ``include`` means selected
membership in the sealed-export process, never authorization or production
proof by itself.
"""

import ast
import fnmatch
import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


SCHEMA = SCHEMAS_ROOT / "open-alpha-package-manifest.schema.json"
SCHEMA_VERSION = "open-alpha-package-manifest/v1"
POLICY_VERSION = "open-alpha-package-scope-policy/v1"
_MATURITIES = {"stable", "experimental", "design_target", "private_excluded"}
_DISPOSITIONS = {"include", "hold", "exclude"}
_FORBIDDEN_INCLUDED_PARTS = {
    ".towow",
    "raw-transcripts",
    "credentials",
    "tokens",
    "customer-material",
    "server-runtime-ledgers",
    "channel-packages",
    "jury-reports",
    "deploy",
}
_FORBIDDEN_RIGHTS_UNKNOWN_PARTS = {"prse", "reference", "towow-snapshot"}
_FORBIDDEN_CANONICAL_INCLUDE_PATTERNS = (
    "harness/src/towow/l2/account_*.py",
    "harness/src/towow/l2/claude_bg_helper.py",
    "harness/src/towow/l2/run_owned_agent.py",
    "harness/src/towow/l2/transcript_efficiency.py",
    "harness/src/towow/l2/owner_session_interaction.py",
    "harness/src/towow/l2/bg_worktree_poller.py",
    "harness/src/towow/awareness/adapters/bg_session.py",
    "harness/src/towow/glue/settings.json",
    "harness/.claude/**",
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_JSON_PUBLIC_LOCATOR_KEYS = {"evidence_locator"}


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False, text=text
    )
    if completed.returncode:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ValidationError(stderr.strip() or "OPEN-ALPHA-SCOPE-GIT-FAILED")
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _relative(repo: Path, path: Path) -> str:
    try:
        value = path.resolve(strict=True).relative_to(repo).as_posix()
    except ValueError as exc:
        raise ValidationError("OPEN-ALPHA-SCOPE-PATH-OUTSIDE-REPOSITORY") from exc
    pure = PurePosixPath(value)
    if not value or pure.is_absolute() or ".." in pure.parts:
        raise ValidationError("OPEN-ALPHA-SCOPE-PATH-INVALID")
    return value


def _policy(repo: Path, path: Path) -> tuple[dict[str, Any], str]:
    relative = _relative(repo, path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-INVALID") from exc
    required = {
        "schema_version", "package_id", "scope_roots", "ignored_generated_paths",
        "rules", "required_include_components", "required_include_paths",
        "required_exclude_paths", "global_boundary",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-SHAPE-INVALID")
    if payload["schema_version"] != POLICY_VERSION:
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-VERSION-INVALID")
    for key in (
        "scope_roots", "ignored_generated_paths", "rules", "required_include_components",
        "required_include_paths", "required_exclude_paths",
    ):
        if not isinstance(payload[key], list):
            raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-SHAPE-INVALID")
    if not payload["scope_roots"] or not payload["rules"] or not payload["required_include_components"]:
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-COVERAGE-EMPTY")
    if len(payload["required_include_components"]) != len(set(payload["required_include_components"])):
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-COMPONENT-DUPLICATE")
    for key in ("required_include_paths", "required_exclude_paths"):
        if not payload[key] or len(payload[key]) != len(set(payload[key])):
            raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-REQUIRED-PATHS-INVALID")
    if set(payload["required_include_paths"]) & set(payload["required_exclude_paths"]):
        raise ValidationError("OPEN-ALPHA-SCOPE-POLICY-REQUIRED-PATH-CONFLICT")
    seen_rules: set[str] = set()
    for rule in payload["rules"]:
        if not isinstance(rule, dict) or set(rule) != {
            "rule_id", "patterns", "maturity", "disposition", "component", "reason", "claim_boundary"
        }:
            raise ValidationError("OPEN-ALPHA-SCOPE-RULE-SHAPE-INVALID")
        if rule["rule_id"] in seen_rules:
            raise ValidationError("OPEN-ALPHA-SCOPE-RULE-DUPLICATE")
        seen_rules.add(rule["rule_id"])
        if not isinstance(rule["patterns"], list) or not rule["patterns"]:
            raise ValidationError("OPEN-ALPHA-SCOPE-RULE-PATTERNS-INVALID")
        if rule["maturity"] not in _MATURITIES or rule["disposition"] not in _DISPOSITIONS:
            raise ValidationError("OPEN-ALPHA-SCOPE-RULE-STATE-INVALID")
        if rule["maturity"] == "private_excluded" and rule["disposition"] != "exclude":
            raise ValidationError("OPEN-ALPHA-SCOPE-PRIVATE-MUST-EXCLUDE")
    return payload, relative


def _assert_clean(repo: Path, roots: list[str], ignored: list[str]) -> None:
    pathspecs = [*roots, *(f":(exclude){item}" for item in ignored)]
    dirty = _git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=no", "--", *pathspecs,
        text=False,
    )
    assert isinstance(dirty, bytes)
    if dirty:
        raise ValidationError("OPEN-ALPHA-SCOPE-TRACKED-WORKTREE-DIRTY")


def _tracked(repo: Path, roots: list[str], ignored: set[str]) -> list[tuple[str, str]]:
    raw = _git(repo, "ls-files", "-s", "-z", "--", *roots, text=False)
    assert isinstance(raw, bytes)
    rows: list[tuple[str, str]] = []
    for entry in (item for item in raw.split(b"\0") if item):
        try:
            metadata, encoded_path = entry.split(b"\t", 1)
            mode, blob, stage = metadata.decode("ascii").split()
            path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError("OPEN-ALPHA-SCOPE-TRACKED-ENTRY-INVALID") from exc
        if path in ignored:
            continue
        if stage != "0" or mode not in {"100644", "100755"}:
            raise ValidationError("OPEN-ALPHA-SCOPE-NONREGULAR-ENTRY")
        rows.append((path, blob))
    if not rows:
        raise ValidationError("OPEN-ALPHA-SCOPE-NO-TRACKED-FILES")
    return sorted(rows)


def _classify(path: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    for rule in rules:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["patterns"]):
            return rule
    raise ValidationError(f"OPEN-ALPHA-SCOPE-UNCLASSIFIED:{path}")


def _assert_inclusion_boundary(path: str, rule: dict[str, Any]) -> None:
    if rule["disposition"] != "include":
        return
    parts = {part.lower() for part in PurePosixPath(path).parts}
    if parts & _FORBIDDEN_INCLUDED_PARTS:
        raise ValidationError(f"OPEN-ALPHA-SCOPE-PRIVATE-INCLUDE:{path}")
    if parts & _FORBIDDEN_RIGHTS_UNKNOWN_PARTS:
        raise ValidationError(f"OPEN-ALPHA-SCOPE-RIGHTS-UNKNOWN-INCLUDE:{path}")
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in _FORBIDDEN_CANONICAL_INCLUDE_PATTERNS):
        raise ValidationError(f"OPEN-ALPHA-SCOPE-CANONICAL-PRIVATE-INCLUDE:{path}")


def _module_name(path: str) -> str | None:
    prefix = "harness/src/"
    if not path.startswith(prefix) or not path.endswith(".py"):
        return None
    module = path[len(prefix):-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    return module


def _absolute_from_module(*, importer_module: str, importer_is_package: bool,
                          level: int, module: str | None) -> str | None:
    if level == 0:
        return module
    package = importer_module if importer_is_package else importer_module.rpartition(".")[0]
    parts = package.split(".") if package else []
    ascend = level - 1
    if ascend > len(parts):
        return None
    base = parts[:len(parts) - ascend] if ascend else parts
    if module:
        base.extend(module.split("."))
    return ".".join(base) or None


def _dependency_closure(*, repo: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report selected canonical Python imports that cross the export boundary.

    This is intentionally a static, first-party import check.  It does not
    claim runtime reachability and it does not replace the later clean-room or
    supply-chain gates.
    """
    module_paths = {
        module: item["path"]
        for item in records
        if (module := _module_name(item["path"])) is not None
    }
    records_by_path = {item["path"]: item for item in records}
    included_python = [
        item for item in records
        if item["disposition"] == "include" and _module_name(item["path"]) is not None
    ]
    blocker_edges: set[tuple[str, str, str | None, str]] = set()
    for item in included_python:
        path = item["path"]
        importer_module = _module_name(path)
        assert importer_module is not None
        try:
            tree = ast.parse((repo / path).read_text(encoding="utf-8"), filename=path)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise ValidationError(f"OPEN-ALPHA-SCOPE-INCLUDED-PYTHON-PARSE-FAILED:{path}") from exc
        requested: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                requested.update(
                    alias.name for alias in node.names
                    if alias.name == "towow" or alias.name.startswith("towow.")
                )
            elif isinstance(node, ast.ImportFrom):
                base = _absolute_from_module(
                    importer_module=importer_module,
                    importer_is_package=path.endswith("/__init__.py"),
                    level=node.level,
                    module=node.module,
                )
                if not base or (base != "towow" and not base.startswith("towow.")):
                    continue
                resolved_any = False
                if base in module_paths:
                    requested.add(base)
                    resolved_any = True
                for alias in node.names:
                    child = f"{base}.{alias.name}"
                    if alias.name != "*" and child in module_paths:
                        requested.add(child)
                        resolved_any = True
                if not resolved_any:
                    requested.add(base)
        for imported_module in requested:
            target_path = module_paths.get(imported_module)
            if target_path is None:
                blocker_edges.add((path, imported_module, None, "unresolved"))
                continue
            target = records_by_path[target_path]
            if target["disposition"] != "include":
                blocker_edges.add((path, imported_module, target_path, target["disposition"]))
    blockers = []
    for importer_path, imported_module, target_path, target_disposition in sorted(
        blocker_edges, key=lambda edge: (edge[0], edge[1], edge[2] or "")
    ):
        blocker_identity = f"{importer_path}\0{imported_module}\0{target_path or ''}\0{target_disposition}"
        blockers.append({
            "blocker_id": "DEP-" + hashlib.sha256(blocker_identity.encode()).hexdigest()[:16],
            "importer_path": importer_path,
            "imported_module": imported_module,
            "target_path": target_path,
            "target_disposition": target_disposition,
            "required_action": "provide-public-adapter-or-include-reviewed-dependency",
        })
    return {
        "status": "blocked" if blockers else "closed",
        "analysis": "static-first-party-python-imports/v1",
        "included_python_files": len(included_python),
        "blockers": blockers,
        "boundary": "Static first-party imports only; runtime reachability, optional imports, packaging, external dependencies, and clean-room execution require separate gates.",
    }


def _resolve_public_reference(
    *, source_path: str, raw: str, records_by_path: dict[str, dict[str, Any]],
    markdown_relative: bool, package_relative: bool = False,
) -> str | None:
    """Resolve a repository-local public consumer reference if it names a file."""

    value = raw.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    if not value or "://" in value or value.startswith("/") or "\\" in value:
        return None
    candidates: list[str] = []
    harness_prefix = "oss/flowness-oss-harness/"
    if package_relative and source_path.startswith(harness_prefix):
        candidates.append(harness_prefix + posixpath.normpath(value))
    if markdown_relative:
        candidates.append(
            posixpath.normpath(
                str(PurePosixPath(source_path).parent / PurePosixPath(value))
            )
        )
    candidates.append(posixpath.normpath(value))
    if source_path.startswith(harness_prefix) and value.startswith(
        ("assets/", "config/", "docs/", "registries/", "schemas/", "src/", "tests/")
    ):
        candidates.append(harness_prefix + posixpath.normpath(value))
    for candidate in candidates:
        if candidate in records_by_path:
            return candidate
    return None


def _consumer_closure(*, repo: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Find public documents/registries that point outside the sealed export.

    The check covers navigable Markdown links, JSON provenance dependencies,
    launch-claim evidence bindings, and every navigable Mechanism Card evidence
    coordinate. Explicit ``withheld_from_open_alpha`` locators are boundaries,
    not links, and therefore do not create a packaged dependency.
    """

    records_by_path = {item["path"]: item for item in records}
    references: set[tuple[str, str, str, str]] = set()
    hash_mismatches: set[tuple[str, str, str, str, str]] = set()

    def record_reference(
        source_path: str,
        field: str,
        raw: str,
        *,
        relative: bool,
        package_relative: bool = False,
    ) -> None:
        target_path = _resolve_public_reference(
            source_path=source_path,
            raw=raw,
            records_by_path=records_by_path,
            markdown_relative=relative,
            package_relative=package_relative,
        )
        if target_path is None:
            return
        target = records_by_path[target_path]
        if target["disposition"] != "include":
            references.add((source_path, field, target_path, target["disposition"]))

    for item in records:
        if item["disposition"] != "include":
            continue
        source_path = item["path"]
        path = repo / source_path
        if source_path.endswith(".md"):
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ValidationError(
                    f"OPEN-ALPHA-CONSUMER-READ-FAILED:{source_path}"
                ) from exc
            for raw in _MARKDOWN_LINK.findall(content):
                record_reference(source_path, "markdown_link", raw, relative=True)
        elif source_path.endswith(".json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError(
                    f"OPEN-ALPHA-CONSUMER-JSON-INVALID:{source_path}"
                ) from exc

            claims = payload.get("claims") if isinstance(payload, dict) else None
            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue
                    bindings = claim.get("evidence_bindings")
                    if not isinstance(bindings, list):
                        continue
                    for binding in bindings:
                        if isinstance(binding, dict) and isinstance(binding.get("path"), str):
                            record_reference(
                                source_path,
                                "claims.evidence_bindings.path",
                                binding["path"],
                                relative=False,
                                package_relative=True,
                            )

            if isinstance(payload, dict) and payload.get("schema_version") == "mechanism-card-registry/v1":
                for card in payload.get("cards", []):
                    if not isinstance(card, dict):
                        continue
                    locators: list[tuple[str, Any]] = []
                    static = card.get("static_coordinates")
                    if isinstance(static, dict):
                        for coordinates in static.values():
                            if isinstance(coordinates, list):
                                locators.extend(("static_coordinates.path", item) for item in coordinates)
                    why = card.get("why_it_exists")
                    if isinstance(why, dict):
                        for anchor in why.get("anchors", []):
                            if isinstance(anchor, dict):
                                locators.extend(
                                    ("history_anchor.path", anchor.get(key))
                                    for key in ("current", "evolution")
                                )
                    contract = card.get("mechanism_semantic_contract")
                    if isinstance(contract, dict):
                        locators.extend(
                            ("semantic_object.coordinate.path", item.get("coordinate"))
                            for item in contract.get("objects", [])
                            if isinstance(item, dict)
                        )
                        chain = contract.get("state_chain")
                        if isinstance(chain, dict):
                            for edge in chain.get("transitions", []):
                                evidence = edge.get("evidence") if isinstance(edge, dict) else None
                                if isinstance(evidence, dict):
                                    locators.extend(
                                        ("semantic_edge.coordinate.path", evidence.get(key))
                                        for key in ("source_coordinate", "target_coordinate")
                                    )
                    for field, locator in locators:
                        if (
                            isinstance(locator, dict)
                            and locator.get("availability") != "withheld_from_open_alpha"
                            and isinstance(locator.get("path"), str)
                        ):
                            record_reference(
                                source_path,
                                field,
                                locator["path"],
                                relative=False,
                            )

            def walk(value: Any, *, key: str = "", in_source_inputs: bool = False) -> None:
                if isinstance(value, dict):
                    locator_path = value.get("path")
                    locator_hash = value.get("sha256")
                    if (
                        value.get("availability") != "withheld_from_open_alpha"
                        and isinstance(locator_path, str)
                        and isinstance(locator_hash, str)
                    ):
                        target_path = _resolve_public_reference(
                            source_path=source_path,
                            raw=locator_path,
                            records_by_path=records_by_path,
                            markdown_relative=False,
                            package_relative=True,
                        )
                        if target_path is not None:
                            target = records_by_path[target_path]
                            if target["disposition"] == "include":
                                observed_hash = target.get("sha256") or _sha256(
                                    repo / target_path
                                )
                                if locator_hash != observed_hash:
                                    hash_mismatches.add(
                                        (
                                            source_path,
                                            "path+sha256",
                                            target_path,
                                            locator_hash,
                                            observed_hash,
                                        )
                                    )
                    for child_key, child in value.items():
                        walk(
                            child,
                            key=child_key,
                            in_source_inputs=in_source_inputs or child_key == "source_inputs",
                        )
                elif isinstance(value, list):
                    for child in value:
                        walk(child, key=key, in_source_inputs=in_source_inputs)
                elif isinstance(value, str) and (
                    key in _JSON_PUBLIC_LOCATOR_KEYS
                    or (in_source_inputs and key == "path")
                ):
                    record_reference(source_path, key, value, relative=False)

            walk(payload)

    blockers = []
    for source_path, field, target_path, disposition in sorted(references):
        identity = f"{source_path}\0{field}\0{target_path}\0{disposition}"
        blockers.append(
            {
                "blocker_id": "REF-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                "consumer_path": source_path,
                "reference_field": field,
                "target_path": target_path,
                "target_disposition": disposition,
                "required_action": "replace-with-included-evidence-or-explicit-nonlink-boundary",
            }
        )
    for source_path, field, target_path, declared_hash, observed_hash in sorted(
        hash_mismatches
    ):
        identity = (
            f"{source_path}\0{field}\0{target_path}\0{declared_hash}\0{observed_hash}"
        )
        blockers.append(
            {
                "blocker_id": "REF-" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                "consumer_path": source_path,
                "reference_field": field,
                "target_path": target_path,
                "target_disposition": "include_hash_mismatch",
                "required_action": "refresh-hash-bound-reference-to-exact-included-bytes",
            }
        )
    return {
        "status": "blocked" if blockers else "closed",
        "analysis": "markdown-links-json-path-hashes-provenance-claims-and-mechanism-coordinates/v3",
        "included_consumer_files": sum(
            item["disposition"] == "include"
            and item["path"].endswith((".md", ".json"))
            for item in records
        ),
        "blockers": blockers,
        "boundary": (
            "Checks navigable Markdown links, JSON evidence/source-input artifacts, "
            "launch claim bindings, and Mechanism Card evidence coordinates. "
            "Explicit withheld locators are non-navigable; runtime reachability "
            "still requires separate claim review."
        ),
    }


def build_open_alpha_package_manifest(*, repo: Path, policy_path: Path) -> dict[str, Any]:
    repo_root = Path(str(_git(repo, "rev-parse", "--show-toplevel")).strip()).resolve(strict=True)
    policy, policy_relative = _policy(repo_root, policy_path.resolve(strict=True))
    roots = policy["scope_roots"]
    ignored = set(policy["ignored_generated_paths"])
    _assert_clean(repo_root, roots, sorted(ignored))
    records: list[dict[str, Any]] = []
    for path, blob in _tracked(repo_root, roots, ignored):
        absolute = repo_root / path
        if absolute.is_symlink() or not absolute.is_file():
            raise ValidationError(f"OPEN-ALPHA-SCOPE-FILE-UNSAFE:{path}")
        observed_blob = str(_git(repo_root, "hash-object", "--", path)).strip()
        if observed_blob != blob:
            raise ValidationError(f"OPEN-ALPHA-SCOPE-BLOB-DRIFT:{path}")
        rule = _classify(path, policy["rules"])
        _assert_inclusion_boundary(path, rule)
        records.append({
            "path": path,
            "git_blob_sha1": "sha1:" + blob,
            "sha256": _sha256(absolute),
            "bytes": absolute.stat().st_size,
            "maturity": rule["maturity"],
            "disposition": rule["disposition"],
            "component": rule["component"],
            "rule_id": rule["rule_id"],
            "reason": rule["reason"],
            "claim_boundary": rule["claim_boundary"],
        })
    paths = [item["path"] for item in records]
    if len(paths) != len(set(paths)):
        raise ValidationError("OPEN-ALPHA-SCOPE-DUPLICATE-PATH")
    records_by_path = {item["path"]: item for item in records}
    missing_required_include_paths = set(policy["required_include_paths"]) - set(records_by_path)
    if missing_required_include_paths:
        raise ValidationError(
            "OPEN-ALPHA-SCOPE-REQUIRED-PATH-MISSING:"
            + ",".join(sorted(missing_required_include_paths))
        )
    wrong_includes = [
        path for path in policy["required_include_paths"]
        if records_by_path[path]["disposition"] != "include"
    ]
    wrong_excludes = [
        path for path in policy["required_exclude_paths"]
        if path in records_by_path and records_by_path[path]["disposition"] != "exclude"
    ]
    if wrong_includes:
        raise ValidationError("OPEN-ALPHA-SCOPE-REQUIRED-INCLUDE-VIOLATION:" + ",".join(wrong_includes))
    if wrong_excludes:
        raise ValidationError("OPEN-ALPHA-SCOPE-REQUIRED-EXCLUDE-VIOLATION:" + ",".join(wrong_excludes))
    included_components = {item["component"] for item in records if item["disposition"] == "include"}
    missing = set(policy["required_include_components"]) - included_components
    if missing:
        raise ValidationError("OPEN-ALPHA-SCOPE-REQUIRED-COMPONENT-MISSING:" + ",".join(sorted(missing)))
    components: dict[str, dict[str, int]] = {}
    for item in records:
        counts = components.setdefault(item["component"], {"include": 0, "hold": 0, "exclude": 0})
        counts[item["disposition"]] += 1
    summary = {
        "tracked_files": len(records),
        "include": sum(item["disposition"] == "include" for item in records),
        "hold": sum(item["disposition"] == "hold" for item in records),
        "exclude": sum(item["disposition"] == "exclude" for item in records),
        "stable_include": sum(item["disposition"] == "include" and item["maturity"] == "stable" for item in records),
        "experimental_include": sum(item["disposition"] == "include" and item["maturity"] == "experimental" for item in records),
        "design_target_include": sum(item["disposition"] == "include" and item["maturity"] == "design_target" for item in records),
        "components": dict(sorted(components.items())),
    }
    head = str(_git(repo_root, "rev-parse", "HEAD")).strip()
    tree = str(_git(repo_root, "rev-parse", "HEAD^{tree}")).strip()
    dependency_closure = _dependency_closure(repo=repo_root, records=records)
    consumer_closure = _consumer_closure(repo=repo_root, records=records)
    identity = {
        "package_id": policy["package_id"],
        "repository": {"head": head, "tree": tree},
        "policy_sha256": _sha256(repo_root / policy_relative),
        "dependency_closure": dependency_closure,
        "consumer_closure": consumer_closure,
        "records": records,
    }
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": "flowness-open-alpha-" + canonical_hash(identity).removeprefix("sha256:")[:24],
        "package_id": policy["package_id"],
        "repository": {"head": head, "tree": tree},
        "policy": {"path": policy_relative, "sha256": _sha256(repo_root / policy_relative)},
        "summary": summary,
        "required_path_assertions": {
            "include": sorted(policy["required_include_paths"]),
            "exclude": sorted(policy["required_exclude_paths"]),
        },
        "dependency_closure": dependency_closure,
        "consumer_closure": consumer_closure,
        "records": records,
        "release_authorized": False,
        "rights_state": "unreviewed",
        "global_boundary": policy["global_boundary"],
    }
    manifest = {**unsigned, "manifest_hash": canonical_hash(unsigned)}
    validate_payload(manifest, SCHEMA, "Open Alpha package manifest")
    return manifest


def write_open_alpha_package_manifest(*, repo: Path, policy_path: Path, output: Path) -> dict[str, Any]:
    manifest = build_open_alpha_package_manifest(repo=repo, policy_path=policy_path)
    atomic_create_json(output, manifest)
    return manifest


def verify_open_alpha_package_manifest(*, repo: Path, policy_path: Path, manifest_path: Path) -> dict[str, Any]:
    actual = load_validated_json(manifest_path, SCHEMA, "Open Alpha package manifest")
    verify_self_hash(actual, "manifest_hash")
    expected = build_open_alpha_package_manifest(repo=repo, policy_path=policy_path)
    if actual != expected:
        raise ValidationError("OPEN-ALPHA-SCOPE-MANIFEST-STALE-OR-MISMATCHED")
    return actual
