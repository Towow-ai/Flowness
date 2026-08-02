"""JudgmentRetrievalProtocol 三层检索 — judgment-retrieval-protocol@v1 (T-JLM-02).

# concept source: judgment-retrieval-protocol@v1 (frozen, plan-interview-oldhand-rework)
#
# 这个模块是判例检索的真路径 (l1 库层): 从账本原始 JUDGMENT_CASE_ENRICHED 事件里, 按三层协议
# (direct / indirect / structural) 检索跟一个查询场景相关的历史判例, 强制一并呈现反例、措辞可撤销。
# 高召回低权威 —— 只负责"想起来", 不裁决"对不对"。
#
# 三层 ↔ JudgmentCase 字段映射 (concept: 四索引对齐三层):
#   direct     表面语义 (字面/关键词匹配)              ← scenario              (这句话在什么场景说的)
#   indirect   意图 (同类场景类比推断)                 ← applicable_conditions (这条在哪些场景适用)
#   structural 桥接 (仅凭结构标签命中、字面无关但结构相同) ← affordance           (能在哪些看似无关的场景被想起来用)
# 优先级 direct > indirect > structural: 一条判例落到它命中的【最强】层。structural 天然"字面无关"——
# 只有 scenario/applicable 都没命中、仅 affordance 命中时才归 structural (concept: bridge = 字面无关但结构相同)。
#
# ledger-read-only (c-3-no-index-infra / preference-as-test-gate-blocks-index-infra 冻结不变量):
# 直调 EventLog 的全量记录读取 API 线性扫原始事件, 【不经】任何类型索引 / 投影 / 二级缓存 / 嵌入向量库。
# 该全量读取 API 自身文档标"test/debug; 用 9 Read APIs"—— 这里【故意】不用被索引化的 get_events_by_type,
# no-index 约束要求检索路径不依赖任何可能漂移的二级投影, 直读账本才是本 gate 未通过前唯一许可的实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import EnrichmentStatus, EventType

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

_OUTPUT = ConfigDict(strict=True, extra="forbid", frozen=True)


class RetrievalTier(StrEnum):
    """三层检索层级 (concept judgment-retrieval-protocol@v1: direct/indirect/structural)。

    本地枚举 (不进 schemas/enums): 检索结果不是落账的事件 payload, 是查询结果本体, 故不需要
    canonical event enum。
    """

    DIRECT = "direct"
    INDIRECT = "indirect"
    STRUCTURAL = "structural"


# 层级排序权重 (ranked list: direct 最强靠前)。
_TIER_RANK = {RetrievalTier.DIRECT: 0, RetrievalTier.INDIRECT: 1, RetrievalTier.STRUCTURAL: 2}


class CounterExampleRef(BaseModel):
    """一条被强制呈现的反例引用 (指向另一条判例的 source_event_id, 可回查原始 quote)。"""

    model_config = _OUTPUT

    case_id: str = Field(min_length=1)
    # 若该反例判例也在账本里, 带出它的 scenario 帮引用者理解"另一侧"是什么; 不在账本则 None。
    scenario: str | None = None


class JudgmentRetrievalHit(BaseModel):
    """检索输出契约的一项 (dc-3): ranked list of {judgment_case_id, tier, counter_examples[], confidence_note}。

    引用者据 judgment_case_id 回查原始判例 quote; counter_examples 是强制呈现的反例侧 (不许被静默丢弃);
    confidence_note 是可撤销措辞 (高召回低权威——想起来、不裁决)。
    """

    model_config = _OUTPUT

    judgment_case_id: str = Field(min_length=1)
    tier: RetrievalTier
    counter_examples: list[CounterExampleRef] = Field(default_factory=list)
    confidence_note: str = Field(min_length=1)


def _features(text: str) -> set[str]:
    """把一段文本降成"特征集"——中英通用的字面/关键词匹配基元。

    中文没有空格分词: 纯 whitespace split 会把整句变成一个巨 token, 什么都匹配不上 (advisor trap 3)。
    故特征 = 空白分词 token (英文关键词) ∪ 去空白后的字符 bigram (中文/无空格子串)。两者并集让
    "字面/关键词匹配"对中英都成立。
    """
    norm = text.lower().strip()
    tokens = {t for t in norm.split() if t}
    compact = "".join(norm.split())
    bigrams = {compact[i : i + 2] for i in range(len(compact) - 1)}
    return tokens | bigrams


def _overlap(query_feats: set[str], field_values: list[str]) -> int:
    """query 特征 与 一组字段值(合并后)特征 的交集大小 —— 越大越相关。"""
    field_feats: set[str] = set()
    for v in field_values:
        field_feats |= _features(v)
    return len(query_feats & field_feats)


def _latest_enriched_cases(event_log: EventLog) -> list[EventRecord]:
    """ledger-read: 全量线性直扫账本取【最新版】enriched 判例记录 (无索引/无投影)。

    两道过滤 (advisor trap 2 + 4):
      ① 只留 enrichment_status=enriched —— JUDGMENT_CASE_ENRICHED 事件名有误导性, draft 也走这个
         事件类型落账 (l1.emit_judgment_case), draft 无行李字段、不该进检索候选池。
      ② 同 source_event_id 的多版本只留 sequence_number 最大 (最新) 的一条 —— 判例被 Nature 后续
         反应纠正走 supersede 链、旧版本仍留在账本 (concept: 同 source_event_id 的多版本)。若不去重,
         会检索出 Nature 已经推翻的旧判断。按 source_event_id 分组取 latest = 天然只留 corrected 版。
    """
    latest_by_source: dict[str, EventRecord] = {}
    for rec in event_log.all_records():
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
    return list(latest_by_source.values())


def _str_list(payload: dict[str, object], key: str) -> list[str]:
    """从 payload 取一个 list[str] 字段 (缺/非法 → 空)。检索直读 payload dict, 不做 strict 模型重建。

    (advisor trap 1: JudgmentCase.model_config strict=True; 账本全量读回的 payload 是 JSON dict、
    enum 已是普通字符串, strict model_validate 会拒 —— 检索只需 5 个字段, 直读 dict 绕开该陷阱。)
    """
    v = payload.get(key)
    if not isinstance(v, list):
        return []
    return [item for item in v if isinstance(item, str)]


def _confidence_note(tier: RetrievalTier, payload: dict[str, object]) -> str:
    """可撤销措辞 (concept: 不许断言式引用, 措辞可撤销 + 高召回低权威)。

    带上 tier 依据 + weight_tier/stability (若有) + non_applicable_conditions ("另一侧"的 JudgmentCase
    原生表达——concept 张力检查在判例世界里的落点), 收尾统一撤销句式。
    """
    tier_reason = {
        RetrievalTier.DIRECT: "字面/关键词直接相关",
        RetrievalTier.INDIRECT: "同类场景类比 (适用条件相近)",
        RetrievalTier.STRUCTURAL: "字面无关但结构相同 (仅凭 affordance 命中)",
    }[tier]
    parts = [f"命中依据: {tier_reason}"]
    weight = payload.get("weight_tier")
    stability = payload.get("stability")
    if isinstance(weight, str):
        parts.append(f"权重={weight}")
    if isinstance(stability, str):
        parts.append(f"稳定性={stability}")
    non_applicable = _str_list(payload, "non_applicable_conditions")
    if non_applicable:
        parts.append("明确不适用于: " + "; ".join(non_applicable))
    parts.append("本协议只负责想起来、不裁决对错; 按此类推, 除非不适用请指出。")
    return " | ".join(parts)


def _resolve_counter_examples(
    counter_ids: list[str],
    scenario_by_source: dict[str, str],
) -> list[CounterExampleRef]:
    """把 counter_example_case_ids 解析成引用 (带出对方 scenario 若在账本)。

    强制呈现 (judgment-retrieval-counter-example-mandatory-surface): 只要 counter_ids 非空, 全部呈现——
    【不】按是否匹配查询过滤 (反例的意义正是"支持结论的另一侧", 常常字面不匹配查询)。
    """
    return [
        CounterExampleRef(case_id=cid, scenario=scenario_by_source.get(cid))
        for cid in counter_ids
    ]


def retrieve_judgments(
    event_log: EventLog,
    query: str,
    *,
    limit: int | None = None,
) -> list[JudgmentRetrievalHit]:
    """三层检索: 对 query 场景, 从账本 enriched 判例里检出相关判例的 ranked list。

    direct(scenario) > indirect(applicable_conditions) > structural(affordance)。任一命中判例若带
    counter_example_case_ids, 反例一并呈现 (强制、不静默丢弃)。ranked by (tier 强度, 匹配分, id)。

    Args:
        event_log: 账本 (全量线性直读, 无索引/无投影 —— ledger-read-only)。
        query: 查询场景描述 (自然语言)。
        limit: 截取前 N 条 (None = 全返回)。

    Returns:
        JudgmentRetrievalHit 的 ranked list (契约: judgment_case_id / tier / counter_examples[] / confidence_note)。
    """
    query_feats = _features(query)
    if not query_feats:
        return []

    records = _latest_enriched_cases(event_log)
    scenario_by_source = {
        rec.payload["source_event_id"]: str(rec.payload.get("scenario") or "")
        for rec in records
    }

    scored: list[tuple[int, int, JudgmentRetrievalHit]] = []
    for rec in records:
        payload = rec.payload
        source_id = str(payload["source_event_id"])
        scenario = [str(payload.get("scenario") or "")]
        applicable = _str_list(payload, "applicable_conditions")
        affordance = _str_list(payload, "affordance")

        # 三层按优先级选【最强】命中层 —— structural 只有前两层都不命中时才成立 (字面无关)。
        direct_score = _overlap(query_feats, scenario)
        indirect_score = _overlap(query_feats, applicable)
        structural_score = _overlap(query_feats, affordance)
        if direct_score > 0:
            tier, score = RetrievalTier.DIRECT, direct_score
        elif indirect_score > 0:
            tier, score = RetrievalTier.INDIRECT, indirect_score
        elif structural_score > 0:
            tier, score = RetrievalTier.STRUCTURAL, structural_score
        else:
            continue

        hit = JudgmentRetrievalHit(
            judgment_case_id=source_id,
            tier=tier,
            counter_examples=_resolve_counter_examples(
                _str_list(payload, "counter_example_case_ids"),
                scenario_by_source,
            ),
            confidence_note=_confidence_note(tier, payload),
        )
        scored.append((_TIER_RANK[tier], score, hit))

    # ranked: tier 强度升序 (direct 先), 同层匹配分降序, 再按 id 稳定排序。
    scored.sort(key=lambda t: (t[0], -t[1], t[2].judgment_case_id))
    hits = [t[2] for t in scored]
    return hits if limit is None else hits[:limit]


__all__ = [
    "CounterExampleRef",
    "JudgmentRetrievalHit",
    "RetrievalTier",
    "retrieve_judgments",
]
