"""RUN-038 L0增强 M-0.3 §4.2/§6.6: bundle_hash 规范化 — 排除 assembler 自身 per-task activation churn,
保留真结构/定义变更触发 cache miss。配合 cache lookup (debt-b529da25797e)。

spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
  §4.1 (L320) / §6.6: "projection 一变 cache miss — 不需要显式 cache invalidation"。真结构变 (new
  concept/obligation/edge/改 definition/scope_rule/canonical_state) 必 flip hash。
  §4.2/§11.1: cache 用于同任务重试复用 verdict ("下次重试 cache 命中")。

问题 (debt-b529da25797e 根因): pipeline 每次 run emit ObligationActivated/ObligationScopeJudged,
翻动 obligation_lifecycle 节点的 lifecycle_state/last_*_event_id —— 若这些进 bundle_hash, 首次 assemble
就让自己的 cache 失效, 重试永远 miss。这些是 assembler 自己的输出, 非 resolver 判定输入 → 从 cache-key
bundle hash 排除。

锁死: (1) 仅 activation churn 字段变 → hash 不变 (cache 仍命中, 重试复用); (2) 真定义/结构变 (新增
obligation / 改 definition / canonical_state superseded) → hash 变 (cache miss, §6.6); (3) 规范化不改
原 bundle (无副作用)。
"""

from __future__ import annotations

from towow.l0.capsule.pipeline import _compute_bundle_hash, _normalize_bundle_for_hash


def _bundle(*, lifecycle_state: str, definition: str, canonical_state: str = "active") -> dict:
    """A one-obligation obligation_lifecycle bundle + a tiny concept_graph."""
    return {
        "concept_graph": {"concepts": [{"concept_id": "c1", "definition": "d"}]},
        "obligation_lifecycle_state": {
            "obligations": [
                {
                    "obligation_id": "obl-1",
                    "canonical_state": canonical_state,
                    "definition": definition,
                    "scope_rule": "applies to X",
                    "scope_type": "concept_neighborhood",
                    "severity": "normal",
                    "nature": "invariant",
                    # assembler-churned per-task bookkeeping (its OWN output):
                    "lifecycle_state": lifecycle_state,
                    "last_observed_lifecycle_event_type": lifecycle_state,
                    "last_state_change_event_id": f"evt-{lifecycle_state}",
                    "last_activation_event_id": (
                        f"evt-act-{lifecycle_state}" if lifecycle_state == "activated" else None
                    ),
                },
            ],
        },
    }


def test_activation_churn_does_not_change_bundle_hash() -> None:
    """仅 activation churn 变 (captured → activated, last_*_event_id 变) → bundle_hash 不变。

    否则首次 assemble emit ObligationActivated 就翻 hash → 同任务重试永远 cache miss (debt 根因)。
    """
    captured = _bundle(lifecycle_state="captured", definition="def-A")
    activated = _bundle(lifecycle_state="activated", definition="def-A")
    assert _compute_bundle_hash(captured) == _compute_bundle_hash(activated), (
        "activation lifecycle churn (assembler's own output) must NOT change the bundle hash"
    )


def test_definition_change_changes_bundle_hash() -> None:
    """真定义变 (definition 改) → bundle_hash 变 → cache miss (§6.6 'projection 一变 cache miss')。"""
    a = _bundle(lifecycle_state="activated", definition="def-A")
    b = _bundle(lifecycle_state="activated", definition="def-B-CHANGED")
    assert _compute_bundle_hash(a) != _compute_bundle_hash(b), (
        "a real obligation definition change must change the bundle hash"
    )


def test_canonical_state_transition_changes_bundle_hash() -> None:
    """canonical_state 转移 (active → superseded) → bundle_hash 变 (退役/取代是真结构变, 该 miss)。"""
    active = _bundle(lifecycle_state="activated", definition="def-A", canonical_state="active")
    superseded = _bundle(
        lifecycle_state="activated", definition="def-A", canonical_state="superseded",
    )
    assert _compute_bundle_hash(active) != _compute_bundle_hash(superseded), (
        "a canonical_state transition (superseded/retired) must change the bundle hash"
    )


def test_new_obligation_changes_bundle_hash() -> None:
    """新增 obligation → bundle_hash 变 (§6.6 'new obligation' 触发 cache miss)。"""
    one = _bundle(lifecycle_state="activated", definition="def-A")
    two = _bundle(lifecycle_state="activated", definition="def-A")
    two["obligation_lifecycle_state"]["obligations"].append(
        {
            "obligation_id": "obl-2",
            "canonical_state": "active",
            "definition": "def-2",
            "scope_rule": "applies to Y",
            "scope_type": "global",
            "severity": "normal",
            "nature": "invariant",
            "lifecycle_state": "captured",
            "last_observed_lifecycle_event_type": "captured",
            "last_state_change_event_id": "evt-2",
        },
    )
    assert _compute_bundle_hash(one) != _compute_bundle_hash(two), (
        "adding a new obligation must change the bundle hash"
    )


def test_concept_graph_change_changes_bundle_hash() -> None:
    """concept_graph 变 (new concept) → bundle_hash 变 (§6.6 'new concept')。"""
    a = _bundle(lifecycle_state="activated", definition="def-A")
    b = _bundle(lifecycle_state="activated", definition="def-A")
    b["concept_graph"]["concepts"].append({"concept_id": "c2", "definition": "d2"})
    assert _compute_bundle_hash(a) != _compute_bundle_hash(b), (
        "a new concept in the concept graph must change the bundle hash"
    )


def test_normalize_does_not_mutate_input() -> None:
    """规范化只读不改原 bundle (无副作用 — 原 obligation 节点的 churn 字段仍在)。"""
    original = _bundle(lifecycle_state="activated", definition="def-A")
    _normalize_bundle_for_hash(original)
    node = original["obligation_lifecycle_state"]["obligations"][0]
    assert "lifecycle_state" in node, "normalization must not mutate the original bundle"
    assert node["lifecycle_state"] == "activated"
