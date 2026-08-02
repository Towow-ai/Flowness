"""M-0.2 ProjectionStore.recompute_one — 外科式单投影重建 (保 cursor / 不碰邻居 / 绝不一键 rebuild).

背景 (投影 stale 诊断, RUN-095 实证 + 记忆铁律 project_v3_projection_staleness_full_rebuild_unsafe):
  持久投影态会与"从零 rebuild"分叉 —— 比如 commit_history 持久 885 > rebuild 825 (base log 序列号
  碰撞 F-019-12, 增量在多次 catchup 中双算), task_graph 持久 < rebuild (快照恢复截断)。catchup() 此刻
  cursor 已到 head → 0 事件修不了; rebuild() 是禁用的一键全量 (会让 commit_history 反退 + 把 cursor 和
  18 个邻居投影一起推倒)。recompute_one 是外科式修法: 只丢 <name>.json 重建, 全程不动 .cursor、不碰邻居。

本组证 (合成 store / tempdir 副本, 绝不触碰 live harness/.towow):
  - canonical 等价: recompute_one(X) 产出 == 一键 rebuild 的 X 分量 (同 get_events_in_range 去重路径) —
    对 19 个投影逐个验。
  - 修分叉: 持久 X 被注入"增量双算" (seq 碰撞 → 持久文件多算一条) 或截断 → recompute_one(X) 修回 canonical。
  - 不碰邻居: recompute_one(X) 后, 其余 18 个投影文件 + .cursor 字节完全不变。
  - meta: 只有 X 的 meta watermark/generation 前进, 邻居 meta 不动。

关键路径锚点 (与 _apply / rebuild 对齐):
  ① 只 unlink <name>.json (邻居 + .cursor 不动)
  ② 本地临时 cursor 0→head 扫 event_log.get_events_in_range (按 seq 去重, 同 rebuild)
  ③ 只跑该 name 对应的 reducer 分支 (_apply_one)
  ④ write_atomic + _persist_meta(name, head); 绝不改 self.cursor / .cursor
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.projection.projection import _PROJECTION_FILES, ProjectionStore
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


# ─── synthetic event-log builders (real EventLog over a JSONL events.log) ──────────────


def _rec(
    seq: int,
    etype: EventType,
    category: EventCategory,
    payload: dict[str, object],
    *,
    event_id: str | None = None,
    subjects: list[tuple[SubjectEntityType, str]] | None = None,
) -> EventRecord:
    subj = subjects if subjects is not None else [(SubjectEntityType.CONCEPT, "c-anchor")]
    return EventRecord(
        event_id=event_id or f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=etype,
        event_category=category,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="recompute-test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=et, entity_id=eid, role=SubjectRole.PRIMARY) for et, eid in subj],
        schema_version="1.0.0",
    )


def _write_log(log_path: Path, records: list[EventRecord]) -> EventLog:
    """Write records as JSONL to events.log (the on-disk canonical form) and open a real EventLog."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")
    return EventLog(log_path)


def _diverse_records() -> list[EventRecord]:
    """A multi-projection event stream so every reducer branch has something to materialize.

    Touches concept_graph, task_graph, impact_graph (二阶), commit_history, ownership,
    obligation_lifecycle_state, debt_registry, capability_status, finding_lifecycle,
    detection_rule_lifecycle, escalation_lifecycle, decision, consumer_relation,
    engineering_consensus_index, review_plan, at_reference_graph, and the 3 interview-side
    projections.
    """
    return [
        # concept_graph: two nodes + a dependency edge (feeds impact_graph 二阶)
        _rec(1, EventType.CONCEPT_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-up", "name": "Upstream"}}),
        _rec(2, EventType.CONCEPT_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-down", "name": "Downstream"}}),
        _rec(3, EventType.CONCEPT_EDGE_ADDED, EventCategory.STATE_TRANSITION,
             {"after_state": {"edge_id": "ce-1", "source_concept_id": "c-down",
                              "target_concept_id": "c-up", "edge_type": "dependency",
                              "direction": "forward"}}),
        # task_graph: a node + a dependency edge (also feeds impact_graph 二阶)
        _rec(4, EventType.TASK_NODE_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"task_id": "t-1", "task_type": "primitive", "plan_id": "p-1"}}),
        _rec(5, EventType.TASK_NODE_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"task_id": "t-2", "task_type": "primitive", "plan_id": "p-1"}}),
        _rec(6, EventType.TASK_DEPENDENCY_EDGE_ADDED, EventCategory.STATE_TRANSITION,
             {"after_state": {"source_task_id": "t-1", "target_task_id": "t-2",
                              "dependency_type": "data"}}),
        # commit_history: two accepts + one reject (this is the projection that double-counts in live)
        _rec(7, EventType.COMMIT_ACCEPTED, EventCategory.STATE_TRANSITION, {"envelope_event_id": "env-a"}),
        _rec(8, EventType.COMMIT_REJECTED, EventCategory.STATE_TRANSITION, {"envelope_event_id": "env-b"}),
        _rec(9, EventType.COMMIT_ACCEPTED, EventCategory.STATE_TRANSITION, {"envelope_event_id": "env-c"}),
        # obligation_lifecycle_state: capture then check
        _rec(10, EventType.OBLIGATION_CAPTURED, EventCategory.STATE_TRANSITION,
             {"obligation_id": "ob-1", "definition": "must X", "scope_rule": "ALL"},
             subjects=[(SubjectEntityType.OBLIGATION, "ob-1")]),
        _rec(11, EventType.OBLIGATION_CHECKED, EventCategory.STATE_TRANSITION,
             {"obligation_id": "ob-1"}, subjects=[(SubjectEntityType.OBLIGATION, "ob-1")]),
        # debt_registry
        _rec(12, EventType.DEBT_REGISTERED, EventCategory.STATE_TRANSITION,
             {"debt_id": "d-1", "title": "stub", "debt_type": "incomplete", "severity": "normal"}),
        # capability_status: baseline ingest + advance
        _rec(13, EventType.CAPABILITY_BASELINE_INGESTED, EventCategory.STATE_TRANSITION,
             {"capabilities": [{"capability_id": "cap-1", "module": "M", "name": "N",
                                "classification": "stub"}]}),
        _rec(14, EventType.CAPABILITY_STATUS_ADVANCED, EventCategory.STATE_TRANSITION,
             {"capability_id": "cap-1", "from_classification": "stub", "to_classification": "built",
              "advanced_by": "T-X", "evidence_ref": "ref"}),
        # finding_lifecycle: create + verify
        _rec(15, EventType.FINDING_CREATED, EventCategory.STATE_TRANSITION,
             {"finding_id": "f-1", "severity": "high", "risk_surface": "rs"}),
        _rec(16, EventType.FINDING_VERIFIED, EventCategory.STATE_TRANSITION,
             {"finding_id": "f-1", "verification_state": "verified"}),
        # detection_rule_lifecycle
        _rec(17, EventType.DETECTION_RULE_PROPOSED, EventCategory.STATE_TRANSITION,
             {"rule_id": "dr-1", "rule_lifecycle_state": "proposed", "rule_definition": "def"}),
        # escalation_lifecycle (after_state shape)
        _rec(18, EventType.ESCALATION_RAISED, EventCategory.STATE_TRANSITION,
             {"after_state": {"escalation_id": "esc-1", "finding_id": "f-1"}}),
        # decision (semantic_judgment category)
        _rec(19, EventType.RESOLVER_DECISION_MADE, EventCategory.SEMANTIC_JUDGMENT,
             {"after_state": {"evidence_summary": "resolver picked X"}}, event_id="evt-jdg-1"),
        # consumer_relation
        _rec(20, EventType.CONSUMER_LIST_PUBLISHED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-up",
                              "consumers": [{"consumer_type": "task", "consumer_id": "t-1"}]}}),
        # engineering_consensus_index
        _rec(21, EventType.ENGINEERING_CONSENSUS_FREEZED, EventCategory.STATE_TRANSITION,
             {"after_state": {"plan_id": "p-1", "batch_info": {"batch_number": 1, "is_final_batch": True},
                              "frozen_concept_ids": ["c-up"]}}),
        # review_plan
        _rec(22, EventType.REVIEW_PLAN_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"review_plan_id": "rp-1", "dimensions": ["method-red-team"]}}),
        # at_reference_graph (T-L0-08 source/target shape)
        _rec(23, EventType.AT_REFERENCE_ADDED, EventCategory.STATE_TRANSITION,
             {"after_state": {"reference_id": "ar-1",
                              "source_entity": {"entity_type": "concept", "entity_id": "c-down"},
                              "target_entity": {"entity_type": "concept", "entity_id": "c-up"},
                              "reference_type": "depends"}}),
        # interview-side: information_need_graph + interview_progress + interview_brief_index
        _rec(24, EventType.INFORMATION_NEED_CREATED, EventCategory.STATE_TRANSITION,
             {"session_id": "s-1", "need_id": "n-1", "description": "what scope", "source": "downstream"}),
        _rec(25, EventType.INFORMATION_NEED_STATUS_CHANGED, EventCategory.STATE_TRANSITION,
             {"session_id": "s-1", "need_id": "n-1", "new_status": "resolved",
              "nature_original_quote": "q", "meaning_interpretation": "m"}),
        _rec(26, EventType.INTERVIEW_BRIEF_PUBLISHED, EventCategory.STATE_TRANSITION,
             {"session_id": "s-1", "brief_id": "b-1", "goal_completion_condition": "done when X",
              "concept_seeds_created": ["c-up"]}),
    ]


def _materialize_all(store: ProjectionStore, log: EventLog) -> None:
    """Catch up the store from 0 → head so every projection is materialized (live-like state)."""
    store.catchup(log)


def _all_neighbor_bytes(graph: Path, exclude: str) -> dict[str, bytes]:
    """Raw bytes of every projection file EXCEPT `exclude`, plus the .cursor file."""
    snap: dict[str, bytes] = {}
    for nm, fn in _PROJECTION_FILES.items():
        if nm == exclude:
            continue
        p = graph / fn
        snap[nm] = p.read_bytes() if p.exists() else b"<absent>"
    cursor = graph / ".cursor"
    snap["__cursor__"] = cursor.read_bytes() if cursor.exists() else b"<absent>"
    return snap


# ─── 1. canonical equivalence: recompute_one(X) == rebuild()'s X slice, for every projection ──


def test_recompute_one_equals_rebuild_slice_for_every_projection(tmp_path: Path) -> None:
    """recompute_one(X) 产出 == 一键 rebuild 的 X 分量 — 对 19 个投影逐个验 (同 get_events_in_range 去重)。"""
    records = _diverse_records()

    # canonical reference: a from-scratch rebuild in its own store.
    ref_log = _write_log(tmp_path / "ref" / "events.log", records)
    ref_store = ProjectionStore(tmp_path / "ref" / "graph")
    ref_store.rebuild(ref_log)
    canonical = {name: ref_store.read(name) for name in _PROJECTION_FILES}

    # under test: a separate live-like store, materialized then recompute_one'd per projection.
    log = _write_log(tmp_path / "live" / "events.log", records)
    graph = tmp_path / "live" / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)

    for name in _PROJECTION_FILES:
        store.recompute_one(name, log)
        assert store.read(name) == canonical[name], (
            f"recompute_one({name}) 必须等于 rebuild 的 {name} 分量"
        )

    # spot-check a few materialized projections actually carry content (not vacuously equal-empty).
    assert len(store.read("commit_history")["commits"]) == 3, "2 accept + 1 reject"
    assert len(store.read("concept_graph")["nodes"]) == 2
    assert len(store.read("impact_graph")["edges"]) >= 2, "二阶: 概念边 + 任务边都进 impact_graph"
    assert len(store.read("finding_lifecycle")["findings"]) == 1
    assert len(store.read("decision")["judgments"]) == 1


# ─── 2. repair a forked projection: 增量双算 (seq collision) → recompute_one restores canonical ──


def test_recompute_one_repairs_double_counted_projection(tmp_path: Path) -> None:
    """模拟 live 持久 885>825 病: 持久 commit_history 被注入"增量双算"多算一条 → recompute_one 修回 canonical。

    live 的根因是同一 envelope 在多次 catchup 中被增量重复 append (持久文件 885), 而 canonical
    rebuild 走 get_events_in_range 按 seq 去重 (825)。这里直接把持久文件做成 "多一条" 的分叉态,
    证明 recompute_one 不信任旧持久文件 (它 unlink 后从 0 按 seq 去重重算), 修回 canonical 计数。
    """
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)

    ch = graph / "commit_history.json"
    healthy = json.loads(ch.read_text(encoding="utf-8"))
    assert len(healthy["commits"]) == 3
    # inject a double-counted (duplicate) commit entry → persisted file now diverges (4 != canonical 3).
    polluted = {"commits": [*healthy["commits"], dict(healthy["commits"][0])]}
    ch.write_text(json.dumps(polluted), encoding="utf-8")
    assert len(json.loads(ch.read_text(encoding="utf-8"))["commits"]) == 4, "持久态已分叉 (双算)"

    store.recompute_one("commit_history", log)

    fixed = store.read("commit_history")
    assert len(fixed["commits"]) == 3, "recompute_one 不信任旧持久文件, 按 seq 去重修回 canonical"
    assert fixed == healthy, "修回的态 = 一键 rebuild 的 canonical 态"


def test_recompute_one_seq_collision_keeps_distinct_events_same_as_rebuild(tmp_path: Path) -> None:
    """T-RMD-SEQ-GUARD 段③ (supersedes RUN-095's seq-dedup framing): 两条 DISTINCT event_id 撞同一 seq
    → recompute_one 与 rebuild 都按 event_id 去重 (两条 distinct 全留), 二者产出仍完全一致。

    历史: 旧 get_events_in_range 按 seq 去重 (_by_sequence last-wins), 把"撞 seq 的两条只算一条"——曾被
    当作 commit_history 持久 885>825 的"canonical 825"修法。finding f-sub-l0-seq-uniqueness-blindspot
    (substrate audit, GC-04 critical) 判定那是 bug: 06-10 三次回退在账本留下的 dup-seq 是【不同 event_id
    的不同真事件】(本测 evt-c05 vs evt-c05dup, envelope 也不同), 按 seq 去重 = 静默丢真事件 (concept_graph
    −9 / task_graph −14 ... 下游有引用)。事件身份是 event_id 不是 seq → 读路径改按 event_id 去重: 撞同一
    seq 的不同 event_id 全留, 只有真重复 (同 event_id) 才折叠。recompute_one 与 rebuild 仍走同一条
    get_events_in_range 路径, 故二者产出仍一致 (本测正是钉这条等价 + "不丢真事件")。
    详 docs/SPEC-CONFLICT-RESOLUTION-LEDGER (T-RMD-SEQ-GUARD vs RUN-095) + 本任务 emit 的 FindingCreated。
    """
    # 9 distinct seqs + 1 collision on seq 5 (distinct event_id) = 10 physical records, all distinct events.
    records = [
        _rec(s, EventType.COMMIT_ACCEPTED, EventCategory.STATE_TRANSITION,
             {"envelope_event_id": f"env-{s}"}, event_id=f"evt-c{s:02d}")
        for s in range(1, 10)
    ]
    collide = _rec(5, EventType.COMMIT_ACCEPTED, EventCategory.STATE_TRANSITION,
                   {"envelope_event_id": "env-5-collision"}, event_id="evt-c05dup")
    records.append(collide)  # distinct event_id sharing seq=5 (06-10 rollback shape)

    # canonical from-scratch rebuild
    ref_log = _write_log(tmp_path / "ref" / "events.log", records)
    ref_store = ProjectionStore(tmp_path / "ref" / "graph")
    ref_store.rebuild(ref_log)
    canonical = ref_store.read("commit_history")

    # recompute_one in a separate store
    log = _write_log(tmp_path / "live" / "events.log", records)
    store = ProjectionStore(tmp_path / "live" / "graph")
    store.recompute_one("commit_history", log)
    got = store.read("commit_history")

    # 10 distinct event_ids (seq 5 carries two) → all 10 kept; no real event dropped (event_id identity).
    assert len(got["commits"]) == 10, "撞 seq 的两条 distinct event 都该留 (按 event_id 去重), 不是 9"
    assert got == canonical, "recompute_one 的 event_id 去重 == rebuild 的 (同 get_events_in_range 路径)"


def test_recompute_one_repairs_truncated_projection(tmp_path: Path) -> None:
    """模拟 task_graph 持久<rebuild 病 (快照恢复截断): 持久 X 被截断 → recompute_one 补回 canonical。"""
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)

    tg = graph / "task_graph.json"
    healthy = json.loads(tg.read_text(encoding="utf-8"))
    assert len(healthy["nodes"]) == 2
    # truncate: drop a node + the edge (snapshot-restore-style under-count).
    tg.write_text(json.dumps({"nodes": healthy["nodes"][:1], "edges": []}), encoding="utf-8")
    assert len(json.loads(tg.read_text(encoding="utf-8"))["nodes"]) == 1, "持久态已截断"

    store.recompute_one("task_graph", log)

    fixed = store.read("task_graph")
    assert len(fixed["nodes"]) == 2, "截断被补回"
    assert len(fixed["edges"]) == 1
    assert fixed == healthy, "修回 = canonical"


def test_recompute_one_repairs_impact_graph_second_order(tmp_path: Path) -> None:
    """impact_graph 是二阶 (从 concept/task 图派生): 用 _recompute_impact_graph 从已物化邻居重算修分叉。"""
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)

    ig = graph / "impact_graph.json"
    healthy = json.loads(ig.read_text(encoding="utf-8"))
    ig.write_text(json.dumps({"edges": []}), encoding="utf-8")  # corrupt to empty

    store.recompute_one("impact_graph", log)
    assert store.read("impact_graph") == healthy, "二阶从 concept_graph+task_graph (邻居) 重算回 canonical"


# ─── 3. isolation: recompute_one(X) leaves the other 18 projections + .cursor byte-for-byte ──


def test_recompute_one_does_not_touch_neighbors_or_cursor(tmp_path: Path) -> None:
    """recompute_one(X) 后: 其余 18 个投影文件 + .cursor 字节完全不变 (不一键 rebuild, 不动全局游标)。"""
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)

    cursor_before = store.cursor

    for target in _PROJECTION_FILES:
        neighbors_before = _all_neighbor_bytes(graph, exclude=target)
        store.recompute_one(target, log)
        neighbors_after = _all_neighbor_bytes(graph, exclude=target)
        assert neighbors_after == neighbors_before, (
            f"recompute_one({target}) 不得改动任何邻居投影文件或 .cursor"
        )
        assert store.cursor == cursor_before, (
            f"recompute_one({target}) 不得改 self.cursor (全局游标与单投影修复正交)"
        )


def test_recompute_one_never_resets_global_cursor_even_when_ahead(tmp_path: Path) -> None:
    """即便 cursor 已到 head (live 真实场景: catchup 已跑过, 0 事件可修), recompute_one 仍修得了 X 且不动 cursor。

    这是 recompute_one 存在的核心理由: 此刻 catchup() 修不了 (0 事件), rebuild() 会把 cursor 推倒重来 +
    扫所有投影。recompute_one 在 cursor=head 时照样只修 X、不碰 cursor。
    """
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)
    assert store.cursor == log.next_sequence, "catchup 后 cursor 已到 head (0 事件可 catchup)"
    assert store.catchup(log) == 0, "确认 catchup 此刻修不了任何东西"

    # corrupt one projection; catchup can't fix it, but recompute_one can — without touching cursor.
    fl = graph / "finding_lifecycle.json"
    fl.write_text(json.dumps({"findings": []}), encoding="utf-8")
    cursor_before = store.cursor

    store.recompute_one("finding_lifecycle", log)
    assert len(store.read("finding_lifecycle")["findings"]) == 1, "cursor=head 时仍修得了 X"
    assert store.cursor == cursor_before, "cursor 一字节不动"


# ─── 4. meta: only the target's watermark/generation advances; neighbor meta untouched ──


def test_recompute_one_advances_only_target_meta(tmp_path: Path) -> None:
    """recompute_one(X): 只有 X 的 meta (watermark/state_hash/generation) 前进, 邻居 meta 不动。"""
    records = _diverse_records()
    log = _write_log(tmp_path / "events.log", records)
    graph = tmp_path / "graph"
    store = ProjectionStore(graph)
    _materialize_all(store, log)  # catchup persists all meta at head

    head = log.next_sequence - 1
    target = "commit_history"
    # white-box meta assertions: _read_meta / _state_hash are the persistence internals recompute_one
    # must advance for the target alone (the public get_watermark surface returns only the int).
    gen_before = store._read_meta(target)["generation"]
    neighbor_meta_before = {nm: store._read_meta(nm) for nm in _PROJECTION_FILES if nm != target}

    store.recompute_one(target, log)

    meta_after = store._read_meta(target)
    assert meta_after["watermark"] == head, "target meta watermark = head (last consumed seq)"
    assert meta_after["generation"] == gen_before + 1, "target meta generation 前进一档"
    # state_hash matches the freshly-written state file.
    state_text = (graph / _PROJECTION_FILES[target]).read_text(encoding="utf-8")
    assert meta_after["state_hash"] == store._state_hash(state_text)

    for nm in _PROJECTION_FILES:
        if nm == target:
            continue
        assert store._read_meta(nm) == neighbor_meta_before[nm], (
            f"邻居 {nm} 的 meta 不得被 recompute_one({target}) 改动"
        )


def test_recompute_one_unknown_projection_raises(tmp_path: Path) -> None:
    log = _write_log(tmp_path / "events.log", _diverse_records())
    store = ProjectionStore(tmp_path / "graph")
    with pytest.raises(ValueError, match="unknown projection"):
        store.recompute_one("not_a_projection", log)
