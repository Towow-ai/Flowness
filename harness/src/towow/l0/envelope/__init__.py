"""M-0.4 Envelope — transaction declaration build + read-back.

builder: submit-wrapper derivation of read_set / write_set / claims (M-0.4 §3.3).
reader:  reconstruct a canonical TransactionEnvelope from the event log (M-0.4 §6.2).
checks:  structural boundary validation — EnvelopeIntegrity (§3.0) + ClaimsBoundary (§3.6).
engine:  task-facing public assembly API over those primitives — Envelope→EventIntent
         (§6.1) + patches routing (§5), plus thin adapters for derive/check/read-back
         (the API surface plan-m04-envelope-payoff named; see engine.py header for the
         stale-premise reconciliation — the derivation core pre-existed in builder/checks/reader).

Naming note: engine.derive_write_set / engine.derive_read_set / engine.check_claims_boundary
are the TASK-FACING variants (envelope-in ergonomic signatures) that DELEGATE to the builder /
checks primitives of the same name — import the one whose signature you need (they are exported
under their module paths, not collapsed here, to keep the two signatures unambiguous).
"""

from towow.l0.envelope.builder import (
    build_envelope,
    derive_actual_write_set_from_commit,
    derive_claims,
    derive_read_set,
    derive_write_set,
)
from towow.l0.envelope.checks import (
    ClaimsBoundaryResult,
    IntegrityResult,
    check_claims_boundary,
    check_envelope_integrity,
)
from towow.l0.envelope.engine import (
    BoundaryViolation,
    DomainIntentSpec,
    EnvelopeEngineError,
    build_envelope_intent,
    patches_to_domain_intent_specs,
    read_envelope_from_event,
)
from towow.l0.envelope.reader import EnvelopeReadError, list_envelopes, read_envelope

__all__ = [
    "BoundaryViolation",
    "ClaimsBoundaryResult",
    "DomainIntentSpec",
    "EnvelopeEngineError",
    "EnvelopeReadError",
    "IntegrityResult",
    "build_envelope",
    "build_envelope_intent",
    "check_claims_boundary",
    "check_envelope_integrity",
    "derive_actual_write_set_from_commit",
    "derive_claims",
    "derive_read_set",
    "derive_write_set",
    "list_envelopes",
    "patches_to_domain_intent_specs",
    "read_envelope",
    "read_envelope_from_event",
]
