"""live-target-execution-evidence@v1 · LEDGER_EVENT machine_check 形态校验 + observable 约束。

钉住: 发布门形态校验拦"发布过、复算永久 fail-closed"的埋雷 —— LEDGER_EVENT 缺 event_kind/
min_occurrences/scope_binding → defect。goal observable 的 evidence 必须 ledger_event + scope=this_goal
(model_validator 强制, 防 advisor #1 的 false-accept)。
"""

from __future__ import annotations

import pytest

from towow.schemas.enums import (
    GATE_RECOMPUTABLE_VERIFICATION_METHODS,
    ClosureVerificationMethod,
    LedgerEventScope,
)
from towow.schemas.payloads.finding import (
    ClosureCriterion,
    closure_criterion_contract_defects,
)
from towow.schemas.payloads.interview_brief import LiveTargetObservable


def test_ledger_event_in_gate_recomputable() -> None:
    assert ClosureVerificationMethod.LEDGER_EVENT in GATE_RECOMPUTABLE_VERIFICATION_METHODS


def test_ledger_event_missing_event_kind_defect() -> None:
    mc = ClosureCriterion(
        condition="账本有 K 事件",
        verification_method=ClosureVerificationMethod.LEDGER_EVENT,
        expected_result="ok",
        min_occurrences=1,
        scope_binding=LedgerEventScope.THIS_GOAL,
    )  # 缺 event_kind
    defects = closure_criterion_contract_defects(mc)
    assert any("event_kind" in d for d in defects)


def test_ledger_event_missing_min_occurrences_defect() -> None:
    mc = ClosureCriterion(
        condition="账本有 K 事件",
        verification_method=ClosureVerificationMethod.LEDGER_EVENT,
        expected_result="ok",
        event_kind="StrandedBatchManifestFrozen",
        scope_binding=LedgerEventScope.THIS_GOAL,
    )  # 缺 min_occurrences
    defects = closure_criterion_contract_defects(mc)
    assert any("min_occurrences" in d for d in defects)


def test_ledger_event_missing_scope_binding_defect() -> None:
    mc = ClosureCriterion(
        condition="账本有 K 事件",
        verification_method=ClosureVerificationMethod.LEDGER_EVENT,
        expected_result="ok",
        event_kind="StrandedBatchManifestFrozen",
        min_occurrences=1,
    )  # 缺 scope_binding
    defects = closure_criterion_contract_defects(mc)
    assert any("scope_binding" in d for d in defects)


def test_ledger_event_complete_no_defect() -> None:
    mc = ClosureCriterion(
        condition="账本有 K 事件",
        verification_method=ClosureVerificationMethod.LEDGER_EVENT,
        expected_result="ok",
        event_kind="StrandedBatchManifestFrozen",
        min_occurrences=1,
        scope_binding=LedgerEventScope.THIS_GOAL,
    )
    assert closure_criterion_contract_defects(mc) == []


def test_observable_rejects_non_ledger_evidence() -> None:
    """goal observable 的 evidence 非 ledger_event → 拒 (goal 层证据必须门读账本)。"""
    with pytest.raises(ValueError, match="ledger_event"):
        LiveTargetObservable(
            observable_id="o1",
            description="x",
            evidence=ClosureCriterion(
                condition="c",
                verification_method=ClosureVerificationMethod.GREP,
                expected_result="ok",
                verification_pattern="foo",
                expected_occurrences=1,
            ),
        )


def test_observable_rejects_global_scope() -> None:
    """goal observable 的 evidence scope=global → 拒 (advisor #1: 别 goal 旧事件会 false-accept)。"""
    with pytest.raises(ValueError, match="this_goal"):
        LiveTargetObservable(
            observable_id="o1",
            description="x",
            evidence=ClosureCriterion(
                condition="c",
                verification_method=ClosureVerificationMethod.LEDGER_EVENT,
                expected_result="ok",
                event_kind="K",
                min_occurrences=1,
                scope_binding=LedgerEventScope.GLOBAL,
            ),
        )


def test_observable_accepts_this_goal_ledger_event() -> None:
    obs = LiveTargetObservable(
        observable_id="o1",
        description="清单被冻结并执行",
        evidence=ClosureCriterion(
            condition="账本 ≥1 条 StrandedBatchManifestFrozen",
            verification_method=ClosureVerificationMethod.LEDGER_EVENT,
            expected_result="frozen",
            event_kind="StrandedBatchManifestFrozen",
            min_occurrences=1,
            scope_binding=LedgerEventScope.THIS_GOAL,
        ),
    )
    assert obs.evidence.event_kind == "StrandedBatchManifestFrozen"
