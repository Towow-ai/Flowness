"""M-0.7 §2.6 + §4 retention policy — retention_action 分级 + policy 变更 (T-L0-36, 波1).

# spec source: M-0.7 §2.6 (RetentionPolicyChanged payload) + §4 (三层 base_classification 矩阵)

retention_action 4 态 (RetentionAction 枚举) 按 base_classification 默认 (§4 矩阵):
  - immutable_truth      → archive_to_cold_after_gc_unreachable (保留原件入冷, 禁 digest, **永不 discard**)
  - abstractable_process → digest_then_archive (可 digest 后归冷)
  - discardable_noise    → discard_after_consumers_advance (consumer 都 advance 后丢)

owner-decided (RUN-038): 事实源永不自动删 — 上面默认 immutable_truth 不映射到 discard, 守住这条。

policy 可经 RetentionPolicyChanged 事件演进 (emit_retention_policy_changed); GC 跑时读最新 policy
(load_active_retention_policy) 决定每条 record 的 retention_action (resolve_retention_action), 据此
判是否可丢 — "GC 按变更后 policy 执行"。无 policy 事件则全走 §4 默认。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    RetentionAction,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog

# §4 三层矩阵默认 — 事实源永不 discard (owner-decided)。
_DEFAULT_BY_CLASSIFICATION: dict[BaseClassification, RetentionAction] = {
    BaseClassification.IMMUTABLE_TRUTH: RetentionAction.ARCHIVE_TO_COLD_AFTER_GC_UNREACHABLE,
    BaseClassification.ABSTRACTABLE_PROCESS: RetentionAction.DIGEST_THEN_ARCHIVE,
    BaseClassification.DISCARDABLE_NOISE: RetentionAction.DISCARD_AFTER_CONSUMERS_ADVANCE,
}


def default_retention_action(classification: BaseClassification) -> RetentionAction:
    """§4 矩阵: 某 base_classification 的默认 retention_action (immutable_truth 永不 discard)。"""
    return _DEFAULT_BY_CLASSIFICATION[classification]


def resolve_retention_action(
    classification: BaseClassification,
    policy_rules: list[dict[str, object]],
) -> RetentionAction:
    """给定 record 的 base_classification + 活跃 policy 规则, 返回适用的 retention_action。

    匹配: policy 里第一条 base_classification 命中且带 retention_action 的规则胜出; 否则 §4 默认。
    (event_category/event_type 级匹配留后续; 本版按 base_classification 解析, 足够 GC 判 keep/discard。)
    """
    resolved: RetentionAction | None = None
    for rule in policy_rules:
        rule_cls = rule.get("base_classification")
        action = rule.get("retention_action")
        if rule_cls == classification.value and isinstance(action, str):
            try:
                resolved = RetentionAction(action)
            except ValueError:
                continue
            break
    if resolved is None:
        resolved = default_retention_action(classification)
    # §4.2 硬约束 + owner-decided: immutable_truth 原件永不 discard, 哪怕 policy 误设也守死。
    if (
        classification is BaseClassification.IMMUTABLE_TRUTH
        and resolved is RetentionAction.DISCARD_AFTER_CONSUMERS_ADVANCE
    ):
        return RetentionAction.ARCHIVE_TO_COLD_AFTER_GC_UNREACHABLE
    return resolved


def load_active_retention_policy(event_log: EventLog) -> list[dict[str, object]]:
    """读最新 RetentionPolicyChanged.after_state.new_rules (policy 演进; 无则空 → 全走 §4 默认)。"""
    events = event_log.get_events_by_type(EventType.RETENTION_POLICY_CHANGED)
    if not events:
        return []
    latest = max(events, key=lambda r: r.sequence_number)
    after = latest.payload.get("after_state")
    if not isinstance(after, dict):
        return []
    rules = after.get("new_rules")
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def emit_retention_policy_changed(
    event_log: EventLog,
    *,
    policy_id: str,
    new_rules: list[dict[str, object]],
    change_reason: str,
) -> str:
    """emit RetentionPolicyChanged (path B) — 演进 retention policy。返回 event_id。

    base_classification=immutable_truth (policy 变更本身不可丢, M-0.1 §3.4)。
    """
    intent = EventIntent(
        local_intent_id=f"retpol-{uuid.uuid4().hex[:12]}",
        event_type=EventType.RETENTION_POLICY_CHANGED,
        event_category=EventCategory.CONSOLIDATION,
        payload={
            "after_state": {
                "policy_id": policy_id,
                "new_rules": new_rules,
                "change_reason": change_reason,
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SNAPSHOT_MODULE.value, actor_id="m07"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.SNAPSHOT, entity_id=policy_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


__all__ = [
    "default_retention_action",
    "emit_retention_policy_changed",
    "load_active_retention_policy",
    "resolve_retention_action",
]
