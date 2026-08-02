"""M-2.1 §4.1 消费方发现 caller-4 — fix complete CIA cascade blast-radius scan unit tests.

# spec source:
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#     §4.1 消费方发现 (forward_slice — start_entity 的下游 = 谁依赖我 = blast radius)
#     §4.1 状态机合法性 (state_machine_check — proposed to_state 是否合法转移)
#     §4.2 SliceResult (含 projection_watermark); §4.4 invariant (含 watermark / max_depth ≤ 5)
#   04-l1-intelligence/M-1.6-fix-skill-detailed-design.md §3.1 (affected_entities) / §3.2 (O-14
#     semantic_upgrade_declaration.affected_concepts + concept_state_changes); §1.1 (cascade_scope
#     永远 provisional — fix 不做 verified cascade)
#   范本: tests/unit/schemas/test_review_plan_created.py (caller-3) / test_run057_cia_query (CIA)
#
# What these prove (unit level):
#   1. 方向实证 probe (§49 铁律, caller-1/2/3 都靠 probe 救场, 别信 docstring): 真边
#      B --dependency--> X (B 依赖 X) 经 impact_graph 归一化成 X→B → forward_slice(X) 返回 B
#      (X 的下游 = 谁依赖 X = blast radius)。同 caller-1/2 的 forward 方向。
#   2. cia_cascade_scan 对 fix 改的概念真跑 forward_slice 记 blast radius + watermark; 空概念集
#      (code-only fix 不动概念图) 仍是成功扫描 (downstream_entities=[] + watermark, 诚实记账)。
#   3. 改的概念集来源: FixProposed.affected_entities (entity_type=="concept") ∪
#      FixCompleted.semantic_upgrade_declaration.affected_concepts (两者都可选、可能空)。
#   4. 可选 state_machine_check: 声明 concept_state_changes 时, 对每个 (concept, to_state) 跑
#      state_machine_check 记转移合法性 + reason (record-only, 非法转移不 fail-close)。
#   5. 🔴 record-only, 绝不 cascade: status 永远 passed; 不调 compute_cascade / 不 emit
#      InvalidationCascade (grep 证明源文件零引用)。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection.projection import ProjectionStore
from towow.l1.cia_query import CIAQueryService
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
from towow.schemas.payloads.fix_completed_checks import (
    build_fix_cia_cascade_check,
    cia_cascade_scan,
    fix_changed_concepts,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── helpers: build a real materialized impact_graph from concept + edge events ──


def _ev(event_type: EventType, after: dict[str, object], seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": after},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="x", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _concept(cid: str, seq: int) -> EventRecord:
    return _ev(EventType.CONCEPT_CREATED, {"concept_id": cid, "name": cid}, seq)


def _cedge(eid: str, src: str, tgt: str, etype: str, seq: int) -> EventRecord:
    return _ev(
        EventType.CONCEPT_EDGE_ADDED,
        {
            "edge_id": eid,
            "source_concept_id": src,
            "target_concept_id": tgt,
            "edge_type": etype,
            "direction": "unidirectional",
        },
        seq,
    )


def _sm_defined(cid: str, seq: int) -> EventRecord:
    return _ev(
        EventType.CONCEPT_STATE_MACHINE_DEFINED,
        {
            "concept_id": cid,
            "states": [
                {"name": "draft", "is_initial": True},
                {"name": "active"},
                {"name": "closed", "is_terminal": True},
            ],
            "transitions": [
                {"from_state": "draft", "to_state": "active", "triggering_event": "Activate"},
                {"from_state": "active", "to_state": "closed", "triggering_event": "Close"},
            ],
            "saga_pattern": True,
        },
        seq,
    )


def _state_transition(cid: str, saga_state: str, seq: int) -> EventRecord:
    return _ev(EventType.CONCEPT_STATE_TRANSITION, {"concept_id": cid, "saga_state": saga_state}, seq)


# ════════════════════════════════════════════════════════════════════════════════
#  1. 方向实证 probe (§49) — 真数据, 不信 docstring (caller-4 是 forward, 同 caller-1/2)
# ════════════════════════════════════════════════════════════════════════════════


def test_direction_probe_forward_slice_returns_downstream_on_real_edge(tmp_path: Path) -> None:
    """§49 铁律方向实证: 真边 B --dependency--> X (B 依赖 X) → impact_graph 归一化成 X→B →
    forward_slice(X) 返回 B (X 的下游 = 谁依赖 X = blast radius)。

    caller-4 用 forward (查下游=谁会被这次修复波及), 同 caller-1/2, 与 caller-3 的 backward 相反。
    用真物化 impact_graph probe 锁死方向, 不信注释。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("B", 1))
    store._apply(_concept("X", 2))
    # RAW edge: source=B, target=X, edge_type=dependency == "B depends on X".
    store._apply(_cedge("e-BX", "B", "X", "dependency", 3))

    # 真物化 impact_graph 归一化方向 (dependency 翻转: source=上游产出, target=下游消费)。
    ig = store.read("impact_graph") or {}
    concept_edges = [e for e in ig.get("edges", []) if e.get("kind") == "concept"]
    assert concept_edges, "impact_graph 必须物化出概念边"
    assert (concept_edges[0]["source"], concept_edges[0]["target"]) == ("X", "B"), (
        f"raw B--dep-->X 必须归一化成 X→B, 实得 {concept_edges[0]['source']}→{concept_edges[0]['target']}"
    )

    cia = CIAQueryService(store)
    # 方向实证: forward_slice(X) 返回 B (X 的下游=谁依赖 X), backward_slice(X) 返回空 (X 不依赖谁)。
    fw = {r.entity.entity_id for r in cia.forward_slice("X").results}
    bw = {r.entity.entity_id for r in cia.backward_slice("X").results}
    assert fw == {"B"}, f"forward_slice(X) 必须返回下游 blast radius B, 实得 {fw}"
    assert bw == set(), f"backward_slice(X) 应为空 (X 不依赖谁), 实得 {bw} — 证明方向真是 forward 非对称伪迹"


def test_direction_probe_transitive_downstream_chain(tmp_path: Path) -> None:
    """传递性下游: C 依赖 B 依赖 A → forward_slice(A) 返回 B(depth1) + C(depth2) = A 的 blast radius。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("A", 1))
    store._apply(_concept("B", 2))
    store._apply(_concept("C", 3))
    store._apply(_cedge("e-CB", "C", "B", "dependency", 4))  # C depends_on B
    store._apply(_cedge("e-BA", "B", "A", "dependency", 5))  # B depends_on A
    cia = CIAQueryService(store)
    out = cia.forward_slice("A", max_depth=5)
    by_id = {r.entity.entity_id: r for r in out.results}
    assert set(by_id) == {"B", "C"}, "A 的传递性下游 = B 直接 + C 经 B"
    assert by_id["B"].depth == 1
    assert by_id["C"].depth == 2


# ════════════════════════════════════════════════════════════════════════════════
#  2. cia_cascade_scan — record-only blast-radius 扫描
# ════════════════════════════════════════════════════════════════════════════════


def test_scan_records_transitive_downstream_blast_radius(tmp_path: Path) -> None:
    """fix 改了 X; B 依赖 X, C 依赖 B → scan 浮出 B(depth1) + C(depth2) = blast radius。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("X", 1))
    store._apply(_concept("B", 2))
    store._apply(_concept("C", 3))
    store._apply(_cedge("e-BX", "B", "X", "dependency", 4))  # B depends_on X
    store._apply(_cedge("e-CB", "C", "B", "dependency", 5))  # C depends_on B

    out = cia_cascade_scan(["X"], [], store)
    assert out["scanned_concepts"] == ["X"]
    by_id = {d["entity_id"]: d for d in out["downstream_entities"]}  # type: ignore[index,union-attr]
    assert set(by_id) == {"B", "C"}, f"X 的下游 blast radius 应为 B (直接) + C (传递), 实得 {set(by_id)}"
    assert by_id["B"]["depth"] == 1
    assert by_id["C"]["depth"] == 2
    assert by_id["B"]["path"], "下游命中必须记非空 impact_graph 边 path (可复算锚)"
    assert out["state_machine_checks"] == []
    assert isinstance(out["projection_watermark"], int)
    assert out["projection_watermark"] >= 0


def test_scan_excludes_changed_concept_set(tmp_path: Path) -> None:
    """fix 同时改了 X 和它的下游 B → B 在改动集内, 不列为 (改动集外) blast radius。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("X", 1))
    store._apply(_concept("B", 2))
    store._apply(_concept("D", 3))
    store._apply(_cedge("e-BX", "B", "X", "dependency", 4))  # B depends_on X (B 在改动集)
    store._apply(_cedge("e-DB", "D", "B", "dependency", 5))  # D depends_on B (D 在改动集外)
    out = cia_cascade_scan(["X", "B"], [], store)
    ds_ids = {d["entity_id"] for d in out["downstream_entities"]}  # type: ignore[union-attr]
    assert "B" not in ds_ids, "改动集内概念 B 不得列为 blast radius (互相依赖已知)"
    assert "D" in ds_ids, "改动集外下游 D 必须浮出"


def test_scan_empty_concept_set_is_still_a_successful_scan(tmp_path: Path) -> None:
    """code-only fix (空概念集, 不动概念图) → downstream_entities=[] 仍是成功扫描.

    诚实记 scanned_concepts=[] + watermark 证明跑过 (= store.cursor-1, 不是 0 占位伪迹), 可复算。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("a", 1))
    store._apply(_concept("b", 2))
    out = cia_cascade_scan([], [], store)
    assert out["scanned_concepts"] == []
    assert out["downstream_entities"] == []
    assert out["state_machine_checks"] == []
    assert out["projection_watermark"] == max(0, store.cursor - 1)


def test_scan_empty_graph_records_real_watermark(tmp_path: Path) -> None:
    """改了概念但图无下游边 → downstream_entities=[] 仍是成功扫描 (watermark 证明跑过)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("solo", 1))  # 无任何边
    out = cia_cascade_scan(["solo"], [], store)
    assert out["scanned_concepts"] == ["solo"]
    assert out["downstream_entities"] == []
    assert isinstance(out["projection_watermark"], int)
    assert out["projection_watermark"] >= 0


# ════════════════════════════════════════════════════════════════════════════════
#  3. 可选 state_machine_check — 声明 concept_state_changes 时跑 (record-only)
# ════════════════════════════════════════════════════════════════════════════════


def test_state_machine_check_legal_transition_recorded(tmp_path: Path) -> None:
    """声明 (SM, active→closed) 合法转移 → 记 is_valid_transition=True + reason, status 仍 passed。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("SM", 1))
    store._apply(_sm_defined("SM", 2))
    store._apply(_state_transition("SM", "active", 3))  # 当前态 active
    state_changes = [{"concept_id": "SM", "from_state": "active", "to_state": "closed"}]
    out = cia_cascade_scan(["SM"], state_changes, store)
    sm = out["state_machine_checks"]
    assert len(sm) == 1  # type: ignore[arg-type]
    rec = sm[0]  # type: ignore[index]
    assert rec["concept_id"] == "SM"
    assert rec["to_state"] == "closed"
    assert rec["current_state"] == "active"
    assert rec["is_valid_transition"] is True, "active → closed 合法"
    assert "closed" in rec["allowed_transitions"]
    assert "合法转移" in rec["reason"]


def test_state_machine_check_illegal_transition_recorded_not_failed(tmp_path: Path) -> None:
    """声明非法转移 (SM, active→draft) → 记 is_valid_transition=False + reason, 但 scan 不 fail-close.

    record-only: 非法转移记下来给下游看, 不阻塞 fix (M-1.5 owns verified closure)。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("SM", 1))
    store._apply(_sm_defined("SM", 2))
    store._apply(_state_transition("SM", "active", 3))  # 当前态 active
    state_changes = [{"concept_id": "SM", "from_state": "active", "to_state": "draft"}]
    out = cia_cascade_scan(["SM"], state_changes, store)
    rec = out["state_machine_checks"][0]  # type: ignore[index]
    assert rec["is_valid_transition"] is False, "active → draft 无此转移, 非法"
    assert "非法转移" in rec["reason"]


def test_state_machine_check_no_machine_recorded(tmp_path: Path) -> None:
    """概念无 state_machine 定义 → state_machine_check 保守记 invalid + reason, 不抛, status 仍 passed。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("PLAIN", 1))
    state_changes = [{"concept_id": "PLAIN", "from_state": "", "to_state": "whatever"}]
    out = cia_cascade_scan(["PLAIN"], state_changes, store)
    rec = out["state_machine_checks"][0]  # type: ignore[index]
    assert rec["is_valid_transition"] is False
    assert rec["reason"], "无状态机时必须给出 reason 说明"


def test_state_machine_check_only_runs_when_declared(tmp_path: Path) -> None:
    """无 concept_state_changes 声明 → state_machine_checks 空 (条件分支不跑)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("SM", 1))
    store._apply(_sm_defined("SM", 2))
    out = cia_cascade_scan(["SM"], [], store)
    assert out["state_machine_checks"] == [], "未声明 concept_state_changes → state_machine_check 不跑"


# ════════════════════════════════════════════════════════════════════════════════
#  4. fix_changed_concepts — 改的概念集来源 (FixProposed ∪ semantic_upgrade_declaration)
# ════════════════════════════════════════════════════════════════════════════════


class _FakeLog:
    """Minimal EventLog stand-in: get_events_by_type(event_type) → [record] (type index)."""

    def __init__(self, records: list[EventRecord]) -> None:
        self._records = records

    def get_events_by_type(self, event_type: EventType, **_kw: object) -> list[EventRecord]:
        return [r for r in self._records if r.event_type == event_type]


def _fix_proposed_record(fix_id: str, affected_entities: list[dict[str, object]]) -> EventRecord:
    """A FixProposed (same shape main.py fix_propose emits — affected_entities[{entity_type,...}])."""
    return EventRecord(
        event_id=f"evt-fp-{uuid.uuid4().hex[:8]}",
        sequence_number=20,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id="li-fp",
        event_type=EventType.FIX_PROPOSED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": {"fix_id": fix_id, "affected_entities": affected_entities}},
        provenance=Provenance(
            actor_type=ActorType.AGENT_SESSION.value, actor_id="m16",
            session_id="s-fix", skill_id="M-1.6",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.PATCH, entity_id=fix_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_changed_concepts_from_fix_proposed_affected_entities() -> None:
    """改的概念来自 FixProposed.affected_entities (只取 entity_type=='concept', 滤掉 file/task)。"""
    fp = _fix_proposed_record(
        "fix-1",
        [
            {"entity_type": "concept", "entity_id": "concept-a@v1"},
            {"entity_type": "file", "entity_id": "src/x.py"},  # 非概念, 滤掉
            {"entity_type": "concept", "entity_id": "concept-b@v1"},
        ],
    )
    log = _FakeLog([fp])
    concepts, state_changes = fix_changed_concepts(log, "fix-1", None)  # type: ignore[arg-type]
    assert concepts == ["concept-a@v1", "concept-b@v1"], "只取概念实体, 排序去重"
    assert state_changes == []


def test_changed_concepts_union_with_semantic_declaration() -> None:
    """改的概念 = FixProposed.affected_entities[concept] ∪ semantic_upgrade.affected_concepts (并集)。"""
    fp = _fix_proposed_record("fix-1", [{"entity_type": "concept", "entity_id": "concept-a@v1"}])
    log = _FakeLog([fp])
    decl = {
        "affected_concepts": ["concept-b@v1", "concept-a@v1"],  # a 与 FixProposed 重叠 (去重)
        "concept_state_changes": [
            {"concept_id": "concept-a@v1", "from_state": "active", "to_state": "closed"},
        ],
    }
    concepts, state_changes = fix_changed_concepts(log, "fix-1", decl)  # type: ignore[arg-type]
    assert concepts == ["concept-a@v1", "concept-b@v1"], "并集 + 去重 + 排序"
    assert state_changes == [{"concept_id": "concept-a@v1", "from_state": "active", "to_state": "closed"}]


def test_changed_concepts_empty_for_code_only_fix() -> None:
    """code-only fix: FixProposed 无概念实体 + 无 semantic declaration → 空概念集 (合法)。"""
    fp = _fix_proposed_record("fix-1", [{"entity_type": "file", "entity_id": "src/x.py"}])
    log = _FakeLog([fp])
    concepts, state_changes = fix_changed_concepts(log, "fix-1", None)  # type: ignore[arg-type]
    assert concepts == []
    assert state_changes == []


def test_changed_concepts_no_fix_proposed_only_declaration() -> None:
    """无 FixProposed 但有 semantic declaration → 概念全来自 declaration (FixProposed 可选)。"""
    log = _FakeLog([])
    decl = {"affected_concepts": ["concept-z@v1"]}
    concepts, _ = fix_changed_concepts(log, "fix-1", decl)  # type: ignore[arg-type]
    assert concepts == ["concept-z@v1"]


# ════════════════════════════════════════════════════════════════════════════════
#  5. build_fix_cia_cascade_check — 组装 blocking_check (record-only, status 永远 passed)
# ════════════════════════════════════════════════════════════════════════════════


def test_build_check_always_passed_with_recomputable_evidence(tmp_path: Path) -> None:
    """组装的 fix.cia_cascade_scan check: status 永远 passed; evidence 含 scanned/downstream/watermark.

    返回 plain dict + 嵌套 dict evidence (caller-1/2/3 同款; NOT FixBlockingCheck 的 str evidence —
    那会被 envelope builder 当非 dict 丢掉降级成 {"check_id":...}, 丢 watermark)。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("X", 1))
    store._apply(_concept("B", 2))
    store._apply(_cedge("e-BX", "B", "X", "dependency", 3))  # B depends_on X
    fp = _fix_proposed_record("fix-1", [{"entity_type": "concept", "entity_id": "X"}])
    log = _FakeLog([fp])

    check = build_fix_cia_cascade_check(log, "fix-1", None, store)  # type: ignore[arg-type]
    assert check["check_id"] == "fix.cia_cascade_scan"
    assert check["status"] == "passed", "record-only — status 永远 passed"
    ev = check["evidence"]
    assert ev["scanned_concepts"] == ["X"]  # type: ignore[index]
    assert {d["entity_id"] for d in ev["downstream_entities"]} == {"B"}, "X 的下游 blast radius = B"  # type: ignore[index,union-attr]
    assert isinstance(ev["projection_watermark"], int)  # type: ignore[index]


def test_build_check_code_only_fix_passes_with_empty_scan(tmp_path: Path) -> None:
    """code-only fix (无概念) → check 仍 passed, evidence scanned_concepts=[] (诚实空扫描)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("X", 1))
    fp = _fix_proposed_record("fix-1", [{"entity_type": "file", "entity_id": "src/x.py"}])
    log = _FakeLog([fp])
    check = build_fix_cia_cascade_check(log, "fix-1", None, store)  # type: ignore[arg-type]
    assert check["status"] == "passed"
    ev = check["evidence"]
    assert ev["scanned_concepts"] == []  # type: ignore[index]
    assert ev["downstream_entities"] == []  # type: ignore[index]
