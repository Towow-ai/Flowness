"""obligation category event payload schemas (event-level; not the canonical Obligation object).

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.10 ObligationFields (L239..L252)
#     §3.10 (L939..L1007) — 6 base obligation lifecycle events
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §4.1 / §4.2 lifecycle event details — full canonical Obligation schema in Step 1.d
#
# Note: This is event-level schema for obligation lifecycle events. The canonical
# Obligation one-class object (M-0.6 §2.1) lives in schemas/obligation.py (Step 1.d).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    ObligationActivationSource,
    ObligationActiveSetRole,
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
    SubjectEntityType,
)
from towow.schemas.obligation import CheckerMetadata, ScopeHint
# M-3.3 §14.3 narrow patch — ObligationCaptured payload 加可选 origin_metadata。OriginMetadata
# 住 leaf 模块 (只依赖 enums) → 单向 import 无环。
from towow.schemas.payloads.migration import OriginMetadata

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class ObligationAttachmentRef(BaseModel):
    """ObligationCaptured.attached_to[] per M-0.1 §3.10."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)


class CaptureProvenanceRef(BaseModel):
    """ObligationCaptured.capture_provenance event-level block per M-0.6 §4.1.1 (RUN-068 件A).

    The event-payload provenance block (distinct from schemas/obligation.py CaptureProvenance,
    the canonical one-class object which also carries captured_at). §4.1.1's event block is
    {capture_source, source_event_refs, captured_by_actor_id} — the authoritative timestamp is
    stamped by the M-0.1 writer, not carried here. source_event_refs traces the upstream events
    that triggered the capture (e.g. NatureJudgmentCaptured.event_id); empty ONLY for the genesis
    system_bootstrap obligations (§5.2). 不变量5: a runtime capture (non-bootstrap) with empty
    source_event_refs is rejected at the commit gate (gate.check_obligation_capture_provenance).
    """

    model_config = _STRICT

    capture_source: ObligationCaptureSource
    source_event_refs: list[str] = Field(default_factory=list)
    captured_by_actor_id: str = Field(min_length=1)


class ObligationDefinitionBlock(BaseModel):
    """ObligationCaptured.obligation_definition nested block per M-0.6 §4.1.1 (RUN-068 件B).

    The full obligation definition (§2.1 Obligation schema, minus capture_provenance which is its
    own §4.1.1 block). Mirrors the flat top-level fields the producer also emits (kept for the
    existing consumers — M-0.2 reducer / commit gate / obligation_checks read the flat fields); this
    nested block is the spec-faithful §4.1.1 shape so the full definition travels as one object.
    """

    model_config = _STRICT

    nature: ObligationNature
    severity: ObligationSeverity
    scope_type: ObligationScopeType
    scope_rule: str = Field(min_length=1)
    scope_hint: ScopeHint | None = None
    definition: str = Field(min_length=1)
    attached_to: list[ObligationAttachmentRef] = Field(min_length=1)
    checker_metadata: CheckerMetadata | None = None


class ObligationCapturedPayload(BaseModel):
    """M-0.1 §3.10 / M-0.6 §4.1.1 ObligationCaptured — first lifecycle event.

    M-3.3 §14.3 Patch M-3.3-3: 加可选字段 origin_metadata — 迁移工具产的 ObligationCaptured
    含此字段反向追溯到原始 v2.x 文件 (§5.1)。schema 字段扩展 (非 breaking, default None)。

    RUN-068 件A/件B: adds the §4.1.1 capture_provenance block (capture_source + source_event_refs
    + captured_by_actor_id) + obligation_definition nested block. The flat top-level fields are
    retained (the M-0.2 reducer / commit gate / obligation_checks read them); the nested blocks are
    additive and OPTIONAL at the schema level so the 20+ hand-built ObligationCaptured fixtures still
    conform. Provenance NECESSITY is enforced at the commit gate (a runtime capture lacking
    source_event_refs is rejected), not by schema-required-ness — the bootstrap genesis captures
    legitimately carry empty source_event_refs (§5.2).
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.CAPTURED] = (
        ObligationFieldsLifecycleState.CAPTURED
    )
    attached_to: list[ObligationAttachmentRef] = Field(min_length=1)
    scope_rule: str = Field(min_length=1)
    scope_type: ObligationScopeType
    scope_hint: ScopeHint | None = None  # M-0.6 §2.1 / §4.1.1 — mechanical-filter assist
    severity: ObligationSeverity
    nature: ObligationNature
    definition: str
    origin_metadata: OriginMetadata | None = None  # M-3.3 §14.3 (迁移源留痕, 非迁移产物为 None)
    # ── RUN-068 §4.1.1 structured blocks (additive; producers always emit them) ──
    capture_provenance: CaptureProvenanceRef | None = None
    obligation_definition: ObligationDefinitionBlock | None = None
    # ── flat producer fields (bootstrap + runtime emit these alongside the nested blocks) ──
    capture_source: ObligationCaptureSource | None = None
    mechanically_checkable: bool | None = None
    forbidden_pattern: str | None = None  # M-0.6 §2.1 checker_metadata — RUN-068 件C event-sources it
    checker_scope_hint: str | None = None


class ObligationActivatedPayload(BaseModel):
    """M-0.1 §3.10 / M-0.6 §4.2.2 — produced by M-0.3 pipeline (stage 4 resolver verdict).

    RUN-068 件B: §4.2.2 fields added (activation_id / activated_for_run_id / capsule_compiled_event_id
    / scope_judgment_event_id / active_set_role / materialized_into). "activated" = activated FOR a
    specific task/run/capsule (an activation edge), NOT the obligation entering a global active
    lifecycle state — the same obligation can be activated for many tasks; there is no deactivation.
    Additive + OPTIONAL so existing producers (which emit only the original fields) still conform.
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.ACTIVATED] = (
        ObligationFieldsLifecycleState.ACTIVATED
    )
    activated_for_task_id: str = Field(min_length=1)
    activation_source: ObligationActivationSource
    resolver_decision_event_id: str | None = None
    # ── RUN-068 §4.2.2 activation-edge fields (additive) ──
    activation_id: str | None = None  # "act-{uuid}" — this materialization's unique id
    activated_for_run_id: str | None = None
    capsule_compiled_event_id: str | None = None  # the capsule that actually materialized it
    scope_judgment_event_id: str | None = None  # the corresponding ObligationScopeJudged event
    active_set_role: ObligationActiveSetRole | None = None  # mirrors severity → placement
    materialized_into: ObligationMaterializedInto | None = None  # capsule | envelope_requirement


class ObligationCheckedPayload(BaseModel):
    """M-0.1 §3.10 / M-0.6 §4.2.3 — produced by M-0.5 commit gate during checks.

    Cap2 (RUN-038): check_method + check_outcome are typed enums (not bare strings) and
    evidence_refs carries audit-verdict provenance. check_outcome only admits the two
    passing outcomes (maintained / not_applicable); a violated obligation produces an
    ObligationViolated event in the rejected batch, never an ObligationChecked.

    check_result is retained for backward compatibility (older producers wrote the agent's
    declared status here); check_outcome is the spec's authoritative typed field.
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.CHECKED] = (
        ObligationFieldsLifecycleState.CHECKED
    )
    checked_in_envelope_event_id: str = Field(min_length=1)
    check_method: ObligationCheckMethod
    check_outcome: ObligationCheckOutcome
    evidence_refs: list[str] | None = None  # M-0.6 §4.2.3 — AuditVerdictReceived refs (audit_verdict)
    # backward-compat: agent's declared status (maintained / violated / not_applicable).
    check_result: ObligationDeclaredStatus | None = None


class ObligationViolatedPayload(BaseModel):
    """M-0.1 §3.10 / M-0.6 §4.2.4 — produced by M-0.5 (violation detection authority).

    RUN-068 件B: §4.2.4 fields added (detected_by enum / violation_evidence object /
    commit_rejected_event_id). M-0.5a Patch 1: ObligationViolated 只在 verdict=reject +
    confirmed_violation 时产 (escalate_to_nature / unverifiable 不产). Additive + OPTIONAL so
    existing producers conform; the gate populates detected_by + violation_evidence going forward.
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.VIOLATED] = (
        ObligationFieldsLifecycleState.VIOLATED
    )
    violated_in_envelope_event_id: str = Field(min_length=1)
    violation_description: str
    recommended_action: ObligationViolatedRecommendedAction
    # ── RUN-068 §4.2.4 detection-provenance fields (additive) ──
    detected_by: ObligationViolatedDetectedBy | None = None
    violation_evidence: dict[str, object] | None = None  # pattern hit location / audit reasoning / self report
    commit_rejected_event_id: str | None = None  # the same-batch CommitRejected


class ObligationEvolvedBeforeAfter(BaseModel):
    """ObligationEvolved.before_state / after_state per M-0.1 §3.10 — evolve a captured obligation."""

    model_config = _STRICT

    scope_rule: str
    severity: ObligationSeverity
    definition: str


class ObligationEvolvedNovelty(BaseModel):
    """ObligationEvolved.novelty per M-0.6 §4.1.2 — consumed by M-0.5 NoveltyCheck.

    Cap3 (RUN-038): an obligation may evolve (active → superseded) only with substantive
    novelty. novelty_type picks one of the 5 PatchNoveltyType values; no_new_information must
    be false (true is forbidden — it would permit oscillation A→A'→A→A'); and at least one of
    the new_* payload fields must be non-empty (a blank / whitespace string does not count).
    """

    model_config = _STRICT

    novelty_type: PatchNoveltyType
    no_new_information: bool
    new_constraint: str | None = None
    new_evidence: str | None = None
    new_resolution: str | None = None
    scope_change: dict[str, object] | None = None
    nature_override: dict[str, object] | None = None

    @model_validator(mode="after")
    def _require_substantive_novelty(self) -> ObligationEvolvedNovelty:
        if self.no_new_information:
            msg = (
                "ObligationEvolved.novelty.no_new_information must be false (M-0.6 §4.1.2 — "
                "no_new_information=true is forbidden, it permits A→A'→A oscillation)"
            )
            raise ValueError(msg)
        has_text = any(
            (val or "").strip() for val in (self.new_constraint, self.new_evidence, self.new_resolution)
        )
        has_obj = self.scope_change is not None or self.nature_override is not None
        if not (has_text or has_obj):
            msg = (
                "ObligationEvolved.novelty must populate at least one of new_constraint / "
                "new_evidence / new_resolution / scope_change / nature_override (M-0.6 §4.1.2)"
            )
            raise ValueError(msg)
        return self


class ObligationEvolvedPayload(BaseModel):
    """M-0.1 §3.10 / M-0.6 §4.1.2 — supersede.is_supersede=true required + novelty mandatory (O-12).

    Cap3 (RUN-038): novelty is now a required, validated block (was missing). An evolve event
    without substantive novelty is structurally unconstructible.
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.EVOLVED] = (
        ObligationFieldsLifecycleState.EVOLVED
    )
    before_state: ObligationEvolvedBeforeAfter
    after_state: ObligationEvolvedBeforeAfter
    novelty: ObligationEvolvedNovelty


class ObligationRetiredPayload(BaseModel):
    """M-0.1 §3.10 — supersede protocol required.

    Bootstrap obligations (capture_source=system_bootstrap, per M-0.6 §5.1) cannot be retired
    — that invariant is enforced at the obligation-system layer, not at this event-schema level.
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    obligation_lifecycle_state: Literal[ObligationFieldsLifecycleState.RETIRED] = (
        ObligationFieldsLifecycleState.RETIRED
    )
    retirement_reason: str = Field(min_length=1)


__all__ = [
    "CaptureProvenanceRef",
    "ObligationActivatedPayload",
    "ObligationAttachmentRef",
    "ObligationCapturedPayload",
    "ObligationCheckedPayload",
    "ObligationDefinitionBlock",
    "ObligationEvolvedBeforeAfter",
    "ObligationEvolvedNovelty",
    "ObligationEvolvedPayload",
    "ObligationRetiredPayload",
    "ObligationViolatedPayload",
]
