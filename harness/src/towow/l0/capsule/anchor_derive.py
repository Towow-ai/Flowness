"""Per-scene concept-anchor derivation for R01 neighborhood delivery (Part B).

# spec source:
#   01-reconciliation/R01-BRAIN-DELIVERY-DESIGN-2026-06-17.md
#     §1 concept-context-progressive-disclosure@v1 (入口层: 概念邻域投递给干活 agent)
#     §2 Job 2 投递层 / §5 复用点 (_derive_task_concept_anchors / EventLog / 投影)
#
# Why this module exists (R01 Part B — execution 之外 5 个 scene 的取锚):
#   Part A 给 execution `work start` 接通了概念邻域投递: assemble_capsule 把任务相关概念图定义渲成
#   per-session 文件让 agent 一开工 Read。execution 能直接非空, 是因为 work start 的 task_id 是真
#   task_id, pipeline 的 _derive_task_concept_anchors 能从 task_graph node 的 concept read_set 自动
#   取锚 (再叠加显式 --touched-node)。
#
#   review/fix/consensus/plan 四个 scene 的 session-start 把 task_id 设成 trigger_event_id /
#   finding_id / 会话 sid —— 这些都不是 task_graph 的 task_id, pipeline 自动取锚返 [] → 邻域空 →
#   KNOWN_FACTS 空 → agent 拿不到这个 scene 碰的概念。要让这四个 scene 也真投递, 每个都得显式从已
#   存在的上游事件解出"这次工作关于哪些概念"的 concept id 集, 作 touched_nodes 喂 _assemble_session_capsule。
#   (interview 在建图前没有概念锚, 邻域空合理 —— 它不在本模块, 不假装取锚。)
#
# 取锚 = 只读已存在上游事件/投影拿 concept id, 不新增任何概念图写入。每条链全程防御: 链上任一跳断
# (事件缺失 / 字段缺 / 解析不出) → 返回 [] (caller no-op 不建文件, 该 scene 无概念锚不报错, 不回归)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.schemas.enums import EventType, SubjectEntityType

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore


def _payload_of(event: object) -> dict[str, object] | None:
    """Defensive: pull a dict payload off an EventRecord-shaped object, else None."""
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else None


def _concept_ids_from_task_read_set(task_graph: object, task_id: str) -> list[str]:
    """The concept-typed read_set entries of the task_graph node for `task_id`.

    Mirror of pipeline._derive_task_concept_anchors (§5 复用点) — kept here as a tiny defensive
    local rather than importing a `_`-private pipeline helper (avoids coupling this read-only
    deriver to the pipeline module + its scene-bundle assumptions). Returns [] for a missing /
    malformed task_graph, an absent task node, or a node with no concept read_set.
    """
    if not isinstance(task_graph, dict):
        return []
    nodes = task_graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    for node in nodes:
        if not isinstance(node, dict) or node.get("task_id") != task_id:
            continue
        read_set = node.get("read_set")
        if not isinstance(read_set, list):
            return []
        anchors: list[str] = []
        for entry in read_set:
            if not isinstance(entry, dict):
                continue
            if entry.get("entity_type") == SubjectEntityType.CONCEPT.value:
                cid = entry.get("entity_id")
                if isinstance(cid, str) and cid:
                    anchors.append(cid)
        return anchors  # task_id is unique in task_graph — first match authoritative
    return []


def _task_id_for_patch(event_log: EventLog, patch_event_id: str) -> str | None:
    """PatchProposed event → its after_state.task_id (state_transition.py PatchProposedAfter).

    Returns None for a missing patch event / non-PatchProposed payload / missing task_id.
    """
    if not patch_event_id:
        return None
    patch = event_log.get_event(patch_event_id)
    payload = _payload_of(patch)
    if payload is None:
        return None
    after = payload.get("after_state")
    if not isinstance(after, dict):
        return None
    task_id = after.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None


def _dedupe(seq: list[str]) -> list[str]:
    """Order-preserving dedupe — stable touched_nodes for snapshot-friendly tests."""
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ─── review ──────────────────────────────────────────────────────────────────


def derive_review_anchors(
    event_log: EventLog,
    store: ProjectionStore,
    trigger_event_id: str,
) -> list[str]:
    """review scene 取锚 — 被审单元碰的概念 = 本次 review 关于的概念。

    review 的 trigger (review_start 把它当 task_id 传) 形态不止一种, 按事件类型分流取锚:
      (a) FindingCreated → related_patch_event_id → PatchProposed.after_state.task_id → task read_set
          concepts (R01 设计 §2 review 行: author_time/fix_after review 一个 finding/patch)。
      (b) PatchProposed → trigger 就是被审 patch → 直接取 after_state.task_id → task read_set concepts。
      (c) EngineeringConsensusFreezed → design_time review 审一批冻结 → 被审单元就是这批冻结概念
          (after_state.frozen_concept_ids, 同 plan 取锚)。这是 design_time review 最常见的 trigger
          (test_m21_caller3 实证: review start --trigger-event-id <freeze>); 不接它 → design_time
          review 永远 no-op, 是大漏。
    任一跳断 → [] (链断可接受, caller no-op)。
    """
    if not trigger_event_id:
        return []
    trig = event_log.get_event(trigger_event_id)
    payload = _payload_of(trig)
    if payload is None:
        return []

    event_type = getattr(trig, "event_type", None)
    if event_type == EventType.ENGINEERING_CONSENSUS_FREEZED:
        # design_time review of a freeze: 被审单元 = 本批冻结概念 (复用 plan 取锚逻辑)。
        return derive_plan_anchors_from_consensus_event(trig)

    patch_event_id: str | None = None
    if event_type == EventType.FINDING_CREATED:
        # FindingCreatedPayload.related_patch_event_id is top-level flat (finding.py:245).
        rp = payload.get("related_patch_event_id")
        patch_event_id = rp if isinstance(rp, str) and rp else None
    elif event_type == EventType.PATCH_PROPOSED:
        # trigger IS the reviewed patch — derive its task directly.
        patch_event_id = trigger_event_id

    task_id = _task_id_for_patch(event_log, patch_event_id) if patch_event_id else None
    if not task_id:
        return []
    task_graph = store.read("task_graph")
    return _dedupe(_concept_ids_from_task_read_set(task_graph, task_id))


# ─── fix ─────────────────────────────────────────────────────────────────────


def _concepts_for_obligations(store: ProjectionStore, obligation_ids: list[str]) -> list[str]:
    """obligation_lifecycle attached_to[] concept entity_ids for the given obligations.

    Reads obligation_lifecycle_state projection (key 'obligations', each node has
    obligation_id + attached_to: list[EntityRef]); collects entity_type==concept ids.
    """
    if not obligation_ids:
        return []
    proj = store.read("obligation_lifecycle_state")
    if not isinstance(proj, dict):
        return []
    nodes = proj.get("obligations")
    if not isinstance(nodes, list):
        return []
    wanted = set(obligation_ids)
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("obligation_id") not in wanted:
            continue
        attached = node.get("attached_to")
        if not isinstance(attached, list):
            continue
        for ref in attached:
            if not isinstance(ref, dict):
                continue
            if ref.get("entity_type") == SubjectEntityType.CONCEPT.value:
                cid = ref.get("entity_id")
                if isinstance(cid, str) and cid:
                    out.append(cid)
    return out


def derive_fix_anchors(
    event_log: EventLog,
    store: ProjectionStore,
    finding_id: str,
) -> list[str]:
    """fix scene 取锚 — 待修 finding 碰的概念 = 本次 fix 关于的概念。

    FindingCreated payload 没有 concept subjects (查实 finding.py: 只有 target.artifact/location +
    related_patch_event_id + related_obligation_ids), 所以两条链合取并去重:
      链1 (patch 路): related_patch_event_id → PatchProposed.after_state.task_id → task read_set concepts。
      链2 (obligation 路): related_obligation_ids → obligation_lifecycle attached_to concept。
    任一条命中即非空; 两条都断 → [] (caller no-op)。

    finding_id 在 fix_start 直接当 task_id 传; 它指向 FindingCreated 事件 (event_id 或 finding 业务 id
    都可能 —— get_event 按 event_id 命中; 命中不了的 finding 业务 id → payload None → 两条链都 [])。
    """
    if not finding_id:
        return []
    finding = event_log.get_event(finding_id)
    payload = _payload_of(finding)
    if payload is None:
        return []

    anchors: list[str] = []

    # 链1: patch → task → read_set concepts
    rp = payload.get("related_patch_event_id")
    if isinstance(rp, str) and rp:
        task_id = _task_id_for_patch(event_log, rp)
        if task_id:
            task_graph = store.read("task_graph")
            anchors.extend(_concept_ids_from_task_read_set(task_graph, task_id))

    # 链2: obligations → attached_to concepts
    rob = payload.get("related_obligation_ids")
    if isinstance(rob, list):
        ob_ids = [o for o in rob if isinstance(o, str) and o]
        anchors.extend(_concepts_for_obligations(store, ob_ids))

    return _dedupe(anchors)


# ─── consensus ─────────────────────────────────────────────────────────────────


def derive_consensus_anchors(brief_payload: dict[str, object] | None) -> list[str]:
    """consensus scene 取锚 — brief 的种子概念 = 本次共识扎根的起点概念。

    consensus 还没有 task_graph 节点 (它正在建图), 所以用 brief 种子: InterviewBriefPublished payload
    的 concept_seeds_created (interview_brief.py:263, list[str], 同 envelope atomic emit 的 ConceptCreated
    的 concept_id 列表 —— 直接就是种子 concept id, 不用再去关联事件)。

    consensus_start 已选好 selected_brief (按 --source-brief-id 或 latest), 直接把它的 payload 传进来。
    字段缺/非列表/空 → [] (caller no-op)。
    """
    if not isinstance(brief_payload, dict):
        return []
    seeds = brief_payload.get("concept_seeds_created")
    if not isinstance(seeds, list):
        return []
    return _dedupe([s for s in seeds if isinstance(s, str) and s])


# ─── plan ──────────────────────────────────────────────────────────────────────


def derive_plan_anchors_from_consensus_event(consensus_freezed_event: object) -> list[str]:
    """plan scene 取锚 — 本批冻结的概念集 = planner 要照着拆 task 的概念。

    plan_start 已读到最新 EngineeringConsensusFreezed 事件 (拿 after_state.plan_id)。本函数从同一个
    事件的 after_state.frozen_concept_ids (main.py 冻结时 sorted(this_batch_concepts) 写入, list[str])
    取本批冻结概念。事件缺/after_state 缺/frozen_concept_ids 缺或非列表 → [] (caller no-op)。
    """
    payload = _payload_of(consensus_freezed_event)
    if payload is None:
        return []
    after = payload.get("after_state")
    if not isinstance(after, dict):
        return []
    frozen = after.get("frozen_concept_ids")
    if not isinstance(frozen, list):
        return []
    return _dedupe([c for c in frozen if isinstance(c, str) and c])


__all__ = [
    "derive_consensus_anchors",
    "derive_fix_anchors",
    "derive_plan_anchors_from_consensus_event",
    "derive_review_anchors",
]
