"""M-2.1 §4.1 消费方发现 caller-3 — review plan-create CIA upstream-prerequisite scan.

# spec source:
#   05-l2-maintenance/M-2.1-global-relation-health-detailed-design.md
#     §4.1 依赖前提核查 (backward slice — start_entity 依赖谁 = 上游前提 entity)
#     §4.2 CIAQueryService.backward_slice + SliceResult (含 projection_watermark)
#     §4.4 invariant: 同步返回 / 不写 event / 含 projection_watermark / max_depth ≤ 5
#
# Why this module (M-2.1 §4 caller-3, 模块健康度第三刀; 范本=caller-1/caller-2):
#   caller-1 (consensus freeze) + caller-2 (plan freeze) 都跑 record-only forward_slice (查下游 —
#   "谁依赖我")。caller-3 是 review plan-create 阶段, 方向反过来用 backward_slice (查上游前提 —
#   "本批 frozen 概念依赖谁")。review_plan 是设计阶段产物 (design_time mode), 此刻还**没有 task** —
#   所以记的是上游前提**概念** (不映射 task)。
#
#   光看"本批 frozen 了哪些概念"漏掉的是: 这些概念依赖的、本批没碰但一旦上游变更会让本批共识
#   失真的上游前提。把它在 review_plan 创建时 record 下来, downstream review 能看见本批共识的
#   完整上游依赖面 (review 该覆盖的 dimensions 之一: 上游前提是否仍稳定)。
#
#   record-only (status 永远 passed, 同 caller-1/2)。enforce 牙齿 = completeness.py REQUIRES
#   review.cia_upstream_scan present on ReviewPlanCreated — 一个 review plan-create 跳过扫描
#   (check 缺失) 被 M-0.5 总闸物理拒 (不可绕)。空 upstream (图无上游边) + watermark 也算成功扫描
#   (可复算: 同 watermark 重跑 backward_slice → 同结果)。
#
# 方向实证 (§49 铁律, caller-1/2 都栽过 docstring 盲信): 真边 `B --dependency--> X` (source=B,
#   target=X = "B 依赖 X") 经 impact_graph 归一化成 `X→B`, backward_slice(B) 返回 X (B 的上游
#   前提)。tests/unit/schemas/test_review_plan_created.py 用真数据 probe 锁死, 不信 docstring。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore


def _effective_kind(payload: dict[str, Any], event_type: str) -> str:
    """事件真实类型 — unwrap stub-rewrap NodeTouched 信封 (同 consensus/plan 尺子的范式).

    历史 path-B 事件可能以 event_type=NodeTouched + payload.kind=<真类型> 落盘; 直读 event_type
    会把 EngineeringConsensusFreezed 当 NodeTouched 跳过 → 解析不到 frozen_concept_ids → 空扫描
    假通过。canonical 路 (12357 emit) 是真 EngineeringConsensusFreezed, 但本 unwrap 防 wrap 路。
    """
    if event_type == "NodeTouched":
        kind = payload.get("kind")
        if isinstance(kind, str) and kind:
            return kind
    return event_type


def _after_state(payload: dict[str, Any], event_type: str) -> dict[str, Any]:
    """业务字段 — canonical 取 payload.after_state; stub-rewrap 取 stub_original_payload.

    与 plan_freezed._after_state 同范式: stub 信封字段在 stub_original_payload【顶层】(after_state
    常为 None — 盘上实证 B6-b), 不能只认 after_state 键。
    """
    if event_type == "NodeTouched" and isinstance(payload.get("kind"), str):
        stub = payload.get("stub_original_payload")
        if isinstance(stub, dict):
            inner = stub.get("after_state")
            if isinstance(inner, dict) and inner:
                return inner
            return stub
    ast = payload.get("after_state")
    return ast if isinstance(ast, dict) else {}


def frozen_concept_ids_from_trigger(
    event_log: EventLog, trigger_event_id: str,
) -> tuple[list[str], str]:
    """Resolve the THIS-batch frozen concept ids the review plan is being created for.

    The review session's meta carries trigger_event_id (the event being reviewed). In design_time
    mode (the only mode review plan-create runs in) that trigger is the EngineeringConsensusFreezed
    event whose after_state.frozen_concept_ids (main.py:12377) is the set of concepts frozen this
    batch. We read THAT event by id (O(1) point lookup, segment/cold aware) and pull its
    frozen_concept_ids — NEVER a free-text --consensus-id resolve (which can't resolve and would
    yield an empty scan = a fake pass).

    Returns (sorted_concept_ids, how-resolved). Empty list + a "how" string when the trigger does
    not resolve to an EngineeringConsensusFreezed with frozen_concept_ids — an empty scan is still a
    real (record-only) scan: it records that nothing was frozen / the trigger isn't a freeze event,
    and the watermark proves the scan ran. The CLI is responsible for only invoking this from a
    design_time review session (mode gate already enforced there).
    """
    if not trigger_event_id.strip():
        return [], "no trigger_event_id"
    record = event_log.get_event(trigger_event_id.strip())
    if record is None:
        return [], f"trigger_event_id {trigger_event_id!r} not found in event log"
    payload = record.payload if isinstance(record.payload, dict) else {}
    kind = _effective_kind(payload, str(record.event_type.value))
    if kind != "EngineeringConsensusFreezed":
        return [], f"trigger {trigger_event_id!r} is {kind}, not EngineeringConsensusFreezed"
    after = _after_state(payload, str(record.event_type.value))
    frozen = after.get("frozen_concept_ids")
    if not isinstance(frozen, list):
        return [], f"trigger {trigger_event_id!r} EngineeringConsensusFreezed has no frozen_concept_ids"
    ids = sorted({str(c) for c in frozen if isinstance(c, str) and c})
    return ids, f"resolved {len(ids)} frozen_concept_ids from trigger {trigger_event_id!r}"


def cia_upstream_scan(
    frozen_concept_ids: list[str],
    store: ProjectionStore,
) -> dict[str, object]:
    """M-2.1 §4.1 依赖前提核查 caller-3 — for every concept frozen THIS batch, run a real CIA
    backward_slice (沿 impact_graph 概念入边 BFS, depth ≤ 5) and collect the transitive UPSTREAM
    prerequisite concepts (depth ≥ 1) it depends on, minus the batch's own concepts.

    Direction (empirically verified, NOT trusted from docstring — test_review_plan_created probe):
    a raw `B --dependency--> X` edge (B depends on X) normalizes to impact_graph `X→B`, so
    backward_slice(B) returns X (B 的上游前提 = B 依赖谁). This is the inverse of caller-1/caller-2's
    forward_slice (查下游 = 谁依赖我).

    design_time 此刻无 task, so we record upstream prerequisite **concepts** (not mapped to tasks) —
    the "光看本批 frozen 了哪些概念漏掉的上游前提" that a downstream review should know is in scope.

    record-only — status 永远 passed (发现并记录, NOT a blocker — a frozen concept legitimately has
    upstream prerequisites). enforce = "must have run" (completeness.py REQUIRES the check present on
    ReviewPlanCreated). Empty result (图无上游边/无 frozen 概念) is still a successful scan:
    upstream_prerequisites=[] + projection_watermark proves the scan ran and is reproducible (re-run
    backward_slice at that watermark → same answer).

    Returns the evidence as a nested dict (caller-1 path — NOT json.dumps'd into one string).
    NOTE: the commit-gate envelope stamping coerces each evidence leaf VALUE to a string (the
    canonical BlockingCheck.evidence is dict[str,str]) — shared framework behavior, same as
    caller-1's consensus.cia_downstream_scan; the outer dict keeps its named keys and the
    stringified values parse back (ast.literal_eval) for re-computation. watermark always present.

    ``store`` — a ProjectionStore already caught up to the review-create point (reads the
    materialized impact_graph 二阶 projection). The CLI passes its own caught-up store.
    """
    from towow.l1.cia_query import CIAQueryService

    cia = CIAQueryService(store)
    batch_set = set(frozen_concept_ids)
    scanned = sorted(batch_set)
    seen: set[str] = set()
    # (depth, concept_id) → entry so the sort key stays int/str (not object-typed dict values).
    collected: list[tuple[int, str, dict[str, object]]] = []
    # Seed watermark from the store so an empty batch (zero frozen concepts) still records the real
    # projection position (= store.cursor-1) — the value every backward_slice would return.
    watermark = max(0, store.cursor - 1)
    for concept_id in scanned:
        result = cia.backward_slice(concept_id)
        watermark = result.projection_watermark
        for hit in result.results:
            # depth ≥ 1 by construction (start node never in results); 去掉本批 concepts —
            # 本批内部互相依赖是已知的 (§8.4 in-batch ref 合法), 上游前提指批 *外* 的依赖前提概念.
            upstream_id = hit.entity.entity_id
            if hit.depth < 1 or upstream_id in batch_set or upstream_id in seen:
                continue
            seen.add(upstream_id)
            collected.append(
                (
                    hit.depth,
                    upstream_id,
                    {
                        "concept_id": upstream_id,
                        "depth": hit.depth,
                        "path": list(hit.path),
                    },
                ),
            )
    collected.sort(key=lambda t: (t[0], t[1]))
    upstream: list[dict[str, object]] = [entry for _depth, _cid, entry in collected]
    return {
        "scanned_concepts": scanned,
        "upstream_prerequisites": upstream,
        "projection_watermark": watermark,
    }


def build_cia_upstream_scan_check(
    event_log: EventLog,
    trigger_event_id: str,
    store: ProjectionStore,
) -> dict[str, object]:
    """Assemble the review.cia_upstream_scan blocking-check entry for the ReviewPlanCreated envelope.

    Resolves frozen_concept_ids from the trigger (the EngineeringConsensusFreezed being reviewed),
    runs the record-only backward-slice scan, and returns a blocking-check dict with nested-dict
    evidence (status ALWAYS passed; teeth = completeness.py REQUIRES it present). The CLI塞 this into
    the blocking_checks list passed to _attempt_skill_commit before the ReviewPlanCreated emit.
    """
    frozen, how = frozen_concept_ids_from_trigger(event_log, trigger_event_id)
    evidence = cia_upstream_scan(frozen, store)
    evidence["trigger_event_id"] = trigger_event_id
    evidence["frozen_resolution"] = how
    return {
        "check_id": "review.cia_upstream_scan",
        "status": "passed",
        "evidence": evidence,
    }


__all__ = [
    "build_cia_upstream_scan_check",
    "cia_upstream_scan",
    "frozen_concept_ids_from_trigger",
]
