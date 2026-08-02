"""R01 — 概念创建相关性检查门 (ConceptCreate relatedness check gate).

# concept source: c-concept-create-relatedness-check-gate@v1
#   建新概念 (ConceptCreated) 过 commit gate 时跑相关性检查: 查它跟既有概念有没有共变
#   (evolutionary-coupling-mining) 或语义关联。**不是强制挂父——是 CHECK**。早期真全新的多,
#   标注理由放行; 后期真孤立的极少, 孤立=可疑, 标出来要求找回该有的关联。owner 定: 创建要硬,
#   fail-closed, 产真实事件留痕。

是 @birth-time-validation@v1 不变量的一个具名实例 (复用其不变量): 出生口 (commit gate 收
ConceptCreated) 校验对象合法性, 不合法即拦, 不靠出生后下游兜底。本实例的"合法性"= **相关性**
——新概念能落到既有概念网 (有声明关联 / 语义关联 / 共变关联), 而非孤立漂浮。出生后才发现一堆
互不关联的孤岛概念再回头补边, 是昂贵的下游兜底; 出生口拒一个真孤立、无理由的新概念是廉价前移。

## 不是强制挂父——是 CHECK (概念 "不是强制挂父" 的字面兑现)

任一相关性信号成立即判 related, 不要求全有, 更不强制声明父概念:

1. **声明关联** (declared): ConceptCreated 的 semantic_annotation.affected_concept_ids 指向某既有
   (或同批共建) 概念, 或同批 ConceptEdgeAdded 把新概念连到某既有概念。
2. **语义关联** (semantic): 新概念 name+definition 与某既有概念的词项重叠 ≥ 阈值。本 harness 包内
   **无 HDC/向量编码器** (towow 产品栈的 HDC 在 backend, 不在本包) —— 故用透明的 token-overlap
   词项代理 (`LexicalRelatednessScorer`), 并留 `RelatednessScorer` Protocol 接口: 一旦本包接入
   真 HDC/向量编码器, 直接换 scorer 即可, 不动门逻辑。代理而非假装是 HDC, 是诚实账。
3. **共变关联** (coupling): 复用 T-R01-2 `EvolutionaryCouplingMiner` 的 `MiningResult` —— 新概念
   映射到的耦合节点与某既有概念有 corresponding 耦合边。出生那一刻新概念通常无变更史 (无共变),
   故共变是给"有历史的节点"准备的可选信号: caller 有现成 MiningResult 就传进来, 不在每次提交的
   热路径上跑 git log (那昂贵且出生口意义不大)。不造轮子——直接吃 T-R01-2 的产物。

## 放行豁免 (孤立但合法 —— "标注理由放行")

三信号皆无 → 孤立。孤立不直接拒, 还有两道豁免:

- **早期图未成熟**: 既有概念 < `maturity_threshold` → 早期真全新的多, 孤立放行 (留 notice 不拒)。
- **标注理由**: semantic_annotation 显式声明 genuinely-new (relationship_type ∈ 全新标记) ——
   作者主动断言"这是真全新, 不是漏连", 放行 (留 notice)。这是"不强制挂父"的逃生阀: 永远可以靠
   声明真全新通过, 门从不逼你挂一个假父。

孤立 + 图已成熟 + 无理由 → **拒** (`concept_isolated_no_relatedness`, fail-closed): 后期孤立=可疑,
出生口把它标出来, 要求声明 affected_concept_ids / 加一条 ConceptEdge / 或主动断言真全新。

## ConceptCreated 双 shape 消费 (@concept:c-conceptcreated-dual-shape-consumption@v1, f-r01-5)

本门提取概念 (新概念 + 既有概念枚举) 必须同时认 ConceptCreated 的两个 shape: nested (M-0.1/M-1.2
`after_state`) 与 flat (M-1.1 §5.5 `ConceptSeedCreatedPayload`, 概念数据在 payload 顶层, brief 种子 +
bootstrap planner 的真生产 emit 路径)。只读 after_state → flat 孤立概念绕过本门被静默放行 (f-r01-5)。
兜底见 `_concept_src` (对齐 projection.py `_reduce_concept_graph` 既定 pattern)。

## 留痕

本门是纯判定函数, 产 `ConceptRelatednessResult`。接进 commit gate `_run_checks` 时: 拒 →
CommitRejected (rejection_type 携 failure_reason/evidence); 放行 → CommitAccepted.checks_passed
记一条 + notices。**真实事件留痕**走 gate 既有的 accept/reject sentinel, 不另造事件 (与其它 gate
check 同构)。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from towow.l1.cia.evolutionary_coupling import MiningResult

# ── 默认阈值 ──────────────────────────────────────────────────────────────────
# 既有概念 ≥ 此数 → 概念图判"成熟", 孤立新概念=可疑 (后期). 低于 → 早期, 孤立放行.
DEFAULT_MATURITY_THRESHOLD = 24
# token Jaccard ≥ 此值 → 判与某既有概念语义关联. 标定: 共享一两个领域术语即足以"非孤立",
# 阈值取低 (宽松) —— 本门要抓的是"真孤立", 不是"关联弱", 宁可放过弱关联也别误拒。
DEFAULT_SEMANTIC_MIN_OVERLAP = 0.10

# "标注理由放行" 的显式全新断言标记 (relationship_type 取此值之一 = 作者主动声明真全新).
# 故意要求显式标记 (而非"有 explanation 即可") —— 否则门形同虚设 (几乎每个概念都带 explanation).
_GENUINELY_NEW_MARKERS = frozenset(
    {"genuinely_new", "genuinely-new", "net_new", "net-new", "brand_new", "brand-new", "全新", "真全新"},
)

_CONCEPT_CREATED = "ConceptCreated"
_CONCEPT_EDGE_ADDED = "ConceptEdgeAdded"
_CONCEPT_GRAPH_PROPOSAL = "ConceptGraphProposal"
_TRANSITION_SUPERSEDED = "superseded"


# ── 相关性打分器 (semantic 信号的可替换接口) ────────────────────────────────────
class RelatednessScorer(Protocol):
    """语义相关性打分器。默认 `LexicalRelatednessScorer` (词项代理); 本包接入真 HDC/向量
    编码器后换实现即可, 门逻辑不变。返回 [0,1], 越大越相关。"""

    def relatedness(self, text_a: str, text_b: str) -> float: ...


# CJK 无空格分词: 取连续中文字符的 bigram + ASCII 词 (len≥3), 共同构成词项集。
_ASCII_WORD = re.compile(r"[a-z][a-z0-9_\-]{2,}")
_CJK_RUN = re.compile(r"[一-鿿]+")


def _semantic_tokens(text: str) -> frozenset[str]:
    text = (text or "").lower()
    tokens: set[str] = set(_ASCII_WORD.findall(text))
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


@dataclass(frozen=True)
class LexicalRelatednessScorer:
    """词项重叠 (token Jaccard) 代理 —— 本包无 HDC 时的诚实 semantic 信号 (非假装是 HDC)。"""

    def relatedness(self, text_a: str, text_b: str) -> float:
        return _jaccard(_semantic_tokens(text_a), _semantic_tokens(text_b))


_DEFAULT_SCORER = LexicalRelatednessScorer()


# ── 数据载体 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ExistingConcept:
    """既有概念的最小投影 (供相关性比对)。"""

    concept_id: str
    name: str = ""
    definition: str = ""

    @property
    def corpus(self) -> str:
        return f"{self.name} {self.definition}".strip()


@dataclass(frozen=True)
class _NewConcept:
    """本批待出生的 ConceptCreated 提取出的最小信息。"""

    concept_id: str
    name: str
    definition: str
    affected_concept_ids: frozenset[str]
    relationship_type: str | None
    explanation: str | None

    @property
    def corpus(self) -> str:
        return f"{self.name} {self.definition}".strip()

    @property
    def asserts_genuinely_new(self) -> bool:
        rt = (self.relationship_type or "").strip().lower()
        return rt in _GENUINELY_NEW_MARKERS


@dataclass(frozen=True)
class RelatednessFinding:
    """单个新概念的相关性判定。signal: declared / semantic / coupling / isolated_allowed_early /
    isolated_allowed_justified / isolated_violation。"""

    concept_id: str
    related: bool
    signal: str
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConceptRelatednessResult:
    """门的判定产物 —— 字段对齐 gate `_run_checks` 其它 check 的消费形态。"""

    passed: bool
    applicable: bool = False  # 本批是否含 ConceptCreated (无则 not_applicable)
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    findings: list[RelatednessFinding] = field(default_factory=list)


# ── 从 domain_intents 提取 (gate 路径用; 接受 EventIntent 或任意 .event_type/.payload 鸭子) ──
def _event_type_name(intent: Any) -> str:
    et = getattr(intent, "event_type", None)
    if et is None and isinstance(intent, dict):
        et = intent.get("event_type")
    return str(getattr(et, "value", et) or "")


def _payload_of(intent: Any) -> dict[str, Any]:
    p = getattr(intent, "payload", None)
    if p is None and isinstance(intent, dict):
        p = intent.get("payload")
    return p if isinstance(p, dict) else {}


def _concept_src(payload: dict[str, Any]) -> dict[str, Any]:
    """ConceptCreated 双 shape 兜底 (@concept:c-conceptcreated-dual-shape-consumption@v1)。

    ConceptCreated 真实并存两 shape: **nested** (M-0.1/M-1.2 `ConceptCreatedPayload`, 概念数据在
    `after_state`) 与 **flat** (M-1.1 §5.5 `ConceptSeedCreatedPayload`, brief 种子 + bootstrap planner
    的**真生产 emit 路径**, 概念数据在 payload 顶层 `concept_id/concept_name/definition`, **无**
    `after_state`)。任一消费 ConceptCreated 提取概念数据的代码必须同时认两 shape, 否则 flat 种子被
    静默丢 (f-r01-5: 只读 after_state 的相关性门把 flat 孤立概念无条件放行)。

    兜底规则对齐既定 pattern (`projection.py` `_reduce_concept_graph` T-L1-02/RUN-032):
    `src = after_state if after_state else payload` —— after_state 是非空 dict 就读它, 否则回退读
    payload 顶层。字段优先级: `concept_id` / `name`||`concept_name` / `definition`。
    """
    after = payload.get("after_state")
    return after if isinstance(after, dict) and after else payload


def _concept_id_of(src: dict[str, Any]) -> str:
    cid = src.get("concept_id")
    return cid if isinstance(cid, str) else ""


def _concept_name_of(src: dict[str, Any]) -> str:
    # nested 用 `name`, flat (ConceptSeedCreatedPayload) 用 `concept_name`.
    return str(src.get("name", src.get("concept_name", "")))


def _extract_new_concepts(domain_intents: Iterable[Any]) -> list[_NewConcept]:
    out: list[_NewConcept] = []
    for intent in domain_intents:
        if _event_type_name(intent) != _CONCEPT_CREATED:
            continue
        payload = _payload_of(intent)
        src = _concept_src(payload)  # 双 shape: nested→after_state / flat→payload 顶层
        cid = _concept_id_of(src)
        if not cid:
            continue
        # semantic_annotation 始终在 payload 顶层 (nested 时与 after_state 同级; flat 种子无此字段).
        annotation = payload.get("semantic_annotation")
        annotation = annotation if isinstance(annotation, dict) else {}
        affected = annotation.get("affected_concept_ids")
        affected_set = frozenset(str(c) for c in affected) if isinstance(affected, list) else frozenset()
        out.append(
            _NewConcept(
                concept_id=cid,
                name=_concept_name_of(src),
                definition=str(src.get("definition", "")),
                affected_concept_ids=affected_set,
                relationship_type=annotation.get("relationship_type"),
                explanation=annotation.get("explanation"),
            ),
        )
    return out


def _extract_edge_linked_concepts(domain_intents: Iterable[Any]) -> set[str]:
    """同批 ConceptEdgeAdded 涉及的 concept_id (新概念若在此集中 = 声明了关联)。"""
    linked: set[str] = set()
    for intent in domain_intents:
        if _event_type_name(intent) != _CONCEPT_EDGE_ADDED:
            continue
        after = _payload_of(intent).get("after_state")
        if not isinstance(after, dict):
            continue
        for key in ("source_concept_id", "target_concept_id"):
            cid = after.get(key)
            if cid:
                linked.add(str(cid))
    return linked


# ── 门主逻辑 ──────────────────────────────────────────────────────────────────
def check_concept_create_relatedness(
    domain_intents: Iterable[Any],
    existing_concepts: Sequence[ExistingConcept],
    *,
    maturity_threshold: int = DEFAULT_MATURITY_THRESHOLD,
    semantic_min_overlap: float = DEFAULT_SEMANTIC_MIN_OVERLAP,
    coupling: MiningResult | None = None,
    scorer: RelatednessScorer | None = None,
) -> ConceptRelatednessResult:
    """跑相关性检查门。

    domain_intents: 本批待提交事件 (gate 给 EventIntent; 测试可给鸭子). 只看 ConceptCreated /
        ConceptEdgeAdded。无 ConceptCreated → applicable=False, passed=True (not_applicable)。
    existing_concepts: 既有概念 (已提交的, **不含**本批待出生的)。其数量是图成熟度信号。
    coupling: 可选 T-R01-2 共变挖掘产物; 提供则启用共变信号 (复用, 不造轮子)。
    scorer: 可选语义打分器; 默认词项代理 (本包无 HDC)。
    """
    new_concepts = _extract_new_concepts(domain_intents)
    if not new_concepts:
        return ConceptRelatednessResult(passed=True, applicable=False)

    scorer = scorer or _DEFAULT_SCORER
    existing_by_id = {c.concept_id: c for c in existing_concepts}
    existing_ids = set(existing_by_id)
    edge_linked = _extract_edge_linked_concepts(domain_intents)
    # 声明关联的合法目标 = 既有概念 ∪ 同批共建的其它概念 (前向引用也算两者关联)。
    co_created_ids = {nc.concept_id for nc in new_concepts}
    declarable_targets = existing_ids | co_created_ids
    mature = len(existing_concepts) >= maturity_threshold

    findings: list[RelatednessFinding] = []
    notices: list[str] = []
    violations: list[str] = []

    for nc in new_concepts:
        finding = _classify_one(
            nc,
            existing_concepts=existing_concepts,
            declarable_targets=declarable_targets,
            edge_linked=edge_linked,
            coupling=coupling,
            scorer=scorer,
            semantic_min_overlap=semantic_min_overlap,
            mature=mature,
        )
        findings.append(finding)
        if finding.related:
            continue
        if finding.signal == "isolated_violation":
            violations.append(nc.concept_id)
        else:
            # 放行豁免 (early / justified) — 留痕但不拒。
            notices.append(
                f"concept_relatedness: {nc.concept_id} 孤立但放行 ({finding.signal}; "
                f"existing={len(existing_concepts)})",
            )

    if violations:
        return ConceptRelatednessResult(
            passed=False,
            applicable=True,
            failure_reason="concept_isolated_no_relatedness",
            failure_evidence={
                "isolated_concepts": ",".join(violations),
                "existing_concept_count": str(len(existing_concepts)),
                "maturity_threshold": str(maturity_threshold),
                "remedy": (
                    "概念图已成熟却建出孤立概念: 声明 semantic_annotation.affected_concept_ids 指向"
                    "相关既有概念, 或同批加一条 ConceptEdgeAdded, 或确为真全新则把 relationship_type "
                    "标为 genuinely_new 主动断言 (标注理由放行)。"
                ),
            },
            notices=notices,
            findings=findings,
        )

    return ConceptRelatednessResult(passed=True, applicable=True, notices=notices, findings=findings)


def _classify_one(
    nc: _NewConcept,
    *,
    existing_concepts: Sequence[ExistingConcept],
    declarable_targets: set[str],
    edge_linked: set[str],
    coupling: MiningResult | None,
    scorer: RelatednessScorer,
    semantic_min_overlap: float,
    mature: bool,
) -> RelatednessFinding:
    # 信号 1: 声明关联 (affected_concept_ids 命中可声明目标, 或同批 edge 连到本概念)。
    declared = (nc.affected_concept_ids & declarable_targets) - {nc.concept_id}
    if declared:
        return RelatednessFinding(nc.concept_id, True, "declared", {"targets": ",".join(sorted(declared))})
    if nc.concept_id in edge_linked:
        return RelatednessFinding(nc.concept_id, True, "declared", {"via": "concept_edge_added"})

    # 信号 2: 语义关联 (与某既有概念词项重叠 ≥ 阈值)。
    best_id, best_score = "", 0.0
    corpus = nc.corpus
    if corpus:
        for ec in existing_concepts:
            score = scorer.relatedness(corpus, ec.corpus)
            if score > best_score:
                best_id, best_score = ec.concept_id, score
    if best_score >= semantic_min_overlap:
        return RelatednessFinding(
            nc.concept_id, True, "semantic", {"nearest": best_id, "overlap": f"{best_score:.3f}"},
        )

    # 信号 3: 共变关联 (复用 T-R01-2; 仅在 caller 传了 MiningResult 时启用)。
    if coupling is not None:
        coupled = [
            e.target
            for e in coupling.neighbors(nc.concept_id, only_corresponding=True)
            if e.target in declarable_targets
        ]
        if coupled:
            return RelatednessFinding(
                nc.concept_id, True, "coupling", {"coupled_to": ",".join(sorted(set(coupled)))},
            )

    # 三信号皆无 → 孤立。豁免: 早期图 / 主动断言真全新。
    if not mature:
        return RelatednessFinding(nc.concept_id, False, "isolated_allowed_early")
    if nc.asserts_genuinely_new:
        return RelatednessFinding(
            nc.concept_id, False, "isolated_allowed_justified", {"relationship_type": str(nc.relationship_type)},
        )
    # 孤立 + 成熟 + 无理由 → 违规 (fail-closed)。
    return RelatednessFinding(
        nc.concept_id,
        False,
        "isolated_violation",
        {"nearest_semantic": best_id, "nearest_overlap": f"{best_score:.3f}"},
    )


# ── 退役/替换检测 (枚举既有概念时排除真退役的旧概念) ──────────────────────────────
def _supersede_field(rec: Any, name: str) -> Any:
    s = getattr(rec, "supersede", None)
    if s is None and isinstance(rec, dict):
        s = rec.get("supersede")
    if s is None:
        return None
    if isinstance(s, dict):
        return s.get(name)
    return getattr(s, name, None)


def _subjects_of(rec: Any) -> list[Any]:
    subs = getattr(rec, "subjects", None)
    if subs is None and isinstance(rec, dict):
        subs = rec.get("subjects")
    return list(subs) if isinstance(subs, (list, tuple)) else []


def _subject_field(subject: Any, name: str) -> Any:
    if isinstance(subject, dict):
        return subject.get(name)
    return getattr(subject, name, None)


def _event_id_of(rec: Any) -> str:
    eid = getattr(rec, "event_id", None)
    if eid is None and isinstance(rec, dict):
        eid = rec.get("event_id")
    return str(eid) if isinstance(eid, str) else ""


def _retired_concept_ids(records: Sequence[Any]) -> set[str]:
    """已提交记录里被**真退役/替换**掉的旧 concept_id —— 不该再当有效关联目标。

    诊断 (f-r01-5 第二面): 相关性门枚举既有概念时不过滤退役概念 → 新概念可以扎根一个已死概念
    蒙混过关。这里识别两种退役形态 (对齐 `concept_retire_gate._extract_retiring` 的口径):

      1. **ConceptGraphProposal** (`is_supersede` 或 `transition_type=superseded`): 旧 id 在
         `proposed_changes[].entity_id` (或 subject), 新 id 在 `new_value.replacement_concept_id`。
      2. **ConceptCreated(`is_supersede`)** republish: 旧 id 经 `supersede.superseded_event_id`
         解析回它那条 ConceptCreated 的 concept_id, 新版本 = 本 record 的 concept_id (双 shape)。

    **保守 (fail-safe)**: 只在"被一个**不同 id** 的新概念替换"时才判退役; 原地升级 (同 id 版本号递增)
    不算退役 —— 概念仍在, 只是定义更新。理由: 过度过滤会拉低 existing 数 → 把成熟图误判"早期图"
    → 孤立概念走 `isolated_allowed_early` 放行 = **反向引入** f-r01-5 同款 bypass。宁可漏过一个该退
    的旧概念 (它顶多多当一次关联目标), 也绝不误删一个活概念把成熟度打下来。
    """
    records = list(records)
    # event_id → concept_id (双 shape), 供 republish 形态解析旧 concept_id。
    concept_id_by_event: dict[str, str] = {}
    for rec in records:
        if _event_type_name(rec) != _CONCEPT_CREATED:
            continue
        eid = _event_id_of(rec)
        cid = _concept_id_of(_concept_src(_payload_of(rec)))
        if eid and cid:
            concept_id_by_event[eid] = cid

    retired: set[str] = set()
    for rec in records:
        et = _event_type_name(rec)
        payload = _payload_of(rec)
        is_sup = bool(_supersede_field(rec, "is_supersede"))

        if et == _CONCEPT_CREATED and is_sup:
            superseded_eid = str(_supersede_field(rec, "superseded_event_id") or "")
            old_cid = concept_id_by_event.get(superseded_eid, "")
            new_cid = _concept_id_of(_concept_src(payload))
            if old_cid and old_cid != new_cid:  # 不同 id 替换 → 旧 id 退役 (同 id 原地升级不算)
                retired.add(old_cid)

        elif et == _CONCEPT_GRAPH_PROPOSAL and (
            is_sup or str(payload.get("transition_type") or "") == _TRANSITION_SUPERSEDED
        ):
            changes = payload.get("after_state")
            changes = changes.get("proposed_changes") if isinstance(changes, dict) else None
            for ch in changes if isinstance(changes, list) else []:
                if not isinstance(ch, dict):
                    continue
                entity_type = str(getattr(ch.get("entity_type"), "value", ch.get("entity_type")) or "")
                if entity_type not in ("", "concept"):
                    continue
                old_cid = str(ch.get("entity_id") or "")
                nv = ch.get("new_value")
                new_cid = str(nv.get("replacement_concept_id") or "") if isinstance(nv, dict) else ""
                if old_cid and new_cid and new_cid != old_cid:  # 仅"被不同概念替换"才判退役
                    retired.add(old_cid)
    return retired


# ── gate 路径便利封装: 从 EventLog 枚举既有概念 → 跑门 ─────────────────────────────
def existing_concepts_from_records(
    records: Iterable[Any],
    *,
    exclude_ids: Sequence[str] = (),
    include_retired: bool = False,
) -> list[ExistingConcept]:
    """从已提交事件记录枚举既有概念 (取每个 ConceptCreated 的概念数据, **双 shape**)。

    供 gate `_run_checks` 一行拿到 existing_concepts。supersede 用最新定义覆盖同 id (按记录顺序,
    后者胜)。exclude_ids 排除本批正在提交的概念 (避免自比)。

    - **双 shape** (@concept:c-conceptcreated-dual-shape-consumption@v1): nested 读 after_state,
      flat (ConceptSeedCreatedPayload) 读 payload 顶层 —— 否则 flat 种子概念不计入成熟度也不当关联
      目标 (f-r01-5)。
    - **退役过滤** (`include_retired=False`, 默认): 排除已被不同概念替换/退役的旧 concept_id
      (`_retired_concept_ids`), 不拿死概念当有效关联目标。相关性门 (gate §relatedness) 要这层过滤 ——
      新概念不该扎根一个已死概念蒙混过成熟度 (f-r01-5)。
    - **`include_retired=True`**: 不减退役集, 枚举每个有过 ConceptCreated 的概念 (含已退役-仍在节点)。
      边完整性门 (GP-05) 要这口径: facade.integrity_violations 的可解析判据是 target ∈ 图内节点, 而
      reducer 从不删节点 (supersede 只就地改字段) → 退役概念**仍是图节点**, facade 判它 RESOLVABLE。
      边门若用默认退役过滤 ⟹ 比它逐字引用的 facade 契约**严**, 会对合法血缘回链 (如 @vN→@v(N-1))
      fail-closed 误拒并谎报『非图内节点/导航撞空』(f-gp05-edge-gate-stricter-than-facade-retired-target)。
    """
    records = list(records)
    excluded = set(exclude_ids)
    if not include_retired:
        excluded |= _retired_concept_ids(records)
    by_id: dict[str, ExistingConcept] = {}
    for rec in records:
        if _event_type_name(rec) != _CONCEPT_CREATED:
            continue
        src = _concept_src(_payload_of(rec))  # 双 shape: nested→after_state / flat→payload 顶层
        cid = _concept_id_of(src)
        if not cid or cid in excluded:
            continue
        by_id[cid] = ExistingConcept(
            concept_id=cid,
            name=_concept_name_of(src),
            definition=str(src.get("definition", "")),
        )
    return list(by_id.values())
