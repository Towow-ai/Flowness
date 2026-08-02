"""RUN-055 — EPIC-5 收尾: 持久 .idx 索引接运行时 + 6 过滤 API 走索引 (非 daemon, 纯性能).

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   §7.1 文件布局 — .towow/events/index/*.idx；§7.2 "索引可重建 ... 索引不是事实源, event 文件才是"
#   附录 B Patch 3 §3.1-§3.2 — 15 indexes built from subjects[]
#   §4.1.2-§4.1.7 range / category / type / actor / task / run / correlation 过滤 Read API
#   §4.2 SLO — 索引查询, 非全扫

前提 (RUN-038 已做, 都真): EventIndex 内存 15 桶 + EventLogIndexStore persist/load/rebuild + 段切分。
RUN-055 接的两小块:

  件A — EventLogIndexStore 接进 EventLog.__init__: 启动 load 命中 (新鲜度水位线匹配) 则跳全扫;
        写后失效, 懒 persist (rebuild 时写 .idx); 删 .idx 能 rebuild byte 一致。
  件B — get_events_by_{type,category,actor,task,run} / get_correlated / in_range 由 O(n) 全扫
        改查 EventIndex 已有桶。

每个能力都用"毒化 _iter_committed_records (全扫) 仍返正确"做结构性证明 (证走索引非全扫),
仿 test_run038_index_persistence.py 的 Cap2 套路。
"""

from __future__ import annotations

import multiprocessing
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
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


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "events.log"


@pytest.fixture
def event_log(log_path: Path) -> EventLog:
    return EventLog(log_path)


def _node_touched(
    *,
    local_intent_id: str,
    entity_id: str = "c-1",
    actor_id: str = "fork-1",
    task_id: str | None = "t-1",
    run_id: str | None = "r-1",
    correlation_id: str | None = None,
) -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=actor_id,
            task_id=task_id,
            run_id=run_id,
            correlation_id=correlation_id,
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _cross_process_append(log_path_str: str, local_intent_id: str, entity_id: str, done_marker_str: str) -> None:
    """Child-process entrypoint: open the shared log and write one NodeTouched event,
    再 touch 一个 done-marker 作完成确认 side-channel —— 让主进程确定性确认子进程真跑完 append,
    不靠 exitcode/固定 30s 超时推断成败 (f-rc01-spawn-test-reliability)。"""
    from pathlib import Path

    log = EventLog(Path(log_path_str))
    log.write_direct(_node_touched(local_intent_id=local_intent_id, entity_id=entity_id))
    Path(done_marker_str).write_text("done", encoding="utf-8")


def _run_peer_append(
    log_path: Path,
    done_marker: Path,
    *,
    local_intent_id: str = "i-peer",
    entity_id: str = "c-peer",
) -> None:
    """Spawn 子进程跑一次 cross-process append, 用 done-marker side-channel 确定性确认完成 +
    显式 terminate/close 清理 —— 不靠固定 30s 超时区分成败, 子进程异常退出不留 zombie 污染
    tmp_path (f-rc01-spawn-test-reliability)。"""
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(
        target=_cross_process_append,
        args=(str(log_path), local_intent_id, entity_id, str(done_marker)),
    )
    p.start()
    try:
        p.join(timeout=30)
        assert p.exitcode is not None, "peer process did not terminate within timeout"
        assert p.exitcode == 0, f"peer process failed with exitcode={p.exitcode}"
        # 完成确认 side-channel: 子进程跑完 append 才 touch done-marker — 确定性, 非靠 exitcode 推断
        assert done_marker.exists(), (
            "peer process exited 0 but did not signal append completion (side-channel missing)"
        )
    finally:
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
        p.close()


def _poison_full_scan(log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any further _iter_committed_records full scan raise — the structural 'no full scan' proof."""

    def _boom() -> object:
        raise AssertionError(
            "must NOT full-scan _iter_committed_records — should resolve via the (loaded/warm) index",
        )

    monkeypatch.setattr(log, "_iter_committed_records", _boom)


# ─── 件A: EventLogIndexStore wired into EventLog runtime ──────────────────────────


def test_a1_write_then_read_persists_idx_to_disk(event_log: EventLog, log_path: Path) -> None:
    """件A AC1: 写后 (经一次触发 rebuild 的读) .idx 真存在于 events/index/ — 不再是零运行时调用."""
    index_dir = log_path.parent / "events" / "index"
    assert not index_dir.exists()  # baseline: 索引目录不存在 (RUN-055 前)

    event_log.write_direct(_node_touched(local_intent_id="i-1"))
    # a read after the write rebuilds the invalidated index → lazy-persist fires here
    event_log.get_events_by_task("t-1")

    assert index_dir.is_dir()
    for name in ("event_id.idx", "type.idx", "task.idx", "run.idx", "category.idx", "entity.idx"):
        assert (index_dir / name).exists(), f"missing persisted index file {name}"


def test_a2_new_eventlog_loads_index_at_init_no_full_scan(
    log_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """件A AC2: 新建 EventLog 命中 load (新鲜度匹配) → 不全扫.

    仿 test_run038_index_persistence.py:189 — 先让一个实例 persist .idx, 再新建第二个实例:
    它必须在 __init__ 就把索引从磁盘 load 进来 (self._index 非 None), 之后毒化全扫, 点查仍解析得到记录。
    """
    writer = EventLog(log_path)
    rec = writer.write_direct(_node_touched(local_intent_id="i-1"))
    writer.get_event(rec.event_id)  # trigger persist
    index_dir = log_path.parent / "events" / "index"
    assert (index_dir / "event_id.idx").exists()

    reader = EventLog(log_path)
    assert reader._index is not None, "__init__ 应从磁盘 load 索引 (新鲜度水位线匹配)"

    _poison_full_scan(reader, monkeypatch)
    found = reader.get_event(rec.event_id)
    assert found is not None
    assert found.event_id == rec.event_id
    # miss 也走索引, 仍不全扫
    assert reader.get_event("evt-does-not-exist") is None


def test_a3_delete_idx_then_rebuild_is_byte_identical(log_path: Path) -> None:
    """件A AC3: 删 .idx → rebuild 出来 byte 一致 (确定性重建; 索引可重建 = 不是事实源).

    持久化序列化是确定性的 (event_id.idx 是按 committed 顺序的 JSONL, key 桶是 insertion-order dict
    的 json.dumps), 故对同一组记录重建出的每个 .idx 文件应 byte-for-byte 相同。
    """
    writer = EventLog(log_path)
    writer.write_direct(_node_touched(local_intent_id="i-a", entity_id="c-a"))
    writer.write_direct(_node_touched(local_intent_id="i-b", entity_id="c-b", task_id="t-2"))
    writer.get_events_by_task("t-1")  # trigger initial persist

    index_dir = log_path.parent / "events" / "index"
    before = {p.name: p.read_bytes() for p in sorted(index_dir.glob("*.idx"))}
    assert before, "首次 persist 应产出 .idx"

    for p in index_dir.glob("*.idx"):
        p.unlink()
    assert not list(index_dir.glob("*.idx"))

    # 新实例: load miss (.idx 删了) → 首读 rebuild 从 event 文件 + 重新 persist
    rebuilder = EventLog(log_path)
    assert rebuilder._index is None  # load miss
    rebuilder.get_events_by_task("t-1")
    after = {p.name: p.read_bytes() for p in sorted(index_dir.glob("*.idx"))}

    assert after.keys() == before.keys()
    for name in before:
        assert after[name] == before[name], f"{name} 重建后非 byte 一致 (确定性重建被破坏)"


def test_a_stale_persisted_index_is_adopted_and_caught_up(
    log_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """件A 正确性护栏 (A2/A3, 2026-07-02 OOM 崩机根治 — 语义更新): persist 后日志又长了 → 新实例
    load 到的索引水位线落后 → 【adopt 该索引 + 从活跃段追赶差量】, 不再是"丢弃重建"。

    并发邻居场景的硬约束不变 (正确性契约): 一个进程读时 persist 了覆盖 N 条的 .idx, 之后另一进程
    又 append; 第三个进程 init 时必须拿到完整的 N+差量条记录 (incomplete 结果 = 正确性回归) ——
    只是达成方式从"整份丢弃、全量重扫"换成"采用 + 折入差量" (2026-07-01 夜 OOM 崩机勘查: 旧版
    `==` 精确匹配门槛在 daemon 每 ~6s 写一条的常态下几乎从不成立, 冷启动因此几乎总付全量重建的
    代价——这正是崩机夜 16 秒/3GB 冷启动的大头)。
    """
    p1 = EventLog(log_path)
    p1.write_direct(_node_touched(local_intent_id="i-1"))
    p1.get_events_by_task("t-1")  # persist .idx covering 1 record

    # a *separate* writer appends another committed record (the persisted .idx is now stale)
    p2 = EventLog(log_path)
    p2.write_direct(_node_touched(local_intent_id="i-2", task_id="t-1"))

    # third instance: adopts the (now-behind) .idx and catches it up to the current tail —
    # never silently serves the stale 1-record view.
    p3 = EventLog(log_path)
    assert p3._index is not None, "落后但可安全追赶的 .idx 应被 adopt (不再是无条件丢弃)"
    assert p3._index.max_sequence == 1, "adopt 后必须已追赶到当前 tail (含新 append 的那条)"
    assert len(p3.get_events_by_task("t-1")) == 2

    # and it serves the complete set even with the full scan poisoned (proves it didn't fall
    # back to a full rebuild to get there — the adopt+catch-up path alone sufficed).
    _poison_full_scan(p3, monkeypatch)
    assert len(p3.get_events_by_task("t-1")) == 2


def test_a_corrupt_idx_self_heals_not_fatal(log_path: Path) -> None:
    """件A 鲁棒性: 损坏的 event_id.idx (并发写半成品) → init 不崩, 当成 miss, 从 event 文件重建."""
    writer = EventLog(log_path)
    writer.write_direct(_node_touched(local_intent_id="i-1"))
    writer.get_events_by_task("t-1")  # persist

    index_dir = log_path.parent / "events" / "index"
    (index_dir / "event_id.idx").write_text("{ this is not valid json record", encoding="utf-8")

    # defensive load: corrupt .idx must not raise out of __init__
    healed = EventLog(log_path)
    assert healed._index is None  # treated as a miss
    assert len(healed.get_events_by_task("t-1")) == 1  # rebuilt from the event log (source of truth)


# ─── 件B: each filter API goes through the index, not an O(n) full scan ───────────


def _seed_for_filter_apis(log: EventLog) -> dict[str, object]:
    """Seed a small mixed stream and return the records, then warm+persist the index."""
    a = log.write_direct(
        _node_touched(local_intent_id="i-a", actor_id="alice", task_id="t-A", run_id="run-1", correlation_id="corr-1"),
    )
    b = log.write_direct(
        _node_touched(local_intent_id="i-b", actor_id="bob", task_id="t-B", run_id="run-1", correlation_id="corr-1"),
    )
    c = log.write_direct(
        _node_touched(local_intent_id="i-c", actor_id="alice", task_id="t-A", run_id="run-2", correlation_id="corr-2"),
    )
    log.get_events_by_task("t-A")  # warm + persist
    return {"a": a, "b": b, "c": c}


def test_b_get_events_by_type_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    assert len(event_log.get_events_by_type(EventType.NODE_TOUCHED)) == 3
    # 不同 type 的桶为空
    assert event_log.get_events_by_type(EventType.COMMIT_ACCEPTED) == []


def test_b_get_events_by_type_respects_since_and_limit(
    event_log: EventLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    first_seq = recs["a"].sequence_number  # type: ignore[attr-defined]
    after_first = event_log.get_events_by_type(EventType.NODE_TOUCHED, since_seq=first_seq)
    assert [r.event_id for r in after_first] == [recs["b"].event_id, recs["c"].event_id]  # type: ignore[attr-defined]
    limited = event_log.get_events_by_type(EventType.NODE_TOUCHED, limit=2)
    assert len(limited) == 2
    assert [r.event_id for r in limited] == [recs["a"].event_id, recs["b"].event_id]  # type: ignore[attr-defined]


def test_b_get_events_by_category_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    env = event_log.get_events_by_category(EventCategory.ENVELOPE)
    assert len(env) == 3
    # since_seq + limit 仍生效
    first_seq = recs["a"].sequence_number  # type: ignore[attr-defined]
    assert len(event_log.get_events_by_category(EventCategory.ENVELOPE, since_seq=first_seq)) == 2
    assert len(event_log.get_events_by_category(EventCategory.ENVELOPE, limit=1)) == 1


def test_b_get_events_by_actor_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    alice = event_log.get_events_by_actor("alice")
    assert {r.event_id for r in alice} == {recs["a"].event_id, recs["c"].event_id}  # type: ignore[attr-defined]
    assert len(event_log.get_events_by_actor("bob")) == 1
    # since_seq
    first_seq = recs["a"].sequence_number  # type: ignore[attr-defined]
    assert {r.event_id for r in event_log.get_events_by_actor("alice", since_seq=first_seq)} == {recs["c"].event_id}  # type: ignore[attr-defined]


def test_b_get_events_by_task_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    assert {r.event_id for r in event_log.get_events_by_task("t-A")} == {recs["a"].event_id, recs["c"].event_id}  # type: ignore[attr-defined]
    assert len(event_log.get_events_by_task("t-B")) == 1
    assert event_log.get_events_by_task("t-NONE") == []


def test_b_get_events_by_run_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    assert {r.event_id for r in event_log.get_events_by_run("run-1")} == {recs["a"].event_id, recs["b"].event_id}  # type: ignore[attr-defined]
    assert len(event_log.get_events_by_run("run-2")) == 1


def test_b_get_correlated_events_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    assert {r.event_id for r in event_log.get_correlated_events("corr-1")} == {recs["a"].event_id, recs["b"].event_id}  # type: ignore[attr-defined]
    assert len(event_log.get_correlated_events("corr-2")) == 1


def test_b_get_events_in_range_via_index(event_log: EventLog, monkeypatch: pytest.MonkeyPatch) -> None:
    recs = _seed_for_filter_apis(event_log)
    _poison_full_scan(event_log, monkeypatch)
    all_three = event_log.get_events_in_range(0, 100)
    assert [r.event_id for r in all_three] == [recs["a"].event_id, recs["b"].event_id, recs["c"].event_id]  # type: ignore[attr-defined]
    # bounded sub-range, in committed (sequence) order
    mid = recs["b"].sequence_number  # type: ignore[attr-defined]
    assert [r.event_id for r in event_log.get_events_in_range(mid, mid)] == [recs["b"].event_id]  # type: ignore[attr-defined]
    assert event_log.get_events_in_range(9000, 9999) == []


def test_b_results_equivalent_to_full_scan_semantics(log_path: Path) -> None:
    """件B 等价性: 走索引的结果与旧全扫语义一致 (顺序 + 内容), 用一个对照实例交叉验证.

    索引桶按 committed (sequence) 插入顺序返回, 故结果与按 sequence_number 排序一致 —
    这正是旧全扫 (按文件/序号顺序 yield) 的顺序语义。
    """
    log = EventLog(log_path)
    _seed_for_filter_apis(log)
    via_index = log.get_events_in_range(0, 100)
    via_index_ids = [r.event_id for r in via_index]

    # a fresh instance rebuilding from the event files yields the identical ordered set
    fresh = EventLog(log_path)
    via_rebuild_ids = [r.event_id for r in fresh.get_events_in_range(0, 100)]
    assert via_index_ids == via_rebuild_ids

    # order is committed/sequence order (what the old per-call full scan produced)
    assert [r.sequence_number for r in via_index] == sorted(r.sequence_number for r in via_index)


# ─── 跨进程读一致性回归测试 (event-log-index-cross-process-read-consistency@v1) ──────


def test_cross_process_mechanism2_invalidate_on_write(tmp_path: Path) -> None:
    """机制2: 跨进程读一致性回归 (T-FND-02 写路径 427f548 + 读路径 catch-up 34ef2e7 综合)。

    历史: 原 RUN-055 机制2 是 invalidate-on-write (写后 self._index=None, 下个读全量重建捡 peer)。
    T-FND-02 综合用更优的 keep-warm + _catch_up_index 取代它 —— 写后索引保持 warm, 下个读只折入
    活动段自上次以来的增量 (含 peer 进程的提交, filter seq>max_sequence), 不全量重建, 且**比旧设计
    更新鲜** (旧设计要等本进程下次写 null 才看到 peer)。真正的正确性 = step 4: 跨进程读看得到 peer
    的 append。mutation-red 证明: 破坏 _catch_up_index 的活动段 delta 折入 → step 4 断言变红。
    """
    log_path = tmp_path / "events.log"
    main_log = EventLog(log_path)

    # 1. 写一条 + 预热索引
    rec_a = main_log.write_direct(_node_touched(local_intent_id="i-main-a", entity_id="c-main"))
    assert main_log.get_event(rec_a.event_id) is not None  # 触发 rebuild + persist
    initial_index = main_log._index
    assert initial_index is not None, "初次读应触发 rebuild → _index 不为 None"

    # 2. 独立进程 append (不经 main_log 实例)
    _run_peer_append(log_path, tmp_path / "peer.done")

    # peer append 后, main_log 仍持有热的(但过时的)索引 — 不知道 peer 的 append
    hot_index = main_log._index
    assert hot_index is not None, "热索引应仍在 (peer append 不影响本实例内存)"

    # 3. 本实例再写一条 → T-FND-02 综合: 索引保持 warm (不再 invalidate=None);
    #    peer 已插过 seq, 本写不走 add() 快路径, 索引留 warm 等读路径 catch-up。
    main_log.write_direct(_node_touched(local_intent_id="i-main-b", entity_id="c-main"))
    assert main_log._index is not None, (
        "T-FND-02 综合: 写后索引保持 warm (不再 self._index=None); "
        "跨进程新鲜度由下次读的 _catch_up_index 保证 (见 step 4)"
    )

    # 4. 下次读触发 _catch_up_index 折入活动段增量 — 必须返回 peer 进程 append 的那条
    peer_records = main_log.get_events_by_entity("concept", "c-peer")
    assert len(peer_records) == 1, (
        "跨进程读一致性失败: peer 进程 append 不可见。catch-up 应折入活动段 seq>max 的新记录 "
        "(含 peer 的提交); mutation-red: 破坏 _catch_up_index 的活动段 delta 折入 → 此断言变红。"
    )


def test_cross_process_mechanism1_new_instance_sees_peer_append(tmp_path: Path) -> None:
    """机制1: 每轮新建 EventLog 实例 (daemon poll 拓扑) 可见他进程 append。

    Daemon 每轮 poll 都创建新 EventLog 实例 — 新实例从磁盘全量重建, 他进程 append 自然可见。
    """
    log_path = tmp_path / "events.log"

    # 他进程 append (没有本地 EventLog 实例持有缓存)
    _run_peer_append(log_path, tmp_path / "peer.done")

    # 每轮新建实例 (daemon poll 拓扑) — 从磁盘初始化, 必须看到 peer append
    new_instance = EventLog(log_path)
    records = new_instance.get_events_by_entity("concept", "c-peer")
    assert len(records) == 1, (
        "机制1失败: 新建 EventLog 实例看不到他进程 append (daemon poll 拓扑下读一致性缺失)。"
    )
