#!/usr/bin/env python3
"""T-PCH-A2: UserPromptSubmit hook — 毫秒级只读端菜 (读 daemon 预物化的信箱).

# spec source:
#   concept hook-lightweight-inbox-injection@v1 (frozen, 本改造的合约本体)
#   src/towow/l2/main_inbound_poller.py (watermark 语义, 本 hook 不再消费)
#   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch F-11-trigger-contract-1 (历史背景)
#
# Trigger:
#   Claude Code 每次 owner 发 prompt 给 main session 时调用 UserPromptSubmit hooks.
#   hook stdin 收 JSON event (含 prompt / session_id / cwd / etc).
#
# 行为 (T-PCH-A2 改造后 — 只端菜, 不做饭):
#   1. 找到 v3-managed repo (cwd 或 ancestor 含 .towow/)
#   2. json.load 单个文件 <repo>/.towow/main_inbound/mailbox.json
#      (daemon 巡检预物化, 内容同源 escalation_lifecycle 投影, 本 hook 不计算)
#   3. rendered_text 字段非空 → 逐字放进 additionalContext 注入 (hook 不渲染不格式化)
#   4. 文件缺失/读失败/rendered_text 空或缺 → silent exit 0 (避免阻塞 prompt)
#
# 不变量 hook-zero-ledger-compute (冻结, 只作用于本 hook):
#   端到端 ≤1s; 禁止 (a) 构造/读取账本索引对象 (hot/cold 分段存储) (b) 拉起任何
#   额外进程 (无 uv run / towow CLI / orchestrator step) (c) 任何 O(账本) 计算;
#   只允许读这一个小 mailbox.json + 毫秒级字符串直通.
#
# Injection 协议 (Claude Code 标准, 本次改造未变, 只换内容源):
#   stdout 输出 JSON:
#     {
#       "hookSpecificOutput": {
#         "hookEventName": "UserPromptSubmit",
#         "additionalContext": "...mailbox rendered_text..."
#       }
#     }
#   Claude Code 把 additionalContext 作为 system reminder 注入下一个 model request.
#
# 失败原则:
#   hook 不该阻塞 owner prompt — exit 0 always (即使 mailbox 不存在/坏). 错误 silent.
#
# 非破坏性 (mailbox-same-source-daemon-materialized 的读侧承诺):
#   hook 读是只读 — 永不写、永不清空、永不推进任何 watermark。check_inbound 的
#   consume-once watermark 机制不删, 仍由 CLI `towow inbound check` 拥有并推进,
#   本 hook 只是停止调用它。幂等: 同一 mailbox 内容每次 prompt 都注入, 直到
#   daemon 重新物化。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _find_v3_repo(start: Path) -> Path | None:
    """Walk up from start looking for .towow/ directory; return repo root or None.

    Prefer deeper match (subdir wins over ancestor). Walks UP from start, but
    we also check if `start/harness/.towow` exists first (common layout where
    Claude Code session top-level has subdir 'harness/' as the v3 repo).
    """
    current = start.resolve()
    # First check for harness/ subdir convention
    if (current / "harness" / ".towow").is_dir():
        return current / "harness"
    while True:
        if (current / ".towow").is_dir():
            return current
        if current == current.parent:
            return None
        current = current.parent


def _read_mailbox_rendered_text(repo: Path) -> str | None:
    """读 <repo>/.towow/main_inbound/mailbox.json, 取 rendered_text 逐字返回.

    文件缺失 / 读失败 / 坏 JSON / rendered_text 空或缺 → None (调用方统一 silent
    exit)。只读单个小文件, 不解析 entries、不做任何格式化 — "零计算" 落地机制。
    """
    mailbox_path = repo / ".towow" / "main_inbound" / "mailbox.json"
    try:
        raw = mailbox_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rendered_text = data.get("rendered_text")
    if not isinstance(rendered_text, str) or not rendered_text:
        return None
    return rendered_text


def _silent_exit() -> None:
    """No injection, no error — hook stays out of the way."""
    sys.exit(0)


def main() -> None:
    # Parse stdin event JSON (we don't actually need prompt content, just cwd hint)
    cwd_hint: str = os.getcwd()
    try:
        raw = sys.stdin.read()
        if raw:
            event = json.loads(raw)
            if isinstance(event, dict):
                cwd_in_event = event.get("cwd")
                if isinstance(cwd_in_event, str) and cwd_in_event:
                    cwd_hint = cwd_in_event
    except (json.JSONDecodeError, ValueError):
        pass  # fall through with os.getcwd()

    repo = _find_v3_repo(Path(cwd_hint))
    if repo is None:
        _silent_exit()
        return

    rendered_text = _read_mailbox_rendered_text(repo)
    if rendered_text is None:
        _silent_exit()
        return

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": rendered_text,
        },
    }
    sys.stdout.write(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
