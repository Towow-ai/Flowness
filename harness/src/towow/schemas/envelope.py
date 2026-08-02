"""TransactionEnvelope canonical schema.

# spec source:
#   03-l0-truth-source/M-0.4-envelope-detailed-design.md
#     §2 (L31..L116) full TransactionEnvelope schema
#     §3 (L120..L170) field responsibility (agent-filled / system-derived / capsule-injected)
#     §3.2 invariant 8 (M-0.4) — active_obligations_declared must cover capsule's
#       final_active_set; checked at M-0.5 commit gate.
#     §5 (L230..L295) per-task-type patches patterns (code_diff / concept_event / task_event /
#       doc_change / config_change)
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     Narrow Patch I — patches.patch_type adds 6th value 'obligation_event'
#       (Patch M-0.6-I; PatchType enum already extended in enums.py)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    EnvelopeClaimType,
    EnvelopeReadSetEntityType,
    EnvelopeUncertaintySeverity,
    EnvelopeUncertaintySuggestedAction,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    ObligationDeclaredStatus,
    PatchNoveltyType,
    PatchType,
    SupersedeNoveltyType,
)
from towow.schemas.payloads.state_transition import EntityRef
from towow.schemas.version import SchemaVersion

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class ReadEntry(BaseModel):
    """TransactionEnvelope.read_set[] per M-0.4 §2."""

    model_config = _STRICT

    entity_type: EnvelopeReadSetEntityType
    entity_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class WriteEntry(BaseModel):
    """TransactionEnvelope.write_set[] per M-0.4 §2."""

    model_config = _STRICT

    entity_type: EnvelopeWriteSetEntityType
    entity_id: str = Field(min_length=1)
    change_type: EnvelopeWriteChangeType


class ClaimEntry(BaseModel):
    """TransactionEnvelope.claims[] per M-0.4 §2 — Planner pre-declared resource holds."""

    model_config = _STRICT

    entity_type: EnvelopeWriteSetEntityType  # reuses write-set entity universe
    entity_id: str = Field(min_length=1)
    claim_type: EnvelopeClaimType


class ActiveObligationDeclared(BaseModel):
    """TransactionEnvelope.active_obligations_declared[] per M-0.4 §2 + M-0.5 invariant 8.

    Invariant: violated / not_applicable status require justification (M-0.4 §2).
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    status: ObligationDeclaredStatus
    justification: str | None = None

    @model_validator(mode="after")
    def enforce_justification(self) -> Self:
        needs_justification = self.status in (
            ObligationDeclaredStatus.VIOLATED,
            ObligationDeclaredStatus.NOT_APPLICABLE,
        )
        if needs_justification and (self.justification is None or not self.justification.strip()):
            raise ValueError(
                f"status={self.status.value} requires non-empty justification",
            )
        return self


class PatchSupersedeBridge(BaseModel):
    """E.1.1 Conflict 5: explicit bridge between PatchNoveltyType and SupersedeNoveltyType.

    Required when `patch.is_supersede=true` and the corresponding domain event also
    walks supersede protocol. Forbids silent automatic mapping — agent must declare
    `mapping_reason` explaining why this PatchNoveltyType maps to that SupersedeNoveltyType.

    Example use case:
      patch.novelty_type = PatchNoveltyType.NEW_RESOLUTION  (patch-level)
      supersede_bridge.supersede_novelty_type = SupersedeNoveltyType.CORRECTED_ERROR
      supersede_bridge.mapping_reason = "new_resolution here addresses a prior incorrect verdict"
    """

    model_config = _STRICT

    supersede_novelty_type: SupersedeNoveltyType
    mapping_reason: str = Field(min_length=1)


class PatchEntry(BaseModel):
    """TransactionEnvelope.patches[] per M-0.4 §2 + Patch M-0.6-I (obligation_event 6th type).

    Invariants (E.1.1 Conflict 5):
      - is_supersede=true requires superseded_entity_ref + novelty_type
      - is_supersede=true requires supersede_bridge (explicit Patch→Supersede novelty mapping)
    """

    model_config = _STRICT

    patch_id: str = Field(min_length=1)
    patch_type: PatchType
    target: str = Field(min_length=1)
    summary: str
    detail_ref: str | None = None
    is_supersede: bool = False
    superseded_entity_ref: str | None = None
    novelty_type: PatchNoveltyType | None = None  # E.1.1 Conflict 5: patch-level novelty
    supersede_bridge: PatchSupersedeBridge | None = None  # E.1.1 Conflict 5: explicit bridge

    @model_validator(mode="after")
    def enforce_supersede_completeness(self) -> Self:
        if self.is_supersede:
            missing = [
                name
                for name, val in (
                    ("superseded_entity_ref", self.superseded_entity_ref),
                    ("novelty_type", self.novelty_type),
                    ("supersede_bridge", self.supersede_bridge),  # E.1.1 Conflict 5
                )
                if val is None
            ]
            if missing:
                raise ValueError(
                    f"is_supersede=true requires non-None: {missing}",
                )
        return self

    @model_validator(mode="after")
    def enforce_detail_ref_format(self) -> Self:
        """T-L0-26 (波1) M-0.4 §5.1-5.5 — detail_ref 按 patch_type 解析, 非自由文本:
        code_diff/doc_change/config_change → "git:<hash>"; concept/task/obligation_event →
        "evt:<id>"(逗号分隔多个)。让 commit gate 能从 patch 追溯到真 git diff / domain event。"""
        if self.detail_ref is None:
            return self
        git_types = {PatchType.CODE_DIFF, PatchType.DOC_CHANGE, PatchType.CONFIG_CHANGE}
        # consolidation_event (M-0.7 §10.3 Patch L) references domain events (CrossRunConsolidation
        # Committed / SnapshotCreated / ArchiveSegmentMoved / DigestSuperseded) → evt:<id> form.
        evt_types = {
            PatchType.CONCEPT_EVENT,
            PatchType.TASK_EVENT,
            PatchType.OBLIGATION_EVENT,
            PatchType.CONSOLIDATION_EVENT,
        }
        if self.patch_type in git_types:
            prefix, kind = "git:", "git hash"
        elif self.patch_type in evt_types:
            prefix, kind = "evt:", "event_id"
        else:  # pragma: no cover — PatchType 6 值已全覆盖, 防御未来新增
            return self
        parts = [p.strip() for p in self.detail_ref.split(",") if p.strip()]
        bad = [p for p in parts if not p.startswith(prefix)]
        if not parts or bad:
            raise ValueError(
                f"patch_type={self.patch_type.value} 的 detail_ref 须 {prefix}<{kind}> 形 "
                f"(逗号分隔多个), 非自由文本; 不合: {bad or ['(空)']}",
            )
        return self


class UncertaintyEntry(BaseModel):
    """TransactionEnvelope.uncertainties[] per M-0.4 §2 — informational only (not used by gate)."""

    model_config = _STRICT

    uncertainty_id: str = Field(min_length=1)
    description: str
    related_entity: EntityRef | None = None
    related_obligation_id: str | None = None
    severity: EnvelopeUncertaintySeverity
    suggested_action: EnvelopeUncertaintySuggestedAction


class SelfCheckResult(BaseModel):
    """TransactionEnvelope.self_check.checks_run[] per M-0.4 §2.

    Soft / informational checks (not commit-blocking). See BlockingCheck for
    commit-blocking variant.
    """

    model_config = _STRICT

    check_type: str = Field(min_length=1)
    passed: bool
    detail: str | None = None


class BlockingCheck(BaseModel):
    """T0.4 — DOGFOOD-001-B repair: commit-blocking self_check entry.

    # spec source:
    #   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Cross-cutting Patch L1-checkpoint-audience-separation-1
    #   docs/DOGFOOD-RUN-001-FINDINGS.md DOGFOOD-001-B
    #
    # Unlike `SelfCheckResult` (informational), a BlockingCheck physically
    # blocks the commit gate. M-0.5 commit gate evaluates blocking_checks and
    # rejects the envelope if any entry has status != "passed".
    #
    # Canonical first user: `interview.owner_brief_confirmed` check, evidence
    # references the NatureJudgmentCaptured(decision=brief_confirmed) event_id.

    Invariants:
      - status != "passed" → commit gate MUST reject envelope
      - status == "passed" requires non-empty evidence dict (so audit can verify)
    """

    model_config = _STRICT

    check_id: str = Field(min_length=1)  # e.g. "interview.owner_brief_confirmed"
    status: Literal["pending", "passed", "failed"]
    evidence: dict[str, str] = Field(default_factory=dict)
    detail: str | None = None

    @model_validator(mode="after")
    def enforce_passed_has_evidence(self) -> Self:
        if self.status == "passed" and not self.evidence:
            raise ValueError(
                f"blocking_check check_id={self.check_id} status=passed requires non-empty evidence",
            )
        return self


class SelfCheck(BaseModel):
    """TransactionEnvelope.self_check per M-0.4 §2 + T0.4 blocking_checks extension.

    Two-tier semantics:
      - checks_run[]      — informational soft checks (M-0.4 §2 original)
      - blocking_checks[] — commit-blocking checks (T0.4 — DOGFOOD-001-B)

    `passed` boolean is overall envelope-level pass flag. Backwards compatible:
    envelopes without blocking_checks behave as before. Commit gate first
    evaluates blocking_checks (any non-passed → reject), then falls back to
    `passed` boolean (false → reject).
    """

    model_config = _STRICT

    passed: bool
    checks_run: list[SelfCheckResult] = Field(default_factory=list)
    blocking_checks: list[BlockingCheck] = Field(default_factory=list)


class TransactionEnvelope(BaseModel):
    """M-0.4 §2 TransactionEnvelope canonical schema.

    Invariants enforced at schema layer:
      - read_set non-empty (every run reads at least the task package; M-0.4 §2)
      - write_set non-empty (no-op runs should be TaskRunCompleted outcome=no_change,
        not submitted envelope; M-0.4 §2)
      - active_obligations_declared non-empty IF any obligation injected (cross-check vs
        capsule.final_active_set lives at M-0.5 commit gate per M-0.4 invariant 8)
      - submitted_at is informational; authoritative timestamp from M-0.1 writer

    System-derived vs agent-filled responsibility (M-0.4 §3.1):
      - Agent: patches / self_check / uncertainties / active_obligations_declared
      - System: read_set / write_set / claims (derived by submit wrapper)
      - Capsule injection: capsule_compiled_event_id / resolver_decision_event_id /
        as_of_projection_seq / active_obligations_declared.obligation_id list (M-0.4 §3.1)
    """

    model_config = _STRICT

    # === 身份 ===
    envelope_id: str = Field(min_length=1, pattern=r"^env-[A-Za-z0-9_-]+$")
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    fork_id: str = Field(min_length=1)

    # === 声明层 ===
    read_set: list[ReadEntry] = Field(min_length=1)
    write_set: list[WriteEntry] = Field(min_length=1)
    # RUN-091 R1 (M-0.4 §3.1): write_set is the SYSTEM-derived FACT (git diff / emitted events).
    # declared_write_set preserves what the AGENT declared it intended to write (its --write-node /
    # self-report) when that diverges from the derived fact — the "意图" half of the 4-source
    # reconciliation, so an audit can read declared-vs-actual side by side. Empty when the agent's
    # declaration already matched the derived fact (nothing to reconcile).
    declared_write_set: list[WriteEntry] = Field(default_factory=list)
    claims: list[ClaimEntry] = Field(default_factory=list)
    active_obligations_declared: list[ActiveObligationDeclared] = Field(default_factory=list)
    patches: list[PatchEntry] = Field(default_factory=list)
    uncertainties: list[UncertaintyEntry] = Field(default_factory=list)
    self_check: SelfCheck
    # RUN-029 第0波 ③ — capabilities this commit claims complete. Each triggers the M-0.5
    # done-claim gate (passed semantic-completeness check + no open blocking debt). Empty =
    # ordinary work-in-progress commit (and debt-payoff commits), never blocked.
    completion_claims: list[str] = Field(default_factory=list)

    # === 系统注入层 ===
    capsule_compiled_event_id: str = Field(min_length=1)
    resolver_decision_event_id: str | None = None
    as_of_projection_seq: int = Field(ge=0)

    # === 元信息 ===
    schema_version: SchemaVersion
    submitted_at: datetime  # informational only


__all__ = [
    "ActiveObligationDeclared",
    "BlockingCheck",
    "ClaimEntry",
    "PatchEntry",
    "PatchSupersedeBridge",
    "ReadEntry",
    "SelfCheck",
    "SelfCheckResult",
    "TransactionEnvelope",
    "UncertaintyEntry",
    "WriteEntry",
]
