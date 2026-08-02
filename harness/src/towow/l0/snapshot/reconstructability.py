"""M-0.7 §6.5 Reconstructability 抽样验证 (Invariant 1 的 5% 全量 replay 抽样 + audit 兜底).

# spec source:
#   00-original-inputs/v3-design-bundle/05-l0-detailed-designs/
#     M-0.7-snapshot-consolidation-detailed-design.md
#     §6.5 Reconstructability 抽样验证策略 (Invariant 1) — v3 初版策略:
#       - 5% snapshot 全量 replay 验证: "随机抽取 snapshot.event_offset 之后的所有 committed
#         event 全量 replay; 断言重建的 projection state_hash == 当前 projection state_hash"
#       - 95% snapshot 信任 schema 物理保证 (append-only / immutable / reducer 纯函数)
#       - 抽样命中失败 → SnapshotSuperseded + Finding(reconstructability_failed) + audit fork
#     §6.5 audit 兜底 audit_consolidation_reconstructability(all_match / mismatched_projections)
#
# 与 consolidation_verify._verify_reconstructability 的关系 (诚实边界):
#   consolidation_verify 的 inv1 是 gate-callable 的 *结构* 检查 (snapshot 存在 + 完整, 重建锚点
#   存在), commit gate 单条 envelope 校验入口。本模块是 *真 5% 全量 replay 抽样* —— 跨所有历史
#   snapshot 抽 5% 真重建并逐 projection 比对当前 head。这正是 §6.5 标 "需 RUN cutoff projection
#   bundle (driver-gated)" 的那层覆盖, 由 maintenance/同步命令驱动 (非 daemon)。
#
# DAEMON 边界 (RUN-070 红线):
#   本模块只建 *库层验证逻辑* + 由同步命令 (towow reconstructability-audit) 驱动。
#   "daemon 自动定期巡检" (cron/常驻) = OFF 开关 (config.reconstructability_auto_audit_enabled,
#   default False) + 挂债, 本 run 绝不启动 daemon / 不 resume / 不动 watermark。
#
# 重建语义 (为什么这是真检查不是壳):
#   一个合法的 reconstructability 锚点 snapshot S (cutoff_seq=k) 必须满足:
#     restore(S.bundle) + replay(events > k) == full_rebuild(events 0..head)
#   即 "从 snapshot 起重放向前" 与 "从零全量重放" 落到同一 head state。两者都在临时 scratch
#   projection store 算 (绝不污染 live graph), 逐 projection canonical 比对。任一 projection
#   不符 → 该 snapshot 重建失败 (fail-closed, 非 vacuous pass); 篡改/损坏 bundle →
#   SnapshotIntegrityError → 该 snapshot 判失败。
"""

from __future__ import annotations

import hashlib
import math
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from towow.l0.projection.projection import (
    _PROJECTION_FILES,
    ProjectionStore,
    _canonical_json,
)
from towow.l0.snapshot.snapshot import (
    SnapshotIntegrityError,
    list_archived_snapshot_dirs,
    list_snapshot_dirs,
    restore_from_snapshot,
)
from towow.l0.snapshot.state import load_config

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog

# §6.5 v3 初版抽样率 (5%). config.sampling_rate 默认 1.0 (全验, 最保守); 显式传 0.05 走 §6.5 轻量。
SPEC_SAMPLING_RATE = 0.05


@dataclass(frozen=True)
class SnapshotReconstructResult:
    """单个 snapshot 的重建一致性结果 (§6.5 audit 兜底 all_match / mismatched_projections)。"""

    snapshot_dir: str
    cutoff_seq: int
    reconstructable: bool
    mismatched_projections: tuple[str, ...] = ()
    reconstructed_hash: str = ""
    head_hash: str = ""
    detail: str = ""


@dataclass(frozen=True)
class ReconstructabilitySamplingResult:
    """§6.5 抽样验证整体结果。

    ``all_passed`` 为 True 当且仅当所有被抽中的 snapshot 都重建一致。
    sampled_count==0 (无 snapshot) → all_passed=True 但 sampled_count=0 (诚实: 无可验对象,
    非假装验过)。failed 列出重建失败的 snapshot 结果 (caller 据此产 SnapshotSuperseded + Finding)。
    """

    sampling_rate: float
    total_snapshots: int
    sampled_count: int
    head_hash: str
    results: tuple[SnapshotReconstructResult, ...] = ()
    all_passed: bool = True

    @property
    def failed(self) -> tuple[SnapshotReconstructResult, ...]:
        return tuple(r for r in self.results if not r.reconstructable)


def _state_hash(store: ProjectionStore) -> tuple[str, dict[str, str]]:
    """对 store 当前所有 reducer-managed projection 算 canonical state_hash + per-projection map。

    返回 (bundle_state_hash, {name: per_projection_canonical_hash})。canonical 序列化 (sort_keys)
    消除 key 顺序/格式抖动; 只覆盖 _PROJECTION_FILES (reducer 驱动的), 不含非 reducer 文件。
    """
    per: dict[str, str] = {}
    for name in sorted(_PROJECTION_FILES):
        canon = _canonical_json(store.read(name))
        per[name] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    bundle = hashlib.sha256(
        _canonical_json(per).encode("utf-8"),
    ).hexdigest()
    return "sha256:" + bundle, per


def _full_rebuild_head_hash(event_log: EventLog, work_root: Path) -> tuple[str, dict[str, str]]:
    """从零全量重放整个 event log 到一个临时 scratch store, 算 head state_hash (权威 head)。"""
    head_dir = work_root / "head-graph"
    head_dir.mkdir(parents=True, exist_ok=True)
    head_store = ProjectionStore(head_dir)
    head_store.rebuild(event_log)
    return _state_hash(head_store)


def _reconstruct_from_snapshot(
    event_log: EventLog,
    towow_dir: Path,
    snapshot_dir: Path,
    work_root: Path,
    head_hash: str,
    head_per: dict[str, str],
) -> SnapshotReconstructResult:
    """对单个 snapshot: restore(bundle) + replay(events>cutoff) 到 scratch store, 比对 head。"""
    cutoff_seq = -1
    try:
        cutoff_seq = int(snapshot_dir.name.removeprefix("snapshot-"))
    except ValueError:
        cutoff_seq = -1

    scratch_dir = work_root / f"recon-{snapshot_dir.name}"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch = ProjectionStore(scratch_dir)
    try:
        restore = restore_from_snapshot(
            event_log=event_log,
            projection_store=scratch,
            towow_dir=towow_dir,
            snapshot_dir=snapshot_dir,
        )
        cutoff_seq = restore.cutoff_seq
    except SnapshotIntegrityError as exc:
        # 篡改/损坏 bundle → fail-closed: 该 snapshot 重建失败 (绝不静默当过)。
        return SnapshotReconstructResult(
            snapshot_dir=snapshot_dir.name,
            cutoff_seq=cutoff_seq,
            reconstructable=False,
            head_hash=head_hash,
            detail=f"snapshot bundle 完整性失败 (fail-closed): {exc}",
        )

    recon_hash, recon_per = _state_hash(scratch)
    mismatched = tuple(
        name for name in sorted(_PROJECTION_FILES) if recon_per.get(name) != head_per.get(name)
    )
    ok = recon_hash == head_hash and not mismatched
    return SnapshotReconstructResult(
        snapshot_dir=snapshot_dir.name,
        cutoff_seq=cutoff_seq,
        reconstructable=ok,
        mismatched_projections=mismatched,
        reconstructed_hash=recon_hash,
        head_hash=head_hash,
        detail=(
            "重建态 == head 态 (snapshot+前向replay 落到全量重放同一 head)"
            if ok
            else f"重建态 != head 态; 不符 projection: {list(mismatched)}"
        ),
    )


def _select_sample(
    snapshot_dirs: list[Path],
    sampling_rate: float,
    seed: int,
    sample_all: bool,
) -> list[Path]:
    """§6.5 抽样选择: ceil(rate*N) 个 (N>0 时至少 1); sample_all → 全选 (audit 兜底 / 测试)。

    随机但可复现 (seeded) —— Python random 可用; v3 初版固定 seed 保证同输入同抽样可审计。
    """
    n = len(snapshot_dirs)
    if n == 0:
        return []
    if sample_all or sampling_rate >= 1.0:
        return list(snapshot_dirs)
    count = max(1, math.ceil(sampling_rate * n))
    if count >= n:
        return list(snapshot_dirs)
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(n), count))
    return [snapshot_dirs[i] for i in idx]


def verify_reconstructability_sampling(
    event_log: EventLog,
    towow_dir: Path,
    *,
    sampling_rate: float | None = None,
    seed: int = 0,
    sample_all: bool = False,
) -> ReconstructabilitySamplingResult:
    """M-0.7 §6.5 — 对所有历史 snapshot (hot + cold-archived) 抽 sampling_rate, 真 5% 全量 replay
    验证重建一致性。

    sampling_rate 缺省取 config.sampling_rate (snapshot-module/config.json, 默认 1.0 全验);
    显式传 0.05 走 §6.5 轻量抽样。sample_all=True → 全验 (audit 兜底, §6.5 抽样命中失败后的
    全验路径; 也是测试确定性入口)。

    每个被抽中 snapshot 在临时 scratch projection store 重建 (绝不污染 live .towow/graph), 逐
    projection canonical 比对 "从零全量重放 head"。这是真验证 (fail-closed, 篡改/不符判失败),
    不是结构壳。库层逻辑, 由同步命令 / maintenance 驱动 —— daemon 自动定期巡检 OFF (挂债)。
    """
    rate = sampling_rate if sampling_rate is not None else load_config(towow_dir).sampling_rate
    snapshot_dirs = [*list_snapshot_dirs(towow_dir), *list_archived_snapshot_dirs(towow_dir)]
    total = len(snapshot_dirs)

    with tempfile.TemporaryDirectory(prefix="run070-recon-") as tmp:
        work_root = Path(tmp)
        head_hash, head_per = _full_rebuild_head_hash(event_log, work_root)
        if total == 0:
            return ReconstructabilitySamplingResult(
                sampling_rate=rate,
                total_snapshots=0,
                sampled_count=0,
                head_hash=head_hash,
                results=(),
                all_passed=True,
            )
        sampled = _select_sample(snapshot_dirs, rate, seed, sample_all)
        results = tuple(
            _reconstruct_from_snapshot(
                event_log, towow_dir, snap, work_root, head_hash, head_per,
            )
            for snap in sampled
        )
    return ReconstructabilitySamplingResult(
        sampling_rate=rate,
        total_snapshots=total,
        sampled_count=len(results),
        head_hash=head_hash,
        results=results,
        all_passed=all(r.reconstructable for r in results),
    )


def reconstructability_auto_audit_enabled(towow_dir: Path) -> bool:
    """daemon 自动定期 reconstructability 巡检开关 (config, 默认 OFF / False)。

    RUN-070 红线: 只建库层 + 同步命令。自动定期巡检 (cron / 常驻 daemon) gated (owner 红线未拍),
    此开关默认 False; 即便置 True 本模块也不自己起 daemon —— 它只是给未来 daemon 层读的 flag。
    """
    cfg = load_config(towow_dir)
    return getattr(cfg, "reconstructability_auto_audit_enabled", False)


__all__ = [
    "SPEC_SAMPLING_RATE",
    "ReconstructabilitySamplingResult",
    "SnapshotReconstructResult",
    "reconstructability_auto_audit_enabled",
    "verify_reconstructability_sampling",
]
