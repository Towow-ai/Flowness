"""M-1.2 §3.7 + §9.3.5 — engineering consensus invariant 形态校验 (T-L1-21).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §3.7 EngineeringConsensusFreezed.payload.invariants (L362..L366):
#         [{invariant_id, description, affected_concepts}] — 不可降级硬门禁 (TECH-015 §1.25 启示)
#     §9.3.5 invariant-extract fork 输出 candidate (L1265..L1268):
#         {statement, source, mechanically_verifiable, verification_method, scope, overlaps_existing}
#         scope ∈ {protocol_level (不可 supersede) | plan_level}
#
# Why this module (T-L1-21):
#   The freeze payload had no invariants field at all — the §3.7 硬门禁 (the不可降级 part that
#   downstream verification keys off) and the §9.3.5 fork output (mechanically_verifiable +
#   protocol_level/plan_level scope) had nowhere to land, so an extracted invariant could not
#   travel with the consensus it constrains. This module validates an invariant object before it
#   enters the freeze payload: invariant_id + description required, scope ∈ the 2 legal values
#   (anything else fail-closed). The freeze CLI rejects an invalid invariant rather than emitting
#   a malformed one.
"""

from __future__ import annotations

# §9.3.5 — scope is exactly protocol_level (不可 supersede) | plan_level.
INVARIANT_SCOPES: tuple[str, ...] = ("protocol_level", "plan_level")


def validate_invariant(obj: object) -> tuple[bool, list[str]]:
    """Validate a single freeze invariant object (§3.7 + §9.3.5). Returns (is_valid, errors).

    Required: invariant_id (non-empty str) + description (non-empty str).
    scope (if present) must be one of INVARIANT_SCOPES. affected_concepts (if present) must be a
    list. mechanically_verifiable (if present) must be a bool. Unknown extra keys are tolerated
    (forward-compat with richer §9.3.5 fork output), but the structural core is enforced.
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, ["invariant must be a JSON object"]
    inv_id = obj.get("invariant_id")
    if not isinstance(inv_id, str) or not inv_id:
        errors.append("invariant_id 必填 (非空 str)")
    desc = obj.get("description")
    if not isinstance(desc, str) or not desc:
        errors.append("description 必填 (非空 str)")
    scope = obj.get("scope")
    if scope is not None and scope not in INVARIANT_SCOPES:
        errors.append(
            f"scope {scope!r} 非法; 须 ∈ {INVARIANT_SCOPES} (§9.3.5 protocol_level/plan_level)",
        )
    affected = obj.get("affected_concepts")
    if affected is not None and not isinstance(affected, list):
        errors.append("affected_concepts 须为 list")
    mech = obj.get("mechanically_verifiable")
    if mech is not None and not isinstance(mech, bool):
        errors.append("mechanically_verifiable 须为 bool")
    return (not errors), errors


def normalize_invariant(obj: dict[str, object]) -> dict[str, object]:
    """Coerce a validated invariant into the freeze payload shape (§3.7 core + §9.3.5 fields).

    Defaults scope to plan_level (not protocol-level unless explicitly extracted as such — a
    protocol_level invariant is 不可 supersede, so it must be an explicit decision, never a默认).
    """
    affected = obj.get("affected_concepts")
    affected_list = list(affected) if isinstance(affected, list) else []
    return {
        "invariant_id": obj["invariant_id"],
        "description": obj["description"],
        "affected_concepts": affected_list,
        "mechanically_verifiable": bool(obj.get("mechanically_verifiable", False)),
        "verification_method": obj.get("verification_method", ""),
        "scope": obj.get("scope", "plan_level"),
    }


__all__ = [
    "INVARIANT_SCOPES",
    "normalize_invariant",
    "validate_invariant",
]
