"""T-L1-35 (RUN-034b / 0-E-3) — build_active_obligation_declarations 单测.

钉住: active obligation declarations 真从 obligation_lifecycle_state projection 的
canonical_state==active 节点逐条派生, status 由真实 ObligationViolated(by session)信号判,
justification 逐条带可核 evidence —— 非恒-maintained 占位假列表 (反陷阱).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.task_run_completed_checks import (
    build_active_obligation_declarations,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_events(towow_dir: Path, events: list[dict]) -> None:
    (towow_dir / "events.log").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8",
    )


def test_declarations_from_active_projection_nodes(tmp_path: Path) -> None:
    (tmp_path / "events.log").write_text("", encoding="utf-8")
    obligations = [
        {"obligation_id": "obl-redline", "canonical_state": "active", "severity": "red_line"},
        {"obligation_id": "obl-normal", "canonical_state": "active", "severity": "normal"},
        {"obligation_id": "obl-retired", "canonical_state": "retired", "severity": "normal"},
        {"obligation_id": "obl-superseded", "canonical_state": "superseded", "severity": "normal"},
    ]
    decls, evidence = build_active_obligation_declarations(tmp_path, obligations, "sess-x")
    declared_ids = {d["obligation_id"] for d in decls}
    # 只 declare canonical_state==active 的 (retired / superseded 不进)
    assert declared_ids == {"obl-redline", "obl-normal"}
    # 无 violation → 全 maintained
    assert all(d["status"] == "maintained" for d in decls)
    # 反陷阱: 每条带可核 justification (非恒定占位)，含 severity + session
    for d in decls:
        assert d.get("justification")
        assert "sess-x" in d["justification"]
    # red_line 的 justification 跟 normal 的不同 (severity 真带进去, 非恒定串)
    by_id = {d["obligation_id"]: d["justification"] for d in decls}
    assert "red_line" in by_id["obl-redline"]
    assert "normal" in by_id["obl-normal"]
    assert "2 active obligation(s)" in evidence


def test_declaration_flips_to_violated_on_session_obligation_violated(tmp_path: Path) -> None:
    """真实 ObligationViolated(by this session) 信号 → status 翻 violated (非恒 maintained)."""
    events = [
        {
            "event_id": "evt-viol-1",
            "event_type": "ObligationViolated",
            "payload": {"obligation_id": "obl-redline"},
            "provenance": {"session_id": "sess-x"},
        },
    ]
    _write_events(tmp_path, events)
    obligations = [
        {"obligation_id": "obl-redline", "canonical_state": "active", "severity": "red_line"},
        {"obligation_id": "obl-normal", "canonical_state": "active", "severity": "normal"},
    ]
    decls, _ = build_active_obligation_declarations(tmp_path, obligations, "sess-x")
    by_id = {d["obligation_id"]: d for d in decls}
    assert by_id["obl-redline"]["status"] == "violated"
    assert "evt-viol-1" in by_id["obl-redline"]["justification"]
    # 同一 obligation 但别的 session 不影响本 session 判定
    assert by_id["obl-normal"]["status"] == "maintained"


def test_other_session_violation_does_not_flip(tmp_path: Path) -> None:
    """别的 session 的 ObligationViolated 不让本 session 的 declaration 翻 violated (session 关联)."""
    events = [
        {
            "event_id": "evt-viol-other",
            "event_type": "ObligationViolated",
            "payload": {"obligation_id": "obl-redline"},
            "provenance": {"session_id": "sess-OTHER"},
        },
    ]
    _write_events(tmp_path, events)
    obligations = [
        {"obligation_id": "obl-redline", "canonical_state": "active", "severity": "red_line"},
    ]
    decls, _ = build_active_obligation_declarations(tmp_path, obligations, "sess-x")
    assert decls[0]["status"] == "maintained"


def test_empty_projection_yields_empty_declarations(tmp_path: Path) -> None:
    (tmp_path / "events.log").write_text("", encoding="utf-8")
    decls, evidence = build_active_obligation_declarations(tmp_path, [], "sess-x")
    assert decls == []
    assert "no active obligations" in evidence
