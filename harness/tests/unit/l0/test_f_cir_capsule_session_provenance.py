"""F-CIR-capsule-fork-not-session — CapsuleCompiled per-session attribution.

Finding: CapsuleCompiled.provenance.session_id 恒空 → 无法把一次胶囊 (注入了哪些义务/红线/概念)
精确归到某一个会话单元; 控制台'大脑'只能退而显示阶段画像 (均值)。根因 = 胶囊以 fork 为主体
(payload.target_fork_id) 而 provenance 不带 session。

Fix: assemble_capsule 串一个 optional session_id 到 CapsuleCompiled.provenance.session_id。
- 6 个 session-start caller 经 _assemble_session_capsule (那里 session_id 必填) 盖上会话归属;
- audit fork / debug `capsule` CLI 真无归属会话 → 留 None (仍 fork-keyed, 向后兼容)。

这两条测试钉住源头行为: 给了 session_id → provenance.session_id == 它 (归属成立);
不给 → None (audit/debug 不被强塞会话, 不回归)。胶囊内容仍 fork-keyed (target_fork_id 不变)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.capsule import assemble_capsule
from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import SceneType

if TYPE_CHECKING:
    from pathlib import Path


def _setup(towow: Path) -> tuple[EventLog, ProjectionStore]:
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    log = EventLog(towow / "events.log")
    gate = CommitGate(log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())
    return log, ProjectionStore(towow / "graph")


def test_capsule_compiled_stamps_session_id(tmp_path: Path) -> None:
    """给了 session_id → emit 的 CapsuleCompiled.provenance.session_id == 它 (per-session 归属成立)。

    同时确认胶囊内容仍以 fork 为主体: payload.target_fork_id 不变 (归属 ≠ 改内容寻址)。
    """
    log, store = _setup(tmp_path / ".towow")
    res = assemble_capsule(
        event_log=log,
        projection_store=store,
        scene_type=SceneType.EXECUTION,
        task_id="task-fcir",
        target_fork_id="fork-fcir",
        session_id="sess-work-fcir0001",
    )
    cc = log.get_event(res.capsule_compiled_event_id)
    assert cc is not None
    assert cc.provenance.session_id == "sess-work-fcir0001"  # 会话归属盖上了
    assert cc.payload["target_fork_id"] == "fork-fcir"  # 内容仍 fork-keyed (不变)


def test_capsule_compiled_session_id_none_when_omitted(tmp_path: Path) -> None:
    """不给 session_id (audit fork / debug `capsule` CLI) → provenance.session_id is None。

    向后兼容 + 语义正确: 这类 fork 真无归属会话, 不该被强塞一个会话 id。
    """
    log, store = _setup(tmp_path / ".towow")
    res = assemble_capsule(
        event_log=log,
        projection_store=store,
        scene_type=SceneType.AUDIT,
        task_id="task-audit",
        target_fork_id="audit-fork",
        # session_id 省略 — 默认 None
    )
    cc = log.get_event(res.capsule_compiled_event_id)
    assert cc is not None
    assert cc.provenance.session_id is None  # 无归属会话 → 不编造


def test_session_id_not_in_content_addressing_distinct_events(tmp_path: Path) -> None:
    """同一 harness 两个会话装内容相同的胶囊: 各得独立 CapsuleCompiled (cache 不复用旧事件),
    各盖自己的 session_id, 但 input_hash + capsule_content_hash 一致 (session_id 不进内容寻址)。

    这同时钉住三件事:
    (1) cache 命中只短路 resolver (stage 4), stage 8 仍 fresh emit → 每会话独立事件, 不会两个
        会话共享一个 CapsuleCompiled (那会让第二个会话的归属串到第一个 — 重新引入本 finding);
    (2) session_id 是 provenance 元数据, 不折进 input_hash (cache key) / §9.2 capsule_content_hash;
    (3) 故 gate 倒查 (按 fork_name=fork_id) 与 cache 命中都不受 session 归属影响。
    """
    log, store = _setup(tmp_path / ".towow")
    common = {
        "scene_type": SceneType.EXECUTION,
        "task_id": "task-same",
        "target_fork_id": "fork-same",
    }
    res_a = assemble_capsule(
        event_log=log, projection_store=store, session_id="sess-aaa", **common,
    )
    res_b = assemble_capsule(
        event_log=log, projection_store=store, session_id="sess-bbb", **common,
    )
    cc_a = log.get_event(res_a.capsule_compiled_event_id)
    cc_b = log.get_event(res_b.capsule_compiled_event_id)
    assert cc_a is not None
    assert cc_b is not None
    # (1) 各自独立事件 — cache 不把两个会话合并到一个 CapsuleCompiled。
    assert cc_a.event_id != cc_b.event_id
    # (2a) 各盖自己的 session_id。
    assert cc_a.provenance.session_id == "sess-aaa"
    assert cc_b.provenance.session_id == "sess-bbb"
    # (2b) 内容寻址完全一致 — session_id 不进 cache key / 审计指纹。
    assert cc_a.payload["input_hash"] == cc_b.payload["input_hash"]
    assert cc_a.payload["capsule_content_hash"] == cc_b.payload["capsule_content_hash"]
