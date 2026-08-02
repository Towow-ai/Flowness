"""RUN-038 L0增强 M-0.3 Patch 2 — _read_additional_events 绑定 as_of_seq (负对照)。

spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md Patch 2 (L852..L868):
  additional_event_reads: read_events_by_type(type, until_seq=bundle.as_of_seq)。
  seq > as_of_seq 的 event 不得被使用 (时间裂缝)。

直接喂 helper 不同 as_of_seq, 证明截点边界真生效 (反 vacuous): 同一组 event, 抬高 as_of_seq
看到的多, 压低 as_of_seq 看到的少; 截点恰好卡在某 event 上是含 (<=)。

NatureJudgmentCaptured 是 path-A 事件 (走 commit gate, 非 write_direct), 故经 attempt_commit 落库;
返回真实 seq 由 helper 的边界断言使用。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.l0.capsule import assemble_capsule
from towow.l0.capsule.pipeline import _read_additional_events
from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    BaseClassification,
    EventCategory,
    EventType,
    SceneType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _prov() -> ProvenanceHint:
    return ProvenanceHint(actor_type="agent", actor_id="m11", task_id="t")


def _injected_declared(log: EventLog, capsule_event_id: str) -> list[dict[str, object]]:
    return [
        {"obligation_id": rec.payload["obligation_id"], "status": "maintained"}
        for rec in log.all_records()
        if rec.event_type is EventType.OBLIGATION_ACTIVATED
        and rec.provenance.causation_event_id == capsule_event_id
    ]


def _envelope(capsule_event_id: str, log: EventLog) -> EventIntent:
    env_id = f"env-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "session_id": "aer-unit",
            "capsule_compiled_event_id": capsule_event_id,
            "self_check": {"passed": True, "checks_run": [], "blocking_checks": []},
            "active_obligations_declared": _injected_declared(log, capsule_event_id),
        },
        provenance_hint=_prov(),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=env_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _commit_njc(log: EventLog, store: ProjectionStore, gate: CommitGate, tag: str) -> tuple[str, int]:
    res = assemble_capsule(
        event_log=log, projection_store=store,
        scene_type=SceneType.INTERVIEW, task_id=f"t-{tag}", target_fork_id=f"f-{tag}",
    )
    njc = EventIntent(
        local_intent_id=f"njc-{uuid.uuid4().hex[:12]}",
        event_type=EventType.NATURE_JUDGMENT_CAPTURED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={"note": tag},
        provenance_hint=_prov(),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    records = gate.attempt_commit(_envelope(res.capsule_compiled_event_id, log), [njc])
    store.catchup(log)
    rec = next(r for r in records if r.event_type is EventType.NATURE_JUDGMENT_CAPTURED)
    return rec.event_id, rec.sequence_number


def _setup(towow: Path) -> tuple[EventLog, ProjectionStore, CommitGate]:
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    log = EventLog(towow / "events.log")
    gate = CommitGate(log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())
    return log, ProjectionStore(towow / "graph"), gate


def test_until_seq_bound_excludes_later_events(tmp_path: Path) -> None:
    log, store, gate = _setup(tmp_path / ".towow")
    id1, seq1 = _commit_njc(log, store, gate, "first")
    id2, seq2 = _commit_njc(log, store, gate, "second")
    assert seq2 > seq1

    # as_of_seq 卡在 seq1 → 只看到第一条 (Patch 2 边界: <=, seq2 被排除)。
    bounded = _read_additional_events(log, [EventType.NATURE_JUDGMENT_CAPTURED], as_of_seq=seq1)
    assert {r.event_id for r in bounded} == {id1}, "as_of_seq=seq1 → seq2 的 event 是时间裂缝, 不得消费"

    # as_of_seq 抬到 seq2 → 两条都看到 (证明 helper 不是恒返回一条 / 恒空)。
    both = _read_additional_events(log, [EventType.NATURE_JUDGMENT_CAPTURED], as_of_seq=seq2)
    assert {r.event_id for r in both} == {id1, id2}

    # as_of_seq 压到 seq1-1 → 一条都看不到。
    none_seen = _read_additional_events(log, [EventType.NATURE_JUDGMENT_CAPTURED], as_of_seq=seq1 - 1)
    assert none_seen == []


def test_empty_template_reads_nothing(tmp_path: Path) -> None:
    log, store, gate = _setup(tmp_path / ".towow")
    _commit_njc(log, store, gate, "x")
    # 模板未声明任何 additional_event_reads → 空列表 (不是恒读全部)。
    assert _read_additional_events(log, [], as_of_seq=10**9) == []


def test_dedup_and_seq_sorted(tmp_path: Path) -> None:
    log, store, gate = _setup(tmp_path / ".towow")
    id1, seq1 = _commit_njc(log, store, gate, "a")
    id2, seq2 = _commit_njc(log, store, gate, "b")
    # 同类型在列表里重复声明 → 去重, 不重复消费; 结果按 seq 升序。
    refs = _read_additional_events(
        log,
        [EventType.NATURE_JUDGMENT_CAPTURED, EventType.NATURE_JUDGMENT_CAPTURED],
        as_of_seq=seq2,
    )
    assert [r.event_id for r in refs] == [id1, id2]
    assert [r.seq for r in refs] == [seq1, seq2]
