"""T-LRF-02 — dead-letter-inbox@v1: 派发不出去/结构性失败对象的一等终点。

# concept source:
#   concept dead-letter-inbox@v1 (evt-324d0c7ceefa461db536ed29eb14b289)
#     state_machine evt-1ed20dbeb09840dbbe75c23e98edd6e2
#   concept terminal-state-reachability@v1 (evt-53fc32d6de2d4d55a43a2ee939a8fff1)
#   plan plan-landing-rootfix / task T-LRF-02-dead-letter-inbox

死信箱是【对象】(finding / task / session 等问题单) 的终点 —— 三类进入源各自钉死、互斥:
  ① unroutable        路由不出 (orchestrator._fallback_finding_route 返回 no-route)
  ② structural_failure 结构性失败 (启动期自检崩 / 同输入必败, 由 dispatch-retry-classification@v1
                        判定 deterministic 后投入 —— 该判定 caller 属 sibling task T-LRF-03,
                        本模块只提供 entry_reason=structural_failure 的入箱 API)
  ③ circuit_tripped    重派熔断 (redispatch_count 达 _redispatch_cap 上限, RedispatchExhausted)

5 态机 (concept state_machine):
  enqueued (initial) → under_triage → {redispatched(terminal) / retired(terminal) /
                                       escalated_to_owner / 回 enqueued(SAGA 补偿)}
  enqueued / escalated_to_owner --超龄 aged_out--> retired(terminal)
  escalated_to_owner --owner 答复经 escalation-answer-reflow 送回--> under_triage

terminal-state-reachability@v1 落点 (本模块兑现 dead_letter_entry 这一类):
  - 静态: 每个中间态 (enqueued/under_triage/escalated_to_owner) 都有 ≥1 条到终态的可执行边
  - 动态: sweep_aged_out() = 巡检发现+行动 (超龄强制终态化, 非只告警) —— 死信箱里不能再卡死
  - 回头客上限: reentry_count 达 _redispatch_cap (默认 3) → 强制改判 retired (deterministic 不可恢复)

持久化沿用 orchestrator marker 范式 (marker = 派生投影/幂等键; 事件 = canonical 审计真相):
  .towow/dead_letter/entries/<entry_id>.json  每条目一份完整状态 (审计/视图, 永久保留)
  .towow/dead_letter/active/<key>             活跃幂等键 → entry_id (仅非终态时存在, 终态时删)

每次状态迁移 emit 一条 NodeTouched(kind=DeadLetter*) lifecycle 事件 (base_classification=
abstractable_process —— 条目生命周期记录, 归档可消化但不可丢弃, 满足 "留痕" 可达终态审计要求)。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog

# ════════════════════════════════════════════════════════════════════════════════
#  Enums —— 钉死的取值集 (concept dead_letter_entry 字段)
# ════════════════════════════════════════════════════════════════════════════════


class DeadLetterState(StrEnum):
    """5 态机状态 (= 条目 triage_status)。terminal = {REDISPATCHED, RETIRED}。"""

    ENQUEUED = "enqueued"
    UNDER_TRIAGE = "under_triage"
    ESCALATED_TO_OWNER = "escalated_to_owner"
    REDISPATCHED = "redispatched"  # terminal
    RETIRED = "retired"  # terminal


_TERMINAL_STATES: frozenset[DeadLetterState] = frozenset(
    {DeadLetterState.REDISPATCHED, DeadLetterState.RETIRED},
)
# 巡检可强制终态化 (aged_out) 的态 —— 严格按 concept state_machine: 只 enqueued (始终未分诊) +
# escalated_to_owner (owner 始终未答复)。under_triage 静态非死胡同 (有 →retired 边), 其 stall 归
# stuck-detection (sibling T-LRF-08), 不在本 sweep 语义内。
_AGEABLE_STATES: frozenset[DeadLetterState] = frozenset(
    {DeadLetterState.ENQUEUED, DeadLetterState.ESCALATED_TO_OWNER},
)


class DeadLetterEntryReason(StrEnum):
    """三类进入源, 互斥 (enum 天然互斥 —— 一条目恰一个进入源)。"""

    UNROUTABLE = "unroutable"
    STRUCTURAL_FAILURE = "structural_failure"
    CIRCUIT_TRIPPED = "circuit_tripped"


class DeadLetterTriageAction(StrEnum):
    """分诊处置三选一。"""

    REDISPATCH = "redispatch"
    RETIRE = "retire"
    ESCALATE = "escalate"


# 退役理由 (RETIRED 终态的子分类 —— 审计可区分手动 / 超龄 / 回头客耗尽 / deterministic)。
class DeadLetterRetireReason(StrEnum):
    MANUAL = "manual"  # 分诊判定不可恢复 (人工退役)
    AGED_OUT = "aged_out"  # 超龄强制终态化 (巡检)
    REENTRY_EXHAUSTED = "reentry_exhausted"  # 回头客计数达上限
    DETERMINISTIC = "deterministic"  # 同输入必败, 不可恢复


# lifecycle event kinds (NodeTouched payload.kind)。
_KIND_ENQUEUED = "DeadLetterEnqueued"
_KIND_TRIAGE_STARTED = "DeadLetterTriageStarted"
_KIND_REDISPATCHED = "DeadLetterRedispatched"
_KIND_RETIRED = "DeadLetterRetired"
_KIND_ESCALATED = "DeadLetterEscalated"
_KIND_REDISPATCH_FAILED = "DeadLetterRedispatchFailed"
_KIND_AGED_OUT = "DeadLetterAgedOut"
_KIND_TRIAGE_RESUMED = "DeadLetterTriageResumed"  # escalated_to_owner → under_triage (reflow 送回)

_DL_ACTOR_TYPE = ActorType.SYSTEM.value
_DL_ACTOR_ID = "dead-letter-inbox"

# ════════════════════════════════════════════════════════════════════════════════
#  Persistence layout
# ════════════════════════════════════════════════════════════════════════════════

_DEAD_LETTER_SUBDIR = "dead_letter"
_ENTRIES_SUBDIR = "entries"
_ACTIVE_SUBDIR = "active"


def _dead_letter_dir(towow_dir: Path) -> Path:
    return towow_dir / _DEAD_LETTER_SUBDIR


def _entries_dir(towow_dir: Path) -> Path:
    return _dead_letter_dir(towow_dir) / _ENTRIES_SUBDIR


def _active_dir(towow_dir: Path) -> Path:
    return _dead_letter_dir(towow_dir) / _ACTIVE_SUBDIR


def ensure_dead_letter_layout(towow_dir: Path) -> None:
    """Create .towow/dead_letter/{entries/, active/} idempotently."""
    _entries_dir(towow_dir).mkdir(parents=True, exist_ok=True)
    _active_dir(towow_dir).mkdir(parents=True, exist_ok=True)


def _slug(value: str) -> str:
    """Filename-safe slug (matches orchestrator._dispatch_target_slug convention)."""
    return "".join(ch if ch.isalnum() else "_" for ch in value)


def _active_key(source_object_ref: str, entry_reason: DeadLetterEntryReason) -> str:
    """幂等键: 同一 (源对象, 进入源) 同一时刻至多一条活跃条目 (防 re-scan 重复入箱)。"""
    return f"{_slug(source_object_ref)}__{entry_reason.value}"


# ════════════════════════════════════════════════════════════════════════════════
#  Entry model
# ════════════════════════════════════════════════════════════════════════════════


@dataclass
class DeadLetterEntry:
    """死信条目 —— 字段钉死 (concept dead_letter_entry)。

    source_object_ref: event_id 或 task_id (被死信的对象引用)。
    triage_status: 对应状态机当前态 (DeadLetterState 值)。
    reentry_count: 回头客计数 (重派失败补偿 / 同对象二次入箱累加); 达 _redispatch_cap → 强制 retired。
    is_returning: 是否回头客 (reentry_count > 0)。
    """

    entry_id: str
    source_object_type: str
    source_object_ref: str
    entry_reason: str  # DeadLetterEntryReason value
    original_trigger_event_id: str | None
    entered_at: float
    triage_status: str  # DeadLetterState value
    reentry_count: int = 0
    is_returning: bool = False
    triage_action: str | None = None  # DeadLetterTriageAction value (一旦分诊出处置)
    triage_actor: str | None = None
    retire_reason: str | None = None  # DeadLetterRetireReason value (RETIRED 时)
    escalation_event_id: str | None = None  # ESCALATED 时关联的 escalation
    updated_at: float | None = None
    last_event_id: str | None = None  # 最近一次 lifecycle 事件 id (审计游标)

    @property
    def is_terminal(self) -> bool:
        return DeadLetterState(self.triage_status) in _TERMINAL_STATES

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, object]) -> DeadLetterEntry:
        fields = {k: data.get(k) for k in cls.__dataclass_fields__}
        return cls(**fields)  # type: ignore[arg-type]


class DeadLetterError(Exception):
    """非法迁移 / 状态约束违反 (调用方用错了状态机)。"""


# ════════════════════════════════════════════════════════════════════════════════
#  Persistence helpers
# ════════════════════════════════════════════════════════════════════════════════


def _entry_path(towow_dir: Path, entry_id: str) -> Path:
    return _entries_dir(towow_dir) / f"{entry_id}.json"


def _write_entry(towow_dir: Path, entry: DeadLetterEntry) -> None:
    ensure_dead_letter_layout(towow_dir)
    path = _entry_path(towow_dir, entry.entry_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry.to_json(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    # active 幂等键随终态增删: 非终态 → 写键指向本条目; 终态 → 删键 (放行同对象后续 re-entry)。
    key_path = _active_dir(towow_dir) / _active_key(
        entry.source_object_ref, DeadLetterEntryReason(entry.entry_reason),
    )
    if entry.is_terminal:
        key_path.unlink(missing_ok=True)
    else:
        key_path.write_text(entry.entry_id, encoding="utf-8")


def get_entry(towow_dir: Path, entry_id: str) -> DeadLetterEntry | None:
    path = _entry_path(towow_dir, entry_id)
    if not path.exists():
        return None
    try:
        return DeadLetterEntry.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def list_entries(
    towow_dir: Path, *, include_terminal: bool = True,
) -> list[DeadLetterEntry]:
    """全部死信条目 (按 entered_at 升序)。include_terminal=False → 仅活跃 (待分诊) 条目。"""
    edir = _entries_dir(towow_dir)
    if not edir.exists():
        return []
    out: list[DeadLetterEntry] = []
    for path in edir.glob("*.json"):
        try:
            entry = DeadLetterEntry.from_json(
                json.loads(path.read_text(encoding="utf-8")),
            )
        except (json.JSONDecodeError, OSError, TypeError):
            continue
        if not include_terminal and entry.is_terminal:
            continue
        out.append(entry)
    out.sort(key=lambda e: e.entered_at)
    return out


def _active_entry_for(
    towow_dir: Path, source_object_ref: str, entry_reason: DeadLetterEntryReason,
) -> DeadLetterEntry | None:
    """该 (源对象, 进入源) 当前是否有活跃 (非终态) 条目。"""
    key_path = _active_dir(towow_dir) / _active_key(source_object_ref, entry_reason)
    if not key_path.exists():
        return None
    entry = get_entry(towow_dir, key_path.read_text(encoding="utf-8").strip())
    if entry is None or entry.is_terminal:
        return None
    return entry


def _prior_terminal_reentry(
    towow_dir: Path, source_object_ref: str, entry_reason: DeadLetterEntryReason,
) -> int:
    """同对象同进入源历史已终结条目中的最大 reentry_count (供 re-entry 累加回头客计数)。"""
    max_count = -1
    for entry in list_entries(towow_dir):
        if (
            entry.source_object_ref == source_object_ref
            and entry.entry_reason == entry_reason.value
            and entry.is_terminal
        ):
            max_count = max(max_count, entry.reentry_count)
    return max_count


# ════════════════════════════════════════════════════════════════════════════════
#  Event emission (NodeTouched + kind, abstractable_process —— lifecycle 审计留痕)
# ════════════════════════════════════════════════════════════════════════════════


def _emit(
    event_log: EventLog,
    kind: str,
    entry: DeadLetterEntry,
    *,
    extra: dict[str, object] | None = None,
) -> str:
    """Emit a dead-letter lifecycle event carrying the full entry snapshot."""
    stub: dict[str, object] = {"kind": kind, "entry": entry.to_json()}
    if extra:
        stub.update(extra)
    payload: dict[str, object] = {
        "target_entity_type": "task",
        "target_entity_id": entry.entry_id,
        "touch_type": "write",
        "kind": kind,
        "stub_original_payload": stub,
    }
    intent = EventIntent(
        local_intent_id=f"dle-{uuid.uuid4().hex[:12]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=_DL_ACTOR_TYPE,
            actor_id=_DL_ACTOR_ID,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=entry.entry_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def _reentry_cap() -> int:
    """回头客上限 = orchestrator._redispatch_cap (concept: '与 _redispatch_cap 同值, 默认 3')。

    懒导入避免与 orchestrator 的模块级循环依赖 (orchestrator 在入箱 wiring 处懒导入本模块)。
    """
    from towow.l2.orchestrator import _redispatch_cap

    return _redispatch_cap()


# ════════════════════════════════════════════════════════════════════════════════
#  入箱 (三类进入源共用; 互斥由 entry_reason enum 保证)
# ════════════════════════════════════════════════════════════════════════════════


def enqueue(
    towow_dir: Path,
    event_log: EventLog,
    *,
    source_object_type: str,
    source_object_ref: str,
    entry_reason: DeadLetterEntryReason,
    original_trigger_event_id: str | None = None,
    now: float | None = None,
) -> DeadLetterEntry:
    """投入死信箱 (enqueued 态)。幂等: 同 (源对象, 进入源) 已有活跃条目 → 返回该条目不重复入箱。

    同对象在历史终结条目后再次入箱 → 新条目带 is_returning=True + reentry_count 累加 (回头客)。
    返回入箱后的 DeadLetterEntry。
    """
    if now is None:
        now = time.time()
    ensure_dead_letter_layout(towow_dir)

    existing = _active_entry_for(towow_dir, source_object_ref, entry_reason)
    if existing is not None:
        return existing  # 幂等 —— re-scan / 重复触发不造重复条目

    prior_terminal = _prior_terminal_reentry(towow_dir, source_object_ref, entry_reason)
    is_returning = prior_terminal >= 0
    reentry_count = prior_terminal + 1 if is_returning else 0

    entry = DeadLetterEntry(
        entry_id=f"dle-{uuid.uuid4().hex[:12]}",
        source_object_type=source_object_type,
        source_object_ref=source_object_ref,
        entry_reason=entry_reason.value,
        original_trigger_event_id=original_trigger_event_id,
        entered_at=now,
        triage_status=DeadLetterState.ENQUEUED.value,
        reentry_count=reentry_count,
        is_returning=is_returning,
        updated_at=now,
    )
    entry.last_event_id = _emit(event_log, _KIND_ENQUEUED, entry)
    _write_entry(towow_dir, entry)
    return entry


# ════════════════════════════════════════════════════════════════════════════════
#  分诊迁移
# ════════════════════════════════════════════════════════════════════════════════


def _require(entry: DeadLetterEntry | None, entry_id: str) -> DeadLetterEntry:
    if entry is None:
        msg = f"dead-letter entry not found: {entry_id}"
        raise DeadLetterError(msg)
    return entry


def _require_state(
    entry: DeadLetterEntry, allowed: frozenset[DeadLetterState] | set[DeadLetterState],
) -> None:
    state = DeadLetterState(entry.triage_status)
    if state not in allowed:
        allowed_names = ", ".join(sorted(s.value for s in allowed))
        msg = (
            f"dead-letter entry {entry.entry_id} in state '{state.value}'; "
            f"transition requires one of [{allowed_names}]"
        )
        raise DeadLetterError(msg)


def start_triage(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    triage_actor: str,
    now: float | None = None,
) -> DeadLetterEntry:
    """enqueued → under_triage (分诊员领取)。"""
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.ENQUEUED})
    entry.triage_status = DeadLetterState.UNDER_TRIAGE.value
    entry.triage_actor = triage_actor
    entry.updated_at = now
    entry.last_event_id = _emit(event_log, _KIND_TRIAGE_STARTED, entry)
    _write_entry(towow_dir, entry)
    return entry


def must_force_retire(entry: DeadLetterEntry) -> bool:
    """回头客计数达上限 → 分诊必须强制改判 retired (deterministic 不可恢复)。"""
    return entry.reentry_count >= _reentry_cap()


def decide_redispatch(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    triage_actor: str,
    now: float | None = None,
) -> DeadLetterEntry:
    """under_triage → redispatched (终态)。仅在重派【确认成功】、回到正常流转后调。

    回头客已达上限的条目禁止重派 (必须 retire) —— 防 deterministic 不可恢复对象无限重蹈。
    """
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.UNDER_TRIAGE})
    if must_force_retire(entry):
        msg = (
            f"entry {entry_id} reentry_count={entry.reentry_count} 达上限 "
            f"{_reentry_cap()} —— 强制改判 retired, 禁止 redispatch"
        )
        raise DeadLetterError(msg)
    entry.triage_status = DeadLetterState.REDISPATCHED.value
    entry.triage_action = DeadLetterTriageAction.REDISPATCH.value
    entry.triage_actor = triage_actor
    entry.updated_at = now
    entry.last_event_id = _emit(event_log, _KIND_REDISPATCHED, entry)
    _write_entry(towow_dir, entry)
    return entry


def decide_retire(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    triage_actor: str,
    retire_reason: DeadLetterRetireReason = DeadLetterRetireReason.MANUAL,
    now: float | None = None,
) -> DeadLetterEntry:
    """under_triage → retired (终态, 带理由)。分诊判定不可恢复。"""
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.UNDER_TRIAGE})
    entry.triage_status = DeadLetterState.RETIRED.value
    entry.triage_action = DeadLetterTriageAction.RETIRE.value
    entry.triage_actor = triage_actor
    entry.retire_reason = retire_reason.value
    entry.updated_at = now
    entry.last_event_id = _emit(event_log, _KIND_RETIRED, entry)
    _write_entry(towow_dir, entry)
    return entry


def decide_escalate(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    triage_actor: str,
    escalation_event_id: str | None = None,
    now: float | None = None,
) -> DeadLetterEntry:
    """under_triage → escalated_to_owner (产 escalation 等 owner 拍板)。

    escalation_event_id = 关联的 EscalationRaised/GoalEscalationRaised (由 caller 产; 本模块只记
    条目侧迁移 + 关联引用 —— escalation 本体 emit + escalation-answer-reflow 送回属 sibling
    T-LRF-06 escalation-answer-reflow@v1 / escalation-inbox 卡片机)。
    """
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.UNDER_TRIAGE})
    entry.triage_status = DeadLetterState.ESCALATED_TO_OWNER.value
    entry.triage_action = DeadLetterTriageAction.ESCALATE.value
    entry.triage_actor = triage_actor
    entry.escalation_event_id = escalation_event_id
    entry.updated_at = now
    entry.last_event_id = _emit(event_log, _KIND_ESCALATED, entry)
    _write_entry(towow_dir, entry)
    return entry


def redispatch_failed(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    failure_summary: str = "",
    now: float | None = None,
) -> DeadLetterEntry:
    """SAGA 补偿: 重派 spawn 起不来/再次崩溃。

    under_triage → enqueued 且 reentry_count+1 (账本不可变, 不删原条目)。
    若 +1 后达上限 → 直接强制改判 retired(reason=reentry_exhausted) (deterministic 不可恢复, 不再
    回 enqueued 蹈无限循环 —— 兑现 terminal-state-reachability: 死信箱里不能再卡死)。
    """
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.UNDER_TRIAGE})
    new_count = entry.reentry_count + 1
    entry.reentry_count = new_count
    entry.is_returning = True
    entry.updated_at = now
    extra: dict[str, object] | None = (
        {"failure_summary": failure_summary} if failure_summary else None
    )
    if new_count >= _reentry_cap():
        entry.triage_status = DeadLetterState.RETIRED.value
        entry.triage_action = DeadLetterTriageAction.RETIRE.value
        entry.retire_reason = DeadLetterRetireReason.REENTRY_EXHAUSTED.value
        entry.last_event_id = _emit(event_log, _KIND_RETIRED, entry, extra=extra)
        _write_entry(towow_dir, entry)
        return entry
    entry.triage_status = DeadLetterState.ENQUEUED.value
    entry.triage_action = None  # 回 enqueued 等下一轮分诊
    entry.last_event_id = _emit(event_log, _KIND_REDISPATCH_FAILED, entry, extra=extra)
    _write_entry(towow_dir, entry)
    return entry


def apply_escalation_answer(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    reflow_event_id: str | None = None,
    now: float | None = None,
) -> DeadLetterEntry:
    """escalated_to_owner → under_triage (owner 答复经 escalation-answer-reflow@v1 送回重新分诊)。

    reflow_event_id = sibling T-LRF-06 产的 EscalationAnswerReflowApplied 事件 (canonical 触发源);
    本函数记条目侧的结果态迁移 (DeadLetterTriageResumed lifecycle 留痕)。
    """
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, {DeadLetterState.ESCALATED_TO_OWNER})
    entry.triage_status = DeadLetterState.UNDER_TRIAGE.value
    entry.triage_action = None
    entry.updated_at = now
    extra: dict[str, object] | None = (
        {"reflow_event_id": reflow_event_id} if reflow_event_id else None
    )
    entry.last_event_id = _emit(event_log, _KIND_TRIAGE_RESUMED, entry, extra=extra)
    _write_entry(towow_dir, entry)
    return entry


# ════════════════════════════════════════════════════════════════════════════════
#  超龄强制终态化 (terminal-state-reachability 动态机制: 发现 + 行动, 非只告警)
# ════════════════════════════════════════════════════════════════════════════════


def _age_out_one(
    towow_dir: Path, event_log: EventLog, entry: DeadLetterEntry, now: float,
) -> DeadLetterEntry:
    entry.triage_status = DeadLetterState.RETIRED.value
    entry.retire_reason = DeadLetterRetireReason.AGED_OUT.value
    entry.updated_at = now
    entry.last_event_id = _emit(event_log, _KIND_AGED_OUT, entry)
    _write_entry(towow_dir, entry)
    return entry


def age_out(
    towow_dir: Path,
    event_log: EventLog,
    entry_id: str,
    *,
    now: float | None = None,
) -> DeadLetterEntry:
    """单条目超龄强制终态化: {enqueued, escalated_to_owner} → retired(reason=aged_out)。"""
    if now is None:
        now = time.time()
    entry = _require(get_entry(towow_dir, entry_id), entry_id)
    _require_state(entry, _AGEABLE_STATES)
    return _age_out_one(towow_dir, event_log, entry, now)


def sweep_aged_out(
    towow_dir: Path,
    event_log: EventLog,
    *,
    ttl_seconds: float,
    now: float | None = None,
) -> list[str]:
    """巡检: 把超 TTL 仍未离开 {enqueued, escalated_to_owner} 的条目强制终态化 (aged_out → retired)。

    这是 terminal-state-reachability@v1 的动态兑现 —— 巡检不止告警而执行终态化, 死信箱里不卡死。
    返回本次被终态化的 entry_id 列表 (供 daemon 巡检 / 观测 / 测试)。
    daemon 巡检接线 (周期调本函数) 属 sibling T-LRF-11/T-LRF-10b 巡检层 —— 本函数是被它调的机制。
    """
    if now is None:
        now = time.time()
    aged: list[str] = []
    for entry in list_entries(towow_dir, include_terminal=False):
        if DeadLetterState(entry.triage_status) not in _AGEABLE_STATES:
            continue
        if (now - entry.entered_at) <= ttl_seconds:
            continue
        _age_out_one(towow_dir, event_log, entry, now)
        aged.append(entry.entry_id)
    return aged


# ════════════════════════════════════════════════════════════════════════════════
#  视图 (owner 与 agent 均有 —— 死信清单 + 进入原因 + 原始对象引用, 等分诊)
# ════════════════════════════════════════════════════════════════════════════════


def _view_row(entry: DeadLetterEntry) -> dict[str, object]:
    return {
        "entry_id": entry.entry_id,
        "source_object_type": entry.source_object_type,
        "source_object_ref": entry.source_object_ref,
        "entry_reason": entry.entry_reason,
        "triage_status": entry.triage_status,
        "reentry_count": entry.reentry_count,
        "is_returning": entry.is_returning,
        "original_trigger_event_id": entry.original_trigger_event_id,
        "entered_at": entry.entered_at,
    }


def agent_view(towow_dir: Path) -> list[dict[str, object]]:
    """agent 分诊视图: 待分诊 (非终态) 条目清单 —— 进入原因 + 原始对象引用, 按入箱时间升序。"""
    return [_view_row(e) for e in list_entries(towow_dir, include_terminal=False)]


def owner_view(towow_dir: Path) -> dict[str, object]:
    """owner 视图: 待分诊清单 + 按进入原因/状态的聚合计数 (含已终结条目的全量统计)。"""
    active = list_entries(towow_dir, include_terminal=False)
    everything = list_entries(towow_dir, include_terminal=True)
    by_reason: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entry in everything:
        by_reason[entry.entry_reason] = by_reason.get(entry.entry_reason, 0) + 1
        by_status[entry.triage_status] = by_status.get(entry.triage_status, 0) + 1
    return {
        "pending": [_view_row(e) for e in active],
        "pending_count": len(active),
        "total_count": len(everything),
        "by_entry_reason": by_reason,
        "by_triage_status": by_status,
    }


__all__ = [
    "DeadLetterEntry",
    "DeadLetterEntryReason",
    "DeadLetterError",
    "DeadLetterRetireReason",
    "DeadLetterState",
    "DeadLetterTriageAction",
    "age_out",
    "agent_view",
    "apply_escalation_answer",
    "decide_escalate",
    "decide_redispatch",
    "decide_retire",
    "enqueue",
    "ensure_dead_letter_layout",
    "get_entry",
    "list_entries",
    "must_force_retire",
    "owner_view",
    "redispatch_failed",
    "start_triage",
    "sweep_aged_out",
]
