"""水位线无关回流对账 (缺陷A 根治, T-REFLOW-02 / reflow_reconcile.py) 测试。

RED-LINE: 每个 git-touching 测试跑在隔离 temp git repo, 绝不碰 live harness 工作树。

核心要验的是 autonomous-valid-reflow-loop 的不变量 inv-watermark-independent-reconcile:
对账不看水位线窗口、扫全账本检出所有 completed-success-yet-unpromoted 工位。故测试故意在完工
事件【之后】再灌入大量无关事件 (模拟完工落到任何"窗口"之后), 仍要被检出 —— 这正是
_auto_promote_completed_worktrees 的窗口扫描会漏、本模块要补的盲区。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import (
    _exec_worktree_id,
    ensure_orchestrator_layout,
    mark_exec_task_dispatched,
)
from towow.l2.reflow_reconcile import scan_stranded_worktrees
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.shell.worktree import WorktreeManager

if TYPE_CHECKING:
    from pathlib import Path


def _emit_task_run_completed(log: EventLog, task_id: str, outcome: str = "success") -> str:
    intent = EventIntent(
        local_intent_id=f"trc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": task_id,
            "touch_type": "write",
            "kind": "TaskRunCompleted",
            "stub_original_payload": {
                "kind": "TaskRunCompleted",
                "after_state": {"task_id": task_id, "outcome": outcome},
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-emitter"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    return log.write_direct(intent).event_id


def _emit_worktree_promoted(log: EventLog, worktree_id: str) -> str:
    intent = EventIntent(
        local_intent_id=f"wp-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": worktree_id,
            "touch_type": "write",
            "kind": "WorktreePromoted",
            "stub_original_payload": {"kind": "WorktreePromoted", "task_id": worktree_id},
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-emitter"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=worktree_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    return log.write_direct(intent).event_id


def _emit_noise(log: EventLog, n: int) -> None:
    """灌 n 条无关事件 (模拟完工后水位线被推过 — 窗口扫描会漏, 全量对账不漏)。"""
    for _ in range(n):
        _emit_task_run_completed(log, f"noise-{uuid.uuid4().hex[:8]}", outcome="aborted_external")


@pytest.fixture
def towow(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "isolated_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.t"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)
    td = repo / ".towow"
    td.mkdir()
    ensure_orchestrator_layout(td)
    return td


def _make_worktree(towow: Path, raw_task_id: str) -> tuple[Path, str]:
    wid = _exec_worktree_id(raw_task_id)
    mgr = WorktreeManager(towow, use_real_git=True)
    wt = mgr.create(task_id=wid, actor_id="exec-1", write_set=["f.txt"], branch=f"task-{wid}")
    return wt, wid


def test_scan_detects_completed_unpromoted_even_after_watermark(towow: Path) -> None:
    """完工成功 + 有隔离工位 + 无 WorktreePromoted → 即便完工事件后灌入大量事件 (落窗口外), 仍被全量对账检出。"""
    log = EventLog(towow / "events.log")
    raw_task_id = "T-STRANDED-1"
    wt, _wid = _make_worktree(towow, raw_task_id)
    mark_exec_task_dispatched(towow, raw_task_id, {"task_id": raw_task_id, "worktree": str(wt)})
    _emit_task_run_completed(log, raw_task_id, outcome="success")
    _emit_noise(log, 50)  # 完工落到水位线之后 — 窗口扫描漏, 全量对账不该漏

    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    ids = {s.task_id for s in stranded}
    assert raw_task_id in ids, f"全量对账漏掉了落窗口外的搁浅工位; got {ids}"
    sw = next(s for s in stranded if s.task_id == raw_task_id)
    assert sw.worktree_id == wt.name


def test_scan_excludes_already_promoted(towow: Path) -> None:
    """已有 WorktreePromoted → 不算搁浅, 不报。"""
    log = EventLog(towow / "events.log")
    raw_task_id = "T-PROMOTED-1"
    wt, _wid = _make_worktree(towow, raw_task_id)
    mark_exec_task_dispatched(towow, raw_task_id, {"task_id": raw_task_id, "worktree": str(wt)})
    _emit_task_run_completed(log, raw_task_id, outcome="success")
    _emit_worktree_promoted(log, wt.name)  # 已回流

    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    assert raw_task_id not in {s.task_id for s in stranded}


def test_scan_excludes_non_isolated_task(towow: Path) -> None:
    """完工但无 exec dispatch 戳 (非隔离任务, 共享主树) → 无需回流, 不报。"""
    log = EventLog(towow / "events.log")
    _emit_task_run_completed(log, "T-SHARED-TREE", outcome="success")
    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    assert "T-SHARED-TREE" not in {s.task_id for s in stranded}


def test_scan_excludes_non_success(towow: Path) -> None:
    """完工但 outcome != success → 不是"完工成功未回流", 不报。"""
    log = EventLog(towow / "events.log")
    raw_task_id = "T-ABORTED-1"
    wt, _wid = _make_worktree(towow, raw_task_id)
    mark_exec_task_dispatched(towow, raw_task_id, {"task_id": raw_task_id, "worktree": str(wt)})
    _emit_task_run_completed(log, raw_task_id, outcome="aborted_unrecoverable")
    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    assert raw_task_id not in {s.task_id for s in stranded}


# ── 缺陷A 在 fix 路径 (f-review-reflow-defectA-fixside-watermark-blindspot): scan 增扫 FixCompleted ──


def _emit_finding_created(log: EventLog, finding_id: str) -> str:
    """emit native FINDING_CREATED via path-A commit gate, 返回 event_id (= fix 工位 key)。"""
    from towow.l2.daemon_run_once import _emit_finding_via_gate

    intent = EventIntent(
        local_intent_id=f"fc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload={
            "finding_id": finding_id,
            "finding_kind": "obligation_issue",
            "severity": "major",
            "risk_surface": "test surface",
            "lifecycle_state": "created",
            "description": "reflow fix-side test finding",
            "detection_method": "automated_rule",
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(
            entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY,
        )],
        schema_version="1.0.0",
    )
    eid = _emit_finding_via_gate(log, intent, log._log_path.parent, closure="fix-side reflow test")
    assert eid is not None
    return eid


def _emit_fix_proposed(log: EventLog, fix_id: str, related_finding_id: str, sha: str) -> None:
    intent = EventIntent(
        local_intent_id=f"fp-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "finding", "target_entity_id": related_finding_id,
            "touch_type": "write", "kind": "FixProposed",
            "stub_original_payload": {
                "kind": "FixProposed",
                "after_state": {
                    "fix_id": fix_id, "related_finding_id": related_finding_id,
                    "worktree_commit_sha": sha,
                },
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(
            entity_type=SubjectEntityType.FINDING, entity_id=related_finding_id, role=SubjectRole.PRIMARY,
        )],
        schema_version="1.0.0",
    )
    log.write_direct(intent)


def _emit_fix_completed(log: EventLog, fix_id: str, outcome: str = "resolved") -> str:
    intent = EventIntent(
        local_intent_id=f"fcomp-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "finding", "target_entity_id": fix_id,
            "touch_type": "write", "kind": "FixCompleted",
            "stub_original_payload": {
                "kind": "FixCompleted",
                "after_state": {"fix_id": fix_id, "outcome": outcome},
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(
            entity_type=SubjectEntityType.FINDING, entity_id=fix_id, role=SubjectRole.PRIMARY,
        )],
        schema_version="1.0.0",
    )
    return log.write_direct(intent).event_id


def _make_detached_fix_worktree(towow: Path, fix_key: str) -> tuple[Path, str]:
    """造 detached fix 工位 + commit 一个不在 main 的改动。返回 (path, commit_sha)。"""
    import subprocess

    from towow.l2.orchestrator import _fix_worktree_id

    wid = _fix_worktree_id(fix_key)
    mgr = WorktreeManager(towow, use_real_git=True)
    wt = mgr.create(
        task_id=wid, actor_id="fix-1", write_set=[], branch=None, detached=True, write_owner=False,
    )
    (wt / "fixwork.py").write_text("z = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"fix {wid}"], cwd=str(wt), check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(wt), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return wt, sha


def test_scan_detects_orphaned_fix_worktree(towow: Path) -> None:
    """FixCompleted(resolved) + detached 工位 commit 不在 main + 工位仍在 → 被全量对账检出为搁浅 (fix 侧)。"""
    log = EventLog(towow / "events.log")
    finding_id = "f-fixside-strand"
    finding_eid = _emit_finding_created(log, finding_id)
    wt, sha = _make_detached_fix_worktree(towow, finding_eid)
    _emit_fix_proposed(log, "fix-strand-1", finding_id, sha)
    _emit_fix_completed(log, "fix-strand-1", outcome="resolved")

    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    ids = {s.task_id for s in stranded}
    assert "fix-strand-1" in ids, f"fix 侧搁浅未被全量对账检出; got {ids}"
    sw = next(s for s in stranded if s.task_id == "fix-strand-1")
    assert sw.worktree_id == wt.name


def test_scan_excludes_reflowed_fix_worktree(towow: Path) -> None:
    """fix 的 commit 已是 main 祖先 (已回流) → 不算搁浅, 即便工位目录仍在。"""
    import subprocess

    log = EventLog(towow / "events.log")
    finding_id = "f-fixside-reflowed"
    finding_eid = _emit_finding_created(log, finding_id)
    wt, _sha = _make_detached_fix_worktree(towow, finding_eid)
    # worktree_commit_sha 指向 main HEAD (trivially main 祖先 = 已回流), 工位仍在
    main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=str(towow.parent),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _emit_fix_proposed(log, "fix-reflowed-1", finding_id, main_sha)
    _emit_fix_completed(log, "fix-reflowed-1", outcome="resolved")

    stranded = scan_stranded_worktrees(towow, EventLog(towow / "events.log"))
    assert "fix-reflowed-1" not in {s.task_id for s in stranded}
    assert wt.exists()  # 工位仍在但不报 (判据是 commit 可达, 非工位存在)


# ── f-review-reflow-stranded-finding-storm: 共享幂等 marker 防风暴 (同路径多轮不重 emit) ──


def test_emit_idempotent_no_finding_storm(towow: Path) -> None:
    """同一搁浅工位连续两轮 emit → 第二轮被共享 marker 挡住 (不每轮派新 fix = 不风暴)。"""
    from towow.l2.reflow_reconcile import emit_stranded_reflow_findings

    log = EventLog(towow / "events.log")
    raw_task_id = "T-STORM-1"
    wt, _wid = _make_worktree(towow, raw_task_id)
    mark_exec_task_dispatched(towow, raw_task_id, {"task_id": raw_task_id, "worktree": str(wt)})
    _emit_task_run_completed(log, raw_task_id, outcome="success")

    fresh = EventLog(towow / "events.log")
    stranded = scan_stranded_worktrees(towow, fresh)
    now = 1_000_000.0
    first = emit_stranded_reflow_findings(towow, fresh, stranded, now=now)
    # 第二轮 (in-flight 期内, now 几乎不变) → marker 新鲜 → skip
    second = emit_stranded_reflow_findings(towow, fresh, stranded, now=now + 60)
    assert len(first) == 1
    assert second == [], "in-flight 期内重复 emit = finding 风暴 (应被共享 marker 挡)"


# ── f-review-reflow-sr-deadletter-coupling: recovery finding 不被 §SR 接纳 (resolution 层) ──


def test_recovery_finding_skipped_by_sr_resolution(towow: Path) -> None:
    """reflow recovery finding (target.artifact=worktree_id) → §SR _resolve_effect_task_for_finding 返 None
    (worktree_id 非执行任务 → _is_execution_task 不命中 → §SR 一致 skip, 不把回流磕绊误当效果不达拉回重算
    → 不会熔断 dead-letter 已合法完工的能力任务)。

    去混淆 (证明 None 来自 worktree_id 判别器, 不是测试空过): 显式断言这条 recovery finding 的 rule_id
    在 §SR 标记门 _EFFECT_REVIEW_RULE_IDS 内 —— 即它【通过】了 rule_id 门, 故 None 必出自 path(a)/(b)
    的 worktree_id-非执行任务判别, 这正是 fix #2 焊上的解耦点。
    """
    from towow.l2.daemon_run_once import _emit_finding_via_gate
    from towow.l2.orchestrator import (
        _EFFECT_REVIEW_RULE_IDS,
        _build_recovery_finding_intent,
        _resolve_effect_task_for_finding,
    )
    from towow.schemas.enums import FindingKind

    log = EventLog(towow / "events.log")
    # worktree_id 形态 (slug__sha) — 非任何 TaskNodeCreated 的 task_id
    wt_id = "t-strand__deadbeef00112233"
    intent = _build_recovery_finding_intent(
        finding_id="f-sr-skip", severity="major", risk_surface="autonomous-valid-reflow-loop",
        description="reflow recovery (worktree-targeted)",
        finding_kind=FindingKind.SYSTEM_GOVERNANCE_DEFECT.value, effect_task=wt_id,
    )
    # 去混淆: 这条 finding 确实通过 §SR 的 rule_id 标记门 (否则 None 可能只因门拒, 测不到判别器)
    assert intent.payload["rule_id"] in _EFFECT_REVIEW_RULE_IDS
    assert intent.payload["target"]["artifact"] == wt_id  # fix #2: target 锚 worktree_id 非 task_id
    assert _emit_finding_via_gate(log, intent, towow, closure="sr-skip-test") is not None

    fresh = EventLog(towow / "events.log")
    assert _resolve_effect_task_for_finding(fresh, "f-sr-skip") is None, (
        "worktree-targeted recovery finding 不该被 §SR 解析 (否则会 dead-letter 完工任务)"
    )
