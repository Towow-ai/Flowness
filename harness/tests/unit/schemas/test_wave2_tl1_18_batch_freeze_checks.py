"""波2 T-L1-18: consensus_batch_freeze §8.3 三check 逻辑单测 (脱 CLI).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §8.3 + §8.4
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md §4.1 (caller-1: freeze 调 CIA)
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
from towow.schemas.payloads.consensus_batch_freeze import (
    run_batch_freeze_checks,
)

if TYPE_CHECKING:
    from pathlib import Path


# design-maintainability-gate@v1 existence-half: a freeze of ≥1 new concept must carry a
# well-formed maintainability clause. Non-empty batches now pass it through so the
# consensus.maintainability_clause_present check passes (empty batches don't require it).
VALID_MAINTAINABILITY_CLAUSE = {
    "concept_ids": ["test-concept@v1"],
    "standard_content_durable": "canonical ConceptCreated record, machine-readable",
    "discoverable_points_current": "registered via capability registry, single current version",
    "updatable": "evolves via consensus supersede mechanism",
    "runtime_observable": "n/a: test fixture",
    "maintenance_not_by_memory": "gate-enforced, not memory",
}


def _clause(*concept_ids: str) -> dict:
    """Maintainability clause attesting exactly the given frozen concept ids (substance-half:
    attested ⊇ frozen-this-batch). Each freeze must attest the concept(s) it actually freezes."""
    return {**VALID_MAINTAINABILITY_CLAUSE, "concept_ids": list(concept_ids)}


def _write_log(towow: Path, events: list[dict]) -> None:
    (towow / "events").mkdir(parents=True, exist_ok=True)
    # base events.log is enough for iter_raw_event_lines when no rotation
    (towow / "events.log").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8",
    )


def _concept(sid: str, cid: str) -> dict:
    return {
        "event_type": "ConceptCreated",
        "provenance": {"session_id": sid},
        "payload": {"after_state": {"concept_id": cid}},
    }


def _consumer(sid: str, cid: str, *consumer_ids: str) -> dict:
    """ConsumerListPublished. Pass consumer_ids to cover machine-scannable consumers (M12-F4
    coverage gate); omit for the common case where the concept has no discoverable consumers."""
    return {
        "event_type": "ConsumerListPublished",
        "provenance": {"session_id": sid},
        "payload": {
            "after_state": {
                "concept_id": cid,
                "consumers": [
                    {"consumer_type": "concept", "consumer_id": c, "usage": "edge:dependency"}
                    for c in consumer_ids
                ],
            },
        },
    }


def _at_ref(sid: str, target_name: str) -> dict:
    return {
        "event_type": "AtReferenceAdded",
        "provenance": {"session_id": sid},
        "payload": {"after_state": {"target_concept_name": target_name}},
    }


def _prior_freeze(plan_id: str, frozen: list[str]) -> dict:
    return {
        "event_type": "EngineeringConsensusFreezed",
        "provenance": {"session_id": "old-sess"},
        "payload": {"after_state": {"plan_id": plan_id, "frozen_concept_ids": frozen}},
    }


def _supersede_proposal(sid: str, old_cid: str, replacement_cid: str) -> dict:
    """F-019-5 supersede 形态的 ConceptGraphProposal（entity_id=被取代的旧概念）。"""
    return {
        "event_type": "ConceptGraphProposal",
        "provenance": {"session_id": sid},
        "supersede": {
            "is_supersede": True,
            "superseded_event_id": "evt-old-create",
            "novelty": "codify seed → versioned concept",
            "novelty_type": "new_evidence",
        },
        "payload": {
            "target_entity_type": "concept",
            "transition_type": "superseded",
            "after_state": {
                "proposed_changes": [
                    {
                        "change_type": "supersede_concept",
                        "entity_type": "concept",
                        "entity_id": old_cid,
                        "new_value": {
                            "superseded_reason": "codified",
                            "replacement_concept_id": replacement_cid,
                        },
                    },
                ],
            },
        },
    }


def _by_id(checks: list[dict]) -> dict[str, str]:
    return {str(c["check_id"]): str(c["status"]) for c in checks}


def test_all_pass_when_consumers_covered_and_refs_resolve(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    sid = "s1"
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1"),
            _at_ref(sid, "concept-a"),  # in-batch ref — legal
        ],
    )
    ok, checks, frozen = run_batch_freeze_checks(
        towow, sid, "plan-x", maintainability_clause=_clause("concept-a@v1"),
    )
    assert ok, _by_id(checks)
    assert "concept-a@v1" in frozen
    by = _by_id(checks)
    assert by["consensus.batch_no_dangling_references"] == "passed"


def test_missing_consumer_fails_internal_consistency(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    sid = "s2"
    _write_log(towow, [_concept(sid, "concept-a@v1")])  # no consumer
    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x")
    assert not ok
    by = _by_id(checks)
    assert by["consensus.batch_internal_consistency"] == "failed"
    assert by["consensus.consumer_scan_complete"] == "failed"


def test_forward_dangling_ref_fails(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    sid = "s3"
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1"),
            _at_ref(sid, "future-thing"),  # not in batch, not frozen → dangling
        ],
    )
    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x")
    assert not ok
    assert _by_id(checks)["consensus.batch_no_dangling_references"] == "failed"


def test_ref_to_previously_frozen_batch_resolves(tmp_path: Path) -> None:
    """§8.4: batch-N 引用 batch-(N-1) 已冻结概念 = 合法。"""
    towow = tmp_path / ".towow"
    sid = "s4"
    _write_log(
        towow,
        [
            _prior_freeze("plan-x", ["concept-base@v1"]),
            _concept(sid, "concept-b@v1"),
            _consumer(sid, "concept-b@v1"),
            _at_ref(sid, "concept-base"),  # frozen in a prior batch → legal
        ],
    )
    ok, checks, _ = run_batch_freeze_checks(
        towow, sid, "plan-x", maintainability_clause=_clause("concept-b@v1"),
    )
    assert ok, _by_id(checks)


def test_supersede_proposal_target_not_counted_as_uncommitted(tmp_path: Path) -> None:
    """§8.5 — 批内 supersede 链指向旧概念合法：supersede 目标不是'待创建概念'，
    不得让 batch_concepts_all_committed 误判 fail。

    真实撞上（2026-06-10 plan-landing-closeout）：seed 占位节点 codify-supersede 到 @v1 后
    freeze 被拒——supersede proposal 的 entity_id（旧 seed id）永远不可能在本会话有
    ConceptCreated，旧逻辑把它当 uncommitted hole，任何 spec 合规的 supersede 批都冻不了。
    """
    towow = tmp_path / ".towow"
    sid = "s-sup"
    _write_log(
        towow,
        [
            _concept(sid, "new-thing@v1"),
            _consumer(sid, "new-thing@v1"),
            _supersede_proposal(sid, "old-seed-placeholder", "new-thing@v1"),
        ],
    )
    ok, checks, frozen = run_batch_freeze_checks(
        towow, sid, "plan-x", maintainability_clause=_clause("new-thing@v1"),
    )
    by = _by_id(checks)
    assert by["consensus.batch_concepts_all_committed"] == "passed", by
    assert ok, by
    # supersede 目标不进 frozen_concept_ids（它不是本批创建的概念）
    assert frozen == {"new-thing@v1"}


def test_creation_proposal_without_commit_still_fails(tmp_path: Path) -> None:
    """反向钉死：非 supersede 的 ConceptGraphProposal（真·创建提案）无 ConceptCreated
    仍然是 uncommitted hole → fail。修复只豁免 supersede 形态，不放水。"""
    towow = tmp_path / ".towow"
    sid = "s-cre"
    _write_log(
        towow,
        [
            _concept(sid, "done@v1"),
            _consumer(sid, "done@v1"),
            {
                "event_type": "ConceptGraphProposal",
                "provenance": {"session_id": sid},
                "payload": {
                    "target_entity_type": "concept",
                    "transition_type": "created",
                    "after_state": {
                        "proposed_changes": [
                            {
                                "change_type": "create_concept",
                                "entity_type": "concept",
                                "entity_id": "proposed-but-never-created@v1",
                            },
                        ],
                    },
                },
            },
        ],
    )
    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x")
    by = _by_id(checks)
    assert by["consensus.batch_concepts_all_committed"] == "failed", by
    assert not ok


def test_empty_batch_all_committed_passes(tmp_path: Path) -> None:
    """向后兼容: 空 consensus (零 concept) — all (zero) committed = passed, 非 fail。

    空批也带 cia_downstream_scan (扫了零个概念, downstream=[], 仍 passed + watermark) — 这条 check
    始终在场, 否则 commit gate 会拒空批 freeze。store=None → 从空 events.log 自建 (fallback 路径)。
    """
    towow = tmp_path / ".towow"
    sid = "s5"
    _write_log(towow, [])
    _ok, checks, frozen = run_batch_freeze_checks(towow, sid, "plan-x")
    assert frozen == set()
    by = _by_id(checks)
    assert by["consensus.batch_concepts_all_committed"] == "passed"
    # cia_downstream_scan present even for an empty batch (gate requires it on every freeze).
    assert by["consensus.cia_downstream_scan"] == "passed"
    ev = _scan_evidence(checks)
    assert ev["scanned_concepts"] == []
    assert ev["downstream_entities"] == []
    assert isinstance(ev["projection_watermark"], int)


# ════════════════════════════════════════════════════════════════════════════════
#  M-2.1 §4.1 caller-1 — consensus.cia_downstream_scan (freeze 调 CIA forward_slice)
# ════════════════════════════════════════════════════════════════════════════════


def _record(event_type: EventType, after: dict, seq: int) -> EventRecord:
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


def _cedge(eid: str, src: str, tgt: str, etype: str, seq: int) -> EventRecord:
    return _record(
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


def _scan_evidence(checks: list[dict]) -> dict:
    for c in checks:
        if c["check_id"] == "consensus.cia_downstream_scan":
            return dict(c["evidence"])
    raise AssertionError("consensus.cia_downstream_scan check not present")


def test_cia_downstream_scan_finds_transitive_downstream_with_path_and_watermark(
    tmp_path: Path,
) -> None:
    """(a) 真有边的 impact_graph: 本批 concept-a 沿 dependency 出边走到批外的下游依赖方
    concept-down (depth1) → concept-deep (depth2)。cia_downstream_scan 必须找到这两个传递性下游,
    evidence 带 path (真 edge_id) + projection_watermark, 且永远 passed (record-only, 非阻断)。

    forward = "谁依赖 concept-a" (O-11)。depends_on raw 约定 source=依赖方=下游 (M-0.2 §3.2):
    "concept-down 依赖 concept-a" = edge source=concept-down→target=concept-a; impact_graph 归一化后
    forward(concept-a) 沿出边走到 concept-down → concept-deep。
    """
    towow = tmp_path / ".towow"
    sid = "s-cia-1"
    # disk events drive this_batch + consumer completeness (existing raw-line readers).
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            # concept-down depends_on concept-a → concept-down IS a consumer of concept-a (M12-F4
            # coverage): the published list must cover it (the direct incoming consuming edge).
            _consumer(sid, "concept-a@v1", "concept-down@v1"),
        ],
    )
    # 真 impact_graph: concept-deep depends_on concept-down depends_on concept-a (raw source=下游)。
    # concept-down / concept-deep are NOT in this batch → they are concept-a 的传递性下游依赖方。
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-a@v1", "name": "a"}, 1))
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-down@v1", "name": "d"}, 2))
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-deep@v1", "name": "x"}, 3))
    store._apply(_cedge("e-down-a", "concept-down@v1", "concept-a@v1", "dependency", 4))  # down 依赖 a
    store._apply(_cedge("e-deep-down", "concept-deep@v1", "concept-down@v1", "dependency", 5))  # deep 依赖 down

    ok, checks, frozen = run_batch_freeze_checks(
        towow, sid, "plan-x", store=store, maintainability_clause=_clause("concept-a@v1"),
    )
    assert ok, _by_id(checks)
    assert frozen == {"concept-a@v1"}
    assert _by_id(checks)["consensus.cia_downstream_scan"] == "passed"

    ev = _scan_evidence(checks)
    assert ev["scanned_concepts"] == ["concept-a@v1"]
    # projection_watermark present (证明扫了 + 可复算)。
    assert "projection_watermark" in ev
    assert isinstance(ev["projection_watermark"], int)
    # 找到两个传递性下游, 本批 concept-a 自己被排除。
    downstream = ev["downstream_entities"]
    by_entity = {d["entity_id"]: d for d in downstream}
    assert set(by_entity) == {"concept-down@v1", "concept-deep@v1"}, downstream
    assert "concept-a@v1" not in by_entity, "本批 concept 不算 downstream"
    # depth + 真 edge_id path (沿 impact_graph 概念边)。
    assert by_entity["concept-down@v1"]["depth"] == 1
    assert by_entity["concept-down@v1"]["path"] == ["e-down-a"]
    assert by_entity["concept-deep@v1"]["depth"] == 2
    assert by_entity["concept-deep@v1"]["path"] == ["e-down-a", "e-deep-down"]


def test_cia_downstream_scan_empty_graph_passes_with_empty_evidence_and_watermark(
    tmp_path: Path,
) -> None:
    """(b) 空图 (无边/无下游): cia_downstream_scan 仍 passed — downstream_entities=[] +
    projection_watermark (证明扫了, 可复算)。空结果不是失败, 是"扫过、没下游"。
    """
    towow = tmp_path / ".towow"
    sid = "s-cia-2"
    _write_log(
        towow,
        [
            _concept(sid, "concept-lone@v1"),
            _consumer(sid, "concept-lone@v1"),
        ],
    )
    # store with the concept node but NO edges → forward_slice returns nothing.
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-lone@v1", "name": "l"}, 1))

    ok, checks, _frozen = run_batch_freeze_checks(
        towow, sid, "plan-x", store=store, maintainability_clause=_clause("concept-lone@v1"),
    )
    assert ok, _by_id(checks)
    assert _by_id(checks)["consensus.cia_downstream_scan"] == "passed"
    ev = _scan_evidence(checks)
    assert ev["downstream_entities"] == []
    assert ev["scanned_concepts"] == ["concept-lone@v1"]
    assert "projection_watermark" in ev
    assert isinstance(ev["projection_watermark"], int)


def test_cia_downstream_scan_builds_store_from_disk_when_none_given(tmp_path: Path) -> None:
    """store=None fallback (e.g. CLI 未传 store): run_batch_freeze_checks 从 towow_dir + events.log
    自建 catchup'd store, CIA 仍读到真 impact_graph。这里用真 EventRecords 落盘 → catchup → 真
    非零 watermark (= cursor-1), 验 fallback 路径不是空跑。
    """
    towow = tmp_path / ".towow"
    sid = "s-cia-3"
    (towow / "events").mkdir(parents=True, exist_ok=True)
    # proper EventRecords on disk: readable by BOTH the raw-line readers (this_batch/consumers)
    # AND ProjectionStore.catchup (impact_graph). seqs start at 0.
    this_sess = Provenance(actor_type="system", actor_id="t", session_id=sid)
    # concept-dn pre-exists in a PRIOR session (not this batch) — it is the downstream dependent that
    # depends on the new concept-src (concept-dn depends_on concept-src), so forward_slice(concept-src)
    # reaches it as transitive downstream. concept-dn must NOT be counted as a batch concept needing a
    # consumer (它在 prior 会话, 非本批)。
    prior_sess = Provenance(actor_type="system", actor_id="t", session_id="prior-sess")
    recs = [
        _record(
            EventType.CONCEPT_CREATED,
            {"concept_id": "concept-dn@v1", "name": "dn"},
            0,
        ).model_copy(update={"provenance": prior_sess}),
        _record(
            EventType.CONCEPT_CREATED,
            {"concept_id": "concept-src@v1", "name": "src"},
            1,
        ).model_copy(update={"provenance": this_sess}),
        _record(
            EventType.CONSUMER_LIST_PUBLISHED,
            # concept-dn depends_on concept-src → concept-dn IS a consumer of concept-src; the
            # published list must cover it (M12-F4 coverage gate, direct incoming consuming edge).
            {
                "concept_id": "concept-src@v1",
                "consumers": [
                    {"consumer_type": "concept", "consumer_id": "concept-dn@v1", "usage": "edge:dependency"},
                ],
            },
            2,
        ).model_copy(update={"provenance": this_sess}),
        # depends_on raw 约定 source=依赖方=下游 (M-0.2 §3.2): concept-dn depends_on concept-src
        # = edge source=concept-dn→target=concept-src; 归一化后 forward(concept-src) 走到下游 concept-dn。
        _cedge("e-dn-src", "concept-dn@v1", "concept-src@v1", "dependency", 3),
    ]
    (towow / "events.log").write_text(
        "\n".join(json.dumps(r.model_dump(mode="json"), default=str) for r in recs) + "\n",
        encoding="utf-8",
    )

    # no store passed → function self-builds + catches up.
    ok, checks, frozen = run_batch_freeze_checks(
        towow, sid, "plan-x", maintainability_clause=_clause("concept-src@v1"),
    )
    assert ok, _by_id(checks)
    assert frozen == {"concept-src@v1"}
    ev = _scan_evidence(checks)
    assert ev["scanned_concepts"] == ["concept-src@v1"]
    by_entity = {d["entity_id"]: d for d in ev["downstream_entities"]}
    assert set(by_entity) == {"concept-dn@v1"}, ev["downstream_entities"]
    assert by_entity["concept-dn@v1"]["depth"] == 1
    assert by_entity["concept-dn@v1"]["path"] == ["e-dn-src"]
    # catchup advanced the cursor over 4 events → watermark = cursor-1 = 3 (real, reproducible).
    assert ev["projection_watermark"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# Conflict-20 fix (cross-plan frozen ref + superseded-source skip) — 2026-06-16
# 修 batch_no_dangling_references 误把"引用其他计划已冻结概念"判悬空; Advisor 要求补这 3 个场景。
# ─────────────────────────────────────────────────────────────────────────────


def _at_ref_src(sid: str, source_cid: str, target_name: str) -> dict:
    """AtReferenceAdded 带 source_concept_id (part-2 superseded-source 跳过需要)。"""
    return {
        "event_type": "AtReferenceAdded",
        "provenance": {"session_id": sid},
        "payload": {
            "after_state": {
                "source_concept_id": source_cid,
                "target_concept_name": target_name,
            },
        },
    }


def test_conflict20_cross_plan_frozen_ref_resolves(tmp_path: Path) -> None:
    """Part 1: 引用『其他计划』已冻结的概念应过 (不是 forward-dangling, 是 backward 复用)。"""
    towow = tmp_path / ".towow"
    sid = "s-crossplan"
    _write_log(
        towow,
        [
            # 另一个计划早已冻结 external-concept@v1
            _prior_freeze("plan-OTHER", ["external-concept@v1"]),
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1"),
            # 本批 (plan-x) 引用跨计划已冻结的 external-concept — 修前会被误判悬空
            _at_ref_src(sid, "concept-a@v1", "external-concept"),
        ],
    )
    ok, checks, _ = run_batch_freeze_checks(
        towow, sid, "plan-x", maintainability_clause=_clause("concept-a@v1"),
    )
    by = _by_id(checks)
    assert by["consensus.batch_no_dangling_references"] == "passed", by
    assert ok, by


def test_conflict20_superseded_source_ref_skipped(tmp_path: Path) -> None:
    """Part 2: 源概念在本会话被 supersede → 它的陈旧 @-ref 不再被悬空检查 (继任者重新表达关系)。"""
    towow = tmp_path / ".towow"
    sid = "s-superseded"
    _write_log(
        towow,
        [
            _concept(sid, "c-bad@v1"),
            # c-bad@v1 带一个指向不存在概念的陈旧 @-ref
            _at_ref_src(sid, "c-bad@v1", "stale-nonexistent-target"),
            # c-bad@v1 被 supersede 成 c-good@v2
            _supersede_proposal(sid, "c-bad@v1", "c-good@v2"),
            _concept(sid, "c-good@v2"),
            _consumer(sid, "c-bad@v1"),
            _consumer(sid, "c-good@v2"),
        ],
    )
    _ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x")
    by = _by_id(checks)
    # c-bad@v1 的陈旧 ref 被跳过 → 不悬空
    assert by["consensus.batch_no_dangling_references"] == "passed", by


def test_conflict20_fictional_concept_still_dangling(tmp_path: Path) -> None:
    """门本职保留: 指向『从未在任何计划定义/冻结』的虚构概念, 且源未被 supersede → 仍判悬空。"""
    towow = tmp_path / ".towow"
    sid = "s-fictional"
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1"),
            # 引用一个纯虚构、任何计划都没冻结过的概念 — 必须仍被拦
            _at_ref_src(sid, "concept-a@v1", "totally-fictional-never-existed"),
        ],
    )
    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x")
    by = _by_id(checks)
    assert by["consensus.batch_no_dangling_references"] == "failed", by
    assert not ok, by


# ─────────────────────────────────────────────────────────────────────────────
# M12-F4 (f-glob-consensus-gates-existence-only): consumer_scan_complete 验实质 coverage,
# 非只验 ConsumerListPublished 存在。空清单/漏掉真实消费方 → 现拒 (机器 initial_scan_consumers 复算)。
# ─────────────────────────────────────────────────────────────────────────────


def test_consumer_coverage_gap_fails_scan_check(tmp_path: Path) -> None:
    """A published consumer list that MISSES a real scannable consumer (concept-dep depends on
    concept-a → it IS a consumer of concept-a) → consensus.consumer_scan_complete coverage gap →
    fail. Existence of the ConsumerListPublished is no longer enough."""
    towow = tmp_path / ".towow"
    sid = "s-cov-gap"
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1"),  # EMPTY — omits the real consumer concept-dep
        ],
    )
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-a@v1", "name": "a"}, 1))
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-dep@v1", "name": "d"}, 2))
    # concept-dep depends_on concept-a (raw source=dep→target=a) → dep is a consumer of concept-a.
    store._apply(_cedge("e-dep-a", "concept-dep@v1", "concept-a@v1", "dependency", 3))

    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-x", store=store)
    by = _by_id(checks)
    assert by["consensus.consumer_scan_complete"] == "failed", by
    assert not ok, by
    ev = next(c["evidence"] for c in checks if c["check_id"] == "consensus.consumer_scan_complete")
    gaps = ev["coverage_gaps"]
    assert gaps[0]["concept_id"] == "concept-a@v1"
    assert "concept-dep@v1" in gaps[0]["missing_consumer_ids"], gaps


def test_consumer_coverage_covered_passes(tmp_path: Path) -> None:
    """Publishing the real scannable consumer → no coverage gap → consumer_scan_complete passes."""
    towow = tmp_path / ".towow"
    sid = "s-cov-ok"
    _write_log(
        towow,
        [
            _concept(sid, "concept-a@v1"),
            _consumer(sid, "concept-a@v1", "concept-dep@v1"),  # covers the real consumer
        ],
    )
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-a@v1", "name": "a"}, 1))
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-dep@v1", "name": "d"}, 2))
    store._apply(_cedge("e-dep-a", "concept-dep@v1", "concept-a@v1", "dependency", 3))

    ok, checks, _ = run_batch_freeze_checks(
        towow, sid, "plan-x", store=store, maintainability_clause=_clause("concept-a@v1"),
    )
    by = _by_id(checks)
    assert by["consensus.consumer_scan_complete"] == "passed", by
    assert ok, by


def test_consumer_coverage_no_scannable_consumers_empty_list_passes(tmp_path: Path) -> None:
    """A concept with NO scannable consumers (no incoming edges / no task refs) legitimately
    publishes an empty list → no coverage gap (empty ≠ under-covered when machine finds none)."""
    towow = tmp_path / ".towow"
    sid = "s-cov-empty"
    _write_log(
        towow,
        [
            _concept(sid, "concept-lone@v1"),
            _consumer(sid, "concept-lone@v1"),  # empty — and the scanner finds no consumers either
        ],
    )
    store = ProjectionStore(towow / "graph")
    store._apply(
        _record(EventType.CONCEPT_CREATED, {"concept_id": "concept-lone@v1", "name": "l"}, 1),
    )

    ok, checks, _ = run_batch_freeze_checks(
        towow, sid, "plan-x", store=store, maintainability_clause=_clause("concept-lone@v1"),
    )
    assert _by_id(checks)["consensus.consumer_scan_complete"] == "passed", _by_id(checks)
    assert ok

# ─────────────────────────────────────────────────────────────────────────────
# Conflict 33 (SPEC-CONFLICT-RESOLUTION-LEDGER): §8.4 resolvable 的 forward/backward 分界是
# 『存在 (committed ConceptCreated)』而非『冻结过』—— 引用已 committed 但从未被任何计划冻结的
# 既有概念 (migration / 能力注册 / pre-freeze 时代概念) 是合法 backward ref, 不再误判 dangling;
# 引用真不存在的未来概念仍拒 (原语义锁死, 见 test_conflict20_fictional_concept_still_dangling)。
# ─────────────────────────────────────────────────────────────────────────────


def test_conflict33_committed_never_frozen_concept_ref_resolves(tmp_path: Path) -> None:
    """引用『概念图里已 committed、但从未出现在任何 EngineeringConsensusFreezed』的既有概念 → 过。"""
    towow = tmp_path / ".towow"
    sid = "s-c33"
    _write_log(
        towow,
        [
            _concept(sid, "concept-new@v1"),
            _consumer(sid, "concept-new@v1"),
            # 引用一个存在于概念图、但没有任何 plan 冻结过的既有概念
            _at_ref_src(sid, "concept-new@v1", "legacy-never-frozen@v1"),
        ],
    )
    # store 的 concept_graph 里有该既有概念 (committed ConceptCreated 的物化产物)
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-new@v1", "name": "n"}, 1))
    store._apply(
        _record(EventType.CONCEPT_CREATED, {"concept_id": "legacy-never-frozen@v1", "name": "l"}, 2),
    )

    ok, checks, _ = run_batch_freeze_checks(
        towow, sid, "plan-c33", store=store, maintainability_clause=_clause("concept-new@v1"),
    )
    by = _by_id(checks)
    assert by["consensus.batch_no_dangling_references"] == "passed", by
    assert ok, by


def test_conflict33_nonexistent_target_still_dangling_with_store(tmp_path: Path) -> None:
    """负向锁: store 在场时, 引用概念图里也不存在的目标 → 仍 dangling (§8.4 forward 拒不放松)。"""
    towow = tmp_path / ".towow"
    sid = "s-c33-neg"
    _write_log(
        towow,
        [
            _concept(sid, "concept-new@v1"),
            _consumer(sid, "concept-new@v1"),
            _at_ref_src(sid, "concept-new@v1", "future-batch-concept-not-yet-created"),
        ],
    )
    store = ProjectionStore(towow / "graph")
    store._apply(_record(EventType.CONCEPT_CREATED, {"concept_id": "concept-new@v1", "name": "n"}, 1))

    ok, checks, _ = run_batch_freeze_checks(towow, sid, "plan-c33", store=store)
    by = _by_id(checks)
    assert by["consensus.batch_no_dangling_references"] == "failed", by
    assert not ok, by
