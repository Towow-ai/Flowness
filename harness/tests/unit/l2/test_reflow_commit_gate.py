from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from towow.l0.event_log import EventLog
from towow.l2.reflow_commit_gate import emit_finding_via_gate
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede


def _finding_intent() -> EventIntent:
    return EventIntent(
        local_intent_id="reflow-gate-positive",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload={
            "finding_id": "f-reflow-gate-positive",
            "finding_kind": "system_governance_defect",
            "severity": "major",
            "risk_surface": "reflow-recovery",
            "lifecycle_state": "created",
            "description": "portable reflow gate positive-path fixture",
            "detection_method": "automated_rule",
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id="reflow-gate-test",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.FINDING,
                entity_id="f-reflow-gate-positive",
                role=SubjectRole.PRIMARY,
            )
        ],
        schema_version="1.0.0",
    )


def test_reflow_gate_commits_real_finding_through_path_a(tmp_path: Path) -> None:
    towow_dir = tmp_path / ".towow"
    towow_dir.mkdir()
    event_log = EventLog(towow_dir / "events.log")

    event_id = emit_finding_via_gate(
        event_log,
        _finding_intent(),
        towow_dir,
        closure="portable-reflow-gate-positive",
    )

    assert event_id is not None
    assert any(
        record.event_id == event_id and record.event_type is EventType.FINDING_CREATED
        for record in event_log.all_records()
    )
    assert event_log.all_records()[-1].event_type is EventType.COMMIT_ACCEPTED


def test_reflow_gate_rejects_wrong_intent_without_writing(tmp_path: Path) -> None:
    towow_dir = tmp_path / ".towow"
    towow_dir.mkdir()
    event_log = EventLog(towow_dir / "events.log")
    wrong_intent = SimpleNamespace(
        event_type=EventType.FINDING_RESOLVED,
        subjects=[object()],
    )

    assert emit_finding_via_gate(
        event_log,
        wrong_intent,
        towow_dir,
        closure="portable-reflow-gate-negative",
    ) is None
    assert event_log.all_records() == []
