"""ValidationScenarioRun payload schema (RUN-058 — M-3.4 §3.3 验证大盘留痕).

# spec source:
#   06-l3-engineering-shell/M-3.4-validation-matrix-detailed-design.md §3.3
#
# Why this event exists:
#   M-3.4 详设了 32 个验证场景 (§4-§10), 但 EPIC-10 此前只有静态注册表 (validation.py 132 行),
#   无 runner、无 ValidationScenarioRun 事件 (空跑道, RUN-058 baseline)。这是验证大盘从"spec"
#   到"真跑出真 outcome"的留痕事件: validation runner 真驱动一个场景的真实代码路径 → 收集 events
#   → 比对 expected_event_sequence (observed/missing/unexpected) → 裁 outcome → emit 本事件。
#
# Category: state_transition (§3.3 — validation_scenario entity transitions to having-been-run)。
# Path B 留痕 (validation runner 自观察, like DebtRegistered / DriftDetected): 验证结果是 runner
# 对系统跑出来的客观观察, 不是要被 commit gate 审计的域 patch。
# base_classification=immutable_truth (§3.3 "validation 留痕永久")。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import TargetEntityType, ValidationOutcome

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class InvariantResult(BaseModel):
    """§3.3 invariant_results[] — 一条被验证的不变量的结果。

    invariant_name 是场景 spec 反退路三栏"没成"列的 invariant_name; passed/evidence 是 runner
    真驱动后的客观观察; violation_observation_point 在 passed=false 时填具体违反观测点。
    """

    model_config = _STRICT

    invariant_name: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)
    violation_observation_point: str | None = None


class CorrectionSignal(BaseModel):
    """§3.3 correction_signal — outcome=failed_with_correction_signal 时, 失败暴露的设计假设。

    对应场景 spec 反退路三栏"没成的修正信号"列: exposed_design_assumption (失败暴露哪个设计假设错)
    + correction_direction (修正方向) + findings_produced (如果 runner 真产了 finding)。
    """

    model_config = _STRICT

    exposed_design_assumption: str = Field(min_length=1)
    correction_direction: str = Field(min_length=1)
    findings_produced: list[str] | None = None


class ObservabilityData(BaseModel):
    """§3.3 observability_data — expected_event_sequence 比对结果 (真填, 非占位)。

    expected_events_observed: 实际观测到的、属于 expected_event_sequence 的 event_id (真 id)。
    expected_events_missing: expected 里没观测到的 event_type (字符串)。
    unexpected_events: 观测到但不在 expected 里的 event_id。
    """

    model_config = _STRICT

    expected_events_observed: list[str] = Field(default_factory=list)
    expected_events_missing: list[str] | None = None
    unexpected_events: list[str] | None = None


class ValidationScenarioRunPayload(BaseModel):
    """ValidationScenarioRun payload — M-3.4 §3.3 一次验证场景执行的完整留痕。"""

    model_config = _STRICT

    target_entity_type: TargetEntityType = TargetEntityType.VALIDATION_SCENARIO
    scenario_id: str = Field(min_length=1)  # 例 "3.5" / "5.2"
    scenario_version: str = Field(min_length=1)  # M-3.4 spec 版本
    run_id: str = Field(min_length=1)
    run_started_at: str = Field(min_length=1)  # ISO-8601
    run_completed_at: str = Field(min_length=1)  # ISO-8601
    outcome: ValidationOutcome
    invariant_results: list[InvariantResult] = Field(default_factory=list)
    correction_signal: CorrectionSignal | None = None
    observability_data: ObservabilityData


__all__ = [
    "CorrectionSignal",
    "InvariantResult",
    "ObservabilityData",
    "ValidationScenarioRunPayload",
]
