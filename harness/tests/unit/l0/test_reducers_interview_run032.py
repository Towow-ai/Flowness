"""T-L1-06 (RUN-032) 采访侧 3 projection reducers — information_need_graph /
interview_progress / interview_brief_index.

External-observable done_criteria (LANDING-PLAN-TASKS T-L1-06):
  .towow/graph/ 出现 information_need_graph.json 等 3 文件且内容非空, rebuild 回填非空;
  reducer 幂等 (重放 state 一致).

Reducers exercised through ProjectionStore._apply with hand-built EventRecords (Patch R flat
payload shape, M-1.1 §5.2), the established reducer-test pattern (cf. test_reducers_batch0b).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path

_SID = "sess-interview-run032"


def _record(event_type: EventType, payload: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.INFORMATION_NEED, entity_id="n", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _created(need_id: str, *, source: str, red_line: bool, reason: str | None = None) -> dict[str, object]:
    return {
        "target_entity_type": "information_need",
        "transition_type": "created",
        "need_id": need_id,
        "description": f"need {need_id}",
        "source": source,
        "red_line": red_line,
        "red_line_reason": reason,
        "downstream_impact": ["engineering_design"],
        "dependencies": [],
        "session_id": _SID,
    }


def _status_changed(need_id: str, *, old: str, new: str) -> dict[str, object]:
    return {
        "target_entity_type": "information_need",
        "transition_type": "modified",
        "need_id": need_id,
        "old_status": old,
        "new_status": new,
        "nature_original_quote": "阈值定 100 QPS" if new == "resolved" else None,
        "meaning_interpretation": None,
        "resolution_method": "nature_answered" if new == "resolved" else None,
        "session_id": _SID,
    }


def _brief(brief_id: str, *, supersedes: str | None = None) -> dict[str, object]:
    return {
        "kind": "InterviewBriefPublished",
        "target_entity_type": "interview_brief",
        "brief_id": brief_id,
        "session_id": _SID,
        "goal_completion_condition": "done when X observable",
        "concept_seeds_created": ["concept-a"],
        "supersedes": supersedes,
    }


_CREATED = EventType.INFORMATION_NEED_CREATED
_CHANGED = EventType.INFORMATION_NEED_STATUS_CHANGED
_BRIEF = EventType.INTERVIEW_BRIEF_PUBLISHED


# ═══ information_need_graph reducer ═══════════════════════════════════════════


def test_information_need_graph_materializes_nodes(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(_CREATED, _created("INF-1", source="nature_unique", red_line=True, reason="改方向"), 1))
    store._apply(_record(_CREATED, _created("INF-2", source="ai_reachable", red_line=False), 2))
    store._apply(_record(_CHANGED, _status_changed("INF-2", old="pending", new="resolved"), 3))

    ing = store.read("information_need_graph")
    assert ing is not None
    sess = ing["sessions"][_SID]  # type: ignore[index]
    nodes = {n["need_id"]: n for n in sess["nodes"]}  # type: ignore[index,union-attr]
    assert set(nodes) == {"INF-1", "INF-2"}
    assert nodes["INF-1"]["status"] == "pending"
    assert nodes["INF-1"]["red_line"] is True
    assert nodes["INF-1"]["created_by_event_id"].startswith("evt-")
    assert nodes["INF-2"]["status"] == "resolved"  # StatusChanged applied
    assert nodes["INF-2"]["nature_original_quote"] == "阈值定 100 QPS"
    assert nodes["INF-2"]["last_status_change_event_id"].startswith("evt-")


def test_information_need_graph_idempotent_double_apply(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    rec_c = _record(_CREATED, _created("INF-1", source="nature_unique", red_line=True, reason="r"), 1)
    rec_s = _record(_CHANGED, _status_changed("INF-1", old="pending", new="resolved"), 2)
    store._apply(rec_c)
    store._apply(rec_s)
    once = json.dumps(store.read("information_need_graph"), sort_keys=True)
    # double-apply same events → identical state (dedup-create + last-write status)
    store._apply(rec_c)
    store._apply(rec_s)
    twice = json.dumps(store.read("information_need_graph"), sort_keys=True)
    assert once == twice
    nodes = store.read("information_need_graph")["sessions"][_SID]["nodes"]  # type: ignore[index]
    assert len(nodes) == 1  # not duplicated


# ═══ interview_progress reducer ═══════════════════════════════════════════════


def test_interview_progress_aggregates(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(_CREATED, _created("INF-1", source="nature_unique", red_line=True, reason="r"), 1))
    store._apply(_record(_CREATED, _created("INF-2", source="ai_reachable", red_line=False), 2))
    store._apply(_record(_CHANGED, _status_changed("INF-2", old="pending", new="resolved"), 3))

    prog = store.read("interview_progress")["sessions"][_SID]  # type: ignore[index]
    assert prog["total_needs"] == 2
    assert prog["by_status"] == {"pending": 1, "resolved": 1}
    assert prog["red_line_total"] == 1
    assert prog["red_line_pending"] == 1
    assert prog["brief_published"] is False


def test_interview_progress_brief_flag_and_recompute_idempotent(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    rec = _record(_CREATED, _created("INF-1", source="nature_unique", red_line=True, reason="r"), 1)
    store._apply(rec)
    store._apply(_record(_BRIEF, _brief("brief-aaa11111"), 2))
    prog = store.read("interview_progress")["sessions"][_SID]  # type: ignore[index]
    assert prog["brief_published"] is True
    assert prog["red_line_pending"] == 1  # still computed from graph
    # double-apply the info-need create → counts unchanged (recompute from node set, not increment)
    before = json.dumps(store.read("interview_progress"), sort_keys=True)
    store._apply(rec)
    after = json.dumps(store.read("interview_progress"), sort_keys=True)
    assert before == after


# ═══ interview_brief_index reducer ════════════════════════════════════════════


def test_interview_brief_index_lookup_and_supersede(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    rec1 = _record(_BRIEF, _brief("brief-old00001"), 1)
    store._apply(rec1)
    old_event_id = rec1.event_id
    rec2 = _record(_BRIEF, _brief("brief-new00002", supersedes=old_event_id), 2)
    store._apply(rec2)

    idx = store.read("interview_brief_index")
    assert idx is not None
    briefs = idx["briefs"]  # type: ignore[index]
    assert set(briefs) == {"brief-old00001", "brief-new00002"}
    assert briefs["brief-old00001"]["superseded_by"] == rec2.event_id  # 回填
    assert briefs["brief-new00002"]["supersedes"] == old_event_id
    assert briefs["brief-new00002"]["goal_completion_condition"] == "done when X observable"


# ═══ T-L1-02 concept_graph flat-seed shape (ConceptCreated 两 shape) ═══════════


def test_concept_graph_reduces_flat_brief_seed(tmp_path: Path) -> None:
    # M-1.1 §5.5 brief-seed flat ConceptCreated (无 after_state) — 之前 reducer 只读
    # after_state.concept_id → 种子静默丢. 现读 flat 且保 seed_origin/source_brief_id.
    store = ProjectionStore(tmp_path / "graph")
    flat = {
        "kind": "ConceptCreated",
        "target_entity_type": "concept",
        "concept_id": "concept-seed-x",
        "concept_name": "X",
        "definition": "种子 X 定义",
        "seed_origin": True,
        "source_brief_id": "brief-abc12345",
    }
    store._apply(_record(EventType.CONCEPT_CREATED, flat, 1))
    cg = store.read("concept_graph")
    assert cg is not None
    nodes = cg["nodes"]  # type: ignore[index]
    assert len(nodes) == 1  # 种子真入图 (非静默丢)
    node = nodes[0]  # type: ignore[index]
    assert node["concept_id"] == "concept-seed-x"
    assert node["name"] == "X"  # flat concept_name → name
    assert node["seed_origin"] is True
    assert node["source_brief_id"] == "brief-abc12345"


def test_concept_graph_reduces_nested_shape_still_works(tmp_path: Path) -> None:
    # M-0.1/M-1.2 nested after_state shape 仍正常 (回归): seed_origin 默认 False.
    store = ProjectionStore(tmp_path / "graph")
    nested = {
        "after_state": {
            "concept_id": "concept-n",
            "name": "N",
            "definition": "d",
            "created_by_stage": "engineering_consensus",
        },
    }
    store._apply(_record(EventType.CONCEPT_CREATED, nested, 1))
    cg = store.read("concept_graph")
    assert cg is not None
    node = cg["nodes"][0]  # type: ignore[index]
    assert node["concept_id"] == "concept-n"
    assert node["name"] == "N"
    assert node["seed_origin"] is False
    assert node["source_brief_id"] is None


def test_three_projection_files_all_materialize(tmp_path: Path) -> None:
    # done_criteria: 三文件都出现且非空.
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_record(_CREATED, _created("INF-1", source="nature_unique", red_line=True, reason="r"), 1))
    store._apply(_record(_BRIEF, _brief("brief-zzz00001"), 2))
    for name in ("information_need_graph", "interview_progress", "interview_brief_index"):
        data = store.read(name)
        assert data is not None, f"{name} 未物化"
        assert data, f"{name} 空"
