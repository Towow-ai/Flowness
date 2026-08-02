"""A4 (2026-07-02 OOM 崩机根治, 新发现 N1): 写路径 max-seq 派生有界化。

崩机勘查的新发现: `_read_max_seq_on_disk` (每次写调用一次, append_transaction_batch /
write_direct / merge_foreign_events 都靠它求下一个 seq) 旧版全量扫【所有】段，实测
21.9万条 ≈ 1.4s CPU/次；daemon 约 6s 一写 → 常态烧掉可观 CPU，是 2026-07-01 夜崩机的
另一个未点名乘数。改成逆序扫段、扫到第一个含 seq 行的段即返回，成本从 O(总账本) 降到
O(尾段)。

本文件钉住:
  1. 等价性: 跨越多段轮转 (旧段 + 活跃段) 的真实数据上，新逆序扫段版与旧全量正序扫版
     给出完全一样的 max seq。
  2. 空活跃段场景: 刚轮转、活跃段为空 (0 行) 时正确回落到前一段找到 max。
  3. 有界性 (结构性证明，不是猜性能): 命中活跃段时，只打开【活跃段这一个文件】——旧段
     文件完全不被 open()，用 monkeypatch 记录被打开的路径来验证 (性能声明不能靠计时器猜，
     必须靠调用记录证明真的没碰旧段)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.segments import next_segment_path, ordered_segment_paths
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
    from io import TextIOWrapper
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


def _old_full_scan_max_seq(log: EventLog) -> int:
    """旧版参照实现 (正序扫【全部】段) —— 用来对拍新版逆序扫段的输出。"""
    import json

    from towow.l0.event_log.segments import iter_raw_event_lines

    max_seq = -1
    for line in iter_raw_event_lines(log._log_path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        seq = obj.get("sequence_number") if isinstance(obj, dict) else None
        if isinstance(seq, int) and seq > max_seq:
            max_seq = seq
    return max_seq


def test_matches_old_full_scan_across_segment_rotation(tmp_path: Path) -> None:
    """跨段 (旧段+活跃段) 真实数据上, 新逆序扫段版与旧全量正序扫版给出完全一样的 max seq。"""
    log_path = tmp_path / "events.log"
    # 用极小的段阈值强制真轮转 (不必写一万条才能测到跨段场景)。
    log = EventLog(log_path, segment_max_events=3, segment_max_bytes=10 * 1024 * 1024)

    for i in range(10):  # 10 条, 阈值 3 条/段 → 必然跨越多个段
        log.write_direct(_node_touched(local_intent_id=f"i-{i}"))

    segs = ordered_segment_paths(log_path)
    assert len(segs) >= 2, f"测试前提: 必须真的发生了段轮转, 实得 {len(segs)} 段"

    assert log._read_max_seq_on_disk() == _old_full_scan_max_seq(log)
    assert log._read_max_seq_on_disk() == 9  # 10 条 path-B 写入, seq 0..9


def test_empty_active_segment_falls_back_to_previous_segment(tmp_path: Path) -> None:
    """刚轮转、活跃段为空 (0 行) 时, 必须正确回落到前一段找到 max (不是误判成 -1)。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=3, segment_max_bytes=10 * 1024 * 1024)
    for i in range(3):
        log.write_direct(_node_touched(local_intent_id=f"i-{i}"))

    # 手动预建一个空的新段 (模拟"刚轮转、还没来得及写第一条"的窗口)。
    empty_seg = next_segment_path(log_path)
    empty_seg.parent.mkdir(parents=True, exist_ok=True)
    empty_seg.touch()

    assert log._read_max_seq_on_disk() == 2  # 必须在空活跃段之外找到已写的 3 条 (seq 0..2)
    assert log._read_max_seq_on_disk() == _old_full_scan_max_seq(log)


def test_hits_only_active_segment_not_older_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有界性结构证明: 命中活跃段时只 open() 活跃段这一个文件, 旧段完全不被打开。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=3, segment_max_bytes=10 * 1024 * 1024)
    for i in range(10):
        log.write_direct(_node_touched(local_intent_id=f"i-{i}"))

    segs = ordered_segment_paths(log_path)
    assert len(segs) >= 2

    from pathlib import Path as PathCls

    opened: list[str] = []
    orig_open = PathCls.open

    def _spy_open(self: PathCls, mode: str = "r", encoding: str | None = None) -> TextIOWrapper:
        if self.suffix == ".jsonl" or self.name == "events.log":
            opened.append(self.name)
        return orig_open(self, mode, encoding=encoding)  # type: ignore[return-value]

    monkeypatch.setattr(PathCls, "open", _spy_open)
    result = log._read_max_seq_on_disk()
    monkeypatch.undo()

    assert result == 9
    assert opened == [segs[-1].name], (
        f"应只打开活跃段(最后一段) {segs[-1].name}, 实际打开了 {opened} "
        f"(旧版会打开全部 {[s.name for s in segs]})"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
