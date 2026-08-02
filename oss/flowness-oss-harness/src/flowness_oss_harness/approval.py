from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .integrity import canonical_hash, canonical_json_bytes
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload

OWNER_APPROVAL_SCHEMA = SCHEMAS_ROOT / "owner-approval.schema.json"
TRUSTED_KEYS_SCHEMA = SCHEMAS_ROOT / "trusted-owner-keys.schema.json"
DEFAULT_TRUSTED_KEYS_PATH = Path("/etc/flowness-oss/trusted-owner-keys.json")


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def approval_message(approval: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in approval.items() if key != "signature"}
    return canonical_json_bytes(unsigned)


def load_trusted_owner_keys(path: Path) -> dict[str, Any]:
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise ValidationError("trusted owner keys must be a regular file")
    current = path
    while True:
        if current.is_symlink():
            raise ValidationError("trusted owner key path cannot contain symlinks")
        stat = current.stat()
        if stat.st_uid != 0:
            raise ValidationError("trusted owner key path must be owned by root")
        if stat.st_mode & 0o022:
            raise ValidationError(
                "trusted owner key path cannot be group/world writable"
            )
        if current.parent == current:
            break
        current = current.parent
    payload = load_validated_json(path, TRUSTED_KEYS_SCHEMA, "trusted owner keys")
    identities = [
        (item["owner_id"], item["key_id"]) for item in payload.get("keys", [])
    ]
    if len(identities) != len(set(identities)):
        raise ValidationError("trusted owner key identities must be unique")
    return payload


def validate_owner_approval(
    approval: dict[str, Any] | None,
    candidate: dict[str, Any],
    policy_hash: str,
    decision_hash: str,
    trusted_keys: dict[str, Any] | None,
    now: datetime | None = None,
) -> str | None:
    if approval is None:
        return None
    if not isinstance(approval, dict):
        raise ValidationError("owner approval must be an independent JSON object")
    validate_payload(approval, OWNER_APPROVAL_SCHEMA, "owner approval")
    if approval.get("candidate_hash") != canonical_hash(candidate):
        raise ValidationError("owner approval candidate hash mismatch")
    if approval.get("snapshot_id") != candidate.get("snapshot", {}).get("snapshot_id"):
        raise ValidationError("owner approval snapshot mismatch")
    if approval.get("policy_hash") != policy_hash:
        raise ValidationError("owner approval policy hash mismatch")
    if approval.get("decision_hash") != decision_hash:
        raise ValidationError("owner approval decision hash mismatch")
    if trusted_keys is None:
        raise ValidationError("owner approval requires root-owned trusted owner keys")
    validate_payload(trusted_keys, TRUSTED_KEYS_SCHEMA, "trusted owner keys")
    matching = [
        item
        for item in trusted_keys.get("keys", [])
        if item.get("owner_id") == approval.get("owner_id")
        and item.get("key_id") == approval.get("key_id")
    ]
    if len(matching) != 1:
        raise ValidationError("owner approval key is not uniquely trusted")
    key = matching[0]
    if key.get("algorithm") != "ed25519":
        raise ValidationError("owner approval key algorithm is not ed25519")

    current = (now or datetime.now(UTC)).astimezone(UTC)
    issued = _timestamp(approval["issued_at"], "issued_at")
    expires = _timestamp(approval["expires_at"], "expires_at")
    not_before = _timestamp(key["not_before"], "key.not_before")
    not_after = _timestamp(key["not_after"], "key.not_after")
    revoked_at = (
        _timestamp(key["revoked_at"], "key.revoked_at")
        if key.get("revoked_at") is not None
        else None
    )
    if issued > current or expires <= current or expires <= issued:
        raise ValidationError("owner approval is not currently valid")
    if issued < not_before or expires > not_after:
        raise ValidationError("owner approval exceeds trusted key validity")
    if revoked_at is not None and (
        current >= revoked_at or expires > revoked_at
    ):
        raise ValidationError("owner approval key is revoked")

    try:
        public_bytes = base64.b64decode(
            key["public_key_base64"],
            validate=True,
        )
        signature_bytes = base64.b64decode(
            approval["signature"]["value_base64"],
            validate=True,
        )
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        public_key.verify(signature_bytes, approval_message(approval))
    except (binascii.Error, ValueError, InvalidSignature) as exc:
        raise ValidationError("owner approval Ed25519 signature is invalid") from exc
    return approval["decision"]
