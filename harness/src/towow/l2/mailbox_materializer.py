"""T-PCH-A3 daemon mailbox materializer — daemon 侧每轮把 escalation_lifecycle 投影当前未解决集
原子覆盖写 <v3_repo_root>/.towow/main_inbound/mailbox.json, 供 UserPromptSubmit hook 毫秒级只读端菜。

# spec source:
#   @concept:hook-lightweight-inbox-injection@v1#mailbox_artifact_format (schema/写方/并发语义, 冻结)
#   @concept:hook-lightweight-inbox-injection@v1#invariant-mailbox-same-source-daemon-materialized
#   @concept:daemon-patrol-cost-separation@v1 (物化=查投影不重读账本, 成本分离原则)
#   @concept:escalation-inbox-unification@v1 (同源上游: escalation_lifecycle 投影权威定义)

【同源不变量】mailbox.entries 的成员集 (escalation_id) 严格 ⊆ escalation_lifecycle 投影当前未解决集 ——
由 escalation_lifecycle reducer (towow.l0.projection.projection) 权威决定, 本模块只 catchup 到 head
再读 (cli/main.py _read_escalation_lifecycle 同款只读 catchup 范式), 绝不旁路重算。投影文件缺失 = 尚无
escalation (合法空态); 投影文件存在但损坏 (无法 json.loads) 会在 ProjectionStore.read 内原样抛出 —— 本
模块不捕获、不静默降级为空, 这就是"缺失/损坏 → 上报, 不 fallback 旁路重算"的落地。

【已知 spec-conflict, 已登记债】entries 需要的 owner_question / session_id 两个 owner-facing 展示字段
不在 EscalationLifecycleEntry (extra=forbid 严格 schema) 内 —— 该投影当前只承载生命周期状态元数据。
本模块从各自的原始 raising 事件 (World-B: GoalEscalationRaised, escalation_id 即其 event_id, O(1)
get_event 直查; World-A: EscalationRaised, escalation_id 是独立 UUID, 经 get_events_by_type 索引查找,
有界于该事件类型历史总数, 非全账本扫描) 补齐这两个展示字段, 不影响"成员集同源于投影"的机器判据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.event_log.event_log import EventLog
from towow.l0.projection.projection import ProjectionStore
from towow.schemas.enums import EscalationLifecycleState, EventType

if TYPE_CHECKING:
    from pathlib import Path

_MAILBOX_SCHEMA_VERSION = "1"
_MAILBOX_SUBDIR = "main_inbound"
_MAILBOX_FILENAME = "mailbox.json"

# escalation_lifecycle 投影当前真实实现的终态集 (RESOLVED / POST_RESOLVED_LEARNING_CAPTURED) ——
# 未解决集 = lifecycle_state 不在此集合内的节点。注: escalation-inbox-unification@v1 概念文正另描述
# 一套含 resolved_externally 的 5 态机, 但那是尚未接线的目标态机 (仓库内无生产者/无 CLI 写回原语),
# 当前 EscalationLifecycleState 枚举只有 4 值 —— 本模块对齐【当前真实实现】, 差异已作为 uncertainty
# 交回复查。
_TERMINAL_ESCALATION_STATES = frozenset(
    {
        EscalationLifecycleState.RESOLVED.value,
        EscalationLifecycleState.POST_RESOLVED_LEARNING_CAPTURED.value,
    },
)

# World-A (native fix/execution 链) escalation 的 escalation_kind 占位 (与 projection.py
# _ESCALATION_KIND_PLACEHOLDER 同值) —— 借此区分 World-A / World-B 走不同的展示字段补齐路径。
_WORLD_A_KIND_PLACEHOLDER = "unspecified"

# rendered_text 写侧有界化 (f-a3-rendered-text-unbounded-owner-question):
# owner_question 是任意能 emit escalation 的会话 (含跑偏/被注入 bg agent) 的自由文本, 经 hook
# 逐字进 additionalContext、每 prompt 注入一次。写侧不设界 → N 条 pending × 超长文本使渲染无界膨胀,
# 破 hook-zero-ledger-compute『只读小 mailbox.json + ≤1s』前提上游。cost-separation 决定 hook 零计算
# 无法自防, 边界只能落 daemon 写侧。注: 上界只作用于渲染文本 rendered_text —— entries[] 结构化字段
# 保持完整 (provenance/调试, 见 MailboxEntry docstring), 绝不在此截断。
_MAX_RENDERED_ENTRIES = 20  # 逐条列出的上限, 超出折叠为 "另有 N 条"
_MAX_OWNER_QUESTION_RENDER_CHARS = 512  # 单条 owner_question 渲染截断上限
_MAX_WAITING_SINCE_RENDER_CHARS = 64  # waiting_since_iso 也源自事件字段, 一并有界


@dataclass(frozen=True)
class MailboxEntry:
    """mailbox.json entries[] 单条 (冻结 5 字段, hook 注入不解析它, 只供 provenance/调试)。"""

    escalation_id: str
    session_id: str
    owner_question: str
    severity: str  # "blocking" | "fyi"
    waiting_since_iso: str

    def to_dict(self) -> dict[str, object]:
        return {
            "escalation_id": self.escalation_id,
            "session_id": self.session_id,
            "owner_question": self.owner_question,
            "severity": self.severity,
            "waiting_since_iso": self.waiting_since_iso,
        }


@dataclass(frozen=True)
class MailboxMaterializeResult:
    """一轮物化的结果 (= mailbox.json 落盘内容, 冻结 7 字段)。"""

    schema_version: str
    materialized_at: str
    materialized_by: str
    source_snapshot_ref: str
    pending_count: int
    rendered_text: str
    entries: tuple[MailboxEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "materialized_at": self.materialized_at,
            "materialized_by": self.materialized_by,
            "source_snapshot_ref": self.source_snapshot_ref,
            "pending_count": self.pending_count,
            "rendered_text": self.rendered_text,
            "entries": [e.to_dict() for e in self.entries],
        }


def _mailbox_dir(towow_dir: Path) -> Path:
    return towow_dir / _MAILBOX_SUBDIR


def mailbox_path(towow_dir: Path) -> Path:
    """mailbox.json 的落盘路径 (repo-relative 固定单文件, 与 main_inbound/watermark.json 同目录)。"""
    return _mailbox_dir(towow_dir) / _MAILBOX_FILENAME


def _resolve_world_b_fields(event_log: EventLog, escalation_id: str) -> tuple[str, str, str]:
    """World-B (goal/dead-letter/owner-gate 链) escalation 的展示字段。

    escalation_id 即物化时用的 GoalEscalationRaised.event_id (projection.py
    _materialize_world_b_escalation_raised 的键约定) —— O(1) get_event 直查, 非账本扫描。

    生产者 (`towow goal escalation-raise`) 发的是原生 GOAL_ESCALATION_RAISED 事件 (字段嵌
    payload.after_state); orchestrator.py 既有 _unwrap_stub_rewrap 是"NodeTouched stub-rewrap
    形态 (字段嵌 stub_original_payload, 或再嵌一层 after_state) vs 原生形态"两态解包的唯一真源 ——
    复用它, 不重复造一遍判别逻辑 (与 _route_event 同一解包入口, 保证两处对同一条事件读出一致字段)。
    """
    from towow.l2.orchestrator import _unwrap_stub_rewrap

    rec = event_log.get_event(escalation_id)
    if rec is None:
        return "", "(escalation detail unavailable)", "blocking"
    _effective_type, effective_payload = _unwrap_stub_rewrap(rec)
    after = effective_payload.get("after_state")
    after = after if isinstance(after, dict) else effective_payload
    session_id = after.get("goal_session_id") or ""
    owner_question = after.get("owner_question") or "(owner_question 缺失)"
    blocking_scope = after.get("blocking_scope") or "global"
    severity = "blocking" if blocking_scope == "global" else "fyi"
    return str(session_id), str(owner_question), severity


def _resolve_world_a_fields(event_log: EventLog, escalation_id: str) -> tuple[str, str, str]:
    """World-A (native fix/execution 链) escalation 的展示字段。

    escalation_id 是独立 UUID (非 event_id), 经 get_events_by_type(ESCALATION_RAISED) 类型索引查找
    (有界于该事件类型历史总数, 非全账本扫描) —— 与 m23_polling.scan_escalation_lifecycle 同款查法。
    """
    for rec in event_log.get_events_by_type(EventType.ESCALATION_RAISED):
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        if after.get("escalation_id") != escalation_id:
            continue
        session_id = after.get("task_id") or after.get("fix_id") or after.get("finding_id") or ""
        owner_question = (
            after.get("decision_needed_from_nature")
            or after.get("nature_facing_summary")
            or "(escalation detail unavailable)"
        )
        return str(session_id), str(owner_question), "blocking"
    return "", "(escalation detail unavailable)", "blocking"


def _sanitize_render_line(text: str, *, max_chars: int) -> str:
    """把来自事件的自由文本压成安全的单行渲染片段 (只作用于 rendered_text, 不改 entries[])。

    - C0 控制字符 (含换行/回车/制表) + DEL → 空格: 折叠成单行, 使注入文本无法自成一行伪造
      system-reminder / 破坏 "⚠ 有 N 件…" 行结构 (finding 点名的『基本控制字符/伪 reminder 前缀清洗』)。
    - 去首尾空白后按 max_chars 截断, 超出加省略号 —— 给单条渲染片段可预期上界。
    """
    cleaned = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in text)
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"
    return cleaned


def _render_owner_text(entries: list[MailboxEntry]) -> str:
    """daemon 预渲染 owner 语言文本 —— hook 逐字注入, 零渲染 ('hook-zero-ledger-compute' 落地机制)。
    空 entries → 空串 (hook 静默的信号)。

    rendered_text 写侧有界 (f-a3-rendered-text-unbounded-owner-question): 单条 owner_question /
    waiting_since 经 _sanitize_render_line 去控制字符+截断, 逐条至多 _MAX_RENDERED_ENTRIES 条,
    超出折叠为 "另有 N 条" —— 无论输入多长/多少条, rendered_text 有可预期上界。上界只落在渲染文本;
    entries[] 结构化字段保持完整 (不在此截断)。"""
    if not entries:
        return ""
    lines = [f"⚠ 有 {len(entries)} 件在等你拍板:"]
    for e in entries[:_MAX_RENDERED_ENTRIES]:
        marker = "🔴" if e.severity == "blocking" else "·"
        question = _sanitize_render_line(e.owner_question, max_chars=_MAX_OWNER_QUESTION_RENDER_CHARS)
        waiting = _sanitize_render_line(e.waiting_since_iso, max_chars=_MAX_WAITING_SINCE_RENDER_CHARS)
        lines.append(f"  {marker} {question} (等待自 {waiting})")
    overflow = len(entries) - _MAX_RENDERED_ENTRIES
    if overflow > 0:
        lines.append(f"  … 另有 {overflow} 条待处理 (完整明细见 mailbox.json entries)")
    return "\n".join(lines)


def _write_mailbox_atomic(towow_dir: Path, result: MailboxMaterializeResult) -> None:
    """tmp + os.replace 原子覆盖写, 同 main_inbound_poller.save_inbound_watermark_atomic 既有范式。
    单写者 (daemon launchd 单实例) + 多读者纯读 + 原子 rename = 无 torn read, 无需锁。"""
    d = _mailbox_dir(towow_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = mailbox_path(towow_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    tmp.replace(path)


def materialize_mailbox(
    towow_dir: Path,
    *,
    materialized_by: str,
    now: datetime | None = None,
) -> MailboxMaterializeResult:
    """每轮以 escalation_lifecycle 投影当前未解决集原子覆盖写 mailbox.json (非 append)。

    Args:
        towow_dir: .towow/ 路径
        materialized_by: 本轮 daemon run/event id (provenance, mailbox.materialized_by 字段)
        now: 测试可注入固定时刻; 省略 = datetime.now(UTC)

    Returns:
        本轮物化结果 (已原子落盘)。

    🔴 escalation_lifecycle 投影损坏 (json 解析失败) 时 ProjectionStore.read 原样抛出 —— 本函数不捕获
       不 fallback 旁路重算 (a3-c2 硬约束); 投影文件缺失 (从未有过 escalation) 是合法空态, 非损坏。
    """
    event_log = EventLog(towow_dir / "events.log")
    store = ProjectionStore(towow_dir / "graph")
    store.catchup(event_log)
    raw = store.read("escalation_lifecycle")
    escs_raw = raw.get("escalations", []) if isinstance(raw, dict) else []
    escs = escs_raw if isinstance(escs_raw, list) else []
    nodes = [e for e in escs if isinstance(e, dict)]

    entries: list[MailboxEntry] = []
    for node in nodes:
        if node.get("lifecycle_state") in _TERMINAL_ESCALATION_STATES:
            continue
        escalation_id = node.get("escalation_id")
        if not isinstance(escalation_id, str) or not escalation_id:
            continue
        escalation_kind = node.get("escalation_kind", _WORLD_A_KIND_PLACEHOLDER)
        if escalation_kind != _WORLD_A_KIND_PLACEHOLDER:
            session_id, owner_question, severity = _resolve_world_b_fields(event_log, escalation_id)
        else:
            session_id, owner_question, severity = _resolve_world_a_fields(event_log, escalation_id)
        waiting_since = node.get("raised_at") or ""
        entries.append(
            MailboxEntry(
                escalation_id=escalation_id,
                session_id=session_id,
                owner_question=owner_question,
                severity=severity,
                waiting_since_iso=str(waiting_since),
            ),
        )

    materialized_at = (now or datetime.now(UTC)).isoformat()
    result = MailboxMaterializeResult(
        schema_version=_MAILBOX_SCHEMA_VERSION,
        materialized_at=materialized_at,
        materialized_by=materialized_by,
        source_snapshot_ref=str(store.cursor),
        pending_count=len(entries),
        rendered_text=_render_owner_text(entries),
        entries=tuple(entries),
    )
    _write_mailbox_atomic(towow_dir, result)
    return result


__all__ = [
    "MailboxEntry",
    "MailboxMaterializeResult",
    "mailbox_path",
    "materialize_mailbox",
]
