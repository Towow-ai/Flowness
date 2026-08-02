from __future__ import annotations

"""Build and verify a deliberately narrow, agent-safe evidence derivative.

This module is not a server collector and not a redactor.  It accepts only
already-redacted records, rejects unsafe material again, and proves only the
integrity of the resulting derivative pack.  The source audit object remains
opaque, so a passing pack never promotes a mechanism to ``current_verified``.
"""

import json
import re
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


RECORD_SCHEMA = SCHEMAS_ROOT / "agent-safe-evidence-record.schema.json"
MANIFEST_SCHEMA = SCHEMAS_ROOT / "agent-evidence-pack-manifest.schema.json"
MANIFEST_NAME = "agent-pack-manifest.json"
SCREENING_NAME = "input-screening-report.json"
_PACK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DENIED_KEY = re.compile(
    r"(?i)(?:password|passwd|secret|credential|token|authorization|"
    r"api[_-]?key|config[_-]?value|repository|source[_-]?path|vault[_-]?locator)"
)
_SAFE_TOKEN_FIELDS = {"subject_token", "opaque_token", "seal_local_token"}
_DENIED_CONTENT = (
    ("private-home-path", re.compile(r"(?:/Users|/home)/[^/\s]+/")),
    ("ip-address", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
    ("email-address", re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")),
    ("credential-url", re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")),
    ("authorization-header", re.compile(r"(?i)\bauthorization\s*:\s*\S+")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("known-secret-token", re.compile(r"\b(?:sk-ant|sk-proj|gh[pousr]|xox[baprs])-[A-Za-z0-9_-]{10,}\b")),
)
_SUCCESS_FIELDS = {"from_state", "to_state", "postcondition", "accepted", "verified"}
_SUCCESS_WORD = re.compile(r"(?i)\b(?:accepted|completed|passed|success(?:ful)?|verified)\b")


def _record_without_hash(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "derived_sha256"}


def _string_is_safe(value: str, field: str) -> None:
    if "\x00" in value or len(value) > 10_000:
        raise ValidationError(f"AGENT-PACK-UNSAFE-{field.upper()}")
    for label, pattern in _DENIED_CONTENT:
        if pattern.search(value):
            raise ValidationError(f"AGENT-PACK-UNSAFE-{label.upper()}")


def _screen_payload(value: Any, path: tuple[str, ...] = ()) -> None:
    field = ".".join(path) or "payload"
    if isinstance(value, str):
        _string_is_safe(value, field)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValidationError("AGENT-PACK-PAYLOAD-OVERSIZED")
        for index, item in enumerate(value):
            _screen_payload(item, (*path, str(index)))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or (_DENIED_KEY.search(key) and key not in _SAFE_TOKEN_FIELDS)
            ):
                raise ValidationError("AGENT-PACK-UNSAFE-PAYLOAD-FIELD")
            _screen_payload(item, (*path, key))
        return
    raise ValidationError("AGENT-PACK-PAYLOAD-TYPE-INVALID")


def _validate_record(record: dict[str, Any], known_mechanism_ids: set[str]) -> None:
    validate_payload(record, RECORD_SCHEMA, "agent-safe evidence record")
    if not set(record["mechanism_ids"]).issubset(known_mechanism_ids):
        raise ValidationError("AGENT-PACK-UNKNOWN-MECHANISM")
    for field in ("record_id", "audit_object_id", "coordinate"):
        value = record[field]
        if isinstance(value, str):
            _string_is_safe(value, field)
        else:
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, str):
                    _string_is_safe(nested_value, f"{field}.{nested_key}")
    _screen_payload(record["payload"])
    if record["capture_status"] != "captured":
        if any(key in record["payload"] for key in _SUCCESS_FIELDS):
            raise ValidationError("AGENT-PACK-NONCAPTURED-SUCCESS-STATE")
        if any(
            isinstance(value, str) and _SUCCESS_WORD.search(value)
            for value in record["payload"].values()
        ):
            raise ValidationError("AGENT-PACK-NONCAPTURED-SUCCESS-STATE")
    if record["derived_sha256"] != canonical_hash(_record_without_hash(record)):
        raise ValidationError("AGENT-PACK-DERIVED-HASH-MISMATCH")


def _ensure_new_target(target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise ValidationError("AGENT-PACK-TARGET-EXISTS")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValidationError("AGENT-PACK-TARGET-PARENT-INVALID")


def _screening_report(pack_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    report = {
        "schema_version": "agent-pack-input-screening/v1",
        "pack_id": pack_id,
        "scope": "second_pass_screen_of_pre_redacted_input_only",
        "accepted_record_ids": [record["record_id"] for record in records],
        "not_proven": [
            "raw audit object existence",
            "correctness of upstream redaction",
            "server runtime behavior",
            "permission to publish",
        ],
    }
    report["report_sha256"] = canonical_hash(report)
    return report


def build_agent_evidence_pack(
    records: list[dict[str, Any]],
    *,
    pack_id: str,
    cutoff: str,
    known_mechanism_ids: set[str],
    target: Path,
) -> dict[str, Any]:
    """Write an immutable, locally self-consistent derivative pack.

    ``records`` must already be redacted.  This function does no source
    collection and invokes no external commands, so it is safe to run against a
    prepared staging input but cannot certify that an audit vault exists.
    """

    if not _PACK_ID.fullmatch(pack_id):
        raise ValidationError("AGENT-PACK-ID-INVALID")
    if not isinstance(cutoff, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z", cutoff
    ):
        raise ValidationError("AGENT-PACK-CUTOFF-INVALID")
    if not records or not known_mechanism_ids:
        raise ValidationError("AGENT-PACK-INPUT-EMPTY")
    if len({record.get("record_id") for record in records}) != len(records):
        raise ValidationError("AGENT-PACK-DUPLICATE-RECORD-ID")
    for record in records:
        if not isinstance(record, dict):
            raise ValidationError("AGENT-PACK-RECORD-INVALID")
        _validate_record(record, known_mechanism_ids)
    _ensure_new_target(target)
    records_dir = target / "records"
    try:
        records_dir.mkdir(parents=True)
    except OSError as exc:
        raise ValidationError("AGENT-PACK-TARGET-CREATE-FAILED") from exc
    entries = []
    for record in sorted(records, key=lambda item: item["record_id"]):
        path = records_dir / f"{record['record_id']}.json"
        atomic_create_json(path, record)
        entries.append(
            {
                "record_id": record["record_id"],
                "path": f"records/{record['record_id']}.json",
                "derived_sha256": record["derived_sha256"],
                "capture_status": record["capture_status"],
                "mechanism_ids": record["mechanism_ids"],
            }
        )
    manifest = {
        "schema_version": "agent-evidence-pack-manifest/v1",
        "pack_id": pack_id,
        "scope": "pre_redacted_agent_side_derivative_only",
        "cutoff": cutoff,
        "source_boundary": "does_not_read_or_prove_audit_vault_server_or_raw_source",
        "records": entries,
        "coherence_checks": [
            "record schema and declared derived hash verified",
            "record IDs and paths are unique",
            "every mechanism ID is in the supplied sealed registry selection",
            "second-pass unsafe-content screening passed",
        ],
        "gaps": sorted(
            record["record_id"]
            for record in records
            if record["capture_status"] != "captured"
        ),
        "exclusions": [
            "raw audit objects and vault locators",
            "server paths, host identity, credentials, configuration values, and private transcripts",
            "upstream redaction and source-existence proof",
        ],
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    validate_payload(manifest, MANIFEST_SCHEMA, "agent evidence pack manifest")
    atomic_create_json(target / MANIFEST_NAME, manifest)
    atomic_create_json(target / SCREENING_NAME, _screening_report(pack_id, records))
    return manifest


def verify_agent_evidence_pack(
    target: Path, *, known_mechanism_ids: set[str]
) -> dict[str, Any]:
    """Verify only pack self-consistency; never elevate runtime truth."""

    if target.is_symlink() or not target.is_dir():
        raise ValidationError("AGENT-PACK-TARGET-INVALID")
    manifest = load_validated_json(
        target / MANIFEST_NAME, MANIFEST_SCHEMA, "agent evidence pack manifest"
    )
    verify_self_hash(manifest, "manifest_sha256")
    expected_paths = {MANIFEST_NAME, SCREENING_NAME}
    for entry in manifest["records"]:
        record_path = target / entry["path"]
        if record_path.is_symlink() or not record_path.is_file():
            raise ValidationError("AGENT-PACK-RECORD-MISSING")
        record = load_validated_json(record_path, RECORD_SCHEMA, "agent-safe evidence record")
        _validate_record(record, known_mechanism_ids)
        if (
            record["record_id"] != entry["record_id"]
            or record["derived_sha256"] != entry["derived_sha256"]
            or record["capture_status"] != entry["capture_status"]
            or record["mechanism_ids"] != entry["mechanism_ids"]
        ):
            raise ValidationError("AGENT-PACK-MANIFEST-RECORD-MISMATCH")
        expected_paths.add(entry["path"])
    actual_paths = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != expected_paths:
        raise ValidationError("AGENT-PACK-UNDECLARED-FILE")
    screening = json.loads((target / SCREENING_NAME).read_text(encoding="utf-8"))
    if (
        screening.get("pack_id") != manifest["pack_id"]
        or screening.get("report_sha256") != canonical_hash(
            {key: value for key, value in screening.items() if key != "report_sha256"}
        )
    ):
        raise ValidationError("AGENT-PACK-SCREENING-REPORT-INVALID")
    return manifest
