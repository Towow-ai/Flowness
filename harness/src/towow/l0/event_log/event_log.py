"""M-0.1 EventLog — append-only JSONL log + 9 Read APIs + 2 write paths.

# spec source: 03-l0-truth-source/M-0.1-event-log-detailed-design.md
#   §3.11 event_type registry (67+ types)
#   §4.1 Read API (9 query dimensions)
#   §5.1 Two write paths (path A via commit gate / path B direct)
#   §5.2 Sequence number monotonicity + fsync durability
#   §5.3 Write-time validation (schema / event_id uniqueness / sequence_number)
#   附录 B Patch 1 §1.4 TransactionBatch sentinel + recovery
#   附录 B Patch 2 §2.1 committed-visible stream
#   附录 B Patch 2 §2.2 batch recovery (truncate to last sentinel)
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import threading
import uuid
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from towow.l0 import fs_guard
from towow.l0.event_log.index import EventIndex, EventLogIndexStore
from towow.l0.event_log.segments import (
    active_segment_path,
    iter_raw_event_lines,
    next_segment_path,
    ordered_segment_paths,
)
from towow.schemas.enums import (
    BaseClassification,
    BatchStatus,
    DebtSeverity,
    DebtType,
    EventCategory,
    EventType,
)
from towow.schemas.event_intent import EventIntent
from towow.schemas.event_record import EventRecord, Provenance
from towow.schemas.payloads.registry import conforms, validate_event_payload
from towow.schemas.transaction_batch import TransactionBatch
from towow.schemas.version import is_known_schema_version, unknown_version_reader_action

# §7.2 / §11.2 — segment 切分参数 (运维值, 非设计值). 默认 ≈ 10,000 events 或 10MB,
# 以先到为准. 默认下首段 = legacy events.log (单文件), rotation 只在超阈值时发生 —
# 未 rotate 的库与切段前行为 byte-identical (向后兼容).
_DEFAULT_SEGMENT_MAX_EVENTS = 10_000
_DEFAULT_SEGMENT_MAX_BYTES = 10 * 1024 * 1024

# T-LRF-01 finding-routability-birth-gate@v1 / birth-time-validation@v1 — event_types that own a
# dedicated path-A birth gate and therefore must NEVER be smuggled through a stub-rewrap NodeTouched
# (payload.kind=<Type>, stub_original_payload={...}). Each such type is born through a commit-gate
# birth gate; wrapping it as a path-B NodeTouched would (1) skip the birth gate (write_direct runs no
# commit gate) yet still get unwrapped + routed/consumed as an effective <Type>, and (2) create an
# L2/L0 unwrap asymmetry. The set grows as more birth-gated object types are added (concept
# birth-time-validation@v1 "覆盖面随实例增长"). NodeTouched-universal-smuggler class: registry.py
# PAYLOAD_VALIDATION_EXEMPT(NODE_TOUCHED) / debt-a6897075d58f.
#   - FindingCreated (f-lrf01): born via the commit gate's finding_routability_birth_gate; unwrapped +
#     routed by orchestrator._unwrap_stub_rewrap as an effective FindingCreated.
#   - InterviewBriefPublished (f-glob-brief-birthgate-nodetouched-smuggle-bypass): born via the
#     owner-confirm publish gate (T-RMD-S2-M11 provenance re-verify). A path-B stub with
#     stub_original_payload={owner_brief_confirmed=passed} is unwrapped by cli._find_latest_brief_payload
#     / _find_brief_payload_by_id and selected as THE brief for consensus_start — never running the
#     commit gate, so owner confirmation never actually happened. Empirically NO legitimate brief ever
#     lands as a NodeTouched stub companion (all 74 canonical briefs are direct InterviewBriefPublished),
#     so banning the smuggle form false-rejects nothing legitimate.
_BIRTHGATE_NONSMUGGLABLE_KINDS: frozenset[str] = frozenset(
    {
        EventType.FINDING_CREATED.value,
        EventType.INTERVIEW_BRIEF_PUBLISHED.value,
    },
)


class SchemaEvolutionError(RuntimeError):
    """T-L0-05 (M-0.1 §5.2): reader 遇 unknown-version immutable_truth 事件 → fail_replay (不静默吞真相)。"""


@dataclass(frozen=True)
class LedgerMergeResult:
    """merge_foreign_events 产出 (f-ledger-canonical-fork-distributed-merge-design)。

    分布式账本=git 式: event_id=身份(全局唯一稳定), seq=canonical 合并回流那一刻重排的顺序
    (本地 seq 只本地有效), 合并=append+按 event_id 去重+永不覆盖(append-only 史料不可变)。
    """

    merged_event_ids: tuple[str, ...] = ()         # 真被并入 canonical 的 (按合并顺序, 已重排 seq)
    skipped_duplicate_event_ids: tuple[str, ...] = ()  # event_id 已在 canonical → 去重跳过 (永不重号)
    base_sequence: int = -1                         # 第一条并入记录分到的全局 seq (空合并=-1)
    appended_count: int = 0                         # 实际尾部追加条数

    @property
    def is_noop(self) -> bool:
        return self.appended_count == 0


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import Any


# Sentinel event types that terminate batches (path A) — Patch 1 §1.4.
_SENTINEL_TYPES: frozenset[EventType] = frozenset(
    [EventType.COMMIT_ACCEPTED, EventType.COMMIT_REJECTED],
)

# AUDIT_TRIGGERED / AUDIT_VERDICT_RECEIVED are producer-only: emitted ONLY by the commit-gate-internal
# audit machinery via _write_audit_event (path-B single record, sanctioned provenance). A forged audit
# chain landing in canonical would let a fake done_elsewhere closure cross-reference it through
# closure-evidence-verification-gate (verdict→AuditTriggered). This producer-only ban closes the two
# CLI/envelope-reachable write surfaces mechanically:
#   (a) manual path-B write_direct — removed from _is_path_b_allowed (手 write_direct raises);
#   (b) any submitted path-A domain-intent batch — the append_transaction_batch guard below. A
#       submitted envelope's domain intents flow into append_transaction_batch, so without this guard an
#       agent could smuggle a forged AuditTriggered+AuditVerdictReceived pair (sanctioned-looking
#       provenance) into an accepted batch, or hand-build a TransactionBatch and call the method直接.
# The ONE legitimate producer path (_write_audit_event → _append_direct_record) never touches
# append_transaction_batch, so this guard cannot false-reject a real audit write.
#
# ⚠️ Threat-model boundary (诚实边界 — 不是完备): "both surfaces closed" 只对 CLI/envelope 面成立.
# There is a THIRD, unguarded write port — segments.append_raw_event_line (raw file append: no seq
# assignment, no payload validation, does NOT route through _reject_audit_event_in_batch /
# _is_path_b_allowed / _write_audit_event). A line it writes carries batch_id absent, so it is
# committed-visible IMMEDIATELY on read (iter_committed_records_from_lines path-B branch), and the
# hot-read / index-build path does NOT recompute record_hash — verify_record_hash runs only under the
# audit_integrity sweep. So an import/filesystem-level writer could append a forged sanctioned audit
# chain that this producer-only guard never sees. That import/raw-append面 is NOT mechanically
# prevented here; it is covered only by audit_integrity's post-hoc record_hash mismatch detection.
# 现实威胁面下不可利用: append_raw_event_line 的唯一调用者是 bg_worktree_poller, 写死固定形状的
# NodeTouched, 没有任何 CLI 把 agent 可控内容喂进它; 且一个能 import 该叶子模块的 agent 本就能绕过
# 一切门. 故此处 = CLI/envelope 面机械预防 + import 面 audit_integrity 事后侦测的分层, 不是全面全封.
# (口径与姊妹模块 owner_confirm.py 收敛: retire 门用 import-proof 的签名, done_elsewhere 写边界用
#  CLI/envelope 机械预防 + 侦测兜底 —— 分级理由见 owner_confirm.py 模块头.)
_AUDIT_PRODUCER_ONLY_TYPES: frozenset[EventType] = frozenset(
    [EventType.AUDIT_TRIGGERED, EventType.AUDIT_VERDICT_RECEIVED],
)


def iter_committed_records_from_lines(lines: Iterator[str]) -> Iterator[EventRecord]:
    """Apply the committed-visibility rule (Patch 2 §2.1) + schema-version policy (§5.2) to a raw
    line stream, yielding only visible records.

    Factored out of EventLog._iter_committed_records so the IDENTICAL filtering can be applied to a
    NON-hot line source — specifically the M-0.7 cold archives (cold_lookup.cold_records_in_range),
    so a cold-archived event reconstructed for a range query passes through the exact same
    committed/visibility + unknown-schema gating as a hot read. A cold archive is a sealed hot
    segment verbatim, and a path-A batch never straddles a segment boundary (rotation invariant) and
    is closed under compaction's _split_pinned_movable, so every archived batch is sentinel-complete
    within its own archive — buffering by batch_id over one archive's lines yields exactly the
    records that were committed-visible when the segment was hot.
    """
    path_a_buffer: dict[str, list[EventRecord]] = {}
    for line in lines:
        try:
            rec = EventRecord.model_validate_json(line)
        except Exception:  # noqa: S112  # truncated tail line; skip silently
            continue
        # T-L0-05 (M-0.1 §5.2): unknown schema_version 按 base_classification 分级 —
        # immutable_truth fail_replay(抛, 不静默吞真相); abstractable warn+skip; discardable skip。
        if not is_known_schema_version(rec.schema_version):
            action = unknown_version_reader_action(rec.base_classification)
            if action == "fail_replay":
                msg = (
                    f"unknown schema_version {rec.schema_version} on immutable_truth event "
                    f"{rec.event_id} — fail-closed (M-0.1 §5.2, 不静默吞真相)"
                )
                raise SchemaEvolutionError(msg)
            if action == "warn_and_skip":
                warnings.warn(
                    f"skipping unknown-version abstractable event {rec.event_id} "
                    f"(schema_version={rec.schema_version})",
                    stacklevel=2,
                )
            continue  # warn_and_skip + skip 都跳过该 event
        if rec.batch_id is None:
            yield rec  # path B: visible immediately
            continue
        path_a_buffer.setdefault(rec.batch_id, []).append(rec)  # path A: buffer until sentinel
        if rec.event_type in _SENTINEL_TYPES:
            yield from path_a_buffer.pop(rec.batch_id)


# f-fix-complete-gate-ignores-amended-closure-contract: batch_id extractor for the index-free type
# scan below. Byte-level (no pydantic parse) — it only decides WHICH raw lines the shared
# committed-visibility gate has to look at; the visibility decision itself stays in
# iter_committed_records_from_lines.
_BATCH_ID_RE = re.compile(r'"batch_id"\s*:\s*(?:"([^"]*)"|null)')


def lines_possibly_of_type(lines: Iterator[str], type_value: str) -> Iterator[str]:
    """Narrow a raw line stream to the lines a type scan for ``type_value`` needs.

    Yields (a) every line whose bytes mention ``"<type_value>"`` — a superset of that type's records,
    since the name can also appear inside another record's payload text (the caller filters by the
    parsed ``event_type``), and (b) ONLY the batch sentinels those lines need in order to become
    committed-visible. Order is preserved (domain lines then their sentinel), so feeding the result to
    ``iter_committed_records_from_lines`` reproduces exactly the visibility verdict a full scan gives
    for records of this type — at O(bytes) cost with a pydantic parse only on the handful of surviving
    lines (实测本账本 ~70k 条: 0.25s vs 6s 全量扫描).

    A candidate line that IS itself a sentinel (scanning for CommitAccepted/CommitRejected) flushes its
    own batch — otherwise it would be buffered and never emitted.
    """
    needle = f'"{type_value}"'
    sentinel_needles = tuple(f'"{t.value}"' for t in _SENTINEL_TYPES)
    pending: dict[str, list[str]] = {}
    for line in lines:
        is_candidate = needle in line
        maybe_sentinel = any(s in line for s in sentinel_needles)
        if not is_candidate and not (pending and maybe_sentinel):
            continue
        match = _BATCH_ID_RE.search(line)
        batch_id = match.group(1) if match else None
        if batch_id is None:  # path B — committed-visible on its own, no sentinel to wait for
            if is_candidate:
                yield line
            continue
        if is_candidate:
            pending.setdefault(batch_id, []).append(line)
        if maybe_sentinel and batch_id in pending:
            yield from pending.pop(batch_id)
            if not is_candidate:
                yield line


def _iter_committed_lifecycle_fields(lines: Iterator[str]) -> Iterator[tuple[int, str]]:
    """Cheap mirror of ``iter_committed_records_from_lines`` yielding only ``(sequence_number,
    event_id)`` pairs, for lifecycle-state derivation at startup (A1, 2026-07-02 OOM 崩机根治).

    2026-07-01 夜的勘查发现: ``EventLog.__init__`` 曾对同一段日志做 3 趟独立全量扫描 (求
    max_seq / 建 event_id 集 / 建 sequence 集)，每趟都走 ``EventRecord.model_validate_json``
    (全 pydantic 构造 nested Provenance/Subject/Supersede/payload 再丢弃) —— 冷启动 3 次为
    同一批数据重复付这笔昂贵的解析成本 (实测 21.9万条 ≈ 每趟 ~3s CPU)。三趟的产出恰好只是
    两个标量/集合字段, 不需要完整对象。

    结构上镜像 ``iter_committed_records_from_lines`` 的 committed-visibility 规则 (Patch 2
    §2.1 path-A 按 batch_id 缓冲到 sentinel 才可见 + §5.2 unknown-schema-version 分级)，但用
    ``json.loads`` 取代 ``EventRecord.model_validate_json`` —— 只解析、不做 pydantic 严格校验。

    已知偏差 (方向安全, 诚实标注): 一行语法合法 JSON 但会被 pydantic 拒绝的畸形行 (如某个
    嵌套字段类型不对)，这里仍可能读出它的 seq/event_id 计入返回集合，而全 pydantic 版本会把
    整条记录连同 seq/event_id 一起丢弃。效果只会让"这个 seq/event_id 已被占用"的判断变严
    (唯一性守卫多拒不漏放)，不会让本该被排除的畸形记录的 seq/id 被服务给合法读者 (它们的
    IO 只在这一处、只影响 __init__ 的三个哨兵值，取值本身不经这条路径对外暴露)。
    """
    sentinel_values = {EventType.COMMIT_ACCEPTED.value, EventType.COMMIT_REJECTED.value}
    path_a_buffer: dict[str, list[tuple[int, str]]] = {}
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # truncated tail / malformed line
        if not isinstance(obj, dict):
            continue
        seq = obj.get("sequence_number")
        event_id = obj.get("event_id")
        if not isinstance(seq, int) or not isinstance(event_id, str):
            continue  # 缺关键字段的行不参与生命周期状态派生 (与 pydantic 版一致地不可用)
        schema_version = obj.get("schema_version")
        if isinstance(schema_version, str) and not is_known_schema_version(schema_version):
            base_classification = obj.get("base_classification")
            if not isinstance(base_classification, str):
                continue  # 分级字段本身坏了, 无法判定 reader action, 保守跳过 (不参与派生)
            try:
                bc = BaseClassification(base_classification)
            except ValueError:
                continue  # 分级字段值不合法, 同上保守跳过
            action = unknown_version_reader_action(bc)
            if action == "fail_replay":
                msg = (
                    f"unknown schema_version {schema_version} on immutable_truth event "
                    f"{event_id} — fail-closed (M-0.1 §5.2, 不静默吞真相)"
                )
                raise SchemaEvolutionError(msg)
            continue  # warn_and_skip + skip 都跳过该 event (与 iter_committed_records_from_lines 对称)
        batch_id = obj.get("batch_id")
        if batch_id is None:
            yield (seq, event_id)  # path B: visible immediately
            continue
        if not isinstance(batch_id, str):
            continue
        path_a_buffer.setdefault(batch_id, []).append((seq, event_id))  # path A: 缓冲到 sentinel
        if obj.get("event_type") in sentinel_values:
            yield from path_a_buffer.pop(batch_id)


def _iter_segment_lines(segment: Path) -> Iterator[str]:
    """Yield stripped non-empty lines of ONE segment file (T-FND-02 active-segment catch-up).

    Unlike segments.iter_raw_event_lines (which spans every segment for a full scan), this reads a
    single file — the active segment — so the index catch-up parses only the bounded tail of the
    log, never the whole ledger. Missing file ⇒ empty (a freshly-rotated active segment that hasn't
    been written yet).
    """
    if not segment.exists():
        return
    with segment.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                yield stripped


class EventLog:
    """Append-only JSONL event log with sequence_number monotonicity + fsync durability.

    Concurrency (M-0.1 §5.2 "单写入点 + 互斥锁"): the spec's stated invariant is that no two
    writes ever share a sequence_number. The spec *assumed* a single OS process would suffice
    and prescribed a process-internal mutex. The actual v3 topology breaks that assumption —
    an always-on daemon and goal-spawn sessions are independent OS processes that all append
    to the same events.log (DOGFOOD F-019-12: two processes each derived the same next_sequence
    from their own stale in-memory counter and wrote duplicate sequence_numbers). We therefore
    keep the spec's invariant but realize the "单写入点 + 互斥锁" at the single physical write
    point with a *cross-process* file lock (fcntl.flock): every write derives its sequence_number
    from the on-disk tail under an exclusive lock, so no two writers (in any process) collide.
    The threading.Lock is retained as the cheap intra-process layer nested inside the file lock.

    Reads do not acquire the lock; they re-scan the file under the visibility rule
    (committed-visible stream, Patch 2 §2.1).

    Path A (via commit gate): append_transaction_batch([domain_records..., sentinel_record])
      atomically appends the batch with a single fsync at the trailing sentinel. Authoritative
      sequence_numbers are (re)assigned under the file lock at append time, so the provisional
      seqs from stamp_for_batch are non-binding.
    Path B (direct write): write_direct(event_intent) appends a single record (no batch).
    """

    def __init__(
        self,
        log_path: Path,
        *,
        segment_max_events: int = _DEFAULT_SEGMENT_MAX_EVENTS,
        segment_max_bytes: int = _DEFAULT_SEGMENT_MAX_BYTES,
    ) -> None:
        """Open the event log rooted at ``log_path`` (the legacy/base segment).

        ``segment_max_events`` / ``segment_max_bytes`` (§7.2/§11.2) bound the active segment;
        once a write would push the active segment past either, the *next* batch/record rotates
        into a fresh ``events/hot/events-NNNNNN.jsonl`` (decided once per write — a path-A batch
        never straddles a segment boundary). Defaults (10k events / 10MB) keep the historical
        single-file behavior for any repo small enough never to cross a threshold.
        """
        # T-FIX-INC-01 layer3: 测试进程物理隔离 — TOWOW_REAL_LEDGER_GUARD=1 (tests/conftest
        # 全局设置, 子进程继承) 时拒绝打开 system-tmp 之外的账本, 测试永远不可能拿真账本当
        # fixture (INCIDENT-TOWOW-WIPE-2026-06-10)。生产无该 env → 零行为。
        fs_guard.assert_event_log_open_allowed(log_path)
        self._log_path = log_path
        self._segment_max_events = segment_max_events
        self._segment_max_bytes = segment_max_bytes
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._log_path.exists():
            self._log_path.touch()
        # T-FIX-INC-01 layer2: 账本根哨兵自动落/刷新 (.towow/.ledger-root) — safe_rmtree
        # 深扫遇 canonical 匹配的哨兵即 fail-closed 拒删。哨兵写失败 (只读介质等) 不挡
        # open: 哨兵是保护性标记, 不是 canonical 内容。
        with contextlib.suppress(OSError):
            fs_guard.write_ledger_sentinel(self._log_path.parent)
        # Cross-process write lock (F-019-12): sidecar under the conventional .towow/locks/.
        # Scope = "this log file"; the lock file lives beside the log's locks dir and is a
        # runtime artifact (not tracked in git, same as commit.lock / scan.lock).
        # The lock *key* is normalized (.parent.resolve()) so two processes that name the same
        # physical log via different spellings — relative vs absolute, or through a symlink such
        # as ../.towow vs ./.towow — derive the *same* lock file and therefore actually exclude
        # each other. Without this, distinct path strings → distinct lock files → no mutual
        # exclusion → the F-019-12 sequence_number collision comes back. Only the lock key is
        # resolved; self._log_path is left as-given (it is used broadly and the OS resolves the
        # path on open anyway, so log read/write is unaffected — only lock keying needs it).
        # .parent already exists (mkdir'd just above), so resolve() is a safe non-strict call.
        self._lock_path = self._log_path.parent.resolve() / "locks" / f"{self._log_path.name}.lock"
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._lock_path.exists():
            self._lock_path.touch()
        self._mutex = threading.Lock()
        # A1 cold-start (eventlog-cold-start-read-path@v1): the write-guard sentinels
        # (_event_id_set / _sequence_set) formerly came from an UNCONDITIONAL full-log json.loads scan
        # here (~4s on the real 236k-事件 / 349MB ledger) — a second whole-ledger pass on TOP of the
        # index load, so even a parse-free ≤0.5s index adopt still blew the contract's ≤3s budget.
        # They are now DERIVED FROM THE ADOPTED INDEX below (the adopted+caught-up index IS the
        # committed stream, so the sets are provably identical to a fresh full scan), with only a
        # cheap O(active-segment) tail read up front for the adopt gate. Seed empty; fill below.
        # T-RMD-SEQ-GUARD 段①: _sequence_set = committed sequence_numbers — the write-time uniqueness
        # guard's authority (M-0.1 §5.2 made seq uniqueness real, see _validate_sequence_unique; a set
        # dedups so the 553 historical dup-seqs collapse to one membership each). Reset by recover()
        # alongside _event_id_set.
        self._event_id_set: set[str] = set()
        self._sequence_set: set[int] = set()
        # Cheap committed-tail proxy for the adopt gate (gate 2 compares loaded.max_sequence against
        # _next_sequence-1). _read_max_seq_on_disk is O(active segment) (A4) and returns the PHYSICAL
        # tail (≥ committed tail); a loaded index only holds committed records so loaded.max_sequence
        # ≤ committed ≤ physical — the gate never falsely rejects a fresh index, and a truncated
        # (recover()'d) ledger has physical==committed so a stale-ahead index is still rejected. On
        # adopt success _next_sequence is corrected to the exact committed tail from the index below.
        self._next_sequence = self._read_max_seq_on_disk() + 1
        # RUN-038 L0增强 (M-0.1 §4.1.1/§4.1.8 + §4.2 SLO): cached subjects[]-derived index for the
        # point-lookup fast path. Built lazily over the committed-visible stream, invalidated on
        # every write so reads after a write rebuild from the current committed records. Without
        # it get_event / get_event_by_input_hash were O(n) full scans (§4.2 点查 <10ms / input_hash
        # <20ms wants an index, not a scan).
        self._index: EventIndex | None = None
        # RUN-055 (§7.1 .idx 持久层接运行时): the in-memory index above was built by a fresh full
        # scan on first read; here we try to load the persisted .idx instead. load() is wrapped
        # defensively: a torn/corrupt .idx (e.g. a concurrent writer's half-written cache) is
        # treated as a miss, never a fatal error — the index is rebuildable.
        self._index_store = EventLogIndexStore(self._log_path.parent)
        # T-FND-02 (daemon-choke fix): catch-up baseline. The previous code nulled _index on every
        # write, so every read after a write re-scanned + re-persisted the whole ledger (the daemon's
        # ~6s loop writes DaemonRunCompleted each round → a full 42MB index rebuild每轮, growing
        # linearly with the ledger = the choke). Now _index is kept warm and a read only folds in the
        # records appended to the ACTIVE segment since the last index update (own writes + other
        # processes' commits) — bounded by segment size, independent of total ledger size. These two
        # track which active segment + byte size the warm index already covers, so a read can tell
        # "nothing new" (size unchanged) from "catch up the delta" from "rotation → full rebuild".
        self._index_active_seg: Path | None = None
        self._index_active_size: int = -1
        # A2/A3 (2026-07-02 OOM 崩机根治): adopt-then-catch-up instead of discard-and-rebuild.
        # OLD behaviour required the loaded index to cover EXACTLY the committed tail (an
        # `== _next_sequence-1` staleness gate) — daemon writes every ~6s, so a freshly-opened
        # process's loaded .idx is almost always behind by a handful of records → the gate never
        # actually passed in practice → every cold start paid a full rebuild (~16s / 3GB peak on
        # the real 21.9万-事件账本, the OOM 事故的冷启动大头). Now: any loaded index whose
        # max_sequence is AT OR BEHIND the tail (never ahead — an ahead index implies the ledger
        # was truncated since persist, definitely stale/unsafe) is a candidate to ADOPT, then
        # immediately fold in whatever's missing from the active segment via the same delta-fold
        # `_catch_up_index` already uses post-write (`_fold_active_segment_tail`) — falling back
        # to the old discard-and-rebuild-on-first-read behaviour (never less safe) whenever the
        # delta isn't a clean contiguous run (loaded index further behind than one active
        # segment: a whole rotated segment sits in between untouched by this scan).
        if loaded_index := self._adopt_persisted_index_if_safe():
            self._index = loaded_index
            self._set_index_baseline()
            # A1 cold-start happy path: derive the write-guard sentinels from the adopted+caught-up
            # index instead of a second full-log scan. This is provably identical to a fresh full
            # committed scan because the adopted index IS the committed stream (A2/A3 adopt-then-
            # catch-up correctness — already the invariant test_a2_a3 / test_run055 pin), so its
            # event_id / sequence sets and its max_sequence (the exact committed tail) equal what
            # _derive_lifecycle_state would return — at O(active-segment) cost, not O(ledger).
            self._event_id_set = loaded_index.event_id_set()
            self._sequence_set = loaded_index.sequence_set()
            self._next_sequence = loaded_index.max_sequence + 1
        else:
            # Adopt miss (first-ever start, or .idx absent / stale / torn beyond one active segment):
            # fall back to the full committed scan to build all three sentinels (the pre-A1 path —
            # correct, and only paid when there is no adoptable index to derive them from).
            self._next_sequence, self._event_id_set, self._sequence_set = self._derive_lifecycle_state()

    # ─── write-boundary typed-payload validation (M-0.1 §5.3.1 / RUN-031 T-L0-01) ─

    def _validate_payload_typed(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """Dispatch the event_type's typed payload schema and validate ``payload`` (§5.3.1).

        Fail-closed for ``PAYLOAD_VALIDATION_ENFORCED`` types (raises
        PayloadSchemaValidationError → the write is rejected before anything hits disk).
        For not-yet-enforced registered types, when ``TOWOW_PAYLOAD_SHADOW=<path>`` is set the
        non-conformance is appended there (conformance measurement / diagnostic) — this never
        blocks the write. See schemas/payloads/registry.py for the graduated-rollout rationale.

        Also enforces the birth-gate-smuggle ban (T-LRF-01): this method is THE shared write boundary
        (both path A append_transaction_batch and path B write_direct call it), so rejecting the
        smuggled form here closes the bypass on both write paths — making "birth-time validation is
        hooked at the creation entrance and cannot be bypassed" literally hold.
        """
        self._reject_birthgate_smuggle(event_type, payload)
        shadow_path = os.environ.get("TOWOW_PAYLOAD_SHADOW")
        if shadow_path:
            err = conforms(event_type, payload)
            log_all = os.environ.get("TOWOW_PAYLOAD_SHADOW_ALL")
            if err is not None or log_all:
                with Path(shadow_path).open("a", encoding="utf-8") as sf:
                    sf.write(
                        json.dumps(
                            {
                                "event_type": event_type.value,
                                "conform": err is None,
                                "error": None if err is None else str(err)[:240],
                            },
                            ensure_ascii=False,
                        )
                        + "\n",
                    )
        validate_event_payload(event_type, payload)

    @staticmethod
    def _reject_birthgate_smuggle(event_type: EventType, payload: dict[str, Any]) -> None:
        """Fail-closed: reject a stub-rewrap NodeTouched smuggling a birth-gated kind (T-LRF-01).

        Mirrors the unwrap detection used by the consumers (``kind`` is str AND
        ``stub_original_payload`` is dict) so exactly the form that would get unwrapped-and-consumed as
        an effective birth-gated event (orchestrator._unwrap_stub_rewrap routes a smuggled
        FindingCreated; cli._find_latest_brief_payload selects a smuggled InterviewBriefPublished) is
        what gets rejected at the write boundary. A non-stub NodeTouched (no ``stub_original_payload``)
        and any ``kind`` outside the ban set are untouched.
        """
        if event_type is not EventType.NODE_TOUCHED or not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if (
            isinstance(kind, str)
            and kind in _BIRTHGATE_NONSMUGGLABLE_KINDS
            and isinstance(payload.get("stub_original_payload"), dict)
        ):
            raise ValueError(
                f"NodeTouched stub-rewrap smuggling kind={kind!r} is rejected at the write boundary: "
                f"{kind} owns a dedicated commit-gate birth gate (finding_routability_birth_gate) and "
                "must be created via path A (commit gate), never wrapped as a path-B NodeTouched — "
                "that would bypass the birth gate and create an L2/L0 unwrap asymmetry "
                "(T-LRF-01 birth-time-validation@v1 / debt-a6897075d58f).",
            )

    @staticmethod
    def _reject_audit_event_in_batch(event_type: EventType) -> None:
        """Fail-closed: reject an audit event smuggled into a submitted path-A batch (forgery gate).

        AUDIT_TRIGGERED / AUDIT_VERDICT_RECEIVED are producer-only (see _AUDIT_PRODUCER_ONLY_TYPES):
        the ONLY legitimate path is the commit-gate-internal _write_audit_event (path-B single record).
        A domain-intent batch that carries one is either a forged audit chain injected via an envelope's
        domain intents or a hand-built TransactionBatch — reject at the write boundary so it never lands
        in canonical (which would otherwise let a fake done_elsewhere TaskNodeClosed cross-reference it
        through closure-evidence-verification-gate). This mirrors the path-B ban (write_direct raises via
        _is_path_b_allowed); together these two CLI/envelope-reachable write surfaces are both closed.
        (NOT完备: the raw-append port segments.append_raw_event_line bypasses this guard and the read
        path does not recompute record_hash — that import/raw-append面 relies on audit_integrity's
        post-hoc record_hash sweep, not this write-boundary lock. Full boundary at _AUDIT_PRODUCER_ONLY_TYPES.)
        """
        if event_type in _AUDIT_PRODUCER_ONLY_TYPES:
            raise ValueError(
                f"event_type={event_type.value} is producer-only and MUST NOT appear in a submitted "
                "path-A domain-intent batch: audit events are emitted solely by the commit-gate-internal "
                "audit machinery via _write_audit_event (path B). A batch carrying one is a forged audit "
                "chain (envelope-injected or hand-built TransactionBatch) — rejected fail-closed at the "
                "write boundary so it cannot back a fake done_elsewhere closure.",
            )

    # ─── cross-process write lock (M-0.1 §5.2 / F-019-12) ─────────────────────

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Hold an exclusive cross-process lock for the seq-derive → append → fsync section.

        fcntl.flock(LOCK_EX) is advisory but honored by every EventLog writer (all writes route
        through write_direct / append_transaction_batch / recover, each of which acquires this
        lock). Blocking acquire — write volume is low (M-0.1 §13: 每分钟几十条), so serializing
        writers is not a bottleneck. Released on exit even if the body raises.
        """
        with self._lock_path.open("a") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_max_seq_on_disk(self) -> int:
        """Return the highest sequence_number physically present in the log (-1 if none).

        Authoritative source for next-seq allocation under the file lock — the in-memory
        ``_next_sequence`` counter goes stale the moment another process appends, so we re-derive
        from disk inside the lock. Counts *all* parseable lines (committed or not) so a seq is
        never reused while it is still physically in the file; un-sentineled crash tails are
        truncated (and their seqs freed) by recover(). Lightweight JSON parse (no full pydantic
        validation) and tolerant of legacy non-record lines that lack sequence_number.

        A4 (2026-07-02 OOM 崩机根治, N1): 逆序扫段，扫到第一个含任何带 seq 行的段即返回其段内
        max —— 不再每次写都全量扫全部段。安全性: sequence_number 在文件锁内严格单调递增分配，
        段按物理写入顺序递增编号 (rotation 只往新段写、从不回填旧段)，因此任何较早段的 max seq
        ≤ 任何较晚段的 max seq (当较晚段有带 seq 内容时) —— 逆序找到的第一个非空段, 其段内 max
        必是全局 max。空段/只含裸 poller 行 (bg worktree 轮询器写的 path-B JSON 无 sequence_number
        字段, 见 append_raw_event_line) 的段视为"本段无候选"，继续向更早的段找。

        旧版每次写都 `iter_raw_event_lines` 扫【全部】段 (实测 21.9万条 ≈ 1.4s CPU/次；daemon
        约 6s 一写 → 常态烧掉可观 CPU，是 2026-07-01 夜 OOM 崩机的另一个未点名乘数)。新版把成本
        从 O(总账本) 降到 O(活跃段) —— 绝大多数写命中当前活跃段就直接返回，仅当活跃段刚轮转/为空
        才多看一段。
        """
        for seg in reversed(ordered_segment_paths(self._log_path)):
            seg_max = -1
            try:
                with seg.open("r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            obj = json.loads(stripped)
                        except json.JSONDecodeError:
                            continue  # truncated tail / malformed line
                        seq = obj.get("sequence_number") if isinstance(obj, dict) else None
                        if isinstance(seq, int) and seq > seg_max:
                            seg_max = seq
            except FileNotFoundError:
                continue  # TOCTOU: 段在 listing 后被冷归档移走 (同 iter_raw_event_lines 的容错)
            if seg_max >= 0:
                return seg_max
        return -1

    # ─── segment rotation (§7.1/§7.2/§11.2) ───────────────────────────────────

    def _active_segment_event_count(self, active: Path) -> int:
        """Count event lines currently in the active segment (cheap line count)."""
        if not active.exists():
            return 0
        count = 0
        with active.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def _write_target(self, incoming: int) -> Path:
        """Return the segment file the next ``incoming`` records should append to.

        Called once per write under the file lock, with the whole pending batch size. If the
        active segment is non-empty AND appending ``incoming`` records would push it past either
        threshold (events or bytes), rotate: the new segment is the write target so the *entire*
        batch lands in one fresh segment (a path-A batch never straddles a boundary, per
        sentinel-buffering invariant). Otherwise keep writing to the active segment.

        An empty active segment is never rotated away from — a single batch larger than the
        threshold still goes somewhere, and rotating an empty file would just leave a 0-line
        segment behind.
        """
        active = active_segment_path(self._log_path)
        current_events = self._active_segment_event_count(active)
        if current_events == 0:
            return active  # nothing to rotate away from
        current_bytes = active.stat().st_size if active.exists() else 0
        would_exceed_events = current_events + incoming > self._segment_max_events
        # Byte threshold is checked against the *current* size: we rotate before writing once the
        # active segment has already reached the cap (we don't pre-measure serialized bytes — the
        # spec's "约 10MB" is a soft operational bound, §11.2).
        would_exceed_bytes = current_bytes >= self._segment_max_bytes
        if would_exceed_events or would_exceed_bytes:
            new_seg = next_segment_path(self._log_path)
            new_seg.parent.mkdir(parents=True, exist_ok=True)
            return new_seg
        return active

    # ─── lifecycle ──────────────────────────────────────────────────────────

    def _derive_lifecycle_state(self) -> tuple[int, set[str], set[int]]:
        """Single-pass derivation of (next_sequence, event_id_set, sequence_set) at startup/recover.

        A1 (2026-07-02 OOM 崩机根治): replaces three former separate full-log scans (each via
        ``EventRecord.model_validate_json`` — full pydantic construction of every committed record,
        discarded right after) with ONE cheap pass over ``_iter_committed_lifecycle_fields`` (raw
        ``json.loads``, no pydantic). The three scans' outputs (max_seq / event_id set / sequence
        set) are all derivable from the same ``(sequence_number, event_id)`` stream in one walk —
        the old code paid the parse cost three times for data it only needed once each.

        next_sequence = max_seq + 1 (empty log ⇒ next_sequence = 0); event_id_set / sequence_set
        dedupe naturally via set construction (T-RMD-SEQ-GUARD: historical dup-seqs collapse to one
        member each, same semantics as the old ``_initialize_sequence_set``).
        """
        max_seq = -1
        event_id_set: set[str] = set()
        sequence_set: set[int] = set()
        for seq, event_id in _iter_committed_lifecycle_fields(
            iter_raw_event_lines(self._log_path),
        ):
            if seq > max_seq:
                max_seq = seq
            event_id_set.add(event_id)
            sequence_set.add(seq)
        return max_seq + 1, event_id_set, sequence_set

    # ─── writer (path A: batch) ─────────────────────────────────────────────

    def append_transaction_batch(self, batch: TransactionBatch) -> list[EventRecord]:
        """Append a TransactionBatch atomically (M-0.1a Patch 1 §1.4); return the written records.

        Authoritative sequence_numbers are (re)assigned here, under the cross-process file lock,
        from the on-disk tail (F-019-12) — stamp_for_batch's provisional seqs are non-binding
        because between stamp and append another process may have advanced the log. Re-sequencing
        keeps event_ids stable (all cross-references are by event_id, never by seq) and recomputes
        each record_hash for its new seq. Batch events keep their relative order, so the within-
        batch strict-monotonicity invariant (TransactionBatch validator) is preserved.

        Writes domain events + sentinel + fsync as a single critical section. Crash mid-batch
        leaves the trailing lines un-sentinel-terminated; recover() truncates them.
        """
        with self._mutex, self._file_lock():
            base = self._read_max_seq_on_disk() + 1
            written: list[EventRecord] = []
            for offset, ev in enumerate(batch.events):
                self._reject_audit_event_in_batch(ev.event_type)  # forgery gate: producer-only 类型堵批注入
                self._validate_uniqueness(ev.event_id)
                self._validate_sequence_unique(base + offset)  # T-RMD-SEQ-GUARD 段①: fail-closed
                self._validate_payload_typed(ev.event_type, ev.payload)
                written.append(self._reseq(ev, base + offset))
            # §7.2: rotation decided once, for the whole batch — the batch never straddles a
            # segment boundary (sentinel buffering relies on a batch being contiguous in one file).
            target = self._write_target(len(written))
            with target.open("a", encoding="utf-8") as f:
                for ev in written:
                    f.write(ev.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            for ev in written:
                self._event_id_set.add(ev.event_id)
                self._sequence_set.add(ev.sequence_number)  # T-RMD-SEQ-GUARD 段①
            self._next_sequence = base + len(written)
            # T-FND-02: do NOT null _index here. The next read folds these appends into the warm
            # index via the active-segment catch-up (they land in the active segment, which the
            # read scans for seq > index.max_sequence). Nulling = full rebuild每轮 = the choke.
            return written

    # ─── writer (path B: direct) ────────────────────────────────────────────

    def write_direct(self, intent: EventIntent) -> EventRecord:
        """Path B direct write — stamp intent with writer fields and append a single record.

        Spec §5.1 path B is restricted to specific event types (snapshot_module / capsule_assembler /
        commit_gate-internal / agent_fork NodeTouched). This method enforces the allowed-type
        check at the API boundary.
        """
        if not self._is_path_b_allowed(intent.event_type):
            raise ValueError(
                f"event_type={intent.event_type.value} not allowed on path B (direct write); "
                "use append_transaction_batch instead",
            )
        return self._append_direct_record(intent)

    # audit 事件的 sanctioned producer 集 (write_audit_event 专用口的 provenance 白名单)。真发射点全集:
    # gate.py:_emit_audit_triggered (m05_commit_gate) / audit CLI fallback trigger (m05_audit_trigger) /
    # verdict 三处 (m05_audit_fork)。这是"抄近路 agent"威胁模型下的防伪 (self-declared provenance,
    # 非密码学不可伪): 目标是把手写伪造从"静默过"变成"像 ConceptCreated 一样 raise"。
    _SANCTIONED_AUDIT_ACTOR_TYPE = "commit_gate"
    _SANCTIONED_AUDIT_ACTOR_IDS = frozenset({"m05_audit_fork", "m05_commit_gate", "m05_audit_trigger"})

    def _write_audit_event(self, intent: EventIntent) -> EventRecord:
        """AUDIT_TRIGGERED / AUDIT_VERDICT_RECEIVED 的唯一合法发射口 (producer-only 边界)。

        二类型已移出 _is_path_b_allowed → 手 write_direct 它们会 raise (镜像 ConceptCreated,
        validation_runner.py:705-710)。此口断言 event_type ∈ 二审计类型 且 provenance 为 sanctioned
        audit producer, 否则 raise ValueError。payload 仍由 _append_direct_record 内 _validate_payload_typed
        校验, 不放松。
        """
        if intent.event_type not in _AUDIT_PRODUCER_ONLY_TYPES:
            raise ValueError(
                f"_write_audit_event only for audit events, got {intent.event_type.value}",
            )
        prov = intent.provenance_hint
        actor_type = getattr(prov, "actor_type", None)
        actor_id = getattr(prov, "actor_id", None)
        if actor_type != self._SANCTIONED_AUDIT_ACTOR_TYPE or actor_id not in self._SANCTIONED_AUDIT_ACTOR_IDS:
            raise ValueError(
                f"_write_audit_event requires sanctioned audit producer "
                f"(actor_type=commit_gate, actor_id∈{sorted(self._SANCTIONED_AUDIT_ACTOR_IDS)}), "
                f"got actor_type={actor_type} actor_id={actor_id}",
            )
        return self._append_direct_record(intent)

    # debt-resolution 独立性判据的两个识别常量 (镜像 closure_evidence_check._verdict_is_affirmative
    # 的识别集, 但不做 stub-rewrap unwrap — 同一反伪造立场: 只信原始扁平 event_type, NodeTouched
    # 换壳的 verdict 一律不采信)。
    _DEBT_AFFIRMATIVE_AUDIT_VERDICTS = frozenset({"pass", "conditional_pass"})
    _DEBT_AFFIRMATIVE_VERIFICATION_STATE = "verified"
    # resolve_superseding_content_author_session 只识别这两种 ref_type (commit/finding 反查
    # provenance.session_id); 其余 ref_type (test/ledger_event) 的 fixer 无法反查 → fixer_session=None。
    # ⚠️ None 在这里【不】容忍放行: 对携带真实会话身份的 verdict (非 sanctioned audit-fork), fixer
    # 反查不到就无法正向证实 actor != fixer 的独立性 —— unknown-fixer != safe, 一律 fail-closed 拒
    # (见 write_debt_resolved 的独立性判定分流; finding
    # f-debt-gate-fixer-exclusion-failopen-on-test-ledger-reftype)。
    _DEBT_FIXER_RESOLVABLE_REF_TYPES = frozenset({"commit", "finding"})

    def write_debt_resolved(self, intent: EventIntent, index: EventIndex) -> EventRecord:
        """DebtResolved 的唯一合法发射口 (T-REFLOW-DEBT — 镜像 _write_audit_event 的 producer-only
        边界; DEBT_RESOLVED 已移出通用 _is_path_b_allowed 白名单, 手写 write_direct 它会 raise)。

        functional-equivalence-closure-criterion@v1 铁约束: 一条 debt 的闭合仅当其闭合记录引用一条
        【可被另一个 fork 独立复核】的功能等价证据链才被接受; merge-base 祖先关系 (evidence_type=NS1)
        不构成合法闭合证据, 无论是否带 verdict。fail-closed 四检 (镜像 closure_evidence_check 的
        done_elsewhere 契约, 但独立性判定复用同一对 helper — verdict_actor_session_excludes_closer_
        and_fixer / resolve_superseding_content_author_session):

          (1) evidence_type != EvidenceReasonCode.NS1 —— NS1 是 non_signal, 从不构成证据。
          (2) intent 必须带真实 resolving session_id (provenance_hint.session_id) —— 匿名/system
              闭合没有独立性对照锚 (镜像 done_elsewhere 的 closed_by 对照锚)。
          (3) verification_verdict_ref 必须 resolvable + 是被识别的 affirmative verdict
              (AuditVerdictReceived verdict∈{pass,conditional_pass} 或 FindingVerified
              verification_state=verified)。
          (4) 该 verdict 的 actor_session 必须【同时】!= 本次 resolving session (closer) 且
              != fixer (经 evidence_ref_type/evidence_ref_id 反查)。独立性只在【能正向证实】时才放行:
                - verdict 是 sanctioned audit-fork (provenance 三元 == build_audit_verdict_intent,
                  session_id 结构性恒 None) → 由 fork 结构本身保证独立, 放行, 不需 fixer 反查;
                - 否则 (真实会话身份的 verdict, 如自报 session 的 FindingVerified): 必须 verdict 带真实
                  (非 None) actor_session 且 fixer 可反查 (非 None) 且两者 != closer/fixer 才放行。
                  fixer 反查不到 (None, 含 test/ledger_event 无锚 ref 或 commit/finding ref 解析失败),
                  或 verdict actor_session 缺失 → 无法证实独立性 → fail-closed 拒 (unknown-fixer !=
                  safe; 修补 f-debt-gate-fixer-exclusion-failopen-on-test-ledger-reftype 的
                  fail-open)。

        payload 仍由 _append_direct_record 内 _validate_payload_typed 校验 (DebtResolvedPayload 的
        必填字段本身即是第一道结构层防线), 本方法只加独立性判定这一层通用 pydantic 管不到的东西。
        """
        # 延迟 import: 避免顶层 import commit_gate 包触发 gate.py → event_log.py 的循环导入
        # (gate.py 直接 `from towow.l0.event_log.event_log import build_accepted_batch_e11` —— 若
        # 本模块顶层就 import commit_gate, 本模块尚未执行完就被 gate.py 反向 import 自己, 会撞
        # partially-initialized module 的 ImportError)。
        from towow.l0.commit_gate.closure_evidence_check import (
            _verdict_is_sanctioned_audit_fork,
            resolve_superseding_content_author_session,
            verdict_actor_session_excludes_closer_and_fixer,
        )
        from towow.l1.functional_equivalence_evidence_tier import EvidenceReasonCode

        if intent.event_type is not EventType.DEBT_RESOLVED:
            raise ValueError(
                f"write_debt_resolved only for DebtResolved, got {intent.event_type.value}",
            )
        payload = intent.payload if isinstance(intent.payload, dict) else {}

        if payload.get("evidence_type") == EvidenceReasonCode.NS1.value:
            raise ValueError(
                "DebtResolved evidence_type="
                f"{EvidenceReasonCode.NS1.value} (merge-base ancestry) is a non_signal per "
                "functional-equivalence-evidence-tier@v1 — never valid closing evidence, "
                "fail-closed (functional-equivalence-closure-criterion@v1)",
            )

        closer_session = intent.provenance_hint.session_id
        if not closer_session:
            raise ValueError(
                "DebtResolved requires a real resolving session_id (provenance_hint.session_id) — "
                "anonymous/system closure has no independence comparison anchor, fail-closed",
            )

        verdict_ref = payload.get("verification_verdict_ref")
        if not isinstance(verdict_ref, str) or not verdict_ref:
            raise ValueError(
                "DebtResolved requires verification_verdict_ref — closure without an independent "
                "verdict is fail-closed rejected (functional-equivalence-closure-criterion@v1)",
            )
        verdict_rec = index.lookup_event_id(verdict_ref)
        if verdict_rec is None:
            raise ValueError(
                f"verification_verdict_ref={verdict_ref} does not resolve to any committed event",
            )
        verdict_payload = verdict_rec.payload if isinstance(verdict_rec.payload, dict) else {}
        affirmative = False
        if verdict_rec.event_type is EventType.AUDIT_VERDICT_RECEIVED:
            after = verdict_payload.get("after_state")
            after = after if isinstance(after, dict) else {}
            affirmative = after.get("verdict") in self._DEBT_AFFIRMATIVE_AUDIT_VERDICTS
        elif verdict_rec.event_type is EventType.FINDING_VERIFIED:
            affirmative = verdict_payload.get("verification_state") == self._DEBT_AFFIRMATIVE_VERIFICATION_STATE
        if not affirmative:
            raise ValueError(
                f"verification_verdict_ref={verdict_ref} is not a recognized affirmative verdict "
                "(AuditVerdictReceived verdict in {pass,conditional_pass} or FindingVerified "
                "verification_state=verified)",
            )

        verdict_actor_session = getattr(verdict_rec.provenance, "session_id", None)
        fixer_session = None
        ref_type = payload.get("evidence_ref_type")
        ref_id = payload.get("evidence_ref_id")
        if ref_type in self._DEBT_FIXER_RESOLVABLE_REF_TYPES and isinstance(ref_id, str) and ref_id:
            fixer_session = resolve_superseding_content_author_session(
                index.records(), index, ref_type, ref_id,
            )
        # 独立性分流 (fail-CLOSED on unresolvable fixer for real-session verdicts):
        # 唯一凭"结构"证明独立的 verdict 是 sanctioned audit-fork —— 其 provenance.session_id 由 fork
        # 契约恒 None, 独立性由 provenance 三元结构本身保证, 无需靠 fixer 反查即放行 (与 done_elsewhere
        # (2f) 对同一 helper 的 None 短路语义一致)。对其余任何【携带自报会话身份】的 verdict (典型:
        # FindingVerified — 无 emission-independence 写边界, session 可自报; 也含真实 session 的
        # AuditVerdictReceived), 必须【正向证实】actor != fixer 才放行:
        #   · verdict_actor_session 缺失 (None) —— 非 sanctioned 却无 actor 身份, 无法比较 → 拒;
        #   · fixer 反查不到 (None; test/ledger_event 无锚, 或 commit/finding ref 解析失败) —— 无法
        #     确证 verdict 不是 fixer 自产 → 拒 (unknown-fixer != safe)。
        # 这一步专挡 finding f-debt-gate-fixer-exclusion-failopen-on-test-ledger-reftype: 修复者 A 发
        # 自己的 FindingVerified(session=A) + distinct closer B 发 DebtResolved(evidence_ref_type=test)
        # 时, 旧路径 fixer_session=None 被 helper 容忍放行 → A 自证闭合。现按"verdict 是否结构性独立"分流。
        if not _verdict_is_sanctioned_audit_fork(verdict_rec):
            if verdict_actor_session is None:
                raise ValueError(
                    "verification_verdict_ref carries no actor_session and is not a sanctioned "
                    "audit-fork verdict — cannot establish independence (actor != fixer), "
                    "fail-closed (functional-equivalence-closure-criterion@v1)",
                )
            if fixer_session is None:
                raise ValueError(
                    "cannot resolve fixer session for a real-session verdict "
                    f"(evidence_ref_type={ref_type!r}) — unresolvable fixer != safe, independence "
                    "cannot be positively established, fail-closed "
                    "(functional-equivalence-closure-criterion@v1 actor != fixer extension)",
                )
        if not verdict_actor_session_excludes_closer_and_fixer(
            verdict_actor_session, closer_session=closer_session, fixer_session=fixer_session,
        ):
            raise ValueError(
                "verification_verdict_ref actor_session equals the resolving (closer) or fixer "
                "session — violates independence (functional-equivalence-closure-criterion@v1 "
                "actor != fixer extension), fail-closed",
            )
        return self._append_direct_record(intent)

    def _append_direct_record(self, intent: EventIntent) -> EventRecord:
        """Path-B append 核心 (write_direct 与 _write_audit_event 共用): 校验 payload + append 单条。

        语义与旧 write_direct [path-b 检查之后] 整段逐字节一致 (validate → 跨进程锁下 stamp/append →
        索引保鲜)。抽出以便 audit 事件经专用 producer-only 口 (_write_audit_event) 而非通用 path-b 集合。
        """
        self._validate_payload_typed(intent.event_type, intent.payload)
        with self._mutex, self._file_lock():
            # F-019-12: derive seq from the on-disk tail under the cross-process lock, never
            # from the (possibly stale) in-memory counter — another process may have appended.
            sequence_number = self._read_max_seq_on_disk() + 1
            record = self._stamp(
                intent,
                sequence_number=sequence_number,
                transaction_id=None,
                batch_id=None,
                batch_position=None,
            )
            self._validate_uniqueness(record.event_id)
            self._validate_sequence_unique(sequence_number)  # T-RMD-SEQ-GUARD 段①: fail-closed
            target = self._write_target(1)
            with target.open("a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._event_id_set.add(record.event_id)
            self._sequence_set.add(record.sequence_number)  # T-RMD-SEQ-GUARD 段①
            self._next_sequence = record.sequence_number + 1
            # T-FND-02 索引保鲜 (写路径 427f548 + 读路径 catch-up 34ef2e7 综合): path-B
            # (write_direct) 是 daemon 热路径 (dispatch/silent-death/goal-session emit 全走这)。
            # 旧 `self._index = None` 让下个读全量重扫+pydantic 重解析全部 committed records
            # (实测 42311 条 = 1434ms/写) → daemon CPU 随账本线性恶化。
            # 改: 索引若已覆盖【正好写前磁盘 tail】(max_sequence == 本条 seq-1, 无外部进程追加),
            # 增量 add 本条 (快路径, 经 iter_committed_records_from_lines 精确匹配 committed 语义)。
            # 否则【不再 =None】—— 索引保持 warm, 下个读经 _catch_up_index 折入活动段增量
            # (含其他进程的提交), 不全量重建 (34ef2e7)。三路径 (_build/add/add_records) 共用
            # _index_one, 语义一致; add_records 的 seq>max_sequence 过滤防 catch-up 重复折入已 add 的本条。
            if self._index is not None and self._index.max_sequence == sequence_number - 1:
                for committed in iter_committed_records_from_lines(
                    iter([record.model_dump_json()]),
                ):
                    self._index.add(committed)
        return record

    def stamp_for_batch(
        self,
        intent: EventIntent,
        *,
        transaction_id: str,
        batch_id: str,
        batch_position: int,
    ) -> EventRecord:
        """Stamp a path-A intent into an EventRecord (used by M-0.5 batch assembler).

        Caller (commit gate) collects multiple stamped records into a TransactionBatch then
        appends via append_transaction_batch. The sequence_number assigned here is *provisional*
        — it only needs to be strictly increasing within the batch (TransactionBatch validator);
        append_transaction_batch re-assigns the authoritative global seq under the cross-process
        file lock (F-019-12), since another process may advance the log between stamp and append.
        """
        with self._mutex:
            provisional_seq = self._next_sequence
            self._next_sequence += 1
            return self._stamp(
                intent,
                sequence_number=provisional_seq,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=batch_position,
            )

    @staticmethod
    def _is_path_b_allowed(event_type: EventType) -> bool:
        """M-0.1 §5.1 path B allowed types (snapshot / consolidation / capsule / NodeTouched /
        gate-internal DriftDetected).

        AuditTriggered / AuditVerdictReceived 已移出此集合 —— 它们改走 producer-only 的
        _write_audit_event (锁写边界: 手 write_direct 它们会 raise, 堵掉手写伪造 verdict 的口子)。

        M-0.6 §8.2 + Narrow Patch G: ObligationScopeJudged + ObligationActivated 同走 path B
        by M-0.3 pipeline (scope judgment stages 3/5; activation after CapsuleCompiled).
        """
        return event_type in {
            # snapshot module
            EventType.SNAPSHOT_CREATED,
            EventType.SNAPSHOT_SUPERSEDED,
            EventType.CROSS_RUN_CONSOLIDATION_COMMITTED,
            EventType.ARCHIVE_SEGMENT_MOVED,
            EventType.DIGEST_SUPERSEDED,
            EventType.RETENTION_POLICY_CHANGED,
            # capsule assembler (M-0.3 pipeline)
            EventType.CAPSULE_COMPILED,
            EventType.RESOLVER_DECISION_MADE,
            # RUN-031 T-L3kc-06 — capsule injection fail-closed signal (capsule assembler, path B)
            EventType.CAPSULE_INJECTION_FAILED,
            # T-L0-16 (波1) — capsule 装配 abort 留痕 (M-0.1 写失败 → 受控中止, capsule assembler path B)
            EventType.CAPSULE_ASSEMBLY_FAILED,
            # M-0.6 §8.2 — M-0.3 pipeline path-B obligation scoped events
            EventType.OBLIGATION_SCOPE_JUDGED,
            EventType.OBLIGATION_ACTIVATED,
            # T-FIX-B5-03 (CONSTITUTION-unknown#3) — 语义冲突检测留痕 (scan 自观测件, like DriftDetected /
            # DaemonRunCompleted)。被同步调用的 scan_semantic_conflicts_and_emit 读 ObligationScopeJudged
            # → detect → 对每条冲突写真事件 → orchestrator route main-inbound。只 surface 不仲裁
            # (仲裁 deferred, debt-run080-semantic-arbitration), 故走 path-B 自观测路径 (非被审计的域 patch)。
            EventType.SEMANTIC_CONFLICT_DETECTED,
            # R08 — owner 答复送回等待会话被消费的硬闭环留痕 (respond CLI / orchestrator 自观测 surface,
            # 非被审计的域 patch; 像 dispatch/alarm 一样走 path-B)。
            EventType.ESCALATION_ANSWER_APPLIED,
            # commit gate internal
            EventType.DRIFT_DETECTED,
            # AUDIT_TRIGGERED / AUDIT_VERDICT_RECEIVED 已移出 → 走 producer-only _write_audit_event
            # (锁写边界: 手 write_direct 它们 raise, 堵手写伪造 verdict 口)。
            # RUN-031 T-L3kc-03 — V-01 owner-guard observation (gate/guard-internal, like drift)
            EventType.OWNER_GUARD_VIOLATION,
            # 哨兵 A6 正经源 (irreversible-action-blocked-audit-event@v1) — PhysicalGate DENY 高危
            # 不可逆动作时 best-effort emit (guard-internal observation, like OwnerGuardViolation;
            # check_and_emit 走 write_direct path-B, 故须在此白名单内, 否则 emit 被拒静默丢)。
            EventType.IRREVERSIBLE_ACTION_BLOCKED,
            # T-FIX-B4-05 — admin-bypass 旗标受控产生/撤销留痕 (guard 出口的 owner 物理动作
            # provenance, guard-internal observation like OwnerGuardViolation — 不走 commit gate:
            # 它记录的是 owner 对门本身的 Class A 决定, 不是要被审计的域 patch)
            EventType.GUARD_ADMIN_BYPASS_GRANTED,
            EventType.GUARD_ADMIN_BYPASS_REVOKED,
            # owner-confirm@v1 (组件6) — 不可伪造 owner 授权载体 (guard 出口的 owner 物理动作
            # provenance, guard-internal observation like GuardAdminBypass — 记录 owner 对某具体不可逆
            # 动作的 Class A 授权, 不是被审计的域 patch; `towow owner-confirm grant` 经 write_direct emit)
            EventType.OWNER_CONFIRMATION_GRANTED,
            # RUN-029 第0波 ③ — self-debt tracking (gate/skill-internal observation, like drift).
            # DEBT_RESOLVED 已移出 (T-REFLOW-DEBT, functional-equivalence-closure-criterion@v1):
            # 一条债的闭合必须过独立性核验 (evidence chain + verdict resolvable + affirmative +
            # actor != closer/fixer), 通用 path-B 白名单管不了这些, 故它现在只走
            # write_debt_resolved 这个 producer-only 口 (镜像 _write_audit_event) —— 手写
            # write_direct(DebtResolved) 会 raise。DEBT_REGISTERED 仍走 path-B (register/re-register
            # 不构成"闭合"主张, 无需独立核验)。
            EventType.DEBT_REGISTERED,
            # T-JLM-01 judgment-case@v1 — 判例富化持久化 (machine-derived self-observation, like
            # self-debt / SemanticConflictDetected)。判例是对一条已捕获 NatureJudgmentCaptured 的机器
            # 结构化加工记录 (actor_type=system), 不是 Nature 本人的 checkpoint (那才走 Path A, 不拿
            # Path B 权限) —— 故与 DebtRegistered 同族走 path-B。纠正走通用 EventBase.supersede。
            # l1.judgment_case (emit_judgment_case / correct_judgment_case) 经 write_direct emit。
            EventType.JUDGMENT_CASE_ENRICHED,
            # T-JLM-03 preference-as-test-harness@v1 — 判例回归验证门结论 (machine-derived
            # self-observation, 同族 JudgmentCaseEnriched / self-debt)。一次回归运行的度量结论
            # (score/pass_threshold/gate_result_state), actor_type=system, 不是 Nature 本人的新判断
            # (故与 JudgmentCaseEnriched 同走 path-B, 非 commit gate 审计的域 patch)。
            # l1.judgment_regression_harness.emit_regression_result 经 write_direct emit。
            EventType.JUDGMENT_REGRESSION_EVALUATED,
            # T-TRACK-01 — capability status tracking (system self-observation, like self-debt)
            EventType.CAPABILITY_BASELINE_INGESTED,
            EventType.CAPABILITY_STATUS_ADVANCED,
            # T-L3kc-04 — orchestrator dispatch-failed audit (daemon-internal observation, like drift)
            EventType.ORCHESTRATOR_DISPATCH_FAILED,
            # T-RMD-S3-REAPER — exec claim reaper 回收留痕 (orchestrator daemon-internal self-observation,
            # 同 ORCHESTRATOR_DISPATCH_FAILED; reap_stale_exec_claims 经 write_direct emit)
            EventType.EXEC_CLAIM_REAPED,
            # 哨兵 A3 空转源 — reconcile pass 收尾发布五计数 (daemon-internal self-observation, like
            # dispatch-failed / drift; reconcile_loop.run_reconcile_pass 经 write_direct emit)
            EventType.RECONCILE_CYCLE_PUBLISHED,
            # T-RMD-S5-OBSERVER (sentinel-blind, critical) — 哨兵 A1-A8 pass liveness/failure 自观测
            # (眼睛崩可见; m2x_polling.run_sentinel_pass_safe 经 write_direct emit, like Reconcile/DaemonRun)
            EventType.SENTINEL_PASS_COMPLETED,
            EventType.SENTINEL_PASS_FAILED,
            # RUN-052 (M-3.1 §10) — run wrapper Run* lifecycle (maintenance-run self-observation,
            # like snapshot/consolidation events). Replaces the per-command DAEMON_RUN_COMPLETED
            # NodeTouched fake with real path-B RunStarted / RunDigestPublished / RunFailed.
            EventType.RUN_STARTED,
            EventType.RUN_DIGEST_PUBLISHED,
            EventType.RUN_FAILED,
            # T-L3kc-01 — init's ProjectInitialized marker (bootstrap step-3, path-B like snapshot)
            EventType.PROJECT_INITIALIZED,
            # RUN-058 (M-3.4 §3.3) — validation runner 留痕 (自观察, like DebtRegistered / DriftDetected)
            EventType.VALIDATION_SCENARIO_RUN,
            # RUN-066 (M-3.3 §11.1) — 迁移 run lifecycle 留痕 (迁移工具自观察, like RunStarted)。
            # 注: MigrationStepRecorded 不在此 — 它进 apply-batch 的 commit gate batch (path A), 跟
            # ConceptCreated/ObligationCaptured 同走 M-0.5 (§0.3 不绕开 commit gate)。这 3 个是 run
            # lifecycle 观察 (启动/batch提交/完成), 不是被审计的域 patch → path B。
            EventType.MIGRATION_RUN_STARTED,
            EventType.MIGRATION_BATCH_SUBMITTED,
            EventType.MIGRATION_RUN_COMPLETED,
            # T-L1-08 (F11) — interview sub-skill "decided NOT to invoke" 留痕 (采访 skill 自观察, like drift)
            EventType.SUB_SKILL_INVOCATION_SKIPPED,
            # K2b-REG#3 (substrate 2, 50-graph-protocol §6.1) — orchestrator 自动 spawn 子会话时
            # emit 的血缘留痕 (daemon-internal self-observation, 同 GoalSessionStarted / dispatch-failed /
            # silent-death alarm — orchestrator 记录"我 spawn 了一个子会话"这个自身动作, 非被审计的域 patch)。
            # 设计 §A 权衡: parent emit (持久 — 子会话死时血缘记录仍在, 对 orphan/血缘检测重要) >
            # child emit (gate 路径更干净但子死前丢)。倾向持久性 → parent (orchestrator) 经 path-B emit
            # 真注册 SESSION_SPAWNED (像 ORCHESTRATOR_DISPATCH_FAILED 那样), 让 node_reducers 真消费物化
            # session_graph 血缘边 (NODE_TOUCHED stub 会让 get_events_by_type(SESSION_SPAWNED) 找不到 →
            # 投影永不物化 = 假兑现, 故必须真 event_type 走 path-B 而非 stub)。
            EventType.SESSION_SPAWNED,
            # B-4 (substrate 4, 50-graph-protocol §6.2) — orchestrator 注册 per-session 执行锁时 emit 的
            # held-by 留痕 (同 SessionSpawned: daemon-internal self-observation; orchestrator 记录"这个会话
            # 此刻持有这把执行锁"这个自身动作)。node_reducers 据此物化 lock_graph 的 lock--held-by-->session
            # 边 (并发契约可查); 同样必须真 event_type 走 path-B 而非 stub (否则 get_events_by_type 找不到 =
            # lock_graph 永空 = 假兑现)。K2b 已建好消费侧 (lock_graph reducer + 测), 此前无源 emit → 现接通。
            EventType.LOCK_ACQUIRED,
            # RUN-078 (M-2.1 §3.2 Patch M-2.1-A) — daemon run 自观察 (scanned_count/findings 可观测两层
            # event, like RunStarted/DriftDetected)。修 debt-e40f15a3: `daemon run-once` 真调 scan 库层
            # 手动单次跑后发真 DaemonRunCompleted, 替掉 _write_stub_event NodeTouched 占位。注: 真 daemon
            # *持续运行自动定期* emit 仍 daemon-gated (本 path-B 仅放行 *手动单次* run-once 写真事件)。
            EventType.DAEMON_RUN_COMPLETED,
            # envelope-submitted (envelope precedes commit gate per M-0.4 §4.2)
            EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            # agent fork
            EventType.NODE_TOUCHED,
            # f-escalation-task-oriented-not-reversibility-framework — owner 把改不了/不可变的
            # finding (如账本 553 历史重号) accept 为已知基线 (owner/治理基线决定, 非被审计的域
            # patch — 像 GuardAdminBypass/DebtRegistered 那类 owner 自观察事件)。path-B 让 owner
            # 一条 write_direct 就能 accept 一个 finding, 哨兵每轮先查 accepted 集跳过, 永不再报。
            EventType.FINDING_ACCEPTED,
        }

    def _stamp(
        self,
        intent: EventIntent,
        *,
        sequence_number: int,
        transaction_id: str | None,
        batch_id: str | None,
        batch_position: int | None,
    ) -> EventRecord:
        """Stamp an intent into an EventRecord at the given sequence_number.

        Builds the record with a placeholder hash first, then sets record_hash via the shared
        _hash_input_for() so stamping and re-sequencing (_reseq) use byte-identical hash logic.
        """
        provenance = Provenance(
            actor_type=intent.provenance_hint.actor_type,
            actor_id=intent.provenance_hint.actor_id,
            session_id=intent.provenance_hint.session_id,
            task_id=intent.provenance_hint.task_id,
            run_id=intent.provenance_hint.run_id,
            causation_event_id=intent.provenance_hint.causation_event_id,
            correlation_id=intent.provenance_hint.correlation_id,
            # E.1.1 Conflict 2: propagate provenance boundary fields
            daemon_name=intent.provenance_hint.daemon_name,
            skill_id=intent.provenance_hint.skill_id,
            parent_session_id=intent.provenance_hint.parent_session_id,
            fork_name=intent.provenance_hint.fork_name,
            writer_id="m01-writer",
        )
        draft = EventRecord(
            event_id=f"evt-{uuid.uuid4().hex}",
            sequence_number=sequence_number,
            timestamp=datetime.now(tz=UTC),
            transaction_id=transaction_id,
            batch_id=batch_id,
            batch_position=batch_position,
            record_hash="0",  # placeholder; excluded from the hash, replaced below
            local_intent_id=intent.local_intent_id,
            event_type=intent.event_type,
            event_category=intent.event_category,
            payload=intent.payload,
            provenance=provenance,
            base_classification=intent.base_classification,
            supersede=intent.supersede,
            subjects=list(intent.subjects),
            schema_version=intent.schema_version,
        )
        return draft.model_copy(
            update={"record_hash": self._compute_record_hash(self._hash_input_for(draft))},
        )

    def _reseq(self, record: EventRecord, new_seq: int) -> EventRecord:
        """Return a copy of record at new_seq with record_hash recomputed (path-A append, F-019-12).

        Only sequence_number (and the dependent record_hash) change; event_id, timestamp,
        provenance and payload are preserved so all event_id cross-references stay valid.
        """
        if record.sequence_number == new_seq:
            return record
        reseqed = record.model_copy(update={"sequence_number": new_seq})
        return reseqed.model_copy(
            update={"record_hash": self._compute_record_hash(self._hash_input_for(reseqed))},
        )

    def normalize_batch_seqs(self, records: list[EventRecord]) -> list[EventRecord]:
        """Renumber ``records`` to strictly-increasing provisional seqs (event_ids preserved).

        Needed when a caller (T-L0-27 commit gate) interleaves stamp_for_batch with a
        write_direct that resets the in-memory next-seq counter — the provisional seqs may no
        longer be monotonic, which the TransactionBatch validator requires. The authoritative
        seqs are re-assigned from the on-disk tail at append_transaction_batch anyway, so this
        only has to satisfy the batch validator; event_ids (all cross-references) are untouched.
        """
        with self._mutex:
            base = self._next_sequence
            out = [self._reseq(rec, base + offset) for offset, rec in enumerate(records)]
            self._next_sequence = base + len(out)
            return out

    # ─── writer (distributed merge: worktree-local ledger → canonical) ────────

    def merge_foreign_events(self, foreign_records: Iterable[EventRecord]) -> LedgerMergeResult:
        """Distributed-ledger merge (f-ledger-canonical-fork-distributed-merge-design).

        Append a foreign (worktree-local) ledger's committed events into THIS canonical ledger,
        with the canonical RE-ASSIGNING each a fresh global ``sequence_number``. The git-style model:

          - ``event_id`` = identity (globally unique, stable) — preserved verbatim, never re-minted.
          - ``sequence_number`` = the canonical order at merge-reflow time — re-assigned here from
            the canonical's on-disk tail (the foreign/local seq is purely local and is dropped, so a
            seq never leaks across ledgers and two ledgers each holding their own seq N can both
            merge in without colliding — F-019-12 same seq-derivation as ``append_transaction_batch``).
          - merge = **append-only + dedup by event_id + never overwrite**. A foreign record whose
            event_id already lives in canonical is skipped (idempotent re-merge); the canonical's
            existing bytes are never rewritten — only the tail grows.

        Batch grouping is preserved: each record keeps its (foreign) ``batch_id`` / ``transaction_id`` /
        ``batch_position``, so a foreign path-A batch (domain events + its trailing CommitAccepted
        sentinel — both yielded by the committed-visibility reader) stays contiguous and remains
        committed-visible after the merge. Only ``sequence_number`` (and the dependent ``record_hash``)
        change, so ``verify_record_integrity`` passes for each merged record at its new seq and every
        event_id cross-reference stays valid.

        Pass committed records only (e.g. ``foreign_log.all_records()`` / ``merge_ledger_from``) —
        crash-tail un-sentineled lines must not be imported.
        """
        with self._mutex, self._file_lock():
            base = self._read_max_seq_on_disk() + 1
            to_append: list[EventRecord] = []
            skipped: list[str] = []
            seen_in_merge: set[str] = set()
            for rec in foreign_records:
                # dedup by event_id — already in canonical, or a dup within this same merge input.
                if rec.event_id in self._event_id_set or rec.event_id in seen_in_merge:
                    skipped.append(rec.event_id)
                    continue
                seen_in_merge.add(rec.event_id)
                to_append.append(rec)
            reseqed = [self._reseq(rec, base + offset) for offset, rec in enumerate(to_append)]
            if reseqed:
                # Rotation decided once for the whole merge so every imported batch stays contiguous
                # in a single segment (path-A sentinel buffering relies on batch contiguity).
                target = self._write_target(len(reseqed))
                with target.open("a", encoding="utf-8") as f:
                    for ev in reseqed:
                        f.write(ev.model_dump_json() + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                for ev in reseqed:
                    self._event_id_set.add(ev.event_id)
                self._next_sequence = base + len(reseqed)
                # Leave the warm index intact (like append_transaction_batch): the appended records
                # land in the active segment and the next read folds them via active-segment catch-up.
            return LedgerMergeResult(
                merged_event_ids=tuple(ev.event_id for ev in reseqed),
                skipped_duplicate_event_ids=tuple(skipped),
                base_sequence=base if reseqed else -1,
                appended_count=len(reseqed),
            )

    def merge_ledger_from(self, foreign_log_path: Path) -> LedgerMergeResult:
        """Open a foreign ledger and merge its committed events into this canonical (convenience).

        Thin wrapper over ``merge_foreign_events``: opens the foreign EventLog (read its
        committed-visible records only) and merges. The foreign ledger is NOT mutated by the merge
        itself (opening it only idempotently refreshes its own ``.ledger-root`` sentinel). Used by
        the controlled reflow that folds a stray/worktree-local ledger back into canonical.
        """
        foreign = EventLog(foreign_log_path)
        return self.merge_foreign_events(foreign.all_records())

    @staticmethod
    def _hash_input_for(record: EventRecord) -> dict[str, object]:
        """Canonical hash-input projection of a record (record_hash itself excluded).

        Single source of hash logic for both _stamp and _reseq — keeps a re-sequenced record's
        hash byte-identical to how it would have been stamped at that seq.
        """
        return {
            "event_id": record.event_id,
            "sequence_number": record.sequence_number,
            "timestamp": record.timestamp.isoformat(),
            "transaction_id": record.transaction_id,
            "batch_id": record.batch_id,
            "batch_position": record.batch_position,
            "local_intent_id": record.local_intent_id,
            "event_type": record.event_type.value,
            "event_category": record.event_category.value,
            "payload": record.payload,
            "provenance": record.provenance.model_dump(),
            "base_classification": record.base_classification.value,
            "supersede": record.supersede.model_dump(),
            "subjects": [s.model_dump() for s in record.subjects],
            "schema_version": record.schema_version,
        }

    @staticmethod
    def _compute_record_hash(record_dict: dict[str, object]) -> str:
        # Hash everything except record_hash itself (it would be self-referential).
        without_hash = {k: v for k, v in record_dict.items() if k != "record_hash"}
        canonical = json.dumps(without_hash, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _validate_uniqueness(self, event_id: str) -> None:
        if event_id in self._event_id_set:
            raise ValueError(f"duplicate event_id: {event_id}")

    def _validate_sequence_unique(self, sequence_number: int) -> None:
        """T-RMD-SEQ-GUARD 段①: 写时 sequence_number 唯一性硬不变量 (fail-closed)。

        命门: M-0.1 §5.2 头注释声称 "no two writes ever share a sequence_number", 但写路径只 ever
        validate event_id (_validate_uniqueness) —— seq 唯一一直是个【假设】(靠 _read_max_seq_on_disk()+1
        派生, 健康时恒成立), 从没在写时被断言。一旦派生的 seq 因任何原因撞上一个已存在的 seq (stale tail
        read / _reseq 计算偏差 / 外部对账本的污染留下的空洞), 旧路径会静默落盘一条重号记录 —— 正是 06-10
        三次账本回退在 base 区留下 553 个 dup-seq 的同型腐蚀, 且因 record_hash 含 seq 故每条各自 hash 合法,
        对防篡改门 (audit_integrity) 与 reconstructability 双盲 (finding f-sub-l0-seq-uniqueness-blindspot)。

        本守卫把 "seq 唯一" 从假设升成真不变量: 在锁内、写盘前断言新 seq 未见, 违反即 raise (写不落地,
        与 event_id 唯一同级 fail-closed)。它不处置存量 553 (归 owner-gated T-RMD-SEQ-FORENSICS), 只
        物理阻止从此再产生新的 dup-seq。
        """
        if sequence_number in self._sequence_set:
            msg = (
                f"duplicate sequence_number: {sequence_number} already present "
                "(T-RMD-SEQ-GUARD 段①: seq uniqueness is a fail-closed write-time invariant)"
            )
            raise ValueError(msg)

    # ─── 完整性校验 / 防篡改门 (T-L0-03, M-0.1 Patch1 §1.3) ────────────────────

    def verify_record_integrity(self, record: EventRecord) -> bool:
        """T-L0-03: 重算 record_hash 与存储值比对 — 完好返 True, 落盘后被改返 False。

        复用写端同一套 hash 逻辑 (_hash_input_for + _compute_record_hash), 故完好 record 重算
        byte-identical。record_hash 此前只写不验 (写产物字段); 本方法 + audit_integrity 让它真承担
        完整性职责 (owner-decided RUN-038: 做防篡改门)。
        """
        recomputed = self._compute_record_hash(self._hash_input_for(record))
        return recomputed == record.record_hash

    def audit_integrity(self) -> list[str]:
        """T-L0-03 防篡改门: 审计路径全量重算比对, 返回被篡改 (recompute≠stored) record 的 event_id。

        触发点 = 审计 / maintenance 路径 (非每读都算, owner-decided 成本可控); 任一不符 = 该 record
        落盘后被改过 → 报出供上层 (maintenance daemon / audit) 处置。完好日志返空。

        T-RMD-SEQ-GUARD 段②: record_hash 含 seq, 故一条 dup-seq record 自身 hash 仍合法 → 防篡改门
        对 seq 重号【完全隐形】。seq 唯一性巡检是这道完整性防线的同族补强, 见 audit_sequence_uniqueness
        (纯检测) 与 audit_integrity_seq_patrol (检测 + 活路径 emit 一条 canonical 报告事件)。
        """
        return [rec.event_id for rec in self._iter_committed_records() if not self.verify_record_integrity(rec)]

    def audit_sequence_uniqueness(self) -> dict[int, list[str]]:
        """T-RMD-SEQ-GUARD 段②: 巡检 committed 流, 报出任何 dup-seq (同一 seq 被多个不同 event_id 共用)。

        复用 awareness.stress_invariants 的 by_seq 检测同型逻辑 (把它搬进 L0 生产路径): record_hash 含
        seq 故每条 dup-seq 各自 hash 合法 → 防篡改门 (audit_integrity) 与 reconstructability 对它双盲
        (finding f-sub-l0-seq-uniqueness-blindspot, GC-04 critical)。本巡检让账本里历史遗留的 dup-seq
        腐蚀 (06-10 三次回退在 base 区留下的 553 个) 从完整性审计的盲区里【显式报出】。

        读 _iter_committed_records (dup-seq 保留, 不经 _by_sequence last-wins 去重)。返回
        {sequence_number: [event_id, ...]} —— 仅含撞同一 seq 的【多个不同 event_id】条目 (event_id
        升序去重), 健康账本返空。事件身份是 event_id 不是 seq, 故同 event_id 不算撞号。
        """
        by_seq: dict[int, list[str]] = {}
        for rec in self._iter_committed_records():
            by_seq.setdefault(rec.sequence_number, []).append(rec.event_id)
        return {seq: sorted(set(eids)) for seq, eids in by_seq.items() if len(set(eids)) > 1}

    def audit_integrity_seq_patrol(
        self,
        *,
        actor_id: str = "l0-seq-integrity-audit",
    ) -> tuple[dict[int, list[str]], EventRecord | None]:
        """T-RMD-SEQ-GUARD 段②: 跑 seq 唯一性巡检 + (若有腐蚀且未登记) emit 一条 canonical 报告事件。

        "在 audit_integrity 加一条 seq 巡检 + 让 553 显式报出" 的【活路径】形态: 巡检发现 dup-seq 时,
        把它登记成一条 canonical DebtRegistered —— deferral 型 (存量 dup-seq 的取证处置延迟到 owner-gated
        T-RMD-SEQ-FORENSICS, depends_on 指它), provenance 非交互 (ActorType.SYSTEM, 非人手 CLI session),
        复用 l0.debt.registry.register_debt 不重造轮子。这条报告事件随后经 EventLog.all_records() 可读到,
        把账本完整性防线从对 dup-seq 双盲变成显式可见。

        幂等: debt_id 稳定 (debt-ledger-seq-uniqueness) + 已登记过则跳过 → 反复巡检不重号刷屏。
        返回 (dup_seq_map, emitted_record_or_None) —— 无腐蚀 / 已登记 → record=None。
        """
        dup = self.audit_sequence_uniqueness()
        if not dup:
            return dup, None
        debt_id = "debt-ledger-seq-uniqueness"
        # 幂等: 已登记过该 debt → 不再 emit (committed 流里已有该 debt_id 的 DebtRegistered)。
        already = any(
            isinstance(rec.payload, dict) and rec.payload.get("debt_id") == debt_id
            for rec in self.get_events_by_type(EventType.DEBT_REGISTERED)
        )
        if already:
            return dup, None
        # lazy import: l0.debt.registry imports EventLog → 模块顶层 import 会循环, 故在调用点引入
        # (同 cold_lookup 的 lazy import 范式)。
        from towow.l0.debt.registry import register_debt

        record_count = sum(len(eids) for eids in dup.values())
        record = register_debt(
            self,
            debt_id=debt_id,
            debt_type=DebtType.DEFERRAL,
            severity=DebtSeverity.NORMAL,
            title=f"账本 dup-seq 腐蚀: {len(dup)} 个 seq 撞号 ({record_count} 条真记录)",
            description=(
                f"seq 唯一性巡检在 committed 流发现 {len(dup)} 个 sequence_number 被多个不同 event_id "
                f"共用 (共 {record_count} 条真记录, 06-10 三次账本回退遗存)。写时守卫 (段①) 已物理挡住新增"
                "腐蚀; 存量这批的取证处置延迟到 owner-gated T-RMD-SEQ-FORENSICS。读路径已改按 event_id 去重 "
                "(段③), 故投影重建不再静默丢这些真事件。"
            ),
            against_capability="ledger-seq-uniqueness-integrity",
            depends_on=["T-RMD-SEQ-FORENSICS"],
            resolution_criteria="T-RMD-SEQ-FORENSICS dispositions the historical dup-seq records.",
            actor_id=actor_id,
        )
        return dup, record

    # ─── point-lookup index fast path (RUN-038, §4.2 SLO) ────────────────────

    def _event_id_at_seq_on_disk(self, seq: int) -> str | None:
        """Cheap raw scan (reverse segment order, json.loads only — no pydantic) for the
        event_id physically on disk at an exact ``sequence_number``. None if not found in any
        segment (e.g. the seq is currently under path-A envelope-only buffering with no sentinel
        yet, or — theoretically — was cold-archived and pruned from hot; either way "not found"
        is the correct, safe answer for the A2 spot-check caller: it means "can't verify, don't
        adopt").

        Only used once at __init__ (A2 adopt-time spot-check), not on any write-hot path. Scans
        from the tail backward with early exit on first match — in the common case (loaded index
        is recent) this finds the target in the first segment it opens.
        """
        for seg in reversed(ordered_segment_paths(self._log_path)):
            try:
                with seg.open("r", encoding="utf-8") as f:
                    for line in f:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            obj = json.loads(stripped)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(obj, dict) and obj.get("sequence_number") == seq:
                            event_id = obj.get("event_id")
                            return event_id if isinstance(event_id, str) else None
            except FileNotFoundError:
                continue  # TOCTOU: segment vanished (cold-move race), same tolerance as elsewhere
        return None

    def _adopt_persisted_index_if_safe(self) -> EventIndex | None:
        """A2/A3 (2026-07-02 OOM 崩机根治): try to adopt the persisted .idx + catch it up to the
        current tail, returning the ready-to-use index — or None when it isn't safe/possible to
        adopt (caller falls back to the pre-existing discard-and-rebuild-on-first-read path).

        Three gates, all must pass:
          1. A persisted index actually loads (I/O and pydantic-parse failures ⇒ None, same as
             the pre-existing defensive load() wrapping).
          2. ``loaded.max_sequence`` is AT OR BEHIND the just-derived committed tail
             (``_next_sequence - 1``). An index whose recorded tail is AHEAD of what a fresh scan
             just found means the ledger was truncated (recover()) since the .idx was persisted —
             definitely stale, never adopt.
          3. Ghost-record spot-check: compare the loaded index's own record at its max_sequence
             against what's physically on disk at that same seq RIGHT NOW. A cross-process
             truncate-then-rewrite (recover() truncates a crash tail, then new writes reuse those
             freed seq numbers for genuinely different records) would leave a stale-but-innocuous-
             looking .idx whose tail seq matches the fresh count yet whose content is wrong —
             this check catches that (and is strictly stronger than the OLD `==` gate, which
             adopted on seq-count match alone with zero content verification).
          4. Delta-fold: whatever's newly on disk beyond ``loaded.max_sequence`` must fold in as a
             clean contiguous run from the ACTIVE segment (``_fold_active_segment_tail``) — if the
             loaded index is further behind than one active segment (a whole rotated segment sits
             in between, untouched by this scan), the fold detects the gap and this returns None
             (full multi-segment adopt-time fold is deliberately not implemented — the common case
             this fixes is "daemon writes every ~6s to the still-active segment"; anything further
             behind safely falls back to the pre-existing full-rebuild path, never less correct).
        """
        try:
            loaded = self._index_store.load()
        except Exception:  # derived cache: any load failure (torn/corrupt .idx) ⇒ no adopt
            return None
        if loaded is None or loaded.max_sequence > self._next_sequence - 1:
            return None
        if loaded.max_sequence >= 0:
            loaded_tail = loaded.lookup_sequence_range(loaded.max_sequence, loaded.max_sequence)
            if not loaded_tail:
                return None  # pragma: no cover — max_sequence is derived FROM this index's own records
            if self._event_id_at_seq_on_disk(loaded.max_sequence) != loaded_tail[0].event_id:
                return None  # ghost-record guard (gate 3) tripped — don't trust this .idx
        if not self._fold_active_segment_tail(loaded):
            return None  # further behind than one active segment — safe fallback, not adopted
        return loaded

    def _committed_index(self) -> EventIndex:
        """Return the cached subjects[]-derived index over the committed-visible stream.

        T-FND-02 (daemon-choke fix): the index is kept WARM across writes. A cold start (or post-
        truncation) builds it once with a full scan; every subsequent read folds in only the records
        appended to the ACTIVE segment since the last index update (this process's writes + other
        processes' commits), via _catch_up_index. The previous design nulled the index on every write
        → every read rebuilt + re-persisted the whole ledger; the always-on daemon writes a
        DaemonRunCompleted each ~6s loop, so that was a full ~42MB rebuild每轮, growing linearly with
        the ledger — the choke this task fixes.

        Correctness: the catch-up only ever APPENDS committed records with seq > the index's current
        max (it can never drop or duplicate), and falls back to a full rebuild on any anomaly (segment
        rotation since the baseline, or a non-contiguous committed-seq gap), so it is never less
        correct than a full scan. Cross-process reads stay coherent: another process's commits land in
        the active segment and are picked up on the next read — strictly fresher than the old design,
        which only saw外部 writes after this process's own next write nulled the index.

        RUN-055 persistence is unchanged: a full (re)build lazily persists the .idx so a NEW process
        loads it at startup; the warm-path catch-up keeps the in-memory index current without re-
        persisting每轮 (the .idx refreshes on the next full rebuild / process restart).
        """
        if self._index is None:
            self._index = self._build_and_persist_index(list(self._iter_committed_records()))
            self._set_index_baseline()
            return self._index
        self._catch_up_index()
        return self._index

    def _set_index_baseline(self) -> None:
        """Record which active segment + byte size the warm index currently covers (T-FND-02).

        The catch-up compares the live active segment against this to tell "nothing new" (size
        unchanged) from "fold the delta" from "rotation → full rebuild".
        """
        active = active_segment_path(self._log_path)
        self._index_active_seg = active
        self._index_active_size = active.stat().st_size if active.exists() else 0

    def _fold_active_segment_tail(self, index: EventIndex, segment: Path | None = None) -> bool:
        """Fold committed records from ``segment`` (default: the ACTIVE segment) with
        seq > index.max_sequence into ``index`` in place (A2/A3, 2026-07-02 OOM 崩机根治:
        提出的可复用 delta-fold 核心).

        Pure read+fold step, no baseline bookkeeping — usable by the post-write catch-up path
        (``_catch_up_index``, which has its own baseline shortcuts layered on top), by the
        adopt-time path (``__init__`` 采用一份持久化索引时, 还没有 baseline 可比对, 直接拿
        ``index.max_sequence`` 当参照点), and by the rotation-fold path (B-2: folding the tail
        of a just-SEALED segment, passed explicitly via ``segment``, before switching the
        baseline to the new active segment).

        Returns True on success (folded, or nothing new to fold). Returns False when a gap is
        detected — the new records don't start contiguously at max_sequence+1 (e.g. the loaded/
        warm index is further behind than this one segment: a whole other segment sits in
        between and this scan never reads it, so the delta it *does* see starts too high) —
        the caller must fall back to a full rebuild in that case (safe; never less correct than
        a full scan, only sometimes less efficient).
        """
        target = segment if segment is not None else active_segment_path(self._log_path)
        cur_max = index.max_sequence
        new_recs = [
            rec
            for rec in iter_committed_records_from_lines(_iter_segment_lines(target))
            if rec.sequence_number > cur_max
        ]
        if not new_recs:
            return True
        seqs = [r.sequence_number for r in new_recs]  # committed-visible order = sequence order
        if seqs[0] != cur_max + 1 or seqs != list(range(seqs[0], seqs[0] + len(seqs))):
            return False
        index.add_records(new_recs)
        return True

    def _rotation_fold(self, index: EventIndex, new_active: Path) -> bool:
        """B-2 (2026-07-15 轮转索引重建提速): fold across a segment rotation WITHOUT a full
        ledger rescan, by chaining two bounded segment-tail folds instead of one whole-log scan.

        Rationale: a warm index reflects everything through the OLD active segment's last
        catch-up. Rotation only SEALS that segment (it stops changing forever — a sealed segment
        never gets new writes) and opens a new, initially-empty active segment. Nothing about
        already-indexed records becomes stale; only two bounded deltas need folding:
          1. whatever was appended to the now-sealed old segment since the last catch-up
             (usually 0-length: the file-locked write that triggered rotation already landed the
             rotating batch in the NEW segment, per ``_write_target``'s §7.2 invariant — but a
             prior write to the old segment between reads can still leave a real tail here);
          2. whatever's already on disk in the new active segment.

        Both folds reuse ``_fold_active_segment_tail``'s seq-contiguity guard, so a genuinely
        skipped segment (e.g. 2+ rotations happened between reads, leaving a whole sealed segment
        neither ``self._index_active_seg`` nor ``new_active`` names) is detected as a gap and
        rejected — the caller falls back to the pre-existing full-rebuild path in that case,
        never less correct than a full scan, only sometimes less efficient.
        """
        old_active = self._index_active_seg
        if old_active is None or not old_active.exists():
            return False  # no sealed predecessor to fold from (first build, or file vanished)
        if not self._fold_active_segment_tail(index, segment=old_active):
            return False
        return self._fold_active_segment_tail(index, segment=new_active)

    def _catch_up_index(self) -> None:
        """Fold committed records appended since the last index update into the warm index (T-FND-02).

        Reads ONLY bounded segment tails (bounded by segment_max_*), so the cost is independent of
        total ledger size — including across a rotation (B-2). Guards keep it correct and cheap:
          - segment rotated since the baseline → try the bounded two-segment rotation fold
            (``_rotation_fold``: sealed-segment tail + new active segment, both bounded reads);
            only on a detected gap (e.g. 2+ rotations skipped between reads) does it fall back to
            a full rebuild (rare; never less correct than a full scan, only sometimes less
            efficient).
          - no baseline yet (very first catch-up) → full rebuild (nothing to fold from).
          - active segment byte size unchanged → append-only log ⇒ nothing new ⇒ O(1) return.
          - the new committed records must be contiguous from max_sequence+1 (delegated to
            ``_fold_active_segment_tail``); any gap → fall back to a full rebuild.
        """
        index = self._index
        if index is None:  # pragma: no cover — caller guarantees warm; kept for type-narrowing
            return
        active = active_segment_path(self._log_path)
        if self._index_active_seg is None:
            self._index = self._build_and_persist_index(list(self._iter_committed_records()))
            self._set_index_baseline()
            return
        if active != self._index_active_seg:
            if not self._rotation_fold(index, active):
                self._index = self._build_and_persist_index(list(self._iter_committed_records()))
            self._set_index_baseline()
            return
        size = active.stat().st_size if active.exists() else 0
        if size == self._index_active_size:
            return  # append-only: unchanged size ⇒ no new records on disk
        if not self._fold_active_segment_tail(index):
            self._index = self._build_and_persist_index(list(self._iter_committed_records()))
            self._set_index_baseline()
            return
        self._index_active_size = size

    def _build_and_persist_index(self, records: list[EventRecord]) -> EventIndex:
        """Build the committed-stream index once and lazily persist it to .idx (§7.1).

        ``EventLogIndexStore.persist`` itself constructs the EventIndex (over ``records``), writes
        the .idx files, and returns it — so we reuse that single build as the cached index rather
        than constructing a second one (debt-e4b81accda38: the commit gate must add no extra full
        index build; building here + a separate persist build would double it).

        Persist is best-effort: the .idx is a derived cache, not the source of truth (Patch3 §3.2
        "索引不是事实源, event 文件才是"), so an OSError (read-only mount / disk full / a concurrent
        writer racing on the cache files) must not fail the read that triggered the build — we fall
        back to an in-memory-only index (single build) and surface the persist failure as a warning.
        """
        try:
            return self._index_store.persist(records)
        except OSError as exc:
            warnings.warn(
                f"event index .idx persist failed ({exc}); derived cache only — "
                "reads unaffected, next startup will rebuild from event files",
                stacklevel=2,
            )
            return EventIndex(records)

    def committed_index(self) -> EventIndex:
        """Public accessor for the cached committed-stream EventIndex (RUN-038 perf).

        The same memoized index get_event/get_events_by_entity use. Returned over an
        EventIndex(all_records()) rebuild for consumers (e.g. M-0.5 commit gate) that need the
        full index over the current committed snapshot: the cache is invalidated on every write,
        so this is rebuilt once after a write and then reused, instead of a fresh O(n) build per
        call (debt-e4b81accda38). The index is a read-only view over committed records — callers
        must not mutate it; it is replaced (not edited) on the next write.
        """
        return self._committed_index()

    # ─── Read API (M-0.1 §4.1) ──────────────────────────────────────────────

    def get_event(self, event_id: str) -> EventRecord | None:
        """4.1.1 — point lookup by event_id (O(1) via the cached index, not a full scan).

        §6.2 Inv2 / M-0.7 §7.3: an archived event is no longer in the hot index, but get_event still
        returns it — on a hot miss we transparently delegate to the M-0.7 cold-storage lookup, which
        decompresses the segment's cold archive and reconstructs the (field-identical) record. The
        cold module is imported lazily to keep M-0.1 free of an M-0.7 import dependency at load time.
        """
        hit = self._committed_index().lookup_event_id(event_id)
        if hit is not None:
            return hit
        from towow.l0.snapshot.cold_lookup import cold_lookup

        return cold_lookup(self._log_path.parent, event_id)

    def get_events_by_entity(self, entity_type: str, entity_id: str) -> list[EventRecord]:
        """4.1 (Patch3 §3.1-§3.2) — events whose subjects[] reference (entity_type, entity_id).

        The subjects[] unified-index Read surface: resolves via the entity index built from each
        record's subjects[], so a point query on "what touched concept X" is an O(1) dict hit, not
        a 59-schema-aware full scan.
        """
        return self._committed_index().lookup_entity(entity_type, entity_id)

    def get_events_in_range(self, start_seq: int, end_seq: int) -> list[EventRecord]:
        """4.1.2 — [start_seq, end_seq] inclusive range query (the rebuild/catchup read path).

        §6.2 Inv2 (range form) / M-0.7 §7.3: like get_event, a range query must transparently include
        events that 段内 compaction moved from hot to cold — otherwise a full rebuild()/recompute_one()
        over a compacted log would silently drop the moved events' projection contribution (the 可重建
        guarantee). has_cold_events() gates the cold merge so a never-compacted log (no cold/ dir) pays
        only a single stat — the hot read path stays cold-free.

        T-RMD-SEQ-GUARD 段③ (finding f-sub-l0-seq-uniqueness-blindspot, GC-04 critical): the hot slice
        is sourced from the dup-seq-PRESERVING committed record list (_committed_index().records()),
        NOT lookup_sequence_range. _by_sequence is last-wins per seq, so the 553 dup-seqs the 06-10
        rollbacks left in the base segment survive there as ONE record each → every projection rebuild
        (catchup / recompute_one both call this) silently DROPPED the others (concept_graph −9 / task_graph
        −14 / finding_lifecycle −1, all with live downstream references). An event's identity is its
        event_id, never its seq: distinct events that wrongly share a seq are ALL real and must be kept;
        only a genuinely duplicated event_id (the same event) is collapsed. So both the hot list and the
        cold+hot merge dedup by event_id (hot wins on a recovery-window overlap), never by seq. A healthy
        ledger has one event_id per seq, so this is behaviour-identical there; only a polluted ledger
        changes — it now returns the full truth instead of a lossy projection.

        NOTE (supersedes RUN-095's seq-dedup framing): recompute_one's docstring + the live commit_history
        "885 vs 825" story described the seq-dedup as canonical. That dedup was conflating distinct-event_id
        collisions (real events, must keep) with same-event_id double-application (must collapse). event_id
        dedup is the correct identity rule for both; the seq-collision count therefore reflects every
        distinct event. See SPEC-CONFLICT-LEDGER (T-RMD-SEQ-GUARD vs RUN-095) + the FindingCreated this
        task emits.
        """
        # f-perf2-catchup-full-materialize-for-narrow-seq-range: records_in_range pre-filters by
        # sequence_number BEFORE materializing (LazyEventIndex: cheap int comparisons over an
        # already-in-memory parallel array; no pydantic parse for records outside [start_seq,
        # end_seq]) — same dup-seq-preserving contract as the old records()+filter, at O(matching
        # records) parse cost instead of O(全账本). See LazyEventIndex.records_in_range's docstring.
        hot = self._committed_index().records_in_range(start_seq, end_seq)
        from towow.l0.snapshot.cold_lookup import cold_records_in_range, has_cold_events

        if not has_cold_events(self._log_path.parent):
            return hot
        by_event_id: dict[str, EventRecord] = {
            rec.event_id: rec
            for rec in cold_records_in_range(self._log_path.parent, start_seq, end_seq)
        }
        for rec in hot:  # hot overrides cold on a genuine (recovery-window) same-event_id overlap
            by_event_id[rec.event_id] = rec
        return sorted(by_event_id.values(), key=lambda r: r.sequence_number)

    def get_events_by_category(
        self,
        category: EventCategory,
        *,
        since_seq: int | None = None,
        limit: int | None = None,
    ) -> list[EventRecord]:
        """4.1.3 — filter by event_category, optional since_seq + limit (RUN-055: via category index).

        The category postings are in committed (sequence) order, so applying since_seq / limit over
        them yields the same result the old O(n) full scan did — only now it skips records of every
        other category instead of walking the entire committed stream per call.
        """
        results: list[EventRecord] = []
        for rec in self._committed_index().lookup_category(category.value):
            if since_seq is not None and rec.sequence_number <= since_seq:
                continue
            results.append(rec)
            if limit is not None and len(results) >= limit:
                break
        return results

    def get_events_by_type(
        self,
        event_type: EventType,
        *,
        since_seq: int | None = None,
        limit: int | None = None,
    ) -> list[EventRecord]:
        """4.1.4 — filter by event_type (RUN-055: via type index)."""
        results: list[EventRecord] = []
        for rec in self._committed_index().lookup_type(event_type.value):
            if since_seq is not None and rec.sequence_number <= since_seq:
                continue
            results.append(rec)
            if limit is not None and len(results) >= limit:
                break
        return results

    def scan_committed_records_of_type(self, event_type: EventType) -> list[EventRecord]:
        """Index-FREE type query — reads the event files themselves, never the derived .idx cache.

        f-fix-complete-gate-ignores-amended-closure-contract: ``get_events_by_type`` answers from the
        persisted index, which is a derived cache built by whichever process last rebuilt it. A reader
        running code that predates an event type (a long-lived daemon started before the enum member
        landed; a sibling checkout) parses such a record as malformed and silently drops it
        (``iter_committed_records_from_lines``'s truncated-tail-line branch), and the index it then
        persists is missing those records for every later reader — 实证: 本账本三条
        FindingClosureContractAmended (seq 69696/70245/70417, 均已 CommitAccepted, 全量扫描扫得到)
        在 event_id.idx 的 70441 行里一条都没有, 而它们的 sentinel 都在。So a governance gate that
        BLOCKS on the answer must not trust that cache: ``get_events_by_type`` returning empty cannot
        distinguish "no such event" from "the cache can't see it", and the failure is silent and
        direction-wrong (读路径失明 会被记成 修复者没修好).

        Same result set as ``get_events_by_type`` on a healthy ledger (identical committed-visibility
        gate, hot + cold), only sourced from the truth (§3.2 索引不是事实源, event 文件才是). Cost is
        O(bytes) with a pydantic parse only on the byte-prefiltered candidates, so it is for the rare
        fail-closed query — NOT a general replacement for the indexed read path.
        """
        by_id: dict[str, EventRecord] = {}
        for rec in iter_committed_records_from_lines(
            lines_possibly_of_type(iter_raw_event_lines(self._log_path), event_type.value),
        ):
            if rec.event_type is event_type:
                by_id[rec.event_id] = rec
        # 段内 compaction 会把 hot 段整体搬进冷归档 — 一条针对老 finding 的 amend 可能已只存在于冷区,
        # 只扫 hot 就会把它读成"没有 amend"(同一个静默失明, 换个位置). 与 get_events_in_range 同款
        # has_cold_events 门: 没冷区的账本只付一次 stat。
        from towow.l0.snapshot.cold_lookup import cold_records_of_type, has_cold_events

        if has_cold_events(self._log_path.parent):
            for rec in cold_records_of_type(self._log_path.parent, event_type):
                by_id.setdefault(rec.event_id, rec)  # hot wins on a recovery-window overlap
        return sorted(by_id.values(), key=lambda r: r.sequence_number)

    def get_events_by_types_or_stub_kinds(
        self, event_types: frozenset[EventType],
    ) -> list[EventRecord]:
        """Records logically typed as one of ``event_types`` — flat OR NodeTouched stub-rewrap.

        f-perf2-vitality-full-materialize-for-narrow-type-scan: for a caller that only needs a
        handful of event types out of the whole committed stream (e.g. session_vitality's work-
        product classification), this is the O(matching records) alternative to
        ``committed_index().records()`` (O(全账本) — pays a full pydantic parse of every committed
        record just to discard almost all of them). Delegates to the index's
        ``lookup_types_or_stub_kinds`` — LazyEventIndex overrides it with a byte-level pre-filter
        that skips the parse for non-matching records (see its docstring); the eager EventIndex
        already holds every record parsed, so there it's a plain filter. Either way the result is
        the same set records() + unwrap would give.
        """
        return self._committed_index().lookup_types_or_stub_kinds(
            frozenset(et.value for et in event_types),
        )

    def get_events_by_actor(
        self,
        actor_id: str,
        *,
        since_seq: int | None = None,
    ) -> list[EventRecord]:
        """4.1.5 — filter by provenance.actor_id (RUN-055: via actor index)."""
        return [
            rec
            for rec in self._committed_index().lookup_actor(actor_id)
            if since_seq is None or rec.sequence_number > since_seq
        ]

    def get_events_by_task(self, task_id: str) -> list[EventRecord]:
        """4.1.5 — filter by provenance.task_id (RUN-055: via task index)."""
        return self._committed_index().lookup_task(task_id)

    def get_events_by_run(self, run_id: str) -> list[EventRecord]:
        """4.1.5 — filter by provenance.run_id (RUN-055: via run index)."""
        return self._committed_index().lookup_run(run_id)

    def get_causation_chain(
        self,
        event_id: str,
        *,
        depth: int | None = None,
    ) -> list[EventRecord]:
        """4.1.6 — traverse causation_event_id upward."""
        chain: list[EventRecord] = []
        current_id: str | None = event_id
        while current_id is not None:
            current = self.get_event(current_id)
            if current is None:
                break
            chain.append(current)
            if depth is not None and len(chain) >= depth:
                break
            current_id = current.provenance.causation_event_id
        return chain

    def get_correlated_events(self, correlation_id: str) -> list[EventRecord]:
        """4.1.7 — events sharing correlation_id (RUN-055: via correlation index)."""
        return self._committed_index().lookup_correlation(correlation_id)

    def get_event_by_input_hash(
        self,
        resolver_id: str,
        input_hash: str,
    ) -> EventRecord | None:
        """4.1.8 — input_hash lookup, only ResolverDecisionMade (O(1) via index, §4.2 <20ms).

        Routes through the (resolver_id, input_hash) index, which is only populated from
        ResolverDecisionMade records (Patch3 §3.2 input_hash.idx "仅 ResolverDecisionMade"), so the
        type restriction is structural — a non-resolver event can never appear under this key.
        """
        return self._committed_index().lookup_input_hash(resolver_id, input_hash)

    def get_supersede_chain(self, event_id: str) -> list[EventRecord]:
        """4.1.9 — traverse supersede.superseded_event_id chain."""
        chain: list[EventRecord] = []
        current_id: str | None = event_id
        while current_id is not None:
            current = self.get_event(current_id)
            if current is None:
                break
            chain.append(current)
            current_id = current.supersede.superseded_event_id
        return chain

    # ─── visibility iterator ────────────────────────────────────────────────

    def _iter_committed_records(self) -> Iterator[EventRecord]:
        """Yield only sentinel-terminated path-A records + all path-B records.

        Patch 2 §2.1: domain events between envelope and sentinel are only visible when
        their sentinel is present. We implement this by buffering path-A batch records and
        emitting them only after the trailing sentinel is observed.

        §7.1/§7.2: iterates every segment (base events.log + rotated events/hot/*.jsonl) in read
        order as one continuous logical stream. The path-A buffer persists across the per-segment
        file boundary (a batch never straddles a boundary by the rotation invariant, but keeping
        one buffer is both correct and order-preserving regardless).

        The per-line committed/visibility + schema-version gating is shared with the cold-range
        reader via iter_committed_records_from_lines (so hot and cold reconstruct records identically).
        """
        yield from iter_committed_records_from_lines(iter_raw_event_lines(self._log_path))

    # ─── recovery (Patch 2 §2.2) ────────────────────────────────────────────

    def recover(self) -> int:
        """Truncate trailing un-sentineled path-A records.

        Scans backward and keeps only lines up through the last sentinel-terminated batch
        (or path-B record). Returns number of bytes truncated.

        §7.1: acts on the *active* (last) segment — a crash mid-batch leaves the un-sentineled
        tail in whatever segment was being written, i.e. the active one. Earlier (already-rotated)
        segments are immutable: a batch never straddles a segment boundary, so a sealed segment
        always ends on a sentinel/path-B record and has nothing to truncate.
        """
        active = active_segment_path(self._log_path)
        if not active.exists():
            return 0
        with self._mutex, self._file_lock():
            content = active.read_bytes()
            if not content:
                return 0
            lines = content.split(b"\n")
            # Walk lines and track last "safe-end" byte offset (end of completed batch or path B).
            safe_end = 0  # bytes consumed including trailing newline
            cursor = 0
            path_a_pending: dict[str, int] = {}  # batch_id -> end byte after pending lines
            for line in lines:
                line_len = len(line) + 1  # +1 for the \n
                if not line:
                    cursor += line_len
                    continue
                try:
                    rec = EventRecord.model_validate_json(line.decode("utf-8"))
                except Exception:
                    cursor += line_len
                    break
                end = cursor + line_len
                if rec.batch_id is None:
                    safe_end = end
                    path_a_pending.clear()
                else:
                    path_a_pending[rec.batch_id] = end
                    if rec.event_type in _SENTINEL_TYPES:
                        safe_end = end
                        path_a_pending.clear()
                cursor = end
            if safe_end == len(content):
                return 0
            truncated = len(content) - safe_end
            active.write_bytes(content[:safe_end])
            # T-RMD-SEQ-GUARD 段①: re-derive all three lifecycle-state sentinels after truncation
            # (A1: single cheap pass, same as __init__ — see _derive_lifecycle_state).
            self._next_sequence, self._event_id_set, self._sequence_set = self._derive_lifecycle_state()
            # Truncation REMOVES records, so the catch-up (which only ever appends) can't repair the
            # warm index — null it + reset the baseline so the next read does a clean full rebuild.
            self._index = None  # RUN-038: invalidate point-lookup index after truncation
            self._index_active_seg = None
            self._index_active_size = -1
            return truncated

    # ─── helpers exposed to tests / cursor ──────────────────────────────────

    def all_records(self) -> list[EventRecord]:
        """Snapshot of committed records (test/debug; consumers should use 9 Read APIs)."""
        return list(self._iter_committed_records())

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def log_path(self) -> Path:
        return self._log_path


# Path A batch builder helpers (E.1.1 LEDGER Conflict 4/6/7 — envelope NOT in batch).


def build_accepted_batch_e11(
    batch_id: str,
    transaction_id: str,
    domain_records: list[EventRecord],
    sentinel_record: EventRecord,
) -> TransactionBatch:
    """E.1.1-canonical accepted batch composer.

    Composition: [domain events..., CommitAccepted sentinel].
    Envelope is NOT in batch (path B; referenced via sentinel.envelope_event_id).
    """
    return TransactionBatch(
        batch_id=batch_id,
        transaction_id=transaction_id,
        status=BatchStatus.COMMITTED,
        events=[*domain_records, sentinel_record],
    )


def build_rejected_batch_e11(
    batch_id: str,
    transaction_id: str,
    gate_lifecycle_records: list[EventRecord],
    sentinel_record: EventRecord,
) -> TransactionBatch:
    """E.1.1-canonical rejected batch composer.

    Composition: [gate-detected lifecycle events..., CommitRejected sentinel].
    Per M-0.5 §6.4 + §11.x: gate-lifecycle events (ObligationViolated / LockReleased)
    allowed; no agent-proposed domain events; envelope NOT in batch.
    """
    return TransactionBatch(
        batch_id=batch_id,
        transaction_id=transaction_id,
        status=BatchStatus.REJECTED,
        events=[*gate_lifecycle_records, sentinel_record],
    )


# Legacy names kept for tests not yet migrated (Tests will be updated in E.1.1 Regression step).
# E.2+ callers should use _e11 variants directly.

def build_accepted_batch(
    batch_id: str,
    transaction_id: str,
    envelope_record: EventRecord,  # ignored — kept for back-compat signature
    domain_records: list[EventRecord],
    sentinel_record: EventRecord,
) -> TransactionBatch:
    """Legacy signature; envelope_record parameter is now ignored.

    E.1.1: envelope is on path B and never in batch. Prefer build_accepted_batch_e11().
    """
    _ = envelope_record  # ignored under E.1.1 canonical
    return build_accepted_batch_e11(
        batch_id=batch_id,
        transaction_id=transaction_id,
        domain_records=domain_records,
        sentinel_record=sentinel_record,
    )


def build_rejected_batch(
    batch_id: str,
    transaction_id: str,
    envelope_record: EventRecord,  # ignored — kept for back-compat signature
    gate_lifecycle_records: list[EventRecord],
    sentinel_record: EventRecord,
) -> TransactionBatch:
    """Legacy signature; envelope_record parameter is now ignored.

    E.1.1: envelope is on path B and never in batch. Prefer build_rejected_batch_e11().
    """
    _ = envelope_record  # ignored under E.1.1 canonical
    return build_rejected_batch_e11(
        batch_id=batch_id,
        transaction_id=transaction_id,
        gate_lifecycle_records=gate_lifecycle_records,
        sentinel_record=sentinel_record,
    )
