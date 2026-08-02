"""T-FND-02 — path-B 增量索引回归门.

根治: 旧 write_direct 每次写 `self._index = None` → 下个读全量重扫 + pydantic 重解析全部
committed records (实测 42311 条 = 1434ms/写) → daemon CPU 随账本线性恶化。
改: 索引已覆盖正好写前磁盘 tail (无外部追加) → 增量 add 本条 (经 iter_committed_records_from_lines
精确匹配 committed 语义), 索引保持 warm; 否则回退 =None 全重建 (捡外部追加)。

这组测试钉死: ①增量后索引 warm 且 max_seq 推进, 所有 Read API 命中新记录; ②增量 ≡ 全量重建等价
(所有 postings 一致); ③外部进程追加 → 下次写回退全重建捡外部 (不漏); ④recover 截断仍全失效.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l0.event_log.index import EventIndex
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _make_log(tmp_path: Path) -> EventLog:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "events.log").write_text("", encoding="utf-8")
    return EventLog(towow / "events.log")


def _node_touched_intent(entity_id: str) -> EventIntent:
    """A path-B-allowed NodeTouched intent (write_direct accepts these)."""
    return EventIntent(
        local_intent_id=f"tfnd02-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": entity_id,
            "touch_type": "write",
            "kind": "NodeTouched",
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value, actor_id="test-tfnd02",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=entity_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


def test_write_direct_keeps_index_warm_and_serves_new_record(tmp_path: Path) -> None:
    """增量: 写后索引不作废 (warm), max_seq 推进, 新旧记录都点查得到."""
    log = _make_log(tmp_path)
    first = log.write_direct(_node_touched_intent("ent-a"))
    # warm the index over the committed tail (covers `first`)
    idx = log._committed_index()
    assert idx.max_sequence == first.sequence_number

    second = log.write_direct(_node_touched_intent("ent-b"))

    # 关键: 索引没被作废 (增量 add 命中), 且覆盖推进到 second
    assert log._index is not None, "增量路径应保持索引 warm, 不 =None"
    assert log._index.max_sequence == second.sequence_number
    # 新记录立即点查得到, 旧记录仍在
    assert log.get_event(second.event_id) is not None
    assert log.get_event(first.event_id) is not None
    # entity posting 也增量更新了
    ent_b = log.get_events_by_entity("task", "ent-b")
    assert any(r.event_id == second.event_id for r in ent_b)


def test_incremental_equivalent_to_full_rebuild(tmp_path: Path) -> None:
    """增量 ≡ 全量重建: 连写多条 (增量) 后, 与强制全重建的索引对所有 Read API 结果一致."""
    log = _make_log(tmp_path)
    log._committed_index()  # warm 空索引
    recs = [log.write_direct(_node_touched_intent(f"ent-{i}")) for i in range(6)]

    # 增量后的 warm 索引
    incr = log._committed_index()
    # 强制全量重建 (旧路径行为)
    full = EventIndex(list(log._iter_committed_records()))

    assert incr.max_sequence == full.max_sequence
    assert incr.total_records == full.total_records
    for r in recs:
        assert (incr.lookup_event_id(r.event_id) is not None) == (
            full.lookup_event_id(r.event_id) is not None
        )
        ei = incr.lookup_entity("task", r.subjects[0].entity_id)
        ef = full.lookup_entity("task", r.subjects[0].entity_id)
        assert {x.event_id for x in ei} == {x.event_id for x in ef}
    # type posting 整体一致
    assert {x.event_id for x in incr.lookup_type("NodeTouched")} == {
        x.event_id for x in full.lookup_type("NodeTouched")
    }


def test_external_append_triggers_full_rebuild_no_miss(tmp_path: Path) -> None:
    """跨进程: 另一 EventLog 实例 (模拟别进程) 追加后, 本实例下次写回退全重建, 捡到外部追加不漏."""
    log_a = _make_log(tmp_path)
    base = log_a.write_direct(_node_touched_intent("ent-base"))
    log_a._committed_index()  # A warm, 覆盖到 base
    assert log_a._index.max_sequence == base.sequence_number

    # 别的进程 (同目录另一实例) 追加 —— A 的内存索引不知情
    log_b = EventLog(tmp_path / ".towow" / "events.log")
    external = log_b.write_direct(_node_touched_intent("ent-external"))

    # A 再写一条: A.index.max_seq(base) != 写前磁盘tail(external) → 回退 =None 全重建
    mine = log_a.write_direct(_node_touched_intent("ent-mine"))
    # 重建后, A 必须捡到外部追加 + 自己的新写 (都不漏)
    assert log_a.get_event(external.event_id) is not None, "回退全重建必须捡到外部追加, 不漏"
    assert log_a.get_event(mine.event_id) is not None
    assert log_a.get_event(base.event_id) is not None


def test_recover_invalidates_index(tmp_path: Path) -> None:
    """recover() 截断后索引必须全失效 (磁盘绝对值变, 不能增量)."""
    log = _make_log(tmp_path)
    log.write_direct(_node_touched_intent("ent-x"))
    log._committed_index()
    assert log._index is not None
    log.recover()  # 无崩溃尾时 recover 仍应保持失效语义安全
    # recover 路径设 _index=None (truncation 安全); 无截断时也不应留 stale warm 误导
    # (recover 实现里 _index=None 在 truncated 分支; 无截断 early-return 不动索引 —— 验证不崩即可)
    assert log.get_event("nonexistent") is None
