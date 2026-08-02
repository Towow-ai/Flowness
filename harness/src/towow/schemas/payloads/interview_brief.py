"""M-1.1 InterviewBriefPublished payload — audience-separated (T0.3 — DOGFOOD-001-B repair).

# spec source:
#   04-l1-intelligence/M-1.1-interview-skill-detailed-design.md §4.1 brief_publish
#   docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Cross-cutting Patch L1-checkpoint-audience-separation-1
#
# DOGFOOD-RUN-001 → DOGFOOD-001-B owner-facing-render-defect surfaced 2026-05-22:
#   M-1.1 brief checkpoint mixed owner-facing summary / skill-facing payload /
#   audit trace in one undifferentiated payload. Nature could not judge "did the
#   system understand me?" — checkpoint became a false-closure point.
#
# T0.3 fix: audience-separated payload (Nature 决策 1):
#   owner_facing_summary    — Nature 当时确认的具体表达 (canonical, NOT projection)
#   owner_facing_summary_hash — sha256 (self_check 验完整性)
#   owner_facing_style_ref  — 当前 "owner-facing-render-style-profile@v1"
#   skill_facing_payload    — M-1.2 / M-1.3 消费的结构化 INF / concept seeds / constraints
#   audit_trace_refs        — provenance event_ids, supersede chain
#   supersedes              — 前一 brief event_id (用于 corrected brief supersede chain)
#
# Pattern is reusable: Cross-cutting Patch L1-checkpoint-audience-separation-1
# will apply same shape to M-1.2 / M-1.3 / M-1.5 / M-1.6 when their respective
# dogfood runs expose the same defect.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import ClosureVerificationMethod, LedgerEventScope

# canonical BlockingCheck — 复用 M-0.4 envelope self_check 的同款类 (T0.4 / DOGFOOD-001-B).
# 不另造 — gate _run_checks 物理校验 status=='passed' 读的就是这个 shape, 且它强制
# "passed 必须有非空 evidence" 的 audit 不变量. brief.stop_condition_passed 跟
# envelope.self_check.blocking_checks 镜像同源, 用同一个类避免两份 BlockingCheck 漂移.
from towow.schemas.envelope import BlockingCheck
from towow.schemas.payloads.finding import ClosureCriterion
from towow.schemas.payloads.state_transition import SemanticAnnotation

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ════════════════════════════════════════════════════════════════════════════════
#  Sub-models — skill-facing payload (M-1.2 / M-1.3 consume)
# ════════════════════════════════════════════════════════════════════════════════


class InformationNeedRef(BaseModel):
    """Reference to a resolved InformationNeed entry — simplified 投影 (RUN-017C INF-007).

    保留简化投影 (给 M-1.2 共识看的 6 字段子集), 不替换成 §7.2 InformationNeedNode
    全 12 字段 — 12 字段 full node 由 audit_trace_refs.information_need_event_ids
    trace back. status filter: status='superseded' 节点不进 brief.skill_facing_payload
    .information_needs (由 brief_publish.filter_information_needs 过滤), 只进
    audit_trace_refs. 故本投影里 status 实际恒 'resolved' (filter 后).
    """

    model_config = _STRICT

    need_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: Literal["nature_unique", "nature_confirm", "ai_reachable", "defer"]
    red_line: bool
    status: Literal["resolved", "superseded"] = "resolved"
    downstream_impact: list[str] = Field(default_factory=list)
    resolution_quote: str | None = None  # Nature's confirming text when applicable


class ConceptSeedRef(BaseModel):
    """Concept seed candidate emerging from the interview (consumed by M-1.2)."""

    model_config = _STRICT

    seed_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SkillFacingPayload(BaseModel):
    """M-1.2 / M-1.3 / M-1.5 / M-1.6 default consumer of the brief."""

    model_config = _STRICT

    information_needs: list[InformationNeedRef] = Field(default_factory=list)
    concept_seeds: list[ConceptSeedRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    red_lines: list[str] = Field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════════
#  Sub-models — audit trace refs (debug / replay / provenance)
# ════════════════════════════════════════════════════════════════════════════════


class AuditTraceRefs(BaseModel):
    """Provenance back-pointers so a brief can be reverse-traced to raw_prompt."""

    model_config = _STRICT

    session_start_event_id: str = Field(min_length=1)
    raw_prompt_event_id: str = Field(min_length=1)
    information_need_event_ids: list[str] = Field(default_factory=list)
    nature_judgment_event_ids: list[str] = Field(default_factory=list)
    superseded_brief_event_id: str | None = None


# ════════════════════════════════════════════════════════════════════════════════
#  Sub-models — stop-condition self-check (§4.5 generic SkillArtifactSelfCheck)
# ════════════════════════════════════════════════════════════════════════════════
#  BlockingCheck 复用 towow.schemas.envelope.BlockingCheck (import 在文件顶部) —
#  §4.5 是 generic SkillArtifactSelfCheck (M-1.1a Patch 1 / 反向 narrow patch P),
#  不是 interview-specific, 所以不另造类. evidence 是 free-form (INF-003 — v3 初版
#  文本 + 数值; envelope.BlockingCheck.evidence 是 dict[str, str], 数值存文本表达).


class LiveTargetObservable(BaseModel):
    """live-target-execution-evidence@v1: goal completion_condition 的一条真实处理断言 (结构化 observable)。

    偿 T-L1-23 (结构化 completion_condition) 的最小切口: goal_completion_condition 仍是单串自由文本,
    额外携带一个 live_target_observables 结构化列表 —— 每项声明"这个 goal 要真在生产对象上产生一条
    (或 N 条) 指定 kind 的真实 domain 事件才算达成"。goal 收口门 (goal.live_target_reconciled) 对每条
    observable 的 evidence 复算 against committed 账本, 任一未满足 → 拒 completion (堵旗舰: 账本 0 条
    StrandedBatchManifestFrozen 却靠撤边 + 单 success 宣布 completion)。

    向后兼容: 字段可选默认空 → 无 observable 的旧 goal 零误伤, 收口门 not_applicable 放行。
    evidence 必须是 LEDGER_EVENT machine_check 且 scope=this_goal (goal 层证据锚定本 goal, 不是别 goal
    的旧事件; scope=global 会被 advisor #1 指出的 false-accept 击穿, 故此处 model_validator 强制)。
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    observable_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: ClosureCriterion  # 必为 LEDGER_EVENT 判据

    @model_validator(mode="after")
    def _enforce_ledger_event_this_goal(self) -> LiveTargetObservable:
        mc = self.evidence
        if mc.verification_method is not ClosureVerificationMethod.LEDGER_EVENT:
            msg = (
                f"live_target_observable {self.observable_id} 的 evidence.verification_method="
                f"{mc.verification_method.value} — goal 层真实处理证据必须是 ledger_event (门读账本计数)"
            )
            raise ValueError(msg)
        if mc.scope_binding is not LedgerEventScope.THIS_GOAL:
            msg = (
                f"live_target_observable {self.observable_id} 的 evidence.scope_binding="
                f"{mc.scope_binding.value if mc.scope_binding else None} — goal observable 必须 scope=this_goal "
                "(锚定本 goal, 不允许 global: 否则别 goal 的旧同类事件会让本 goal 空声称 false-accept)"
            )
            raise ValueError(msg)
        return self


class BriefStopCondition(BaseModel):
    """§5.4 stop_condition_passed — brief publish 全部 publish-gating self-check 留痕.

    InterviewBriefPublishedPayload.stop_condition_passed 承载 5 项 blocking_check 的
    结果快照 (跟 envelope.self_check.blocking_checks 镜像同源 — envelope 是 commit
    gate 物理校验入口, 本字段是 canonical event 上的留痕, 便于下游 / audit 不读
    envelope 也能看到证据). §7.3 publish 前提同时列 "Nature member check 确认" +
    "停止条件三测试结果", 故本字段含:
      - interview.owner_brief_confirmed (Patch L1 / DOGFOOD-001-B member check gate)
      - interview.red_line_gap_closed     ┐
      - interview.direction_stability      │ §4.5 4 项停止条件 (含 red-line gap + 三测试)
      - interview.design_derivability      │
      - interview.nature_unique_coverage  ┘

    T0.5 consensus_start precondition 读本字段验 owner_brief_confirmed=passed.
    passed == all(c.status == "passed" for c in blocking_checks).
    """

    model_config = _STRICT

    passed: bool
    blocking_checks: list[BlockingCheck] = Field(min_length=1)


# ════════════════════════════════════════════════════════════════════════════════
#  Sub-model — concept seed payload (§5.5 flat shape, M-1.1 brief-publish 产)
# ════════════════════════════════════════════════════════════════════════════════


class ConceptSeedCreatedPayload(BaseModel):
    """§5.5 ConceptCreated payload — M-1.1 采访产的种子概念 (flat shape).

    spec §5.5 明写 M-1.1 *复用* M-1.2 的 ConceptCreated event_type (不新增
    ConceptSeedCreated), 通过 provenance.actor_id + skill_id metadata 区分采访产出.
    §5.5 列的 payload 字段是 flat (concept_id / concept_name / definition /
    seed_origin / source_brief_id).

    本 shape 跟 M-0.1 schemas/payloads/state_transition.py ConceptCreatedPayload
    (after_state nesting, 无 seed_origin / source_brief_id) 有真实落差 — codify 进
    docs/SPEC-CONFLICT-RESOLUTION-LEDGER.md Patch Q. v3 初版 M-1.1 emit §5.5 flat
    shape; M-1.2 共识 skill 真消费 ConceptCreated 时再做 reconcile
    (PHASE-E.3-CLOSURE-ROADMAP §5, RUN-017C scope 外).

    source_brief_id 用 brief_id (语义 id), 不是 InterviewBriefPublished event_id:
    atomic 单 envelope 内 event_id 在 commit 时才分配 (commit gate stamp 时机),
    intent 构造时不可知, 故 brief↔concept 互链用确定性语义 id (brief_id / concept_id),
    event_id 链路由 projection 1:1 还原 (跟 §4.1 envelope.patches 用 local_intent_id
    而非 event_id 同款 pattern). 详 Patch Q.
    """

    model_config = _STRICT

    kind: Literal["ConceptCreated"] = "ConceptCreated"
    target_entity_type: Literal["concept"] = "concept"
    concept_id: str = Field(min_length=1)
    concept_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    seed_origin: Literal[True] = True  # 采访阶段产的种子恒 true
    source_brief_id: str = Field(min_length=1, pattern=r"^brief-[A-Za-z0-9_-]+$")
    # R01 concept-create-relatedness gate (f-interview-seed-relatedness-emitter-gap 修):
    # 概念图成熟后 (>= maturity_threshold) 出生门 concept_isolated_no_relatedness 要求新概念声明
    # 相关性 (affected_concept_ids / relationship_type=genuinely_new)。此前采访种子路径无声明入口 →
    # 成熟图上任何 seed 被出生门孤立拒, 逼零种子发布断 F2 (brief→共识 context 链)。补上 semantic_annotation
    # 让种子跟姊妹命令 consensus concept-create 一样能带关联发布。gate 读 payload 顶层 semantic_annotation
    # (concept_create_gate._extract_new_concepts), model_dump(mode="json") 含此字段即被门识别。
    semantic_annotation: SemanticAnnotation | None = None



# ════════════════════════════════════════════════════════════════════════════════
#  RUN-018-F11 — Sub-skill invocation evaluation (per M-1.1 §7 触发条件审计)
# ════════════════════════════════════════════════════════════════════════════════


class SubSkillEvalEntry(BaseModel):
    """每个 sub-skill (M-1.1 §7.1-§7.9) 的触发判定留痕 (F11 spec gap fix).

    采访棒 brief publish 时, 对每个 sub-skill 给出 evaluation。本结构是
    **skip-ledger + 有限 evidence 反推**, 不冒充全覆盖的 invoked 判定 (T-FIX-B6-13 /
    INT-content#2 修): decision 的可信度按 sub-skill 是否在 canonical 事件日志留痕分档,
    判定逻辑见 brief_publish.build_sub_skill_evaluation / _SUB_SKILL_EVIDENCE_KIND。

      - decision=skipped: 三种情形之一 —
          (a) 该 session emit 过 SubSkillInvocationSkipped → 真判未命中 (rationale 取自事件);
          (b) 可查 canonical 痕迹的工具 (info-map-build) 本 session 无痕迹 → 无法确认调用,
              诚实降级不冒充 invoked;
          (c) 条件触发且不写 event log 的工具 (conops/influence/sdm/cdm) 无痕迹无 skip →
              无法确认是否调用, skip-ledger 不冒充 invoked。
      - decision=invoked: 三种情形之一 (可信度不同, rationale 自带说明) —
          (a) info-map-build 有 canonical checkpoint envelope 痕迹 → evidence-backed;
          (b) brief-publish 正在发布 → 发布动作即 evidence;
          (c) spec 必走但不写 event log 的工具 (prep-research/consistency-verify/
              information-power) 无 skip 留痕 → 按设计预期判 invoked, 但 *非* canonical
              evidence-backed (rationale 注明)。

    故 invoked **不等于** "有 Agent tool call / CLI emit evidence" — 仅 (a)(b) 两类
    evidence-backed, (c) 类是设计预期推断。rationale 始终诚实记录依据。

    回头审能区分 'agent 真判未命中' / 'agent 忘了调' / '无法确认调用'. 这正是 F11 voi_rationale.
    """

    model_config = _STRICT

    sub_skill_id: str = Field(min_length=1)  # e.g. "interview-conops-construct"
    decision: Literal["invoked", "skipped"]
    rationale: str = Field(min_length=1)  # 一句话为什么这个 decision
    triggering_condition_evaluation: str = Field(default="")  # spec line + 实际对话评估

# ════════════════════════════════════════════════════════════════════════════════
#  Top-level — InterviewBriefPublishedPayload
# ════════════════════════════════════════════════════════════════════════════════


class InterviewBriefPublishedPayload(BaseModel):
    """M-1.1 §4.1 brief_publish checkpoint canonical payload.

    Mandatory invariants:
      - session_id non-empty (T0.2 session_id inheritance enforces continuity)
      - owner_facing_summary non-empty (Nature 当时确认的具体表达; canonical event payload)
      - owner_facing_summary_hash = sha256(owner_facing_summary) (T0.4 self_check 验完整性)
      - owner_facing_style_ref points to active style profile
        (e.g. "owner-facing-render-style-profile@v1")

    Supersede chain (audit_trace_refs.superseded_brief_event_id + supersedes):
      When a corrected brief is published (e.g. after Nature confirms reworded summary),
      `supersedes` carries the prior brief's event_id, allowing M-1.2 precondition
      check (T0.5) to read latest active brief and verify owner_confirmed status.
    """

    model_config = _STRICT

    kind: Literal["InterviewBriefPublished"] = "InterviewBriefPublished"
    # Patch Q-interview-brief-canonical-emit-1: target_entity_type=interview_brief
    # 现注册在 TargetEntityType enum (RUN-017C). 之前 stub-rewrap 经 NodeTouched 无此判别符.
    target_entity_type: Literal["interview_brief"] = "interview_brief"
    brief_id: str = Field(min_length=1, pattern=r"^brief-[A-Za-z0-9_-]+$")
    session_id: str = Field(min_length=1, pattern=r"^sess-interview-[A-Za-z0-9_-]+$")

    # Audience: owner (Nature)
    owner_facing_summary: str = Field(min_length=1)
    owner_facing_summary_hash: str = Field(
        min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$",
    )  # sha256 hex
    owner_facing_style_ref: str = Field(min_length=1)

    # Audience: downstream skills (M-1.2 / M-1.3 / ...)
    skill_facing_payload: SkillFacingPayload

    # Audience: debug / replay / provenance
    audit_trace_refs: AuditTraceRefs

    # §5.4 mandatory fields (RUN-017C — 之前 stub-rewrap 缺这 3 个, brief 装样子)
    # goal_completion_condition: brief.success_criteria → /goal 翻译 (INF-005 CLI required)
    goal_completion_condition: str = Field(min_length=1)
    # live-target-execution-evidence@v1: goal 的真实处理断言 (结构化 observable 列表, 偿 T-L1-23 最小切口)。
    # 可选默认空 → 无 observable 的旧 goal 零误伤; 有 observable 时 goal 收口门对每条复算账本证据。
    live_target_observables: list[LiveTargetObservable] = Field(default_factory=list)
    # concept_seeds_created: 同 envelope atomic emit 的 ConceptCreated 的 concept_id 列表
    # (INF-004). 用 concept_id 语义 id 不用 event_id — atomic 内 event_id commit 时才分配,
    # 见 ConceptSeedCreatedPayload docstring + Patch Q.
    concept_seeds_created: list[str] = Field(default_factory=list)
    # stop_condition_passed: §4.5 4 项停止条件 blocking_check 留痕 (INF-003)
    stop_condition_passed: BriefStopCondition

    # F11 fix: sub-skill 触发判定留痕 (每个 §7.1-§7.9 一条 entry, optional 默认 []
    # — v3 初版不强制必出, future SKILL.md enforce in F1 fix)
    sub_skill_evaluation: list[SubSkillEvalEntry] = Field(default_factory=list)

    # Supersede chain (corrected brief workflow)
    supersedes: str | None = None  # prior brief event_id when this is a correction


__all__ = [
    "AuditTraceRefs",
    "BlockingCheck",  # re-export from envelope for convenience (canonical class lives there)
    "BriefStopCondition",
    "ConceptSeedCreatedPayload",
    "ConceptSeedRef",
    "InformationNeedRef",
    "InterviewBriefPublishedPayload",
    "LiveTargetObservable",
    "SkillFacingPayload",
    "SubSkillEvalEntry",  # F11 fix
]
