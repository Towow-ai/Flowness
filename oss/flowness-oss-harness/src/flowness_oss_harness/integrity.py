from __future__ import annotations

import hashlib
import json
from typing import Any

from .registry import ValidationError


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_hash(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def signed_payload_hash(payload: dict[str, Any], field: str = "signature") -> str:
    unsigned = {key: value for key, value in payload.items() if key != field}
    return canonical_hash(unsigned)


def verify_self_hash(payload: dict[str, Any], field: str = "signature") -> None:
    if payload.get(field) != signed_payload_hash(payload, field):
        raise ValidationError(f"invalid immutable {field}")
