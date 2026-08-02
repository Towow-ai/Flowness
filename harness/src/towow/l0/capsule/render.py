"""Capsule → markdown text renderers (section-level).

# spec source:
#   01-reconciliation/R01-BRAIN-DELIVERY-DESIGN-2026-06-17.md
#     §2 Job 2 投递层 (入口层: 把入口概念定义+引用结构化喂给干活 agent)
#     §5 复用点: render_audit_capsule_text (sections→文本渲染器)
#
# Why this module exists (R01 Part A — concept-context-progressive-disclosure@v1 入口层):
#   assemble_capsule() 已组装出 KNOWN_FACTS 概念邻域 section, 但 6 个 session-start 调用方只取
#   capsule_compiled_event_id (倒查链 / gate 用), 把 .sections 丢弃 → 干活 agent 真读的是
#   .towow/capsules/<role>-knowledge.md (Channel 1 方法论), 拿不到本任务相关的概念图定义。
#   本模块把概念邻域 section 渲成 markdown, 供 `work start` 原子写进 per-session 文件让 agent Read。
#
# 与 l1/audit_fork.render_audit_capsule_text 的关系: 那个把 AUDIT capsule 的全部 section 渲成喂
#   audit fork 的文本。本模块把它的渲染逻辑提取成共享核 (_render_sections), audit_fork 改调它,
#   行为逐字节不变 (审计输出有快照测保护)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.schemas.enums import CapsuleSection

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l0.capsule.pipeline import CapsulePipelineResult
    from towow.schemas.capsule import CapsuleResult

# R01 入口层投递的概念上下文 section 集 — 概念邻域定义 (KNOWN_FACTS) + 该任务要读的文件
# (FILES_TO_READ)。CONCEPT_NEIGHBORHOOD 当前不是独立 enum (邻域渲进 KNOWN_FACTS), 但 spec 提到
# "若有" → 显式列上, 未来若拆出独立 section 这里零改即生效, 现在它永不命中 (优雅向前兼容)。
_NEIGHBORHOOD_SECTIONS: frozenset[str] = frozenset(
    {
        CapsuleSection.KNOWN_FACTS.value,
        "CONCEPT_NEIGHBORHOOD",  # 未来若拆出独立 section (现 enum 无此值 → 永不命中)
        CapsuleSection.FILES_TO_READ.value,
    },
)


def _render_sections(
    capsule: CapsuleResult,
    *,
    only: Iterable[str] | None = None,
    skip_empty: bool = False,
) -> str:
    """共享渲染核 — 把 capsule.sections 按 `### {section}\\n{content}` 渲成文本, 段间空行分隔。

    only=None    → 渲染全部 section (audit 路径, 与原 render_audit_capsule_text 逐字节一致)。
    only={...}   → 只渲染 section 名在集合里的 (按 capsule.sections 原顺序保留)。
    skip_empty   → True 时跳过 content 空白的 section (邻域投递: 任务无概念锚 → 空 section 不渲)。

    skip_empty 默认 False 以保 audit 行为不变 (audit 渲全部 section, 含空 content)。
    """
    only_set = set(only) if only is not None else None
    parts: list[str] = []
    for sec in capsule.sections:
        name = sec.section.value
        if only_set is not None and name not in only_set:
            continue
        if skip_empty and not sec.content.strip():
            continue
        parts.append(f"### {name}\n{sec.content}")
    return "\n\n".join(parts)


def render_neighborhood_capsule_text(capsule_result: CapsulePipelineResult) -> str:
    """R01 入口层 — 从 CapsulePipelineResult 抽概念邻域 section 渲成 markdown 喂给干活 agent。

    抽 KNOWN_FACTS (概念图邻域定义) + FILES_TO_READ (本任务要读的文件) (+ 未来 CONCEPT_NEIGHBORHOOD
    若拆独立 section), 空 content section 跳过。所有目标 section 都空/缺 → 返回 "" (caller 据此
    no-op 不建文件, 任务无概念锚不报错)。

    只读 capsule_result.capsule.sections, 不碰 capsule_compiled_event_id 倒查链 / gate 行为。
    """
    return _render_sections(
        capsule_result.capsule,
        only=_NEIGHBORHOOD_SECTIONS,
        skip_empty=True,
    )


__all__ = ["render_neighborhood_capsule_text"]
