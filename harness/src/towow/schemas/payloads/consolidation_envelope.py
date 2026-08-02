"""M-0.7 §6.3 ConsolidationEnvelope schema.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §6.1 consolidation 复用 runtime protocol (O-06 §3.5) — consolidation run 是普通 run, 走
#          capsule + envelope + commit gate; M-0.7 不是调度器, 是 verify provider (§11.7)
#     §6.3 ConsolidationEnvelope schema — read_set / write_set / 三个 compaction invariant 声明 /
#          patches (patch_type=consolidation_event, §10.3 Patch L) / claims (lookback_window +
#          consolidation_proposal)
#     §6.4 verify_consolidation_invariants(envelope) — M-0.5 commit gate 调用入口 (Patch M)
#
# WHY a typed model (not the generic TransactionEnvelope): consolidation reuses the runtime
# protocol, but verify_consolidation_invariants needs a STRUCTURED view of exactly the §6.3 shape
# (which segment is read, which digest/snapshot is written, the three compaction invariants the run
# declared it maintained, the digest proposal with provenance_refs). This model is that verify-
# provider input contract — it pins the §6.3 fields so the verify function reads them type-safely
# rather than digging through an opaque dict. A consolidation run fills it; M-0.5 hands it to
# verify_consolidation_invariants_full (consolidation_verify.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import ObligationDeclaredStatus, ProvenanceRefRelevance, SceneType
from towow.schemas.payloads.consolidation import LookbackWindow

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)

# §6.3 active_obligations_declared — the three compaction invariants a consolidation run must
# declare maintained (O-06: reconstructability / digest-provenance / correctable-consolidation).
OBL_RECONSTRUCTABILITY = "obl-snapshot-reconstructability"
OBL_DIGEST_PROVENANCE = "obl-digest-provenance"
OBL_CORRECTABLE_CONSOLIDATION = "obl-correctable-consolidation"
COMPACTION_INVARIANT_OBLIGATIONS = (
    OBL_RECONSTRUCTABILITY,
    OBL_DIGEST_PROVENANCE,
    OBL_CORRECTABLE_CONSOLIDATION,
)


class ConsolidationReadEntry(BaseModel):
    """§6.3 read_set[] — the event segment + snapshot a consolidation run is based on."""

    model_config = _STRICT

    entity_type: Literal["event_segment", "snapshot"]
    entity_id: str = Field(min_length=1)  # "seg-{start}-{end}" / "snapshot-{seq}"


class ConsolidationWriteEntry(BaseModel):
    """§6.3 write_set[] — the digest / new snapshot / segment a consolidation run will write."""

    model_config = _STRICT

    entity_type: Literal["digest", "snapshot", "segment"]
    entity_id: str = Field(min_length=1)  # "digest-{hash}" / "snapshot-{new_seq}" / "seg-{range}"


class CompactionInvariantDeclared(BaseModel):
    """§6.3 active_obligations_declared[] — one of the three compaction invariants.

    obligation_id must be one of COMPACTION_INVARIANT_OBLIGATIONS; status is the run's declared
    status (verify_consolidation_invariants is what actually checks it — a declaration is not proof).
    """

    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    status: ObligationDeclaredStatus


class ConsolidationProvenanceRef(BaseModel):
    """§6.3 consolidation_proposal.provenance_refs[] — digest references back to source events.

    Same shape as consolidation.ProvenanceRef (O-06 Inv2) — the digest's references to the events
    it summarizes; verify_consolidation_invariants (inv2) checks each is still hot/cold accessible.
    """

    model_config = _STRICT

    event_id: str = Field(min_length=1)
    relevance: ProvenanceRefRelevance


class ConsolidationProposal(BaseModel):
    """§6.3 claims.consolidation_proposal — the digest the agent proposes (verify input)."""

    model_config = _STRICT

    digest_hash: str = Field(min_length=1)
    digest_storage_path: str = Field(min_length=1)
    provenance_refs: list[ConsolidationProvenanceRef] = Field(default_factory=list)
    # the [start_seq, end_seq] the digest folds — verify inv1/inv2 scan this range.
    segment_start_seq: int = Field(ge=0)
    segment_end_seq: int = Field(ge=0)


class ConsolidationEnvelope(BaseModel):
    """M-0.7 §6.3 ConsolidationEnvelope — a consolidation run's commit envelope (verify input).

    scene_type is pinned to consolidation (§10.3 Patch K). The three compaction invariants must all
    be declared (active_obligations_declared) — verify_consolidation_invariants_full checks each.
    write_set names the snapshot whose reconstructability is checked (inv1) + the digest whose
    provenance is checked (inv2); supersede_targets names any SnapshotSuperseded / DigestSuperseded
    the batch produces (inv3 correctability — must point at valid existing event_ids).
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    scene_type: Literal[SceneType.CONSOLIDATION] = SceneType.CONSOLIDATION
    capsule_compiled_event_id: str = Field(min_length=1)
    read_set: list[ConsolidationReadEntry] = Field(min_length=1)
    write_set: list[ConsolidationWriteEntry] = Field(min_length=1)
    active_obligations_declared: list[CompactionInvariantDeclared] = Field(min_length=1)
    lookback_window: LookbackWindow
    consolidation_proposal: ConsolidationProposal
    # write_set entity_ids that are SUPERSEDING events (point back at superseded snapshot/digest
    # event_ids) — inv3 correctability checks each superseded ref resolves to a real event.
    snapshot_event_id: str | None = None  # the SnapshotCreated whose reconstructability inv1 checks
    digest_event_id: str | None = None  # the CrossRunConsolidationCommitted whose provenance inv2 checks
    superseded_snapshot_event_ids: list[str] = Field(default_factory=list)
    superseded_digest_event_ids: list[str] = Field(default_factory=list)


__all__ = [
    "COMPACTION_INVARIANT_OBLIGATIONS",
    "OBL_CORRECTABLE_CONSOLIDATION",
    "OBL_DIGEST_PROVENANCE",
    "OBL_RECONSTRUCTABILITY",
    "CompactionInvariantDeclared",
    "ConsolidationEnvelope",
    "ConsolidationProposal",
    "ConsolidationProvenanceRef",
    "ConsolidationReadEntry",
    "ConsolidationWriteEntry",
]
