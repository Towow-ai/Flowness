"""commit category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.7 CommitFields (L202..L213)
#     §3.7 (L813..L846) — 3 base commit payloads
#   03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#     §6.5 CommitAccepted/CommitRejected payload schemas (L811..L848)  # Patch M-0.5a-4/6
#
# E.1.1 Conflict 4/6/7 (resolved per LEDGER):
#   - CommitAccepted/CommitRejected payload 强化:
#       envelope_event_id (required) — 指向 path B envelope
#       gate_run_id (required) — 此次 commit gate 运行的唯一 ID
#       accepted_domain_event_count (CommitAccepted) — batch 内 domain events 个数
#       recovery_of_abandoned (CommitRejected) — true iff produced by scan_abandoned
#   - projection 从 outcome sentinel 直接拿全部信息，不需要回查其他 event.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import CommitVerdict, RejectionType, SubjectEntityType

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class CheckPerformed(BaseModel):
    """checks_passed[] / rejection_reasons[] item per M-0.5 §6.5."""

    model_config = _STRICT

    check_type: str = Field(min_length=1)
    result: str | None = None
    details: str | None = None
    passed: bool | None = None  # legacy M-0.1 §3.7 simple shape


class LocalIntentMapping(BaseModel):
    """local_to_event_id_mapping[] per M-0.5 §6.5 — writer fills."""

    model_config = _STRICT

    local_intent_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)


class AuditChainItem(BaseModel):
    """audit_chain[] per M-0.5 §6.5."""

    model_config = _STRICT

    audit_triggered_event_id: str = Field(min_length=1)
    audit_verdict_event_id: str = Field(min_length=1)
    overall_outcome: str = Field(min_length=1)


class RejectionReasonItem(BaseModel):
    """rejection_reasons[] per M-0.5 §6.5."""

    model_config = _STRICT

    check_type: str = Field(min_length=1)
    failure_reason: str
    evidence: dict[str, object] = Field(default_factory=dict)


class CommitAcceptedPayload(BaseModel):  # Patch M-0.5a-4 + M-0.5a-6 + E.1.1 Conflict 4/6/7
    """M-0.5 §6.5 CommitAccepted detailed payload + E.1.1 Conflict 4/6/7 强化字段.

    `envelope_event_id` references the path-B envelope event (see LEDGER Conflict 4/6/7).
    `gate_run_id` uniquely identifies this commit gate run. `accepted_domain_event_count`
    lets projection know how many domain events the accepted batch contains without
    counting batch entries.
    """

    model_config = _STRICT

    envelope_event_id: str = Field(min_length=1)
    capsule_compiled_event_id: str = Field(min_length=1)
    verdict: Literal[CommitVerdict.ACCEPTED] = CommitVerdict.ACCEPTED
    gate_run_id: str = Field(min_length=1)
    accepted_domain_event_count: int = Field(ge=0)
    local_to_event_id_mapping: list[LocalIntentMapping] = Field(default_factory=list)
    checks_passed: list[CheckPerformed] = Field(default_factory=list)
    audit_chain: list[AuditChainItem] = Field(default_factory=list)
    informational_notices: list[str] | None = None


class CommitRejectedPayload(BaseModel):  # Patch M-0.5a-4 + M-0.5a-6 + E.1.1 Conflict 4/6/7
    """M-0.5 §6.5 CommitRejected detailed payload + E.1.1 Conflict 4/6/7 强化字段.

    `envelope_event_id` references the path-B envelope. `gate_run_id` uniquely identifies
    this commit gate run. `recovery_of_abandoned=True` iff this rejection was produced by
    scan_abandoned recovery (vs normal commit gate rejection).

    rebase_allowed=true ↔ rejection_type ∈ {version_conflict, write_conflict} — both are stale-
    baseline conflicts the caller can rebase + retry (M-0.5 §3.2 / §7.4). Other rejection_types
    (obligation_violation / novelty_missing / claim_boundary_exceeded / drift_detected /
    audit_failed) are not rebase-retryable. (RUN-029: was version_conflict-only before the
    enum split into version/write conflict.)
    """

    model_config = _STRICT

    envelope_event_id: str = Field(min_length=1)
    capsule_compiled_event_id: str = Field(min_length=1)
    verdict: Literal[CommitVerdict.REJECTED] = CommitVerdict.REJECTED
    rejection_type: RejectionType
    gate_run_id: str = Field(min_length=1)
    recovery_of_abandoned: bool = False
    rejection_reasons: list[RejectionReasonItem] = Field(min_length=1)
    rejected_local_intent_ids: list[str] = Field(default_factory=list)
    detected_violation_event_refs: list[str] = Field(default_factory=list)
    drift_detected_event_ref: str | None = None
    audit_chain: list[AuditChainItem] = Field(default_factory=list)
    lock_release_event_ref: str | None = None
    rebase_allowed: bool


class EntityRefShort(BaseModel):
    """drift_nodes[] / original_touched_nodes[] per M-0.1 §3.7."""

    model_config = _STRICT

    entity_type: SubjectEntityType
    entity_id: str = Field(min_length=1)


class DriftDetectedPayload(BaseModel):
    """M-0.1 §3.7 DriftDetected — path B direct write, drift_nodes / original_touched_nodes diff."""

    model_config = _STRICT

    envelope_event_id: str = Field(min_length=1)
    drift_nodes: list[EntityRefShort] = Field(min_length=1)
    original_touched_nodes: list[EntityRefShort] = Field(default_factory=list)
    original_assembly_event_id: str = Field(min_length=1)


class OwnerGuardViolationPayload(BaseModel):
    """M-3.1 §5.3 V-01 owner-guard violation (RUN-031 T-L3kc-03) — path B guard observation.

    Emitted when a write to a task worktree's file is attempted by an actor that does not own
    the task, or for a file outside the task's declared write_set.
    """

    model_config = _STRICT

    task_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    attempted_by_actor_id: str = Field(min_length=1)
    owner_actor_id: str | None = None  # the task's rightful owner (None if no .owner file)
    reason: str = Field(min_length=1)  # "not_in_write_set" / "no_owner_file"


class GuardAdminBypassGrantedPayload(BaseModel):
    """T-FIX-B4-05 — admin-bypass 旗标受控产生 (path B guard observation, like OwnerGuardViolation).

    .towow/locks/admin-bypass.flag 是 PreToolUse 物理门的唯一出口。本事件是旗标的 canonical
    provenance: 只有 `towow guard admin-bypass --reason ... --owner-confirm` (Class A owner
    物理动作) 这一条产生路径, emit 先于旗标落地 (emit 失败 → 旗标不写, fail-closed)。
    """

    model_config = _STRICT

    reason: str = Field(min_length=1)  # owner 授权理由 (--reason, 必填)
    granted_by: str = Field(min_length=1)  # owner marker (e.g. "nature")
    flag_path: str = Field(min_length=1)  # 旗标落地路径 (含 locks/admin-bypass.flag)
    granted_at: str = Field(min_length=1)  # ISO-8601 UTC 时间戳


class GuardAdminBypassRevokedPayload(BaseModel):
    """T-FIX-B4-05 — admin-bypass 旗标撤销留痕 (删旗标在先, emit 在后 — 门先关, fail-closed)."""

    model_config = _STRICT

    revoked_by: str = Field(min_length=1)
    flag_path: str = Field(min_length=1)
    revoked_at: str = Field(min_length=1)  # ISO-8601 UTC 时间戳
    grant_reason: str | None = None  # 被撤销旗标里的授权理由 (旗标内容不可解析时 None)


class OwnerConfirmationGrantedPayload(BaseModel):
    """owner-confirm@v1 — 不可伪造 owner 授权的 canonical 载体 (组件6, path B guard observation).

    与 GuardAdminBypass 同族但【硬于】它: admin-bypass 的 --owner-confirm 只是布尔 flag (任何 CLI
    调用方都能传, 物理没验任何东西); 本事件带的 signature 是 owner 用私钥对【本次动作的确定性挑战】
    做的 Ed25519 detached 签名, 由 closure_gate.owner_confirm 模块的钉死公钥验证 —— agent emit 得出
    事件、造不出有效签名 (除非拿到 agent 信封外的私钥)。挑战 = canonical_challenge(red_line_class,
    task_id, finding_id, action_fingerprint, nonce) 逐字节复算, 门只信验签结果, 不信 payload 自报。

    唯一受控产生路径 = `towow owner-confirm grant` (先验签再 emit, 验不过不留痕, fail-closed)。
    消费方两处: retire 闭合门 (task_id+finding_id 锚定) + 4 红线物理门 (action_fingerprint 锚定)。
    """

    model_config = _STRICT

    # red_line_class: 本授权只对这一类动作有效 (retire_task / delete_data / outbound_publish /
    # funds / public_commitment)。挑战第一字段 —— owner 对 retire 的签名换不到 delete_data。
    red_line_class: str = Field(min_length=1)
    # retire 锚 (task_id + finding_id) 与 4 红线锚 (action_fingerprint) 互斥填 —— 未用的那组为 None,
    # 挑战复算时按 None 参与 (签/验两侧一致)。
    task_id: str | None = None
    finding_id: str | None = None
    action_fingerprint: str | None = None
    nonce: str = Field(min_length=1)  # 防重放 freshness; 与挑战身份字段一起绑死, 跨目标复用即验签失败
    signature: str = Field(min_length=1)  # Ed25519 detached 签名 (hex, owner 私钥对挑战签)
    key_id: str = Field(min_length=1)  # 用哪把钉死公钥验 (轮换/多锚清晰度)
    granted_by: str = Field(min_length=1)  # owner marker (e.g. "nature")
    granted_at: str = Field(min_length=1)  # ISO-8601 UTC 时间戳


class IrreversibleActionBlockedPayload(BaseModel):
    """A6 哨兵正经源 (irreversible-action-blocked-audit-event@v1) — path B guard observation.

    物理闸 (PhysicalGate, layer ④) DENY 一个高危不可逆动作时 best-effort emit, 上看板。
    DENY 是 fail-closed 主动作; 本事件只观测, emit 失败绝不回退放行 (INV-SENT-EMIT-ONLY)。
    per-instance (INV-SENT-A6-PER-INSTANCE): 同 risk_class 不同 command_or_payload 必产不同事件
    — command_or_payload + write_set 是 per-instance 签名主载体, 故不 dedup/collapse。

    risk_class 用 inline Literal (不从 awareness.HighRiskClass import — schemas 是基础层,
    反向依赖 awareness 会倒置依赖、registry import 时埋环), emit 侧传 verdict.risk_class.value。
    """

    model_config = _STRICT

    risk_class: Literal[
        "prod_deploy", "outbound_publish", "funds", "delete_data", "public_commitment"
    ]
    decision: Literal["deny"] = "deny"  # 恒 deny — 本事件只在 DENY 路径产生
    tool_name: str = Field(min_length=1)
    command_or_payload: str  # 被拦命令原文 (per-instance 签名主载体; 不加 min_length 以免 valid block 不可 emit)
    write_set: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    blocked_at: str = Field(min_length=1)  # ISO-8601 UTC 时间戳


__all__ = [
    "AuditChainItem",
    "CheckPerformed",
    "CommitAcceptedPayload",
    "CommitRejectedPayload",
    "DriftDetectedPayload",
    "EntityRefShort",
    "GuardAdminBypassGrantedPayload",
    "GuardAdminBypassRevokedPayload",
    "IrreversibleActionBlockedPayload",
    "LocalIntentMapping",
    "OwnerGuardViolationPayload",
    "RejectionReasonItem",
]
