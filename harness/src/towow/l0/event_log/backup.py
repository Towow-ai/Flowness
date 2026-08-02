"""T-FIX-INC-01 layer4 — 账本自动冷备份 (节流, 账本外, 保留最近 N 份)。

# 事故源 (INCIDENT-TOWOW-WIPE-2026-06-10): 真账本被整目录扫掉时, 06-04 之后的 hot 段
# (events-000003.jsonl, 5200+ 条) 从未进过 git → 不可恢复。本模块把 events.log + hot 段
# 周期性 cp 到【账本外】的 .towow-backup/ — 账本目录被扫掉时备份还活着。
#
# 接线点: orchestrator polling loop 每轮调 maybe_backup_ledger (节流默认 1h), 放在
# paused 检查【之前】— 事故当晚有一个 paused daemon 在场却帮不上忙; paused 只是
# "不派活", 备份护栏照跑。daemon 不跑时无备份 — 可接受 (daemon 跑起来是投产常态)。
#
# 轻量铁律: 纯 stdlib (shutil/json/time); 备份失败绝不打断轮询 (错误落
# .towow-backup/last_error.txt 可观测, 不静默丢弃也不炸 daemon)。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from towow.l0.fs_guard import safe_rmtree

DEFAULT_BACKUP_INTERVAL_S = 3600.0  # 每小时一次
DEFAULT_BACKUP_KEEP = 24  # 保留最近 24 份 (≈ 一天)
_STATE_FILE = "state.json"


def default_backup_root(towow_dir: Path) -> Path:
    """默认备份目录: 账本同级的 .towow-backup/ (账本外 — 这是 layer4 的存在意义)。"""
    return towow_dir.parent / ".towow-backup"


def maybe_backup_ledger(
    towow_dir: Path,
    *,
    interval_s: float = DEFAULT_BACKUP_INTERVAL_S,
    keep: int = DEFAULT_BACKUP_KEEP,
    backup_root: Path | None = None,
    now: float | None = None,
) -> Path | None:
    """节流备份 events.log + events/hot/*.jsonl 到账本外。

    返回本次备份目录 (backup-<ts>/), 节流跳过/无东西可备/失败 → None。
    失败绝不 raise (polling loop 的护栏不能反过来杀 loop) — 错误写
    backup_root/last_error.txt 供观测。

    备份布局 (backup-<ts>/ledger/ 子目录一层是有意的): events.log 拷贝不直接躺在
    backup-<ts>/ 下, 否则 backup-<ts> 自己长成"账本根"形状, safe_rmtree 的保留轮换
    会被 layer1 绝对断言拒掉。哨兵 (.ledger-root) 故意不拷 — 备份是拷贝不是账本。
    """
    root = backup_root or default_backup_root(towow_dir)
    ts = time.time() if now is None else now
    try:
        return _backup_once(towow_dir, root=root, interval_s=interval_s, keep=keep, ts=ts)
    except Exception as exc:  # 护栏绝不杀宿主; 错误落盘可观测
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / "last_error.txt").write_text(
                f"ts={ts} maybe_backup_ledger failed: {exc!r}\n", encoding="utf-8",
            )
        except OSError:
            pass
        return None


def _backup_once(
    towow_dir: Path, *, root: Path, interval_s: float, keep: int, ts: float,
) -> Path | None:
    state_path = root / _STATE_FILE
    if state_path.exists():
        try:
            last = float(json.loads(state_path.read_text(encoding="utf-8"))["last_backup_at"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            last = 0.0  # 坏 state → 视为从未备份 (宁多备一次)
        if ts - last < interval_s:
            return None  # 节流窗口内
    base = towow_dir / "events.log"
    hot_dir = towow_dir / "events" / "hot"
    hot_segments = sorted(hot_dir.glob("events-*.jsonl")) if hot_dir.is_dir() else []
    if not base.exists() and not hot_segments:
        return None  # 没有账本内容可备

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ts))
    dest = root / f"backup-{stamp}"
    suffix = 1
    while dest.exists():  # 同秒碰撞 (interval_s=0 的测试/手动连发) → 加序号
        suffix += 1
        dest = root / f"backup-{stamp}.{suffix}"
    ledger_dest = dest / "ledger"
    (ledger_dest / "hot").mkdir(parents=True)
    if base.exists():
        shutil.copy2(base, ledger_dest / "events.log")
    for seg in hot_segments:
        shutil.copy2(seg, ledger_dest / "hot" / seg.name)

    state_path.write_text(
        json.dumps({"last_backup_at": ts, "last_backup_dir": dest.name}), encoding="utf-8",
    )
    _prune(root, keep=keep)
    return dest


def _prune(root: Path, *, keep: int) -> None:
    """只留最近 keep 份 (按目录名时间戳升序删最老)。删除走 safe_rmtree (吃同一套护栏)。"""
    backups = sorted(d for d in root.iterdir() if d.is_dir() and d.name.startswith("backup-"))
    for old in backups[: max(0, len(backups) - keep)]:
        safe_rmtree(old)


__all__ = [
    "DEFAULT_BACKUP_INTERVAL_S",
    "DEFAULT_BACKUP_KEEP",
    "default_backup_root",
    "maybe_backup_ledger",
]
