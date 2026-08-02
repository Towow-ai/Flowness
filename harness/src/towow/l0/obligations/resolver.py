"""M-0.6 §6.5 Conservative Fallback resolver.

# spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#   §6.5 Conservative Fallback (L968..L1000) — when LLM unavailable / timeout / error
#   §3.2 (M-0.3) fallback shape: hit=true, confidence=0.0, fallback_applied=true
#
# The LLM resolver (the semantic sufficiency judge for candidates that passed the
# mechanical neighborhood check) lands in E.3 (skill deployment). Until then this
# conservative fallback is the resolver path — it is spec-correct behavior (§6.5), not a
# stub: the discriminator between "applies to you / does not" runs upstream in the M-0.3
# §2.3 mechanical filter (GLOBAL/ALL_PATCHES/CONCEPT_NEIGHBORHOOD anchors∩neighborhood).
# Whatever reaches the resolver is conservatively included.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from towow.schemas.resolver import (
    ObligationJudgment,
    ResolverVerdict,
    VerdictMeta,
)

if TYPE_CHECKING:
    from towow.schemas.resolver import ResolverInput


# ─── Resolver call error taxonomy (M-0.3 §3.1) ───────────────────────────────


class ResolverError(RuntimeError):
    """M-0.3 §3.1 m06_error — a recoverable resolver failure → conservative fallback.

    Base of the fallback-eligible errors (timeout / unreachable are subclasses). Raising any of
    these from the resolver callable means "the resolver could not produce a usable verdict", and
    the §3.1 contract says: do NOT abort the pipeline — conservatively include all candidates so
    the agent never runs naked of obligations ("宁可多注入也不能让 agent 裸跑").
    """


class ResolverTimeout(ResolverError):
    """M-0.3 §3.1 timeout — the resolver exceeded resolver_config.timeout_ms → conservative fallback."""


class ResolverUnreachable(ResolverError):
    """M-0.3 §3.1 m06_unreachable — the resolver service could not be reached → conservative fallback."""


class ResolverSchemaInvalid(RuntimeError):
    """M-0.3 §3.1 schema_invalid — the resolver returned a structurally invalid verdict → ABORT.

    Deliberately NOT a subclass of ResolverError: a schema-invalid verdict means the resolver
    implementation is broken (M-0.6 bug), so §3.1 says pipeline_abort("resolver_schema_invalid")
    rather than conservative fallback. Continuing would mean trusting a resolver that cannot honor
    its own output contract.
    """


class _ResolverCallable(Protocol):
    """The resolver invocation seam — receives ResolverInput, returns a ResolverVerdict.

    The default is conservative_fallback_verdict (E.1.5; LLM resolver lands E.3). A real resolver
    raises ResolverTimeout / ResolverError / ResolverUnreachable on transient failure, and returns
    a verdict that may fail validation (→ ResolverSchemaInvalid in call_resolver).
    """

    def __call__(self, resolver_input: ResolverInput) -> ResolverVerdict: ...


def conservative_fallback_verdict(resolver_input: ResolverInput) -> ResolverVerdict:
    """Conservative fallback: hit=true for all red_line + invariant; uncertain otherwise.

    M-0.6 §6.5 spec: when resolver unavailable, return hit=true with low confidence so
    capsule injection is conservative (false-positive cheaper than false-negative for
    obligations).
    """
    start = datetime.now(tz=UTC)
    judgments: list[ObligationJudgment] = []
    for candidate in resolver_input.candidates:
        # M-0.6 §6.5 / M-0.3 §3.2 — conservative include: hit=true at confidence=0.0
        # (explicit low confidence), so capsule injection is conservative when the LLM
        # resolver is unavailable. "宁可多注入也不能让 agent 裸跑."
        judgments.append(
            ObligationJudgment(
                obligation_id=candidate.obligation_id,
                hit=True,
                confidence=0.0,
                reason="resolver unavailable, conservative include",
                evidence=[],
                uncertainty="resolver timeout/error, unable to judge scope",
                fallback_applied=True,
            ),
        )
    runtime_ms = int((datetime.now(tz=UTC) - start).total_seconds() * 1000)
    meta = VerdictMeta(
        verdict_id=f"vid-fallback-{uuid.uuid4().hex[:12]}",
        resolver_runtime_ms=runtime_ms,
        model="conservative_fallback",
        contract_version=resolver_input.resolver_config.contract_version,
        total_candidates=len(judgments),
        total_hits=sum(1 for j in judgments if j.hit),
        total_fallbacks=sum(1 for j in judgments if j.fallback_applied),
    )
    return ResolverVerdict(judgments=judgments, meta=meta)


def call_resolver(
    resolver_input: ResolverInput,
    *,
    resolver: _ResolverCallable | None = None,
) -> ResolverVerdict:
    """M-0.3 §3.1 resolver call contract — invoke the resolver and apply error_handling.

    error_handling (§3.1):
      timeout / m06_error / m06_unreachable  → conservative_fallback()  (do NOT abort)
      schema_invalid                          → raise ResolverSchemaInvalid  (pipeline aborts)

    The `resolver` seam defaults to conservative_fallback_verdict (E.1.5; the LLM resolver lands in
    E.3). A real resolver raises ResolverTimeout / ResolverError / ResolverUnreachable on transient
    failure — here those are caught and converted to a conservative fallback verdict (every
    needs_resolver candidate hit=true at confidence 0.0, fallback_applied=true), so the agent is
    never left without its obligations. A returned verdict is validated against the input
    (coverage per M-0.3 §3.3 + the verdict's own schema invariants); a validation failure is a
    schema_invalid abort — NOT a fallback — because a resolver that cannot honor its output
    contract must not be silently trusted.
    """
    invoke = resolver if resolver is not None else conservative_fallback_verdict
    try:
        verdict = invoke(resolver_input)
    except ResolverError:
        # timeout / m06_error / m06_unreachable (all ResolverError subclasses or the base) →
        # conservative fallback. The fallback verdict is itself schema-valid by construction.
        return conservative_fallback_verdict(resolver_input)
    # Validate the produced verdict — coverage + schema. Any failure → schema_invalid abort.
    try:
        verdict.check_covers(resolver_input)
    except ValueError as exc:
        raise ResolverSchemaInvalid(str(exc)) from exc
    return verdict
