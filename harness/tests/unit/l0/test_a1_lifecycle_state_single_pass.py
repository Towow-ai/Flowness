"""A1 (2026-07-02 OOM 崩机根治): EventLog._derive_lifecycle_state 单趟廉价派生的等价性钉子。

崩机勘查发现: __init__ 曾对同一段日志做 3 趟独立全量 pydantic 扫描 (求 max_seq / 建
event_id 集 / 建 sequence 集)，改成一趟廉价 json.loads 扫描 (_iter_committed_lifecycle_fields)。
本文件钉住: 新的单趟廉价路径与旧的 pydantic 全扫路径在真实数据 (含未提交 path-A 批次)
上产出完全一致的三元组，且 committed-visibility 规则 (Patch 2 §2.1) 没有被廉价化破坏掉。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _node_touched(*, local_intent_id: str, entity_id: str = "c-1") -> EventIntent:
    return EventIntent(
        local_intent_id=local_intent_id,
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": entity_id, "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id="fork-1",
            task_id="t-1",
            parent_session_id="sess-test",
            fork_name="fork-1",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id=entity_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _uncommitted_envelope() -> EventIntent:
    """一个只有 envelope、没有 sentinel 的半截 path-A 批次 (Patch 2 §2.1 不可见)。"""
    return EventIntent(
        local_intent_id="env-half",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "read_set": [], "write_set": [], "active_obligations_declared": [],
            "claims": [], "patches": [], "uncertainties": [],
            "self_check": {"passed": True, "checks_run": []},
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value, actor_id="fork-1", task_id="t-1",
            parent_session_id="sess-test", fork_name="fork-1",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_single_pass_matches_pydantic_scan_on_mixed_committed_stream(tmp_path: Path) -> None:
    """真实混合流 (多条 path-B 提交 + 一条未提交 path-A envelope) 上, 新旧两条路径必须给出
    完全一样的 (next_sequence, event_id_set, sequence_set) —— 且未提交批次的 seq/event_id
    绝不能泄漏进任一条路径的结果 (committed-visibility 不能被廉价化破坏)。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path)

    committed_records = [log.write_direct(_node_touched(local_intent_id=f"i-{i}")) for i in range(5)]

    # 手动追加一条未提交 (无 sentinel) 的 path-A envelope — 必须被两条路径都排除。
    env = log.stamp_for_batch(
        _uncommitted_envelope(), transaction_id="tx-half", batch_id="b-half", batch_position=0,
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(env.model_dump_json() + "\n")

    # "旧路径" 参照系: 直接用仍然存在、仍走 pydantic 全解析的 _iter_committed_records。
    old_next_seq = max((r.sequence_number for r in log._iter_committed_records()), default=-1) + 1
    old_event_ids = {r.event_id for r in log._iter_committed_records()}
    old_seqs = {r.sequence_number for r in log._iter_committed_records()}

    new_next_seq, new_event_ids, new_seqs = log._derive_lifecycle_state()

    assert new_next_seq == old_next_seq, f"next_sequence 必须一致: 新={new_next_seq} 旧={old_next_seq}"
    assert new_event_ids == old_event_ids, "event_id_set 必须完全一致"
    assert new_seqs == old_seqs, "sequence_set 必须完全一致"

    # 语义断言: 5 条已提交 + 未提交 envelope 被排除 (不是 6 条)。
    assert len(new_event_ids) == 5, f"未提交 path-A envelope 不该计入 (实得 {len(new_event_ids)} 条)"
    assert env.event_id not in new_event_ids, "未提交 envelope 的 event_id 绝不能出现在 event_id_set"
    assert env.sequence_number not in new_seqs, "未提交 envelope 的 seq 绝不能出现在 sequence_set"
    assert new_next_seq == max(r.sequence_number for r in committed_records) + 1


def test_single_pass_after_recover_matches_pydantic_scan(tmp_path: Path) -> None:
    """recover() 截断路径同样改用单趟派生 —— 截断后三元组仍必须与 pydantic 参照系一致。"""
    log_path = tmp_path / "events.log"
    log = EventLog(log_path)
    for i in range(3):
        log.write_direct(_node_touched(local_intent_id=f"i-{i}"))

    # 手动追加一条损坏的截断尾巴 (模拟崩溃中断的写), 触发 recover() 的截断路径。
    with log_path.open("a", encoding="utf-8") as f:
        f.write('{"broken json..\n')

    truncated = log.recover()
    assert truncated > 0, "recover 应检测到并截掉损坏的尾行"

    old_next_seq = max((r.sequence_number for r in log._iter_committed_records()), default=-1) + 1
    old_event_ids = {r.event_id for r in log._iter_committed_records()}
    old_seqs = {r.sequence_number for r in log._iter_committed_records()}

    # recover() 内部已用新方法重新派生过一次; 这里独立再调一次确认幂等且与参照系一致。
    new_next_seq, new_event_ids, new_seqs = log._derive_lifecycle_state()
    assert new_next_seq == old_next_seq
    assert new_event_ids == old_event_ids
    assert new_seqs == old_seqs
    assert len(new_event_ids) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
