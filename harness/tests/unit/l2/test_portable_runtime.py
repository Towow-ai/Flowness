from __future__ import annotations

from pathlib import Path

import pytest

from towow.l2 import portable_runtime


def _no_optional_adapter(_name: str) -> object:
    raise ModuleNotFoundError


def test_public_runtime_without_private_adapters_is_explicit_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portable_runtime.importlib, "import_module", _no_optional_adapter)

    assert portable_runtime.default_status_path(tmp_path) is None
    assert portable_runtime.governor_gate(tmp_path) == (False, None)
    assert portable_runtime.governance_finding_dispatch_decision(
        "major", owner_unfrozen=True,
    ) == "dashboard"
    assert portable_runtime.maybe_revive_stalled_session(tmp_path, object(), "gs-1") is False
    portable_runtime.clear_revive_marker(tmp_path, "gs-1")
    portable_runtime.sweep_inv_e_refreeze(tmp_path, object())

    with pytest.raises(RuntimeError, match="authorized runtime adapter"):
        portable_runtime.emit_finding_via_gate(
            object(), object(), tmp_path, closure="fixture",
        )


def test_public_runtime_keeps_mock_spawn_runnable_without_private_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portable_runtime.importlib, "import_module", _no_optional_adapter)

    result = portable_runtime.spawn_bg_session(
        "do the bounded task",
        tmp_path,
        "parent-1",
        method=portable_runtime.SpawnMethod.MOCK,
        goal_session_id="goal-1",
    )

    assert result.launched is False
    assert result.method is portable_runtime.SpawnMethod.MOCK
    assert result.goal_session_id == "goal-1"
    assert result.worktree_path == str(tmp_path)
    with pytest.raises(RuntimeError, match="not part of Flowness Open Alpha"):
        portable_runtime.spawn_bg_session(
            "real task",
            tmp_path,
            "parent-1",
            method=portable_runtime.SpawnMethod.CLAUDE_BG,
        )


def test_public_runtime_shares_only_an_existing_canonical_ledger(tmp_path: Path) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()

    assert portable_runtime.ensure_shared_towow(worktree, main) is False
    (main / ".towow").mkdir()
    assert portable_runtime.ensure_shared_towow(worktree, main) is True
    assert (worktree / ".towow").resolve() == (main / ".towow").resolve()
    assert portable_runtime.ensure_shared_towow(worktree, main) is True
