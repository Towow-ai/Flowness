"""f-perf2-vitality-full-materialize-for-narrow-type-scan 回归测试。

背景 (ledger-perf-diagnosis 确诊, 性能治理第二轮 症状B): `towow vitality` 峰值内存 5.4GB /
耗时 33s, 大头是 session_vitality.build_work_product_map 调用
`event_log.committed_index().records()` —— 把整条账本(现网 62.9 万+事件, 91% 是 NodeTouched
stub-rewrap)全部 pydantic 解析成 EventRecord, 只为了从中挑出 ~600 条它真正关心的 6 种类型
(TaskRunCompleted/PatchProposed/TransactionEnvelopeSubmitted/FixProposed/FixCompleted/
CommitAccepted, 直接类型或 NodeTouched stub-rewrap 两种形态都要认)。

修法: EventIndex/LazyEventIndex 新增 lookup_types_or_stub_kinds —— LazyEventIndex 对
NodeTouched 桶先做一次廉价的原始字节 `"kind":"<T>"` 子串预筛, miss 的记录零 pydantic 解析成本
直接跳过; hit 的和已缓存的记录才走真正的 _materialize()+payload.kind 复核 (预筛只筛掉候选,
从不是包含判据本身, 结果与"materialize 全量再 unwrap 过滤"字节对字节相同)。

本文件钉三件事:
  1. test_lazy_lookup_matches_full_scan_reference — LazyEventIndex 路径(真实 reopen 触发的
     adopted-index 场景)的结果与"物化全部再手工 unwrap 过滤"的参照实现完全一致 (直接类型 +
     stub-rewrap 匹配 kind + stub-rewrap 不匹配 kind + 无关 flat 类型 混合, 语义等价的核心证据)。
  2. test_lazy_lookup_skips_materialize_for_non_matching_stubs — 字节预筛真的在跳过工作:
     不匹配的 NodeTouched 记录不应触发 _materialize (spy 计数 < NodeTouched 总数)。
  3. test_build_work_product_map_matches_full_scan_reference — session_vitality 层端到端等价:
     新走的窄类型查询与旧的"全量物化 + 逐条 unwrap"参照实现产出同一个 work-product 字典。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.l0.event_log import EventLog
from towow.l0.event_log.index import LazyEventIndex
from towow.l2.orchestrator import _unwrap_stub_rewrap
from towow.l2.session_vitality import build_work_product_map
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

_WANTED_TYPES = frozenset({
    EventType.TASK_RUN_COMPLETED, EventType.PATCH_PROPOSED,
    EventType.TRANSACTION_ENVELOPE_SUBMITTED, EventType.FIX_PROPOSED,
    EventType.FIX_COMPLETED, EventType.COMMIT_ACCEPTED,
})
_WANTED_TYPE_VALUES = frozenset(t.value for t in _WANTED_TYPES)


def _stub(event_log: EventLog, kind: str, after_state: dict[str, object]) -> str:
    """Emit a stub-rewrap NodeTouched carrying `kind` (same shape production/round-1 tests use)."""
    entity_id = str(after_state.get("task_id") or after_state.get("plan_id") or kind)
    intent = EventIntent(
        local_intent_id=f"{kind.lower()}-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": entity_id,
            "touch_type": "write",
            "kind": kind,
            "stub_original_payload": {"kind": kind, "after_state": after_state, **after_state},
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-emitter"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(entity_type=SubjectEntityType.TASK, entity_id=entity_id, role=SubjectRole.PRIMARY),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def _direct_envelope(event_log: EventLog, task_id: str) -> str:
    """Emit a FLAT (non-stub) TransactionEnvelopeSubmitted — exercises the direct-type-match branch
    of lookup_types_or_stub_kinds (not just the NodeTouched-unwrap branch). Exempt from strict
    payload validation (PAYLOAD_VALIDATION_EXEMPT), so a minimal payload is legal here."""
    intent = EventIntent(
        local_intent_id=f"env-{uuid.uuid4().hex[:8]}",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={"task_id": task_id},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value, actor_id=f"fork-{task_id}",
            task_id=task_id, parent_session_id=f"sess-{task_id}", fork_name=f"fork-{task_id}",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def _build_mixed_ledger(event_log: EventLog) -> None:
    """Direct + stub-rewrap matching events, mixed with plenty of non-matching NodeTouched noise
    (mirrors the real ledger's 91%-NodeTouched shape) and one unrelated flat event type."""
    _direct_envelope(event_log, "T-direct-env")
    _stub(event_log, "TaskRunCompleted", {"task_id": "T-succ-1", "outcome": "success"})
    _stub(event_log, "TaskRunCompleted", {"task_id": "T-fail-1", "outcome": "aborted_for_replan"})
    _stub(event_log, "PatchProposed", {"task_id": "T-patch-1"})
    _stub(event_log, "FixProposed", {"task_id": "T-fix-1"})
    _stub(event_log, "FixCompleted", {"task_id": "T-fix-1"})
    _stub(event_log, "CommitAccepted", {"task_id": "T-commit-1"})
    # noise: NodeTouched stub-rewraps whose kind is NOT in the wanted set — must never be returned.
    for i in range(30):
        _stub(event_log, "TaskNodeTouchedNoise", {"task_id": f"T-noise-{i}"})
    # noise: a flat event type unrelated to the wanted set.
    _stub(event_log, "PlanFreezed", {"plan_id": "plan-noise-1"})


def _reference_matching_records(event_log: EventLog) -> list[tuple[str, dict[str, object]]]:
    """Ground truth: materialize EVERY committed record and unwrap it — the pre-fix behavior."""
    out = []
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        if etype in _WANTED_TYPE_VALUES:
            out.append((rec.event_id, payload))
    return out


def test_lazy_lookup_matches_full_scan_reference(tmp_path: Path) -> None:
    """LazyEventIndex.lookup_types_or_stub_kinds 与全量物化+unwrap 参照实现结果一致。"""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path)
    _build_mixed_ledger(writer)

    reference = _reference_matching_records(writer)
    reference_ids = {eid for eid, _ in reference}

    reader = EventLog(log_path)  # fresh process-like open — adopts the persisted .idx
    assert isinstance(reader._index, LazyEventIndex), (
        "test precondition: reopen should adopt a LazyEventIndex (the realistic cold-CLI scenario "
        "this fix targets) — if this fails the adopt-gate behavior changed and this test's premise "
        "no longer holds"
    )

    got = reader.get_events_by_types_or_stub_kinds(_WANTED_TYPES)
    got_ids = {rec.event_id for rec in got}

    assert got_ids == reference_ids, (
        f"lookup_types_or_stub_kinds diverged from full-scan reference: "
        f"missing={reference_ids - got_ids} extra={got_ids - reference_ids}"
    )
    # the flat direct-type record must be included (proves the direct-type branch, not just unwrap)
    direct_env = [r for r in got if r.event_type is EventType.TRANSACTION_ENVELOPE_SUBMITTED]
    assert len(direct_env) == 1


def test_lazy_lookup_skips_materialize_for_non_matching_stubs(tmp_path: Path) -> None:
    """字节预筛应跳过大多数不匹配的 NodeTouched — 不该对着 30 条 noise 都付 _materialize 成本。"""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path)
    _build_mixed_ledger(writer)
    writer.committed_index()  # force writer to persist .idx so the reopen below can adopt it

    reader = EventLog(log_path)  # fresh process-like open — adopts the persisted .idx
    assert isinstance(reader._index, LazyEventIndex), (
        "test precondition: reopen should adopt a LazyEventIndex (the realistic cold-CLI scenario "
        "this fix targets)"
    )

    node_touched_total = len(reader._index._lazy_postings.get("type", {}).get("NodeTouched", []))
    assert node_touched_total >= 30, "test precondition: enough NodeTouched noise to matter"

    real_materialize = reader._index._materialize
    counter = {"n": 0}

    def _spy(event_id: str):
        counter["n"] += 1
        return real_materialize(event_id)

    reader._index._materialize = _spy  # type: ignore[method-assign]

    got = reader.get_events_by_types_or_stub_kinds(_WANTED_TYPES)
    assert len(got) == 7  # 1 direct + 6 matching stubs from _build_mixed_ledger

    assert counter["n"] < node_touched_total, (
        f"byte pre-filter should have skipped _materialize for most of the {node_touched_total} "
        f"non-matching NodeTouched records, but it was called {counter['n']} times — pre-filter "
        "regression (every NodeTouched record is paying the pydantic parse again)"
    )


def test_build_work_product_map_matches_full_scan_reference(tmp_path: Path) -> None:
    """session_vitality.build_work_product_map 端到端: 新窄查询路径与旧全量扫描参照实现同结果。"""
    log_path = tmp_path / "events.log"
    writer = EventLog(log_path)
    _build_mixed_ledger(writer)

    # reference implementation: the OLD full-scan-and-unwrap logic, reimplemented inline so this
    # test doesn't silently pass by calling the same (possibly-buggy) production code twice.
    succ: set[str] = set()
    part: set[str] = set()
    abrt: set[str] = set()
    _partial_types = {
        "PatchProposed", "TransactionEnvelopeSubmitted", "FixProposed",
        "FixCompleted", "CommitAccepted",
    }
    for rec in writer.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state", payload) if isinstance(payload, dict) else {}
        tid = (after.get("task_id") if isinstance(after, dict) else None) or (
            payload.get("task_id") if isinstance(payload, dict) else None
        )
        if not tid:
            continue
        if etype == "TaskRunCompleted":
            if isinstance(after, dict) and after.get("outcome") == "success":
                succ.add(tid)
            else:
                part.add(tid)
                abrt.add(tid)
        elif etype in _partial_types:
            part.add(tid)
    keys = succ | part
    reference = {k: (k in succ, k in part, k in abrt) for k in keys}

    reader = EventLog(log_path)
    assert isinstance(reader._index, LazyEventIndex)
    got = build_work_product_map(reader)

    assert got == reference, f"build_work_product_map diverged from reference: got={got} ref={reference}"
    assert got["T-succ-1"] == (True, False, False)
    assert got["T-fail-1"] == (False, True, True)
    assert got["T-patch-1"] == (False, True, False)
    assert got["T-direct-env"] == (False, True, False)
    assert "T-noise-0" not in got
