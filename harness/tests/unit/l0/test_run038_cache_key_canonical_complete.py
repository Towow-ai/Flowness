"""RUN-038 L0增强 M-0.3 §4.1 / Patch3: cache key 完整 canonical 组成 (补 description / reentry /
per-candidate definition_hash + scope_rule_hash / scene_type)。

spec source: 03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
  §4.1 Cache Key 构成 (L307..L320) + 附录 B Patch 3 (L872..L911):
    canonical_resolver_input:
      task_scope: task_id / task_type / description / target_artifacts / declared_read_set /
                  declared_write_set / touched_nodes / reentry / parent_task_id?
      candidates: [{obligation_id, definition_hash, scope_rule_hash}]
      projection_bundle_hash
      scene_type
      reentry_context_hash?
      resolver_contract_version

  Patch3 的修正动机 (L876): 原 key 漏 task 语义描述 / reentry_context —— "两个改同一文件但意图不同的
  任务可能错误 cache 命中"。本 cap 把 description / reentry / per-candidate definition_hash +
  scope_rule_hash / scene_type 折进 key, 使语义/范围任一项变 → 不同 key (负对照逐项确认)。

M-0.6 Cap3 (test_run038_cache_key_bundle_hash_contract_version.py) 已锁 bundle_hash + contract_version;
本文件锁 Patch3 的剩余 canonical 组成项, 两文件互补。
"""

from __future__ import annotations

from towow.l0.capsule.pipeline import _CandidateKey, _compute_input_hash
from towow.schemas.enums import SceneType


def _base_kwargs() -> dict[str, object]:
    return {
        "task_id": "t-cache",
        "task_type": SceneType.EXECUTION,
        "description": "rewrite the payment reducer",
        "target_artifacts": ["a.py"],
        "touched_nodes": ["concept:x"],
        "candidates": [
            _CandidateKey(obligation_id="obl-1", definition="def-1", scope_rule="rule-1"),
            _CandidateKey(obligation_id="obl-2", definition="def-2", scope_rule="rule-2"),
        ],
        "bundle_hash": "sha256:bundle-AAA",
        "contract_version": "1.0.0",
        "scene_type": SceneType.EXECUTION,
        "reentry": False,
    }


def test_description_change_changes_input_hash() -> None:
    """task 语义描述变 → cache key 变 (Patch3 L876: 同文件不同意图不得错误命中)。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    changed = {**base, "description": "delete the payment reducer"}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different task description must produce a different input_hash"


def test_reentry_flag_change_changes_input_hash() -> None:
    """reentry 标志变 → cache key 变 (Patch3 canonical_resolver_input.task_scope.reentry)。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    changed = {**base, "reentry": True}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different reentry flag must produce a different input_hash"


def test_candidate_definition_change_changes_input_hash() -> None:
    """候选 obligation 的 definition 变 (definition_hash 变) → cache key 变。

    Patch3: candidates 折 definition_hash —— obligation 定义改了, 旧 verdict 不该被复用。
    """
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    new_cands = [
        _CandidateKey(obligation_id="obl-1", definition="DEFINITION CHANGED", scope_rule="rule-1"),
        _CandidateKey(obligation_id="obl-2", definition="def-2", scope_rule="rule-2"),
    ]
    changed = {**base, "candidates": new_cands}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different candidate definition must produce a different input_hash"


def test_candidate_scope_rule_change_changes_input_hash() -> None:
    """候选 obligation 的 scope_rule 变 (scope_rule_hash 变) → cache key 变。

    Patch3: candidates 折 scope_rule_hash —— 范围规则改了, 旧判定不再适用。
    """
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    new_cands = [
        _CandidateKey(obligation_id="obl-1", definition="def-1", scope_rule="RULE CHANGED"),
        _CandidateKey(obligation_id="obl-2", definition="def-2", scope_rule="rule-2"),
    ]
    changed = {**base, "candidates": new_cands}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different candidate scope_rule must produce a different input_hash"


def test_candidate_id_change_changes_input_hash() -> None:
    """候选集合变 (obligation_id 变) → cache key 变 (原 §4.1 candidates_ids 组成, 保留)。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    new_cands = [
        _CandidateKey(obligation_id="obl-1", definition="def-1", scope_rule="rule-1"),
        _CandidateKey(obligation_id="obl-3", definition="def-2", scope_rule="rule-2"),
    ]
    changed = {**base, "candidates": new_cands}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different candidate id set must produce a different input_hash"


def test_scene_type_change_changes_input_hash() -> None:
    """scene_type 变 → cache key 变 (Patch3 canonical_resolver_input.scene_type)。

    不同场景模板读不同 projection / 邻域跳数, 同 task_scope 在不同场景下判定可不同 → 不得错误复用。
    """
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    changed = {**base, "scene_type": SceneType.REVIEW}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different scene_type must produce a different input_hash"


def test_same_inputs_same_input_hash() -> None:
    """同输入 → 同 input_hash (确定性, 才能命中复用)。"""
    assert _compute_input_hash(**_base_kwargs()) == _compute_input_hash(**_base_kwargs())  # type: ignore[arg-type]


def test_candidate_order_does_not_matter() -> None:
    """候选顺序不影响 key (候选按 obligation_id 排序后折入, 召回顺序抖动不致 cache miss)。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    reordered = {
        **base,
        "candidates": [
            _CandidateKey(obligation_id="obl-2", definition="def-2", scope_rule="rule-2"),
            _CandidateKey(obligation_id="obl-1", definition="def-1", scope_rule="rule-1"),
        ],
    }
    h2 = _compute_input_hash(**reordered)  # type: ignore[arg-type]
    assert h1 == h2, "candidate order must not affect the input_hash"
