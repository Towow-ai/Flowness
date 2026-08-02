"""M-0.7 §7 — Cold Storage physical layout + prepare/commit/finalize archival + cold lookup.

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md
#     §7.1 物理布局: .towow/events/cold/archive-NNNNNN.jsonl.gz (gzip, 1 archive ≈ 1 hot segment) +
#          cold/staging/ (prepare 阶段 tmp) + cold index (event_id → segment, location 路由).
#     §7.2 三段式: prepare (写 staging tmp, 不动 hot/index) → commit (accepted ArchiveSegmentMoved) →
#          finalize (atomic rename staging→cold, 更新 index, unlink hot). Crash recovery / forbidden 路径.
#     §7.3 cold lookup: get_event 查不到 hot → fallback 解压 archive 读回.
#
# This module is §7.1 layout only; §7.2/§7.3 land in sibling commits (cold_archive.py /
# cold_lookup.py reuse these primitives). Everything here is daemon-independent physical IO.
#
# Design decisions (§7.1):
#   - archive 文件 1:1 hot segment (不合并); gzip 压缩 (简单广泛支持, 不引 zstd).
#   - cold index 平铺单文件 cold-index.json: {event_id: {location: "cold", segment: N}} —— location
#     字段路由 (M-0.1 hot 命中走 hot, miss 才查 cold index). §7.1 说 index 平铺 hot/cold 共用; v3
#     初版 M-0.1 的 EventLogIndexStore 不带 location 字段, 故 M-0.7 自管一份 cold-only 路由表, 不改
#     M-0.1 hot index 语义 (诚实边界: 这是 M-0.7 拥有的 cold 路由, 不冒充改 M-0.1 平铺 index).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.projection.projection import os_replace_with_fsync

if TYPE_CHECKING:
    from pathlib import Path

# §7.1 — 6-digit zero-padded segment id in archive/staging filenames (matches hot events-NNNNNN).
_ARCHIVE_FMT = "archive-{:06d}.jsonl.gz"
_STAGING_FMT = "archive-{:06d}.jsonl.gz.tmp"


def cold_dir(towow_dir: Path) -> Path:
    """`.towow/events/cold/` — M-0.7 owns this dir (archived event segments)."""
    return towow_dir / "events" / "cold"


def cold_staging_dir(towow_dir: Path) -> Path:
    """`.towow/events/cold/staging/` — prepare 阶段 staging tmp 落这里 (commit 前不动可见状态)."""
    return cold_dir(towow_dir) / "staging"


def cold_archive_path(towow_dir: Path, segment_id: int) -> Path:
    """Final cold archive path for a hot segment — archive-{seg:06d}.jsonl.gz."""
    return cold_dir(towow_dir) / _ARCHIVE_FMT.format(segment_id)


def cold_staging_path(towow_dir: Path, segment_id: int) -> Path:
    """Staging tmp path for a segment being prepared — archive-{seg:06d}.jsonl.gz.tmp."""
    return cold_staging_dir(towow_dir) / _STAGING_FMT.format(segment_id)


def cold_index_path(towow_dir: Path) -> Path:
    """Cold routing index — event_id → {location, segment}. M-0.7 owned (not M-0.1's hot index)."""
    return cold_dir(towow_dir) / "cold-index.json"


def load_cold_index(towow_dir: Path) -> dict[str, dict[str, object]]:
    """Read the cold routing index; missing/corrupt → empty dict (fail-safe — nothing in cold yet).

    A missing index legitimately means "no segment archived yet". A corrupt index returns empty too:
    the cold index is a derived routing cache (the archive .gz files + accepted ArchiveSegmentMoved
    events are the truth), so a re-finalize / recovery can always re-populate it.
    """
    path = cold_index_path(towow_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _atomic_write_index(towow_dir: Path, index: dict[str, dict[str, object]]) -> None:
    path = cold_index_path(towow_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    os_replace_with_fsync(tmp, path, path.parent)


def update_cold_index(
    towow_dir: Path,
    *,
    event_ids: list[str],
    segment_id: int,
) -> None:
    """§7.1/§7.2 finalize step 2 — mark these event_ids as living in cold/archive-{segment_id}.

    Reads-modifies-writes the routing index atomically. Idempotent for the same (event_ids,
    segment_id): re-running maps the same ids to the same segment (used by crash-recovery finalize).
    """
    index = load_cold_index(towow_dir)
    for eid in event_ids:
        index[eid] = {"location": "cold", "segment": segment_id}
    _atomic_write_index(towow_dir, index)


__all__ = [
    "cold_archive_path",
    "cold_dir",
    "cold_index_path",
    "cold_staging_dir",
    "cold_staging_path",
    "load_cold_index",
    "update_cold_index",
]
