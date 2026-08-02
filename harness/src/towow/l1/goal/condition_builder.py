"""GOAL mode condition builder — RUN-003 T-E3.

# spec source:
#   harness/docs/dogfood-runs/DOGFOOD-RUN-003-launch/launch-context.md §0.5.2 / §0.5.3
#   harness/docs/dogfood-runs/DOGFOOD-RUN-003-launch/concept-list.md C-03 / C-04
#   02-meta-and-requirements/SYSTEM-REQUIREMENTS.md §四 P-12
#   04-l1-intelligence/M-1.1-interview-skill-detailed-design.md §8.2

按 C-03 entity reference 协议 + C-04 designer-style craft 生成 /goal 的 condition 文本：

- 只 reference v3 已有 entity (brief / capsule / @ refs / obligation / plan)，不重写任务详情
- 写法 = 设计意图（"解决什么 / 为什么 / 原则 / 不能动什么"），不是步骤指令清单
- 上限 4000 字符 (P-12 物理约束 schema-enforced in GoalSessionStartedPayload)
- enforce_designer_style() 检测命令式步骤清单作为反例

Caller (M-1.4 execution skill / goal spawn CLI) 用 build_condition() 构造文本，
然后通过 spawn_bg_session(prompt=...) 启动 background session。
"""

from __future__ import annotations

import re

GOAL_CONDITION_MAX_CHARS = 4000

# 命令式步骤清单关键词——detector reject 这些（C-04 反例）
_IMPERATIVE_PATTERNS = (
    # 中文：先 X 再 Y / Step N / 第 N 步
    r"先[做完执跑]+\s*\S+\s*再[做完执跑]+",
    r"第\s*[一二三四五六七八九十0-9]+\s*步",
    r"Step\s*\d+",
    r"步骤\s*[一二三四五六七八九十0-9]+",
    # 英文 imperative numbered list
    r"^\s*\d+\.\s*(Do|Run|Execute|Build|Implement|Write)\b",
)


class ConditionTooLongError(ValueError):
    """Raised when generated condition exceeds GOAL_CONDITION_MAX_CHARS."""


class ImperativeStyleError(ValueError):
    """Raised when condition contains imperative step list patterns (C-04 violation)."""


def enforce_designer_style(condition_text: str) -> None:
    """Reject condition_text if it contains imperative step-list patterns.

    Raises ImperativeStyleError on detection. Detector is heuristic — won't catch every
    imperative pattern but flags the obvious "先 X 再 Y" / "Step N" / "第 N 步" cases
    that C-04 explicitly forbids. Real review still in PR review (M-1.5).
    """
    for pattern in _IMPERATIVE_PATTERNS:
        m = re.search(pattern, condition_text, re.IGNORECASE | re.MULTILINE)
        if m:
            raise ImperativeStyleError(
                f"condition contains imperative step pattern: '{m.group(0)}'. "
                "C-04 requires designer-style (设计意图: 解决什么 / 为什么 / 原则 / 不能动什么), "
                "not step-list (先 X 再 Y / Step N).",
            )


def build_condition(
    *,
    brief_event_id: str,
    capsule_event_id: str,
    plan_freezed_event_id: str,
    active_obligation_ids: list[str],
    design_intent: str,
    rationale: str,
    principles: list[str],
    not_to_touch: list[str],
    extra_invariants: list[str] | None = None,
) -> str:
    """构造符合 C-03 + C-04 协议的 GOAL condition 文本.

    Args:
        brief_event_id: brief publish event_id（任务详情来源——不重写到 condition）.
        capsule_event_id: capsule_compiled_event_id（GOAL session 的 capsule）.
        plan_freezed_event_id: plan freeze event_id（task graph 来源）.
        active_obligation_ids: 当前 active obligation id 列表（全局约束）.
        design_intent: 一句话设计意图——"我们要解决什么".
        rationale: 一句话"为什么现在做"（错了会怎样的延伸）.
        principles: 设计原则列表（"我们倾向 X"风格陈述，不是"做 X"指令）.
        not_to_touch: 明确不能动的边界（已有不变量 / 约束）.
        extra_invariants: 可选——补充关键不变量陈述.

    Returns:
        condition 文本字符串 (≤4000 字符).

    Raises:
        ConditionTooLongError: 生成文本超 4000 字符.
        ImperativeStyleError: principles 或 design_intent 含命令式步骤模式.
    """
    obl_str = ", ".join(active_obligation_ids) if active_obligation_ids else "(none active)"
    principles_section = "\n".join(f"- {p}" for p in principles)
    not_to_touch_section = "\n".join(f"- {n}" for n in not_to_touch)
    extra_section = ""
    if extra_invariants:
        extra_section = "\n\n关键不变量:\n" + "\n".join(f"- {i}" for i in extra_invariants)

    condition = (
        f"本 GOAL session 工作 entity refs：\n"
        f"  brief_event_id = {brief_event_id}\n"
        f"  capsule_compiled_event_id = {capsule_event_id}\n"
        f"  plan_freezed_event_id = {plan_freezed_event_id}\n"
        f"  active_obligation_ids = [{obl_str}]\n"
        f"\n"
        f"evaluator 每轮判定协议（设计意图）：\n"
        f"  done = brief.goal.completion_condition 全 observable 满足\n"
        f"         AND plan.task_ids 全部 latest FindingResolved.closure_state ∈ {{closed}}\n"
        f"         AND active_obligations 全部未被 violated\n"
        f"  not done → 列出未满足的 entity ref + 具体缺什么 evidence\n"
        f"  evidence 查在 .towow/events.log + .towow/graph/<projection>.json\n"
        f"\n"
        f"我们要解决什么：{design_intent}\n"
        f"为什么：{rationale}\n"
        f"\n"
        f"原则：\n"
        f"{principles_section}\n"
        f"\n"
        f"不能动什么：\n"
        f"{not_to_touch_section}"
        f"{extra_section}"
    )

    # Reject imperative patterns (C-04)
    enforce_designer_style(condition)

    if len(condition) > GOAL_CONDITION_MAX_CHARS:
        raise ConditionTooLongError(
            f"condition is {len(condition)} chars; P-12 hard limit = {GOAL_CONDITION_MAX_CHARS}. "
            "Move detail to brief / linked entities, condition should only reference.",
        )

    return condition


__all__ = [
    "GOAL_CONDITION_MAX_CHARS",
    "ConditionTooLongError",
    "ImperativeStyleError",
    "build_condition",
    "enforce_designer_style",
]
