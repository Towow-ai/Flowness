"""A5 (2026-07-02 OOM 事故根治): EventLogIndexStore._atomic_write 的 tmp 文件名唯一化。

崩机根因链的一环: 旧版 `_atomic_write` 用确定性 tmp 文件名 (``<name>.idx.tmp``)。当晚多个
towow 进程 (orchestrator/monitor/CLI) 并发冷启动、都触发全量 persist(重写 340MB
event_id.idx) 时，共享同一个 tmp 路径 —— 先 `tmp.replace(path)` 的那个把 tmp 文件"拿走"，
后一个的 `tmp.replace` 找不到 tmp 文件 → FileNotFoundError (ENOENT)，被 `_build_and_persist_index`
吞成 warning，缓存永远写不成，下次冷启动又全量重建 —— 恶性循环。

本测试钉住两件事:
  1. 结构性: 同一路径连续多次 persist，每次生成的 tmp 文件名都不同 (pid+uuid 唯一化)。
  2. 真并发: 多个【真实进程】同时对同一目录并发 persist，全部成功，无 ENOENT/warning，
     且结束状态是某一次 persist 的完整快照 (不是撞坏的半成品)。
"""

from __future__ import annotations

import multiprocessing
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log.index import EventLogIndexStore
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


def _make_record(seq: int) -> EventRecord:
    return EventRecord(
        event_id=f"evt-a5-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": "c-1", "touch_type": "read"},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="a5-test", task_id="t-1"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_tmp_filenames_are_unique_per_write(tmp_path: Path) -> None:
    """连续两次 persist() 到同一路径, 观察到的 tmp 文件名 (写入瞬间) 不相同。"""
    store = EventLogIndexStore(tmp_path)
    records = [_make_record(1)]

    from pathlib import Path as PathCls

    captured: list[str] = []
    orig = PathCls.write_text

    def _spy_write_text(self: PathCls, data: str, encoding: str | None = None) -> int:
        if self.name.startswith("event_id.idx.") and self.name.endswith(".tmp"):
            captured.append(self.name)
        return orig(self, data, encoding=encoding)

    PathCls.write_text = _spy_write_text  # type: ignore[method-assign,assignment]
    try:
        store.persist(records)
        store.persist(records)
    finally:
        PathCls.write_text = orig  # type: ignore[method-assign]

    assert len(captured) == 2, f"expected 2 tmp writes, got {captured}"
    assert len(set(captured)) == 2, f"tmp 文件名必须每次不同 (唯一化), 实得 {captured}"


def _persist_worker(towow_dir: str, seq_base: int) -> str:
    """子进程: 对同一目录重复 persist 若干次, 返回 'ok' 或异常字符串 (供父进程汇总断言)。"""
    import uuid as _uuid
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime
    from pathlib import Path as PathCls

    from towow.l0.event_log.index import EventLogIndexStore as Store
    from towow.schemas.enums import ActorType as AT
    from towow.schemas.enums import BaseClassification as BC
    from towow.schemas.enums import EventCategory as EC
    from towow.schemas.enums import EventType as ET
    from towow.schemas.enums import SubjectEntityType as SET
    from towow.schemas.enums import SubjectRole as SR
    from towow.schemas.event_intent import Subject as Subj
    from towow.schemas.event_intent import Supersede as Sup
    from towow.schemas.event_record import EventRecord as ER
    from towow.schemas.event_record import Provenance as Prov

    recs = [
        ER(
            event_id=f"evt-a5-{seq_base}-{_uuid.uuid4().hex}",
            sequence_number=seq_base * 1000 + i,
            timestamp=_datetime.now(_UTC),
            record_hash=f"sha256:{_uuid.uuid4().hex}",
            local_intent_id=f"li-{_uuid.uuid4().hex[:8]}",
            event_type=ET.NODE_TOUCHED,
            event_category=EC.ENVELOPE,
            payload={"target_entity_id": "c-1", "touch_type": "read"},
            provenance=Prov(actor_type=AT.SYSTEM.value, actor_id="a5-test", task_id="t-1"),
            base_classification=BC.DISCARDABLE_NOISE,
            supersede=Sup(is_supersede=False),
            subjects=[Subj(entity_type=SET.CONCEPT, entity_id="c-1", role=SR.PRIMARY)],
            schema_version="1.0.0",
        )
        for i in range(20)
    ]
    try:
        store = Store(PathCls(towow_dir))
        for _ in range(5):
            store.persist(recs)
        return "ok"
    except Exception as e:  # 子进程把异常序列化回父进程做断言
        return f"FAIL: {type(e).__name__}: {e}"


def test_concurrent_persist_from_multiple_processes_never_races(tmp_path: Path) -> None:
    """多个真实进程并发对同一目录重复 persist, 全部成功、无 ENOENT (2026-07-02 事故复现场景)。"""
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=4) as pool:
        results = pool.starmap(_persist_worker, [(str(tmp_path), i) for i in range(4)])

    failures = [r for r in results if r != "ok"]
    assert not failures, f"并发 persist 不应有任何进程失败, 实得: {failures}"

    # 结束态可加载 (不是撞坏的半成品)
    store = EventLogIndexStore(tmp_path)
    loaded = store.load()
    assert loaded is not None, "并发写完后索引应能正常 load (不是损坏状态)"

    # 目录里不该留下任何孤儿 .tmp 文件 (全部成功 replace 掉了)
    leftover_tmp = list(tmp_path.glob("**/*.tmp"))
    assert not leftover_tmp, f"不该有残留 .tmp 文件, 实得: {leftover_tmp}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
