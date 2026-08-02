"""A2/A3 (2026-07-02 OOM 崩机根治): adopt-then-catch-up a persisted .idx instead of
discard-and-rebuild-on-exact-match.

崩机勘查: 旧版 `__init__` 要求 loaded.max_sequence 精确等于刚派生的 committed tail 才信任
(`==` 门); daemon 每 ~6s 一写, 冷启动读到的 .idx 几乎永远差一两条 → 门几乎从不成立 → 冷启动
几乎总付全量重建代价 (16秒/3GB 峰值的大头)。改为: 只要落后【一个活跃段之内】就 adopt + 从
活跃段折入差量; 落后更多 (跨越整段轮转) 或有截断幽灵记录风险则安全回落到原有的丢弃重建路径。

本文件钉住 test_run055 契约测试之外的场景:
  1. 精确匹配 (旧 `==` 情形) 仍然 adopt 成功 (向后兼容, 且现在多了 spot-check 保护)。
  2. 落后【一个活跃段内的若干条】→ adopt + 追平 (核心新能力)。
  3. 落后【超过一个活跃段】(跨过一次真轮转) → 安全回落 (self._index is None, 下次读全量重建)。
  4. 幽灵记录防护: 手工伪造一份"seq 数对得上但 event_id 对不上"的 .idx (模拟 truncate 后在同一
     seq 上写了不同内容) → 必须拒绝 adopt, 不能让被截断/重写的幻影记录悄悄服务给读者。
  5. 空日志边界: 全新账本 (max_sequence=-1) 时 adopt 路径不炸。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.index import EventLogIndexStore
from towow.l0.event_log.segments import ordered_segment_paths
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


def _node_touched(*, local_intent_id: str, entity_id: str = "c-1") -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_exact_match_still_adopts(tmp_path: Path) -> None:
    """旧 `==` 情形 (persist 后没有新 append) 仍然 adopt 成功, 不退化。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path)
    log.write_direct(_node_touched(local_intent_id="i-1"))
    log.get_events_by_task("t-1")  # 触发 persist

    reopened = EventLog(log_path)
    assert reopened._index is not None
    assert reopened._index.max_sequence == 0


def test_adopt_and_catch_up_within_active_segment(tmp_path: Path) -> None:
    """核心新能力: 落后【活跃段内若干条】(daemon 常态写模式) → adopt + 折入差量, 不全量重建。"""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path)
    writer.write_direct(_node_touched(local_intent_id="i-1"))
    writer.get_events_by_task("t-1")  # persist .idx covering seq 0

    # 模拟持续写入 (还在同一个活跃段内, 未轮转)
    for i in range(2, 6):
        writer.write_direct(_node_touched(local_intent_id=f"i-{i}"))

    reader = EventLog(log_path)
    assert reader._index is not None, "落后但在活跃段内 → 应 adopt (不是丢弃)"
    assert reader._index.max_sequence == 4  # 0..4 共 5 条
    assert len(reader.get_events_by_task("t-1")) == 5


def test_falls_back_when_behind_by_more_than_active_segment(tmp_path: Path) -> None:
    """落后【超过一个活跃段】(真轮转发生在 persist 之后) → 安全回落到丢弃重建, 不产生错误结果。"""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path, segment_max_events=3, segment_max_bytes=10 * 1024 * 1024)
    writer.write_direct(_node_touched(local_intent_id="i-1"))
    writer.get_events_by_task("t-1")  # persist .idx covering seq 0 only

    # 写够多条, 强制跨越至少一次段轮转 (阈值=3)
    for i in range(2, 12):
        writer.write_direct(_node_touched(local_intent_id=f"i-{i}"))
    segs = ordered_segment_paths(log_path)
    assert len(segs) >= 2, "测试前提: 必须真的轮转过"

    reader = EventLog(log_path)
    assert reader._index is None, "落后超过一个活跃段 → 必须安全回落 (不是 adopt 一份不完整的索引)"
    # 回落之后正确性契约仍然成立: 首读全量重建拿到全部 11 条, 不完整/不丢数据。
    assert len(reader.get_events_by_task("t-1")) == 11


def test_ghost_record_spot_check_rejects_mismatched_idx(tmp_path: Path) -> None:
    """幽灵记录防护: .idx 的 tail seq 数对得上, 但那个 seq 位置的 event_id 和磁盘上实际内容不一致
    (模拟 truncate 后在同一 seq 上写了不同的新记录) → 必须拒绝 adopt, 不能服务幻影内容。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path)
    log.write_direct(_node_touched(local_intent_id="i-1"))
    log.get_events_by_task("t-1")  # persist .idx: event_id.idx 的 tail 记录 event_id=真实的那条

    # 直接改写磁盘上 event_id.idx 里 tail 记录的 event_id 字段, 伪造"陈旧索引记的是幻影记录"。
    store = EventLogIndexStore(log_path.parent)
    idx_path = store.index_dir / "event_id.idx"
    lines = idx_path.read_text(encoding="utf-8").splitlines()
    tail_obj = json.loads(lines[-1])
    tail_obj["event_id"] = "evt-GHOST-does-not-match-disk"
    lines[-1] = json.dumps(tail_obj)
    idx_path.write_text("\n".join(lines), encoding="utf-8")

    reopened = EventLog(log_path)
    assert reopened._index is None, "event_id 对不上磁盘实际内容的 .idx 绝不能被 adopt"
    # 正确性契约仍然成立 (首读全量重建, 拿到真实数据, 不是幽灵)
    assert len(reopened.get_events_by_task("t-1")) == 1
    real = reopened.get_events_by_task("t-1")[0]
    assert real.event_id != "evt-GHOST-does-not-match-disk"


def test_empty_log_adopt_path_does_not_crash(tmp_path: Path) -> None:
    """空日志边界: max_sequence=-1 (从未写过) 时 adopt 路径不炸, 走正常首读全量构建路径。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path)
    assert log._index is None  # 从未 persist 过, 正常 miss
    assert log.get_events_by_task("t-1") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
