"""波1 T-L0-36: RetentionPolicyChanged retention_action 4态 + GC 按 policy 执行 (M-0.7 §2.6/§4).

源表 done_criteria: policy 变更产 RetentionPolicyChanged 事件(含 retention_action 4 态), 且 GC 按
变更后 policy 执行。owner-decided (RUN-038): 事实源永不自动删 (= §4 矩阵默认 + §4.2 硬约束)。

现状(改前): RetentionRule 仅 event_category/retention_days/can_digest, 无 retention_action 枚举;
GC 写死按 base_classification is DISCARDABLE_NOISE 判可丢。本组证: 4 态枚举 + §4 矩阵默认(事实源
archive 永不 discard) + resolve 按 policy 解析 + immutable_truth 硬约束(policy 误设 discard 也守死)
+ emit/load policy + GC 真按 resolve 判可丢。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l0.snapshot.retention_policy import (
    default_retention_action,
    emit_retention_policy_changed,
    load_active_retention_policy,
    resolve_retention_action,
)
from towow.schemas.enums import BaseClassification, RetentionAction

if TYPE_CHECKING:
    from pathlib import Path

_IT = BaseClassification.IMMUTABLE_TRUTH
_AP = BaseClassification.ABSTRACTABLE_PROCESS
_DN = BaseClassification.DISCARDABLE_NOISE


def test_default_matrix_immutable_never_discard() -> None:
    """§4 矩阵默认: 事实源 archive(永不 discard) / abstractable digest / discardable discard。"""
    assert default_retention_action(_IT) is RetentionAction.ARCHIVE_TO_COLD_AFTER_GC_UNREACHABLE
    assert default_retention_action(_AP) is RetentionAction.DIGEST_THEN_ARCHIVE
    assert default_retention_action(_DN) is RetentionAction.DISCARD_AFTER_CONSUMERS_ADVANCE
    assert default_retention_action(_IT) is not RetentionAction.DISCARD_AFTER_CONSUMERS_ADVANCE


def test_resolve_policy_override_then_default() -> None:
    """policy 命中规则胜出; 未命中走默认。"""
    rules = [{"base_classification": "discardable_noise", "retention_action": "keep_hot_forever"}]
    assert resolve_retention_action(_DN, rules) is RetentionAction.KEEP_HOT_FOREVER  # policy 改了
    assert resolve_retention_action(_AP, rules) is RetentionAction.DIGEST_THEN_ARCHIVE  # 未命中→默认


def test_immutable_truth_discard_hard_blocked() -> None:
    """§4.2 + owner: policy 误把 immutable_truth 设成 discard, resolve 也守死成 archive (永不 discard)。"""
    evil = [{"base_classification": "immutable_truth", "retention_action": "discard_after_consumers_advance"}]
    assert resolve_retention_action(_IT, evil) is RetentionAction.ARCHIVE_TO_COLD_AFTER_GC_UNREACHABLE


def test_emit_and_load_latest_policy(tmp_path: Path) -> None:
    """emit RetentionPolicyChanged → load 读回 new_rules; 多次 emit 取最新。"""
    event_log = EventLog(tmp_path / "events.log")
    assert load_active_retention_policy(event_log) == []  # 无 policy → 空 (走默认)
    emit_retention_policy_changed(
        event_log, policy_id="pol-1",
        new_rules=[{"base_classification": "discardable_noise", "retention_action": "keep_hot_forever"}],
        change_reason="v1",
    )
    emit_retention_policy_changed(
        event_log, policy_id="pol-2",
        new_rules=[{"base_classification": "discardable_noise", "retention_action": "digest_then_archive"}],
        change_reason="v2 latest",
    )
    rules = load_active_retention_policy(EventLog(tmp_path / "events.log"))
    assert len(rules) == 1
    assert rules[0]["retention_action"] == "digest_then_archive"  # 最新 (pol-2) 胜出


def test_gc_consults_policy_immutable_never_in_archivable(tmp_path: Path) -> None:
    """GC 按 resolve 判可丢: 默认下 immutable_truth 永不进可丢集 (事实源不删)。"""
    from towow.l0.commit_gate.gate import CommitGate
    from towow.l0.obligations.bootstrap import bootstrap_protocol_invariants
    from towow.l0.projection.projection import ProjectionStore
    from towow.l0.snapshot import create_standalone_snapshot
    from towow.l0.snapshot.gc import run_gc

    towow = tmp_path / ".towow"
    towow.mkdir(parents=True)
    (towow / "locks").mkdir()
    event_log = EventLog(towow / "events.log")
    CommitGate(event_log).bootstrap_commit(bootstrap_protocol_invariants())  # immutable_truth obligations
    ps = ProjectionStore(towow / "graph")
    create_standalone_snapshot(event_log=event_log, projection_store=ps, towow_dir=towow)  # bound>0
    result = run_gc(event_log=event_log, projection_store=ps, towow_dir=towow)
    # bootstrap obligation 事件是 immutable_truth → 默认 archive, 永不进可丢集
    immutable_seqs = {
        r.sequence_number for r in event_log.all_records()
        if r.base_classification is _IT
    }
    assert immutable_seqs, "应有 immutable_truth 事件 (bootstrap)"
    assert not (immutable_seqs & set(result.archivable_noise_seqs)), "事实源永不进 GC 可丢集"
