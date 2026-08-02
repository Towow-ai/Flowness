"""M-0.7 §3 garbage collection — archive-seq watermark + retention sweep (RUN-027 block 5).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §3.1 double-filter: archivable ⟺ seq ≤ retention_watermark AND GC-unreachable
#     §3.2 archive_seq_bound = min(consumer_watermark, hot_replay_floor)
#     §4.1 three-layer action: immutable_truth → never; discardable_noise → drop-with-tombstone;
#          abstractable_process → digest (via consolidation)
#     §5.2 retention: keep last 5 snapshots; older snapshot files move to cold
#
# Why (E-DEBT-PAYOFF-ROADMAP §1 block-5): gc was a pure stub ("0 archives expired"). This
# makes "能按规矩清理过期记录" real for the two safe, bounded actions:
#   (1) snapshot retention sweep (§5.2) — concrete physical cleanup, rule-based.
#   (2) archive-seq watermark (§3.2) + archivable discardable_noise analysis (§3.1 + §4.1) —
#       a real GC report (what the ordering filter says is collectable).
# The destructive event-log cold-storage segment archival (§7 3-phase prepare/commit/finalize)
# + abstractable digest (via the consolidation run, §6) stay scoped — they rewrite the hot
# log and are tied to the consolidation/maintenance-daemon machinery (E.5).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.event_log.cursor import CursorStore
from towow.l0.event_log.lease import RetentionLeaseStore
from towow.l0.snapshot.reachability import compute_gc_reachable, compute_gc_roots
from towow.l0.snapshot.retention_policy import (
    load_active_retention_policy,
    resolve_retention_action,
)
from towow.l0.snapshot.snapshot import enforce_retention, list_snapshot_dirs
from towow.l0.snapshot.state import push_retention_watermark, write_gc_state
from towow.schemas.enums import EventType, RetentionAction

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.projection.projection import ProjectionStore


@dataclass(frozen=True)
class GcResult:
    """Outcome of a gc run."""

    archive_seq_bound: int
    consumer_watermark: int
    hot_replay_floor: int
    archivable_noise_seqs: list[int] = field(default_factory=list)
    archived_snapshot_dirs: list[str] = field(default_factory=list)
    # §3.1 second layer: seqs that retention_action + bound (Layer 1) would have admitted as
    # archivable, but the GC reachability (semantic) layer withheld because they are still
    # referenced by a live object / lease / supersede chain. Observability of the second filter.
    gc_reachable_protected_seqs: list[int] = field(default_factory=list)
    # finding f-r12-4-dropfile-orphan-leak-in-repo-tree: 第 4 态 orphan verdict drop-file 被本次清扫
    # 回收的文件名 (.towow/tmp/fork-verdict/ 下), 让 maintenance run 可观测 GC 这条卫生路径做了什么。
    orphan_fork_verdict_dropfiles_swept: list[str] = field(default_factory=list)


# ── finding f-r12-4-dropfile-orphan-leak-in-repo-tree: fork-verdict orphan drop-file 周期清扫 ──
# 必须 > fork poll 超时 (l1.verification_fork.DEFAULT_TIMEOUT_S=900s): drop-file 仅在其 parent 仍
# 轮询时被等待, parent 至多轮询 timeout_s 即 fail-closed。故"mtime 年龄 > 此阈值 ⇒ 已无在跑的 parent
# 等它" 是清扫的并发安全条件 —— 在跑的 fork-gate 周期 (drop-file mtime 必在阈值内) 绝不被误删, 只删
# 确属 orphan 的陈旧文件。4× 默认超时留足余量 (env TOWOW_VERIFICATION_FORK_TIMEOUT_S 调高超时时,
# ops 同步调高本阈值即可; 漏调最坏只是少清一轮, 下轮 GC 仍会捕获, 不破坏正确性)。
ORPHAN_FORK_VERDICT_MAX_AGE_S = 3600.0


def sweep_orphan_fork_verdict_dropfiles(
    towow_dir: Path,
    *,
    max_age_s: float = ORPHAN_FORK_VERDICT_MAX_AGE_S,
    now: float | None = None,
) -> list[str]:
    """清扫 ``.towow/tmp/fork-verdict/`` 里的孤儿 verdict drop-file (M-2.2 GC 卫生路径)。

    背景 (finding f-r12-4-dropfile-orphan-leak-in-repo-tree): BG_POLLED gate fork 的 drop-file 回收
    靠 l2.claude_bg_helper.run_bg_polled_gate_fork 的 try/finally unlink, 覆盖『解析成功 / 超时 /
    fail-closed』三个主进程态。但有隐式第 4 态: parent 轮询超时 fail-closed 时 finally unlink 跑在 fork
    落盘**之前** (no-op), 之后慢 detached fork 才把 verdict 写进 ``<repo>/.towow/tmp/fork-verdict/
    <...>-<uuid>.json`` → orphan 永留 repo 工作树 (不像 parent _default_runner 泄漏到系统 /tmp 可被 OS
    回收)。对正确性无害 (uuid 段保证下一实例永不误读、anti-fail-open 保持), 但工作树会无界累积。本扫是
    fork-verdict-dropfile-convention@v1 三态 unlink 的**补充兜底**, 不替代它、不碰任何 anti-fail-open 不
    变量。目录经 l1.verification_fork.fork_verdict_dir 取 (与落盘口共用同一 SSOT, 不漂移)。

    best-effort: 目录不存在 → 空; 单文件 stat/unlink 出错 (权限 / 并发双删) → 跳过不抛, 不让卫生清扫
    crash 整个 maintenance run。并发安全见 ORPHAN_FORK_VERDICT_MAX_AGE_S 注释。
    """
    from towow.l1.verification_fork import fork_verdict_dir

    base = fork_verdict_dir(towow_dir.parent)
    if not base.exists():
        return []
    current = time.time() if now is None else now
    swept: list[str] = []
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        try:
            age = current - entry.stat().st_mtime
        except OSError:
            continue
        if age < max_age_s:
            continue  # 可能仍有在跑的 parent 在等它 — 不碰
        try:
            entry.unlink(missing_ok=True)
        except OSError:
            continue  # 并发双删 / 权限 — best-effort, 下轮 GC 再试
        swept.append(entry.name)
    return sorted(swept)


def _retained_snapshot_offsets(event_log: EventLog) -> list[int]:
    """event_offset of every hot SnapshotCreated event."""
    offsets: list[int] = []
    for rec in event_log.get_events_by_type(EventType.SNAPSHOT_CREATED):
        off = rec.payload.get("event_offset")
        if isinstance(off, int):
            offsets.append(off)
    return offsets


def _lease_watermark_floor(
    lease_store: RetentionLeaseStore,
    event_log: EventLog,
    *,
    now: datetime | None = None,
) -> int | None:
    """T-L0-20 (波1): 活跃 retention lease 给 watermark 的上限贡献 (= 最低保护 seq - 1); 无则 None。

    min_seq lease (active_task) 保护 seq≥min_seq → 贡献 min_seq-1。event_ids lease
    (capsule_reconstruction) 保护那些 event → 解析其 seq, 贡献 min(seq)-1。两类都让被引用段不归档。
    input_hashes lease (resolver_cache) 走 §3.3 GC 语义根集 (未实现) 不进 watermark。-1 是为对齐本仓
    "seq ≤ bound 即可归档"惯例 (见 lease.py docstring 边界校准)。
    """
    protected_seqs: list[int] = []
    seq_by_id: dict[str, int] | None = None
    for lease in lease_store.active_leases(now=now):
        if lease.min_seq is not None:
            protected_seqs.append(lease.min_seq)
        if lease.event_ids:
            if seq_by_id is None:
                seq_by_id = {rec.event_id: rec.sequence_number for rec in event_log.all_records()}
            protected_seqs.extend(seq_by_id[eid] for eid in lease.event_ids if eid in seq_by_id)
    if not protected_seqs:
        return None
    return min(protected_seqs) - 1


def compute_archive_seq_bound(
    event_log: EventLog,
    projection_store: ProjectionStore,
    lease_store: RetentionLeaseStore | None = None,
    *,
    cursor_store: CursorStore | None = None,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    """M-0.7 §3.2 — (archive_seq_bound, consumer_watermark, hot_replay_floor).

    Layer 1 consumer_watermark: the projection has durably consumed up to cursor-1. Per M-0.1a
    Patch 4 §4.2 ``retention_watermark = min(last_durable_processed_seq of all active consumers)``
    — so when a CursorStore is supplied, the watermark is further floored by the *minimum* durable
    seq across **every** registered consumer (RUN-038 Cap5: a lagging consumer must not have its
    not-yet-processed discardable_noise dropped — "discardable can be dropped only after ALL
    interested consumers have advanced beyond it"). The projection is one such consumer; a
    drift_detector / capsule re-evaluator that lags caps the bound at its own durable seq.

    Then floored by active M-0.3 retention leases (Patch 5 / T-L0-20) so lease-referenced segments
    stay un-archivable. Layer 2 hot_replay_floor: the oldest retained snapshot's event_offset must
    stay hot-replayable; with no snapshot yet the floor is 0 (nothing archivable — conservative).
    bound = min of the two layers.
    """
    consumer_watermark = max(0, projection_store.cursor - 1)
    if cursor_store is not None:
        # Patch4 §4.2 — min over ALL registered consumers (each declares durable seq it processed).
        registered = cursor_store.retention_watermark_with_default(consumer_watermark)
        consumer_watermark = max(0, min(consumer_watermark, registered))
    if lease_store is not None:
        floor = _lease_watermark_floor(lease_store, event_log, now=now)
        if floor is not None:
            consumer_watermark = max(0, min(consumer_watermark, floor))
    offsets = _retained_snapshot_offsets(event_log)
    hot_replay_floor = min(offsets) if offsets else 0
    return min(consumer_watermark, hot_replay_floor), consumer_watermark, hot_replay_floor


def run_gc(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    retention: int | None = None,
) -> GcResult:
    """Run the bounded GC: snapshot retention sweep + archive-seq analysis.

    A seq is reported archivable only when BOTH M-0.7 §3.1 filters agree: Layer 1 (the ordering
    filter, archive_seq_bound — seq ≤ bound AND retention_action says discardable) AND Layer 2
    (the semantic filter — the event is GC-UNreachable, §3.3/§3.4). An event Layer 1 would admit
    but that is still GC-reachable (lease / projection / supersede-chain referenced) is withheld
    and recorded in gc_reachable_protected_seqs — this is the second filter doing its job (without
    it, dropping a still-referenced event is a data-loss bug). The actual hot-log removal is §7
    cold storage (scoped) — this run physically cleans snapshot files per §5.2 and reports the
    double-filtered collectable noise.
    """
    projection_store.catchup(event_log)
    # T-L0-20 (波1): 把活跃 retention lease 纳入 archive_seq_bound 的 Layer 1 (被 lease 引用段不归档)。
    lease_store = RetentionLeaseStore(towow_dir / "events" / "leases")
    # RUN-038 Cap5 (Patch4 §4.2): 把所有已注册 consumer cursor 纳入 Layer 1 — 任一 consumer 落后,
    # 它尚未 durable 处理的 discardable_noise 不可被丢 ("ALL interested consumers 越过才可丢")。
    cursor_store = CursorStore(towow_dir / "events" / "cursors")
    bound, consumer_watermark, hot_replay_floor = compute_archive_seq_bound(
        event_log,
        projection_store,
        lease_store,
        cursor_store=cursor_store,
    )
    # RUN-038 Cap3 (§3.1 Layer 2): GC 语义可达性闭包 — 即使 retention 说可丢、即使 seq ≤ bound,
    # 只要事件被活对象/projection/supersede 链/lease 引用 (GC 可达) 就保护 (不进 archivable)。
    # 两层互相独立 (§3.1): Layer 1 看"顺序消费者越过没", Layer 2 看"还有活引用没"。
    gc_roots = compute_gc_roots(event_log, projection_store, lease_store)
    gc_reachable = compute_gc_reachable(event_log, gc_roots)
    # T-L0-36 (波1): GC 按 retention policy 执行 — 每条 record 解析 retention_action (活跃 policy
    # 无则 §4 矩阵默认), 仅 discard_after_consumers_advance 的可丢。默认下 = discardable_noise 可丢、
    # immutable_truth 永不丢 (= 旧行为); policy 变更后 GC 据新 policy 执行。
    archivable: list[int] = []
    gc_protected: list[int] = []
    if bound > 0:
        policy_rules = load_active_retention_policy(event_log)
        for rec in event_log.all_records():
            if rec.sequence_number > bound:
                continue
            action = resolve_retention_action(rec.base_classification, policy_rules)
            if action is not RetentionAction.DISCARD_AFTER_CONSUMERS_ADVANCE:
                continue
            # Layer 1 (bound + retention) admitted it — now apply Layer 2 (§3.1 semantic filter).
            if rec.event_id in gc_reachable:
                gc_protected.append(rec.sequence_number)  # still referenced → withhold (no drop)
            else:
                archivable.append(rec.sequence_number)
    archived_dirs = enforce_retention(towow_dir, retention=retention)
    # finding f-r12-4: 独立卫生步 (不依赖 archive bound — orphan 清理与归档语义无关)。清掉超时后慢
    # detached fork 落盘、caller finally unlink 已错过的 orphan verdict drop-file。best-effort, 不抛。
    orphan_swept = sweep_orphan_fork_verdict_dropfiles(towow_dir)
    # T-L0-35 (波1): push retention_watermark + 记 GC run 到 snapshot-module 状态文件
    # (watermark 真跨 GC/consumer 传递 — consumer cursor 进 → 本次 push 的 consumer_watermark 随之进)。
    push_retention_watermark(
        towow_dir,
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
    )
    write_gc_state(
        towow_dir,
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
    )
    return GcResult(
        archive_seq_bound=bound,
        consumer_watermark=consumer_watermark,
        hot_replay_floor=hot_replay_floor,
        archivable_noise_seqs=sorted(archivable),
        archived_snapshot_dirs=archived_dirs,
        gc_reachable_protected_seqs=sorted(gc_protected),
        orphan_fork_verdict_dropfiles_swept=orphan_swept,
    )


__all__ = [
    "ORPHAN_FORK_VERDICT_MAX_AGE_S",
    "GcResult",
    "compute_archive_seq_bound",
    "list_snapshot_dirs",
    "run_gc",
    "sweep_orphan_fork_verdict_dropfiles",
]
