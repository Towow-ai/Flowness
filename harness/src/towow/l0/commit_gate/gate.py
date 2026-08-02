"""M-0.5 Commit Gate — bootstrap_commit / attempt_commit / scan_abandoned (E.1.1-canonical).

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#   §2 commit attempt state machine
#   §3 6 check types (E.1 minimal: schema + envelope_integrity + novelty)
#   §3.5.1 NoveltyCheck (Patch M-2.1-D: skip for InvalidationCascade)
#   §5.4 submit wrapper driver loop
#   §6 batch assembler (CommitAccepted / CommitRejected sentinels)
#   §7 abandoned envelope scan + LockReleased
#
# E.1.1 LEDGER Conflict 4/6/7 (resolved):
#   - envelope ALWAYS path B (write_direct), batch_id=None, immediately audit-visible
#   - accepted batch = [domain events..., CommitAccepted] (no envelope in batch)
#   - rejected batch = [gate-lifecycle..., CommitRejected] (no envelope)
#   - recovery batch = [LockReleased, CommitRejected(recovery_of_abandoned=true)]
#     (no synthetic envelope)
#   - scan_abandoned CAS protocol: acquire mutex → recheck outcomes → produce or no-op
#   - CommitAccepted/Rejected payload 强化 (envelope_event_id + gate_run_id + ...)
#
# Phase E.1 simplifications (Plan §3 step 6):
#   - Bootstrap special path skips ObligationCheck / DriftCheck / ClaimsBoundary / WriteConflict
#
# RUN-029 第0波 ① — the brief diagnosed RUN-027 block-3's ✅ as itself fake (4 of 6 checks
# were schema-sanity, audit was mock). Status after RUN-029:
#   - §3.1 VersionConsistency: REAL (semantic_checks.check_version_consistency — forward
#     supersede-index lookup; stub capsule ref → skip+notice, a tracked debt signal).
#   - §3.2 WriteConflict: REAL (semantic_checks.check_write_conflict — entity-index scan
#     after as_of_projection_seq; no-baseline → skip+notice).
#   - §3.3 DriftCheck / §3.4 ObligationCheck: FreshnessDrift + obligation maintenance REAL;
#     ScopeDrift + obligation coverage are blocked by the capsule-stub gap (CapsuleCompiled
#     stores summary counts only, M-0.1 §3.5 vs M-0.5 §3.3.1) → DebtRegistered, not silent
#     stub. See drift_checks.py / obligation_checks.py.
#   - §5 Audit: gate-side trigger + verdict integration REAL; the LLM fork execution is the
#     M-3.1 submit-wrapper boundary → DebtRegistered. See audit.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from towow.l0.commit_gate.activation_acceptance_check import (
    check_activation_acceptance,
)
from towow.l0.commit_gate.audit import (
    AuditSamplingConfig,
    AuditTrigger,
    SampleDecision,
    audit_scope_summary,
    detect_audit_triggers,
    headline_reason,
    resolve_default_audit_sampler,
)
from towow.l0.commit_gate.closure_evidence_check import (
    check_closure_evidence_verification,
)
from towow.l0.commit_gate.completeness import check_completeness_requirements
from towow.l0.commit_gate.completion_checks import check_completion_claims
from towow.l0.commit_gate.concept_create_gate import (
    _extract_new_concepts,
    check_concept_create_relatedness,
    existing_concepts_from_records,
)
from towow.l0.commit_gate.concept_retire_gate import check_concept_retire_migration_for_gate
from towow.l0.commit_gate.consolidation_check import check_consolidation_invariants
from towow.l0.commit_gate.drift_checks import (
    check_freshness_drift,
    check_scope_drift,
    freshness_relevant_subjects,
    scope_touched_from_payload,
)
from towow.l0.commit_gate.fencing_check import check_fencing_token
from towow.l0.commit_gate.finding_birth_gate import check_finding_routability
from towow.l0.commit_gate.finding_classification_consistency import (
    check_finding_classification_consistency,
)
from towow.l0.commit_gate.graph_integrity_check import check_graph_edge_integrity
from towow.l0.commit_gate.live_target_reconcile_check import (
    check_live_target_reconciled,
)
from towow.l0.commit_gate.obligation_checks import (
    ObligationViolation,
    check_obligation_capture_provenance,
    check_obligations,
)
from towow.l0.commit_gate.review_verdict_check import (
    check_review_verdict_gated_completion,
)
from towow.l0.commit_gate.semantic_checks import (
    check_ownership_conflict,
    check_version_consistency,
    check_write_conflict,
)
from towow.l0.commit_gate.view_refresh import auto_refresh_views_after_accept
from towow.l0.envelope.builder import (
    backfill_envelope_event_id_refs,
    backfill_patch_detail_refs,
    backfill_semantic_sibling_refs,
    canonicalize_envelope_payload,
    derive_as_of_projection_seq,
    derive_capsule_compiled_event_id,
    derive_read_set,
    derive_write_set,
)
from towow.l0.envelope.checks import check_claims_boundary, check_envelope_integrity
from towow.l0.event_log.event_log import build_accepted_batch_e11, build_rejected_batch_e11
from towow.l0.event_log.index import EventIndex
from towow.l0.obligations.bootstrap import check_retire_allowed
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    JudgmentType,
    ObligationCheckMethod,
    RejectionType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


class CommitGateError(Exception):
    """Generic commit gate failure."""


@dataclass(frozen=True)
class _DriftInfo:
    """§3.3 Drift detection carried out of _run_checks so attempt_commit can write the
    DriftDetected event (path B) BEFORE the rejected batch, then link it from the sentinel.

    drift_type = "freshness" (§3.3.2) or "scope" (§3.3.1) — set on the DriftDetected payload.
    drift_nodes = for freshness, the invalidated SUBSET (relevant subjects that had an
    invalidating event); for scope, the touched concepts that escaped the capsule scope.
    relevant_subjects = the full original scope (read_set ∪ write_set proxy for freshness;
    capsule.touched_nodes for scope). DriftDetected.original_touched_nodes carries the latter
    so L1 can diff "full scope" vs "what drifted" (RUN-030 review IMPORTANT-4: the two were填成同值).
    invalidating_event_ids = freshness-only evidence (empty for scope).
    """

    drift_nodes: tuple[tuple[str, str], ...]
    relevant_subjects: tuple[tuple[str, str], ...]
    invalidating_event_ids: tuple[str, ...]
    capsule_compiled_event_id: str
    drift_type: str = "freshness"


@dataclass(frozen=True)
class _CheckOutcome:
    """Result of the gate check pipeline (block-1 boundary checks + existing checks).

    rejection_type None → accepted; notices carry informational signals (claims_absent,
    gate-derived boundaries) into CommitAccepted.informational_notices (M-0.5 Patch 4).
    """

    rejection_type: RejectionType | None = None
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    # §3.4.3 — detected obligation violations → ObligationViolated events in the rejected batch.
    obligation_violations: list[ObligationViolation] = field(default_factory=list)
    # §3.3.2 — set iff FreshnessDrift rejected; drives the path-B DriftDetected write.
    drift_detected: _DriftInfo | None = None
    # RUN-040 F-RA-1 — the checks _run_checks ACTUALLY evaluated this commit + each result, in
    # §3.7 run order. On the accept path this是 CommitAccepted.checks_passed 的真实来源 (取代写死
    # 单条 envelope_integrity 占位): 反映本次真跑过哪些 check ("passed" / "not_applicable"), 不
    # 是与实跑无关的硬编码。Only populated on the success fall-through (a rejection short-circuits
    # before the full list is built — rejection 走 rejection_reasons, 不用 checks_passed)。
    checks_performed: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class PendingAudit:
    """RUN-051 (M-0.5 §5.2) — state carried between attempt_commit (two-phase) and continue_attempt.

    The envelope is already on path B (written before checks); the domain events are stamped but
    NOT yet appended (no sentinel). If the process crashes between attempt and continue, the
    envelope has no outcome → scan_abandoned recovers it (CommitRejected recovery_of_abandoned).
    继续提交不经 log 重建 domain intents (它们只在 caller 手里), 故 continue_attempt 拿这个 pending
    而非 spec 字面的裸 envelope_event_id —— envelope_event_id 仍可由 envelope_record 取到。
    """

    envelope_record: EventRecord
    domain_records: list[EventRecord]
    domain_intents: list[EventIntent]
    gate_run_id: str
    transaction_id: str
    batch_id: str
    outcome: _CheckOutcome
    payload: dict[str, object]
    audit_triggered_event_id: str
    audit_subjects: tuple[tuple[str, str], ...]  # (subject_type, target_ref) per triggered subject
    scope_hint: str  # serialized audit_scope (谁/为什么触发) — §5.3 audit_scope summary
    trigger_reason: str  # headline trigger_reason value

    @property
    def envelope_event_id(self) -> str:
        return self.envelope_record.event_id


@dataclass(frozen=True)
class CommitAttemptResult:
    """RUN-051 — two-phase commit attempt outcome (returned when audit_blocking=True).

    status:
      - "accepted"       → records = [envelope, ..., CommitAccepted]
      - "rejected"       → records = [envelope, ..., CommitRejected]
      - "wait_for_audit" → pending set; submit driver runs the audit fork + continue_attempt
                           (§5.2 synchronous blocking flow). DOES NOT finalize accept yet.

    audit_blocking=False keeps attempt_commit returning a plain list[EventRecord] (backward compat:
    audit fires留痕 but does NOT block — the pre-RUN-051 behavior all existing callers rely on).
    """

    status: str
    records: list[EventRecord] = field(default_factory=list)
    pending: PendingAudit | None = None

    @property
    def sentinel(self) -> EventRecord | None:
        return self.records[-1] if self.records else None


def _obligation_match_content(payload: dict[str, object]) -> str:
    """RUN-068 件C — build the matchable text for §3.4.2 forbidden_pattern matching.

    Joins the envelope's patch summaries + any inline content/detail text into one string the
    physical matcher scans. The gate operates on the committed event log (not the working tree),
    so this is the content actually present in the envelope — patch summaries + declared text.
    A forbidden_pattern that hits this content contradicts a `maintained` declaration.
    """
    parts: list[str] = []
    patches = payload.get("patches")
    if isinstance(patches, list):
        for p in patches:
            if not isinstance(p, dict):
                continue
            for key in ("summary", "content", "detail", "after_content", "description"):
                v = p.get(key)
                if isinstance(v, str) and v:
                    parts.append(v)
    for key in ("change_summary", "content", "diff", "definition"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    return "\n".join(parts)


class CommitGate:
    """Minimal M-0.5 commit gate implementation.

    Provides three public APIs:
      - bootstrap_commit(intents): special path for `towow init` — writes obligations
        as a single accepted batch with envelope + N obligations + CommitAccepted.
        Skips ObligationCheck / DriftCheck / ClaimsBoundary / WriteConflict per Plan §3
        step 6 + §8 open issues (surfaced as narrow patch candidate).
      - attempt_commit(envelope_intent, domain_intents): normal path — runs schema +
        envelope_integrity + novelty checks (Patch M-2.1-D skip for InvalidationCascade).
      - scan_abandoned(): scan in-flight envelopes past lock_timeout → produce
        LockReleased + CommitRejected per M-0.5 §7.

    All event writes go through the provided EventLog (Path A via append_transaction_batch).
    """

    def __init__(
        self,
        event_log: EventLog,
        *,
        audit_sampler: SampleDecision | None = None,
        audit_config: AuditSamplingConfig | None = None,
        prewarm_committed_index: bool = True,
    ) -> None:
        self._event_log = event_log
        # B-1 (最小止血, bootstrap 修复包 — owner paused.flag 2026-07-14: "锁临界区仍在当前 60.7
        # 万行账本上发生锁内全量 hydrate"): attempt_commit() 里 committed_index() 那次调用(§3.7/
        # gate.py ~L594)本身只做增量 catch-up(EventLog._catch_up_index, 有界于 active segment)——
        # 真正贵的是 EventIndex *第一次* 物化(EventLog._committed_index 冷启动分支: 全量
        # _iter_committed_records() + 逐条 model_validate_json, RUN-038 记录的真实数字 ~29s/26.7 万
        # 事件)。调用方几乎全部已经在 acquire commit lock 之前构造 CommitGate(仅 submit() /
        # MigrationEngine.apply_batch / inv_e_refreeze._commit_domain_intent 是例外, 三处已同步改为
        # 锁外构造) —— 但构造 CommitGate 本身此前不触碰 event_log, 那次"贵"的物化仍然被推迟到锁内
        # attempt_commit 的 committed_index() 首次调用才发生, 构造顺序对了也没用。这行把物化提前到
        # 构造 CommitGate 的时刻(锁外), 让锁内那次 committed_index() 调用退化成 O(active-segment) 的
        # 增量 catch-up。正确性不变: 锁内那次调用仍然真读一次磁盘现状(_catch_up_index 比较 active
        # segment 字节数, 有新内容就真的 fold 进来), 判定基于的仍是持锁那一刻的账本, 不是过期快照
        # ——只是历史部分不用再重新扫一遍。bootstrap_commit-only 用法(如 `towow init`)会顺带被物化,
        # 但发生在初始化时的空/小账本上, 代价可忽略。prewarm_committed_index=False 留给需要精确复现
        # "冷 index"行为的场景(如本任务自带的 B-1 lock-hold 性能对照测试)。
        if prewarm_committed_index:
            self._event_log.committed_index()
        # §5.1 audit sampling. RUN-053 (owner decision "开着才能测试"): production defaults the
        # probabilistic sampler ON — `audit_sampler=None` resolves `random_audit_sampler()` via
        # resolve_default_audit_sampler() (env `TOWOW_AUDIT_SAMPLER=off` opts back to never-sample
        # for CI/dev reproducibility; tests/conftest.py sets it suite-wide). An explicitly-passed
        # sampler (real RNG / forcing / never) always wins. The 100% triggers (self_check failure /
        # oscillation) fire regardless of the sampler.
        self._audit_sampler = audit_sampler if audit_sampler is not None else resolve_default_audit_sampler()
        self._audit_config = audit_config
        # T-L0-30 (M-0.5 §4.1 TOCTOU): a test-only hook fired inside attempt_commit, in the
        # check→write window, to deterministically simulate a concurrent path-B write landing
        # there (the multi-process race the §4.1 re-check guards). None in production — the real
        # window is only non-empty under the §4.3 multi-process future, out of v3 scope.
        self._toctou_pre_write_hook: Callable[[], None] | None = None

    # ─── bootstrap special path (Plan §3 step 6 + §8 open issues) ──────────

    def bootstrap_commit(self, obligation_intents: list[EventIntent]) -> list[EventRecord]:
        """Bootstrap path — only EnvelopeIntegrity + Schema + BatchAtomicity checks.

        E.1.1-canonical: envelope walks path B; batch = [obligation_captures, CommitAccepted].

        Used exclusively by `towow init` (init=True flag at call site).
        Returns the appended EventRecord list (envelope + domain + sentinel).
        """
        gate_run_id = f"gr-bootstrap-{uuid.uuid4().hex[:12]}"
        transaction_id = f"tx-bootstrap-{uuid.uuid4().hex[:12]}"
        batch_id = f"b-bootstrap-{uuid.uuid4().hex[:12]}"

        # Envelope path B — immediately audit-visible (E.1.1 Conflict 4/6/7).
        # T-L0-24/25: bootstrap envelope is canonical too, so read_envelope reconstructs it.
        envelope_intent = self._build_bootstrap_envelope_intent(obligation_intents)
        bp = envelope_intent.provenance_hint
        envelope_intent = envelope_intent.model_copy(
            update={
                "payload": canonicalize_envelope_payload(
                    dict(envelope_intent.payload),
                    obligation_intents,
                    fallback_session_ref=envelope_intent.local_intent_id,
                    prov_task_id=bp.task_id,
                    prov_run_id=bp.run_id,
                    prov_fork_id=bp.fork_name or bp.actor_id,
                ),
            },
        )
        envelope_record = self._event_log.write_direct(envelope_intent)

        # Domain events stamped for path-A batch (envelope NOT in batch)
        domain_records: list[EventRecord] = []
        for idx, intent in enumerate(obligation_intents):
            rec = self._event_log.stamp_for_batch(
                intent,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=idx,
            )
            domain_records.append(rec)

        sentinel_intent = self._build_commit_accepted_intent(
            envelope_event_id=envelope_record.event_id,
            capsule_compiled_event_id="evt-bootstrap-no-capsule",
            gate_run_id=gate_run_id,
            accepted_domain_event_count=len(domain_records),
            local_to_event_id_mapping=[
                {"local_intent_id": i.local_intent_id, "event_id": d.event_id}
                for i, d in zip(obligation_intents, domain_records, strict=True)
            ],
            # Bootstrap special path runs EnvelopeIntegrity + Schema + BatchAtomicity only
            # (Plan §3 step 6 + §8) — record those truthfully, not the normal-path check set.
            checks_passed=[
                {"check_type": "envelope_integrity", "result": "passed"},
                {"check_type": "schema", "result": "passed"},
                {"check_type": "batch_atomicity", "result": "passed"},
            ],
            informational_notices=["bootstrap_commit special path (Plan §3 step 6)"],
        )
        sentinel_record = self._event_log.stamp_for_batch(
            sentinel_intent,
            transaction_id=transaction_id,
            batch_id=batch_id,
            batch_position=len(obligation_intents),
        )

        batch = build_accepted_batch_e11(
            batch_id,
            transaction_id,
            domain_records,
            sentinel_record,
        )
        written = self._event_log.append_transaction_batch(batch)
        # written carries the authoritative (file-lock-assigned) seqs + hashes (F-019-12);
        # envelope_record is already authoritative (path B write_direct).
        # M-3.2 §7.1 Auto-refresh (同 _finalize_accept): bootstrap batch 落地后失效受影响 view
        # (ObligationCaptured → obligations/* + dashboard)。fail-safe, 不连累 commit。
        auto_refresh_views_after_accept(self._event_log, written)
        return [envelope_record, *written]

    @staticmethod
    def _build_bootstrap_envelope_intent(
        obligation_intents: list[EventIntent],
    ) -> EventIntent:
        return EventIntent(
            local_intent_id=f"env-bootstrap-{uuid.uuid4().hex[:12]}",
            event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
            event_category=EventCategory.ENVELOPE,
            payload={
                "envelope_kind": "bootstrap",
                "active_obligations_declared": [],
                "patches": [
                    {
                        "patch_id": f"p-{intent.local_intent_id}",
                        "patch_type": "obligation_event",
                        "target": intent.payload.get("obligation_id", ""),
                        "summary": f"bootstrap capture obligation {intent.payload.get('obligation_id')}",
                    }
                    for intent in obligation_intents
                ],
                "uncertainties": [],
                "self_check": {"passed": True, "checks_run": ["bootstrap_special_path"]},
                "capsule_compiled_event_id": "evt-bootstrap-no-capsule",
                "as_of_projection_seq": 0,
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.SYSTEM.value,
                actor_id="m05_bootstrap_commit",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.OBLIGATION,
                    entity_id=str(intent.payload.get("obligation_id", "")),
                    role=SubjectRole.PRIMARY,
                )
                for intent in obligation_intents
            ],
            schema_version="1.0.0",
        )

    @staticmethod
    def _build_commit_accepted_intent(
        *,
        envelope_event_id: str,
        capsule_compiled_event_id: str,
        gate_run_id: str,
        accepted_domain_event_count: int,
        local_to_event_id_mapping: list[dict[str, str]],
        checks_passed: list[dict[str, str]],
        informational_notices: list[str] | None = None,
        audit_chain: list[dict[str, str]] | None = None,
    ) -> EventIntent:
        """E.1.1 Conflict 4/6/7: payload 强化 envelope_event_id / gate_run_id /
        accepted_domain_event_count.

        RUN-040 F-RA-1: `checks_passed` 由调用方传入本次真跑过哪些 check (normal path =
        _run_checks 累积; bootstrap = 其 special-path 检查集), 不再写死单条占位。
        RUN-051: `audit_chain` 由 continue_attempt 在 §5.5 pass/conditional_pass 时传入
        (AuditChainItem 引 audit_triggered + audit_verdict 事件), 非两阶段路径仍为 []。
        """
        return EventIntent(
            local_intent_id=f"ca-{uuid.uuid4().hex[:12]}",
            event_type=EventType.COMMIT_ACCEPTED,
            event_category=EventCategory.COMMIT,
            payload={
                "envelope_event_id": envelope_event_id,
                "capsule_compiled_event_id": capsule_compiled_event_id,
                "verdict": "accepted",
                "gate_run_id": gate_run_id,
                "accepted_domain_event_count": accepted_domain_event_count,
                "local_to_event_id_mapping": local_to_event_id_mapping,
                "checks_passed": checks_passed,
                "audit_chain": audit_chain or [],
                "informational_notices": informational_notices or [],
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id="commit_gate",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )

    # ─── normal path attempt_commit ────────────────────────────────────────

    def attempt_commit(
        self,
        envelope_intent: EventIntent,
        domain_intents: list[EventIntent],
        *,
        audit_blocking: bool = False,
    ) -> list[EventRecord] | CommitAttemptResult:
        """E.1.1-canonical commit attempt.

        Protocol (per LEDGER Conflict 4/6/7):
          1. envelope path B write → immediately audit-visible
          2. run checks (envelope_integrity / novelty / Patch M-2.1-D InvalidationCascade skip)
          3. if pass → accepted batch [domain..., CommitAccepted(envelope_event_id=...)]
             if fail → rejected batch [gate-lifecycle..., CommitRejected(envelope_event_id=...)]

        Envelope is NEVER in batch. Envelope ↔ outcome correlation via sentinel payload
        envelope_event_id field.

        audit_blocking (RUN-051, M-0.5 §5.2):
          - False (default): unchanged pre-RUN-051 behavior — audit triggers emit AuditTriggered
            留痕 but DO NOT block; returns list[EventRecord]. All existing callers rely on this.
          - True: two-phase. If a §5.1 audit trigger fires (and checks otherwise pass), DO NOT
            finalize accept — return CommitAttemptResult(status="wait_for_audit", pending=...). The
            submit driver runs the independent audit fork, writes AuditVerdictReceived, then calls
            continue_attempt to apply the §5.5 verdict回流. With no trigger / on rejection, returns
            CommitAttemptResult(status="accepted"|"rejected", records=...).
        """
        # T-L0-31 (Patch J / M-0.6 §5.3): a system_bootstrap red-line obligation is physically
        # un-retireable. Detected BEFORE any write so a retire attempt leaves no envelope behind
        # — raises ProtocolInvariantImmutabilityError (the spec's "必抛"); the obligation stays.
        self._assert_no_protocol_invariant_retire(domain_intents)

        gate_run_id = f"gr-{uuid.uuid4().hex[:12]}"
        transaction_id = f"tx-{uuid.uuid4().hex[:12]}"
        batch_id = f"b-{uuid.uuid4().hex[:12]}"

        # T-L0-24/25: assemble a stored-CANONICAL envelope (M-0.4 §3.1 submit wrapper) so the
        # persisted event is a full TransactionEnvelope read_envelope can reconstruct — not the
        # boundary-less stub the caller handed in. Derives read_set/write_set/claims + coerces
        # agent sub-objects via build_envelope. Identity fields fall back to provenance.
        #
        # STORAGE is canonical; CHECKS still run on the original (boundary-normalized) payload so
        # gate behavior is byte-for-byte unchanged — the canonical SelfCheck schema cannot
        # represent (and would mask) a passed-without-evidence blocking_check the gate must reject,
        # and claims/obligations must be checked exactly as the caller declared them.
        prov_hint = envelope_intent.provenance_hint
        raw_payload = dict(envelope_intent.payload)
        session_ref = (
            prov_hint.session_id
            or (str(raw_payload.get("session_id")) if raw_payload.get("session_id") else None)
            or envelope_intent.local_intent_id
        )

        # T-L0-27 (M-0.4 §4.3): stamp the domain events FIRST so their real (append-stable)
        # event_ids are known, then backfill them into the patches' detail_ref placeholders
        # BEFORE the envelope is canonicalized + written. stamp_for_batch assigns only a
        # provisional seq (re-sequenced at append) but the FINAL event_id — so the stored
        # envelope can point at the events actually committed in the same accepted batch.
        # CHECKS still run on the un-backfilled raw payload (placeholders), so gate behavior is
        # unchanged. On reject these stamped records are simply discarded (never written).
        domain_records = self._stamp_domain_records(domain_intents, transaction_id, batch_id)
        local_to_event_id = {
            i.local_intent_id: d.event_id for i, d in zip(domain_intents, domain_records, strict=True)
        }
        backfilled_raw = backfill_patch_detail_refs(raw_payload, local_to_event_id)

        # Cap1 (M-0.4 §3.1): system-derive capsule_compiled_event_id from the committed log
        # (matched by the run's fork) so an agent cannot forge which capsule it was given —
        # agent 不能伪造. None for forks with no CapsuleCompiled (raw value kept as fallback).
        # perf (commit-path-single-index-materialization@v1): 复用 warm committed_index 已物化的
        # records, 而非又一次全量 all_records() (逐条 model_validate_json ~29s/26.7万事件). committed_index()
        # 此处也把 self._index 建暖, 供 583 行的 gate 读复用 (那次只 catch-up delta, 不再全量重建).
        committed_records = self._event_log.committed_index().records()
        prov_fork_id = prov_hint.fork_name or prov_hint.actor_id
        derived_capsule_id = derive_capsule_compiled_event_id(committed_records, fork_id=prov_fork_id)
        # The CHECK path verifies the SYSTEM-DERIVED capsule id (the ground-truth one), not the
        # agent's raw value: a legitimate commit is not false-rejected just because the agent
        # mis-stated the id, while a fork with no real CapsuleCompiled (derived None) falls back to
        # the raw value and a genuinely fabricated ref is still rejected by check_version_consistency.
        if derived_capsule_id is not None:
            raw_payload["capsule_compiled_event_id"] = derived_capsule_id
        # Cap3 (M-0.4 §2): the CHECK path's WriteConflict / FreshnessDrift baseline is the capsule's
        # bundle.as_of_seq (system-derived), not the agent value — so a stale/forged baseline cannot
        # dodge concurrent-write detection. None (no capsule / no baseline) leaves the raw value.
        derived_as_of = derive_as_of_projection_seq(
            committed_records, capsule_compiled_event_id=derived_capsule_id,
        )
        if derived_as_of is not None:
            raw_payload["as_of_projection_seq"] = derived_as_of

        canonical_payload = canonicalize_envelope_payload(
            backfilled_raw,
            domain_intents,
            fallback_session_ref=session_ref,
            prov_task_id=prov_hint.task_id,
            prov_run_id=prov_hint.run_id,
            prov_fork_id=prov_fork_id,
            committed_records=committed_records,
        )
        canonical_intent = envelope_intent.model_copy(update={"payload": canonical_payload})

        # E.1.1: envelope path B (immediately audit-visible, batch_id=None)
        envelope_record = self._event_log.write_direct(canonical_intent)

        # Cap4 (M-0.4 §7.5 + M-0.1 §3.2): a co-submitted sibling SemanticUpgradeDeclaration names
        # its main event via after_state.sibling_event_id; the main event is the envelope, whose
        # event_id is only known now (post path-B write). Resolve the `local:envelope` placeholder
        # to the real envelope event_id so the STORED sibling links to the envelope it interprets.
        # No-op when no semantic sibling is in the batch; checks already ran on the raw payload.
        domain_records = backfill_semantic_sibling_refs(domain_records, envelope_event_id=envelope_record.event_id)

        # RUN-094 件B (M-0.4 §4.3): same shape for TaskRunCompleted.after_state.envelope_event_id —
        # execution submits the `local:envelope` placeholder, resolved here to the envelope it ran
        # under so the STORED TaskRunCompleted carries a real, resolvable execution→commit backlink
        # (was hardcoded None → 25/25 null, audit v2 §2 R4). No-op for records without the placeholder.
        domain_records = backfill_envelope_event_id_refs(domain_records, envelope_event_id=envelope_record.event_id)

        # Reuse the EventLog's warm committed-stream index (RUN-038 + T-FND-02, resolves
        # debt-e4b81accda38) instead of a fresh EventIndex(all_records()) build. The index is kept
        # WARM across writes (T-FND-02): a cold process adopts the persisted .idx and folds in only
        # the active-segment delta (incl. the path-B envelope write just above) — O(active-segment),
        # not a full O(n) rebuild. Shared by _run_checks (version/writeconflict/drift/obligation/
        # review-verdict/closure/relatedness/edge-integrity, which now read index.records() rather
        # than each re-running a full all_records() materialization) and the §5 audit-trigger
        # detection below — all read the same committed snapshot. This is the single materialization
        # per commit that commit-path-single-index-materialization@v1 pins.
        index = self._event_log.committed_index()
        # T-L0-30 (M-0.5 §4.1 TOCTOU): snapshot the committed max seq the checks run against, so
        # the write-pre re-check can detect a path-B write landing in the check→write window and
        # re-run FreshnessDrift against the new events (rather than committing on a stale check).
        current_committed_seq = self._event_log._read_max_seq_on_disk()
        # Checks read the ORIGINAL payload (normalized for boundaries only), not the canonical
        # one — see the storage/checks split rationale above.
        check_record = envelope_record.model_copy(update={"payload": raw_payload})
        payload, base_notices = self._normalize_envelope_payload(check_record, domain_intents)

        # Run checks
        outcome = self._run_checks(envelope_record, domain_intents, index, payload, base_notices)

        # §5.1 audit-trigger detection (RUN-030 T2). Always detect + emit ONE AuditTriggered (path
        # B, §3.7 batched)留痕. RUN-051: the triggers also drive the two-phase wait_for_audit signal
        # when audit_blocking — the verdict-blocking fork is no longer debt (l1/audit_fork.py).
        triggers = self._detect_audit_triggers(payload, domain_intents, index)
        audit_triggered_ref = (
            self._emit_audit_triggered(envelope_record, payload, triggers) if triggers else None
        )

        if outcome.rejection_type is not None:
            records = self._reject_from_outcome(
                envelope_record, transaction_id, batch_id, gate_run_id, outcome,
            )
            return CommitAttemptResult("rejected", records) if audit_blocking else records

        # Two-phase audit (RUN-051, M-0.5 §5.2): when audit_blocking AND a §5.1 trigger fired, DO
        # NOT finalize accept — return a wait_for_audit signal carrying the pending state. The
        # submit driver runs the independent audit fork, writes AuditVerdictReceived, then calls
        # continue_attempt to apply the §5.5 verdict回流 (pass→accept / fail→reject / cond→accept+notice).
        if audit_blocking and triggers and audit_triggered_ref is not None:
            pending = PendingAudit(
                envelope_record=envelope_record,
                domain_records=domain_records,
                domain_intents=domain_intents,
                gate_run_id=gate_run_id,
                transaction_id=transaction_id,
                batch_id=batch_id,
                outcome=outcome,
                payload=payload,
                audit_triggered_event_id=audit_triggered_ref,
                audit_subjects=tuple((t.subject_type, t.target_ref) for t in triggers),
                scope_hint=audit_scope_summary(triggers),
                trigger_reason=headline_reason(triggers).value,
            )
            return CommitAttemptResult("wait_for_audit", [envelope_record], pending)

        # audit_blocking off (or no trigger) — 留痕 only, verdict unchanged (pre-RUN-051 behavior).
        if audit_triggered_ref is not None:
            outcome.notices.append(
                f"audit_triggered: {audit_triggered_ref} — gate-side留痕 signal, verdict unchanged "
                "(audit-blocking off; two-phase fork engaged only via attempt_commit(audit_blocking=True))",
            )

        records = self._finalize_accept(
            envelope_record=envelope_record,
            domain_records=domain_records,
            domain_intents=domain_intents,
            gate_run_id=gate_run_id,
            transaction_id=transaction_id,
            batch_id=batch_id,
            outcome=outcome,
            payload=payload,
            current_committed_seq=current_committed_seq,
        )
        return CommitAttemptResult("accepted", records) if audit_blocking else records

    # ─── RUN-051 two-phase audit (M-0.5 §5.2 / §5.5) ───────────────────────

    def continue_attempt(
        self,
        pending: PendingAudit,
        *,
        audit_verdict_event_id: str,
    ) -> CommitAttemptResult:
        """M-0.5 §5.5 — second phase: apply the audit verdict回流 to the deferred commit.

        Reads the AuditVerdictReceived event (written by the submit driver after the fork ran) by
        id, then per §5.5 three branches:
          - verdict=pass            → finalize accept (CommitAccepted carries the audit_chain).
          - verdict=fail            → CommitRejected (rejection_type=audit_failed) + ObligationViolated
                                       for each obligation-subject trigger; audit_chain links the verdict.
          - verdict=conditional_pass→ escalate_to_nature semantics: 不阻断, finalize accept + an
                                       informational_notice (so the conditional concern is recorded).

        The CommitAccepted/CommitRejected audit_chain[].audit_verdict_event_id IS the spec's
        "audit_verdict_event_ref" (M-0.5 §6.5 AuditChainItem) — the non-self-authored proof that
        the verdict drove the decision.
        """
        verdict_record = self._event_log.get_event(audit_verdict_event_id)
        verdict_value = self._read_audit_verdict(verdict_record)
        overall_outcome = "reject" if verdict_value == "fail" else verdict_value
        audit_chain = [
            {
                "audit_triggered_event_id": pending.audit_triggered_event_id,
                "audit_verdict_event_id": audit_verdict_event_id,
                "overall_outcome": overall_outcome,
            },
        ]

        if verdict_value == "fail":
            # §5.5 reject branch. Obligation-subject triggers → ObligationViolated (the audit deemed
            # the obligation the trigger named actually unsound). Non-obligation triggers (self_check
            # / supersede) reject via AUDIT_FAILED + audit_chain with no synthetic obligation.
            violations = [
                ObligationViolation(
                    obligation_id=target_ref,
                    reason="audit_failed",
                    description=f"independent audit verdict=fail on {subject_type}:{target_ref} "
                    f"(audit_verdict={audit_verdict_event_id})",
                )
                for subject_type, target_ref in pending.audit_subjects
                if subject_type == "obligation_declaration"
            ]
            records = self._build_and_append_rejected(
                envelope_record=pending.envelope_record,
                transaction_id=pending.transaction_id,
                batch_id=pending.batch_id,
                gate_run_id=pending.gate_run_id,
                rejection_type=RejectionType.AUDIT_FAILED,
                recovery_of_abandoned=False,
                failure_reason="audit_failed",
                failure_evidence={
                    "audit_verdict_event_id": audit_verdict_event_id,
                    "audit_triggered_event_id": pending.audit_triggered_event_id,
                    "audit_scope": pending.scope_hint,
                },
                obligation_violations=violations,
                audit_chain=audit_chain,
            )
            return CommitAttemptResult("rejected", records)

        # §5.5 pass / conditional_pass → accept. conditional_pass records an informational_notice
        # (escalate_to_nature 不阻断: the concern is surfaced, the commit proceeds).
        if verdict_value == "conditional_pass":
            pending.outcome.notices.append(
                f"audit_conditional_pass: {audit_verdict_event_id} — 独立审计有顾虑但不阻断 commit "
                "(§5.5 escalate_to_nature, informational)。",
            )
        else:
            pending.outcome.notices.append(
                f"audit_pass: {audit_verdict_event_id} — 独立审计放行 (§5.5 pass)。",
            )
        current_committed_seq = self._event_log._read_max_seq_on_disk()
        records = self._finalize_accept(
            envelope_record=pending.envelope_record,
            domain_records=pending.domain_records,
            domain_intents=pending.domain_intents,
            gate_run_id=pending.gate_run_id,
            transaction_id=pending.transaction_id,
            batch_id=pending.batch_id,
            outcome=pending.outcome,
            payload=pending.payload,
            current_committed_seq=current_committed_seq,
            audit_chain=audit_chain,
        )
        # _finalize_accept may itself reject on a TOCTOU drift — reflect the true sentinel.
        status = "accepted" if records[-1].event_type is EventType.COMMIT_ACCEPTED else "rejected"
        return CommitAttemptResult(status, records)

    @staticmethod
    def _read_audit_verdict(verdict_record: EventRecord | None) -> str:
        """Read after_state.verdict from an AuditVerdictReceived record. fail-closed: missing/
        malformed → 'fail' (a continue_attempt with no resolvable verdict must NOT silently accept)."""
        if verdict_record is None:
            return "fail"
        after = verdict_record.payload.get("after_state")
        if not isinstance(after, dict):
            return "fail"
        verdict = after.get("verdict")
        return verdict if isinstance(verdict, str) and verdict else "fail"

    def _reject_from_outcome(
        self,
        envelope_record: EventRecord,
        transaction_id: str,
        batch_id: str,
        gate_run_id: str,
        outcome: _CheckOutcome,
    ) -> list[EventRecord]:
        """Build + append the rejected batch from a failed _CheckOutcome (DriftDetected path B first)."""
        drift_ref: str | None = None
        if outcome.drift_detected is not None:
            # §3.3.2 — DriftDetected (path B) written BEFORE the rejected batch.
            drift_ref = self._event_log.write_direct(
                self._build_drift_detected_intent(envelope_record, outcome.drift_detected),
            ).event_id
        return self._build_and_append_rejected(
            envelope_record=envelope_record,
            transaction_id=transaction_id,
            batch_id=batch_id,
            gate_run_id=gate_run_id,
            rejection_type=outcome.rejection_type,
            recovery_of_abandoned=False,
            failure_reason=outcome.failure_reason,
            failure_evidence=outcome.failure_evidence,
            obligation_violations=outcome.obligation_violations,
            drift_detected_event_ref=drift_ref,
        )

    def _finalize_accept(
        self,
        *,
        envelope_record: EventRecord,
        domain_records: list[EventRecord],
        domain_intents: list[EventIntent],
        gate_run_id: str,
        transaction_id: str,
        batch_id: str,
        outcome: _CheckOutcome,
        payload: dict[str, object],
        current_committed_seq: int,
        audit_chain: list[dict[str, str]] | None = None,
    ) -> list[EventRecord]:
        """Build + append the accepted batch (or reject on a TOCTOU drift). Shared by attempt_commit
        (single-phase accept) and continue_attempt (post-audit accept). audit_chain rides the
        CommitAccepted when a §5.5 pass/conditional_pass verdict drove the accept."""
        # T-L0-30 (M-0.5 §4.1 TOCTOU): write-pre re-check. Before write_batch, re-read the
        # committed max seq; if it changed since the checks ran (a path-B write — e.g. another
        # DriftDetected/state event — landed in the check→write window), re-run FreshnessDrift
        # against the NEW events. A drift now detected → reject instead of committing on stale
        # checks. §4.3: the multi-process CAS upgrade is explicitly out of scope (future).
        recheck = self._recheck_freshness_before_write(envelope_record, payload, current_committed_seq)
        if recheck is not None:
            drift_ref = self._event_log.write_direct(
                self._build_drift_detected_intent(envelope_record, recheck),
            ).event_id
            return self._build_and_append_rejected(
                envelope_record=envelope_record,
                transaction_id=transaction_id,
                batch_id=batch_id,
                gate_run_id=gate_run_id,
                rejection_type=RejectionType.DRIFT_DETECTED,
                recovery_of_abandoned=False,
                failure_reason="freshness_drift_toctou",
                failure_evidence={
                    "invalidating_event_ids": ",".join(recheck.invalidating_event_ids),
                    "drift_nodes": ";".join(f"{t}:{i}" for t, i in recheck.drift_nodes),
                    "toctou": "write-pre re-check (M-0.5 §4.1): committed max seq changed",
                },
                drift_detected_event_ref=drift_ref,
            )

        # Build accepted batch (envelope NOT in batch). domain_records were already stamped
        # before the envelope write (T-L0-27) so the stored envelope's backfilled detail_refs
        # point at THESE event_ids — reuse them here, do not re-stamp (a re-stamp would mint new
        # event_ids and the backfilled refs would dangle).
        all_intents: list[EventIntent] = list(domain_intents)

        # M-0.6 §8.2 + Patch F (RUN-005 fix bucket A — DOGFOOD-001-P):
        # accepted batch 同 batch 内为每条 envelope.active_obligations_declared 里
        # status=maintained / not_applicable 的 obligation emit ObligationChecked.
        checked_intents = self._build_obligation_checked_intents(envelope_record)
        for intent in checked_intents:
            rec = self._event_log.stamp_for_batch(
                intent,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=len(domain_records),
            )
            domain_records.append(rec)
            all_intents.append(intent)

        sentinel_intent = self._build_commit_accepted_intent(
            envelope_event_id=envelope_record.event_id,
            capsule_compiled_event_id=str(
                envelope_record.payload.get("capsule_compiled_event_id", "evt-no-capsule"),
            ),
            gate_run_id=gate_run_id,
            accepted_domain_event_count=len(domain_records),
            local_to_event_id_mapping=[
                {"local_intent_id": i.local_intent_id, "event_id": d.event_id}
                for i, d in zip(all_intents, domain_records, strict=True)
            ],
            # RUN-040 F-RA-1: real per-check accumulation from _run_checks (本次真跑过哪些 check),
            # 替换写死单条 envelope_integrity 占位。
            checks_passed=outcome.checks_performed,
            informational_notices=outcome.notices or None,
            audit_chain=audit_chain or [],
        )
        sentinel_record = self._event_log.stamp_for_batch(
            sentinel_intent,
            transaction_id=transaction_id,
            batch_id=batch_id,
            batch_position=len(domain_records),
        )
        # T-L0-27: domain records were stamped BEFORE the path-B envelope write, which reset the
        # in-memory next-seq counter — the provisional seqs across [domain..., obligation_checked,
        # sentinel] may no longer be strictly increasing. Renumber to satisfy the TransactionBatch
        # validator (event_ids preserved; append re-seqs authoritatively from the on-disk tail).
        *normalized_domain, normalized_sentinel = self._event_log.normalize_batch_seqs(
            [*domain_records, sentinel_record],
        )
        batch = build_accepted_batch_e11(
            batch_id,
            transaction_id,
            normalized_domain,
            normalized_sentinel,
        )
        written = self._event_log.append_transaction_batch(batch)
        # written carries the authoritative (file-lock-assigned) seqs + hashes (F-019-12);
        # envelope_record is already authoritative (path B write_direct).
        # M-3.2 §7.1 Auto-refresh: batch 真 accept 后, 按 batch 里的 canonical event_type 把受影响
        # view 标 stale (next `towow status --view` 自动 re-render)。🔴 fail-safe (view_refresh 内部
        # try/except): commit 已既成事实, view 刷新失败绝不倒灌、绝不影响上面已 append 的 sentinel /
        # 本方法返回。view 是 commit 下游只读副产物, 不 emit / 不改 canonical 状态 (无循环)。
        auto_refresh_views_after_accept(self._event_log, written)
        return [envelope_record, *written]

    def _stamp_domain_records(
        self,
        domain_intents: list[EventIntent],
        transaction_id: str,
        batch_id: str,
    ) -> list[EventRecord]:
        """Stamp the caller's domain intents into the (provisional-seq) batch records.

        T-L0-27: called BEFORE the envelope write so the assigned event_ids (final; only the
        seq is provisional) can be backfilled into the envelope's patch detail_refs. On accept
        these exact records are appended; on reject they are discarded (never written).
        """
        return [
            self._event_log.stamp_for_batch(
                intent,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=idx,
            )
            for idx, intent in enumerate(domain_intents)
        ]

    @staticmethod
    def _build_obligation_checked_intents(
        envelope_record: EventRecord,
    ) -> list[EventIntent]:
        """M-0.6 §8.2 + Patch F — emit ObligationChecked per maintained/not_applicable obligation
        in envelope.active_obligations_declared (commit gate has check authority).
        """
        declared_raw = envelope_record.payload.get("active_obligations_declared", [])
        if not isinstance(declared_raw, list):
            return []
        intents: list[EventIntent] = []
        for declared in declared_raw:
            if not isinstance(declared, dict):
                continue
            status = declared.get("status")
            obligation_id = declared.get("obligation_id")
            if not isinstance(obligation_id, str) or not obligation_id:
                continue
            if status not in {"maintained", "not_applicable"}:
                continue  # violated handled in rejected path
            intents.append(
                EventIntent(
                    local_intent_id=f"oc-{uuid.uuid4().hex[:12]}",
                    event_type=EventType.OBLIGATION_CHECKED,
                    event_category=EventCategory.OBLIGATION,
                    payload={
                        "obligation_id": obligation_id,
                        "obligation_lifecycle_state": "checked",
                        "checked_in_envelope_event_id": envelope_record.event_id,
                        # Cap2 (RUN-038, M-0.6 §4.2.3): typed check_method + check_outcome.
                        # The gate accepts the agent's declared maintained/not_applicable
                        # without running a per-obligation mechanical pattern or audit fork
                        # here, so the method is self_declared_only; check_outcome mirrors the
                        # passing status (status is one of maintained/not_applicable by the
                        # filter above — violated is handled in the rejected path).
                        "check_method": ObligationCheckMethod.SELF_DECLARED_ONLY.value,
                        "check_outcome": status,
                        "check_result": status,  # backward-compat
                    },
                    provenance_hint=ProvenanceHint(
                        actor_type=ActorType.COMMIT_GATE.value,
                        actor_id="m05_commit_gate",
                    ),
                    base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
                    supersede=Supersede(is_supersede=False),
                    subjects=[
                        Subject(
                            entity_type=SubjectEntityType.OBLIGATION,
                            entity_id=obligation_id,
                            role=SubjectRole.PRIMARY,
                        ),
                    ],
                    schema_version="1.0.0",
                ),
            )
        return intents

    @staticmethod
    def _build_obligation_violated_intent(
        envelope_record: EventRecord,
        violation: ObligationViolation,
    ) -> EventIntent:
        """§3.4.3 — ObligationViolated for a gate-detected violation (rejected-batch member).

        M-0.5 holds violation-detection authority (M-0.6 holds the rest of the lifecycle).
        """
        return EventIntent(
            local_intent_id=f"ov-{uuid.uuid4().hex[:12]}",
            event_type=EventType.OBLIGATION_VIOLATED,
            event_category=EventCategory.OBLIGATION,
            payload={
                "obligation_id": violation.obligation_id,
                "obligation_lifecycle_state": "violated",
                "violated_in_envelope_event_id": envelope_record.event_id,
                "violation_description": f"{violation.reason}: {violation.description}",
                "recommended_action": "fix",
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.OBLIGATION,
                    entity_id=violation.obligation_id,
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )

    # ─── RUN-030 第0波 ① — DriftDetected (§3.3.2) + AuditTriggered (§5.1) path-B emitters ──

    @staticmethod
    def _subject_entity_type(value: str) -> SubjectEntityType:
        """Coerce a payload/intent entity_type string to SubjectEntityType (fallback TASK).

        read_set/write_set/supersede entity_type strings come from valid M-0.4 / subject
        sources, but DriftDetected/AuditTriggered must carry a valid enum subject — an
        unrecognized type degrades to TASK rather than raising at the write boundary.
        """
        try:
            return SubjectEntityType(value)
        except ValueError:
            return SubjectEntityType.TASK

    def _build_drift_detected_intent(
        self,
        envelope_record: EventRecord,
        drift: _DriftInfo,
    ) -> EventIntent:
        """§3.3.2 — DriftDetected (path B), produced BEFORE the rejected batch (M-0.1 §3.7).

        drift_nodes = the relevant subjects whose semantic premise changed; the invalidating
        event ids ride in evidence on the CommitRejected sentinel. original_assembly_event_id
        is the capsule the stale envelope was compiled against (L1 re-assembles from it).
        """
        return EventIntent(
            local_intent_id=f"dd-{uuid.uuid4().hex[:12]}",
            event_type=EventType.DRIFT_DETECTED,
            event_category=EventCategory.COMMIT,
            payload={
                "envelope_event_id": envelope_record.event_id,
                "drift_nodes": [
                    {"entity_type": self._subject_entity_type(t).value, "entity_id": i}
                    for t, i in drift.drift_nodes
                ],
                "original_touched_nodes": [
                    {"entity_type": self._subject_entity_type(t).value, "entity_id": i}
                    for t, i in drift.relevant_subjects
                ],
                "original_assembly_event_id": drift.capsule_compiled_event_id,
                "drift_type": drift.drift_type,
                "invalidating_event_ids": list(drift.invalidating_event_ids),
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=self._subject_entity_type(t),
                    entity_id=i,
                    role=SubjectRole.PRIMARY,
                )
                for t, i in drift.drift_nodes
            ]
            or [
                Subject(
                    entity_type=SubjectEntityType.TASK,
                    entity_id=str(envelope_record.payload.get("task_id", "unknown")),
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )

    def _detect_audit_triggers(
        self,
        payload: dict[str, object],
        domain_intents: list[EventIntent],
        index: EventIndex,
    ) -> tuple[AuditTrigger, ...]:
        """§5.1 — detect audit triggers over the envelope (sampler/config from gate construction).

        Pure detection (no writes). RUN-051: the triggers drive both the留痕 AuditTriggered emit
        and the two-phase wait_for_audit signal (when audit_blocking)."""
        kwargs: dict[str, object] = {}
        if self._audit_sampler is not None:
            kwargs["sample_decision"] = self._audit_sampler
        if self._audit_config is not None:
            kwargs["config"] = self._audit_config
        return detect_audit_triggers(index, payload, domain_intents, **kwargs)  # type: ignore[arg-type]

    def _emit_audit_triggered(
        self,
        envelope_record: EventRecord,
        payload: dict[str, object],
        triggers: tuple[AuditTrigger, ...],
    ) -> str:
        """§5.3 — emit ONE AuditTriggered (producer-only 口,留痕) for the detected triggers; return its id.

        锁写边界 (f 修): AuditTriggered 已移出 _is_path_b_allowed → 经 producer-only _write_audit_event
        发射 (手 write_direct 会 raise)。intent 的 provenance actor_id=m05_commit_gate 是 sanctioned。
        """
        record = self._event_log._write_audit_event(
            self._build_audit_triggered_intent(envelope_record, payload, triggers),
        )
        return record.event_id

    def _build_audit_triggered_intent(
        self,
        envelope_record: EventRecord,
        payload: dict[str, object],
        triggers: tuple[AuditTrigger, ...],
    ) -> EventIntent:
        """§5.3 — AuditTriggered (path B). The canonical AuditTriggeredAfter carries one
        trigger_reason + one audit_scope string; we pick the highest-priority reason as the
        headline and serialize every triggered subject into audit_scope (no info dropped).
        """
        task_id = str(payload.get("task_id", "unknown"))
        subjects = [
            Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY),
        ]
        for t in triggers:
            subjects.append(
                Subject(
                    entity_type=self._subject_entity_type(t.subject_entity_type),
                    entity_id=t.target_ref,
                    role=SubjectRole.AFFECTED,
                ),
            )
        return EventIntent(
            local_intent_id=f"at-{uuid.uuid4().hex[:12]}",
            event_type=EventType.AUDIT_TRIGGERED,
            event_category=EventCategory.SEMANTIC_JUDGMENT,
            payload={
                "judgment_type": JudgmentType.AUDIT_TRIGGER.value,
                "after_state": {
                    "trigger_reason": headline_reason(triggers).value,
                    "envelope_event_id": envelope_record.event_id,
                    "audit_scope": audit_scope_summary(triggers),
                },
                "confidence": None,
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=subjects,
            schema_version="1.0.0",
        )

    @staticmethod
    def _normalize_envelope_payload(
        envelope_record: EventRecord,
        domain_intents: list[EventIntent],
    ) -> tuple[dict[str, object], list[str]]:
        """Fill the M-0.4 §3.1 system-derived boundary fields the wrapper should have set.

        Debt-payoff block 1 makes EnvelopeIntegrity (§3.0) + ClaimsBoundary (§3.6) real.
        Emit paths not yet on the submit wrapper (block 4 migration) hand the gate a
        boundary-less stub; rather than silently skip the checks, the gate derives the
        same mechanical boundaries the wrapper would (write_set from the domain intents,
        read_set floor = task package, ids from provenance) and records that it did so in
        a notice — so the audit trail shows exactly which commits were gate-derived.

        Returns (payload_for_checking, derivation_notices). Never mutates the stored event.
        """
        payload = dict(envelope_record.payload)
        prov = envelope_record.provenance
        notices: list[str] = []
        derived: list[str] = []

        if not payload.get("schema_version"):
            payload["schema_version"] = envelope_record.schema_version
        if not payload.get("envelope_id"):
            payload["envelope_id"] = envelope_record.local_intent_id or envelope_record.event_id

        session_ref = (
            payload.get("session_id") or prov.session_id or payload.get("envelope_id") or "unknown"
        )
        if not payload.get("task_id"):
            payload["task_id"] = prov.task_id or f"session:{session_ref}"
            derived.append("task_id")
        if not payload.get("run_id"):
            payload["run_id"] = prov.run_id or f"session:{session_ref}"
            derived.append("run_id")
        if not payload.get("fork_id"):
            payload["fork_id"] = prov.fork_name or prov.actor_id
            derived.append("fork_id")
        if "active_obligations_declared" not in payload:
            payload["active_obligations_declared"] = []

        capsule_id = str(payload.get("capsule_compiled_event_id") or "")
        task_id = str(payload["task_id"])

        if not payload.get("write_set"):
            write_set = [e.model_dump(mode="json") for e in derive_write_set(domain_intents)]
            if not write_set and domain_intents:
                # Domain events exist but none map to the write_set entity universe
                # (M-0.4 §2: 7 types; finding / rule / task_edge have no equivalent).
                # Declare the run's task as modified — the precise change lives in the
                # committed events / patches; this is the write_set floor for such commits.
                write_set = [
                    {"entity_type": "task", "entity_id": task_id, "change_type": "modified"}
                ]
            payload["write_set"] = write_set
            if write_set:
                derived.append("write_set")
        if not payload.get("read_set"):
            payload["read_set"] = [
                e.model_dump(mode="json")
                for e in derive_read_set(task_id=task_id, capsule_compiled_event_id=capsule_id or "evt-no-capsule")
            ]
            derived.append("read_set")

        if derived:
            notices.append(
                "gate_derived_boundaries: "
                + ",".join(derived)
                + " (caller not yet on submit wrapper; M-0.4 §3.1 / block-4 migration)",
            )
        return payload, notices

    def _run_checks(
        self,
        envelope_record: EventRecord,
        domain_intents: list[EventIntent],
        index: EventIndex,
        payload: dict[str, object],
        notices: list[str],
    ) -> _CheckOutcome:
        """Run the gate check pipeline; returns a _CheckOutcome (rejection + notices).

        Block-1 boundary checks: EnvelopeIntegrity (§3.0) + ClaimsBoundary (§3.6), now
        real over a normalized payload. Existing checks retained: SkillArtifactSelfCheck
        blocking_checks (T0.4 / DOGFOOD-001-B) and physical NoveltyCheck (§3.5.1, Patch
        M-2.1-D InvalidationCascade exempt). Short-circuits on first failure (§3.7).

        `index` + `payload` + `notices` are built once by attempt_commit and shared with the
        §5 audit-trigger detection (avoids a second index build).
        """
        # RUN-040 F-RA-1: accumulate每道真跑过的 check + 结果, 在 accept fall-through 返给
        # CommitAccepted.checks_passed (取代写死单条 envelope_integrity 占位)。一道 check 拒批
        # 时 §3.7 短路提前 return (走 rejection_reasons, 不用 checks_passed), 故只在全过时填满。
        performed: list[dict[str, str]] = []

        # §3.0 EnvelopeIntegrity (pre-check) — must pass before anything else.
        integrity = check_envelope_integrity(payload)
        if not integrity.passed:
            # rejection_type enum (M-0.1 §2.3.7) has no integrity category; use the
            # precedented catch-all + carry the precise reason in the rejected payload.
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=integrity.failure_reason,
                failure_evidence=integrity.evidence,
            )
        performed.append({"check_type": "envelope_integrity", "result": "passed"})

        # RUN-068 件A (M-0.6 §11 不变量5 / §4.1.1) — ObligationCaptured provenance enforcement. A
        # runtime capture (capture_source != system_bootstrap) co-submitted in this envelope MUST
        # carry capture_provenance.source_event_refs (≥1 upstream trigger event). An empty-provenance
        # capture is rejected BEFORE the ObligationCaptured lands — provenance is真强制, not摆设.
        # (The genesis system_bootstrap captures go through bootstrap_commit, not this path.)
        provenance = check_obligation_capture_provenance(domain_intents)
        if not provenance.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=provenance.failure_reason,
                failure_evidence=provenance.failure_evidence or {},
            )
        performed.append({"check_type": "obligation_capture_provenance", "result": "passed"})

        # T-LRF-01 finding-routability-birth-gate@v1 (birth-time-validation@v1 第一具名实例) —
        # FindingCreated 出生闸: 校验可路由性 (finding_kind ∈ _DISPATCH_TABLE_FINDING_KIND 或
        # suggested_fix_layer.primary 可映射 skill); 两者皆无 → 出生拦截 (该 FindingCreated 不落
        # canonical)。前移修复: 不可路由 finding 根本不准进系统, 杜绝"进了再 fallback 重派死循环"
        # (commit 65128f6 兜底派 fix 造的 6 活体死循环根因)。orchestrator 的 _fallback_finding_route
        # 降级为纵深兜底保留。非 FindingCreated intent → check 跳过 (not_applicable, 不冒充评过)。
        routability = check_finding_routability(domain_intents)
        if not routability.passed:
            return _CheckOutcome(
                # rejection_type enum (M-0.1 §2.3.7) 无 routability 类别; 复用 integrity/completeness
                # check 的先例 catch-all + 精确 failure_reason 携真因 (出生拦截 = 拒整批, finding 不落账)。
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=routability.failure_reason or "finding_unroutable",
                failure_evidence=routability.failure_evidence,
            )
        _finding_present = any(
            intent.event_type.value == "FindingCreated" for intent in domain_intents
        )
        performed.append({
            "check_type": "finding_routability_birth_gate",
            "result": "passed" if _finding_present else "not_applicable",
        })

        # W1-R2 (LEDGER Conflict 32) — finding-classification-consistency-birth-gate@v1: R2 (退役
        # 语义误分类) + R3 (premise_false 缺 task 锚定)。warn-only 观察期 (默认): 命中只留
        # notices, 不拒批; owner 在 .towow/maintenance/config.json 显式解冻对应 enforce key 后,
        # R2 收窄范围内 (finding_kind=concept_issue 且机械模式命中) 才 fail-closed 拒。线程
        # self._event_log 让 enforce 开关读取真实 (event_log=None 时两条规则皆 fail-safe-closed
        # False, 不影响 verdict)。
        classification = check_finding_classification_consistency(domain_intents, self._event_log)
        if not classification.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=classification.failure_reason
                or "finding_classification_inconsistent",
                failure_evidence=classification.failure_evidence,
            )
        notices.extend(classification.notices)
        performed.append({
            "check_type": "finding_classification_consistency",
            "result": "passed" if _finding_present else "not_applicable",
        })

        # RUN-029 第0波 ①a/①b — §3.1 VersionConsistency + §3.2 WriteConflict, real over the
        # committed event index (was schema-sanity / always-pass in E.1, gate.py header L20-24).

        # §3.1 VersionConsistencyCheck — capsule referenced by envelope not superseded.
        version = check_version_consistency(index, str(payload.get("capsule_compiled_event_id", "")))
        if not version.passed:
            # capsule_superseded is rebase-retryable (stale snapshot, VERSION_CONFLICT);
            # invalid_capsule_reference is a hard malformed-ref error (no rebase) — reuse the
            # precedented catch-all the integrity check uses (M-0.1 §2.3.7 has no integrity type).
            rej = (
                RejectionType.VERSION_CONFLICT
                if version.failure_reason == "capsule_superseded"
                else RejectionType.OBLIGATION_VIOLATION
            )
            return _CheckOutcome(
                rejection_type=rej,
                failure_reason=version.failure_reason,
                failure_evidence=version.failure_evidence,
            )
        if version.notice:
            notices.append(version.notice)
        performed.append({"check_type": "version_consistency", "result": "passed"})

        # §3.2 WriteConflictCheck — write_set entity changed by a committed state_transition
        # after envelope.as_of_projection_seq (concurrent-write detection; rebase-retryable).
        as_of_seq_raw = payload.get("as_of_projection_seq", 0)
        as_of_seq = as_of_seq_raw if isinstance(as_of_seq_raw, int) else 0
        write_set = payload.get("write_set", [])
        conflict = check_write_conflict(
            index,
            write_set if isinstance(write_set, list) else [],
            as_of_seq,
        )
        if not conflict.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.WRITE_CONFLICT,
                failure_reason=conflict.failure_reason,
                failure_evidence=conflict.failure_evidence,
            )
        if conflict.notice:
            notices.append(conflict.notice)
        performed.append({"check_type": "write_conflict", "result": "passed"})

        # §9.2 ownership WriteConflict — write_set entity held by a DIFFERENT task's live lock.
        # Reads the M-0.2 §3.4 ownership projection (T-L0-07 reducer is the spec's正规输入), vs
        # the temporal entity-index scan above. Self-locks / boundary-less envelopes skip.
        submitter_run_id = payload.get("run_id")
        submitter_run_id_norm = submitter_run_id if isinstance(submitter_run_id, str) else None
        write_set_norm = write_set if isinstance(write_set, list) else []
        # Precondition guard: check_ownership_conflict's no-run_id / empty-write_set skip
        # branches (semantic_checks.py ~L99-109) never touch the projection argument, so only
        # pay _read_ownership_projection() (a per-commit ProjectionStore.catchup) when the check
        # can actually use the result. NOTE: today this guard is purely defensive — §3.0
        # EnvelopeIntegrity above already rejects empty write_set (checks.py ~L106) and
        # _normalize_envelope_payload always backfills run_id, so every commit reaching this
        # line reads the projection exactly as before. It starts saving folds only if those
        # upstream guarantees relax (e.g. empty write_set legalized by the T-L0-24/25 boundary
        # work). Semantics unchanged either way: the check sees None only when it would skip.
        ownership_projection = (
            self._read_ownership_projection()
            if submitter_run_id_norm and write_set_norm
            else None
        )
        own_conflict = check_ownership_conflict(
            ownership_projection,
            write_set_norm,
            submitter_run_id_norm,
        )
        if not own_conflict.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.WRITE_CONFLICT,
                failure_reason=own_conflict.failure_reason,
                failure_evidence=own_conflict.failure_evidence,
            )
        if own_conflict.notice:
            notices.append(own_conflict.notice)
        performed.append({"check_type": "ownership_conflict", "result": "passed"})

        # §3.3 DriftCheck — §3.7 order: ScopeDrift (§3.3.1) → FreshnessDrift (§3.3.2). A drift
        # reject is a physical short-circuit (§3.7) and produces DriftDetected (path B, written by
        # attempt_commit BEFORE the rejected batch).
        capsule_ref = str(payload.get("capsule_compiled_event_id") or "evt-no-capsule")
        # RUN-038 §3.3.2 — the relevant-subject set folds the capsule neighborhood (touched_nodes,
        # CapsuleCompiled.payload.neighborhood_concept_ids) into the envelope read_set ∪ write_set
        # proxy, so an invalidating event on a capsule-scope concept the envelope did not list is no
        # longer missed. Stub capsule → exactly the read∪write proxy (backward compatible).
        relevant_subjects = freshness_relevant_subjects(index, capsule_ref, payload)

        # RUN-038 §3.3.1 ScopeDrift — real now that the capsule scope (touched_nodes) is stored on
        # CapsuleCompiled.payload.neighborhood_concept_ids (M-0.3 §2.2 / §9.2). drift = touched
        # concepts - capsule scope; non-empty → scope_exceeded reject (§7.4: needs new capsule +
        # new run → LockReleased, NOT rebase). Stub capsule ref → skip. The touched set is read_set
        # ∪ write_set MINUS `created` write entries (a newly-minted node is the envelope's own
        # output, not a pre-existing node reached outside scope — see scope_touched_from_payload).
        scope_touched = scope_touched_from_payload(payload)
        scope = check_scope_drift(index, capsule_ref, scope_touched)
        if not scope.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.DRIFT_DETECTED,
                failure_reason="scope_exceeded",
                failure_evidence={
                    "drift_set": ";".join(f"{t}:{i}" for t, i in scope.drift_nodes),
                    "capsule_scope": ",".join(scope.capsule_scope),
                },
                drift_detected=_DriftInfo(
                    drift_nodes=scope.drift_nodes,
                    # original_touched_nodes留痕 = the capsule's full declared scope (concept nodes),
                    # so L1 can diff "what the capsule allowed" vs "what escaped" (drift_nodes).
                    relevant_subjects=tuple(("concept", c) for c in scope.capsule_scope),
                    invalidating_event_ids=(),
                    capsule_compiled_event_id=capsule_ref,
                    drift_type="scope",
                ),
            )
        if scope.notice:
            notices.append(scope.notice)
        performed.append({"check_type": "scope_drift", "result": "passed"})

        # §3.3.2 FreshnessDrift — real over the envelope's declared scope (read_set ∪ write_set as
        # the relevant-subject proxy; full neighborhood = wave1 capsule-content debt).
        drift = check_freshness_drift(index, relevant_subjects, as_of_seq)
        if not drift.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.DRIFT_DETECTED,
                failure_reason="freshness_drift",
                failure_evidence={
                    "invalidating_event_ids": ",".join(drift.invalidating_event_ids),
                    "drift_nodes": ";".join(f"{t}:{i}" for t, i in drift.drift_nodes),
                },
                drift_detected=_DriftInfo(
                    drift_nodes=drift.drift_nodes,
                    relevant_subjects=tuple(sorted(relevant_subjects)),
                    invalidating_event_ids=drift.invalidating_event_ids,
                    capsule_compiled_event_id=capsule_ref,
                ),
            )
        if drift.notice:
            notices.append(drift.notice)
        performed.append({"check_type": "freshness_drift", "result": "passed"})

        # T0.4 SkillArtifactSelfCheck: evaluate blocking_checks (DOGFOOD-001-B repair).
        self_check = payload.get("self_check")
        _self_check_evaluated = False
        if isinstance(self_check, dict):
            blocking_checks = self_check.get("blocking_checks", [])
            if isinstance(blocking_checks, list):
                for check in blocking_checks:
                    if not isinstance(check, dict):
                        continue
                    _self_check_evaluated = True
                    if check.get("status") != "passed":
                        return _CheckOutcome(
                            rejection_type=RejectionType.OBLIGATION_VIOLATION,
                            failure_reason="blocking_check_failed",
                            failure_evidence={"check_id": str(check.get("check_id", ""))},
                        )
        # 如实记录: 有 blocking_checks 才算真评了 self-check; 无则 not_applicable (不冒充评过)。
        performed.append({
            "check_type": "skill_artifact_self_check",
            "result": "passed" if _self_check_evaluated else "not_applicable",
        })

        # F-026-1 (block 3): semantic-completeness requirement — a stage-freeze domain event
        # must carry its spec-designated completeness blocking_checks (present + passed).
        # "缺必需检查 ≠ 检查通过" — closes the零-blocking_check 蒙混过关 hole at the gate (总闸).
        completeness = check_completeness_requirements(
            [intent.event_type for intent in domain_intents],
            self_check,
        )
        if not completeness.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=completeness.failure_reason,
                failure_evidence=completeness.failure_evidence or {},
            )
        performed.append({"check_type": "completeness_requirements", "result": "passed"})

        # §3.6 ClaimsBoundaryCheck — entity_id-exact set ops; absent → notice + skip.
        claims = check_claims_boundary(payload)
        if not claims.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.CLAIM_BOUNDARY_EXCEEDED,
                failure_reason=claims.failure_reason,
                failure_evidence={"exceeded": ";".join(f"{t}:{i}" for t, i in claims.exceeded)},
            )
        if claims.notice:
            notices.append(claims.notice)
        # claims.notice 表示 claims 缺省被跳过 (absent → notice); 据此如实标 not_applicable。
        performed.append({
            "check_type": "claims_boundary",
            "result": "not_applicable" if claims.notice else "passed",
        })

        # NoveltyCheck (M-0.5 §3.5.1) — supersede events need substantive novelty. Patch M-2.1-D
        # exempt. T-L0-29 (波1): 收紧 `not novelty` → `.strip()` 拒 vacuous (空/纯空白) novelty
        # (concept_event novelty 全空 supersede 真拒, 含所有 event_type)。no_new_information 在
        # SupersedeNoveltyType 4 枚举里没有该成员 → schema 层不可构造 (是 review-dispute 概念,
        # 非 gate-supersede 字段); supersede→novelty 必填由 Supersede model_validator 强制 (None 拒)。
        _novelty_evaluated = False
        for intent in domain_intents:
            if intent.event_type is EventType.INVALIDATION_CASCADE:
                continue
            if intent.supersede.is_supersede:
                _novelty_evaluated = True
                if not (intent.supersede.novelty or "").strip():
                    return _CheckOutcome(
                        rejection_type=RejectionType.NOVELTY_MISSING,
                        failure_reason="novelty_missing",
                    )
        # 无 supersede intent → novelty 检查无对象, not_applicable (不冒充评过)。
        performed.append({
            "check_type": "novelty",
            "result": "passed" if _novelty_evaluated else "not_applicable",
        })

        # §3.4 ObligationCheck (coverage floor + maintenance) — RUN-029 ①d. Runs last (§3.7).
        # Maintenance violations (self-reported / red_line not_applicable) → reject + produce
        # ObligationViolated in the rejected batch (§3.4.3). Coverage gap is a coarse count
        # signal (precise injected-list coverage is capsule-stub debt) → notice only, no reject.
        declared_raw = payload.get("active_obligations_declared", [])
        declared_list: list[object] = list(declared_raw) if isinstance(declared_raw, list) else []
        # RUN-068 件C — the matchable content for §3.4.2 forbidden_pattern matching: the envelope's
        # patch summaries + declared content (the text the gate actually has in the event log). A
        # `maintained` obligation whose forbidden_pattern hits this content is rejected (the matcher
        # is no longer vacuous now that capture event-sources checker_metadata.forbidden_pattern).
        obligations = check_obligations(
            index,
            str(payload.get("capsule_compiled_event_id", "")),
            declared_list,
            match_content=_obligation_match_content(payload),
        )
        notices.extend(obligations.notices)
        if not obligations.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason="obligation_violated",
                failure_evidence={
                    "violated_obligation_ids": ",".join(v.obligation_id for v in obligations.violations),
                },
                obligation_violations=list(obligations.violations),
            )
        performed.append({"check_type": "obligation_check", "result": "passed"})

        # ConsolidationInvariantCheck (M-0.7 §10.3 Patch M) — runs after ObligationCheck (§3.7
        # order). NO-OP for non-consolidation envelopes (no patch_type==consolidation_event → skip,
        # no false reject). When applicable, verifies the §6.3 ConsolidationEnvelope against the
        # three compaction invariants (§6.4); a violation → reject with the §6.6 / Patch N
        # rejection_type=consolidation_invariant_violated. Producing a consolidation envelope is
        # driver-gated (debt-008857ede88c), but the gate enforces the invariants whenever one arrives.
        consolidation = check_consolidation_invariants(payload, self._event_log)
        if consolidation.applicable and not consolidation.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.CONSOLIDATION_INVARIANT_VIOLATED,
                failure_reason=consolidation.failure_reason,
                # _CheckOutcome.failure_evidence is dict[str, str] — stringify the structured
                # invariant evidence (failed_invariants map / evidence_refs) for the rejected payload.
                failure_evidence={k: str(v) for k, v in consolidation.failure_evidence.items()},
            )
        # 非 consolidation envelope → check 不适用 (not_applicable), 不冒充评过。
        performed.append({
            "check_type": "consolidation_invariants",
            "result": "passed" if consolidation.applicable else "not_applicable",
        })

        # RUN-029 第0波 ③ — done-claim gate (owner tiered invariant): a commit claiming a
        # capability complete must carry a passed semantic-completeness check AND have no open
        # blocking debt against that capability. Final check (a done-claim presupposes the rest).
        completion = check_completion_claims(index, payload)
        if not completion.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=completion.failure_reason,
                failure_evidence=completion.failure_evidence,
            )
        performed.append({"check_type": "completion_claims", "result": "passed"})

        # T-LND-04 (INV-B1-3): REVIEW task 的 TaskRunCompleted(success) 合法 ⟺ 其 review-unit 的
        # 派生 verdict=passed (法则一: 推进凭证是 verdict 非"测试绿")。verdict!=passed → 物理拒。
        # 非 REVIEW task / abort → 跳过 (本门只管 REVIEW success)。
        review_verdict = check_review_verdict_gated_completion(
            domain_intents, index, index.records(),  # warm index reuse (see committed_index() note above)
        )
        if not review_verdict.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=review_verdict.failure_reason,
                failure_evidence=review_verdict.failure_evidence,
            )
        performed.append({"check_type": "review_verdict_gated_completion", "result": "passed"})

        # T-DEC-2 closure-evidence-verification-gate@v1 (anti-fake-done 铁约束): done-elsewhere 的
        # TaskNodeClosed 仅当三重 fail-closed 核验全成立才接受 —— (1)superseded_by 解析真实 commit/
        # finding (2)verification_verdict_ref 解析到真实、正向、锚定本 task 的独立 verdict (3)verdict
        # 产出会话 != closer (关闭者不能自核)。否则拒 → TaskNodeClosed 不落账 → task 留 open。镜像
        # review_verdict_gated_completion: 防 done-elsewhere 关闭沦为新的"假装做完"后门。非 TaskNodeClosed
        # → 跳过 (本门只管 done-elsewhere 关闭)。
        closure_evidence = check_closure_evidence_verification(
            domain_intents, index, index.records(),  # warm index reuse (see committed_index() note above)
        )
        if not closure_evidence.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=closure_evidence.failure_reason,
                failure_evidence=closure_evidence.failure_evidence,
            )
        performed.append({
            "check_type": "closure_evidence_verification",
            "result": "passed" if closure_evidence.applicable else "not_applicable",
        })

        # activation-acceptance-gate@v1 (激活验收门 · anti-fake-done 建成未接线侧): 闭合类 envelope
        # (TaskRunCompleted success / TaskNodeClosed / FindingVerified) 若其闭合 task 的 concept_refs 含
        # 已注册能力 (harness-capability-registry 成员), 则要求携带一条带 expiry 的 activation-debt (真空期
        # 欠条) —— 这是 v1 唯一满足路径; 无债 → 拒。v1 【不做】"出示有机证据免债"释放阀 (账本扫描分不清能力
        # "被设计"与"被有机使用" → 会恒放行 = 第 175 条空转门; 诚实边界见 activation_acceptance_check 模块
        # docstring), 故演习/伪造留痕在 v1 无过关面 —— 一律走"无债即拒", 不存在独立的"伪造有机证据→拒"分支。
        # fire-always/demand-narrow: 每次 accept 都 append (not_applicable 也 append)
        # 以满足自指免疫 inv3 (gate_check_firing.activation_acceptance 非潜伏、触发计数≥1)。门是纯 validator
        # 不自 emit —— 靠"拒绝无证据且无债的闭合"强制 executor 登债 (write_direct path B, 门读 committed 即见)。
        activation = check_activation_acceptance(domain_intents, index.records())
        if not activation.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=activation.failure_reason,
                failure_evidence=activation.failure_evidence,
            )
        performed.append({
            "check_type": "activation_acceptance",
            "result": "passed" if activation.applicable else "not_applicable",
        })

        # live-target-execution-evidence@v1 goal 收口门 (承重 · anti-fake-done 生产副作用侧): 任何把 goal
        # 推向 GoalSessionTerminated(reason=completion) 的路径先过本门 —— 对该 goal 的 brief.
        # live_target_observables 逐条复算 against committed 账本 (共用唯一真值源 gate_ledger_event)。任一
        # 未满足 → 拒 completion (旗舰: 账本 0 条 StrandedBatchManifestFrozen 却靠撤边+单 success 宣布
        # completion)。门看账本证据不看 task 图 → 撤边逃逸无效。非 completion 收口 / 无 observable → 放行。
        live_target = check_live_target_reconciled(domain_intents, index.records())
        if not live_target.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=live_target.failure_reason,
                failure_evidence=live_target.failure_evidence,
            )
        performed.append({
            "check_type": "live_target_reconciled",
            "result": "passed" if live_target.applicable else "not_applicable",
        })

        # B-3 (substrate 4, K5 fencing): TaskRunCompleted 携带 fencing_token 时, 资源侧校验它 ≥ 该 task
        # 当前最高 .fence (拒被抢锁旧会话复活的迟到写, Kleppmann 时序脑裂)。三 case: no-token→PASS
        # (绝大多数 producer, None≠stale, verify 之前就放行) / current→PASS / stale→拒。
        # 🔴 enforcement 默认 ON (TOWOW_FENCING_ENFORCE 默认 on, owner 2026-06-30 在场受控点亮): stale
        # token 真拒 (堵时序脑裂)。回滚口 = 显式 TOWOW_FENCING_ENFORCE=off 退回只观测留痕不真拒。
        # f-rp-autopilot-fencing-enforce-silently-off-no-detection: enforce 真实态 (fencing.enforce_enabled)
        # 每次门运行都记进 checks_passed → 写进 canonical CommitAccepted → 每个 gate 进程的真实 enforce 态
        # 可观测。此前 off 静默不可见: 一个 env 带 off 的 gate 进程放行 stale 写 (would_reject_observed) 却
        # 无人察觉跨进程不一致。now: enforce=off 逐条留在账本, 巡检 (scripts/fencing_monitor.py) 可告警。
        fencing = check_fencing_token(domain_intents, self._event_log._log_path)
        if not fencing.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=fencing.failure_reason,
                failure_evidence=fencing.failure_evidence,
            )
        performed.append({
            "check_type": "fencing_token",
            "result": "would_reject_observed" if fencing.would_reject else "passed",
            "enforce": "on" if fencing.enforce_enabled else "off",
        })

        # R01 c-concept-create-relatedness-check-gate@v1 (instance_of @birth-time-validation@v1):
        # 新概念出生口相关性检查 —— 一个 ConceptCreated 必须能落到既有概念网 (声明关联 / 语义关联 /
        # 共变关联), 否则在成熟图里=可疑孤岛, 出生口拒 (fail-closed), 要求找回该有的关联。不是强制挂
        # 父——三信号任一成立即过, 且永远可靠主动断言真全新放行。non-applicable (本批无 ConceptCreated)
        # → no-op 不误拒。existing_concepts 从已提交记录枚举 (排除本批待出生的, 避免自比)。
        # f-r01-5 残留: 双 shape 抽取 (复用已测的 _extract_new_concepts) —— flat-shape ConceptCreated
        # (概念数据在 payload 顶层、无 after_state) 的 concept_id 也要取到, 否则 exclude_ids 漏排本批
        # 待出生的同 id 概念, 同 id flat 概念 re-emit 时会跟自己上次发射语义自匹配静默过门。
        new_concept_ids = [c.concept_id for c in _extract_new_concepts(domain_intents)]
        relatedness = check_concept_create_relatedness(
            domain_intents,
            existing_concepts_from_records(index.records(), exclude_ids=new_concept_ids),  # warm index reuse
        )
        if not relatedness.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=relatedness.failure_reason or "concept_isolated_no_relatedness",
                failure_evidence=relatedness.failure_evidence,
            )
        notices.extend(relatedness.notices)
        performed.append({
            "check_type": "concept_create_relatedness",
            "result": "passed" if relatedness.applicable else "not_applicable",
        })

        # R01 c-concept-retire-hard-gate-staged-migration@v1: 退役/升级一个概念 (ConceptCreated
        # 带 supersede.is_supersede=True) 时, 先算依赖它的 CIS (T-R01-3), 再用 at-reference supersede
        # blocking (§4.3) 核每个依赖"改完没" —— 任一 active explicit-decision 引用指向旧版本且本批未迁
        # → 物理拒 (concept_retire_unmigrated_dependents, fail-closed)。分阶段迁移: 依赖逐个迁完才最终
        # 放行。non-applicable (本批无退役 supersede) → no-op 不误拒。全局强共识有牙 (owner: 退役要硬)。
        retire = check_concept_retire_migration_for_gate(domain_intents, self._event_log)
        if not retire.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=retire.failure_reason or "concept_retire_unmigrated_dependents",
                failure_evidence=retire.failure_evidence,
            )
        notices.extend(retire.notices)
        performed.append({
            "check_type": "concept_retire_migration",
            "result": "passed" if retire.applicable else "not_applicable",
        })

        # GP-05 (f-sub-graph-protocol-readside-dormant): 图边完整性 —— 门**自己从 edge payload 算**
        # ConceptEdgeAdded 的 target 可解析性 (target ∈ 既有概念[含已退役-仍在节点, 对齐
        # facade.integrity_violations: reducer 从不删节点 → 退役概念仍是图节点 = 合法 target,
        # f-gp05-edge-gate-stricter-than-facade-retired-target] ∪ 同批 co-created ∪ 合法
        # external_anchor), 任一撞空 → fail-closed 拒。契约 (graph_protocol.py §完整性) 声称门加边时
        # 校验、此前零执行 (悬空边 20 条静默过关, 让 investigate 跨域导航走进空洞)。这是"门自算非信
        # 自报"的真牙齿 (区别于 completeness.py 的 presence-only check), 与 relatedness/retire 同 pattern:
        # 本批无 ConceptEdgeAdded → not_applicable, 不误拒。
        edge_integrity = check_graph_edge_integrity(
            domain_intents,
            index.records(),  # warm index reuse (see committed_index() note above)
        )
        if not edge_integrity.passed:
            return _CheckOutcome(
                rejection_type=RejectionType.OBLIGATION_VIOLATION,
                failure_reason=edge_integrity.failure_reason or "edge_target_unresolvable",
                failure_evidence=edge_integrity.failure_evidence or {},
            )
        notices.extend(edge_integrity.notices)
        performed.append({
            "check_type": "graph_edge_integrity",
            "result": "passed" if edge_integrity.applicable else "not_applicable",
        })

        return _CheckOutcome(notices=notices, checks_performed=performed)

    def _recheck_freshness_before_write(
        self,
        envelope_record: EventRecord,
        payload: dict[str, object],
        current_committed_seq: int,
    ) -> _DriftInfo | None:
        """M-0.5 §4.1 — write-pre TOCTOU re-check. Returns a _DriftInfo iff a NEW drift is found.

        Fires the (test-only) pre-write hook, then re-reads the committed max seq. If it is
        unchanged since the checks ran, there is nothing new to re-check → None (the common
        single-process case — no overhead beyond a tail read). If it changed (a path-B write
        landed in the check→write window), rebuild the index and re-run FreshnessDrift over the
        envelope's relevant subjects (read_set ∪ write_set proxy, same as §3.3.2). A drift now
        detected (an invalidating event touching a relevant subject after the envelope baseline)
        → return its _DriftInfo so attempt_commit rejects (DRIFT_DETECTED) instead of committing
        on a stale check. No new drift → None (the new events were irrelevant) → proceed to write.
        """
        if self._toctou_pre_write_hook is not None:
            self._toctou_pre_write_hook()

        new_max = self._event_log._read_max_seq_on_disk()
        if new_max == current_committed_seq:
            return None  # nothing committed in the window → no re-check needed

        # Re-run FreshnessDrift against the freshly-committed events. The window write invalidated
        # the log's cached index, so committed_index() rebuilds fresh here over the new snapshot
        # (RUN-038: reuse the log cache rather than a separate all_records() build).
        index = self._event_log.committed_index()
        # RUN-038: same relevant-subject set as the main §3.3.2 check (envelope read∪write ∪ capsule
        # neighborhood) so the TOCTOU re-check has the same coverage — a window write touching a
        # capsule-scope concept the envelope did not list is also caught here, not just in _run_checks.
        capsule_ref = str(payload.get("capsule_compiled_event_id") or "evt-no-capsule")
        relevant_subjects = freshness_relevant_subjects(index, capsule_ref, payload)
        as_of_seq_raw = payload.get("as_of_projection_seq", 0)
        as_of_seq = as_of_seq_raw if isinstance(as_of_seq_raw, int) else 0
        drift = check_freshness_drift(index, relevant_subjects, as_of_seq)
        if drift.passed:
            return None  # the new events did not invalidate any relevant subject → safe to write

        return _DriftInfo(
            drift_nodes=drift.drift_nodes,
            relevant_subjects=tuple(sorted(relevant_subjects)),
            invalidating_event_ids=drift.invalidating_event_ids,
            capsule_compiled_event_id=str(
                payload.get("capsule_compiled_event_id") or "evt-no-capsule",
            ),
        )

    def _build_and_append_rejected(
        self,
        *,
        envelope_record: EventRecord,
        transaction_id: str,
        batch_id: str,
        gate_run_id: str,
        rejection_type: RejectionType,
        recovery_of_abandoned: bool,
        failure_reason: str | None = None,
        failure_evidence: dict[str, str] | None = None,
        obligation_violations: list[ObligationViolation] | None = None,
        drift_detected_event_ref: str | None = None,
        audit_chain: list[dict[str, str]] | None = None,
    ) -> list[EventRecord]:
        """E.1.1-canonical rejected batch builder (envelope NOT in batch).

        Rejected batch = [gate-detected lifecycle events..., CommitRejected sentinel].
        §3.4.3: detected obligation violations become ObligationViolated events in this batch
        (path A, atomic with CommitRejected — M-0.5 holds violation-detection authority).
        envelope_event_id links to path-B envelope via sentinel payload.
        """
        # §7.4 lock-release strategy. scope_exceeded shares the DRIFT_DETECTED rejection_type with
        # freshness_drift (the M-0.1 §2.3.7 enum collapses both), but they differ on lock release:
        # freshness → no release (reassemble + retry, agent keeps the lock); scope_exceeded →
        # release (needs new capsule + new run). Distinguish by the precise failure_reason.
        produce_lock_release = (
            recovery_of_abandoned
            or rejection_type
            in {
                RejectionType.OBLIGATION_VIOLATION,
                RejectionType.CLAIM_BOUNDARY_EXCEEDED,
            }
            or failure_reason == "scope_exceeded"
        )
        gate_lifecycle: list[EventRecord] = []
        lock_release_event_ref: str | None = None
        if produce_lock_release:
            reason = (
                "abandoned_envelope_timeout"
                if recovery_of_abandoned
                else "rejection_default_strategy"
            )
            lock_intent = self._build_lock_released_intent(envelope_record, reason)
            lock_record = self._event_log.stamp_for_batch(
                lock_intent,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=len(gate_lifecycle),
            )
            gate_lifecycle.append(lock_record)
            lock_release_event_ref = lock_record.event_id

        # §3.4.3 — one ObligationViolated per detected violation, in the rejected batch.
        violation_event_ids: list[str] = []
        for violation in obligation_violations or []:
            ov_intent = self._build_obligation_violated_intent(envelope_record, violation)
            ov_record = self._event_log.stamp_for_batch(
                ov_intent,
                transaction_id=transaction_id,
                batch_id=batch_id,
                batch_position=len(gate_lifecycle),
            )
            gate_lifecycle.append(ov_record)
            violation_event_ids.append(ov_record.event_id)

        sentinel_intent = self._build_commit_rejected_intent(
            envelope_event_id=envelope_record.event_id,
            capsule_compiled_event_id=str(
                envelope_record.payload.get("capsule_compiled_event_id") or "evt-no-capsule",
            ),
            rejection_type=rejection_type,
            gate_run_id=gate_run_id,
            recovery_of_abandoned=recovery_of_abandoned,
            lock_release_event_ref=lock_release_event_ref,
            failure_reason=failure_reason,
            failure_evidence=failure_evidence,
            detected_violation_event_refs=violation_event_ids,
            drift_detected_event_ref=drift_detected_event_ref,
            audit_chain=audit_chain,
        )
        sentinel_record = self._event_log.stamp_for_batch(
            sentinel_intent,
            transaction_id=transaction_id,
            batch_id=batch_id,
            batch_position=len(gate_lifecycle),
        )
        batch = build_rejected_batch_e11(
            batch_id,
            transaction_id,
            gate_lifecycle,
            sentinel_record,
        )
        written = self._event_log.append_transaction_batch(batch)
        # written carries the authoritative (file-lock-assigned) seqs + hashes (F-019-12).
        return [envelope_record, *written]

    def _assert_no_protocol_invariant_retire(self, domain_intents: list[EventIntent]) -> None:
        """T-L0-31 — reject (raise) any ObligationRetired targeting a system_bootstrap obligation.

        Wires the previously-dead bootstrap.check_retire_allowed (M-0.6 §5.3 / Patch J) into the
        commit gate: the bootstrap red-line obligations are protocol invariants and cannot be
        deleted. capture_source is read from the obligation's committed ObligationCaptured event.
        """
        for intent in domain_intents:
            if intent.event_type is not EventType.OBLIGATION_RETIRED:
                continue
            oid = intent.payload.get("obligation_id")
            if not isinstance(oid, str) or not oid:
                continue
            capture_source = self._obligation_capture_source(oid)
            if capture_source is not None:
                check_retire_allowed(capture_source)  # raises for system_bootstrap

    def _obligation_capture_source(self, obligation_id: str) -> str | None:
        """capture_source from the obligation's committed ObligationCaptured event (latest)."""
        capture_source: str | None = None
        best_seq = -1
        for rec in self._event_log.get_events_by_type(EventType.OBLIGATION_CAPTURED):
            if rec.payload.get("obligation_id") != obligation_id:
                continue
            src = rec.payload.get("capture_source")
            if isinstance(src, str) and rec.sequence_number > best_seq:
                capture_source = src
                best_seq = rec.sequence_number
        return capture_source

    def _read_ownership_projection(self) -> dict[str, object] | None:
        """Read the M-0.2 §3.4 ownership projection, caught up to the committed log (T-L0-07).

        Built lazily over the conventional .towow/graph dir beside the event log, then caught up
        (idempotent, incremental) so the WriteConflict §9.2 lock check reads current lock state.
        Returns None if the projection has no materialized state yet.
        """
        from towow.l0.projection.projection import ProjectionStore

        graph_dir = self._event_log.log_path.parent / "graph"
        store = ProjectionStore(graph_dir)
        store.catchup(self._event_log)
        return store.read("ownership")

    @staticmethod
    def _build_lock_released_intent(
        envelope_record: EventRecord,
        release_reason: str,
    ) -> EventIntent:
        payload = envelope_record.payload
        return EventIntent(
            local_intent_id=f"lr-{uuid.uuid4().hex[:12]}",
            event_type=EventType.LOCK_RELEASED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={
                "target_entity_type": "task",
                "target_entity_id": payload.get("task_id", "unknown"),
                "transition_type": "modified",
                "after_state": {
                    "task_id": str(payload.get("task_id", "unknown")),
                    "run_id": str(payload.get("run_id", "unknown")),
                    "entities": payload.get("write_set", []),
                    "release_reason": release_reason,
                },
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.TASK,
                    entity_id=str(payload.get("task_id", "unknown")),
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )

    @staticmethod
    def _build_commit_rejected_intent(
        *,
        envelope_event_id: str,
        capsule_compiled_event_id: str = "evt-no-capsule",
        rejection_type: RejectionType,
        gate_run_id: str,
        recovery_of_abandoned: bool = False,
        lock_release_event_ref: str | None = None,
        failure_reason: str | None = None,
        failure_evidence: dict[str, str] | None = None,
        detected_violation_event_refs: list[str] | None = None,
        drift_detected_event_ref: str | None = None,
        audit_chain: list[dict[str, str]] | None = None,
    ) -> EventIntent:
        """E.1.1 Conflict 4/6/7: payload 强化 envelope_event_id / gate_run_id /
        recovery_of_abandoned.

        failure_reason / failure_evidence carry the precise check failure (e.g.
        envelope_malformed, write_set_exceeds_claims) since the coarse rejection_type
        enum (M-0.1 §2.3.7, 7 values) has no envelope-integrity category.
        detected_violation_event_refs lists the ObligationViolated events produced in this
        same rejected batch (§3.4.3) — RUN-029 review F1: was always [] even when violations
        were produced, so consumers couldn't find them via the sentinel.
        drift_detected_event_ref links the path-B DriftDetected (§3.3.2) produced before this
        batch, so consumers reach the drift evidence via the sentinel (CommitRejectedPayload).
        capsule_compiled_event_id (§6.5 redundant-for-query) is the envelope's real capsule
        ref — RUN-030 review CRITICAL-1: was hardcoded "evt-no-capsule", which broke the drift
        reentry path (L1 re-assembles from the capsule named on the rejected sentinel; the
        DriftDetected carried the real ref but the sentinel disagreed). recovery/bootstrap
        rejects keep the "evt-no-capsule" default (no capsule to name).
        """
        return EventIntent(
            local_intent_id=f"cr-{uuid.uuid4().hex[:12]}",
            event_type=EventType.COMMIT_REJECTED,
            event_category=EventCategory.COMMIT,
            payload={
                "envelope_event_id": envelope_event_id,
                "capsule_compiled_event_id": capsule_compiled_event_id,
                "verdict": "rejected",
                "rejection_type": rejection_type.value,
                "gate_run_id": gate_run_id,
                "recovery_of_abandoned": recovery_of_abandoned,
                "rejection_reasons": [
                    {
                        "check_type": failure_reason or rejection_type.value,
                        "failure_reason": failure_reason or f"{rejection_type.value} detected",
                        "evidence": failure_evidence or {},
                    },
                ],
                "rejected_local_intent_ids": [],
                "detected_violation_event_refs": detected_violation_event_refs or [],
                "drift_detected_event_ref": drift_detected_event_ref,
                "audit_chain": audit_chain or [],
                "lock_release_event_ref": lock_release_event_ref,
                # write/version conflict are rebase-retryable (§3.2 R6 lock retained).
                "rebase_allowed": rejection_type
                in {RejectionType.VERSION_CONFLICT, RejectionType.WRITE_CONFLICT},
            },
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.COMMIT_GATE.value,
                actor_id="m05_commit_gate",
            ),
            base_classification=BaseClassification.IMMUTABLE_TRUTH,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.CONCEPT,
                    entity_id="commit_gate",
                    role=SubjectRole.PRIMARY,
                ),
            ],
            schema_version="1.0.0",
        )

    # ─── scan_abandoned (M-0.5 §7) ─────────────────────────────────────────

    def scan_abandoned(self, lock_timeout_seconds: int = 1800) -> int:
        """E.1.1-canonical M-0.5 §7.3 abandoned envelope scan with CAS recovery.

        Protocol (per LEDGER Conflict 4/6/7):

          1. snapshot envelopes + current outcomes
          2. for each envelope past lock_timeout:
             a. CAS recheck — re-read latest outcomes (may be written concurrently)
             b. if still no outcome → produce recovery batch:
                  [LockReleased(release_reason=abandoned_envelope_timeout),
                   CommitRejected(envelope_event_id=env.event_id, recovery_of_abandoned=true)]
                NO synthetic envelope event is created (envelope stays on path B).

        envelope ↔ recovery outcome correlated via sentinel.envelope_event_id payload.

        Returns count of abandoned envelopes processed (excluding CAS-skipped no-ops).

        T-FIX-SCAN-ABANDONED (2026-06-10, 投产阻断): the pre-filter uses ONE up-front
        outcomes snapshot instead of a fresh full-ledger collection per envelope. The old
        shape was O(envelopes × ledger) — on the production ledger (25.5k records × 820
        envelopes ≈ 21M model_validate calls) a bare `towow submit` startup scan pinned a
        CPU for 5+ minutes. The CAS guarantee is NOT weakened: a snapshot can only be
        *stale-negative* (miss an outcome written after it was taken), never claim a
        nonexistent outcome, so every snapshot-filtered skip is correct, and each true
        candidate (no outcome in snapshot + past lock_timeout) still gets the verbatim
        per-candidate fresh _collect_envelope_outcomes() recheck below before any
        recovery batch is produced. Cost: 1 full collection + 1 per true candidate
        (normally 0..K with K≈0-2) instead of 1 per envelope.
        """
        envelopes = self._event_log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED)
        now = datetime.now(tz=UTC)
        abandoned_count = 0

        # O(N) pre-filter snapshot (see docstring): one full collection up front.
        outcomes_snapshot = self._collect_envelope_outcomes()

        for env in envelopes:
            if env.event_id in outcomes_snapshot:
                continue
            age = (now - env.timestamp).total_seconds()
            if age < lock_timeout_seconds:
                continue

            # E.1.1 CAS recheck (verbatim, per true candidate only): fetch fresh outcomes —
            # another process may have produced this envelope's outcome since the snapshot.
            fresh_outcomes = self._collect_envelope_outcomes()
            if env.event_id in fresh_outcomes:
                continue

            gate_run_id = f"gr-recover-{uuid.uuid4().hex[:12]}"
            transaction_id = f"tx-recover-{uuid.uuid4().hex[:12]}"
            batch_id = f"b-recover-{uuid.uuid4().hex[:12]}"

            # E.1.1: no synthetic envelope. Recovery batch = [LockReleased, CommitRejected].
            self._build_and_append_rejected(
                envelope_record=env,
                transaction_id=transaction_id,
                batch_id=batch_id,
                gate_run_id=gate_run_id,
                rejection_type=RejectionType.OBLIGATION_VIOLATION,  # placeholder for "abandoned"
                recovery_of_abandoned=True,
            )
            abandoned_count += 1
        return abandoned_count

    def _collect_envelope_outcomes(self) -> set[str]:
        """E.1.1 CAS helper: snapshot envelope_event_id set with existing outcomes.

        perf (commit-path-single-index-materialization@v1): reads the warm committed_index instead
        of a fresh full all_records() materialization. Same committed snapshot semantics — the CAS
        guarantee (a snapshot may be stale-negative, never claims a nonexistent outcome) is preserved
        because committed_index() reflects all committed appends up to this read.
        """
        outcomes: set[str] = set()
        for rec in self._event_log.committed_index().records():
            if rec.event_type not in {EventType.COMMIT_ACCEPTED, EventType.COMMIT_REJECTED}:
                continue
            env_id = rec.payload.get("envelope_event_id")
            if isinstance(env_id, str):
                outcomes.add(env_id)
        return outcomes
