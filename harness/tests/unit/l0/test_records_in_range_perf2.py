"""f-perf2-catchup-full-materialize-for-narrow-seq-range 回归测试。

背景 (ledger-perf-diagnosis 确诊, 性能治理第二轮 P0, 2026-07-18): `EventLog.get_events_in_range`
(ProjectionStore.catchup / recompute_one 的读路径, 被几乎每条只读 CLI 命令的 `store.catchup
(event_log)` 引导调用) 旧实现是 `self._committed_index().records()` 全量物化再按 seq 过滤 ——
`LazyEventIndex.records()` 是"唯一付全量 pydantic 解析成本"的查询, 即便请求的 range 只有 0~几条新
事件 (投影已追平的常见情形) 也照付。现网量得: status / vitality / concept slice 三条命令冷启动后
的稳态 (已有持久 .idx, 走 LazyEventIndex adopt 快路径) 峰值 RSS 均 ~5.5GB / ~28s —— 完全一致的signature,
根因就是这条共享的 catchup 引导路径。

修法: LazyEventIndex 新增 `_seqs` (与 `_order` 对齐的 sequence_number 平行数组, 由
EventLogIndexStore.load() 里那条本来就在扫的 event_id 正则顺手多捕获一个字段拿到, 零额外扫描成本)
+ `records_in_range(start_seq, end_seq)`: 先按 `_seqs` 做纯 int 比较预筛 (零解析成本), 只对落在
range 内的候选付真正的 `_materialize` (pydantic 解析)。EventIndex (eager) 也有对应的
`records_in_range` (基类版, 已在内存里, 纯过滤无重扫成本)。

本文件钉三件事:
  1. test_records_in_range_preserves_distinct_dup_seq_events — dup-seq (T-RMD-SEQ-GUARD: 两条
     DISTINCT event_id 撞同一 sequence_number, 06-10 回退遗留的真实形状) 在新路径下两条都留, 不因
     "按 seq 去重"静默丢真事件 —— 与旧 `records()`+filter 参照实现字节对字节一致。
  2. test_records_in_range_respects_boundaries — range 两端 (start_seq-1 / end_seq+1) 正确被排除。
  3. test_lazy_records_in_range_skips_materialize_outside_range — 字节级预筛真的在跳过工作: range
     外的记录不该触发 `_materialize` (spy 计数 < 全量记录数), 证明不是"先物化全部再过滤"换皮。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l0.event_log.index import LazyEventIndex
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


def _rec(seq: int, event_id: str, task_id: str) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.COMMIT_ACCEPTED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"task_id": task_id},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="records-in-range-test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _write_log(log_path: Path, records: list[EventRecord]) -> EventLog:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")
    return EventLog(log_path)


def _dup_seq_records() -> list[EventRecord]:
    """9 distinct seqs (0..8) + a seq-5 collision with a DISTINCT event_id (06-10 rollback shape)."""
    records = [_rec(s, f"evt-r{s:02d}", f"T-{s}") for s in range(9)]
    records.append(_rec(5, "evt-r05dup", "T-5-collision"))  # distinct event_id sharing seq=5
    return records


def _reference_range(log: EventLog, start_seq: int, end_seq: int) -> set[str]:
    """Ground truth: the OLD records()+filter behaviour, reimplemented inline (not calling
    get_events_in_range so this test doesn't validate production code against itself)."""
    return {
        rec.event_id
        for rec in log.committed_index().records()
        if start_seq <= rec.sequence_number <= end_seq
    }


def test_records_in_range_preserves_distinct_dup_seq_events(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    writer = _write_log(log_path, _dup_seq_records())
    reference = _reference_range(writer, 0, 8)
    assert reference == {f"evt-r{s:02d}" for s in range(9)} | {"evt-r05dup"}, (
        "test precondition: both distinct events sharing seq=5 must be in the reference set"
    )
    writer.committed_index()  # force persist so the reopen below can adopt a LazyEventIndex

    reader = EventLog(log_path)  # fresh process-like open — adopts the persisted .idx
    assert isinstance(reader._index, LazyEventIndex), (
        "test precondition: reopen should adopt a LazyEventIndex (the realistic cold-CLI scenario "
        "this fix targets) — if this fails the adopt-gate behavior changed and this test's premise "
        "no longer holds"
    )

    got = {rec.event_id for rec in reader.get_events_in_range(0, 8)}
    assert got == reference, (
        f"get_events_in_range diverged from full-scan reference: "
        f"missing={reference - got} extra={got - reference}"
    )
    assert "evt-r05dup" in got, "dup-seq event must survive (event_id identity, not seq identity)"
    assert "evt-r05" in got, "the other seq=5 event must also survive"


def test_records_in_range_respects_boundaries(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    writer = _write_log(log_path, _dup_seq_records())
    writer.committed_index()

    reader = EventLog(log_path)
    assert isinstance(reader._index, LazyEventIndex)

    got = {rec.sequence_number for rec in reader.get_events_in_range(2, 4)}
    assert got == {2, 3, 4}, "start_seq-1 (=1) and end_seq+1 (=5) must be excluded"

    empty = reader.get_events_in_range(20, 30)
    assert empty == [], "a range with no matching records returns empty, not an error"


def test_lazy_records_in_range_skips_materialize_outside_range(tmp_path: Path) -> None:
    """字节级预筛真的在跳过工作: range 外的记录不该触发 _materialize。"""
    records = [_rec(s, f"evt-w{s:03d}", f"T-{s}") for s in range(50)]
    log_path = tmp_path / "events.log"
    writer = _write_log(log_path, records)
    writer.committed_index()

    reader = EventLog(log_path)
    assert isinstance(reader._index, LazyEventIndex)

    real_materialize = reader._index._materialize
    counter = {"n": 0}

    def _spy(event_id: str):
        counter["n"] += 1
        return real_materialize(event_id)

    reader._index._materialize = _spy  # type: ignore[method-assign]

    got = reader.get_events_in_range(10, 12)
    assert {rec.sequence_number for rec in got} == {10, 11, 12}
    assert counter["n"] < 50, (
        f"byte pre-filter should have skipped _materialize for records outside [10,12] out of 50 "
        f"total, but it was called {counter['n']} times — pre-filter regression (records_in_range "
        "is materializing the whole committed stream again, not just the matching subset)"
    )
    assert counter["n"] <= 3, f"expected exactly the 3 in-range records to be materialized, got {counter['n']}"
