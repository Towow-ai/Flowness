#!/usr/bin/env python3
"""DOGFOOD-001-F deeper fix — Stop hook detect confirmation-loop pattern.

# spec source:
#   docs/DOGFOOD-RUN-001-FINDINGS.md DOGFOOD-001-F continue-confirmation-loop-defect
#   docs/AGENT-DECISION-OWNERSHIP-RUBRIC.md (Class C: "我倾向 X，除非反对继续")
#
# Trigger condition:
#   agent's last assistant turn contains confirmation-loop pattern ("继续吗",
#   "想先看", "或者你想", "还是想", ...) AND we are in a v3-managed repo
#   (.towow/ exists) AND there is plan-approved scope (PlanFreezed event in log).
#
# Action: exit 2 with stderr guidance → Claude Code injects message back into
# agent context → agent must re-output without confirmation-loop pattern.
#
# False positive bound: if agent really needs Class A owner-level decision,
# guidance explicitly says "if真 Class A, explain why owner-only" — agent can
# re-output with proper Class A framing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CONFIRMATION_PATTERNS = [
    "继续吗",
    "想先看",
    "或者你想",
    "还是想",
    "需要你确认",
    "或者你想要先",
    "继续走 M-",
    "or do you want me to",
    "or should I",
]

# DOGFOOD-001-K mid-term fix (RUN-011): detect 列表式 owner 问题 anti-pattern.
# 触发条件: 最近 assistant turn 含 "件 1 / 件 2" 或 "option (a)/(b)/(c)" 类编号列表
# 同时跟 "owner 判断点 / 需要你拍板 / 让你决定" 类 phrasing 出现 → 强烈暗示
# agent 把 class B/C 工程归位类问题伪装成 owner-only decision.
LIST_OWNER_QUESTION_PATTERNS = [
    # 列表序号
    r"件\s*1\b",
    r"件\s*2\b",
    r"件\s*3\b",
    r"option\s*\(a\)",
    r"option\s*\(b\)",
    r"option\s*\(c\)",
    r"选项\s*A\b",
    r"选项\s*B\b",
    r"选项\s*C\b",
]
OWNER_DECISION_PHRASING = [
    "owner 判断点",
    "需要你拍板",
    "需要你决定",
    "让你决定",
    "你拍板",
    "你想.{0,4}哪",
    "你倾向.{0,4}哪",
]


def _first_user_text(transcript_path: str) -> str | None:
    """Return the transcript's first user-message text, or None if unreadable.

    Handles both string content and content-block-list shapes.
    """
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else msg
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            t = c.get("text")
                            if isinstance(t, str):
                                parts.append(t)
                        elif isinstance(c, str):
                            parts.append(c)
                    return "\n".join(parts)
                return str(content)
    except OSError:
        return None
    return None


def is_autonomous_goal_session(event: dict) -> bool:
    """True iff this is an autonomous `claude --bg "/goal ..."` session.

    Why this guard must skip such sessions (RUN-038 9x stop-hook-block-cap fix):
    this Stop guard nudges the INTERACTIVE owner-facing agent to not be lazy
    (confirmation-loops / list-style owner questions) *while the owner is present*.
    An autonomous /goal session has no live owner to be lazy toward — surfacing the
    plan's owner-gated decisions and stopping at wave boundaries is the CORRECT,
    required behavior. Blocking those (exit 2 → re-prompt) is exactly what made the
    堆② goal spin 9x and get force-stopped. So: skip the guard for /goal sessions.

    Signal: Claude Code's Stop-hook stdin has NO bg/headless flag (confirmed via
    claude-code-guide); the reliable signal is the transcript's first user message —
    autonomous sessions are launched as `claude --bg "/goal ..."`, so it starts with
    "/goal". NOT "/work": /work is the owner-facing entry where the guard SHOULD apply.
    """
    text = _first_user_text(event.get("transcript_path", ""))
    if text is None:
        return False  # can't determine → treat as interactive (conservative: keep guard)
    return text.lstrip().startswith("/goal")


def main() -> None:
    # Parse stdin JSON event
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # Can't parse → no decision

    # Skip if already inside stop_hook loop (防 infinite recursion)
    if event.get("stop_hook_active"):
        sys.exit(0)

    # RUN-038 fix: skip for autonomous /goal sessions — this guard is for the
    # interactive owner-facing session; an autonomous goal legitimately surfaces
    # owner-gated decisions + stops at wave boundaries (blocking those caused the
    # 9x stop-hook-block-cap spin). Interactive (/work, conversation) sessions still
    # get the guard.
    if is_autonomous_goal_session(event):
        sys.exit(0)

    # Need transcript path to scan
    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        sys.exit(0)

    transcript_file = Path(transcript_path)
    if not transcript_file.exists():
        sys.exit(0)

    # Read last assistant text turn
    last_assistant_text: str = ""
    try:
        with transcript_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                message = rec.get("message", {})
                content = message.get("content", []) if isinstance(message, dict) else []
                if not isinstance(content, list):
                    continue
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text_val = c.get("text", "")
                        if isinstance(text_val, str):
                            texts.append(text_val)
                if texts:
                    last_assistant_text = "\n".join(texts)
    except OSError:
        sys.exit(0)

    if not last_assistant_text:
        sys.exit(0)

    # Detect confirmation-loop patterns (DOGFOOD-001-F)
    matches = [p for p in CONFIRMATION_PATTERNS if p in last_assistant_text]

    # Detect list-style owner question patterns (DOGFOOD-001-K mid-term)
    import re
    list_matches = []
    owner_phrase_matches = []
    for p in LIST_OWNER_QUESTION_PATTERNS:
        if re.search(p, last_assistant_text):
            list_matches.append(p)
    for p in OWNER_DECISION_PHRASING:
        if re.search(p, last_assistant_text):
            owner_phrase_matches.append(p)

    # Trigger K detection only when list pattern + owner phrasing both present.
    # 这样减少 false positive (agent legit 列 finding 表不算 owner question).
    k_violation = len(list_matches) >= 2 and len(owner_phrase_matches) >= 1

    if not matches and not k_violation:
        sys.exit(0)

    # Verify we're in v3-managed repo with plan-approved scope.
    # v3 root 可能在 cwd 自身或 cwd/harness 子目录（Harness/ 顶层 cwd + harness/
    # 子项目 layout）— hook 上移到 Harness/.claude/ 后必须自动识别两种 layout.
    towow = Path(".towow")
    if not (towow / "events.log").exists() and (Path("harness/.towow") / "events.log").exists():
        towow = Path("harness/.towow")
    events_log = towow / "events.log"
    if not events_log.exists():
        sys.exit(0)  # Not v3 repo

    has_plan_freezed = False
    try:
        with events_log.open("r", encoding="utf-8") as f:
            for line in f:
                if "PlanFreezed" in line or "plan_freezed" in line:
                    has_plan_freezed = True
                    break
    except OSError:
        sys.exit(0)

    if not has_plan_freezed:
        sys.exit(0)  # No plan-approved scope yet

    # Violation — emit guidance + exit 2 (re-prompt agent)
    guidance_parts = []
    if matches:
        matches_str = ", ".join('"' + m + '"' for m in matches)
        guidance_parts.append(
            "[BLOCKED] DOGFOOD-001-F Stop guard: 最近输出含 confirmation-loop 模式（"
            + matches_str + "）。\n\n"
            "按 AGENT-DECISION-OWNERSHIP-RUBRIC class C：在 plan-approved scope 内"
            "（PlanFreezed event 已 in event log），phase 完成应该是\n"
            "「我倾向 X，除非反对继续」然后直接做下一 phase，不应该笼统地问\n"
            "「继续吗 / 想先看 / 或者...」。\n\n"
            "请重新输出。两种合法形式：\n\n"
            "  形式 A - 默认（直接做）：「已完成 X，接下来开始 Y」（不问继续）\n\n"
            "  形式 B - 真有 Class A owner-level decision 才停下来：明确说明\n"
            "    「我需要你拍板：[具体 owner-level decision]。这是 class A 因为\n"
            "     [影响 scope/风险偏好/价值取舍 等具体理由]」\n\n"
            "不要重复 confirmation-loop 模式。\n",
        )
    if k_violation:
        list_str = ", ".join('"' + m + '"' for m in list_matches[:3])
        owner_str = ", ".join('"' + m + '"' for m in owner_phrase_matches[:2])
        guidance_parts.append(
            "[BLOCKED] DOGFOOD-001-K Stop guard: 最近输出含列表式 owner 问题模式\n"
            "(编号列表: " + list_str + " + owner-decision phrasing: " + owner_str + ").\n\n"
            "按 docs/agent-behavior-enforcement-pattern.md + CLAUDE.md \"Surface 前 hard checklist\":\n"
            "看到 \"件 1 / 件 2 / option (a)/(b)/(c)\" + \"owner 判断点\" 这类组合 →\n"
            "停下来逐项 RUBRIC self-classify:\n"
            "- 这一项**真**只有 owner 能决 (产品策略 / 哲学原则 / 风险偏好 / scope)?\n"
            "- 不是 → 该项 collapse 成 \"我做了 X, 理由 Y\" 短汇报, **不进列表**.\n"
            "- 列表里 0 项真 class A → 整个列表删除, 改成一段汇报.\n"
            "- 列表里 1 项真 class A → 只问那一项, 不要把已自决的事项重新拉出来\"让 owner 过目\".\n\n"
            "工程归位类问题 (commit 边界 / trivial fix / 实现细节 / 接入草案 approve)\n"
            "即使你觉得 owner 可能想知道, 也属 class C — 自决做掉, 汇报里一句话带过即可.\n\n"
            "请重新输出: 默认偏向 \"自决 + 短汇报\", 不是 \"列表 + 让她拍板\".\n",
        )
    sys.stderr.write("\n".join(guidance_parts))
    sys.exit(2)


if __name__ == "__main__":
    main()
