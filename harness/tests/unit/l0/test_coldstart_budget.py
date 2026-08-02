"""A1 cold-start read-path budget (eventlog-cold-start-read-path@v1).

# concept: eventlog-cold-start-read-path@v1
#   field cold_start_construction_budget — two composite clauses, both required:
#     (1) 绝对墙钟门: EventLog() 构造入口 → read-ready index ≤3s @ reference scale
#         (committed 账本 ≥219k 事件 且 event_id.idx ≥325MB, Darwin 参考机);
#     (2) 渐近子句 (机器无关): 任何构造路径不得对全量 committed 账本做 EventRecord pydantic 校验 —
#         构造期校验条数须 O(tail delta / active segment), 不随 total committed 规模线性增长.
#   field freshness_adopt_trust_rule — adopt/derive-from-index 不得牺牲跨进程读一致性:
#     从索引派生的 write-guard 哨兵必须 == 全量 committed 全扫的产出 (equivalence, 见末测).

The病灶 this固化 (index.py:306-321 pre-A1): load() re-parsed every event_id.idx line with
EventRecord.model_validate_json (~29s / 349MB on the real ledger; even raw json.loads of the same
349MB is ~18s), AND __init__ then did a SECOND full-log json.loads scan for the write-guard sets
(~4s) — so no full-parse strategy could meet ≤3s. The fix: a LazyEventIndex load (mmap offset scan
+ persisted postings, parse-free) + derive the write-guard sentinels from the adopted index instead
of a second full scan. Both bottlenecks become O(active segment); construction-period pydantic
validation count drops to O(tail), proven machine-independently below.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.index import EventIndex, EventLogIndexStore, LazyEventIndex
from towow.l0.event_log.segments import active_segment_path
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path

_REFERENCE_IDX_BYTES = 325 * 1024 * 1024  # contract reference scale: event_id.idx ≥ 325MB
_BUDGET_SECONDS = 3.0


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


class _ValidationCounter:
    """Count EventRecord.model_validate_json calls (the pydantic full-construction the contract bounds).

    Patched over the classmethod for one construction — the fold path (iter_committed_records_from_lines)
    and any lazy materialize both route through it, so this is the true construction-period count.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        original = EventRecord.model_validate_json.__func__  # type: ignore[attr-defined]

        def counting(cls: type[EventRecord], *args: object, **kwargs: object) -> EventRecord:
            self.count += 1
            return original(cls, *args, **kwargs)

        monkeypatch.setattr(EventRecord, "model_validate_json", classmethod(counting))


def _make_records(n: int, *, payload_pad: int = 0, start_seq: int = 0) -> list[EventRecord]:
    """Build ``n`` valid committed (path-B) EventRecords cheaply.

    One template is validated once; the rest are ``model_copy`` variants (pydantic v2 model_copy does
    not re-validate) with unique event_id / sequence_number. ``payload_pad`` filler bytes let a modest
    record count reach the ≥325MB reference file size (the cold-start cost that scales with LEDGER
    SIZE is the mmap offset scan over event_id.idx — i.e. bytes, not record count).
    """
    pad = "x" * payload_pad if payload_pad else ""
    template = EventRecord(
        event_id=f"evt-{start_seq:08d}",
        sequence_number=start_seq,
        timestamp=datetime(2026, 7, 2, tzinfo=UTC),
        record_hash="h",
        local_intent_id=f"i-{start_seq}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": "c-1", "touch_type": "read", "pad": pad},
        provenance=Provenance(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    out = [template]
    for i in range(1, n):
        seq = start_seq + i
        out.append(
            template.model_copy(
                update={"event_id": f"evt-{seq:08d}", "sequence_number": seq, "local_intent_id": f"i-{seq}"},
            ),
        )
    return out


def _resolver_decision(*, seq: int, resolver_id: str, input_hash: str) -> EventRecord:
    """Build a committed ``ResolverDecisionMade`` EventRecord carrying an ``input_hash`` bucket key
    (index.py ``_index_one`` only populates ``_by_input_hash`` for this event_type + a
    ``payload.after_state.{resolver_id, input_hash}`` shape).
    """
    return EventRecord(
        event_id=f"evt-res{seq:08d}",
        sequence_number=seq,
        timestamp=datetime(2026, 7, 2, tzinfo=UTC),
        record_hash="h",
        local_intent_id=f"i-res-{seq}",
        event_type=EventType.RESOLVER_DECISION_MADE,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={"after_state": {"resolver_id": resolver_id, "input_hash": input_hash}},
        provenance=Provenance(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _touched(*, seq: int, event_id: str, task_id: str | None) -> EventRecord:
    """A committed NODE_TOUCHED record with a caller-chosen ``task_id``.

    ``task_id=None`` reproduces the systematic real-ledger tail shape: DaemonRunCompleted /
    CommitAccepted carry ``task_id=run_id=correlation_id=causation=null``, so ``_index_one`` touches
    NONE of the conditionally-populated buckets (task/run/…) for them — the exact record shape whose
    non-touching tail let a stale ``task.idx`` slip past the pre-fix tail-consistency check.
    """
    return EventRecord(
        event_id=event_id,
        sequence_number=seq,
        timestamp=datetime(2026, 7, 2, tzinfo=UTC),
        record_hash="h",
        local_intent_id=f"i-{event_id}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": "c-1", "touch_type": "read"},
        provenance=Provenance(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id=task_id,
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _write_segment(path: Path, records: list[EventRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")


# ─── dc1: absolute wall-clock ≤3s at reference scale ─────────────────────────────


def test_coldstart_construction_under_3s_at_reference_scale(tmp_path: Path) -> None:
    """a1-dc1-wallclock: 参考规模 (.idx ≥325MB) + 一份落后不超一个 active segment 的持久化索引下,
    EventLog 构造到 read-ready ≤3s。修复前 load() 对全部 ~236k 行 model_validate_json ≈29s。

    Only the active segment + the .idx set are read at construction (adopt happy path), so the
    fixture writes a ≥325MB event_id.idx (padded payloads keep the record count — and thus setup
    time — modest while the mmap-scanned byte size is reference-faithful) plus a small active segment
    carrying the lagged tail. An O(N)-parse regression over this padded 325MB set is seconds over
    budget, so the assertion has teeth (the machine-independent guarantee is the next test).
    """
    payload_pad = 16 * 1024  # ~16KB/record → ~21k records reach 325MB (event_id.idx = 1 full record/line)
    per_record = len(_make_records(1, payload_pad=payload_pad)[0].model_dump_json()) + 1
    total = _REFERENCE_IDX_BYTES // per_record + 200
    lag = 8  # persisted .idx lags the on-disk tail by 8 records (well within one active segment)

    records = _make_records(total, payload_pad=payload_pad)
    log_path = tmp_path / "events.log"
    log_path.touch()  # empty base segment
    # persist the .idx over all-but-the-last-`lag` records (the lagging cache the cold start adopts)
    store = EventLogIndexStore(log_path.parent)
    store.persist(records[: total - lag])
    idx_bytes = (store.index_dir / "event_id.idx").stat().st_size
    assert idx_bytes >= _REFERENCE_IDX_BYTES, f"fixture event_id.idx {idx_bytes}B < reference {_REFERENCE_IDX_BYTES}B"
    # active segment holds the .idx's tail record (for the ghost spot-check) + the `lag` newer records,
    # so gate-3 resolves and the fold catches up contiguously.
    _write_segment(active_segment_path(log_path), records[total - lag - 1 :])

    t0 = time.perf_counter()
    log = EventLog(log_path, segment_max_bytes=_REFERENCE_IDX_BYTES * 2)
    elapsed = time.perf_counter() - t0

    assert log._index is not None, "reference-scale .idx within one active segment must be ADOPTED, not rebuilt"
    assert log._index.max_sequence == total - 1, "adopt+fold must reach the on-disk committed tail"
    assert elapsed <= _BUDGET_SECONDS, f"cold-start construction {elapsed:.2f}s > {_BUDGET_SECONDS}s budget"


# ─── dc2: asymptotic — construction pydantic count is O(active segment), not O(total) ────


def _build_lagging_ledger(tmp_path: Path, *, total: int, seg_events: int, lag: int) -> Path:
    """Real-write-path ledger of ``total`` records in ``seg_events``-sized segments, with the persisted
    .idx lagging the tail by ``lag`` records (all within the active segment)."""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path, segment_max_events=seg_events, segment_max_bytes=10 * 1024 * 1024)
    for i in range(total - lag):
        writer.write_direct(_node_touched(local_intent_id=f"i-{i}"))
    writer.get_events_by_task("t-1")  # RUN-055 lazy persist → .idx now covers seq 0..(total-lag-1)
    for i in range(total - lag, total):
        writer.write_direct(_node_touched(local_intent_id=f"i-{i}"))
    return log_path


def test_coldstart_pydantic_count_bounded_by_active_segment_not_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a1-dc2-asymptotic (machine-independent): construction-period EventRecord pydantic validation
    count is bounded by the ACTIVE SEGMENT tail (O(tail)), and does NOT grow with total committed size.

    Proven by constructing at two totals (small vs 4× larger) sharing the SAME active-segment size:
    the counts must be equal and ≤ active-segment size + a small constant (the fold parses the active
    segment to find its delta; the ghost gate materializes 1). This is the durable guard the
    contract's asymptotic clause demands — it fails on any O(total) parse regardless of machine speed.
    """
    seg_events = 50
    lag = 5
    counts: dict[int, int] = {}
    for total in (100, 400):  # 2 vs 8 segments; identical active-segment tail geometry
        sub = tmp_path / f"n{total}"
        sub.mkdir()
        log_path = _build_lagging_ledger(sub, total=total, seg_events=seg_events, lag=lag)

        counter = _ValidationCounter(monkeypatch)
        log = EventLog(log_path, segment_max_events=seg_events, segment_max_bytes=10 * 1024 * 1024)
        counts[total] = counter.count
        monkeypatch.undo()

        assert log._index is not None
        assert log._index.max_sequence == total - 1
        # bounded by the active segment (≤ seg_events records parsed by the fold) + a tiny constant,
        # and strictly far below total — never an O(total) full re-parse.
        assert counts[total] <= seg_events + 5, f"total={total}: {counts[total]} validations > active-seg bound"
        assert counts[total] < total, f"total={total}: {counts[total]} validations not << total (O(total) regression)"

    assert counts[100] == counts[400], (
        f"pydantic count scaled with total ledger size ({counts[100]} vs {counts[400]}) — O(total) regression"
    )


# ─── freshness_adopt_trust_rule: derive-from-index sentinels == full committed scan ──────


def test_derive_lifecycle_from_index_equals_full_scan(tmp_path: Path) -> None:
    """freshness_adopt_trust_rule / correctness landmine: the write-guard sentinels
    (_event_id_set / _sequence_set / _next_sequence) DERIVED from the adopted index on the cold-start
    happy path must be byte-identical to what a full committed scan (the adopt-miss fallback) builds —
    a subtle divergence would let a duplicate seq / event_id slip past the write guards = L0 corruption.

    Exercised against an un-sentineled (uncommitted) active-segment tail: it is physically present but
    NOT committed-visible, so both the derive-from-index path and the full committed scan must exclude
    it identically.
    """
    log_path = _build_lagging_ledger(tmp_path / "adopt", total=40, seg_events=12, lag=4)
    # append a lone path-A domain line (batch_id set, no sentinel) → uncommitted, invisible to reads.
    with log_path.open("a", encoding="utf-8") as f:
        ghost = _make_records(1, start_seq=999)[0].model_copy(
            update={
                "transaction_id": "tx-open",
                "batch_id": "b-open",
                "batch_position": 0,
                "event_id": "evt-uncommitted-tail",
            },
        )
        f.write(ghost.model_dump_json() + "\n")

    # (a) adopt path — sentinels derived from the adopted index
    adopt_log = EventLog(log_path)
    assert adopt_log._index is not None, "expected adopt (lagging .idx within active segment)"
    adopt_eids = set(adopt_log._event_id_set)
    adopt_seqs = set(adopt_log._sequence_set)
    adopt_next = adopt_log._next_sequence

    # (b) full-scan reference — force the adopt-miss fallback by deleting the persisted .idx
    for idx_file in EventLogIndexStore(log_path.parent).index_dir.glob("*.idx"):
        idx_file.unlink()
    scan_log = EventLog(log_path)
    assert scan_log._index is None, "no .idx → full-scan fallback path"

    assert adopt_eids == set(scan_log._event_id_set), "derived event_id set != full committed scan"
    assert adopt_seqs == set(scan_log._sequence_set), "derived sequence set != full committed scan"
    assert adopt_next == scan_log._next_sequence, "derived next_sequence != full committed scan"
    # the uncommitted tail is excluded from both (committed-visibility preserved)
    assert "evt-uncommitted-tail" not in adopt_eids
    assert 999 not in adopt_seqs


def test_load_tolerates_historical_dup_seqs(tmp_path: Path) -> None:
    """Regression (real-ledger adopt): the live ledger carries 553 historical dup-seqs (the 06-10
    rollback) — two records share a sequence_number, so sequence.idx (keyed by seq, last-wins) collapses
    BELOW the event_id.idx line count. load() must NOT reject such a .idx on a record-count mismatch:
    an earlier naive ``len(sequence.idx) == len(event_id.idx)`` check did, making the real 237k-event
    ledger fall back to a full ~29s rebuild on every cold start (adopt never succeeded). The
    dup-seq-tolerant tail-consistency check accepts it while still catching torn-persist.
    """
    records = _make_records(10)
    # force a duplicate seq: records[6] reuses seq 5 (distinct event_id) → sequence.idx collapses to 9 keys
    records[6] = records[6].model_copy(update={"sequence_number": 5})
    store = EventLogIndexStore(tmp_path)
    store.persist(records)

    # posting files are now wrapped ``{"__gen__": token, "buckets": {...}}`` (torn-persist generation
    # anchor, f-a1-coldstart-torn-persist-optional-posting-freshness-hole) — unwrap to the buckets.
    seq_idx = json.loads((store.index_dir / "sequence.idx").read_text(encoding="utf-8"))["buckets"]
    eid_lines = [ln for ln in (store.index_dir / "event_id.idx").read_text(encoding="utf-8").splitlines() if ln]
    assert len(seq_idx) < len(eid_lines), "fixture must exercise the dup-seq collapse (seq keys < record lines)"

    loaded = store.load()
    assert loaded is not None, "dup-seq .idx must still load (no naive count reject → the real-ledger regression)"
    assert isinstance(loaded, LazyEventIndex)
    assert loaded.event_id_set() == {r.event_id for r in records}, "all event_ids present despite the seq collapse"
    assert loaded.lookup_event_id(records[6].event_id) is not None, "the dup-seq record is still resolvable"


@pytest.mark.parametrize("torn_bucket", ["task", "input_hash"])
def test_load_rejects_torn_stale_optional_posting_with_non_touching_tail(
    tmp_path: Path,
    torn_bucket: str,
) -> None:
    """f-a1-coldstart-torn-persist-optional-posting-freshness-hole (closure criterion 1).

    The torn-persist hole: ``load`` began trusting the on-disk key→postings files directly, guarded
    only by a tail-consistency check that verifies event_id.idx's tail record is present in the
    buckets IT belongs to. But task/run/…/input_hash are conditionally-populated buckets, and the real
    ledger tail (DaemonRunCompleted/CommitAccepted) systematically carries ``task_id=run_id=null`` —
    a null-field tail touches NONE of them, so a ``{bucket}.idx`` frozen a generation behind (missing a
    record the new event_id.idx committed) was adopted uncensored → ``get_events_by_task`` /
    ``lookup_input_hash`` served a stale view missing an already-committed record.

    The generation anchor closes it for EVERY conditional bucket (parametrized over task + input_hash,
    per the contract's "≥2 different conditional buckets"): each posting is stamped with the token of
    the event_id.idx it was built for, and ``load`` rejects any posting whose stamp != the token it
    recomputes from the event_id.idx it actually reads. The check is INDEPENDENT of whether the tail
    touches the bucket, so the null-field-tail hole is gone.
    """
    prefix = _touched(seq=0, event_id="evt-prefix", task_id=None)  # an earlier generation's record
    keyed = (
        _touched(seq=1, event_id="evt-keyed", task_id="T-late")
        if torn_bucket == "task"
        else _resolver_decision(seq=1, resolver_id="R", input_hash="H")
    )
    tail = _touched(seq=2, event_id="evt-tail", task_id=None)  # null-field tail: touches no conditional bucket
    newer = [prefix, keyed, tail]  # the real committed set: `keyed` populates `torn_bucket`; the tail does not

    store = EventLogIndexStore(tmp_path)
    store.persist([prefix])  # an earlier generation whose `{torn_bucket}.idx` lacks `keyed`
    stale_posting = (store.index_dir / f"{torn_bucket}.idx").read_bytes()
    store.persist(newer)  # the new generation: event_id.idx + all postings advance
    assert store.load() is not None, "a clean, consistent .idx set must still ADOPT (guard not over-tightened)"

    # torn / concurrent persist: this ONE posting never advanced to the new generation (missing `keyed`)
    (store.index_dir / f"{torn_bucket}.idx").write_bytes(stale_posting)
    assert tail.provenance.task_id is None  # the tail touches neither task nor input_hash

    assert store.load() is None, (
        f"a stale {torn_bucket}.idx a generation behind (missing a committed record) must be rejected "
        "even though the null-field tail touches no conditional bucket — the exact freshness hole"
    )


def test_load_still_rejects_torn_posting_when_tail_touches_bucket(tmp_path: Path) -> None:
    """f-a1-coldstart-torn-persist-optional-posting-freshness-hole (closure criterion 2 — control).

    The fix must not LOOSEN the guard: when the tail record DOES touch the torn bucket, a posting a
    generation behind is still rejected. Here the stale ``task.idx`` still lists the tail record (so
    the pre-existing tail-consistency check would PASS on it) yet is missing a NON-tail committed
    record (``T-mid``) — only the generation anchor catches it. This pins that the anchor rejects on
    generation identity, not on "does the tail happen to be present".
    """
    prefix = _touched(seq=0, event_id="evt-prefix", task_id="T-prefix")
    keyed = _touched(seq=1, event_id="evt-mid", task_id="T-mid")  # committed record the stale task.idx drops
    tail = _touched(seq=2, event_id="evt-tail", task_id="T-tail")  # tail DOES touch the task bucket

    store = EventLogIndexStore(tmp_path)
    # older = prefix + tail (so the stale task.idx still lists the tail → tail-consistency would pass);
    # it lacks T-mid, which `newer` commits.
    store.persist([prefix, tail])
    stale_task = (store.index_dir / "task.idx").read_bytes()
    store.persist([prefix, keyed, tail])
    (store.index_dir / "task.idx").write_bytes(stale_task)
    assert tail.provenance.task_id == "T-tail"  # tail touches the torn bucket, yet load must still reject

    assert store.load() is None, (
        "a task.idx a generation behind must be rejected even when the tail touches the task bucket "
        "and the stale posting still lists the tail (guard must reject on generation, not tail presence)"
    )


def test_load_path_matches_rebuild_byte_for_byte(tmp_path: Path) -> None:
    """a1-dc4-derived-cache: .idx 永为派生缓存、绝不成为事实源 — the cold-start load path's output must
    equal a from-records rebuild on (event_id 集合 / max_sequence / postings) 逐字一致.

    The A1 lazy load reads the persisted postings directly (parse-free) rather than re-deriving them
    from parsed records; this pins that the realization it now TRUSTS is byte-for-byte what a rebuild
    over the same records produces — trusting the cache never diverges from the source of truth.
    (dc4's gate selector test_run038_index_persistence.py stays green via its rebuild-consistency
    tests; this adds the precise event_id-set / max_sequence / postings byte-equality assertion.)
    """
    records = _make_records(9)
    store = EventLogIndexStore(tmp_path)

    store.persist(records)
    loaded = store.load()  # lazy load path (reads persisted postings + offset map)
    assert loaded is not None

    for idx_file in store.index_dir.glob("*.idx"):  # nuke the cache, rebuild straight from records
        idx_file.unlink()
    rebuilt = store.rebuild_from_records(records)  # eager from-records path

    assert loaded.event_id_set() == rebuilt.event_id_set(), "event_id set diverges between load and rebuild"
    assert loaded.max_sequence == rebuilt.max_sequence, "max_sequence diverges between load and rebuild"
    assert loaded.serializable_key_indexes() == rebuilt.serializable_key_indexes(), (
        "postings diverge between the load path and a from-records rebuild — derived cache became authoritative"
    )


def test_fold_input_hash_collision_last_wins_matches_rebuild(tmp_path: Path) -> None:
    """f-a1-lazy-fold-input-hash-last-wins-divergence: ``input_hash`` is a single-value last-wins
    bucket (``EventIndex._index_one`` dict-assigns ``_by_input_hash[(resolver_id, input_hash)] = rec``),
    unlike the other 13 postings, which are list-append multi-value buckets. Folding a
    ``ResolverDecisionMade`` that reuses an ``input_hash`` key already present in the base must
    OVERWRITE that key — a from-scratch rebuild over base+fold only ever keeps the LAST record for a
    given key. Before the fix, ``LazyEventIndex.add_records`` unconditionally ``extend``-ed every
    bucket, so the folded index kept BOTH records (``[old, new]``) while a full rebuild kept only
    ``[new]``; ``lookup_input_hash`` then returned the stale ``old`` record instead of the latest
    decision.
    """
    base = [_resolver_decision(seq=0, resolver_id="R", input_hash="H")]
    fold = [_resolver_decision(seq=1, resolver_id="R", input_hash="H")]  # reuses base's (resolver_id, input_hash)

    store = EventLogIndexStore(tmp_path)
    store.persist(base)
    lazy = store.load()
    assert lazy is not None
    lazy.add_records(fold)

    rebuilt = EventIndex(base + fold)

    assert lazy.serializable_key_indexes()["input_hash"] == rebuilt.serializable_key_indexes()["input_hash"], (
        "folded input_hash postings diverge from a from-scratch rebuild"
    )
    latest = lazy.lookup_input_hash("R", "H")
    rebuilt_latest = rebuilt.lookup_input_hash("R", "H")
    assert latest is not None
    assert rebuilt_latest is not None
    assert latest.event_id == fold[0].event_id, "lookup_input_hash returned a stale record after fold"
    assert latest.event_id == rebuilt_latest.event_id


def _make_colliding_pair(*, seq: int) -> tuple[EventRecord, EventRecord]:
    """Two distinct committed EventRecords engineered to collide on the SAME key across every
    named ``_index_one`` bucket at once — same sequence_number / transaction_id / batch_id /
    event_type / event_category / actor_id / task_id / run_id / correlation_id /
    causation_event_id / supersede target / an obligation-typed subject (doubles as the entity-
    bucket key) / a shared ``ResolverDecisionMade`` (resolver_id, input_hash) — so folding both
    into one ``EventIndex`` exercises every bucket's real assignment-vs-append semantics with a
    single fixture.
    """
    template = EventRecord(
        event_id=f"evt-collide-a-{seq:08d}",
        sequence_number=seq,
        timestamp=datetime(2026, 7, 2, tzinfo=UTC),
        record_hash="h",
        local_intent_id=f"i-collide-a-{seq}",
        transaction_id="tx-collide",
        batch_id="b-collide",
        batch_position=0,
        event_type=EventType.RESOLVER_DECISION_MADE,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={"after_state": {"resolver_id": "R-collide", "input_hash": "H-collide"}},
        provenance=Provenance(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="actor-collide",
            task_id="t-collide",
            run_id="run-collide",
            correlation_id="corr-collide",
            causation_event_id="evt-cause-collide",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(
            is_supersede=True,
            superseded_event_id="evt-superseded-collide",
            novelty="collision fixture",
            novelty_type=SupersedeNoveltyType.CORRECTED_ERROR,
        ),
        subjects=[
            Subject(entity_type=SubjectEntityType.OBLIGATION, entity_id="ob-collide", role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )
    rec_a = template
    rec_b = template.model_copy(
        update={"event_id": f"evt-collide-b-{seq:08d}", "local_intent_id": f"i-collide-b-{seq}"},
    )
    return rec_a, rec_b


def test_single_value_key_indexes_matches_actual_dict_assignment_semantics() -> None:
    """f-a1-single-value-bucket-registration-unenforced: ``_SINGLE_VALUE_KEY_INDEXES`` is a hand-
    maintained frozenset declaring which ``_index_one`` buckets are single-value (dict assignment,
    last-wins) rather than multi-value (list-append) — nothing tied the two declarations together, so
    a future bucket added to ``_index_one`` without updating the frozenset would silently reproduce
    f-a1-lazy-fold-input-hash-last-wins-divergence (``LazyEventIndex.add_records`` extending a bucket
    that should overwrite).

    f-a1-single-value-heuristic-defaultdict-convention-dependent (verify-step fix_after 复验): an
    earlier version of this test classified buckets by ``isinstance(bucket, dict) and not
    isinstance(bucket, defaultdict)`` — a CONTAINER-TYPE proxy for "single-value", not a direct test
    of the assignment semantics it claims to pin. That proxy only holds because every current
    multi-value bucket happens to be coded ``defaultdict(list)``; a future bucket coded as plain
    ``dict`` + ``setdefault(key, []).append(rec)`` (a real alternative coding path) would be
    misjudged single-value by container type alone, and a maintainer "fixing" the resulting failure
    by registering that bucket into ``_SINGLE_VALUE_KEY_INDEXES`` (instead of fixing the container)
    would reproduce f-a1-lazy-fold-input-hash-last-wins-divergence in reverse (silent data loss on
    the OTHER buckets). This version instead feeds two colliding records (``_make_colliding_pair``)
    through the real ``EventIndex`` construction path and classifies each bucket BEHAVIORALLY, by how
    many event_ids the shared key ends up holding (1 → overwrite/single-value, 2 → append/
    multi-value) — it never inspects the container, so it cannot be misled by container type.
    """
    rec_a, rec_b = _make_colliding_pair(seq=1)
    index = EventIndex([rec_a, rec_b])
    postings = index.serializable_key_indexes()

    actual_single_value_buckets = set()
    for name, buckets in postings.items():
        assert len(buckets) == 1, (
            f"bucket {name!r} split the colliding pair across {len(buckets)} keys instead of 1 — "
            "the fixture no longer collides on this bucket's key"
        )
        (event_ids,) = buckets.values()
        count = len(event_ids)
        assert count in (1, 2), f"bucket {name!r}: {count} event_ids for a 2-record collision fixture (want 1 or 2)"
        if count == 1:
            actual_single_value_buckets.add(name)

    assert actual_single_value_buckets == EventIndex._SINGLE_VALUE_KEY_INDEXES

    # Falsification check: confirm the behavioral (count-based) rule above — unlike the old
    # container-type proxy — correctly reads a hand-simulated multi-value bucket implemented
    # WITHOUT defaultdict (plain dict + setdefault-append) as multi-value.
    plain_dict_multi_value_bucket: dict[str, list[str]] = {}
    plain_dict_multi_value_bucket.setdefault("k", []).append(rec_a.event_id)
    plain_dict_multi_value_bucket.setdefault("k", []).append(rec_b.event_id)
    assert not isinstance(plain_dict_multi_value_bucket, defaultdict), "fixture must be a plain dict, not defaultdict"
    assert len(plain_dict_multi_value_bucket["k"]) == 2, (
        "the count-based classification must read this plain-dict setdefault-append bucket as "
        "multi-value by its actual record count, not misjudge it single-value by container type"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
