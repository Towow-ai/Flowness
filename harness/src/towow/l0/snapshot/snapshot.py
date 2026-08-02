"""M-0.7 §5 standalone snapshot creation + historical retention (RUN-027 block 5).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §2.1 SnapshotCreated payload (event_offset / projection_state_hash / snapshot_type /
#       covered_projections / bundle_hash / snapshot_storage_path)
#     §5.1 Mode 1 standalone snapshot (Path B direct write — SNAPSHOT_CREATED allowed)
#     §5.2 historical retention — keep last 5; older snapshot files move to cold
#     §5.3 atomic physical persistence (M-0.2a Patch 3 atomic mode: tmp dir → os.replace)
#
# Why (E-DEBT-PAYOFF-ROADMAP §1 block-5): snapshot was a pure stub (CLI emitted a
# SnapshotCreated with a random hash + event_offset=0, persisting nothing). This makes
# "系统能给自己拍历史快照" real: cutoff_seq = real max committed seq; the projection bundle
# is physically dumped + hashed; the SnapshotCreated event carries the real offset/hash/path;
# retention keeps the last 5 and archives older snapshot dirs to cold.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from towow.l0.fs_guard import safe_rmtree
from towow.l0.projection.projection import os_replace_with_fsync
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SnapshotType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog
    from towow.l0.projection.projection import ProjectionStore

DEFAULT_RETENTION = 5


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of create_standalone_snapshot."""

    snapshot_event_id: str
    cutoff_seq: int
    bundle_hash: str
    snapshot_storage_path: str
    covered_projections: list[str]
    archived_snapshot_dirs: list[str]  # dirs moved to cold by retention (this run)


class SnapshotIntegrityError(RuntimeError):
    """M-0.7 §5.4 fail-closed — snapshot bundle/manifest 完整性校验失败。绝不静默恢复坏态。"""


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of restore_from_snapshot (M-0.7 §5.4)."""

    cutoff_seq: int
    bundle_hash: str
    restored_projections: list[str]
    events_replayed: int  # events > cutoff_seq replayed by catchup to reach the live head


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def create_standalone_snapshot(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    snapshot_type: SnapshotType = SnapshotType.MILESTONE,
    retention: int | None = None,
) -> SnapshotResult:
    """M-0.7 §5.1 Mode 1 — create a real standalone snapshot (Path B direct write).

    cutoff_seq is the real max committed seq; the materialized projection files are
    dumped + per-projection hashed + composed into a bundle_hash, persisted atomically to
    .towow/snapshots/snapshot-{cutoff_seq}/, and a real SnapshotCreated event is written.
    Then retention is enforced (§5.2).
    """
    projection_store.catchup(event_log)
    cutoff_seq = max(0, event_log.next_sequence - 1)

    # Dump the materialized projection bundle + per-projection hashes (§5.1 stage 2/3).
    graph_dir = towow_dir / "graph"
    per_projection: list[dict[str, object]] = []
    file_hashes: dict[str, str] = {}
    dumped: dict[str, str] = {}  # projection_name -> json text
    for proj_file in sorted(graph_dir.glob("*.json")):
        name = proj_file.stem
        raw = proj_file.read_bytes()
        h = _hash_bytes(raw)
        file_hashes[name] = h
        dumped[name] = raw.decode("utf-8")
        per_projection.append(
            {"projection_name": name, "projection_state_hash": h, "watermark": cutoff_seq},
        )
    bundle_hash = _hash_bytes(
        json.dumps(file_hashes, sort_keys=True).encode("utf-8"),
    )
    covered = sorted(file_hashes.keys())

    # Persist atomically (§5.3): tmp dir → os.replace.
    snapshots_dir = towow_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = snapshots_dir / f"snapshot-{cutoff_seq}"
    tmp_dir = snapshots_dir / f"snapshot-{cutoff_seq}.tmp"
    if tmp_dir.exists():
        # T-FIX-INC-01: 递归删除一律走 safe_rmtree (账本根/哨兵断言 + snapshots 白名单段)
        safe_rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "bundle").mkdir()
    for name, text in dumped.items():
        (tmp_dir / "bundle" / f"{name}.json").write_text(text, encoding="utf-8")
    (tmp_dir / "manifest.json").write_text(
        json.dumps(
            {
                "cutoff_seq": cutoff_seq,
                "bundle_hash": bundle_hash,
                "snapshot_type": snapshot_type.value,
                "covered_projections": per_projection,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if snapshot_dir.exists():
        safe_rmtree(snapshot_dir)  # T-FIX-INC-01: 护栏删除
    os_replace_with_fsync(tmp_dir, snapshot_dir, snapshots_dir)
    storage_path = f".towow/snapshots/snapshot-{cutoff_seq}/"

    # Real SnapshotCreated event (Path B — §5.1 stage 5).
    correlation_id = f"snapshot-{uuid.uuid4().hex[:12]}"
    intent = EventIntent(
        local_intent_id=f"snap-{uuid.uuid4().hex[:12]}",
        event_type=EventType.SNAPSHOT_CREATED,
        event_category=EventCategory.SNAPSHOT,
        payload={
            "event_offset": cutoff_seq,
            "projection_state_hash": bundle_hash,
            "snapshot_type": snapshot_type.value,
            "covered_projections": covered,
            "bundle_hash": bundle_hash,
            "snapshot_storage_path": storage_path,
            "consolidation_event_id": None,
            "per_projection": per_projection,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SNAPSHOT_MODULE.value,
            actor_id="m07",
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.SNAPSHOT,
                entity_id=f"snapshot-{cutoff_seq}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    snapshot_event_id = event_log.write_direct(intent).event_id

    archived = enforce_retention(towow_dir, retention=retention)
    return SnapshotResult(
        snapshot_event_id=snapshot_event_id,
        cutoff_seq=cutoff_seq,
        bundle_hash=bundle_hash,
        snapshot_storage_path=storage_path,
        covered_projections=covered,
        archived_snapshot_dirs=archived,
    )


def restore_from_snapshot(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    towow_dir: Path,
    snapshot_dir: Path,
) -> RestoreResult:
    """M-0.7 §5.4 / M-0.2 §5.4 — restore projection state from a hot standalone snapshot dir.

    Inverse of create_standalone_snapshot. Reads the snapshot manifest + per-projection bundle,
    re-verifies bundle_hash from the on-disk bundle files (fail-closed on tamper/corruption —
    never restore a mismatched bundle), hands the bundle to M-0.2 (restore_bundle: load projection
    state + set watermark = cutoff_seq), then catches up over events > cutoff_seq (§5.4 step 3-5).
    Restore is the inverse that makes SnapshotCreated two-directional: before this, the system
    could photograph its history but never load one back.

    ``snapshot_dir`` is an on-disk snapshot directory (manifest.json + bundle/). It may be a hot
    ``.towow/snapshots/snapshot-N/`` OR a retention-archived ``.towow/snapshots-archive/snapshot-N/``
    (same structure — restore reads the manifest+bundle identically either way). RUN-056 件B:
    restoring a snapshot that §5.2 retention moved to cold is no longer deferred — call
    ``fetch_snapshot_from_cold(towow_dir, cutoff_seq)`` first to bring the archived dir back to hot,
    then restore from the returned path (the CLI `snapshot-restore` does this automatically).

    Idempotent: restoring the same snapshot twice reproduces identical projection state (bundle
    load replaces in place; reducers replay idempotently). Returns a RestoreResult.
    """
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"snapshot manifest 不存在: {manifest_path}"
        raise SnapshotIntegrityError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        cutoff_seq = int(manifest["cutoff_seq"])
        expected_bundle_hash = str(manifest["bundle_hash"])
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"snapshot manifest 缺 cutoff_seq/bundle_hash 或非法: {manifest_path} ({exc})"
        raise SnapshotIntegrityError(msg) from exc

    bundle_dir = snapshot_dir / "bundle"
    if not bundle_dir.is_dir():
        msg = f"snapshot bundle 目录不存在: {bundle_dir}"
        raise SnapshotIntegrityError(msg)

    # Re-verify bundle_hash from the on-disk bundle, using the exact recipe create used (hash of
    # the sorted {name: per-file-hash} map). Any mismatch → fail-closed: a tampered/corrupt bundle
    # must never be restored (would silently load wrong state — the owner red line).
    file_hashes: dict[str, str] = {}
    bundle_texts: dict[str, str] = {}
    for proj_file in sorted(bundle_dir.glob("*.json")):
        raw = proj_file.read_bytes()
        file_hashes[proj_file.stem] = _hash_bytes(raw)
        bundle_texts[proj_file.stem] = raw.decode("utf-8")
    actual_bundle_hash = _hash_bytes(json.dumps(file_hashes, sort_keys=True).encode("utf-8"))
    if actual_bundle_hash != expected_bundle_hash:
        msg = (
            f"snapshot bundle_hash 不符 (manifest {expected_bundle_hash} != 实算 {actual_bundle_hash}) "
            "— bundle 被篡改/损坏, fail-closed 不恢复"
        )
        raise SnapshotIntegrityError(msg)

    # M-0.2 §5.4 step 1-2: load snapshot projection state + set watermark = cutoff_seq.
    restored = projection_store.restore_bundle(bundle_texts, cutoff_seq=cutoff_seq)
    # §5.4 step 3-5: replay committed events > cutoff_seq + persist.
    replayed = projection_store.catchup(event_log)
    return RestoreResult(
        cutoff_seq=cutoff_seq,
        bundle_hash=expected_bundle_hash,
        restored_projections=restored,
        events_replayed=replayed,
    )


def _snapshot_seq(snapshot_dir: Path) -> int:
    """Parse the cutoff_seq out of a snapshot-{seq} dir name (-1 if unparseable)."""
    try:
        return int(snapshot_dir.name.removeprefix("snapshot-"))
    except ValueError:
        return -1


def list_snapshot_dirs(towow_dir: Path) -> list[Path]:
    """Hot snapshot dirs (.towow/snapshots/snapshot-*), ascending by cutoff_seq."""
    snapshots_dir = towow_dir / "snapshots"
    if not snapshots_dir.exists():
        return []
    dirs = [
        d
        for d in snapshots_dir.iterdir()
        if d.is_dir() and d.name.startswith("snapshot-") and not d.name.endswith(".tmp")
    ]
    return sorted(dirs, key=_snapshot_seq)


def enforce_retention(towow_dir: Path, *, retention: int | None = None) -> list[str]:
    """M-0.7 §5.2 — keep the last `retention` snapshot dirs hot; move older ones to cold
    (.towow/snapshots-archive/). The SnapshotCreated events stay hot (untouched) — only the
    physical snapshot files move. Returns the dir names archived this call.

    T-L0-35 (波1): retention=None 时从 snapshot-module/config.json 取 (effective_retention);
    显式传值优先。改 config.json 的 retention 即变本函数保留条数。lazy import 破 snapshot↔state 循环。
    """
    # lazy import: 破 snapshot↔state 模块加载循环 (state 在加载期 import 本模块的 DEFAULT_RETENTION)
    from towow.l0.snapshot.state import effective_retention

    resolved = effective_retention(towow_dir, retention)
    dirs = list_snapshot_dirs(towow_dir)
    if len(dirs) <= resolved:
        return []
    to_archive = dirs[: len(dirs) - resolved]
    archive_root = towow_dir / "snapshots-archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for d in to_archive:
        dest = archive_root / d.name
        if dest.exists():
            safe_rmtree(dest)  # T-FIX-INC-01: 护栏删除 (snapshots-archive 白名单段)
        shutil.move(str(d), str(dest))
        archived.append(d.name)
    return archived


def list_archived_snapshot_dirs(towow_dir: Path) -> list[Path]:
    """Cold-archived snapshot dirs (.towow/snapshots-archive/snapshot-*), ascending by cutoff_seq.

    These are snapshots §5.2 retention moved out of hot (keep last 5). Their SnapshotCreated events
    stay hot; only the physical bundle dir moved. ``fetch_snapshot_from_cold`` brings one back.
    """
    archive_root = towow_dir / "snapshots-archive"
    if not archive_root.exists():
        return []
    dirs = [
        d
        for d in archive_root.iterdir()
        if d.is_dir() and d.name.startswith("snapshot-") and not d.name.endswith(".tmp")
    ]
    return sorted(dirs, key=_snapshot_seq)


def fetch_snapshot_from_cold(towow_dir: Path, cutoff_seq: int) -> Path:
    """M-0.7 §5.2/§7 — bring a retention-archived snapshot back to hot so it can be restored (件B).

    Moves ``.towow/snapshots-archive/snapshot-{cutoff_seq}/`` back to
    ``.towow/snapshots/snapshot-{cutoff_seq}/`` and returns the hot path. Idempotent: if the snapshot
    is already hot, returns it untouched; if it lives in neither place, fail-closed
    (SnapshotIntegrityError) rather than silently restoring nothing. This is the "fetch-from-cold
    first" step §5.4 deferred to EPIC-6 — now real: restore_from_snapshot then reads the hot dir.
    """
    hot = towow_dir / "snapshots" / f"snapshot-{cutoff_seq}"
    if hot.is_dir():
        return hot
    archived = towow_dir / "snapshots-archive" / f"snapshot-{cutoff_seq}"
    if not archived.is_dir():
        msg = (
            f"snapshot-{cutoff_seq} 在 hot (.towow/snapshots/) 与 cold (.towow/snapshots-archive/) 均不存在 "
            "— 无可 fetch 的归档快照, fail-closed"
        )
        raise SnapshotIntegrityError(msg)
    snapshots_dir = towow_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archived), str(hot))
    return hot


__all__ = [
    "DEFAULT_RETENTION",
    "SnapshotResult",
    "create_standalone_snapshot",
    "enforce_retention",
    "fetch_snapshot_from_cold",
    "list_archived_snapshot_dirs",
    "list_snapshot_dirs",
]
