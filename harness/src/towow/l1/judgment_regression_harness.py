"""PreferenceAsTestHarness 判例回归验证门 — preference-as-test-harness@v1 (T-JLM-03).

# concept source: preference-as-test-harness@v1 (frozen, plan-interview-oldhand-rework)
#
# 这个模块是判例检索机制 (judgment-retrieval-protocol@v1) 正式投用前的语义正确性回归门 (l1 库层):
# 从账本历史 enriched 判例里【留出一批 holdout】(评估期间绝不进入被评估的检索候选池); 对每条留出
# 判例, 把它的 scenario 还原成"如果现在遇到类似场景, Nature 会怎么判"的预测任务, 喂给按
# judgment-retrieval-protocol@v1 跑起来的【真检索路径】(不用留出判例本身), 产出预测; 跟该判例
# 真实记录的 quote/meaning 的【方向】对答案 (不要求措辞一致); 方向一致率 ≥ pass_threshold → 门通过。
#
# 两条冻结不变量 (engineering-consensus freeze, plan-interview-oldhand-rework):
#   preference-as-test-holdout-exclusion  — holdout 判例在评估运行期间【绝不出现在被评估的检索
#     候选池】中 (交集为空)。本实现用一个只暴露 all_records 读取口的候选池视图 (_PoolView), 把 holdout
#     判例 (按 source_event_id 身份, 含其任何旧版本) 从喂给检索的记录里【物理剔除】—— 是"候选池不含"
#     而非"结果层过滤", 检索根本看不到它们。
#   preference-as-test-gate-blocks-index-infra — 无论评分结果如何, 本 task 【不建】任何索引/嵌入
#     基础设施; 未达标只输出"仅支持提示词内人工按三层检索原则查账本"的结论。达标不代表本 task 去建
#     索引 (索引投资是独立的后续 batch 决策)。
#
# c-3-no-index-infra: 回归实现【一次性直读 EventLog 全量记录读取口线性扫原始事件】, 不引入 embedding /
# 向量库 / 二级索引投影 —— 与被测的 judgment_retrieval.py (ledger-read-only) 同源。全量记录只在
# evaluate_regression 顶层读【一次】, 派生给各 helper (单次扫账本, 也是唯一的账本接触点)。
#
# pass_threshold≥0.70 是占位值 (concept: 待 Nature 校准, 本概念先占位不锁死), threshold_is_placeholder
# 恒 True。方向函数 (_direction_of) 是【诚实的占位代理】: 从 quote/meaning 抽粗粒度极性 (affirm/
# negate/neutral), 不声称语义精准 —— 真正的方向语义 (更强模型/人逐条标) 与阈值校准同属后续 batch,
# 已登 deferral 债 (见 work complete envelope)。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

from towow.l1.judgment_retrieval import retrieve_judgments
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EnrichmentStatus,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.payloads.judgment_case import (
    GateResultState,
    JudgmentRegressionCaseResult,
    JudgmentRegressionEvaluated,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

_CONCEPT_ID = "preference-as-test-harness@v1"
_DEFAULT_ACTOR_ID = "m1x_judgment_regression"

# pass_threshold 占位 (concept 冻结: ≥70% 待 Nature 校准, 不锁死) + holdout 留出比例。
DEFAULT_PASS_THRESHOLD = 0.70
DEFAULT_HOLDOUT_RATIO = 0.30

# gate_result_state 两态 (concept: passed | not_passed)。
GATE_PASSED: GateResultState = "passed"
GATE_NOT_PASSED: GateResultState = "not_passed"

# 无检索命中时的预测方向哨兵 (记为方向不一致)。
_NO_PREDICTION = "none"

# 方向极性关键词 (中英混排; 诚实占位代理, 非语义精准)。negate 优先于 affirm。
_NEGATE_MARKERS = (
    "不要", "不应", "别", "避免", "拒", "停", "反对", "禁止",
    "don't", "do not", "never", "avoid", "reject", "no-go",
)
_AFFIRM_MARKERS = (
    "应该", "必须", "要", "偏好", "采用", "坚持", "优先",
    "should", "must", "prefer", "adopt", "decision", "决策",
)


class _PoolView:
    """最小全量记录视图 —— 给 retrieve_judgments 只喂【候选池】记录 (holdout 物理不进池)。

    retrieve_judgments 只经 EventLog 的全量记录读取口读账本 (已核验其唯一 EventLog 接触点), 故一个
    仅暴露该读取口的视图即可让 holdout 判例的 JudgmentCaseEnriched 事件【根本不出现在检索候选池】
    —— 落实冻结不变量 preference-as-test-holdout-exclusion (交集为空, 非结果层过滤)。
    """

    def __init__(self, records: Sequence[EventRecord]) -> None:
        self._records = list(records)

    def all_records(self) -> list[EventRecord]:
        return self._records


def _latest_enriched_cases(records: list[EventRecord]) -> list[EventRecord]:
    """从全量记录取【最新版】enriched 判例 (无索引/无投影, 同 judgment_retrieval)。

    两道过滤与 retrieval 一致: 只留 enrichment_status=enriched; 同 source_event_id 只留 sequence
    最大 (最新) 一条 (supersede 后的 corrected 版)。按 sequence_number 稳定排序, 让 holdout 划分确定。
    """
    latest_by_source: dict[str, EventRecord] = {}
    for rec in records:
        if rec.event_type is not EventType.JUDGMENT_CASE_ENRICHED:
            continue
        payload = rec.payload
        if payload.get("enrichment_status") != EnrichmentStatus.ENRICHED.value:
            continue
        source_id = payload.get("source_event_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        prior = latest_by_source.get(source_id)
        if prior is None or rec.sequence_number > prior.sequence_number:
            latest_by_source[source_id] = rec
    return sorted(latest_by_source.values(), key=lambda r: r.sequence_number)


def _partition_holdout(
    cases: list[EventRecord],
    *,
    holdout_ratio: float,
) -> tuple[list[EventRecord], list[EventRecord]]:
    """确定性把 enriched 判例划成 (holdout, pool) —— 均匀取样, 保证两侧都可为空的边界安全。

    n_holdout = round(len*ratio), 夹在 [1, len-1] (留出非空且候选池非空) —— 除非 len<2 无法两分:
    len==0 → 两侧皆空; len==1 → 无法既留出又留候选, holdout=空、pool=该条 (无留出 → 门自然 not_passed)。
    均匀跨步取 holdout, 让留出样本铺开在序列上 (不聚在一头)。
    """
    n = len(cases)
    min_splittable = 2  # 至少 1 留出 + 1 候选 才能两分。
    if n < min_splittable:
        return [], list(cases)
    n_holdout = round(n * holdout_ratio)
    n_holdout = max(1, min(n_holdout, n - 1))
    stride = n / n_holdout
    holdout_idx = {min(n - 1, round(i * stride)) for i in range(n_holdout)}
    holdout = [c for i, c in enumerate(cases) if i in holdout_idx]
    pool = [c for i, c in enumerate(cases) if i not in holdout_idx]
    return holdout, pool


def _nj_meaning_index(records: list[EventRecord]) -> dict[str, dict[str, object]]:
    """NatureJudgmentCaptured event_id → after_state (取 quote/meaning 定方向)。全量直读, 无索引。"""
    index: dict[str, dict[str, object]] = {}
    for rec in records:
        if rec.event_type is not EventType.NATURE_JUDGMENT_CAPTURED:
            continue
        after = rec.payload.get("after_state")
        if isinstance(after, dict):
            index[rec.event_id] = after
    return index


def _direction_of(nj_after: dict[str, object] | None) -> str:
    """从一条判断的 quote/meaning 抽粗粒度方向极性 (诚实占位代理, 非语义精准)。

    affirm (主张/采用) / negate (反对/避免) / neutral。negate 优先 (显式否定信号更强)。真实记录缺失
    (源 NJ 找不到) → neutral。这是 concept 明确占位、待后续 batch 强化的部分 (见模块 docstring)。
    """
    if not nj_after:
        return "neutral"
    text = f"{nj_after.get('meaning') or ''} {nj_after.get('quote') or ''}".lower()
    neg = sum(text.count(m) for m in _NEGATE_MARKERS)
    pos = sum(text.count(m) for m in _AFFIRM_MARKERS)
    if neg > pos:
        return "negate"
    if pos > neg:
        return "affirm"
    return "neutral"


def evaluate_regression(
    event_log: EventLog,
    *,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    holdout_ratio: float = DEFAULT_HOLDOUT_RATIO,
) -> JudgmentRegressionEvaluated:
    """跑一次回归 (纯计算, 不落账): 留出 holdout → 候选池检索预测 → 方向对答案 → 定 gate_result_state。

    对每条 holdout: 用其 scenario 查【剔除 holdout 的候选池】, 取最强命中判例, 其真实方向作为预测,
    跟 holdout 自己的真实方向比。方向一致率 = matched/holdout_size (holdout 空 → 0.0)。
    gate_result_state=passed 当且仅当 holdout_size>0 且 score≥pass_threshold。

    冻结不变量 preference-as-test-holdout-exclusion 在此落实: 检索只看候选池视图 (_PoolView),
    holdout 判例 (按 source_event_id 身份, 含旧版本) 从记录里物理剔除。
    """
    # 唯一账本接触点: 全量记录只读【一次】, 派生给各 helper (单次线性扫, 无索引/无投影)。
    records = event_log.all_records()
    cases = _latest_enriched_cases(records)
    nj_index = _nj_meaning_index(records)
    holdout, _pool_cases = _partition_holdout(cases, holdout_ratio=holdout_ratio)

    holdout_source_ids = {str(c.payload["source_event_id"]) for c in holdout}
    pool_records = _pool_records_excluding(records, holdout_source_ids)
    pool_view = cast("EventLog", _PoolView(pool_records))
    candidate_pool_size = sum(
        1
        for r in pool_records
        if r.event_type is EventType.JUDGMENT_CASE_ENRICHED
        and r.payload.get("enrichment_status") == EnrichmentStatus.ENRICHED.value
    )

    case_results: list[JudgmentRegressionCaseResult] = []
    matched = 0
    for case in holdout:
        source_id = str(case.payload["source_event_id"])
        actual = _direction_of(nj_index.get(source_id))
        scenario = str(case.payload.get("scenario") or "")
        hits = retrieve_judgments(pool_view, scenario, limit=1)
        if hits:
            retrieved_id: str | None = hits[0].judgment_case_id
            predicted = _direction_of(nj_index.get(str(retrieved_id)))
        else:
            retrieved_id, predicted = None, _NO_PREDICTION
        is_match = predicted != _NO_PREDICTION and predicted == actual
        if is_match:
            matched += 1
        case_results.append(
            JudgmentRegressionCaseResult(
                holdout_case_id=source_id,
                actual_direction=actual,
                predicted_direction=predicted,
                retrieved_case_id=retrieved_id,
                matched=is_match,
            ),
        )

    holdout_size = len(holdout)
    score = matched / holdout_size if holdout_size else 0.0
    passed = holdout_size > 0 and score >= pass_threshold
    return JudgmentRegressionEvaluated(
        score=score,
        pass_threshold=pass_threshold,
        gate_result_state=GATE_PASSED if passed else GATE_NOT_PASSED,
        holdout_size=holdout_size,
        candidate_pool_size=candidate_pool_size,
        matched_count=matched,
        threshold_is_placeholder=True,
        case_results=case_results,
    )


def _pool_records_excluding(
    records: list[EventRecord],
    holdout_source_ids: set[str],
) -> list[EventRecord]:
    """候选池记录 = 全量记录剔除【任何 source_event_id ∈ holdout 的 JudgmentCaseEnriched 事件】。

    按 source_event_id 身份剔除 (含被 supersede 的旧版本) —— 保证被留出的那条判断整体不在候选池,
    杜绝旧版本泄漏回池 (preference-as-test-holdout-exclusion 交集为空的强形式)。
    """
    out: list[EventRecord] = []
    for rec in records:
        if rec.event_type is EventType.JUDGMENT_CASE_ENRICHED:
            sid = rec.payload.get("source_event_id")
            if isinstance(sid, str) and sid in holdout_source_ids:
                continue
        out.append(rec)
    return out


def emit_regression_result(
    event_log: EventLog,
    result: JudgmentRegressionEvaluated,
    *,
    actor_id: str = _DEFAULT_ACTOR_ID,
) -> EventRecord:
    """把一次回归结论经 JudgmentRegressionEvaluated 事件 (path-B write_direct) 落账。

    provenance=system (非交互): 回归是机器对账本的加工/度量, 不是 Nature 本人的新判断 —— 同
    JudgmentCaseEnriched 范式。挂 preference-as-test-harness@v1 概念 (subject: 事件作用于谁)。
    """
    intent = EventIntent(
        local_intent_id=f"jre-{uuid.uuid4().hex[:12]}",
        event_type=EventType.JUDGMENT_REGRESSION_EVALUATED,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload=result.model_dump(mode="json"),
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=_CONCEPT_ID,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent)


def run_and_emit_regression(
    event_log: EventLog,
    *,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    holdout_ratio: float = DEFAULT_HOLDOUT_RATIO,
    actor_id: str = _DEFAULT_ACTOR_ID,
) -> tuple[JudgmentRegressionEvaluated, EventRecord]:
    """跑一次回归并把结论落账 —— CLI / live-fire 的一站式入口。返回 (结论, 落账事件)。"""
    result = evaluate_regression(
        event_log, pass_threshold=pass_threshold, holdout_ratio=holdout_ratio,
    )
    rec = emit_regression_result(event_log, result, actor_id=actor_id)
    return result, rec


__all__ = [
    "DEFAULT_HOLDOUT_RATIO",
    "DEFAULT_PASS_THRESHOLD",
    "GATE_NOT_PASSED",
    "GATE_PASSED",
    "emit_regression_result",
    "evaluate_regression",
    "run_and_emit_regression",
]
