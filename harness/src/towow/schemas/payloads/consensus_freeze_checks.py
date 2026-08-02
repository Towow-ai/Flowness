"""M-1.2 consensus freeze blocking check — consumer completeness.

# spec source:
#   src/towow/skills/engineering-consensus/SKILL.md §55-64 (F6 fix RUN-018):
#     "每个 ConceptCreated 后必跟一次 ConsumerListPublished scan ... freeze 前 verify 每
#      concept 都有对应 ConsumerListPublished, 否则不 freeze"
#   docs/DOGFOOD-RUN-026-FINDINGS.md F-026-1
#
# Why this module (DOGFOOD-RUN-026 F-026-1):
#   The consensus SKILL DESIGNED a fail-closed completeness check ("否则不 freeze"), but
#   consensus_freeze was a pure emit with zero blocking_check — the discipline was left as a
#   soft prompt, never wired into the gate. So a consensus that created concepts but skipped
#   their ConsumerListPublished froze clean (→ consumer-漏报 → InvalidationCascade misses).
#   Same class as F-019-7 (gate designed but unenforceable/unwired). This module wires the
#   designed check: every ConceptCreated in the consensus session must have a matching
#   ConsumerListPublished in the same session, else freeze is rejected.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.event_log.segments import iter_raw_event_lines

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from towow.l0.projection import ProjectionStore


def _read_events(events_path: Path) -> list[dict[str, object]]:
    # T-L0-04 (独立审抓出的漏迁): 跨所有物理段读 (base events.log + rotated events/hot/*.jsonl),
    # 不只读 base — 这是 M-1.2 consensus 消费方完整性 freeze 门 (fail-closed), 切段后若只读 base,
    # hot 段的 ConsumerListPublished 不可见 → 误判漏报 → 错误阻断合法 freeze (或 hot 段 ConceptCreated
    # 逃过完整性要求)。与另两把尺子 (plan_freezed/task_run_completed_checks) 同款迁段感知。
    out: list[dict[str, object]] = []
    for line in iter_raw_event_lines(events_path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _effective_kind(e: dict[str, object]) -> str:
    """Real event_type, unwrapping stub-rewrap NodeTouched (payload.kind)."""
    et = str(e.get("event_type", ""))
    if et == "NodeTouched":
        p = e.get("payload")
        if isinstance(p, dict) and isinstance(p.get("kind"), str):
            return str(p["kind"])
    return et


def _session_id(e: dict[str, object]) -> str:
    prov = e.get("provenance")
    return str(prov.get("session_id", "")) if isinstance(prov, dict) else ""


def _concept_id(e: dict[str, object]) -> str:
    """concept_id from payload.after_state.concept_id, fallback subject entity_id."""
    p = e.get("payload")
    if isinstance(p, dict):
        a = p.get("after_state")
        if isinstance(a, dict) and isinstance(a.get("concept_id"), str):
            return str(a["concept_id"])
    subs = e.get("subjects")
    if isinstance(subs, list) and subs and isinstance(subs[0], dict):
        ent = subs[0].get("entity_id")
        if isinstance(ent, str):
            return ent
    return ""


def _session_created_concept_ids(
    events: list[dict[str, object]],
    session_id: str,
) -> set[str]:
    """concept_ids ConceptCreated within this consensus session (stub-rewrap aware)."""
    created: set[str] = set()
    for e in events:
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) == "ConceptCreated":
            cid = _concept_id(e)
            if cid:
                created.add(cid)
    return created


def check_consumer_completeness(
    towow_dir: Path,
    session_id: str,
) -> tuple[bool, list[str]]:
    """Per consensus SKILL §64: every concept ConceptCreated in this consensus session
    must have a matching ConsumerListPublished in the same session.

    Returns (all_covered, missing_concept_ids). reused concepts (not ConceptCreated in
    this session — e.g. interview-seeded or earlier) are not required here.

    This is the EXISTENCE half (a ConsumerListPublished event exists per concept). The
    coverage SUBSTANCE half — its content actually covers the real consumers — is
    check_consumer_coverage (M12-F4).
    """
    events = _read_events(towow_dir / "events.log")
    created = _session_created_concept_ids(events, session_id)
    have_consumer: set[str] = set()
    for e in events:
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) == "ConsumerListPublished" and _concept_id(e):
            have_consumer.add(_concept_id(e))
    missing = sorted(created - have_consumer)
    return (not missing), missing


def _latest_published_consumers(
    events: list[dict[str, object]],
    session_id: str,
) -> dict[str, list[object]]:
    """concept_id → the consumers list from its LATEST ConsumerListPublished in this session.

    Events are in append order, so a later publish (a corrected scan / an M-2.1 update) wins
    over an earlier one — coverage is judged against what was last published, not a stale cut.
    """
    out: dict[str, list[object]] = {}
    for e in events:
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) != "ConsumerListPublished":
            continue
        cid = _concept_id(e)
        if not cid:
            continue
        consumers: list[object] = []
        payload = e.get("payload")
        if isinstance(payload, dict):
            after = payload.get("after_state")
            if isinstance(after, dict) and isinstance(after.get("consumers"), list):
                consumers = list(after["consumers"])
        out[cid] = consumers
    return out


def _consumer_identities(consumers: Sequence[object]) -> set[str]:
    """The set of consumer entity ids in a published / scanned consumer list.

    Accepts both the producer shape ({consumer_type, consumer_id, usage}) and the spec
    ConsumerRef shape ({entity_type, entity_id, ...}) — identity is consumer_id or entity_id.
    """
    ids: set[str] = set()
    for c in consumers:
        if isinstance(c, dict):
            cid = c.get("consumer_id") or c.get("entity_id")
            if isinstance(cid, str) and cid:
                ids.add(cid)
    return ids


def check_consumer_coverage(
    towow_dir: Path,
    session_id: str,
    store: ProjectionStore,
) -> tuple[bool, list[dict[str, object]]]:
    """M12-F4 substance gate (consensus 门验实质不只验存在): for every concept ConceptCreated in
    this session, the published ConsumerListPublished must COVER the consumers that the machine
    scanner (M-1.2 §7.1 initial_scan_consumers) independently re-derives from the live
    task_graph / concept_graph. A published list that misses a consumer the scanner finds is
    under-covered → a coverage gap. Existence of the ConsumerListPublished event is not enough:
    its content must match what is machine-rediscoverable (@live-fire-machine-check-contract —
    the agent cannot fake coverage with an empty/short list because the scanner recomputes it;
    rc-gate-verifies-claim-not-substance-flag-only — the claim门 is given a real substance check).

    A concept with genuinely zero discoverable consumers (scanner also finds none) legitimately
    publishes an empty list → no gap. Over-coverage (publishing consumers the scanner can't see —
    e.g. code-level consumers from the not-yet-implemented §7.1 source(2)) is allowed: the check is
    one-directional (published ⊇ machine-found).

    Returns (all_covered, gaps); each gap is
    {concept_id, missing_consumer_ids, published_consumer_count, machine_found_count}.
    """
    from towow.l1.consumer_scan import initial_scan_consumers

    events = _read_events(towow_dir / "events.log")
    created = _session_created_concept_ids(events, session_id)
    published = _latest_published_consumers(events, session_id)
    gaps: list[dict[str, object]] = []
    for concept_id in sorted(created):
        machine_ids = _consumer_identities(initial_scan_consumers(concept_id, store))
        published_ids = _consumer_identities(published.get(concept_id, []))
        missing = sorted(machine_ids - published_ids)
        if missing:
            gaps.append(
                {
                    "concept_id": concept_id,
                    "missing_consumer_ids": missing,
                    "published_consumer_count": len(published_ids),
                    "machine_found_count": len(machine_ids),
                },
            )
    return (not gaps), gaps


# ── Maintainability gate — existence-half (design-maintainability-gate@v1) ──────────
#
# Why this check (motivating instance: finding f-capability-registry-discovery-lists-superseded,
# evt-c0c0e928d6b4453ea9b533f4d5214d84):
#   owner directive (2026-07-07): "'可维护性' — 东西本身能不能被维护 — 要作为一个非常强的概念加入,
#   以后所有的设计都必须要过这一关" — mechanically enforced, not memory / soft advice.
#   The live-target-execution-evidence gate was superseded v1→v2, but the capability-registry
#   discovery query kept listing BOTH (didn't know which is current) because "retiring the old
#   edge" rode on a registrar remembering to do it. A design whose own maintainability rides on
#   memory recreates exactly the病 the harness exists to kill.
#
# This is the EXISTENCE half (@design-maintainability-gate@v1 checklist is DECLARED and structurally
# well-formed): the freeze carries a maintainability clause naming the concept(s) it attests and a
# non-empty justification for each canonical criterion. It is the same shape as
# check_consumer_completeness (existence) vs check_consumer_coverage (substance): here we verify the
# clause is present and every required key is a non-empty string — a structural, machine-checkable
# floor that is harder to fake than a bare bool flag. It does NOT judge whether the justifications
# are TRUE (that substance-half — does the discovery surface actually point to the current version,
# is the maintenance action really system-backed — needs a scanner integrated with
# single-pointer-retirement, registered as its own task/debt).
#
# ENFORCEMENT STATUS — LIVE (wired into the consensus freeze gate).
#   Called from run_batch_freeze_checks (check_id consensus.maintainability_clause_present, contributes
#   to all_passed) and registered in completeness.py REQUIRED_COMPLETENESS_CHECKS for
#   ENGINEERING_CONSENSUS_FREEZED → a freeze of ≥1 concept (a design) is rejected — at the CLI path AND
#   the un-bypassable M-0.5 总闸 (direct-emit bypass closed) — unless it carries a well-formed
#   --maintainability-clause. Empty freezes (no new concept) are not designs → not required. Covered by
#   tests/unit/test_maintainability_clause_check.py (ruler) + freeze integration tests (wiring).
#   SCOPE (honest): live ONLY for the EngineeringConsensusFreezed surface (consensus concepts). Other
#   design surfaces — PlanFreezed, skill/protocol authoring — are NOT yet gated (debt-maintainability-
#   gate-not-yet-fail-closed@v2 block ②). The authoring SKILLs (engineering-consensus / batch-freeze-
#   assemble) that teach agents to pass the clause are updated separately (were peer-locked at wiring
#   time); until then the fail-closed error message guides authors.

# The canonical maintainability criteria a design must justify at freeze (design-maintainability-gate@v1).
MAINTAINABILITY_CLAUSE_KEYS: tuple[str, ...] = (
    "standard_content_durable",  # criteria/content landed as canonical immutable record, machine-readable
    "discoverable_points_current",  # discoverable via a standard entry, surface points to the CURRENT version
    "updatable",  # supersede / evolution goes through a mechanism, not hand-edits
    "runtime_observable",  # observable in normal operation (or an explicit "n/a: <why>")
    "maintenance_not_by_memory",  # keeping the above true is system-backed, not "someone remembers to"
)


def check_maintainability_clause_present(
    clause: object,
) -> tuple[bool, dict[str, object]]:
    """design-maintainability-gate@v1 existence-half: the freeze must carry a maintainability
    clause that names the concept(s) it attests and gives a non-empty justification for every
    canonical criterion in MAINTAINABILITY_CLAUSE_KEYS.

    Passes iff: clause is a dict, ``concept_ids`` is a non-empty list, and every required key is a
    present, non-empty string. Absent / malformed / any-empty-key → fails (fail-closed). Returns
    (ok, evidence). Does not judge truth of the justifications — that is the substance-half.
    """
    if not isinstance(clause, dict):
        return False, {
            "reason": "no maintainability clause supplied (design-maintainability-gate@v1 existence-half)",
            "required_keys": list(MAINTAINABILITY_CLAUSE_KEYS),
        }
    concept_ids = clause.get("concept_ids")
    concept_ids_ok = isinstance(concept_ids, list) and any(
        isinstance(c, str) and c.strip() for c in concept_ids
    )
    missing_or_empty = [
        k
        for k in MAINTAINABILITY_CLAUSE_KEYS
        if not (isinstance(clause.get(k), str) and str(clause.get(k)).strip())
    ]
    ok = concept_ids_ok and not missing_or_empty
    return ok, {
        "attested_concept_ids": [c for c in (concept_ids or []) if isinstance(c, str)]
        if isinstance(concept_ids, list)
        else [],
        "concept_ids_present": concept_ids_ok,
        "missing_or_empty_keys": missing_or_empty,
        "required_keys": list(MAINTAINABILITY_CLAUSE_KEYS),
    }


def check_maintainability_clause_covers_batch(
    clause: object,
    frozen_concept_ids: set[str],
) -> tuple[bool, dict[str, object]]:
    """design-maintainability-gate@v1 SUBSTANCE-half: the clause must actually attest EVERY concept
    frozen this batch — every id in ``frozen_concept_ids`` must appear in ``clause.concept_ids``.

    This is the substance the existence-half can't give: check_maintainability_clause_present only
    verifies a well-formed clause EXISTS; without this, an agent games the gate by passing a clause
    that attests unrelated / made-up concept_ids while freezing something else. Same shape as
    check_consumer_coverage (published ⊇ machine-found): here attested ⊇ frozen-this-batch. The
    existence-half (clause present + 5 keys non-empty) is assumed already checked by the caller; this
    only judges coverage. Returns (covers, evidence); covers=False lists the unattested frozen concepts.
    """
    attested: set[str] = set()
    if isinstance(clause, dict):
        raw = clause.get("concept_ids")
        if isinstance(raw, list):
            attested = {c for c in raw if isinstance(c, str) and c.strip()}
    missing = sorted(frozen_concept_ids - attested)
    return (not missing), {
        "unattested_frozen_concepts": missing,
        "attested_concept_ids": sorted(attested),
        "frozen_concept_ids": sorted(frozen_concept_ids),
    }


__all__ = [
    "MAINTAINABILITY_CLAUSE_KEYS",
    "check_consumer_completeness",
    "check_consumer_coverage",
    "check_maintainability_clause_covers_batch",
    "check_maintainability_clause_present",
]
