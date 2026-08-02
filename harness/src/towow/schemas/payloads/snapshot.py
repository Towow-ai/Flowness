"""snapshot category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.3 SnapshotFields (L151..L159)
#     §3.3 (L708..L728) — 2 base snapshot payloads
#     §6.3 Correctable Consolidation invariant (snapshot supersede via new event)
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §2 6 event payload extensions — full payloads in Step 1.h
#
# Patch M-2.2-G (framing patch on M-0.7 §13.7):
#   "maintenance agent skill 定义（§8 接口契约）—— wrapper 实现归 M-3.1, skill 内容归 M-2.2"
#   This is a framing-only refinement, no schema impact. Anchor recorded here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import SnapshotSupersedeNoveltyType, SnapshotType

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class SnapshotCreatedPayload(BaseModel):
    """M-0.1 §3.3 SnapshotCreated — base_classification=immutable_truth.

    Snapshot itself can never be dropped — it is the baseline for incremental derivation.
    """

    model_config = _STRICT

    event_offset: int = Field(ge=0)
    projection_state_hash: str = Field(min_length=1)
    snapshot_type: SnapshotType
    covered_projections: list[str] = Field(min_length=1)


class SnapshotSupersedeNovelty(BaseModel):
    """M-0.7 §2.2 SnapshotSuperseded.novelty — why the superseded snapshot's content is wrong.

    no_new_information is pinned to False (Literal[False]) — a SnapshotSuperseded is by
    definition a correction that carries novelty; a supersede claiming "no new information" is
    contradictory (§2.2 invariant). The optional detail fields mirror the 3 novelty_type values:
    corrected_error / missing_projection / hash_mismatch.
    """

    model_config = _STRICT

    novelty_type: SnapshotSupersedeNoveltyType
    no_new_information: Literal[False] = False  # §2.2 invariant — must be false
    corrected_error: str | None = None
    missing_projection: str | None = None
    hash_mismatch: str | None = None


class SnapshotSupersededPayload(BaseModel):
    """SnapshotSuperseded payload — M-0.1 §3.3 minimal form + M-0.7 §2.2 full enrichment.

    Backward compatible: the M-0.1 §3.3 producer path supplies only event_offset +
    projection_state_hash (both still required). M-0.7 §2.2 adds the supersede semantics —
    which snapshot is superseded (superseded_snapshot_event_id, also carried on the EventIntent
    supersede block), at what offset, the novelty block (why it was wrong), and the optional
    replacement snapshot. The §2.2 fields default to None so existing minimal producers keep
    validating.

    §2.2 semantic boundary: SnapshotSuperseded is ONLY for "this snapshot's content is wrong"
    (e.g. reconstructability sampling failed) — NOT for "create a new snapshot" (that is a plain
    SnapshotCreated; multi-snapshot history is retained, §4.2 / §11.4).
    """

    model_config = _STRICT

    event_offset: int = Field(ge=0)
    projection_state_hash: str = Field(min_length=1)
    # ─── M-0.7 §2.2 enrichment (optional for M-0.1 minimal-form backward compat) ───
    superseded_snapshot_event_id: str | None = None
    superseded_at_event_offset: int | None = Field(default=None, ge=0)
    novelty: SnapshotSupersedeNovelty | None = None
    replacement_snapshot_event_id: str | None = None


__all__ = [
    "SnapshotCreatedPayload",
    "SnapshotSupersedeNovelty",
    "SnapshotSupersededPayload",
]
