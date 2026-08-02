"""M-2.1 §4.1 消费方发现 caller-3 — review plan-create CIA backward (上游前提) scan unit tests.

# spec source:
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#     §4.1 依赖前提核查 (backward_slice — start_entity 依赖谁 = 上游前提)
#     §4.2 SliceResult (含 projection_watermark); §4.4 invariant (含 watermark / max_depth ≤ 5)
#   范本: tests/unit/schemas/test_plan_freezed.py (caller-2) / test_wave2_tl1_18 (caller-1)
#
# What these prove (unit level):
#   1. 方向实证 probe (§49 铁律, caller-1/2 都栽过 docstring 盲信): 真边 B--dependency-->X
#      (B 依赖 X) 经 impact_graph 归一化, backward_slice(B) 返回 X (B 的上游前提) —— 用真数据
#      probe, 不信 docstring。这是 caller-3 与 caller-1/2 唯一的方向差异, 必须实证锁死。
#   2. cia_upstream_scan 对本批 frozen 概念真跑 backward_slice, 记上游前提概念 + watermark;
#      空图 (无上游边) 仍是成功扫描 (upstream_prerequisites=[] + watermark)。
#   3. scan 起点从 trigger_event_id 解析 frozen_concept_ids: 读那条 EngineeringConsensusFreezed
#      的 after_state.frozen_concept_ids (NOT 自由文本 --consensus-id); trigger 非 freeze /
#      不存在 → 空解析 (仍记录, 不假通过)。
#   4. 能抓到"光看 frozen 概念漏掉的上游前提": frozen 了 B, 但 B 依赖的 X 本批没碰 → scan 浮出 X。
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
from towow.schemas.payloads.review_plan_created import (
    cia_upstream_scan,
    frozen_concept_ids_from_trigger,
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


# ════════════════════════════════════════════════════════════════════════════════
#  1. 方向实证 probe (§49) — 真数据, 不信 docstring
# ════════════════════════════════════════════════════════════════════════════════


def test_direction_probe_backward_slice_returns_upstream_on_real_edge(tmp_path: Path) -> None:
    """§49 铁律方向实证: 真边 B --dependency--> X (B 依赖 X, X 是 B 的上游前提) →
    impact_graph 归一化成 X→B → backward_slice(B) 返回 X (上游前提)。

    这是 caller-3 与 caller-1/2 唯一的方向差异。caller-1/2 用 forward (查下游=谁依赖我),
    caller-3 用 backward (查上游=我依赖谁)。用真物化 impact_graph probe 锁死方向, 不信注释。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("B", 1))
    store._apply(_concept("X", 2))
    # RAW edge: source=B, target=X, edge_type=dependency == "B depends on X" (X = B 的上游前提).
    store._apply(_cedge("e-BX", "B", "X", "dependency", 3))

    # 真物化 impact_graph 归一化方向 (dependency 翻转: source=上游, target=下游)。
    ig = store.read("impact_graph") or {}
    concept_edges = [e for e in ig.get("edges", []) if e.get("kind") == "concept"]
    assert concept_edges, "impact_graph 必须物化出概念边"
    assert (concept_edges[0]["source"], concept_edges[0]["target"]) == ("X", "B"), (
        f"raw B--dep-->X 必须归一化成 X→B, 实得 {concept_edges[0]['source']}→{concept_edges[0]['target']}"
    )

    cia = CIAQueryService(store)
    # 方向实证: backward_slice(B) 返回 X (B 的上游前提), forward_slice(B) 返回空 (没谁依赖 B)。
    bw = {r.entity.entity_id for r in cia.backward_slice("B").results}
    fw = {r.entity.entity_id for r in cia.forward_slice("B").results}
    assert bw == {"X"}, f"backward_slice(B) 必须返回上游前提 X, 实得 {bw}"
    assert fw == set(), f"forward_slice(B) 应为空 (无人依赖 B), 实得 {fw} — 证明方向真是 backward 非对称伪迹"


def test_direction_probe_transitive_upstream_chain(tmp_path: Path) -> None:
    """传递性上游: C 依赖 B 依赖 A → backward_slice(C) 返回 B(depth1) + A(depth2)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("A", 1))
    store._apply(_concept("B", 2))
    store._apply(_concept("C", 3))
    store._apply(_cedge("e-CB", "C", "B", "dependency", 4))  # C depends_on B
    store._apply(_cedge("e-BA", "B", "A", "dependency", 5))  # B depends_on A
    cia = CIAQueryService(store)
    out = cia.backward_slice("C", max_depth=5)
    by_id = {r.entity.entity_id: r for r in out.results}
    assert set(by_id) == {"A", "B"}, "C 的传递性上游前提 = B 直接 + A 经 B"
    assert by_id["B"].depth == 1
    assert by_id["A"].depth == 2


# ════════════════════════════════════════════════════════════════════════════════
#  2. cia_upstream_scan — record-only 上游前提扫描
# ════════════════════════════════════════════════════════════════════════════════


def test_scan_records_transitive_upstream_prerequisites(tmp_path: Path) -> None:
    """本批 frozen B; B 依赖 X (本批没碰), X 依赖 Y → scan 浮出 X(depth1) + Y(depth2)。

    抓的正是"光看 frozen 概念 (只有 B) 漏掉的上游前提" X / Y。
    """
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("B", 1))
    store._apply(_concept("X", 2))
    store._apply(_concept("Y", 3))
    store._apply(_cedge("e-BX", "B", "X", "dependency", 4))  # B depends_on X
    store._apply(_cedge("e-XY", "X", "Y", "dependency", 5))  # X depends_on Y

    out = cia_upstream_scan(["B"], store)
    assert out["scanned_concepts"] == ["B"]
    by_id = {u["concept_id"]: u for u in out["upstream_prerequisites"]}  # type: ignore[index,union-attr]
    assert set(by_id) == {"X", "Y"}, f"B 的上游前提应为 X (直接) + Y (传递), 实得 {set(by_id)}"
    assert by_id["X"]["depth"] == 1
    assert by_id["Y"]["depth"] == 2
    assert by_id["X"]["path"], "上游前提命中必须记非空 impact_graph 边 path (可复算锚)"
    assert isinstance(out["projection_watermark"], int)
    assert out["projection_watermark"] >= 0


def test_scan_excludes_in_batch_concepts(tmp_path: Path) -> None:
    """本批同时 frozen B 和它的上游 X → X 是批内概念, 不列为 (批外) 上游前提。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("B", 1))
    store._apply(_concept("X", 2))
    store._apply(_concept("Z", 3))
    store._apply(_cedge("e-BX", "B", "X", "dependency", 4))  # B depends_on X (X 在批内)
    store._apply(_cedge("e-XZ", "X", "Z", "dependency", 5))  # X depends_on Z (Z 在批外)
    out = cia_upstream_scan(["B", "X"], store)
    up_ids = {u["concept_id"] for u in out["upstream_prerequisites"]}  # type: ignore[union-attr]
    assert "X" not in up_ids, "批内概念 X 不得列为上游前提 (in-batch ref 已知合法)"
    assert "Z" in up_ids, "批外上游前提 Z 必须浮出"


def test_scan_empty_graph_is_still_a_successful_scan(tmp_path: Path) -> None:
    """图无上游边 → upstream_prerequisites=[] 仍是成功扫描 (watermark 证明跑过, 可复算)。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("solo", 1))  # 无任何边
    out = cia_upstream_scan(["solo"], store)
    assert out["scanned_concepts"] == ["solo"]
    assert out["upstream_prerequisites"] == []
    assert isinstance(out["projection_watermark"], int)
    assert out["projection_watermark"] >= 0


def test_scan_empty_frozen_set_records_real_watermark(tmp_path: Path) -> None:
    """零 frozen 概念 (空批) 仍记录真 projection 水位 (= store.cursor-1), 不是 0 占位伪迹。"""
    store = ProjectionStore(tmp_path / "graph")
    store._apply(_concept("a", 1))
    store._apply(_concept("b", 2))
    out = cia_upstream_scan([], store)
    assert out["scanned_concepts"] == []
    assert out["upstream_prerequisites"] == []
    assert out["projection_watermark"] == max(0, store.cursor - 1)


# ════════════════════════════════════════════════════════════════════════════════
#  3. scan 起点 — 从 trigger_event_id 解析 frozen_concept_ids (NOT --consensus-id)
# ════════════════════════════════════════════════════════════════════════════════


class _FakeLog:
    """Minimal EventLog stand-in: get_event(event_id) → record | None (point lookup)."""

    def __init__(self, records: dict[str, EventRecord]) -> None:
        self._records = records

    def get_event(self, event_id: str) -> EventRecord | None:
        return self._records.get(event_id)


def _consensus_freezed_record(event_id: str, frozen: list[str]) -> EventRecord:
    """A canonical EngineeringConsensusFreezed (same shape main.py:12357 emits)."""
    return EventRecord(
        event_id=event_id,
        sequence_number=10,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id="li-freeze",
        event_type=EventType.ENGINEERING_CONSENSUS_FREEZED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": {"plan_id": "plan-1", "frozen_concept_ids": frozen}},
        provenance=Provenance(
            actor_type=ActorType.AGENT_SESSION.value, actor_id="m12",
            session_id="s-consensus", skill_id="M-1.2",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_frozen_ids_resolved_from_trigger_consensus_freezed() -> None:
    """trigger = EngineeringConsensusFreezed → 解析出它的 frozen_concept_ids (本批 frozen 概念)。"""
    rec = _consensus_freezed_record("evt-freeze-1", ["concept-b@v1", "concept-a@v1"])
    log = _FakeLog({"evt-freeze-1": rec})
    ids, how = frozen_concept_ids_from_trigger(log, "evt-freeze-1")  # type: ignore[arg-type]
    assert ids == ["concept-a@v1", "concept-b@v1"], "必须按 frozen_concept_ids 解析 + 排序去重"
    assert "resolved 2 frozen_concept_ids" in how


def test_frozen_ids_empty_when_trigger_not_a_freeze() -> None:
    """trigger 不是 EngineeringConsensusFreezed → 空解析 (仍记录原因, 不假装解析到)。"""
    rec = _ev(EventType.CONCEPT_CREATED, {"concept_id": "x"}, 5)
    log = _FakeLog({rec.event_id: rec})
    ids, how = frozen_concept_ids_from_trigger(log, rec.event_id)  # type: ignore[arg-type]
    assert ids == []
    assert "not EngineeringConsensusFreezed" in how


def test_frozen_ids_empty_when_trigger_not_found() -> None:
    """trigger event 不在日志 → 空解析 (not found), 不抛。"""
    log = _FakeLog({})
    ids, how = frozen_concept_ids_from_trigger(log, "evt-missing")  # type: ignore[arg-type]
    assert ids == []
    assert "not found" in how


def test_frozen_ids_handles_node_touched_wrapped_freeze() -> None:
    """trigger 是 NodeTouched-wrap 的 EngineeringConsensusFreezed → unwrap 后仍解析到 frozen ids.

    防 stub-rewrap 路 (直读 event_type=NodeTouched 会跳过 = 空扫描假通过, caller-1/2 同款防御)。
    """
    wrapped = EventRecord(
        event_id="evt-wrapped-freeze",
        sequence_number=11,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id="li-wrap",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={
            "kind": "EngineeringConsensusFreezed",
            "stub_original_payload": {"after_state": {"frozen_concept_ids": ["concept-w@v1"]}},
        },
        provenance=Provenance(
            actor_type=ActorType.AGENT_SESSION.value, actor_id="m12",
            session_id="s-consensus", skill_id="M-1.2",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    log = _FakeLog({"evt-wrapped-freeze": wrapped})
    ids, how = frozen_concept_ids_from_trigger(log, "evt-wrapped-freeze")  # type: ignore[arg-type]
    assert ids == ["concept-w@v1"], "NodeTouched-wrap 的 freeze 必须 unwrap 后解析到 frozen ids"
    assert "resolved 1 frozen_concept_ids" in how
