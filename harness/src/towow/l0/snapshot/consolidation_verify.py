"""M-0.7 §6.4 verify_consolidation_invariants — the three compaction invariant checker.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §6.4 verify_consolidation_invariants(envelope) -> VerifyResult — M-0.5 commit gate
#          (ConsolidationInvariantCheck, §10.3 Patch M) 调用入口; 三个 invariant 各自结果
#     §6.5 Invariant 1 reconstructability 抽样验证策略 (v3 初版 5% 抽样 + audit 兜底)
#     §6.6 verify 失败的拒绝路径 (rejection_type=consolidation_invariant_violated, §10.3 Patch N)
#     §4.2 / §6.4 inv2 — immutable_truth 不可被 digest (复用 consolidation_guard)
#     §6.3 / O-06 Inv3 — correctable consolidation: supersede 必指有效旧 event_id, 不原地覆盖
#
# DAEMON-INDEPENDENT (judgment layer, same范式 as GC reachability): verify_consolidation_invariants
# is M-0.7's verify-provider role (§11.7) — it does NOT run consolidation, it JUDGES whether a
# proposed consolidation envelope respects the three compaction invariants. The actual consolidation
# RUN (真摘要 + 写 cold) is driver-gated (debt-008857ede88c); this verifier is the gate-callable
# judgment that protects the invariants regardless of whether a driver exists yet. M-0.5
# ConsolidationInvariantCheck (Patch M) wires it into the commit pipeline.
#
# v3 INITIAL SCOPE (honest): inv1 here does the STRUCTURAL reconstructability check the daemon-indep
# layer can do — the snapshot the run names must exist, be retrievable, and be tamper-intact (the
# baseline replay anchor). The §6.5 5% full-replay sampling (replay snapshot.offset+1.. and assert
# state_hash match) needs the consolidation RUN's cutoff projection bundle (driver-gated) → that
# extra coverage is tracked as debt, not faked here. inv2 (immutable_truth + provenance_refs
# accessible) and inv3 (correctability) are fully checkable now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.snapshot.consolidation_guard import verify_consolidation_invariants
from towow.schemas.payloads.consolidation_envelope import (
    COMPACTION_INVARIANT_OBLIGATIONS,
)

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog
    from towow.schemas.enums import ObligationDeclaredStatus
    from towow.schemas.payloads.consolidation_envelope import ConsolidationEnvelope


@dataclass(frozen=True)
class InvariantResult:
    """Result of one of the three §6.4 compaction invariants."""

    invariant_id: str
    passed: bool
    detail: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsolidationVerifyResult:
    """§6.4 VerifyResult — overall pass + per-invariant results + evidence refs.

    ``passed`` is True iff all three invariants pass. On failure, M-0.5 rejects the envelope with
    rejection_type=consolidation_invariant_violated (§6.6 / §10.3 Patch N) and carries
    invariant_results as evidence.
    """

    passed: bool
    invariant_results: dict[str, InvariantResult] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


def _verify_reconstructability(
    envelope: ConsolidationEnvelope,
    event_log: EventLog,
) -> InvariantResult:
    """§6.4 Invariant 1 — current-state reconstructability (structural, daemon-indep slice).

    The snapshot the run names as its replay baseline must exist, be retrievable, and be
    tamper-intact (verify_record_integrity). Without a valid baseline snapshot there is nothing to
    derive current state from → reconstructability cannot hold. The §6.5 5% full-replay sampling
    (assert replayed state_hash == current projection hash) needs the RUN's cutoff projection bundle
    (driver-gated) and is tracked separately as debt — not asserted (and not faked-pass) here.
    """
    snap_id = envelope.snapshot_event_id
    if snap_id is None:
        # no baseline snapshot named → cannot establish reconstructability (fail-closed, not vacuous)
        return InvariantResult(
            invariant_id="reconstructability",
            passed=False,
            detail="envelope.snapshot_event_id is None — no replay baseline to reconstruct from",
        )
    snap = event_log.get_event(snap_id)
    if snap is None:
        return InvariantResult(
            invariant_id="reconstructability",
            passed=False,
            detail=f"snapshot baseline {snap_id} not found in event log",
            evidence=(snap_id,),
        )
    if not event_log.verify_record_integrity(snap):
        return InvariantResult(
            invariant_id="reconstructability",
            passed=False,
            detail=f"snapshot baseline {snap_id} failed integrity check (tampered)",
            evidence=(snap_id,),
        )
    return InvariantResult(
        invariant_id="reconstructability",
        passed=True,
        detail=(
            f"baseline snapshot {snap_id} exists + intact (structural check). "
            "§6.5 5% full-replay sampling is driver-gated (debt)."
        ),
        evidence=(snap_id,),
    )


def _verify_provenance(
    envelope: ConsolidationEnvelope,
    event_log: EventLog,
) -> InvariantResult:
    """§6.4 Invariant 2 — provenance-preserving abstraction.

    Two real checks (both daemon-indep):
      a. immutable_truth-not-digested (§4.2) — no immutable_truth event in the digested segment
         (reuses consolidation_guard.verify_consolidation_invariants).
      b. provenance_refs accessible — every event_id in consolidation_proposal.provenance_refs must
         still be retrievable from the event log (hot or cold); a digest whose source events vanished
         has broken provenance.
    """
    proposal = envelope.consolidation_proposal
    # a. immutable_truth not folded into the digest
    guard = verify_consolidation_invariants(
        event_log,
        start_seq=proposal.segment_start_seq,
        end_seq=proposal.segment_end_seq,
    )
    if not guard.ok:
        return InvariantResult(
            invariant_id="provenance",
            passed=False,
            detail=(
                "digest segment would fold immutable_truth originals (§4.2): "
                f"{guard.immutable_truth_in_segment}"
            ),
            evidence=tuple(guard.immutable_truth_in_segment),
        )
    # b. every provenance_ref still retrievable
    missing = [ref.event_id for ref in proposal.provenance_refs if event_log.get_event(ref.event_id) is None]
    if missing:
        return InvariantResult(
            invariant_id="provenance",
            passed=False,
            detail=f"digest provenance_refs point at events not in the log: {missing}",
            evidence=tuple(missing),
        )
    return InvariantResult(
        invariant_id="provenance",
        passed=True,
        detail=(
            f"no immutable_truth in digest segment + all {len(proposal.provenance_refs)} "
            "provenance_refs retrievable"
        ),
    )


def _verify_correctability(
    envelope: ConsolidationEnvelope,
    event_log: EventLog,
) -> InvariantResult:
    """§6.4 Invariant 3 — correctable consolidation.

    Any SnapshotSuperseded / DigestSuperseded the batch produces must point at a VALID existing
    event_id (no dangling supersede), and the original must still be retrievable (append-only — the
    superseded record is never overwritten in place, just superseded). A supersede naming a
    non-existent target is uncorrectable garbage.
    """
    dangling: list[str] = []
    for old_id in (*envelope.superseded_snapshot_event_ids, *envelope.superseded_digest_event_ids):
        if event_log.get_event(old_id) is None:
            dangling.append(old_id)
    if dangling:
        return InvariantResult(
            invariant_id="correctability",
            passed=False,
            detail=f"supersede targets do not resolve to existing events: {dangling}",
            evidence=tuple(dangling),
        )
    total = len(envelope.superseded_snapshot_event_ids) + len(envelope.superseded_digest_event_ids)
    return InvariantResult(
        invariant_id="correctability",
        passed=True,
        detail=f"all {total} supersede targets resolve to existing (append-only) events",
    )


def _verify_declared_invariants_complete(envelope: ConsolidationEnvelope) -> InvariantResult:
    """Structural precondition — the envelope must DECLARE all three compaction invariants.

    §6.3 requires active_obligations_declared cover the three compaction obligations. A run that
    omits one cannot be verified against it (the missing declaration is itself a violation — you
    cannot claim to maintain what you never declared). This is the anti-vacuous guard: an empty /
    partial declaration set does NOT pass by default.
    """
    declared = {d.obligation_id: d.status for d in envelope.active_obligations_declared}
    missing = [obl for obl in COMPACTION_INVARIANT_OBLIGATIONS if obl not in declared]
    if missing:
        return InvariantResult(
            invariant_id="declared_complete",
            passed=False,
            detail=f"envelope omits required compaction invariant declarations: {missing}",
            evidence=tuple(missing),
        )
    not_maintained = [
        obl for obl, status in declared.items()
        if obl in COMPACTION_INVARIANT_OBLIGATIONS and not _is_maintained(status)
    ]
    if not_maintained:
        return InvariantResult(
            invariant_id="declared_complete",
            passed=False,
            detail=f"compaction invariants declared but not maintained: {not_maintained}",
            evidence=tuple(not_maintained),
        )
    return InvariantResult(
        invariant_id="declared_complete",
        passed=True,
        detail="all three compaction invariants declared maintained",
    )


def _is_maintained(status: ObligationDeclaredStatus) -> bool:
    from towow.schemas.enums import ObligationDeclaredStatus as _S

    return status is _S.MAINTAINED


def verify_consolidation_invariants_full(
    envelope: ConsolidationEnvelope,
    event_log: EventLog,
) -> ConsolidationVerifyResult:
    """M-0.7 §6.4 — verify the three compaction invariants for a consolidation envelope.

    Returns a ConsolidationVerifyResult with per-invariant results. ``passed`` is True iff the
    declaration is complete AND all three invariants (reconstructability / provenance /
    correctability) pass. M-0.5 ConsolidationInvariantCheck (§10.3 Patch M) calls this; on a False
    result it rejects with rejection_type=consolidation_invariant_violated (§6.6 / Patch N).

    This is the judgment layer (verify provider, §11.7) — daemon-independent. It does not run a
    consolidation; it decides whether a proposed one is invariant-safe.
    """
    declared = _verify_declared_invariants_complete(envelope)
    inv1 = _verify_reconstructability(envelope, event_log)
    inv2 = _verify_provenance(envelope, event_log)
    inv3 = _verify_correctability(envelope, event_log)
    results = {
        "declared_complete": declared,
        "reconstructability": inv1,
        "provenance": inv2,
        "correctability": inv3,
    }
    evidence: list[str] = []
    for r in results.values():
        evidence.extend(r.evidence)
    return ConsolidationVerifyResult(
        passed=all(r.passed for r in results.values()),
        invariant_results=results,
        evidence_refs=tuple(evidence),
    )


__all__ = [
    "ConsolidationVerifyResult",
    "InvariantResult",
    "verify_consolidation_invariants_full",
]
