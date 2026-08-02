"""波1 T-L0-23: claims 派生接入生产 submit (从 Planner claim event 派生, OVERRIDE agent 手填).

源表 done_criteria: 生产 submit 的 envelope.claims 来自 derive_claims(Planner event), 篡改 agent
手填 claims 不影响真 claims; M-0.5 ClaimsBoundary 检查拿到真 claims 基线。

现状(改前, fake-done): (a) derive_claims 读 payload.claimed_entities/entities 顶层形, 但真实 Planner
payload 是 after_state.{read_set|write_set} → 对真实事件恒返空 (单测过但生产派不出真 claims);
(b) 生产 submit 的 _derive_envelope_boundaries 不碰 claims → envelope.claims 是 agent 手填或空。
本组证: derive_claims 真读 after_state payload; _derive_envelope_boundaries 从 Planner 事件派生 claims
并 OVERRIDE agent 手填 (篡改不影响真 claims)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from towow.cli.main import _derive_envelope_boundaries
from towow.l0.envelope.builder import derive_claims
from towow.schemas.enums import (
    BaseClassification,
    EnvelopeClaimType,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

_READ = EventType.TASK_READ_SET_CLAIMED
_WRITE = EventType.TASK_WRITE_SET_CLAIMED


def _claim_record(seq: int, event_type: EventType, after_state: dict[str, object]) -> EventRecord:
    """构造真实形 Planner claim 事件 (after_state.{read_set|write_set})。"""
    return EventRecord(
        event_id=f"evt-{seq}",
        sequence_number=seq,
        timestamp=datetime(2026, 5, 30, tzinfo=UTC) + timedelta(microseconds=seq),
        transaction_id=None,
        batch_id=None,
        batch_position=None,
        record_hash=f"h-{seq}",
        local_intent_id=f"li-{seq}",
        event_type=event_type,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"after_state": after_state},
        provenance=Provenance(actor_type="system", actor_id="planner", writer_id="m01-writer"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False, novelty=None),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t-1", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _claim(seq: int, kind: EventType, task_id: str, etype: str, eid: str) -> EventRecord:
    """单实体 read/write claim 记录 (紧凑构造)。"""
    set_key = "read_set" if kind is _READ else "write_set"
    return _claim_record(seq, kind, {"task_id": task_id, set_key: [{"entity_type": etype, "entity_id": eid}]})


class _StubLog:
    """最小 event_log: 只实现 _derive_envelope_boundaries 用到的 get_events_by_type。"""

    def __init__(self, by_type: dict[EventType, list[EventRecord]]) -> None:
        self._by_type = by_type

    def get_events_by_type(self, event_type: EventType) -> list[EventRecord]:
        return self._by_type.get(event_type, [])


def test_derive_claims_reads_real_after_state_payload() -> None:
    """derive_claims 真从 after_state.{read_set|write_set} 提 claims (改前读错路径恒返空)。"""
    events = [
        _claim(1, _READ, "t-1", "concept", "c-1"),
        _claim(2, _WRITE, "t-1", "task", "t-9"),
    ]
    by = {(c.entity_type.value, c.entity_id): c.claim_type for c in derive_claims(events)}
    assert by[("concept", "c-1")] is EnvelopeClaimType.READ_CLAIM
    assert by[("task", "t-9")] is EnvelopeClaimType.WRITE_CLAIM


def test_submit_derives_claims_from_planner_overriding_agent() -> None:
    """_derive_envelope_boundaries 从 Planner 事件派生 claims, OVERRIDE agent 手填 (篡改不影响真 claims)。"""
    log = _StubLog(
        {
            _READ: [_claim(1, _READ, "t-1", "concept", "c-real")],
            _WRITE: [_claim(2, _WRITE, "t-1", "task", "t-real")],
        },
    )
    # agent 手填一条伪造 claim (想绕过边界检查)
    envelope_data: dict[str, object] = {
        "task_id": "t-1",
        "claims": [{"entity_type": "concept", "entity_id": "c-FORGED", "claim_type": "write_claim"}],
    }
    _derive_envelope_boundaries(envelope_data, [], log)  # type: ignore[arg-type]

    claims = envelope_data["claims"]
    assert isinstance(claims, list)
    ids = {c["entity_id"] for c in claims}
    assert ids == {"c-real", "t-real"}, "claims 来自 Planner 事件"
    assert "c-FORGED" not in ids, "agent 手填的伪造 claim 被 OVERRIDE 忽略 (篡改不影响真 claims)"


def test_no_planner_claims_yields_empty_overriding_agent() -> None:
    """无 Planner 预声明 → claims=[] (OVERRIDE agent 手填; M-0.5 空则跳边界检查)。"""
    log = _StubLog({})  # 该 task 无任何 claim 事件
    envelope_data: dict[str, object] = {
        "task_id": "t-1",
        "claims": [{"entity_type": "task", "entity_id": "t-sneaky", "claim_type": "write_claim"}],
    }
    _derive_envelope_boundaries(envelope_data, [], log)  # type: ignore[arg-type]
    assert envelope_data["claims"] == [], "无 Planner 声明 → 真 claims 空, agent 手填被清"


def test_filters_claim_events_by_task_id() -> None:
    """只取本 task 的 Planner claim 事件 (别的 task 的 claim 不串)。"""
    log = _StubLog(
        {
            _READ: [
                _claim(1, _READ, "t-OTHER", "concept", "c-other"),
                _claim(2, _READ, "t-1", "concept", "c-mine"),
            ],
        },
    )
    envelope_data: dict[str, object] = {"task_id": "t-1", "claims": []}
    _derive_envelope_boundaries(envelope_data, [], log)  # type: ignore[arg-type]
    claims = envelope_data["claims"]
    assert isinstance(claims, list)
    assert {c["entity_id"] for c in claims} == {"c-mine"}, "别的 task 的 claim 不算"
