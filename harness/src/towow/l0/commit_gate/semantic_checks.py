"""M-0.5 §3 semantic check pipeline — the real (non-schema-sanity) checks.

# spec source: 03-l0-truth-source/M-0.5-commit-gate-detailed-design.md
#   §3.1 VersionConsistencyCheck — capsule referenced by envelope not superseded
#   §3.2 WriteConflictCheck — write_set entity changed by a committed state_transition
#         after envelope.as_of_projection_seq (concurrent-write detection)
#   §3.3 DriftCheck (ScopeDrift + FreshnessDrift) — see drift_checks.py
#   §3.4 ObligationCheck (coverage + maintenance) — see obligation_checks.py
#
# RUN-029 第0波 ① — these were "simplified to schema sanity" / "always pass" in E.1
# (gate.py header lines 20-24). This module makes §3.1/§3.2 real physical checks over the
# committed event index. The honest boundary: §3.1 reads only the envelope's capsule ref +
# the supersede index (no capsule content needed → fully real). §3.2 reads the entity index
# + envelope.as_of_projection_seq (no capsule content needed → fully real). The checks that
# need the capsule's INNER content (touched_nodes / injected_obligations) — ScopeDrift,
# ObligationCoverage — are blocked by the capsule-stub gap (M-0.1 §3.5 stores summary counts
# only, conflicting with M-0.5 §3.3.1's `capsule.touched_nodes` assumption) and register
# debt rather than pretend (see drift_checks.py / obligation_checks.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l0.event_log.index import EventIndex

# Capsule references that are not real CapsuleCompiled events — VersionConsistency cannot
# verify a capsule that was never compiled. These are themselves a debt signal (the
# "527 提交写死 evt-stub-capsule" problem the RUN-029 brief named).
_STUB_CAPSULE_PREFIXES = ("evt-no-capsule", "evt-bootstrap-no-capsule", "evt-stub-capsule")

# subject roles that count as "this event changed the entity" (M-0.5 §3.2 / §3.3.2).
_WRITING_ROLES = frozenset({"primary", "affected"})


def _is_stub_capsule_ref(ref: str) -> bool:
    return any(ref == p or ref.startswith(p) for p in _STUB_CAPSULE_PREFIXES)


@dataclass(frozen=True)
class VersionConsistencyResult:
    """Outcome of VersionConsistencyCheck (M-0.5 §3.1)."""

    passed: bool
    skipped: bool = False
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notice: str | None = None


@dataclass(frozen=True)
class WriteConflictResult:
    """Outcome of WriteConflictCheck (M-0.5 §3.2)."""

    passed: bool
    skipped: bool = False
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notice: str | None = None


@dataclass(frozen=True)
class OwnershipConflictResult:
    """Outcome of the ownership write-lock conflict check (M-0.5 §9.2 / T-L0-07)."""

    passed: bool
    skipped: bool = False
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notice: str | None = None


def check_ownership_conflict(
    ownership_projection: dict[str, object] | None,
    write_set: list[object],
    submitter_run_id: str | None,
) -> OwnershipConflictResult:
    """M-0.5 §9.2 — reject if a write_set entity is held by a DIFFERENT live run's write lock.

    Reads the M-0.2 §3.4 ownership projection (TaskWriteSetClaimed → lock / CommitAccepted →
    release / LockReleased → zombie-lock handoff) — the spec's正规输入 for WriteConflict, vs
    the entity-index temporal scan in check_write_conflict (which catches "entity changed
    since my baseline", not "a live peer holds the write lock").

    Conflict identity = ``lock_holder_run_id`` (the run that holds the write lock). The check
    fails closed only for a lock whose holder run is *known* (non-null) and *different* from
    the submitting run — a genuine concurrent-run write collision. A self-held lock (same run)
    is the run committing what it claimed → no conflict.

    Honest degradation / safe-by-construction with the current producer reality: a lock whose
    ``lock_holder_run_id`` is null is not attributable to a live run, so it does not block. This
    is the only correct posture until T-L0-24/25 give envelopes canonical write_set boundaries
    (so CommitAccepted actually releases locks instead of leaving null-run zombie locks) and
    producers populate run_id on TaskWriteSetClaimed — tracked as debt. Boundary-less commits
    (no submitter run_id / empty write_set) skip with a notice, same posture as §3.2.
    """
    if not submitter_run_id:
        return OwnershipConflictResult(
            passed=True,
            skipped=True,
            notice="ownership_conflict_skipped: envelope has no run_id (boundary-less — "
            "caller not on submit wrapper); no live-run identity to lock-check",
        )
    if not write_set:
        return OwnershipConflictResult(
            passed=True, skipped=True, notice="ownership_conflict_skipped: empty write_set",
        )
    entities = (ownership_projection or {}).get("entities")
    if not isinstance(entities, list) or not entities:
        return OwnershipConflictResult(
            passed=True, skipped=True, notice="ownership_conflict_skipped: no ownership state",
        )
    locks: dict[tuple[str, str], dict[str, object]] = {}
    for e in entities:
        if (
            isinstance(e, dict)
            and e.get("write_locked")
            and isinstance(e.get("entity_type"), str)
            and isinstance(e.get("entity_id"), str)
        ):
            locks[(e["entity_type"], e["entity_id"])] = e
    for entry in write_set:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("entity_type")
        eid = entry.get("entity_id")
        if not isinstance(etype, str) or not isinstance(eid, str):
            continue
        lock = locks.get((etype, eid))
        if lock is None:
            continue
        holder_run = lock.get("lock_holder_run_id")
        if isinstance(holder_run, str) and holder_run and holder_run != submitter_run_id:
            return OwnershipConflictResult(
                passed=False,
                failure_reason="write_conflict",
                failure_evidence={
                    "locked_entity": f"{etype}:{eid}",
                    "lock_holder_run_id": holder_run,
                    "lock_owner_task_id": str(lock.get("owner_task_id")),
                    "submitter_run_id": submitter_run_id,
                },
            )
    return OwnershipConflictResult(passed=True)


def check_version_consistency(
    index: EventIndex,
    capsule_compiled_event_id: str,
) -> VersionConsistencyResult:
    """M-0.5 §3.1 — the capsule the envelope was compiled against must not be superseded.

    Physical: forward supersede-index lookup. In v3 a CapsuleCompiled is an immutable
    historical snapshot and is never superseded, so this normally passes — but it is a
    real check (not schema sanity): if a later event declares `superseded_event_id ==
    capsule_compiled_event_id`, the commit is rejected (capsule_superseded), carrying the
    superseding event id.

    A stub / sentinel capsule ref (evt-no-capsule, evt-stub-capsule*) cannot be checked —
    there is no compiled capsule to verify. Returns skipped=True + a notice that names the
    stub ref (the gate turns this into a debt signal, not a silent pass).
    """
    ref = (capsule_compiled_event_id or "").strip()
    if not ref or _is_stub_capsule_ref(ref):
        return VersionConsistencyResult(
            passed=True,
            skipped=True,
            notice=f"version_consistency_skipped: capsule ref '{ref or '(empty)'}' is a "
            "stub/sentinel, not a compiled capsule — cannot verify supersede status",
        )
    if index.lookup_event_id(ref) is None:
        # A non-stub ref that resolves to NO committed event is invalid (§3.0
        # invalid_capsule_reference). EnvelopeIntegrity (checks.py) only verifies the field
        # is a non-empty string — it has no event-log access to resolve the ref — so this
        # is the layer that owns the reject. Hard reject (not skip): silently passing a
        # fake-but-non-stub capsule ref is exactly the 假done pattern RUN-029 roots out.
        # (Known stub sentinels are handled above and skip-with-notice — the capsule
        # pipeline is a 第1波 stub, so forcing resolution would reject every current commit.)
        return VersionConsistencyResult(
            passed=False,
            failure_reason="invalid_capsule_reference",
            failure_evidence={"capsule_compiled_event_id": ref},
        )
    superseding = index.lookup_supersede(ref)
    if superseding:
        return VersionConsistencyResult(
            passed=False,
            failure_reason="capsule_superseded",
            failure_evidence={
                "capsule_compiled_event_id": ref,
                "superseded_by_event_id": superseding[0].event_id,
            },
        )
    return VersionConsistencyResult(passed=True)


def check_write_conflict(
    index: EventIndex,
    write_set: list[object],
    as_of_projection_seq: int,
) -> WriteConflictResult:
    """M-0.5 §3.2 — has a write_set entity been changed by a committed state_transition
    event after the envelope's as_of_projection_seq? (concurrent-write detection).

    Physical: entity-index lookup per write_set entry, filtered to state_transition events
    with the entity in a writing role (primary/affected) and sequence_number strictly after
    as_of_projection_seq. First conflict → reject(write_conflict) with the conflicting event
    id + entity (rebase-retryable, RejectionType.VERSION_CONFLICT).

    as_of_projection_seq <= 0 means the envelope carries no valid projection baseline
    (bootstrap / gate-derived stub envelope, M-0.4 §3.1 not populated by the caller). With
    no baseline there is no conflict window to scan — scanning from seq 0 would false-flag
    the entity's own legitimate prior edits — so the check is skipped with a notice (a
    debt signal that the caller is not on the submit wrapper). Single-process v3 commits
    are sequential, so a real conflict requires a genuine stale baseline.
    """
    if as_of_projection_seq <= 0:
        return WriteConflictResult(
            passed=True,
            skipped=True,
            notice="write_conflict_skipped: envelope as_of_projection_seq<=0 (no projection "
            "baseline — bootstrap or caller not on submit wrapper); no conflict window",
        )
    for entry in write_set:
        if not isinstance(entry, dict):
            continue
        etype = entry.get("entity_type")
        eid = entry.get("entity_id")
        if not isinstance(etype, str) or not isinstance(eid, str):
            continue
        for rec in index.lookup_entity(etype, eid):
            if rec.event_category.value != "state_transition":
                continue
            if rec.sequence_number <= as_of_projection_seq:
                continue
            if not any(
                s.entity_type.value == etype
                and s.entity_id == eid
                and s.role.value in _WRITING_ROLES
                for s in rec.subjects
            ):
                continue
            return WriteConflictResult(
                passed=False,
                failure_reason="write_conflict",
                failure_evidence={
                    "conflicting_entity": f"{etype}:{eid}",
                    "conflicting_event_id": rec.event_id,
                    "conflicting_seq": str(rec.sequence_number),
                    "as_of_projection_seq": str(as_of_projection_seq),
                },
            )
    return WriteConflictResult(passed=True)


__all__ = [
    "OwnershipConflictResult",
    "VersionConsistencyResult",
    "WriteConflictResult",
    "check_ownership_conflict",
    "check_version_consistency",
    "check_write_conflict",
]
