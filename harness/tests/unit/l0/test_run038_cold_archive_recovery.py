"""RUN-038 L0增强 M-0.7 §7.2 — Crash recovery + forbidden recovery 协议.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §7.2 Crash Recovery 协议:
#     recover_after_crash():
#       1. accepted ArchiveSegmentMoved 但 finalize 未完成 → 按物理态补 finalize (幂等):
#          - cold 存在 + hot 不存在  → 已 finalize, 只补记 finalize-log
#          - staging 存在            → 崩在 rename 前, 继续 finalize
#          - cold + hot 都在         → 崩在 unlink hot 前, 继续 finalize
#          - 都不在 (异常态)         → 报 finding, 不补 event
#       2. orphan staging (没被任何 accepted event 引用) → 删
#     forbidden: cold 存在但无对应 accepted ArchiveSegmentMoved → 报 finding, 不补 event
#                (event log 唯一事实源硬约束 — 不能补一个原本没 commit 的 event)
#
# Done 判据 (§17 #6): 模拟各阶段 crash → recovery 正确 (不丢不重复); forbidden 态拒.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

from towow.l0.event_log.event_log import EventLog
from towow.l0.event_log.segments import ordered_hot_segments, segment_number_of
from towow.l0.snapshot.cold_archive import (
    is_finalize_completed,
    prepare_archive_to_cold,
    recover_after_crash,
)
from towow.l0.snapshot.cold_storage import (
    cold_archive_path,
    cold_dir,
    cold_staging_dir,
    cold_staging_path,
    load_cold_index,
)
from towow.schemas.enums import (
    ActorType,
    ArchiveDestination,
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

    from towow.l0.snapshot.cold_archive import PreparedArchive


def _touch_intent(i: int) -> EventIntent:
    return EventIntent(
        local_intent_id=f"touch-{i}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"node_id": f"n-{i}"},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="sys"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=f"n-{i}", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _sealed(towow: Path) -> tuple[EventLog, Path, int]:
    (towow / "locks").mkdir(parents=True, exist_ok=True)
    log = EventLog(towow / "events.log", segment_max_events=2)
    for i in range(5):
        log.write_direct(_touch_intent(i))
    sealed = ordered_hot_segments(towow / "events.log")[0]
    seg = segment_number_of(sealed)
    assert seg is not None
    return log, sealed, seg


def _emit_accepted_archive_event(log: EventLog, prepared: PreparedArchive) -> str:
    """Write a real accepted ArchiveSegmentMoved (path B) carrying the prepared_artifact_ref."""
    rec = log.write_direct(
        EventIntent(
            local_intent_id=f"arch-{prepared.segment_id}",
            event_type=EventType.ARCHIVE_SEGMENT_MOVED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={
                "segment_range": {"start_seq": 0, "end_seq": prepared.original_event_count - 1},
                "archive_destination": ArchiveDestination.COLD.value,
                "original_event_count": prepared.original_event_count,
                "provenance_preserved": True,
                "prepared_artifact_ref": {
                    "segment_id": prepared.segment_id,
                    "staging_path": prepared.staging_path,
                    "source_hash": prepared.source_hash,
                    "archive_hash": prepared.archive_hash,
                    "hot_path_unchanged": prepared.hot_path_unchanged,
                },
            },
            provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="snapshot-module"),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.SNAPSHOT,
                    entity_id=f"archive-{prepared.segment_id}",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        ),
    )
    return rec.event_id


def _archived_event_ids(sealed_lines: list[str]) -> list[str]:
    import json

    return [json.loads(ln)["event_id"] for ln in sealed_lines]


def test_recover_crash_before_rename_continues_finalize(tmp_path: Path) -> None:
    """阶段 A — accepted event + staging 存在 (崩在 rename 前) → recovery 补 finalize, round-trip 不丢."""
    towow = tmp_path / ".towow"
    log, sealed, seg = _sealed(towow)
    sealed_lines = [ln for ln in sealed.read_text(encoding="utf-8").splitlines() if ln.strip()]
    prepared = prepare_archive_to_cold(towow, sealed)
    _emit_accepted_archive_event(log, prepared)
    # crash here: accepted event written, staging present, NOT finalized
    assert cold_staging_path(towow, seg).exists()
    assert not cold_archive_path(towow, seg).exists()
    assert not is_finalize_completed(towow, prepared)

    report = recover_after_crash(towow, log)

    assert seg in report.finalized_segments
    assert not report.findings
    # finalize completed: cold exists, staging+hot gone, round-trip byte-identical
    assert cold_archive_path(towow, seg).exists()
    assert not cold_staging_path(towow, seg).exists()
    assert not sealed.exists()
    with gzip.open(cold_archive_path(towow, seg), "rb") as f:
        cold_lines = [ln.decode("utf-8").rstrip("\n") for ln in f if ln.strip()]
    assert cold_lines == sealed_lines  # no event lost during recovery
    idx = load_cold_index(towow)
    for eid in _archived_event_ids(sealed_lines):
        assert idx[eid]["segment"] == seg
    assert is_finalize_completed(towow, prepared)


def test_recover_crash_after_rename_before_unlink(tmp_path: Path) -> None:
    """阶段 B — cold 存在 + hot 还在 (崩在 unlink hot 前) → recovery 续 unlink + 更新 index, 不重复."""
    towow = tmp_path / ".towow"
    log, sealed, seg = _sealed(towow)
    prepared = prepare_archive_to_cold(towow, sealed)
    _emit_accepted_archive_event(log, prepared)
    # simulate: rename done (cold present) but crash before unlink hot
    cold_dir(towow).mkdir(parents=True, exist_ok=True)
    cold_staging_path(towow, seg).replace(cold_archive_path(towow, seg))
    cold_before = cold_archive_path(towow, seg).read_bytes()
    assert sealed.exists()  # hot not yet unlinked

    report = recover_after_crash(towow, log)

    assert seg in report.finalized_segments
    assert not sealed.exists()  # hot now unlinked
    assert cold_archive_path(towow, seg).read_bytes() == cold_before  # cold not rewritten/corrupted
    assert is_finalize_completed(towow, prepared)


def test_recover_crash_after_unlink_only_finalize_log_missing(tmp_path: Path) -> None:
    """阶段 C — cold 存在 + hot 不存在 (已 finalize, 只 finalize-log 没记) → recovery 只补记, 不重做."""
    towow = tmp_path / ".towow"
    log, sealed, seg = _sealed(towow)
    prepared = prepare_archive_to_cold(towow, sealed)
    _emit_accepted_archive_event(log, prepared)
    # simulate: fully finalized physically (cold present, hot gone) but finalize-log lost
    cold_dir(towow).mkdir(parents=True, exist_ok=True)
    cold_staging_path(towow, seg).replace(cold_archive_path(towow, seg))
    sealed.unlink()
    cold_before = cold_archive_path(towow, seg).read_bytes()

    report = recover_after_crash(towow, log)

    assert seg in report.finalized_segments
    assert cold_archive_path(towow, seg).read_bytes() == cold_before  # untouched
    assert is_finalize_completed(towow, prepared)


def test_recover_skips_already_finalized(tmp_path: Path) -> None:
    """已记 finalize-log 的 segment → recovery 跳过 (不重复处理)."""
    towow = tmp_path / ".towow"
    log, sealed, seg = _sealed(towow)
    sealed_lines = [ln for ln in sealed.read_text(encoding="utf-8").splitlines() if ln.strip()]
    prepared = prepare_archive_to_cold(towow, sealed)
    _emit_accepted_archive_event(log, prepared)
    from towow.l0.snapshot.cold_archive import finalize_archive

    finalize_archive(towow, prepared, event_ids=_archived_event_ids(sealed_lines))
    assert is_finalize_completed(towow, prepared)

    report = recover_after_crash(towow, log)
    assert seg not in report.finalized_segments  # nothing to do — already completed


def test_recover_orphan_staging_cleaned(tmp_path: Path) -> None:
    """orphan staging (没被任何 accepted event 引用) → recovery 删."""
    towow = tmp_path / ".towow"
    log, _sealed_path, _seg = _sealed(towow)
    # an orphan staging file for a segment that was never committed
    cold_staging_dir(towow).mkdir(parents=True, exist_ok=True)
    orphan = cold_staging_path(towow, 999)
    orphan.write_bytes(b"orphan staging junk")
    assert orphan.exists()

    report = recover_after_crash(towow, log)

    assert not orphan.exists()  # orphan removed
    assert str(orphan) in report.orphan_staging_removed


def test_recover_forbidden_cold_without_accepted_event_emits_finding(tmp_path: Path) -> None:
    """forbidden — cold 存在但无对应 accepted ArchiveSegmentMoved → 报 finding, 绝不补 event."""
    towow = tmp_path / ".towow"
    log, _sealed_path, _seg = _sealed(towow)
    # a cold archive for segment 7 with NO accepted ArchiveSegmentMoved event referencing it
    cold_dir(towow).mkdir(parents=True, exist_ok=True)
    rogue_cold = cold_archive_path(towow, 7)
    with gzip.open(rogue_cold, "wb") as f:
        f.write(b'{"event_id":"rogue"}\n')
    archive_events_before = len(log.get_events_by_type(EventType.ARCHIVE_SEGMENT_MOVED))

    report = recover_after_crash(towow, log)

    # forbidden state is surfaced as a finding, NOT papered over by synthesizing an event
    assert any(f.kind == "archive_recovery_forbidden_cold" and f.segment_id == 7 for f in report.findings)
    assert rogue_cold.exists()  # recovery does not delete it either — it reports for human/audit
    # hard constraint: recovery wrote ZERO new ArchiveSegmentMoved events
    assert len(log.get_events_by_type(EventType.ARCHIVE_SEGMENT_MOVED)) == archive_events_before
