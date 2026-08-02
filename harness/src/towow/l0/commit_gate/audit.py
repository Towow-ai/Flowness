"""M-0.5 §5 Audit Protocol — gate-side trigger detection (RUN-030 第0波 ① 剩余).

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#   §5.1 trigger conditions (4 cases)
#   §5.2 synchronous blocking flow / §5.3 AuditTriggered event schema
#   §3.7 — multiple audit-needing checks collapse into ONE AuditTriggered (avoid N forks)
#
# SCOPE — this module does the gate-side half ONLY: detect the four §5.1 conditions and
# (in gate.py) emit a留痕 AuditTriggered (path B). It does NOT run the LLM audit fork,
# collect verdicts, or block accept/reject on a verdict — that two-phase
# attempt/continue协议 + fork execution is the M-3.1 submit-wrapper boundary, already
# registered as debt (M-0.5:audit-fork, debt-4bdbedc51008, depends_on M-3.1 + wave1). So an
# AuditTriggered produced here is a SIGNAL留痕, NOT a completed audit: it does not change the
# commit verdict. (Wiring the verdict back to flip accept→reject is the debt-paying work.)
#
# What is REAL vs honest gap per trigger (§5.1):
#   1. self_check.passed == false       → REAL, deterministic (read payload.self_check.passed).
#   2. not_applicable on non-red_line   → REAL — severity is read from the obligation's
#      ObligationCaptured event (same source as ObligationCheck); sampled at the configured rate.
#   3. supersede novelty                → REAL — 5% sampling of any supersede domain intent;
#      24h oscillation (≥threshold supersedes on the same entity in window) fires at 100%.
#      Reads the supersede index + event timestamps; builds no new projection (per brief).
#   4. obligation maintained, no checker → REAL over available data, with a precision gap:
#      `checker_metadata.mechanically_checkable` is NOT event-sourced (it lives only on the
#      canonical Obligation object, never in any event payload). So "no resolvable checker"
#      is the test — absent proof of a mechanical checker, a maintained obligation is treated
#      as no-checker (conservative: it gets a model-audit sample rather than being assumed
#      mechanically checked). Today that is ALL maintained obligations; it becomes precise once
#      checker_metadata is event-sourced (wave1). Registered as debt
#      (M-0.5:audit-trigger-no-checker-precision).
#
# Sampling is config-driven (§3.4). RUN-053 (owner decision "开着才能测试"): production now defaults
# the probabilistic sampler ON — `CommitGate(log)` resolves `random_audit_sampler()` via
# `resolve_default_audit_sampler()` (env `TOWOW_AUDIT_SAMPLER=off` opts back to never-sample for
# CI/dev reproducibility). The fork that resolves an AuditTriggered now EXISTS (l1/audit_fork.py),
# wired into the submit driver, so a probabilistic trigger is no longer dangling留痕 — it drives a
# real audit fork + verdict回流 when the submit path runs audit_blocking. The 100% deterministic
# triggers (self_check failure / oscillation) are always on regardless of the sampler.
#
# The PURE function `detect_audit_triggers` keeps its `sample_decision=_never_sample` default
# (deterministic, unit-testable in isolation); the production ON-by-default lives one layer up in
# CommitGate so a bare `detect_audit_triggers(...)` call stays reproducible.
#
# Rates are §6 config-钉死 (M-0.5 §3.4.2 / §6 config.json audit_sampling_rate):
#   obligation_no_checker 0.01 (1%) · novelty_substantive 0.05 (5%) · red_line not_applicable 1.0
#   (the red-line 1.0 is a HARD REJECT in ObligationCheck, never an audit sample). The non-red_line
#   not_applicable audit reuses the generic §3.4.2 obligation-declaration sampling (1%).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from towow.l0.commit_gate.obligation_checks import _obligation_severity
from towow.schemas.enums import AuditTriggerReason

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex
    from towow.schemas.event_intent import EventIntent

# Sampler signature: given a rate in [0,1], decide whether this draw samples in.
SampleDecision = Callable[[float], bool]

# PURE-function default — deterministic OFF (see module docstring). `detect_audit_triggers`
# called directly (unit tests) stays reproducible; the production ON-by-default is resolved one
# layer up in CommitGate (resolve_default_audit_sampler). The 100% triggers are unaffected.
def _never_sample(_rate: float) -> bool:
    return False


def random_audit_sampler(rng: random.Random | None = None) -> SampleDecision:
    """A real probabilistic sampler `rate -> (random() < rate)` for production wiring.

    Pass an explicit `random.Random(seed)` for reproducible sampling in tests.
    """
    r = rng if rng is not None else random.Random()  # noqa: S311 — sampling, not crypto
    return lambda rate: r.random() < rate


# Env opt-out (RUN-053): production defaults the sampler ON; CI/dev sets this to "off" for
# reproducibility (a commit gate that behaves randomly run-to-run is itself an auditability
# problem). tests/conftest.py sets it suite-wide; a test wanting real sampling injects its own.
_ENV_AUDIT_SAMPLER = "TOWOW_AUDIT_SAMPLER"


def resolve_default_audit_sampler(rng: random.Random | None = None) -> SampleDecision:
    """Production default for `CommitGate(audit_sampler=None)` — ON unless explicitly opted out.

    `TOWOW_AUDIT_SAMPLER=off` → `_never_sample` (deterministic; CI/dev opt-out). Any other value
    (incl. unset) → `random_audit_sampler()` (owner decision RUN-053 — "开着才能测试"). This is the
    ONLY place the production default flips; the bare `detect_audit_triggers` default stays OFF.
    """
    import os

    if os.environ.get(_ENV_AUDIT_SAMPLER, "").strip().lower() == "off":
        return _never_sample
    return random_audit_sampler(rng)


@dataclass(frozen=True)
class AuditSamplingConfig:
    """§3.4 sampling rates + §5.1.3 oscillation window (all config-adjustable defaults).

    Defaults are §6 config-钉死 (M-0.5 §6 config.json audit_sampling_rate / §3.4.2):
      no_checker 0.01 (obligation_no_checker) · novelty 0.05 (novelty_substantive). The non-red_line
      not_applicable audit reuses the generic §3.4.2 obligation-declaration 1% sample, so
      not_applicable_rate mirrors no_checker at 0.01. (red_line not_applicable is a hard reject in
      ObligationCheck, never an audit sample — no rate here.)
    """

    no_checker_rate: float = 0.01
    not_applicable_rate: float = 0.01
    supersede_novelty_rate: float = 0.05
    oscillation_window_hours: int = 24
    # ≥ this many supersedes on the SAME entity inside the window → 100% audit (A→A'→A churn).
    # 2 = the current supersede + ≥1 prior in-window = rapid re-supersession.
    oscillation_supersede_threshold: int = 2


@dataclass(frozen=True)
class AuditTrigger:
    """One detected §5.1 condition. The gate batches all triggers into ONE AuditTriggered."""

    reason: AuditTriggerReason
    subject_type: str  # obligation_declaration | supersede_novelty | self_check_failure
    target_ref: str
    detail: str
    # The committed-event SubjectEntityType for this subject (so the gate can index the
    # AuditTriggered event by it). obligation → "obligation"; supersede → the entity's own
    # type; self_check → "task".
    subject_entity_type: str = "task"


# Headline-reason priority when collapsing N triggers into ONE AuditTriggered (schema carries
# a single trigger_reason). Most severe first.
_REASON_PRIORITY: tuple[AuditTriggerReason, ...] = (
    AuditTriggerReason.HIGH_RISK_PATCH,
    AuditTriggerReason.OBLIGATION_VIOLATION_SUSPECTED,
    AuditTriggerReason.SEMANTIC_CHECK_NEEDED,
    AuditTriggerReason.RANDOM_SAMPLE,
)


def headline_reason(triggers: tuple[AuditTrigger, ...]) -> AuditTriggerReason:
    """Pick the highest-priority reason among batched triggers for the single schema field."""
    by_reason = {t.reason for t in triggers}
    for reason in _REASON_PRIORITY:
        if reason in by_reason:
            return reason
    return AuditTriggerReason.RANDOM_SAMPLE


def _self_check_failed(payload: dict[str, object]) -> bool:
    self_check = payload.get("self_check")
    return isinstance(self_check, dict) and self_check.get("passed") is False


def _obligation_has_resolvable_checker(_index: EventIndex, _obligation_id: str) -> bool:
    """Whether a mechanical checker can be resolved for this obligation FROM THE LOG.

    `checker_metadata.mechanically_checkable` is not event-sourced (canonical-object-only),
    so this is always False today → every maintained obligation is treated as no-checker
    (conservative). Becomes precise once checker_metadata is event-sourced (wave1). Isolated
    here (signature ready for the _index/_obligation_id read) so the wave1 fix is one function.
    """
    return False


def _supersede_oscillates(
    index: EventIndex,
    entity_type: str,
    entity_id: str,
    *,
    config: AuditSamplingConfig,
    now: datetime,
) -> int:
    """Count prior in-window supersede events on the same entity (the A→A'→A churn signal).

    Returns the number of committed supersede events touching (entity_type, entity_id) whose
    timestamp is within `oscillation_window_hours` of `now`. The current (uncommitted) intent
    is NOT in the index, so caller adds 1 to compare against the threshold.
    """
    cutoff = now - timedelta(hours=config.oscillation_window_hours)
    count = 0
    for rec in index.lookup_entity(entity_type, entity_id):
        if not rec.supersede.is_supersede:
            continue
        if rec.timestamp < cutoff:
            continue
        count += 1
    return count


def detect_audit_triggers(
    index: EventIndex,
    payload: dict[str, object],
    domain_intents: list[EventIntent],
    *,
    config: AuditSamplingConfig | None = None,
    sample_decision: SampleDecision = _never_sample,
    now: datetime | None = None,
) -> tuple[AuditTrigger, ...]:
    """Detect the four §5.1 audit-trigger conditions over an envelope + its domain intents.

    Returns all detected triggers (the gate collapses them into ONE AuditTriggered, §3.7).
    Pure detection — no event writes, no verdict, no accept/reject effect.
    """
    cfg = config or AuditSamplingConfig()
    current_now = now or datetime.now(tz=UTC)
    triggers: list[AuditTrigger] = []

    # §5.1.4 — agent self_check.passed == false → always audit (deterministic, no sampling).
    if _self_check_failed(payload):
        envelope_ref = str(payload.get("envelope_id") or payload.get("envelope_event_id") or "envelope")
        triggers.append(
            AuditTrigger(
                reason=AuditTriggerReason.SEMANTIC_CHECK_NEEDED,
                subject_type="self_check_failure",
                target_ref=envelope_ref,
                detail="agent self_check.passed=false — audit must look even if other checks pass",
                subject_entity_type="task",
            )
        )

    # §5.1.1 / §5.1.2 — per declared obligation.
    declared = payload.get("active_obligations_declared")
    if isinstance(declared, list):
        for decl in declared:
            if not isinstance(decl, dict):
                continue
            oid = decl.get("obligation_id")
            status = decl.get("status")
            if not isinstance(oid, str) or not oid:
                continue
            if status == "not_applicable":
                # §5.1.2 — not_applicable on a non-red_line obligation, sampled. (red_line
                # not_applicable is already a hard reject in ObligationCheck — no audit needed.)
                severity = _obligation_severity(index, oid)
                if severity != "red_line" and sample_decision(cfg.not_applicable_rate):
                    triggers.append(
                        AuditTrigger(
                            reason=AuditTriggerReason.OBLIGATION_VIOLATION_SUSPECTED,
                            subject_type="obligation_declaration",
                            target_ref=oid,
                            detail=f"not_applicable on non-red_line obligation {oid} — sampled "
                            f"(rate={cfg.not_applicable_rate})",
                            subject_entity_type="obligation",
                        )
                    )
            elif status == "maintained" and not _obligation_has_resolvable_checker(index, oid):
                # §5.1.1 — maintained obligation with no resolvable mechanical checker, sampled.
                if sample_decision(cfg.no_checker_rate):
                    triggers.append(
                        AuditTrigger(
                            reason=AuditTriggerReason.SEMANTIC_CHECK_NEEDED,
                            subject_type="obligation_declaration",
                            target_ref=oid,
                            detail=f"maintained obligation {oid} has no resolvable checker — "
                            f"sampled (rate={cfg.no_checker_rate})",
                            subject_entity_type="obligation",
                        )
                    )

    # §5.1.3 — supersede novelty: 5% sample of any supersede; 24h oscillation at 100%.
    for intent in domain_intents:
        if not intent.supersede.is_supersede:
            continue
        primary = next(
            (s for s in intent.subjects if s.role.value == "primary"),
            intent.subjects[0] if intent.subjects else None,
        )
        if primary is None:
            continue
        etype = primary.entity_type.value
        eid = primary.entity_id
        prior = _supersede_oscillates(index, etype, eid, config=cfg, now=current_now)
        if prior + 1 >= cfg.oscillation_supersede_threshold:
            triggers.append(
                AuditTrigger(
                    reason=AuditTriggerReason.HIGH_RISK_PATCH,
                    subject_type="supersede_novelty",
                    target_ref=eid,
                    detail=f"oscillation: {prior + 1} supersedes on {etype}:{eid} within "
                    f"{cfg.oscillation_window_hours}h (A→A'→A churn) — 100% audit",
                    subject_entity_type=etype,
                )
            )
        elif sample_decision(cfg.supersede_novelty_rate):
            triggers.append(
                AuditTrigger(
                    reason=AuditTriggerReason.RANDOM_SAMPLE,
                    subject_type="supersede_novelty",
                    target_ref=eid,
                    detail=f"supersede on {etype}:{eid} — sampled (rate={cfg.supersede_novelty_rate})",
                    subject_entity_type=etype,
                )
            )

    return tuple(triggers)


def audit_scope_summary(triggers: tuple[AuditTrigger, ...]) -> str:
    """Serialize all batched subjects into the single AuditTriggered.audit_scope string.

    The §5.3 design carries a rich audit_subjects[] list; the canonical schema
    (AuditTriggeredAfter) carries one trigger_reason + one audit_scope string. We preserve
    every subject by serializing them here — no information is dropped in the collapse.
    """
    parts = [f"[{t.reason.value}] {t.subject_type}:{t.target_ref} — {t.detail}" for t in triggers]
    return " | ".join(parts)


__all__ = [
    "AuditSamplingConfig",
    "AuditTrigger",
    "SampleDecision",
    "audit_scope_summary",
    "detect_audit_triggers",
    "headline_reason",
    "random_audit_sampler",
    "resolve_default_audit_sampler",
]
