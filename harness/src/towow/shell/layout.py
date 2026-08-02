"""Physical .towow/ directory layout (M-3.1 §3 minimal viable set) + skill deployment.

# spec source: 06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md
#   §3 物理目录
#   §8.1 skill 物理部署 (.claude/skills/)
"""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

from towow.l0.fs_guard import safe_rmtree

# E.1 minimal viable subdirectories per Plan §2 + M-3.1 §3
_TOWOW_SUBDIRS = (
    "events",
    "events/hot",
    "events/cold/staging",
    "events/cold/archive",
    "events/cursors",
    "events/index",
    "graph",
    "obligation-system",
    "worktrees",
    "locks",
)


def create_skeleton(project_dir: Path) -> Path:
    """Create the .towow/ skeleton + deploy bundled L1/M-2.2 skills.

    Returns path to the .towow/ directory.
    """
    towow = project_dir / ".towow"
    towow.mkdir(parents=True, exist_ok=True)
    for subdir in _TOWOW_SUBDIRS:
        (towow / subdir).mkdir(parents=True, exist_ok=True)
    # Touch lock files so fcntl operations work later
    (towow / "locks" / "commit.lock").touch()
    (towow / "locks" / "scan.lock").touch()
    # config.yaml (E.1 minimal)
    config_path = towow / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            "lock_timeout_seconds: 1800\nschema_version: '1.0.0'\n",
            encoding="utf-8",
        )
    # M-3.1 §8.1 — deploy bundled skill manifests + knowledge packs into .claude/skills/
    deploy_skills(project_dir)
    return towow


def deploy_skills(project_dir: Path) -> Path:
    """M-3.1 §8.1: copy bundled SKILL.md + knowledge/ into .claude/skills/.

    Source: src/towow/skills/<skill_id>/ in the harness Python package.
    Destination: project_dir/.claude/skills/<skill_id>/.

    Returns destination directory.
    """
    target_root = project_dir / ".claude" / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    # Locate bundled skills directory via importlib.resources for installed-mode safety
    try:
        skills_root = Path(str(files("towow") / "skills"))
    except Exception:  # fallback to source tree path for editable install
        skills_root = Path(__file__).resolve().parent.parent / "skills"
    if not skills_root.exists():
        return target_root
    for skill_dir in skills_root.iterdir():
        if not skill_dir.is_dir():
            continue
        dst = target_root / skill_dir.name
        if dst.exists():
            # Idempotent — overwrite to ensure fresh
            # T-FIX-INC-01: 部署覆盖也走护栏删除 (.claude/skills/<id> 命中 skills 白名单段)
            safe_rmtree(dst)
        shutil.copytree(skill_dir, dst)
    return target_root
