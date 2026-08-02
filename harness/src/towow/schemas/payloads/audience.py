"""Cross-cutting L1-checkpoint audience separation — owner-facing vs skill-facing render.

# spec source:
#   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md
#     Patch L1-checkpoint-audience-separation-1 §3 (pattern — 所有 L1 checkpoint 复用同一形态):
#       owner_facing_summary / owner_facing_summary_hash / owner_facing_style_ref /
#       skill_facing_payload / audit_trace_refs / supersedes
#       + envelope.self_check.blocking_checks 一项 <skill>.<checkpoint>.owner_confirmed
#   docs/style-profiles/owner-facing-render-style-profile-v2.md (rule 10 第一段无内部术语 /
#     rule 11 中文为主, 英文术语只在被命名时出现 — 反例 "emit envelope 走 commit gate 触发
#     self_check.blocking_checks")
#
# RUN-070 AC4 (跨切 L1-checkpoint-audience-separation): M-1.1 brief 已应用 (interview_brief.py
# 作 reference design); 本模块把同一形态抽成可复用机制并应用到 M-1.2/1.3/1.5 checkpoint。
#
# 为什么 owner_facing_summary 必须是 canonical event payload (不是 projection 派生): 它是 "Nature
# 当时确认的具体表达", 必须可被 owner_confirmed self_check 用 hash 钉住完整性 — 派生的 render 会
# 漂移。skill_facing_payload 才是下游 skill 消费的结构化内容。两个受众物理分离, owner 永远拿不到
# 内部 schema (DOGFOOD-001-B owner-facing-render-defect 的根治)。
#
# 真分离逻辑 (反空壳): detect_audience_leak 按 style-profile-v2 真扫 owner-facing 文本里漏进的
# 内部术语/模块编号/事件 id/JSON 片段 — 漏了就 reject。这不是声明字段而已, 是 enforced 写边界
# (model_validator 在 schema 层拒掉混入内部 schema 的 owner-facing render)。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 当前 latest active 风格档 (v1 已 supersede, 见 owner-facing-render-style-profile-v2.md)。
STYLE_PROFILE_V2_REF = "owner-facing-render-style-profile@v2"

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


def compute_owner_facing_summary_hash(summary: str) -> str:
    """sha256 hex (64) of the owner-facing summary — self_check 用它验完整性 (LEDGER §3)。"""
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


# ── style-profile-v2 leak markers (owner-facing 文本里不该出现的内部术语/结构) ──
# rule 10: 模块编号 (M-0.x / Phase E.x / DOGFOOD-001-x) 不进 owner-facing。
_MODULE_CODE_RE = re.compile(r"\bM-[0-9]+\.[0-9]+\b|\bPhase\s+[A-Z]\.[0-9]|\bDOGFOOD-[0-9]")
# 内部实体 id 前缀 (事件/任务/概念 raw id 不进 owner-facing)。
_INTERNAL_ID_RE = re.compile(r"\b(?:evt|tnode|task|sess|brief|debt|crun|finding)-[0-9a-z]{4,}", re.IGNORECASE)
# JSON / schema 片段直接糊进散文 (rule 11 反例形态)。
_JSON_FRAGMENT_RE = re.compile(r'\{\s*"|"\s*:\s*"|after_state|provenance_hint')
# rule 11 反例里点名的内部管线/schema 术语 (raw 英文 identifier 糊进中文)。
_PIPELINE_JARGON = (
    "envelope",
    "commit gate",
    "commit_gate",
    "self_check",
    "blocking_check",
    "base_classification",
    "scan_method",
    "concept_graph",
    "state_machine",
    "projection",
    "ConsumerListPublished",
    "EngineeringConsensusFreezed",
    "PlanFreezed",
    "FindingCreated",
)


def detect_audience_leak(owner_facing_summary: str) -> list[str]:
    """按 style-profile-v2 (rule 10 + 11) 扫 owner-facing 文本里漏进的内部术语/结构。

    返回命中的 leak marker 列表 (空 = 干净, 可呈现给 owner)。这是真分离逻辑: owner_facing_summary
    若混入模块编号 / 事件 id / JSON 片段 / 内部管线术语 → 被判 leak → schema 层 reject。
    不追求穷尽 (不可能), 但钉死 style-profile-v2 点名的反模式 (DOGFOOD-001-B 的根因形态)。
    """
    leaks: list[str] = []
    for m in _MODULE_CODE_RE.findall(owner_facing_summary):
        leaks.append(f"module-code:{m}")
    for m in _INTERNAL_ID_RE.findall(owner_facing_summary):
        leaks.append(f"internal-id:{m}")
    if _JSON_FRAGMENT_RE.search(owner_facing_summary):
        leaks.append("json-or-schema-fragment")
    for tok in _PIPELINE_JARGON:
        if tok in owner_facing_summary:
            leaks.append(f"pipeline-jargon:{tok}")
    return leaks


class CheckpointAudienceSeparation(BaseModel):
    """LEDGER §3 — L1 checkpoint 受众分离形态 (owner-facing vs skill-facing), 可复用一等对象。

    挂在任何需要 Nature confirmation/awareness 的 L1 checkpoint event payload 上 (M-1.2 consensus
    freeze / M-1.3 plan freeze / M-1.5 finding)。schema 层 enforce: hash 完整性 + style ref 是
    active (非 v1 superseded) + owner-facing 文本无内部术语泄漏 (detect_audience_leak)。混入内部
    schema 的 owner-facing render 在构造时即 reject (enforced 写边界, 非声明字段)。
    """

    model_config = _STRICT

    # Audience: owner (Nature) — canonical confirmed 表达, NOT projection 派生。
    owner_facing_summary: str = Field(min_length=1)
    owner_facing_summary_hash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    owner_facing_style_ref: str = STYLE_PROFILE_V2_REF
    # Audience: 下游 skill 消费的结构化内容 (内部 schema 住这里, 不进 owner-facing)。
    skill_facing_payload: dict[str, Any] = Field(default_factory=dict)
    # Audience: debug / replay / provenance (event_id / session_id / supersede chain)。
    audit_trace_refs: list[str] = Field(default_factory=list)
    # 前一 checkpoint event_id (correction supersede chain)。
    supersedes: str | None = None

    @model_validator(mode="after")
    def enforce_audience_separation(self) -> Self:
        """schema 层 enforce 受众分离 (LEDGER §3 + style-profile-v2)。

        1. hash 完整性: owner_facing_summary_hash == sha256(owner_facing_summary);
        2. style ref active: 引 style-profile 家族且非 v1 (v1 已 supersede);
        3. 无泄漏: owner-facing 文本无模块编号/内部 id/JSON 片段/管线术语 (detect_audience_leak)。
        """
        expected = compute_owner_facing_summary_hash(self.owner_facing_summary)
        if self.owner_facing_summary_hash != expected:
            msg = (
                "owner_facing_summary_hash 不符 sha256(owner_facing_summary) "
                "— self_check 完整性失败 (LEDGER §3)"
            )
            raise ValueError(msg)
        ref = self.owner_facing_style_ref
        if "owner-facing-render-style-profile@v" not in ref or ref.endswith("@v1"):
            msg = (
                f"owner_facing_style_ref={ref!r} 必须引 active style profile "
                "(owner-facing-render-style-profile@v2+; v1 已 supersede)"
            )
            raise ValueError(msg)
        leaks = detect_audience_leak(self.owner_facing_summary)
        if leaks:
            msg = (
                f"owner_facing_summary 混入内部 schema/术语 (style-profile-v2 违反): {leaks} "
                "— owner-facing render 必须无内部术语泄漏 (DOGFOOD-001-B 根治)"
            )
            raise ValueError(msg)
        return self


def build_audience_separation(
    *,
    owner_facing_summary: str,
    skill_facing_payload: dict[str, Any] | None = None,
    audit_trace_refs: list[str] | None = None,
    supersedes: str | None = None,
    style_ref: str = STYLE_PROFILE_V2_REF,
) -> CheckpointAudienceSeparation:
    """便捷构造: 自动算 hash, 默认 style-profile-v2。owner_facing_summary 泄漏内部术语会被 reject。"""
    return CheckpointAudienceSeparation(
        owner_facing_summary=owner_facing_summary,
        owner_facing_summary_hash=compute_owner_facing_summary_hash(owner_facing_summary),
        owner_facing_style_ref=style_ref,
        skill_facing_payload=skill_facing_payload or {},
        audit_trace_refs=audit_trace_refs or [],
        supersedes=supersedes,
    )


def render_owner_facing(sep: CheckpointAudienceSeparation) -> str:
    """owner-facing render: 只给 Nature 看的 canonical 表达 (保证无内部术语 — 构造时已校验)。"""
    return sep.owner_facing_summary


def render_skill_facing(sep: CheckpointAudienceSeparation) -> dict[str, Any]:
    """skill-facing render: 下游 skill 消费的结构化内容 (内部 schema 住这里, 不混进 owner-facing)。"""
    return dict(sep.skill_facing_payload)


def build_owner_confirmed_check(
    *,
    skill: str,
    checkpoint: str,
    nature_judgment_event_id: str,
    confirmed_owner_facing_summary_hash: str,
) -> dict[str, Any]:
    """LEDGER §3 — envelope.self_check.blocking_checks 的 <skill>.<checkpoint>.owner_confirmed 项。

    下游 skill 入口 precondition 检查 latest checkpoint 的 self_check 已 confirmed; 若无 → exit 1。
    """
    return {
        "check_id": f"{skill}.{checkpoint}.owner_confirmed",
        "status": "passed",
        "evidence": {
            "nature_judgment_event_id": nature_judgment_event_id,
            "confirmed_owner_facing_summary_hash": confirmed_owner_facing_summary_hash,
        },
    }


__all__ = [
    "STYLE_PROFILE_V2_REF",
    "CheckpointAudienceSeparation",
    "build_audience_separation",
    "build_owner_confirmed_check",
    "compute_owner_facing_summary_hash",
    "detect_audience_leak",
    "render_owner_facing",
    "render_skill_facing",
]
