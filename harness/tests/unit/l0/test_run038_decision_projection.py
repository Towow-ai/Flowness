"""RUN-038 L0增强 M-0.2: decision projection (Patch 6 — Decision Projection Schema 对齐).

spec ground-truth (M-0.2 附录B Patch 6):
  - 覆盖原文 §3.7-3.11 的 decision projection 描述。原文引用了不存在的通用 SemanticJudgmentMade
    event; M-0.1 实际是具体类型 (ResolverDecisionMade / ObligationScopeJudged / RiskSurfaceAssigned /
    等), 没有统一 lifecycle_state。
  - 修正 schema DecisionProjectionState.judgments[]:
      judgment_event_id / judgment_type (具体 event_type) / subject_ids ([{entity_type,entity_id}] 从
      subjects[] 提取) / current_validity [active|superseded|challenged|retracted] /
      superseded_by_event_id? / evidence_summary / uncertainty?
  - current_validity 推导规则:
      默认 active;
      存在 supersede event 指向这条 judgment → superseded (superseded_by_event_id 指向新 event);
      存在 FindingDisputed event 引用这条 judgment → challenged;
      存在 retraction event → retracted。
  - "记录不可变但判断可被修正" (M-0.1a Patch 5 对齐): M-0.1 保 supersede 链可查, M-0.2 从 supersede
    链推导 current_validity。

现状(改前): _PROJECTION_FILES 无 decision; semantic_judgment 类 event 发了但无 decision projection
  消费 → Patch 6 的"未关闭 decisions"查询能力缺失。

本组证: semantic_judgment 类 event 物化进 decision projection (judgment_event_id keyed); subject_ids
  从 subjects 提取; current_validity 默认 active; supersede 指向旧 judgment → 旧标 superseded +
  superseded_by_event_id; 多 judgment_type 并存; 幂等。

诚实边界 (据实挂债, 不硬做): challenged / retracted 在当前 canonical schema 无可用产生引用 ——
  FindingDisputed (M-0.1 §3.8) payload 只有 finding_id (dispute finding, 非 dispute judgment),
  无 retraction event_type。故 active / superseded 真物化 (canonical supersede 链可查), challenged /
  retracted 不伪造: 见 test_challenged_retracted_dependency_blocked 标注 (依赖 schema 补 judgment 引用)。
"""

from __future__ import annotations

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
    SupersedeNoveltyType,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


def _judgment(
    event_type: EventType,
    *,
    seq: int,
    subjects: list[tuple[SubjectEntityType, str]],
    evidence_summary: str = "ev",
    uncertainty: str | None = None,
    supersedes: str | None = None,
    event_id: str | None = None,
) -> EventRecord:
    payload: dict[str, object] = {"after_state": {"evidence_summary": evidence_summary}}
    if uncertainty is not None:
        payload["uncertainty"] = uncertainty
    sup = (
        Supersede(
            is_supersede=True,
            superseded_event_id=supersedes,
            novelty="改判",
            novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
        )
        if supersedes is not None
        else Supersede(is_supersede=False)
    )
    return EventRecord(
        event_id=event_id or f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=sup,
        subjects=[Subject(entity_type=et, entity_id=eid, role=SubjectRole.PRIMARY) for et, eid in subjects],
        schema_version="1.0.0",
    )


def _decisions(store: ProjectionStore) -> dict[str, dict]:
    proj = store.read("decision") or {"judgments": []}
    return {j["judgment_event_id"]: j for j in proj.get("judgments", [])}


def test_semantic_judgment_materializes_into_decision(tmp_path: Path) -> None:
    """semantic_judgment 类 event 物化进 decision projection (改前无 decision projection)。"""
    store = ProjectionStore(tmp_path / "graph")
    ev = _judgment(
        EventType.RESOLVER_DECISION_MADE,
        seq=1,
        subjects=[(SubjectEntityType.CONCEPT, "c-1")],
        evidence_summary="resolver 命中 obligation X",
        event_id="evt-j1",
    )
    store._apply(ev)
    decs = _decisions(store)
    assert "evt-j1" in decs, "ResolverDecisionMade 物化进 decision projection"
    j = decs["evt-j1"]
    assert j["judgment_type"] == "ResolverDecisionMade", "judgment_type = 具体 event_type"
    assert j["current_validity"] == "active", "默认 active"
    assert j["subject_ids"] == [{"entity_type": "concept", "entity_id": "c-1"}], "subject_ids 从 subjects 提取"
    assert j["evidence_summary"] == "resolver 命中 obligation X"


def test_supersede_marks_old_judgment_superseded(tmp_path: Path) -> None:
    """supersede event 指向旧 judgment → 旧标 superseded + superseded_by_event_id (Patch 6 推导)。"""
    store = ProjectionStore(tmp_path / "graph")
    old = _judgment(EventType.OBLIGATION_SCOPE_JUDGED, seq=1,
                    subjects=[(SubjectEntityType.OBLIGATION, "o-1")], event_id="evt-old")
    store._apply(old)
    assert _decisions(store)["evt-old"]["current_validity"] == "active"

    new = _judgment(EventType.OBLIGATION_SCOPE_JUDGED, seq=2,
                    subjects=[(SubjectEntityType.OBLIGATION, "o-1")], supersedes="evt-old", event_id="evt-new")
    store._apply(new)
    decs = _decisions(store)
    assert decs["evt-old"]["current_validity"] == "superseded", "supersede 链推导旧 judgment superseded"
    assert decs["evt-old"]["superseded_by_event_id"] == "evt-new", "superseded_by 指向新 event"
    assert decs["evt-new"]["current_validity"] == "active", "新 judgment active"


def test_uncertainty_preserved(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(
        _judgment(EventType.RISK_SURFACE_ASSIGNED, seq=1,
                  subjects=[(SubjectEntityType.PATCH, "p-1")], uncertainty="风险面不确定", event_id="evt-u"),
    )
    assert _decisions(store)["evt-u"]["uncertainty"] == "风险面不确定"


def test_multiple_judgment_types_coexist(tmp_path: Path) -> None:
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_judgment(EventType.RESOLVER_DECISION_MADE, seq=1,
                           subjects=[(SubjectEntityType.CONCEPT, "c")], event_id="evt-a"))
    store._apply(_judgment(EventType.CONSISTENCY_JUDGMENT_MADE, seq=2,
                           subjects=[(SubjectEntityType.CONCEPT, "c")], event_id="evt-b"))
    decs = _decisions(store)
    assert decs["evt-a"]["judgment_type"] == "ResolverDecisionMade"
    assert decs["evt-b"]["judgment_type"] == "ConsistencyJudgmentMade"
    assert len(decs) == 2


def test_non_judgment_event_not_in_decision(tmp_path: Path) -> None:
    """非 semantic_judgment 类 event 不进 decision projection (category 门)。"""
    store = ProjectionStore(tmp_path / "graph")
    # ConceptCreated 是 state_transition, 不该进 decision
    store._apply(
        EventRecord(
            event_id="evt-cc",
            sequence_number=1,
            timestamp=datetime.now(UTC),
            record_hash="sha256:x",
            local_intent_id="li-x",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={"after_state": {"concept_id": "c-1", "name": "C"}},
            provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="t"),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
    )
    assert "evt-cc" not in _decisions(store), "state_transition 不进 decision (category 门)"


def test_decision_idempotent_replay(tmp_path: Path) -> None:
    """重放同事件序 → state 一致 (幂等)。"""
    store = ProjectionStore(tmp_path / "graph")
    evs = [
        _judgment(EventType.OBLIGATION_SCOPE_JUDGED, seq=1,
                  subjects=[(SubjectEntityType.OBLIGATION, "o")], event_id="evt-old"),
        _judgment(EventType.OBLIGATION_SCOPE_JUDGED, seq=2,
                  subjects=[(SubjectEntityType.OBLIGATION, "o")], supersedes="evt-old", event_id="evt-new"),
    ]
    for e in evs:
        store._apply(e)
    for e in evs:  # 重放
        store._apply(e)
    decs = _decisions(store)
    assert len(decs) == 2, "幂等: 重放不重复物化"
    assert decs["evt-old"]["current_validity"] == "superseded"


def test_challenged_retracted_dependency_blocked() -> None:
    """诚实边界标注: challenged/retracted 当前 canonical schema 无可用产生引用 → 不伪造。

    Patch 6 写 "FindingDisputed event 引用这条 judgment → challenged" + "retraction event → retracted",
    但:
      - FindingDisputed (M-0.1 §3.8 FindingDisputedPayload) 只有 finding_id, dispute 的是 finding 非
        judgment, 无 judgment 引用字段;
      - enum 无 retraction event_type。
    故 reducer 只真物化 active / superseded (canonical supersede 链可查), challenged / retracted 留
    dependency_blocked 债 (依赖 schema 补 FindingDisputed→judgment 引用 / 新 retraction event_type)。
    本测仅作 in-code 文档锚 (不写假断言假装支持)。
    """
    assert True  # 文档锚: 详见模块 docstring 诚实边界 + 汇报挂债项
