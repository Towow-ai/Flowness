"""orphan-worktree-reconciliation-scan@v2 升级回归 (task T-AWARE-03)。

v1 → v2 契约升级验的两件事:
  DC1: 发现机制从"命名规则预测路径 + exists() 检查"升级为【git 地面真相判定 merge-base
       --is-ancestor】—— commit 落进【命名不符 fix__ 规则的复用工位】也能被发现 (根治 verified
       finding f-fixafter-scan-orphaned-fix-worktrees-reused-exec-blindspot)。
  DC2: 全量领先-main 清单 (非仅 SLA 超龄告警子集) 作为 OrphanWorktreeInventoryProduced 一等输出,
       六字段齐全。

RED-LINE: 每个 git-touching 测试跑在【隔离 temp git repo】, 绝不碰 live harness 工作树。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import _fix_commit_reachable_from_main, _fix_worktree_id
from towow.l2.reflow_sentinel import (
    WorktreeInventoryItem,
    _fix_id_from_worktree_name,
    _reverse_lookup_task_or_fix_id,
    run_sentinel_once,
    scan_ahead_of_main_worktrees,
    scan_stranded_past_sla,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """隔离 git repo, 在 main 上一个 base commit + 空 .towow/worktrees。"""
    repo = tmp_path / "isolated_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=str(repo), check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init main")
    (repo / ".towow" / "worktrees").mkdir(parents=True)
    return repo


def _add_ahead_worktree(
    repo: Path, worktree_id: str, *, branch: str | None, filename: str,
) -> Path:
    """在 .towow/worktrees/<worktree_id> 建一个领先 main 一个 commit 的工位。

    branch=None → detached (git worktree add --detach); 否则新命名分支 (模拟 exec 复用工位)。
    """
    wt_path = repo / ".towow" / "worktrees" / worktree_id
    if branch is None:
        _git(repo, "worktree", "add", "--detach", "-q", str(wt_path), "main")
    else:
        _git(repo, "worktree", "add", "-q", "-b", branch, str(wt_path), "main")
    (wt_path / filename).write_text("ahead\n", encoding="utf-8")
    _git(wt_path, "add", "-A")
    _git(wt_path, "commit", "-q", "-m", f"ahead commit in {worktree_id}")
    return wt_path


def test_reused_exec_path_worktree_detected_via_merge_base(temp_git_repo: Path) -> None:
    """DC1: 命名规则外的复用路径工位 (exec 风格命名, 非 fix__<eid>__<sha>) 领先 main 时被 v2 扫出。

    复现 finding f-fixafter-scan-orphaned-fix-worktrees-reused-exec-blindspot 的结构性路径: fix 会话
    被迫复用既有 exec worktree (named branch, 命名不符 fix__ 规则) 直接提交。v1 按 _fix_worktree_id
    反推的期望路径不存在 → exists() 静默判"非搁浅"跳过 → 该孤儿 commit 逃逸。v2 枚举 git 实际登记
    工位 + merge-base --is-ancestor 判定 → 复用工位被检出。
    """
    repo = temp_git_repo
    # exec 风格命名的复用工位 —— 刻意【不】匹配 fix__<eid>__<sha> 命名规则。
    reused_id = "T_PCH_A3_daemon_mailbox_materialize__94f1531fc18e9ec3"
    wt_path = _add_ahead_worktree(
        repo, reused_id, branch="task-T_PCH_A3_daemon_mailbox_materialize__94f1531fc18e9ec3",
        filename="reused.txt",
    )
    head_sha = _git(wt_path, "rev-parse", "HEAD").strip()

    # 前置断言: 该工位的 HEAD 确实【领先 main】(merge-base 地面真相, 非命名推断)。
    assert _fix_commit_reachable_from_main(repo, head_sha) is False

    inventory = scan_ahead_of_main_worktrees(repo / ".towow")
    ids = {item.worktree_id for item in inventory}
    assert reused_id in ids, (
        "命名规则外的复用工位应被 v2 merge-base 扫出 (v1 命名预测会静默漏检)"
    )
    item = next(i for i in inventory if i.worktree_id == reused_id)
    assert item.head_sha == head_sha
    assert item.commits_ahead == 1  # 领先 main 一个 commit — 由 git 地面真相判定, 非路径预测


def test_full_inventory_six_fields_not_only_sla_subset(temp_git_repo: Path) -> None:
    """DC2: 全量领先-main 清单 (非仅 SLA 超龄子集), 六字段齐全, 且落成 canonical 一等输出。

    构造两个领先 main 的工位: 一个超龄 (age ≥ min_age), 一个新鲜 (age < min_age)。v2 全量清单必须
    含【两个】(证明不是只 SLA 超龄子集); 而按 SLA 年龄过滤只留超龄的一个。
    """
    repo = temp_git_repo
    towow_dir = repo / ".towow"
    aged = _add_ahead_worktree(repo, "wt_aged__aaaa1111", branch="task-aged", filename="a.txt")
    fresh = _add_ahead_worktree(repo, "wt_fresh__bbbb2222", branch=None, filename="b.txt")

    # 给超龄工位写一个 .owner 让反查字段有真值 (新鲜工位不写 → 反查失败应为 None, 不丢弃该条)。
    (aged / ".owner").write_text(
        json.dumps({"task_id": "T-AGED-001", "write_set": []}), encoding="utf-8",
    )

    now = 2_000_000_000.0
    min_age = 3600
    os.utime(aged, (now - 100_000, now - 100_000))  # age ≈ 100000s ≫ min_age → 超龄
    os.utime(fresh, (now - 60, now - 60))            # age ≈ 60s < min_age → 新鲜/在飞

    inventory = scan_ahead_of_main_worktrees(towow_dir, now=now)
    by_id = {item.worktree_id: item for item in inventory}
    assert "wt_aged__aaaa1111" in by_id
    assert "wt_fresh__bbbb2222" in by_id, "新鲜工位也须在全量清单里 (不是只 SLA 超龄子集)"

    # 六字段齐全 (每条清单项)。
    for item in inventory:
        assert isinstance(item, WorktreeInventoryItem)
        assert item.worktree_id
        assert isinstance(item.worktree_path, str)
        assert item.worktree_path
        assert item.branch  # 分支名或 "detached"
        assert len(item.head_sha) == 40  # 完整 sha
        assert item.commits_ahead == 1
        assert item.task_or_fix_id is None or isinstance(item.task_or_fix_id, str)
        assert isinstance(item.age_seconds, float)  # mtime 派生

    # 反查: 有 .owner 的超龄工位取到 task_id; 无 .owner 的新鲜工位为 None (不因反查失败丢弃该条)。
    assert by_id["wt_aged__aaaa1111"].task_or_fix_id == "T-AGED-001"
    assert by_id["wt_fresh__bbbb2222"].task_or_fix_id is None
    assert by_id["wt_fresh__bbbb2222"].branch == "detached"

    # "不是只 SLA 超龄子集": 全量清单 2 个, 按 SLA 年龄过滤只剩超龄的 1 个 (严格超集)。
    sla_overage = [i for i in inventory if i.age_seconds is not None and i.age_seconds >= min_age]
    assert len(inventory) == 2
    assert [i.worktree_id for i in sla_overage] == ["wt_aged__aaaa1111"]

    # v1 的 SLA 子集路径 (scan_stranded_past_sla, 靠事件日志推断) 对这两个未登记完工事件的孤儿工位
    # 完全失明 —— 正是 v2 全量清单要补的覆盖面。
    log = EventLog(towow_dir / "events.log")
    assert scan_stranded_past_sla(towow_dir, log, min_age_seconds=min_age, now=now) == []

    # 一等输出: 全量清单落成 canonical OrphanWorktreeInventoryProduced (NodeTouched stub-rewrap),
    # 供下游 merge-audit-gate / valid-reflow-loop 消费。
    report = run_sentinel_once(towow_dir, min_age_seconds=min_age, emit=True, now=now)
    assert report.inventory_count == 2
    assert report.inventory_event_id is not None
    rec = log.get_event(report.inventory_event_id)
    assert rec is not None
    stub = rec.payload["stub_original_payload"]
    assert isinstance(stub, dict)
    assert stub["kind"] == "OrphanWorktreeInventoryProduced"
    assert stub["inventory_count"] == 2
    inv_payload = stub["inventory"]
    assert isinstance(inv_payload, list)
    six_fields = {
        "worktree_id", "worktree_path", "branch", "head_sha",
        "commits_ahead", "task_or_fix_id", "age_seconds",
    }
    for entry in inv_payload:
        assert isinstance(entry, dict)
        assert set(entry.keys()) == six_fields


def test_fix_worktree_reverse_looks_up_fix_id_from_name(temp_git_repo: Path) -> None:
    """finding f-aware03: 无 .owner 的 detached fix 工位 (fix__<slug>__<digest>) 也能反查出 fix_id。

    修前只走 .owner.task_id 路径, fix 工位 (write_owner=False → 无 .owner) task_or_fix_id 恒 None,
    与字段/docstring 承诺的 "task_id 或 fix_id" 失真。修后从工位命名反推: fix_id = slug 段。
    """
    repo = temp_git_repo
    fix_key = "evt-4cdf60a20de04276b8739a2eba4387b0"  # 触发 finding 的 event_id 形态
    wid = _fix_worktree_id(fix_key)  # → fix__evt_4cdf..._<digest>, 与派发侧同款命名
    # detached、【不写 .owner】—— 精确复刻 _prepare_fix_worktree 的 fix 工位形态。
    _add_ahead_worktree(repo, wid, branch=None, filename="fix.txt")

    inventory = scan_ahead_of_main_worktrees(repo / ".towow")
    item = next(i for i in inventory if i.worktree_id == wid)
    assert item.task_or_fix_id is not None, (
        "fix 工位 (fix__<slug>__<digest>, 无 .owner) 应从命名反查出 fix_id, 不再恒 None"
    )
    # slug = fix_key 经 _fix_worktree_id slug 化 (非字母数字→_), 有损但足以指回对应 finding。
    assert item.task_or_fix_id == "evt_4cdf60a20de04276b8739a2eba4387b0"


def test_fix_id_from_worktree_name_dual_and_no_false_match() -> None:
    """反推函数与 _fix_worktree_id 对偶; 不误匹配 exec 命名 / 非 fix 目录名。"""
    # 对偶性: 任意 fix_key 生成的名字都能切回其 slug 段。
    for fix_key in ("T-EXEC-9", "evt-abc123", "f-some-finding-id::lens"):
        name = _fix_worktree_id(fix_key)
        slug = "".join(ch if ch.isalnum() else "_" for ch in fix_key)[:40]
        assert _fix_id_from_worktree_name(name) == slug
    # 不误匹配: exec 复用工位 (无 fix__ 前缀) 与随意目录名 → None。
    assert _fix_id_from_worktree_name("wt_fresh__bbbb2222") is None
    assert _fix_id_from_worktree_name("task-T_PCH_A3__94f1531fc18e") is None
    assert _fix_id_from_worktree_name("fix-done-elsewhere-owner-confirm") is None


def test_owner_task_id_takes_precedence_over_fix_name(tmp_path: Path) -> None:
    """有 .owner.task_id 时优先取它 (保留原 exec 工位行为), 命名反推只是无 .owner 时的兜底。"""
    wt = tmp_path / _fix_worktree_id("evt-xyz")
    wt.mkdir()
    (wt / ".owner").write_text(json.dumps({"task_id": "T-REAL-001"}), encoding="utf-8")
    assert _reverse_lookup_task_or_fix_id(wt) == "T-REAL-001"
