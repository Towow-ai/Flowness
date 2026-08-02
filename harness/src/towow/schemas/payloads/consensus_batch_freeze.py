"""M-1.2 §8 — 分批冻结协议 (batch freeze) checks — T-L1-18.

# spec source:
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §8
#     §8.3 Batch 提交协议 — 3 blocking_checks (L827..L838):
#         consensus.batch_concepts_all_committed
#         consensus.batch_internal_consistency  (本 batch 内部无矛盾)
#         consensus.batch_no_dangling_references (本 batch 引用的概念都已存在)
#     §8.4 跨 Batch 影响 (L840..L846):
#         batch-N 引用 batch-(N-1) 已冻结概念 = 合法
#         batch-N 引用未来 batch-(N+1) 概念 = 非法 (commit gate 拒)
#         batch-N 内部互相引用 = 合法
#     §8.5 Batch supersede (L848..L858): 不删旧 freeze event; 新 batch 带 supersede 链。
#
# Why this module (T-L1-18, 源表): consensus freeze 之前是整体一次冻结 (no --batch-number),
#   §8 设计了分批冻结 (R1 拍板) 但 CLI 无 batch 参数 + 无 §8.3 三 check + 无 §8.4 跨 batch 门。
#   This module is the §8 协议落地: each batch freeze runs the 3 §8.3 checks; §8.4 cross-batch
#   reference validity (referencing a concept defined neither in this session nor a previously
#   frozen batch of the same plan = dangling forward reference → rejected fail-closed).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from towow.l0.event_log.segments import iter_raw_event_lines
from towow.schemas.payloads.consensus_freeze_checks import (
    check_consumer_completeness,
    check_consumer_coverage,
    check_maintainability_clause_covers_batch,
    check_maintainability_clause_present,
)

if TYPE_CHECKING:
    from pathlib import Path

    from towow.l0.projection import ProjectionStore


def _read_events(towow_dir: Path) -> list[dict[str, object]]:
    """Span all physical segments (base + rotated hot) — same 迁段感知 as the sibling尺子."""
    out: list[dict[str, object]] = []
    for line in iter_raw_event_lines(towow_dir):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _effective_kind(e: dict[str, object]) -> str:
    et = str(e.get("event_type", ""))
    if et == "NodeTouched":
        p = e.get("payload")
        if isinstance(p, dict) and isinstance(p.get("kind"), str):
            return str(p["kind"])
    return et


def _session_id(e: dict[str, object]) -> str:
    prov = e.get("provenance")
    return str(prov.get("session_id", "")) if isinstance(prov, dict) else ""


def _after(e: dict[str, object]) -> dict[str, object]:
    p = e.get("payload")
    if isinstance(p, dict):
        a = p.get("after_state")
        if isinstance(a, dict):
            return a
    return {}


def session_concept_ids(towow_dir: Path, session_id: str) -> set[str]:
    """concept_ids ConceptCreated within this consensus session."""
    out: set[str] = set()
    for e in _read_events(towow_dir):
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) == "ConceptCreated":
            cid = _after(e).get("concept_id")
            if isinstance(cid, str):
                out.add(cid)
    return out


def _is_supersede_proposal(e: dict[str, object]) -> bool:
    """True iff this ConceptGraphProposal is a supersede (F-019-5), not a creation proposal.

    §8.5 — a batch may legally carry supersede chains whose entity_id points at an OLD concept
    (frozen in a prior batch, or a brief seed). Those targets are by design never ConceptCreated
    in this session, so they must not count as "proposed but uncommitted" in §8.3 check 1.
    Discriminators (either marks it): top-level supersede.is_supersede (M-0.5 novelty-checked
    canonical flag) or payload.transition_type == "superseded" (set by the supersede CLI).
    """
    sup = e.get("supersede")
    if isinstance(sup, dict) and sup.get("is_supersede") is True:
        return True
    p = e.get("payload")
    return isinstance(p, dict) and p.get("transition_type") == "superseded"


def pending_uncommitted_concept_ids(towow_dir: Path, session_id: str) -> set[str]:
    """§8.3 check 1 — concept_ids referenced by a ConceptGraphProposal in this session that
    never reached a committed ConceptCreated in the same session (an uncommitted hole).

    A clean batch has zero of these (empty set). This makes "all proposed concepts committed"
    decidable without requiring the batch to be non-empty (向后兼容 freeze-empty-consensus).

    Supersede proposals (§8.5) are excluded: their entity_id is the superseded OLD concept,
    not a creation proposal — counting it would make every spec-legal supersede-carrying batch
    unfreezable (hit live 2026-06-10, plan-landing-closeout seed codify-supersede).
    """
    committed = session_concept_ids(towow_dir, session_id)
    proposed: set[str] = set()
    for e in _read_events(towow_dir):
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) != "ConceptGraphProposal":
            continue
        if _is_supersede_proposal(e):
            continue
        changes = _after(e).get("proposed_changes")
        if isinstance(changes, list):
            for ch in changes:
                if isinstance(ch, dict) and isinstance(ch.get("entity_id"), str):
                    proposed.add(str(ch["entity_id"]))
    return proposed - committed


def session_superseded_concept_ids(towow_dir: Path, session_id: str) -> set[str]:
    """concept_ids superseded (replaced) within this session via a supersede ConceptGraphProposal.

    Conflict-20 fix part 2: a superseded concept is replaced by its successor (which carries the
    live relationships, e.g. as edges). Its now-stale @-references must NOT be dangling-checked —
    the replacement concept, not the dead version, is what the batch freezes. Without this, a
    spec-legal in-batch supersede (old version had a cross-ref, new version expresses it differently)
    would be permanently unfreezable. Discriminated by the same _is_supersede_proposal helper §8.3
    check-1 uses, so the two checks agree on what "superseded" means.
    """
    out: set[str] = set()
    for e in _read_events(towow_dir):
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) != "ConceptGraphProposal":
            continue
        if not _is_supersede_proposal(e):
            continue
        changes = _after(e).get("proposed_changes")
        if isinstance(changes, list):
            for ch in changes:
                if isinstance(ch, dict) and isinstance(ch.get("entity_id"), str):
                    out.add(str(ch["entity_id"]))
    return out


def session_at_reference_targets(towow_dir: Path, session_id: str) -> list[str]:
    """Target concept names of every AtReferenceAdded in this session (§4 typed-id parse).

    Uses the materialized target_concept_name (T-L1-12). Falls back to nothing if absent.

    Conflict-20 fix part 2: @-refs whose source_concept_id was superseded in this session are
    skipped — the dead version's stale refs are moot once its successor replaces it.
    """
    superseded = session_superseded_concept_ids(towow_dir, session_id)
    out: list[str] = []
    for e in _read_events(towow_dir):
        if _session_id(e) != session_id:
            continue
        if _effective_kind(e) == "AtReferenceAdded":
            a = _after(e)
            src = a.get("source_concept_id")
            if isinstance(src, str) and src in superseded:
                continue
            tgt = a.get("target_concept_name")
            if isinstance(tgt, str) and tgt:
                out.append(tgt)
    return out


def previously_frozen_concept_ids(towow_dir: Path, plan_id: str) -> set[str]:
    """concept_ids frozen in earlier EngineeringConsensusFreezed batches of the same plan (§8.4).

    A batch-N reference to a concept frozen in batch-(N-1) is legal; this set is what makes that
    legality decidable.
    """
    out: set[str] = set()
    for e in _read_events(towow_dir):
        if _effective_kind(e) != "EngineeringConsensusFreezed":
            continue
        a = _after(e)
        if a.get("plan_id") != plan_id:
            continue
        frozen = a.get("frozen_concept_ids")
        if isinstance(frozen, list):
            out.update(str(c) for c in frozen)
    return out


def all_frozen_concept_ids(towow_dir: Path) -> set[str]:
    """concept_ids frozen in ANY plan's EngineeringConsensusFreezed batches.

    Conflict-20 fix part 1: §8.4's dangling check must treat a reference to a concept already
    frozen in *any* plan as resolvable, not just one frozen in the SAME plan's prior batch. A
    cross-plan reference to an existing frozen concept (e.g. a global premise frozen by another
    plan) is a backward reference to something that already exists — NOT a forward-dangling ref to
    a not-yet-defined future concept (which is what §8.4 fail-closed is meant to catch). The old
    same-plan-only restriction (previously_frozen_concept_ids) wrongly rejected legitimate
    cross-plan reuse (R12 hit this; documented as Conflict 20).
    """
    out: set[str] = set()
    for e in _read_events(towow_dir):
        if _effective_kind(e) != "EngineeringConsensusFreezed":
            continue
        frozen = _after(e).get("frozen_concept_ids")
        if isinstance(frozen, list):
            out.update(str(c) for c in frozen)
    return out


def cia_downstream_scan(
    this_batch_concepts: set[str],
    store: ProjectionStore,
) -> dict[str, object]:
    """M-2.1 §4.1 消费方发现 caller-1 — for every concept frozen THIS batch, run a real
    CIA forward_slice (沿 impact_graph 概念出边 BFS, depth ≤ 5) and collect the transitive
    downstream entities (depth ≥ 1) it reaches, minus the batch's own concepts.

    This is the "发现并记录" half of M-2.1 §4 (not a blocker): freezing a new concept can have
    transitive downstream impact that the in-batch consumer-scan (扫 task read/write set) does NOT
    surface — consumer_scan answers "who reads this concept's fields", forward_slice answers "what
    is reachable along the concept dependency/reference edges (传递性 N 跳)". Both must run; they
    抓不同的东西。The result is recorded as evidence so a downstream review can see, at freeze time,
    the full transitive blast radius of the new concepts.

    Returns the evidence dict (always status=passed at the check level — present=被 gate 要求 才是
    enforce; 见 run_batch_freeze_checks / completeness.py). Empty result (图无边/无下游) is still a
    successful scan: downstream_entities=[] + projection_watermark proves the scan ran and is
    reproducible (re-run forward_slice at that watermark → same answer).

    Why a ProjectionStore (not towow_dir): forward_slice reads the materialized impact_graph
    (二阶 projection from concept_graph.edges). The store must already be caught up to the events
    being frozen — run_batch_freeze_checks passes the freeze command's already-catchup'd store
    (or builds one from towow_dir + events.log catchup when none is given).
    """
    from towow.l1.cia_query import CIAQueryService

    cia = CIAQueryService(store)
    scanned = sorted(this_batch_concepts)
    seen: set[str] = set()
    # Collect (depth, entity_id) → entry so the sort key stays int/str (not object-typed dict values).
    collected: list[tuple[int, str, dict[str, object]]] = []
    # Seed watermark from the store so an empty batch (zero concepts to scan) still records the real
    # projection position — same value every forward_slice would return (= store.cursor-1).
    watermark = max(0, store.cursor - 1)
    for concept_id in scanned:
        result = cia.forward_slice(concept_id)
        watermark = result.projection_watermark
        for hit in result.results:
            # depth ≥ 1 by construction (start node never in results); 去掉本批 concepts —
            # 本批内部互相依赖是已知的 (§8.4 in-batch ref 合法), 传递性下游指批 *外* 受影响 entity.
            target_id = hit.entity.entity_id
            if hit.depth < 1 or target_id in this_batch_concepts or target_id in seen:
                continue
            seen.add(target_id)
            collected.append(
                (
                    hit.depth,
                    target_id,
                    {
                        "entity_id": target_id,
                        "entity_type": hit.entity.entity_type,
                        "depth": hit.depth,
                        "path": list(hit.path),
                    },
                ),
            )
    collected.sort(key=lambda t: (t[0], t[1]))
    downstream: list[dict[str, object]] = [entry for _depth, _tid, entry in collected]
    return {
        "scanned_concepts": scanned,
        "downstream_entities": downstream,
        "projection_watermark": watermark,
    }


def run_batch_freeze_checks(
    towow_dir: Path,
    session_id: str,
    plan_id: str,
    store: ProjectionStore | None = None,
    maintainability_clause: object = None,
) -> tuple[bool, list[dict[str, object]], set[str]]:
    """§8.3 three blocking_checks + M-2.1 §4 CIA downstream scan. Returns
    (all_passed, checks, this_batch_concept_ids).

    The returned concept_ids set is what the freeze payload records as frozen_concept_ids
    (so the next batch's §8.4 cross-batch reference check can resolve against it).

    ``store`` (optional) — a ProjectionStore already caught up to the freeze point, reused by the
    M-2.1 §4 cia_downstream_scan check (the freeze command passes its freeze_store so the scan
    doesn't re-catchup). When None, a store is built from ``towow_dir`` and caught up to
    ``events.log`` here (so the scan still reads the live materialized impact_graph).
    """
    this_batch = session_concept_ids(towow_dir, session_id)
    prior_frozen = previously_frozen_concept_ids(towow_dir, plan_id)
    pending = pending_uncommitted_concept_ids(towow_dir, session_id)
    # Built once here and reused by BOTH the consumer-coverage substance check (M12-F4) and the
    # M-2.1 §4 cia_downstream_scan — both read the materialized task_graph / impact_graph at the
    # freeze point. The freeze CLI passes its already-catchup'd freeze_store; unit callers get one
    # built from towow_dir + events.log.
    scan_store = store if store is not None else _build_caught_up_store(towow_dir)
    checks: list[dict[str, object]] = []

    # §8.3 check 1 — batch_concepts_all_committed: every concept proposed this batch must be
    # committed (no ConceptCreated stuck as a rejected/pending proposal). The literal §8.3 wording
    # ("batch 概念全部 ConceptCreated 完成后") is "all proposed concepts committed", NOT "batch
    # must be non-empty" — an empty consensus trivially has all (zero) concepts committed (向后兼容
    # 既有 freeze-empty-consensus 用法). A ConceptGraphProposal whose target concept_id never
    # reached a committed ConceptCreated in this session is an uncommitted hole → fail.
    committed_ok = not pending
    checks.append(
        {
            "check_id": "consensus.batch_concepts_all_committed",
            "status": "passed" if committed_ok else "failed",
            "evidence": {
                "committed_concept_count": len(this_batch),
                "uncommitted_proposed_concepts": sorted(pending),
            },
        },
    )

    # §8.3 check 2 — batch_internal_consistency: reuse the consumer-completeness invariant
    # (every concept created this batch has a ConsumerListPublished — else InvalidationCascade
    # 漏 propagate). 本 batch 内部无矛盾的可机械验证部分。
    consumers_covered, missing_consumers = check_consumer_completeness(towow_dir, session_id)
    checks.append(
        {
            "check_id": "consensus.batch_internal_consistency",
            "status": "passed" if consumers_covered else "failed",
            "evidence": {"concepts_missing_consumer_list": missing_consumers},
        },
    )
    # F-026-1 gate requirement: the M-0.5 commit gate (completeness.py) requires the
    # EngineeringConsensusFreezed envelope to carry a PASSED consensus.consumer_scan_complete
    # check (REQUIRED_COMPLETENESS_CHECKS). Batch freeze keeps it — same consumer-completeness
    # result under the gate's required check_id (向后兼容 + 防漏报). Not dropped by 分批.
    #
    # M12-F4 (f-glob-consensus-gates-existence-only): this check now verifies SUBSTANCE, not just
    # event existence. Beyond "a ConsumerListPublished exists per concept" (consumers_covered), it
    # re-runs the machine scanner (check_consumer_coverage → initial_scan_consumers) and rejects a
    # freeze whose published consumer list MISSES a real consumer the scanner independently finds
    # (空清单/漏掉真实消费方照过 → 现拒)。Because this check_id is gate-required, the coverage
    # substance is enforced at the un-bypassable M-0.5 总闸 (真接), not only CLI-side. The agent
    # cannot fake coverage — the scanner recomputes it from the live task_graph / concept_graph.
    coverage_ok, coverage_gaps = check_consumer_coverage(towow_dir, session_id, scan_store)
    checks.append(
        {
            "check_id": "consensus.consumer_scan_complete",
            "status": "passed" if (consumers_covered and coverage_ok) else "failed",
            "evidence": {
                "concepts_missing_consumer_list": missing_consumers,
                "coverage_gaps": coverage_gaps,
            },
        },
    )

    # §8.3 check 3 + §8.4 — batch_no_dangling_references: every @ ref target must resolve to a
    # concept in THIS batch, a PREVIOUSLY frozen batch, or one already committed into the concept
    # graph. A forward reference to a future (not-yet-defined) batch concept is a dangling
    # reference → fail-closed (§8.4 非法). Conflict 33: the forward/backward boundary is
    # existence (committed ConceptCreated), not frozenness — migration / capability-registry /
    # pre-freeze-era concepts never appear in any frozen_concept_ids yet are legal backward
    # targets; only refs to concepts that do not exist at freeze time stay dangling.
    resolvable = (
        this_batch
        | prior_frozen
        | all_frozen_concept_ids(towow_dir)
        | _committed_concept_ids(scan_store)
    )
    ref_targets = session_at_reference_targets(towow_dir, session_id)
    dangling = sorted(
        {t for t in ref_targets if t not in resolvable and not _matches_any_concept_id(t, resolvable)},
    )
    no_dangling = not dangling
    checks.append(
        {
            "check_id": "consensus.batch_no_dangling_references",
            "status": "passed" if no_dangling else "failed",
            "evidence": {
                "dangling_reference_targets": dangling,
                "resolvable_against": sorted(resolvable),
            },
        },
    )

    # M-2.1 §4.1 消费方发现 caller-1 — run a real CIA forward_slice for each concept frozen this
    # batch and record the transitive downstream blast radius. status is ALWAYS passed (this is
    # 发现并记录, NOT a blocker — a new concept legitimately can have downstream impact). What makes
    # it enforce: completeness.py REQUIRES this check_id present on EngineeringConsensusFreezed, so a
    # freeze that skips the scan (check absent) is rejected by the M-0.5 commit gate (un-bypassable).
    # scan_store was built once at the top (reused by the consumer-coverage substance check).
    checks.append(
        {
            "check_id": "consensus.cia_downstream_scan",
            "status": "passed",
            "evidence": cia_downstream_scan(this_batch, scan_store),
        },
    )

    # design-maintainability-gate@v1 强制门 (existence-half, fail-closed) — owner 指令"以后所有设计都
    # 必须过这一关"的机械载体。只要本批 freeze 了 ≥1 个概念 (= 一个设计), 就必须携带 well-formed 可维护性
    # 条款 (5 判据各非空 justification); 缺失/畸形 → failed → freeze 拒。空冻结 (无新概念, 如迁移/smoke)
    # 不是设计, 不要求 (maint_required=False → 恒 passed)。条款是 freeze-TIME 参数 (像 --invariant), 在途
    # 会话 freeze 时补即可、不 ambush 已建概念。也进 completeness.py REQUIRED → 直 emit 旁路一并拒
    # (堵 rc-freeze-gate-only-partial-enforced-soft-path-bypass 软路径病)。
    # existence-half: 条款在场且 5 判据非空。substance-half (check_maintainability_clause_covers_batch):
    # 条款的 concept_ids 必须覆盖本批实际冻结的每个概念 —— 堵掉"塞一份格式合规、但 attests 无关/编造概念的
    # 假条款"的 gaming 洞 (同 check_consumer_coverage: attested ⊇ frozen-this-batch)。存在半过了才验覆盖半;
    # 本批含概念时两半都过才 passed。检查 id 保持不变 (总闸 REQUIRED 已引它)。
    maint_required = bool(this_batch)
    present_ok, maint_evidence = check_maintainability_clause_present(maintainability_clause)
    covers_ok, covers_evidence = check_maintainability_clause_covers_batch(
        maintainability_clause, this_batch,
    )
    maint_evidence["maintainability_required_this_batch"] = maint_required
    maint_evidence["substance_covers_batch"] = covers_ok
    maint_evidence["unattested_frozen_concepts"] = covers_evidence["unattested_frozen_concepts"]
    checks.append(
        {
            "check_id": "consensus.maintainability_clause_present",
            "status": "passed" if (not maint_required or (present_ok and covers_ok)) else "failed",
            "evidence": maint_evidence,
        },
    )

    all_passed = all(c["status"] == "passed" for c in checks)
    return all_passed, checks, this_batch


def _committed_concept_ids(store: ProjectionStore) -> set[str]:
    """All concept_ids with a committed ConceptCreated, from the materialized concept graph.

    Conflict 33 (§8.4 resolvable set): a backward @ ref to a concept that exists in the graph but
    never went through any plan's freeze is legal; frozenness is not the forward/backward
    boundary, existence is. The store is the freeze command's already-caught-up freeze_store, so
    this reads the graph exactly at the freeze point.
    """
    cg = store.read("concept_graph") or {}
    nodes = cg.get("nodes")
    if isinstance(nodes, dict):
        return {str(k) for k in nodes}
    if isinstance(nodes, list):
        return {
            str(n.get("concept_id"))
            for n in nodes
            if isinstance(n, dict) and n.get("concept_id")
        }
    return set()


def _build_caught_up_store(towow_dir: Path) -> ProjectionStore:
    """Build a ProjectionStore from ``towow_dir`` and catch it up to events.log.

    Used when run_batch_freeze_checks is called without a store (e.g. the unit tests / any caller
    that hasn't already materialized projections). The freeze CLI path passes its own freeze_store
    instead so it isn't caught up twice.
    """
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore

    store = ProjectionStore(towow_dir / "graph")
    store.catchup(EventLog(towow_dir / "events.log"))
    return store


def _matches_any_concept_id(target_name: str, concept_ids: set[str]) -> bool:
    """Resolve a typed @ ref's concept_name to a known concept_id.

    The §4.5 parser treats '@vN' as a lock_event_id suffix, so a ref written as
    '@concept:concept-users@v1' parses to concept_name='concept-users' (version stripped),
    while the concept_id is 'concept-users@v1'. Accept a match if target_name equals the
    concept_id, the version-stripped concept_id, or the slug head (concept-<slug>@vN → <slug>).
    """
    for cid in concept_ids:
        if cid == target_name:
            return True
        # version-stripped: 'concept-users@v1' → 'concept-users'
        if cid.split("@", 1)[0] == target_name:
            return True
        # slug head: 'concept-users@v1' → 'users'
        slug = cid.removeprefix("concept-").split("@", 1)[0]
        if slug == target_name:
            return True
    return False


__all__ = [
    "all_frozen_concept_ids",
    "cia_downstream_scan",
    "pending_uncommitted_concept_ids",
    "previously_frozen_concept_ids",
    "run_batch_freeze_checks",
    "session_at_reference_targets",
    "session_concept_ids",
    "session_superseded_concept_ids",
]
