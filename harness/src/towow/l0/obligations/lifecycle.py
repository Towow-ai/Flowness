"""M-0.6 obligation lifecycle state transitions — minimum viable (RUN-005 Bucket A / DOGFOOD-001-P).

# spec source:
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §3.2 lifecycle states
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md §3.10 obligation events
#
# Path: ObligationActivated/Checked/Violated events are NOT allowed on path B
# (direct write); per M-0.1 §5.1 they MUST go through commit gate path A wrapped
# in a TransactionEnvelope. This module builds the EventIntent + wraps in envelope
# + invokes commit gate.
#
# Scope (PARTIAL-RESOLVED for finding P):
#   - activate_obligation: emit ObligationActivated event through commit gate.
#   - check_obligation: emit ObligationChecked (commit gate post-action verdict).
#   - violate_obligation: emit ObligationViolated when check result = violated.
#
# Resolved (RUN-038 + RUN-050):
#   - retire_obligation (path A, protocol-invariant guard) — RUN-038.
#   - evolve_obligation (path A, supersede.is_supersede=true + Cap3 novelty strong-validation,
#     old obligation → superseded via reducer) — RUN-050. CLI obligation evolve/retire de-stubbed.
#
# Out of scope (deferred):
#   - LLM resolver-driven activation (capsule pipeline stage 4)
"""

from __future__ import annotations

import uuid
from pathlib import Path

from towow.l0.commit_gate import CommitGate
from towow.l0.event_log import EventLog
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    CheckerPatternType,
    EventCategory,
    EventType,
    ObligationActivationSource,
    ObligationActiveSetRole,
    ObligationCanonicalState,
    ObligationCaptureSource,
    ObligationCheckMethod,
    ObligationCheckOutcome,
    ObligationDeclaredStatus,
    ObligationFieldsLifecycleState,
    ObligationMaterializedInto,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    ObligationViolatedDetectedBy,
    ObligationViolatedRecommendedAction,
    PatchNoveltyType,
    PatchType,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.payloads.obligation import (
    ObligationEvolvedBeforeAfter,
    ObligationEvolvedNovelty,
    ObligationEvolvedPayload,
)

# PatchNoveltyType (payload-level, 5 values) → SupersedeNoveltyType (event-level, 4 values).
# The two domains do not 1:1 align (SupersedeNoveltyType has corrected_error, not new_constraint/
# new_resolution); this is the explicit bridge for the ObligationEvolved supersede block (M-0.4 §2
# "no silent automatic mapping" — the mapping is declared here, not improvised per call).
_PATCH_TO_SUPERSEDE_NOVELTY: dict[PatchNoveltyType, SupersedeNoveltyType] = {
    PatchNoveltyType.NEW_CONSTRAINT: SupersedeNoveltyType.SCOPE_CHANGE,
    PatchNoveltyType.NEW_EVIDENCE: SupersedeNoveltyType.NEW_EVIDENCE,
    PatchNoveltyType.NEW_RESOLUTION: SupersedeNoveltyType.CORRECTED_ERROR,
    PatchNoveltyType.SCOPE_CHANGE: SupersedeNoveltyType.SCOPE_CHANGE,
    PatchNoveltyType.NATURE_OVERRIDE: SupersedeNoveltyType.NATURE_OVERRIDE,
}

# ─── Canonical lifecycle state machine (M-0.6 §3.1 / §3.2) ───────────────────
#
# Three canonical states: active / superseded / retired. superseded and retired are
# terminal. Only `active` is a legal source of a canonical transition:
#     active → superseded   (ObligationEvolved on the old obligation)
#     active → retired      (ObligationRetired)
# §3.2 explicitly forbids reviving / cross-transitioning the terminal states:
#     superseded → active, retired → active, superseded → retired, retired → superseded.
# A no-op (state → same state) is not a transition and is therefore allowed.
_LEGAL_CANONICAL_TRANSITIONS: frozenset[tuple[ObligationCanonicalState, ObligationCanonicalState]] = (
    frozenset(
        {
            (ObligationCanonicalState.ACTIVE, ObligationCanonicalState.SUPERSEDED),
            (ObligationCanonicalState.ACTIVE, ObligationCanonicalState.RETIRED),
        },
    )
)


def is_legal_canonical_transition(
    current: ObligationCanonicalState,
    target: ObligationCanonicalState,
) -> bool:
    """True iff `current → target` is a legal canonical lifecycle transition (M-0.6 §3.2).

    Self-stays (current == target) are no-ops, not transitions → legal. Every cross-state
    move out of a terminal state (superseded / retired) is illegal.
    """
    if current == target:
        return True
    return (current, target) in _LEGAL_CANONICAL_TRANSITIONS


class IllegalObligationTransitionError(Exception):
    """Raised when a caller attempts an illegal canonical lifecycle transition (M-0.6 §3.2)."""

    def __init__(
        self,
        current: ObligationCanonicalState,
        target: ObligationCanonicalState,
    ) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"illegal obligation canonical transition {current.value} → {target.value} "
            f"(M-0.6 §3.2: superseded / retired are terminal — capture a new obligation instead)",
        )


def _emit_lifecycle_event(
    towow_dir: Path,
    domain_intent: EventIntent,
    primary_subject: Subject,
    actor_id: str,
    session_id: str,
) -> str:
    """Wrap domain intent in envelope + commit through gate (path A). Returns domain event_id."""
    event_log = EventLog(towow_dir / "events.log")
    gate = CommitGate(event_log)
    env_id = f"env-oblig-{uuid.uuid4().hex[:12]}"
    envelope_intent = EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": session_id,
            "capsule_compiled_event_id": "evt-stub-capsule-oblig-lifecycle",
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
            session_id=session_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[primary_subject],
        schema_version="1.0.0",
    )
    records = gate.attempt_commit(envelope_intent, [domain_intent])
    sentinel = records[-1]
    if sentinel.event_type is not EventType.COMMIT_ACCEPTED:
        raise RuntimeError(
            f"obligation lifecycle commit rejected: sentinel={sentinel.event_id} "
            f"type={sentinel.event_type.value}",
        )
    return records[1].event_id  # domain event (envelope=0, domain=1, commit=last)


def _rejection_diagnostic(payload: dict) -> str:
    """Extract human-readable rejection text from a CommitRejected sentinel payload.

    The commit gate emits the LANDED plural array `rejection_reasons[]` (gate.py
    _build_commit_rejected_intent), each item being {check_type, failure_reason, evidence}
    (M-0.5 §6.5 / RejectionReasonItem). There is NO singular `rejection_reason` field —
    reading it always yielded None ('reason=None'), losing the real cause (L0-conformance#5,
    T-FIX-B6-06). Join the per-item failure_reason texts so the diagnostic carries the
    real cause. Falls back to failure_reason / raw payload only when the array is absent.
    """
    reasons = payload.get("rejection_reasons")
    if isinstance(reasons, list) and reasons:
        parts: list[str] = []
        for item in reasons:
            if isinstance(item, dict):
                text = item.get("failure_reason") or item.get("check_type")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        if parts:
            return "; ".join(parts)
    # legacy / non-gate sentinels: best-effort fallback (never the always-None singular read)
    fallback = payload.get("failure_reason") or payload.get("rejection_type")
    return str(fallback) if fallback else "(no rejection_reasons in sentinel payload)"


def _submit_obligation_event_through_gate(
    towow_dir: Path,
    domain_intent: EventIntent,
    obligation_id: str,
    patch_summary: str,
    actor_id: str,
    session_id: str,
) -> str:
    """M-0.6 §7.1 / §7.3 — submit an obligation lifecycle domain event through the commit gate
    (path A) with an `obligation_event` patch in the envelope (NOT a bypass / direct write).

    The envelope carries exactly one patch with patch_type=obligation_event, target=obligation_id
    (§7.1 step 2 / §7.3 step 2). The gate runs the normal check pipeline (EnvelopeIntegrity sees a
    legal obligation_event patch_type; boundaries are gate-derived from the domain intent + the
    obligation subject) and writes the domain event into the accepted batch. Returns the domain
    event_id. Raises RuntimeError if the gate rejects (e.g. a protocol-invariant retire raises
    ProtocolInvariantImmutabilityError before any write — see attempt_commit).
    """
    event_log = EventLog(towow_dir / "events.log")
    gate = CommitGate(event_log)
    env_id = f"env-oblig-{uuid.uuid4().hex[:12]}"
    envelope_intent = EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": session_id,
            "capsule_compiled_event_id": "evt-stub-capsule-oblig-lifecycle",
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": [],
            "patches": [
                {
                    "patch_id": f"p-{domain_intent.local_intent_id}",
                    "patch_type": PatchType.OBLIGATION_EVENT.value,
                    "target": obligation_id,
                    "is_supersede": False,
                    "summary": patch_summary,
                },
            ],
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
            session_id=session_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.OBLIGATION,
                entity_id=obligation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    records = gate.attempt_commit(envelope_intent, [domain_intent])
    sentinel = records[-1]
    if sentinel.event_type is not EventType.COMMIT_ACCEPTED:
        raise RuntimeError(
            f"obligation_event commit rejected: sentinel={sentinel.event_id} "
            f"type={sentinel.event_type.value} reason={_rejection_diagnostic(sentinel.payload)}",
        )
    return records[1].event_id  # domain event (envelope path B, domain=1 in batch, commit=last)


def capture_obligation(
    towow_dir: Path,
    obligation_id: str,
    nature: ObligationNature,
    severity: ObligationSeverity,
    scope_type: ObligationScopeType,
    scope_rule: str,
    definition: str,
    attached_to: list[dict[str, str]],
    captured_by_actor_id: str,
    source_event_refs: list[str],
    capture_source: ObligationCaptureSource = ObligationCaptureSource.ENGINEERING_CONSENSUS,
    *,
    mechanically_checkable: bool = False,
    forbidden_pattern: str | None = None,
    pattern_type: CheckerPatternType | None = None,
    checker_scope_hint: str | None = None,
) -> str:
    """M-0.6 §7.1 / §4.1.1 — runtime capture of a NEW obligation through the commit gate (path A).

    This is the runtime capture path an L1 skill uses when it identifies "need a new obligation"
    (e.g. Nature says "以后任务都要避免 X"). Unlike `bootstrap_protocol_invariants` (the genesis
    special path via gate.bootstrap_commit), a runtime capture goes through the NORMAL
    attempt_commit pipeline — an envelope carrying an `obligation_event` patch + a co-submitted
    ObligationCaptured domain event (§7.1 steps 2-5). The capture is NOT a bypass / direct write.

    capture_source is the caller-declared runtime source (one of the 5 non-bootstrap §2.1 sources;
    default engineering_consensus). It is NEVER system_bootstrap — only the genesis bootstrap path
    mints protocol invariants — so a runtime-captured obligation stays retireable (§5.3: only
    system_bootstrap protocol invariants are un-retireable).

    RUN-068 件A (M-0.6 §11 不变量5 / §4.1.1): a runtime capture MUST carry provenance. `source_event_refs`
    traces the upstream events that triggered the obligation (e.g. the NatureJudgmentCaptured /
    ReviewFinding the obligation was upgraded from). The emitted ObligationCaptured carries a
    `capture_provenance` block {capture_source, source_event_refs, captured_by_actor_id} +
    `obligation_definition` nested block (§4.1.1). An EMPTY source_event_refs is rejected at the
    commit gate (check_obligation_capture_provenance) — provenance is真强制, not摆设. The genesis
    bootstrap path (system_bootstrap) is the only legitimate empty-source_event_refs capture (§5.2),
    and it goes through bootstrap_commit, not this runtime path.

    RUN-068 件C: an obligation may declare a mechanically_checkable forbidden_pattern (+ pattern_type)
    so the M-0.5 §3.4.2 maintenance check has a physical pattern to match against (no longer vacuous).
    Returns the ObligationCaptured event_id; raises RuntimeError if the gate rejects (e.g. empty
    source_event_refs → obligation_provenance_missing).
    """
    if capture_source is ObligationCaptureSource.SYSTEM_BOOTSTRAP:
        msg = (
            "capture_obligation cannot mint a system_bootstrap obligation — protocol invariants are "
            "only created by the genesis bootstrap path (M-0.6 §5.1). Use a runtime capture_source."
        )
        raise ValueError(msg)
    session_id = f"sess-oblig-capture-{uuid.uuid4().hex[:8]}"
    # §4.1.1 capture_provenance block — the authoritative provenance record (不变量5). The flat
    # capture_source is kept for the existing consumers (gate._obligation_capture_source /
    # obligation_checks._obligation_severity read flat fields).
    capture_provenance = {
        "capture_source": capture_source.value,
        "source_event_refs": list(source_event_refs),
        "captured_by_actor_id": captured_by_actor_id,
    }
    # §2.1 checker_metadata — mechanically_checkable obligations carry forbidden_pattern + pattern_type
    # so the §3.4.2 physical matcher (RUN-068 件C) has data to match. Non-checkable → scope_hint only.
    checker_metadata = {
        "mechanically_checkable": mechanically_checkable,
        "forbidden_pattern": forbidden_pattern,
        "pattern_type": pattern_type.value if pattern_type is not None else None,
        "checker_scope_hint": checker_scope_hint,
    }
    # §4.1.1 obligation_definition nested block — the full definition as one structured object.
    obligation_definition = {
        "nature": nature.value,
        "severity": severity.value,
        "scope_type": scope_type.value,
        "scope_rule": scope_rule,
        "definition": definition,
        "attached_to": attached_to,
        "checker_metadata": checker_metadata,
    }
    intent = EventIntent(
        local_intent_id=f"oblig-capture-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_CAPTURED,
        event_category=EventCategory.OBLIGATION,
        payload={
            "obligation_id": obligation_id,
            "obligation_lifecycle_state": ObligationFieldsLifecycleState.CAPTURED.value,
            "attached_to": attached_to,
            "scope_rule": scope_rule,
            "scope_type": scope_type.value,
            "severity": severity.value,
            "nature": nature.value,
            "definition": definition,
            # flat capture_source (backward-compat for existing flat-field consumers)
            "capture_source": capture_source.value,
            # RUN-068 §4.1.1 structured blocks
            "capture_provenance": capture_provenance,
            "obligation_definition": obligation_definition,
            # flat checker fields (event-sourced forbidden_pattern for §3.4.2 matcher — 件C)
            "mechanically_checkable": mechanically_checkable,
            "forbidden_pattern": forbidden_pattern,
            "checker_scope_hint": checker_scope_hint,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=captured_by_actor_id,
            session_id=session_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.OBLIGATION,
                entity_id=obligation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return _submit_obligation_event_through_gate(
        towow_dir,
        intent,
        obligation_id=obligation_id,
        patch_summary=f"Capture new obligation: {obligation_id}",
        actor_id=captured_by_actor_id,
        session_id=session_id,
    )


def retire_obligation(
    towow_dir: Path,
    obligation_id: str,
    retirement_reason: str,
    retired_by_actor_id: str,
) -> str:
    """M-0.6 §7.3 — retire an obligation through the commit gate (path A).

    An L1 skill / Nature decides to retire an obligation. The retire goes through the NORMAL
    attempt_commit pipeline — an envelope carrying an `obligation_event` patch (is_supersede=false,
    retire has no successor) + a co-submitted ObligationRetired domain event carrying
    obligation_id + retirement_reason (§7.3 steps 2-5). The gate runs its checks (no NoveltyCheck —
    retire is not a supersede) and writes ObligationRetired into the accepted batch; the M-0.2
    reducer then sets canonical_state=retired (§7.3 step 6).

    Protocol invariant guard (§5.3 / §11.6 / Patch J): if the target's capture_source is
    system_bootstrap, the gate raises ProtocolInvariantImmutabilityError BEFORE any write — the
    obligation is un-retireable and survives. Returns the ObligationRetired event_id on success.
    """
    if not retirement_reason.strip():
        msg = "retire_obligation requires a non-empty retirement_reason (M-0.6 §7.3 step 3)."
        raise ValueError(msg)
    session_id = f"sess-oblig-retire-{uuid.uuid4().hex[:8]}"
    intent = EventIntent(
        local_intent_id=f"oblig-retire-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_RETIRED,
        event_category=EventCategory.OBLIGATION,
        payload={
            "obligation_id": obligation_id,
            "obligation_lifecycle_state": ObligationFieldsLifecycleState.RETIRED.value,
            "retirement_reason": retirement_reason,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=retired_by_actor_id,
            session_id=session_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),  # retire is NOT a supersede (no successor)
        subjects=[
            Subject(
                entity_type=SubjectEntityType.OBLIGATION,
                entity_id=obligation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return _submit_obligation_event_through_gate(
        towow_dir,
        intent,
        obligation_id=obligation_id,
        patch_summary=f"Retire obligation: {obligation_id}",
        actor_id=retired_by_actor_id,
        session_id=session_id,
    )


def _current_obligation_definition(
    towow_dir: Path,
    obligation_id: str,
) -> tuple[str, ObligationEvolvedBeforeAfter]:
    """Read the obligation's current canonical-defining event + its (scope_rule, severity, definition).

    The "current definition" is the highest-sequence ObligationCaptured (genesis) OR ObligationEvolved
    (a prior evolve's after_state becomes the new before_state) for this obligation_id. The returned
    event_id is what the new ObligationEvolved supersedes (M-0.6 §7.2 supersede chain). Raises
    ValueError if the obligation was never captured (no before_state to evolve from).
    """
    event_log = EventLog(towow_dir / "events.log")
    best_seq = -1
    best: tuple[str, ObligationEvolvedBeforeAfter] | None = None
    for rec in event_log.get_events_by_type(EventType.OBLIGATION_CAPTURED):
        if rec.payload.get("obligation_id") != obligation_id or rec.sequence_number <= best_seq:
            continue
        best_seq = rec.sequence_number
        best = (
            rec.event_id,
            ObligationEvolvedBeforeAfter(
                scope_rule=str(rec.payload.get("scope_rule", "")),
                severity=ObligationSeverity(rec.payload.get("severity", ObligationSeverity.NORMAL.value)),
                definition=str(rec.payload.get("definition", "")),
            ),
        )
    for rec in event_log.get_events_by_type(EventType.OBLIGATION_EVOLVED):
        after = rec.payload.get("after_state")
        if rec.payload.get("obligation_id") != obligation_id or rec.sequence_number <= best_seq:
            continue
        if not isinstance(after, dict):
            continue
        best_seq = rec.sequence_number
        best = (
            rec.event_id,
            ObligationEvolvedBeforeAfter(
                scope_rule=str(after.get("scope_rule", "")),
                severity=ObligationSeverity(after.get("severity", ObligationSeverity.NORMAL.value)),
                definition=str(after.get("definition", "")),
            ),
        )
    if best is None:
        msg = (
            f"evolve_obligation: no ObligationCaptured found for '{obligation_id}' — "
            "cannot evolve an obligation that was never captured (no before_state)."
        )
        raise ValueError(msg)
    return best


def evolve_obligation(
    towow_dir: Path,
    obligation_id: str,
    novelty: ObligationEvolvedNovelty,
    evolved_by_actor_id: str,
    new_definition: str | None = None,
    new_scope_rule: str | None = None,
    new_severity: ObligationSeverity | None = None,
    supersede_reason: str | None = None,
) -> str:
    """M-0.6 §4.1.2 / §7.2 — evolve an obligation (active → superseded) through the commit gate (path A).

    An L1 skill / Nature sharpens an obligation (new constraint / evidence / resolution / scope /
    nature). The evolve goes through the NORMAL attempt_commit pipeline — an envelope carrying an
    `obligation_event` patch + a co-submitted ObligationEvolved domain event with
    supersede.is_supersede=true (M-0.1 §3.10 / O-12). The gate runs NoveltyCheck (a supersede needs
    substantive novelty); the M-0.2 reducer then sets the OLD obligation's canonical_state=superseded
    (§3.2 active → superseded). Returns the ObligationEvolved event_id.

    before_state is read automatically from the obligation's current canonical-defining event (the
    caller does not re-state it); after_state applies the caller's overrides (new_definition /
    new_scope_rule / new_severity), defaulting to before's values when omitted. `novelty` is the
    validated ObligationEvolvedNovelty block (Cap3 strong validation: no_new_information=false +
    at least one substantive new_* field — a vacuous evolve is structurally unconstructible, so a
    fake evolve cannot reach this function). Raises ValueError if the obligation was never captured.
    """
    superseded_event_id, before_state = _current_obligation_definition(towow_dir, obligation_id)
    after_state = ObligationEvolvedBeforeAfter(
        scope_rule=new_scope_rule if new_scope_rule is not None else before_state.scope_rule,
        severity=new_severity if new_severity is not None else before_state.severity,
        definition=new_definition if new_definition is not None else before_state.definition,
    )
    # Construct the typed payload so the Cap3 novelty validators run NOW (build-time) — the event
    # log does not re-validate payloads, so building the pydantic model is what enforces "no vacuous
    # evolve". model_dump(mode="json") emits enum values as strings for the EventIntent payload dict.
    evolved_payload = ObligationEvolvedPayload(
        obligation_id=obligation_id,
        before_state=before_state,
        after_state=after_state,
        novelty=novelty,
    )
    supersede_novelty = _PATCH_TO_SUPERSEDE_NOVELTY[novelty.novelty_type]
    # event-level supersede.novelty string (consumed by gate NoveltyCheck §3.5.1 — must be non-empty):
    # caller reason, else the substantive text from the novelty block.
    reason = (supersede_reason or "").strip() or _novelty_text(novelty)
    session_id = f"sess-oblig-evolve-{uuid.uuid4().hex[:8]}"
    intent = EventIntent(
        local_intent_id=f"oblig-evolve-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_EVOLVED,
        event_category=EventCategory.OBLIGATION,
        payload=evolved_payload.model_dump(mode="json"),
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=evolved_by_actor_id,
            session_id=session_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(
            is_supersede=True,  # ObligationEvolved IS a supersede (M-0.1 §3.10 / O-12) — has a successor
            superseded_event_id=superseded_event_id,
            novelty=reason,
            novelty_type=supersede_novelty,
        ),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.OBLIGATION,
                entity_id=obligation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return _submit_obligation_event_through_gate(
        towow_dir,
        intent,
        obligation_id=obligation_id,
        patch_summary=f"Evolve obligation: {obligation_id}",
        actor_id=evolved_by_actor_id,
        session_id=session_id,
    )


def _novelty_text(novelty: ObligationEvolvedNovelty) -> str:
    """A non-empty human reason from a validated novelty block (for the supersede.novelty string)."""
    for val in (novelty.new_constraint, novelty.new_evidence, novelty.new_resolution):
        if (val or "").strip():
            return str(val).strip()
    if novelty.scope_change is not None:
        return f"scope_change: {novelty.scope_change}"
    if novelty.nature_override is not None:
        return f"nature_override: {novelty.nature_override}"
    # Unreachable: ObligationEvolvedNovelty's validator guarantees at least one of the above.
    return f"evolve novelty ({novelty.novelty_type.value})"


def activate_obligation(
    towow_dir: Path,
    obligation_id: str,
    activated_for_task_id: str,
    activation_source: ObligationActivationSource = ObligationActivationSource.FALLBACK_CONSERVATIVE,
    resolver_decision_event_id: str | None = None,
    *,
    activation_id: str | None = None,
    activated_for_run_id: str | None = None,
    capsule_compiled_event_id: str | None = None,
    scope_judgment_event_id: str | None = None,
    active_set_role: ObligationActiveSetRole = ObligationActiveSetRole.NORMAL,
    materialized_into: ObligationMaterializedInto = ObligationMaterializedInto.CAPSULE,
) -> str:
    """Emit ObligationActivated via path B (direct write). Returns the event_id.

    Cap5 (RUN-038, M-0.6 §8.2): ObligationActivated is an activation *edge* (scoped fact),
    not a commit-gated state transition — it MUST go path B (direct write), the same path the
    M-0.3 capsule-assembly pipeline uses after CapsuleCompiled. Previously this helper wrongly
    routed it through the commit gate (path A), contradicting the §8.2 responsibility table.
    base_classification is abstractable_process (§4.2.2 — scoped fact, digestable), not
    immutable_truth.

    RUN-068 件B (M-0.6 §4.2.2): the activation edge now carries activation_id (auto-minted
    "act-{uuid}" when omitted) + capsule_compiled_event_id (the capsule that materialized it) +
    scope_judgment_event_id (the ObligationScopeJudged it followed) + active_set_role (mirrors
    severity → placement) + materialized_into (capsule | envelope_requirement). All additive.
    """
    subject = Subject(
        entity_type=SubjectEntityType.OBLIGATION,
        entity_id=obligation_id,
        role=SubjectRole.PRIMARY,
    )
    session_id = f"sess-oblig-activate-{uuid.uuid4().hex[:8]}"
    act_id = activation_id or f"act-{uuid.uuid4().hex[:12]}"
    intent = EventIntent(
        local_intent_id=f"oblig-activate-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_ACTIVATED,
        event_category=EventCategory.OBLIGATION,
        payload={
            "obligation_id": obligation_id,
            "obligation_lifecycle_state": "activated",
            "activated_for_task_id": activated_for_task_id,
            "activation_source": activation_source.value,
            "resolver_decision_event_id": resolver_decision_event_id,
            # RUN-068 §4.2.2 activation-edge fields
            "activation_id": act_id,
            "activated_for_run_id": activated_for_run_id,
            "capsule_compiled_event_id": capsule_compiled_event_id,
            "scope_judgment_event_id": scope_judgment_event_id,
            "active_set_role": active_set_role.value,
            "materialized_into": materialized_into.value,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.CAPSULE_ASSEMBLER.value,
            actor_id="m06-obligation-lifecycle",
            session_id=session_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[subject],
        schema_version="1.0.0",
    )
    event_log = EventLog(towow_dir / "events.log")
    return event_log.write_direct(intent).event_id


def check_obligation(
    towow_dir: Path,
    obligation_id: str,
    checked_in_envelope_event_id: str,
    check_result: ObligationDeclaredStatus,
) -> str:
    """Emit ObligationChecked event through commit gate. Returns domain event_id.

    Cap2 (RUN-038, M-0.6 §4.2.3): ObligationChecked carries only the two passing outcomes
    (maintained / not_applicable). A `violated` declared status is not a check outcome — it
    must go through violate_obligation (ObligationViolated, §4.2.4); passing it here is an
    error rather than a silently-mismapped event.
    """
    if check_result is ObligationDeclaredStatus.VIOLATED:
        msg = (
            "check_obligation cannot record a violated status — ObligationChecked only carries "
            "maintained / not_applicable (M-0.6 §4.2.3). Use violate_obligation for violations."
        )
        raise ValueError(msg)
    check_outcome = (
        ObligationCheckOutcome.MAINTAINED
        if check_result is ObligationDeclaredStatus.MAINTAINED
        else ObligationCheckOutcome.NOT_APPLICABLE
    )
    subject = Subject(
        entity_type=SubjectEntityType.OBLIGATION,
        entity_id=obligation_id,
        role=SubjectRole.PRIMARY,
    )
    session_id = f"sess-oblig-check-{uuid.uuid4().hex[:8]}"
    intent = EventIntent(
        local_intent_id=f"oblig-check-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_CHECKED,
        event_category=EventCategory.OBLIGATION,
        payload={
            "obligation_id": obligation_id,
            "obligation_lifecycle_state": "checked",
            "checked_in_envelope_event_id": checked_in_envelope_event_id,
            "check_method": ObligationCheckMethod.SELF_DECLARED_ONLY.value,
            "check_outcome": check_outcome.value,
            "check_result": check_result.value,  # backward-compat
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.COMMIT_GATE.value,
            actor_id="m05-commit-gate-check",
            session_id=session_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[subject],
        schema_version="1.0.0",
    )
    return _emit_lifecycle_event(
        towow_dir, intent, subject, "m05-commit-gate-check", session_id,
    )


def violate_obligation(
    towow_dir: Path,
    obligation_id: str,
    violated_in_envelope_event_id: str,
    violation_description: str,
    recommended_action: ObligationViolatedRecommendedAction,
    *,
    detected_by: ObligationViolatedDetectedBy = ObligationViolatedDetectedBy.M05_SELF_REPORTED,
    violation_evidence: dict[str, object] | None = None,
    commit_rejected_event_id: str | None = None,
) -> str:
    """Emit ObligationViolated event through commit gate. Returns domain event_id.

    RUN-068 件B (M-0.6 §4.2.4): carries detected_by (m05_mechanical_pattern / m05_audit_verdict /
    m05_self_reported), violation_evidence (object — pattern hit location / audit reasoning / self
    report), commit_rejected_event_id (the same-batch CommitRejected). M-0.5a Patch 1: only produced
    on a confirmed_violation. All additive (defaults preserve the prior self-reported shape).
    """
    subject = Subject(
        entity_type=SubjectEntityType.OBLIGATION,
        entity_id=obligation_id,
        role=SubjectRole.PRIMARY,
    )
    session_id = f"sess-oblig-violate-{uuid.uuid4().hex[:8]}"
    intent = EventIntent(
        local_intent_id=f"oblig-violate-{uuid.uuid4().hex[:12]}",
        event_type=EventType.OBLIGATION_VIOLATED,
        event_category=EventCategory.OBLIGATION,
        payload={
            "obligation_id": obligation_id,
            "obligation_lifecycle_state": "violated",
            "violated_in_envelope_event_id": violated_in_envelope_event_id,
            "violation_description": violation_description,
            "recommended_action": recommended_action.value,
            # RUN-068 §4.2.4 detection-provenance fields
            "detected_by": detected_by.value,
            "violation_evidence": violation_evidence or {"self_report": violation_description},
            "commit_rejected_event_id": commit_rejected_event_id,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.COMMIT_GATE.value,
            actor_id="m05-commit-gate-violate",
            session_id=session_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[subject],
        schema_version="1.0.0",
    )
    return _emit_lifecycle_event(
        towow_dir, intent, subject, "m05-commit-gate-violate", session_id,
    )


__all__ = [
    "IllegalObligationTransitionError",
    "activate_obligation",
    "capture_obligation",
    "check_obligation",
    "evolve_obligation",
    "is_legal_canonical_transition",
    "retire_obligation",
    "violate_obligation",
]
