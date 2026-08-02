"""M-0.5 §3.4 ObligationCheck — coverage + maintenance (RUN-029 第0波 ①d).

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md §3.4
#   §3.4.1 Coverage — declared must cover the capsule's injected obligations
#   §3.4.2 Maintenance — per declaration: violated → reject; not_applicable + red_line →
#          reject; maintained + forbidden_pattern → pattern-match; no checker → audit sample
#   §3.4.3 ObligationViolated produced in the REJECTED batch (path A, atomic) by M-0.5
#          (violation-detection authority)
#
# What is REAL here vs honest debt:
#   - self-reported `violated`            → reject + ObligationViolated  (REAL)
#   - `not_applicable` on a red_line obl  → reject + ObligationViolated  (REAL — severity read
#                                            from the obligation's ObligationCaptured event)
#   - coverage (count floor)              → declared count < capsule.obligation_count → gap
#                                            (REAL coarse check over the data the capsule DOES
#                                            carry — summary counts, M-0.1 §3.5)
#   - coverage (which specific obligation)→ REAL (RUN-038 L0增强, resolves debt-d6f14ce7b0c5).
#                                            capsule.injected_obligations is recoverable: the M-0.3
#                                            pipeline emits one ObligationActivated per
#                                            final_active_set member causation-linked to the
#                                            CapsuleCompiled (pipeline._emit_obligation_activated →
#                                            causation_event_id=capsule id). So the injected set =
#                                            {ObligationActivated.obligation_id : causation == capsule
#                                            id} via EventIndex.lookup_causation. missing =
#                                            injected - declared → reject(obligation_silently_omitted)
#                                            (§3.4.1). When no ObligationActivated is causation-linked
#                                            (stub capsule / no pipeline run) we fall back to the coarse
#                                            count floor — honest degradation, never a false 'missing'.
#   - `maintained` + forbidden_pattern    → KEPT AS DEBT (debt-89f6923c4cd2). The canonical
#                                            CheckerMetadata schema (schemas/obligation.py) HAS a
#                                            forbidden_pattern field, but the EVENT-LEVEL
#                                            ObligationCapturedPayload (schemas/payloads/obligation.py)
#                                            does NOT carry checker_metadata — and the only producer
#                                            of ObligationCaptured (l0/obligations/bootstrap.py) emits
#                                            mechanically_checkable + checker_scope_hint only, with NO
#                                            forbidden_pattern (M-0.6 §5.2: all 4 bootstrap obligations
#                                            have forbidden_pattern=null — the mechanically-checkable
#                                            ones are covered by NoveltyCheck §3.5.1 / EnvelopeIntegrity
#                                            §3.0, not pattern matching). So there is ZERO
#                                            forbidden_pattern data anywhere in the live event log to
#                                            match against. A matcher built now would be vacuous →
#                                            debt KEPT, not fake-implemented. Becomes real once a
#                                            producer event-sources checker_metadata.forbidden_pattern.
#   - no-checker sampling audit           → audit fork (§5) → DebtRegistered (M-3.1 boundary)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l0.event_log.index import EventIndex
    from towow.schemas.event_intent import EventIntent


@dataclass(frozen=True)
class ObligationViolation:
    """One detected violation → one ObligationViolated event in the rejected batch."""

    obligation_id: str
    reason: str  # self_reported_violation | red_line_obligation_not_applicable | forbidden_pattern_matched
    description: str


# ─── RUN-068 件A — ObligationCaptured provenance enforcement (M-0.6 §11 不变量5 / §4.1.1) ─────


@dataclass(frozen=True)
class ObligationProvenanceCheckResult:
    """Outcome of the runtime-capture provenance check. passed=False → reject the commit."""

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] | None = None


def _capture_source(payload: dict[str, object]) -> str | None:
    """capture_source from a (possibly nested) ObligationCaptured payload — prefers the §4.1.1
    capture_provenance.capture_source block, falls back to the flat field."""
    prov = payload.get("capture_provenance")
    if isinstance(prov, dict):
        src = prov.get("capture_source")
        if isinstance(src, str) and src:
            return src
    flat = payload.get("capture_source")
    return flat if isinstance(flat, str) and flat else None


def _source_event_refs(payload: dict[str, object]) -> list[str]:
    """source_event_refs from a (possibly nested) ObligationCaptured payload — prefers the §4.1.1
    capture_provenance.source_event_refs block, falls back to a flat field. Returns [] if absent."""
    prov = payload.get("capture_provenance")
    if isinstance(prov, dict):
        refs = prov.get("source_event_refs")
        if isinstance(refs, list):
            return [r for r in refs if isinstance(r, str) and r.strip()]
    flat = payload.get("source_event_refs")
    if isinstance(flat, list):
        return [r for r in flat if isinstance(r, str) and r.strip()]
    return []


def check_obligation_capture_provenance(
    domain_intents: Iterable[EventIntent],
) -> ObligationProvenanceCheckResult:
    """M-0.6 §11 不变量5 / §4.1.1 — a runtime ObligationCaptured MUST carry non-empty provenance.

    不变量5: "obligation 节点必须有 provenance — capture_provenance 字段必填; source_event_refs
    追溯触发事件". A runtime capture (any capture_source that is NOT system_bootstrap) without at
    least one non-blank source_event_refs entry has no traceable trigger → the commit is rejected
    (provenance is真强制, not摆设). The genesis system_bootstrap captures legitimately carry empty
    source_event_refs (§5.2) and go through gate.bootstrap_commit (not this attempt_commit path),
    so on the normal commit path any ObligationCaptured lacking source_event_refs is a provenance gap.

    Runs over the co-submitted domain intents BEFORE the obligation lands — a rejected capture never
    writes the ObligationCaptured. Returns passed=True when no ObligationCaptured is present (no-op).
    """
    for intent in domain_intents:
        if intent.event_type.value != "ObligationCaptured":
            continue
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        capture_source = _capture_source(payload)
        if capture_source == "system_bootstrap":
            # Genesis exception — should not reach attempt_commit, but never false-reject it.
            continue
        if not _source_event_refs(payload):
            oid = payload.get("obligation_id")
            return ObligationProvenanceCheckResult(
                passed=False,
                failure_reason="obligation_provenance_missing",
                failure_evidence={
                    "obligation_id": str(oid) if isinstance(oid, str) else "<unknown>",
                    "capture_source": capture_source or "<unset>",
                    "rule": (
                        "M-0.6 §11 不变量5 / §4.1.1 — a runtime ObligationCaptured must carry "
                        "capture_provenance.source_event_refs (≥1 upstream trigger event); empty "
                        "provenance is only legitimate for the genesis system_bootstrap path."
                    ),
                },
            )
    return ObligationProvenanceCheckResult(passed=True)


# ─── RUN-068 件C — forbidden_pattern physical matcher (M-0.5 §3.4.2 / M-0.6 §2.1) ──────────────

# Match outcomes. MATCH → the forbidden pattern is present in the content (a `maintained`
# declaration is contradicted → violation). NO_MATCH → pattern absent (maintained holds).
# UNCHECKABLE → the matcher cannot physically decide (ast pattern_type needs a language-aware
# parser — honest debt, NOT a fake pass; the obligation falls through to the §3.4.2 audit-sampling
# path, exactly as a no-checker obligation does — never a silent fail-open).
MATCH = "match"
NO_MATCH = "no_match"
UNCHECKABLE = "uncheckable"


def match_forbidden_pattern(pattern: str | None, pattern_type: str | None, content: str) -> str:
    """M-0.5 §3.4.2 / M-0.6 §2.1 — physically match a forbidden_pattern against content.

    Real for regex + text_match; ast is honest debt (returns UNCHECKABLE — AST matching needs a
    per-language parser, deferred). An empty pattern or empty content → NO_MATCH (nothing to find).
    A malformed regex → UNCHECKABLE (cannot physically decide; not a fake pass). This is the library
    layer; the daemon continuous-maintenance sweep that scans the live graph is daemon-gated and
    remains debt (legit-future).
    """
    if not pattern or not content:
        return NO_MATCH
    if pattern_type == "regex":
        try:
            return MATCH if re.search(pattern, content) else NO_MATCH
        except re.error:
            return UNCHECKABLE
    if pattern_type == "text_match":
        return MATCH if pattern in content else NO_MATCH
    if pattern_type == "ast":
        return UNCHECKABLE
    # Unknown / unset pattern_type with a pattern present — cannot decide the matching strategy.
    return UNCHECKABLE


def _obligation_forbidden_pattern(
    index: EventIndex,
    obligation_id: str,
) -> tuple[str, str] | None:
    """Latest (forbidden_pattern, pattern_type) for an obligation, from its lifecycle events.

    Reads obligation_definition.checker_metadata.{forbidden_pattern, pattern_type} (RUN-068 §4.1.1
    event-sourced checker metadata) from the highest-sequence ObligationCaptured / ObligationEvolved.
    Returns None when the obligation declares no mechanically-checkable forbidden_pattern (the common
    case — those obligations take the §3.4.2 audit-sampling path, not pattern matching).
    """
    best_seq = -1
    found: tuple[str, str] | None = None
    for rec in index.lookup_obligation(obligation_id):
        if rec.event_type.value not in {"ObligationCaptured", "ObligationEvolved"}:
            continue
        if rec.sequence_number <= best_seq:
            continue
        pattern: str | None = None
        ptype: str | None = None
        definition = rec.payload.get("obligation_definition")
        if isinstance(definition, dict):
            cm = definition.get("checker_metadata")
            if isinstance(cm, dict):
                p = cm.get("forbidden_pattern")
                t = cm.get("pattern_type")
                pattern = p if isinstance(p, str) and p else None
                ptype = t if isinstance(t, str) and t else None
        if pattern is None:
            flat = rec.payload.get("forbidden_pattern")
            pattern = flat if isinstance(flat, str) and flat else None
        if pattern is not None and ptype is None:
            ptype = "text_match"  # a flat-only forbidden_pattern defaults to substring matching
        best_seq = rec.sequence_number
        found = (pattern, ptype) if pattern is not None else None
    return found


@dataclass(frozen=True)
class ObligationCheckResult:
    """Outcome of ObligationCheck. violations non-empty → reject (all produced, §3.7)."""

    passed: bool
    violations: tuple[ObligationViolation, ...] = ()
    notices: tuple[str, ...] = ()
    # capability refs the gate should register debt against (capsule-blocked coverage etc.)
    coverage_gap_count: int | None = None


def _injected_obligation_ids(index: EventIndex, capsule_compiled_event_id: str) -> set[str]:
    """§3.4.1 — the obligations the capsule injected = ObligationActivated events the M-0.3
    pipeline emitted causation-linked to this CapsuleCompiled.

    Empty when the capsule ref does not resolve / no pipeline ObligationActivated was
    causation-linked (stub capsule / hand-written CapsuleCompiled) — the caller then falls back
    to the coarse count floor rather than fabricating a 'missing' reject.
    """
    injected: set[str] = set()
    for rec in index.lookup_causation(capsule_compiled_event_id):
        if rec.event_type.value != "ObligationActivated":
            continue
        oid = rec.payload.get("obligation_id")
        if isinstance(oid, str) and oid:
            injected.add(oid)
    return injected


def _obligation_severity(index: EventIndex, obligation_id: str) -> str | None:
    """Latest declared severity for an obligation, read from its lifecycle events.

    Reads ObligationCaptured (and any later ObligationEvolved carrying severity) from the
    entity index — avoids a ProjectionStore dependency in the gate. Bootstrap red_line
    obligations never evolve, so the captured severity is authoritative for them.
    """
    severity: str | None = None
    best_seq = -1
    for rec in index.lookup_obligation(obligation_id):
        if rec.event_type.value not in {"ObligationCaptured", "ObligationEvolved"}:
            continue
        sev = rec.payload.get("severity")
        if isinstance(sev, str) and rec.sequence_number > best_seq:
            severity = sev
            best_seq = rec.sequence_number
    return severity


def check_obligations(
    index: EventIndex,
    capsule_compiled_event_id: str,
    active_obligations_declared: list[object],
    match_content: str = "",
) -> ObligationCheckResult:
    """M-0.5 §3.4 — coverage floor + maintenance verification.

    Runs every declaration even after the first failure (§3.7 exception) so the rejected
    batch carries ALL detected ObligationViolated, not just the first.

    RUN-068 件C (§3.4.2 maintenance): a `maintained` declaration for an obligation that
    event-sources a mechanically-checkable forbidden_pattern is now physically matched against
    `match_content` (the envelope's matchable text — patch summaries + declared content, supplied
    by the gate). A MATCH contradicts the `maintained` claim → ObligationViolated
    (forbidden_pattern_matched). An UNCHECKABLE pattern (ast / malformed regex) or a no-checker
    obligation falls through to the §3.4.2 audit-sampling path (notice, NOT a silent pass). The
    daemon continuous-maintenance sweep over the live graph stays daemon-gated debt (legit-future).
    """
    violations: list[ObligationViolation] = []
    notices: list[str] = []

    declared_ids: set[str] = set()
    for decl in active_obligations_declared:
        if not isinstance(decl, dict):
            continue
        oid = decl.get("obligation_id")
        status = decl.get("status")
        if not isinstance(oid, str) or not oid:
            continue
        declared_ids.add(oid)

        if status == "violated":
            # §3.4.2 — agent self-reports a violation; the gate cannot accept it (fail-fast).
            violations.append(
                ObligationViolation(
                    obligation_id=oid,
                    reason="self_reported_violation",
                    description=f"declaration self-reports obligation {oid} as violated",
                ),
            )
        elif status == "not_applicable":
            severity = _obligation_severity(index, oid)
            if severity == "red_line":
                # §3.4.2 — a red_line obligation is always applicable; not_applicable is a reject.
                violations.append(
                    ObligationViolation(
                        obligation_id=oid,
                        reason="red_line_obligation_not_applicable",
                        description=f"red_line obligation {oid} declared not_applicable",
                    ),
                )
            elif severity is None:
                notices.append(
                    f"obligation_severity_unresolved: {oid} not_applicable but no "
                    "ObligationCaptured found in index — cannot enforce red_line rule",
                )
        elif status == "maintained":
            # §3.4.2 — `maintained` + a mechanically-checkable forbidden_pattern → physical match
            # against the envelope content (RUN-068 件C; resolves the vacuous-matcher debt for the
            # checkable case now that capture event-sources checker_metadata.forbidden_pattern). A
            # MATCH means the agent declared maintained while the forbidden pattern IS present → reject.
            checker = _obligation_forbidden_pattern(index, oid)
            if checker is not None:
                pattern, ptype = checker
                outcome = match_forbidden_pattern(pattern, ptype, match_content)
                if outcome == MATCH:
                    violations.append(
                        ObligationViolation(
                            obligation_id=oid,
                            reason="forbidden_pattern_matched",
                            description=(
                                f"obligation {oid} declared maintained but its forbidden_pattern "
                                f"({ptype}:{pattern!r}) physically matched the envelope content (§3.4.2)"
                            ),
                        ),
                    )
                elif outcome == UNCHECKABLE:
                    notices.append(
                        f"obligation_forbidden_pattern_uncheckable: {oid} pattern_type={ptype} "
                        "cannot be physically matched (ast/ malformed) → §3.4.2 audit-sampling path "
                        "(daemon continuous sweep is daemon-gated debt, legit-future)",
                    )
            # No event-sourced forbidden_pattern → the §3.4.2 no-checker audit-sampling path
            # (audit fork = M-0.5:audit-fork boundary), NOT a silent pass — never a fail-open here.

    # §3.4.1 coverage. RUN-038: PRECISE coverage first — the injected obligation ids are the
    # ObligationActivated events the M-0.3 pipeline causation-linked to this capsule. missing =
    # injected - declared → reject(obligation_silently_omitted) per §3.4.1 (sentinel produces an
    # ObligationViolated for each, §3.4.3). Falls back to the coarse count floor only when no
    # injected set is recoverable (stub capsule / no pipeline ObligationActivated) — honest
    # degradation, never a fabricated 'missing'.
    coverage_gap: int | None = None
    injected = _injected_obligation_ids(index, capsule_compiled_event_id)
    if injected:
        for missing_id in sorted(injected - declared_ids):
            violations.append(
                ObligationViolation(
                    obligation_id=missing_id,
                    reason="obligation_silently_omitted",
                    description=(
                        f"capsule injected obligation {missing_id} but the envelope's "
                        "active_obligations_declared does not cover it (§3.4.1 silent omission)"
                    ),
                ),
            )
    else:
        # Coarse count floor (no precise injected set available) — notice only, not a reject.
        cap = index.lookup_event_id(capsule_compiled_event_id)
        if cap is not None:
            summary = cap.payload.get("capsule_sections_summary")
            if isinstance(summary, dict):
                inj = summary.get("obligation_count")
                if isinstance(inj, int) and len(declared_ids) < inj:
                    coverage_gap = inj - len(declared_ids)
                    notices.append(
                        f"obligation_coverage_gap: declared {len(declared_ids)} < capsule "
                        f"injected {inj} (count floor; precise injected set unavailable — no "
                        "causation-linked ObligationActivated, e.g. stub capsule)",
                    )

    return ObligationCheckResult(
        passed=not violations,
        violations=tuple(violations),
        notices=tuple(notices),
        coverage_gap_count=coverage_gap,
    )


__all__ = [
    "MATCH",
    "NO_MATCH",
    "UNCHECKABLE",
    "ObligationCheckResult",
    "ObligationProvenanceCheckResult",
    "ObligationViolation",
    "check_obligation_capture_provenance",
    "check_obligations",
    "match_forbidden_pattern",
]
