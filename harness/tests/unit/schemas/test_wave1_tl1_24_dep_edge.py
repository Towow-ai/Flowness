"""波1 T-L1-24: TaskDependencyEdgeAdded 6 dep-type + evidence/strength (M-1.3 §3.2 / Patch W).

源表 done_criteria: plan-dep-add 支持 6 种 dep-type, 缺 evidence/strength 被拒 (现仅 3 种)。
spec ground truth M-1.3 §3.2 L117-119:
  dependency_type ∈ {data_dependency, ordering, semantic_dependency, resource_conflict,
    review_dependency, state_machine_transition_dependency}  (6)
  evidence: string (为什么有这条依赖)
  strength: enum [hard | medium]
"""

from __future__ import annotations

import pytest

from towow.schemas.enums import EventType, TaskDependencyStrength, TaskDependencyType
from towow.schemas.payloads.registry import conforms


def test_six_dependency_types_present() -> None:
    vals = {t.value for t in TaskDependencyType}
    assert vals == {
        "data_dependency",
        "ordering",
        "semantic_dependency",
        "resource_conflict",
        "review_dependency",
        "state_machine_transition_dependency",
    }


def test_dependency_strength_enum() -> None:
    assert {s.value for s in TaskDependencyStrength} == {"hard", "medium"}


def _payload(after_extra: dict[str, object]) -> dict[str, object]:
    return {
        "target_entity_type": "task_edge",
        "transition_type": "created",
        "after_state": {
            "source_task_id": "T1",
            "target_task_id": "T2",
            "dependency_type": "semantic_dependency",
            **after_extra,
        },
    }


def test_payload_requires_evidence_and_strength() -> None:
    # 完整 (含 evidence + strength) → conforms
    assert conforms(
        EventType.TASK_DEPENDENCY_EDGE_ADDED,
        _payload({"evidence": "T1 写 concept-x, T2 读 concept-x", "strength": "hard"}),
    ) is None
    # 缺 evidence → 不 conform (被拒)
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_ADDED, _payload({"strength": "hard"})) is not None
    # 缺 strength → 不 conform (被拒)
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_ADDED, _payload({"evidence": "e"})) is not None


@pytest.mark.parametrize(
    "dep_type",
    [
        "data_dependency", "ordering", "semantic_dependency",
        "resource_conflict", "review_dependency", "state_machine_transition_dependency",
    ],
)
def test_all_six_dep_types_validate(dep_type: str) -> None:
    p = {
        "target_entity_type": "task_edge",
        "transition_type": "created",
        "after_state": {
            "source_task_id": "T1", "target_task_id": "T2",
            "dependency_type": dep_type, "evidence": "e", "strength": "medium",
        },
    }
    assert conforms(EventType.TASK_DEPENDENCY_EDGE_ADDED, p) is None
