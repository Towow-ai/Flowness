"""EventRecord — writer-stamped immutable record.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     附录 B Patch 1 §1.3 (L1535..L1556) EventRecord schema
#     §5.2 写入并发语义 — sequence_number 全局严格递增
#     §7.1 物理布局 — path A 含 transaction_id / batch_id; path B 不含
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
)
from towow.schemas.event_intent import ProvenanceHint, Subject, Supersede
from towow.schemas.version import SchemaVersion


class Provenance(ProvenanceHint):
    """Writer-confirmed provenance per M-0.1a Patch 1 §1.3.

    Extends ProvenanceHint with optional writer_id — writer may augment
    "writer can add writer_id" (spec §1.3 wording, no other writer-side augmentation).
    """

    writer_id: str | None = None


class EventRecord(BaseModel):
    """Writer-stamped event record — immutable, append-only.

    Path A (commit gate route): transaction_id / batch_id / batch_position all required.
    Path B (direct write):      transaction_id / batch_id / batch_position all None.

    Writer assigns event_id / sequence_number / timestamp / record_hash unconditionally;
    preserves local_intent_id for producer receipt match (M-0.1a Patch 1 §1.3).
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # === Writer-assigned ===
    event_id: str = Field(min_length=5, pattern=r"^evt-[A-Za-z0-9_-]+$")
    sequence_number: int = Field(ge=0)
    timestamp: datetime
    transaction_id: str | None = None
    batch_id: str | None = None
    batch_position: int | None = Field(default=None, ge=0)
    record_hash: str = Field(min_length=1)

    # === Preserved from EventIntent ===
    local_intent_id: str = Field(min_length=1)
    event_type: EventType
    event_category: EventCategory
    payload: dict[str, Any]
    provenance: Provenance
    base_classification: BaseClassification
    supersede: Supersede
    subjects: list[Subject] = Field(min_length=1)
    schema_version: SchemaVersion

    @model_validator(mode="after")
    def enforce_path_consistency(self) -> Self:
        """Path A: all three batch fields set; Path B: all three None (M-0.1a Patch 1 §1.4)."""
        has_tx = self.transaction_id is not None
        has_batch = self.batch_id is not None
        has_pos = self.batch_position is not None
        all_set = has_tx and has_batch and has_pos
        none_set = not (has_tx or has_batch or has_pos)
        if not (all_set or none_set):
            raise ValueError(
                "transaction_id / batch_id / batch_position must all be set (path A) "
                f"or all None (path B); got transaction_id={self.transaction_id!r}, "
                f"batch_id={self.batch_id!r}, batch_position={self.batch_position!r}",
            )
        return self


__all__ = [
    "EventRecord",
    "Provenance",
]
