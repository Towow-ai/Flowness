from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .inventory import build_inventory
from .models import utc_now
from .registry import ValidationError, atomic_write_json


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValidationError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def seal_repository_snapshot(repo: Path, output: Path) -> dict[str, Any]:
    repo = repo.resolve()
    output = output.resolve()
    if output == repo or repo in output.parents:
        raise ValidationError("snapshot output must be outside the repository")
    if not repo.is_dir():
        raise ValidationError(f"repository is not a directory: {repo}")
    commit_sha = _git(repo, "rev-parse", "HEAD")
    tree_sha = _git(repo, "rev-parse", "HEAD^{tree}")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    inventory = build_inventory(repo)
    if (
        commit_sha != _git(repo, "rev-parse", "HEAD")
        or tree_sha != _git(repo, "rev-parse", "HEAD^{tree}")
        or status
        != _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise ValidationError("repository changed while snapshot was being captured")
    identity = {
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "dirty": bool(status),
        "dirty_status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        "repository_content_hash": inventory["repository_content_hash"],
    }
    snapshot_id = "sha256:" + hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()
    payload = {
        "schema_version": "evidence-snapshot/v1",
        "snapshot_id": snapshot_id,
        "repository": str(repo),
        **identity,
        "captured_at": utc_now(),
        "dirty_paths": status.splitlines(),
        "inventory": inventory,
        # A clean Git identity is enough to assemble a *candidate*.  It is not
        # evidence of export rights, clean-room installability, runtime use,
        # jury passage, or owner approval, so it must never be labelled as a
        # release qualification.
        "candidate_assembly_eligible": not bool(status),
        "release_eligible": False,
        "boundary": (
            "A dirty snapshot is valid for research but cannot seed candidate "
            "assembly. A clean snapshot only establishes local source identity; "
            "it is not release eligibility. Runtime reachability, export rights, "
            "clean-room installation, jury passage, and owner approval require "
            "separate evidence."
        ),
    }
    atomic_write_json(output, payload)
    return payload
