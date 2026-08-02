"""finding-routability-birth-gate@v2 (@birth-time-validation@v1 第一具名实例) — commit gate 对
FindingCreated 准入加一道出生闸, 校验其【可路由性】。

不变量 (@birth-time-validation@v1): 凡产生一等对象的出生入口必须校验对象合法性, 不合法对象在
出生时被拦截 (拒绝/不落盘), 不靠出生后下游兜底。FindingCreated 的合法性 = 可路由性。

【v2 corrected_error: 纠正 v1 的子集漂移】v1 把可路由判据钉成两条【表成员】检查 (finding_kind ∈
_DISPATCH_TABLE_FINDING_KIND 或 suggested_fix_layer.primary ∈ _FALLBACK_FIX_LAYER_DISPATCH —— 即
复制 orchestrator 的两张路由表)。但 orchestrator._route_event 对 FindingCreated 的真实路由能力含
【表外内联分支】: _fallback_finding_route 的 is_review→fix (finding_kind=None ∧ 无可映射
suggested_fix_layer ∧ source.type=model_review → fix), 以及 design_time 前置分支
(resolve_review_mode==design_time → review)。两者皆不在任一表内。故 v1 判据 = orchestrator 真实路由
能力的【严格子集】→ 一条 schema-legal 的 model_review finding (finding_kind=None、无
suggested_fix_layer、带齐 finding.py model_validator 强制的 voi_rationale/target/
falsification_evidence/closure_contract) 被出生闸误判不可路由 → fail-closed 拒整批 → 合法 finding 及
同 envelope 的 FindingVerified 被静默丢 (v2 要堵的 false-reject 方向)。

【v2 钉死的稳定判据】可路由性 ⟺ orchestrator 对该 FindingCreated payload 的【路由决策】≠ no-route。
该判据由【调用 orchestrator 真实路由判定逻辑 (_route_event) 读其决策结果】得出, 不复制路由表、不重新
推导/复刻分支组合 —— 因为"表成员" ≠ "路由结果" (反例: governance finding_kind 在表内但经
owner_unfrozen gating 路由到 "Nature dashboard"; is_review 不在任一表内但路由到 fix)。复制表/复刻分支
必然再次漂移; 读决策则结构上不再漂移: 将来 orchestrator 增删任一路由分支 (含 design_time 前置 /
is_review→fix), 出生闸自动跟随、判据永远 = orchestrator 当下真实路由能力。

实现 (M-1.4 §3 执行裁量): 把每个 FindingCreated intent mint 成一具未落账的 EventRecord, 喂给
OrchestratorDaemon._route_event —— 它是唯一的【组合层】入口 (含 design_time 前置 + finding_kind 路由
+ fallback 三支), 读其返回 decision: 任一 dispatch_to ≠ no-route → 可路由。不复刻这套组合 (复刻只跟随
被复刻的子函数, 不跟随 _route_event 组合层/前置分支的将来增删)。

L0→L2 import 走函数内 lazy import: 避免 L0 核心模块 load 时把 L2 拉进 import 图 (orchestrator 模块级
不 import commit_gate, 运行期实测无环)。

event_log 形参 (M-1.4 §3 执行裁量): governance finding_kind 路由 (_route_finding_kind) 需 event_log
读 INF-003 unfrozen flag 才能算出 dispatch target。本闸把它作【可选】形参: 调用方 (commit gate
_run_checks) 应线程 self._event_log 进来 → governance 路由按真实决策算 (见 finding
f-bgrv2-gate-thread-eventlog: gate.py 的 1 行调用方接线, 落另一 write_set)。缺失时仅 governance
finding 的【路由计算】退化 —— 但 orchestrator 对任一 governance finding 的路由【结果】恒为非
no-route (fix / Nature dashboard, 永不 no-route), 故【可路由性 verdict】不变, 退化路径忠实于 verdict
(admit, 与 v1 经表成员放行 governance 同侧、不回归)。线程 event_log 让 governance 路由【计算】精确,
verdict 不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.l2.orchestrator import OrchestratorDaemon
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

_FINDING_CREATED = "FindingCreated"
_NO_ROUTE = "no-route"
# Probe records are never persisted — they exist only to be fed to orchestrator's read-only routing
# logic, which (for the FindingCreated path) reads only event_type / payload / event_id. A fixed
# sentinel timestamp makes that explicit (routing never reads it; no real wall-clock is implied).
_PROBE_TIMESTAMP = datetime(1970, 1, 1, tzinfo=UTC)
_PROBE_EVENT_ID = "evt-finding-routability-probe"


@dataclass(frozen=True)
class FindingRoutabilityResult:
    """gate finding-routability 检查结果 (镜像 review_verdict_check.ReviewVerdictResult 形态)。

    notices (W1-R2 新增): warn-only 观察期的非阻断提示 (passed=True 但留痕), 由调用方
    (gate.py _run_checks) 并入 CommitAccepted.informational_notices。check_finding_routability
    本身不产 notices (恒空列表, 向后兼容); finding_classification_consistency.py 复用本 dataclass
    时才会填充。
    """

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)


def _intent_to_probe_record(intent: EventIntent) -> EventRecord:
    """Mint an un-written EventRecord from a domain intent, for orchestrator routing probing.

    orchestrator._route_event reads only event_type / payload / event_id off the record for the
    FindingCreated path; the writer-stamped fields below are placeholders (this record is never
    persisted). Mirrors EventLog._stamp's intent→record field copy without needing a live EventLog
    (the birth gate must mint a record even when no event_log is threaded — see
    check_finding_routability).
    """
    from towow.schemas.event_record import EventRecord, Provenance

    return EventRecord(
        event_id=_PROBE_EVENT_ID,
        sequence_number=0,
        timestamp=_PROBE_TIMESTAMP,
        transaction_id=None,
        batch_id=None,
        batch_position=None,
        record_hash="0",
        local_intent_id=intent.local_intent_id,
        event_type=intent.event_type,
        event_category=intent.event_category,
        payload=intent.payload,
        provenance=Provenance(**intent.provenance_hint.model_dump()),
        base_classification=intent.base_classification,
        supersede=intent.supersede,
        subjects=list(intent.subjects),
        schema_version=intent.schema_version,
    )


def _finding_is_routable(
    daemon: OrchestratorDaemon,
    intent: EventIntent,
    *,
    event_log_threaded: bool,
) -> bool:
    """可路由性 ⟺ orchestrator._route_event 对该 FindingCreated 的【路由决策】≠ no-route。

    读 orchestrator 真实决策 (组合层 _route_event 覆盖 design_time 前置 + finding_kind 路由含
    governance gating + _fallback_finding_route 含 is_review→fix), 任一 decision.dispatch_to ≠
    no-route → 可路由。空 decision list → 不可路由 (无路由)。不复刻这套组合 → orchestrator 将来增删
    任一路由分支, 本闸自动跟随。
    """
    rec = _intent_to_probe_record(intent)
    try:
        decisions = daemon._route_event(rec)
    except AttributeError:
        if event_log_threaded:
            # 调用方已线程真 event_log —— 此处 AttributeError 是真 routing bug, 不吞 (fail-loud)。
            raise
        # 调用方未线程 event_log: _route_event 的 FindingCreated 路径里【唯一】解引用 event_log 的是
        # governance finding_kind 路由 (_route_finding_kind 读 INF-003 unfrozen flag)。orchestrator
        # 对任一 governance finding 的路由结果恒为非 no-route (fix / Nature dashboard, 永不 no-route),
        # 故【可路由性 verdict】恒为 routable → admit (与 v1 经表成员放行 governance 同侧, 不回归)。
        # 线程 self._event_log (commit gate _run_checks → check_finding_routability) 让 governance
        # 路由【计算】精确; verdict 不变。非 governance finding 不解引用 event_log → 不会走到这里。
        return True
    return any(decision.dispatch_to != _NO_ROUTE for decision in decisions)


def check_finding_routability(
    domain_intents: list[EventIntent],
    event_log: EventLog | None = None,
) -> FindingRoutabilityResult:
    """对 envelope batch 里每个 FindingCreated 校验可路由性; 首个不可路由 → fail (出生拦截)。

    可路由性 ⟺ orchestrator 对该 FindingCreated 的真实路由决策 ≠ no-route (v2: 读决策, 非复制表)。
    非 FindingCreated intent → 跳过 (本闸只管 FindingCreated 出生)。可路由 → 通过。一个不可路由
    FindingCreated → fail-closed 拒整批, 该 FindingCreated 不落 canonical 事件。

    event_log: governance finding_kind 路由所需 (见模块 docstring); 调用方 (commit gate) 应线程
    self._event_log。缺省 None 时 governance 路由退化但 verdict 不变 (governance finding 恒可路由)。
    """
    # SSOT (单一真相源, 防漂移): 读 orchestrator 真实路由【决策】, 不在本模块复制路由表/复刻分支组合。
    # lazy import 避 L0 模块级 L0→L2 环 (运行期实测无环)。
    from towow.l2.orchestrator import OrchestratorDaemon

    event_log_threaded = event_log is not None
    # event_log 可能为 None (调用方未线程 / 既有单测): 仅 governance finding 路由解引用它, 由
    # _finding_is_routable 的退化分支忠实处理 (governance 恒可路由)。cast 标注此意图。
    daemon = OrchestratorDaemon(cast("EventLog", event_log))

    for intent in domain_intents:
        if intent.event_type.value != _FINDING_CREATED:
            continue
        if _finding_is_routable(daemon, intent, event_log_threaded=event_log_threaded):
            continue
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        finding_kind = payload.get("finding_kind")
        sfl = payload.get("suggested_fix_layer")
        primary = sfl.get("primary") if isinstance(sfl, dict) else None
        source = payload.get("source")
        source_type = source.get("type") if isinstance(source, dict) else None
        return FindingRoutabilityResult(
            passed=False,
            failure_reason="finding_unroutable",
            failure_evidence={
                "finding_id": str(payload.get("finding_id", "")),
                "finding_kind": str(finding_kind),
                "suggested_fix_layer_primary": str(primary),
                "source_type": str(source_type),
                "reason": "FindingCreated 不可路由 (orchestrator 对该 payload 的路由决策 = no-route) → "
                "出生拦截 (birth-time-validation@v1: 不可路由 finding 不准进系统, 杜绝'进了再 fallback "
                "重派死循环'; v2: 判据 = orchestrator 真实路由决策, 非复制表)",
            },
        )
    return FindingRoutabilityResult(passed=True)


__all__ = ["FindingRoutabilityResult", "check_finding_routability"]
