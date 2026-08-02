"""ConsumerCursor + retention watermark (M-0.1a Patch 4).

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   附录 B Patch 4 §4.1-4.3 ConsumerCursor + retention_watermark
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=False)


class ConsumerCursor(BaseModel):
    """Per-consumer cursor per M-0.1a Patch 4 §4.1."""

    model_config = _STRICT

    consumer_id: str = Field(min_length=1)
    consumer_type: str = Field(min_length=1)
    interested_event_categories: list[str] | None = None
    interested_event_types: list[str] | None = None
    last_durable_processed_seq: int = Field(ge=0)
    updated_at: str  # iso8601


class CursorStore:
    """File-backed cursor store at .towow/events/cursors/."""

    def __init__(self, cursors_dir: Path) -> None:
        self._dir = cursors_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        consumer_id: str,
        consumer_type: str,
        *,
        interested_event_categories: list[str] | None = None,
        interested_event_types: list[str] | None = None,
    ) -> ConsumerCursor:
        cursor = ConsumerCursor(
            consumer_id=consumer_id,
            consumer_type=consumer_type,
            interested_event_categories=interested_event_categories,
            interested_event_types=interested_event_types,
            last_durable_processed_seq=0,
            updated_at=datetime.now(tz=UTC).isoformat(),
        )
        self._write(cursor)
        return cursor

    def advance(self, consumer_id: str, new_seq: int) -> ConsumerCursor:
        cursor = self.get(consumer_id)
        if cursor is None:
            raise KeyError(f"consumer not registered: {consumer_id}")
        if new_seq < cursor.last_durable_processed_seq:
            raise ValueError(
                f"cursor advance backwards forbidden: "
                f"{cursor.last_durable_processed_seq} → {new_seq}",
            )
        cursor.last_durable_processed_seq = new_seq
        cursor.updated_at = datetime.now(tz=UTC).isoformat()
        self._write(cursor)
        return cursor

    def get(self, consumer_id: str) -> ConsumerCursor | None:
        path = self._dir / f"{consumer_id}.json"
        if not path.exists():
            return None
        return ConsumerCursor.model_validate_json(path.read_text(encoding="utf-8"))

    def all_cursors(self) -> list[ConsumerCursor]:
        results = []
        for path in self._dir.glob("*.json"):
            results.append(ConsumerCursor.model_validate_json(path.read_text(encoding="utf-8")))
        return results

    def retention_watermark(self) -> int:
        """min(last_durable_processed_seq) across all active consumers per Patch 4 §4.2."""
        cursors = self.all_cursors()
        if not cursors:
            return 0
        return min(c.last_durable_processed_seq for c in cursors)

    def retention_watermark_with_default(self, default: int) -> int:
        """Patch4 §4.2 watermark, returning ``default`` when no consumer is registered here.

        ``retention_watermark`` returns 0 for an empty store, which a caller combining it with
        another watermark (M-0.7 §3.2 Layer 1) would wrongly read as "everything pinned". This
        variant returns the supplied default (e.g. the projection's own watermark) so a store with
        no *extra* registered consumers does not over-restrict the GC bound — it only ever lowers
        the bound when a registered consumer actually lags below ``default``.
        """
        cursors = self.all_cursors()
        if not cursors:
            return default
        return min(c.last_durable_processed_seq for c in cursors)

    def _write(self, cursor: ConsumerCursor) -> None:
        path = self._dir / f"{cursor.consumer_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cursor.model_dump(), indent=2), encoding="utf-8")
        tmp.replace(path)
