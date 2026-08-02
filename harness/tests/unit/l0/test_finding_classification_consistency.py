"""finding-classification-consistency-birth-gate@v1 (W1-R2, LEDGER Conflict 32).

R2: finding_kind != premise_false 但 description/closure_contract 命中退役信号正则 → 疑似误分类。
    机械模式 (--reason retired 等代码化字面 token) + finding_kind=concept_issue + enforce=True →
    硬拒；其余命中 (自然语言模式 / 非 concept_issue kind / enforce=False) → 只告警。
R3: finding_kind=premise_false 但 subjects 无 entity_type=task 锚定 → enforce=True 硬拒, 否则告警。

mutation 判别力: 删掉 R2/R3 检查 (让 check_finding_classification_consistency 对任何 batch 恒
passed=True 且 notices=[]) → 本文件对应硬拒/告警断言全红 (见 test_mutation_*)。

真实案例锚定 (Conflict 32, finding f-prw-testdebug-premise-false,
evt-ea2a9989a3674356b8b8df2ccabb3c0b 原文): closure_contract.closure_criteria[0].condition =
"T-PRW-TEST-DEBUG 经 plan task-close --reason retired 关闭", description 含"应退休"——两条正则
(机械/自然语言) 都必须命中这份真实文本, 否则整个机制对它唯一已知的真阳性哑火。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING

from towow.l0.commit_gate import CommitGate
from towow.l0.commit_gate.finding_classification_consistency import (
    _RETIREMENT_SCAN_TEXT_LIMIT,
    MECHANICAL_RETIREMENT_PATTERN,
    NATURAL_LANGUAGE_RETIREMENT_PATTERN,
    check_finding_classification_consistency,
    enforced_flags,
    truncate_for_retirement_scan,
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


# ─── helpers ────────────────────────────────────────────────────────────────────────


def _finding_intent(
    *,
    finding_id: str = "f-test",
    finding_kind: str | None = None,
    description: str = "test finding",
    closure_contract: dict[str, object] | None = None,
    task_subject_id: str | None = None,
) -> EventIntent:
    payload: dict[str, object] = {
        "finding_id": finding_id,
        "severity": "major",
        "risk_surface": "test surface",
        "lifecycle_state": "created",
        "description": description,
        "detection_method": "automated_rule",
        "finding_kind": finding_kind,
    }
    if closure_contract is not None:
        payload["closure_contract"] = closure_contract
    subjects = [Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY)]
    if task_subject_id is not None:
        subjects.append(
            Subject(entity_type=SubjectEntityType.TASK, entity_id=task_subject_id, role=SubjectRole.AFFECTED),
        )
    return EventIntent(
        local_intent_id=f"fc-{uuid.uuid4().hex[:8]}",
        event_type=EventType.FINDING_CREATED,
        event_category=EventCategory.FINDING,
        payload=payload,
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _event_log_with_config(tmp_path: Path, config: dict[str, object] | None) -> EventLog:
    """建一个真 EventLog (供 enforced_flags 从 event_log 推导 towow_dir); 可选写 maintenance/config.json。"""
    towow_dir = tmp_path / ".towow"
    (towow_dir / "graph").mkdir(parents=True)
    if config is not None:
        maintenance_dir = towow_dir / "maintenance"
        maintenance_dir.mkdir(parents=True)
        (maintenance_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return EventLog(towow_dir / "events.log")


def _finding_envelope_intent(finding_id: str) -> EventIntent:
    """承载 FindingCreated 的 envelope — 自检带 review.review_contract_present (FINDING_CREATED 的
    completeness 必需), 让正向路径过总闸 (镜像 test_finding_birth_gate.py 同名 helper)。"""
    env_id = f"env-{uuid.uuid4().hex[:12]}"
    return EventIntent(
        local_intent_id=env_id,
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "envelope_id": env_id,
            "capsule_compiled_event_id": "evt-stub-capsule-fcc-test",
            "self_check": {
                "passed": True,
                "checks_run": [],
                "blocking_checks": [
                    {
                        "check_id": "review.review_contract_present",
                        "status": "passed",
                        "evidence": {"closure": "finding-classification-consistency-test"},
                    },
                ],
            },
            "active_obligations_declared": [],
        },
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test-fcc"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.FINDING, entity_id=finding_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


_CONFLICT_32_CLOSURE_CONTRACT = {
    "closure_criteria": [{
        "condition": "T-PRW-TEST-DEBUG 经 plan task-close --reason retired 关闭，从 plan 的 "
        "ready-frontier/coverage 检查范围中移除",
        "verification_method": "grep",
        "expected_result": "TaskNodeClosed 事件存在且 reason=retired，superseded_by 指向本 finding",
    }],
}
_CONFLICT_32_DESCRIPTION = (
    "T-PRW-TEST-DEBUG 是排查 task-create CLI 编码问题时误建的调试测试 task，"
    "与 T-PRW-work-md 撞同一 target_artifact 且无依赖边隔离，premise-false，应退休不参与 plan 执行"
)


# ─── 真实案例锚定: 正则必须命中 Conflict 32 原文 ────────────────────────────────────────


def test_mechanical_pattern_matches_conflict_32_real_closure_contract() -> None:
    """机械模式必须命中真实案例的 closure_contract condition ('--reason retired')。"""
    text = _CONFLICT_32_CLOSURE_CONTRACT["closure_criteria"][0]["condition"]
    assert MECHANICAL_RETIREMENT_PATTERN.search(text) is not None


def test_mechanical_pattern_matches_conflict_32_expected_result() -> None:
    """机械模式同样命中 expected_result 里的 'reason=retired'。"""
    text = _CONFLICT_32_CLOSURE_CONTRACT["closure_criteria"][0]["expected_result"]
    assert MECHANICAL_RETIREMENT_PATTERN.search(text) is not None


def test_natural_language_pattern_matches_conflict_32_real_description() -> None:
    """自然语言模式必须命中真实案例的 description ('应退休')。"""
    assert NATURAL_LANGUAGE_RETIREMENT_PATTERN.search(_CONFLICT_32_DESCRIPTION) is not None


# ─── 设计稿测试计划 1-5 (总控裁决收窄版) ────────────────────────────────────────────────


def test_concept_issue_with_retirement_language_in_closure_contract_rejected() -> None:
    """1. 复现 Conflict 32: concept_issue + 机械模式命中 + enforce=True → 硬拒。"""
    intent = _finding_intent(
        finding_id="f-prw-testdebug-premise-false",
        finding_kind="concept_issue",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
    )
    res = _check_with_enforce([intent], r2=True, r3=False)
    assert res.passed is False
    assert res.failure_reason == "finding_classification_retirement_language_misrouted"
    assert res.failure_evidence.get("rule") == "R2"


def test_premise_false_with_matching_closure_contract_admitted() -> None:
    """2. 正对照: finding_kind=premise_false (已经标对了) + 同款退役措辞 + 有 task 锚定 → 不拒, 无告警。"""
    intent = _finding_intent(
        finding_id="f-prw-testdebug-premise-false",
        finding_kind="premise_false",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
        task_subject_id="T-PRW-TEST-DEBUG",
    )
    res = _check_with_enforce([intent], r2=True, r3=True)
    assert res.passed is True
    assert res.notices == []


def test_concept_issue_with_suggested_fix_layer_primary_concept_admitted() -> None:
    """3. 防误伤: concept_issue, 描述里无任何退役措辞 → 不拒, 无告警。"""
    intent = _finding_intent(
        finding_id="f-normal-concept-issue",
        finding_kind="concept_issue",
        description="handoff skill 的问人漂移地图概念层文档内容需要补采访环节倒转例外",
    )
    res = _check_with_enforce([intent], r2=True, r3=True)
    assert res.passed is True
    assert res.notices == []


def test_premise_false_without_task_anchor_rejected() -> None:
    """4. R3: finding_kind=premise_false 但 subjects 无 task 锚定 + enforce=True → 硬拒。"""
    intent = _finding_intent(
        finding_id="f-premise-false-no-anchor",
        finding_kind="premise_false",
        description="某 task 前提为假应退役",
    )
    res = _check_with_enforce([intent], r2=False, r3=True)
    assert res.passed is False
    assert res.failure_reason == "finding_classification_premise_false_missing_task_anchor"
    assert res.failure_evidence.get("rule") == "R3"


def test_adjacent_code_issue_with_retirement_language_not_flagged() -> None:
    """5. 防假阳性 (总控裁决: R2 第一版只对 concept_issue 硬拒): adjacent_code_issue 命中机械模式 +
    enforce=True → 不硬拒 (kind 不在收窄范围内), 只留 warn-only notice。"""
    intent = _finding_intent(
        finding_id="f-adjacent-code-issue",
        finding_kind="adjacent_code_issue",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
    )
    res = _check_with_enforce([intent], r2=True, r3=False)
    assert res.passed is True
    assert len(res.notices) == 1
    assert "R2" in res.notices[0]


# ─── mutation 判别力: 删掉 R2/R3 → 上面几条硬拒断言全红 ─────────────────────────────────


def test_mutation_r2_disabled_stub_would_fail_hard_reject_assertions() -> None:
    """哨兵: 若 R2 被删 (对任意 batch 恒 passed=True), test_concept_issue_with_retirement_language_
    in_closure_contract_rejected 的 `res.passed is False` 断言会红——证明该测试确实钉住 R2 存在。"""
    intent = _finding_intent(
        finding_id="f-prw-testdebug-premise-false",
        finding_kind="concept_issue",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
    )
    res = _check_with_enforce([intent], r2=True, r3=False)
    assert res.passed is False, "R2 存在时本条必须硬拒; 若此断言意外为 True 说明 R2 被静默禁用"


def test_mutation_r3_disabled_stub_would_fail_hard_reject_assertions() -> None:
    """哨兵: 若 R3 被删, test_premise_false_without_task_anchor_rejected 的硬拒断言会红。"""
    intent = _finding_intent(
        finding_id="f-premise-false-no-anchor",
        finding_kind="premise_false",
        description="某 task 前提为假应退役",
    )
    res = _check_with_enforce([intent], r2=False, r3=True)
    assert res.passed is False, "R3 存在时本条必须硬拒; 若此断言意外为 True 说明 R3 被静默禁用"


# ─── warn-only 默认行为 (总控裁决: 整个新检查先 warn-only, 两条规则各自独立开关) ──────────


def test_default_enforce_false_never_hard_rejects_even_conflict_32_shape() -> None:
    """默认 (event_log=None, 无 enforce 覆盖) → 即使喂 Conflict 32 原文形态, 也只告警不拒批。"""
    intent = _finding_intent(
        finding_id="f-prw-testdebug-premise-false",
        finding_kind="concept_issue",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
    )
    res = check_finding_classification_consistency([intent])
    assert res.passed is True
    assert len(res.notices) == 1
    assert "[warn-only][R2]" in res.notices[0]


def test_enforced_flags_default_false_when_event_log_none() -> None:
    assert enforced_flags(None) == (False, False)


def test_enforced_flags_false_when_config_missing(tmp_path: Path) -> None:
    log = _event_log_with_config(tmp_path, config=None)
    assert enforced_flags(log) == (False, False)


def test_enforced_flags_false_when_config_malformed(tmp_path: Path) -> None:
    towow_dir = tmp_path / ".towow"
    (towow_dir / "graph").mkdir(parents=True)
    maintenance_dir = towow_dir / "maintenance"
    maintenance_dir.mkdir(parents=True)
    (maintenance_dir / "config.json").write_text("{not valid json", encoding="utf-8")
    log = EventLog(towow_dir / "events.log")
    assert enforced_flags(log) == (False, False)


def test_enforced_flags_independent_keys(tmp_path: Path) -> None:
    """R2/R3 各自独立开关: 只解冻 R2 不影响 R3 (反之亦然)。"""
    log_r2_only = _event_log_with_config(
        tmp_path / "r2only", {"finding_classification_r2_enforce": True},
    )
    assert enforced_flags(log_r2_only) == (True, False)

    log_r3_only = _event_log_with_config(
        tmp_path / "r3only", {"finding_classification_r3_enforce": True},
    )
    assert enforced_flags(log_r3_only) == (False, True)


def test_config_enforce_true_actually_hard_rejects_via_event_log(tmp_path: Path) -> None:
    """端到端: event_log 线程真配置 (r2_enforce=true) → check 函数真读盘并硬拒, 不是只有测试内部
    enforce 覆盖参数才能拒。"""
    log = _event_log_with_config(tmp_path, {"finding_classification_r2_enforce": True})
    intent = _finding_intent(
        finding_id="f-prw-testdebug-premise-false",
        finding_kind="concept_issue",
        description=_CONFLICT_32_DESCRIPTION,
        closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
    )
    res = check_finding_classification_consistency([intent], log)
    assert res.passed is False
    assert res.failure_reason == "finding_classification_retirement_language_misrouted"


# ─── W1-R2 返修轮 (VERIFY-REPORT §2.d)：notices 端到端 (真走 CommitGate.attempt_commit) ────
#
# 上面 test_default_enforce_false_never_hard_rejects_even_conflict_32_shape 只调
# check_finding_classification_consistency() 本身，没有一条走完整 attempt_commit()——独立验证者
# 指出这是覆盖空隙: "notices 真的从 gate 出口带出来" 跟 "enforce 开关真读盘生效硬拒" 是两回事，
# 都值得测。本测试补上前者: 自建真实 EventLog + CommitGate，提交一条 Conflict-32 形态的 finding
# (enforce=False, 默认), 断言 warn-only 提示真的从 sentinel 的 informational_notices 带出来。


def test_warn_only_notice_reaches_commit_accepted_informational_notices(tmp_path: Path) -> None:
    """端到端 (真走 attempt_commit): R2 warn-only 命中时, CommitAccepted.informational_notices
    真带出 [warn-only][R2] 提示——不只是 check_finding_classification_consistency() 单测层面。"""
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)
    finding_id = "f-prw-testdebug-premise-false"
    records = gate.attempt_commit(
        _finding_envelope_intent(finding_id),
        domain_intents=[
            _finding_intent(
                finding_id=finding_id,
                finding_kind="concept_issue",
                description=_CONFLICT_32_DESCRIPTION,
                closure_contract=_CONFLICT_32_CLOSURE_CONTRACT,
            ),
        ],
    )
    sentinel = records[-1]
    assert sentinel.event_type is EventType.COMMIT_ACCEPTED, (
        f"warn-only (enforce=False 默认) 不应拒批, 实得 sentinel={sentinel.event_type}, "
        f"payload={sentinel.payload}"
    )
    notices = sentinel.payload.get("informational_notices") or []
    assert any("[warn-only][R2]" in n for n in notices), (
        f"CommitAccepted.informational_notices 应带出 R2 warn-only 提示, 实得 notices={notices}"
    )


# ─── 防御性: 畸形 finding 不能让整条检查抛异常 ──────────────────────────────────────────


def test_malformed_closure_contract_does_not_raise() -> None:
    """closure_contract 不是预期形状 (list 而非 dict) → 跳过该条, 不 raise, 不误判整批。"""
    intent = _finding_intent(finding_kind="concept_issue")
    intent.payload["closure_contract"] = ["not", "a", "dict"]
    res = _check_with_enforce([intent], r2=True, r3=True)
    assert res.passed is True


def test_non_finding_intent_skipped() -> None:
    intent = EventIntent(
        local_intent_id="nt-1",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.STATE_TRANSITION,
        payload={"kind": "x"},
        provenance_hint=ProvenanceHint(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id="t", role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )
    assert check_finding_classification_consistency([intent]).passed is True


def test_empty_batch_passes() -> None:
    assert check_finding_classification_consistency([]).passed is True


# ─── W1-R2 返修轮 (VERIFY-REPORT §2.f)：性能回归护栏 ────────────────────────────────────
#
# 修复前 `task[-_]close.*retired` 的贪婪 `.*` 在"文本里有多个 task-close 但没有 retired"时退化为
# O(n²) 回溯 (VERIFY-REPORT 实测: ~100,040 字符耗时 3,285.11ms)。双保险: (1) 正则本身量词有界化
# (`.{0,120}`)；(2) 喂给正则前的检索文本长度截断 (truncate_for_retirement_scan)。下面三条测试
# 分别钉住这两层, 用非 flaky 的实现 (宽松但有意义的耗时上界, 不用纯计时断言判"多快算通过", 而是
# 判"离修复前的退化耗时够远")。

_ADVERSARIAL_TEXT_LEN = 100_000
# 宽松但有意义的上界: 修复前同等长度单次 search 实测 ~3.3s, 这里给 20 倍余量防机器慢/CI 抖动 flaky,
# 仍然远低于退化耗时, 足以证明"没有回归成 O(n²)"。
_PERF_REGRESSION_BOUND_S = 1.0


def _adversarial_no_retired_text(length: int) -> str:
    """重复 'task-close ' 但不含 'retired' 的对抗文本——是修复前贪婪 `.*` 退化的触发形态。"""
    unit = "task-close "
    repeats = length // len(unit) + 1
    return (unit * repeats)[:length]


def test_truncate_for_retirement_scan_caps_length() -> None:
    """双保险第二层: 截断函数输出长度必须 <= 上限, 不论输入多长。"""
    text = _adversarial_no_retired_text(_ADVERSARIAL_TEXT_LEN)
    assert len(text) == _ADVERSARIAL_TEXT_LEN  # 前提: 对抗文本确实比截断上限大得多
    truncated = truncate_for_retirement_scan(text)
    assert len(truncated) <= _RETIREMENT_SCAN_TEXT_LIMIT


def test_mechanical_pattern_search_not_quadratic_on_adversarial_text() -> None:
    """双保险第一层: 即使不经截断, 有界量词本身也不能在 100KB 不匹配文本上退化成 O(n²)。

    直接对未截断的原始对抗文本跑正则 (绕开 truncate_for_retirement_scan), 只验证量词本身的
    有界性——修复前 (`.*`) 这个规模的输入会耗时 3 秒以上, 修复后 (`.{0,120}`) 应在毫秒级。
    """
    text = _adversarial_no_retired_text(_ADVERSARIAL_TEXT_LEN)
    start = time.perf_counter()
    matched = MECHANICAL_RETIREMENT_PATTERN.search(text)
    elapsed = time.perf_counter() - start
    assert matched is None
    assert elapsed < _PERF_REGRESSION_BOUND_S, (
        f"耗时 {elapsed:.3f}s 超过 {_PERF_REGRESSION_BOUND_S}s 上界, 疑似量词有界化回归"
    )


def test_check_finding_classification_consistency_fast_on_adversarial_description() -> None:
    """端到端: 100KB 对抗 description 走完整 check 函数 (经 _closure_contract_text 截断) 必须快。

    该检查在 enforce=False (生产默认) 时也无条件跑 (VERIFY-REPORT §2.f 第 2 点), 所以这里刻意
    不传 enforce 覆盖, 用默认路径 (event_log=None → 两条规则皆 warn-only) 验证"默认路径也快"。
    """
    intent = _finding_intent(
        finding_id="f-adversarial-perf",
        finding_kind="concept_issue",
        description=_adversarial_no_retired_text(_ADVERSARIAL_TEXT_LEN),
    )
    start = time.perf_counter()
    res = check_finding_classification_consistency([intent])
    elapsed = time.perf_counter() - start
    assert res.passed is True
    assert elapsed < _PERF_REGRESSION_BOUND_S, f"耗时 {elapsed:.3f}s 超过 {_PERF_REGRESSION_BOUND_S}s 上界"


# ─── enforce 覆盖测试用 helper (不经磁盘, 直接跑纯逻辑) ──────────────────────────────────


def _check_with_enforce(intents: list[EventIntent], *, r2: bool, r3: bool):
    """绕开 event_log/磁盘, 直接用 tmp_path-free 的合成 EventLog 拿到确定性 enforce 组合。

    实现: 用真实 tmp 目录写 maintenance/config.json 达成指定 (r2, r3) 组合——不 monkeypatch 内部
    函数, 走跟生产代码相同的 enforced_flags() 路径, 只是测试自己管临时目录生命周期。
    """
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        towow_dir = _Path(tmp) / ".towow"
        (towow_dir / "graph").mkdir(parents=True)
        maintenance_dir = towow_dir / "maintenance"
        maintenance_dir.mkdir(parents=True)
        config = {}
        if r2:
            config["finding_classification_r2_enforce"] = True
        if r3:
            config["finding_classification_r3_enforce"] = True
        (maintenance_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        log = EventLog(towow_dir / "events.log")
        return check_finding_classification_consistency(intents, log)
