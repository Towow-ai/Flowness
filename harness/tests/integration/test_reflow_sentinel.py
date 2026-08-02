"""带外回流产出哨兵 (out-of-band-reflow-sla-sentinel, T-REFLOW-04) 测试。

验: 哨兵盯【产出 SLA】(完工却未回流且超龄), 而非心跳; 超龄搁浅→检出+触发 Finding→Fix;
在飞 (未超 SLA) 的不误报; dry-run 只报不 emit。RED-LINE: 隔离 temp git repo。
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

import pytest

from towow.l0.event_log import EventLog
from towow.l2.orchestrator import (
    _exec_worktree_id,
    ensure_orchestrator_layout,
    mark_exec_task_dispatched,
)
from towow.l2.reflow_reconcile import StrandedWorktree
from towow.l2.reflow_sentinel import (
    DEFAULT_MIN_AGE_SECONDS,
    _worktree_age_seconds,
    run_sentinel_once,
    scan_stranded_past_sla,
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
from towow.shell.worktree import WorktreeManager


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


@pytest.fixture
def towow(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
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


def _make_stranded(towow: Path, raw_task_id: str) -> None:
    """建一个'完工成功 + 有隔离工位 + 无 WorktreePromoted'的搁浅工位。"""
    wid = _exec_worktree_id(raw_task_id)
    mgr = WorktreeManager(towow, use_real_git=True)
    wt = mgr.create(task_id=wid, actor_id="exec-1", write_set=["f.txt"], branch=f"task-{wid}")
    mark_exec_task_dispatched(towow, raw_task_id, {"task_id": raw_task_id, "worktree": str(wt)})
    _emit_task_run_completed(EventLog(towow / "events.log"), raw_task_id, outcome="success")


def test_sentinel_detects_stranded_past_sla(towow: Path) -> None:
    """超 SLA 的搁浅 → 检出 + emit Finding (走 Finding→Fix)。"""
    _make_stranded(towow, "T-OLD-STRAND")
    # now 设到工位 mtime + min_age + 余量 → 视作超龄
    future = time.time() + DEFAULT_MIN_AGE_SECONDS + 60
    report = run_sentinel_once(towow, emit=True, now=future)
    assert "T-OLD-STRAND" in report.stranded_past_sla
    assert report.stranded_count == 1
    assert len(report.findings_emitted) == 1


def test_sentinel_skips_in_flight_within_sla(towow: Path) -> None:
    """刚完工、未超 SLA 的工位 (在飞) → 不误报 (orchestrator 本轮窗口会正常 promote)。"""
    _make_stranded(towow, "T-FRESH-STRAND")
    report = run_sentinel_once(towow, emit=True, now=time.time())  # 工位刚建, age≈0 < min_age
    assert report.stranded_count == 0
    assert report.findings_emitted == []


def test_sentinel_dry_run_detects_but_no_emit(towow: Path) -> None:
    """dry-run: 检出超龄搁浅但不 emit Finding (surface-only 先行)。"""
    _make_stranded(towow, "T-DRY-STRAND")
    future = time.time() + DEFAULT_MIN_AGE_SECONDS + 60
    report = run_sentinel_once(towow, emit=False, now=future)
    assert report.stranded_count == 1
    assert report.findings_emitted == []


def test_scan_past_sla_age_filter(towow: Path) -> None:
    """scan_stranded_past_sla 的 SLA 过滤: 同一搁浅工位, now 早(未超龄)→空, now 晚(超龄)→命中。"""
    _make_stranded(towow, "T-AGE")
    log = EventLog(towow / "events.log")
    fresh = scan_stranded_past_sla(towow, log, now=time.time())
    aged = scan_stranded_past_sla(towow, log, now=time.time() + DEFAULT_MIN_AGE_SECONDS + 60)
    assert [s.task_id for s in fresh] == []
    assert [s.task_id for s in aged] == ["T-AGE"]


# ── f-review-reflow-sentinel-age-oserror-silent-drop: stat 失败两类区分 ──


def _sw(path: Path) -> StrandedWorktree:
    return StrandedWorktree(
        task_id="t", worktree_id="w", worktree_path=path, completion_event_id="e",
    )


def test_age_filenotfound_is_zero(tmp_path: Path) -> None:
    """工位目录没了 (FileNotFoundError = 已 cleanup) → 0.0 (合法不超龄, 不告警)。"""
    sw = _sw(tmp_path / "gone" / "nowhere")
    assert _worktree_age_seconds(sw, now=time.time()) == 0.0


def test_age_other_oserror_is_inf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stat 读不了 (非 FileNotFound 的 OSError, 如权限/IO) = 监控探针自身失败 → inf (偏告警, 不静默漏)。"""
    sw = _sw(tmp_path)
    real_stat = Path.stat

    def boom(self: Path, *a: object, **k: object) -> object:
        if self == sw.worktree_path:
            raise PermissionError("stat denied")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", boom)
    assert _worktree_age_seconds(sw, now=time.time()) == float("inf")


# ── f-review-reflow-conflict-marker-permanent-blindness: 陈旧 recovery marker 经哨兵独立 backstop 重发 ──


def test_sentinel_stale_marker_reemits_backstop(towow: Path) -> None:
    """超 SLA 搁浅 emit 后: 在飞期内不重发; marker 陈旧 (>2×min_age) 且工位仍未回流 → 独立 backstop 重发。"""
    _make_stranded(towow, "T-STALE-BACKSTOP")
    base = time.time() + DEFAULT_MIN_AGE_SECONDS + 60  # 超 SLA
    first = run_sentinel_once(towow, emit=True, now=base)
    assert len(first.findings_emitted) == 1, "首发应 emit 一条 recovery finding"

    # 在飞期内 (marker 新鲜) — 不重发 (防风暴)
    inflight = run_sentinel_once(towow, emit=True, now=base + 60)
    assert inflight.findings_emitted == [], "in-flight 期内不该重发"

    # marker 陈旧 (> 2×min_age) 且工位仍未回流 → 独立 backstop 重发 (不依赖 fix 自报闭合)
    stale = run_sentinel_once(towow, emit=True, now=base + 2 * DEFAULT_MIN_AGE_SECONDS + 120)
    assert len(stale.findings_emitted) == 1, "陈旧且仍未回流应 backstop 重发"
