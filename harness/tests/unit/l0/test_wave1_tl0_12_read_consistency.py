"""波1 T-L0-12: M-0.2a Patch 2 Read consistency mode (fresh_required/allow_stale/watermark_at_least).

源表 done_criteria: 以 watermark_at_least 模式读未达水位 projection 时 fail-closed 报错或阻塞至
水位; 不静默返陈旧态。timeout_ms (仅 fresh_required/watermark_at_least 生效) + min_watermark
(仅 watermark_at_least)。

现状(改前): ProjectionStore read/catchup 一律 lazy catch-up (= 等价 fresh), 无 mode/超时/fail-closed。
本组证: allow_stale 真不 catch up (返陈旧); fresh_required 真到最新 committed; watermark_at_least
达水位返回 / 未达水位 fail-closed 抛错 (绝不静默返陈旧)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.projection import ConsistencyMode, ProjectionConsistencyError, ProjectionStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    event_log = EventLog(towow / "events.log")
    CommitGate(event_log).bootstrap_commit(bootstrap_protocol_invariants())
    return towow  # 注意: 故意不 catchup → 新 ProjectionStore cursor=0 (陈旧)


def test_allow_stale_does_not_catchup_but_fresh_does(project: Path) -> None:
    """allow_stale 不 catch up (返陈旧 watermark); fresh_required catch up 到最新 (对比证模式真不同)。"""
    event_log = EventLog(project / "events.log")
    ps = ProjectionStore(project / "graph")
    assert ps.cursor == 0  # 未 catchup

    stale = ps.read_consistent(event_log, mode=ConsistencyMode.ALLOW_STALE)
    assert stale == 0, "allow_stale 返当前(陈旧)水位"
    assert ps.cursor == 0, "allow_stale 绝不触发 catch up"

    fresh = ps.read_consistent(event_log, mode=ConsistencyMode.FRESH_REQUIRED)
    assert fresh == event_log.next_sequence - 1, "fresh_required catch up 到最新 committed"
    assert fresh > stale, "fresh 真比 allow_stale 高 (证 catchup 真发生)"


def test_fresh_required_reaches_latest_committed(project: Path) -> None:
    event_log = EventLog(project / "events.log")
    ps = ProjectionStore(project / "graph")
    wm = ps.read_consistent(event_log, mode=ConsistencyMode.FRESH_REQUIRED)
    assert wm == event_log.next_sequence - 1


def test_watermark_at_least_succeeds_when_reachable(project: Path) -> None:
    """min_watermark ≤ 可达水位 → 返回 (watermark ≥ min_watermark)。"""
    event_log = EventLog(project / "events.log")
    ps = ProjectionStore(project / "graph")
    target = event_log.next_sequence - 1  # 已 committed, 可达
    wm = ps.read_consistent(
        event_log, mode=ConsistencyMode.WATERMARK_AT_LEAST, min_watermark=target,
    )
    assert wm >= target


def test_watermark_at_least_fail_closed_when_beyond(project: Path) -> None:
    """min_watermark 超过现有 committed 水位 → fail-closed 抛错, 绝不静默返陈旧态 (核心 done_criteria)。"""
    event_log = EventLog(project / "events.log")
    ps = ProjectionStore(project / "graph")
    beyond = event_log.next_sequence + 5  # 不存在的水位
    with pytest.raises(ProjectionConsistencyError, match="fail-closed"):
        ps.read_consistent(
            event_log,
            mode=ConsistencyMode.WATERMARK_AT_LEAST,
            min_watermark=beyond,
            timeout_ms=0,  # 一次尝试不达即刻 fail-closed (不真等 2000ms)
        )


def test_watermark_at_least_requires_min_watermark(project: Path) -> None:
    event_log = EventLog(project / "events.log")
    ps = ProjectionStore(project / "graph")
    with pytest.raises(ValueError, match="min_watermark"):
        ps.read_consistent(event_log, mode=ConsistencyMode.WATERMARK_AT_LEAST)
