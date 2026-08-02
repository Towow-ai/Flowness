"""闭合合约机器复算锚 — f-closure-gate-verdict-check-inverted-and-write-boundary-forgeable
判据 3 (组件2: 写边界锁 audit 两类型)。

历史根因: _is_path_b_allowed 含 AUDIT_VERDICT_RECEIVED / AUDIT_TRIGGERED 且无 producer 绑定 → 任何
import write_direct 者可手发伪造 verdict (node CLI 明拒暴露成伪造口, 内部 write_direct 绕过;
writer_id=m01-writer 真伪一致不能鉴别)。修后: 二类型移出 path-B allow-list, 手 write_direct 它们
raise (镜像 ConceptCreated); 合法路由改走 producer-only 的 _write_audit_event (断言 provenance 为
sanctioned audit producer 才落盘)。

本文件是该 finding closure_contract 判据3 的机器复算锚: 手写伪造 raise + 合法 producer 口仍产真 verdict。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog
from towow.l1.audit_fork import AuditDriverResult, build_audit_verdict_intent
from towow.schemas.enums import (
    ActorType,
    AuditVerdictValue,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path


def _fresh_log(tmp_path: Path) -> EventLog:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    return EventLog(towow / "events.log")


def _audit_triggered_intent(*, actor_id: str = "m05_commit_gate") -> EventIntent:
    return EventIntent(
        local_intent_id=f"at-{uuid.uuid4().hex[:8]}",
        event_type=EventType.AUDIT_TRIGGERED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload={
            "judgment_type": "audit_trigger",
            "after_state": {
                "trigger_reason": "random_sample",
                "envelope_event_id": "evt-env-x",
                "audit_scope": "boundary test",
            },
            "confidence": None,
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.COMMIT_GATE.value, actor_id=actor_id),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="commit_gate", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _sanctioned_verdict_intent() -> EventIntent:
    dr = AuditDriverResult(
        verdict=AuditVerdictValue.PASS, findings=[], recommended_action=None,
        confidence=0.9, summary="ok",
        fork_verdict=type("FV", (), {"spawned": True, "result": {}, "command_text": "", "error": None})(),
    )
    return build_audit_verdict_intent(
        envelope_event_id="evt-env-x", audit_triggered_event_id="evt-at-x", driver_result=dr,
    )


def test_hand_written_audit_verdict_raises(tmp_path: Path) -> None:
    """手 write_direct(AUDIT_VERDICT_RECEIVED / AUDIT_TRIGGERED) 现 raise (二类型移出 path-B allow-list),
    堵掉"任何 import write_direct 者手发伪造 verdict"的口; 合法 producer 口 _write_audit_event 仍产真事件。"""
    log = _fresh_log(tmp_path)

    # 手写伪造两类型经 write_direct → raise (镜像 ConceptCreated 的写边界)。
    with pytest.raises(ValueError, match="not allowed on path B"):
        log.write_direct(_sanctioned_verdict_intent())
    with pytest.raises(ValueError, match="not allowed on path B"):
        log.write_direct(_audit_triggered_intent())

    # producer-only 合法口: sanctioned audit 两类型经 _write_audit_event 落盘成功。
    rec_t = log._write_audit_event(_audit_triggered_intent())
    assert rec_t.event_type is EventType.AUDIT_TRIGGERED
    rec_v = log._write_audit_event(_sanctioned_verdict_intent())
    assert rec_v.event_type is EventType.AUDIT_VERDICT_RECEIVED


def test_non_sanctioned_producer_rejected_at_audit_write(tmp_path: Path) -> None:
    """_write_audit_event 对非 sanctioned producer (actor 不是 commit_gate/m05_audit_* 三值之一) → raise。
    确保合法口本身不是新伪造面 (自报非 sanctioned provenance 打不进)。"""
    log = _fresh_log(tmp_path)
    forged = _audit_triggered_intent(actor_id="m14-execution-session")
    with pytest.raises(ValueError, match="sanctioned audit producer"):
        log._write_audit_event(forged)
