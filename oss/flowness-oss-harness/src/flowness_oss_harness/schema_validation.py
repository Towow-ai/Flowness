from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .registry import ValidationError


def _load_schema(schema_path: Path, label: str) -> dict[str, Any]:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label} schema: {schema_path}") from exc
    if not isinstance(schema, dict):
        raise ValidationError(f"invalid {label} schema: root must be an object")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ValidationError(f"invalid {label} schema: {exc}") from exc
    return schema


def _json_pointer(path: tuple[str | int, ...]) -> str:
    if not path:
        return "<root>"
    return "/" + "/".join(
        str(item).replace("~", "~0").replace("/", "~1") for item in path
    )


def validate_openai_response_format_schema(
    schema_path: Path,
    label: str,
) -> None:
    """Reject locally the response-schema defects known to produce API 400s.

    This is deliberately narrower than the business-schema validator: it preserves
    conditional business rules while enforcing deterministic transport invariants
    for every schema handed to ``codex exec --output-schema``.
    """

    schema = _load_schema(schema_path, label)
    errors: list[tuple[str, str]] = []

    def visit(node: Any, path: tuple[str | int, ...]) -> None:
        if isinstance(node, dict):
            pointer = _json_pointer(path)
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                errors.append(
                    (pointer, "object schemas must set additionalProperties to false")
                )
            for keyword in ("const", "enum"):
                if keyword not in node:
                    continue
                if "type" not in node:
                    errors.append(
                        (pointer, f"{keyword} schemas must declare an explicit type")
                    )
                    continue
                values = [node["const"]] if keyword == "const" else node["enum"]
                type_schema = {"type": node["type"]}
                for index, value in enumerate(values):
                    if not Draft202012Validator(type_schema).is_valid(value):
                        errors.append(
                            (
                                pointer,
                                f"{keyword} value {index} does not match declared type",
                            )
                        )
            for key, value in node.items():
                visit(value, (*path, key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, (*path, index))

    visit(schema, ())
    if errors:
        pointer, detail = sorted(errors)[0]
        raise ValidationError(
            f"{label} schema is not OpenAI response-format compatible at "
            f"{pointer}: {detail}"
        )


def validate_payload(
    payload: Any,
    schema_path: Path,
    label: str,
) -> None:
    schema = _load_schema(schema_path, label)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "<root>"
        raise ValidationError(f"{label} schema violation at {location}: {error.message}")


def load_validated_json(path: Path, schema_path: Path, label: str) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path}") from exc
    validate_payload(payload, schema_path, label)
    return payload
