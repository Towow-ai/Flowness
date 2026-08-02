"""Daemon base class + DaemonRunCompleted event production.

# spec source:
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md §3.2
#     DaemonRunCompleted (Patch M-2.1-A) — shared across M-2.1/M-2.2/M-2.3 daemons
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md §7
#     F-11 orchestrator daemon polling form
#
# E.5 implementation: each daemon subclasses Daemon and implements `_scan()` returning
# DaemonOutcomeRecord. Base class handles DaemonRunCompleted event emission with
# observability fields (scanned_count / findings_produced / run_duration_ms / outcome).
"""

from __future__ import annotations

import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    DaemonName,
    DaemonOutcome,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog


_LAST_HEARTBEAT_AT: dict[str, float] = {}  # B6-c: per-process 心跳节流表 (daemon_name → monotonic)


@dataclass
class DaemonOutcomeRecord:
    """Return value of Daemon._scan()."""

    scanned_count: int = 0
    findings_produced: int = 0
    cascade_events_produced: int | None = None
    outcome: DaemonOutcome = DaemonOutcome.COMPLETED
    partial_failure_reason: str | None = None
    side_event_ids: list[str] = field(default_factory=list)


class Daemon(ABC):
    """Base class for all M-2.x + M-3.1 daemons.

    Subclasses implement `_scan()` returning DaemonOutcomeRecord; base class wraps
    timing + DaemonRunCompleted event emission + provenance.
    """

    daemon_name: DaemonName  # must be set by subclass
    # B6-c (PARALLEL-EXEC-FIX): 高频轮询 daemon 置 True — 空转 run 不 emit 心跳。
    # 病根: orchestrator ~6s 一轮, 每轮 emit DaemonRunCompleted → 自己下轮又消费它 →
    # watermark 追自己尾巴 + 账本灌水 (实测 2 万+条)。要事 (findings/cascade/非COMPLETED/
    # 有 decision) 必发; 纯空转按间隔发 (默认 300s, TOWOW_HEARTBEAT_INTERVAL_S 可调)。
    heartbeat_throttled: bool = False

    def __init__(self, event_log: EventLog) -> None:
        self._event_log = event_log

    def _run_notable(self, outcome_record: DaemonOutcomeRecord) -> bool:
        """本轮有没有值得留痕的事 (子类可扩展, 如 orchestrator 把 decisions 非空算 notable)。"""
        return (
            (outcome_record.findings_produced or 0) > 0
            or (outcome_record.cascade_events_produced or 0) > 0
            or outcome_record.outcome is not DaemonOutcome.COMPLETED
        )

    def _heartbeat_due(self) -> bool:
        raw = os.environ.get("TOWOW_HEARTBEAT_INTERVAL_S", "").strip()
        try:
            interval = float(raw) if raw else 300.0
        except ValueError:
            interval = 300.0
        last = _LAST_HEARTBEAT_AT.get(self.daemon_name.value)
        return last is None or (time.monotonic() - last) >= interval

    @abstractmethod
    def _scan(self) -> DaemonOutcomeRecord:
        """Subclass implementation: scan event log/state and produce findings/events.

        Should be pure-ish — side-effects (event production) happen via self._produce_*
        helpers or directly via self._event_log. Returns observability record consumed
        by the base run() loop for DaemonRunCompleted emission.
        """

    def run_once(self) -> str:
        """One-shot run. Returns the event_id of the emitted DaemonRunCompleted."""
        started = time.perf_counter()
        try:
            outcome = self._scan()
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return self._emit_daemon_run_completed(
                DaemonOutcomeRecord(
                    outcome=DaemonOutcome.FAILED,
                    partial_failure_reason=f"unhandled exception: {exc!r}",
                ),
                duration_ms,
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        if self.heartbeat_throttled and not self._run_notable(outcome) and not self._heartbeat_due():
            return ""  # B6-c: 空转心跳降频 — 不灌账本、不自喂 (返回空 = 本轮未 emit)
        _LAST_HEARTBEAT_AT[self.daemon_name.value] = time.monotonic()
        return self._emit_daemon_run_completed(outcome, duration_ms)

    def _emit_daemon_run_completed(
        self,
        outcome_record: DaemonOutcomeRecord,
        run_duration_ms: int,
    ) -> str:
        """Emit DaemonRunCompleted (path B; M-2.1 §3.2 Patch M-2.1-A)."""
        daemon_run_id = f"dr-{self.daemon_name.value}-{uuid.uuid4().hex[:8]}"
        payload = {
            "target_entity_type": "daemon_run",
            "transition_type": "created",
            "after_state": {
                "daemon_run_id": daemon_run_id,
                "daemon_name": self.daemon_name.value,
                "scanned_count": outcome_record.scanned_count,
                "findings_produced": outcome_record.findings_produced,
                "cascade_events_produced": outcome_record.cascade_events_produced,
                "run_duration_ms": run_duration_ms,
                "outcome": outcome_record.outcome.value,
                "partial_failure_reason": outcome_record.partial_failure_reason,
            },
        }
        intent = EventIntent(
            local_intent_id=f"drun-{daemon_run_id}",
            event_type=EventType.DAEMON_RUN_COMPLETED,
            event_category=EventCategory.STATE_TRANSITION,
            payload=payload,
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.DAEMON.value,
                actor_id=self.daemon_name.value,
                daemon_name=self.daemon_name.value,
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id=f"daemon:{self.daemon_name.value}",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )
        # DaemonRunCompleted is state_transition — should walk path A in real
        # implementation. For E.5 stub we use NodeTouched-equivalent wrapping via the
        # cli stub-rewrite logic? Actually DaemonRunCompleted is state_transition; per
        # M-0.1 §5.1 path B allowed types it is not directly allowed. To preserve event
        # type semantics in E.5 scaffolding, we stamp+write via a single-event batch
        # variant (no envelope, just sentinel-equivalent direct write). For simplicity
        # we degrade event_type to NodeTouched (path B allowed) and embed daemon_name
        # in payload.kind — same pattern as CLI stub rewrite.
        return self._write_via_path_b_or_stub(intent, daemon_run_id)

    def _write_via_path_b_or_stub(
        self,
        intent: EventIntent,
        daemon_run_id: str,
    ) -> str:
        """E.5 stub: degrade non-path-B event_type to NodeTouched wrapping."""
        original_event_type = intent.event_type
        if original_event_type is EventType.DAEMON_RUN_COMPLETED:
            stub_intent = EventIntent(
                local_intent_id=intent.local_intent_id,
                event_type=EventType.NODE_TOUCHED,
                event_category=EventCategory.ENVELOPE,
                payload={
                    "target_entity_type": "concept",
                    "target_entity_id": f"daemon-run:{daemon_run_id}",
                    "touch_type": "write",
                    "kind": "DaemonRunCompleted",
                    "stub_original_payload": intent.payload,
                },
                provenance_hint=intent.provenance_hint,
                base_classification=BaseClassification.DISCARDABLE_NOISE,
                supersede=intent.supersede,
                subjects=intent.subjects,
                schema_version=intent.schema_version,
            )
            rec = self._event_log.write_direct(stub_intent)
            return rec.event_id
        rec = self._event_log.write_direct(intent)
        return rec.event_id


class DaemonRegistry:
    """Daemon registry — instantiated by orchestrator / CLI `towow daemon run-once`."""

    def __init__(self) -> None:
        self._daemons: dict[DaemonName, type[Daemon]] = {}

    def register(self, daemon_cls: type[Daemon]) -> None:
        self._daemons[daemon_cls.daemon_name] = daemon_cls

    def get(self, name: DaemonName) -> type[Daemon] | None:
        return self._daemons.get(name)

    def all_names(self) -> list[DaemonName]:
        return list(self._daemons.keys())


__all__ = ["Daemon", "DaemonOutcomeRecord", "DaemonRegistry"]
