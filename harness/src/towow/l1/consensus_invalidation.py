"""M-1.2 §4.3 — @ 引用失效响应策略 (T-L1-13).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §4.3 (L433..L470)
#     当 target concept supersede 时, 引用方按 on_target_supersede.response_policy 响应:
#       auto_accept_latest          → 跟随到 supersede 后新 event, emit AtReferenceAutoUpdated
#       pin_to_snapshot             → 锁定不变, emit AtReferenceTargetSuperseded(pinned_to_old)
#       explicit_decision_required  → emit AtReferenceTargetSuperseded(awaiting_decision,
#                                       blocks_commit_for_referer=true) [默认]
#
# Why this module (T-L1-13):
#   The CLI stored each @ reference but never carried a response policy, and a concept supersede
#   fired no per-reference response — the §4.3 失效响应 mechanism (the half of "@ 引用做最完整"
#   that keeps referers consistent across upstream change) was inert. This module computes, for a
#   target concept that just superseded, which active @ references point at it and what auxiliary
#   event each one's policy要求 emit. The CLI supersede path consumes plan_aux_events() and emits
#   the canonical AtReferenceAutoUpdated / AtReferenceTargetSuperseded events through commit gate.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.event_log.segments import iter_raw_event_lines
from towow.schemas.enums import EventType, LockingPolicy

if TYPE_CHECKING:
    from pathlib import Path

    from towow.l0.projection.projection import ProjectionStore


def _after(e: dict[str, object]) -> dict[str, object]:
    p = e.get("payload")
    if isinstance(p, dict):
        a = p.get("after_state")
        if isinstance(a, dict):
            return a
    return {}


def _resolves_to(stored_target_name: object, superseded_concept_id: str) -> bool:
    """Does a stored @ ref target_concept_name resolve to the just-superseded concept_id?

    The §4.5 parser treats '@vN' as a lock_event_id suffix, so a ref '@concept:concept-users@v1'
    stores target_concept_name='concept-users' (version stripped) while the concept_id passed by
    supersede is 'concept-users@v1'. Accept a match against the concept_id, its version-stripped
    head, or the slug head (concept-<slug>@vN → <slug>). Same resolution as the batch-freeze
    §8.4 cross-batch check (consensus_batch_freeze._matches_any_concept_id) — kept consistent.
    """
    if not isinstance(stored_target_name, str) or not stored_target_name:
        return False
    cid = superseded_concept_id
    if stored_target_name in {cid, cid.split("@", 1)[0]}:
        return True
    slug = cid.removeprefix("concept-").split("@", 1)[0]
    return stored_target_name == slug


def active_references_to(towow_dir: Path, superseded_concept_id: str) -> list[dict[str, object]]:
    """All AtReferenceAdded whose parsed target resolves to ``superseded_concept_id`` (not later removed).

    Reads the canonical log across all physical segments. A reference is dropped if a matching
    AtReferenceRemoved (by at_reference string) appears later (向后兼容 removal, though the
    consensus CLI does not currently remove refs).
    """
    added: list[dict[str, object]] = []
    removed_refs: set[str] = set()
    for line in iter_raw_event_lines(towow_dir):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        et = rec.get("event_type")
        if et == "AtReferenceAdded":
            a = _after(rec)
            if _resolves_to(a.get("target_concept_name"), superseded_concept_id):
                added.append({**a, "_event_id": rec.get("event_id")})
        elif et == "AtReferenceRemoved":
            a = _after(rec)
            ref = a.get("at_reference")
            if isinstance(ref, str):
                removed_refs.add(ref)
    return [r for r in added if r.get("at_reference") not in removed_refs]


def _record_hash_of(towow_dir: Path, event_id: str) -> str | None:
    """RUN-092 R2 件A — the record_hash (state fingerprint) of a committed event, or None.

    Lazy EventLog import keeps this module load-light; resolves through the same committed-visible
    Read surface the lock 等值 check uses. Returns None when the event does not resolve.
    """
    from towow.l0.event_log.event_log import EventLog

    rec = EventLog(towow_dir / "events.log").get_event(event_id)
    return rec.record_hash if rec is not None else None


def _policy_of(ref: dict[str, object]) -> LockingPolicy:
    """The stored response policy (默认 explicit_decision_required per §4.3)."""
    raw = ref.get("on_target_supersede_policy")
    if isinstance(raw, str):
        try:
            return LockingPolicy(raw)
        except ValueError:
            pass
    return LockingPolicy.EXPLICIT_DECISION_REQUIRED


def plan_aux_events(
    towow_dir: Path,
    superseded_concept_id: str,
    supersede_event_id: str,
) -> list[tuple[EventType, dict[str, object]]]:
    """M-1.2 §4.3 — for every active @ reference to ``superseded_concept_id``, the auxiliary event
    its policy要求 emit when the target just superseded (``supersede_event_id``).

    Returns a list of (event_type, after_state) the caller emits through commit gate. An empty
    list means nothing references the target (no response fires — 非误报).

    RUN-092 R2 件A §3.5: each aux event carries the new ``locked_at_state_hash`` — the record_hash
    of the supersede event (auto_accept_latest follows to it; pin/explicit record it for drift
    visibility). ``old_locked_at_state_hash`` echoes the ref's pinned snapshot hash when present.
    """
    new_state_hash = _record_hash_of(towow_dir, supersede_event_id)
    out: list[tuple[EventType, dict[str, object]]] = []
    for ref in active_references_to(towow_dir, superseded_concept_id):
        ref_id = str(ref.get("at_reference", ""))
        old_lock = str(ref.get("lock_event_id") or ref.get("_event_id") or "")
        old_state_hash = ref.get("locked_at_state_hash")
        policy = _policy_of(ref)
        if policy is LockingPolicy.AUTO_ACCEPT_LATEST:
            out.append(
                (
                    EventType.AT_REFERENCE_AUTO_UPDATED,
                    {
                        "reference_id": ref_id,
                        "old_lock_event_id": old_lock,
                        "new_lock_event_id": supersede_event_id,
                        "old_locked_at_state_hash": old_state_hash,
                        # auto_accept_latest 跟随到新 event → 新锁状态 hash 更新为 supersede 事件指纹
                        "locked_at_state_hash": new_state_hash,
                        "target_concept_name": superseded_concept_id,
                    },
                ),
            )
        elif policy is LockingPolicy.PIN_TO_SNAPSHOT:
            out.append(
                (
                    EventType.AT_REFERENCE_TARGET_SUPERSEDED,
                    {
                        "reference_id": ref_id,
                        "old_lock_event_id": old_lock,
                        "new_target_event_id": supersede_event_id,
                        # pin 不跟随: 锁状态 hash 保持旧 snapshot; 仅记录新 target 指纹供漂移可见
                        "locked_at_state_hash": old_state_hash,
                        "new_target_state_hash": new_state_hash,
                        "target_concept_name": superseded_concept_id,
                        "status": "pinned_to_old",
                        "blocks_commit_for_referer": False,
                    },
                ),
            )
        else:  # explicit_decision_required (默认)
            out.append(
                (
                    EventType.AT_REFERENCE_TARGET_SUPERSEDED,
                    {
                        "reference_id": ref_id,
                        "old_lock_event_id": old_lock,
                        "new_target_event_id": supersede_event_id,
                        "locked_at_state_hash": old_state_hash,
                        "new_target_state_hash": new_state_hash,
                        "target_concept_name": superseded_concept_id,
                        "status": "awaiting_decision",
                        "blocks_commit_for_referer": True,
                    },
                ),
            )
    return out


def detect_stale_references(store: ProjectionStore) -> list[dict[str, object]]:
    """M-1.2 §4.6 — 扫 at_reference_graph projection 列出 stale @ 引用。

    A reference is stale when its target concept has superseded but the reference did not follow
    (pin_to_snapshot / explicit_decision_required → the §4.3 response set is_stale=true on the
    projection node). auto_accept_latest references followed the supersede (AtReferenceAutoUpdated
    reset is_stale=false) and references whose target never changed were never flagged — so
    neither is reported (非误报). Only active references are considered.

    Reads the materialized at_reference_graph projection (the spec §4.6 query surface). The caller
    is responsible for catching the store up to the desired watermark first.
    """
    graph = store.read("at_reference_graph") or {}
    refs_raw = graph.get("references")
    refs = refs_raw if isinstance(refs_raw, list) else []
    stale: list[dict[str, object]] = []
    for r in refs:
        if not isinstance(r, dict):
            continue
        if r.get("is_active") is False:
            continue
        if not bool(r.get("is_stale")):
            continue
        stale.append(
            {
                "reference_id": r.get("reference_id"),
                "source_concept_id": r.get("source_concept_id"),
                "target_concept_name": r.get("target_concept_name"),
                "locked_event_id": r.get("locked_event_id"),
                "target_superseded_at_event_id": r.get("target_superseded_at_event_id"),
                "staleness_status": r.get("staleness_status"),
                "on_target_supersede_policy": r.get("on_target_supersede_policy"),
            },
        )
    return stale


__all__ = [
    "active_references_to",
    "detect_stale_references",
    "plan_aux_events",
]
