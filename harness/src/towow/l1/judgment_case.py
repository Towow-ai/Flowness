"""JudgmentCase 富化 / 纠正 / bounded 种子集 — judgment-case@v1 (T-JLM-01).

# concept source: judgment-case@v1 (frozen, plan-interview-oldhand-rework)
#
# 这个模块是判例持久化的真路径 (l1 库层): 把一条 NatureJudgmentCaptured 原始事件富化成 JudgmentCase,
# 经 JUDGMENT_CASE_ENRICHED 事件 (path-B write_direct) 落账; 纠正复用 EventBase.supersede 通用机制;
# 并能产一个够支撑一次 PreferenceAsTestHarness 回归的 bounded enriched 种子集 (非全量回填)。
#
# path-B 理由 (同 DebtRegistered / SemanticConflictDetected): 判例富化是对既有 Nature 判断的结构化
# 观测记录, 不是需要 commit gate 批准的状态变更。provenance 非交互 (actor_type=system) —— 富化是
# 机器 / agent 对账本的加工, 不是 Nature 本人的新判断 (Nature 的原话是被引用的 source_event_id)。
#
# c-1-no-index-infra 约束 (preference-as-test-gate-blocks-index-infra): 本模块只做 schema + emit +
# 线性扫描 (get_events_by_type / all_records), 【不建】任何索引 / 嵌入类基础设施。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EnrichmentStatus,
    EventCategory,
    EventType,
    Stability,
    SubjectEntityType,
    SubjectRole,
    SupersedeNoveltyType,
    WeightTier,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.payloads.judgment_case import JudgmentCase

if TYPE_CHECKING:
    from collections.abc import Callable

    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

# 判例本体挂在 judgment-case@v1 概念上 (subject 索引: 事件作用于谁) —— 同 DebtRegistered 挂
# against_capability 概念的范式。per-case 追踪靠 payload.source_event_id + supersede 链, 不靠 subject。
_JUDGMENT_CASE_CONCEPT_ID = "judgment-case@v1"
_DEFAULT_ACTOR_ID = "m1x_judgment_case"

# bounded 种子集: 硬上限, 结构性钉死"非全量回填" (非 5000+ 条全量; concept: 十数~数十条量级)。
SEED_HARD_CAP = 99
DEFAULT_SEED_LIMIT = 20


def _scenario_from_nj(after_state: dict[str, object]) -> str:
    """从 NatureJudgmentCaptured.after_state 派生 scenario (这句话在什么场景下说的)。

    优先 scope + meaning (最能刻画场景); 都缺则退回 quote 片段; 再缺给一个明确占位。
    """
    scope = str(after_state.get("scope") or "").strip()
    meaning = str(after_state.get("meaning") or "").strip()
    if scope and meaning:
        return f"{scope} — {meaning}"
    if scope or meaning:
        return scope or meaning
    quote = str(after_state.get("quote") or "").strip()
    if quote:
        return quote[:120]
    return "(scenario 未从原始判断中派生: after_state 缺 scope/meaning/quote)"


def draft_from_nature_judgment(nj: EventRecord) -> JudgmentCase:
    """从一条 NatureJudgmentCaptured 事件建 draft 骨架 (source_event_id 指回它 + 派生 scenario)。"""
    after_state = nj.payload.get("after_state")
    after = after_state if isinstance(after_state, dict) else {}
    return JudgmentCase.draft_skeleton(
        source_event_id=nj.event_id,
        scenario=_scenario_from_nj(after),
    )


def _emit(
    event_log: EventLog,
    case: JudgmentCase,
    *,
    actor_id: str,
    supersede: Supersede,
) -> EventRecord:
    """把一个 JudgmentCase 经 JUDGMENT_CASE_ENRICHED 事件 (path-B) 落账。provenance=system (非交互)。"""
    intent = EventIntent(
        local_intent_id=f"jce-{uuid.uuid4().hex[:12]}",
        event_type=EventType.JUDGMENT_CASE_ENRICHED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload=case.model_dump(mode="json"),
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=supersede,
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=_JUDGMENT_CASE_CONCEPT_ID,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)


def emit_judgment_case(
    event_log: EventLog,
    case: JudgmentCase,
    *,
    actor_id: str = _DEFAULT_ACTOR_ID,
) -> EventRecord:
    """落一条【新】判例 (draft 或 enriched, 非纠正)。is_supersede=false。"""
    return _emit(event_log, case, actor_id=actor_id, supersede=Supersede(is_supersede=False))


def correct_judgment_case(
    event_log: EventLog,
    *,
    prior_event_id: str,
    revised: JudgmentCase,
    novelty: str,
    novelty_type: SupersedeNoveltyType = SupersedeNoveltyType.NATURE_OVERRIDE,
    actor_id: str = _DEFAULT_ACTOR_ID,
) -> EventRecord:
    """纠正一条已 enriched 判例 —— 新版本走【标准 envelope 通用 supersede】指回旧事件 (dc-2)。

    不新造纠正机制: is_supersede=true + superseded_event_id=prior_event_id + novelty/novelty_type,
    与 concept supersede / 任何 supersede 走完全相同的 EventBase.supersede 协议。novelty_type 缺省
    nature_override (被 Nature 后续反应修正), 也可传 corrected_error 等。
    """
    if revised.enrichment_status is not EnrichmentStatus.ENRICHED:
        raise ValueError("纠正产出必须是 enriched 判例 (draft 不能作为纠正版本)")
    return _emit(
        event_log,
        revised,
        actor_id=actor_id,
        supersede=Supersede(
            is_supersede=True,
            superseded_event_id=prior_event_id,
            novelty=novelty,
            novelty_type=novelty_type,
        ),
    )


def _default_seed_enricher(draft: JudgmentCase, nj_after: dict[str, object]) -> JudgmentCase:
    """种子富化器: 从 NJ 确定性派生一个 enriched 判例 (bootstrap 用, 不声称语义精准)。

    这是一个诚实的 bootstrap 种子: 让 enriched 判例先存在, 好让 PreferenceAsTestHarness 回归有得跑。
    真正的语义富化 (人 / 更强模型逐条打磨) 是下游工作, 不是本 seed 的职责。
    """
    meaning = str(nj_after.get("meaning") or "").lower()
    is_value_judgment = bool(nj_after.get("is_value_judgment"))
    if "correct" in meaning or "纠正" in meaning:
        weight = WeightTier.CORRECTION
    elif "decision" in meaning or "决策" in meaning or "brief" in meaning:
        weight = WeightTier.REAL_DECISION
    elif is_value_judgment:
        weight = WeightTier.ACTIVE_PREFERENCE
    else:
        weight = WeightTier.CASUAL_MENTION
    scope = str(nj_after.get("scope") or "").strip() or "unspecified-scope"
    return draft.enrich(
        weight_tier=weight,
        applicable_conditions=[f"scope={scope} 的同类场景"],
        affordance=[draft.scenario],
        stability=Stability.TEMPORARY_TACTIC,
    )


def generate_bounded_seed_set(
    event_log: EventLog,
    *,
    limit: int = DEFAULT_SEED_LIMIT,
    actor_id: str = _DEFAULT_ACTOR_ID,
    enricher: Callable[[JudgmentCase, dict[str, object]], JudgmentCase] | None = None,
) -> list[EventRecord]:
    """产一个 bounded enriched 种子集 —— 富化历史 NatureJudgmentCaptured 的一个【受限子集】。

    非全量回填 (concept: 十数~数十条, 不是 5000+): 实际条数 = min(limit, SEED_HARD_CAP, 可用 NJ 数)。
    每条: draft_from_nature_judgment → enricher → emit_judgment_case (is_supersede=false)。
    返回落账的 EventRecord 列表 (调用方据此断言 [10,100) 且 < 全量 NJ 计数)。
    """
    enrich = enricher if enricher is not None else _default_seed_enricher
    nj_records = event_log.get_events_by_type(EventType.NATURE_JUDGMENT_CAPTURED)
    count = min(limit, SEED_HARD_CAP, len(nj_records))
    emitted: list[EventRecord] = []
    for nj in nj_records[:count]:
        after_state = nj.payload.get("after_state")
        nj_after = after_state if isinstance(after_state, dict) else {}
        draft = draft_from_nature_judgment(nj)
        enriched = enrich(draft, nj_after)
        emitted.append(emit_judgment_case(event_log, enriched, actor_id=actor_id))
    return emitted


__all__ = [
    "DEFAULT_SEED_LIMIT",
    "SEED_HARD_CAP",
    "correct_judgment_case",
    "draft_from_nature_judgment",
    "emit_judgment_case",
    "generate_bounded_seed_set",
]
