"""Deterministic, private reflow receipts for Content Graph changes.

The Content Graph impact plan is intentionally only a bounded invalidation
worklist.  This module closes the *local planning* loop: it verifies that
worklist, assigns each obligation to roles already present in a hash-bound
role registry, and seals a machine-readable private update receipt.

Assignments are ``planned_not_dispatched``.  This module does not start an
agent, write a draft, mutate a graph, call a channel, collect analytics, or
perform any other external effect.  A receipt therefore proves that a change
has a deterministic review route, never that multi-agent runtime work ran or
that material is ready to publish.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .content_impact_review_plan import verify_content_impact_review_plan
from .controller import _load_roles
from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


SCHEMA = SCHEMAS_ROOT / "content-update-receipt.schema.json"
ASSIGNMENT_POLICY_ID = "content-machine/CM-009/v1"
NO_EXTERNAL_EFFECT_BOUNDARY = (
    "private deterministic update planning only; assignments are not dispatched and cannot mutate "
    "claims, evidence, candidate state, approvals, source material, publish, collect network data, "
    "use credentials, schedule, or send externally."
)
_EFFECT_ATTESTATION = {
    "role_dispatch": "not_attempted",
    "claim_registry": "not_mutated",
    "evidence_registry": "not_mutated",
    "candidate_state": "not_mutated",
    "approval_state": "not_mutated",
    "source_material": "not_mutated",
    "publish": "not_attempted",
    "network": "not_attempted",
    "credential_use": "not_attempted",
    "external_send": "not_attempted",
    "schedule": "not_attempted",
}
_COMPLETION_CONDITION = "typed_private_output_and_all_assigned_independent_reviews_required"
_CHANNEL_REVIEWERS = ("judge.channel-distribution-a", "judge.channel-distribution-b")
_PUBLIC_CLARITY_REVIEWERS = ("judge.public-clarity-a", "judge.public-clarity-b")
_VISUAL_REVIEWERS = ("judge.visual-demo-a", "judge.visual-demo-b")
_COMMUNITY_REVIEWERS = ("judge.oss-community-a", "judge.oss-community-b")


def _safe_root(root: Path | str) -> Path:
    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir():
        raise ValidationError("CONTENT-UPDATE-RECEIPT-ROOT-UNSAFE")
    return base


def _safe_relative_file(root: Path, value: Path | str, *, error: str) -> tuple[Path, str]:
    requested = Path(value)
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValidationError(error) from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError(error)
    stable = root / relative
    if stable.is_symlink() or not stable.is_file():
        raise ValidationError(error)
    return stable, relative.as_posix()


def _hash_bytes(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_id(unsigned: dict[str, Any]) -> str:
    identity = {key: value for key, value in unsigned.items() if key not in {"receipt_id"}}
    identity["receipt_id"] = ""
    return "content-update-" + canonical_hash(identity).removeprefix("sha256:")[:24]


def _work_item_id(obligation: dict[str, Any], assigned: tuple[str, ...], reviewers: tuple[str, ...]) -> str:
    identity = {
        "source_obligation_id": obligation["obligation_id"],
        "kind": obligation["kind"],
        "target_id": obligation["target_id"],
        "assigned_role_ids": list(assigned),
        "reviewer_role_ids": list(reviewers),
    }
    return "content-update-work-" + canonical_hash(identity).removeprefix("sha256:")[:24]


def _assignment(obligation: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the deliberately narrow CM-009 assignment policy.

    The policy is metadata routing, not an inference about which agent is
    capable of resolving a mechanism.  The asset-id branches identify the
    existing visual/demo and community entry routes; all other source material
    receives the general content route.  Channel rebuild has two independent
    distribution reviewers by design.
    """

    kind = obligation["kind"]
    target = obligation["target_id"]
    if kind == "reassemble_channel_package":
        return ("channel.adapter", "publisher.stager"), _CHANNEL_REVIEWERS
    if kind == "revalidate_claim":
        return ("content.compiler",), _PUBLIC_CLARITY_REVIEWERS
    if kind == "review_predecessor_removal":
        return ("content.compiler",), _PUBLIC_CLARITY_REVIEWERS
    if kind != "review_asset":
        raise ValidationError("CONTENT-UPDATE-RECEIPT-OBLIGATION-KIND-INVALID")
    lowered = target.lower()
    if any(token in lowered for token in ("architecture", "demo", "casebook")):
        return ("visual_demo.compiler",), _VISUAL_REVIEWERS
    if "community" in lowered:
        return ("publisher.stager",), _COMMUNITY_REVIEWERS
    return ("content.compiler",), _PUBLIC_CLARITY_REVIEWERS


def _load_role_registry(root: Path, roles_path: Path | str) -> tuple[dict[str, Any], set[str]]:
    path, path_ref = _safe_relative_file(root, roles_path, error="CONTENT-UPDATE-RECEIPT-ROLE-REGISTRY-INVALID")
    roles = _load_roles(path)
    role_ids = {role.role_id for role in roles}
    required = {
        "content.compiler", "visual_demo.compiler", "channel.adapter", "publisher.stager",
        *_CHANNEL_REVIEWERS, *_PUBLIC_CLARITY_REVIEWERS, *_VISUAL_REVIEWERS, *_COMMUNITY_REVIEWERS,
    }
    if not required.issubset(role_ids):
        raise ValidationError("CONTENT-UPDATE-RECEIPT-ROLE-REGISTRY-INCOMPLETE")
    return {
        "source_path": path_ref,
        "content_hash": _hash_bytes(path),
        "assignment_policy_id": ASSIGNMENT_POLICY_ID,
    }, role_ids


def _plan_identity(plan: dict[str, Any], path_ref: str) -> dict[str, str]:
    return {
        "source_path": path_ref,
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "verification": "verified_content_impact_review_plan",
    }


def build_content_update_receipt(
    *, graph_root: Path | str, review_plan_path: Path | str, roles_path: Path | str,
) -> dict[str, Any]:
    """Build a deterministic non-dispatched route for every impact obligation."""

    root = _safe_root(graph_root)
    review_path, review_ref = _safe_relative_file(
        root, review_plan_path, error="CONTENT-UPDATE-RECEIPT-REVIEW-PLAN-INVALID"
    )
    plan = verify_content_impact_review_plan(review_path, graph_root=root)
    if not plan["review_obligations"]:
        raise ValidationError("CONTENT-UPDATE-RECEIPT-NO-REVIEW-OBLIGATIONS")
    role_registry, known_roles = _load_role_registry(root, roles_path)
    staged: list[dict[str, Any]] = []
    for obligation in plan["review_obligations"]:
        assigned, reviewers = _assignment(obligation)
        if not set(assigned).issubset(known_roles) or not set(reviewers).issubset(known_roles):
            raise ValidationError("CONTENT-UPDATE-RECEIPT-ROLE-ASSIGNMENT-UNKNOWN")
        staged.append({
            "work_item_id": _work_item_id(obligation, assigned, reviewers),
            "source_obligation_id": obligation["obligation_id"],
            "kind": obligation["kind"],
            "target_id": obligation["target_id"],
            "assignment_state": "planned_not_dispatched",
            "private_stage": "private_review_queue_only",
            "assigned_role_ids": list(assigned),
            "reviewer_role_ids": list(reviewers),
            "completion_condition": _COMPLETION_CONDITION,
        })
    staged.sort(key=lambda item: item["work_item_id"])
    unsigned = {
        "schema_version": "content-update-receipt/v1",
        "receipt_id": "",
        "authorization": "local_review_only",
        "source_review_plan": _plan_identity(plan, review_ref),
        "role_registry": role_registry,
        "staged_update_plan": staged,
        "effect_attestation": _EFFECT_ATTESTATION,
        "boundary": NO_EXTERNAL_EFFECT_BOUNDARY,
    }
    unsigned["receipt_id"] = _receipt_id(unsigned)
    receipt = {**unsigned, "receipt_hash": canonical_hash(unsigned)}
    validate_payload(receipt, SCHEMA, "content update receipt")
    return receipt


def create_content_update_receipt(
    *, graph_root: Path | str, review_plan_path: Path | str, roles_path: Path | str, output: Path | str,
) -> dict[str, Any]:
    """Seal a local receipt; no task, role, channel or source operation occurs."""

    receipt = build_content_update_receipt(
        graph_root=graph_root, review_plan_path=review_plan_path, roles_path=roles_path,
    )
    target = Path(output)
    if target.is_symlink():
        raise ValidationError("CONTENT-UPDATE-RECEIPT-PATH-UNSAFE")
    atomic_create_json(target, receipt)
    return receipt


def verify_content_update_receipt(
    receipt_path: Path | str, *, graph_root: Path | str,
) -> dict[str, Any]:
    """Fail closed if its input plan, role registry or derived route drifts."""

    root = _safe_root(graph_root)
    target, _ = _safe_relative_file(root, receipt_path, error="CONTENT-UPDATE-RECEIPT-PATH-UNSAFE")
    receipt = load_validated_json(target, SCHEMA, "content update receipt")
    verify_self_hash(receipt, "receipt_hash")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if receipt["receipt_id"] != _receipt_id(unsigned):
        raise ValidationError("CONTENT-UPDATE-RECEIPT-ID-MISMATCH")
    if receipt["authorization"] != "local_review_only" or receipt["boundary"] != NO_EXTERNAL_EFFECT_BOUNDARY:
        raise ValidationError("CONTENT-UPDATE-RECEIPT-BOUNDARY-INVALID")
    if receipt["effect_attestation"] != _EFFECT_ATTESTATION:
        raise ValidationError("CONTENT-UPDATE-RECEIPT-EFFECT-ATTESTATION-INVALID")
    rebuilt = build_content_update_receipt(
        graph_root=root,
        review_plan_path=receipt["source_review_plan"]["source_path"],
        roles_path=receipt["role_registry"]["source_path"],
    )
    if rebuilt != receipt:
        raise ValidationError("CONTENT-UPDATE-RECEIPT-RECOMPUTATION-MISMATCH")
    return receipt
