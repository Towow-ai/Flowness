from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .registry import ValidationError
from .resources import CONFIG_ROOT, SCHEMAS_ROOT
from .schema_validation import validate_payload

EXECUTION_POLICY_PATH = CONFIG_ROOT / "execution-policy.json"
EXECUTION_POLICY_SCHEMA = SCHEMAS_ROOT / "execution-policy.schema.json"

# Updating execution authority is a source change: the bytes are pinned in the
# runtime package and the corresponding constant must change in the same review.
EXECUTION_POLICY_SHA256 = (
    "6ee9efa07038580011d2811e67a319b596eb3c58ec9c08028de11f25134fa95e"
)
EXECUTION_POLICY_SCHEMA_SHA256 = (
    "cea334edd7fdc9c16ed0cc419ad08c7a9d0c4e312c614047e8d64490fc823e37"
)


def _read_regular(path: Path, missing_code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(missing_code)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError(missing_code) from exc


def load_execution_policy() -> tuple[dict[str, Any], str]:
    raw = _read_regular(EXECUTION_POLICY_PATH, "EXECUTION-POLICY-MISSING")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXECUTION_POLICY_SHA256:
        raise ValidationError("EXECUTION-POLICY-HASH-MISMATCH")
    schema_raw = _read_regular(
        EXECUTION_POLICY_SCHEMA, "EXECUTION-POLICY-SCHEMA-MISSING"
    )
    if hashlib.sha256(schema_raw).hexdigest() != EXECUTION_POLICY_SCHEMA_SHA256:
        raise ValidationError("EXECUTION-POLICY-SCHEMA-HASH-MISMATCH")
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("EXECUTION-POLICY-INVALID-JSON") from exc
    validate_payload(policy, EXECUTION_POLICY_SCHEMA, "execution policy")
    return policy, f"sha256:{digest}"


def require_command_execution_allowed(command: str) -> tuple[dict[str, Any], str]:
    """Reject every current CLI command while the program is planning-frozen."""

    return require_agent_execution_allowed(f"command:{command}")


def require_agent_execution_allowed(operation: str) -> tuple[dict[str, Any], str]:
    """Fail closed before a role directory or Codex subprocess can be created."""

    policy, policy_hash = load_execution_policy()
    status = policy["status"]
    if status["phase"] == "planning_freeze":
        raise ValidationError(
            f"EXECUTION-FROZEN: operation={operation} phase=planning_freeze "
            f"policy_hash={policy_hash}"
        )
    if status["execution_allowed"] is not True:
        raise ValidationError(
            f"EXECUTION-NOT-ALLOWED: operation={operation} "
            f"phase={status['phase']} policy_hash={policy_hash}"
        )
    if status["control_enforcement"] != "controller_bound_and_tested":
        raise ValidationError(
            f"EXECUTION-CONTROL-NOT-BOUND: operation={operation} "
            f"policy_hash={policy_hash}"
        )
    if "resume_decision" not in policy:
        raise ValidationError(
            f"RESUME-EVIDENCE-MISSING: operation={operation} "
            f"policy_hash={policy_hash}"
        )
    return policy, policy_hash
