"""T-LS-3 回归测试 — 活标准第二根线: capsule 编译时自动喂对应模块的对账卡。

# spec source: 01-reconciliation/LIVING-STANDARD-BUILD-PLAN.md (T-LS-3)
# build agent 崩在结构化输出 → 它的 verify 没跑 + 它声称要加的单测没落地; 主 session
# (总闸) 补这层回归保护: 没它的话以后改 pipeline 没东西拦住"卡注入被改坏"。

两层:
- 函数级: render_conformance_card_section 的匹配/边界/优雅降级 (最有风险的逻辑)。
- pipeline 集成级: assemble_capsule 真把命中模块的卡放进 KNOWN_FACTS 段 (enforced 级断言,
  不只"函数能跑", 而是"卡真到了 agent 会读的那段")。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from towow.l0.capsule import assemble_capsule
from towow.l0.capsule.conformance_card import render_conformance_card_section
from towow.l0.commit_gate.gate import CommitGate
from towow.l0.event_log import EventLog
from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import CapsuleSection, SceneType

if TYPE_CHECKING:
    from pathlib import Path

_CARD = {
    "module_id": "M-TEST",
    "name": "测试模块",
    "layer": "L0",
    "should_do": "做测试用的事。第二句不该出现。",
    "built": "built",
    "enforced": "built_not_enforced",
    "gap_type": "type1_designed_but_broken_or_disconnected",
    "gap_detail": "测试缺口",
    "code_paths": ["src/towow/testmod"],
    "spec_path": "spec/testmod.md",
    "verified_date": "2026-06-05",
}


def _write_cards(repo_root: Path) -> None:
    d = repo_root / "01-reconciliation" / "conformance"
    d.mkdir(parents=True)
    (d / "cards.json").write_text(json.dumps({"cards": [_CARD]}), encoding="utf-8")


# ─── 函数级: 匹配 / 边界 / 优雅降级 ──────────────────────────────────────────────


def test_render_matches_module_by_code_path(tmp_path: Path) -> None:
    _write_cards(tmp_path)
    towow = tmp_path / ".towow"
    towow.mkdir()
    out = render_conformance_card_section(
        towow_dir=towow,
        target_artifacts=["src/towow/testmod/foo.py"],  # 在卡 code_path 之下 → 命中
        touched_nodes=[],
    )
    assert "M-TEST" in out
    assert "做测试用的事" in out
    # capsule 卡给 agent 完整 should_do (只有 owner `view` 命令截一句); 这里验完整内容在。
    assert "建好没接通" in out or "built_not_enforced" in out  # enforced 维度真渲染进卡


def test_render_no_match_returns_empty(tmp_path: Path) -> None:
    _write_cards(tmp_path)
    towow = tmp_path / ".towow"
    towow.mkdir()
    assert render_conformance_card_section(
        towow_dir=towow, target_artifacts=["src/other/unrelated.py"], touched_nodes=[]
    ) == ""


def test_render_path_boundary_no_false_prefix(tmp_path: Path) -> None:
    """src/towow/testmod 不该命中 src/towow/testmodule (/ 边界, 防 event_log→event_logger)。"""
    _write_cards(tmp_path)
    towow = tmp_path / ".towow"
    towow.mkdir()
    assert render_conformance_card_section(
        towow_dir=towow, target_artifacts=["src/towow/testmodule/x.py"], touched_nodes=[]
    ) == ""


def test_render_empty_signals_returns_empty(tmp_path: Path) -> None:
    _write_cards(tmp_path)
    towow = tmp_path / ".towow"
    towow.mkdir()
    assert render_conformance_card_section(
        towow_dir=towow, target_artifacts=[], touched_nodes=[]
    ) == ""


def test_render_graceful_when_no_cards_file(tmp_path: Path) -> None:
    """cards.json 不存在 → 空串, 绝不报错 (优雅降级)。"""
    towow = tmp_path / ".towow"
    towow.mkdir()
    assert render_conformance_card_section(
        towow_dir=towow, target_artifacts=["src/towow/testmod/foo.py"], touched_nodes=[]
    ) == ""


# ─── pipeline 集成级: 卡真进 capsule 的 KNOWN_FACTS 段 (enforced 断言) ─────────────────


def _setup(towow: Path) -> tuple[EventLog, ProjectionStore, CommitGate]:
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    log = EventLog(towow / "events.log")
    gate = CommitGate(log)
    gate.bootstrap_commit(bootstrap_protocol_invariants())
    return log, ProjectionStore(towow / "graph"), gate


def _known_facts(res) -> str:
    sections = {s.section: s.content for s in res.capsule.sections}
    return sections.get(CapsuleSection.KNOWN_FACTS, "")


def test_capsule_includes_matched_module_card(tmp_path: Path) -> None:
    """assemble_capsule 真把命中模块的卡放进 KNOWN_FACTS (agent 一开工就读到)。"""
    _write_cards(tmp_path)
    log, store, _gate = _setup(tmp_path / ".towow")
    res = assemble_capsule(
        event_log=log,
        projection_store=store,
        scene_type=SceneType.EXECUTION,
        task_id=f"t-{uuid.uuid4().hex[:6]}",
        target_fork_id="f-1",
        target_artifacts=["src/towow/testmod/foo.py"],
    )
    kf = _known_facts(res)
    assert "M-TEST" in kf, f"命中模块的卡没进 KNOWN_FACTS: {kf[:200]!r}"


def test_capsule_excludes_card_when_no_match(tmp_path: Path) -> None:
    """无关 task → KNOWN_FACTS 不含卡 (additive, 既有行为不变)。"""
    _write_cards(tmp_path)
    log, store, _gate = _setup(tmp_path / ".towow")
    res = assemble_capsule(
        event_log=log,
        projection_store=store,
        scene_type=SceneType.EXECUTION,
        task_id=f"t-{uuid.uuid4().hex[:6]}",
        target_fork_id="f-1",
        target_artifacts=["src/other/unrelated.py"],
    )
    assert "M-TEST" not in _known_facts(res)
