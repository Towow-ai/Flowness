"""RUN-038 L0增强 M-0.6 Cap3: cache key(input_hash)含 bundle_hash + contract_version; 复用 input_hash.idx.

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
  §6.6 Cache 协议 (L1001..L1025):
    input_hash = hash(task_scope.* + candidates_ids + bundle.bundle_hash + resolver_contract_version)
    - bundle.bundle_hash: "projection 一变 cache miss"
    - resolver_contract_version: "prompt / schema 版本变更 cache miss"
    - Cache 存储位置: M-0.1 input_hash.idx (M-0.3 复用, 不独立 cache 文件)
  与 M-0.3 §4.1 cache key 钉死一致。

M-0.6 owns 的是 *contract*(key 必含这两项, 使旧 cache 在新合约下自动失效); cache 机制 M-0.3 §4.1
执行, 存储 M-0.1 input_hash.idx。lookup 命中跳阶段 4 的 perf 复用是独立 debt(debt-b529da25797e),
不在本 cap scope —— 本 cap 只锁 key 组成。

锁死: (1) bundle 内容变 → input_hash 变(projection 一变 cache miss); (2) contract_version 变 →
input_hash 变(prompt/schema 版本变 cache miss); (3) input_hash.idx 是 cache 存储(lookup_input_hash
按 input_hash 查回 ResolverDecisionMade)。
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


def test_bundle_hash_change_changes_input_hash() -> None:
    """projection 一变(bundle_hash 变)→ cache key 变 → cache miss(§6.6 'projection 一变 cache miss')。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    changed = {**base, "bundle_hash": "sha256:bundle-BBB"}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different bundle_hash must produce a different input_hash"


def test_contract_version_change_changes_input_hash() -> None:
    """contract_version 变(prompt/schema 版本变)→ cache key 变 → 旧 cache 自动失效(§6.6)。"""
    base = _base_kwargs()
    h1 = _compute_input_hash(**base)  # type: ignore[arg-type]
    changed = {**base, "contract_version": "1.1.0"}
    h2 = _compute_input_hash(**changed)  # type: ignore[arg-type]
    assert h1 != h2, "different contract_version must produce a different input_hash"


def test_same_inputs_same_input_hash() -> None:
    """同输入 → 同 input_hash(确定性, 才能命中复用)。"""
    assert _compute_input_hash(**_base_kwargs()) == _compute_input_hash(**_base_kwargs())  # type: ignore[arg-type]


def test_input_hash_idx_is_cache_storage(tmp_path: object) -> None:
    """§6.6 'Cache 存储位置: M-0.1 input_hash.idx': 真跑一遍 pipeline 后, ResolverDecisionMade 写入
    携带 input_hash, EventIndex.lookup_input_hash 按 (resolver_id, input_hash) 查回该记录 —— 证 cache
    复用的是 input_hash.idx 索引(非独立 cache 文件)。"""
    from pathlib import Path

    from towow.l0.capsule.pipeline import assemble_capsule
    from towow.l0.commit_gate.gate import CommitGate
    from towow.l0.event_log import EventLog
    from towow.l0.event_log.index import EventIndex
    from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
    from towow.l0.projection.projection import ProjectionStore
    from towow.schemas.enums import EventType

    assert isinstance(tmp_path, Path)
    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    log = EventLog(towow / "events.log")
    CommitGate(log).bootstrap_commit(bootstrap_protocol_invariants())
    store = ProjectionStore(towow / "graph")
    res = assemble_capsule(
        event_log=log,
        projection_store=store,
        scene_type=SceneType.EXECUTION,
        task_id="t-idx",
        target_fork_id="fork-idx",
    )
    # The ResolverDecisionMade event carries input_hash; rebuild the index and look it up.
    rdm = next(
        r for r in log.all_records() if r.event_type is EventType.RESOLVER_DECISION_MADE
    )
    after = rdm.payload["after_state"]
    assert isinstance(after, dict)
    input_hash = after["input_hash"]
    resolver_id = after["resolver_id"]
    assert isinstance(input_hash, str)
    assert input_hash.startswith("sha256:")
    index = EventIndex(log.all_records())
    # input_hash.idx keys on (resolver_id, input_hash) — the spec's reuse index, not a separate file.
    found = index.lookup_input_hash(str(resolver_id), input_hash)
    assert found is not None
    assert found.event_id == rdm.event_id
    # the assembled capsule recorded its input_hash too (same key the cache would look up)
    assert res.capsule.input_hash == input_hash
