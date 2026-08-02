"""activation-debt@v1 专用注册入口 — '建成未接线' 缺口的可追踪债登记。

# spec source: engineering_consensus concept activation-debt@v1 (evt-06abe9a731b540ae8b43e6230eced4c5)
#   "'建成未接线'缺口的记录载体: 复用既有 DebtRegistered 一等事件家族, 扩 expiry 到期语义。语义=
#    某闭合体声称完成但生产路径尚无有机流量证明其真被接线时, 登记一条带 expiry 的可追踪债。"
#
# 为何独立入口而非复用 l0/debt/registry.py::register_debt:
#   register_debt 正被 plan-ledger-read-governance 的 T-LRG-B4-migrate-A 并发迁移离 all_records();
#   编辑它会撞跨 plan 写集致本 plan 冻结被拒。故 activation 债走本专用 l1 入口自建 EventIntent, 与
#   register_debt 的 path-B write_direct 范式一致, 只是携带 activation 载体的 4 个新字段
#   (expiry/gap_type/state/root_cause_key)。register_debt 现范式仅参考勿改。
#
# path B 写入 (immediately audit-visible, 同 register_debt): 登记/再登记不是闭合声明, 无需过门。
# DebtRegisteredPayload 的 model_validator 在写边界强制 debt_type==activation 必带 expiry+gap_type
# (schema 层, 见 schemas/payloads/debt.py) —— 本入口默认填齐, 缺则写时 loud fail。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActivationGapType,
    ActorType,
    BaseClassification,
    DebtSeverity,
    DebtState,
    DebtType,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


def register_activation_debt(
    event_log: EventLog,
    *,
    against_capability: str,
    gap_type: ActivationGapType,
    expiry: str,
    root_cause_key: str,
    title: str,
    description: str,
    resolution_criteria: str,
    severity: DebtSeverity = DebtSeverity.NORMAL,
    state: DebtState = DebtState.AWAITING_ORGANIC_TRAFFIC,
    depends_on: list[str] | None = None,
    debt_id: str | None = None,
    registered_by_task: str | None = None,
    registered_by_event_id: str | None = None,
    actor_id: str = "activation_debt_registry",
) -> EventRecord:
    """Emit an activation-class DebtRegistered event (path B). Returns the written record.

    真空期(闭合→首次有机流量)默认挂 state=awaiting_organic_traffic、不算失败(owner 定 2026-07-07);
    到期仍无有机流量才升级 —— 升级判据/到期比对是下游 activation-acceptance-gate 任务的范围, 本入口
    只登记载体, 不做到期逻辑。

    subject 指向 `against_capability`(作 concept ref), 使 capability → 其激活欠账的实体索引成立,
    与 register_debt 一致。
    """
    did = debt_id or f"debt-{uuid.uuid4().hex[:12]}"
    intent = EventIntent(
        local_intent_id=f"adr-{uuid.uuid4().hex[:12]}",
        event_type=EventType.DEBT_REGISTERED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "debt_id": did,
            "debt_type": DebtType.ACTIVATION.value,
            "severity": severity.value,
            "title": title,
            "description": description,
            "against_capability": against_capability,
            "depends_on": depends_on or [],
            "resolution_criteria": resolution_criteria,
            "registered_by_task": registered_by_task,
            "registered_by_event_id": registered_by_event_id,
            "expiry": expiry,
            "gap_type": gap_type.value,
            "state": state.value,
            "root_cause_key": root_cause_key,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=against_capability,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)


__all__ = ["register_activation_debt"]
