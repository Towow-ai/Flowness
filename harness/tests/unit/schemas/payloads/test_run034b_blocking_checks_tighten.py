"""T-L1-34 (RUN-034b / 0-E-3) — 5 项 execution blocking_check 收紧 (单测).

收紧两项原 loose check:
  - check_obligations_maintained: 从"无 ObligationViolated"(负向, 0 obligation 平凡过) 收紧成
    正向 accounting + red_line coverage gate + 违规一致性 gate。
  - check_no_unhandled_mismatch: 逻辑真 fail-closed (注入未解 mismatch → fail) — 钉住非
    vacuous; 生产 emit 源 (T-L1-38) 缺是 dependency_blocked 债 (见 FINDINGS / debt registry)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.schemas.payloads.task_run_completed_checks import (
    check_no_unhandled_mismatch,
    check_obligations_maintained,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_events(towow_dir: Path, events: list[dict]) -> None:
    (towow_dir / "events.log").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n" if events else "",
        encoding="utf-8",
    )


# ── check_no_unhandled_mismatch (逻辑真 fail-closed) ──────────────────────────


def test_no_unhandled_mismatch_fails_on_injected_unresolved(tmp_path: Path) -> None:
    """注入一个未解 mismatch (stub-rewrap NodeTouched) → check 真 fail (非 vacuous pass)."""
    _write_events(tmp_path, [
        {
            "event_id": "evt-mis-1",
            "event_type": "NodeTouched",
            "payload": {"kind": "MismatchDetected"},
            "provenance": {"run_id": "run-x"},
        },
    ])
    passed, evidence = check_no_unhandled_mismatch(tmp_path, "run-x")
    assert passed is False
    assert "unresolved MismatchDetected" in evidence


def test_no_unhandled_mismatch_passes_when_resolved(tmp_path: Path) -> None:
    _write_events(tmp_path, [
        {
            "event_id": "evt-mis-1",
            "event_type": "NodeTouched",
            "payload": {"kind": "MismatchDetected"},
            "provenance": {"run_id": "run-x"},
        },
        {
            "event_id": "evt-res-1",
            "event_type": "NodeTouched",
            "payload": {"kind": "MismatchResolutionDecided"},
            "provenance": {"run_id": "run-x"},
        },
    ])
    passed, _ = check_no_unhandled_mismatch(tmp_path, "run-x")
    assert passed is True


def test_no_unhandled_mismatch_empty_passes_with_live_source_note(tmp_path: Path) -> None:
    _write_events(tmp_path, [])
    passed, evidence = check_no_unhandled_mismatch(tmp_path, "run-x")
    assert passed is True
    # RUN-038 加固(2) 后: 生产 emit 源已接 (work mismatch CLI), 空集是"本 run 确无 mismatch",
    # 不再是"T-L1-38 债空转"。措辞订正反映这点。
    assert "production emit source live" in evidence
    assert "RUN-038" in evidence


# ── check_obligations_maintained (收紧) ──────────────────────────────────────

_ACTIVE = [
    {"obligation_id": "obl-rl-1", "canonical_state": "active", "severity": "red_line"},
    {"obligation_id": "obl-rl-2", "canonical_state": "active", "severity": "red_line"},
    {"obligation_id": "obl-norm", "canonical_state": "active", "severity": "normal"},
]


def test_obligations_pass_full_coverage_all_maintained(tmp_path: Path) -> None:
    _write_events(tmp_path, [])
    declared = [
        {"obligation_id": "obl-rl-1", "status": "maintained"},
        {"obligation_id": "obl-rl-2", "status": "maintained"},
        {"obligation_id": "obl-norm", "status": "maintained"},
    ]
    passed, evidence = check_obligations_maintained(
        tmp_path, "sess-x", active_obligations=_ACTIVE, declared=declared,
    )
    assert passed is True
    # 正向 accounting evidence 逐条枚举 (非"无违规"负向占位)
    assert "obl-rl-1:maintained" in evidence
    assert "3 obligation(s) accounted" in evidence


def test_obligations_fail_red_line_coverage_gap(tmp_path: Path) -> None:
    """active red_line obligation 漏报 (不在 declared) → fail (NEW 收紧)."""
    _write_events(tmp_path, [])
    declared = [
        {"obligation_id": "obl-rl-1", "status": "maintained"},
        # obl-rl-2 漏掉
        {"obligation_id": "obl-norm", "status": "maintained"},
    ]
    passed, evidence = check_obligations_maintained(
        tmp_path, "sess-x", active_obligations=_ACTIVE, declared=declared,
    )
    assert passed is False
    assert "coverage gap" in evidence
    assert "obl-rl-2" in evidence


def test_obligations_fail_declared_violated(tmp_path: Path) -> None:
    _write_events(tmp_path, [])
    declared = [
        {"obligation_id": "obl-rl-1", "status": "violated", "justification": "broke it"},
        {"obligation_id": "obl-rl-2", "status": "maintained"},
        {"obligation_id": "obl-norm", "status": "maintained"},
    ]
    passed, evidence = check_obligations_maintained(
        tmp_path, "sess-x", active_obligations=_ACTIVE, declared=declared,
    )
    assert passed is False
    assert "violated" in evidence


def test_obligations_fail_misreported_violation(tmp_path: Path) -> None:
    """session emit 了 ObligationViolated 但 envelope 谎报 maintained → 一致性 gate fail."""
    _write_events(tmp_path, [
        {
            "event_id": "evt-v",
            "event_type": "ObligationViolated",
            "payload": {"obligation_id": "obl-rl-1"},
            "provenance": {"session_id": "sess-x"},
        },
    ])
    declared = [
        {"obligation_id": "obl-rl-1", "status": "maintained"},  # 谎报
        {"obligation_id": "obl-rl-2", "status": "maintained"},
        {"obligation_id": "obl-norm", "status": "maintained"},
    ]
    passed, evidence = check_obligations_maintained(
        tmp_path, "sess-x", active_obligations=_ACTIVE, declared=declared,
    )
    assert passed is False
    assert "did not declare them violated" in evidence


def test_obligations_backward_compat_no_projection(tmp_path: Path) -> None:
    """active_obligations=None → 旧 loose 判定 (向后兼容)."""
    _write_events(tmp_path, [])
    passed, evidence = check_obligations_maintained(tmp_path, "sess-x")
    assert passed is True
    assert "no projection coverage check" in evidence
