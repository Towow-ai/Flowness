"""GO session lock helpers — T-E5 (RUN-004 prereq for nested-spawn防爆).

# spec source:
#   harness/docs/dogfood-runs/DOGFOOD-RUN-003-FINDINGS.md (T-E5 prereq)
#   02-meta-and-requirements/SYSTEM-REQUIREMENTS.md §四 P-12 + C-05 (1 worktree =
#     1 bg session = 1 GOAL granularity)
#
# Purpose:
#   A background session running /goal 模式时，如果其内部某个 phase 想再 spawn
#   下一个 bg session（嵌套 spawn），按 C-05 1:1:1 原则应当 short-circuit —
#   要么放弃 spawn、改为 internal dispatch（当前 session 直接装下个 skill 人格），
#   要么 raise NestedGoalSpawnError 让 caller 重新决策。
#
#   防 fork 爆炸：bg session 内每个 phase 又 spawn 新 bg session → 子孙 session
#   无限嵌套；同时 goal_session_id 链断裂，escalation pause-resume 找不到 owner。
#
# Mechanism:
#   `.towow/locks/goal_session.lock` JSON 文件，含 goal_session_id + worktree_path
#   + started_at. goal_spawn CLI 在 emit GoalSessionStarted 后写入 target worktree
#   的 lock；GoalSessionTerminated 时清除（待 future CLI 落地）.
#
#   spawn_bg_session caller 在调 helper 前调 is_in_goal_session(current_worktree)
#   检测：True 时改走 internal dispatch / raise NestedGoalSpawnError；False 时
#   走正常 spawn 路径.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


class NestedGoalSpawnError(RuntimeError):
    """Raised when spawn_bg_session is called from within an active GO session."""


@dataclass(frozen=True)
class GoalLockInfo:
    """Contents of `.towow/locks/goal_session.lock`."""

    goal_session_id: str
    worktree_path: str
    started_at: str


def _lock_path(towow_dir: Path) -> Path:
    return towow_dir / "locks" / "goal_session.lock"


def is_in_goal_session(towow_dir: Path) -> tuple[bool, GoalLockInfo | None]:
    """Check if the given .towow dir has an active goal_session.lock.

    Returns:
        (True, GoalLockInfo) if lock present and parseable; (False, None) otherwise.
        Malformed lock returns (False, None) — fail-open per principle of "no lock =
        not in goal session" (the lock is the positive signal, not its parse-ability).
    """
    path = _lock_path(towow_dir)
    if not path.exists():
        return False, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return True, GoalLockInfo(
            goal_session_id=str(data["goal_session_id"]),
            worktree_path=str(data["worktree_path"]),
            started_at=str(data["started_at"]),
        )
    except (OSError, json.JSONDecodeError, KeyError):
        return False, None


def acquire_goal_lock(
    towow_dir: Path,
    goal_session_id: str,
    worktree_path: str,
    *,
    started_at: str | None = None,
) -> GoalLockInfo:
    """Write `.towow/locks/goal_session.lock` for the given GO session.

    Raises:
        NestedGoalSpawnError: lock already exists (active GO session in this worktree).
        OSError: .towow/locks/ dir missing or unwritable.
    """
    path = _lock_path(towow_dir)
    if path.exists():
        existing_in_session, existing_info = is_in_goal_session(towow_dir)
        if existing_in_session and existing_info is not None:
            raise NestedGoalSpawnError(
                f"goal_session.lock already exists for {existing_info.goal_session_id} "
                f"(worktree={existing_info.worktree_path}); refusing to acquire nested.",
            )
        raise NestedGoalSpawnError(
            f"goal_session.lock exists but unparseable at {path}; manual cleanup required.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    info = GoalLockInfo(
        goal_session_id=goal_session_id,
        worktree_path=worktree_path,
        started_at=started_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    path.write_text(
        json.dumps(
            {
                "goal_session_id": info.goal_session_id,
                "worktree_path": info.worktree_path,
                "started_at": info.started_at,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return info


def release_goal_lock(towow_dir: Path, *, expected_owner: str | None = None) -> bool:
    """Remove `.towow/locks/goal_session.lock`. Returns True iff a lock was removed.

    expected_owner 守卫 (finding-goal-terminate-no-explicit-gid-neighbor-kill-1):
        autopilot 多 goal session 并发共享同一 `.towow` 时, goal_session.lock 是单指针锁 —
        晚到的邻居 spawn 会覆盖前者。收尾若无条件 unlink, 会删掉邻居的锁 (邻居锁被释放后
        可被第三方双抢 = double-drive)。给定 expected_owner (调用者自己的 gid) 时, 仅当锁
        owner == expected_owner 才 unlink; owner 不符 (锁已被邻居覆盖) 或锁不可解析所有权 →
        跳过不碰, 返回 False。expected_owner=None → 无条件 unlink (单会话向后兼容, 原行为)。
    """
    path = _lock_path(towow_dir)
    if not path.exists():
        return False
    if expected_owner is not None:
        try:
            owner = str(json.loads(path.read_text(encoding="utf-8"))["goal_session_id"])
        except (OSError, json.JSONDecodeError, KeyError):
            return False  # 锁不可解析所有权 → 保守跳过, 不删无法验主的锁
        if owner != expected_owner:
            return False  # 邻居锁 — 不碰
    path.unlink()
    return True


__all__ = [
    "GoalLockInfo",
    "NestedGoalSpawnError",
    "acquire_goal_lock",
    "is_in_goal_session",
    "release_goal_lock",
]
