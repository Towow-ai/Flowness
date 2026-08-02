"""RUN-038 L0增强 M-0.6 Cap2: ObligationChecked check_method + check_outcome enum + evidence_refs.

spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md §4.2.3
  payload:
    check_method:  enum [mechanical_pattern | audit_verdict | self_declared_only]
    check_outcome: enum [maintained | not_applicable]    # violated 走 ObligationViolated
    evidence_refs: [string]?   # 若有 audit_verdict, 引用 AuditVerdictReceived

要证真: payload 用 enum (非裸 str) + evidence_refs 字段; 非法 enum 值被 schema 拒;
check_outcome 不接受 violated (那是 ObligationViolated 的领域)。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from towow.schemas.enums import ObligationCheckMethod, ObligationCheckOutcome
from towow.schemas.payloads.obligation import ObligationCheckedPayload


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "obligation_id": "obl-x",
        "checked_in_envelope_event_id": "evt-env-1",
        "check_method": ObligationCheckMethod.SELF_DECLARED_ONLY,
        "check_outcome": ObligationCheckOutcome.MAINTAINED,
    }
    base.update(overrides)
    return base


def test_check_method_and_outcome_are_enums() -> None:
    p = ObligationCheckedPayload(**_valid_kwargs())  # type: ignore[arg-type]
    assert p.check_method is ObligationCheckMethod.SELF_DECLARED_ONLY
    assert p.check_outcome is ObligationCheckOutcome.MAINTAINED
    # evidence_refs defaults to None when no audit
    assert p.evidence_refs is None


def test_evidence_refs_carried_for_audit_verdict() -> None:
    p = ObligationCheckedPayload(
        **_valid_kwargs(  # type: ignore[arg-type]
            check_method=ObligationCheckMethod.AUDIT_VERDICT,
            evidence_refs=["evt-audit-verdict-7"],
        ),
    )
    assert p.check_method is ObligationCheckMethod.AUDIT_VERDICT
    assert p.evidence_refs == ["evt-audit-verdict-7"]


def test_check_method_rejects_bare_invalid_string() -> None:
    with pytest.raises(ValidationError):
        ObligationCheckedPayload(**_valid_kwargs(check_method="totally_made_up"))  # type: ignore[arg-type]


def test_check_outcome_rejects_violated() -> None:
    """violated 不是 check_outcome 的合法值 (走 ObligationViolated, §4.2.4)。"""
    with pytest.raises(ValidationError):
        ObligationCheckedPayload(**_valid_kwargs(check_outcome="violated"))  # type: ignore[arg-type]


def test_all_three_check_methods_valid() -> None:
    for m in ObligationCheckMethod:
        p = ObligationCheckedPayload(**_valid_kwargs(check_method=m))  # type: ignore[arg-type]
        assert p.check_method is m


def test_both_check_outcomes_valid() -> None:
    for o in ObligationCheckOutcome:
        p = ObligationCheckedPayload(**_valid_kwargs(check_outcome=o))  # type: ignore[arg-type]
        assert p.check_outcome is o
