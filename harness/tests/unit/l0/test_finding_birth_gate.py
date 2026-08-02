"""T-LRF-01 finding-routability-birth-gate@v1 (birth-time-validation@v1 第一具名实例).

dc1 (出生拦截 + 拒绝测试断言不落 canonical): commit gate 对不可路由的 FindingCreated 准入拒绝,
该 FindingCreated 不落 canonical 事件 (rejected batch = [gate-lifecycle…, CommitRejected], 无域事件)。
dc2 (fallback 纵深保留): orchestrator._fallback_finding_route 保留为出生后兜底, 未删除。

可路由性判据 (concept 钉死): finding_kind ∈ _DISPATCH_TABLE_FINDING_KIND 或
suggested_fix_layer.primary 可映射 (∈ _FALLBACK_FIX_LAYER_DISPATCH); 两者皆无 → 出生拦截。

companion (write_set 扩展, finding-lrf-batch-writeset-underdeclaration 复发): 出生闸 enforce 后,
活体 sentinel 自监控 (m-l6-sentinel, 经 commit gate emit, 历史 finding_kind=None) 会被门拒而静默
熄火 → 同 envelope 原子修 sentinel 发出可路由 finding_kind。本测试含 sentinel 回归守卫。

合成 tempdir / 合成 intent, 绝不触碰 live 账本。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from towow.l0.commit_gate import CommitGate
from towow.l0.commit_gate.finding_birth_gate import (
    FindingRoutabilityResult,
    check_finding_routability,
)
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


# ─── helpers: 合成 FindingCreated / envelope intent ────────────────────────────────────


def _finding_intent(
    *,
    finding_id: str = "f-test",
    finding_kind: str | None = None,
    suggested_fix_layer: dict | None = None,
    source: dict | None = None,
) -> EventIntent:
    payload: dict[str, object] = {
        "finding_id": finding_id,
        "severity": "major",
        "risk_surface": "test surface",
        "lifecycle_state": "created",
        "description": "test finding",
        "detection_method": "automated_rule",
        "finding_kind": finding_kind,
    }
    if suggested_fix_layer is not None:
        payload["suggested_fix_layer"] = suggested_fix_layer
    if source is not None:
        payload["source"] = source
    return EventIntent(
        local_intent_id=f"fc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload=payload,
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _non_finding_intent() -> EventIntent:
    """一个非 FindingCreated 域 intent (出生闸应跳过)。"""
    return EventIntent(
        local_intent_id=f"nt-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"kind": "x"},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _finding_envelope_intent(finding_id: str = "f-test") -> EventIntent:
    """承载 FindingCreated 的 envelope — 自检带 review.review_contract_present (FINDING_CREATED 的
    completeness 必需), 让正向路径过总闸 (镜像 daemon._emit_finding_via_gate 形态)。"""
    env_id = f"env-{uuid.uuid4().hex[:12]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "capsule_compiled_event_id": "evt-stub-capsule-birthgate-test",
            "self_check": {
                "passed": True,
                "checks_run": [],
                "blocking_checks": [
                    {
                        "check_id": "review.review_contract_present",
                        "status": "passed",
                        "evidence": {"closure": "birth-gate-test"},
                    },
                ],
            },
            "active_obligations_declared": [],
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value, actor_id="test-birthgate",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _finding_records_in_log(log: EventLog) -> list[object]:
    return [r for r in log.all_records() if r.event_type is EventType.FINDING_CREATED]


# ─── 单元: check_finding_routability 判据 ──────────────────────────────────────────────


def test_unroutable_none_kind_no_layer_rejected() -> None:
    """finding_kind=None + 无 suggested_fix_layer → 不可路由 (出生应拦)。"""
    res = check_finding_routability([_finding_intent(finding_kind=None)])
    assert res.passed is False
    assert res.failure_reason == "finding_unroutable"
    assert res.failure_evidence.get("finding_id") == "f-test"


def test_routable_via_finding_kind() -> None:
    """finding_kind 在 dispatch 表里 → 可路由。"""
    res = check_finding_routability([_finding_intent(finding_kind="anomaly")])
    assert res.passed is True


def test_routable_via_suggested_fix_layer() -> None:
    """finding_kind=None 但 suggested_fix_layer.primary 可映射 → 可路由 (review finding 形态)。"""
    res = check_finding_routability(
        [_finding_intent(finding_kind=None, suggested_fix_layer={"primary": "code"})]
    )
    assert res.passed is True


def test_invalid_finding_kind_rejected() -> None:
    """finding_kind 是字符串但不在 dispatch 表 (路由到 no-route) → 不可路由 (按表成员判, 非 enum 成员)。"""
    res = check_finding_routability([_finding_intent(finding_kind="not_a_real_kind")])
    assert res.passed is False
    assert res.failure_reason == "finding_unroutable"


def test_unroutable_layer_not_in_map_rejected() -> None:
    """suggested_fix_layer.primary 不在 fallback 映射表 (如 'capsule') + finding_kind=None → 不可路由。"""
    res = check_finding_routability(
        [_finding_intent(finding_kind=None, suggested_fix_layer={"primary": "capsule"})]
    )
    assert res.passed is False


def test_non_finding_intent_skipped() -> None:
    """非 FindingCreated intent → 出生闸跳过 (passed)。"""
    assert check_finding_routability([_non_finding_intent()]).passed is True


def test_empty_batch_passes() -> None:
    """无域 intent → 出生闸无对象, 通过。"""
    assert check_finding_routability([]).passed is True


def test_result_is_frozen_dataclass() -> None:
    """结果是 frozen dataclass (镜像其他 gate check 结果形态)。"""
    res = FindingRoutabilityResult(passed=True)
    assert res.passed is True
    assert res.failure_reason is None


# ─── 端到端 (dc1): 经 CommitGate.attempt_commit, 出生拦截 + 不落 canonical ────────────────


def test_unroutable_finding_rejected_at_birth_absent_from_canonical(tmp_path: Path) -> None:
    """★ dc1 核心: 不可路由 FindingCreated 过 commit gate → 拒批, 且该 FindingCreated 不落 canonical。

    rejected batch = [gate-lifecycle…, CommitRejected], 无域事件 → 直接断言 log 里零 FindingCreated。
    """
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    records = gate.attempt_commit(
        _finding_envelope_intent("f-unroutable"),
        domain_intents=[_finding_intent(finding_id="f-unroutable", finding_kind=None)],
    )
    sentinel = records[-1]
    assert sentinel.event_type is EventType.COMMIT_REJECTED, (
        f"不可路由 finding 必须被出生闸拒, 实得 sentinel={sentinel.event_type}"
    )
    # 出生拦截的实质: FindingCreated 没进 canonical 事件流 (不是"进了再下游处理")。
    assert _finding_records_in_log(log) == [], "被拒的 FindingCreated 绝不能落 canonical 事件"


def test_routable_finding_admitted_lands_in_canonical(tmp_path: Path) -> None:
    """★ 正向回归守卫: 可路由 FindingCreated (finding_kind ∈ 表) 过门被接受, 真落 canonical。

    防"出生闸误拒合法 finding" (advisor 钉死的正向回归面)。
    """
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    records = gate.attempt_commit(
        _finding_envelope_intent("f-routable"),
        domain_intents=[_finding_intent(finding_id="f-routable", finding_kind="anomaly")],
    )
    sentinel = records[-1]
    assert sentinel.event_type is EventType.COMMIT_ACCEPTED, (
        f"可路由 finding 必须被接受, 实得 sentinel={sentinel.event_type}"
    )
    landed = _finding_records_in_log(log)
    assert len(landed) == 1
    assert landed[0].payload.get("finding_id") == "f-routable"


# ─── sentinel companion 回归守卫 (write_set 扩展: sentinel_loop.py 发可路由 kind) ──────────


def test_sentinel_built_finding_passes_birth_gate() -> None:
    """活体 sentinel 发的 FindingCreated 必须过出生闸 (修后 emit 可路由 finding_kind, 不再静默熄火)。"""
    from towow.awareness.detection_rules import Route, SentinelFinding, Severity
    from towow.l2.sentinel_loop import build_sentinel_finding_created_intent

    f = SentinelFinding(
        rule_id="A1", finding_kind="safety_invariant", severity=Severity.CRITICAL,
        route=Route.PUSH, detail="test sentinel observation", trigger_event_id="evt-x",
    )
    intent = build_sentinel_finding_created_intent(f)
    assert check_finding_routability([intent]).passed is True, (
        "sentinel finding 必须可路由 (出生闸 enforce 后才不静默熄火活体自监控)"
    )


def test_sentinel_kind_mapping() -> None:
    """sentinel taxonomy → 可路由 dispatch kind: 非表内 kind → anomaly (纯 surface);
    已在表内 kind (efficiency_regression → R07 治理环) 原样保留。"""
    from towow.l2.sentinel_loop import _sentinel_routable_finding_kind

    assert _sentinel_routable_finding_kind("safety_invariant") == "anomaly"
    assert _sentinel_routable_finding_kind("liveness_misjudge") == "anomaly"
    assert _sentinel_routable_finding_kind(None) == "anomaly"
    assert _sentinel_routable_finding_kind("efficiency_regression") == "efficiency_regression"


def test_sentinel_internal_taxonomy_preserved_for_dedup() -> None:
    """内部 SentinelFinding.finding_kind 不被改 (finding_signature 去重锚仍用它); 只 emit 的 payload
    finding_kind 被设为可路由 dispatch kind (rule_id/risk_surface 保细粒度)。"""
    from towow.awareness.detection_rules import Route, SentinelFinding, Severity
    from towow.l2.sentinel_loop import build_sentinel_finding_created_intent

    f = SentinelFinding(
        rule_id="A2", finding_kind="liveness_misjudge", severity=Severity.MAJOR,
        route=Route.PULL, detail="detail-text",
    )
    intent = build_sentinel_finding_created_intent(f)
    # 内部 taxonomy 仍在 dataclass 上 (供 signature 去重), 细粒度落 rule_id。
    assert f.finding_kind == "liveness_misjudge"
    assert intent.payload.get("finding_kind") == "anomaly"
    assert intent.payload.get("rule_id") == "A2"


# ─── dc2: fallback 纵深保留 (未删除) ───────────────────────────────────────────────────


def test_fallback_finding_route_retained_as_defense_in_depth() -> None:
    """dc2: orchestrator._fallback_finding_route 保留 (出生后纵深兜底, 未删除)。

    出生闸是正常路径首拦; fallback 针对未来新增 enum 等出生闸未覆盖的漏网, 作纵深防御保留。
    """
    from towow.l2.orchestrator import _fallback_finding_route

    # 仍可调用且对可映射 suggested_fix_layer 给出非空路由 (兜底语义在)。
    target, reason = _fallback_finding_route({"suggested_fix_layer": {"primary": "code"}})
    assert target == "fix"
    assert isinstance(reason, str)
    assert reason


# ─── T-LRF-01 path-B birth-gate bypass closure (f-lrf01-birthgate-pathb-nodetouched-bypass) ────────
#
# 出生闸只挂在 path-A commit gate。但一个 stub-rewrap NodeTouched(kind=FindingCreated,
# stub_original_payload={...}) 经 write_direct(path-B) 既不跑 commit gate(出生闸不执行)、又被
# orchestrator._unwrap_stub_rewrap 当 effective FindingCreated 路由 → 出生闸被旁路 + L2/L0 解包不对称。
# 修: EventLog 在【共用写边界】(_validate_payload_typed, path-A append_transaction_batch + path-B
# write_direct 都过它) fail-closed 拒该形态, 让"出生校验挂在创建入口绕不过"字面成立。


def _smuggled_finding_nodetouched_intent(*, finding_kind: str | None = None) -> EventIntent:
    """走 path-B 旁路出生闸的形态: NodeTouched(kind=FindingCreated, stub_original_payload={...})。"""
    finding_id = f"f-smuggle-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=f"nt-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "finding",
            "target_entity_id": finding_id,
            "touch_type": "write",
            "kind": "FindingCreated",
            "stub_original_payload": {
                "kind": "FindingCreated",
                "finding_id": finding_id,
                "finding_kind": finding_kind,
                "severity": "major",
                "description": "smuggled finding",
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-smuggle"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def _legit_goal_started_nodetouched_intent() -> EventIntent:
    """合法 stub-rewrap NodeTouched (kind 非 birth-gated) — 必须仍可走 path-B (无误拦)。"""
    gsid = f"sess-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=f"nt-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": gsid,
            "touch_type": "write",
            "kind": "GoalSessionStarted",
            "stub_original_payload": {"kind": "GoalSessionStarted", "goal_session_id": gsid},
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-legit"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=gsid, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_write_direct_rejects_smuggled_finding_nodetouched(tmp_path: Path) -> None:
    """★ 核心: write_direct(path-B) 对 NodeTouched(kind=FindingCreated, stub) fail-closed 拒, 不落账。"""
    log = EventLog(tmp_path / "events.log")
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log.write_direct(_smuggled_finding_nodetouched_intent(finding_kind="anomaly"))
    # 拒 = 不落账 (出生拦截的实质: 该形态绝不进 canonical 事件流)。
    assert all(r.event_type is not EventType.NODE_TOUCHED for r in log.all_records())


def test_write_direct_rejects_smuggled_finding_even_when_unrouted(tmp_path: Path) -> None:
    """旁路出生闸的 unroutable 形态(finding_kind=None)同样被写边界拒 (出生闸覆盖可路由+不可路由)。"""
    log = EventLog(tmp_path / "events.log")
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log.write_direct(_smuggled_finding_nodetouched_intent(finding_kind=None))


def test_validate_payload_typed_rejects_smuggle_covers_both_write_paths(tmp_path: Path) -> None:
    """両条写路都过 _validate_payload_typed → 在它上拦 = path-A(append_transaction_batch) 同样被拦。"""
    log = EventLog(tmp_path / "events.log")
    intent = _smuggled_finding_nodetouched_intent(finding_kind="anomaly")
    # 共用写边界 (path-A 第 398 行 / path-B 第 430 行都调它) 拒该形态。
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log._validate_payload_typed(intent.event_type, intent.payload)


def test_legit_stub_nodetouched_still_writes(tmp_path: Path) -> None:
    """无误拦回归守卫: 合法 stub-rewrap (GoalSessionStarted 等非 birth-gated kind) 仍可 path-B 落账。"""
    log = EventLog(tmp_path / "events.log")
    rec = log.write_direct(_legit_goal_started_nodetouched_intent())
    assert rec.event_type is EventType.NODE_TOUCHED
    assert any(r.event_id == rec.event_id for r in log.all_records())


def test_nonstub_nodetouched_with_finding_kind_not_rejected() -> None:
    """精确镜像 _unwrap_stub_rewrap: 无 stub_original_payload 的 NodeTouched(kind=FindingCreated) 不是
    可被路由的旁路形态 (orchestrator 也不会解包路由它) → 写边界不误拦。"""
    # 直接验静态判据 (不构造完整 intent): kind=FindingCreated 但缺 stub_original_payload → 放行。
    EventLog._reject_birthgate_smuggle(
        EventType.NODE_TOUCHED, {"kind": "FindingCreated"},
    )  # 不 raise = 通过


def test_reject_birthgate_smuggle_skips_non_nodetouched() -> None:
    """非 NodeTouched event_type → 写边界 smuggle 拦截跳过 (本拦截只管 NodeTouched 信封)。"""
    EventLog._reject_birthgate_smuggle(
        EventType.FINDING_CREATED, {"kind": "FindingCreated", "stub_original_payload": {}},
    )  # 不 raise = 通过 (native FindingCreated 走 path-A 出生闸, 不是本拦截对象)


# ─── f-glob-brief-birthgate-nodetouched-smuggle-bypass ────────────────────────────────────
#
# 同款走私在 InterviewBriefPublished 上的未覆盖实例 (birth-time-validation@v1 覆盖面随实例增长):
# brief 拥有 commit-gate 出生门 (owner-confirm publish gate, T-RMD-S2-M11 provenance 复验)。一份
# 伪造 brief 经 write_direct(path-B) 写成 NodeTouched(kind=InterviewBriefPublished,
# stub_original_payload={owner_brief_confirmed=passed}) 既不跑 commit gate (owner 确认从未发生)、
# 又被 cli._find_latest_brief_payload / _find_brief_payload_by_id unwrap 选为 THE brief → consensus
# owner 确认门被 out-of-band 翻墙。修: 把 INTERVIEW_BRIEF_PUBLISHED 纳入 _BIRTHGATE_NONSMUGGLABLE_KINDS,
# 共用写边界对该形态 fail-closed 拒 (镜像 FindingCreated)。


def _smuggled_brief_nodetouched_intent() -> EventIntent:
    """走 path-B 旁路 owner-confirm 出生门的形态: NodeTouched(kind=InterviewBriefPublished, stub)。"""
    brief_id = f"brief-smuggle-{uuid.uuid4().hex[:8]}"
    return EventIntent(
        local_intent_id=f"nt-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "brief",
            "target_entity_id": brief_id,
            "touch_type": "write",
            "kind": "InterviewBriefPublished",
            "stub_original_payload": {
                "kind": "InterviewBriefPublished",
                "brief_id": brief_id,
                "session_id": "sess-forged",
                "owner_facing_summary": "smuggled brief",
                "owner_facing_summary_hash": "h",
                "stop_condition_passed": {
                    "blocking_checks": [
                        {"check_id": "interview.owner_brief_confirmed", "status": "passed"},
                    ],
                },
            },
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-smuggle"),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=brief_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


def test_write_direct_rejects_smuggled_brief_nodetouched(tmp_path: Path) -> None:
    """★ 核心: write_direct(path-B) 对 NodeTouched(kind=InterviewBriefPublished, stub) fail-closed 拒。"""
    log = EventLog(tmp_path / "events.log")
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log.write_direct(_smuggled_brief_nodetouched_intent())
    # 拒 = 不落账 (非落账由 test_smuggled_brief_not_selectable_by_finder_live_fire 经 finder 断言)。


def test_smuggled_brief_covers_both_write_paths(tmp_path: Path) -> None:
    """两条写路都过 _validate_payload_typed → path-A(append_transaction_batch) 同样被拦。"""
    log = EventLog(tmp_path / "events.log")
    intent = _smuggled_brief_nodetouched_intent()
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log._validate_payload_typed(intent.event_type, intent.payload)


def test_smuggled_brief_not_selectable_by_finder_live_fire(tmp_path: Path) -> None:
    """live-fire (镜像 T-LRF-01): 真跑 write_direct 一份 brief stub 被拒 → 账本无该 brief →
    consensus_start 的 brief-finder (_find_latest_brief_payload) 选不出任何伪造 brief。"""
    from towow.cli.main import _find_latest_brief_payload

    log = EventLog(tmp_path / "events.log")
    with pytest.raises(ValueError, match="rejected at the write boundary"):
        log.write_direct(_smuggled_brief_nodetouched_intent())
    # 写边界拒 → 账本里没有任何 InterviewBriefPublished (直/stub) → finder 返 None。
    assert _find_latest_brief_payload(tmp_path) is None


def test_nonstub_nodetouched_with_brief_kind_not_rejected() -> None:
    """精确镜像 unwrap: 无 stub_original_payload 的 NodeTouched(kind=InterviewBriefPublished) 不是
    可被 unwrap-选中 的旁路形态 → 写边界不误拦。"""
    EventLog._reject_birthgate_smuggle(
        EventType.NODE_TOUCHED, {"kind": "InterviewBriefPublished"},
    )  # 不 raise = 通过
