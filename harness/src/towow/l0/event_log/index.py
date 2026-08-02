"""15 in-memory indexes per M-0.1a Patch 3 §3.2 + persistent .idx layer (RUN-038).

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   附录 B Patch 3 §3.1-§3.2 — subjects[] unified indexing field + 15 indexes
#   §7.1 文件布局 — .towow/events/index/*.idx
#   §7.2 索引格式 — "简单的 key-value 文件"; "索引是可重建的 ... 索引不是事实源, event 文件才是"
#
# EventIndex: in-memory derived index over a list of EventRecord (built from subjects[]).
# EventLogIndexStore (RUN-038 L0增强): persists the 15 indexes to .towow/events/index/*.idx
# and rebuilds them from the event files. The persisted index is a *derived cache* — the
# event log files remain the source of truth, so load() returns None when nothing is
# persisted and rebuild_from_records() can always reconstruct from scratch.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mmap
import os
import re
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from towow.schemas.event_record import EventRecord

if TYPE_CHECKING:
    from pathlib import Path

# Stub-rewrap envelope type (see towow.l2.orchestrator._unwrap_stub_rewrap): a record whose
# event_type is this carries its REAL logical type in payload["kind"] + the real payload in
# payload["stub_original_payload"]. lookup_types_or_stub_kinds (below, both EventIndex and
# LazyEventIndex) needs this to find records "logically of type T" whether they're flat T records
# or NodeTouched(kind=T) stub-rewraps.
_STUB_REWRAP_TYPE = "NodeTouched"

# The top-level event_id + sequence_number of every event_id.idx line (compact model_dump_json
# puts event_id first and sequence_number immediately second, per EventRecord field declaration
# order — so the first match on a line is the record's own id/seq, never a payload-nested one).
# Used by the cold-start offset scan (no full pydantic parse; A1 手法) AND by the range pre-filter
# (f-perf2-catchup-full-materialize-for-narrow-seq-range — see LazyEventIndex.records_in_range):
# capturing both fields in one match gets the range-query pre-filter key at zero extra scan cost
# over the event_id-only regex it replaces.
_EVENT_ID_SEQ_RE = re.compile(rb'"event_id":"(evt-[A-Za-z0-9_-]+)","sequence_number":(\d+)')

# Reserved key wrapping every persisted key→postings file with the generation token of the
# event_id.idx it was built for (f-a1-coldstart-torn-persist-optional-posting-freshness-hole):
# ``{"__gen__": <token>, "buckets": {key → [event_id]}}``. load() recomputes the token from the
# event_id.idx it actually scans and adopts a posting ONLY when the two agree — see
# ``_generation_token`` / ``EventLogIndexStore.load``.
_GENERATION_KEY = "__gen__"
_BUCKETS_KEY = "buckets"


class EventIndex:
    """In-memory derived index over a list of EventRecord."""

    # Named postings (serializable_key_indexes() keys) whose bucket is a single last-wins value
    # (dict assignment in _index_one — e.g. self._by_sequence[k] = rec) rather than a list append.
    # LazyEventIndex.add_records must OVERWRITE these keys when folding, not extend: a from-scratch
    # rebuild only ever keeps the LAST record for a single-value key, so appending here would let a
    # folded record that reuses an existing key drift from what a full rebuild produces
    # (f-a1-lazy-fold-input-hash-last-wins-divergence).
    _SINGLE_VALUE_KEY_INDEXES = frozenset({"sequence", "input_hash"})

    def __init__(self, records: list[EventRecord]) -> None:
        self._records = records
        self._by_event_id: dict[str, EventRecord] = {}
        self._by_sequence: dict[int, EventRecord] = {}
        self._by_transaction: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_batch: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_type: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_category: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_actor: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_task: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_run: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_correlation: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_causation: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_supersede: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_entity: dict[tuple[str, str], list[EventRecord]] = defaultdict(list)
        self._by_obligation: dict[str, list[EventRecord]] = defaultdict(list)
        self._by_input_hash: dict[tuple[str, str], EventRecord] = {}
        self._build()

    def _build(self) -> None:
        for rec in self._records:
            self._index_one(rec)

    def _index_one(self, rec: EventRecord) -> None:
        """Fold ONE record into every bucket (the per-record body of _build).

        Factored out so the bulk build (_build), 写路径单条增量 add() (427f548) 和读路径批量
        catch-up add_records (T-FND-02 daemon-choke fix) share the IDENTICAL indexing logic —
        三路径 can never drift, so an incrementally-grown index is byte-for-byte the index a full
        rebuild over the same records would produce.
        """
        self._by_event_id[rec.event_id] = rec
        self._by_sequence[rec.sequence_number] = rec
        if rec.transaction_id is not None:
            self._by_transaction[rec.transaction_id].append(rec)
        if rec.batch_id is not None:
            self._by_batch[rec.batch_id].append(rec)
        self._by_type[rec.event_type.value].append(rec)
        self._by_category[rec.event_category.value].append(rec)
        self._by_actor[rec.provenance.actor_id].append(rec)
        if rec.provenance.task_id is not None:
            self._by_task[rec.provenance.task_id].append(rec)
        if rec.provenance.run_id is not None:
            self._by_run[rec.provenance.run_id].append(rec)
        if rec.provenance.correlation_id is not None:
            self._by_correlation[rec.provenance.correlation_id].append(rec)
        if rec.provenance.causation_event_id is not None:
            self._by_causation[rec.provenance.causation_event_id].append(rec)
        if rec.supersede.superseded_event_id is not None:
            self._by_supersede[rec.supersede.superseded_event_id].append(rec)
        for subject in rec.subjects:
            self._by_entity[(subject.entity_type.value, subject.entity_id)].append(rec)
            if subject.entity_type.value == "obligation":
                self._by_obligation[subject.entity_id].append(rec)
        # input_hash only on ResolverDecisionMade
        if rec.event_type.value == "ResolverDecisionMade":
            payload = rec.payload
            if isinstance(payload, dict):
                after = payload.get("after_state")
                if isinstance(after, dict):
                    resolver_id = after.get("resolver_id")
                    input_hash = after.get("input_hash")
                    if isinstance(resolver_id, str) and isinstance(input_hash, str):
                        self._by_input_hash[(resolver_id, input_hash)] = rec

    def add(self, rec: EventRecord) -> None:
        """Incrementally index one newly-committed record (T-FND-02 写路径 / 427f548).

        Keeps _records complete (so a later persist() over self._records writes the full set) and
        updates all 15 postings via the same _index_one() the full rebuild uses. Caller (EventLog)
        only calls this when the index already covers exactly the pre-write committed tail (no
        external append since build) — 否则 EventLog 现在保持索引 warm, 留给读路径 _catch_up_index
        折入活动段增量 (34ef2e7), 不再 invalidate=None。recover()/truncation always invalidates.

        并发 (T-FND-02 review 抓出的设计点): add() 由 EventLog 在写锁 (self._mutex+file_lock) 内调,
        而 lookup_* 读在锁外。in-place 改 dict 理论上让锁外读看到"部分更新"的索引。但: ①daemon 热
        路径单线程顺序写→读, 进程内无并发; ②各 lookup 是 dict.get / list(copy), GIL 下原子且对并发
        append 不崩 (不同于 dict 迭代); ③看到 in-flight 写的部分可见性, 对"与写并发的读"语义可接受。
        故非 correctness blocker; 若未来引入多线程并发读写同一 EventLog, 需给 lookup 加读锁或快照。
        """
        self._records.append(rec)
        self._index_one(rec)

    def add_records(self, records: list[EventRecord]) -> None:
        """Append newly-committed records to the index in place (T-FND-02 daemon-choke fix).

        The EventLog folds newly-committed records (its own appends + other processes' commits,
        discovered by the active-segment catch-up) into the warm index instead of nulling it and
        re-scanning the whole ledger on the next read. ``records`` must be in committed (sequence)
        order — the caller passes them straight from iter_committed_records_from_lines, which
        preserves it — so every postings bucket stays in sequence order exactly as _build leaves it
        (max_sequence auto-tracks: it reads _by_sequence). Callers must hand only records the index
        does not already hold (seq > max_sequence); re-adding an existing record would double it.
        """
        for rec in records:
            self._records.append(rec)
            self._index_one(rec)

    def records(self) -> list[EventRecord]:
        """Snapshot of the indexed committed records in sequence order (T-FND-02).

        The in-memory equivalent of EventLog.all_records() for consumers (e.g. the orchestrator
        stuck-baton sweep) that want the whole committed stream WITHOUT a fresh disk scan + pydantic
        re-parse — the catch-up keeps the index current, so this is an in-memory list copy, not an
        O(ledger) reload. Copied so callers can't mutate the index's internal record list.
        """
        return list(self._records)

    @property
    def total_records(self) -> int:
        return len(self._records)

    @property
    def max_sequence(self) -> int:
        """Highest committed sequence_number covered by this index (-1 if empty).

        The freshness watermark for the persisted index (RUN-055): an EventLog loading a .idx at
        startup compares this against the committed tail it derives from the log files, and only
        trusts the loaded index when they agree — an append since persist makes the loaded index
        stale, so it is discarded and rebuilt from the event files (the source of truth).
        """
        return max(self._by_sequence, default=-1)

    def event_id_set(self) -> set[str]:
        """The committed event_id set this index covers (write-time uniqueness-guard authority).

        Equivalent to ``{r.event_id for r in committed_records}`` — EventLog derives its
        ``_event_id_set`` from here on the adopt path instead of a second full-log scan (A1
        cold-start: the adopted+caught-up index already IS the committed stream, so the sets are
        provably identical to a fresh full scan). event_id is unique, so no dedup subtlety.
        """
        return set(self._by_event_id)

    def sequence_set(self) -> set[int]:
        """The committed sequence_number set this index covers (T-RMD-SEQ-GUARD 段① authority).

        Equivalent to the ``set()`` a full committed scan builds — historical dup-seqs collapse to
        one membership each because ``_by_sequence`` is keyed by seq (same dedup as the old
        ``_derive_lifecycle_state``).
        """
        return set(self._by_sequence)

    def lookup_event_id(self, event_id: str) -> EventRecord | None:
        return self._by_event_id.get(event_id)

    def lookup_entity(self, entity_type: str, entity_id: str) -> list[EventRecord]:
        return list(self._by_entity.get((entity_type, entity_id), []))

    def lookup_obligation(self, obligation_id: str) -> list[EventRecord]:
        return list(self._by_obligation.get(obligation_id, []))

    def lookup_supersede(self, superseded_event_id: str) -> list[EventRecord]:
        """Forward supersede lookup: events that supersede the given event_id.

        Used by M-0.5 VersionConsistencyCheck (§3.1) — is the capsule a commit references
        superseded by a later event? `get_supersede_chain` traverses backward (what X
        supersedes); this is the forward direction (who supersedes X).
        """
        return list(self._by_supersede.get(superseded_event_id, []))

    def lookup_causation(self, causation_event_id: str) -> list[EventRecord]:
        """Events whose provenance.causation_event_id == the given event_id (the events it caused).

        Used by M-0.5 ObligationCheck §3.4.1: the obligations a capsule injected are the
        ObligationActivated events the M-0.3 pipeline emitted causation-linked to that
        CapsuleCompiled (pipeline._emit_obligation_activated sets causation_event_id=capsule id),
        so the precise injected-obligation set is recoverable from the committed log.
        """
        return list(self._by_causation.get(causation_event_id, []))

    def lookup_input_hash(self, resolver_id: str, input_hash: str) -> EventRecord | None:
        return self._by_input_hash.get((resolver_id, input_hash))

    # ─── filter-API postings (RUN-055: §4.1.2-§4.1.7 走索引桶, 非 O(n) 全扫) ───────
    #
    # Each returns the bucket in committed (sequence) order — _build appends in the iteration
    # order of the records list, which the EventLog builds from _iter_committed_records (sequence
    # order). The since_seq / limit query params stay in the EventLog API (they are per-call, not
    # part of the index). Lists are copied so a caller can't mutate the index's internal postings.

    def lookup_type(self, event_type: str) -> list[EventRecord]:
        return list(self._by_type.get(event_type, []))

    def lookup_types_or_stub_kinds(self, types: frozenset[str]) -> list[EventRecord]:
        """Records logically typed as one of ``types`` — flat OR NodeTouched(kind=<type>) stub-rewrap.

        Equivalent to unwrapping every committed record via the stub-rewrap convention
        (towow.l2.orchestrator._unwrap_stub_rewrap) and keeping the ones whose unwrapped type is in
        ``types``. The eager index already holds every record parsed (built at construction), so
        this is a plain filter over already-materialized records — LazyEventIndex overrides this
        with a byte-level pre-filter that skips the pydantic parse entirely for non-matching
        NodeTouched records (this base implementation has nothing to skip: the parse already
        happened at __init__).
        """
        out: list[EventRecord] = []
        for t in types:
            out.extend(self._by_type.get(t, []))
        for rec in self._by_type.get(_STUB_REWRAP_TYPE, []):
            if isinstance(rec.payload, dict) and rec.payload.get("kind") in types:
                out.append(rec)
        return out

    def lookup_category(self, category: str) -> list[EventRecord]:
        return list(self._by_category.get(category, []))

    def lookup_actor(self, actor_id: str) -> list[EventRecord]:
        return list(self._by_actor.get(actor_id, []))

    def lookup_task(self, task_id: str) -> list[EventRecord]:
        return list(self._by_task.get(task_id, []))

    def lookup_run(self, run_id: str) -> list[EventRecord]:
        return list(self._by_run.get(run_id, []))

    def lookup_correlation(self, correlation_id: str) -> list[EventRecord]:
        return list(self._by_correlation.get(correlation_id, []))

    def lookup_sequence_range(self, start_seq: int, end_seq: int) -> list[EventRecord]:
        """[start_seq, end_seq] inclusive, in committed order (§4.1.2 range query via _by_sequence).

        Iterates the in-memory sequence bucket (no disk read, no pydantic re-parse) — even the
        O(n) walk over dict items is the indexed fast path vs the old per-call full log scan, and
        _by_sequence preserves committed/sequence insertion order so the result order is unchanged.
        """
        return [rec for seq, rec in self._by_sequence.items() if start_seq <= seq <= end_seq]

    def records_in_range(self, start_seq: int, end_seq: int) -> list[EventRecord]:
        """[start_seq, end_seq] inclusive, committed order, dup-seq PRESERVING (T-RMD-SEQ-GUARD).

        Unlike ``lookup_sequence_range`` (above), which walks the last-wins-per-seq
        ``_by_sequence`` bucket and so silently drops historical dup-seq records (553 on the real
        ledger — see ``EventLog.get_events_in_range``'s docstring), this walks the full committed
        record list in commit order and keeps every record whose OWN sequence_number falls in
        range, regardless of how many other records share that seq. The base ``EventIndex`` already
        holds every record parsed in memory (``self._records``), so this is a cheap in-memory
        filter, not a reload — ``LazyEventIndex`` overrides it with a byte-level sequence_number
        pre-filter that skips the pydantic parse for out-of-range records (see its docstring); this
        base implementation has nothing to skip, the parse already happened at construction.
        """
        return [rec for rec in self._records if start_seq <= rec.sequence_number <= end_seq]

    # ─── serializable views (for EventLogIndexStore persistence) ────────────────

    def serializable_key_indexes(self) -> dict[str, dict[str, list[str]]]:
        """Return the 15 indexes (minus event_id-payload) as ``name → {key → [event_id]}``.

        These are the persisted *key→postings* files (§7.2 "简单的 key-value 文件"). The full
        records live in the separate event_id.idx file; every other index stores only event_ids
        (Patch3 §3.2 ``type.idx`` = ``event_type → [event_id]`` etc.). Composite keys
        (entity = (type,id); input_hash = (resolver,hash)) are joined with a NUL separator,
        which can never appear in a JSON string key.
        """
        sep = "\x00"

        def ids(buckets: dict[str, list[EventRecord]]) -> dict[str, list[str]]:
            return {k: [r.event_id for r in v] for k, v in buckets.items()}

        return {
            "sequence": {str(k): [v.event_id] for k, v in self._by_sequence.items()},
            "transaction": ids(self._by_transaction),
            "batch": ids(self._by_batch),
            "type": ids(self._by_type),
            "category": ids(self._by_category),
            "actor": ids(self._by_actor),
            "task": ids(self._by_task),
            "run": ids(self._by_run),
            "correlation": ids(self._by_correlation),
            "causation": ids(self._by_causation),
            "supersede": ids(self._by_supersede),
            "obligation": ids(self._by_obligation),
            "entity": {f"{et}{sep}{eid}": [r.event_id for r in v] for (et, eid), v in self._by_entity.items()},
            "input_hash": {
                f"{rid}{sep}{ih}": [rec.event_id] for (rid, ih), rec in self._by_input_hash.items()
            },
        }


class LazyEventIndex(EventIndex):
    """Cold-start read-path index that materializes records on demand (eventlog-cold-start-read-path@v1).

    Why this exists (A1, 2026-07-02 OOM 崩机根治 收尾): the eager ``EventIndex(records)`` build had
    to ``EventRecord.model_validate_json`` every one of the ~236k committed records at construction
    (实测 ~29s / 349MB on the real ledger) — and even a raw ``json.loads`` single scan of that same
    349MB is ~18s, so no full-parse strategy can meet the contract's ≤3s absolute wall-clock budget.
    This index instead loads only what's cheap at construction and defers the per-record pydantic
    parse to the queries that actually touch a record:

      - the 14 key→postings ``.idx`` files (``name → {key → [event_id]}``, ~53MB total, ~0.3s) — the
        already-persisted realization of the 15 indexes, so lookups resolve keys → event_ids with NO
        record parse;
      - a ``event_id → byte-offset`` map over ``event_id.idx`` (mmap + regex scan, ~0.2s) — so a
        lookup that needs the *record* seeks to its one line and ``model_validate_json``s just that
        line (O(1) per accessed record; construction-period pydantic validation count = 0, satisfying
        the contract's machine-independent asymptotic clause).

    The public read API is byte-for-byte the eager ``EventIndex``'s (same lookup results / same
    ``serializable_key_indexes`` postings) — this is a subclass so ``EventIndex | None`` typing and
    ``isinstance`` callers are unaffected — only *when* the record objects get built differs.

    Freshness (freshness_adopt_trust_rule): trusting the persisted postings is a NEW trust the eager
    load never took (it re-derived postings from records), so ``load()`` proves cross-file
    consistency before handing back a LazyEventIndex, and the caller's four adopt gates
    (``_adopt_persisted_index_if_safe``) still prove disk-tail freshness on top.

    Catch-up folds (adopt-then-catch-up + write-path warm keep): ``add`` / ``add_records`` fold the
    active-segment tail's REAL records on top of the lazy base, reusing the eager ``EventIndex``'s own
    placement logic (a throwaway ``EventIndex(folded).serializable_key_indexes()`` merged in) so the
    folded postings can never drift from what a full rebuild would produce.
    """

    def __init__(
        self,
        eid_path: Path,
        offsets: dict[str, int],
        postings: dict[str, dict[str, list[str]]],
        order: list[str],
        seq_set: set[int],
        max_seq: int,
        seqs: list[int],
    ) -> None:
        # Deliberately does NOT call super().__init__ (that eager-builds over a record list — the very
        # cost this class avoids). Every method the eager buckets back is overridden below.
        self._eid_path = eid_path
        self._offsets = offsets  # event_id → byte offset of its line in event_id.idx
        self._lazy_postings = postings  # name → {key → [event_id]} (the 14 persisted key indexes)
        self._order = order  # event_ids in committed (sequence) order: base scan order, then folds
        # sequence_number of _order[i], parallel array, same length/order as `order`
        # (f-perf2-catchup-full-materialize-for-narrow-seq-range): populated by the same parse-free
        # regex scan that builds `order` (see EventLogIndexStore.load), so records_in_range can
        # pre-filter by seq with zero pydantic parse cost before materializing only the matching
        # subset.
        self._seqs = seqs
        self._seq_set = seq_set  # committed sequence_numbers (base + folds)
        self._max_seq = max_seq
        self._cache: dict[str, EventRecord] = {}  # materialized records (folded eagerly + accessed lazily)
        self._mm: mmap.mmap | None = None
        self._fh: object | None = None

    # ── on-demand single-record materialization ──────────────────────────────
    def _mmap(self) -> mmap.mmap:
        if self._mm is None:
            fh = self._eid_path.open("rb")
            self._fh = fh
            self._mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        return self._mm

    def _materialize(self, event_id: str) -> EventRecord | None:
        """Return the record for ``event_id`` (cached / folded, else seek+parse its one line).

        None when the id isn't in the base offset map and wasn't folded — the correct, safe answer
        for the adopt-time ghost spot-check: a sequence posting that points at an event_id absent
        from event_id.idx is a torn/corrupt .idx set the caller must not adopt.
        """
        rec = self._cache.get(event_id)
        if rec is not None:
            return rec
        off = self._offsets.get(event_id)
        if off is None:
            return None
        mm = self._mmap()
        nl = mm.find(b"\n", off)
        end = mm.size() if nl == -1 else nl
        rec = EventRecord.model_validate_json(mm[off:end].decode("utf-8"))
        self._cache[event_id] = rec
        return rec

    def _materialize_all(self, event_ids: list[str]) -> list[EventRecord]:
        return [r for r in (self._materialize(e) for e in event_ids) if r is not None]

    def close(self) -> None:
        if self._mm is not None:
            with contextlib.suppress(Exception):
                self._mm.close()
            self._mm = None
        if self._fh is not None:
            with contextlib.suppress(Exception):
                self._fh.close()  # type: ignore[attr-defined]
            self._fh = None

    def __del__(self) -> None:
        self.close()

    # ── lifecycle-state derivation (EventLog reads these instead of a 2nd full-log scan) ──
    def event_id_set(self) -> set[str]:
        return set(self._order)

    def sequence_set(self) -> set[int]:
        return set(self._seq_set)

    # ── scalar / bulk views ──────────────────────────────────────────────────
    @property
    def max_sequence(self) -> int:
        return self._max_seq

    @property
    def total_records(self) -> int:
        return len(self._order)

    def records(self) -> list[EventRecord]:
        """Materialize the whole committed stream (in sequence order).

        This is the one query that pays the full parse — an inherent cost of "give me every record",
        not a construction cost (the contract budgets constructor→read-ready, and scope-outs read
        content / long-lived daemon patrol to their own concepts). Consumers that only point-lookup
        never trigger it.
        """
        return self._materialize_all(self._order)

    def records_in_range(self, start_seq: int, end_seq: int) -> list[EventRecord]:
        """[start_seq, end_seq] inclusive, committed order, dup-seq PRESERVING (T-RMD-SEQ-GUARD).

        f-perf2-catchup-full-materialize-for-narrow-seq-range: EventLog.get_events_in_range (the
        ProjectionStore.catchup / recompute_one read path — called by nearly every read-only CLI
        command via `store.catchup(event_log)`) used to call ``records()`` — the ONE query that
        pays the full pydantic parse of the WHOLE committed stream — just to filter it down to
        [start_seq, end_seq] a moment later. On the real ledger (625k+ committed records) that is
        ~28s / +5GB RSS EVERY TIME a fresh CLI process opens the ledger, even when the requested
        range is empty (projections already at HEAD, the common steady-state case) or a handful of
        records — confirmed by direct measurement (ledger-perf-diagnosis, 2026-07-18 P0): `status`
        / `vitality` / `concept slice` all show the identical ~28s/~5.5GB signature despite doing
        unrelated work downstream, because they all pay this same shared bootstrap cost.

        Fix: pre-filter by sequence_number BEFORE materializing. ``self._seqs`` is a parallel array
        to ``self._order`` populated by the same parse-free regex scan that resolves event_id byte
        offsets (see EventLogIndexStore.load) — so this loop is plain int comparisons over an
        already-in-memory list, zero pydantic parse cost for records outside the range. Only the
        matching subset is ever materialized. Deliberately does NOT use ``lookup_sequence_range``'s
        ``_lazy_postings["sequence"]`` bucket (last-wins per seq — T-RMD-SEQ-GUARD: 553 historical
        dup-seq records on the real ledger would silently collapse to one each); this walks the
        full per-record seq array instead, so every record that shares a sequence_number with
        another is still kept — byte-for-byte the same result ``records()`` + a manual filter
        would give, just without paying to materialize the records the filter immediately discards.
        """
        ids = [eid for eid, seq in zip(self._order, self._seqs, strict=True) if start_seq <= seq <= end_seq]
        return self._materialize_all(ids)

    # ── key → postings lookups (resolve event_ids with no parse, then materialize) ──
    def lookup_event_id(self, event_id: str) -> EventRecord | None:
        return self._materialize(event_id)

    def _bucket(self, name: str, key: str) -> list[EventRecord]:
        return self._materialize_all(self._lazy_postings.get(name, {}).get(key, []))

    def lookup_entity(self, entity_type: str, entity_id: str) -> list[EventRecord]:
        return self._bucket("entity", f"{entity_type}\x00{entity_id}")

    def lookup_obligation(self, obligation_id: str) -> list[EventRecord]:
        return self._bucket("obligation", obligation_id)

    def lookup_supersede(self, superseded_event_id: str) -> list[EventRecord]:
        return self._bucket("supersede", superseded_event_id)

    def lookup_causation(self, causation_event_id: str) -> list[EventRecord]:
        return self._bucket("causation", causation_event_id)

    def lookup_input_hash(self, resolver_id: str, input_hash: str) -> EventRecord | None:
        eids = self._lazy_postings.get("input_hash", {}).get(f"{resolver_id}\x00{input_hash}", [])
        return self._materialize(eids[0]) if eids else None

    def lookup_type(self, event_type: str) -> list[EventRecord]:
        return self._bucket("type", event_type)

    def lookup_types_or_stub_kinds(self, types: frozenset[str]) -> list[EventRecord]:
        """Records logically typed as one of ``types`` — flat OR NodeTouched(kind=<type>) stub-rewrap.

        f-perf2-vitality-full-materialize-for-narrow-type-scan: session_vitality.build_work_product_map
        used to call EventLog.committed_index().records() — the whole committed stream — just to keep
        ~600 TaskRunCompleted/PatchProposed/.../CommitAccepted-kind records out of 630k total on the
        real ledger. 91% of records there are NodeTouched stub-rewraps, so records() paid a full
        EventRecord.model_validate_json() pydantic parse for every single one (measured: ~28s / +4.4GB
        RSS) only to discard nearly all of them a moment later — confirmed via cProfile: 21.8s of that
        is pydantic_core validate_json, not the mmap/byte-scan machinery this class exists to avoid.

        Fix: for the dominant NodeTouched bucket, do a cheap raw-byte pre-filter (regex-free substring
        check for `"kind":"<T>"` — the record's compact model_dump_json field values are never escaped
        for these plain ASCII type names) over each candidate's on-disk line BEFORE paying the pydantic
        parse. A miss is skipped with zero parse cost. Every byte-level hit (and every already-cached
        record, hit or not) still goes through the real _materialize() + a real ``payload.kind`` check
        below — the byte scan is only ever a cheap pre-filter that trims candidates, never the source of
        truth for inclusion, so the result is byte-for-byte identical to materializing every record and
        unwrapping it (same contract as the base EventIndex.lookup_types_or_stub_kinds).
        """
        direct_ids = [
            eid
            for t in types
            for eid in self._lazy_postings.get("type", {}).get(t, [])
        ]
        out = self._materialize_all(direct_ids)
        node_touched_ids = self._lazy_postings.get("type", {}).get(_STUB_REWRAP_TYPE, [])
        if not node_touched_ids:
            return out
        kind_markers = [f'"kind":"{k}"'.encode() for k in types]
        mm = self._mmap()
        for eid in node_touched_ids:
            if eid not in self._cache:
                off = self._offsets.get(eid)
                if off is None:
                    continue  # unresolvable id — _materialize would also return None for it
                nl = mm.find(b"\n", off)
                end = mm.size() if nl == -1 else nl
                line = mm[off:end]
                if not any(marker in line for marker in kind_markers):
                    continue  # cheap byte pre-filter miss: definitely not one of `types` — skip the parse
            rec = self._materialize(eid)
            if rec is not None and isinstance(rec.payload, dict) and rec.payload.get("kind") in types:
                out.append(rec)
        return out

    def lookup_category(self, category: str) -> list[EventRecord]:
        return self._bucket("category", category)

    def lookup_actor(self, actor_id: str) -> list[EventRecord]:
        return self._bucket("actor", actor_id)

    def lookup_task(self, task_id: str) -> list[EventRecord]:
        return self._bucket("task", task_id)

    def lookup_run(self, run_id: str) -> list[EventRecord]:
        return self._bucket("run", run_id)

    def lookup_correlation(self, correlation_id: str) -> list[EventRecord]:
        return self._bucket("correlation", correlation_id)

    def lookup_sequence_range(self, start_seq: int, end_seq: int) -> list[EventRecord]:
        seq_postings = self._lazy_postings.get("sequence", {})
        eids: list[str] = []
        for seq_key, bucket in seq_postings.items():  # insertion order == committed/sequence order
            if start_seq <= int(seq_key) <= end_seq:
                eids.extend(bucket)
        return self._materialize_all(eids)

    def serializable_key_indexes(self) -> dict[str, dict[str, list[str]]]:
        return self._lazy_postings

    # ── catch-up folds (reuse eager placement → zero drift vs a full rebuild) ──
    def add(self, rec: EventRecord) -> None:
        self.add_records([rec])

    def add_records(self, records: list[EventRecord]) -> None:
        """Fold newly-committed records (in sequence order, all seq > max_sequence) onto the base.

        Placement is delegated to a throwaway eager ``EventIndex`` over just these folded records:
        its ``serializable_key_indexes()`` is the SINGLE source of bucket-key truth (shared with the
        full rebuild + the persisted files), so folded postings extend the base ones in exactly the
        order a from-scratch rebuild over base+folded would emit — except the single-value last-wins
        buckets (``_SINGLE_VALUE_KEY_INDEXES``), which are overwritten instead of extended so a folded
        record reusing an existing key replaces it, matching ``EventIndex._index_one``'s dict
        assignment for those buckets.
        """
        if not records:
            return
        folded = EventIndex(records).serializable_key_indexes()
        for name, buckets in folded.items():
            target = self._lazy_postings.setdefault(name, {})
            if name in self._SINGLE_VALUE_KEY_INDEXES:
                target.update(buckets)
            else:
                for key, eids in buckets.items():
                    target.setdefault(key, []).extend(eids)
        for rec in records:
            self._cache[rec.event_id] = rec
            self._order.append(rec.event_id)
            self._seqs.append(rec.sequence_number)
            self._seq_set.add(rec.sequence_number)
            if rec.sequence_number > self._max_seq:
                self._max_seq = rec.sequence_number


class EventLogIndexStore:
    """Persist / load / rebuild the EventIndex as .idx files under ``events/index/`` (§7.1).

    The persisted index is a derived cache, never the source of truth (§7.2 / Patch3 §3.2):
      - ``event_id.idx``   JSONL, one full EventRecord per line (lets load() reconstruct records)
      - ``<name>.idx``     JSON ``{key → [event_id]}`` for each of the other 14 indexes

    ``load()`` returns None when nothing is persisted (caller should rebuild). ``rebuild_from_records``
    re-derives an EventIndex from the given records and re-persists, so a deleted/corrupt .idx set
    can always be regenerated from the event log files.
    """

    # The 14 key→postings index files (event_id is stored as full records, separately).
    _KEY_INDEX_NAMES = (
        "sequence",
        "transaction",
        "batch",
        "type",
        "category",
        "actor",
        "task",
        "run",
        "correlation",
        "causation",
        "supersede",
        "obligation",
        "entity",
        "input_hash",
    )

    def __init__(self, towow_dir: Path) -> None:
        self._dir = towow_dir / "events" / "index"

    @property
    def index_dir(self) -> Path:
        return self._dir

    def persist(self, records: list[EventRecord]) -> EventIndex:
        """Build an EventIndex over ``records`` and atomically write all .idx files. Returns it."""
        index = EventIndex(records)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Generation anchor (f-a1-coldstart-torn-persist-optional-posting-freshness-hole): the token
        # is the content hash of THIS record set's event_id list — exactly the list load() reconstructs
        # by scanning event_id.idx. Every posting file is stamped with it, so load() can prove each
        # posting was built for precisely the event_id.idx it reads (regardless of which persist wrote
        # which file, or in what order — this survives concurrent cold-start persists, unlike a
        # write-ordering argument). Deterministic in the records ⇒ a re-persist over the same records
        # is byte-identical (the derived cache stays rebuildable — test_a3_delete_idx_then_rebuild).
        generation = self._generation_token([r.event_id for r in records])
        # event_id.idx — full records, one JSON per line (the reconstruction source). NOT stamped:
        # its content IS the generation, and load() derives the token from it directly (keeping the
        # 400MB file's byte format untouched so the mmap offset scan / materialize / prune are unchanged).
        self._atomic_write(
            self._dir / "event_id.idx",
            "\n".join(r.model_dump_json() for r in records),
        )
        for name, buckets in index.serializable_key_indexes().items():
            self._atomic_write(
                self._dir / f"{name}.idx",
                json.dumps({_GENERATION_KEY: generation, _BUCKETS_KEY: buckets}, ensure_ascii=False),
            )
        return index

    def load(self) -> EventIndex | None:
        """Reconstruct a read-ready index from the persisted .idx set; None if not persisted/adoptable.

        A1 (eventlog-cold-start-read-path@v1): returns a ``LazyEventIndex`` that defers per-record
        pydantic construction — the old ``EventRecord.model_validate_json`` over every event_id.idx
        line was ~29s on the real ledger (and even a raw json.loads single scan of the same 349MB is
        ~18s), so no full-parse load can meet the ≤3s cold-start budget. Instead:
          1. scan event_id.idx for ``event_id → byte-offset`` (mmap + regex, no full parse);
          2. read the 14 persisted key→postings files verbatim (they ARE the on-disk realization of
             the indexes — no need to re-derive them from records, which is what forced the full parse);
          3. prove cross-file consistency before trusting the postings (see below).

        The 14 postings files were "intentionally not consulted" by the old eager load because it
        re-derived them from the records it parsed. Loading them directly is what makes the cold path
        parse-free — but it means trusting files the eager path never trusted, so we MUST verify they
        agree with event_id.idx (freshness_adopt_trust_rule extended to the postings): the 15 files
        are each written atomically (tmp→replace) but NOT as a group, so a crash mid-persist — or a
        concurrent cold-start persist landing in the write window — can leave event_id.idx and some
        postings from DIFFERENT generations.

        Generation anchor (f-a1-coldstart-torn-persist-optional-posting-freshness-hole). ``persist``
        stamps every posting file with the GENERATION TOKEN of the event_id.idx it was built for — a
        cheap content hash of that record set's event_id list (``_generation_token``). ``load`` recom-
        putes the token from the id list it scans out of event_id.idx and adopts a posting ONLY when
        the stamp matches. Because the token is derived from event_id.idx's own content (not from a
        write-order argument), it proves each posting belongs to precisely the event_id.idx being read
        — for EVERY bucket, including the conditionally-populated ones (task/run/…/input_hash) that a
        systematically null-field ledger tail (DaemonRunCompleted/CommitAccepted) leaves untouched.
        (A prior guard verified only that event_id.idx's tail record appeared in the buckets it
        belongs to; a null-field tail touches none of the conditional buckets, so a stale task.idx a
        generation behind was adopted uncensored — the hole this closes.) An unstamped legacy .idx
        (no manifest of generations existed before) fails the match ⇒ one rebuild re-stamps the set.

        The tail-consistency check is kept below as defense-in-depth (it catches an internally mal-
        formed but correctly-stamped posting); it is dup-seq-tolerant — a naive record-count equality
        (event_id.idx lines vs sequence.idx keys) would FALSELY reject the real ledger, whose histor-
        ical dup-seqs (the 06-10 rollback) collapse sequence.idx below the event_id.idx line count.
        Any mismatch ⇒ None (the caller falls back to a from-event-files rebuild — never less
        correct); the caller's four adopt gates then prove disk-tail freshness.
        """
        eid_path = self._dir / "event_id.idx"
        if not eid_path.exists():
            return None
        # 1. event_id → byte offset (parse-free single scan); capture the tail line + the ordered id
        #    list (the id list IS this event_id.idx's generation identity — hashed at step 2).
        offsets: dict[str, int] = {}
        order: list[str] = []
        # sequence_number of order[i] (f-perf2-catchup-full-materialize-for-narrow-seq-range):
        # captured by the SAME regex match as event_id (both fields sit at the front of every
        # compact model_dump_json line — see _EVENT_ID_SEQ_RE), so this costs nothing extra over
        # the event_id-only scan it replaces. Feeds LazyEventIndex.records_in_range's pre-filter.
        seqs: list[int] = []
        tail_line: bytes | None = None
        size = eid_path.stat().st_size
        if size > 0:  # size==0 ⇒ persisted-but-empty ledger (mmap can't map a 0-byte file); order stays []
            with eid_path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                pos = 0
                while pos < size:
                    nl = mm.find(b"\n", pos)
                    end = size if nl == -1 else nl
                    if end > pos:  # non-empty line
                        match = _EVENT_ID_SEQ_RE.search(mm, pos, end)
                        if match is None:
                            return None  # a line with no parseable event_id/seq ⇒ torn/corrupt ⇒ don't adopt
                        event_id = match.group(1).decode("ascii")
                        offsets[event_id] = pos
                        order.append(event_id)
                        seqs.append(int(match.group(2)))
                        tail_line = mm[pos:end]
                    if nl == -1:
                        break
                    pos = nl + 1
        # 2. generation anchor (PRIMARY torn-persist guard): the token of the event_id.idx we just
        #    scanned. Every posting was stamped at persist with the token of the event_id.idx it was
        #    built for, so a posting whose stamp != this token belongs to a DIFFERENT generation than
        #    the records event_id.idx now holds — regardless of which persist wrote which file or in
        #    what order (survives concurrent cold-start persists; no write-ordering assumption).
        generation = self._generation_token(order)
        # 3. read + generation-verify the 14 key→postings files. Reject on: a missing file (incomplete
        #    set), a corrupt/unstamped (legacy) file, or one stamped for a DIFFERENT generation. This
        #    covers EVERY posting — including the conditionally-populated buckets (task/run/correlation/
        #    causation/supersede/obligation/input_hash/transaction/batch) that a systematically null-
        #    field tail record (DaemonRunCompleted/CommitAccepted) never touches and the tail-
        #    consistency check below alone cannot see (f-a1-coldstart-torn-persist-optional-posting-…).
        postings: dict[str, dict[str, list[str]]] = {}
        for name in self._KEY_INDEX_NAMES:
            path = self._dir / f"{name}.idx"
            if not path.exists():
                return None
            try:
                wrapped = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None  # corrupt posting file ⇒ don't adopt
            if not isinstance(wrapped, dict) or wrapped.get(_GENERATION_KEY) != generation:
                return None  # unstamped legacy file OR a posting from another generation ⇒ torn ⇒ None
            buckets = wrapped.get(_BUCKETS_KEY)
            if not isinstance(buckets, dict):
                return None
            postings[name] = buckets
        # 4. empty event_id.idx: the generation-verified postings are already proven consistent (an
        #    empty id list hashes to a fixed token; a non-empty posting would carry a different token
        #    and have been rejected at step 3), so no further check is needed.
        if not order:
            return LazyEventIndex(eid_path, offsets, postings, order, set(), -1, seqs)
        # 5. tail-consistency proof (defense-in-depth, per the finding's closure contract — the
        #    generation anchor at step 3 is the primary guard): every posting the event_id.idx tail
        #    record belongs to must already list it, catching an internally malformed but correctly-
        #    stamped posting. Placement is the eager EventIndex's — the single bucket-key truth. Dup-
        #    seq-tolerant: keyed by seq, the 06-10 rollback's dup-seqs don't trip a naive count check.
        assert tail_line is not None  # order non-empty ⇒ we captured the last line
        tail_rec = EventRecord.model_validate_json(tail_line.decode("utf-8"))  # O(1): one record
        for name, buckets in EventIndex([tail_rec]).serializable_key_indexes().items():
            for key in buckets:
                if tail_rec.event_id not in postings.get(name, {}).get(key, []):
                    return None  # a posting is missing the newest record ⇒ torn generation ⇒ don't adopt
        seq_set = {int(seq_key) for seq_key in postings["sequence"]}
        max_seq = max(seq_set) if seq_set else -1
        return LazyEventIndex(eid_path, offsets, postings, order, seq_set, max_seq, seqs)

    def rebuild_from_records(self, records: list[EventRecord]) -> EventIndex:
        """Rebuild + re-persist the index from authoritative event-log records (§7.2 rebuildable)."""
        return self.persist(records)

    def prune_event_ids(self, removed_event_ids: set[str]) -> int:
        """M-0.7 v3.6+ candidate 丁-1 — drop the full records of ``removed_event_ids`` from
        ``event_id.idx`` (+ re-derive every key→postings .idx from what remains). Returns #removed.

        Why this exists (DESIGN-M07-v3.6 §3.3/§4.2): ``event_id.idx`` stores one FULL EventRecord per
        line as the index-reconstruction source — so even after candidate 乙 moves a segment's events
        to cold, their full records linger in ``event_id.idx`` (43MB on the live log). They are now
        verbatim in ``cold/archive-NNNNNN.jsonl.gz`` and re-loadable via ``cold_lookup``/the cold
        index, so the hot ``event_id.idx`` no longer needs to carry them. Pruning them is SAFE because
        ``event_id.idx`` is a DERIVED CACHE (§7.2 "索引不是事实源, event 文件才是"): a cold start that
        finds an event missing from the loaded index re-derives it (the EventLog rebuilds the index
        from the event files; a cold event is served via the cold-lookup delegation in get_event).

        Reads the current ``event_id.idx`` (if present), keeps only records whose event_id is NOT in
        ``removed_event_ids``, and re-persists the full index set over the survivors (so the 14 key→
        postings files stay consistent with the pruned record set). A missing ``event_id.idx`` is a
        no-op (nothing persisted yet). Atomic per-file writes (tmp→replace) like persist().
        """
        eid_path = self._dir / "event_id.idx"
        if not eid_path.exists():
            return 0
        survivors: list[EventRecord] = []
        removed = 0
        for line in eid_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = EventRecord.model_validate_json(line)
            if rec.event_id in removed_event_ids:
                removed += 1
            else:
                survivors.append(rec)
        if removed == 0:
            return 0
        # re-persist the full index set (event_id.idx + the 14 key→postings) over the survivors, so
        # every .idx stays internally consistent with the pruned record set.
        self.persist(survivors)
        return removed

    @staticmethod
    def _generation_token(event_ids: list[str]) -> str:
        """Content hash of a persisted set's ordered event_id list — its GENERATION identity.

        The event_id list uniquely identifies the record set (event_id is unique; a correction mints a
        new id via supersede rather than mutating one), and ``load`` reconstructs the SAME list by
        scanning event_id.idx — so a token computed here at persist and there at load agree iff the
        postings were built for exactly the event_id.idx being read. Cheap by design: hashes the id
        list (~9MB on the real ledger), NOT the ~400MB record payload, so it adds no measurable cost
        to the ≤3s cold-start budget. Deterministic in ``event_ids`` ⇒ a re-persist over the same
        records stamps the identical token ⇒ the .idx set stays byte-for-byte rebuildable (the derived
        cache never diverges from a from-records rebuild).
        """
        h = hashlib.sha256()
        for event_id in event_ids:
            h.update(event_id.encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write ``content`` to ``path`` atomically via a per-writer-unique tmp file.

        2026-07-02 OOM 事故根治 (A5): 旧版 tmp 文件名是确定性的 (``<name>.idx.tmp``) — 多个
        进程并发冷启动都触发全量 persist 时会共享同一个 tmp 路径, 先 replace 的那个把 tmp
        文件"拿走", 后 replace 的那个找不到 tmp → ENOENT (被 _build_and_persist_index 吞成
        warning, 缓存永远写不成, 下次冷启动又全量重建 — 恶性循环, 也是当晚 OOM 的乘数之一)。
        用 pid+uuid 拼进文件名, 每个写者天然互不相撞; tmp.replace(path) 仍是同目录同文件系统
        的原子 rename, 原子性不受影响。写失败时清理掉自己的 tmp, 不留垃圾文件。
        """
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
