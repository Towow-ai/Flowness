"""R01 Part B — per-scene concept-anchor derivers (unit).

# contract: 01-reconciliation/R01-BRAIN-DELIVERY-DESIGN-2026-06-17.md
#   §1 concept-context-progressive-disclosure@v1 / §2 Job 2 投递层 (入口层)
#
# 证 (机制层 — 取锚逻辑本身, 非"agent 真用"; 那归主 session 真 session 验):
#   每个 scene 取锚函数两组用例:
#     - 有锚: 上游事件/投影链完整 → 返回该 scene 关于的 concept id 集 (非空)。
#     - no-op: 链上任一跳断 (事件缺/字段缺/解析不出) → 返回 [] (caller no-op 不建文件)。
#
# Anti-vacuous: 有锚 case 证真解出对的 concept id (不是恒空); no-op case 逐跳断各证一次
#   (不是只测一个恒返 [] 的死路径)。
#
# 这些 deriver 是纯函数, 只调 event_log.get_event / .payload / .event_type + store.read(name) —
# 用极简 stub 喂上游形态 (真 CLI 接线由 test_r01_partb_session_neighborhood_delivery.py 集成测)。
"""

from __future__ import annotations

from towow.l0.capsule.anchor_derive import (
    derive_consensus_anchors,
    derive_fix_anchors,
    derive_plan_anchors_from_consensus_event,
    derive_review_anchors,
)
from towow.schemas.enums import EventType, SubjectEntityType

# ─── stubs ────────────────────────────────────────────────────────────────────


class _StubEvent:
    """EventRecord-shaped: only the attrs the derivers read (.payload dict, .event_type enum)."""

    def __init__(self, payload: dict[str, object], event_type: EventType | None = None) -> None:
        self.payload = payload
        self.event_type = event_type


class _StubLog:
    """event_log.get_event(id) lookup from a dict; missing id → None (链断)."""

    def __init__(self, events: dict[str, _StubEvent]) -> None:
        self._events = events

    def get_event(self, event_id: str) -> _StubEvent | None:
        return self._events.get(event_id)


class _StubStore:
    """store.read(name) lookup from a dict; missing projection → None (链断)."""

    def __init__(self, projections: dict[str, dict[str, object]]) -> None:
        self._proj = projections

    def read(self, name: str) -> dict[str, object] | None:
        return self._proj.get(name)


_CONCEPT = SubjectEntityType.CONCEPT.value


def _task_graph(task_id: str, concept_ids: list[str]) -> dict[str, object]:
    return {
        "nodes": [
            {
                "task_id": task_id,
                "read_set": [
                    {"entity_type": _CONCEPT, "entity_id": cid} for cid in concept_ids
                ]
                + [{"entity_type": "file", "entity_id": "src/x.py"}],  # 非 concept 项要被滤掉
            },
        ],
    }


# ─── review ──────────────────────────────────────────────────────────────────


def test_review_anchors_via_finding_trigger() -> None:
    """trigger 是 FindingCreated → related_patch_event_id → patch.task_id → task read_set concepts。"""
    log = _StubLog(
        {
            "evt-finding": _StubEvent(
                {"related_patch_event_id": "evt-patch"},
                EventType.FINDING_CREATED,
            ),
            "evt-patch": _StubEvent(
                {"after_state": {"task_id": "task-7"}},
                EventType.PATCH_PROPOSED,
            ),
        },
    )
    store = _StubStore({"task_graph": _task_graph("task-7", ["c-a@v1", "c-b@v1"])})
    assert derive_review_anchors(log, store, "evt-finding") == ["c-a@v1", "c-b@v1"]


def test_review_anchors_via_patch_trigger_directly() -> None:
    """trigger 本身就是 PatchProposed → 直接取它的 after_state.task_id。"""
    log = _StubLog(
        {"evt-patch": _StubEvent({"after_state": {"task_id": "task-9"}}, EventType.PATCH_PROPOSED)},
    )
    store = _StubStore({"task_graph": _task_graph("task-9", ["c-z@v1"])})
    assert derive_review_anchors(log, store, "evt-patch") == ["c-z@v1"]


def test_review_anchors_via_consensus_freeze_trigger() -> None:
    """design_time review 审一批冻结: trigger 是 EngineeringConsensusFreezed → 本批冻结概念作锚。"""
    log = _StubLog(
        {
            "evt-freeze": _StubEvent(
                {"after_state": {"frozen_concept_ids": ["c-b@v1"]}},
                EventType.ENGINEERING_CONSENSUS_FREEZED,
            ),
        },
    )
    assert derive_review_anchors(log, _StubStore({}), "evt-freeze") == ["c-b@v1"]


def test_review_anchors_noop_missing_trigger() -> None:
    assert derive_review_anchors(_StubLog({}), _StubStore({}), "evt-gone") == []


def test_review_anchors_noop_finding_no_patch_ref() -> None:
    """trigger 是 finding 但没 related_patch_event_id → 链断 → []。"""
    log = _StubLog({"evt-f": _StubEvent({"related_patch_event_id": None}, EventType.FINDING_CREATED)})
    assert derive_review_anchors(log, _StubStore({}), "evt-f") == []


def test_review_anchors_noop_patch_task_not_in_graph() -> None:
    """patch.task_id 不在 task_graph (task 节点缺) → []。"""
    log = _StubLog(
        {
            "evt-f": _StubEvent({"related_patch_event_id": "evt-p"}, EventType.FINDING_CREATED),
            "evt-p": _StubEvent({"after_state": {"task_id": "task-missing"}}, EventType.PATCH_PROPOSED),
        },
    )
    store = _StubStore({"task_graph": _task_graph("task-other", ["c-a@v1"])})
    assert derive_review_anchors(log, store, "evt-f") == []


def test_review_anchors_noop_empty_trigger_id() -> None:
    assert derive_review_anchors(_StubLog({}), _StubStore({}), "") == []


# ─── fix ───────────────────────────────────────────────────────────────────────


def test_fix_anchors_via_patch_chain() -> None:
    """链1: finding.related_patch_event_id → patch.task_id → task read_set concepts。"""
    log = _StubLog(
        {
            "finding-1": _StubEvent(
                {"related_patch_event_id": "evt-patch", "related_obligation_ids": None},
                EventType.FINDING_CREATED,
            ),
            "evt-patch": _StubEvent({"after_state": {"task_id": "task-3"}}, EventType.PATCH_PROPOSED),
        },
    )
    store = _StubStore({"task_graph": _task_graph("task-3", ["c-fix@v1"])})
    assert derive_fix_anchors(log, store, "finding-1") == ["c-fix@v1"]


def test_fix_anchors_via_obligation_chain() -> None:
    """链2: finding.related_obligation_ids → obligation_lifecycle attached_to concept。"""
    log = _StubLog(
        {
            "finding-2": _StubEvent(
                {"related_patch_event_id": None, "related_obligation_ids": ["ob-1", "ob-2"]},
                EventType.FINDING_CREATED,
            ),
        },
    )
    store = _StubStore(
        {
            "obligation_lifecycle_state": {
                "obligations": [
                    {
                        "obligation_id": "ob-1",
                        "attached_to": [
                            {"entity_type": _CONCEPT, "entity_id": "c-ob1@v1"},
                            {"entity_type": "file", "entity_id": "src/y.py"},  # 滤掉
                        ],
                    },
                    {
                        "obligation_id": "ob-2",
                        "attached_to": [{"entity_type": _CONCEPT, "entity_id": "c-ob2@v1"}],
                    },
                    {  # 不在 related_obligation_ids → 不取
                        "obligation_id": "ob-other",
                        "attached_to": [{"entity_type": _CONCEPT, "entity_id": "c-noise@v1"}],
                    },
                ],
            },
        },
    )
    assert derive_fix_anchors(log, store, "finding-2") == ["c-ob1@v1", "c-ob2@v1"]


def test_fix_anchors_merges_both_chains_dedup() -> None:
    """两条链都命中, 合取去重 (patch 链的 concept 与 obligation 链重叠时不重复)。"""
    log = _StubLog(
        {
            "finding-3": _StubEvent(
                {"related_patch_event_id": "evt-p", "related_obligation_ids": ["ob-x"]},
                EventType.FINDING_CREATED,
            ),
            "evt-p": _StubEvent({"after_state": {"task_id": "task-x"}}, EventType.PATCH_PROPOSED),
        },
    )
    store = _StubStore(
        {
            "task_graph": _task_graph("task-x", ["c-shared@v1", "c-patch@v1"]),
            "obligation_lifecycle_state": {
                "obligations": [
                    {
                        "obligation_id": "ob-x",
                        "attached_to": [{"entity_type": _CONCEPT, "entity_id": "c-shared@v1"}],
                    },
                ],
            },
        },
    )
    # patch 链先: c-shared, c-patch; obligation 链: c-shared(已见, 去重) → 结果不含重复
    assert derive_fix_anchors(log, store, "finding-3") == ["c-shared@v1", "c-patch@v1"]


def test_fix_anchors_noop_missing_finding() -> None:
    assert derive_fix_anchors(_StubLog({}), _StubStore({}), "finding-gone") == []


def test_fix_anchors_noop_both_chains_broken() -> None:
    """finding 既无 patch ref 也无 obligation ids → []。"""
    log = _StubLog(
        {
            "finding-4": _StubEvent(
                {"related_patch_event_id": None, "related_obligation_ids": []},
                EventType.FINDING_CREATED,
            ),
        },
    )
    assert derive_fix_anchors(log, _StubStore({}), "finding-4") == []


# ─── consensus ─────────────────────────────────────────────────────────────────


def test_consensus_anchors_from_brief_seeds() -> None:
    """brief.concept_seeds_created (同 envelope atomic 种子 ConceptCreated 的 id 列表) 直接作锚。"""
    brief = {"brief_id": "brief-abc", "concept_seeds_created": ["c-seed1@v1", "c-seed2@v1"]}
    assert derive_consensus_anchors(brief) == ["c-seed1@v1", "c-seed2@v1"]


def test_consensus_anchors_dedup() -> None:
    brief = {"concept_seeds_created": ["c-1@v1", "c-1@v1", "c-2@v1"]}
    assert derive_consensus_anchors(brief) == ["c-1@v1", "c-2@v1"]


def test_consensus_anchors_noop_no_seeds_field() -> None:
    assert derive_consensus_anchors({"brief_id": "brief-x"}) == []


def test_consensus_anchors_noop_none_payload() -> None:
    assert derive_consensus_anchors(None) == []


def test_consensus_anchors_noop_empty_seeds() -> None:
    assert derive_consensus_anchors({"concept_seeds_created": []}) == []


# ─── plan ──────────────────────────────────────────────────────────────────────


def test_plan_anchors_from_frozen_concepts() -> None:
    """EngineeringConsensusFreezed.after_state.frozen_concept_ids = 本批冻结概念。"""
    freeze = _StubEvent(
        {"after_state": {"plan_id": "plan-1", "frozen_concept_ids": ["c-f1@v1", "c-f2@v1"]}},
        EventType.ENGINEERING_CONSENSUS_FREEZED,
    )
    assert derive_plan_anchors_from_consensus_event(freeze) == ["c-f1@v1", "c-f2@v1"]


def test_plan_anchors_dedup() -> None:
    freeze = _StubEvent(
        {"after_state": {"frozen_concept_ids": ["c-a@v1", "c-a@v1", "c-b@v1"]}},
    )
    assert derive_plan_anchors_from_consensus_event(freeze) == ["c-a@v1", "c-b@v1"]


def test_plan_anchors_noop_none_event() -> None:
    assert derive_plan_anchors_from_consensus_event(None) == []


def test_plan_anchors_noop_no_frozen_field() -> None:
    freeze = _StubEvent({"after_state": {"plan_id": "plan-2"}})
    assert derive_plan_anchors_from_consensus_event(freeze) == []


def test_plan_anchors_noop_missing_after_state() -> None:
    freeze = _StubEvent({"plan_id": "plan-3"})  # 平铺无 after_state
    assert derive_plan_anchors_from_consensus_event(freeze) == []
