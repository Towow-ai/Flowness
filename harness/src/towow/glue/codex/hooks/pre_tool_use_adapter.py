#!/usr/bin/env python3
"""把 Codex hook 事件翻译给 Harness 共享 PreToolUse 守卫。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_MOVE_PATH = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_shared_guard(event: dict[str, Any]) -> int:
    guard = _repo_root() / ".claude" / "hooks" / "PreToolUse-guard.sh"
    if not guard.is_file():
        print(f"Harness V3 共享守卫不存在: {guard}", file=sys.stderr)
        return 2
    result = subprocess.run(  # noqa: S603 - repo-owned fixed executable
        [str(guard)],
        input=json.dumps(event, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def _patch_paths(command: str) -> list[str]:
    paths = [*_PATCH_PATH.findall(command), *_MOVE_PATH.findall(command)]
    return list(dict.fromkeys(path.strip() for path in paths if path.strip()))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("Harness V3 守卫无法解析 Codex hook 事件，fail-closed。", file=sys.stderr)
        return 2
    if not isinstance(event, dict):
        return 2
    if event.get("tool_name") != "apply_patch":
        return _run_shared_guard(event)

    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        print("Harness V3 守卫未收到 apply_patch command，fail-closed。", file=sys.stderr)
        return 2
    paths = _patch_paths(command)
    if not paths:
        print("Harness V3 守卫无法从 apply_patch 提取目标路径，fail-closed。", file=sys.stderr)
        return 2

    for path in paths:
        translated = dict(event)
        translated["tool_name"] = "Edit"
        translated["tool_input"] = {
            **tool_input,
            "file_path": path,
            "new_string": command,
        }
        returncode = _run_shared_guard(translated)
        if returncode != 0:
            return returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
