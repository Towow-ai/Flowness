"""activation-acceptance-gate@v1 (激活验收门, anti-fake-done · 建成未接线侧): commit gate 对闭合类
envelope 加一道 blocking_check —— 闭合那一刻要求出示"生产路径真被有机调用"的证据, 出示不了则强制携带
一条带 expiry 的 activation-debt (真空期欠条), 非法闭合则拒。

# spec source:
#   concept activation-acceptance-gate@v1 (ConceptCreated evt-d170b5796a5c4aec99c49a51160b8bac)
#     "扩展既有任务/finding 闭合 gate 的激活验收关卡: 闭合那一刻要求出示'生产路径真被有机调用'的账本
#      证据(据 activation-evidence-organic-only@v1, 只认有机、不认演习); 出示不了则强制登记一条带
#      expiry 的 activation-debt(真空期标 awaiting_organic_traffic、不算失败), 证据造假/非法闭合则拒。
#      覆盖全类型 workload、全七型缺口。自指免疫要求: 本门挂在每次闭合必经路径上、天生每次被行使,
#      其开火在 gate_check_firing 投影可查(触发计数≥1、非潜伏), 证明它自己不是第 175 条'建成未接线'。"
#     "C/E 型证据经 activation-multisource-cli-probe@v1 纯 CLI 取。"

════ 设计: fire-always / demand-narrow (镜像 live_target_reconcile_check 的零误伤范式) ════

本门在【每次 accept】都 append 到 checks_performed (applicable=False 时 result=not_applicable 也
append) —— 满足自指免疫 inv3 非潜伏 (gate_check_firing.activation_acceptance 触发计数≥1)。但 DEMAND
(要求带债、否则拒) 只落在【适用闭合】上, 不误伤普通闭合 (universal-demand 会拒掉每一个不带 activation-debt
的兄弟/未来闭合, 而本门只能改 gate.py + 本文件、改不了那些 executor —— 拒掉作者无从修的活 = 不可上线的门;
这与 live_target "覆盖 completion 但只在 observable 在场时 demand" 同构)。

════ 适用性信号 (self-immunity 选出的, 非测试选出的) ════

DEMAND 适用 ⟺ 闭合 task 的 concept_refs 含一个【已注册能力】(harness-capability-registry@v1 的
committed reference 边净成员)。选它的判据是自指免疫: 本门自己建成时, T-ACT-4 的闭合 concept_refs 含
activation-acceptance-gate@v1 (它随本 task 被登记进注册表), 故本门天生对【自己的闭合】开火 demand ——
这正是"证明它自己不是第 175 条建成未接线"的具体兑现。信号在真实"建/碰能力"的闭合上今天就有机触发, 对
bug-fix / doc 闭合 (不声明能力概念) 静默 (零误伤)。无 requires_activation_evidence 概念标记存在 (代码里
查无), 故 key 到注册表成员这一 event-sourced 真相源。

════ DEMAND 满足条件 (v1: 债释放阀) ════

存在一条 activation 类 DebtRegistered (committed 账本 OR 本批 intent), 其 debt_type=activation、带非空
expiry、against_capability==该能力 或 registered_by_task==闭合 task → 放行 (真空期挂欠条、不算失败, owner
定 2026-07-07)。register_activation_debt 走 write_direct (path B, 即时 audit-visible) —— executor 在
work complete 前先登债, 门读 committed 即见, 故不强求同批。

真空期现实: 能力刚建成时生产路径必然零有机流量 (这正是"建成未接线"的本义), 故债释放阀是 v1 的主/唯一路径。

════ 为何 v1 无"有机证据免债"释放阀 (诚实边界, 登债追踪) ════

concept 的有机证据源是 **probe** (C/E 型经 activation-multisource-cli-probe 纯 CLI 取) 或闭合方【出示】;
但 (a) strict 闭合/envelope schema 无字段可让闭合方"出示"有机证据 ref; (b) 对账本做"扫出该能力的有机
使用留痕"是不健全的 —— is_organic 是【判某条事件是否有机】的分类器, 不是【在账本里找该能力被有机使用】的
扫描器; 经共识诞生的能力必带 ConceptStateMachineDefined / ConsumerListPublished / envelope 等有机
provenance 的【设计】事件 (以能力为 subject), 泛化扫描无法把"能力被设计"与"能力被有机使用"区分开 → 会把
每个共识能力误判为"已有有机证据" → 门恒放行 = 第 175 条空转门。故 v1 **不做**账本扫描释放阀; 有机证据的
【接受】(经 probe 接线) 与 probe 本身的 gate 侧接线一并留债 (against 本门自身)。这不弱化 anti-fake-done:
v1 不接受任何账本外自报/演习证据, "证据造假 → 拒"反而更强 (伪造有机证据在 v1 无任何过关面, 一律走
"无债即拒")。

════ 拒 (fail-closed) ════

适用闭合无 activation-debt → 拒 (建成未接线的闭合不带诚实欠条 = 假激活后门)。演习/livefire 留痕在 v1
无过关面 —— 摆演习留痕不能替代诚实欠条。拒绝 = 整批 envelope 被拒 → 闭合不落账 → task 留 open (镜像
closure_evidence_check)。

实现 schema-independent (镜像 closure_evidence_check / live_target_reconcile_check): 字符串事件类型 +
dict payload 访问, 不 import 闭合事件的 typed EventType/Payload —— 本门自包含。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

_TASK_RUN_COMPLETED = "TaskRunCompleted"
_TASK_NODE_CLOSED = "TaskNodeClosed"
_FINDING_VERIFIED = "FindingVerified"
_TASK_NODE_CREATED = "TaskNodeCreated"
_CONCEPT_EDGE_ADDED = "ConceptEdgeAdded"
_CONCEPT_EDGE_REMOVED = "ConceptEdgeRemoved"
_DEBT_REGISTERED = "DebtRegistered"
_NODE_TOUCHED = "NodeTouched"

_REGISTRY_CONCEPT_ID = "harness-capability-registry@v1"
_REFERENCE_EDGE_TYPE = "reference"

_OUTCOME_SUCCESS = "success"
_DEBT_TYPE_ACTIVATION = "activation"

_CLOSURE_EVENT_TYPES = (_TASK_RUN_COMPLETED, _TASK_NODE_CLOSED, _FINDING_VERIFIED)


@dataclass(frozen=True)
class ActivationAcceptanceResult:
    """gate activation-acceptance 检查结果 (镜像 ClosureEvidenceResult 形态)。"""

    passed: bool
    applicable: bool = False  # 本批是否含【适用】闭合 (触碰已注册能力); 供 checks_performed 标 not_applicable
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)


def _unwrap_stub_rewrap(rec_or_intent: Any) -> tuple[str, dict[str, Any]]:
    """(effective_event_type, payload), 还原 NodeTouched stub-rewrap (镜像 closure_evidence_check)。

    orchestrator 自动派下一棒 / 部分闭合以 NodeTouched(kind=真类型, payload.stub_original_payload=真载荷)
    落账 —— 裸按 event_type 匹配会 miss。EventRecord 与 EventIntent 同有 .event_type.value / .payload。
    """
    payload = rec_or_intent.payload if isinstance(rec_or_intent.payload, dict) else {}
    if rec_or_intent.event_type.value == _NODE_TOUCHED:
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    return rec_or_intent.event_type.value, payload


def _after(payload: dict[str, Any]) -> dict[str, Any]:
    a = payload.get("after_state")
    return a if isinstance(a, dict) else {}


def _registered_capabilities(committed_records: list[EventRecord]) -> set[str]:
    """harness-capability-registry@v1 的 committed reference 边净成员 (added − removed)。

    成员 = 存在 ConceptEdgeAdded(source=registry, edge_type=reference, target=X) 且未被后续
    ConceptEdgeRemoved 撤销的 target 集。按 edge_id 净算 (add/remove 都带 edge_id); edge_id 缺省时退化按
    target 匹配。发现面据此机械算, 不靠静态清单 (harness-capability-registry@v1 的 event-sourced 约定)。
    """
    added: dict[str, str] = {}  # edge_id -> target
    added_no_id: set[str] = set()  # 退化: 无 edge_id 的 target
    removed_ids: set[str] = set()
    removed_targets: set[str] = set()
    for rec in committed_records:
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype == _CONCEPT_EDGE_ADDED:
            after = _after(payload)
            if (
                after.get("source_concept_id") == _REGISTRY_CONCEPT_ID
                and after.get("edge_type") == _REFERENCE_EDGE_TYPE
            ):
                target = after.get("target_concept_id")
                if isinstance(target, str) and target:
                    edge_id = after.get("edge_id")
                    if isinstance(edge_id, str) and edge_id:
                        added[edge_id] = target
                    else:
                        added_no_id.add(target)
        elif etype == _CONCEPT_EDGE_REMOVED:
            before = payload.get("before_state")
            before = before if isinstance(before, dict) else {}
            if (
                before.get("source_concept_id") == _REGISTRY_CONCEPT_ID
                and before.get("edge_type") == _REFERENCE_EDGE_TYPE
            ):
                edge_id = before.get("edge_id")
                if isinstance(edge_id, str) and edge_id:
                    removed_ids.add(edge_id)
                target = before.get("target_concept_id")
                if isinstance(target, str) and target:
                    removed_targets.add(target)
    members = {target for eid, target in added.items() if eid not in removed_ids}
    members |= {t for t in added_no_id if t not in removed_targets}
    return members


def _closing_task_id(etype: str, after: dict[str, Any]) -> str | None:
    """闭合事件 → 其归属 task_id (仅 task 型闭合可导出; FindingVerified 无 task 锚定 → None)。

    TaskRunCompleted: 仅 outcome=success 才是"声称做成"闭合 (aborted_* 不是完成声明, 不适用本门)。
    TaskNodeClosed: done_elsewhere / retired 终态, task_id 直接携带。
    """
    if etype == _TASK_RUN_COMPLETED:
        if after.get("outcome") != _OUTCOME_SUCCESS:
            return None
        tid = after.get("task_id")
        return tid if isinstance(tid, str) and tid else None
    if etype == _TASK_NODE_CLOSED:
        tid = after.get("task_id")
        return tid if isinstance(tid, str) and tid else None
    return None


def _task_concept_ref_ids(committed_records: list[EventRecord], task_id: str) -> list[str]:
    """task_id → TaskNodeCreated.concept_refs[].concept_id (声明碰的概念, event-sourced)。"""
    for rec in committed_records:
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype != _TASK_NODE_CREATED:
            continue
        after = _after(payload)
        if after.get("task_id") != task_id:
            continue
        refs = after.get("concept_refs")
        if not isinstance(refs, list):
            return []
        out: list[str] = []
        for r in refs:
            if isinstance(r, dict):
                cid = r.get("concept_id")
                if isinstance(cid, str) and cid:
                    out.append(cid)
        return out
    return []


def _activation_debt_covers(
    domain_intents: list[EventIntent],
    committed_records: list[EventRecord],
    *,
    capability: str,
    task_id: str,
) -> bool:
    """是否存在覆盖该能力/task 的 activation 类 DebtRegistered (本批 intent OR committed 账本)。

    覆盖 ⟺ debt_type=activation ∧ 带非空 expiry ∧ (against_capability==capability 或
    registered_by_task==task_id)。register_activation_debt 走 write_direct (即时 committed), 亦支持
    同批 intent 携带 —— 两路都认。
    """

    def _matches(payload: dict[str, Any]) -> bool:
        if payload.get("debt_type") != _DEBT_TYPE_ACTIVATION:
            return False
        expiry = payload.get("expiry")
        if not isinstance(expiry, str) or not expiry:
            return False
        return payload.get("against_capability") == capability or (
            payload.get("registered_by_task") == task_id
        )

    for intent in domain_intents:
        etype, payload = _unwrap_stub_rewrap(intent)
        if etype == _DEBT_REGISTERED and _matches(payload):
            return True
    for rec in committed_records:
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype == _DEBT_REGISTERED and _matches(payload):
            return True
    return False


def check_activation_acceptance(
    domain_intents: list[EventIntent],
    committed_records: list[EventRecord],
) -> ActivationAcceptanceResult:
    """闭合类 envelope 的激活验收门 (fire-always / demand-narrow, fail-closed)。

    本批无【适用】闭合 (无触碰已注册能力的 task 型闭合) → applicable=False, passed=True (仍 append
    not_applicable, 满足自指免疫 inv3)。任一适用闭合不带覆盖它的 activation-debt → fail-closed 拒。
    """
    registered = _registered_capabilities(committed_records)
    applicable = False

    for intent in domain_intents:
        etype, payload = _unwrap_stub_rewrap(intent)
        if etype not in _CLOSURE_EVENT_TYPES:
            continue
        after = _after(payload)
        task_id = _closing_task_id(etype, after)
        if task_id is None:
            # FindingVerified / 非 success 的 TaskRunCompleted → 无 task→concept_refs 锚定, 本门不冒充
            # 能验它 (零误伤, honest not_applicable)。
            continue
        touched_caps = [
            cid for cid in _task_concept_ref_ids(committed_records, task_id) if cid in registered
        ]
        if not touched_caps:
            continue  # 该闭合不声明已注册能力 → 不适用 demand (普通闭合零误伤)

        applicable = True
        for cap in touched_caps:
            if _activation_debt_covers(
                domain_intents, committed_records, capability=cap, task_id=task_id,
            ):
                continue  # 真空期宽限: 带 activation-debt → 放行
            return ActivationAcceptanceResult(
                passed=False, applicable=True,
                failure_reason="activation_no_evidence_no_debt",
                failure_evidence={
                    "task_id": task_id, "capability": cap,
                    "reason": (
                        "闭合声明完成能力 "
                        f"{cap} 但未携带带 expiry 的 activation-debt (真空期欠条) —— 生产路径尚无有机调用"
                        "证明其真被接线, 建成未接线的假激活闭合, fail-closed 拒 (演习/livefire 留痕不替代欠条)"
                    ),
                },
            )

    return ActivationAcceptanceResult(passed=True, applicable=applicable)


__all__ = ["ActivationAcceptanceResult", "check_activation_acceptance"]
