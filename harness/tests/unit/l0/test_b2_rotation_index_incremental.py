"""B-2 (2026-07-15) — 账本轮转触发索引全量重建的增量化修复。

背景: B-1 (060b95032) 让索引常驻内存 (warm), 但 `_catch_up_index` 遇到 rotation (active
segment 变了) 时仍走 `self._build_and_persist_index(list(self._iter_committed_records()))`
—— 扫全部 segment (base + 每个 hot segment), 62 万事件下分钟级, 且随账本增长恶化。

修复: `_rotation_fold` 把"rotation → 全量重建"改成"折叠刚封存段的尾巴 + 折叠新段", 两次都是
有界读 (`_fold_active_segment_tail` 只读一个 segment 文件), 不再重扫已封存、且不再变化的旧
segment。只有真出现"两次 rotation 之间漏了一整段"这种 gap 时才回退全量重建 (safe, 从不比全
扫更不正确, 只是偶尔没那么快)。

这组测试钉死:
  ①增量路径产出的索引 ≡ 全量重建等价 (跨多次 rotation, 每种 postings 都比对);
  ②rotation 场景下 `_build_and_persist_index` (全量重建的唯一入口) 真的没被调用 —— 证明走的
    是增量路径, 不是"凑巧结果一样但其实还在全扫";
  ③"两次 rotation 之间漏了一整段" (gap) 时安全回退全量重建, 结果依然正确 (never less correct);
  ④性能实测: 大账本 + 多次 rotation 后一次 catch-up 的耗时, 增量路径 vs 全量重建路径对比,
    写进测试输出 (不是断言 —— 环境噪声下不适合做 flaky 的耗时断言, 但要把数字打印出来供人核验)。
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.segments import active_segment_path
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

# ─── fixtures / helpers ──────────────────────────────────────────────────────────


def _pathb_intent(local_intent_id: str, entity_id: str) -> EventIntent:
    """A path-B NodeTouched intent (one record per write_direct, cheap to spam for volume)."""
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            run_id="r-1",
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


def _write_n(log: EventLog, n: int, prefix: str) -> None:
    for i in range(n):
        log.write_direct(_pathb_intent(f"{prefix}-{i}-{uuid.uuid4().hex[:6]}", f"c-{prefix}-{i % 5}"))


def _postings_snapshot(index) -> dict:  # noqa: ANN001 — EventIndex, keep test import-light
    """A comparable snapshot of every indexed key → sorted event_ids (equivalence oracle)."""
    snap: dict[str, object] = {"max_sequence": index.max_sequence}
    snap["by_event_id"] = sorted(index._by_event_id.keys())  # noqa: SLF001 — test-only introspection
    snap["by_sequence"] = sorted(index._by_sequence.keys())  # noqa: SLF001
    for attr in (
        "_by_type",
        "_by_category",
        "_by_actor",
        "_by_task",
        "_by_run",
        "_by_entity",
    ):
        bucket = getattr(index, attr)
        snap[attr] = {
            str(k): sorted(r.event_id for r in v) for k, v in bucket.items()
        }
    return snap


# ─── ① 增量 ≡ 全量重建等价 (跨多次 rotation) ──────────────────────────────────────


def test_rotation_incremental_index_matches_full_rebuild(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=5)

    # 触发多轮 rotation: 每 5 条一段, 写 37 条 → 至少 7 次 rotation。
    for batch in range(6):
        _write_n(log, 6, f"b{batch}")
        log.committed_index()  # force a catch-up between batches (mixed timing, like real traffic)
    _write_n(log, 1, "tail")

    incremental = log.committed_index()
    incremental_snapshot = _postings_snapshot(incremental)

    # Negative control: force the FULL rebuild path directly on a fresh EventLog instance over the
    # same on-disk ledger, and assert the two snapshots are identical. If the incremental path were
    # silently dropping/duplicating records, this equivalence would break.
    fresh_log = EventLog(log_path, segment_max_events=5)
    fresh_log._index = None  # noqa: SLF001 — force cold rebuild regardless of adopt-from-.idx path
    full_rebuild = fresh_log._build_and_persist_index(list(fresh_log._iter_committed_records()))  # noqa: SLF001
    full_snapshot = _postings_snapshot(full_rebuild)

    assert incremental_snapshot == full_snapshot
    assert incremental.max_sequence == full_rebuild.max_sequence >= 30


# ─── ② rotation 场景下真的没有触发全量重建 ────────────────────────────────────────


def test_rotation_does_not_trigger_full_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=4)

    # Warm the index once before any rotation (baseline set).
    _write_n(log, 3, "warm")
    log.committed_index()

    calls = {"n": 0}
    original = EventLog._build_and_persist_index

    def _spy(self: EventLog, records: list) -> object:  # noqa: ANN001
        calls["n"] += 1
        return original(self, records)

    monkeypatch.setattr(EventLog, "_build_and_persist_index", _spy)

    # Push well past the 4-event threshold → guaranteed rotation(s) before the next read.
    _write_n(log, 10, "rotate")
    log.committed_index()  # the read that must catch up across the rotation

    assert calls["n"] == 0, (
        f"rotation catch-up called _build_and_persist_index (full rebuild) {calls['n']} times — "
        "expected the bounded _rotation_fold path to handle this, not a full rescan"
    )


# ─── ③ gap (跳过一整段) 时安全回退全量重建, 结果仍正确 ────────────────────────────


def test_rotation_gap_falls_back_to_full_rebuild_safely(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=3)

    _write_n(log, 2, "warm")
    log.committed_index()  # baseline set on segment #1

    # Force TWO rotations worth of writes with no intervening read — the bounded rotation-fold can
    # only see the immediately-sealed segment + the new active one; a whole segment sandwiched in
    # between is invisible to it, so this must be detected as a gap and safely fall back.
    _write_n(log, 3, "seg2")  # rotates into segment #2
    _write_n(log, 3, "seg3")  # rotates again into segment #3 — segment #2 now fully skippable

    index = log.committed_index()
    expected_ids = {r.event_id for r in log._iter_committed_records()}  # noqa: SLF001
    got_ids = set(index._by_event_id.keys())  # noqa: SLF001
    assert got_ids == expected_ids
    assert index.max_sequence == log.next_sequence - 1


# ─── ④ 性能实测: 大账本 + rotation 后一次 catch-up 的耗时 ──────────────────────────


def test_rotation_catchup_perf_incremental_vs_full(tmp_path: Path) -> None:
    """Not a pass/fail perf gate (flaky under CI noise) — prints timings for human verification
    that the rotation-fold path is materially cheaper than a full rebuild on a nontrivial ledger.
    """
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=200)

    # Build a few thousand committed records across ~15 segments, reading periodically so the warm
    # index tracks along (mirrors real traffic: writes interleaved with reads).
    for batch in range(15):
        _write_n(log, 200, f"bulk{batch}")
        log.committed_index()

    # One more rotation-worth of writes with NO read in between (the exact shape B-2 targets).
    _write_n(log, 150, "final")

    t0 = time.perf_counter()
    log.committed_index()  # incremental rotation-fold path
    incremental_elapsed = time.perf_counter() - t0

    # Compare against a cold full rebuild over the same on-disk ledger.
    fresh_log = EventLog(log_path, segment_max_events=200)
    fresh_log._index = None  # noqa: SLF001
    t1 = time.perf_counter()
    fresh_log._build_and_persist_index(list(fresh_log._iter_committed_records()))  # noqa: SLF001
    full_elapsed = time.perf_counter() - t1

    total_records = log.next_sequence
    print(  # noqa: T201 — deliberate perf evidence, not log noise
        f"\n[B-2 perf] {total_records} committed records across ~16 segments: "
        f"incremental rotation-fold catch-up = {incremental_elapsed * 1000:.2f}ms, "
        f"full rebuild = {full_elapsed * 1000:.2f}ms "
        f"({full_elapsed / max(incremental_elapsed, 1e-6):.1f}x slower)",
    )
    # Sanity: incremental must not be SLOWER than a full rebuild (the whole point of the fix) —
    # generous margin (2x) to absorb timing noise on a small fixture; the point is a real gap, not
    # a razor-thin one that could flip on scheduling jitter.
    assert incremental_elapsed <= full_elapsed * 2 or incremental_elapsed < 0.05


def test_active_segment_path_helper_still_used(tmp_path: Path) -> None:
    """Cheap smoke check that segment path helpers still resolve after the refactor (sanity, not
    a behavior claim — the real coverage is in tests ①-④ above)."""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path, segment_max_events=3)
    _write_n(log, 5, "smoke")
    assert active_segment_path(log_path).exists()
