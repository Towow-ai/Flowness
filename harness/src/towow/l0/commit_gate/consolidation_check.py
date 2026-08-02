"""M-0.5 §3 ConsolidationInvariantCheck (M-0.7 §10.3 Patch M).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §10.3 Patch M → M-0.5 §3 检查 pipeline 加 ConsolidationInvariantCheck (在 ObligationCheck 后):
#       if envelope contains patches where patch_type == "consolidation_event":
#         result = M-0.7.verify_consolidation_invariants(envelope)
#         if not result.passed: reject(rejection_type=consolidation_invariant_violated, evidence=...)
#     §6.4 verify_consolidation_invariants — the three compaction invariant checker
#     §6.6 拒绝路径 — rejection_type=consolidation_invariant_violated (§10.3 Patch N)
#
# This is the commit-gate wiring of M-0.7's verify-provider role. The check is a NO-OP for
# non-consolidation envelopes (no patch_type==consolidation_event → skip, no false reject). When a
# consolidation_event patch is present, the envelope must carry a §6.3 ConsolidationEnvelope under
# payload["consolidation_envelope"]; the gate hands it to verify_consolidation_invariants_full and
# rejects on any compaction-invariant violation. Producing such an envelope is driver-gated (a real
# consolidation RUN, debt-008857ede88c) — but the gate check is wired now so that whenever such a
# run does submit, the three invariants are enforced at the commit boundary, not trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.snapshot.consolidation_verify import verify_consolidation_invariants_full
from towow.schemas.enums import PatchType
from towow.schemas.payloads.consolidation_envelope import ConsolidationEnvelope

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog


@dataclass(frozen=True)
class ConsolidationCheckResult:
    """Outcome of ConsolidationInvariantCheck. ``applicable`` is False for non-consolidation
    envelopes (the check is skipped, not failed). ``passed`` is only meaningful when applicable."""

    applicable: bool
    passed: bool = True
    failure_reason: str = ""
    failure_evidence: dict[str, object] = field(default_factory=dict)


def _has_consolidation_patch(payload: dict[str, object]) -> bool:
    patches = payload.get("patches", [])
    if not isinstance(patches, list):
        return False
    for patch in patches:
        if isinstance(patch, dict) and patch.get("patch_type") == PatchType.CONSOLIDATION_EVENT.value:
            return True
    return False


def check_consolidation_invariants(
    payload: dict[str, object],
    event_log: EventLog,
) -> ConsolidationCheckResult:
    """§10.3 Patch M — run verify_consolidation_invariants iff the envelope is a consolidation one.

    Skips (applicable=False) when no patch_type==consolidation_event patch is present — a plain
    domain commit is never touched. When applicable, the envelope MUST carry a §6.3
    ConsolidationEnvelope under payload["consolidation_envelope"]; a consolidation_event patch
    WITHOUT that structured envelope is itself a violation (you cannot verify what was not declared
    — fail-closed, not skip). Otherwise the §6.3 envelope is verified against the three compaction
    invariants (§6.4); any failure → reject (rejection_type=consolidation_invariant_violated).
    """
    if not _has_consolidation_patch(payload):
        return ConsolidationCheckResult(applicable=False)

    raw_env = payload.get("consolidation_envelope")
    if not isinstance(raw_env, dict):
        return ConsolidationCheckResult(
            applicable=True,
            passed=False,
            failure_reason="consolidation_event patch present but no consolidation_envelope payload",
            failure_evidence={"has_consolidation_envelope": raw_env is not None},
        )
    try:
        # The consolidation_envelope arrives as JSON-deserialized payload data (enum fields are
        # plain strings on the wire), so validate non-strict — strict=True would reject "primary"
        # for a ProvenanceRefRelevance field even though it is the canonical serialized form.
        envelope = ConsolidationEnvelope.model_validate(raw_env, strict=False)
    except (ValueError, TypeError) as exc:
        return ConsolidationCheckResult(
            applicable=True,
            passed=False,
            failure_reason="consolidation_envelope failed §6.3 schema validation",
            failure_evidence={"error": str(exc)},
        )

    result = verify_consolidation_invariants_full(envelope, event_log)
    if result.passed:
        return ConsolidationCheckResult(applicable=True, passed=True)

    failed = {
        inv_id: r.detail
        for inv_id, r in result.invariant_results.items()
        if not r.passed
    }
    return ConsolidationCheckResult(
        applicable=True,
        passed=False,
        failure_reason="consolidation_invariant_violated",
        failure_evidence={
            "failed_invariants": failed,
            "evidence_refs": list(result.evidence_refs),
        },
    )


__all__ = ["ConsolidationCheckResult", "check_consolidation_invariants"]
