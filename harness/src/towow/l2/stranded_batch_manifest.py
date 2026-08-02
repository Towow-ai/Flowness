"""T-REFLOW-MANIFEST — 冻结存量搁浅工位批处置枚举 manifest (stranded-worktree-batch-disposition@v2)。

# spec source:
#   .towow/capsules/sessions/sess-work-4ece7a68009c-neighborhood.md
#     stranded-worktree-batch-disposition@v2 §完成判据 / functional-equivalence-evidence-tier@v1

现状: 冻结枚举 manifest 不存在, 只有 owner 口述计数 (nj-374b91fb: 59 = 18 真孤儿/13 已等价/28 空壳)。
三计数打架 (口述 59 / 陈旧 backlog 文件 53 / 冻结执行时刻 git worktree list 实况) 必调和 —— 不静默取
其一。冻结 = 在【冻结执行时刻】对两类空间 (.towow/worktrees/、.claude/worktrees/) 各枚举一次磁盘实际
存在的工位, 逐个记录所属空间 + skeleton 标记 (磁盘存在但未被 `git worktree` 注册, 如 T-FIX-B4-03-demo)
+ 一个【初始猜测】。该猜测不是终判——终判是下游 T-REFLOW-BATCH-EXECUTE 按
functional-equivalence-evidence-tier@v1 重校验的活; 本模块只在【硬证据】(与 base 零内容差异) 下猜
empty_shell, 其余一律 needs_reverify (不用 merge-base 祖先关系猜, 那是 NS1 non_signal)。

冻结后成员集合不可变: 同一 batch_id 再次调用 freeze_manifest 直接返回既有记录, 不重扫、不重新 emit ——
下游 (BATCH-EXECUTE) manifest↔终态 1:1 全锚这份冻结记录, 复算不能因磁盘后续变化而漂移。

emit 走 path-B NodeTouched (kind=StrandedBatchManifestFrozen, stub_original_payload 载荷), 与既有
WorktreePromoted / OrchestratorDispatched 同一惯例 (EventType.NODE_TOUCHED 在
schemas.payloads.registry.PAYLOAD_VALIDATION_EXEMPT 内, stub-rewrap 额外字段不会被 strict schema 拒)。
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import _unwrap_stub_rewrap
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

MANIFEST_FROZEN_KIND = "StrandedBatchManifestFrozen"
SPACE_TOWOW = "towow"
SPACE_CLAUDE = "claude"
GUESS_EMPTY_SHELL = "empty_shell"
GUESS_NEEDS_REVERIFY = "needs_reverify"

_MANIFEST_CONCEPT_ID = "stranded-worktree-batch-disposition@v2"
_ACTOR_ID = "stranded-batch-manifest"


@dataclass(frozen=True)
class ManifestMember:
    """一个被冻结批次成员: 工位目录名 + 所属空间 + skeleton 标记 + 初始三分类猜测。"""

    worktree_id: str
    space: str
    skeleton: bool
    initial_classification_guess: str


@dataclass(frozen=True)
class ReconciliationRecord:
    """59(口述)/53(陈旧 backlog)/冻结实况三计数调和留痕——每个差异成员都有理由, 不静默漏项。"""

    owner_verbal_count: int
    owner_verbal_breakdown: dict[str, int]
    backlog_file_count: int
    frozen_member_count: int
    vanished_from_backlog: tuple[dict[str, str], ...]
    new_since_backlog: tuple[dict[str, str], ...]
    active_session_excluded: tuple[dict[str, str], ...]
    notes: str


@dataclass(frozen=True)
class FrozenManifest:
    """冻结记录: 已落账的 canonical 事件 + 成员集合(不可变) + 调和留痕。"""

    event_id: str
    batch_id: str
    frozen_at: str
    members: tuple[ManifestMember, ...]
    reconciliation: ReconciliationRecord
    already_frozen: bool  # True = 本次调用命中既有冻结记录 (幂等空转), 非本次新 emit


def _git_registered_worktree_paths(repo_dir: Path) -> set[str]:
    """git 当前注册的工位绝对路径集合 (resolved), 经 `git worktree list --porcelain`。"""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],  # noqa: S607
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(str(Path(line[len("worktree "):]).resolve()))
    return paths


def _worktree_head_sha(worktree_path: Path) -> str | None:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _has_only_owner_metadata(worktree_path: Path) -> bool:
    """目录内唯一内容就是我们自己的 `.owner` 记账文件 (无可保全物)。"""
    try:
        entries = list(worktree_path.iterdir())
    except OSError:
        return False
    return entries == [worktree_path / ".owner"]


def _has_uncommitted_or_untracked_content(worktree_path: Path) -> bool:
    """工作目录内是否存在未提交(staged/unstaged)或未跟踪(untracked)的真实改动。

    只比较两个已提交 commit 间的 `git diff --stat` 会漏掉『fork 出来后从未 commit、但工作目录里
    躺着真实内容』的搁浅工位(finding f-treflow-manifest-emptyshell-ignores-uncommitted)——这里必须
    先查工作目录实况, 查询本身失败时保守当作『有内容』(fail-closed, 不放行 empty_shell 判定)。
    """
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(worktree_path), "status", "--porcelain"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def _guess_initial_classification(
    repo_dir: Path,
    worktree_path: Path,
    *,
    skeleton: bool,
    main_branch: str,
) -> str:
    """初始猜测(非终判): 只在硬证据(与 base 零内容差异, 含工作目录未提交/未跟踪内容)下猜
    empty_shell, 其余 needs_reverify。"""
    if skeleton:
        return GUESS_EMPTY_SHELL if _has_only_owner_metadata(worktree_path) else GUESS_NEEDS_REVERIFY
    if _has_uncommitted_or_untracked_content(worktree_path):
        return GUESS_NEEDS_REVERIFY
    sha = _worktree_head_sha(worktree_path)
    if sha is None:
        return GUESS_NEEDS_REVERIFY
    base_result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_dir), "merge-base", sha, main_branch],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    base_sha = base_result.stdout.strip()
    if base_result.returncode != 0 or not base_sha:
        return GUESS_NEEDS_REVERIFY
    diff_result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_dir), "diff", "--stat", base_sha, sha],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if diff_result.returncode == 0 and not diff_result.stdout.strip():
        return GUESS_EMPTY_SHELL
    return GUESS_NEEDS_REVERIFY


def scan_candidate_worktrees(
    *,
    towow_worktrees_dir: Path,
    claude_worktrees_dir: Path,
    repo_dir: Path,
    main_branch: str = "main",
) -> list[ManifestMember]:
    """枚举【冻结执行时刻】两类空间上磁盘实际存在的工位(含 skeleton), 逐个算初始猜测。

    不靠 backlog 文件、不靠 owner 口述——直接读磁盘 + git 注册表, 这是"冻结"这一步本身要做的事
    (下游消费方才是那个"不靠运行时重扫推断成员集"的一方)。
    """
    registered = _git_registered_worktree_paths(repo_dir)
    members: list[ManifestMember] = []
    for space, base_dir in ((SPACE_TOWOW, towow_worktrees_dir), (SPACE_CLAUDE, claude_worktrees_dir)):
        if not base_dir.is_dir():
            continue
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir():
                continue
            skeleton = str(entry.resolve()) not in registered
            guess = _guess_initial_classification(
                repo_dir, entry, skeleton=skeleton, main_branch=main_branch,
            )
            members.append(
                ManifestMember(
                    worktree_id=entry.name,
                    space=space,
                    skeleton=skeleton,
                    initial_classification_guess=guess,
                ),
            )
    return members


def _load_backlog(backlog_path: Path) -> list[str]:
    if not backlog_path.is_file():
        return []
    try:
        data = json.loads(backlog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, str)]


def reconcile_against_backlog(
    members: list[ManifestMember],
    backlog_path: Path,
    *,
    owner_verbal_count: int,
    owner_verbal_breakdown: dict[str, int],
    active_session_excluded: list[dict[str, str]] | None = None,
    notes: str = "",
    on_disk_ids: set[str] | None = None,
) -> ReconciliationRecord:
    """59(口述)/53(陈旧 backlog)/冻结实况三计数调和——每个差异成员都留下"为何在/不在本批"的理由。

    on_disk_ids (scope 正交修): vanished/new_since 是【backlog 快照 vs 冻结时刻磁盘实况】的调和, 与
    "本批处置哪几个(scope)"正交。scope 收窄 members 后, 若仍拿 members 算 vanished, 会把"被 scope 排除
    但仍在盘"的工位误打成"磁盘已不存在"(vanished), 而它同时在 active_session_excluded("磁盘存在")—— 同
    一工位落进两个语义互斥列表 = 不可变账本里的自相矛盾陈述(2026-07-06 首次真跑实锤: T-FIX-B4-03-demo)。
    故 vanished/new 必须对【全部在盘 scanned 全集】算, 由 on_disk_ids 传入; None = 旧行为(members 即全部
    在盘, 无 scope 场景等价)。"""
    backlog = _load_backlog(backlog_path)
    backlog_set = set(backlog)
    member_ids = {m.worktree_id for m in members}
    disk_ids = on_disk_ids if on_disk_ids is not None else member_ids
    vanished = sorted(backlog_set - disk_ids)
    new_since = sorted(disk_ids - backlog_set)
    return ReconciliationRecord(
        owner_verbal_count=owner_verbal_count,
        owner_verbal_breakdown=dict(owner_verbal_breakdown),
        backlog_file_count=len(backlog),
        frozen_member_count=len(members),
        vanished_from_backlog=tuple(
            {
                "worktree_id": w,
                "reason": "在陈旧 backlog 文件中登记, 冻结执行时刻磁盘已不存在(已消失/已由其他路径处置)",
            }
            for w in vanished
        ),
        new_since_backlog=tuple(
            {
                "worktree_id": w,
                "reason": "冻结执行时刻磁盘存在, 陈旧 backlog 文件未登记(backlog 快照早于其产生, 或原扫描遗漏)",
            }
            for w in new_since
        ),
        active_session_excluded=tuple(active_session_excluded or ()),
        notes=notes,
    )


def _member_to_dict(m: ManifestMember) -> dict[str, Any]:
    return {
        "worktree_id": m.worktree_id,
        "space": m.space,
        "skeleton": m.skeleton,
        "initial_classification_guess": m.initial_classification_guess,
    }


def _reconciliation_to_dict(r: ReconciliationRecord) -> dict[str, Any]:
    return {
        "owner_verbal_count": r.owner_verbal_count,
        "owner_verbal_breakdown": r.owner_verbal_breakdown,
        "backlog_file_count": r.backlog_file_count,
        "frozen_member_count": r.frozen_member_count,
        "vanished_from_backlog": list(r.vanished_from_backlog),
        "new_since_backlog": list(r.new_since_backlog),
        "active_session_excluded": list(r.active_session_excluded),
        "notes": r.notes,
    }


def _parse_frozen_payload(event_id: str, payload: dict[str, Any], *, already_frozen: bool) -> FrozenManifest:
    members = tuple(
        ManifestMember(
            worktree_id=m["worktree_id"],
            space=m["space"],
            skeleton=m["skeleton"],
            initial_classification_guess=m["initial_classification_guess"],
        )
        for m in payload["members"]
    )
    r = payload["reconciliation"]
    reconciliation = ReconciliationRecord(
        owner_verbal_count=r["owner_verbal_count"],
        owner_verbal_breakdown=dict(r["owner_verbal_breakdown"]),
        backlog_file_count=r["backlog_file_count"],
        frozen_member_count=r["frozen_member_count"],
        vanished_from_backlog=tuple(r["vanished_from_backlog"]),
        new_since_backlog=tuple(r["new_since_backlog"]),
        active_session_excluded=tuple(r["active_session_excluded"]),
        notes=r["notes"],
    )
    return FrozenManifest(
        event_id=event_id,
        batch_id=payload["batch_id"],
        frozen_at=payload["frozen_at"],
        members=members,
        reconciliation=reconciliation,
        already_frozen=already_frozen,
    )


def find_frozen_manifest(event_log: EventLog, *, batch_id: str) -> FrozenManifest | None:
    """幂等读: 该 batch_id 若已冻结过, 返回既有记录(不重扫、不重复 emit)。"""
    for rec in event_log.all_records():
        effective_type, payload = _unwrap_stub_rewrap(rec)
        if effective_type != MANIFEST_FROZEN_KIND:
            continue
        if payload.get("batch_id") != batch_id:
            continue
        return _parse_frozen_payload(rec.event_id, payload, already_frozen=True)
    return None


def freeze_manifest(
    *,
    event_log: EventLog,
    towow_worktrees_dir: Path,
    claude_worktrees_dir: Path,
    repo_dir: Path,
    backlog_path: Path,
    batch_id: str,
    owner_verbal_count: int,
    owner_verbal_breakdown: dict[str, int],
    now: str,
    main_branch: str = "main",
    active_session_excluded: list[dict[str, str]] | None = None,
    notes: str = "",
    scope_worktree_ids: set[str] | None = None,
    attribution_task_id: str | None = None,
    attribution_run_id: str | None = None,
) -> FrozenManifest:
    """冻结入口: 幂等——同一 batch_id 已冻结过直接返回既有记录, 成员集合不受调用后磁盘变化影响。

    scope_worktree_ids (2026-07-06 首次对活仓库真跑暴露的 gap): scan_candidate_worktrees 对两类空间
    的每个子目录【无过滤枚举】—— 在活仓库上这会把正在跑的他计划工位 / 活 claude 会话工位一并扫进,
    且其中零 committed-diff 的活工位会被 initial guess 猜成 empty_shell (下游删除候选)。冻结不可变 +
    下游 1:1 锚定成员集 → 盲冻会把活工位钉进批处置枚举。scope 给定本批【意图处置】的工位 id 集: 命中的
    成为成员, 其余磁盘工位【不进成员集】但如实记进 reconciliation.active_session_excluded (冻结时刻在盘、
    显式排除在本批外的留痕, 不静默漏项)。None = 旧行为 (枚举全部, 供 repo 已静默 / 全量批场景)。

    attribution_task_id / attribution_run_id (live-target-execution-evidence@v1 · debt-82d4effd33a0 修):
    本事件是"处理真实对象"的真实生产事件, goal 收口门 (goal.live_target_reconciled) 用 this_goal scope
    对它计数须能锚定"这条冻结归哪个 goal 的哪个 task"。旧行为只带 SYSTEM provenance 无 task_id → this_goal
    scope (provenance.task_id ∈ 本 goal task 集) 恒不匹配 → 真冻结了也满足不了 observable (never-done trap)。
    调用方 (真在跑处置的 EXECUTE task 会话) 传入本 task 的 task_id/run_id, emit 进 provenance → this_goal
    可满足。默认 None = 旧行为, 向后兼容 (无归属的历史/系统冻结不受影响)。"""
    existing = find_frozen_manifest(event_log, batch_id=batch_id)
    if existing is not None:
        return existing

    scanned = scan_candidate_worktrees(
        towow_worktrees_dir=towow_worktrees_dir,
        claude_worktrees_dir=claude_worktrees_dir,
        repo_dir=repo_dir,
        main_branch=main_branch,
    )
    if scope_worktree_ids is None:
        members = scanned
        excluded = list(active_session_excluded or ())
    else:
        members = [m for m in scanned if m.worktree_id in scope_worktree_ids]
        excluded = list(active_session_excluded or ()) + [
            {
                "worktree_id": m.worktree_id,
                "space": m.space,
                "reason": "冻结时刻磁盘存在但显式不在本批 scope(活会话/他计划工位/owner 保留), 本批不处置",
            }
            for m in scanned
            if m.worktree_id not in scope_worktree_ids
        ]
    reconciliation = reconcile_against_backlog(
        members,
        backlog_path,
        owner_verbal_count=owner_verbal_count,
        owner_verbal_breakdown=owner_verbal_breakdown,
        active_session_excluded=excluded,
        notes=notes,
        on_disk_ids={m.worktree_id for m in scanned},
    )
    payload: dict[str, Any] = {
        "kind": MANIFEST_FROZEN_KIND,
        "batch_id": batch_id,
        "frozen_at": now,
        "members": [_member_to_dict(m) for m in members],
        "reconciliation": _reconciliation_to_dict(reconciliation),
    }
    intent = EventIntent(
        local_intent_id=f"node_touched-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "concept",
            "target_entity_id": _MANIFEST_CONCEPT_ID,
            "touch_type": "write",
            "kind": MANIFEST_FROZEN_KIND,
            "stub_original_payload": payload,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=_ACTOR_ID,
            task_id=attribution_task_id,
            run_id=attribution_run_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=_MANIFEST_CONCEPT_ID,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    record = event_log.write_direct(intent)
    return _parse_frozen_payload(record.event_id, payload, already_frozen=False)


__all__ = [
    "GUESS_EMPTY_SHELL",
    "GUESS_NEEDS_REVERIFY",
    "MANIFEST_FROZEN_KIND",
    "SPACE_CLAUDE",
    "SPACE_TOWOW",
    "FrozenManifest",
    "ManifestMember",
    "ReconciliationRecord",
    "find_frozen_manifest",
    "freeze_manifest",
    "reconcile_against_backlog",
    "scan_candidate_worktrees",
]
