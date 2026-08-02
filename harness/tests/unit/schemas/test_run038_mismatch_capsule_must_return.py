"""RUN-038 波3 — mismatch capsule MUST_RETURN 含完整结构 (T-L1-45, M-1.4 §3.5 / §6.5).

# spec source:
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §3.5 (advisor verdict 结构) / §6.5
#   src/towow/skills/advisor-consult/SKILL.md §"输出 Structured Result" (advisor fork 必返回结构)
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md §6.5 (mismatch scene)
#
# 病 (RUN-038 波3 前): T-L1-46 把 mismatch capsule MUST_RETURN 从已删 MismatchVerdict 改为只
#   "MismatchResolutionDecided 格式 ... 或 AdvisorVerdictDelivered" — 是事件**命名引用**, 不是
#   advisor fork 真正要返回的完整结构 (决策 + rationale + evidence scope binding 等)。
#   advisor-consult fork 读这个 mismatch capsule 做判断, MUST_RETURN 必须明确告诉它要返回什么。
#
# 修 (RUN-038 波3): MUST_RETURN 内容补全为 advisor verdict 完整结构 — 5 个 decision path +
#   rationale (必填) + evidence_scope_summary (v2.1 必填, evidence scope binding) + specific_steps
#   (custom_action 时) + 两条 mismatch 处理路径 (executor 自决 MismatchResolutionDecided /
#   经 advisor AdvisorVerdictDelivered)。非占位命名。
"""

from __future__ import annotations

from towow.schemas.capsule_templates import MISMATCH_TEMPLATE, get_scene_template
from towow.schemas.enums import CapsuleSection, SceneType


def _must_return() -> str:
    return MISMATCH_TEMPLATE.capsule_section_customization[CapsuleSection.MUST_RETURN]


def test_mismatch_template_lookup() -> None:
    assert get_scene_template(SceneType.MISMATCH) is MISMATCH_TEMPLATE


def test_must_return_present() -> None:
    assert CapsuleSection.MUST_RETURN in MISMATCH_TEMPLATE.capsule_section_customization


def test_must_return_no_longer_references_deleted_mismatch_verdict() -> None:
    """T-L1-46: 不再引已删 MismatchVerdict event (方向相反残留)."""
    assert "MismatchVerdict" not in _must_return()


def test_must_return_names_both_resolution_paths() -> None:
    """两条 mismatch 处理路径 (executor 自决 / 经 advisor) 都在 MUST_RETURN."""
    mr = _must_return()
    assert "MismatchResolutionDecided" in mr
    assert "AdvisorVerdictDelivered" in mr


def test_must_return_carries_all_five_advisor_decisions() -> None:
    """完整结构 (非命名占位): advisor verdict 5 个 decision path 都在 MUST_RETURN."""
    mr = _must_return()
    for decision in (
        "use_path_self_heal",
        "use_path_consult_again",
        "use_path_trigger_replan",
        "use_path_abort_task",
        "custom_action",
    ):
        assert decision in mr, f"advisor decision {decision} missing from mismatch MUST_RETURN"


def test_must_return_requires_rationale_and_evidence_scope() -> None:
    """rationale 必填 + evidence_scope_summary (v2.1 evidence scope binding 必填) 在 MUST_RETURN."""
    mr = _must_return()
    assert "rationale" in mr
    assert "evidence_scope_summary" in mr
    # v2.1 binding 语义: scope 外新证据须重新 consult
    assert "重新 consult" in mr or "重新咨询" in mr


def test_must_return_requires_specific_steps_for_custom_action() -> None:
    """custom_action 时必带 specific_steps — MUST_RETURN 要交代 (否则 verdict 空心)."""
    mr = _must_return()
    assert "specific_steps" in mr


def test_must_return_is_substantive_not_placeholder() -> None:
    """非占位: MUST_RETURN 明显比原"事件命名引用"一行长 (含结构字段)."""
    # 原占位 ~ "MismatchResolutionDecided 格式 (executor 自决) 或经 advisor 的 AdvisorVerdictDelivered"
    # 约 50 字; 完整结构应远长于此 (含 5 decision + 必填字段约束)。
    assert len(_must_return()) > 200, f"MUST_RETURN too short to carry full structure: {len(_must_return())}"
