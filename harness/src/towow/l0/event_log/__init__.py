"""M-0.1 Event Log — single source of truth for v3 events.

Public surface for downstream consumers (M-0.2 / M-0.3 / M-0.5 / M-0.6 / M-0.7 / L1 skills).

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
"""

from towow.l0.event_log.cursor import ConsumerCursor, CursorStore
from towow.l0.event_log.event_log import EventLog, LedgerMergeResult, SchemaEvolutionError
from towow.l0.event_log.index import EventIndex, EventLogIndexStore
from towow.l0.event_log.lease import (  # T-L0-20 (波1) — M-0.3 Patch 5 retention lease
    LeaseReason,
    RetentionLease,
    RetentionLeaseStore,
)
from towow.l0.event_log.segments import (  # T-L0-04 (波1) — §7.1/§7.2 segment rotation
    append_raw_event_line,
    iter_raw_event_lines,
    ordered_segment_paths,
)

__all__ = [
    "ConsumerCursor",
    "CursorStore",
    "EventIndex",
    "EventLog",
    "EventLogIndexStore",
    "LeaseReason",
    "LedgerMergeResult",
    "RetentionLease",
    "RetentionLeaseStore",
    "SchemaEvolutionError",
    "append_raw_event_line",
    "iter_raw_event_lines",
    "ordered_segment_paths",
]
