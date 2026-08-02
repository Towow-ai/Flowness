from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .registry import ValidationError
from .resources import CONFIG_ROOT, PACKAGE_ROOT, SCHEMAS_ROOT
from .schema_validation import validate_payload

APPROVED_POLICY_PATH = CONFIG_ROOT / "governance-policy.json"
APPROVED_POLICY_SCHEMA = SCHEMAS_ROOT / "governance-policy.schema.json"

# This value is intentionally compiled into the package. Changing policy bytes
# requires an owner-reviewed code change and a new release of this harness.
APPROVED_POLICY_SHA256 = (
    "013e1f2c6d861aafdd78d9136f4e34429e247977594e01794fd0d6e918ada885"
)


def load_approved_policy(policy_path: Path | None = None) -> tuple[dict[str, Any], str]:
    path = APPROVED_POLICY_PATH if policy_path is None else policy_path.resolve()
    if path != APPROVED_POLICY_PATH.resolve():
        raise ValidationError("release evaluation only accepts the built-in policy")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != APPROVED_POLICY_SHA256:
        raise ValidationError("built-in owner-approved policy hash mismatch")
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("built-in policy is not valid JSON") from exc
    validate_payload(policy, APPROVED_POLICY_SCHEMA, "governance policy")
    return policy, f"sha256:{digest}"
