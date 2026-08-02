"""M-3.1 §5 worktree management — per-task git worktrees + V-01 owner-guard.

# spec source:
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md
#     §5.1 worktree 物理形态 (L474..L483)
#     §5.2 worktree lifecycle (L485..L506)
#     §5.3 V-01 owner-guard 物理实施 (L508..L532) — wrapper-level check
#     §5.4 crash recovery (L534..L550)
#
# E.3 implementation: wrapper-level worktree machinery without OS-level FS guards
# (M-3.1 §5.3 论证 — Claude Code Edit/Write tools 不支持 OS guard,
#  双层防御 = SKILL.md convention + commit gate envelope.write_set check).
"""

from __future__ import annotations

import contextlib
import posixpath
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.l0.fs_guard import assert_rmtree_safe

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=False)


def normalize_guard_target(path: str) -> str:
    """Canonical comparison key for the V-01 write-guard (R05 dispatch-deadlock fix).

    A write_set claim key and the actual edited file path can legitimately denote the
    SAME file in different forms; the guard must compare them apples-to-apples or it
    silently denies every write (the R05 live deadlock — .owner stored 'file:src/...'
    entity keys, the guard compared bare 'harness/src/...' paths → never matched):
      - entity-key form 'file:<path>' (plan write_set claim) vs bare path (actual write)
      - repo-relative 'harness/<pkg-path>' vs v3-package-relative '<pkg-path>'
    '.'/'..' are collapsed FIRST so a traversal string can never alias a legit target
    (e.g. 'harness/../etc/passwd' -> '../etc/passwd', which matches no declared
    write_set entry → still denied — the normalization only unifies the two benign
    conventions, it never widens write permission). Only the 'file:' entity prefix is
    stripped — 'concept:'/other entity types must NOT grant file-write permission.
    """
    s = path.strip()
    if s.startswith("file:"):
        s = s[len("file:") :]
    s = posixpath.normpath(s)
    if s.startswith("harness/"):
        s = s[len("harness/") :]
    return s


def designated_trunk(main_repo: Path) -> str | None:
    """命名主干不变量 (git-discipline keystone, 2026-07-11) 的单一真相源读取器。

    主干身份不再是"主树此刻 HEAD 在哪"这个位置性信号——那正是 2026-07-11 分叉事故的根因:
    一次游离 checkout 把主树切到残留任务分支, 而"merge 进当前分支 / ff 追随当前分支"把这一次
    错位无声放大成十几分钟分叉。主干由 ``<main_repo>/.towow/trunk-branch`` 这个受版本控制的文件
    【显式命名】(内容一行 = 主干分支名), 所有"主干在哪"的判断读它, 不再读 ``git branch --show-current``。

    返回主干分支名 (去首尾空白); 文件缺失 / 为空 → ``None`` = "此仓库未指定主干, 不做主干校验"。
    缺失【不】raise: WorktreeManager 会被非 harness 仓库 / 测试 tmp repo 复用, 那些仓库没有
    trunk-branch 文件, 若在此 raise 会 brick 它们所有 promote (自伤回归)。真正的灾难级保护
    (main 只许快进, 防双主干复发) 落在 git 原生 reference-transaction hook 上, 与本文件在不在
    无关——所以文件缺失只关掉较软的"留在主干上"校验, 绝不放开"main 快进"不变量。
    """
    trunk_file = main_repo / ".towow" / "trunk-branch"
    try:
        name = trunk_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


class OwnerFile(BaseModel):
    """`.owner` file content per M-3.1 §5.1.

    Persisted as JSON at `.towow/worktrees/{task_id}/.owner`. Used by V-01 owner-guard
    wrapper-level check (M-3.1 §5.3).
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    write_set: list[str] = Field(default_factory=list)  # file paths allowed for write
    # branch=None ⇒ detached worktree (无分支隔离, detached-worktree-workstation@v1): the worktree
    # sits at a detached HEAD with no branch (inv-nb-no-branch-switch / inv-nb-per-task-worktree).
    # Reflow back to main goes through serial git-apply (patch-serial-reflow), never a branch merge.
    branch: str | None = None
    created_at: str  # ISO-8601
    retry_count: int = Field(default=0, ge=0)
    last_activity_time: str | None = None  # ISO-8601


class OwnerGuardViolation(Exception):
    """V-01 owner-guard violation — file path not in task.write_set."""


class WorktreePromoteConflict(Exception):
    """M-3.1 §4.2.3 step6 — `git merge` of the task branch into main hit a conflict.

    Raised after the merge is aborted (`git merge --abort`), so main is left UNCHANGED
    (fail-closed: never leave the main branch half-merged). The worktree is preserved for retry.
    """

    def __init__(self, branch: str, git_stderr: str) -> None:
        self.branch = branch
        self.git_stderr = git_stderr
        super().__init__(f"merge of branch {branch!r} into main conflicted (aborted): {git_stderr.strip()}")


class GitDisciplineViolation(WorktreePromoteConflict):
    """回流被拒: 主树 HEAD 不在【命名主干】上 (git-discipline keystone, 2026-07-11 分叉事故根因)。

    2026-07-11 事故: 一次游离 checkout 把主树 HEAD 切到一条残留任务分支, 而 promote 的
    "merge/apply 进【主树当前分支】" 语义把这一次错位无声放大成十几分钟分叉 —— 因为"主干在哪"
    此前是【位置性】的 (= 主树此刻 HEAD 在哪), 没有命名不变量当参照。本异常是 python 断言层
    (治【放大】): promote 入口先断言 "主树 HEAD == 命名主干", 不成立即在【任何主树变异之前】
    fail-closed —— main 纹丝不动、工位保留, 而不是无声堆进错误分支。

    刻意【继承 WorktreePromoteConflict】: orchestrator/cli 三处 promote 调用点已有
    ``except WorktreePromoteConflict`` 兜底 (不 cleanup + 写冲突 marker + 显著告警 + 不崩循环)。
    继承使本异常自动走这条已验证的 fail-closed 兜底路 (工位保留/main 未动), 无需改这三处调用点;
    同时它仍是【独立子类】, 未来若要对它单独 emit/处置, 上游 ``except GitDisciplineViolation``
    先于父类接住即可。``branch`` 用作冲突 marker 的分支字段 (哨兵值), ``git_stderr`` 携带全诊断。
    """

    def __init__(self, current_branch: str, trunk: str) -> None:
        self.current_branch = current_branch
        self.trunk = trunk
        cur = current_branch or "(detached HEAD)"
        super().__init__(
            branch="(git-discipline:off-trunk)",
            git_stderr=(
                f"拒绝回流: 主树 HEAD 在 {cur!r}, 非命名主干 {trunk!r}。"
                f"游离 checkout 未复位, 回流会污染错误分支 —— fail-closed, main 未动、工位保留。"
                f"复位: 在主树 `git checkout {trunk}` 回到命名主干后重试回流。"
            ),
        )


class PromoteResult(BaseModel):
    """Outcome of WorktreeManager.promote_to_main (M-3.1 §4.2.3 step6)."""

    model_config = _STRICT

    promoted: bool
    branch: str | None = None
    merge_commit: str | None = None  # main HEAD after the merge (only when promoted)
    reason: str | None = None  # why NOT promoted (e.g. not_a_git_worktree / nothing_to_merge)


class WorktreeManager:
    """M-3.1 §5 worktree lifecycle manager.

    Phase E.3 scope: create / cleanup / orphan-detect. Does NOT run git operations on
    main repo (test isolation) unless `use_real_git=True`. Defaults to writing skeleton
    directories so tests can exercise the lifecycle without spawning real git worktrees.
    """

    def __init__(self, towow_dir: Path, *, use_real_git: bool = False) -> None:
        self._towow = towow_dir
        self._worktrees_dir = towow_dir / "worktrees"
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._use_real_git = use_real_git

    def create(
        self,
        *,
        task_id: str,
        actor_id: str,
        write_set: list[str],
        branch: str | None = None,
        detached: bool = False,
        write_owner: bool = True,
    ) -> Path:
        """Create per-task worktree + .owner file (M-3.1 §5.2 step 1).

        detached=True (无分支隔离 / detached-worktree-workstation@v1): create the worktree at a
        detached HEAD with no branch (`git worktree add --detach`), honoring inv-nb-no-branch-switch
        / inv-nb-per-task-worktree. branch is forced to None in the .owner file; the per-task patch
        reflows to main through the serial git-apply path (patch-serial-reflow), not a branch merge.
        Default detached=False keeps the legacy branch-per-task behavior for existing callers.

        write_owner=False (inv-nb-fix-no-empty-owner): skip writing the `.owner` file entirely. fix
        worktrees use this — a fix does NOT pre-declare a V-01 write_set, so an empty-write_set
        `.owner` would make the V-01 owner-guard deny *every* write (check_owner_guard: allowed=∅).
        With no `.owner`, the CLI guard skips (read_owner is None ⇒ no V-01); fix write-safety comes
        from its closure_contract + fix_after three-gate verification instead, never V-01.
        """
        if not task_id:
            raise ValueError("task_id required")
        worktree_path = self._worktrees_dir / task_id
        worktree_path.mkdir(parents=True, exist_ok=False)  # exists_ok=False — caller must check
        # detached ⇒ no branch recorded (无分支隔离); 否则沿用传入或默认 task-{id}
        branch = None if detached else (branch or f"task-{task_id}")
        # fnd redteam-nb3-partial-create-isolation-breach: mkdir 成功后, `_git_worktree_add` (或写
        # .owner) 可能失败 (git worktree add 撞已注册 / index.lock / 并发)。若就此把刚建的【空目录】
        # (无 .git) 留在盘上, 调用方的幂等门 (`if not wt_path.exists()`) 会把它误判成"已建成"→ 重派时
        # 跳过 create → 工位无 .git → 工位里的 git 命令穿透到主仓库 = 破坏 inv-nb-per-task-worktree 隔离
        # (独立复查实测 git add 从该目录 staged 进主树 index)。fail-closed: 失败时清掉刚建的空目录残骸,
        # 让重派从干净状态重建。修在共享 create() 一并覆盖 exec/fix 两类调用方。
        try:
            if self._use_real_git:
                self._git_worktree_add(worktree_path, branch, detached=detached)
            if write_owner:
                owner = OwnerFile(
                    task_id=task_id,
                    actor_id=actor_id,
                    write_set=write_set,
                    branch=branch,
                    created_at=datetime.now(tz=UTC).isoformat(),
                )
                (worktree_path / ".owner").write_text(
                    owner.model_dump_json(indent=2), encoding="utf-8",
                )
        except BaseException:
            # 清掉没建成的残骸: git 可能已部分注册 worktree → 先试 git worktree remove (容错),
            # 再删目录树 (assert_rmtree_safe 三道断言, 工位命中 worktrees 白名单段)。绝不留无 .git 空目录。
            with contextlib.suppress(Exception):
                if self._use_real_git and (worktree_path / ".git").exists():
                    self._git_worktree_remove(worktree_path)
            with contextlib.suppress(Exception):
                if worktree_path.exists():
                    self._rmtree(worktree_path)
            raise
        return worktree_path

    def read_owner(self, task_id: str) -> OwnerFile | None:
        path = self._worktrees_dir / task_id / ".owner"
        if not path.exists():
            return None
        return OwnerFile.model_validate_json(path.read_text(encoding="utf-8"))

    def check_owner_guard(self, task_id: str, file_path: str) -> bool:
        """V-01 wrapper-level check (M-3.1 §5.3).

        Raises OwnerGuardViolation if file_path not in task.write_set.
        Caller (skill code) invokes this before write operations.
        """
        owner = self.read_owner(task_id)
        if owner is None:
            raise OwnerGuardViolation(
                f"no .owner file for task_id={task_id} — worktree not created or .owner missing",
            )
        allowed = {normalize_guard_target(w) for w in owner.write_set}
        if normalize_guard_target(file_path) not in allowed:
            raise OwnerGuardViolation(
                f"V-01 violation: file_path={file_path!r} not in task {task_id} "
                f"write_set={owner.write_set}",
            )
        return True

    def enforce_owner_guard(
        self,
        task_id: str,
        file_path: str,
        *,
        event_log: EventLog,
        attempted_by_actor_id: str,
    ) -> bool:
        """T-L3kc-03 — owner-guard check that EMITS a canonical OwnerGuardViolation on violation.

        The event-backed enforcement primitive every write path (skill code / `towow worktree
        guard-check` CLI) calls before writing: on a V-01 violation it writes a real path-B
        OwnerGuardViolation event (audit-visible) and re-raises OwnerGuardViolation so the write
        is blocked; on a legitimate owner write it returns True and emits nothing. This wires the
        previously-dead check (门没接、event 没注册) into a real, observable guard.
        """
        owner = self.read_owner(task_id)
        reason: str | None = None
        owner_actor_id: str | None = None
        if owner is None:
            reason = "no_owner_file"
        else:
            owner_actor_id = owner.actor_id
            allowed = {normalize_guard_target(w) for w in owner.write_set}
            if normalize_guard_target(file_path) not in allowed:
                reason = "not_in_write_set"
            elif attempted_by_actor_id != owner.actor_id:
                reason = "not_owner_actor"
        if reason is None:
            return True
        self._emit_owner_guard_violation(
            event_log,
            task_id=task_id,
            file_path=file_path,
            attempted_by_actor_id=attempted_by_actor_id,
            owner_actor_id=owner_actor_id,
            reason=reason,
        )
        raise OwnerGuardViolation(
            f"V-01 violation ({reason}): actor={attempted_by_actor_id!r} file_path={file_path!r} "
            f"task={task_id} owner={owner_actor_id}",
        )

    @staticmethod
    def _emit_owner_guard_violation(
        event_log: EventLog,
        *,
        task_id: str,
        file_path: str,
        attempted_by_actor_id: str,
        owner_actor_id: str | None,
        reason: str,
    ) -> None:
        from towow.schemas.enums import (
            ActorType,
            BaseClassification,
            EventCategory,
            EventType,
            SubjectEntityType,
            SubjectRole,
        )
        from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

        intent = EventIntent(
            local_intent_id=f"ogv-{task_id}-{abs(hash((task_id, file_path, attempted_by_actor_id))) % (10**10)}",
            event_type=EventType.OWNER_GUARD_VIOLATION,
            event_category=EventCategory.COMMIT,
            payload={
                "task_id": task_id,
                "file_path": file_path,
                "attempted_by_actor_id": attempted_by_actor_id,
                "owner_actor_id": owner_actor_id,
                "reason": reason,
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.SYSTEM.value,
                actor_id="m31_owner_guard",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        )
        event_log.write_direct(intent)

    def increment_retry(self, task_id: str) -> int:
        """Increment .owner.retry_count after commit rejection (§5.2 step 3 reject)."""
        owner = self.read_owner(task_id)
        if owner is None:
            raise OwnerGuardViolation(f"no .owner for task_id={task_id}")
        owner.retry_count += 1
        owner.last_activity_time = datetime.now(tz=UTC).isoformat()
        path = self._worktrees_dir / task_id / ".owner"
        path.write_text(owner.model_dump_json(indent=2), encoding="utf-8")
        return owner.retry_count

    def _assert_main_on_trunk(self, main_repo: Path) -> None:
        """命名主干断言 (git-discipline 层 c, 治【放大】): 主树 HEAD 必须 == 命名主干, 否则 fail-closed。

        在 promote 的每一处主树变异【之前】调用。若主树被游离 checkout 切到别的分支, 断言在任何
        merge/apply/commit 发生前 raise GitDisciplineViolation —— main 纹丝不动、工位保留, 而不是把
        改动无声堆进错误分支 (2026-07-11 分叉事故的放大机理)。

        命名主干未指定 (designated_trunk → None: 非 harness 仓库 / 测试 tmp repo 无 trunk-branch 文件)
        时【跳过】断言 —— 见 designated_trunk 文档: 硬保证 (main 只许快进) 落在 git 原生
        reference-transaction hook 上, 与本断言在不在无关; 此处缺文件只关掉较软的"留在主干"校验,
        绝不 brick 那些仓库所有 promote (自伤回归)。
        """
        trunk = designated_trunk(main_repo)
        if trunk is None:
            return
        cur = self._git(
            ["symbolic-ref", "--short", "HEAD"], cwd=main_repo, check=False,
        ).stdout.strip()
        if cur != trunk:
            raise GitDisciplineViolation(cur, trunk)

    def promote_to_main(
        self,
        task_id: str,
        *,
        commit_message: str,
        worktree_path: Path | None = None,
        branch: str | None = None,
        main_repo: Path | None = None,
        fail_closed_on_noop: bool = False,
    ) -> PromoteResult:
        """M-3.1 §4.2.3 step6 / §5.2 — reflow a task worktree's change back into main.

        TWO reflow paths, picked by whether the worktree is detached (无分支隔离):
          - DETACHED (owner.branch is None / git HEAD detached) → serial git-apply reflow
            (patch-serial-reflow@v1, see _promote_detached_serial_apply): export the worktree's
            base..HEAD diff and `git apply` it onto main inside the commit mutex — no branch, no
            merge (inv-nb-serial-apply-reflow). Baseline drift is handled by an in-worktree rebase;
            an unresolvable conflict fails closed (main untouched, worktree preserved).
          - BRANCH (legacy, owner.branch set) → the original `git merge --no-ff` of the task branch:
          1. stage + commit any pending changes on the worktree's branch;
          2. `git merge --no-ff` that branch into the branch checked out in the main working tree;
          3. return the resulting main HEAD as `merge_commit`.

        Honesty contract:
          - Runs real git ONLY when the target is a real git worktree (a `.git` file/dir exists).
            A skeleton worktree (the default `worktree create` shape, no `.git`) is NOT faked as
            promoted — it returns ``PromoteResult(promoted=False, reason="not_a_git_worktree")``.
          - On merge conflict the merge is aborted and ``WorktreePromoteConflict`` is raised, so the
            main branch is never left half-merged (fail-closed). Caller preserves the worktree.

        CONCURRENCY (M-3.1 §5 red-line): this mutates the main working tree's branch. Callers must
        run it inside / right after the global commit mutex so two sessions never merge into main
        concurrently. Tests MUST use an isolated temp git repo, never the live repo working tree.
        """
        worktree_path = (worktree_path or (self._worktrees_dir / task_id)).resolve()
        main_repo = (main_repo or self._towow.parent).resolve()
        # git-discipline 层 c: 任何主树变异前先断言主树 HEAD == 命名主干 (治 2026-07-11 分叉放大)。
        # 未指定命名主干 (tmp repo / 非 harness 仓库) 时为 no-op, 不影响既有 promote 行为。
        self._assert_main_on_trunk(main_repo)
        if branch is None:
            owner = self.read_owner(task_id)
            branch = owner.branch if owner is not None else None
        # Real git worktrees carry a `.git` file (gitdir pointer) or dir; skeleton dirs do not.
        if not (worktree_path / ".git").exists():
            return PromoteResult(promoted=False, branch=branch, reason="not_a_git_worktree")
        # 无分支隔离 (detached-worktree-workstation / patch-serial-reflow): a detached worktree has no
        # branch to merge — owner.branch is None, or git HEAD itself is detached (symbolic-ref fails).
        # Route it to the serial git-apply reflow (inv-nb-serial-apply-reflow), NOT a branch merge.
        head_detached = (
            self._git(["symbolic-ref", "-q", "HEAD"], cwd=worktree_path, check=False).returncode != 0
        )
        if branch is None or head_detached:
            return self._promote_detached_serial_apply(
                worktree_path=worktree_path, main_repo=main_repo, commit_message=commit_message,
                fail_closed_on_noop=fail_closed_on_noop,
            )
        # ── legacy branch-merge path (existing branch worktrees) ─────────────────────────────────
        # 1. commit pending changes on the worktree branch (only if there is something to commit).
        # 排除运行时文件类 (.owner/.towow) — 同 serial-apply 路 (2026-06-26): 新 commit 不再把工位运行时
        # 文件累积进 branch。注: branch-merge 是 legacy 路, merge 本身仍可能带 branch 历史里的运行时文件;
        # 根因已由 .owner gitignore+untracked (commit 96649f9) 从源头治。
        # 注: 不在负 pathspec 里点名 .owner —— 它已被 gitignore (commit 96649f9), git add -A 自动跳过;
        # 而在负 pathspec 里【点名】一个已 gitignore 的路径会触发 git "paths are ignored" 报错 + exit 1
        # (git 2.50.1 实测), 让本步 fail-closed 误废。.towow 未被 gitignore (工位内是 symlink),
        # 必须显式排除, 故对它的负 pathspec 保留。
        self._git(["add", "-A", "--", ".", ":!.towow"], cwd=worktree_path)
        # .owner 鲁棒撤暂存 (finding f-review-reflow-owner-reset-checkfalse-silent, 邻测
        # test_detached_promote_nothing_to_promote 捕获): .owner 被 gitignore 时 git add -A 本就跳过、此步
        # 幂等 no-op; 若工位的 .owner 未被 gitignore (某些测试 fixture / 客户端布局) 则被 git add -A 扫进 →
        # 这里撤掉暂存, 保证 .owner (per-worktree 运行态) 绝不随 patch 回流, 也使空工位仍判 nothing_to_promote。
        # 用 `git rm --cached --ignore-unmatch` 而非旧的 `git reset check=False` (后者恰在它有用时——.owner
        # 真被暂存——若 reset 失败 (unborn HEAD / index.lock 争用) 会 check=False 静默吞 → .owner 留暂存随
        # commit 进 main = 静默脏 main, 本 finding 病根):
        #   - --cached 只撤暂存、保留工作树 .owner (运行态文件绝不删);
        #   - --ignore-unmatch 对【未暂存/不存在】幂等 exit 0 (gitignore 跳过的常态), 且 git rm 不查 gitignore
        #     (gitignore 只影响 add), 不重蹈 :!.owner 命名 gitignore 路径触发 exit1 的坑;
        #   - git rm 仅操作 index、不读 HEAD → unborn HEAD 下照样工作 (reset 在 unborn HEAD 反而失败);
        #   - check=True (默认) 让 index.lock 争用等【真失败】raise/fail-closed, 不再静默吞。
        self._git(["rm", "--cached", "--ignore-unmatch", "--", ".owner"], cwd=worktree_path)
        if self._git_has_pending(worktree_path):
            self._git(["commit", "-m", commit_message], cwd=worktree_path)
        # 1b. drop .towow runtime files the branch history accidentally carries but main does NOT —
        # they collide with main's live *untracked* files at the same path and would abort the merge,
        # permanently stranding the branch (f-worktree-legacy-branch-merge-no-towow-exclusion-promote-
        # stranding). Mirrors the detached serial-apply path's .towow exclusion; real code conflicts
        # still fail-closed at step 2 below.
        self._strip_branch_only_towow_before_merge(
            worktree_path=worktree_path, main_repo=main_repo, branch=branch,
        )
        # 2. merge the branch into the branch checked out in the main working tree.
        main_head_before = self._git(["rev-parse", "HEAD"], cwd=main_repo).stdout.strip()
        merged = self._git(
            ["merge", "--no-ff", "-m", commit_message, branch],
            cwd=main_repo,
            check=False,
        )
        if merged.returncode != 0:
            self._git(["merge", "--abort"], cwd=main_repo, check=False)
            raise WorktreePromoteConflict(branch, merged.stderr)
        merge_commit = self._git(["rev-parse", "HEAD"], cwd=main_repo).stdout.strip()
        # worktree-promotion-ledger@v1 D2 (branch-merge 侧的同款 no-op): a branch with NO commits
        # ahead of main (`git merge --no-ff` on an already-up-to-date branch) reports success but
        # creates no new commit — main_head_before == merge_commit. Surfacing that as if it were a
        # fresh integration commit is exactly the D2 bug the detached path's "nothing_to_promote"
        # reason guards against (2026-06 production forensics: T_LRF_03 / T_LRF_01 both hit this via
        # the branch-merge path, recording an unrelated pre-existing main HEAD sha as their
        # "integration commit" — a real sha that exists on main, but not one this promotion made).
        if merge_commit == main_head_before:
            return PromoteResult(
                promoted=True, branch=branch, merge_commit=merge_commit, reason="nothing_to_promote",
            )
        return PromoteResult(promoted=True, branch=branch, merge_commit=merge_commit)

    def _strip_branch_only_towow_before_merge(
        self, *, worktree_path: Path, main_repo: Path, branch: str,
    ) -> None:
        """Drop from the branch tip any .towow files the branch tracks but main does NOT, before the
        legacy branch-merge (f-worktree-legacy-branch-merge-no-towow-exclusion-promote-stranding).

        A .towow runtime file accidentally committed into a branch's history (e.g. daemon.pid.lock,
        predating .towow gitignore governance 96649f9) collides with main's live *untracked* file at
        the same path: `git merge` aborts with "untracked working tree files would be overwritten by
        merge" and fail-closes into a permanent promote_conflict marker, stranding the branch and all
        fixes stacked on it. The detached serial-apply path is structurally immune (it diffs base..HEAD
        excluding .towow); this mirrors that exclusion for the branch-merge path.

        Only branch-ONLY .towow paths are stripped. Paths tracked on BOTH sides (legitimate .towow
        ledger such as dispatched markers / cost_cumulative.json) are left untouched — removing them
        would delete main's ledger on merge. Real *code* conflicts are unaffected: the merge in
        promote_to_main still fail-closes on those; this only removes runtime cruft that was never a
        real conflict.
        """
        main_toplevel = self._git_toplevel(main_repo)
        try:
            towow_rel = self._towow.resolve().relative_to(main_toplevel.resolve()).as_posix()
        except ValueError:
            towow_rel = ".towow"
        branch_towow = self._tracked_paths_under(worktree_path, branch, towow_rel)
        if not branch_towow:
            return
        main_towow = self._tracked_paths_under(main_repo, "HEAD", towow_rel)
        branch_only = sorted(branch_towow - main_towow)
        if not branch_only:
            return
        # ls-tree/rm run at the git toplevel with toplevel-relative paths (v3 may nest under harness/);
        # `git rm --cached` edits the INDEX only — it never touches main's live runtime files.
        worktree_toplevel = self._git_toplevel(worktree_path)
        self._git(["rm", "--cached", "--", *branch_only], cwd=worktree_toplevel)
        self._git(
            [
                "commit",
                "-m",
                f"chore: strip {len(branch_only)} branch-only .towow runtime file(s) before promote "
                "(f-worktree-legacy-branch-merge-no-towow-exclusion-promote-stranding)",
            ],
            cwd=worktree_toplevel,
        )

    def _tracked_paths_under(self, repo: Path, ref: str, path_prefix: str) -> set[str]:
        """git-toplevel-relative paths of files tracked under ``path_prefix`` at ``ref``.

        Reads the committed tree (`git ls-tree -r`), so it is symlink-agnostic: a worktree's own
        ``.towow`` may be a runtime symlink, but the branch-tracked .towow blobs live at the real
        ``<toplevel>/<path_prefix>`` path this enumerates.
        """
        toplevel = self._git_toplevel(repo)
        out = self._git(
            ["ls-tree", "-r", "--name-only", "-z", ref, "--", path_prefix], cwd=toplevel,
        ).stdout
        return {p for p in out.split("\0") if p}

    def _promote_detached_serial_apply(
        self, *, worktree_path: Path, main_repo: Path, commit_message: str,
        fail_closed_on_noop: bool = False,
    ) -> PromoteResult:
        """无分支隔离回流 (patch-serial-reflow@v1) — detached 工位改动经【串行 git apply】回主树, 替 merge。

        inv-nb-serial-apply-reflow: 回流必在全局 commit mutex 内串行 git apply (调用方持锁), 无并发、无 merge。
        inv-nb-reflow-fail-closed: apply 不上时 main 工作树纹丝不动 + 工位保留 + raise WorktreePromoteConflict,
          绝不留半 apply。
        detached HEAD 基线漂移: 工位在 detach-base 上开发, main 可能已被别的 task 前移。先按 base..HEAD 导出
          纯改动 diff 试 apply; 失败=漂移 → 在【工位内】rebase 到 main HEAD 刷新基线 (3-way merge 隔离在工位、
          可 abort) 再试一次; rebase 冲突 (真重叠) → abort 还原工位 + fail-closed。main 只吃最终干净 apply。

        fail_closed_on_noop (f-reflow-ledger-fixfail-noop-failopen): 调用方【已确立此工位有必须回流的改动】
          (如 fix-reflow: recorded FixProposed_SHA 不可达 main = 搁浅) 时置 True。此上下文下 base==wt_head
          (工位无新提交) 不是真·无可回流, 而是【工位 HEAD 漂到 main 祖先、fix commit 悬空】的异常 → 必须
          fail-closed (raise + 工位保留), 绝不冒充 nothing_to_promote 成功掩盖搁浅。默认 False = 无此上下文
          (exec 真空 promote / 原始 primitive 诚实 no-op), base==wt_head 仍返 nothing_to_promote 成功。镜像
          姊妹守卫 (空 merge-base fail-closed, fnd redteam-nb2-empty-mergebase-failopen-discard) 的精神。
        """
        # git-discipline 层 c: detached 串行回流入口同样断言主树 HEAD == 命名主干 (fix-reflow 走此路)。
        # 经 promote_to_main 路由进来时上游已断言过一次, 直接调用时此处兜底 —— 幂等只读, 双跑无害。
        self._assert_main_on_trunk(main_repo)
        # 1. commit 工位内 pending 改动 (使 base..HEAD 成为完整 patch)。
        # 排除整个【运行时文件类】: .owner (V-01 元数据) + .towow (账本/锁/symlink, 经 symlink 共享、绝不该
        # 随 patch promote)。它们被 git add -A 扫进 patch 回流到 main 会日积月累弄脏 main 工作树 (2026-06-26
        # 实证: 54 文件脏 + .owner 撞车 + partial-commit 把 main 弄砖, 全源于此)。只让【真代码改动】回流。
        # ':!<path>' = root-anchored exclude pathspec; '.' 提供必须的 positive pathspec。
        # 注: 不在负 pathspec 里点名 .owner —— 它已被 gitignore (commit 96649f9), git add -A 自动跳过;
        # 而在负 pathspec 里【点名】一个已 gitignore 的路径会触发 git "paths are ignored" 报错 + exit 1
        # (git 2.50.1 实测), 让本步 fail-closed 误废。.towow 未被 gitignore (工位内是 symlink),
        # 必须显式排除, 故对它的负 pathspec 保留。
        self._git(["add", "-A", "--", ".", ":!.towow"], cwd=worktree_path)
        # .owner 鲁棒撤暂存 (finding f-review-reflow-owner-reset-checkfalse-silent, 邻测
        # test_detached_promote_nothing_to_promote 捕获): .owner 被 gitignore 时 git add -A 本就跳过、此步
        # 幂等 no-op; 若工位的 .owner 未被 gitignore (某些测试 fixture / 客户端布局) 则被 git add -A 扫进 →
        # 这里撤掉暂存, 保证 .owner (per-worktree 运行态) 绝不随 patch 回流, 也使空工位仍判 nothing_to_promote。
        # 用 `git rm --cached --ignore-unmatch` 而非旧的 `git reset check=False` (后者恰在它有用时——.owner
        # 真被暂存——若 reset 失败 (unborn HEAD / index.lock 争用) 会 check=False 静默吞 → .owner 留暂存随
        # commit 进 main = 静默脏 main, 本 finding 病根):
        #   - --cached 只撤暂存、保留工作树 .owner (运行态文件绝不删);
        #   - --ignore-unmatch 对【未暂存/不存在】幂等 exit 0 (gitignore 跳过的常态), 且 git rm 不查 gitignore
        #     (gitignore 只影响 add), 不重蹈 :!.owner 命名 gitignore 路径触发 exit1 的坑;
        #   - git rm 仅操作 index、不读 HEAD → unborn HEAD 下照样工作 (reset 在 unborn HEAD 反而失败);
        #   - check=True (默认) 让 index.lock 争用等【真失败】raise/fail-closed, 不再静默吞。
        self._git(["rm", "--cached", "--ignore-unmatch", "--", ".owner"], cwd=worktree_path)
        if self._git_has_pending(worktree_path):
            self._git(["commit", "-m", commit_message], cwd=worktree_path)
        wt_head = self._git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
        main_head = self._git(["rev-parse", "HEAD"], cwd=main_repo).stdout.strip()
        # base = 工位与 main 的共同祖先 (= detach 点, 即便 main 已前移也对)。
        base = self._git(
            ["merge-base", wt_head, main_head], cwd=worktree_path, check=False,
        ).stdout.strip()
        # fnd redteam-nb2-empty-mergebase-failopen-discard: 空 merge-base (工位与 main 无共同祖先 —
        # orphan history / 工位 HEAD 被异常重置) **不是** "无可回流", 而是无法安全 diff/apply 的异常。
        # 绝不能与 base==wt_head 合并当 nothing_to_promote 返回 promoted=True —— 那会声称成功、工位
        # base..HEAD 改动从未 apply 到 main、随后 cleanup 删工位 = 永久数据丢失 (fail-open)。fail-closed:
        # raise + 工位保留, 守 inv-nb-reflow-fail-closed (apply 不上时 main 不动 + 工位保留 + 绝不静默)。
        if not base:
            raise WorktreePromoteConflict(
                "(detached)",
                "no merge-base between worktree HEAD and main (unrelated histories / 工位 HEAD 异常重置) "
                "— fail-closed, 工位保留 (绝不静默丢弃工位改动, inv-nb-reflow-fail-closed)",
            )
        if base == wt_head:
            # f-reflow-ledger-fixfail-noop-failopen: 工位无新提交 (HEAD == 共同祖先)。默认这是真·无可
            # 回流 (honest no-op)。但当调用方已确立此工位【有必须回流的改动】(fail_closed_on_noop=True,
            # 如 fix-reflow: recorded FixProposed_SHA 不可达 main = 搁浅) 时, 此 no-op 反而是【工位 HEAD
            # 漂到 main 祖先、fix commit 悬空】的异常 —— 决不能返 promoted=True nothing_to_promote (那会让
            # promote_and_record 冒充 fix×success(sha=None) + cleanup 删工位, 搁浅被永久掩盖, 正是本 finding
            # 的 fail-open)。fail-closed: raise + 工位保留, 镜像姊妹情形空 merge-base 的守卫。
            if fail_closed_on_noop:
                raise WorktreePromoteConflict(
                    "(detached)",
                    "stranded: 工位无新提交 (HEAD==共同祖先) 但调用方已确立此工位有必须回流的改动 "
                    "(如 recorded fix commit 不可达 main) —— 工位 HEAD 漂到 main 祖先 / fix commit 悬空, "
                    "fail-closed 工位保留 (绝不冒充 nothing_to_promote 成功掩盖搁浅, inv-nb-reflow-fail-closed)",
                )
            # 真·无可回流 (honest no-op, main 不动)。
            return PromoteResult(
                promoted=True, branch=None, merge_commit=main_head, reason="nothing_to_promote",
            )
        # 2. 第一试: 按 base..HEAD 纯改动 diff 直接 apply 到 main (无漂移时即成)。
        if self._serial_apply_commit(main_repo, worktree_path, base, wt_head, commit_message):
            merge_commit = self._git(["rev-parse", "HEAD"], cwd=main_repo).stdout.strip()
            return PromoteResult(promoted=True, branch=None, merge_commit=merge_commit)
        # 3. 漂移 → 刷新基线: 工位内 rebase 到 main HEAD (3-way 隔离在工位, 失败可 abort 还原)。
        rebased = self._git(["rebase", main_head], cwd=worktree_path, check=False)
        if rebased.returncode != 0:
            self._git(["rebase", "--abort"], cwd=worktree_path, check=False)
            raise WorktreePromoteConflict(
                "(detached)", rebased.stderr or rebased.stdout or "rebase onto main conflicted",
            )
        # rebase 成功 → 工位 HEAD 现坐在 main_head 上, diff main_head..new_HEAD 与 main 无漂移。
        new_head = self._git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
        if self._serial_apply_commit(main_repo, worktree_path, main_head, new_head, commit_message):
            merge_commit = self._git(["rev-parse", "HEAD"], cwd=main_repo).stdout.strip()
            return PromoteResult(promoted=True, branch=None, merge_commit=merge_commit)
        # 刷新基线后仍 apply 不上 (mutex 内不该发生, 防御 fail-closed)。
        raise WorktreePromoteConflict(
            "(detached)", "git apply failed even after baseline refresh (rebase onto main HEAD)",
        )

    def _serial_apply_commit(
        self,
        main_repo: Path,
        worktree_path: Path,
        base: str,
        head: str,
        commit_message: str,
    ) -> bool:
        """导出 base..head 的 diff → 试 apply 到 main → 精确暂存 patch 文件 + commit。

        返回 True = 已 apply+commit (回流成); False = apply 不上 (漂移/冲突, **main 工作树未动**, 调用方
        决定刷基线/fail-closed)。fail-closed 核: `git apply --check` 先整体验证 (不改 main), 验过才真 apply。
        精确 `git add -- <patch 文件>` 只暂存 patch 触及的文件, 绝不 `git add -A` sweep 进 .towow / 邻居
        会话的无关改动 (并发共享主树纪律)。空 diff = no-op 视作已回流。

        `--no-renames` 两处必带 (finding t-nb-2-reflow-rename-omits-old-path-residual): 默认
        diff.renames=true 下 rename 被折叠成只剩【新名】, name-only 漏掉旧名(删除端) → `git apply`
        删旧名/建新名后, 精确 `git add -- <仅新名>` 不暂存旧名删除 → main HEAD 残留旧名+新名两份 +
        promoted=True 静默错误成功。加 `--no-renames` 让 rename 在 diff 与 name-only 中都表示为
        delete(旧名)+add(新名), files 因此含两端, 精确 add 覆盖旧名删除。承重: 删此标志 →
        test_detached_promote_rename_no_residual_old_file 立即报警。
        """
        diff = self._git(["diff", "--binary", "--no-renames", base, head], cwd=worktree_path).stdout
        files = [
            f.strip()
            for f in self._git(
                ["diff", "--name-only", "--no-renames", base, head], cwd=worktree_path,
            ).stdout.splitlines()
            if f.strip()
        ]
        if not diff.strip() or not files:
            return True  # 空 patch = 无改动, 视作已回流。
        # ★ pathspec 根因修 (finding-reflow-serial-apply-gitadd-cwd-pathspec-orphans): diff/name-only
        # 输出的 files 是【git toplevel 相对】路径 (如 'harness/src/...'), 因 v3 包嵌在 git 仓库子目录
        # harness/ 下、而 main_repo = self._towow.parent = 内层 harness/ ≠ git toplevel (外层)。
        # git apply / git add 必须在【toplevel】跑, 否则把 'harness/src/...' 当成相对 harness/ 解析 =
        # 双前缀 'harness/harness/src/...' → exit 128 → patch 静默孤儿化 (commit accept 但永不上 main)。
        # 非嵌套布局 (toplevel == main_repo) 时 toplevel 即 main_repo, 行为不变。
        toplevel = self._git_toplevel(main_repo)
        # --check: 整体验证 patch 能否 apply (不改 main 工作树); 失败 = 漂移/冲突 → main 纹丝不动。
        if not self._git_apply_diff(toplevel, diff, check_only=True):
            return False
        # ── 集成时刻复验 (T-REFLOW-03, autonomous-valid-reflow-loop): apply 到 main【之前】先在隔离
        # scratch 上物化"集成后状态"(= main 工作树当前内容 + 本 patch), 对改到的 .py 跑语法复验。抓
        # "文本能干净合 (--check 过)、但合出来语法坏"—— 工位 gate 时刻→集成时刻之间 main 漂移、或相邻
        # 改动干净 merge 却破坏语法的缺口 (工位本地 commit 过 gate, 但集成时刻原先不复验是缺口)。
        # 关键: 在 scratch 上验, 失败时 main 工作树【从未被动过】(严格 fail-closed, 满足 done_criteria
        # "在 apply 到 main 之前做、main 还没动"); 只跑 git apply + 内建 compile(), 不抢 commit.lock (无自死锁)。
        ok, detail = self._verify_integration_compile(toplevel, diff, files)
        if not ok:
            raise WorktreePromoteConflict(
                "(detached)",
                f"集成时刻复验 fail-closed (T-REFLOW-03): {detail} — main 未改动 "
                f"(apply 前在隔离 scratch 验), 工位保留待修。",
            )
        # 验过 → 真 apply。--check 已保证可 apply, mutex 内确定性。
        if not self._git_apply_diff(toplevel, diff, check_only=False):
            # --check 过却失败 (mutex 内近乎不可能): 精确还原 patch 触及文件, 绝不留半成品 + fail-closed。
            self._restore_files_to_head(toplevel, files)
            raise WorktreePromoteConflict(
                "(detached)", "git apply 在 --check 通过后失败 — 已精确还原 patch 文件 (fail-closed)",
            )
        # 精确暂存 patch 文件 (不 sweep 无关改动) + commit。files 是 toplevel 相对, 故在 toplevel add/commit。
        self._git(["add", "--", *files], cwd=toplevel)
        self._git(["commit", "-m", commit_message], cwd=toplevel)
        return True

    def _verify_integration_compile(
        self, toplevel: Path, diff: str, files: list[str],
    ) -> tuple[bool, str]:
        """集成时刻复验 (T-REFLOW-03): 在隔离 scratch 上物化"集成后状态"再对改动的 .py 跑语法复验。

        为什么用 scratch 而非"直接 apply 到 main 再验": 失败时 main 工作树【从未被动过】(严格 fail-closed,
        满足 done_criteria "在 apply 到 main 之前做、main 还没动")。复验只跑 git apply (子进程) + 内建
        compile() (in-process, **仅编译不执行**) —— 不抢 commit.lock, 无自死锁 (满足"复验不在持锁时再申请
        同一锁")。

        物化方法: 把 main 工作树 (toplevel) 当前内容里【所有 patch 触及且存在的文件】拷进 scratch 同相对
        路径 (`git apply --check` 验的就是工作树, 故 scratch 须镜像工作树, 不能用 HEAD), 再对 scratch 跑
        同一 diff 的 `git apply` → scratch 即"集成后状态"。modify/delete 的 base 必须在场 (故拷所有触及
        且存在的文件, 不止 .py), add 由 apply 凭空建。然后对每个改动的 .py (patch 删除的跳过) 跑 compile()。

        返回 (True, 说明) 全过 / (False, 首个失败原因)。patch 不含 .py → (True, ...) 直接过 (无可编译,
        但判断真跑过, 非默认 no-op)。compile 只做【语法层】确定性检查 (不 import、不跑测试 → 不会超时、无
        副作用), 这是刻意取舍: 语义层缺口 (如 main 删了本 patch 调用的函数, 编译过但运行 NameError) 不在此
        catch —— 那是 audit fork 的范畴, 此处刻意避开会超时的重检查 (任务"别依赖会超时的 audit fork")。
        """
        py_files = [f for f in files if f.endswith(".py")]
        if not py_files:
            return True, "patch 不含 .py 文件 — 无可编译 (判断真跑过, 非默认 no-op)"
        scratch = Path(tempfile.mkdtemp(prefix="towow-reflow-verify-"))
        try:
            # 镜像 main 工作树里所有 patch 触及且存在的文件 (modify/delete 的 base 需在场; add 由 apply 建)。
            for f in files:
                src = toplevel / f
                if src.is_file():
                    dst = scratch / f
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            # apply 同一 diff 到 scratch → 集成后状态 (--check 已过, 此处近乎确定性)。
            if not self._git_apply_diff(scratch, diff, check_only=False):
                return False, "patch 无法 apply 到 scratch (--check 过后却失败, 异常 → 保守 fail-closed)"
            for f in py_files:
                target = scratch / f
                if not target.exists():
                    continue  # patch 删除的 .py, 无可编译
                try:
                    # read_bytes (非 read_text(utf-8)): compile(bytes) 按 PEP 263 honor 源文件 coding 声明
                    # (finding f-review-reflow-integration-gate-test-degenerate 子项)。合法的非 utf-8 Python
                    # (如带 `# coding: latin-1` 声明的源) 不该被复验【过度拦】死回流; 旧 read_text(utf-8) 对这类
                    # 合法源抛 UnicodeDecodeError → fail-closed = 过度拦。改 read_bytes 后: 无 coding 声明的非
                    # utf-8 字节由 compile 自身报 SyntaxError ("Non-UTF-8 code...") 仍 fail-closed (正确, 那确
                    # 是坏源); NUL 字节报 ValueError 仍 fail-closed。即更精确——只拦真坏源, 不误伤合法非 utf-8。
                    compile(target.read_bytes(), f, "exec")
                except (SyntaxError, ValueError) as exc:
                    # SyntaxError = 集成后语法坏 / 非 utf-8 无 coding 声明; ValueError (UnicodeDecodeError 是其
                    # 子类) = 源含 NUL 字节等 compile() 拒编译。
                    return False, f"{f} 集成后 py 语法复验失败: {exc}"
                except OSError as exc:
                    return False, f"{f} 集成后无法读取 .py 文件: {exc}"
            return True, f"{len(py_files)} 个改动 .py 集成后语法复验通过"
        finally:
            # scratch 是系统 temp 隔离目录 (非 .towow 白名单) → 直接 shutil.rmtree, 不走 assert_rmtree_safe。
            shutil.rmtree(scratch, ignore_errors=True)

    def _git_toplevel(self, repo: Path) -> Path:
        """repo 所在 git 仓库的根 (toplevel)。v3 包嵌在 git 子目录 harness/ 下时, main_repo(=内层
        harness/) ≠ git toplevel(外层); diff/name-only 输出的 files 是 toplevel 相对, 故 apply/add
        必须在 toplevel 跑 (见 _serial_apply_commit 的 pathspec 根因注释)。rev-parse 失败 → 退回 repo
        (非嵌套布局时 toplevel == repo, 退回也对)。"""
        res = self._git(["rev-parse", "--show-toplevel"], cwd=repo, check=False)
        top = res.stdout.strip()
        return Path(top) if top else repo

    @staticmethod
    def _git_apply_diff(repo: Path, diff: str, *, check_only: bool) -> bool:
        """`git apply [--check]` 喂 stdin diff, 返回是否成功 (rc==0)。check_only=True 只验不改。"""
        args = ["git", "apply", "--whitespace=nowarn"]
        if check_only:
            args.append("--check")
        proc = subprocess.run(  # noqa: S603
            args,
            cwd=str(repo),
            input=diff,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    def _restore_files_to_head(self, repo: Path, files: list[str]) -> None:
        """精确把 patch 触及的文件还原到 HEAD (existed → checkout 还原; patch 新增 → 删)。

        只动 patch 触及的文件, 绝不 `git reset --hard` 全树 (会连带毁掉 main 工作树里 .towow / 邻居会话的
        无关改动 — 与 INCIDENT-TOWOW-WIPE 同类数据丢失风险)。"""
        for f in files:
            existed = self._git(["cat-file", "-e", f"HEAD:{f}"], cwd=repo, check=False).returncode == 0
            if existed:
                self._git(["checkout", "HEAD", "--", f], cwd=repo, check=False)
            else:
                (repo / f).unlink(missing_ok=True)

    def promote_and_record(
        self,
        task_id: str,
        worktree_path: Path,
        commit_event_id: str,
        commit_message: str,
        *,
        promotion_path: Literal["exec_merge", "fix_serial_apply"] = "exec_merge",
        event_log: EventLog | None = None,
        fail_closed_on_noop: bool = False,
    ) -> PromoteResult:
        """M-3.1 §4.2.3 step6 — promote_to_main + (成功则 cleanup) + emit WorktreePromoted。

        The single reusable "promote a done worktree back into main" primitive shared by BOTH
        reflow paths:
          - the manual `submit --worktree <path>` fallback (`cli._promote_accepted_worktree`,
            now a thin wrapper that just calls this + echoes) — always exec-style (branch/detached
            task worktree), never fix, hence the `promotion_path` default of "exec_merge" covers it
            without that caller needing to pass it explicitly;
          - the autopilot exec path (orchestrator sees TaskRunCompleted(success) and auto-promotes
            the per-task isolated worktree), also covered by the "exec_merge" default; and
          - the autopilot fix-reflow path (orchestrator sees FixCompleted and reflows the fix's
            detached worktree), which passes promotion_path="fix_serial_apply" explicitly.

        Behaviour:
          1. promote_to_main (real `git merge --no-ff` when worktree is a real git worktree; honest
             no-op `not_a_git_worktree` for a skeleton dir; raises WorktreePromoteConflict on a
             merge conflict AFTER aborting so main is never half-merged — fail-closed).
          2. on success: cleanup the worktree, then emit a WorktreePromoted audit event recording
             the integration (merge) commit sha so the integration world-line is queryable (S5
             back-fill — PatchProposed only carries the worktree-local sha). worktree-promotion-
             ledger@v1 D2: a `reason="nothing_to_promote"` no-op success (detached worktree had
             nothing to reflow) records integration_commit_sha=None — `result.merge_commit` in that
             case is just main's pre-existing HEAD, not a commit this promotion produced, so
             recording it would fabricate a fake git-parity match against an unrelated commit.

        fail_closed_on_noop (f-reflow-ledger-fixfail-noop-failopen): 调用方【已确立此工位有必须回流的
          改动】(fix-reflow: recorded FixProposed_SHA 不可达 main = 搁浅) 时置 True。此上下文下 detached
          serial-apply 的 base==wt_head no-op 不是真·无可回流, 而是工位 HEAD 漂到 main 祖先 / fix commit
          悬空的异常 → promote_to_main raise WorktreePromoteConflict, 本方法【绝不 cleanup、绝不 emit
          success】, 由调用方的 except 走 fail-closed 落账 (fix×failure)。默认 False = exec 真空 promote /
          原始 primitive 诚实 no-op, 仍 cleanup + emit success(sha=None)。

        ⚠ CONCURRENCY / SELF-DEADLOCK (M-3.1 §5 red-line, boundary `a`): the WorktreePromoted event
        is emitted via EventLog.write_direct (path-B, does NOT go through the commit gate /
        attempt_commit). This is mandatory: callers run this whole method inside the global commit
        mutex (.towow/locks/commit.lock); attempt_commit would re-acquire that same fcntl lock from
        the same process → self-deadlock. write_direct only touches the event log's own
        `events.log.lock` (a different lock file), so it composes safely under the commit mutex.
        """
        result = self.promote_to_main(
            task_id,
            commit_message=commit_message,
            worktree_path=worktree_path,
            fail_closed_on_noop=fail_closed_on_noop,
        )
        if result.promoted:
            self.cleanup(task_id, worktree_path=worktree_path)
            self._emit_worktree_promoted(
                task_id=task_id,
                branch=result.branch,
                integration_commit_sha=(
                    None if result.reason == "nothing_to_promote" else result.merge_commit
                ),
                commit_event_id=commit_event_id,
                event_log=event_log,
                promotion_path=promotion_path,
                outcome="success",
            )
        return result

    def emit_promotion_failure(
        self,
        *,
        task_id: str,
        promotion_path: Literal["exec_merge", "fix_serial_apply"],
        failure_reason: str,
        source_event_ref: str,
        event_log: EventLog | None = None,
    ) -> None:
        """worktree-promotion-ledger@v1 覆盖矩阵 2×2 的 failure 半格 (exec×failure / fix×failure)。

        A failed promotion attempt never produces an integration commit, so this always records
        integration_commit_sha=None + branch=None. Callers pass the worktree/finding identity as
        `task_id` (mirrors the legacy `promote_and_record` field naming: for exec it's the task-
        derived worktree id, for fix it's the finding-derived worktree id, or fix_id itself when the
        worktree identity couldn't be resolved) and a `failure_reason` category — the known seed
        categories are auto_promote_conflict / bootstrap_canary_block / other_exception (exec) and
        serial_apply_stranded (fix); the category set is open (concept: 开放可扩类目), callers may
        introduce new ones as long as the underlying detail text is preserved elsewhere (marker
        files / OrchestratorDispatched alert), which this method does not duplicate.
        """
        self._emit_worktree_promoted(
            task_id=task_id,
            branch=None,
            integration_commit_sha=None,
            commit_event_id=source_event_ref,
            event_log=event_log,
            promotion_path=promotion_path,
            outcome="failure",
            failure_reason=failure_reason,
        )

    def _emit_worktree_promoted(
        self,
        *,
        task_id: str,
        branch: str | None,
        integration_commit_sha: str | None,
        commit_event_id: str,
        event_log: EventLog | None,
        promotion_path: Literal["exec_merge", "fix_serial_apply"],
        outcome: Literal["success", "failure"],
        failure_reason: str | None = None,
    ) -> None:
        """Emit a worktree-promotion-ledger@v1 audit event (path-B write_direct, NOT through the gate).

        kind="WorktreePromoted" (outcome=success) keeps the EXACT legacy payload shape (NodeTouched
        envelope, stub_original_payload carries task_id / branch / integration sha / commit_event_id)
        byte-for-byte for the historical ~11 records + every existing reader (orchestrator
        `_worktree_already_promoted` / `_unwrap_stub_rewrap` callers) — only NEW fields are added
        (promotion_path / outcome / failure_reason), nothing renamed or removed.
        kind="WorktreePromotionFailed" (outcome=failure) is a NEW, distinct kind for the exec×failure
        / fix×failure half of the 2×2 coverage matrix (net-new: these paths never emitted anything
        into this ledger before) — kept separate from "WorktreePromoted" so every existing reader
        that filters on that exact kind string (idempotency checks, "was this worktree promoted"
        scans) is unaffected by the newly-added failure records; promotion_ledger_query.py is the one
        place that reads both kinds and merges them into the unified 2×2 view.
        """
        from towow.l0.event_log import EventLog as _EventLog
        from towow.schemas.enums import (
            ActorType,
            BaseClassification,
            EventCategory,
            EventType,
            SubjectEntityType,
            SubjectRole,
        )
        from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

        kind = "WorktreePromoted" if outcome == "success" else "WorktreePromotionFailed"
        log = event_log or _EventLog(self._towow / "events.log")
        intent = EventIntent(
            local_intent_id=f"node_touched-{uuid.uuid4().hex[:8]}",
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={
                "target_entity_type": "task",
                "target_entity_id": task_id,
                "touch_type": "write",
                "kind": kind,
                "stub_original_payload": {
                    "kind": kind,
                    "task_id": task_id,
                    "branch": branch,
                    "integration_commit_sha": integration_commit_sha,
                    "commit_event_id": commit_event_id,
                    "promotion_path": promotion_path,
                    "outcome": outcome,
                    "failure_reason": failure_reason,
                },
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.SYSTEM.value,
                actor_id="m31-worktree-promote",
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id="cli-node_touched",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )
        log.write_direct(intent)

    def cleanup(self, task_id: str, *, worktree_path: Path | None = None) -> None:
        """Remove worktree (after accept per §5.2 step 3 or recovery per §5.4).

        Detects a real git worktree by the presence of `.git` and uses `git worktree remove
        --force` (which deletes the directory itself); a skeleton dir is removed with `_rmtree`.
        `worktree_path` overrides the managed `.towow/worktrees/{task_id}` location (used by the
        submit-accept wiring where `--worktree` may point elsewhere).

        Pure primitive — no ledger emission (mirrors `promote_to_main` being the pure git op below
        `promote_and_record`). Callers that need the deletion traceable use `cleanup_and_record`.
        """
        worktree_path = worktree_path or (self._worktrees_dir / task_id)
        if not worktree_path.exists():
            return
        if (worktree_path / ".git").exists():
            # T-FIX-INC-01: git 路径的删除也吃同一套护栏断言 (git worktree remove --force
            # 同样能整目录扫掉一棵树 — 不能只拦 shutil.rmtree 这一种删法)。
            assert_rmtree_safe(worktree_path)
            # `git worktree remove --force` deletes the working-tree directory; nothing left to rm.
            self._git_worktree_remove(worktree_path)
            if worktree_path.exists():  # defensive: leftover (e.g. nested untracked) → rm remainder
                self._rmtree(worktree_path)
            return
        # Skeleton dir (no real git worktree): remove tree (json/owner + any files).
        self._rmtree(worktree_path)

    def _classify_worktree_space(self, path: Path) -> str:
        """(worktree-promotion-ledger@v1-adjacent) which registry a worktree belongs to —
        `.towow/worktrees/` (towow task workstations) vs `.claude/worktrees/` (Claude Code daemon
        workstations) — per T-REFLOW-MANIFEST's two-space convention. Path-string classification
        (not `self._worktrees_dir` identity) because `cleanup_and_record` is called with an
        arbitrary `worktree_path` override, not just this manager's own managed directory.
        """
        parts = path.resolve().parts
        if ".claude" in parts and "worktrees" in parts:
            return ".claude/worktrees"
        if ".towow" in parts and "worktrees" in parts:
            return ".towow/worktrees"
        return "other"

    def cleanup_and_record(
        self,
        task_id: str,
        *,
        worktree_path: Path | None = None,
        reclaim_reason: str,
        triggered_by: str,
        event_log: EventLog | None = None,
    ) -> str | None:
        """cleanup() + canonical deletion record — the traceable-deletion counterpart of
        `promote_and_record` (worktree-promotion-ledger@v1 D2 记'谁删的'一半:
        f-recover-worktree-cleanup-no-event-emission / debt-0330a4300e34).

        Captures worktree identity (space + pre-deletion HEAD sha, when the worktree is a real git
        worktree — read BEFORE physical removal, since git info is gone after) then delegates the
        physical delete to `cleanup()`, then emits the record ONLY if something was actually there
        to delete (idempotent no-op on an already-gone path records nothing — honest: no deletion
        happened here).

        Deliberately NOT wired into `cleanup()` itself: `dispose_as_equivalent` /
        `dispose_as_empty_shell` (l2/stranded_batch_disposition.py) and `promote_and_record` already
        call bare `cleanup()` and emit their OWN specific canonical record (WorktreeClosed /
        StrandedWorktreeDeletedEmptyShell / WorktreePromoted) with richer reason/evidence — making
        `cleanup()` itself auto-emit would double-record every one of those deletions. This method
        is the dedicated path for callers (currently: `recover --worktree`) that have no such
        specific record of their own.
        """
        resolved = worktree_path or (self._worktrees_dir / task_id)
        if not resolved.exists():
            return None
        space = self._classify_worktree_space(resolved)
        pre_deletion_sha: str | None = None
        if (resolved / ".git").exists():
            rev = self._git(["rev-parse", "HEAD"], cwd=resolved, check=False)
            if rev.returncode == 0:
                pre_deletion_sha = rev.stdout.strip()
        self.cleanup(task_id, worktree_path=worktree_path)
        return self._emit_worktree_deleted(
            worktree_id=task_id,
            space=space,
            pre_deletion_sha=pre_deletion_sha,
            reclaim_reason=reclaim_reason,
            triggered_by=triggered_by,
            event_log=event_log,
        )

    def _emit_worktree_deleted(
        self,
        *,
        worktree_id: str,
        space: str,
        pre_deletion_sha: str | None,
        reclaim_reason: str,
        triggered_by: str,
        event_log: EventLog | None,
    ) -> str:
        """Emit a worktree-deletion audit event — path-B write_direct (NOT through the commit gate),
        mirroring `_emit_worktree_promoted`'s no-self-deadlock contract exactly: callers running
        this inside the global commit mutex (.towow/locks/commit.lock) never re-acquire it, because
        `write_direct` only touches the event log's own `events.log.lock`.

        kind="WorktreeDeletionRecorded" is a NEW, distinct kind (not "WorktreePromoted" /
        "WorktreeClosed" / "StrandedWorktreeDeletedEmptyShell") — existing readers that filter on
        those exact kind strings are unaffected by this addition.
        """
        from towow.l0.event_log import EventLog as _EventLog
        from towow.schemas.enums import (
            ActorType,
            BaseClassification,
            EventCategory,
            EventType,
            SubjectEntityType,
            SubjectRole,
        )
        from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

        kind = "WorktreeDeletionRecorded"
        log = event_log or _EventLog(self._towow / "events.log")
        intent = EventIntent(
            local_intent_id=f"node_touched-{uuid.uuid4().hex[:8]}",
            event_type=EventType.NODE_TOUCHED,
            event_category=EventCategory.ENVELOPE,
            payload={
                "target_entity_type": "task",
                "target_entity_id": worktree_id,
                "touch_type": "write",
                "kind": kind,
                "stub_original_payload": {
                    "kind": kind,
                    "worktree_id": worktree_id,
                    "space": space,
                    "pre_deletion_sha": pre_deletion_sha,
                    "reclaim_reason": reclaim_reason,
                    "triggered_by": triggered_by,
                },
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.SYSTEM.value,
                actor_id="m31-worktree-cleanup",
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id="cli-node_touched",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )
        return log.write_direct(intent).event_id

    def list_active(self) -> list[OwnerFile]:
        """Snapshot of all .owner files (.towow/worktrees/*/.owner)."""
        results: list[OwnerFile] = []
        for sub in self._worktrees_dir.iterdir():
            if not sub.is_dir():
                continue
            owner = self.read_owner(sub.name)
            if owner is not None:
                results.append(owner)
        return results

    def detect_orphans(
        self,
        completed_worktree_names: set[str],
        *,
        lock_timeout_seconds: int = 1800,
    ) -> list[tuple[OwnerFile, str]]:
        """M-3.1 §5.4 orphan detection.

        Returns list of (owner, reason) tuples for worktrees that are orphan/stalled:
          - no .owner file + detached git worktree + work done → orphan_no_owner
          - work done (worktree dir-name in completed_worktree_names) → orphan_task_done
          - last_activity_time + lock_timeout < now → stalled

        ★ 命名空间 (f-tnb5-orphan-no-owner-deadpath-naming-mismatch): `completed_worktree_names`
        是【工位目录名】集 (= `sub.name`), **不是 raw task_id 集**。生产工位目录名永远是包装名 ——
        exec 工位 `_exec_worktree_id(task_id)`、fix 工位 `_fix_worktree_id(fix_key)` (orchestrator.py),
        而完成信号 (TaskRunCompleted.after_state.task_id / FixCompleted) 落的是 raw id。两者命名空间
        不同 → 直接拿 raw id 比工位目录名【恒不相交】(本 finding 修的死路: orphan_no_owner 分支在生产
        永不触发)。归一化 (raw id → 包装工位名) 由 caller 做 (recover callsite, main.py — sha256 单向
        不可逆, 故 forward-map raw→wrapped, 参考 auto-promote `_lookup_dispatched_worktree` 先例);
        本函数只在【工位目录名命名空间】里比 `sub.name`, 两个分支 (no-owner / owner) 一致。

        无 .owner 锚点的工位 (detached-worktree-workstation@v1): fix 会话拿独立 detached 工位但
        write_owner=False 不写 .owner (fix-isolation-post-hoc-gate@v1 — 空 write_set 的 .owner 会
        让 V-01 挡死每次写)。这类工位失去了 `.owner` 这个回收锚点 → 旧实现直接 skip → 会话死后工位永久
        泄漏。判据 (task 描述 "按 worktree list 的 detached 工位 + task 状态判孤儿"): 只有当该子目录
        ① 是 git worktree list 登记的真 detached 工位 (排除 skeleton/stray 目录, 不误删) 且 ② 其
        工位 (= 目录名) 已完成/中止时, 才判 orphan_no_owner。保守: 未完成绝不回收 (可能是在跑的
        fix 会话)。无 .owner 即无 last_activity_time 锚点, 故无 stalled-timeout 兜底 (见返回前注释)。
        """
        results: list[tuple[OwnerFile, str]] = []
        now = datetime.now(tz=UTC)
        detached_paths: frozenset[Path] | None = None  # lazily computed once iff a no-.owner dir hit
        for sub in self._worktrees_dir.iterdir():
            if not sub.is_dir():
                continue
            owner = self.read_owner(sub.name)
            if owner is None:
                # 无 .owner 锚点 → 靠 git worktree list (确认真 detached 工位) + 完成信号判孤儿。
                # 比【工位目录名】sub.name (生产 fix 工位 = _fix_worktree_id 包装名), 不比 raw id。
                if detached_paths is None:
                    detached_paths = self._list_detached_worktree_paths()
                if sub.resolve() in detached_paths and sub.name in completed_worktree_names:
                    results.append((self._synthesize_orphan_owner(sub), "orphan_no_owner"))
                # 工位未完成 / 非 detached 真工位 → 保守不回收 (在跑的 fix 会话 / stray 目录都不误删)。
                # 注: 会话猝死且从未 emit 完成信号的 detached no-owner 工位无完成信号也无
                # last_activity_time 锚点 → 这里捕不到 (诚实记账 uncertainty + finding, 见任务收尾)。
                continue
            # 比【工位目录名】sub.name (生产 exec 工位 = _exec_worktree_id 包装名 = owner.task_id = wid,
            # 但 sub.name 是工位身份的 ground truth — .owner 若 stale 也以目录名为准)。
            if sub.name in completed_worktree_names:
                results.append((owner, "orphan_task_done"))
                continue
            if owner.last_activity_time:
                try:
                    last = datetime.fromisoformat(owner.last_activity_time)
                except ValueError:
                    continue
                if (now - last).total_seconds() > lock_timeout_seconds:
                    results.append((owner, "stalled"))
        return results

    def _list_detached_worktree_paths(self) -> frozenset[Path]:
        """`git worktree list --porcelain -z` 中【detached HEAD】工位的绝对路径集 (resolved)。

        用于 detect_orphans 在无 .owner 时确认一个子目录确实是 git 登记的 detached 工位 (而非
        skeleton/stray 目录), 避免误判误删。Read-only、fail-safe: 非 git 仓 / git 缺失 / 出错一律
        返回空集 (→ 调用方保守不回收), 且 **不依赖 self._use_real_git** — 生产 recover 用
        WorktreeManager(towow) (use_real_git=False) 却照样需要这个查询 (与 cleanup 同, 它的 git 操作
        也独立于该 flag)。porcelain 块形如 `worktree <abs>` / `HEAD <sha>` / (`detached` | `branch ...`)。

        **必须用 -z (NUL 分隔), 不能用裸 --porcelain + splitlines()**
        (f-tnb5-porcelain-newline-injection-latent): porcelain v1 不转义工位路径, 含换行的工位路径
        在按行解析下会被拆成伪造的 `worktree <任意路径>` + `detached` 行, 把任意路径注入 detached 集 →
        绕过本函数为 detect_orphans 提供的 detached 防误删护栏 (本仓有 INCIDENT-TOWOW-WIPE 前科)。
        -z 把每个属性字段改为 NUL 终止, 路径里的换行只是字段内容 (NUL 是路径唯一非法字节, 无法注入)
        → 注入面彻底关死。-z 下块边界是空记录 (块间相邻 NUL → split('\\0') 后的空串)。git 出错时
        fail-safe 返回空集, 与上文一致 (保守不回收, 绝不误删)。
        """
        try:
            result = self._git(
                ["worktree", "list", "--porcelain", "-z"],
                cwd=self._towow.parent,
                check=False,
            )
        except OSError:
            return frozenset()
        if result.returncode != 0:
            return frozenset()
        paths: set[Path] = set()
        current: Path | None = None
        for field in result.stdout.split("\0"):
            if field.startswith("worktree "):
                current = Path(field[len("worktree ") :]).resolve()
            elif field == "detached" and current is not None:
                paths.add(current)
            elif not field:
                current = None  # block boundary (empty NUL record)
        return frozenset(paths)

    @staticmethod
    def _synthesize_orphan_owner(sub: Path) -> OwnerFile:
        """Minimal OwnerFile for a no-.owner orphan worktree (the .owner anchor is gone).

        Only `task_id` (= the worktree dir name) is meaningful — it's what the
        `list[tuple[OwnerFile, str]]` consumers (recover CLI / _echo_orphan_worktrees /
        cleanup) read. actor_id is an explicit placeholder marking the record synthetic;
        created_at carries the dir mtime (best honest "when last touched" we have without
        the .owner) so reports show a sensible time rather than a fabricated one.
        """
        try:
            mtime = datetime.fromtimestamp(sub.stat().st_mtime, tz=UTC).isoformat()
        except OSError:
            mtime = datetime.now(tz=UTC).isoformat()
        return OwnerFile(
            task_id=sub.name,
            actor_id="<no-owner>",
            write_set=[],
            branch=None,
            created_at=mtime,
        )

    # ─── git helpers (only invoked when a real git worktree is involved) ─────

    @staticmethod
    def _git(
        args: list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git subcommand in `cwd`, capturing text output."""
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=str(cwd),
            check=check,
            capture_output=True,
            text=True,
        )

    def _git_has_pending(self, worktree_path: Path) -> bool:
        """True iff there are staged changes to commit (`git diff --cached --quiet` → exit 1)."""
        return self._git(["diff", "--cached", "--quiet"], cwd=worktree_path, check=False).returncode != 0

    def _git_worktree_add(
        self, worktree_path: Path, branch: str | None = None, *, detached: bool = False,
    ) -> None:
        # Run in the OWNING repo (self._towow.parent), NOT the process cwd: `git worktree add`
        # registers the worktree against whatever repo `cwd` resolves to, so omitting cwd would
        # silently attach the worktree to whichever repo the process happens to sit in (a real
        # bug — it could pollute an unrelated live repo). The owning repo is the one containing
        # this manager's `.towow/`.
        #
        # detached=True (无分支隔离, inv-nb-no-branch-switch): `git worktree add --detach` checks the
        # worktree out at a detached HEAD without creating ANY branch — so no `-b` and no branch name
        # ever touches git. detached=False keeps the legacy `-b <branch>` per-task branch.
        if detached:
            args = ["worktree", "add", "--detach", str(worktree_path)]
        else:
            if branch is None:
                raise ValueError("_git_worktree_add: branch required when detached=False")
            args = ["worktree", "add", "-b", branch, str(worktree_path)]
        self._git(args, cwd=self._towow.parent)

    def _git_worktree_remove(self, worktree_path: Path) -> None:
        """`git worktree remove --force` + 分支尸体回收 (git-discipline 修改点 6).

        为什么存在: `git worktree remove` 只解除工位【目录】的注册, 它绑定的分支 ref 在共享的
        对象库里原样留着 —— 长期运行下来仓库堆满"工位已删、分支还在"的尸体 (dogfood 实测单仓
        ~68 条)。判据必须【只】对本管理器自己命名空间下的分支下手 (`task-{id}` / 传入的
        `worktree-*` 惯例前缀), 绝不因为这条通用删除路径而误删调用方手工传入的任意分支名。

        分支名必须在【删除前】查 —— 工位目录一旦被 `git worktree remove` 摘掉就再也读不到它
        HEAD 指向哪条分支了 (detached 工位查询本就失败, 返回 None, 不进回收判断, 与
        detached-worktree-workstation@v1 的"无分支隔离"一致, 不算异常)。

        安全判据【已并入命名主干】而非"从当前分支视角看已 merge" (`git branch -d` 默认那套) ——
        用 `git merge-base --is-ancestor <branch> <命名主干>` (designated_trunk 单一真相源, 与
        _assert_main_on_trunk / reap-merged-branches.sh 同一判据, 不重复发明): 是祖先→真安全可删;
        不是→保留 + 告警一行, 绝不猜测性删除。命名主干未指定 (非 harness 仓库 / tmp repo) 时同样
        保守保留, 不判定。删除用 `-D` (强制) 是因为我们已经用更强的"已并入主干"判据替代了 `-d`
        默认的"已并入当前分支"检查, 而非绕过安全检查。分支若仍被另一个活工位签出, git 会拒绝
        删除 (`cannot delete branch ... checked out at ...`) —— 吞掉这个失败、按"未回收"处理,
        不让一次删不掉的分支炸掉整条 cleanup 路径。
        """
        main_repo = self._towow.parent
        branch_result = self._git(
            ["symbolic-ref", "-q", "--short", "HEAD"], cwd=worktree_path, check=False,
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

        self._git(
            ["worktree", "remove", "--force", str(worktree_path)],
            cwd=main_repo,
        )

        if not branch or not (branch.startswith("task-") or branch.startswith("worktree-")):
            return  # detached (无分支) 或命名空间外的分支 — 不在自动回收范围内, 原样保留
        trunk = designated_trunk(main_repo)
        if trunk is None:
            return  # 未指定命名主干 — 无判据, 保守不删 (与 _assert_main_on_trunk 同一 fail-open 语义)
        is_ancestor = self._git(
            ["merge-base", "--is-ancestor", branch, trunk], cwd=main_repo, check=False,
        )
        if is_ancestor.returncode != 0:
            print(
                f"⚠ 分支尸体回收: 分支 {branch!r} 未确认已并入命名主干 {trunk!r}, 保留 (未回收)",
                file=sys.stderr,
            )
            return
        delete_result = self._git(["branch", "-D", branch], cwd=main_repo, check=False)
        if delete_result.returncode != 0:
            print(
                f"⚠ 分支尸体回收: 分支 {branch!r} 已并入 {trunk!r} 但删除失败 "
                f"({delete_result.stderr.strip()}), 保留",
                file=sys.stderr,
            )

    @staticmethod
    def _rmtree(path: Path) -> None:
        """Best-effort recursive remove (Python stdlib shutil.rmtree alternative).

        T-FIX-INC-01: 删除前必过 assert_rmtree_safe (账本根/哨兵/白名单三道断言) —
        worktree 工位 (.towow/worktrees/<task>) 命中 worktrees 白名单段; 工位里 git
        checkout 出来的 .towow 基线【拷贝】(哨兵 canonical 指真账本位置) 不拦;
        真账本本体/含真哨兵的树 fail-closed 拒删 (INCIDENT-TOWOW-WIPE-2026-06-10)。
        """
        assert_rmtree_safe(path)
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


__all__ = [
    "GitDisciplineViolation",
    "OwnerFile",
    "OwnerGuardViolation",
    "PromoteResult",
    "WorktreeManager",
    "WorktreePromoteConflict",
    "designated_trunk",
]
