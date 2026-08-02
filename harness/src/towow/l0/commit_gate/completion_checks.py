"""M-0.5 done-claim gate — RUN-029 第0波 ③ "诚实做成强制".

# spec source: RUN-029 brief-1e96066a ③ + owner invariant decision (2026-05-29, tiered):
#   "无'语义完整性检查 present+passed'门即拒标 done；开放 blocking 级债的能力不许标 done。"
#
# The root failure RUN-029 fixes is 假done — a capability marked done over a stub. ① made
# the gate's semantic checks real; ② puts review on the path; this is the ③ enforcement that
# makes "done" mean done. A commit that CLAIMS a capability complete declares it in the
# envelope `completion_claims: [capability_id, ...]`. For each claimed capability the gate
# requires, fail-closed:
#   1. a passed semantic-completeness check present in self_check.blocking_checks
#      (check_id == completion.semantic_completeness_verified) — no done without a check that
#      actually ran and passed;
#   2. NO open blocking-severity debt registered against that capability (owner's tiered
#      invariant — normal/informational debt is visible-but-non-blocking, blocking hard-blocks).
#
# Commits with no completion_claims are unaffected (ordinary work-in-progress commits, and
# crucially the commits that PAY OFF debt, are never blocked by this).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.debt.registry import open_blocking_debt_ids_from_index

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex

# Checks a done-claim must carry as status=passed (+ evidence) before the gate accepts it.
# ③ completeness: the capability was verified semantically complete (not just structurally).
# ② review-on-path: the capability was adversarially reviewed before being called done — the
#   controlled-RUN enforcement of "评审接回必经路" (the orchestrator auto-dispatch form is the
#   remaining ② work, tracked as debt; this hard-blocks an unreviewed done-claim at the gate).
_COMPLETENESS_CHECK_ID = "completion.semantic_completeness_verified"
_REVIEW_CHECK_ID = "review.author_time_complete"
_REQUIRED_DONE_CHECK_IDS = (_COMPLETENESS_CHECK_ID, _REVIEW_CHECK_ID)


@dataclass(frozen=True)
class CompletionClaimResult:
    """Outcome of the done-claim gate (M-0.5 ③)."""

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)


def _passed_check_ids(self_check: object) -> set[str]:
    """check_ids whose blocking_check is status=passed AND carries non-empty evidence.

    The evidence requirement mirrors the BlockingCheck schema invariant (passed must have
    evidence). The gate reads self_check as a raw dict (not via the pydantic model), so a
    forged `{status: passed}` with no evidence would otherwise satisfy the done-claim gate
    without any auditable proof the check actually ran — the exact 假done pattern RUN-029
    roots out (review finding F5). An evidence-less passed check is treated as absent.
    """
    if not isinstance(self_check, dict):
        return set()
    blocking = self_check.get("blocking_checks")
    if not isinstance(blocking, list):
        return set()
    return {
        c["check_id"]
        for c in blocking
        if isinstance(c, dict)
        and isinstance(c.get("check_id"), str)
        and c.get("status") == "passed"
        and bool(c.get("evidence"))
    }


def check_completion_claims(index: EventIndex, payload: dict[str, object]) -> CompletionClaimResult:
    """Enforce the done-claim rules for every capability the envelope claims complete.

    Returns the first failure (short-circuit, §3.7). No completion_claims → pass (WIP commit).
    """
    claims = payload.get("completion_claims")
    if not isinstance(claims, list) or not claims:
        return CompletionClaimResult(passed=True)

    passed_checks = _passed_check_ids(payload.get("self_check"))

    for raw in claims:
        if not isinstance(raw, str) or not raw:
            continue
        # rule 1 — no done without each required check present+passed (+evidence):
        #   completeness (③) AND review (②). First missing → reject naming it.
        for required in _REQUIRED_DONE_CHECK_IDS:
            if required not in passed_checks:
                reason = (
                    "completion_without_completeness_check"
                    if required == _COMPLETENESS_CHECK_ID
                    else "completion_without_review_check"
                )
                return CompletionClaimResult(
                    passed=False,
                    failure_reason=reason,
                    failure_evidence={"capability": raw, "required_check_id": required},
                )
        # rule 2 — no done while open blocking debt stands against the capability.
        blocking = open_blocking_debt_ids_from_index(index, raw)
        if blocking:
            return CompletionClaimResult(
                passed=False,
                failure_reason="completion_blocked_by_open_debt",
                failure_evidence={
                    "capability": raw,
                    "open_blocking_debt_ids": ",".join(sorted(blocking)),
                },
            )
    return CompletionClaimResult(passed=True)


__all__ = ["CompletionClaimResult", "check_completion_claims"]
