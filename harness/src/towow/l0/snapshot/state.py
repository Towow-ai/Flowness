"""M-0.7 §9 snapshot-module 运行时状态文件 (T-L0-35, 波1).

`.towow/snapshot-module/` 下三个运行时状态文件 (spec §9 物理存储 1271-1274):
  - config.json             : snapshot retention / sampling_rate / lookback 默认 (可调)
  - retention-watermark.json: 当前 retention_watermark (M-0.7 push, M-0.1 pull-back-and-cache)
  - gc-state.json           : GC run 历史 (最后 GC 时间 + archive_seq_bound + run_count + 运行状态 hash)

原状 (改前): snapshot.py 硬编码 DEFAULT_RETENTION=5, 无任何状态文件; retention 不可调,
watermark 只在内存里算完即弃 (无跨 GC/consumer 传递的载体)。本模块补上三个文件 →
retention 可配置 (改 config.json 即变 enforce_retention 行为), watermark 真 push/pull。

诚实边界: gc-state 的 "根集 hash" 现记 §3.2 顺序保护层 (archive_seq_bound / consumer_watermark /
hot_replay_floor) 的运行状态 hash (gc_run_state_hash); §3.3 语义保护根集 (Nature anchors /
supersede chains / projection refs) 尚未实现 (compute_gc_reachable 未落) → full roots-set hash 待
§3.3 roots 落地后补, 此处不假装已含语义根集。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.projection.projection import os_replace_with_fsync
from towow.l0.snapshot.snapshot import DEFAULT_RETENTION

if TYPE_CHECKING:
    from pathlib import Path

_SNAPSHOT_MODULE_DIRNAME = "snapshot-module"
_DEFAULT_SAMPLING_RATE = 1.0
_DEFAULT_LOOKBACK = 50


@dataclass(frozen=True)
class SnapshotModuleConfig:
    """M-0.7 §9 config.json — snapshot/GC 可调默认。缺/坏字段逐项按默认回退 (fail-safe)。"""

    retention: int = DEFAULT_RETENTION
    sampling_rate: float = _DEFAULT_SAMPLING_RATE
    lookback: int = _DEFAULT_LOOKBACK
    # RUN-070 §6.5 daemon 自动定期 reconstructability 巡检开关 — 默认 OFF (False)。
    # owner 红线未拍 daemon 全自动: 只建库层 + 同步命令; 此 flag 给未来 daemon 层读, 置 True
    # 也不会让 reconstructability 模块自起 daemon。
    reconstructability_auto_audit_enabled: bool = False


def _module_dir(towow_dir: Path) -> Path:
    return towow_dir / _SNAPSHOT_MODULE_DIRNAME


def _read_json(path: Path) -> dict[str, object] | None:
    """读 JSON; 缺失/坏/非 dict → None (上层按默认回退, 不崩)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os_replace_with_fsync(tmp, path, path.parent)


def _coerce_nonneg_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):  # bool 是 int 子类, 显式排除
        return fallback
    return value if isinstance(value, int) and value >= 0 else fallback


def load_config(towow_dir: Path) -> SnapshotModuleConfig:
    """M-0.7 §9 — 读 config.json; 缺/坏 → 全默认; 单字段坏 → 该字段回退默认。"""
    data = _read_json(_module_dir(towow_dir) / "config.json")
    if data is None:
        return SnapshotModuleConfig()
    sr = data.get("sampling_rate")
    sampling_rate = (
        float(sr)
        if isinstance(sr, (int, float)) and not isinstance(sr, bool) and sr > 0
        else _DEFAULT_SAMPLING_RATE
    )
    auto_audit = data.get("reconstructability_auto_audit_enabled")
    return SnapshotModuleConfig(
        retention=_coerce_nonneg_int(data.get("retention"), DEFAULT_RETENTION),
        sampling_rate=sampling_rate,
        lookback=_coerce_nonneg_int(data.get("lookback"), _DEFAULT_LOOKBACK),
        # OFF 开关 (RUN-070): 仅 bool True 才开; 缺/非 bool → 默认 False (保持 daemon 自动巡检关停)。
        reconstructability_auto_audit_enabled=auto_audit is True,
    )


def effective_retention(towow_dir: Path, explicit: int | None) -> int:
    """显式 retention 优先 (调用方传了就用); 否则取 config.json 的 retention (缺则默认)。"""
    if explicit is not None:
        return explicit
    return load_config(towow_dir).retention


def push_retention_watermark(
    towow_dir: Path,
    *,
    archive_seq_bound: int,
    consumer_watermark: int,
    hot_replay_floor: int,
    now: datetime | None = None,
) -> None:
    """M-0.7 push — 把当前 retention_watermark 落到 retention-watermark.json。

    retention_watermark = archive_seq_bound (§3.1 archivability 用的那个 watermark);
    consumer_watermark / hot_replay_floor 作为 §3.2 组成留档, 供"跨 consumer 真传递"核验。
    M-0.1 之后 pull-back-and-cache (pull_retention_watermark)。
    """
    stamp = (now or datetime.now(tz=UTC)).isoformat()
    _atomic_write_json(
        _module_dir(towow_dir) / "retention-watermark.json",
        {
            "retention_watermark": archive_seq_bound,
            "consumer_watermark": consumer_watermark,
            "hot_replay_floor": hot_replay_floor,
            "pushed_at": stamp,
        },
    )


def pull_retention_watermark(towow_dir: Path) -> int | None:
    """M-0.1 pull-back-and-cache — 读回最近 push 的 retention_watermark (无/坏则 None)。"""
    data = _read_json(_module_dir(towow_dir) / "retention-watermark.json")
    if data is None:
        return None
    wm = data.get("retention_watermark")
    return wm if isinstance(wm, int) and not isinstance(wm, bool) else None


def load_retention_watermark(towow_dir: Path) -> dict[str, object] | None:
    """读回 retention-watermark.json 全貌 (含 consumer_watermark / hot_replay_floor 组成)。"""
    return _read_json(_module_dir(towow_dir) / "retention-watermark.json")


def _gc_run_state_hash(
    *, archive_seq_bound: int, consumer_watermark: int, hot_replay_floor: int,
) -> str:
    raw = json.dumps(
        {
            "archive_seq_bound": archive_seq_bound,
            "consumer_watermark": consumer_watermark,
            "hot_replay_floor": hot_replay_floor,
        },
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_gc_state(
    towow_dir: Path,
    *,
    archive_seq_bound: int,
    consumer_watermark: int,
    hot_replay_floor: int,
    now: datetime | None = None,
) -> dict[str, object]:
    """M-0.7 §9 — 记一次 GC run 到 gc-state.json (最后 GC 时间 + bound + run_count 累加 + 运行状态 hash)。

    run_count 在已存状态上累加 (跨 GC run 保留历史)。根集 hash 见模块 docstring 诚实边界。
    """
    path = _module_dir(towow_dir) / "gc-state.json"
    prev = _read_json(path)
    run_count = _coerce_nonneg_int(prev.get("run_count") if prev else None, 0) + 1
    state: dict[str, object] = {
        "last_gc_at": (now or datetime.now(tz=UTC)).isoformat(),
        "last_gc_archive_seq_bound": archive_seq_bound,
        "last_gc_consumer_watermark": consumer_watermark,
        "last_gc_hot_replay_floor": hot_replay_floor,
        "run_count": run_count,
        "gc_run_state_hash": _gc_run_state_hash(
            archive_seq_bound=archive_seq_bound,
            consumer_watermark=consumer_watermark,
            hot_replay_floor=hot_replay_floor,
        ),
    }
    _atomic_write_json(path, state)
    return state


def load_gc_state(towow_dir: Path) -> dict[str, object] | None:
    """读回 gc-state.json (无/坏则 None)。"""
    return _read_json(_module_dir(towow_dir) / "gc-state.json")


__all__ = [
    "SnapshotModuleConfig",
    "effective_retention",
    "load_config",
    "load_gc_state",
    "load_retention_watermark",
    "pull_retention_watermark",
    "push_retention_watermark",
    "write_gc_state",
]
