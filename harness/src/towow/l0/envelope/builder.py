"""M-0.4 Envelope builder — submit-wrapper derivation of read_set / write_set / claims.

# spec source: 03-l0-truth-source/M-0.4-envelope-detailed-design.md
#   §2 (L31..L116) full TransactionEnvelope schema
#   §3.1 (L124..L131) responsibility split — read_set / write_set / claims are
#     SYSTEM-derived by the submit wrapper (NOT agent self-filled), §7.1 rationale:
#     mechanical derivation > agent self-report.
#   §3.3 (L144..L169) derive rules:
#     read_set  = capsule files_to_read ∪ trace ∪ (fallback) projection neighborhood
#     write_set = git diff (code) ∪ emitted domain events (concept / task / obligation ...)
#     claims    = Planner TaskReadSetClaimed / TaskWriteSetClaimed events (empty if none)
#   §10 open: with the M-0.3 capsule still a stub (Phase E block 2), read_set has no
#     real projection neighborhood to bound against — §2 invariant "read_set 不允许空数组
#     （任何 run 至少读了 task package）" gives the floor: the task package itself.
#
# This module is the "正规包" half of debt-payoff block 1 (E-DEBT-PAYOFF-ROADMAP §1):
# turn "提交一笔改动" from a boundary-less stub into a real, derivable TransactionEnvelope.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from towow.schemas.enums import (
    EnvelopeClaimType,
    EnvelopeReadSetEntityType,
    EnvelopeWriteChangeType,
    EnvelopeWriteSetEntityType,
    EventType,
    SubjectRole,
)
from towow.schemas.envelope import (
    ActiveObligationDeclared,
    BlockingCheck,
    ClaimEntry,
    PatchEntry,
    ReadEntry,
    SelfCheck,
    SelfCheckResult,
    TransactionEnvelope,
    UncertaintyEntry,
    WriteEntry,
)

if TYPE_CHECKING:
    from towow.schemas.event_intent import EventIntent
    from towow.schemas.event_record import EventRecord

# Subject roles that count as "this event writes the entity" (M-0.5 §3.2 WriteConflict
# uses role ∈ {primary, affected}; the same set bounds write_set derivation).
_WRITE_ROLES = frozenset({SubjectRole.PRIMARY, SubjectRole.AFFECTED})

# Event-type name fragments → change_type. Default created (most domain events create).
_REMOVED_FRAGMENTS = ("Removed", "Deleted", "Archived", "Retired", "Tombstoned")

# change_type strength: when one entity appears with different change_types the net effect wins
# (removed > modified > created). Shared by derive_write_set + derive_actual_write_set_from_commit.
_CHANGE_STRENGTH = {
    EnvelopeWriteChangeType.CREATED: 0,
    EnvelopeWriteChangeType.MODIFIED: 1,
    EnvelopeWriteChangeType.REMOVED: 2,
}


def _infer_change_type(intent: EventIntent) -> EnvelopeWriteChangeType:
    """M-0.4 §3.3 change_type inference (created / modified / removed)."""
    if intent.supersede.is_supersede:
        return EnvelopeWriteChangeType.MODIFIED
    name = intent.event_type.value
    if any(frag in name for frag in _REMOVED_FRAGMENTS):
        return EnvelopeWriteChangeType.REMOVED
    if "Updated" in name or "Modified" in name or "Changed" in name:
        return EnvelopeWriteChangeType.MODIFIED
    return EnvelopeWriteChangeType.CREATED


def _subject_to_write_entity_type(value: str) -> EnvelopeWriteSetEntityType | None:
    """Map a SubjectEntityType value onto the write_set entity universe.

    EnvelopeWriteSetEntityType (7) is a subset of SubjectEntityType (13) by string value.
    Subject types with no write-set equivalent (task_edge / finding / rule / snapshot /
    capsule / information_need) return None and are not represented in write_set — those
    changes live in patches[] (patch_type=task_event / etc.) per M-0.4 §5.
    """
    try:
        return EnvelopeWriteSetEntityType(value)
    except ValueError:
        return None


def derive_write_set(domain_intents: list[EventIntent]) -> list[WriteEntry]:
    """M-0.4 §3.3 write_set derivation from emitted domain event intents.

    For each domain intent, every primary/affected subject mappable to the write_set
    entity universe becomes one WriteEntry. Deduplicated by (entity_type, entity_id);
    when the same entity appears with different change_types, the strongest wins
    (removed > modified > created) so the declaration reflects the net effect.
    """
    best: dict[tuple[EnvelopeWriteSetEntityType, str], EnvelopeWriteChangeType] = {}
    order: list[tuple[EnvelopeWriteSetEntityType, str]] = []
    for intent in domain_intents:
        change_type = _infer_change_type(intent)
        for subject in intent.subjects:
            if subject.role not in _WRITE_ROLES:
                continue
            entity_type = _subject_to_write_entity_type(subject.entity_type.value)
            if entity_type is None:
                continue
            key = (entity_type, subject.entity_id)
            if key not in best:
                best[key] = change_type
                order.append(key)
            elif _CHANGE_STRENGTH[change_type] > _CHANGE_STRENGTH[best[key]]:
                best[key] = change_type
    return [
        WriteEntry(entity_type=etype, entity_id=eid, change_type=best[(etype, eid)])
        for (etype, eid) in order
    ]


def derive_actual_write_set_from_commit(
    repo_dir: object, commit_sha: str,
) -> list[WriteEntry]:
    """M-0.4 §3.3 #1 — derive the ACTUAL write_set from a real git commit's name-status.

    This is the machine-computed fact (NOT agent self-report): each changed file becomes a FILE
    WriteEntry. git name-status → change_type: A→created / M,T→modified / D→removed / R→old
    removed + new created / C→new created. When an entity appears twice the strongest wins
    (removed > modified > created), matching derive_write_set's net-effect rule.

    Honesty contract (R1 / RUN-091): returns [] when repo_dir is not a git repo, commit_sha is
    not a real commit object, or git is unavailable — it NEVER fabricates a write_set. The
    submit / execution wrapper unions this (code writes) with derive_write_set(domain_intents)
    (non-code event writes) and uses the union to OVERRIDE the agent-declared write_set (§3.1:
    system-derived authoritative; agent self-report kept only as declared_write_set intent).
    """
    ref = str(commit_sha).strip()
    if not ref:
        return []
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_dir), "show", "--no-color", "--format=", "--name-status", ref],  # noqa: S607
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    status_change = {
        "A": EnvelopeWriteChangeType.CREATED,
        "M": EnvelopeWriteChangeType.MODIFIED,
        "T": EnvelopeWriteChangeType.MODIFIED,  # type change (mode/symlink)
        "D": EnvelopeWriteChangeType.REMOVED,
    }
    best: dict[str, EnvelopeWriteChangeType] = {}
    order: list[str] = []

    def _record(path: str, change: EnvelopeWriteChangeType) -> None:
        p = path.strip()
        if not p:
            return
        if p not in best:
            best[p] = change
            order.append(p)
        elif _CHANGE_STRENGTH[change] > _CHANGE_STRENGTH[best[p]]:
            best[p] = change

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0].strip()[:1]
        if code in ("R", "C") and len(parts) >= 3:
            # rename/copy: `R### old new` / `C### old new`. Old path is removed only on rename.
            if code == "R":
                _record(parts[1], EnvelopeWriteChangeType.REMOVED)
            _record(parts[2], EnvelopeWriteChangeType.CREATED)
        elif len(parts) >= 2:
            _record(parts[1], status_change.get(code, EnvelopeWriteChangeType.MODIFIED))
    return [
        WriteEntry(entity_type=EnvelopeWriteSetEntityType.FILE, entity_id=p, change_type=best[p])
        for p in order
    ]


def derive_read_set(
    *,
    task_id: str,
    capsule_compiled_event_id: str,
    capsule_files: list[str] | None = None,
    declared_read_set: list[ReadEntry] | None = None,
    neighborhood_concept_ids: list[str] | None = None,
) -> list[ReadEntry]:
    """M-0.4 §3.3 read_set derivation; guaranteed non-empty (§2 invariant).

    Sources, in order:
      #1 capsule files_to_read (FILE entries) ∪ agent-declared entries.
      #2 trace = the run's actual reads — NOT available at L0: §10 defers the read-trace
         mechanism to M-3.1 工程化外壳详设. Tracked as dependency_blocked debt (no trace source).
      #3 (Cap5) the capsule's projection NEIGHBORHOOD as the read_set UPPER BOUND when no trace
         exists (§3.3 #3): each neighborhood concept becomes a CONCEPT entry versioned by the
         capsule it came from. Now real because M-0.3 computes a real neighborhood and persists its
         concept ids on CapsuleCompiled (M-0.3 §2.2 / the Cap5 capsule field).

    Floor: the §2 invariant "any run reads at least its task package" supplies the minimum — a
    TASK read entry versioned by the capsule — only when nothing else produced an entry.
    """
    entries: list[ReadEntry] = []
    seen: set[tuple[str, str]] = set()

    def _add(entry: ReadEntry) -> None:
        key = (entry.entity_type.value, entry.entity_id)
        if key not in seen:
            seen.add(key)
            entries.append(entry)

    for path in capsule_files or []:
        _add(
            ReadEntry(
                entity_type=EnvelopeReadSetEntityType.FILE,
                entity_id=path,
                version=capsule_compiled_event_id,
            ),
        )
    # Declared entries before neighborhood so an explicitly-declared concept (with its own version)
    # is kept verbatim — the neighborhood upper bound only ADDS concepts the agent did not name.
    for declared in declared_read_set or []:
        _add(declared)
    for concept_id in neighborhood_concept_ids or []:
        if concept_id:
            _add(
                ReadEntry(
                    entity_type=EnvelopeReadSetEntityType.CONCEPT,
                    entity_id=concept_id,
                    version=capsule_compiled_event_id,
                ),
            )

    if not entries:
        # §2 floor: the task package the run was given.
        _add(
            ReadEntry(
                entity_type=EnvelopeReadSetEntityType.TASK,
                entity_id=task_id,
                version=capsule_compiled_event_id,
            ),
        )
    return entries


def derive_capsule_compiled_event_id(
    committed_records: list[EventRecord],
    *,
    fork_id: str | None,
) -> str | None:
    """M-0.4 §2/§3.1 — system-derive the run's CapsuleCompiled event_id (agent 不能伪造).

    §3.1 三层责任: capsule_compiled_event_id is capsule-injected — "系统自动关联". The submit
    wrapper does NOT trust the agent's value; it finds the run's real CapsuleCompiled event from
    the committed log by matching its payload.target_fork_id against the envelope's fork_id (M-0.3
    §2.8/§9.2 — CapsuleCompiled carries target_fork_id; the only persisted run↔capsule link).

    Re-entry (M-0.3 §2.9) re-runs the full pipeline → a fresh CapsuleCompiled for the same fork;
    the run actually executed against the LATEST one, so the highest-sequence match wins. Returns
    None when no CapsuleCompiled exists for this fork (e.g. bootstrap / capsule-less paths) so the
    caller keeps its floor token rather than fabricating an association.
    """
    if not fork_id:
        return None
    best_seq = -1
    best_id: str | None = None
    for rec in committed_records:
        if rec.event_type is not EventType.CAPSULE_COMPILED:
            continue
        if rec.payload.get("target_fork_id") != fork_id:
            continue
        if rec.sequence_number > best_seq:
            best_seq = rec.sequence_number
            best_id = rec.event_id
    return best_id


def _resolver_was_called(rec: EventRecord) -> bool:
    """True iff a ResolverDecisionMade payload records resolver_called=true (M-0.3 §9.1).

    The pipeline ALWAYS emits ResolverDecisionMade (Stage 5a) — even for a mechanical-only run it
    writes one with after_state.pipeline_stages.stage_4_semantic_judgment.resolver_called=false.
    M-0.4 §2 makes resolver_decision_event_id optional ("如果 resolver 被调用"), so only a真 invoke
    (resolver_called=true) is associated.
    """
    after = rec.payload.get("after_state")
    if not isinstance(after, dict):
        return False
    stages = after.get("pipeline_stages")
    if not isinstance(stages, dict):
        return False
    stage4 = stages.get("stage_4_semantic_judgment")
    return isinstance(stage4, dict) and stage4.get("resolver_called") is True


def derive_resolver_decision_event_id(
    committed_records: list[EventRecord],
    *,
    capsule_compiled_event_id: str | None,
) -> str | None:
    """M-0.4 §2/§3.1 — system-derive resolver_decision_event_id for the run's capsule (optional).

    §2: the field is set only "如果 resolver 被调用". The ResolverDecisionMade event belonging to
    the run shares the capsule's correlation_id (the M-0.3 pipeline stamps the same correlation_id
    on ResolverDecisionMade and CapsuleCompiled, §2.5/§2.8). So: find the resolved CapsuleCompiled
    event, read its provenance.correlation_id, and associate the ResolverDecisionMade event with
    that same correlation_id IFF its payload records resolver_called=true.

    Returns None when: no capsule resolved, the capsule event is not in the log, the capsule has no
    correlation_id, no matching ResolverDecisionMade, or the resolver was not actually invoked
    (mechanical-only run留痕). Capsule-injected — agent 不能伪造 (never reads an agent-supplied id).
    """
    if not capsule_compiled_event_id:
        return None
    correlation_id: str | None = None
    for rec in committed_records:
        if rec.event_id == capsule_compiled_event_id and rec.event_type is EventType.CAPSULE_COMPILED:
            correlation_id = rec.provenance.correlation_id
            break
    if not correlation_id:
        return None
    for rec in committed_records:
        if rec.event_type is not EventType.RESOLVER_DECISION_MADE:
            continue
        if rec.provenance.correlation_id != correlation_id:
            continue
        if _resolver_was_called(rec):
            return rec.event_id
    return None


def derive_as_of_projection_seq(
    committed_records: list[EventRecord],
    *,
    capsule_compiled_event_id: str | None,
) -> int | None:
    """M-0.4 §2 — system-derive as_of_projection_seq from the run's capsule (= bundle.as_of_seq).

    §2: as_of_projection_seq is "capsule assembly 时的 bundle.as_of_seq（系统自动关联）" — the
    WriteConflict / FreshnessDrift baseline. M-0.3 §2.8/§9.2 persist it on CapsuleCompiled
    (after the pipeline captured the read_consistent watermark), so the submit wrapper reads it
    straight from the resolved CapsuleCompiled event.

    Returns None when no capsule is resolved, the capsule event is absent, or the capsule carries
    no baseline (as_of_projection_seq<=0 — legacy / capsule-less留痕). None means "do not clobber
    the caller's own baseline with a meaningless 0"; a real >0 value overrides the agent value.
    """
    if not capsule_compiled_event_id:
        return None
    for rec in committed_records:
        if rec.event_id != capsule_compiled_event_id or rec.event_type is not EventType.CAPSULE_COMPILED:
            continue
        raw = rec.payload.get("as_of_projection_seq")
        if isinstance(raw, int) and raw > 0:
            return raw
        return None
    return None


def derive_neighborhood_concept_ids(
    committed_records: list[EventRecord],
    *,
    capsule_compiled_event_id: str | None,
) -> list[str]:
    """M-0.4 §3.3 #3 — the run's capsule projection neighborhood (read_set upper bound, Cap5).

    Reads neighborhood_concept_ids off the resolved CapsuleCompiled event (M-0.3 §2.2 persists the
    N-hop slice's concept ids). Empty when no capsule resolved / the field is absent (legacy
    capsule留痕) — read_set then falls back to #1 + floor. Capsule-injected — agent 不能伪造.
    """
    if not capsule_compiled_event_id:
        return []
    for rec in committed_records:
        if rec.event_id != capsule_compiled_event_id or rec.event_type is not EventType.CAPSULE_COMPILED:
            continue
        raw = rec.payload.get("neighborhood_concept_ids")
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, str) and c]
        return []
    return []


def derive_claims(claim_events: list[EventRecord] | None = None) -> list[ClaimEntry]:
    """M-0.4 §3.3 claims from Planner TaskReadSetClaimed / TaskWriteSetClaimed events.

    Empty list when the Planner did not pre-declare (claims are optional; M-0.5 §3.6
    then skips the boundary check with an informational notice). Each claim event's
    payload carries claimed entity refs under `claimed_entities` / `entities`.
    """
    if not claim_events:
        return []
    claims: list[ClaimEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for rec in claim_events:
        name = rec.event_type.value
        if "ReadSetClaimed" in name:
            claim_type = EnvelopeClaimType.READ_CLAIM
            after_key = "read_set"
        elif "WriteSetClaimed" in name:
            claim_type = EnvelopeClaimType.WRITE_CLAIM
            after_key = "write_set"
        else:
            continue
        # T-L0-23: 真实 Planner payload 是 after_state.{read_set|write_set} (list of EntityRef);
        # 回退 legacy claimed_entities/entities 顶层形 (防早期形 ripple)。此前只读 legacy 顶层 →
        # 对真实事件恒返空 (fake-done: 单测过但生产派不出真 claims)。
        after = rec.payload.get("after_state")
        if isinstance(after, dict) and isinstance(after.get(after_key), list):
            raw: object = after[after_key]
        else:
            raw = rec.payload.get("claimed_entities") or rec.payload.get("entities") or []
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            etype = item.get("entity_type")
            eid = item.get("entity_id")
            if not isinstance(etype, str) or not isinstance(eid, str) or not eid:
                continue
            mapped = _subject_to_write_entity_type(etype)
            if mapped is None:
                continue
            key = (mapped.value, eid, claim_type.value)
            if key in seen:
                continue
            seen.add(key)
            claims.append(ClaimEntry(entity_type=mapped, entity_id=eid, claim_type=claim_type))
    return claims


def build_envelope(
    *,
    envelope_id: str,
    task_id: str,
    run_id: str,
    fork_id: str,
    domain_intents: list[EventIntent],
    capsule_compiled_event_id: str,
    as_of_projection_seq: int,
    self_check: SelfCheck,
    schema_version: str = "1.0.0",
    capsule_files: list[str] | None = None,
    declared_read_set: list[ReadEntry] | None = None,
    neighborhood_concept_ids: list[str] | None = None,
    write_set: list[WriteEntry] | None = None,
    declared_write_set: list[WriteEntry] | None = None,
    claims: list[ClaimEntry] | None = None,
    claim_events: list[EventRecord] | None = None,
    patches: list[PatchEntry] | None = None,
    active_obligations_declared: list[ActiveObligationDeclared] | None = None,
    uncertainties: list[UncertaintyEntry] | None = None,
    completion_claims: list[str] | None = None,
    resolver_decision_event_id: str | None = None,
    submitted_at: datetime | None = None,
) -> TransactionEnvelope:
    """Assemble a complete, schema-valid TransactionEnvelope (M-0.4 §2 + §3).

    Agent-filled inputs: self_check / patches / uncertainties / active_obligations_declared.
    System-derived here: read_set / write_set / claims (M-0.4 §3.1 — the submit wrapper's job).
    Capsule-injected: capsule_compiled_event_id / resolver_decision_event_id /
      as_of_projection_seq.
    """
    # write_set: caller-resolved (agent-declared / submit-wrapper floored) overrides derivation.
    resolved_write_set = write_set if write_set is not None else derive_write_set(domain_intents)
    read_set = derive_read_set(
        task_id=task_id,
        capsule_compiled_event_id=capsule_compiled_event_id,
        capsule_files=capsule_files,
        declared_read_set=declared_read_set,
        neighborhood_concept_ids=neighborhood_concept_ids,
    )
    # claims: caller-resolved (agent-declared) overrides derivation from Planner claim events.
    resolved_claims = claims if claims is not None else derive_claims(claim_events)
    return TransactionEnvelope(
        envelope_id=envelope_id,
        task_id=task_id,
        run_id=run_id,
        fork_id=fork_id,
        read_set=read_set,
        write_set=resolved_write_set,
        declared_write_set=declared_write_set or [],
        claims=resolved_claims,
        active_obligations_declared=active_obligations_declared or [],
        patches=patches or [],
        uncertainties=uncertainties or [],
        completion_claims=completion_claims or [],
        self_check=self_check,
        capsule_compiled_event_id=capsule_compiled_event_id,
        resolver_decision_event_id=resolver_decision_event_id,
        as_of_projection_seq=as_of_projection_seq,
        schema_version=schema_version,
        submitted_at=submitted_at or datetime.now(tz=UTC),
    )


# ════════════════════════════════════════════════════════════════════════════════
#  canonicalize — submit-wrapper one-shot assembly of a stored-canonical envelope
#  (RUN-031 T-L0-24/25). Turns any boundary-less / stub envelope payload into an exact
#  TransactionEnvelope payload so read_envelope can reconstruct it (§6.2 不独立持久化).
# ════════════════════════════════════════════════════════════════════════════════

_STATUS_LITERALS = frozenset({"pending", "passed", "failed"})


def _coerce_self_check(raw: object) -> SelfCheck:
    """Coerce a raw self_check payload into a schema-valid SelfCheck (defensive).

    Pre-T-L0-24 emit paths store self_check with checks_run as bare strings and
    blocking_checks that may omit evidence on passed checks. The commit gate only reads
    blocking_checks[].{check_id,status} (completeness.py) + self_check.passed / blocking
    status — never evidence *content* — so supplying a structural evidence floor for passed
    checks preserves gate behavior while satisfying the BlockingCheck evidence invariant.
    """
    if not isinstance(raw, dict):
        return SelfCheck(passed=True)
    checks_run: list[SelfCheckResult] = []
    for c in raw.get("checks_run") or []:
        if isinstance(c, str) and c:
            checks_run.append(SelfCheckResult(check_type=c, passed=True))
        elif isinstance(c, dict) and isinstance(c.get("check_type"), str):
            checks_run.append(
                SelfCheckResult(
                    check_type=str(c["check_type"]),
                    passed=bool(c.get("passed", True)),
                    detail=c["detail"] if isinstance(c.get("detail"), str) else None,
                ),
            )
    blocking: list[BlockingCheck] = []
    for b in raw.get("blocking_checks") or []:
        if not isinstance(b, dict):
            continue
        cid = b.get("check_id")
        status = b.get("status")
        if not isinstance(cid, str) or status not in _STATUS_LITERALS:
            continue
        ev_raw = b.get("evidence")
        if isinstance(ev_raw, dict):
            evidence = {str(k): str(v) for k, v in ev_raw.items()}
        elif ev_raw:
            # f-glob-blocking-check-evidence-wiped: 非 dict 但非空的 evidence (旧 str 形态 / 任何
            # 生产者) 不再被静默丢成 floor —— 原文存进 {"summary": ...} 保留真痕 (闸跑没跑、什么
            # 结论在账本里可证)。canonical evidence 是 dict[str,str], 故 str() 归一。
            evidence = {"summary": str(ev_raw)}
        else:
            evidence = {}
        if status == "passed" and not evidence:
            # structural floor 仅当真没有任何 evidence (legacy 兼容: 旧 emit 路径 passed 漏 evidence);
            # gate reads status not content。有真 evidence 时绝不抹 (上面分支已保留)。
            evidence = {"check_id": cid}
        blocking.append(
            BlockingCheck(
                check_id=cid,
                status=status,
                evidence=evidence,
                detail=b["detail"] if isinstance(b.get("detail"), str) else None,
            ),
        )
    return SelfCheck(passed=bool(raw.get("passed", True)), checks_run=checks_run, blocking_checks=blocking)


def _coerce_model_list[ModelT: BaseModel](
    raw: object,
    model: type[ModelT],
    *,
    strict: bool = False,
) -> list[ModelT]:
    """Validate each dict in ``raw`` into ``model``; skip entries that do not conform."""
    out: list[ModelT] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(model.model_validate(item, strict=strict))
        except Exception:  # noqa: S112
            continue
    return out


def canonicalize_envelope_payload(
    raw_payload: dict[str, object],
    domain_intents: list[EventIntent],
    *,
    fallback_session_ref: str,
    prov_task_id: str | None = None,
    prov_run_id: str | None = None,
    prov_fork_id: str | None = None,
    claim_events: list[EventRecord] | None = None,
    committed_records: list[EventRecord] | None = None,
) -> dict[str, object]:
    """M-0.4 §3.1 — assemble a stored-canonical TransactionEnvelope payload from a raw one.

    The single submit-wrapper assembly point (T-L0-24): derives read_set/write_set/claims via
    build_envelope, coerces agent-filled sub-objects (self_check/patches/uncertainties/
    active_obligations) to their schemas, and supplies the §2 non-empty write_set floor. The
    returned dict is an exact TransactionEnvelope.model_dump — read_envelope reconstructs it
    without raising (T-L0-25). Ad-hoc non-schema fields (e.g. session_id) are dropped (the gate
    reads session via provenance fallback); identity fields fall back to provenance / the
    session ref so the canonical schema's non-empty invariants hold.

    M-0.4 §3.1 capsule-injected fields (Cap1): when ``committed_records`` is supplied, the
    capsule_compiled_event_id is SYSTEM-DERIVED from the run's real CapsuleCompiled event (matched
    by fork_id) — overriding any agent-supplied value (agent 不能伪造). Callers that do not pass
    committed_records keep the raw value (backward compatible — derivation is opt-in).
    """
    env_id = str(raw_payload.get("envelope_id") or "")
    if not env_id.startswith("env-"):
        env_id = f"env-{env_id}" if env_id else f"env-{fallback_session_ref}"
    task_id = str(raw_payload.get("task_id") or prov_task_id or f"session:{fallback_session_ref}")
    run_id = str(raw_payload.get("run_id") or prov_run_id or f"session:{fallback_session_ref}")
    fork_id = str(raw_payload.get("fork_id") or prov_fork_id or f"session:{fallback_session_ref}")
    # Cap1 (M-0.4 §3.1): system-derive capsule_compiled_event_id from the committed log; the
    # agent's raw value is only a fallback when no records are provided / no capsule for the fork.
    derived_capsule_id = (
        derive_capsule_compiled_event_id(committed_records, fork_id=fork_id)
        if committed_records is not None
        else None
    )
    capsule_id = str(derived_capsule_id or raw_payload.get("capsule_compiled_event_id") or "evt-no-capsule")
    # Cap2 (M-0.4 §2/§3.1): system-derive resolver_decision_event_id from the committed log via
    # the resolved capsule's correlation_id — only when the resolver was actually invoked. Falls
    # back to the raw value when no records are provided (backward compatible / opt-in).
    derived_resolver_id = (
        derive_resolver_decision_event_id(committed_records, capsule_compiled_event_id=derived_capsule_id)
        if committed_records is not None
        else None
    )
    # Cap3 (M-0.4 §2): as_of_projection_seq = the resolved capsule's bundle.as_of_seq (the
    # WriteConflict / FreshnessDrift baseline) — overrides the agent value. None (no capsule /
    # no baseline) falls back to the raw value (backward compatible).
    derived_as_of = (
        derive_as_of_projection_seq(committed_records, capsule_compiled_event_id=derived_capsule_id)
        if committed_records is not None
        else None
    )
    as_of_raw = derived_as_of if derived_as_of is not None else raw_payload.get("as_of_projection_seq", 0)
    as_of = as_of_raw if isinstance(as_of_raw, int) and as_of_raw >= 0 else 0
    # Cap5 (M-0.4 §3.3 #3): the run's capsule projection neighborhood = read_set upper bound (when
    # no real read trace exists — #2 trace is M-3.1 debt). Empty when no records / no capsule.
    neighborhood_ids = (
        derive_neighborhood_concept_ids(committed_records, capsule_compiled_event_id=derived_capsule_id)
        if committed_records is not None
        else []
    )

    declared_read = _coerce_model_list(raw_payload.get("read_set"), ReadEntry)
    declared_claims = _coerce_model_list(raw_payload.get("claims"), ClaimEntry)
    write_set = _coerce_model_list(raw_payload.get("write_set"), WriteEntry) or derive_write_set(domain_intents)
    # RUN-091 R1: the agent's declared write intent (preserved when it diverges from the derived
    # fact above) — carried verbatim into the stored canonical envelope for declared-vs-actual audit.
    declared_write = _coerce_model_list(raw_payload.get("declared_write_set"), WriteEntry)
    if not write_set:
        # §2 floor: a no-op-looking commit still declares its task as the write target. (The
        # gate still rejects a genuinely contentless RAW envelope on the un-floored payload —
        # this floor only keeps the STORED canonical envelope schema-valid / readable.)
        write_set = [
            WriteEntry.model_validate(
                {"entity_type": "task", "entity_id": task_id, "change_type": "modified"},
                strict=False,
            ),
        ]

    completion_raw = raw_payload.get("completion_claims")
    completion_claims = (
        [str(c) for c in completion_raw if isinstance(c, str)] if isinstance(completion_raw, list) else []
    )
    resolver_ref = derived_resolver_id or raw_payload.get("resolver_decision_event_id")

    env = build_envelope(
        envelope_id=env_id,
        task_id=task_id,
        run_id=run_id,
        fork_id=fork_id,
        domain_intents=domain_intents,
        capsule_compiled_event_id=capsule_id,
        as_of_projection_seq=as_of,
        self_check=_coerce_self_check(raw_payload.get("self_check")),
        declared_read_set=declared_read or None,
        neighborhood_concept_ids=neighborhood_ids or None,
        write_set=write_set,
        declared_write_set=declared_write or None,
        claims=declared_claims or (derive_claims(claim_events) if claim_events else None),
        claim_events=None,
        patches=_coerce_model_list(raw_payload.get("patches"), PatchEntry),
        active_obligations_declared=_coerce_model_list(
            raw_payload.get("active_obligations_declared"), ActiveObligationDeclared,
        ),
        uncertainties=_coerce_model_list(raw_payload.get("uncertainties"), UncertaintyEntry),
        completion_claims=completion_claims,
        resolver_decision_event_id=resolver_ref if isinstance(resolver_ref, str) else None,
    )
    return env.model_dump(mode="json")


# ════════════════════════════════════════════════════════════════════════════════
#  detail_ref backfill (T-L0-27 / M-0.4 §4.3). Envelope.patches[].detail_ref is
#  declarative ("I claim I wrote these events"); domain events are factual. §4.3:
#  "patches[i].detail_ref 指向这些 domain event 的 event_id（写入后回填——submit
#  wrapper 先生成 event_id，然后 M-0.1 writer 确认）". The submit wrapper declares each
#  evt-type patch's detail_ref against the domain intents' local_intent_id using the
#  placeholder token `local:<local_intent_id>` (so it is still `evt:`-prefixed and
#  passes PatchEntry.enforce_detail_ref_format BEFORE the events exist). After the gate
#  stamps the domain events (assigning their real, append-stable event_ids), this
#  function rewrites each `local:<local_intent_id>` token to the real event_id, so the
#  STORED envelope's patches point at the events actually written in the same batch.
# ════════════════════════════════════════════════════════════════════════════════

_LOCAL_REF_PREFIX = "local:"


def backfill_patch_detail_refs(
    raw_payload: dict[str, object],
    local_to_event_id: dict[str, str],
) -> dict[str, object]:
    """Return a copy of ``raw_payload`` with patch detail_ref placeholders resolved.

    Each comma-separated token of the form ``evt:local:<local_intent_id>`` is rewritten
    to ``evt:<real_event_id>`` using ``local_to_event_id`` (local_intent_id → stamped
    domain event_id). Tokens that are already real ``evt:<id>`` / ``git:<hash>`` refs, or
    whose local_intent_id is not in the mapping, are left untouched (a placeholder for an
    intent the gate did not stamp is preserved verbatim — surfacing a mis-declaration
    rather than silently fabricating a ref). Non-list/non-dict patches pass through.

    Mutation is on a shallow-then-per-patch copy, so the caller's raw payload is unchanged
    (the gate runs checks on the un-backfilled payload).
    """
    patches_raw = raw_payload.get("patches")
    if not isinstance(patches_raw, list):
        return raw_payload
    new_patches: list[object] = []
    for patch in patches_raw:
        if not isinstance(patch, dict):
            new_patches.append(patch)
            continue
        detail_ref = patch.get("detail_ref")
        if not isinstance(detail_ref, str) or _LOCAL_REF_PREFIX not in detail_ref:
            new_patches.append(patch)
            continue
        resolved_tokens: list[str] = []
        for token in (t.strip() for t in detail_ref.split(",")):
            if not token:
                continue
            body = token[len("evt:") :] if token.startswith("evt:") else token
            if body.startswith(_LOCAL_REF_PREFIX):
                local_id = body[len(_LOCAL_REF_PREFIX) :]
                real = local_to_event_id.get(local_id)
                resolved_tokens.append(f"evt:{real}" if real is not None else token)
            else:
                resolved_tokens.append(token)
        new_patch = dict(patch)
        new_patch["detail_ref"] = ",".join(resolved_tokens)
        new_patches.append(new_patch)
    new_payload = dict(raw_payload)
    new_payload["patches"] = new_patches
    return new_payload


# ════════════════════════════════════════════════════════════════════════════════
#  SemanticUpgradeDeclaration sibling-ref backfill (Cap4 / M-0.4 §7.5 + M-0.1 §3.2).
#  §7.5: the O-14 semantic interpretation is NOT an inlined envelope field — it is a
#  first-class sibling SemanticUpgradeDeclaration event co-submitted in the SAME accepted
#  batch as the envelope, whose after_state.sibling_event_id names "哪个主 event 的 sibling"
#  (M-0.1 §3.2). The main event is the envelope, whose event_id is only known after its
#  path-B write — so the agent submits the sibling with a `local:envelope` placeholder, and
#  the gate rewrites it to the real envelope event_id before the batch is appended (same
#  shape as T-L0-27 patch detail_ref backfill).
# ════════════════════════════════════════════════════════════════════════════════

_ENVELOPE_SIBLING_PLACEHOLDER = "local:envelope"


def backfill_semantic_sibling_refs(
    domain_records: list[EventRecord],
    *,
    envelope_event_id: str,
) -> list[EventRecord]:
    """Resolve SemanticUpgradeDeclaration sibling_event_id placeholders to the envelope event_id.

    For each SemanticUpgradeDeclaration record whose after_state.sibling_event_id is the
    ``local:envelope`` placeholder, return a copy with it rewritten to ``envelope_event_id`` (the
    main event the declaration is a sibling of). Records that are not SemanticUpgradeDeclaration,
    or whose sibling ref is already a real value, pass through unchanged. The caller's input
    records are never mutated (per-record model_copy), and a batch with no semantic sibling is a
    no-op — so this is safe to call unconditionally on the accepted-batch domain records.
    """
    out: list[EventRecord] = []
    for rec in domain_records:
        if rec.event_type is not EventType.SEMANTIC_UPGRADE_DECLARATION:
            out.append(rec)
            continue
        after = rec.payload.get("after_state")
        if not isinstance(after, dict) or after.get("sibling_event_id") != _ENVELOPE_SIBLING_PLACEHOLDER:
            out.append(rec)
            continue
        new_after = dict(after)
        new_after["sibling_event_id"] = envelope_event_id
        new_payload = dict(rec.payload)
        new_payload["after_state"] = new_after
        out.append(rec.model_copy(update={"payload": new_payload}))
    return out


# ════════════════════════════════════════════════════════════════════════════════
#  envelope_event_id backfill (RUN-094 件B / M-0.4 §4.3 — execution→commit 链可追溯).
#  TaskRunCompleted.after_state.envelope_event_id 命名"本次 task run 落进哪个 envelope 事件"。
#  那个 envelope 的真 event_id 在 domain intent 构造时还不可知 —— 它是 path-B write_direct 才
#  分配的, 跟 SemanticUpgradeDeclaration.sibling_event_id / patch detail_ref 同一个先有蛋问题。
#  所以 execution 用 `local:envelope` 占位符提交 TaskRunCompleted, gate 在 envelope path-B
#  write 之后把占位符回填成真 envelope event_id (同 backfill_semantic_sibling_refs 的形态)。
#  RUN-094 前硬编码 None → 25/25 TaskRunCompleted 全 null, execution→commit 链断 (审计 v2 §2 R4)。
# ════════════════════════════════════════════════════════════════════════════════


def backfill_envelope_event_id_refs(
    domain_records: list[EventRecord],
    *,
    envelope_event_id: str,
) -> list[EventRecord]:
    """Resolve `after_state.envelope_event_id == 'local:envelope'` placeholders to the real id.

    For each domain record whose after_state.envelope_event_id is the ``local:envelope``
    placeholder (TaskRunCompleted etc.), return a copy with it rewritten to ``envelope_event_id``
    — the path-B TransactionEnvelopeSubmitted event committed in the SAME accepted batch. Records
    without the placeholder (different event types, already-real values, legacy None) pass through
    unchanged, so this is safe to call unconditionally on every accepted-batch domain record set.
    The caller's input records are never mutated (per-record model_copy).
    """
    out: list[EventRecord] = []
    for rec in domain_records:
        after = rec.payload.get("after_state")
        if not isinstance(after, dict) or after.get("envelope_event_id") != _ENVELOPE_SIBLING_PLACEHOLDER:
            out.append(rec)
            continue
        new_after = dict(after)
        new_after["envelope_event_id"] = envelope_event_id
        new_payload = dict(rec.payload)
        new_payload["after_state"] = new_after
        out.append(rec.model_copy(update={"payload": new_payload}))
    return out


__all__ = [
    "backfill_envelope_event_id_refs",
    "backfill_patch_detail_refs",
    "backfill_semantic_sibling_refs",
    "build_envelope",
    "canonicalize_envelope_payload",
    "derive_as_of_projection_seq",
    "derive_capsule_compiled_event_id",
    "derive_claims",
    "derive_neighborhood_concept_ids",
    "derive_read_set",
    "derive_resolver_decision_event_id",
    "derive_write_set",
]
