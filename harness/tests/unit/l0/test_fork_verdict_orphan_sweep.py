"""finding f-r12-4-dropfile-orphan-leak-in-repo-tree — fork-verdict orphan drop-file 周期清扫。

BG_POLLED gate fork 的 drop-file 回收靠 run_bg_polled_gate_fork try/finally unlink, 覆盖三个主进程态。
隐式第 4 态: parent 超时 fail-closed 时 unlink 跑在慢 detached fork 落盘之前 (no-op), fork 之后才把
verdict 写进 <repo>/.towow/tmp/fork-verdict/<...>-<uuid>.json → orphan 永留 repo 工作树。本测固定
M-2.2 GC 的兜底回收行为, 并 **pin 住路径与落盘口同一 SSOT** (l1 fork_verdict_dir): 用真正的落盘口
new_collision_proof_drop_file 造孤儿, 断言 GC 扫的就是它落的那个目录、且把陈旧孤儿删掉。两处若漂移
这条测试就红 —— 堵住 "GC 静默扫空目录、orphan 永不被清" 的 looks-closed-isnt 失败。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from towow.l0.snapshot.gc import (
    ORPHAN_FORK_VERDICT_MAX_AGE_S,
    sweep_orphan_fork_verdict_dropfiles,
)
from towow.l1.verification_fork import fork_verdict_dir, new_collision_proof_drop_file

if TYPE_CHECKING:
    from pathlib import Path


def _plant_orphan(repo_dir: Path, *, age_s: float) -> Path:
    """经真正的落盘口造一个孤儿 drop-file, 并把其 mtime 调到 age_s 之前。"""
    drop = new_collision_proof_drop_file(repo_dir, "verify-step", "sess-fork-abc")
    drop.write_text('{"passed": false}', encoding="utf-8")
    old = drop.stat().st_mtime - age_s
    os.utime(drop, (old, old))
    return drop


def test_sweep_targets_the_real_dropfile_dir_and_deletes_aged_orphan(tmp_path: Path) -> None:
    """pin: 清扫的目录 == new_collision_proof_drop_file 落盘的目录; 陈旧孤儿被删。"""
    repo = tmp_path
    towow = repo / ".towow"
    towow.mkdir()
    orphan = _plant_orphan(repo, age_s=ORPHAN_FORK_VERDICT_MAX_AGE_S + 60)

    # 路径耦合断言: 孤儿确实落在 fork_verdict_dir (SSOT) 下 — 漂移则此断言先红。
    assert orphan.parent == fork_verdict_dir(repo)

    swept = sweep_orphan_fork_verdict_dropfiles(towow)

    assert swept == [orphan.name]
    assert not orphan.exists()


def test_sweep_spares_fresh_dropfile_of_in_flight_fork(tmp_path: Path) -> None:
    """并发安全: mtime 在阈值内的 drop-file (可能仍有在跑的 parent 在等) 绝不被删。"""
    repo = tmp_path
    towow = repo / ".towow"
    towow.mkdir()
    fresh = _plant_orphan(repo, age_s=ORPHAN_FORK_VERDICT_MAX_AGE_S - 60)

    swept = sweep_orphan_fork_verdict_dropfiles(towow)

    assert swept == []
    assert fresh.exists()


def test_sweep_age_boundary_uses_injected_now(tmp_path: Path) -> None:
    """阈值边界 + now 注入: age 恰好越过 max_age_s 才删。"""
    repo = tmp_path
    towow = repo / ".towow"
    towow.mkdir()
    drop = new_collision_proof_drop_file(repo, "audit", "sess-xyz")
    drop.write_text("{}", encoding="utf-8")
    mtime = drop.stat().st_mtime

    # now 让 age 刚好 = max_age_s - 1 → 不删
    kept = sweep_orphan_fork_verdict_dropfiles(
        towow, now=mtime + ORPHAN_FORK_VERDICT_MAX_AGE_S - 1,
    )
    assert kept == []
    assert drop.exists()

    # now 让 age = max_age_s + 1 → 删
    gone = sweep_orphan_fork_verdict_dropfiles(
        towow, now=mtime + ORPHAN_FORK_VERDICT_MAX_AGE_S + 1,
    )
    assert gone == [drop.name]
    assert not drop.exists()


def test_sweep_missing_dir_is_noop(tmp_path: Path) -> None:
    """best-effort: fork-verdict 目录不存在 → 空结果, 不抛。"""
    towow = tmp_path / ".towow"
    towow.mkdir()
    assert sweep_orphan_fork_verdict_dropfiles(towow) == []


def test_sweep_ignores_subdirs_and_only_files(tmp_path: Path) -> None:
    """目录项里混入子目录不报错, 只清文件。"""
    repo = tmp_path
    towow = repo / ".towow"
    towow.mkdir()
    base = fork_verdict_dir(repo)
    base.mkdir(parents=True)
    (base / "some-subdir").mkdir()
    orphan = _plant_orphan(repo, age_s=ORPHAN_FORK_VERDICT_MAX_AGE_S + 10)

    swept = sweep_orphan_fork_verdict_dropfiles(towow)

    assert swept == [orphan.name]
    assert (base / "some-subdir").is_dir()
