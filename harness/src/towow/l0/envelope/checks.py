"""M-0.5 structural boundary checks over a TransactionEnvelope payload.

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#   §3.0 (L118..L133) EnvelopeIntegrity pre-check — schema_version / 必填字段非空 /
#     entity_type 合法 / patch_type 合法 / supersede 完整性. Any failure → reject,
#     no audit, no later checks.
#   §3.6 (L369..L398) ClaimsBoundaryCheck — set ops over read_set/write_set vs claims;
#     claims empty → informational notice + skip (R9: Planner optional, not blocking);
#     entity_id exact match.
#   §11.x Patch 4 (LEDGER L1227) — ClaimsAbsentWarning has no standalone event; it lives
#     in CommitAccepted.informational_notices.
#
# These are the "验边界" half of debt-payoff block 1: structural set-ops / field checks
# only. Semantic completeness (consumer scan / coverage / done_criteria / contract) is
# block 3 (F-026-1); scope-drift vs the capsule's touched_nodes is block 2 (capsule real).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from towow.schemas.enums import (
    EnvelopeReadSetEntityType,
    EnvelopeWriteSetEntityType,
    PatchType,
)

_KNOWN_SCHEMA_VERSIONS = frozenset({"1.0.0"})

# §3.0 必填字段 — 非空 string ids + non-empty read/write sets + present self_check / obligations.
_REQUIRED_STR_FIELDS = ("envelope_id", "task_id", "run_id", "fork_id")


@dataclass(frozen=True)
class IntegrityResult:
    """Outcome of EnvelopeIntegrity (M-0.5 §3.0)."""

    passed: bool
    failure_reason: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimsBoundaryResult:
    """Outcome of ClaimsBoundaryCheck (M-0.5 §3.6).

    claims_absent → skip the check but surface an informational notice (Patch 4: no
    standalone event). passed=False carries failure_reason + the exceeded entity refs.
    """

    passed: bool
    claims_absent: bool = False
    failure_reason: str | None = None
    exceeded: tuple[tuple[str, str], ...] = ()
    notice: str | None = None


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _refs(entries: object) -> set[tuple[str, str]]:
    """Collect (entity_type, entity_id) pairs from a read/write/claims list payload."""
    out: set[tuple[str, str]] = set()
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("entity_type")
        eid = entry.get("entity_id")
        if isinstance(etype, str) and isinstance(eid, str):
            out.add((etype, eid))
    return out


def check_envelope_integrity(payload: dict[str, object]) -> IntegrityResult:
    """M-0.5 §3.0 EnvelopeIntegrity pre-check over the envelope payload.

    Validates the structural floor every later check assumes: known schema_version,
    required ids present, read_set / write_set non-empty with legal entity_types,
    active_obligations_declared / self_check present, patch_type legal, and supersede
    completeness. Returns the first failure (short-circuit per §3.7).
    """
    schema_version = payload.get("schema_version")
    if not _is_nonempty_str(schema_version):
        return IntegrityResult(False, "envelope_malformed", {"field": "schema_version"})
    if schema_version not in _KNOWN_SCHEMA_VERSIONS:
        return IntegrityResult(
            False,
            "schema_version_unsupported",
            {"schema_version": str(schema_version)},
        )

    for fld in _REQUIRED_STR_FIELDS:
        if not _is_nonempty_str(payload.get(fld)):
            return IntegrityResult(False, "envelope_malformed", {"field": fld})

    if not _is_nonempty_str(payload.get("capsule_compiled_event_id")):
        return IntegrityResult(False, "envelope_malformed", {"field": "capsule_compiled_event_id"})

    read_set = payload.get("read_set")
    write_set = payload.get("write_set")
    if not isinstance(read_set, list) or len(read_set) == 0:
        return IntegrityResult(False, "envelope_malformed", {"field": "read_set"})
    if not isinstance(write_set, list) or len(write_set) == 0:
        return IntegrityResult(False, "envelope_malformed", {"field": "write_set"})

    if "active_obligations_declared" not in payload or not isinstance(
        payload.get("active_obligations_declared"), list
    ):
        return IntegrityResult(
            False, "envelope_malformed", {"field": "active_obligations_declared"}
        )
    if not isinstance(payload.get("self_check"), dict):
        return IntegrityResult(False, "envelope_malformed", {"field": "self_check"})

    read_types = {e.value for e in EnvelopeReadSetEntityType}
    for entry in read_set:
        etype = entry.get("entity_type") if isinstance(entry, dict) else None
        if etype not in read_types:
            return IntegrityResult(
                False, "envelope_malformed", {"read_set.entity_type": str(etype)}
            )
    write_types = {e.value for e in EnvelopeWriteSetEntityType}
    for entry in write_set:
        etype = entry.get("entity_type") if isinstance(entry, dict) else None
        if etype not in write_types:
            return IntegrityResult(
                False, "envelope_malformed", {"write_set.entity_type": str(etype)}
            )

    patch_types = {p.value for p in PatchType}
    patches = payload.get("patches", [])
    if isinstance(patches, list):
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            ptype = patch.get("patch_type")
            if ptype not in patch_types:
                return IntegrityResult(
                    False, "envelope_malformed", {"patch_type": str(ptype)}
                )
            if patch.get("is_supersede") and (
                not _is_nonempty_str(patch.get("superseded_entity_ref"))
                or not _is_nonempty_str(patch.get("novelty_type"))
            ):
                return IntegrityResult(
                    False,
                    "supersede_declaration_incomplete",
                    {"patch_id": str(patch.get("patch_id", ""))},
                )

    return IntegrityResult(True)


def check_claims_boundary(payload: dict[str, object]) -> ClaimsBoundaryResult:
    """M-0.5 §3.6 ClaimsBoundaryCheck over the envelope payload.

    claims empty → skip + informational notice (Patch 4). Otherwise entity_id-exact set
    ops: read_set ⊆ (read_claims ∪ write_claims); write_set ⊆ write_claims. Failure
    returns the exceeded refs.
    """
    claims = payload.get("claims")
    if not isinstance(claims, list) or len(claims) == 0:
        return ClaimsBoundaryResult(
            True,
            claims_absent=True,
            notice="claims_absent: Planner pre-declared no resource claims; boundary check skipped (M-0.5 §3.6 R9)",
        )

    read_claim_ids: set[tuple[str, str]] = set()
    write_claim_ids: set[tuple[str, str]] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        etype = claim.get("entity_type")
        eid = claim.get("entity_id")
        if not isinstance(etype, str) or not isinstance(eid, str):
            continue
        if claim.get("claim_type") == "read_claim":
            read_claim_ids.add((etype, eid))
        elif claim.get("claim_type") == "write_claim":
            write_claim_ids.add((etype, eid))

    read_set_ids = _refs(payload.get("read_set"))
    write_set_ids = _refs(payload.get("write_set"))

    read_exceeded = read_set_ids - (read_claim_ids | write_claim_ids)
    if read_exceeded:
        return ClaimsBoundaryResult(
            False,
            failure_reason="read_set_exceeds_claims",
            exceeded=tuple(sorted(read_exceeded)),
        )
    write_exceeded = write_set_ids - write_claim_ids
    if write_exceeded:
        return ClaimsBoundaryResult(
            False,
            failure_reason="write_set_exceeds_claims",
            exceeded=tuple(sorted(write_exceeded)),
        )
    return ClaimsBoundaryResult(True)


__all__ = [
    "ClaimsBoundaryResult",
    "IntegrityResult",
    "check_claims_boundary",
    "check_envelope_integrity",
]
