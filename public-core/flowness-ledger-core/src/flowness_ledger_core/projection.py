from __future__ import annotations

"""Small deterministic committed-view projection with explicit freshness."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .ledger import Ledger, LedgerError


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _head(ledger: Ledger) -> tuple[int, str | None]:
    rows, incomplete = ledger._load()
    if incomplete:
        raise LedgerError("projection refuses an incomplete ledger tail")
    ledger._index(rows)
    return (rows[-1]["sequence"], rows[-1]["record_hash"]) if rows else (0, None)


def rebuild_type_projection(ledger: Ledger, projection_id: str = "committed-types") -> dict[str, Any]:
    """Persist a projection whose watermark covers every audit record.

    The value is derived only from committed records, while the watermark moves
    across accepted, rejected and proposal records alike. This prevents a
    rejected decision from leaving the projection deceptively "fresh".
    """

    if not projection_id or "/" in projection_id or ".." in projection_id:
        raise LedgerError("projection_id is unsafe")
    sequence, record_hash = _head(ledger)
    counts: dict[str, int] = {}
    for row in ledger.read("committed"):
        value = row["payload"].get("type")
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    unsigned = {
        "format": "flowness-ledger-core/projection/v1",
        "projection_id": projection_id,
        "watermark": {"sequence": sequence, "record_hash": record_hash},
        "committed_type_counts": dict(sorted(counts.items())),
    }
    payload = {**unsigned, "projection_hash": _hash(unsigned)}
    directory = ledger.directory / "projections"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise LedgerError("projection directory is unsafe")
    path = directory / f"{projection_id}.json"
    if path.is_symlink():
        raise LedgerError("projection path is unsafe")
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        handle.write(_canonical(payload) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return payload


def read_fresh_type_projection(ledger: Ledger, projection_id: str = "committed-types") -> dict[str, Any]:
    path = ledger.directory / "projections" / f"{projection_id}.json"
    if path.is_symlink() or not path.is_file():
        raise LedgerError("projection does not exist or is unsafe")
    try:
        payload = json.loads(path.read_bytes())
    except json.JSONDecodeError as exc:
        raise LedgerError("projection is not valid JSON") from exc
    unsigned = {key: value for key, value in payload.items() if key != "projection_hash"}
    if payload.get("format") != "flowness-ledger-core/projection/v1" or payload.get("projection_hash") != _hash(unsigned):
        raise LedgerError("projection hash is invalid")
    sequence, record_hash = _head(ledger)
    if payload.get("watermark") != {"sequence": sequence, "record_hash": record_hash}:
        raise LedgerError("projection is stale; rebuild required")
    return payload
