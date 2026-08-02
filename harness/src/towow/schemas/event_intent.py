"""EventIntent — producer-submitted event before writer stamping.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     附录 B Patch 1 §1.2 (L1492..L1527) EventIntent canonical schema
#     §2.1 EventBase 共有元字段 (L60..L100) — supersede 协议 + provenance shape
#     §2.1 supersede + O-12 cross-cutting — novelty required when is_supersede=true
#
# Patch M-0.1a-1: layered EventIntent / EventRecord — producer 不填 event_id /
# sequence_number / timestamp / transaction_id / batch_id / batch_position / record_hash.
# All writer-assigned fields land in EventRecord (schemas/event_record.py).
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
)
from towow.schemas.version import SchemaVersion


class ProvenanceHint(BaseModel):
    """Producer-supplied provenance per M-0.1a Patch 1 §1.2 + E.1.1 Conflict 2 边界.

    Writer confirms and may augment with writer_id before promoting into
    EventRecord.provenance (same shape, no schema divergence).

    Provenance 语义边界 per E.1.1 LEDGER Conflict 2 (enforced by model_validator):
      actor_type=daemon          → daemon_name required
      actor_type=agent_session   → session_id + skill_id required
      actor_type=agent_fork      → parent_session_id + fork_name required
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    actor_type: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    session_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    causation_event_id: str | None = None
    correlation_id: str | None = None
    # E.1.1 Conflict 2: 9 canonical actor_type values + provenance 语义边界
    daemon_name: str | None = None  # required when actor_type=daemon
    skill_id: str | None = None  # required when actor_type=agent_session (with session_id)
    parent_session_id: str | None = None  # required when actor_type=agent_fork (with fork_name)
    fork_name: str | None = None  # required when actor_type=agent_fork (with parent_session_id)

    @model_validator(mode="after")
    def enforce_actor_type_boundary(self) -> Self:
        """E.1.1 LEDGER Conflict 2: actor_type 决定必填的 provenance 字段。"""
        if self.actor_type == ActorType.DAEMON.value and self.daemon_name is None:
            raise ValueError(
                "actor_type=daemon requires daemon_name (e.g. 'm21.cascade_daemon')",
            )
        if self.actor_type == ActorType.AGENT_SESSION.value:
            missing = [
                name
                for name, val in (("session_id", self.session_id), ("skill_id", self.skill_id))
                if val is None
            ]
            if missing:
                raise ValueError(
                    f"actor_type=agent_session requires non-None: {missing}",
                )
        if self.actor_type == ActorType.AGENT_FORK.value:
            missing = [
                name
                for name, val in (
                    ("parent_session_id", self.parent_session_id),
                    ("fork_name", self.fork_name),
                )
                if val is None
            ]
            if missing:
                raise ValueError(
                    f"actor_type=agent_fork requires non-None: {missing}",
                )
        return self


class Supersede(BaseModel):
    """supersede protocol per M-0.1 §2.1 EventBase + O-12 cross-cutting.

    Invariant (O-12 + Patch 5 §5.1): is_supersede=true ↔
      superseded_event_id / novelty / novelty_type all required;
    is_supersede=false ↔ all three must be None.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    is_supersede: bool
    superseded_event_id: str | None = None
    novelty: str | None = None
    novelty_type: SupersedeNoveltyType | None = None  # E.1.1 Conflict 5: event-level

    @model_validator(mode="after")
    def enforce_supersede_consistency(self) -> Self:
        if self.is_supersede:
            missing = [
                name
                for name, val in (
                    ("superseded_event_id", self.superseded_event_id),
                    ("novelty", self.novelty),
                    ("novelty_type", self.novelty_type),
                )
                if val is None
            ]
            if missing:
                raise ValueError(
                    f"supersede.is_supersede=true requires non-None: {missing}",
                )
        else:
            unexpected = [
                name
                for name, val in (
                    ("superseded_event_id", self.superseded_event_id),
                    ("novelty", self.novelty),
                    ("novelty_type", self.novelty_type),
                )
                if val is not None
            ]
            if unexpected:
                raise ValueError(
                    f"supersede.is_supersede=false forbids: {unexpected}",
                )
        return self


class Subject(BaseModel):
    """EventIntent.subjects[] per M-0.1a Patch 3 §3.1.

    Unified indexing field — "this event acts on whom".
    Used by 15 indexes (M-0.1a Patch 3 §3.2) — entity.idx / obligation.idx etc.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)
    role: SubjectRole


class EventIntent(BaseModel):
    """Producer-submitted event intent (before writer stamps event_id / sequence_number).

    M-0.1a Patch 1 §1.2 — producer = agent_fork / commit_gate / capsule_assembler /
    snapshot_module / resolver / system / nature / daemon / agent_session.

    Writer-assigned fields are intentionally excluded — those land in EventRecord:
      event_id / sequence_number / timestamp / transaction_id / batch_id /
      batch_position / record_hash.

    Invariant (§2.1 + Patch 1 §1.2):
      - subjects ≥ 1 (event must declare at least one acted-on entity for indexing)
      - supersede sub-object always present (is_supersede=false for non-supersede events)
      - payload is opaque dict here — concrete per-category payload schemas land in Step 1.c
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    local_intent_id: str = Field(min_length=1)
    event_type: EventType
    event_category: EventCategory
    payload: dict[str, Any]
    provenance_hint: ProvenanceHint
    base_classification: BaseClassification
    supersede: Supersede
    subjects: list[Subject] = Field(min_length=1)
    schema_version: SchemaVersion


__all__ = [
    "EventIntent",
    "ProvenanceHint",
    "Subject",
    "Supersede",
]
