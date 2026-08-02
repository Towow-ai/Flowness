"""水位线无关的回流对账 (缺陷A 根治) — T-REFLOW-02 / autonomous-valid-reflow-loop。

为什么存在 (缺陷A): orchestrator._auto_promote_completed_worktrees 与 fix 侧
_detect_orphaned_fix_commits 都只扫 daemon 本轮的水位线窗口 [watermark, _last_seq-1]
(实证: 全仓无任何 scan_start=0 的全量补扫)。一个工位的 TaskRunCompleted(success) 事件一旦
落到水位线之后 (被任一轮推过 / daemon 重启前移水位线 / 该轮 auto-promote 抛异常被吞), 就
**永不再被扫到** —— 整夜 53 工位静默搁浅的根因之一。照抄 fix 侧补扫是陷阱: 它也只扫窗口。

本模块补的是真正缺的那条: **不看水位线、扫全账本** 的对账。它【只检出 + emit 一条 recovery
FindingCreated】走 agent-first-reflow-repair (Finding→Fix), **绝不自己直接 promote** —— 把
"动 main" 的决定留给经 commit gate 的 fix 闭环 (autonomous-valid-reflow-loop: 每条 promote
都走 gate)。由带外哨兵 (out-of-band-reflow-sla-sentinel, T-REFLOW-04) 周期调用, 不挂在
orchestrator 的派发循环里 (那条本身就是只扫窗口的病灶, 且带外才不与被监控机制同生共死)。

性能: 一次顺序扫全账本建 promoted-id set (O(账本)), 不对每个工位各跑一遍 all_records
(_worktree_already_promoted 是 O(工位 × 账本), 在 10 万+ 事件上爆开销)。
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import (
    _FIX_REFLOW_CLOSED_OUTCOMES,
    _PROMOTE_CONFLICT_PREFIX,
    _build_orch_nodetouched,
    _dispatched_dir,
    _extract_outcome,
    _extract_task_id,
    _fix_commit_reachable_from_main,
    _fix_worktree_id,
    _lookup_dispatched_worktree,
    _record_src,
    _unwrap_stub_rewrap,
    is_promote_conflict_pending,
)
from towow.schemas.enums import EventType, FindingKind

# ── recovery-Finding 幂等 marker (三路径共享: stranded / conflict / triage) ──────────────
# f-review-reflow-stranded-finding-storm: 三条回流 recovery 路径 (stranded 重发 / conflict 转 /
# 存量 triage) 原各自无/有私有 marker → 同一搁浅工位每轮被重复 emit 新 finding (新 uuid) →
# fix-dispatch 去重键是 FindingCreated event_id → 每轮派新 fix 会话 = OPUS fix 风暴。三路径统一
# 用这一个【按 worktree_id】的共享 marker: emit 前查 (新鲜=在飞→skip), 只在 emit 成功后写。
# 取代原 reflow_conflict_finding__ / reflow_triage_finding__ 两个私有 marker。
_RECOVERY_FINDING_MARKER_PREFIX = "reflow_recovery_finding__"

# f-review-reflow-conflict-marker-permanent-blindness: 幂等 marker 不是"永久跳过"而是"在飞期间跳过、
# 陈旧且仍未回流则重发"。marker 带时间戳; 超过这个阈值 (一个 fix 该完成回流的时长) 工位仍无回流 →
# 那条 fix 可能被假闭合/卡死 → 独立 backstop 重新 emit (不依赖 fix 自报闭合)。默认 = 2× sentinel
# 默认 min_age (3600s) = 7200s; sentinel 调用时按自己的 min_age_seconds 传 2×, 直驱路径用此默认。
_RECOVERY_MARKER_STALE_AFTER_SECONDS = 2 * 3600

# debt-72c5025798c4 根治: 三条 reflow recovery 路径各自的 rule_id (非 §SR continuation marker
# self-fulfilling-effect-recovery)。_build_recovery_finding_intent 曾无条件对 reflow 调用方也盖
# §SR 的 continuation rule_id, 致这些 finding 被 fix 落账后误经 §SR sweep 的 rule_id 白名单门
# (_EFFECT_REVIEW_RULE_IDS) → 对无 published TaskPackage 的存量回流任务误 emit spurious
# f-effect-unverifiable (实证 T-REFLOW-02/03/05)。三条路径各自传真实语义的 rule_id, 从根上不再命中
# 那扇门。triage 路径的常量在 reflow_triage.py 就近定义 (import 本模块其余共享机制的同一惯例)。
_REFLOW_CONFLICT_RULE_ID = "reflow-conflict-recovery"
_REFLOW_STRANDED_RULE_ID = "reflow-stranded-recovery"

# location 同 rule_id 一并根治 (debt-72c5025798c4 resolution_criteria 明确点名两个字段): §SR 默认值
# "done_criterion:live-fire-effect" 对 reflow finding 同样错标 (target.artifact=worktree_id, 从不是
# 真执行任务的 live-fire 效果 done_criterion)。§SR 白名单门只判 rule_id (不判 location), 故 location
# 错标不致误触发那扇门, 但仍是同一"字段挂错语义"模式的残留, 一并标定真实语义 (daemon_run_once
# M-2.3 §5.1.2 历史模式聚合消费 target.location 当 file_path 分组维度)。
_REFLOW_CONFLICT_LOCATION = "reflow-worktree:promote-conflict"
_REFLOW_STRANDED_LOCATION = "reflow-worktree:stranded"


def _recovery_marker_path(towow_dir: Path, worktree_id: str) -> Path:
    """共享 recovery-Finding 幂等 marker 路径 (写在 dispatched/ 下, 按 worktree_id)。"""
    return _dispatched_dir(towow_dir) / f"{_RECOVERY_FINDING_MARKER_PREFIX}{worktree_id}"


def _recovery_marker_fresh(
    towow_dir: Path, worktree_id: str, *, now: float, stale_after_seconds: float,
) -> bool:
    """该工位的 recovery finding 是否仍在 fix 在飞期 (= 应 skip 不重发)。

    marker 不存在 → False (首发该 emit)。
    marker 存在且未陈旧 (now - 写入时刻 < stale_after) → True (fix 在飞, 幂等 skip)。
    marker 存在但已陈旧 / 损坏不可读 → False (那条 fix 可能假闭合/卡死 → 独立 backstop 重发)。
    """
    marker = _recovery_marker_path(towow_dir, worktree_id)
    if not marker.exists():
        return False
    try:
        ts = float(marker.read_text(encoding="utf-8").split(":", 1)[0].strip())
    except (OSError, ValueError):
        return False  # 损坏/不可读 → 当未在飞, 允许重发 (fail-toward-rediscover, 别让坏 marker 永久失明)
    return (now - ts) < stale_after_seconds


def _write_recovery_marker(
    towow_dir: Path, worktree_id: str, finding_id: str, *, now: float,
) -> None:
    """emit 成功后写共享幂等 marker (内容 = `<写入时刻>:<finding_id>`, 时间戳供陈旧 backstop 判定)。"""
    _recovery_marker_path(towow_dir, worktree_id).write_text(
        f"{now}:{finding_id}", encoding="utf-8",
    )


# 冲突回流 recovery finding 的 risk_surface — emit (reconcile_blocked_reflows) 与 resolution judge
# (_conflict_reflow_resolved) 两处共用同一常量, 避免字面字符串漂移让 judge 扫不到自己 emit 的 finding。
_CONFLICT_REFLOW_RISK_SURFACE = "agent-first-reflow-repair"


@dataclass(frozen=True)
class _BlockedReflowIndex:
    """reconcile_blocked_reflows 一次 O(账本) 单遍扫出的全部判据源 (守模块单遍承诺)。

    f-rereview-reflow-conflict-stale-resend-storm: 冲突工位"是否已回流"不能只靠
    `worktree_id in promoted` —— 冲突工位结构性永无自己的 WorktreePromoted (两条 promote 路径都在
    is_promote_conflict_pending→return; 全仓无 marker 清除), 故该守卫几乎永远 False → 每 stale_after
    对【已被解决】的冲突工位也重发新 finding = OPUS fix 风暴。真判据 = 解冲突的那条 fix 是否已落 main,
    与 _scan_orphaned_fix_worktrees 同 trust model (fix 工位 WorktreePromoted ∪ FixProposed.sha 可达)。
    """

    promoted: frozenset[str]  # WorktreePromoted 的 task_id 集 (exec 工位名 ∪ fix 工位名)
    # 冲突工位 worktree_id → 为它 emit 过的 conflict recovery finding [(finding_id, FindingCreated event_id)]。
    # event_id 是派 fix 的 trigger = fix 工位 key (_fix_worktree_id), 故用它反查解冲突 fix 的工位名。
    recovery_by_artifact: dict[str, list[tuple[str, str]]]
    # recovery finding_id → 解它的 fix [(fix_id, worktree_commit_sha|None)] (FixProposed.related_finding_id)。
    fix_by_finding: dict[str, list[tuple[str, str | None]]]
    closed_fix_ids: frozenset[str]  # outcome ∈ _FIX_REFLOW_CLOSED_OUTCOMES 的 FixCompleted 的 fix_id 集


def _build_blocked_reflow_index(event_log: EventLog) -> _BlockedReflowIndex:
    """一遍 all_records 建 reconcile_blocked_reflows 需要的全部判据源 (promoted set + 解决链三映射)。

    用 _unwrap_stub_rewrap + _record_src 统一两形态 (真 event_type 与 NodeTouched stub-rewrap 信封) ——
    生产里 FindingCreated/FixProposed 约 28% 是 stub-rewrap 形态, get_events_by_type 只认真类型会漏 →
    解决链断 → 误判未解决 → 不必要重发 (与 _scan_orphaned_fix_worktrees / _fix_worktree_path_for_fix 的
    健壮性对齐)。
    """
    promoted: set[str] = set()
    recovery_by_artifact: dict[str, list[tuple[str, str]]] = {}
    fix_by_finding: dict[str, list[tuple[str, str | None]]] = {}
    closed_fix_ids: set[str] = set()
    # T-LRG-B2 P0 迁移: warm committed_index snapshot 替代全量磁盘 re-scan (等价已提交事件集, O(索引))
    for rec in event_log.committed_index().records():
        effective_type, _payload = _unwrap_stub_rewrap(rec)
        if effective_type == "WorktreePromoted":
            src = _record_src(rec)
            wid = src.get("task_id")
            if isinstance(wid, str) and wid:
                promoted.add(wid)
        elif effective_type == EventType.FINDING_CREATED.value:
            src = _record_src(rec)
            if src.get("risk_surface") != _CONFLICT_REFLOW_RISK_SURFACE:
                continue
            target = src.get("target")
            artifact = target.get("artifact") if isinstance(target, dict) else None
            fcid = src.get("finding_id")
            if isinstance(artifact, str) and artifact and isinstance(fcid, str) and fcid:
                recovery_by_artifact.setdefault(artifact, []).append((fcid, rec.event_id))
        elif effective_type == EventType.FIX_PROPOSED.value:
            src = _record_src(rec)
            rf = src.get("related_finding_id")
            fid = src.get("fix_id")
            if isinstance(rf, str) and rf and isinstance(fid, str) and fid:
                wcs = src.get("worktree_commit_sha")
                sha = wcs.strip() if isinstance(wcs, str) and wcs.strip() else None
                fix_by_finding.setdefault(rf, []).append((fid, sha))
        elif effective_type == EventType.FIX_COMPLETED.value:
            src = _record_src(rec)
            fid = src.get("fix_id")
            if (
                isinstance(fid, str) and fid
                and src.get("outcome") in _FIX_REFLOW_CLOSED_OUTCOMES
            ):
                closed_fix_ids.add(fid)
    return _BlockedReflowIndex(
        promoted=frozenset(promoted),
        recovery_by_artifact=recovery_by_artifact,
        fix_by_finding=fix_by_finding,
        closed_fix_ids=frozenset(closed_fix_ids),
    )


def _conflict_reflow_resolved(
    repo_dir: Path, index: _BlockedReflowIndex, worktree_id: str,
) -> bool:
    """冲突工位是否已被真解决 (= 别再重发 recovery finding)。

    冲突工位无自己的 WorktreePromoted (结构性), 故真判据走解冲突 fix 的回流态, 与
    _scan_orphaned_fix_worktrees 同 trust model:
      worktree_id → 它的 conflict recovery finding(s) → 解它的 fix(closed FixCompleted) → 该 fix 已落 main
      (fix 工位名 ∈ promoted 集 [隔离工位 serial-apply 回流] 或 FixProposed.sha 可达 main [直落主树])。
    要求 closed FixCompleted (非 needs_further_review): 落了点代码但未声称闭合的 fix 不算解决 (hollow
    closure 由 fix_after 抓, 不在这层 —— 与 _scan_orphaned 的分工一致)。

    覆盖诚实: recovery→FixProposed 反查链与 _fix_worktree_path_for_fix 同族 (历史通率 ~72%, 早期/
    多轮 fix 部分断链)。链断 → 返 False → 重发 = 安全方向 (fail-toward-rediscover, 不退回永久失明),
    故本 judge 是【收窄风暴】不是对断链冲突 100% 消除 (登债说明残留)。
    """
    if worktree_id in index.promoted:
        return True  # 罕见: 冲突工位本身竟拿到 WorktreePromoted (人工/旧路径直 promote)
    for finding_id, finding_eid in index.recovery_by_artifact.get(worktree_id, []):
        for fix_id, sha in index.fix_by_finding.get(finding_id, []):
            if fix_id not in index.closed_fix_ids:
                continue  # 未闭合 (needs_further_review / 在飞) — 不算解决
            if _fix_worktree_id(finding_eid) in index.promoted:
                return True  # 解冲突 fix 的隔离工位已 serial-apply 回流 (WorktreePromoted 留痕)
            # functional-equivalence-closure-criterion@v1 (c): 此处 merge-base 只是【辅助线索】, 不是本
            # 判定的唯一/决定性判据 —— 上面 index.promoted (H3 硬证据) 才是主判据, 这里是它的 fallback:
            # 未隔离/直落主树的 fix 结构性永无 WorktreePromoted, sha 是 main 祖先意味着这个字面提交 (同
            # 哈希) 确已进入 main, 用于避免对已解决的直落冲突重复转 finding (非声明"已闭合",只是"不必
            # 再派 agent 重查这条")。真正的闭合记录 (FindingResolved/TaskNodeClosed 等) 仍须走
            # closure-evidence-verification-gate@v1 的独立核验, 本函数从不 emit 那些事件。
            if sha is not None and _fix_commit_reachable_from_main(repo_dir, sha) is True:
                return True  # 解冲突 fix 直落主树 (commit 是 main 祖先)
    return False


@dataclass(frozen=True)
class StrandedWorktree:
    """一个完工成功却未回流主干的隔离工位 (缺陷A 的受害者)。"""

    task_id: str
    worktree_id: str  # = worktree_path.name (= WorktreePromoted 幂等 key)
    worktree_path: Path
    completion_event_id: str


def scan_stranded_worktrees(
    towow_dir: Path,
    event_log: EventLog,
) -> list[StrandedWorktree]:
    """不看水位线、扫全账本, 找出所有"有 TaskRunCompleted(success) 却无 WorktreePromoted"的隔离工位。

    一次顺序扫: 同一遍里既建 promoted_worktree_ids set, 又收集 completed-success 的 (task_id,
    event_id)。判 stranded 用内存 set 查 (O(1)), 不再逐工位重扫账本。

    排除 (非 stranded, 不报):
      - 已有 WorktreePromoted (worktree_id ∈ promoted set) —— 已回流。
      - 无隔离工位 (_lookup_dispatched_worktree 返回 None) —— 非隔离任务 (共享主树), 不需回流。
      - 工位目录已不在 —— 已 promote+cleanup / 从未隔离。
      - 已有 promote_conflict marker —— 待人工解的真冲突, 非静默搁浅, 不重复告警。
    """
    promoted_worktree_ids: set[str] = set()
    # task_id -> 最新一条 TaskRunCompleted(success) 的 event_id (同 task 多 run 取最后, latest-wins)。
    completed_success: dict[str, str] = {}

    # T-LRG-B2 P0 迁移: warm committed_index snapshot 替代全量磁盘 re-scan (等价已提交事件集, O(索引))
    for rec in event_log.committed_index().records():
        effective_type, effective_payload = _unwrap_stub_rewrap(rec)
        # WorktreePromoted 无 EventType 枚举成员 — 只作 stub-rewrap 的 kind 字符串落账
        # (_worktree_already_promoted 同样按字面 "WorktreePromoted" 比对)。
        if effective_type == "WorktreePromoted":
            if isinstance(effective_payload, dict):
                wid = effective_payload.get("task_id")
                if isinstance(wid, str) and wid:
                    promoted_worktree_ids.add(wid)
        elif effective_type == EventType.TASK_RUN_COMPLETED.value:
            if _extract_outcome(effective_payload) != "success":
                continue
            tid = _extract_task_id(effective_payload)
            if tid:
                completed_success[tid] = rec.event_id

    stranded: list[StrandedWorktree] = []
    seen_worktree_ids: set[str] = set()
    for task_id, completion_event_id in completed_success.items():
        worktree_path = _lookup_dispatched_worktree(towow_dir, task_id)
        if worktree_path is None:
            continue  # 非隔离任务 (共享主树) — 无需回流
        worktree_id = worktree_path.name
        if worktree_id in promoted_worktree_ids:
            continue  # 已回流
        if not worktree_path.exists():
            continue  # 已 promote+cleanup / 从未隔离
        if is_promote_conflict_pending(towow_dir, worktree_id):
            continue  # 真冲突待人工 — 非静默搁浅, 不重复告警
        stranded.append(
            StrandedWorktree(
                task_id=task_id,
                worktree_id=worktree_id,
                worktree_path=worktree_path,
                completion_event_id=completion_event_id,
            ),
        )
        seen_worktree_ids.add(worktree_id)

    # 缺陷A 在 fix 路径 (f-review-reflow-defectA-fixside-watermark-blindspot): 上面只扫 exec 工位
    # (TaskRunCompleted success)。fix 侧 FixCompleted 完工同样会落水位线外永不再扫 → 把 fix 侧搁浅
    # 也纳入全量对账 (exec/fix 工位 worktree_id 命名空间不同, seen set 防极端同名双报)。
    for fsw in _scan_orphaned_fix_worktrees(towow_dir, event_log):
        if fsw.worktree_id in seen_worktree_ids:
            continue
        stranded.append(fsw)
        seen_worktree_ids.add(fsw.worktree_id)
    return stranded


def _scan_orphaned_fix_worktrees(
    towow_dir: Path,
    event_log: EventLog,
) -> list[StrandedWorktree]:
    """fix 侧搁浅全量对账 (缺陷A 在 fix 路径): 找出"FixCompleted(closed outcome) 但其 commit 未回流
    main、工位仍在"的 detached fix 工位。

    fix 工位无 WorktreePromoted (走 detached serial-apply 回流), 故"已回流"判据 = commit 是 main
    祖先 (_fix_commit_reachable_from_main is True), 不是 WorktreePromoted set。工位反查链同
    _fix_worktree_path_for_fix (fix_id → FixProposed.related_finding_id → FindingCreated event_id →
    _fix_worktree_id), sha 同 _fix_proposed_worktree_sha (FixProposed.worktree_commit_sha)。

    性能 (守本模块 O(账本) 单遍承诺, 不照搬 _fix_worktree_path_for_fix/_fix_proposed_worktree_sha 的
    O(fix×账本)): 一遍 all_records 建 fix_id→{sha, finding} + finding_id→event_id 映射; 且把 git
    可达性 subprocess 推到最后 —— 只对【工位目录仍在】的少数候选跑 (历史已清 fix 不跑 subprocess)。
    """
    repo_dir = towow_dir.parent
    # 一遍扫账本建映射 (latest-wins): 用 _unwrap_stub_rewrap + _record_src 统一两形态 (真 event_type
    # 与 NodeTouched stub-rewrap 信封), 与 _fix_worktree_path_for_fix 的健壮性对齐。
    latest_fc: dict[str, str] = {}  # fix_id -> 最新 closed-outcome FixCompleted event_id
    fix_sha: dict[str, str] = {}  # fix_id -> worktree_commit_sha
    fix_finding: dict[str, str] = {}  # fix_id -> related_finding_id
    finding_eid: dict[str, str] = {}  # finding_id -> FindingCreated event_id (= fix 工位 key)
    # T-LRG-B2 P0 迁移: warm committed_index snapshot 替代全量磁盘 re-scan (等价已提交事件集, O(索引))
    for rec in event_log.committed_index().records():
        effective_type, _payload = _unwrap_stub_rewrap(rec)
        if effective_type == EventType.FIX_COMPLETED.value:
            src = _record_src(rec)
            fid = src.get("fix_id")
            if (
                isinstance(fid, str) and fid
                and src.get("outcome") in _FIX_REFLOW_CLOSED_OUTCOMES
            ):
                latest_fc[fid] = rec.event_id
        elif effective_type == EventType.FIX_PROPOSED.value:
            src = _record_src(rec)
            fid = src.get("fix_id")
            if isinstance(fid, str) and fid:
                wcs = src.get("worktree_commit_sha")
                if isinstance(wcs, str) and wcs.strip():
                    fix_sha[fid] = wcs.strip()
                rf = src.get("related_finding_id")
                if isinstance(rf, str) and rf:
                    fix_finding[fid] = rf
        elif effective_type == EventType.FINDING_CREATED.value:
            src = _record_src(rec)
            fcid = src.get("finding_id")
            if isinstance(fcid, str) and fcid:
                finding_eid[fcid] = rec.event_id

    wt_base = towow_dir / "worktrees"
    orphans: list[StrandedWorktree] = []
    for fix_id, completion_event_id in latest_fc.items():
        sha = fix_sha.get(fix_id)
        if sha is None:
            continue  # 无结构化 commit (escalation/finding 反向 outcome) — 无回流义务
        # 先反查工位 (纯 map + 一次 stat, 便宜); 工位已清 = 回流后清理 → 非搁浅, skip
        rf = fix_finding.get(fix_id)
        eid = finding_eid.get(rf) if rf is not None else None
        if eid is None:
            continue  # 反查断链 — windowed _detect_orphaned_fix_commits fail-closed 已 surface, 不重复
        worktree_path = wt_base / _fix_worktree_id(eid)
        if not worktree_path.exists():
            continue  # 工位已清 (回流+cleanup) / 不在 — 非工作树搁浅对象
        worktree_id = worktree_path.name
        if is_promote_conflict_pending(towow_dir, worktree_id):
            continue  # 真冲突待人工 — 走 conflict 路径不重复
        # 工位仍在的候选才跑 git 可达性 (省掉历史已清 fix 的 subprocess)。is True = 已回流; False
        # (孤儿) / None (不可验证: gc/bad ref) 都 fail-closed 当未回流报出 (别静默漏)。
        # functional-equivalence-closure-criterion@v1 (c): merge-base 在此仅是辅助的"字面提交是否在场"
        # 检测 (真判据仍是"工位是否仍搁浅"这一存在性问题, 非正式闭合判定) —— 本函数从不 emit
        # FindingResolved/TaskNodeClosed 等闭合记录, 只决定是否把这个工位列入待关注的搁浅候选。
        if _fix_commit_reachable_from_main(repo_dir, sha) is True:
            continue
        orphans.append(
            StrandedWorktree(
                task_id=fix_id,
                worktree_id=worktree_id,
                worktree_path=worktree_path,
                completion_event_id=completion_event_id,
            ),
        )
    return orphans


def emit_stranded_reflow_findings(
    towow_dir: Path,
    event_log: EventLog,
    stranded: list[StrandedWorktree],
    *,
    now: float | None = None,
    stale_after_seconds: float = _RECOVERY_MARKER_STALE_AFTER_SECONDS,
) -> list[str]:
    """对每个 stranded 工位 emit 一条 recovery FindingCreated (走 agent-first-reflow-repair / Finding→Fix)。

    复用 orchestrator._build_recovery_finding_intent + daemon_run_once._emit_finding_via_gate (Path A
    经 commit gate emit, 不直写账本)。finding_kind=system_governance_defect (回流搁浅 = 系统治理缺陷,
    路由到 fix, 不劫持 anomaly — 见 governance-finding-kind@v1)。本模块只检出+emit, 不 promote
    (动 main 留给经 gate 的 fix 闭环)。

    幂等 (f-review-reflow-stranded-finding-storm): 共享 recovery marker 在飞期间 skip, 陈旧仍未回流则
    重发 (本路径的工位由 scan 保证仍未回流, 故陈旧即可重发); 只在 emit 成功 (event_id 非 None) 后写。

    §SR 解耦 (f-review-reflow-sr-deadletter-coupling): target.artifact = worktree_id (非真执行 task_id)
    → _is_execution_task 不命中 → _resolve_effect_task_for_finding 返 None → §SR run_self_fulfilling_
    recovery_sweep 一致 skip, 不把一次回流磕绊误当"能力任务效果不达"拉回重算→超 cap→dead-letter 已合法
    完工的任务。下游 fix 工位解析靠 finding event_id (_fix_worktree_path_for_fix), 不受此影响。rule_id
    传 _REFLOW_STRANDED_RULE_ID (debt-72c5025798c4 根治, 见 _build_recovery_finding_intent 参数化说明),
    不再继承 §SR 的 continuation marker。

    A-2 铸造上限 (小补丁包 a, 参数化复用 conflict 路径 reconcile_blocked_reflows 同款机制, 见
    _reflow_mint_cap 一带): 同一工位 (跨 stranded/conflict/triage 三路径共享计数器, 键 worktree_id)
    铸造次数达上限后不再铸新 finding, 改一次性 emit GoalEscalationRaised 升级后静默 —— conflict 路径
    此前已有此上限, stranded 路径此前无界 (哨兵重启后唯一还会无界漏水的口子之一)。

    返回成功 emit 的 finding event_id 列表。
    """
    from towow.l2.reflow_commit_gate import emit_finding_via_gate
    from towow.l2.orchestrator import _build_recovery_finding_intent

    now = time.time() if now is None else now
    emitted: list[str] = []
    for sw in stranded:
        if _recovery_marker_fresh(
            towow_dir, sw.worktree_id, now=now, stale_after_seconds=stale_after_seconds,
        ):
            continue  # fix 在飞期 — 幂等 skip (防 finding 风暴)
        mint_count = reflow_conflict_mint_count(towow_dir, sw.worktree_id)
        cap = _reflow_mint_cap()
        if mint_count >= cap:
            if not _reflow_mint_cap_escalated(towow_dir, sw.worktree_id):
                esc_event_id = _emit_reflow_mint_cap_escalation(
                    event_log, worktree_id=sw.worktree_id, mint_count=mint_count, cap=cap,
                )
                if esc_event_id:
                    _write_reflow_mint_cap_escalated(towow_dir, sw.worktree_id, esc_event_id, now=now)
            continue
        finding_id = f"f-reflow-stranded-{uuid.uuid4().hex[:12]}"
        intent = _build_recovery_finding_intent(
            finding_id=finding_id,
            severity="major",  # FindingSeverity enum: critical/major/minor/observation/purple
            risk_surface="autonomous-valid-reflow-loop",
            description=(
                f"隔离工位 {sw.worktree_id} (task {sw.task_id}) 已完工 "
                f"(completion={sw.completion_event_id}) 却未回流主干 (缺陷A 全量对账检出, 含 fix 侧)。"
                f"工位路径 {sw.worktree_path}。走 agent-first-reflow-repair: 由 fix 会话智能修复并经 "
                f"commit gate 回流, 永不销毁工位。"
            ),
            finding_kind=FindingKind.SYSTEM_GOVERNANCE_DEFECT.value,
            effect_task=sw.worktree_id,
            rule_id=_REFLOW_STRANDED_RULE_ID,
            target_location=_REFLOW_STRANDED_LOCATION,
        )
        event_id = emit_finding_via_gate(
            event_log, intent, towow_dir, closure="reflow-stranded-reconcile",
        )
        if event_id:
            _write_recovery_marker(towow_dir, sw.worktree_id, finding_id, now=now)
            _bump_reflow_conflict_mint_count(towow_dir, sw.worktree_id)
            emitted.append(event_id)
    return emitted


def reconcile_stranded_reflow(towow_dir: Path, event_log: EventLog) -> list[str]:
    """对账入口 (带外哨兵周期调用): 扫全账本找 stranded → emit recovery Finding。返回 emit 的 finding id。"""
    stranded = scan_stranded_worktrees(towow_dir, event_log)
    if not stranded:
        return []
    return emit_stranded_reflow_findings(towow_dir, event_log, stranded)


# ── A-2 (f-review-reflow-conflict-mint-unbounded): 冲突 finding 铸造上限 ──────────────────
#
# 实证: _recovery_marker_fresh 的陈旧 backstop (f-review-reflow-conflict-marker-permanent-blindness)
# 只解决"永久跳过", 没解决"永久重发" —— 卡死无终态的 promote 冲突工位 (fix 反复假闭合/一直不落地),
# 每过 stale_after (默认 2h) 就铸一条【全新】finding_id, 无计数上限、无终态, 48h 实测 143 条。这是
# bug-b 联动闭合 (close_disposed_reflow_findings) 的姊妹篇: 联动闭合处理"工位已被真解决"的一半,
# 本节处理"工位一直没被解决"的另一半 —— 无限铸造本身也是噪音/OPUS fix 风暴源, 不能靠"迟早被解决"
# 兜底 (对一直解不了的工位, 迟早等不到)。
#
# 设计: 持久计数器 (跨轮累加, 不随 in-flight marker 重置, 与 orchestrator.bump_nonexec_redispatch_
# count 同一持久计数器范式) 记该冲突工位【铸造过几次】。达上限 (默认 3, TOWOW_REFLOW_MINT_CAP 可配)
# 且最近一条铸造的 recovery marker 也已陈旧 (与其余 backstop 重发同一"陈旧才算这条尝试失败"节奏,
# 不在上一条铸造仍在 stale_after 窗口内合法工作时抢先给 owner 报"已放弃"制造假警报) → 不再铸新
# finding, 改为一次性 emit GoalEscalationRaised 告知 owner"自动恢复已放弃"(复用
# orchestrator.emit_owner_gate_escalation / stranded_batch_disposition.escalate_for_owner 同款
# stub-rewrap 范式, 不新造机制) 后静默 (escalated marker 防重复升级, 与 emit_owner_gate_escalation
# 系语义一致: 升级一次, 不逐轮刷屏)。工位真被 close_disposed_reflow_findings 联动闭合解决后, 计数器
# 与 escalated marker 一并清零 (下次若这个 worktree_id 意外复现新冲突, 不会背着历史计数直接判上限)。
_REFLOW_MINT_CAP_ENV = "TOWOW_REFLOW_MINT_CAP"
_DEFAULT_REFLOW_MINT_CAP = 3

_REFLOW_MINT_COUNT_PREFIX = "reflow_conflict_mint_count__"
_REFLOW_MINT_CAP_ESCALATED_PREFIX = "reflow_conflict_mint_cap_escalated__"


def _reflow_mint_cap() -> int:
    """同一冲突工位铸造 recovery finding 的次数上限。默认 3; TOWOW_REFLOW_MINT_CAP 可配, 非正整数
    字符串落默认 (与 orchestrator._exec_max_parallel 的 env 解析约定一致: raw.isdigit() and >0)。"""
    raw = os.environ.get(_REFLOW_MINT_CAP_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_REFLOW_MINT_CAP


def _reflow_conflict_mint_count_path(towow_dir: Path, worktree_id: str) -> Path:
    return _dispatched_dir(towow_dir) / f"{_REFLOW_MINT_COUNT_PREFIX}{worktree_id}"


def reflow_conflict_mint_count(towow_dir: Path, worktree_id: str) -> int:
    """该冲突工位已铸造过几次 recovery finding (持久计数, 跨轮累加)。无计数器/损坏不可读 → 0
    (fail-toward-rediscover, 别让坏计数文件永久卡死在上限判定上)。"""
    p = _reflow_conflict_mint_count_path(towow_dir, worktree_id)
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _bump_reflow_conflict_mint_count(towow_dir: Path, worktree_id: str) -> int:
    """铸一条新 finding 成功后 +1。Returns 累加后的新值。"""
    new_count = reflow_conflict_mint_count(towow_dir, worktree_id) + 1
    _reflow_conflict_mint_count_path(towow_dir, worktree_id).write_text(
        str(new_count), encoding="utf-8",
    )
    return new_count


def _reflow_mint_cap_escalated_path(towow_dir: Path, worktree_id: str) -> Path:
    return _dispatched_dir(towow_dir) / f"{_REFLOW_MINT_CAP_ESCALATED_PREFIX}{worktree_id}"


def _reflow_mint_cap_escalated(towow_dir: Path, worktree_id: str) -> bool:
    """该冲突工位是否已经因铸造上限升级过 (存在即已升级, 防每轮重复 emit escalation 刷屏)。"""
    return _reflow_mint_cap_escalated_path(towow_dir, worktree_id).exists()


def _write_reflow_mint_cap_escalated(
    towow_dir: Path, worktree_id: str, escalation_event_id: str, *, now: float,
) -> None:
    _reflow_mint_cap_escalated_path(towow_dir, worktree_id).write_text(
        f"{now}:{escalation_event_id}", encoding="utf-8",
    )


def clear_reflow_conflict_mint_state(towow_dir: Path, worktree_id: str) -> None:
    """工位真被解决 (联动闭合路径, 见 close_disposed_reflow_findings) 后铸造计数清零 + 解除升级静默。

    best-effort: 文件不存在/IO 失败都不抛 (清计数是闭合收尾动作, 不是闭合本身成败的判据, 不能因为
    这一步失败反过来阻断 FindingResolved 已经落账成功的闭合结果)。
    """
    for p in (
        _reflow_conflict_mint_count_path(towow_dir, worktree_id),
        _reflow_mint_cap_escalated_path(towow_dir, worktree_id),
    ):
        with contextlib.suppress(OSError):
            p.unlink(missing_ok=True)


def _emit_reflow_mint_cap_escalation(
    event_log: EventLog, *, worktree_id: str, mint_count: int, cap: int,
) -> str:
    """铸造上限熔断 → emit GoalEscalationRaised 告知 owner"该工位自动恢复已放弃"。

    复用既有 escalation 机制 (orchestrator.emit_owner_gate_escalation / stranded_batch_disposition.
    escalate_for_owner 同款 GoalEscalationRaised stub-rewrap NodeTouched 范式, path-B write_direct,
    不经 commit gate —— 这类 orchestrator 自 emit 的系统告警件一向如此, 与 EscalationRaised 走
    _emit_finding_via_gate 的域 Finding 不同层)。独立 decision_id 前缀 (reflow-mint-cap-esc-) 不与
    owner-gate (owner-gate-esc-) / 批处置 (batch-disposition-esc-) 键空间冲突。Returns event_id。

    三路径共用本函数 (小补丁包 a: stranded/triage 铸造上限参数化复用 conflict 路径同款机制, 计数器
    键 worktree_id 三路径共享 — 与既已共享的 recovery marker 同一预算, 不让同一工位靠"换检测路径"
    绕开上限)。owner_question 文案故意不点名"promote 冲突"(那只是三种检出方式之一), 保持对 conflict/
    stranded/triage 三种来源都准确。reason 字段值维持原样 (既有测试/消费方按此字符串识别铸造上限
    escalation, 不因新增调用方而改名)。
    """
    decision_id = f"reflow-mint-cap-esc-{worktree_id}"
    payload: dict[str, object] = {
        "kind": "GoalEscalationRaised",
        "decision_id": decision_id,
        "escalation_id": decision_id,
        "goal_session_id": decision_id,
        "owner_question": (
            f"隔离工位 {worktree_id} 回流一直受阻 (promote 冲突 / 完工未回流 / 存量 triage 之一), "
            f"已连续铸造 {mint_count} 条 recovery finding 仍未被真解决 (达上限 {cap}) —— 该工位的自动"
            f"恢复已放弃, 需要你人工介入查看 (工位路径 worktrees/{worktree_id})。"
        ),
        "reason": "reflow_conflict_mint_cap_exhausted",
        "raised_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "awaiting_response": True,
        "blocking_scope": "session_only",
        "worktree_id": worktree_id,
        "mint_count": mint_count,
        "mint_cap": cap,
    }
    intent = _build_orch_nodetouched(
        kind="GoalEscalationRaised",
        decision_id=decision_id,
        payload_body=payload,
    )
    return event_log.write_direct(intent).event_id


def reconcile_blocked_reflows(
    towow_dir: Path,
    event_log: EventLog,
    *,
    now: float | None = None,
    stale_after_seconds: float = _RECOVERY_MARKER_STALE_AFTER_SECONDS,
) -> list[str]:
    """回流受阻 (promote 冲突 / 集成 gate 拒) → Finding→Fix 接线 (T-REFLOW-05, agent-first-reflow-repair)。

    现状缺口: promote 撞冲突 (基线漂移真重叠) 或集成复验拒 (T-REFLOW-03) 时, 调用方写 promote_conflict__
    marker + 一条没人主动看的 passive main-inbound 事件 —— 冲突就【死在 marker】, 无人修。scan_stranded_
    worktrees 还特意把带 conflict marker 的工位跳过 (视作"待人工")。结果冲突工位永久搁浅。

    本函数把它接进 agent-first 修复: 扫 promote_conflict__ marker, 对每个冲突工位 emit 一条 recovery
    FindingCreated (经 commit gate, _emit_finding_via_gate) → 现有 fix dispatch 自动起 Fix 会话智能
    解冲突/rebase 后重新回流 (不新建机制、不改 orchestrator)。永不销毁工位。

    独立 backstop (f-review-reflow-conflict-marker-permanent-blindness): promote_conflict__ marker 全仓
    无代码清除 → 原 reflow_conflict_finding__ 私有 marker 一写即永久跳过 → 若那条 fix 被假闭合, 工位对
    全部探测器永久失明。改用共享 recovery marker (带时间戳): 在飞期间 skip; 但若工位仍无 WorktreePromoted
    且 marker 已陈旧 (超 stale_after = 一个 fix 该完成的窗口) → 重新 emit (不依赖 fix 自报闭合, 独立重发现)。

    §SR 解耦 + 幂等: 见 emit_stranded_reflow_findings (effect_task=worktree_id 让 §SR 一致 skip; 共享
    marker 只 emit 成功后写)。

    A-2 铸造上限: 同一冲突工位铸造次数达 _reflow_mint_cap() (默认 3) 后不再铸新 finding, 改一次性
    emit GoalEscalationRaised 升级后静默 (见上方 "A-2" 节)。escalation event_id 不进本函数返回值
    (语义是 recovery finding event_id 列表, 供 caller 反查 fix 工位; escalation 不是待派 fix 的
    finding, 混进去会让 caller 误当 finding 处理)。

    返回本轮 emit 的 finding event_id。
    """
    from towow.l2.reflow_commit_gate import emit_finding_via_gate
    from towow.l2.orchestrator import _build_recovery_finding_intent

    ddir = _dispatched_dir(towow_dir)
    if not ddir.is_dir():
        return []
    now = time.time() if now is None else now
    repo_dir = towow_dir.parent
    # 陈旧 backstop 重发前确认冲突【真被解决】(已解决的别重发) —— marker 不可靠 (从不清), 且冲突工位
    # 结构性永无自己的 WorktreePromoted, 故不能只查 `worktree_id in promoted`。一次 O(账本) 建索引,
    # 真判据走解冲突 fix 的回流态 (见 _conflict_reflow_resolved)。
    index = _build_blocked_reflow_index(event_log)
    emitted: list[str] = []
    for marker in sorted(ddir.iterdir()):
        if not marker.name.startswith(_PROMOTE_CONFLICT_PREFIX):
            continue
        worktree_id = marker.name[len(_PROMOTE_CONFLICT_PREFIX) :]
        # bug-a (回流噪音修复 1): 工位磁盘已不在 → 结构性永不可能再有解冲突的 fix 落 main
        # (_conflict_reflow_resolved 要求的判据本身要求工位/其 fix 工位存在), 陈旧 backstop 于是
        # 每 stale_after 周期永久重发, 无上限 —— 实证: 7 个已从磁盘消失的工位各堆 58 条同模板
        # f-reflow-conflict-* (跨 2026-07-05~07-10, 间隔与 stale_after 吻合), 是 552 条回流噪音的
        # 主源。scan_stranded_worktrees 一侧早已有等价 `worktree_path.exists()` 判据 (见该函数),
        # 本处補上 conflict 族对称的一半, 按其语义: 目标不在了就不再造新搁浅/冲突 finding。
        if not (towow_dir / "worktrees" / worktree_id).exists():
            continue
        if _conflict_reflow_resolved(repo_dir, index, worktree_id):
            continue  # 冲突已被解冲突 fix 落 main — 无需再转 Finding (即便有陈旧 recovery marker)
        if _recovery_marker_fresh(
            towow_dir, worktree_id, now=now, stale_after_seconds=stale_after_seconds,
        ):
            continue  # fix 在飞期 — 幂等 skip (首发后 / backstop 重发后均在飞); 即便已达上限也不该在
            # 上一条铸造的 fix 仍在窗口内合法工作时抢先给 owner 报"已放弃"(那会是假警报) —— 达上限的
            # 判定必须等这条也熬过 stale_after 仍未解决 (与其余 backstop 重发同一"陈旧才算失败"节奏)。
        # A-2 铸造上限: marker 已陈旧 (上一条铸造的 fix 没能在窗口内解决) 且累计铸造次数达上限 → 永久
        # 停止铸造 (硬门槛), 只在首次达上限的这一轮 emit 一次性 escalation, escalated marker 防重复
        # 升级; 此后每轮都在这里静默 continue。
        mint_count = reflow_conflict_mint_count(towow_dir, worktree_id)
        cap = _reflow_mint_cap()
        if mint_count >= cap:
            if not _reflow_mint_cap_escalated(towow_dir, worktree_id):
                esc_event_id = _emit_reflow_mint_cap_escalation(
                    event_log, worktree_id=worktree_id, mint_count=mint_count, cap=cap,
                )
                if esc_event_id:
                    _write_reflow_mint_cap_escalated(towow_dir, worktree_id, esc_event_id, now=now)
            continue
        finding_id = f"f-reflow-conflict-{uuid.uuid4().hex[:12]}"
        intent = _build_recovery_finding_intent(
            finding_id=finding_id,
            severity="major",  # FindingSeverity enum
            risk_surface=_CONFLICT_REFLOW_RISK_SURFACE,
            description=(
                f"隔离工位 {worktree_id} promote 回流受阻 (promote_conflict marker = 基线漂移真重叠 / "
                f"集成复验拒) —— 走 agent-first-reflow-repair: 由 fix 会话智能解冲突或 rebase 后重新回流, "
                f"永不销毁工位。冲突原先死在 marker + 无人看的 main-inbound, 本 Finding 把它接进自动修复。"
            ),
            finding_kind=FindingKind.SYSTEM_GOVERNANCE_DEFECT.value,
            effect_task=worktree_id,
            rule_id=_REFLOW_CONFLICT_RULE_ID,
            target_location=_REFLOW_CONFLICT_LOCATION,
        )
        event_id = emit_finding_via_gate(
            event_log, intent, towow_dir, closure="reflow-conflict-repair",
        )
        if event_id:
            _write_recovery_marker(towow_dir, worktree_id, finding_id, now=now)
            _bump_reflow_conflict_mint_count(towow_dir, worktree_id)
            emitted.append(event_id)
    return emitted


# ── bug-b (回流噪音修复 2): 处置/回流真解决后未联动闭合 recovery finding ─────────────────────────
#
# 实证: T_LRF_07/T_R12_2/T_RECK_02 三个工位 2026-07-06 已由 stranded_batch_disposition.dispose_as_
# superseded 落 superseded_then_removed 终态(见 docs/reflow/batch-disposition-completion-record.md),
# 但它们名下的 f-reflow-stranded-*/f-reflow-conflict-* recovery finding 至今 (07-10+) 仍
# lifecycle_state=created —— emit_stranded_reflow_findings/reconcile_blocked_reflows 两条 emit 路径
# 从不回头看"这个 worktree_id 后来是否已经有权威终态", 闭合这条边缺失。
#
# 走轻量路径 (不在 dispose_* 内联做, 那是 SAGA compensation 敏感路径, 加一次跨模块 gate 往返会提高
# 它的失败面且让两个关注点耦合失败): dispose_* 已 emit 的三条终态事件 (WorktreeClosed /
# StrandedWorktreeDeletedEmptyShell / StrandedWorktreeSupersededThenRemoved) 与正常回流的
# WorktreePromoted 本身就是"已处置/已解决"的权威标记 (无需再造一个新标记事件), 由本巡检 (与
# reconcile_blocked_reflows/scan_stranded_worktrees 同节奏, 带外哨兵每轮调) 下一轮读到 → 反查仍
# open 的 recovery finding → emit FindingResolved(confirmed_and_accepted, evidence=终态事件) 收口。
# 幂等天然: 下一轮该 finding 已在 FindingResolved 集合里, resolved 集合会排除, 不重复 emit。
_RECOVERY_FINDING_RISK_SURFACES = frozenset(
    {_CONFLICT_REFLOW_RISK_SURFACE, "autonomous-valid-reflow-loop"},
)

# stranded_batch_disposition._emit_node_touched 落的三条终态 NodeTouched kind (merged_back_then_
# removed 复用既有 promote_and_record, 表现为 WorktreePromoted, 已在下面单独判, 不在此列)。
_DISPOSITION_TERMINAL_KINDS = frozenset(
    {
        "WorktreeClosed",
        "StrandedWorktreeDeletedEmptyShell",
        "StrandedWorktreeSupersededThenRemoved",
    },
)


@dataclass(frozen=True)
class _OpenRecoveryFinding:
    """一条仍 open (无对应 FindingResolved) 的 recovery finding, 供闭合联动消费。"""

    finding_id: str
    worktree_id: str  # target.artifact


def _scan_open_recovery_findings_and_terminal_evidence(
    event_log: EventLog,
) -> tuple[list[_OpenRecoveryFinding], dict[str, str]]:
    """一遍账本建两张表: (a) 仍 open 的 recovery finding [(finding_id, worktree_id)]; (b) worktree_id
    → 终态证据 event_id (WorktreePromoted ∪ 批处置三终态, 谁先被扫到就取谁, 只作证据引用, 不影响判定
    本身 —— 判定只问"有没有", 不判"哪个更权威")。

    风险面收口: 只收 risk_surface ∈ _RECOVERY_FINDING_RISK_SURFACES (本模块两条 emit 路径自己产的
    finding), 不动其他来源的 finding (如 review/fix 产的普通 finding)。
    """
    created: dict[str, str] = {}  # finding_id -> worktree_id
    resolved: set[str] = set()
    terminal_evidence: dict[str, str] = {}  # worktree_id -> event_id
    for rec in event_log.committed_index().records():
        effective_type, payload = _unwrap_stub_rewrap(rec)
        if effective_type == "WorktreePromoted":
            if isinstance(payload, dict):
                wid = payload.get("task_id")
                if isinstance(wid, str) and wid:
                    terminal_evidence.setdefault(wid, rec.event_id)
        elif effective_type in _DISPOSITION_TERMINAL_KINDS:
            wid = payload.get("worktree_id") if isinstance(payload, dict) else None
            if isinstance(wid, str) and wid:
                terminal_evidence.setdefault(wid, rec.event_id)
        elif effective_type == EventType.FINDING_CREATED.value:
            src = _record_src(rec)
            if src.get("risk_surface") not in _RECOVERY_FINDING_RISK_SURFACES:
                continue
            fid = src.get("finding_id")
            target = src.get("target")
            wid = target.get("artifact") if isinstance(target, dict) else None
            if isinstance(fid, str) and fid and isinstance(wid, str) and wid:
                created[fid] = wid
        elif effective_type == EventType.FINDING_RESOLVED.value:
            src = _record_src(rec)
            fid = src.get("finding_id")
            if isinstance(fid, str) and fid:
                resolved.add(fid)
    open_findings = [
        _OpenRecoveryFinding(finding_id=fid, worktree_id=wid)
        for fid, wid in created.items()
        if fid not in resolved
    ]
    return open_findings, terminal_evidence


def close_disposed_reflow_findings(towow_dir: Path, event_log: EventLog) -> list[str]:
    """bug-b 闭合联动入口: 扫仍 open 的 recovery finding, 命中已回流(WorktreePromoted)/已处置(批处置
    三终态)的 worktree_id → emit FindingResolved(confirmed_and_accepted, evidence=终态事件) 收口。

    由带外哨兵每轮调用 (与 reconcile_blocked_reflows/scan_stranded_worktrees 同一巡检节奏, 不新起
    独立调度)。走正规 M-1.5 FindingResolved 语义 (Path A 经 commit gate), 非 write_direct 绕过门。
    返回本轮成功 emit 的 FindingResolved event_id 列表。
    """
    from towow.l2.reflow_commit_gate import emit_finding_resolved_via_gate
    from towow.l2.orchestrator import _build_recovery_finding_resolved_intent

    open_findings, terminal_evidence = _scan_open_recovery_findings_and_terminal_evidence(event_log)
    closed: list[str] = []
    for of in open_findings:
        evidence_event_id = terminal_evidence.get(of.worktree_id)
        if evidence_event_id is None:
            continue  # 未处置/未回流 — 仍是真 open, 不动 (never-destroy 精神的闭合侧对应: 拿不准不收)
        intent = _build_recovery_finding_resolved_intent(
            finding_id=of.finding_id,
            resolution_evidence=(
                f"工位 {of.worktree_id} 已回流/处置 (终态证据 {evidence_event_id}) — recovery finding "
                "的目标已消解 (回流成功或经 stranded-worktree-batch-disposition 权威处置), 无需再派 fix"
            ),
        )
        event_id = emit_finding_resolved_via_gate(
            event_log, intent, towow_dir, closure="reflow-disposed-close",
        )
        if event_id:
            closed.append(event_id)
            # A-2: 工位真被联动闭合解决 — 铸造计数 + 升级静默一并清零 (计数不该背着一个已解决问题的
            # 历史铸造次数不放; 小补丁包 a 之前, stranded/triage 家族从不写这两个文件, unlink 是
            # 无操作 —— 三路径共享铸造计数器后, stranded/triage 工位现在也会真写它们, 这里清零
            # 对它们同样生效, 不再是无操作)。
            clear_reflow_conflict_mint_state(towow_dir, of.worktree_id)
    return closed
