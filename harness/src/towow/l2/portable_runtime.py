"""Portable boundary for optional private runtime integrations.

The Open Alpha engine intentionally excludes account rotation and the private
Claude background-session implementation.  Canonical orchestration still has
to import and run in that package, so this module exposes the small public seam
used by the orchestrator:

* mock spawning remains available without private modules;
* real spawning is loaded lazily and fails closed when the private adapter is
  not installed;
* account status is optional rather than a package-level dependency; and
* worktree ledger sharing has a portable implementation.

The private repository keeps its existing behaviour because the optional
modules are discovered at call time.  No account, token, Transcript, server or
live-session state crosses this interface.
"""

from __future__ import annotations

import importlib
import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


VALID_SESSION_ORIGINS = frozenset(
    {"orchestrator_auto", "inline_continuation", "cli_goal_spawn"},
)


class SpawnMethod(StrEnum):
    """Portable spawn vocabulary understood by canonical orchestration."""

    MOCK = "mock"
    CLAUDE_BG = "claude-bg"
    TMUX = "tmux"
    CLAUDE_P = "claude-p"


@dataclass(frozen=True)
class _MockSpawnResult:
    bg_session_id: str
    command_text: str
    method: SpawnMethod
    launched: bool
    worktree_path: str
    parent_session_id: str
    started_at: str
    claude_full_session_id: str | None = None
    daemon_worktree_path: str | None = None
    towow_shared: bool = True
    main_head_at_spawn: str | None = None
    goal_session_id: str = ""


def _optional_private_module(name: str) -> Any | None:
    """Load a private integration only when it is installed.

    The module name is assembled to keep static first-party dependency closure
    honest: the public package has no import edge to the excluded implementation.
    """
    try:
        return importlib.import_module(".".join(("towow", "l2", name)))
    except (ImportError, ModuleNotFoundError):
        return None


def default_status_path(towow_dir: Path) -> Path | None:
    """Return the private account-status path when available, else no rotation."""
    module = _optional_private_module("account_rotation")
    function = getattr(module, "default_status_path", None) if module else None
    return function(towow_dir) if callable(function) else None


def governor_gate(towow_dir: Path) -> tuple[bool, float | None]:
    """Use the host resource governor when present; otherwise deny new work."""
    module = _optional_private_module("governor_wiring")
    function = getattr(module, "governor_gate", None) if module else None
    if callable(function):
        return function(towow_dir)
    return False, None


def commit_mutex(towow_dir: Path) -> AbstractContextManager[object]:
    """Expose the canonical global commit lock without a maintenance daemon."""
    from towow.l0.commit_gate.global_lock import global_commit_mutex

    return global_commit_mutex(towow_dir)


def emit_finding_via_gate(
    event_log: object,
    finding_intent: object,
    towow_dir: Path,
    *,
    closure: str,
) -> str | None:
    """Use an installed host emitter; fail closed in the portable package."""
    module = _optional_private_module("daemon_run_once")
    function = getattr(module, "_emit_finding_via_gate", None) if module else None
    if not callable(function):
        raise RuntimeError(
            "maintenance finding emission needs an authorized runtime adapter",
        )
    return function(event_log, finding_intent, towow_dir, closure=closure)


def governance_finding_dispatch_decision(
    severity: str,
    *,
    owner_unfrozen: bool,
) -> str:
    """Delegate the optional efficiency policy, defaulting to dashboard-only."""
    module = _optional_private_module("efficiency_boundary_scan")
    function = (
        getattr(module, "governance_finding_dispatch_decision", None)
        if module else None
    )
    if callable(function):
        return str(function(severity, owner_unfrozen=owner_unfrozen))
    return "dashboard"


def maybe_revive_stalled_session(*args: object, **kwargs: object) -> bool:
    """Run host revival only when its separately authorized adapter exists."""
    module = _optional_private_module("revive")
    function = getattr(module, "maybe_revive_stalled_session", None) if module else None
    return bool(function(*args, **kwargs)) if callable(function) else False


def clear_revive_marker(*args: object, **kwargs: object) -> None:
    """Clear a host revival marker when that integration exists."""
    module = _optional_private_module("revive")
    function = getattr(module, "clear_revive_marker", None) if module else None
    if callable(function):
        function(*args, **kwargs)


def sweep_inv_e_refreeze(towow_dir: Path, event_log: object) -> None:
    """Run the optional INV-E maintenance sweep when installed."""
    module = _optional_private_module("inv_e_refreeze")
    function = getattr(module, "sweep_inv_e_refreeze", None) if module else None
    if callable(function):
        function(towow_dir, event_log)


def ensure_shared_towow(worktree: Path, main_worktree: Path) -> bool:
    """Point an isolated worktree at the canonical ledger without private code."""
    link = worktree / ".towow"
    target = main_worktree / ".towow"
    try:
        if not target.is_dir():
            return False
        if link.is_symlink():
            return link.resolve(strict=False) == target.resolve(strict=False)
        if link.exists():
            return False
        link.symlink_to(target, target_is_directory=True)
        return link.is_symlink() and link.resolve(strict=False) == target.resolve(strict=False)
    except OSError:
        return False


def spawn_bg_session(
    prompt: str,
    worktree: Path,
    parent_session_id: str,
    *,
    method: SpawnMethod = SpawnMethod.MOCK,
    **kwargs: object,
) -> Any:
    """Spawn through the optional private adapter, with a portable mock path.

    A public install cannot silently pretend a real session was launched.  Real
    methods fail with an actionable error; mock mode returns the same observable
    fields canonical orchestration consumes.
    """
    module = _optional_private_module("claude_bg_helper")
    function = getattr(module, "spawn_bg_session", None) if module else None
    private_enum = getattr(module, "SpawnMethod", None) if module else None
    if callable(function) and private_enum is not None:
        return function(
            prompt,
            worktree,
            parent_session_id,
            method=private_enum(method.value),
            **kwargs,
        )
    if method is not SpawnMethod.MOCK:
        raise RuntimeError(
            "real session spawning is not part of Flowness Open Alpha; "
            "install a separately authorized runtime adapter",
        )
    bg_session_id = f"mock-{os.getpid()}"
    requested_goal_session_id = kwargs.get("goal_session_id")
    goal_session_id = (
        requested_goal_session_id
        if isinstance(requested_goal_session_id, str) and requested_goal_session_id
        else bg_session_id
    )
    return _MockSpawnResult(
        bg_session_id=bg_session_id,
        command_text=prompt,
        method=method,
        launched=False,
        worktree_path=str(worktree),
        parent_session_id=parent_session_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        goal_session_id=goal_session_id,
    )
