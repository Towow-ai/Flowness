"""RUN-038 L0增强 M-0.7 §7.3 — Cold Lookup 协议 + get_event 冷回溯 (解锁 Inv2 debt-edb1529a1bc4).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §7.3 Cold Lookup 协议:
#     M-0.1 get_event(event_id) 即使 event 已归档也能返回 (M-0.1 §6.2 承诺). M-0.7 cold_lookup:
#       location = index.get_location(event_id); cold → 解压 archive-{seg}.jsonl.gz 找 event_id.
#   §13.1 反向贡献对 M-0.1: cold_lookup 由 M-0.7 实现, M-0.1 透传调用.
#
# Done 判据 (§17 #6): 归档到 cold 的事件 get_event 仍能回溯读出 (解压+查) 逐字段相同.
# 这正是 M-0.1 §6.2 Inv2 (consolidate 后 get_event 从冷存储回溯被归档事件) 的实现.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.event_log.event_log import EventLog
from towow.l0.event_log.segments import ordered_hot_segments, segment_number_of
from towow.l0.snapshot.cold_archive import finalize_archive, prepare_archive_to_cold
from towow.l0.snapshot.cold_lookup import cold_lookup, cold_records_in_range, has_cold_events
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
        payload={"node_id": f"n-{i}", "marker": i},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="sys"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=f"n-{i}", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )


def _archive_first_segment(towow: Path) -> tuple[EventLog, list[str], int]:
    """Build a log, archive its first sealed segment to cold, return (log, archived_ids, seg)."""
    (towow / "locks").mkdir(parents=True, exist_ok=True)
    log = EventLog(towow / "events.log", segment_max_events=2)
    for i in range(5):
        log.write_direct(_touch_intent(i))
    sealed = ordered_hot_segments(towow / "events.log")[0]
    seg = segment_number_of(sealed)
    assert seg is not None
    archived_ids = [
        json.loads(ln)["event_id"]
        for ln in sealed.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    prepared = prepare_archive_to_cold(towow, sealed)
    finalize_archive(towow, prepared, event_ids=archived_ids)
    assert not sealed.exists()  # archived: hot gone
    return log, archived_ids, seg


def test_cold_lookup_returns_archived_event_field_identical(tmp_path: Path) -> None:
    """§7.3 — archive 到 cold 的事件, cold_lookup 解压找回, 逐字段与归档前相同."""
    towow = tmp_path / ".towow"
    # snapshot the original record BEFORE archiving so we can compare field-by-field
    (towow / "locks").mkdir(parents=True, exist_ok=True)
    log = EventLog(towow / "events.log", segment_max_events=2)
    originals = {}
    for i in range(5):
        rec = log.write_direct(_touch_intent(i))
        originals[rec.event_id] = rec
    sealed = ordered_hot_segments(towow / "events.log")[0]
    seg = segment_number_of(sealed)
    assert seg is not None
    archived_ids = [
        json.loads(ln)["event_id"]
        for ln in sealed.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    prepared = prepare_archive_to_cold(towow, sealed)
    finalize_archive(towow, prepared, event_ids=archived_ids)

    for eid in archived_ids:
        got = cold_lookup(towow, eid)
        assert got is not None, f"archived event {eid} not retrievable from cold"
        # field-identical: same event_id, seq, payload, hash, provenance, etc.
        assert got.event_id == originals[eid].event_id
        assert got.sequence_number == originals[eid].sequence_number
        assert got.payload == originals[eid].payload
        assert got.record_hash == originals[eid].record_hash
        assert got.model_dump_json() == originals[eid].model_dump_json()  # whole record byte-identical


def test_cold_lookup_miss_returns_none(tmp_path: Path) -> None:
    """§7.3 — cold 里没有的 event_id → None (没短路恒返回某条)."""
    towow = tmp_path / ".towow"
    _archive_first_segment(towow)
    assert cold_lookup(towow, "evt-does-not-exist") is None


def test_get_event_falls_back_to_cold_after_archive(tmp_path: Path) -> None:
    """§7.3 / M-0.1 §6.2 Inv2 — 归档后 get_event 仍返回被归档事件 (透传 cold 回溯). 解锁 debt-edb1529a1bc4."""
    towow = tmp_path / ".towow"
    log, archived_ids, _seg = _archive_first_segment(towow)
    # an event still in hot (not archived) must still come from hot
    hot_events = log.all_records()
    assert hot_events, "some events remain hot after archiving the first segment"
    hot_eid = hot_events[-1].event_id

    # 1. archived event: hot index misses → get_event must transparently retrieve from cold
    for eid in archived_ids:
        rec = log.get_event(eid)
        assert rec is not None, f"get_event lost archived event {eid} (Inv2 violated)"
        assert rec.event_id == eid

    # 2. still-hot event: get_event still returns it from hot (cold fallback did not break hot path)
    assert log.get_event(hot_eid) is not None

    # 3. truly-missing event: get_event returns None (fallback isn't a recall-everything short circuit)
    assert log.get_event("evt-nonexistent") is None


def test_get_events_in_range_merges_cold_after_archive(tmp_path: Path) -> None:
    """§6.2 Inv2 (range form) — after archiving a segment, get_events_in_range(0, head) must include
    the archived events (transparent cold merge), in sequence order, exactly one record per seq. This
    is the batch analog of get_event's cold fallback; the projection rebuild tools depend on it."""
    towow = tmp_path / ".towow"
    log, archived_ids, _seg = _archive_first_segment(towow)
    head = log.next_sequence - 1

    ranged = log.get_events_in_range(0, head)
    eids = {r.event_id for r in ranged}
    seqs = [r.sequence_number for r in ranged]
    # archived (cold) events reappear in the full range
    for eid in archived_ids:
        assert eid in eids, f"get_events_in_range dropped archived event {eid} (Inv2 range form broken)"
    # still-hot events are present too (merge, not replace)
    for r in log.all_records():
        assert r.event_id in eids
    # no duplicate seqs from the hot+cold merge, and the result is in sequence order
    assert len(seqs) == len(set(seqs)), "duplicate seqs after hot+cold merge"
    assert seqs == sorted(seqs), "merged range not in sequence order"


def test_cold_records_in_range_above_cold_returns_empty(tmp_path: Path) -> None:
    """The daemon/poller hot path: a range query entirely ABOVE the archived seqs (e.g. [watermark,
    head] with watermark > every cold seq) yields no cold records — the cold archives are skipped /
    short-circuited, never mis-merged into a forward range."""
    towow = tmp_path / ".towow"
    log, archived_ids, _seg = _archive_first_segment(towow)
    head = log.next_sequence - 1
    max_cold_seq = max(cold_lookup(towow, eid).sequence_number for eid in archived_ids)
    # query strictly above the archived region
    above = cold_records_in_range(towow, max_cold_seq + 1, head)
    assert above == [], "cold records leaked into an above-cold range query"
    # and the EventLog range over that window equals the hot-only slice (no cold contribution)
    hot_only = [r for r in log.all_records() if max_cold_seq + 1 <= r.sequence_number <= head]
    merged = log.get_events_in_range(max_cold_seq + 1, head)
    assert {r.event_id for r in merged} == {r.event_id for r in hot_only}


def test_cold_records_in_range_subrange_slice(tmp_path: Path) -> None:
    """A bounded range that straddles the cold region returns exactly the archived records whose seq
    falls in [start, end] — not the whole archive."""
    towow = tmp_path / ".towow"
    _log, archived_ids, _seg = _archive_first_segment(towow)
    cold_seqs = sorted(cold_lookup(towow, eid).sequence_number for eid in archived_ids)
    assert len(cold_seqs) >= 2, "fixture must archive ≥2 events for a meaningful sub-range"
    lo, hi = cold_seqs[0], cold_seqs[-2]  # drop the top cold seq to prove the upper bound is honored
    got = cold_records_in_range(towow, lo, hi)
    got_seqs = sorted(r.sequence_number for r in got)
    expected = [s for s in cold_seqs if lo <= s <= hi]
    assert got_seqs == expected, f"sub-range slice wrong: got {got_seqs}, expected {expected}"


def test_has_cold_events_false_without_archive(tmp_path: Path) -> None:
    """A never-compacted log (no cold/ dir) → has_cold_events False, so get_events_in_range pays only a
    single stat and never touches cold (the hot read path stays cold-free). And the range result is
    exactly the hot committed stream."""
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True, exist_ok=True)
    log = EventLog(towow / "events.log", segment_max_events=2)
    for i in range(5):
        log.write_direct(_touch_intent(i))
    assert has_cold_events(towow) is False, "no archive yet — must report no cold events"
    assert cold_records_in_range(towow, 0, log.next_sequence - 1) == []
    # range == the full hot committed stream
    ranged = log.get_events_in_range(0, log.next_sequence - 1)
    assert {r.event_id for r in ranged} == {r.event_id for r in log.all_records()}


def test_scan_committed_records_of_type_includes_archived_records(tmp_path: Path) -> None:
    """f-fix-complete-gate-ignores-amended-closure-contract — 索引无关的类型扫描必须也看冷区.

    ``scan_committed_records_of_type`` 存在的理由是"不吃派生索引, None 必须真的等于账本里没有";
    如果它只扫 hot, 那么一条已被段内 compaction 搬进冷归档的记录会被读成"不存在" —— 同一个静默失明
    换了个位置 (对闭合门就是: 老 finding 的 amend 被归档后, 门又回落到修订前的合约)。
    """
    towow = tmp_path / ".towow"
    log, archived_ids, _seg = _archive_first_segment(towow)
    assert has_cold_events(towow), "fixture 应已归档一段到冷区"
    hot_ids = {r.event_id for r in log.all_records()}
    assert not (set(archived_ids) & hot_ids), "fixture 的归档记录不该还在 hot (否则测不到冷区)"

    scanned = log.scan_committed_records_of_type(EventType.NODE_TOUCHED)
    scanned_ids = [r.event_id for r in scanned]
    assert set(archived_ids) <= set(scanned_ids), "冷区里的记录没被类型扫描读到"
    assert set(hot_ids) <= set(scanned_ids), "hot 区的记录没被类型扫描读到"
    seqs = [r.sequence_number for r in scanned]
    assert seqs == sorted(seqs), "hot/cold 合并后没按 sequence_number 升序"
    assert len(scanned_ids) == len(set(scanned_ids)), "hot/cold 合并出现重复"

    # 别的类型不会被误捞 (前缀/子串误命中的反证)
    assert log.scan_committed_records_of_type(EventType.COMMIT_ACCEPTED) == []
