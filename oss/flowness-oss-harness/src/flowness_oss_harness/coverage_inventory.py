from __future__ import annotations

"""Fail-closed static coverage inventory for the mechanism-excavation gate.

This module deliberately does *not* decide whether code is live, authorized,
or releasable.  Its narrow job is to make the local static discovery universe
auditable: every object yielded by the declared discovery contract has one
and only one highest-priority mapping to a seeded mechanism or an explicit
Unknown.  A discovered object without such a mapping is an error, rather than
an omission hidden by the prose mechanism registry.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .integrity import canonical_hash
from .inventory import build_inventory
from .registry import ValidationError, atomic_write_json
from .static_chain import verify_static_chain_candidate


SCHEMA_VERSION = "coverage-inventory/v2"
BOUNDARY = "UNSEALED-LOCAL-SOURCE;STATIC-DISCOVERY-ONLY;RUNTIME-UNAVAILABLE"
SEMANTIC_BOUNDARY = (
    "UNSEALED-LOCAL-SOURCE;STATIC-OBJECT-TO-MECHANISM-BINDING;"
    "RUNTIME-UNAVAILABLE"
)
DECLARED_KINDS = {
    "executable_entrypoint",
    "cli_command",
    "api_route",
    "config_surface",
    "persistent_state",
    "event_type",
    "projection",
    "daemon",
    "watcher",
    "worker",
    "hook",
    "state_machine",
    "irreversible_action",
    "recovery_path",
    "human_takeover",
    "permission_gate",
}
_IGNORED_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", "build", "dist"}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"COVERAGE-INVENTORY-{label}-INVALID") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"COVERAGE-INVENTORY-{label}-INVALID")
    return value


def _relative_path(locator: str) -> str:
    return locator.split(":", 1)[0]


def _scope_hash(root: Path, scopes: list[str]) -> str:
    entries: list[str] = []
    for scope in scopes:
        directory = root / scope
        if not directory.is_dir() or directory.is_symlink():
            raise ValidationError(f"COVERAGE-INVENTORY-SCOPE-MISSING:{scope}")
        for current, dirs, files in os.walk(directory):
            dirs[:] = sorted(name for name in dirs if name not in _IGNORED_DIRS)
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                entries.append(f"{relative}\0{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return "sha256:" + hashlib.sha256("\n".join(sorted(entries)).encode()).hexdigest()


def _validate_config(payload: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], str]:
    """Validate the discovery shell, not a mechanism-assignment shortcut.

    Path rules deliberately only answer whether a discovered object is inside
    the declared universe.  They may place it in explicit Unknown, but they
    may never grant it a mechanism.  A concrete object-to-mechanism binding is
    required for that later, separate step.
    """

    required = {
        "schema_version", "boundary", "source_scopes", "object_kinds",
        "path_discovery_rules", "semantic_mapping_registry",
    }
    if set(payload) != required or payload.get("schema_version") != "coverage-inventory-scope/v2":
        raise ValidationError("COVERAGE-INVENTORY-CONFIG-INVALID")
    if payload.get("boundary") != BOUNDARY:
        raise ValidationError("COVERAGE-INVENTORY-BOUNDARY-INVALID")
    scopes = payload.get("source_scopes")
    kinds = payload.get("object_kinds")
    rules = payload.get("path_discovery_rules")
    semantic_registry = payload.get("semantic_mapping_registry")
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(not isinstance(item, str) or not item or item.startswith("/") or ".." in Path(item).parts for item in scopes)
        or len(set(scopes)) != len(scopes)
        or not isinstance(kinds, list)
        or set(kinds) != DECLARED_KINDS
        or len(kinds) != len(DECLARED_KINDS)
        or not isinstance(rules, list)
        or not rules
        or not _valid_relative_path(semantic_registry)
    ):
        raise ValidationError("COVERAGE-INVENTORY-CONFIG-INVALID")
    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"rule_id", "path_prefix", "priority", "unknown"}:
            raise ValidationError("COVERAGE-INVENTORY-RULE-INVALID")
        rule_id = rule.get("rule_id")
        prefix = rule.get("path_prefix")
        if (
            not isinstance(rule_id, str)
            or not rule_id
            or rule_id in rule_ids
            or not isinstance(prefix, str)
            or not prefix
            or prefix.startswith("/")
            or ".." in Path(prefix).parts
            or not isinstance(rule.get("priority"), int)
            or rule.get("unknown") is not True
        ):
            raise ValidationError("COVERAGE-INVENTORY-RULE-INVALID")
        rule_ids.add(rule_id)
    return sorted(scopes), rules, semantic_registry


def _valid_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    candidate = Path(value)
    return ".." not in candidate.parts and str(candidate) == value


def _seed_ids(seed_path: Path) -> set[str]:
    seed = _load_object(seed_path, "SEED")
    mechanisms = seed.get("mechanisms")
    if seed.get("schema_version") != "mechanism-registry-seed/v0" or not isinstance(mechanisms, list):
        raise ValidationError("COVERAGE-INVENTORY-SEED-INVALID")
    ids = [item.get("mechanism_id") for item in mechanisms if isinstance(item, dict)]
    if len(ids) != len(mechanisms) or any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValidationError("COVERAGE-INVENTORY-SEED-INVALID")
    return set(ids)


def _matches(path: str, rule: dict[str, Any]) -> bool:
    return path.startswith(rule["path_prefix"])


def _path_disposition(path: str, item_id: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [rule for rule in rules if _matches(path, rule)]
    if not matches:
        raise ValidationError(f"COVERAGE-INVENTORY-MAPPING-MISSING:{item_id}")
    highest = max(rule["priority"] for rule in matches)
    winners = [rule for rule in matches if rule["priority"] == highest]
    if len(winners) != 1:
        raise ValidationError(f"COVERAGE-INVENTORY-MAPPING-AMBIGUOUS:{item_id}")
    rule = winners[0]
    return {
        "rule_id": rule["rule_id"],
        "matching_rule_ids": sorted(rule["rule_id"] for rule in matches),
        "mechanism_ids": [],
        "unknown_id": f"UNKNOWN-COVERAGE-INVENTORY-{item_id.upper()}",
        "mapping_state": "explicit_unknown",
        "assignment_basis": "path_discovery_unknown_default",
        "semantic_binding_ids": [],
    }


def _read_relative_json(root: Path, relative: str, error: str) -> dict[str, Any]:
    if not _valid_relative_path(relative):
        raise ValidationError(error)
    path = (root / relative).resolve()
    if path.is_symlink() or not path.is_file() or root not in path.parents:
        raise ValidationError(error)
    return _load_object(path, error)


def _semantic_evidence_node(root: Path, reference: Any, mechanism_id: str) -> dict[str, Any]:
    if not isinstance(reference, dict) or set(reference) != {"manifest_path", "role", "ordinal"}:
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-EVIDENCE-INVALID")
    manifest_path, role, ordinal = reference.get("manifest_path"), reference.get("role"), reference.get("ordinal")
    if not _valid_relative_path(manifest_path) or role not in {"definition", "caller", "consumer", "test", "failure", "recovery"} or not isinstance(ordinal, int) or ordinal < 0:
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-EVIDENCE-INVALID")
    manifest = _read_relative_json(root, manifest_path, "COVERAGE-INVENTORY-SEMANTIC-EVIDENCE-INVALID")
    verify_static_chain_candidate(manifest, root)
    matches = [
        node
        for chain in manifest["chains"]
        if chain["mechanism_id"] == mechanism_id
        for node in chain["nodes"]
        if node["role"] == role
    ]
    if ordinal >= len(matches):
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-EVIDENCE-MISSING")
    return matches[ordinal]


def _semantic_bindings(
    root: Path,
    registry_relative: str,
    objects: list[dict[str, Any]],
    known_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Load explicit object semantics and reject implicit multi-ownership."""

    registry = _read_relative_json(root, registry_relative, "COVERAGE-INVENTORY-SEMANTIC-REGISTRY-INVALID")
    if set(registry) != {"schema_version", "boundary", "assignment_policy", "bindings"} or registry.get("schema_version") != "mechanism-coverage-semantic-bindings/v1" or registry.get("boundary") != SEMANTIC_BOUNDARY:
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-REGISTRY-INVALID")
    policy = registry.get("assignment_policy")
    if not isinstance(policy, dict) or set(policy) != {"path_default", "one_to_many", "ambiguity"} or policy != {
        "path_default": "explicit_unknown",
        "one_to_many": "requires_shared_object_group_and_distinct_relation_roles",
        "ambiguity": "reject",
    }:
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-POLICY-INVALID")
    bindings = registry.get("bindings")
    if not isinstance(bindings, list):
        raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-REGISTRY-INVALID")
    objects_by_id = {item["item_id"]: item for item in objects}
    resolved: dict[str, list[dict[str, Any]]] = {}
    binding_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {
            "binding_id", "object_id", "mechanism_id", "relation_role", "evidence_node", "rationale", "shared_object_group"
        }:
            raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-BINDING-INVALID")
        binding_id, object_id, mechanism_id = binding.get("binding_id"), binding.get("object_id"), binding.get("mechanism_id")
        if (
            not isinstance(binding_id, str) or not binding_id or binding_id in binding_ids
            or not isinstance(object_id, str) or object_id not in objects_by_id
            or not isinstance(mechanism_id, str) or mechanism_id not in known_ids
            or not isinstance(binding.get("relation_role"), str) or not binding["relation_role"].strip()
            or not isinstance(binding.get("rationale"), str) or not binding["rationale"].strip()
            or (binding.get("shared_object_group") is not None and (not isinstance(binding["shared_object_group"], str) or not binding["shared_object_group"].strip()))
        ):
            raise ValidationError("COVERAGE-INVENTORY-SEMANTIC-BINDING-INVALID")
        _semantic_evidence_node(root, binding["evidence_node"], mechanism_id)
        binding_ids.add(binding_id)
        resolved.setdefault(object_id, []).append(binding)
    for object_id, object_bindings in resolved.items():
        roles = [item["relation_role"] for item in object_bindings]
        mechanisms = [item["mechanism_id"] for item in object_bindings]
        if len(roles) != len(set(roles)):
            raise ValidationError(f"COVERAGE-INVENTORY-SEMANTIC-ROLE-DUPLICATE:{object_id}")
        if len(object_bindings) > 1:
            groups = {item["shared_object_group"] for item in object_bindings}
            if len(groups) != 1 or None in groups or len(mechanisms) != len(set(mechanisms)):
                raise ValidationError(f"COVERAGE-INVENTORY-SEMANTIC-MULTI-OWNERSHIP-INVALID:{object_id}")
    return resolved, registry


def build_coverage_inventory(root: Path, config_path: Path, seed_path: Path) -> dict[str, Any]:
    """Rebuild the declared static universe and reject missing mappings."""

    root = root.resolve(strict=True)
    config = _load_object(config_path, "CONFIG")
    scopes, rules, semantic_registry_relative = _validate_config(config)
    known_ids = _seed_ids(seed_path)
    discovered = build_inventory(root)
    items = [
        item
        for item in discovered["items"]
        if item["kind"] in DECLARED_KINDS
        and any(_relative_path(item["locator"]).startswith(scope) for scope in scopes)
    ]
    items.sort(key=lambda item: (item["kind"], item["locator"], item["symbol"]))
    item_ids = {item["item_id"] for item in items}
    if len(item_ids) != len(items):
        raise ValidationError("COVERAGE-INVENTORY-DUPLICATE-OBJECT")
    relations = [
        relation
        for relation in discovered["relations"]
        if relation.get("source_item_id") in item_ids or relation.get("target_item_id") in item_ids
    ]
    relation_index: dict[tuple[str, str], list[str]] = {}
    for relation in relations:
        target = relation.get("target_item_id")
        if isinstance(target, str) and relation.get("relation_type") in {"producer", "consumer"}:
            relation_index.setdefault((target, relation["relation_type"]), []).append(relation["relation_id"])

    raw_objects: list[dict[str, Any]] = []
    for item in items:
        raw_objects.append(
            {
                "item_id": item["item_id"],
                "object_class": item["kind"],
                "discovery_source": item["discovery_method"],
                "locator": item["locator"],
                "symbol": item["symbol"],
                "confidence": item["confidence"],
                "producer_relation_ids": sorted(relation_index.get((item["item_id"], "producer"), [])),
                "consumer_relation_ids": sorted(relation_index.get((item["item_id"], "consumer"), [])),
            }
        )
    # Validate universe membership before reading semantic bindings.  A bad
    # path scope must not be masked by an unrelated binding-registry error.
    path_dispositions = {
        item["item_id"]: _path_disposition(_relative_path(item["locator"]), item["item_id"], rules)
        for item in raw_objects
    }
    semantic_by_object, semantic_registry = _semantic_bindings(root, semantic_registry_relative, raw_objects, known_ids)
    objects: list[dict[str, Any]] = []
    for item in raw_objects:
        mapping = path_dispositions[item["item_id"]]
        bindings = semantic_by_object.get(item["item_id"], [])
        if bindings:
            mapping = {
                "rule_id": mapping["rule_id"],
                "matching_rule_ids": mapping["matching_rule_ids"],
                "mechanism_ids": sorted(binding["mechanism_id"] for binding in bindings),
                "unknown_id": None,
                "mapping_state": "seed_mapped",
                "assignment_basis": "explicit_semantic_object_binding",
                "semantic_binding_ids": sorted(binding["binding_id"] for binding in bindings),
            }
        objects.append({**{key: value for key, value in item.items() if key != "item_id"}, "object_id": item["item_id"], "mapping": mapping})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "boundary": BOUNDARY,
        "source_scopes": scopes,
        "source_content_hash": _scope_hash(root, scopes),
        "discovery_contract": {
            "object_kinds": sorted(DECLARED_KINDS),
            "static_only": True,
            "does_not_prove": [
                "complete server source coverage outside declared scopes",
                "runtime reachability or consumer behavior",
                "deployment, authority, rights, or release readiness",
            ],
        },
        "seed": {"path": str(seed_path.resolve()), "sha256": "sha256:" + hashlib.sha256(seed_path.read_bytes()).hexdigest()},
        "config": {"path": str(config_path.resolve()), "sha256": "sha256:" + hashlib.sha256(config_path.read_bytes()).hexdigest()},
        "semantic_mapping_registry": {
            "path": str((root / semantic_registry_relative).resolve()),
            "sha256": "sha256:" + hashlib.sha256((root / semantic_registry_relative).read_bytes()).hexdigest(),
            "assignment_policy": semantic_registry["assignment_policy"],
        },
        "objects": objects,
        "counts": {
            "objects": len(objects),
            "seed_mapped": sum(item["mapping"]["mapping_state"] == "seed_mapped" for item in objects),
            "explicit_unknown": sum(item["mapping"]["mapping_state"] == "explicit_unknown" for item in objects),
            "object_classes": {
                kind: sum(item["object_class"] == kind for item in objects)
                for kind in sorted(DECLARED_KINDS)
            },
        },
    }
    payload["inventory_hash"] = canonical_hash(payload)
    return payload


def verify_coverage_inventory(
    inventory: dict[str, Any], root: Path, config_path: Path, seed_path: Path
) -> dict[str, Any]:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("COVERAGE-INVENTORY-INVALID")
    expected = build_coverage_inventory(root, config_path, seed_path)
    if inventory != expected:
        raise ValidationError("COVERAGE-INVENTORY-REBUILD-MISMATCH")
    return {
        "schema_version": "coverage-inventory-verification/v1",
        "inventory_hash": inventory["inventory_hash"],
        "object_count": inventory["counts"]["objects"],
        "ceiling": "static-discovery-only",
    }


def write_coverage_inventory(root: Path, config_path: Path, seed_path: Path, output: Path) -> dict[str, Any]:
    payload = build_coverage_inventory(root, config_path, seed_path)
    atomic_write_json(output, payload)
    return payload
