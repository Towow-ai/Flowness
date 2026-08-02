"""T-LND-02 (review-unit@v1): FindingCreated 携带 review_unit_id — 一次 review 产生的全部 finding
共享的 verdict 折叠分组边界 (= review session_id)。

本测试钉住 schema 侧契约:
  1. FindingCreatedPayload 接受 review_unit_id, round-trip 不丢 (RED: 字段未加前 extra=forbid 拒)
  2. 向后兼容: 省略 → None (系统/maintenance/execution 侧 finding 不属任何 review-unit)
  3. review 语境 (source.type=model_review) 带 review_unit_id 仍过写边界 model_validator

CLI 盖 sid 的接线见 review_finding_create (main.py); reducer 兜底从 provenance.session_id 派生
见 test_lnd01 (T-LND-01 reducer 侧)。
"""

from __future__ import annotations

from towow.schemas.enums import EventType, FindingDetectionMethod, FindingSeverity
from towow.schemas.payloads.finding import FindingCreatedPayload
from towow.schemas.payloads.registry import validate_event_payload


def test_finding_accepts_review_unit_id_roundtrip() -> None:
    p = FindingCreatedPayload(
        finding_id="f-1",
        severity=FindingSeverity.MINOR,
        risk_surface="x",
        description="d",
        detection_method=FindingDetectionMethod.MANUAL_REVIEW,
        review_unit_id="sess-review-abc",
    )
    assert p.review_unit_id == "sess-review-abc"


def test_finding_review_unit_id_backward_compat_none() -> None:
    p = FindingCreatedPayload(
        finding_id="f-2",
        severity=FindingSeverity.MINOR,
        risk_surface="x",
        description="d",
        detection_method=FindingDetectionMethod.MANUAL_REVIEW,
    )
    assert p.review_unit_id is None


def test_review_finding_with_review_unit_id_passes_real_write_boundary() -> None:
    """完整 model_review finding 带 review_unit_id → 过 CLI 实际走的写边界 validate_event_payload."""
    payload = {
        "finding_id": "f-3",
        "severity": "major",
        "risk_surface": "state-fidelity",
        "lifecycle_state": "created",
        "description": "verdict 折叠分组键缺失",
        "detection_method": "manual_review",
        "review_unit_id": "sess-review-xyz",  # ← T-LND-02 新字段, 随完整 review finding 过门
        "voi_rationale": "不变量 INV-B1-1 (projection.py:1166): finding 缺 review_unit_id 则对 verdict 不可见即蒸发",
        "target": {
            "artifact": "src/towow/l0/projection/projection.py",
            "location": "projection.py:1166",
        },
        "falsification_evidence": {
            "attempt": "构造 review-unit 无分组键 → verdict 折叠拿不到 finding",
            "result": "confirmed",
        },
        "review_dimension": "method-execution-path",
        "is_preexisting": False,
        "closure_contract": {
            "closure_criteria": [
                {
                    "condition": "finding_lifecycle 条目携带 review_unit_id",
                    "verification_method": "test",
                    "expected_result": "test_lnd01 passes",
                    "test_selector": "tests/unit/l0/test_lnd01_finding_lifecycle_review_unit.py",
                },
            ],
        },
        "source": {"type": "model_review", "agent_id": "m15.review"},
    }
    # validate_event_payload raise = 写边界拒; 不抛 = 过门 (review_unit_id 随完整 review finding
    # 过 CLI 实际走的写边界, 不破坏 §3.2 enforce)。无显式断言即"不抛"为通过条件。
    validate_event_payload(EventType.FINDING_CREATED, payload)
