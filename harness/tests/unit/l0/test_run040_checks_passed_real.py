"""RUN-040 F-RA-1: commit gate checks_passed 接运行时真值 (非写死占位).

# 现状(本测试先暴露): CommitAccepted.checks_passed 全仓唯一赋值点写死
#   [{"check_type": "envelope_integrity", "result": "passed"}] —— 与 _run_checks 实跑的
#   ~10 道 check (EnvelopeIntegrity / VersionConsistency / WriteConflict / FreshnessDrift /
#   ObligationCheck / CompletionClaim …) 完全无关。本测试断言 checks_passed 必须反映本次真
#   跑过哪些 check + 各自结果 —— 在写死实现下失败 (RED), 接真累积后通过 (GREEN)。
#
# 拒批侧 detected_violation_event_refs (RUN-029 已修镜像字段) 不在本 cap 触碰范围, 不回退。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.commit_gate import CommitGate
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
from towow.schemas.payloads.commit import CommitAcceptedPayload

if TYPE_CHECKING:
    from pathlib import Path


def _make_envelope_intent(task_id: str = "t-r04") -> EventIntent:
    """一个干净 envelope — 走 accept 路径 (全 check 通过)。模板取自 test_e11_regression。"""
    return EventIntent(
        local_intent_id=f"env-{task_id}",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "task_id": task_id,
            "run_id": f"r-{task_id}",
            "capsule_compiled_event_id": "evt-stub-capsule-r040",
            "read_set": [{"entity_type": "task", "entity_id": task_id, "version": "evt-cap"}],
            "write_set": [{"entity_type": "task", "entity_id": task_id, "change_type": "modified"}],
            "patches": [],
            "self_check": {"passed": True, "checks_run": ["smoke"]},
            "as_of_projection_seq": 0,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=f"fork-{task_id}",
            task_id=task_id,
            run_id=f"r-{task_id}",
            parent_session_id=f"sess-{task_id}",
            fork_name=f"fork-{task_id}",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _commit_accepted_checks(tmp_path: Path) -> list[dict[str, object]]:
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    records = gate.attempt_commit(_make_envelope_intent(), domain_intents=[])
    sentinel = records[-1]
    assert sentinel.event_type is EventType.COMMIT_ACCEPTED
    checks = sentinel.payload["checks_passed"]
    assert isinstance(checks, list)
    return checks


def test_checks_passed_is_not_hardcoded_single_envelope_integrity(tmp_path: Path) -> None:
    """RED 暴露: 写死实现下 checks_passed 恒为单条 envelope_integrity, 与实跑无关。

    accept 路径上 _run_checks 真跑了 VersionConsistency / WriteConflict / FreshnessDrift /
    ObligationCheck / CompletionClaim 等多道 check, 它们必须出现在 checks_passed 里。
    写死实现 → 只有 envelope_integrity 一条 → 本断言失败。
    """
    checks = _commit_accepted_checks(tmp_path)
    types = {str(c.get("check_type")) for c in checks}

    # 写死占位只有这一条 — 真累积后必然 > 1 道。
    assert len(checks) > 1, f"checks_passed 仍是写死单条占位: {checks}"

    # 这几道在干净 accept 路径上确实跑过且通过, 必须如实出现 (envelope_integrity 之外)。
    expected_real_checks = {
        "version_consistency",
        "write_conflict",
        "freshness_drift",
        "obligation_check",
        "completion_claims",
    }
    missing = expected_real_checks - types
    assert not missing, f"checks_passed 漏了真跑过的 check: {missing}; 实有 {types}"


def test_checks_passed_each_item_records_result(tmp_path: Path) -> None:
    """每条 checks_passed item 带 check_type + result, accept 路径上 result 反映真结果(passed)。"""
    checks = _commit_accepted_checks(tmp_path)
    assert "envelope_integrity" in {str(c.get("check_type")) for c in checks}
    for c in checks:
        assert c.get("check_type"), f"check_type 不能空: {c}"
        # accept 路径上每道记录的 check 结果是 passed / not_applicable (真结果), 不是 None 占位。
        assert c.get("result") in {"passed", "not_applicable"}, f"非法 result: {c}"


def test_accepted_payload_with_real_checks_validates_schema(tmp_path: Path) -> None:
    """真累积的 checks_passed 仍满足 CommitAcceptedPayload schema (CheckPerformed)。"""
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    records = gate.attempt_commit(_make_envelope_intent(), domain_intents=[])
    sentinel = records[-1]
    # schema 校验不抛 = 真累积结果结构合法。
    model = CommitAcceptedPayload.model_validate(sentinel.payload)
    assert len(model.checks_passed) > 1
