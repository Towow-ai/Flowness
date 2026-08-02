"""debt lifecycle event payload schemas (RUN-029 第0波 ③ — system self-debt tracking).

# spec source:
#   docs/E-DEBT-PAYOFF-ROADMAP.md — the debt the system carries silently
#   RUN-029 brief (events.log brief-1e96066a): "每处 stub/deferral 产生即 emit 可追踪债条目
#     (新 DebtRegistered 类)" — a genuine design gap the brief sanctioned adding.
#
# Why these events exist:
#   The root failure RUN-029 fixes is 假done — "完成" marked over a stub. The cure is to make
#   every consciously-incomplete spot (stub / deferral / blocked dependency / spec conflict)
#   produce a tracked, first-class debt entry instead of a silent gap. The debt_registry
#   projection then derives an honest "what is actually done vs owes debt" ledger — the
#   升级形态 of phase-status (machine-derived from real events, not hand-maintained).
#
# Category: reuses semantic_judgment (the system judging a capability incomplete) — no new
# EventCategory, to keep projection-routing blast radius minimal. Written path B (like
# DriftDetected / AuditTriggered — a gate/skill-internal observation, immediately audit-visible).
#
# T-REFLOW-DEBT (functional-equivalence-closure-criterion@v1): DebtResolved alone used to carry
# only a free-text resolution_evidence — no evidence chain, no independent verdict, so a debt
# could be "resolved" on a bare merge-base ancestry claim (the debt-7d46343941ad反面案例 this
# task corrects). DebtResolvedPayload now carries the same evidence-chain shape the closure gate
# demands: evidence_type (functional-equivalence-evidence-tier@v1 EvidenceReasonCode — never NS1,
# merge-base-only is a non_signal), evidence_ref_type/evidence_ref_id (what the evidence points
# at — commit/finding/test/ledger_event), verification_verdict_ref (hard-required — an
# independent, affirmative verdict: AuditVerdictReceived pass/conditional_pass or FindingVerified
# verified), and agent_verdict_ref (soft — an optional companion pointer to an ad hoc agent
# investigation record, not independently re-validated by the gate). The write-boundary gate
# (EventLog.write_debt_resolved) enforces resolvability + independence (actor != closer/fixer)
# before this payload ever reaches disk — see l0/event_log/event_log.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.l1.functional_equivalence_evidence_tier import EvidenceReasonCode
from towow.schemas.enums import ActivationGapType, DebtSeverity, DebtState, DebtType

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class DebtRegisteredPayload(BaseModel):
    """DebtRegistered payload — one tracked incompleteness the system is owning out loud."""

    model_config = _STRICT

    debt_id: str = Field(min_length=1)
    debt_type: DebtType
    severity: DebtSeverity
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # the capability / check / concept this debt is against — what is NOT fully done.
    # free-form ref (e.g. "semantic-gate-real@v1:ScopeDrift", "M-0.5:audit-fork").
    against_capability: str = Field(min_length=1)
    # what would unblock / what this waits on (e.g. ["第1波-capsule-content-accessible"]).
    depends_on: list[str] = Field(default_factory=list)
    # what objectively makes this debt resolvable (the closure criterion).
    resolution_criteria: str = Field(min_length=1)
    # provenance: the task / event that incurred the debt.
    registered_by_task: str | None = None
    registered_by_event_id: str | None = None
    # ── activation-debt@v1 载体字段 ──────────────────────────────────────────────────────
    # 全部 Optional(default None) 保【历史重放安全】: pre-activation-debt 的存量 DebtRegistered
    # (debt_type ∈ {stub/deferral/...}, 无这 4 键)必须继续通过 typed 校验重放 —— 字段 Optional
    # 即满足, 镜像同文件 DebtResolvedPayload 的重放安全惯例。
    #
    # 为何用 pydantic model_validator (而非 DebtResolved 那样的写边界门): DebtResolved 的证据链是
    # 每条历史 DebtResolved 都缺的必填, schema 层强制会炸断全部重放, 故只能放写边界"只在新写入跑";
    # 而 activation 的 expiry+gap_type 要求只在 debt_type==activation 时成立 —— activation 是全新
    # 枚举值, 无任何历史事件携带, validator 的 activation 分支在每条既有事件上不可达 → 重放安全, 且
    # 这是纯 intra-payload 约束(不需查账本), pydantic validator 比写边界更贴切。下个读者别"改回"写边界。
    expiry: str | None = None  # 到期时限, 绝对时间 ISO str (真空期 vs 到期失败的分界锚)
    gap_type: ActivationGapType | None = None  # 引 activation-gap-type-taxonomy@v1 的 A-G
    state: DebtState | None = None  # 登记时的初始状态 (activation 默认 awaiting_organic_traffic)
    root_cause_key: str | None = None  # 存量按共同根因合并用的归并键

    @model_validator(mode="after")
    def _activation_requires_expiry_and_gap_type(self) -> DebtRegisteredPayload:
        """activation-debt@v1: debt_type==activation 时必带 expiry+gap_type。

        非 activation 债(历史与新写)不受约束 —— 分支不可达, 保后向重放。
        """
        if self.debt_type is DebtType.ACTIVATION and (self.expiry is None or self.gap_type is None):
            raise ValueError(
                "activation debt requires both `expiry` and `gap_type` "
                f"(got expiry={self.expiry!r}, gap_type={self.gap_type!r})"
            )
        return self


class DebtResolvedPayload(BaseModel):
    """DebtResolved payload — a previously-registered debt has been genuinely paid off.

    functional-equivalence-closure-criterion@v1: closure is only accepted when it carries a
    fail-closed evidence chain — evidence_type must never be EvidenceReasonCode.NS1 (merge-base
    ancestry alone is a non_signal, never valid closing evidence), and verification_verdict_ref
    must resolve to a real, affirmative, independent (actor != closer/fixer) verdict. Both
    invariants are enforced at the write boundary (EventLog.write_debt_resolved), not here —
    this schema only pins the required shape.
    """

    model_config = _STRICT

    debt_id: str = Field(min_length=1)
    resolution_evidence: str = Field(min_length=1)
    # ── T-REFLOW-DEBT evidence-chain fields ──────────────────────────────────────────────
    # Schema 层全部 Optional(default None) 保【历史重放安全】: pre-T-REFLOW-DEBT 的存量
    # DebtResolved(仅 debt_id+resolution_evidence)必须能继续通过 typed 校验重放, 否则
    # committed_index/读路径在历史事件上 ValidationError 中断(集成实撞: LEDGER 对拍读 live
    # 账本被历史 DebtResolved 炸断, stub 记录丢失)。新写入的 fail-closed 必填强制在写边界
    # EventLog.write_debt_resolved(缺任一字段 raise, 9 处覆盖)——镜像 package machine_check
    # 的既有惯例"schema 层默认 None 保重放, 门只在新发布跑"。
    evidence_type: EvidenceReasonCode | None = None
    # what evidence_ref_id names: "commit" | "finding" | "test" | "ledger_event".
    evidence_ref_type: str | None = None
    # the concrete reference (commit_sha / finding_id / test_id / event_id) evidence_ref_type names.
    evidence_ref_id: str | None = None
    # hard-required for NEW writes (write-boundary enforced): an independent, affirmative verdict
    # event_id (AuditVerdictReceived pass/conditional_pass or FindingVerified verified) —
    # resolvability + independence enforced at the write boundary.
    verification_verdict_ref: str | None = None
    # soft companion: an optional pointer to an ad hoc agent-investigation record (not
    # independently re-validated by the gate — informational traceability only).
    agent_verdict_ref: str | None = None
    resolved_by_event_id: str | None = None


__all__ = ["DebtRegisteredPayload", "DebtResolvedPayload"]
