"""R01 — 概念退役/升级硬门 + 分阶段迁移 (concept retire/upgrade hard gate + staged migration).

# concept source: c-concept-retire-hard-gate-staged-migration@v1
#   退役/升级一个概念时, 系统先算依赖它的 CIS (candidate-impact-set-system-derived), 提交前硬拦
#   这些依赖没改完就拒 (真 CommitRejected), 然后分阶段迁移 (依赖逐个迁完才最终放行)。复用
#   CommitRejected 事件 + at-reference supersede blocking + CIS。这是全局强共识有牙 (owner: 不会
#   "那里规定这里却关掉", 防漂移最大方法)。本体演化范式: 提交前标出依赖旧定义的断言 → 分阶段迁移。

是 supersede 协议的退役口校验: 一个 ConceptCreated 带 supersede.is_supersede=True 退役/升级某既有
概念时, 这一口校验"依赖它的下游都迁完了没"——没迁完即拦, 不靠出生后下游兜底再回头补 (那是昂贵的
下游兜底; 退役口拦一个还有未迁依赖的退役是廉价前移)。

## 三个复用 (不造轮子)

1. **CIS (T-R01-3 `CandidateImpactSetComputer`)** —— "先算依赖它的"。从退役概念出发算系统派生的候选
   下游集 (结构闭包 ∪ 可选历史共变)。这是要迁移的依赖宇宙 + 拒批留痕里的迁移范围。本门**不自己遍历
   概念图**, 调 `cis_for` 拿现成 `CandidateImpactSet`。
2. **at-reference supersede blocking (M-1.2 §4.3 `consensus_invalidation.active_references_to`)** ——
   每个依赖"改完没"的探针。一条 active @ 引用指向退役概念旧版本、且
   `on_target_supersede_policy=explicit_decision_required` (默认; §4.3 `blocks_commit_for_referer=True`)
   → 这个依赖"没改完" (旧定义的断言仍 live)。本门**不自己扫 @ 引用**, 调 `active_references_for` 拿。
   `auto_accept_latest` (自动跟随) / `pin_to_snapshot` (主动钉旧) 的引用不阻塞——它们的策略已显式说明
   退役后怎么办, 不是"没改完"。
3. **CommitRejected** —— 拦下的留痕。本门是纯判定函数, 产 `ConceptRetireMigrationResult`; 接进
   gate `_run_checks` 时拒 → CommitRejected (rejection 携 failure_reason/evidence), 与其它 check 同构,
   **不另造事件**。

## 分阶段迁移 (staged migration) —— 只认证据, 不认自报 (finding-r016)

退役不是一锤子买卖: 依赖可以逐个迁。一个依赖算"迁完"当且仅当有**可核验的 canonical 证据**
证明它已离开旧定义 —— 满足任一:

- 本批有一条 `AtReferenceRemoved` 删掉了它对旧版本的引用 (删了 → 在 active 判据里也已不阻塞);
- 本批有一条 `AtReferenceAdded` 把它重指到**新版本** (重指即迁完);
- 它自身在本批被 (co-)创建/supersede (源概念跟着升级 = 迁完);
- **committed 历史**里它已有一条重指到新版本的 @ 引用 (前几个 stage 真重指完的证据)。

**为什么不认 `semantic_annotation.migrated_concept_ids` 自报 (finding-r016)**: 退役 intent 的
`migrated_concept_ids` 是 agent 自报"我迁了这些", 无证据。它曾被当作放行依据并**凌驾**上述证据 ——
agent 只要把一个旧版本 @ 引用仍 active (=没真迁) 的依赖塞进自报, 门就放它过 → 旧定义的 live 断言
被孤儿化 → 防漂移失效。现在自报**不能单独放行**任何依赖: 一条仍 active 的 explicit-decision 引用,
只有上面四类真证据能清掉它。自报仅用于留痕 (拒批时标出"自报但未核实"的依赖, 让 agent 知道台账被拒)。

> 注: 当前系统**没有**自动 emit `AtReferenceRemoved` 的机制 (旧路径删除是独立未落地任务), 所以前几个
> stage 用"重指到新版本"做迁移时, 旧版引用不会自动失活 —— 这正是要认 committed 历史重指证据的原因
> (否则真迁完的依赖会被永久误拦)。pure-retire (无 replacement 新版本) 的依赖, 只能靠删引用 /
> 源被 supersede 来迁; 仅靠"源在 committed 历史被 supersede"而旧引用未删的情形当前 fail-closed 保守拦,
> agent 应对该 stale 引用 emit `AtReferenceRemoved` 解除 (与旧路径删除机制方向一致)。

只有当**没有**未迁的 explicit-decision 引用时, 最终退役才放行。每次留 notice 报进度 (还剩几个未迁)。

## fail-closed

- 任一 active explicit-decision 引用指向旧版本且未在本批迁移 → 拒 (`concept_retire_unmigrated_dependents`)。
- 退役概念解析不出旧 concept_id (supersede 指向的事件取不到) → 不静默放行: 无法证明依赖都迁完 = 不能退役。
- CIS 算不出来 (impact_graph 投影空/stale) 也不放过: 阻塞判据走 @ 引用 (读 raw 事件, 永远当前), CIS 只
  做召回范围补充, 故 CIS 缺位不会让门误放 (under-block)。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from towow.schemas.enums import LockingPolicy

if TYPE_CHECKING:
    from towow.l1.cia.candidate_impact_set import CandidateImpactSet

_CONCEPT_CREATED = "ConceptCreated"
# 退役/升级一个概念的真实 CLI 路径 (`consensus supersede`) emit ConceptGraphProposal
# (transition=superseded, top-level supersede.is_supersede=True), 被退役 concept_id 落在
# after_state.proposed_changes[].entity_id; ConceptCreated(is_supersede=True) 是 republish 形态。两条都认。
_CONCEPT_GRAPH_PROPOSAL = "ConceptGraphProposal"
_TRANSITION_SUPERSEDED = "superseded"
_AT_REFERENCE_ADDED = "AtReferenceAdded"
_AT_REFERENCE_REMOVED = "AtReferenceRemoved"

# 只有 explicit_decision_required (默认策略, §4.3 blocks_commit_for_referer=true) 的 active 引用阻塞退役。
_BLOCKING_POLICY = LockingPolicy.EXPLICIT_DECISION_REQUIRED


# ── 数据载体 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RetiringConcept:
    """本批一个退役/升级动作 —— 一个 ConceptCreated(is_supersede=True) 提取出的最小信息。"""

    old_concept_id: str  # 被退役/升级的旧 concept_id (从 supersede.superseded_event_id 解析)
    new_concept_id: str  # 新版本 concept_id (升级目标; 纯退役时可与旧 base 同名不同版本)
    superseded_event_id: str
    declared_migrated: frozenset[str]  # semantic_annotation.migrated_concept_ids (分阶段台账)


@dataclass(frozen=True)
class DependentMigrationFinding:
    """单个依赖的迁移判定 (留痕 + 拒批 evidence)。"""

    source_concept_id: str  # 依赖方 (持有指向旧版本的 @ 引用的概念)
    reference_id: str
    old_concept_id: str
    migrated: bool
    via: str  # migrated 的依据 / 或 "unmigrated" (still blocking)


@dataclass(frozen=True)
class ConceptRetireMigrationResult:
    """门的判定产物 —— 字段对齐 gate `_run_checks` 其它 check 的消费形态。"""

    passed: bool
    applicable: bool = False  # 本批是否含 ConceptCreated(is_supersede=True) 退役/升级 (无则 not_applicable)
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    findings: list[DependentMigrationFinding] = field(default_factory=list)


# ── 鸭子取值 (gate 给 EventIntent; 测试可给 SimpleNamespace / dict) ─────────────────
def _event_type_name(intent: Any) -> str:
    et = getattr(intent, "event_type", None)
    if et is None and isinstance(intent, Mapping):
        et = intent.get("event_type")
    return str(getattr(et, "value", et) or "")


def _payload_of(intent: Any) -> dict[str, Any]:
    p = getattr(intent, "payload", None)
    if p is None and isinstance(intent, Mapping):
        p = intent.get("payload")
    return p if isinstance(p, dict) else {}


def _supersede_of(intent: Any) -> Any:
    s = getattr(intent, "supersede", None)
    if s is None and isinstance(intent, Mapping):
        s = intent.get("supersede")
    return s


def _supersede_field(supersede: Any, name: str) -> Any:
    if supersede is None:
        return None
    if isinstance(supersede, Mapping):
        return supersede.get(name)
    return getattr(supersede, name, None)


def _after_state(intent: Any) -> dict[str, Any]:
    after = _payload_of(intent).get("after_state")
    return after if isinstance(after, dict) else {}


def _subjects_of(intent: Any) -> list[Any]:
    subs = getattr(intent, "subjects", None)
    if subs is None and isinstance(intent, Mapping):
        subs = intent.get("subjects")
    return list(subs) if isinstance(subs, (list, tuple)) else []


def _subject_field(subject: Any, name: str) -> Any:
    if isinstance(subject, Mapping):
        return subject.get(name)
    return getattr(subject, name, None)


def _entity_type_str(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _declared_migrated(*sources: Any) -> frozenset[str]:
    """从若干 dict 里取 migrated_concept_ids (分阶段已迁台账; semantic_annotation 或 proposed new_value)。"""
    out: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        migrated = src.get("migrated_concept_ids")
        if isinstance(migrated, list):
            out.update(str(c) for c in migrated)
    return frozenset(out)


def _proposal_concept_changes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    after = payload.get("after_state")
    changes = after.get("proposed_changes") if isinstance(after, dict) else None
    if not isinstance(changes, list):
        return []
    return [
        c
        for c in changes
        if isinstance(c, dict) and _entity_type_str(c.get("entity_type")) in ("", "concept")
    ]


def _ref_field(ref: Mapping[str, Any], *names: str) -> str:
    """@ 引用 dict 取第一个非空字段 (兼容 active_references_to / 投影 / 测试 三套键名)。"""
    for n in names:
        v = ref.get(n)
        if v:
            return str(v)
    return ""


# ── 退役/升级动作提取 ───────────────────────────────────────────────────────────
def _extract_retiring(
    domain_intents: Iterable[Any],
    superseded_concept_resolver: Callable[[str], str | None],
) -> tuple[list[RetiringConcept], list[str]]:
    """提取本批 ConceptCreated(is_supersede=True) → 退役动作。

    返回 (retiring, unresolvable): unresolvable = 检出退役 supersede 但旧 concept_id 解析失败的
    superseded_event_id (fail-closed — 无法证明依赖迁完, 由 caller 拒)。
    """
    retiring: list[RetiringConcept] = []
    unresolvable: list[str] = []
    for intent in domain_intents:
        et = _event_type_name(intent)
        payload = _payload_of(intent)
        supersede = _supersede_of(intent)
        is_sup = bool(_supersede_field(supersede, "is_supersede"))
        superseded_event_id = str(_supersede_field(supersede, "superseded_event_id") or "")
        transition = str(payload.get("transition_type") or "")

        if et == _CONCEPT_CREATED and is_sup:
            # republish 形态: 旧 concept_id 经 superseded_event_id 解析; 新版本 = after_state.concept_id。
            old_cid = superseded_concept_resolver(superseded_event_id) if superseded_event_id else None
            if not old_cid:
                unresolvable.append(superseded_event_id or "<missing-superseded_event_id>")
                continue
            after = _after_state(intent)
            new_cid = str(after.get("concept_id", "")) or str(old_cid)
            declared = _declared_migrated(payload.get("semantic_annotation"))
            retiring.append(RetiringConcept(str(old_cid), new_cid, superseded_event_id, declared))

        elif et == _CONCEPT_GRAPH_PROPOSAL and (is_sup or transition == _TRANSITION_SUPERSEDED):
            # 真 CLI 形态 (`consensus supersede`): 旧 concept_id 直接在 proposed_changes[].entity_id /
            # subject; 新版本 = new_value.replacement_concept_id (有则替换, 无则原地升级保持同 id)。
            changes = _proposal_concept_changes(payload)
            old_cid = ""
            new_cid = ""
            new_values: list[Any] = []
            for c in changes:
                if not old_cid and c.get("entity_id"):
                    old_cid = str(c.get("entity_id"))
                nv = c.get("new_value")
                if isinstance(nv, dict):
                    new_values.append(nv)
                    if not new_cid and nv.get("replacement_concept_id"):
                        new_cid = str(nv.get("replacement_concept_id"))
            if not old_cid:
                for subj in _subjects_of(intent):
                    if _entity_type_str(_subject_field(subj, "entity_type")) == "concept":
                        eid = _subject_field(subj, "entity_id")
                        if eid:
                            old_cid = str(eid)
                            break
            if not old_cid:
                unresolvable.append(superseded_event_id or "<proposal-no-concept-entity>")
                continue
            declared = _declared_migrated(*new_values)
            retiring.append(RetiringConcept(old_cid, new_cid or old_cid, superseded_event_id, declared))

    return retiring, unresolvable


# ── 本批迁移动作收集 (这一 stage 把哪些依赖迁完了) ─────────────────────────────────
@dataclass(frozen=True)
class _BatchMigrations:
    removed_reference_ids: frozenset[str]  # 本批 AtReferenceRemoved 删掉的引用 id
    repointed_sources: frozenset[str]  # 本批把引用重指到某新版本的源概念
    batch_concept_ids: frozenset[str]  # 本批 (co-)创建/supersede 的概念 (源自身迁了)


def _collect_batch_migrations(
    domain_intents: Iterable[Any],
    new_version_ids: frozenset[str],
) -> _BatchMigrations:
    removed: set[str] = set()
    repointed: set[str] = set()
    batch_concepts: set[str] = set()
    for intent in domain_intents:
        et = _event_type_name(intent)
        if et == _CONCEPT_CREATED:
            cid = str(_after_state(intent).get("concept_id", ""))
            if cid:
                batch_concepts.add(cid)
        elif et == _CONCEPT_GRAPH_PROPOSAL:
            # 一个依赖在本批被它自己的 supersede 提案迁掉 (源跟着升级)。
            for c in _proposal_concept_changes(_payload_of(intent)):
                eid = c.get("entity_id")
                if eid:
                    batch_concepts.add(str(eid))
        elif et == _AT_REFERENCE_REMOVED:
            after = _after_state(intent)
            ref_id = _ref_field(after, "at_reference", "reference_id")
            if ref_id:
                removed.add(ref_id)
        elif et == _AT_REFERENCE_ADDED:
            after = _after_state(intent)
            target = _ref_field(after, "target_concept_name")
            source = _ref_field(after, "source_concept_id")
            # 重指到本批某新版本 → 该源已迁。
            if source and target and any(_resolves_to(target, nv) for nv in new_version_ids):
                repointed.add(source)
    return _BatchMigrations(
        removed_reference_ids=frozenset(removed),
        repointed_sources=frozenset(repointed),
        batch_concept_ids=frozenset(batch_concepts),
    )


def _resolves_to(stored_target_name: str, concept_id: str) -> bool:
    """@ 引用 target_concept_name 是否解析到 concept_id (同 §4.3 consensus_invalidation 的口径)。"""
    if not stored_target_name or not concept_id:
        return False
    base = concept_id.split("@", 1)[0]
    slug = base.removeprefix("concept-")
    return stored_target_name in {concept_id, base, slug}


def _policy_of(ref: Mapping[str, Any]) -> LockingPolicy:
    raw = ref.get("on_target_supersede_policy")
    if isinstance(raw, str):
        try:
            return LockingPolicy(raw)
        except ValueError:
            pass
    # 默认 explicit_decision_required (§4.3) —— 没标策略的引用按最硬处理 (fail-closed)。
    return LockingPolicy.EXPLICIT_DECISION_REQUIRED


# ── 门主逻辑 ──────────────────────────────────────────────────────────────────
def check_concept_retire_migration(
    domain_intents: Iterable[Any],
    *,
    superseded_concept_resolver: Callable[[str], str | None],
    active_references_for: Callable[[str], Iterable[Mapping[str, Any]]],
    cis_for: Callable[[str], CandidateImpactSet] | None = None,
    committed_repointed_sources_for: Callable[[frozenset[str]], frozenset[str]] | None = None,
) -> ConceptRetireMigrationResult:
    """跑概念退役/升级硬门。

    domain_intents: 本批待提交事件。只在含 ConceptCreated(is_supersede=True) 时 applicable。
    superseded_concept_resolver: superseded_event_id → 旧 concept_id (None=解析不出, fail-closed 拒)。
    active_references_for: 旧 concept_id → 指向它的 active @ 引用 (复用 at-reference supersede blocking)。
        每条引用 dict 期望键: source_concept_id / at_reference|reference_id / on_target_supersede_policy。
    cis_for: 可选 旧 concept_id → CIS (复用 T-R01-3); 提供则把系统派生依赖范围纳入留痕 + 召回补充。
    committed_repointed_sources_for: 可选 new_version_ids → 在 committed 历史里已有 @ 引用重指到这些
        新版本的 source_concept_id 集合 (前几个 stage 真重指完的证据; finding-r016 用它替代自报台账,
        让真迁完的跨批依赖不被误拦, 同时不放行无证据的自报)。None / 不提供 = 无 committed 证据 (fail-closed)。
    """
    domain_intents = list(domain_intents)
    retiring, unresolvable = _extract_retiring(domain_intents, superseded_concept_resolver)

    if not retiring and not unresolvable:
        return ConceptRetireMigrationResult(passed=True, applicable=False)

    # fail-closed: 检出退役 supersede 但旧 concept_id 解析不出 → 无法验证迁移, 直接拒。
    if unresolvable:
        return ConceptRetireMigrationResult(
            passed=False,
            applicable=True,
            failure_reason="concept_retire_superseded_target_unresolvable",
            failure_evidence={
                "unresolvable_superseded_event_ids": ",".join(unresolvable),
                "remedy": (
                    "退役/升级的 ConceptCreated.supersede.superseded_event_id 必须指向一个真实、可解析"
                    "出 concept_id 的既有事件; 解析不出无法核验依赖是否迁完 → 拒 (fail-closed)。"
                ),
            },
        )

    # 新版本 id 只取真"新"的 (≠ 旧 id): pure-retire (new==old) 没有可重指的新版本, 不能把"重指到旧
    # 自己"误当迁移证据。
    new_version_ids = frozenset(
        r.new_concept_id for r in retiring if r.new_concept_id and r.new_concept_id != r.old_concept_id
    )
    batch = _collect_batch_migrations(domain_intents, new_version_ids)
    committed_repointed = (
        committed_repointed_sources_for(new_version_ids)
        if committed_repointed_sources_for is not None and new_version_ids
        else frozenset()
    )

    findings: list[DependentMigrationFinding] = []
    notices: list[str] = []
    unmigrated_sources: list[str] = []
    unmigrated_refs: list[str] = []

    for rc in retiring:
        cis = cis_for(rc.old_concept_id) if cis_for is not None else None
        cis_dependents = cis.candidates() if cis is not None else set()

        refs = list(active_references_for(rc.old_concept_id))
        blocking_refs = [r for r in refs if _policy_of(r) is _BLOCKING_POLICY]

        ref_sources: set[str] = set()
        remaining_for_concept = 0
        for ref in blocking_refs:
            ref_id = _ref_field(ref, "at_reference", "reference_id")
            source = _ref_field(ref, "source_concept_id")
            ref_sources.add(source)
            migrated, via = _migration_status(source, ref_id, rc, batch, committed_repointed)
            findings.append(
                DependentMigrationFinding(
                    source_concept_id=source,
                    reference_id=ref_id,
                    old_concept_id=rc.old_concept_id,
                    migrated=migrated,
                    via=via,
                ),
            )
            if not migrated:
                remaining_for_concept += 1
                if source:
                    unmigrated_sources.append(source)
                if ref_id:
                    unmigrated_refs.append(ref_id)

        # 召回补充: CIS 算出的依赖里, 没被任何 active 阻塞引用覆盖的 (可能是 @ 引用没登记 / 投影 stale)。
        untracked = cis_dependents - ref_sources - {rc.old_concept_id, rc.new_concept_id}
        if untracked:
            notices.append(
                f"concept_retire: {rc.old_concept_id} 的 CIS 依赖 {sorted(untracked)} 无对应 active 阻塞引用 "
                f"(可能未登记 @ 引用或投影 stale; 阻塞判据走 @ 引用, 这些仅作召回提示)",
            )
        notices.append(
            f"concept_retire: {rc.old_concept_id}→{rc.new_concept_id} 阻塞引用 {len(blocking_refs)} 条, "
            f"本 stage 仍未迁 {remaining_for_concept} 条 (CIS 依赖范围 {len(cis_dependents)})",
        )

    if unmigrated_sources or unmigrated_refs:
        retired_ids = ",".join(sorted({r.old_concept_id for r in retiring}))
        return ConceptRetireMigrationResult(
            passed=False,
            applicable=True,
            failure_reason="concept_retire_unmigrated_dependents",
            failure_evidence={
                "retiring_concepts": retired_ids,
                "unmigrated_dependents": ",".join(sorted(set(unmigrated_sources))),
                "unmigrated_references": ",".join(sorted(set(unmigrated_refs))),
                "remedy": (
                    "退役/升级一个概念前, 依赖它旧定义的下游必须逐个迁完 (分阶段), 且要留**真证据**: 把每个"
                    "依赖的 @ 引用重指到新版本 (AtReferenceAdded), 或删除 (AtReferenceRemoved)。前几个 stage "
                    "已重指到新版本的依赖会被 committed 历史证据自动认作迁完。退役 intent 的 "
                    "semantic_annotation.migrated_concept_ids 自报已不能单独放行 (finding-r016: 自报无证据可绕过); "
                    "标了自报但无证据的依赖会显示 via=declared_but_unverified 并仍被拦。全部迁完前退役被硬拦 (fail-closed)。"
                ),
            },
            notices=notices,
            findings=findings,
        )

    return ConceptRetireMigrationResult(passed=True, applicable=True, notices=notices, findings=findings)


def _migration_status(
    source: str,
    reference_id: str,
    rc: RetiringConcept,
    batch: _BatchMigrations,
    committed_repointed_sources: frozenset[str],
) -> tuple[bool, str]:
    """一个依赖算不算"迁完", 返回 (migrated, via)。

    只认可核验的 canonical 证据 (finding-r016): 本批删引用 / 本批重指新版本 / 源本批被 supersede /
    committed 历史已重指新版本。`migrated_concept_ids` 自报**不再单独放行** —— 一条仍 active 的
    explicit-decision 引用必须由真证据清掉; 自报只在拒批留痕里标"declared_but_unverified", 不放行。
    """
    if reference_id and reference_id in batch.removed_reference_ids:
        return True, "at_reference_removed_in_batch"
    if source and source in batch.repointed_sources:
        return True, "at_reference_repointed_to_new_in_batch"
    if source and source in committed_repointed_sources:
        return True, "at_reference_repointed_to_new_committed"
    if source and source in batch.batch_concept_ids:
        return True, "source_co_superseded_in_batch"
    if source and source in rc.declared_migrated:
        # 自报已迁但无任何证据 → 仍判未迁 (fail-closed), via 标出让 agent 知道台账被拒。
        return False, "declared_but_unverified"
    return False, "unmigrated"


# ── committed 历史里的重指证据 (finding-r016: 替代自报台账) ───────────────────────────
def _committed_repointed_sources(towow_dir: Any, new_version_ids: frozenset[str]) -> frozenset[str]:
    """扫 committed canonical 日志: 哪些 source 已有一条 @ 引用重指到 new_version_ids 之一。

    读 raw 事件 (永远当前; 只含已提交事件, 不含本批待提交 intent —— 故只会捞到"前几个 stage"的重指)。
    这是 finding-r016 的证据替代: 前几个 stage 真把依赖重指到新版本 (但旧版引用未删=仍 active) 时,
    用这条 committed 证据认它迁完, 而不靠 agent 自报。无新版本 id → 无证据 (空集)。
    """
    if not new_version_ids:
        return frozenset()
    import json

    from towow.l0.event_log.segments import iter_raw_event_lines

    out: set[str] = set()
    for line in iter_raw_event_lines(towow_dir):
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(rec, dict) or rec.get("event_type") != _AT_REFERENCE_ADDED:
            continue
        payload = rec.get("payload")
        after = payload.get("after_state") if isinstance(payload, dict) else None
        if not isinstance(after, dict):
            continue
        target = _ref_field(after, "target_concept_name")
        source = _ref_field(after, "source_concept_id")
        if source and target and any(_resolves_to(target, nv) for nv in new_version_ids):
            out.add(source)
    return frozenset(out)


# ── gate 路径便利封装: 从 EventLog 组装 resolver → 跑门 ───────────────────────────────
def check_concept_retire_migration_for_gate(
    domain_intents: Iterable[Any],
    event_log: Any,
) -> ConceptRetireMigrationResult:
    """gate `_run_checks` 一行调用 —— 从 event_log 组装 superseded resolver + at-reference 阻塞探针
    (`consensus_invalidation.active_references_to`, 读 raw 事件=永远当前) + CIS (T-R01-3, 仅结构路, 不在
    提交热路径上跑 git-log 共变挖掘)。

    只在本批含 ConceptCreated(is_supersede=True) 时才真去组装 resolver / 读事件 —— 普通提交早返回
    not_applicable (零额外成本)。
    """
    from towow.l1.consensus_invalidation import active_references_to

    towow_dir = event_log.log_path.parent

    def _resolve_superseded(event_id: str) -> str | None:
        if not event_id:
            return None
        rec = event_log.get_event(event_id)
        if rec is None:
            return None
        after = getattr(rec, "payload", None)
        after = after.get("after_state") if isinstance(after, dict) else None
        cid = after.get("concept_id") if isinstance(after, dict) else None
        return str(cid) if cid else None

    def _active_refs(old_cid: str) -> Sequence[Mapping[str, Any]]:
        return active_references_to(towow_dir, old_cid)

    def _cis(old_cid: str) -> CandidateImpactSet:
        from towow.l0.projection.projection import ProjectionStore
        from towow.l1.cia.candidate_impact_set import CandidateImpactSetComputer
        from towow.l1.cia_query import CIAQueryService

        store = ProjectionStore(towow_dir / "graph")
        computer = CandidateImpactSetComputer(CIAQueryService(store))
        # 结构路 only (热路径不跑历史共变挖掘 git-log, 同 concept_create_gate 的取舍)。
        return computer.compute([old_cid])

    def _committed_repointed(new_version_ids: frozenset[str]) -> frozenset[str]:
        return _committed_repointed_sources(towow_dir, new_version_ids)

    return check_concept_retire_migration(
        domain_intents,
        superseded_concept_resolver=_resolve_superseded,
        active_references_for=_active_refs,
        cis_for=_cis,
        committed_repointed_sources_for=_committed_repointed,
    )


__all__ = [
    "ConceptRetireMigrationResult",
    "DependentMigrationFinding",
    "RetiringConcept",
    "check_concept_retire_migration",
    "check_concept_retire_migration_for_gate",
]
