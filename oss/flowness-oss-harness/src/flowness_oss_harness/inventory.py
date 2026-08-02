from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import re
import shlex
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

IGNORED_DIRS = {
    ".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", "__pycache__", "build", "dist", "node_modules",
}
# First match wins. This is also the public precedence for ambiguous old items.
ROLE_WORDS = (
    "daemon", "watcher", "worker", "hook", "sentinel", "orchestrator",
    "projection", "event", "cli", "server", "adapter", "installer",
)
EVENT_RE = re.compile(
    r"""(?:event_type|type)\s*[:=]\s*["']([A-Z][A-Za-z0-9_]+)["']"""
)
ENV_RE = re.compile(
    r"\$\{([A-Z][A-Z0-9_]{2,})(?::?[-+?][^}]*)?}|\$([A-Z][A-Z0-9_]{2,})"
)
API_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "websocket"}
PRODUCERS = {"emit", "publish_event", "append_event", "send_event", "dispatch_event"}
CONSUMERS = {"on_event", "subscribe", "register_handler", "event_handler"}
RECOVERY_WORDS = {"rollback", "retry", "recover", "recovery", "reopen", "resume", "compensate", "dead_letter", "deadletter"}
HUMAN_PHRASES = {"human_takeover", "manual_takeover", "request_human", "handoff_to_human", "escalate_to_owner", "owner_takeover", "manual_intervention"}
PERSIST_CALLS = {
    "_write_run_manifest",
    "append_event",
    "atomic_write_json",
    "commit_event",
    "persist_state",
    "save_checkpoint",
    "store_checkpoint",
    "write_state",
}
TRUSTED_PERSIST_WRAPPERS = {"_write_run_manifest": "atomic_write_json"}
REQUIRED_CONFIG_FILES = {
    "gates.json", "governance-policy.json", "roles.json", "source-boundaries.json",
    "governance-policy.schema.json", "owner-approval.schema.json",
    "trusted-owner-keys.schema.json",
}
REGISTRATION_METHODS = {
    "pyproject-console-script", "python-ast-api-decorator",
    "python-ast-cli-decorator", "python-ast-cli-registration",
    "python-ast-event-consumer", "python-ast-event-producer",
    "python-ast-main", "python-main-module", "systemd-exec-directive",
    "systemd-unit-file",
}
CRITICAL_METHODS = {
    "python-ast-authoritative-write-call", "python-ast-database-write",
    "python-ast-enum", "python-ast-filesystem-mutation",
    "python-ast-guard-condition", "python-ast-open-write",
    "python-ast-path-write", "python-ast-side-effect-call",
    "python-ast-transition-table",
}


@dataclass(frozen=True)
class InventoryItem:
    item_id: str
    kind: str
    locator: str
    symbol: str
    discovery_method: str
    confidence: str
    verification_state: str = "candidate"
    criticality: str = "search_seed"
    coverage_strategy: str = "search_seed"
    blocking_reason: str | None = None
    effect_class: str | None = None


@dataclass(frozen=True)
class InventoryRelation:
    relation_id: str
    relation_type: str
    source_item_id: str | None
    source_locator: str
    target_item_id: str | None
    target_locator: str
    discovery_method: str
    confidence: str
    verification_state: str = "candidate"


def _iter_files(repo: Path) -> Iterable[Path]:
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(
            name for name in dirs
            if name not in IGNORED_DIRS and not name.endswith(".egg-info")
        )
        for name in sorted(files):
            path = Path(root) / name
            if not path.is_symlink():
                yield path


def _item_id(kind: str, locator: str, symbol: str) -> str:
    raw = f"{kind}\0{locator}\0{symbol}".encode()
    return f"inv-{hashlib.sha256(raw).hexdigest()[:16]}"


def _is_test(locator: str) -> bool:
    parts = [part.lower() for part in Path(locator.split(":", 1)[0]).parts]
    stem = Path(parts[-1]).stem if parts else ""
    return (
        any(part in {"test", "tests", "fixtures", "test-fixtures"} for part in parts[:-1])
        or stem.startswith(("test_", "test-"))
        or stem.endswith(("_test", "-test"))
    )


def _item(
    kind: str,
    locator: str,
    symbol: str,
    method: str,
    confidence: str,
    *,
    criticality: str | None = None,
    blocking_reason: str | None = None,
    effect_class: str | None = None,
    coverage_strategy: str | None = None,
) -> InventoryItem:
    if _is_test(locator):
        criticality, blocking_reason, coverage_strategy = "search_seed", None, "search_seed"
    elif criticality is None and method in REGISTRATION_METHODS:
        criticality = "registration_surface"
        blocking_reason = "strong static registration or executable entrypoint"
    elif criticality is None and method in CRITICAL_METHODS:
        criticality = "critical"
        blocking_reason = "strong static control or state-effect structure"
    else:
        criticality = criticality or "search_seed"
    strategy = coverage_strategy or {
        "critical": "required_mechanism",
        "registration_surface": "required_registration",
    }.get(criticality, "search_seed")
    return InventoryItem(
        _item_id(kind, locator, symbol), kind, locator, symbol, method,
        confidence, "candidate", criticality, strategy, blocking_reason,
        effect_class,
    )


def _relation(
    relation_type: str,
    source_locator: str,
    target_locator: str,
    method: str,
    confidence: str,
    *,
    source_item_id: str | None = None,
    target_item_id: str | None = None,
) -> InventoryRelation:
    fields = (relation_type, source_item_id or "", source_locator, target_item_id or "", target_locator)
    relation_id = "rel-" + hashlib.sha256("\0".join(fields).encode()).hexdigest()[:16]
    return InventoryRelation(
        relation_id, relation_type, source_item_id, source_locator,
        target_item_id, target_locator, method, confidence,
    )


def _render(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _last(value: str) -> str:
    return value.rsplit(".", 1)[-1].lower()


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _role(value: str, *, path: bool = False) -> str | None:
    candidate = f"/{value}" if path else value
    for word in ROLE_WORDS:
        if (f"/{word}" if path else word) in candidate:
            return word
    return None


def _has_word(value: str, words: set[str]) -> bool:
    value = value.lower().replace("-", "_")
    return any(re.search(rf"(?:^|_){re.escape(word)}(?:_|$)", value) for word in words)


def _permission(value: str) -> bool:
    value = value.lower()
    return "approval" in value or "approved" in value or "permission" in value or "authoriz" in value or "require_owner" in value


def _permission_call(value: str) -> bool:
    name = _last(value)
    if "permission" in name or "authoriz" in name:
        return True
    return (
        ("approval" in name or "approved" in name)
        and name.startswith(("check_", "enforce_", "has_", "load_approved_", "require_", "validate_", "verify_"))
    )


def _event_name(call: ast.Call) -> str | None:
    value = _literal(call.args[0]) if call.args else None
    return value if value and re.fullmatch(r"[A-Z][A-Za-z0-9_]+", value) else None


def _is_main_guard(node: ast.AST) -> bool:
    if not (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
    ):
        return False
    left, right = node.left, node.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and _literal(right) == "__main__"
    ) or (
        _literal(left) == "__main__"
        and isinstance(right, ast.Name)
        and right.id == "__name__"
    )


def _path_value(node: ast.AST) -> str | None:
    if value := _literal(node):
        return value
    if isinstance(node, ast.Call) and _last(_render(node.func)) == "path" and node.args:
        return _literal(node.args[0])
    return None


def _scope(definitions: list[ast.AST], relative: str, line: int) -> str:
    parents = [node for node in definitions if node.lineno <= line <= getattr(node, "end_lineno", node.lineno)]
    if not parents:
        return relative
    node = max(parents, key=lambda item: item.lineno)
    return f"{relative}:{node.lineno}:{node.name}"


def _effect_class(call: ast.Call) -> str | None:
    name = _render(call.func).lower()
    last = _last(name)
    receiver = name.rsplit(".", 1)[0] if "." in name else ""
    if name in {"os.remove", "os.unlink", "shutil.rmtree"} or last in {"unlink", "rmdir"}:
        return "filesystem_delete"
    if last in {"delete_data", "drop_table", "truncate_table"} or (
        last in {"delete", "drop"} and any(word in receiver for word in ("db", "database", "session", "table"))
    ):
        return "data_delete"
    if last in {"delete_repository", "force_push", "rename_repository", "transfer_repository"}:
        return "repository_mutation"
    if last in {"create_release", "deploy_release", "publish_release", "publish_staged_package", "upload_release"} or last.startswith(("publish_to_", "send_to_channel")):
        return "external_publish"
    if last in {"publish", "deploy", "upload"} and any(word in receiver for word in ("channel", "deployer", "github", "publisher", "release_client")):
        return "external_publish"
    if name in {"subprocess.run", "subprocess.call", "subprocess.check_call"} and call.args and isinstance(call.args[0], (ast.List, ast.Tuple)):
        words = [_literal(node) for node in call.args[0].elts]
        if len(words) >= 2 and words[0] == "git":
            command, args = words[1], [word for word in words[2:] if word]
            if command == "push":
                return "repository_mutation"
            if command in {"branch", "tag"}:
                mutating_flags = {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy"}
                read_only_flags = {"-l", "--list", "--show-current", "--contains", "--merged", "--no-merged"}
                positional = [arg for arg in args if not arg.startswith("-")]
                if mutating_flags & set(args) or (positional and not read_only_flags & set(args)):
                    return "repository_mutation"
    return None


def _python_items(path: Path, relative: str) -> tuple[list[InventoryItem], list[InventoryRelation]]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return [], []
    items: list[InventoryItem] = []
    relations: list[InventoryRelation] = []
    definitions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    decorators = {
        id(decorator): definition
        for definition in definitions
        for decorator in definition.decorator_list
        if isinstance(decorator, ast.Call)
    }
    if path.name == "__main__.py":
        items.append(_item("executable_entrypoint", relative, "__main__", "python-main-module", "high"))

    for node in ast.walk(tree):
        locator = f"{relative}:{getattr(node, 'lineno', 1)}"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if matched := _role(node.name.lower()):
                items.append(_item(matched, locator, node.name, "python-ast-name", "medium"))
            if _has_word(node.name, RECOVERY_WORDS):
                items.append(_item("recovery_path", locator, node.name, "python-ast-recovery-name", "low"))
            if any(phrase in node.name.lower() for phrase in HUMAN_PHRASES):
                items.append(_item("human_takeover", locator, node.name, "python-ast-human-name", "low"))

        if isinstance(node, ast.ClassDef):
            bases = {_last(_render(base)) for base in node.bases}
            members = {
                _render(target).lower()
                for statement in node.body
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target])
            }
            signals = {"pending", "running", "complete", "completed", "failed", "cancelled", "canceled", "reopened", "escalated", "terminal"}
            state_named = any(word in node.name.lower() for word in ("state", "status", "phase", "lifecycle"))
            if bases & {"enum", "strenum", "intenum"} and (state_named or members & signals):
                items.append(_item("state_machine", locator, node.name, "python-ast-enum", "high"))

        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [_render(target).lower() for target in targets]
            if any(name.endswith(("transitions", "state_machine", "transition_table")) for name in names):
                items.append(_item("state_machine", locator, sorted(names)[0], "python-ast-transition-table", "medium"))

        if isinstance(node, ast.If):
            rendered = _render(node.test)
            if _is_main_guard(node.test):
                items.append(_item("executable_entrypoint", locator, "__main__", "python-ast-main", "high"))
            if _permission(rendered):
                items.append(_item("permission_gate", locator, rendered, "python-ast-guard-condition", "medium"))

        if isinstance(node, ast.Subscript) and _render(node.value) in {"os.environ", "environ"}:
            if (name := _literal(node.slice)) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                items.append(_item("config_surface", locator, name, "python-ast-environment-read", "high"))

        if not isinstance(node, ast.Call):
            continue
        call_name, last = _render(node.func), _last(_render(node.func))
        scope = _scope(definitions, relative, node.lineno)
        if last == "getenv" or call_name in {"os.environ.get", "environ.get"}:
            if node.args and (name := _literal(node.args[0])) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name):
                items.append(_item("config_surface", locator, name, "python-ast-environment-read", "high"))

        if last == "add_parser" and node.args and (command := _literal(node.args[0])):
            item = _item("cli_command", locator, command, "python-ast-cli-registration", "high")
            items.append(item)
            relations.append(_relation("registration", locator, scope, item.discovery_method, "medium", source_item_id=item.item_id))

        if definition := decorators.get(id(node)):
            if last in API_METHODS | {"route"} and node.args and (route := _literal(node.args[0])):
                methods = [last.upper()]
                if last == "route":
                    methods = [
                        value.upper() for keyword in node.keywords if keyword.arg == "methods"
                        for value in ([_literal(element) for element in keyword.value.elts] if isinstance(keyword.value, (ast.List, ast.Tuple)) else [])
                        if value
                    ] or ["ANY"]
                for method in sorted(set(methods)):
                    item = _item("api_route", locator, f"{method} {route}", "python-ast-api-decorator", "high")
                    items.append(item)
                    relations.append(_relation("registration", f"{relative}:{definition.lineno}:{definition.name}", locator, item.discovery_method, "high", target_item_id=item.item_id))
            if last == "command":
                command = _literal(node.args[0]) if node.args else definition.name
                item = _item("cli_command", locator, command, "python-ast-cli-decorator", "high")
                items.append(item)
                relations.append(_relation("registration", locator, f"{relative}:{definition.lineno}:{definition.name}", item.discovery_method, "high", source_item_id=item.item_id))
            if last in CONSUMERS and (event := _event_name(node)):
                item = _item("event_type", locator, event, "python-ast-event-consumer", "medium")
                items.append(item)
                relations.append(_relation("consumer", f"{relative}:{definition.lineno}:{definition.name}", locator, item.discovery_method, "medium", target_item_id=item.item_id))

        if last in PRODUCERS and (event := _event_name(node)):
            item = _item("event_type", locator, event, "python-ast-event-producer", "medium")
            items.append(item)
            relations.append(_relation("producer", scope, locator, item.discovery_method, "medium", target_item_id=item.item_id))
        if _permission_call(call_name):
            items.append(_item("permission_gate", locator, call_name, "python-ast-permission-name", "low"))
        if _has_word(last, RECOVERY_WORDS):
            items.append(_item("recovery_path", locator, call_name, "python-ast-recovery-name", "low"))
        if any(phrase in call_name.lower() for phrase in HUMAN_PHRASES):
            items.append(_item("human_takeover", locator, call_name, "python-ast-human-name", "low"))

        if id(node) not in decorators and (effect := _effect_class(node)):
            items.append(_item("irreversible_action", locator, call_name, "python-ast-side-effect-call", "medium", effect_class=effect))

        target = method = None
        confidence = "high"
        state_effect_class = None
        if call_name.lower() in {"os.rename", "os.replace"} or last == "rename":
            target, method, confidence = call_name, "python-ast-filesystem-mutation", "medium"
            state_effect_class = "filesystem_mutation"
        elif last in {"write_text", "write_bytes"} and isinstance(node.func, ast.Attribute):
            target = _path_value(node.func.value) or _render(node.func.value) or "<dynamic-target>"
            confidence = "high" if _path_value(node.func.value) else "medium"
            method = "python-ast-path-write"
            state_effect_class = "filesystem_mutation"
        elif last == "open" and (node.args or isinstance(node.func, ast.Attribute)):
            is_method = isinstance(node.func, ast.Attribute)
            mode_index = 0 if is_method else 1
            mode = _literal(node.args[mode_index]) if len(node.args) > mode_index else None
            mode = next((_literal(keyword.value) for keyword in node.keywords if keyword.arg == "mode"), mode)
            if mode and any(flag in mode for flag in "wax+"):
                target_node = node.func.value if is_method else node.args[0]
                target = _path_value(target_node) or _render(target_node) or "<dynamic-target>"
                confidence = "high" if _path_value(target_node) else "medium"
                method = "python-ast-open-write"
                state_effect_class = "filesystem_mutation"
        elif last in {"execute", "executemany"} and node.args and (sql := _literal(node.args[0])) and re.match(r"^\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE)\b", sql, re.I):
            target, method = re.sub(r"\s+", " ", sql.strip())[:160], "python-ast-database-write"
            if re.match(r"^\s*(DELETE|DROP|TRUNCATE)\b", sql, re.I):
                items.append(_item("irreversible_action", locator, target, "python-ast-side-effect-call", "high", effect_class="data_delete"))
        elif (
            last in PERSIST_CALLS
            and TRUSTED_PERSIST_WRAPPERS.get(scope.rsplit(":", 1)[-1]) != last
        ):
            target, method, confidence = call_name, "python-ast-authoritative-write-call", "medium"
        if target and method:
            item = _item(
                "persistent_state", locator, target, method, confidence,
                effect_class=state_effect_class,
            )
            items.append(item)
            relations.append(_relation("authoritative_write", scope, locator, method, confidence, target_item_id=item.item_id))

    for line_number, line in enumerate(text.splitlines(), 1):
        for match in EVENT_RE.finditer(line):
            locator = f"{relative}:{line_number}"
            items.append(_item("event_type", locator, match.group(1), "event-string-heuristic", "low"))
    return items, relations


def _pyproject(path: Path, relative: str) -> tuple[list[InventoryItem], list[InventoryRelation]]:
    try:
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return [], []
    items = [_item("config_surface", relative, path.name, "pyproject-structured-config", "high")]
    relations: list[InventoryRelation] = []
    for table in ("scripts", "gui-scripts"):
        scripts = project.get(table, {}) if isinstance(project, dict) else {}
        if not isinstance(scripts, dict):
            continue
        for command, target in sorted(scripts.items()):
            if not isinstance(command, str) or not isinstance(target, str):
                continue
            locator = f"{relative}:[project.{table}].{command}"
            cli = _item("cli_command", locator, command, "pyproject-console-script", "high")
            entry = _item("executable_entrypoint", locator, target, "pyproject-console-script", "high")
            items.extend((cli, entry))
            relations.append(_relation("registration", locator, locator, cli.discovery_method, "high", source_item_id=cli.item_id, target_item_id=entry.item_id))
    return items, relations


def _systemd(path: Path, relative: str) -> tuple[list[InventoryItem], list[InventoryRelation]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []
    kind = "service" if path.suffix == ".service" else "timer"
    unit = _item(kind, relative, path.name, "systemd-unit-file", "high")
    items, relations, section = [unit], [], ""
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        locator = f"{relative}:{number}"
        if section == "Service" and key.startswith("Exec") and value:
            try:
                command = shlex.split(value.lstrip("-+!:@"))[0]
            except (ValueError, IndexError):
                command = value.split()[0] if value.split() else ""
            if command:
                entry = _item("executable_entrypoint", locator, command, "systemd-exec-directive", "high")
                items.append(entry)
                relations.append(_relation("registration", relative, locator, entry.discovery_method, "high", source_item_id=unit.item_id, target_item_id=entry.item_id))
        if section == "Service" and key in {"Environment", "EnvironmentFile"}:
            if key == "EnvironmentFile":
                names = [value.lstrip("-")]
            else:
                try:
                    names = [token.split("=", 1)[0] for token in shlex.split(value) if "=" in token]
                except ValueError:
                    names = []
            for name in sorted(set(filter(None, names))):
                required = key == "EnvironmentFile"
                items.append(_item(
                    "config_surface", locator, name,
                    "systemd-environment-directive", "high",
                    criticality="registration_surface" if required else "search_seed",
                    blocking_reason="systemd registered configuration input" if required else None,
                    coverage_strategy="required_registration" if required else "search_seed",
                ))
        if kind == "timer" and section == "Timer" and key.startswith("On"):
            relations.append(_relation("registration", relative, locator, "systemd-timer-schedule", "high", source_item_id=unit.item_id))
        if kind == "timer" and section == "Timer" and key == "Unit" and value:
            relations.append(_relation("registration", relative, value, "systemd-timer-target", "high", source_item_id=unit.item_id))
    return items, relations


def _shell(path: Path, relative: str) -> list[InventoryItem]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    strong = text.startswith("#!") or os.access(path, os.X_OK)
    items = [_item(
        "shell_entrypoint", relative, path.name, "executable-file",
        "high" if strong else "medium",
        criticality="registration_surface" if strong else "search_seed",
        blocking_reason="executable shell entrypoint" if strong else None,
    )]
    assigned = {
        match.group(1) for line in text.splitlines()
        if (match := re.match(r"^\s*(?:(?:export|readonly|local)\s+)?([A-Z][A-Z0-9_]*)=", line))
    }
    for number, line in enumerate(text.splitlines(), 1):
        content = line.split("#", 1)[0]
        assignment = re.match(r"^\s*(?:(?:export|readonly|local)\s+)?([A-Z][A-Z0-9_]*)=", content)
        for match in ENV_RE.finditer(content):
            name = match.group(1) or match.group(2)
            self_default = bool(assignment and assignment.group(1) == name and match.group(1) and ":-" in match.group(0))
            if name not in assigned or self_default:
                items.append(_item("config_surface", f"{relative}:{number}", name, "shell-environment-expansion", "medium"))
    return items


def _config(path: Path, relative: str) -> InventoryItem | None:
    parts = [part.lower() for part in Path(relative).parts]
    required = path.name in REQUIRED_CONFIG_FILES
    if path.name == "pyproject.toml" or not (required or
        "config" in parts[:-1] or any(word in path.name.lower() for word in ("config", "settings", ".env"))
    ):
        return None
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
        if suffix == ".json" and isinstance(json.loads(text), (dict, list)):
            method, confidence = "json-config-parse", "high"
        elif suffix == ".toml" and isinstance(tomllib.loads(text), dict):
            method, confidence = "toml-config-parse", "high"
        elif suffix in {".ini", ".cfg"}:
            parser = configparser.ConfigParser()
            parser.read_string(text)
            if not parser.sections() and not parser.defaults():
                return None
            method, confidence = "ini-config-parse", "high"
        elif suffix in {".env", ".example"} and any(re.match(r"^\s*(?:export\s+)?[A-Z][A-Z0-9_]*\s*=", line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")):
            method, confidence = "env-config-parse", "high"
        elif suffix in {".yaml", ".yml"} and any(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")):
            method, confidence = "yaml-top-level-key-scan", "medium"
        else:
            return None
    except (OSError, UnicodeDecodeError, ValueError, configparser.Error):
        return None
    return _item(
        "config_surface", relative, path.name, method, confidence,
        criticality="registration_surface" if required else "search_seed",
        blocking_reason="registered governance or public configuration contract" if required else None,
        coverage_strategy="required_registration" if required else "search_seed",
    )


def build_inventory(repo: Path) -> dict:
    repo = repo.resolve()
    items: list[InventoryItem] = []
    relations: list[InventoryRelation] = []
    file_hashes: list[str] = []
    for path in _iter_files(repo):
        relative = path.relative_to(repo).as_posix()
        try:
            file_hashes.append(f"{relative}\0{hashlib.sha256(path.read_bytes()).hexdigest()}")
        except OSError:
            continue
        if path.suffix == ".py":
            found, edges = _python_items(path, relative)
            items.extend(found); relations.extend(edges)
        if path.name == "pyproject.toml":
            found, edges = _pyproject(path, relative)
            items.extend(found); relations.extend(edges)
        if path.suffix in {".service", ".timer"}:
            found, edges = _systemd(path, relative)
            items.extend(found); relations.extend(edges)
        if path.suffix in {".sh", ".bash", ".zsh"}:
            items.extend(_shell(path, relative))
        if found_config := _config(path, relative):
            items.append(found_config)
        if matched := _role(relative.lower(), path=True):
            items.append(_item(matched, relative, path.name, "path-name-heuristic", "low"))

    items = sorted(
        {(item.kind, item.locator, item.symbol): item for item in items}.values(),
        key=lambda item: (item.kind, item.locator, item.symbol),
    )
    known = {item.item_id for item in items}
    relations = sorted(
        {
            (edge.relation_type, edge.source_item_id, edge.source_locator, edge.target_item_id, edge.target_locator): edge
            for edge in relations
            if (not edge.source_item_id or edge.source_item_id in known)
            and (not edge.target_item_id or edge.target_item_id in known)
        }.values(),
        key=lambda edge: (edge.relation_type, edge.source_item_id or "", edge.source_locator, edge.target_item_id or "", edge.target_locator),
    )
    unknowns = [
        {
            "unknown_id": f"unknown-{item.item_id}",
            "inventory_item_id": item.item_id,
            "question": f"Verify whether {item.kind} {item.symbol} at {item.locator} is reachable in the canonical runtime and identify its consumers.",
            "blocking": item.blocking_reason is not None,
            "criticality": item.criticality,
            "blocking_reason": item.blocking_reason,
        }
        for item in items
    ]
    repo_hash = hashlib.sha256("\n".join(sorted(file_hashes)).encode()).hexdigest()
    criticality_counts = {
        level: sum(item.criticality == level for item in items)
        for level in ("critical", "registration_surface", "search_seed")
    }
    return {
        "schema_version": "repository-inventory/v2",
        "repository": str(repo),
        "repository_content_hash": repo_hash,
        "inventory_semantics": (
            "Static discovery candidates only. Search seeds never block coverage; "
            "strong registrations and control or mutation syntax require follow-up "
            "but still do not prove reachability, authority, runtime effects, or public readiness."
        ),
        "items": [asdict(item) for item in items],
        "relations": [asdict(edge) for edge in relations],
        "unknowns": unknowns,
        "counts": {
            "items": len(items), "relations": len(relations),
            "blocking_unknowns": sum(item["blocking"] for item in unknowns),
            **criticality_counts,
        },
    }


def write_inventory(repo: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_inventory(repo), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
