"""带外回流产出哨兵 (out-of-band-reflow-sla-sentinel, T-REFLOW-04)。

为什么存在 (昨晚 53 工位 + 4 冲突卡 2-3 天无人知的系统性根因): 唯一在流的健康信号
DaemonRunCompleted 只证 "orchestrator 循环在转"(心跳), **不证 "活真合回了 main"**(产出)。
daemon 整夜在转、但 0 工位回流, 心跳一路绿 —— 心跳 ≠ 产出。

本哨兵盯【产出 SLA】: "有 TaskRunCompleted(success) 却无 WorktreePromoted 且已超过 min_age 的隔离
工位" = 完工却未在 SLA 内回流。检出即调 reflow_reconcile.emit_stranded_reflow_findings → 走
agent-first-reflow-repair (Finding→Fix), 不自己 promote。

★ 带外 (co-location 陷阱): 本哨兵【必须】作为独立进程跑 (launchd/cron 调 `python -m
towow.l2.reflow_sentinel`), **绝不挂进 orchestrator 的派发循环** —— 若与被监控的 daemon 同生共死,
daemon 一 pause/crash 哨兵也没了, 正是昨晚"心跳在转却没人发现没产出"的同款失明。检查者必须在被检查
机制之外运行。默认 OFF (不调就不跑)。

min_age 阈值: 只对【超龄】的搁浅告警, 避免对刚完工、orchestrator 本轮窗口马上就会 promote 的"在飞"
工位误报 (那些不是搁浅, 是正常在途)。工位年龄取工位目录 mtime (≈ 最后活动/完工时刻)。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import _fix_commit_reachable_from_main
from towow.l2.reflow_reconcile import (
    StrandedWorktree,
    close_disposed_reflow_findings,
    emit_stranded_reflow_findings,
    reconcile_blocked_reflows,
    scan_stranded_worktrees,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

DEFAULT_MIN_AGE_SECONDS = 3600  # 1h: 超过这么久还没回流才算 SLA 违约 (短于此 = 可能在飞, 不误报)

# fix 隔离工位目录名格式 (对偶 orchestrator._fix_worktree_id): `fix__<slug>__<12hex digest>`。
# digest 恒为末尾 `__` + 12 位十六进制, 用它锚定切出 slug (= fix_key 的 slug 化前缀 = 反推的 fix_id)。
_FIX_WORKTREE_NAME_RE = re.compile(r"^fix__(?P<slug>.+)__[0-9a-f]{12}$")

# ── v2 全量清单 (orphan-worktree-reconciliation-scan@v2) ──────────────────────────────────
# v1 只对外暴露 SLA 超龄告警子集; v2 补上【全量领先-main 工位清单】为一等输出, 且发现机制从
# "命名规则预测路径 + 路径存在性检查" 升级为【git 地面真相判定 merge-base --is-ancestor】。
# 根因 (verified finding f-fixafter-scan-orphaned-fix-worktrees-reused-exec-blindspot): commit 可
# 落进命名不符的【复用工位】(如 fix 会话被迫复用既有 exec worktree, 非专属 fix__<eid>__<sha>),
# v1 按 _fix_worktree_id(finding_eid) 反推的期望路径不存在 → exists() 静默判"非搁浅"跳过 → 该孤儿
# commit 永不出现在扫描结果里。v2 不再预测路径, 而是【枚举 git 实际登记的工位】(git worktree list),
# 对每个工位的 HEAD 做 merge-base --is-ancestor <head> main 判定, 只要不是 main 祖先即领先 main、
# 入册 —— 复用工位因此不再逃逸。
# 一等输出 = 经 canonical 事件落账 (OrphanWorktreeInventoryProduced, NodeTouched stub-rewrap 形态,
# 与 WorktreePromoted 同族、无需新增 EventType 枚举), 供下游 agent-advisor-merge-audit-gate@v1 /
# autonomous-valid-reflow-loop 消费。本概念只拥有观测与清单产出权, 不决定 merge/promote。
_SENTINEL_ACTOR_TYPE = ActorType.SYSTEM.value  # 带外自观测, 非交互 CLI session (同 orchestrator 范式)
_SENTINEL_ACTOR_ID = "out-of-band-reflow-sentinel"
_INVENTORY_EVENT_KIND = "OrphanWorktreeInventoryProduced"


def _worktree_age_seconds(sw: StrandedWorktree, now: float) -> float:
    """工位年龄 (秒) = now - 工位目录 mtime (≈ 最后活动/完工时刻)。

    f-review-reflow-sentinel-age-oserror-silent-drop: stat 失败必须区分两类, 别一律返 0.0 把已确认搁浅
    的工位静默判"不超龄"而漏告警 —— 监控自身探针失败应偏告警, 不偏沉默:
      - FileNotFoundError (目录没了 = 已 cleanup / 回流后清理): 合法不超龄 → 0.0。
      - 其余 OSError (读不了: 权限 / IO / 异常): 监控探针自己失败 → float('inf') 当必超 SLA 告警。
    """
    try:
        return max(0.0, now - sw.worktree_path.stat().st_mtime)
    except FileNotFoundError:
        return 0.0  # 目录没了 = 已 cleanup — 合法, 不超龄
    except OSError:
        return float("inf")  # stat 读不了 = 探针失败 — 偏告警, 当必超 SLA (别静默漏掉真搁浅)


def scan_stranded_past_sla(
    towow_dir: Path,
    event_log: EventLog,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    now: float | None = None,
) -> list[StrandedWorktree]:
    """扫出"完工成功却未回流、且工位年龄 ≥ min_age"的隔离工位 (= 超过 SLA 仍未回流的真搁浅)。

    底座是 reflow_reconcile.scan_stranded_worktrees (水位线无关全量扫); 本哨兵在其上加【SLA 年龄过滤】,
    把刚完工还在飞的工位排除掉, 只留真超龄搁浅的。now 可注入便于测试 (默认 time.time())。
    """
    now = time.time() if now is None else now
    stranded = scan_stranded_worktrees(towow_dir, event_log)
    return [sw for sw in stranded if _worktree_age_seconds(sw, now) >= min_age_seconds]


@dataclass(frozen=True)
class WorktreeInventoryItem:
    """全量清单的一条 (orphan-worktree-reconciliation-scan@v2 六字段)。

    字段对应概念定义 (每条清单项):
      - worktree_id / worktree_path: 工位标识与路径 (合为一组)。
      - branch: 分支名, 或标 "detached" (git worktree list porcelain 的 detached 无分支)。
      - head_sha: HEAD / commit sha。
      - commits_ahead: 领先 main 的 commit 数 (rev-list --count main..head); -1 = 不可验证 (bad
        ref / gc — merge-base 判 None 的同因, 如实标, 属概念"诚实残留"范畴, 不当 0 掩盖)。
      - task_or_fix_id: 反查到的 task_id 或 fix_id; 反查失败为 None (概念明文: 不得因此丢弃该条)。
      - age_seconds: 工位年龄 (now - 目录 mtime); None = stat 探针失败 (fail-closed 偏告警语义沿用
        v1, 下游按"疑似超龄"处理, 不当"新鲜"放过)。
    """

    worktree_id: str
    worktree_path: str
    branch: str
    head_sha: str
    commits_ahead: int
    task_or_fix_id: str | None
    age_seconds: float | None

    def as_payload(self) -> dict[str, object]:
        """清单项落 canonical 事件 payload 的 JSON-safe 投影 (age inf 不入 JSON, 用 None 表探针失败)。"""
        return {
            "worktree_id": self.worktree_id,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "commits_ahead": self.commits_ahead,
            "task_or_fix_id": self.task_or_fix_id,
            "age_seconds": self.age_seconds,
        }


def _enumerate_git_worktrees(repo_dir: Path) -> list[tuple[Path, str, str]]:
    """枚举 git 实际登记的工位 (git worktree list --porcelain) → [(path, head_sha, branch|"detached")]。

    这是 v2 的地面真相入口: 不靠命名规则预测路径, 而是问 git "到底有哪些工位、各自 HEAD 指哪"——
    复用工位 (命名不符 fix__ 规则) 因此不再逃逸。porcelain 每条工位块形如:
        worktree <abs path>\nHEAD <sha>\nbranch refs/heads/<name>  (或裸 "detached" 行)\n<空行>
    subprocess 失败 (git 不在 / 超时 / 非 git 仓) → 返 [] (哨兵默认 OFF、带外, 探针自身失败偏静默此层,
    真搁浅由后续轮次 / SLA 子集路径兜底; 不 raise 拖垮带外链)。
    """
    try:
        # list-args + shell=False 已排除注入; S603/S607 是 intentional 外部 git by-PATH 调用
        # (同 orchestrator._fix_commit_reachable_from_main precedent, 但本文件不在 pyproject
        # per-file-ignores、亦不在本 task write_set 内, 故就地 noqa 保持 boundary)。
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_dir), "worktree", "list", "--porcelain"],  # noqa: S607
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    worktrees: list[tuple[Path, str, str]] = []
    cur_path: Path | None = None
    cur_head: str | None = None
    cur_branch = "detached"
    for raw in proc.stdout.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("worktree "):
            cur_path = Path(line[len("worktree ") :])
            cur_head = None
            cur_branch = "detached"
        elif line.startswith("HEAD "):
            cur_head = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            cur_branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur_branch = "detached"
        elif line == "":
            if cur_path is not None and cur_head:
                worktrees.append((cur_path, cur_head, cur_branch))
            cur_path = None
            cur_head = None
            cur_branch = "detached"
    if cur_path is not None and cur_head:  # 末块可能无尾随空行
        worktrees.append((cur_path, cur_head, cur_branch))
    return worktrees


def _commits_ahead_of_main(repo_dir: Path, head_sha: str) -> int:
    """领先 main 的 commit 数 (git rev-list --count main..<head>); 不可验证 (bad ref/gc/异常) → -1。"""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_dir), "rev-list", "--count", f"main..{head_sha}"],  # noqa: S607
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    if proc.returncode != 0:
        return -1
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return -1


def _inventory_age_seconds(worktree_path: Path, now: float) -> float | None:
    """工位年龄 (秒) = now - 目录 mtime; stat 失败 fail-closed → None (偏告警, 沿用 v1 语义)。

    与 v1 _worktree_age_seconds 一致的两类区分, 但清单项用 JSON-safe 的 None 表探针失败 (替代 v1
    的 float('inf') —— inf 不进 canonical JSON payload):
      - FileNotFoundError (目录没了 = 已 cleanup): 合法, 0.0 (不超龄)。
      - 其余 OSError (读不了: 权限/IO): 探针失败 → None (下游按疑似超龄告警处理, 别静默当新鲜)。
    """
    try:
        return max(0.0, now - worktree_path.stat().st_mtime)
    except FileNotFoundError:
        return 0.0
    except OSError:
        return None


def _reverse_lookup_task_or_fix_id(worktree_path: Path) -> str | None:
    """尽力反查工位对应的 task_id / fix_id; 查不到返 None (不丢弃该条)。

    两条反查路径, 覆盖清单里两类工位来源:
      1. exec/派发工位 create 时写 .owner (含 task_id) → 读 .owner.task_id。
      2. fix 隔离工位【不写 .owner】(_prepare_fix_worktree write_owner=False), 但工位目录名由
         orchestrator._fix_worktree_id 编码为 `fix__<slug>__<12hex digest>`, slug = 派发 fix_key
         (= decision.task_id 或触发 finding 的 event_id) 的 slug 化前缀 → 从命名反推 fix_id。

    这是 finding f-aware03-task-or-fix-id-never-resolves-fix-id 的闭合: v2 目标群体恰是【无 .owner
    的 detached fix 工位】, 只走 .owner 路径它们 task_or_fix_id 恒 None, 与字段/docstring 承诺的
    "task_id 或 fix_id" 失真。补上命名反推让 fix 工位也能归因。

    反推是 best-effort 附注 (非入册判据, 入册只认 git 地面真相): fix_id = slug 段, 即 fix_key 经
    _fix_worktree_id slug 化 (非字母数字→`_`, 截 40 字符) 的结果 —— 有损、不可完美还原原 fix_key,
    但足以把该 fix 工位指回它对应的 finding/task。两路都查不到 → None (概念明文: 允许为空)。
    """
    owner = worktree_path / ".owner"
    try:
        data = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        tid = data.get("task_id")
        if isinstance(tid, str) and tid:
            return tid
    # .owner 无 task_id (含 detached fix 工位无 .owner): 退回 fix__ 命名反推。
    return _fix_id_from_worktree_name(worktree_path.name)


def _fix_id_from_worktree_name(name: str) -> str | None:
    """从 fix 隔离工位目录名 `fix__<slug>__<12hex>` 反推 fix_id (= slug 段); 不匹配返 None。

    与 orchestrator._fix_worktree_id 的产出格式对偶: digest 恒为末尾 `__` + 12 位十六进制, 用它锚定
    切出 slug。exec 工位不带 `fix__` 前缀 (同函数注释: "与 exec 同款但 fix__ 前缀区分来源"), 故不会
    误匹配。slug 为空 (理论上不该发生, fix_key 非空) 也返 None, 不产空串附注。
    """
    m = _FIX_WORKTREE_NAME_RE.match(name)
    if m is None:
        return None
    slug = m.group("slug")
    return slug or None


def scan_ahead_of_main_worktrees(
    towow_dir: Path,
    *,
    now: float | None = None,
) -> list[WorktreeInventoryItem]:
    """v2 全量清单: 枚举 .towow/worktrees 下【所有】git 工位, 用 merge-base --is-ancestor 判地面真相,
    产出每个【领先 main】(HEAD 非 main 祖先) 工位的六字段清单项。

    这是 v2 相对 v1 的核心判据升级 —— 不是"去掉年龄过滤", 而是把发现机制从命名规则预测换成 git 枚举 +
    可达性判定, 让 commit 落在【命名预测路径之外的复用工位】也能被发现 (根治 finding
    f-fixafter-scan-orphaned-fix-worktrees-reused-exec-blindspot)。

    入册判据 (与 _fix_commit_reachable_from_main 同 trust model, 概念强制的 git 地面真相):
      reachable is True  → HEAD 已是 main 祖先 (已回流 / 主树本身) → 不入册。
      reachable is False → 领先 main 的孤儿工位 → 入册。
      reachable is None  → 不可验证 (bad ref / gc) → fail-closed 入册 (偏告警, 别静默漏)。

    范围: 只枚举 towow_dir/worktrees 下的隔离工位 (与 v1 wt_base 同命名空间), 不扫 Claude Code 自建的
    .claude/worktrees (非回流对象)。now 可注入便于测试。
    """
    now = time.time() if now is None else now
    repo_dir = towow_dir.parent
    wt_base = (towow_dir / "worktrees").resolve()
    inventory: list[WorktreeInventoryItem] = []
    for wt_path, head_sha, branch in _enumerate_git_worktrees(repo_dir):
        try:
            resolved = wt_path.resolve()
        except OSError:
            resolved = wt_path
        if wt_base not in resolved.parents and resolved != wt_base:
            continue  # 只管 .towow/worktrees 下的隔离工位
        if _fix_commit_reachable_from_main(repo_dir, head_sha) is True:
            continue  # HEAD 是 main 祖先 → 已回流 / 主树, 非领先 main
        inventory.append(
            WorktreeInventoryItem(
                worktree_id=wt_path.name,
                worktree_path=str(wt_path),
                branch=branch,
                head_sha=head_sha,
                commits_ahead=_commits_ahead_of_main(repo_dir, head_sha),
                task_or_fix_id=_reverse_lookup_task_or_fix_id(wt_path),
                age_seconds=_inventory_age_seconds(wt_path, now),
            ),
        )
    return inventory


def emit_orphan_worktree_inventory(
    event_log: EventLog,
    inventory: list[WorktreeInventoryItem],
) -> str:
    """把全量清单落成 canonical OrphanWorktreeInventoryProduced (一等输出, 供下游消费)。

    NodeTouched stub-rewrap 形态 (kind=OrphanWorktreeInventoryProduced), 与 WorktreePromoted 同族——
    无 EventType 枚举成员却仍是 canonical 落账事件, 不需改 enums。path-B write_direct (带外自观测,
    非交互 session), 不经 commit gate (与 orchestrator 自审计留痕同口径)。返回 event_id。
    """
    decision_id = f"reflow-inventory-{uuid.uuid4().hex[:12]}"
    intent = EventIntent(
        local_intent_id=f"orphan-inv-{uuid.uuid4().hex[:12]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": decision_id,
            "touch_type": "write",
            "kind": _INVENTORY_EVENT_KIND,
            "stub_original_payload": {
                "kind": _INVENTORY_EVENT_KIND,
                "decision_id": decision_id,
                "inventory_count": len(inventory),
                "inventory": [item.as_payload() for item in inventory],
            },
        },
        provenance_hint=ProvenanceHint(
            actor_type=_SENTINEL_ACTOR_TYPE,
            actor_id=_SENTINEL_ACTOR_ID,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=decision_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


@dataclass(frozen=True)
class SentinelReport:
    """哨兵一轮的结构化结果。"""

    stranded_past_sla: list[str] = field(default_factory=list)  # 超 SLA 搁浅的 task_id
    findings_emitted: list[str] = field(default_factory=list)  # 超SLA搁浅 emit 的 recovery finding
    conflict_findings_emitted: list[str] = field(default_factory=list)  # 回流冲突转的 Finding (T-REFLOW-05)
    # bug-b (回流噪音修复 2): 本轮因工位已回流/已处置而联动闭合的 recovery finding (FindingResolved)。
    disposed_findings_closed: list[str] = field(default_factory=list)
    # v2 全量清单 (领先 main 的所有工位, 非仅 SLA 超龄子集) + 其落账 event_id (emit=True 时非 None)。
    inventory: list[WorktreeInventoryItem] = field(default_factory=list)
    inventory_event_id: str | None = None
    emitted: bool = True
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS

    @property
    def stranded_count(self) -> int:
        return len(self.stranded_past_sla)

    @property
    def inventory_count(self) -> int:
        return len(self.inventory)


def run_sentinel_once(
    towow_dir: Path,
    *,
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    emit: bool = True,
    now: float | None = None,
) -> SentinelReport:
    """哨兵跑一轮 (供 launchd/cron 周期调, 或测试直调)。

    扫超 SLA 搁浅 → (emit=True) emit recovery FindingCreated 走 Finding→Fix → 返回 report。
    emit=False = dry-run 只报不动 (先看真实搁浅分布再开自动修, surface-only 先行)。
    """
    log = EventLog(towow_dir / "events.log")
    past_sla = scan_stranded_past_sla(
        towow_dir, log, min_age_seconds=min_age_seconds, now=now,
    )
    # v2 全量清单: 领先 main 的【所有】工位 (非仅 SLA 超龄子集), git 地面真相判定 (捕获复用工位)。
    inventory = scan_ahead_of_main_worktrees(towow_dir, now=now)
    inventory_event_id: str | None = None
    findings: list[str] = []
    conflict_findings: list[str] = []
    disposed_closed: list[str] = []
    if emit:
        # 全量清单落成 canonical 一等输出 (供下游 merge-audit-gate / valid-reflow-loop 消费)。
        # dry-run (emit=False) 只在内存返回, 不落账 (surface-only 先看真实分布)。
        inventory_event_id = emit_orphan_worktree_inventory(log, inventory)
        # recovery marker 陈旧阈值 = 2× 本哨兵 min_age (一个 fix 该在 ~2 个 SLA 窗口内完成回流; 超过
        # 仍未回流 → 那条 fix 可能假闭合/卡死 → 独立 backstop 重发, 不依赖 fix 自报)。now 同步注入 (测试
        # 可驱动陈旧重发判定; 生产 None → 各函数取 time.time())。
        stale_after = 2 * min_age_seconds
        if past_sla:
            findings = emit_stranded_reflow_findings(
                towow_dir, log, past_sla, now=now, stale_after_seconds=stale_after,
            )
        # T-REFLOW-05: 回流受阻 (promote 冲突 / 集成 gate 拒) 也转 Finding→Fix, 不让冲突死在 marker。
        conflict_findings = reconcile_blocked_reflows(
            towow_dir, log, now=now, stale_after_seconds=stale_after,
        )
        # bug-b: 上面两条路径产的 recovery finding, 一旦其工位后续已回流/已被批处置权威收口, 联动关闭
        # (同一巡检节奏内做, 不新起独立调度)。
        disposed_closed = close_disposed_reflow_findings(towow_dir, log)
    return SentinelReport(
        stranded_past_sla=[sw.task_id for sw in past_sla],
        findings_emitted=findings,
        conflict_findings_emitted=conflict_findings,
        disposed_findings_closed=disposed_closed,
        inventory=inventory,
        inventory_event_id=inventory_event_id,
        emitted=emit,
        min_age_seconds=min_age_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    """带外 CLI 入口: `python -m towow.l2.reflow_sentinel --project-dir <harness> [--dry-run]`。

    设计成独立进程跑 (launchd/cron), 不经 orchestrator —— 这是"带外、不与 daemon 同生共死"的物理落地。
    """
    parser = argparse.ArgumentParser(description="带外回流产出哨兵 (out-of-band-reflow-sla-sentinel)")
    parser.add_argument("--project-dir", type=Path, required=True, help="含 .towow 的项目根 (harness)")
    parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="只报不 emit Finding (surface-only)")
    args = parser.parse_args(argv)
    towow_dir = args.project_dir / ".towow"
    if not towow_dir.is_dir():
        print(f"error: {towow_dir} 不存在 (--project-dir 应指含 .towow 的项目根)", file=sys.stderr)
        return 2
    report = run_sentinel_once(
        towow_dir, min_age_seconds=args.min_age_seconds, emit=not args.dry_run,
    )
    print(
        f"reflow-sentinel: 超SLA搁浅 {report.stranded_count} 个 "
        f"{report.stranded_past_sla}; emit findings {len(report.findings_emitted)} "
        f"{report.findings_emitted}; conflict findings {len(report.conflict_findings_emitted)} "
        f"{report.conflict_findings_emitted}; disposed-closed {len(report.disposed_findings_closed)} "
        f"{report.disposed_findings_closed}; 全量领先-main 清单 {report.inventory_count} 个 "
        f"(event={report.inventory_event_id}) (dry_run={args.dry_run})",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
