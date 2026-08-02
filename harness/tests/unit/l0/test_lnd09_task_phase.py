"""T-LND-09 (INV-B2-4 task-has-phase): TaskNodeCreated.after_state 补 phase 字段
(design|implementation|review), 使阶段先后可表达 + 机器校验 (review_required bool 表达不了
"谁先于谁")。phase 是 T-LND-10 (review_plan 先于 implementation) 依赖边的载体。

钉住:
  - TaskNodeCreatedAfter 接受 phase (enum) + 向后兼容 None
  - task_graph 投影暴露 phase
  - _default_phase_for_task_type 派生 (review→review / design_change→design / 其余→implementation)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.cli.main import _default_phase_for_task_type
from towow.l0.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    TaskPhase,
    TaskType,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.payloads.state_transition import TaskNodeCreatedAfter

if TYPE_CHECKING:
    from pathlib import Path


def test_task_node_after_accepts_phase() -> None:
    a = TaskNodeCreatedAfter(task_id="T1", task_type=TaskType.REVIEW, description="d", phase=TaskPhase.REVIEW)
    assert a.phase is TaskPhase.REVIEW


def test_task_node_after_phase_backward_compat_none() -> None:
    a = TaskNodeCreatedAfter(task_id="T1", task_type=TaskType.CODE_CHANGE, description="d")
    assert a.phase is None


def test_default_phase_derivation() -> None:
    assert _default_phase_for_task_type("review") == "review"
    assert _default_phase_for_task_type("design_change") == "design"
    for tt in ("code_change", "fix", "doc_change", "maintain"):
        assert _default_phase_for_task_type(tt) == "implementation"


def test_task_graph_projection_exposes_phase(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    rec = EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=1,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id="li-1",
        event_type=EventType.TASK_NODE_CREATED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": {"task_id": "T-R", "task_type": "review", "phase": "review", "description": "d"}},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="T-R", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    store._apply(rec)
    node = next(n for n in store.read("task_graph")["nodes"] if n["task_id"] == "T-R")
    assert node["phase"] == "review"
