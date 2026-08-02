"""T-RMD-SEQ-GUARD — EventLog sequence_number 唯一性: 写时守卫 (段①) + 巡检 emit (段②) + 投影不丢 (段③).

finding f-sub-l0-seq-uniqueness-blindspot (substrate audit, GC-04 critical): 账本 base 区 553 个 seq 真
碰撞 (同 seq 不同 event_id 不同事件, 06-10 三次回退遗存)。命门 "seq 单调唯一" 这条 L0 最底层不变量
【既没写时守卫】(_validate_uniqueness 只验 event_id)【也没完整性审计能查】(record_hash 含 seq 故每条碰撞
各自合法 → 对 audit_integrity / reconstructability 双盲)。伤害: 读路径按 seq 去重 (last-wins) → 每次投影
重建静默丢真事件 (concept_graph −9 / task_graph −14 / finding_lifecycle −1, 下游有引用)。

本组是 @live-fire-machine-check-contract 的 per-task 门 (code-path-real, 进程内集成测试, 区分
daemon-vs-CLI provenance; 不读生产 .towow, 不声称 production-real — 见 @per-task-vs-production-real-boundary)。
machine_check test_selector:
  · test_collision_write_rejected            ← 段① 写时守卫 fail-closed (done_criterion 1a)
  · test_audit_integrity_seq_patrol_emits    ← 段② 巡检经 live 路径 emit canonical 报告事件 (done_criterion 1b)
  · test_projection_does_not_drop_dup_seq    ← 段③ 投影计数不退行 (done_criterion 2)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path


# ─── helpers (real EventLog over a JSONL events.log; tmp_path is under system-tmp so fs_guard allows) ──


def _touch_intent(marker: int | str) -> EventIntent:
    """A valid path-B EventIntent (NodeTouched) for write_direct — seq is derived at append time."""
    return EventIntent(
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "concept",
            "target_entity_id": f"n-{marker}",
            "touch_type": "write",
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="seq-guard-test"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=f"n-{marker}", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _raw_append_dup_seq(log_path: Path, seq: int, marker: str) -> EventRecord:
    """Raw-append a record sharing ``seq`` with an existing one but a DISTINCT event_id.

    Simulates the 06-10 rollback dup-seq pollution: it lands at the FILE level (bypassing the write
    guard, exactly as a git restore / overwrite would), so the patrol/read-path fixes — not the write
    guard — are what must cope with it.
    """
    rec = EventRecord(
        event_id=f"evt-dup-{marker}-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{marker}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_type": "concept", "target_entity_id": f"dup-{marker}", "touch_type": "write"},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="rollback-residue"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=f"dup-{marker}", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(rec.model_dump_json() + "\n")
    return rec


def _commit_rec(seq: int, *, event_id: str, envelope_event_id: str) -> EventRecord:
    """A CommitAccepted record (feeds the commit_history projection)."""
    return EventRecord(
        event_id=event_id,
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"envelope_event_id": envelope_event_id},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="seq-guard-test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-anchor", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


# ─── 段① 写时守卫 (done_criterion 1a) ──────────────────────────────────────────────


def test_collision_write_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """段①: a write whose derived seq collides with an already-present seq is rejected fail-closed and
    does NOT land. Reads all_records() to confirm no dup-seq event leaked onto disk.

    Trigger reproduces the F-019-12 / 06-10 failure mode: a stale on-disk tail read derives a seq that
    is already used. Without the guard the old write path silently appended a dup-seq record; with it the
    write raises and nothing lands.
    """
    log_path = tmp_path / ".towow" / "events.log"
    log = EventLog(log_path)
    for i in range(3):
        log.write_direct(_touch_intent(i))  # seqs 0, 1, 2
    assert [r.sequence_number for r in log.all_records()] == [0, 1, 2]

    # stale tail read → derives seq 1 (already present) → guard must fail-closed.
    monkeypatch.setattr(log, "_read_max_seq_on_disk", lambda: 0)
    with pytest.raises(ValueError, match="duplicate sequence_number"):
        log.write_direct(_touch_intent("collision"))

    # fail-closed = not landed: re-open from disk and confirm the committed stream is unchanged + no dup.
    after = EventLog(log_path).all_records()
    seqs = [r.sequence_number for r in after]
    assert seqs == [0, 1, 2], "collision write leaked onto disk despite the guard"
    assert len(seqs) == len(set(seqs)), "a dup-seq record landed despite the fail-closed write guard"


# ─── 段② 巡检 emit canonical 报告事件 (done_criterion 1b — live-fire) ─────────────────


def test_audit_integrity_seq_patrol_emits(tmp_path: Path) -> None:
    """段②: the audit_integrity seq patrol detects a dup-seq and emits a canonical DebtRegistered report
    event via the live write path, with NON-INTERACTIVE provenance. Asserts the event EXISTS in
    EventLog.all_records() + correct TYPE + non-interactive provenance — the @live-fire-machine-check
    contract's three checks, read from the real (in-process) ledger.

    Non-interactive proof: provenance.actor_type == 'system' (ActorType.SYSTEM) — a system-internal
    automatic emit, NOT a human CLI session (the contract's load-bearing discriminator against demo≈mock).
    """
    log_path = tmp_path / ".towow" / "events.log"
    log = EventLog(log_path)
    log.write_direct(_touch_intent(0))            # seq 0 (event A)
    _raw_append_dup_seq(log_path, 0, "B")          # seq 0 again, distinct event_id → dup-seq pollution

    reopened = EventLog(log_path)
    # detection: the patrol surfaces the dup-seq the integrity defenses were blind to.
    dup = reopened.audit_sequence_uniqueness()
    assert 0 in dup, "seq patrol did not surface the dup-seq"
    assert len(dup[0]) == 2, "seq patrol did not report both distinct event_ids at the colliding seq"

    # live emit: running the patrol produces a canonical report event on the ledger.
    _dup, record = reopened.audit_integrity_seq_patrol()
    assert record is not None, "seq patrol did not emit a report event"
    assert record.event_type is EventType.DEBT_REGISTERED

    # the report event is canonical (live signature) — readable via all_records() from a fresh open.
    after = EventLog(log_path).all_records()
    debts = [r for r in after if r.event_type is EventType.DEBT_REGISTERED]
    assert len(debts) == 1, "the seq-patrol report event is not in the canonical ledger"
    rep = debts[0]
    assert rep.payload["debt_id"] == "debt-ledger-seq-uniqueness"
    assert rep.payload["debt_type"] == "deferral"
    assert "T-RMD-SEQ-FORENSICS" in rep.payload["depends_on"]
    # NON-INTERACTIVE provenance — the live-fire signature (system-internal, not a human CLI session).
    assert rep.provenance.actor_type == ActorType.SYSTEM.value
    assert rep.provenance.actor_id == "l0-seq-integrity-audit"

    # idempotent: a second patrol run does not register a duplicate report.
    _dup2, record2 = EventLog(log_path).audit_integrity_seq_patrol()
    assert record2 is None
    debts2 = [r for r in EventLog(log_path).all_records() if r.event_type is EventType.DEBT_REGISTERED]
    assert len(debts2) == 1, "patrol re-emitted a duplicate report (not idempotent)"


# ─── 段③ 投影不静默丢 dup-seq 真事件 (done_criterion 2) ───────────────────────────────


def test_projection_does_not_drop_dup_seq(tmp_path: Path) -> None:
    """段③: a projection rebuilt over a dup-seq ledger keeps ALL distinct events — count does not regress.

    Before the fix get_events_in_range deduped by seq (last-wins via _by_sequence), so a full
    rebuild()/recompute_one() silently dropped distinct events sharing a seq. Identity is event_id, not
    seq: two CommitAccepted events colliding on a seq are two real commits and must both be counted.
    """
    records = [
        _commit_rec(s, event_id=f"evt-c{s:02d}", envelope_event_id=f"env-{s}")
        for s in range(1, 6)
    ]
    # distinct CommitAccepted colliding on seq 5 (06-10 rollback shape) — must NOT be dropped.
    records.append(_commit_rec(5, event_id="evt-c05dup", envelope_event_id="env-5-collision"))

    log_path = tmp_path / "events.log"
    log_path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")
    log = EventLog(log_path)

    store = ProjectionStore(tmp_path / "graph")
    store.rebuild(log)
    commits = store.read("commit_history")["commits"]
    assert len(commits) == 6, "projection dropped a distinct dup-seq event (段③ regression: seq-dedup loss)"
    # both distinct envelopes survive (the dropped one used to vanish).
    env_ids = {c.get("envelope_event_id") for c in commits}
    assert "env-5-collision" in env_ids, "the colliding distinct event was dropped by the rebuild"
    assert "env-5" in env_ids, "the seq-5 baseline event went missing"
