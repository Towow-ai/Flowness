"""M-1.2 §4.5 — @ 引用 lock_event 真解析 + target 等值校验 + 状态 hash (RUN-092 R2 件A/B).

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md
#     §3.5 (L273..L274): target.locked_at_state_event_id + locked_at_state_hash
#       (锁定时 target 的状态 hash — 用于检测漂移)
#     §4.5 validate (L510..L525): lock_event 必须真实存在, 且 event.target_entity_id ==
#       target.concept_id (target 等值), 否则 "Invalid lock_event"
#
# Why this module (RUN-092 R2, source: REALITY-AUDIT-V2 §1.2-b):
#   The §4.5 typed-id parser splits a locator into 5 parts but never resolved the optional
#   ``@lock_event_id`` against the real event log — validate_against_graph only checked concept
#   existence. A baseline probe forged a lock id ('@...@event-FORGED-999') and it passed cleanly.
#   This module wires the *online* half of §4.5 validate (lock realness + target 等值) with real
#   resolvers over the event log / concept_graph projection, plus computes locked_at_state_hash
#   (件A — the field the spec declared at §3.5 but code never carried).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from towow.schemas.payloads.at_reference_locator import (
    ParsedAtReference,
    validate_against_graph,
)

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog

# A trailing '@vN' is a concept VERSION anchor, not a committed-event lock. The §4.5 parser
# captures any '@<suffix>' as lock_event_id, but the established convention (consensus_invalidation
# / batch-freeze §8.4 / wave3 lifecycle-following tests) overloads '@v1' as a version reference
# resolved via the supersede chain — NOT the event log. Such anchors are exempt from event-lock
# realness; only event-id-shaped locks (the audit's forged 'event-abc123def', real 'evt-...') are
# enforced. This keeps RUN-092's forged-lock gate from breaking legitimate '@concept:foo@v1' refs.
_VERSION_ANCHOR = re.compile(r"v\d+")


def is_version_anchor(lock_event_id: str | None) -> bool:
    """True when the parsed lock suffix is a '@vN' concept-version anchor (not an event lock)."""
    return bool(lock_event_id) and _VERSION_ANCHOR.fullmatch(lock_event_id) is not None


def _slug_heads(concept_id: str) -> set[str]:
    """The names a concept_id can be referenced by in a typed @ locator.

    A locator ``concept_name`` is a slug (``[a-zA-Z][a-zA-Z0-9_.-]*``), while a concept_id carries
    an optional ``concept-`` prefix + ``@vN`` version suffix (e.g. ``concept-runs@v1``). Same
    resolution family as ``consensus_invalidation._resolves_to`` / batch-freeze §8.4 — kept
    consistent so the lock 等值 check matches name↔id the same way the rest of M-1.2 does.
    """
    base = concept_id.split("@", 1)[0]  # strip @vN
    return {concept_id, base, base.removeprefix("concept-")}


def resolve_concept_id_by_name(concept_graph: dict | None, concept_name: str) -> str | None:
    """concept_name → concept_id via concept_graph nodes (exact name / concept_id / slug-head).

    Returns None when no node matches (e.g. a forward reference to a concept created later in the
    same batch — the dangling check defers to freeze-time consistency-verify, not create time).
    """
    nodes = (concept_graph or {}).get("nodes") if isinstance(concept_graph, dict) else None
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        cid = n.get("concept_id")
        if not isinstance(cid, str) or not cid:
            continue
        if n.get("name") == concept_name or concept_name in _slug_heads(cid):
            return cid
    return None


def lock_event_concept_target(event_log: EventLog, lock_event_id: str) -> tuple[bool, str | None]:
    """``(found?, the concept the lock event acts on)``.

    Returns ``(False, None)`` when no committed event has this id (forged / dangling lock). When
    found, the second element is the locked event's concept subject entity_id — the PRIMARY-role
    concept subject if present, else the first concept subject, else None (a non-concept event id
    was pinned). The string is compared for 等值 against the referenced concept's id by
    ``validate_against_graph``.
    """
    rec = event_log.get_event(lock_event_id)
    if rec is None:
        return False, None
    primary: str | None = None
    first_concept: str | None = None
    for s in rec.subjects:
        if s.entity_type.value != "concept":
            continue
        if first_concept is None:
            first_concept = s.entity_id
        if s.role.value == "primary":
            primary = s.entity_id
            break
    return True, (primary or first_concept)


def lock_state_hash(event_log: EventLog, lock_event_id: str) -> str | None:
    """§3.5 locked_at_state_hash — the record_hash of the locked event (件A).

    The writer-stamped ``record_hash`` IS the immutable state fingerprint of that event version;
    pinning it lets a later drift check compare "is the target still at the snapshot I locked?".
    Returns None when the lock event does not resolve (forged lock — rejected upstream anyway).
    """
    rec = event_log.get_event(lock_event_id)
    return rec.record_hash if rec is not None else None


def lock_validation_errors(
    parsed_refs: list[ParsedAtReference],
    *,
    event_log: EventLog,
    concept_graph: dict | None,
) -> list[str]:
    """All §4.5 lock errors across a batch of parsed @ refs (empty = all locks valid).

    ``concept_exists`` is intentionally NOT wired here: forward references to not-yet-created
    concepts are allowed at create time (the dangling-concept guard lives at freeze-time
    consistency-verify). Only the lock-realness + target-等值 half is enforced on the create path.
    """

    def _resolve_id(name: str) -> str | None:
        return resolve_concept_id_by_name(concept_graph, name)

    def _resolve_lock(eid: str) -> tuple[bool, str | None]:
        return lock_event_concept_target(event_log, eid)

    errors: list[str] = []
    for p in parsed_refs:
        # '@vN' version anchors are resolved via the supersede chain, not the event log — exempt.
        if is_version_anchor(p.lock_event_id):
            continue
        errors.extend(
            validate_against_graph(
                p,
                concept_exists=None,
                resolve_concept_id=_resolve_id,
                resolve_lock_event=_resolve_lock,
            ),
        )
    return errors


__all__ = [
    "is_version_anchor",
    "lock_event_concept_target",
    "lock_state_hash",
    "lock_validation_errors",
    "resolve_concept_id_by_name",
]
