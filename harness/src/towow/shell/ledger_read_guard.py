"""账本全量读静态护栏 · 内容级判定层 + emit 单一真源 (concept ledger-read-static-guardrail@v1).

PreToolUse-guard.sh (bash 路由层) 检测到一次 write-class 工具调用的 new_string/content 里
**可能**引入账本全量读调用 (cheap bash 预筛命中 `all_records` 字样) 时, 薄路由调本模块 —— 本模块
是【单一真源】判定层, 不在 bash 里复刻豁免正则或判定逻辑 (镜像 T-RMD-S4-PHYS-MATCHER 的
physical_gate_check 薄路由先例)。

为什么单独成模块、不进 cli/main.py:
- hook 在每次工具调用前同步跑, 入口要轻 (只 import schemas + EventLog on-violation, 不拉整 CLI)。
- cli/main.py 是并发多会话高频共改的热文件, 把 hook 入口放它里 = 在共享树上跟邻居抢同一文件。

单一真源契约 (nj-8cfdb4f6):
- 层1 (本 hook 入口) 与层2 (守护测试 T-LRG-B1-guardtest) 共享**同一条豁免正则** ``EXEMPTION_MARKER_RE``
  与检测正则 ``ALL_RECORDS_CALL_RE`` —— 两层导入本模块的同一常量, 逐字节一致由"只有一份定义"自动保证
  (比在 bash 里再抄一份更强, 不会漂移)。

v1 静态拦截范围 (owner nj-9367a728): 只拦公开符号 all_records() 的新增未豁免调用。无界
get_events_in_range 有边界参数、静态判不出无界用法, **不进** v1 静态拦截, 留给运行时探针。

行为 (fail-closed):
- 从 hook event 的 tool_input 抽出本次**新增**文本 (Write→content / Edit→new_string /
  MultiEdit→每个 edit 的 new_string / NotebookEdit→new_source)。
- 逐行找账本全量读调用点; 同一行带合法豁免标记 (EXEMPTION_MARKER_RE 命中) → 视为已豁免、放行;
  不带标记 → 未豁免命中。
- 有未豁免命中 → 沿 V-01 先例产一条 canonical OwnerGuardViolation(reason=CONCEPT_ID) + 打印含三样
  (概念 id / 替代 API / 豁免语法全文) 的拒绝消息到 stderr + exit 2。
- 无命中 → exit 0。
- 判不了 (project-dir 定位不到 .towow / emit 崩) 但**有未豁免命中** → 仍打印消息 + exit 2 (block 优先,
  emit 是 best-effort 审计, 判不了从严宁拦, 绝不 fail-open 放过坏读法)。

退出码契约 (hook 据此放行/拦截): 0 = 放行, 2 = 物理拒。其余非 0 也被 hook 当拒 (fail-closed)。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog

# 本护栏拦的概念 id —— 同时是 emit 事件的 reason (done_criterion 要求精确等于它)。
CONCEPT_ID = "ledger-read-static-guardrail@v1"

# 检测正则: 账本全量读调用点 `.all_records(` (公开符号 all_records())。
# 拆写两段相邻字面拼接, 使本源码任何单行都不含完整调用点字样 —— 守护测试扫全 src/ 断言"未豁免命中=0"
# 时不会把本判定器自身误判成一处违规 (self-reference 陷阱防护)。
ALL_RECORDS_CALL_RE = re.compile(r"\.all" r"_records\s*\(")

# 豁免标记正则 (concept ledger-read-static-guardrail@v1 逐字, 单一真源):
#   `.all_records()  # ledger-scan-ok: <reason> | transcript=<session_id> | approved=<approval_event_id>`
# 三字段必填: reason 非空自然语言; transcript 可追溯会话/transcript 标识; approved 指向审批落账事件 id。
# 层1 hook 与层2 守护测试用**同一条**此正则解析、判定逐字节一致 (nj-8cfdb4f6)。
EXEMPTION_MARKER_RE = re.compile(
    r"#\s*ledger-scan-ok:\s*(?P<reason>.+?)\s*\|\s*transcript=(?P<anchor>\S+)\s*\|\s*approved=(?P<approval>\S+)",
)

# 拒绝消息内嵌三样让不知情 agent 顺消息即改对 (nj-48617de7 陌生 agent 验证):
#   ① 概念 id  ② 替代 API 清单  ③ 豁免语法全文。缺任一 = 拒绝消息不合格。
_ALT_APIS = (
    "committed_index().records() / lookup_type / lookup_event_id / lookup_task / "
    "有界 get_events_in_range (9 Read APIs, 均走 warm 增量索引)"
)
_EXEMPTION_SYNTAX = (
    "# ledger-scan-ok: <reason 非空理由> | transcript=<会话/transcript 标识> "
    "| approved=<审批落账事件 id>"
)


def _added_text_from_tool_input(tool_name: str, tool_input: dict[str, object]) -> str:
    """从 hook event 的 tool_input 抽出本次工具**新增**的文本 (只看新增, 不看被删/既有)。"""
    parts: list[str] = []
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            parts.append(content)
    elif tool_name == "Edit":
        new_string = tool_input.get("new_string")
        if isinstance(new_string, str):
            parts.append(new_string)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    ns = edit.get("new_string")
                    if isinstance(ns, str):
                        parts.append(ns)
    elif tool_name == "NotebookEdit":
        new_source = tool_input.get("new_source")
        if isinstance(new_source, str):
            parts.append(new_source)
    return "\n".join(parts)


def find_unexempted_all_records(text: str) -> list[str]:
    """返回 text 中**未豁免**的账本全量读调用点所在行 (去首尾空白)。

    判据: 该行含账本全量读调用点 (ALL_RECORDS_CALL_RE) 且该行未命中豁免标记 (EXEMPTION_MARKER_RE)。
    层1 hook 只判"豁免标记在场且格式合法"; approved/transcript 锚的**对账**验真 (锚可解析、审批 actor
    ≠ 作者) 属层2 守护测试范围, 不在本 hook 入口做 (职责分层, 与 concept 一致)。
    """
    offending: list[str] = []
    for line in text.splitlines():
        if ALL_RECORDS_CALL_RE.search(line) and not EXEMPTION_MARKER_RE.search(line):
            offending.append(line.strip())
    return offending


def rejection_message(offending_lines: list[str]) -> str:
    """三段式拒绝消息 (概念 id / 替代 API / 豁免语法全文)。"""
    shown = "\n".join(f"  {ln}" for ln in offending_lines[:5])
    more = "" if len(offending_lines) <= 5 else f"\n  (+{len(offending_lines) - 5} 处)"
    return (
        f"✗ PreToolUse Guard 拒绝 (账本全量读静态护栏, concept {CONCEPT_ID}):\n\n"
        f"检测到新增一处**无豁免**的账本全量读调用点 (公开符号 all_records()):\n"
        f"{shown}{more}\n\n"
        "账本单调增长后全量读逐个引爆 (CPU 满 / 分钟级卡死), 交互/daemon 路径的账本读取必须 "
        "O(增量)/O(索引)。\n"
        f"· 改用增量·索引读 API: {_ALT_APIS}。\n"
        "· 确属声明+审批的离线维护场景? 在调用点行尾加豁免标记 (审批经 owner-confirm/escalation 通道):\n"
        f"    {_EXEMPTION_SYNTAX}\n"
    )


def _resolve_towow(project_dir: Path) -> Path | None:
    """定位 project_dir (或其 harness/ 子目录布局) 下的 canonical `.towow` (镜像 physical_gate_check)。"""
    for candidate in (project_dir / ".towow", project_dir / "harness" / ".towow"):
        if candidate.is_dir():
            return candidate
    return None


def emit_ledger_guard_violation(
    event_log: EventLog,
    *,
    file_path: str,
    attempted_by_actor_id: str,
    offending_lines: list[str],
) -> None:
    """沿 V-01 先例 (shell.worktree._emit_owner_guard_violation) 产一条 canonical OwnerGuardViolation。

    reason 精确 = CONCEPT_ID (ledger-read-static-guardrail@v1), 使 goal 收口门 obs-1
    (obs-ledger-guard-live-denial) 可复算命中。task_id 用 CONCEPT_ID 作稳定标识 (本护栏非按 task
    write_set 触发, 复用 OwnerGuardViolation 事件类型 + reason 作判别, 与 task package 指定一致)。
    """
    from towow.schemas.enums import (
        ActorType,
        BaseClassification,
        EventCategory,
        EventType,
        SubjectEntityType,
        SubjectRole,
    )
    from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

    snippet = offending_lines[0] if offending_lines else "<unknown>"
    intent = EventIntent(
        local_intent_id=f"lrg-{abs(hash((file_path, snippet, attempted_by_actor_id))) % (10**10)}",
        event_type=EventType.OWNER_GUARD_VIOLATION,
        event_category=EventCategory.COMMIT,
        payload={
            "task_id": CONCEPT_ID,
            "file_path": file_path,
            "attempted_by_actor_id": attempted_by_actor_id,
            "owner_actor_id": None,
            "reason": CONCEPT_ID,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id="ledger_read_guard",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=CONCEPT_ID, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    event_log.write_direct(intent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="towow.shell.ledger_read_guard",
        description="账本全量读静态护栏内容级判定入口 (PreToolUse hook 薄路由目标); exit 0=放行 / 2=物理拒。",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="项目根 (定位 .towow 真账本, emit OwnerGuardViolation 用; 支持 <root> 或 <root>/harness 布局)。",
    )
    parser.add_argument(
        "--event-json",
        default=None,
        help="hook event JSON (调试用; 缺省从 stdin 读)。",
    )
    args = parser.parse_args(argv)

    raw = args.event_json if args.event_json is not None else sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # 解析不出 event → 无从判内容 → 放行 (与 bash 层 jq 缺失的诚实边界一致: 探不到就不假装拦)。
        return 0

    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    added_text = _added_text_from_tool_input(tool_name, tool_input)
    offending = find_unexempted_all_records(added_text)
    if not offending:
        return 0  # 无未豁免命中 → 放行 (fast path; 未构造 EventLog)

    file_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "<unknown>")
    attempted_by = str(event.get("session_id") or "unknown-session")

    # 有未豁免命中 → best-effort emit canonical OwnerGuardViolation, 无论 emit 成败都 block。
    towow = _resolve_towow(args.project_dir.resolve())
    if towow is not None:
        try:
            from towow.l0.event_log.event_log import EventLog

            emit_ledger_guard_violation(
                EventLog(towow / "events.log"),
                file_path=file_path,
                attempted_by_actor_id=attempted_by,
                offending_lines=offending,
            )
        except Exception as exc:  # emit 崩不阻止 block (审计 best-effort, 安全优先; BLE001 全局 ignore)
            print(f"⚠ ledger-read-guard: OwnerGuardViolation emit 失败 ({type(exc).__name__}: {exc})", file=sys.stderr)
    else:
        print(
            f"⚠ ledger-read-guard: 定位不到 .towow 账本 (project_dir={args.project_dir}), "
            "OwnerGuardViolation 未 emit — 仍 fail-closed 拒。",
            file=sys.stderr,
        )

    print(rejection_message(offending), file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover — 进程入口, 由 PreToolUse hook 薄路由调
    sys.exit(main())
