"""RUN-038 L0增强 M-0.7 §7.2 — Prepare / Commit / Finalize 三段式原子归档.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §7.2 (+§11.9 Patch 1):
#     prepare: copy hot segment → staging cold tmp (gzip), 算 source_hash/archive_hash, 不更新 index,
#              不删 hot, 不改 M-0.1 可见位置.
#     commit (M-0.5 verify): staging 存在且 hash 匹配 + hot 未改动.
#     finalize (accepted 之后): atomic rename staging→cold, 更新 index hot→cold, 最后 unlink hot,
#              记 finalize_completed.
#     reject: 删 staging, hot/index 从未改变.
#
# Done 判据 (§17 #6): 三段式真原子归档一个段 + 归档后事件仍可读 (从 cold) + 中途不丢.
# 数据安全重点: hot 事件 → 归档 cold → 解压读回 逐字节相同, 不丢不损.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

from towow.l0.event_log.event_log import EventLog
from towow.l0.event_log.segments import ordered_hot_segments, segment_number_of
from towow.l0.snapshot.cold_archive import (
    finalize_archive,
    is_finalize_completed,
    prepare_archive_to_cold,
    rollback_prepared_archive,
)
from towow.l0.snapshot.cold_storage import (
    cold_archive_path,
    cold_staging_path,
    load_cold_index,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
)
from towow.schemas.event_intent import (
    EventIntent,
    ProvenanceHint,
    Subject,
    SubjectRole,
    Supersede,
)

if TYPE_CHECKING:
    from pathlib import Path


def _touch_intent(i: int) -> EventIntent:
    return EventIntent(
        local_intent_id=f"touch-{i}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"node_id": f"n-{i}", "seq_marker": i},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="sys"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=f"n-{i}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


def _log_with_sealed_segment(towow: Path) -> tuple[EventLog, Path, int]:
    """Build a log whose events-000001.jsonl is sealed (rotated away) and return (log, sealed, seg).

    segment_max_events=2 → after writing event 3 the active segment rotates, so events-000001.jsonl
    is a sealed, immutable segment safe to archive. Returns the sealed segment path + its number.
    """
    (towow / "locks").mkdir(parents=True, exist_ok=True)
    log = EventLog(towow / "events.log", segment_max_events=2)
    for i in range(5):  # 5 path-B writes with cap 2 → rotates, sealing earlier segments
        log.write_direct(_touch_intent(i))
    sealed = ordered_hot_segments(towow / "events.log")[0]
    seg = segment_number_of(sealed)
    assert seg is not None
    return log, sealed, seg


def test_prepare_writes_staging_does_not_touch_hot_or_index(tmp_path: Path) -> None:
    """§7.2 prepare — staging tmp 写出 + hashes 算出; hot 不动, index 不动, cold archive 不存在."""
    towow = tmp_path / ".towow"
    _log, sealed, seg = _log_with_sealed_segment(towow)
    sealed_before = sealed.read_bytes()

    prepared = prepare_archive_to_cold(towow, sealed)

    # staging exists; source/archive hashes present; hot unchanged; index untouched; no final cold yet
    assert cold_staging_path(towow, seg).exists()
    assert prepared.segment_id == seg
    assert prepared.source_hash
    assert prepared.archive_hash
    assert prepared.hot_path_unchanged == str(sealed)
    assert sealed.exists()  # hot not deleted
    assert sealed.read_bytes() == sealed_before  # hot byte-identical
    assert load_cold_index(towow) == {}  # M-0.1 visible position unchanged
    assert not cold_archive_path(towow, seg).exists()


def test_finalize_round_trips_events_byte_identical_and_unlinks_hot(tmp_path: Path) -> None:
    """§7.2 finalize — staging→cold atomic rename + index 更新 + hot unlink; round-trip 逐字节相同."""
    towow = tmp_path / ".towow"
    _log, sealed, seg = _log_with_sealed_segment(towow)
    # capture the exact archived events (raw lines) BEFORE finalize unlinks hot
    import json

    sealed_lines = [ln for ln in sealed.read_text(encoding="utf-8").splitlines() if ln.strip()]
    archived_event_ids = [json.loads(ln)["event_id"] for ln in sealed_lines]

    prepared = prepare_archive_to_cold(towow, sealed)

    finalize_archive(towow, prepared, event_ids=archived_event_ids)

    # 1. final cold archive exists, staging gone, hot unlinked
    assert cold_archive_path(towow, seg).exists()
    assert not cold_staging_path(towow, seg).exists()
    assert not sealed.exists()
    # 2. cold index routes every archived event_id → this segment/cold
    idx = load_cold_index(towow)
    for eid in archived_event_ids:
        assert idx[eid] == {"location": "cold", "segment": seg}
    # 3. DATA SAFETY round-trip: decompress cold → the exact same JSONL lines, byte-identical
    with gzip.open(cold_archive_path(towow, seg), "rb") as f:
        cold_lines = [ln.decode("utf-8").rstrip("\n") for ln in f if ln.strip()]
    assert cold_lines == sealed_lines  # not one byte lost or mutated
    # 4. finalize recorded as completed
    assert is_finalize_completed(towow, prepared)


def test_reject_rollback_removes_staging_leaves_hot_and_index_untouched(tmp_path: Path) -> None:
    """§7.2 reject 路径 — 删 staging, hot/index 从未改变 (不存在 '半事实')."""
    towow = tmp_path / ".towow"
    _log, sealed, seg = _log_with_sealed_segment(towow)
    sealed_before = sealed.read_bytes()

    prepared = prepare_archive_to_cold(towow, sealed)
    assert cold_staging_path(towow, seg).exists()

    rollback_prepared_archive(towow, prepared)

    assert not cold_staging_path(towow, seg).exists()  # staging removed
    assert sealed.exists()  # hot never deleted
    assert sealed.read_bytes() == sealed_before  # hot never changed
    assert load_cold_index(towow) == {}  # index never changed
    assert not cold_archive_path(towow, seg).exists()  # no final cold


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    """§7.2 — finalize 幂等 (crash recovery 重做安全): 二次调用不丢不重复, cold/index 不变."""
    towow = tmp_path / ".towow"
    _log, sealed, seg = _log_with_sealed_segment(towow)
    import json

    sealed_lines = [ln for ln in sealed.read_text(encoding="utf-8").splitlines() if ln.strip()]
    event_ids = [json.loads(ln)["event_id"] for ln in sealed_lines]

    prepared = prepare_archive_to_cold(towow, sealed)
    finalize_archive(towow, prepared, event_ids=event_ids)
    cold_bytes = cold_archive_path(towow, seg).read_bytes()

    # second finalize (staging already gone, hot already gone) must be a safe no-op
    finalize_archive(towow, prepared, event_ids=event_ids)
    assert cold_archive_path(towow, seg).read_bytes() == cold_bytes  # not corrupted/rewritten badly
    idx = load_cold_index(towow)
    for eid in event_ids:
        assert idx[eid] == {"location": "cold", "segment": seg}
