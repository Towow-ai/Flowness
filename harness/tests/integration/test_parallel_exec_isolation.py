"""B1 (PARALLEL-EXEC-FIX-DESIGN) — 隔离工位 + 事件回流 真跨进程集成测试 (T10).

work/interview 教训: 同进程 CliRunner 测不出跨进程/文件系统级行为。这里用真 git repo +
真 git worktree + 真 subprocess 验三件事:
  1. _prepare_exec_worktree 造出真 git worktree (独立分支) + .owner + .towow symlink 回主。
  2. 从工位 cwd 起的【真子进程】写事件 → 物理落在主 .towow 的账本 (单源回流, DOGFOOD-001-V)。
  3. 两个并行工位互不踩: 各自分支、各自 .owner、共享同一主账本。
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

from towow.l2.orchestrator import _exec_worktree_id, _prepare_exec_worktree

if TYPE_CHECKING:
    from pathlib import Path


def _make_git_repo_with_towow(tmp_path: Path) -> Path:
    """真 git repo (有一个 commit) + .towow 骨架 — 工位 worktree 的母体。"""
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    towow = repo / ".towow"
    (towow / "locks").mkdir(parents=True)
    (towow / "events.log").write_text("", encoding="utf-8")
    return repo


def test_workstation_created_with_owner_and_shared_towow(tmp_path: Path) -> None:
    repo = _make_git_repo_with_towow(tmp_path)
    towow = repo / ".towow"
    wt, err = _prepare_exec_worktree(
        towow, "T-task one §特殊", actor_id="test-orch", write_set=["concept:c1"],
    )
    assert wt is not None, err
    # 真 git worktree + 独立分支
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    wid = _exec_worktree_id("T-task one §特殊")
    assert f"task-{wid}" in branches
    # .owner 写权限声明 (V-01)
    owner = json.loads((wt / ".owner").read_text(encoding="utf-8"))
    assert owner["write_set"] == ["concept:c1"]
    # .towow 是回流主账本的 symlink (硬上线门)
    assert (wt / ".towow").is_symlink()
    assert (wt / ".towow").resolve() == towow.resolve()
    # 幂等: 再备同一工位不炸 (失败重派复用)
    wt2, err2 = _prepare_exec_worktree(
        towow, "T-task one §特殊", actor_id="test-orch", write_set=["concept:c1"],
    )
    assert wt2 == wt, err2


def test_subprocess_write_from_workstation_lands_in_main_ledger(tmp_path: Path) -> None:
    """真子进程在工位 cwd 经相对路径 .towow/events.log 写 → 物理落主账本 (单源)。"""
    repo = _make_git_repo_with_towow(tmp_path)
    towow = repo / ".towow"
    wt, err = _prepare_exec_worktree(towow, "T-flow", actor_id="t", write_set=[])
    assert wt is not None, err
    code = (
        "import pathlib\n"
        "p = pathlib.Path('.towow/events.log')\n"
        "f = p.open('a', encoding='utf-8')\n"
        "f.write('{\"marker\": \"from-workstation\"}\\n')\n"
        "f.close()\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=wt, check=True)
    main_log = (towow / "events.log").read_text(encoding="utf-8")
    assert "from-workstation" in main_log, "工位写的事件没回流主账本 — 单源破裂"


def test_two_parallel_workstations_isolated_branches_shared_ledger(tmp_path: Path) -> None:
    repo = _make_git_repo_with_towow(tmp_path)
    towow = repo / ".towow"
    wa, ea = _prepare_exec_worktree(towow, "T-A", actor_id="t", write_set=["concept:a"])
    wb, eb = _prepare_exec_worktree(towow, "T-B", actor_id="t", write_set=["concept:b"])
    assert wa is not None, ea
    assert wb is not None, eb
    assert wa != wb
    # 各自分支互不踩 (代码写隔离), 写各自文件后互不可见
    (wa / "a.txt").write_text("A", encoding="utf-8")
    (wb / "b.txt").write_text("B", encoding="utf-8")
    assert not (wa / "b.txt").exists()
    assert not (wb / "a.txt").exists()
    # 共享同一主账本 (事件单源)
    assert (wa / ".towow").resolve() == (wb / ".towow").resolve() == towow.resolve()
