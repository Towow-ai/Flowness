"""M-0.3 Capsule Assembly Pipeline — 9 stage implementation.

# spec source:
#   03-l0-truth-source/M-0.3-capsule-assembler-detailed-design.md
#     §2 Pipeline 9 stages (L41..L228)
#     §3 Resolver call contract (L248..L302)
#     §4 Cache contract (L305..L344)
#     §6 9 scene templates (L376..L526)
#     §7 capsule size management (L528..L590)
#     §9 EventIntent specs (L620..L700)
#     Patch G (§8.3 / §10.3) — stage 5 also writes ObligationScopeJudged;
#       after stage 8 (CapsuleCompiled) emit ObligationActivated for each
#       final_active_set obligation.
#
# Phase E.1.5 (RUN-005 fix bucket A — DOGFOOD-001-L):
#   - Stages 1-8 真实现 (replace evt-stub-capsule-* sentinels).
#   - Resolver call uses conservative_fallback_verdict only (LLM resolver lands E.3).
#   - Path-B real emit for: ResolverDecisionMade, ObligationScopeJudged (per candidate
#     judgment), CapsuleCompiled, ObligationActivated (per final_active_set member).
#
# RUN-027 block 2 (E-DEBT-PAYOFF-ROADMAP §1 block-2):
#   - Stage 2 neighborhood expansion真实化 (expand_neighborhood BFS over the now-real
#     concept_graph projection) — feeds the §2.3 anchors∩neighborhood filter,
#     KNOWN_FACTS section, and ResolverInput.projection_slice (no longer nodes=[]/edges=[]).
#   - Stage 3 CONCEPT_NEIGHBORHOOD分流真实化: anchors∩neighborhood → needs_resolver / miss.
#   - Stage 7 KNOWN_FACTS (neighborhood concept defs) + FILES_TO_READ (target_artifacts)
#     真实化; stage 8 CapsuleCompiled known_facts_count / files_to_read_count真值.
#   - Candidate retrieval stays a conservative superset (all active) so every active
#     obligation still gets an explicit hit/miss audit event (M-0.6 §4.2.1).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from towow.l0.capsule.config import (
    capsule_catchup_timeout_ms,
    capsule_max_tokens_override,
    obligation_resolver_model_override,
    obligation_resolver_replay_file,
    resolve_obligation_resolver_mode,
    resolver_contract_version,
    resolver_timeout_ms,
)
from towow.l0.capsule.conformance_card import render_conformance_card_section
from towow.l0.capsule.neighborhood import Neighborhood, expand_neighborhood
from towow.l0.capsule.truncation import _TruncationAbort, truncate_capsule
from towow.l0.obligations.bootstrap_specs import (
    BOOTSTRAP_OBLIGATION_SPECS,
    BootstrapObligationSpec,
)
from towow.l0.obligations.llm_resolver import (
    MODE_REAL,
    MODE_REPLAY,
    bind_llm_resolver,
)
from towow.l0.obligations.resolver import (
    ResolverSchemaInvalid,
    call_resolver,
)
from towow.l0.projection.projection import ConsistencyMode, ProjectionConsistencyError
from towow.schemas.capsule import CapsuleResult, CapsuleSectionContent
from towow.schemas.capsule_templates import SceneTemplate, get_scene_template
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    CapsuleSection,
    EventCategory,
    EventType,
    JudgmentType,
    ObligationActivationSource,
    ObligationCanonicalState,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeJudgment,
    ObligationScopeType,
    ObligationSeverity,
    SceneType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.obligation import ScopeHint
from towow.schemas.resolver import (
    ConceptGraphNeighborhood,
    EntityRefShort,
    ObligationCandidate,
    ObligationJudgment,
    ProjectionSlice,
    ReentryContext,
    ResolverConfig,
    ResolverInput,
    ResolverVerdict,
    TaskScope,
    VerdictMeta,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from towow.l0.event_log.event_log import EventLog
    from towow.l0.projection.projection import ProjectionStore


# ─── Capsule assembler error ─────────────────────────────────────────────────


class PipelineError(RuntimeError):
    """M-0.3 §2.10 pipeline abort error — non-retryable from M-0.3 perspective."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ─── Pipeline result (returned to caller) ────────────────────────────────────


@dataclass(frozen=True)
class CapsulePipelineResult:
    """Outcome of `assemble_capsule()` per M-0.3 §2.0.

    Carries the assembled CapsuleResult plus event_ids the pipeline produced
    (so callers / tests can audit-link them).
    """

    capsule: CapsuleResult
    capsule_compiled_event_id: str
    resolver_decision_event_id: str
    scope_judged_event_ids: list[str]
    activated_event_ids: list[str]
    final_active_set: list[str]
    # debt-e623ea238ded (件 B): the caller-declared write_set echoed back (EntityRefShort), so the
    # session-start caller can inject the SAME set into its envelope write_set — the capsule and the
    # envelope then carry one consistent declared boundary (capsule TaskScope ↔ M-0.4 write_set).
    declared_write_set: list[EntityRefShort] = field(default_factory=list)


# ─── Internal stage outcome dataclasses ──────────────────────────────────────


@dataclass(frozen=True)
class _CandidateRecord:
    """A canonical obligation pulled from obligation_lifecycle projection.

    All fields needed downstream by stages 3 / 4 / 6 / 7 — no re-read from projection.
    """

    obligation_id: str
    nature: ObligationNature
    severity: ObligationSeverity
    scope_type: ObligationScopeType
    scope_rule: str
    definition: str
    canonical_state: ObligationCanonicalState
    scope_hint: ScopeHint | None  # M-0.6 §2.1 — mechanical-filter assist (concept_anchors)
    concept_anchors: frozenset[str]  # precomputed: scope_hint.concept_anchors ∪ attached_to concept ids


@dataclass(frozen=True)
class _MechanicalFilterOutcome:
    """Stage 3 output — three lists per M-0.3 §2.3."""

    mechanical_hits: list[_CandidateRecord]
    mechanical_misses: list[_CandidateRecord]
    needs_resolver: list[_CandidateRecord]


@dataclass(frozen=True)
class _CandidateKey:
    """One candidate's contribution to the M-0.3 §4.1 / Patch3 cache key.

    Patch3 canonical_resolver_input.candidates folds each obligation's id PLUS its
    definition_hash + scope_rule_hash — so a definition or scope-rule edit (even with the
    same obligation_id set) invalidates the cached verdict ("两个改同一文件但意图不同的任务
    不得错误命中" generalizes to "obligation 定义/范围改了旧判定不该复用"). The hashing of
    definition / scope_rule into the key happens in _compute_input_hash.
    """

    obligation_id: str
    definition: str
    scope_rule: str


# T-L0-17 (波1) M-0.3 §7.1 — MAX_CAPSULE_TOKENS 按 model tier 分级 (现状曾硬编码 8000)。
# opus 200K 窗口 40% / sonnet 30% / resolver(本身 Sonnet, capsule 更精简)。
_MAX_CAPSULE_TOKENS_DEFAULT: dict[str, int] = {
    "opus_task": 80000,
    "sonnet_task": 60000,
    "resolver": 30000,
}


def _max_capsule_tokens(model_tier: str, projection_store: ProjectionStore) -> int:
    """Token 上限按 model_tier 取分级值, config.json 可覆盖 (改 config → capsule 截断阈值随之变)。

    T-L0-21 (波2): 覆盖来源统一到 spec §10 的 canonical 位置 `.towow/capsule-assembler/config.json`
    (经 capsule.config 模块, T-L0-14 已落该模块但 token 覆盖函数此前无 caller = 死代码 — 本次接进
    pipeline)。向后兼容: spec 位无覆盖时回退 T-L0-17 旧位 `.towow/config.json`, 再回退分级默认 —
    既存只在旧位放 config 的项目行为不变。
    """
    towow_dir = projection_store._dir.parent  # internal store dir (.towow/graph → .towow)
    default = _MAX_CAPSULE_TOKENS_DEFAULT.get(model_tier, _MAX_CAPSULE_TOKENS_DEFAULT["opus_task"])
    # 1) spec §10 canonical 位 (.towow/capsule-assembler/config.json) — T-L0-21。
    spec_override = capsule_max_tokens_override(towow_dir, model_tier)
    if spec_override is not None:
        return spec_override
    # 2) 向后兼容: T-L0-17 旧位 .towow/config.json。
    config_path = towow_dir / "config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
        override = data.get("MAX_CAPSULE_TOKENS") if isinstance(data, dict) else None
        if isinstance(override, dict) and isinstance(override.get(model_tier), int):
            return int(override[model_tier])
    return default


# ─── assemble_capsule() — top-level entry ────────────────────────────────────


def assemble_capsule(
    *,
    event_log: EventLog,
    projection_store: ProjectionStore,
    scene_type: SceneType,
    task_id: str,
    target_fork_id: str,
    # F-CIR-capsule-fork-not-session: the session unit this capsule is assembled FOR (stamped onto
    # CapsuleCompiled.provenance.session_id). Optional at this layer — audit forks / the debug
    # `capsule` CLI genuinely have no owning session and stay fork-keyed (None). The 6 session-start
    # callers pass their session sid via _assemble_session_capsule (required there) so per-session
    # capsule attribution is restored. session_id is provenance metadata only — NOT folded into
    # input_hash (cache key) or the §9.2 audit fingerprint, so two sessions assembling identical
    # capsule content still cache-hit and the gate 倒查 (fork_name=fork_id) is unaffected.
    session_id: str | None = None,
    task_type: SceneType | None = None,
    target_artifacts: list[str] | None = None,
    touched_nodes: list[str] | None = None,
    task_text: str = "",
    why_this_fork: str = "",
    must_return: str = "",
    # debt-e623ea238ded (件 B): §2.0 caller_params 补全 — caller 直接注入的素材/约束/写集声明。
    #   known_facts        → 进 KNOWN_FACTS section (caller 已知事实, 与 neighborhood 渲染合并)。
    #   must_not_do        → 进 MUST_NOT_DO section (caller 声明的硬约束, 与 obligation 渲染合并)。
    #   declared_write_set → 进 TaskScope.declared_write_set (真信号给 M-0.4 写集派生) + 经
    #                        CapsulePipelineResult 回传, caller 注入同一集到 envelope write_set。
    known_facts: list[str] | None = None,
    must_not_do: list[str] | None = None,
    declared_write_set: list[EntityRefShort] | None = None,
    model_tier: str = "opus_task",  # T-L0-17: opus_task / sonnet_task / resolver → token 上限分级
    reentry: bool = False,  # M-0.3 §5.1/§5.2 — re-eval: force full re-run, bypass cache
    reentry_reason: str = "",  # §5.2 — why re-eval (recorded in reentry_context)
    previous_verdict_event_id: str | None = None,  # §5.2 — link to prior ResolverDecisionMade
    previous_run_id: str | None = None,  # §5.2 — prior run id (optional)
) -> CapsulePipelineResult:
    """Run the full M-0.3 §2 pipeline 1-8 and return CapsulePipelineResult.

    Caller (L1 skill / CLI) supplies scene_type + task_id + target_fork_id; the
    pipeline reads projections, candidates, judges scope, emits decision events,
    assembles the capsule, emits CapsuleCompiled, and finally emits
    ObligationActivated for the final_active_set.

    Failures abort the pipeline (M-0.3 §2.10) — already-written decision events stay.
    """
    correlation_id = f"capsule-{uuid.uuid4().hex[:12]}"
    actual_task_type = task_type or scene_type

    # ─── Stage 1: projection bundle read ─────────────────────────────────
    # M-0.3 §2.1 — single fresh_required bundle read, no mid-pipeline reread.
    # T-L0-14 (波2): catch-up 走 read_consistent(fresh_required) (T-L0-12 已落), 含 timeout +
    # fail-closed (ProjectionConsistencyError)。spec §2.1/§2.10 row1: fresh_required 超时 →
    # pipeline abort + PipelineError("projection_catch_up_timeout") (调用方决定重试/escalate),
    # **此时尚无任何已写 capsule 事件**。复用 T-L0-16 的 _abort_capsule_assembly 留痕路径 (另一条
    # abort 路径: fresh 超时, 区别于 T-L0-16 的 write 失败 abort)。
    try:
        # M-0.3 §2.1 — read_consistent returns the committed cutoff watermark; this IS the
        # bundle.as_of_seq the whole pipeline reads against (single fresh read, no mid-pipeline
        # reread). Captured here and stamped onto CapsuleCompiled (§2.8/§9.2) so M-0.4 can derive
        # the envelope's as_of_projection_seq baseline (Cap3).
        as_of_seq = projection_store.read_consistent(
            event_log,
            mode=ConsistencyMode.FRESH_REQUIRED,
            timeout_ms=capsule_catchup_timeout_ms(projection_store._dir.parent),
        )
    except ProjectionConsistencyError as exc:
        _abort_capsule_assembly(
            event_log,
            task_id=task_id,
            correlation_id=correlation_id,
            input_hash=None,
            failed_stage="stage_1_projection_fresh",  # spec §2.10 row1 / done_criteria projection_fresh
            partial_event_ids=[],  # §2.10: fresh 超时 abort 时无已写内容
            final_error=str(exc),
        )
        raise PipelineError("projection_catch_up_timeout") from exc
    template = get_scene_template(scene_type)
    bundle = projection_store.get_projection_bundle(
        [p.value for p in template.required_projections],
    )
    obligation_proj = bundle.get("obligation_lifecycle_state") or {"obligations": []}
    concept_graph = bundle.get("concept_graph")

    # ─── Stage 2: candidate retrieval + neighborhood expansion ───────────
    # M-0.3 §2.2 — N-hop neighborhood around task_scope.touched_nodes (BFS over the
    # concept graph; hops from the scene template). Candidate retrieval stays a
    # conservative superset (all active obligations) so every active obligation gets an
    # explicit hit/miss audit event (M-0.6 §4.2.1); the neighborhood scopes the
    # CONCEPT_NEIGHBORHOOD branch of stage 3 + fills KNOWN_FACTS / the resolver slice.
    #
    # T-BRAIN-01 (concept-graph signal injection): the neighborhood SEEDS are the task's
    # own concept anchors — the concepts the planner declared this task is "about"
    # (the task_graph node's concept-typed read_set entries — the planner's `--read-set`
    # concept claims, projected verbatim via TaskReadSetClaimed; NOT concept_refs/`--concept-ref`,
    # which only lands in after_state['concept_refs'] and is never projected into the node's
    # read_set). The scene templates' neighborhood_center is literally "task_scope.
    # target_artifacts 对应的概念/文件节点" (M-0.3 §3) — i.e. touched_nodes is MEANT to be
    # derived from the task scope, not hand-passed. Production callers (orchestrator-
    # dispatched `work start`) pass no --touched-node, so until now the seed set was empty
    # → empty neighborhood → empty KNOWN_FACTS → the agent received no concept graph at all
    # (the root of "AI 共识不对 / migration 不用概念图"). Auto-deriving the seeds from the
    # task's concept anchors makes the injection automatic. Explicit caller touched_nodes
    # still augment (union, widening-only — strictly safe for the gate's ScopeDrift, which
    # only rejects concepts OUTSIDE scope). A missing task_graph / task node yields no auto
    # seeds (identical to today's empty-seed behavior — no regression for task-less scenes
    # like the session-start marker). This is the single funnel: every task-bearing scene
    # (execution / review / fix / mismatch, whose templates already read TASK_GRAPH) is
    # fixed here without each caller having to remember --touched-node.
    neighborhood_seeds = _dedupe_preserve_order(
        [
            *(touched_nodes or []),
            *_derive_task_concept_anchors(bundle.get("task_graph"), task_id),
        ],
    )
    neighborhood = expand_neighborhood(
        concept_graph,
        neighborhood_seeds,
        hops=template.neighborhood_hops,
    )
    candidates = _retrieve_candidates(obligation_proj)
    # M-0.3 §2.2 — candidates += protocol_level_invariants(task_scope.task_type). The protocol-level
    # invariants (M-0.6 §5.2 bootstrap) are guaranteed candidates by task_type from the canonical
    # definition source, deduped against the projection-sourced ones (so a captured invariant's real
    # obligation_id wins; the synthetic injection only fires when the projection is missing it).
    candidates = _merge_protocol_invariants(
        candidates, protocol_level_invariants(actual_task_type),
    )

    # M-0.3 §6 / Patch 2 — consume the scene template's additional_event_reads, bounded to the
    # bundle's committed cutoff (until_seq=as_of_seq). These become the capsule's source_event_refs
    # (§9.2/Patch 4 audit指纹) — events written after the cutoff are not visible (一致性, §2.1).
    source_event_refs = _read_additional_events(event_log, template.additional_event_reads, as_of_seq)

    # ─── Stage 3: mechanical filter ──────────────────────────────────────
    # M-0.3 §2.3 — split candidates by scope_type into hit / miss / needs_resolver.
    # CONCEPT_NEIGHBORHOOD is scoped here by anchors∩neighborhood (邻域命中是必要不充分).
    filter_outcome = _mechanical_filter(
        candidates,
        actual_task_type,
        neighborhood,
        target_artifacts=target_artifacts or [],  # §2.3 specific_artifact 机械 artifact_match
    )

    # M-0.3 §5.2 — re-entry context: carried into the TaskScope (resolver knows it is an
    # incremental re-judgment, step 3) AND recorded on the ResolverDecisionMade trail. The
    # TaskScope schema requires reentry_context iff reentry=true; previous_verdict_event_id is the
    # ReentryContext link (a synthetic placeholder is used when the caller omitted it, since the
    # schema requires a non-empty id — the recorded reentry_context on the event keeps the real
    # nullable value).
    reentry_context_obj = (
        ReentryContext(
            previous_run_id=previous_run_id or task_id,
            previous_verdict_event_id=previous_verdict_event_id or "unknown",
        )
        if reentry
        else None
    )

    # Build task_scope (resolver input + ObligationScopeJudged payload + cache key).
    # touched_nodes carries the hops-expanded neighborhood per M-0.6 §6.2.
    task_scope_input = TaskScope(
        task_id=task_id,
        task_type=actual_task_type,
        target_artifacts=target_artifacts or [],
        # debt-e623ea238ded (件 B): 真信号 — caller 声明的写集进 TaskScope (resolver 看得到本次
        # task 要写哪些实体), 不再硬编码 []。空时仍 [] (向后兼容, 非义务 caller 不受影响)。
        declared_write_set=list(declared_write_set or []),
        touched_nodes=[
            EntityRefShort(entity_type=SubjectEntityType.CONCEPT, entity_id=cid)
            for cid in sorted(neighborhood.concept_ids)
        ],
        reentry=reentry,
        reentry_context=reentry_context_obj,
    )

    # Cap3 (M-0.6 §6.6): the resolver contract_version drives BOTH the resolver config + the cache
    # key (one value, read once), so a contract bump flips both consistently.
    contract_version = resolver_contract_version(projection_store._dir.parent)

    # Compute input_hash (M-0.3 §4.1 / Patch3 cache key) BEFORE stage 4 — §4.2 lookup must run
    # before deciding whether to invoke the resolver. bundle_hash makes a projection change a miss;
    # contract_version makes a resolver-contract bump a miss; the full canonical key (Cap §4.1) folds
    # description / reentry / scene_type / per-candidate definition+scope_rule hashes too.
    bundle_hash = _compute_bundle_hash(bundle)
    input_hash = _compute_input_hash(
        task_id=task_id,
        task_type=actual_task_type,
        description=task_text,
        target_artifacts=target_artifacts or [],
        touched_nodes=sorted(neighborhood.concept_ids),
        candidates=[
            _CandidateKey(
                obligation_id=c.obligation_id,
                definition=c.definition,
                scope_rule=c.scope_rule,
            )
            for c in candidates
        ],
        bundle_hash=bundle_hash,
        contract_version=contract_version,
        scene_type=scene_type,
        reentry=reentry,
        reentry_context_hash=(
            _component_hash(reentry_reason or "reentry") if reentry else None
        ),
    )

    # ─── Stage 4: resolver call (semantic judgment) ──────────────────────
    # M-0.3 §2.4 + §4.2/§4.3 — before invoking the resolver, look up the cache (input_hash.idx).
    # On hit the cached ResolverDecisionMade's judgments are reused (resolver NOT re-run, cache_hit
    # =true, cache_lookup_hash recorded — §4.3); on miss the resolver runs (E.1.5 conservative
    # fallback; LLM resolver lands E.3). Skip both when needs_resolver is empty.
    # (resolve debt-b529da25797e — cache lookup reuse; cache_hit was hardcoded False before.)
    resolver_called = False
    resolver_verdict: ResolverVerdict | None = None
    resolver_runtime_ms: int | None = 0
    fallback_applied = False
    cache_hit = False
    cache_lookup_hash: str | None = None
    if filter_outcome.needs_resolver:
        resolver_input = _build_resolver_input(
            task_scope_input,
            filter_outcome.needs_resolver,
            neighborhood,
            resolver_timeout_ms(projection_store._dir.parent),
            contract_version,
        )
        # M-0.3 §5.2 — reentry=true forces a full re-run: the cache is NOT consulted (re-eval
        # means "re-judge", reusing the prior verdict would defeat the re-evaluation). On a normal
        # call (§4.2/§4.3) the cache is looked up and a hit short-circuits stage 4.
        cached = (
            None if reentry else _cache_lookup(event_log, input_hash, filter_outcome.needs_resolver)
        )
        if cached is not None:
            # §4.3 — cache hit: reuse cached verdict, skip the resolver (stage 4 short-circuit).
            resolver_verdict = cached
            cache_hit = True
            cache_lookup_hash = input_hash
            resolver_called = False
            resolver_runtime_ms = None  # §9.1 — null when cache hit (resolver not invoked)
        else:
            # §4.2 cache miss / §5.2 reentry — invoke the resolver via the §3.1 call contract:
            # timeout / m06_error / m06_unreachable → conservative fallback (留痕, no abort);
            # schema_invalid → ResolverSchemaInvalid → pipeline abort("resolver_schema_invalid").
            # RUN-049: resolver= 真接 LLM (mode=llm/replay → bind_llm_resolver; fallback → None →
            # conservative_fallback_verdict)。终结"从不调 LLM"的机械兜底。mode=fallback 时走单参
            # 旧路径 call_resolver(resolver_input) (与改前 byte 一致, 不破现有单参 monkeypatch)。
            resolver = _select_resolver(projection_store._dir.parent)
            try:
                if resolver is None:
                    resolver_verdict = call_resolver(resolver_input)
                else:
                    resolver_verdict = call_resolver(resolver_input, resolver=resolver)
            except ResolverSchemaInvalid as exc:
                # M-0.3 §3.1 / §2.10 — abort BEFORE writing any capsule event (no decision event yet).
                _abort_capsule_assembly(
                    event_log,
                    task_id=task_id,
                    correlation_id=correlation_id,
                    input_hash=input_hash,
                    failed_stage="stage_4_resolver_schema_invalid",
                    partial_event_ids=[],  # §2.10 — schema_invalid abort: no event written yet
                    final_error=str(exc),
                )
                raise PipelineError("resolver_schema_invalid") from exc
            resolver_called = True
            resolver_runtime_ms = resolver_verdict.meta.resolver_runtime_ms
        # call_resolver already validated coverage on the miss path; re-check on the cache-hit path
        # (cached verdict must still cover the current candidate set, M-0.3 §3.3).
        resolver_verdict.check_covers(resolver_input)
        fallback_applied = any(j.fallback_applied for j in resolver_verdict.judgments)

    # ─── Compute final_active_set ────────────────────────────────────────
    # Active set = mechanical_hits + resolver judgments with hit=true, plus the
    # f-r12-4 red_line/invariant floor: a red_line-severity or invariant-nature
    # candidate the resolver dropped (hit=false) at LOW confidence is force-included
    # (defense-in-depth — a buggy/injected low-confidence hit=false must not silently
    # drop a red_line obligation; mirrors conservative_fallback's "宁可多注入" but only
    # for red_line/invariant, and only when the drop is NOT high-confidence so a
    # genuinely confident resolver verdict is still respected). Floored ids are recorded
    # on ResolverDecisionMade (stage 5a below) — the floor never overrides silently.
    final_active: list[_CandidateRecord] = list(filter_outcome.mechanical_hits)
    resolver_hit_ids: set[str] = set()
    floored_ids: set[str] = set()
    if resolver_verdict is not None:
        judgment_by_id = {j.obligation_id: j for j in resolver_verdict.judgments}
        for j in resolver_verdict.judgments:
            if j.hit:
                resolver_hit_ids.add(j.obligation_id)
        for cand in filter_outcome.needs_resolver:
            judgment = judgment_by_id.get(cand.obligation_id)
            if judgment is not None and judgment.hit:
                final_active.append(cand)
            elif _red_line_floor_applies(cand, judgment):
                final_active.append(cand)
                floored_ids.add(cand.obligation_id)
    final_active_ids = [c.obligation_id for c in final_active]

    # T-L0-13 (波1): confidence_map from resolver judgments (mechanical_hits 不在 map = 高置信)。
    confidence_map: dict[str, float] = {}
    if resolver_verdict is not None:
        for judgment in resolver_verdict.judgments:
            if judgment.hit:
                confidence_map[judgment.obligation_id] = judgment.confidence

    # T-L0-13: Stage 6 placement 移到 Stage 5 decision events **前** (Patch1 顺序) — 这样
    # ResolverDecisionMade.stage_6_placement 留痕非空, 且 low_confidence obligation 落 HIDDEN_REMINDER。
    placements = _placement_map(final_active, confidence_map)

    # ─── Stage 5-8 + Patch G: emit 序列 (T-L0-16: write 失败 → 受控 abort) ────────────
    # M-0.3 §2.8/§2.10 — 任一写入失败时不交付无留痕半截 capsule: emit CapsuleAssemblyFailed
    # 留痕 (失败 stage + 已写 partial event ids) 后抛受控 PipelineError。已写事件被 FAILED 标记
    # 作废 (append-only 无法物理回滚, 故用留痕逻辑作废而非物理 rollback)。
    written_event_ids: list[str] = []
    failed_stage = "stage_5_resolver_decision"
    try:
        # M-0.3 §2.5 + Patch G — ResolverDecisionMade + ObligationScopeJudged per candidate.
        resolver_decision_event_id = _emit_resolver_decision_made(
            event_log=event_log,
            task_id=task_id,
            input_hash=input_hash,
            as_of_projection_seq=as_of_seq,  # M-0.3 §9.1 — bundle.as_of_seq this decision judged at
            filter_outcome=filter_outcome,
            resolver_verdict=resolver_verdict,
            resolver_called=resolver_called,
            resolver_runtime_ms=resolver_runtime_ms,
            fallback_applied=fallback_applied,
            final_active_ids=final_active_ids,
            red_line_floored_ids=floored_ids,  # f-r12-4: red_line/invariant floor 留痕 (非静默覆盖)
            correlation_id=correlation_id,
            placements=placements,  # T-L0-13: stage_6_placement 留痕 (非空)
            cache_hit=cache_hit,  # §4.3 — true when stage 4 short-circuited via input_hash.idx
            cache_lookup_hash=cache_lookup_hash,  # §4.3 — the input_hash the cache hit on
            reentry=reentry,  # §5.2 — re-eval forced a full re-run
            reentry_reason=reentry_reason,  # §5.2 — why re-eval (reentry_context.reason)
            previous_verdict_event_id=previous_verdict_event_id,  # §5.2 — prior verdict link
            previous_run_id=previous_run_id,  # §5.2 — prior run id
        )
        written_event_ids.append(resolver_decision_event_id)

        failed_stage = "stage_5_scope_judged"
        scope_judged_event_ids = _emit_obligation_scope_judged(
            event_log=event_log,
            task_id=task_id,
            task_type=actual_task_type,
            target_artifacts=target_artifacts or [],
            filter_outcome=filter_outcome,
            resolver_verdict=resolver_verdict,
            resolver_decision_event_id=resolver_decision_event_id,
            correlation_id=correlation_id,
        )
        written_event_ids.extend(scope_judged_event_ids)

        # ─── Stage 6: context placement (T-L0-13: 已移到 Stage 5 前算, 见上 placements) ──
        # ─── Stage 7: capsule assembly (纯计算, 无写) ───────────────────────────
        # M-0.3 §2.7 — build 8 sections + truncation。
        # T-LS-3 (活标准第二根线): 识别这个 capsule 关于哪个模块 (拿 target_artifacts / touched_nodes
        # 跟对账卡 cards.json 的 code_paths 比), 命中就把那张卡 (该长啥样/built/enforced/红点是否过期)
        # 渲染成一小段, 与 KNOWN_FACTS 合并 —— agent 一开工就拿到模块标准。识别不到 / cards.json
        # 不存在 / git 不可用 → 空串 (优雅降级, 不报错), 既有 8 段行为不变 (additive)。
        conformance_card_text = render_conformance_card_section(
            towow_dir=projection_store._dir.parent,
            target_artifacts=target_artifacts or [],
            # T-BRAIN-01: module-card identification matches code_paths (file paths), not concept
            # ids — so this keeps the explicit caller touched_nodes; auto-derived concept anchors
            # would never match a code_path anyway (no behavior change for card matching).
            touched_nodes=touched_nodes or [],
        )
        failed_stage = "stage_7_capsule_assembly"
        capsule = _assemble_capsule_object(
            input_hash=input_hash,
            scene_type=scene_type,
            target_fork_id=target_fork_id,
            task_text=task_text,
            why_this_fork=why_this_fork,
            must_return=must_return,
            final_active=final_active,
            placements=placements,
            neighborhood=neighborhood,
            files_to_read=_files_to_read(target_artifacts or []),
            max_capsule_tokens=_max_capsule_tokens(model_tier, projection_store),  # T-L0-17
            concept_graph=concept_graph,  # T-L0-18: KNOWN_FACTS reduce_neighborhood_hops re-render
            # T-BRAIN-01: pass the resolved seeds (caller touched_nodes ∪ auto-derived concept
            # anchors), NOT the raw caller arg — else a token-truncation hops re-render
            # (_render_at_hops) would re-expand from empty seeds and silently drop the concept
            # graph back out of KNOWN_FACTS for big capsules.
            touched_nodes=neighborhood_seeds,
            # debt-e623ea238ded (件 B): caller 注入素材/约束进对应 section。
            caller_known_facts=list(known_facts or []),
            caller_must_not_do=list(must_not_do or []),
            # T-LS-3: 命中模块对账卡渲染文本 (空串=没命中, 优雅降级)。
            conformance_card_text=conformance_card_text,
        )

        # ─── Stage 8: CapsuleCompiled (path B) ─────────────────────────────────
        failed_stage = "stage_8_capsule_compiled"
        capsule_compiled_event_id = _emit_capsule_compiled(
            event_log=event_log,
            capsule=capsule,
            task_id=task_id,
            correlation_id=correlation_id,
            session_id=session_id,  # F-CIR-capsule-fork-not-session — per-session capsule attribution
            as_of_projection_seq=as_of_seq,  # M-0.3 §9.2 — bundle.as_of_seq baseline (Cap3)
            neighborhood_concept_ids=sorted(neighborhood.concept_ids),  # §2.2 — read_set #3 上界 (Cap5)
            source_event_refs=source_event_refs,  # §6/Patch2 — additional_event_reads (≤ as_of_seq)
            # §9.2/Patch4 审计指纹 inputs:
            template=template,
            bundle_hash=bundle_hash,
            resolver_input_hash=input_hash,
            caller_params={
                "task": task_text,
                "why_this_fork": why_this_fork,
                "must_return": must_return,
                # debt-e623ea238ded (件 B): caller_params 完整 — 注入素材/约束/写集声明也进审计指纹,
                # 改它们即改 capsule_assembly_hash (不同 caller 注入 → 不同 capsule, cache 不误命中)。
                "known_facts": "\n".join(known_facts or []),
                "must_not_do": "\n".join(must_not_do or []),
                "declared_write_set": ",".join(
                    f"{e.entity_type.value}:{e.entity_id}" for e in (declared_write_set or [])
                ),
            },
            # T-BRAIN-01: fingerprint over the seeds actually used (caller ∪ auto-derived),
            # so the audit指纹 reflects the real neighborhood that produced this capsule.
            touched_nodes=neighborhood_seeds,
        )
        written_event_ids.append(capsule_compiled_event_id)

        # ─── Patch G post-stage-8: ObligationActivated per final_active ──────
        failed_stage = "post_stage_8_obligation_activated"
        activated_event_ids = _emit_obligation_activated(
            event_log=event_log,
            task_id=task_id,
            capsule_compiled_event_id=capsule_compiled_event_id,
            resolver_decision_event_id=resolver_decision_event_id,
            final_active=final_active,
            resolver_hit_ids=resolver_hit_ids,
            floored_ids=floored_ids,  # f-r12-4: red_line/invariant floor → fallback_conservative source
            correlation_id=correlation_id,
        )
        written_event_ids.extend(activated_event_ids)
    except (OSError, RuntimeError, ValueError) as exc:
        # T-L0-16: write/装配 失败 → 留痕 + 受控抛错 (不返回引用幻象 capsule 的半截 result)。
        _abort_capsule_assembly(
            event_log,
            task_id=task_id,
            correlation_id=correlation_id,
            input_hash=input_hash,
            failed_stage=failed_stage,
            partial_event_ids=written_event_ids,
            final_error=str(exc),
        )
        if isinstance(exc, PipelineError):
            raise  # 内层已是受控错 (留痕已补) — 原样上抛, 不二次包裹
        msg = f"capsule assembly aborted at {failed_stage}: {exc}"
        raise PipelineError(msg) from exc

    return CapsulePipelineResult(
        capsule=capsule,
        capsule_compiled_event_id=capsule_compiled_event_id,
        resolver_decision_event_id=resolver_decision_event_id,
        scope_judged_event_ids=scope_judged_event_ids,
        activated_event_ids=activated_event_ids,
        final_active_set=final_active_ids,
        # debt-e623ea238ded (件 B): 回传 caller 声明的写集, session-start caller 注入同一集到 envelope。
        declared_write_set=list(declared_write_set or []),
    )


# ─── Stage 2: neighborhood seed derivation (T-BRAIN-01) ──────────────────────


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Order-stable dedupe (drops falsy) — keeps the neighborhood seed list deterministic.

    Determinism matters: the seed set feeds expand_neighborhood whose concept_ids flow into
    the cache key (input_hash) and the CapsuleCompiled audit fingerprint — a stable order
    keeps both reproducible across re-assembly of the same bundle.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _derive_task_concept_anchors(task_graph: object, task_id: str) -> list[str]:
    """T-BRAIN-01 — the task's own concept anchors, as concept-graph neighborhood seeds.

    Source: the task_graph projection node for `task_id`, its concept-typed read_set entries
    — the planner's `--read-set` concept claims (M-1.3 task-create), projected verbatim via
    TaskReadSetClaimed. (NOT `--concept-ref`/concept_refs, which only lands in
    after_state['concept_refs'] and is never projected into the node's read_set.) Those ARE
    the concepts the task is "about" — the neighborhood center the M-0.3 §3 scene templates
    describe ("task_scope.target_artifacts 对应的概念/文件节点"). Returning them lets the
    pipeline auto-seed the neighborhood instead of depending on each caller to hand-fill
    touched_nodes (the production-path gap that left ~95% of capsules with an empty concept
    neighborhood → empty KNOWN_FACTS → agent gets no concept graph).

    Reads the task_graph projection already in the scene bundle (EXECUTION/REVIEW/FIX/MISMATCH
    templates declare TASK_GRAPH in required_projections) — zero extra I/O, consistent with the
    pipeline's single fresh bundle read (§2.1). Defensive at every step: a missing/malformed
    task_graph, an absent task node, or a node with no concept read_set → [] (the caller then
    falls back to its explicit touched_nodes; no regression for task-less scenes whose bundle
    carries no task_graph).
    """
    if not isinstance(task_graph, dict):
        return []
    nodes = task_graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    for node in nodes:
        if not isinstance(node, dict) or node.get("task_id") != task_id:
            continue
        read_set = node.get("read_set")
        if not isinstance(read_set, list):
            return []
        anchors: list[str] = []
        for entry in read_set:
            if not isinstance(entry, dict):
                continue
            if entry.get("entity_type") == SubjectEntityType.CONCEPT.value:
                cid = entry.get("entity_id")
                if isinstance(cid, str) and cid:
                    anchors.append(cid)
        return anchors  # task_id is unique in task_graph — first match is authoritative
    return []


# ─── Stage 2: candidate retrieval ────────────────────────────────────────────


def _retrieve_candidates(
    obligation_proj: dict[str, object],
) -> list[_CandidateRecord]:
    """M-0.3 §2.2 — pull active obligations from projection (conservative superset).

    Every canonical_state=active obligation is a candidate; the §2.3 mechanical filter
    (now neighborhood-aware) does the real scoping and writes an explicit hit/miss event
    per candidate (M-0.6 §4.2.1 — single-obligation audit needs miss events too). Each
    record carries scope_hint + a precomputed concept_anchors set for the
    anchors∩neighborhood check.
    """
    nodes_raw = obligation_proj.get("obligations", [])
    if not isinstance(nodes_raw, list):
        return []
    candidates: list[_CandidateRecord] = []
    for node in nodes_raw:
        if not isinstance(node, dict):
            continue
        canonical_str = node.get("canonical_state")
        if not isinstance(canonical_str, str):
            continue
        if canonical_str != ObligationCanonicalState.ACTIVE.value:
            continue  # Patch H eligibility: exclude superseded / retired
        obligation_id = node.get("obligation_id")
        if not isinstance(obligation_id, str):
            continue
        scope_hint = _parse_scope_hint(node.get("scope_hint"))
        candidates.append(
            _CandidateRecord(
                obligation_id=obligation_id,
                nature=ObligationNature(node.get("nature", ObligationNature.INVARIANT.value)),
                severity=ObligationSeverity(
                    node.get("severity", ObligationSeverity.NORMAL.value),
                ),
                scope_type=ObligationScopeType(
                    node.get("scope_type", ObligationScopeType.GLOBAL.value),
                ),
                scope_rule=str(node.get("scope_rule", "")),
                definition=str(node.get("definition", "")),
                canonical_state=ObligationCanonicalState(canonical_str),
                scope_hint=scope_hint,
                concept_anchors=_collect_concept_anchors(scope_hint, node.get("attached_to")),
            ),
        )
    return candidates


def protocol_level_invariants(task_type: SceneType) -> list[_CandidateRecord]:
    """M-0.3 §2.2 — protocol-level invariant candidates for this task_type.

    Sourced from the canonical bootstrap definition list (M-0.6 §5.2 — the 4 v3 protocol invariants),
    NOT the obligation projection. This guarantees the v3 core invariants are capsule candidates by
    task_type even if the projection is missing them (defense-in-depth: protocol invariants are core
    guarantees, not contingent on a projection being materialized / intact).

    Relevance by scope_type mirrors the stage-3 mechanical filter so the injected set matches what
    stage 3 would hit: a GLOBAL invariant applies to every task_type; an ALL_PATCHES invariant only
    to patch-producing task_types (_PATCH_SCENE_TYPES); any other scope_type is carried (stage 3
    decides). The caller dedupes these against projection-sourced candidates by slug.
    """
    out: list[_CandidateRecord] = []
    for spec in BOOTSTRAP_OBLIGATION_SPECS:
        if spec.scope_type is ObligationScopeType.ALL_PATCHES and task_type not in _PATCH_SCENE_TYPES:
            continue  # an all_patches invariant is not relevant to a non-patch task type
        out.append(_candidate_from_spec(spec))
    return out


def _candidate_from_spec(spec: BootstrapObligationSpec) -> _CandidateRecord:
    """Synthesize a _CandidateRecord from a bootstrap protocol-invariant spec.

    obligation_id = the bare slug (deterministic, no uuid) so dedupe against a captured
    '{slug}-{uuid}' is by prefix. concept_anchors from the concept-typed attached_to refs.
    """
    concept_anchors = frozenset(
        eid
        for etype, eid in spec.attached_to
        if etype == SubjectEntityType.CONCEPT.value and eid
    )
    return _CandidateRecord(
        obligation_id=spec.slug,
        nature=spec.nature,
        severity=spec.severity,
        scope_type=spec.scope_type,
        scope_rule=spec.scope_rule,
        definition=spec.definition,
        canonical_state=ObligationCanonicalState.ACTIVE,
        scope_hint=None,  # GLOBAL / ALL_PATCHES go to mechanical hit/miss — no resolver scope_hint needed
        concept_anchors=concept_anchors,
    )


def _merge_protocol_invariants(
    projection_candidates: list[_CandidateRecord],
    protocol_candidates: list[_CandidateRecord],
) -> list[_CandidateRecord]:
    """M-0.3 §2.2 dedupe_and_filter_active — add protocol invariants not already in the projection set.

    A protocol invariant captured into the projection appears with id '{slug}-{uuid}'; the synthetic
    candidate uses the bare slug. Dedup by slug-prefix so the captured one (real obligation_id →
    correct downstream ObligationScopeJudged / ObligationActivated provenance) wins, and the synthetic
    is only appended when the projection is missing that invariant (a gap). Order preserved:
    projection-sourced first, then any gap-filling protocol invariants.
    """
    existing_ids = [c.obligation_id for c in projection_candidates]
    merged = list(projection_candidates)
    for pc in protocol_candidates:
        slug = pc.obligation_id
        already = any(eid == slug or eid.startswith(slug + "-") for eid in existing_ids)
        if not already:
            merged.append(pc)
    return merged


def _parse_scope_hint(raw: object) -> ScopeHint | None:
    """Reconstruct a ScopeHint from the projection node dict (None if absent / malformed)."""
    if not isinstance(raw, dict):
        return None
    try:
        return ScopeHint.model_validate(raw)
    except (ValueError, TypeError):
        return None


def _collect_concept_anchors(
    scope_hint: ScopeHint | None,
    attached_to: object,
) -> frozenset[str]:
    """Anchor set for the M-0.3 §2.3 anchors∩neighborhood check.

    Primary source is scope_hint.concept_anchors (M-0.6 §2.1 — the designated mechanical
    filter assist for concept_neighborhood). Falls back to the concept-typed attached_to
    refs (the same node set §2.2 uses for candidate recall), so a concept_neighborhood
    obligation that declared only attached_to is still scoped correctly.
    """
    anchors: set[str] = set()
    if scope_hint is not None and scope_hint.concept_anchors:
        anchors.update(scope_hint.concept_anchors)
    if isinstance(attached_to, list):
        for ref in attached_to:
            if isinstance(ref, dict) and ref.get("entity_type") == SubjectEntityType.CONCEPT.value:
                eid = ref.get("entity_id")
                if isinstance(eid, str) and eid:
                    anchors.add(eid)
    return frozenset(anchors)


# ─── Stage 3: mechanical filter ──────────────────────────────────────────────


_PATCH_SCENE_TYPES: frozenset[SceneType] = frozenset(
    {
        SceneType.EXECUTION,
        SceneType.FIX,
        SceneType.ENGINEERING_CONSENSUS,
        SceneType.PLANNING,
    },
)


def _mechanical_filter(
    candidates: list[_CandidateRecord],
    task_type: SceneType,
    neighborhood: Neighborhood,
    target_artifacts: list[str] | None = None,
) -> _MechanicalFilterOutcome:
    """M-0.3 §2.3 — split by scope_type. Pure computation, no side effects."""
    task_artifacts = frozenset(target_artifacts or [])
    hits: list[_CandidateRecord] = []
    misses: list[_CandidateRecord] = []
    needs_resolver: list[_CandidateRecord] = []
    for cand in candidates:
        if cand.scope_type is ObligationScopeType.GLOBAL:
            hits.append(cand)
        elif cand.scope_type is ObligationScopeType.ALL_PATCHES:
            if task_type in _PATCH_SCENE_TYPES:
                hits.append(cand)
            else:
                misses.append(cand)
        elif cand.scope_type is ObligationScopeType.SPECIFIC_ARTIFACT:
            # M-0.3 §2.3 / M-0.6 §2.1 (L79/L89) — specific_artifact 是机械可判: obligation 声明的目标
            # artifact (scope_hint.target_artifacts) 与 task 声明的 target_artifacts 字符串相交 → hit,
            # 否则 miss (机械判定, 不送 resolver)。obligation 未声明目标 / task 未声明 artifact →
            # 交集必空 → 保守 miss (不误命中)。
            if _artifact_match(cand.scope_hint, task_artifacts):
                hits.append(cand)
            else:
                misses.append(cand)
        elif cand.scope_type is ObligationScopeType.CONCEPT_NEIGHBORHOOD:
            # M-0.3 §2.3 — 邻域命中是必要不充分: anchors∩neighborhood non-empty → needs_resolver
            # (resolver / fallback judges semantic sufficiency); empty → mechanical miss.
            if cand.concept_anchors & neighborhood.concept_ids:
                needs_resolver.append(cand)
            else:
                misses.append(cand)
        else:  # SEMANTIC_DEPENDENT — always full semantic judgment
            needs_resolver.append(cand)
    return _MechanicalFilterOutcome(
        mechanical_hits=hits,
        mechanical_misses=misses,
        needs_resolver=needs_resolver,
    )


def _artifact_match(scope_hint: ScopeHint | None, task_artifacts: frozenset[str]) -> bool:
    """M-0.3 §2.3 / M-0.6 §2.1 — specific_artifact 机械 artifact_match (字符串相交)。

    obligation 声明的目标 artifact (scope_hint.target_artifacts) 与 task 声明的 target_artifacts
    任一相交即命中。任一侧为空 (obligation 未填目标 / task 未声明 artifact) → 交集必空 → False
    (保守 miss, 不误命中)。纯字符串匹配 — 与 §2.1 "跟 attached_to 字符串匹配" 一致, 不依赖
    at_reference_graph 路径展开 (那是更精细的 @ 引用解析, 非机械 scope 判定所需)。
    """
    if not task_artifacts or scope_hint is None or not scope_hint.target_artifacts:
        return False
    return bool(frozenset(scope_hint.target_artifacts) & task_artifacts)


# ─── Stage 4 prep: build ResolverInput ───────────────────────────────────────


def _build_resolver_input(
    task_scope: TaskScope,
    needs_resolver: list[_CandidateRecord],
    neighborhood: Neighborhood,
    resolver_timeout_ms_value: int,
    contract_version: str = "1.0.0",
) -> ResolverInput:
    """Build M-0.6 §6.2 ResolverInput — candidates carry scope_hint; projection_slice
    carries the real N-hop concept-graph neighborhood (no longer empty).

    T-L0-21 (波2): resolver_config.timeout_ms 取自 `.towow/capsule-assembler/config.json` 的
    RESOLVER_TIMEOUT_MS (§3.4, spec §10 — 此前硬编码 ResolverConfig() 默认 30000, config 不生效)。
    改 config 即改 pipeline 给 resolver 的超时合约。

    Cap3 (RUN-038, M-0.6 §6.6): resolver_config.contract_version 取自 obligation-system config
    (resolver_contract_version), 与 cache key (input_hash) 折进的同一个 contract_version 值, 故合约
    bump 一致地流进 verdict meta + cache key (一处改, 两处都失效旧 cache)。
    """
    return ResolverInput(
        task_scope=task_scope,
        candidates=[
            ObligationCandidate(
                obligation_id=c.obligation_id,
                nature=c.nature,
                severity=c.severity,
                scope_type=c.scope_type,
                scope_rule=c.scope_rule,
                scope_hint=c.scope_hint,
                definition=c.definition,
            )
            for c in needs_resolver
        ],
        projection_slice=ProjectionSlice(
            concept_graph_neighborhood=ConceptGraphNeighborhood(
                nodes=list(neighborhood.nodes),
                edges=list(neighborhood.edges),
            ),
        ),
        resolver_config=ResolverConfig(
            timeout_ms=resolver_timeout_ms_value,
            contract_version=contract_version,
        ),
    )


# ─── Stage 4 resolver selection (RUN-049, M-0.6 §6.4) ────────────────────────


def _select_resolver(towow_dir: Path) -> Callable[[ResolverInput], ResolverVerdict] | None:
    """RUN-049: 按 mode 选 resolver callable —— 终结"永远机械兜底"。

    返回 None → call_resolver 用默认 conservative_fallback_verdict (机械保守兜底, CI/测试缺省)。
    返回 bind_llm_resolver(...) → 真起 claude --bg 喂 §6.4 prompt 出真 verdict (生产默认 llm),
    或 replay 模式从 verdict 文件读 (集成测试注入真 verdict, 不起 claude)。

    mode 来源 (config.resolve_obligation_resolver_mode): env > obligation-system/config.json > llm。
    """
    mode = resolve_obligation_resolver_mode(towow_dir)
    if mode == "fallback":
        return None  # 机械保守兜底 (conservative_fallback_verdict)
    prompts_dir = towow_dir / "obligation-system" / "resolver-prompts"
    repo_dir = towow_dir.parent
    model = obligation_resolver_model_override(towow_dir)
    if mode == "replay":
        replay = obligation_resolver_replay_file(towow_dir)
        from pathlib import Path as _Path

        return bind_llm_resolver(
            prompts_dir=prompts_dir,
            repo_dir=repo_dir,
            mode=MODE_REPLAY,
            replay_file=_Path(replay) if replay else None,
            model=model,
        )
    return bind_llm_resolver(
        prompts_dir=prompts_dir,
        repo_dir=repo_dir,
        mode=MODE_REAL,
        model=model,
    )


# ─── Stage 4 cache lookup (M-0.3 §4.2 / §4.3) ────────────────────────────────


_RESOLVER_ID = "obligation_activation_resolver"


def _cache_lookup(
    event_log: EventLog,
    input_hash: str,
    needs_resolver: list[_CandidateRecord],
) -> ResolverVerdict | None:
    """M-0.3 §4.2 — look up a cached ResolverDecisionMade by input_hash and rebuild its verdict.

    Cache storage IS the M-0.1 event log (§4.2 / §11.3 — "历史 ResolverDecisionMade event 就是
    cache", keyed on input_hash.idx, no separate cache file). On a hit we reconstruct the
    ResolverVerdict from the cached event's stage_4 judgments so stage 4 can be skipped and the
    exact same judgments reused (§4.3 — reused result must equal a re-run, else the cache is a bug).

    Returns None (cache miss) when:
      - no event indexed under (resolver_id, input_hash), or
      - the cached judgments do not cover exactly the current needs_resolver set (defensive:
        a stale / schema-incompatible verdict is treated as a miss → resolver re-runs).
    """
    cached_rec = event_log.get_event_by_input_hash(_RESOLVER_ID, input_hash)
    if cached_rec is None:
        return None
    after = cached_rec.payload.get("after_state")
    if not isinstance(after, dict):
        return None
    stages = after.get("pipeline_stages")
    if not isinstance(stages, dict):
        return None
    stage_4 = stages.get("stage_4_semantic_judgment")
    if not isinstance(stage_4, dict):
        return None
    raw_judgments = stage_4.get("judgments")
    if not isinstance(raw_judgments, list):
        return None  # the cached decision had no resolver judgments to reuse

    judgments: list[ObligationJudgment] = []
    for raw in raw_judgments:
        if not isinstance(raw, dict):
            return None
        try:
            confidence = float(raw["confidence"])
            judgment = ObligationJudgment(
                obligation_id=str(raw["obligation_id"]),
                hit=bool(raw["hit"]),
                confidence=confidence,
                reason=str(raw["reason"]),
                evidence=[],
                # uncertainty required by schema when confidence < 0.7; the cached summary
                # trims it, so supply a reconstruction marker (the judgment itself is reused).
                uncertainty=("reused from cached resolver verdict" if confidence < 0.7 else None),
                fallback_applied=bool(raw.get("fallback_applied", False)),
            )
        except (KeyError, ValueError, TypeError):
            return None  # malformed cached judgment → treat as miss, resolver re-runs
        judgments.append(judgment)
    if not judgments:
        return None

    # The rebuilt verdict must cover exactly the current needs_resolver set; otherwise the cached
    # entry is for a different candidate set (should not happen — candidate ids are in the key — but
    # treat any mismatch as a miss so the resolver re-runs rather than reuse a wrong verdict).
    cached_ids = {j.obligation_id for j in judgments}
    needs_ids = {c.obligation_id for c in needs_resolver}
    if cached_ids != needs_ids:
        return None

    meta = VerdictMeta(
        verdict_id=f"vid-cached-{uuid.uuid4().hex[:12]}",
        resolver_runtime_ms=0,  # reused — no resolver runtime
        model="cache_reuse",
        contract_version=ResolverConfig().contract_version,
        total_candidates=len(judgments),
        total_hits=sum(1 for j in judgments if j.hit),
        total_fallbacks=sum(1 for j in judgments if j.fallback_applied),
    )
    return ResolverVerdict(judgments=judgments, meta=meta)


# ─── Stage 5a: emit ResolverDecisionMade ─────────────────────────────────────


def _emit_resolver_decision_made(
    *,
    event_log: EventLog,
    task_id: str,
    input_hash: str,
    as_of_projection_seq: int,
    filter_outcome: _MechanicalFilterOutcome,
    resolver_verdict: ResolverVerdict | None,
    resolver_called: bool,
    resolver_runtime_ms: int | None,
    fallback_applied: bool,
    final_active_ids: list[str],
    red_line_floored_ids: set[str] | None = None,
    correlation_id: str,
    placements: dict[str, CapsuleSection] | None = None,
    cache_hit: bool = False,
    cache_lookup_hash: str | None = None,
    reentry: bool = False,
    reentry_reason: str = "",
    previous_verdict_event_id: str | None = None,
    previous_run_id: str | None = None,
) -> str:
    """M-0.3 §2.5 + §9.1 — single aggregate ResolverDecisionMade event."""
    stage_4: dict[str, object] = {
        "resolver_called": resolver_called,
        "candidate_count": len(filter_outcome.needs_resolver),
    }
    if resolver_verdict is not None:
        stage_4["judgments"] = [
            {
                "obligation_id": j.obligation_id,
                "hit": j.hit,
                "confidence": j.confidence,
                "reason": j.reason,
                "fallback_applied": j.fallback_applied,
            }
            for j in resolver_verdict.judgments
        ]
    if red_line_floored_ids:
        # f-r12-4: red_line/invariant candidates force-included despite a low-confidence
        # resolver hit=false. Recorded so the floor is auditable, never a silent override.
        stage_4["red_line_floored"] = sorted(red_line_floored_ids)

    payload: dict[str, object] = {
        "judgment_type": JudgmentType.RESOLVER_DECISION.value,
        "after_state": {
            "resolver_id": "obligation_activation_resolver",
            "task_id": task_id,
            "input_hash": input_hash,
            "pipeline_stages": {
                "stage_2_candidate_retrieval": {
                    "candidate_count": (
                        len(filter_outcome.mechanical_hits)
                        + len(filter_outcome.mechanical_misses)
                        + len(filter_outcome.needs_resolver)
                    ),
                },
                "stage_3_mechanical_filtering": {
                    "mechanical_hit_count": len(filter_outcome.mechanical_hits),
                    "mechanical_miss_count": len(filter_outcome.mechanical_misses),
                    "needs_resolver_count": len(filter_outcome.needs_resolver),
                },
                "stage_4_semantic_judgment": stage_4,
                # T-L0-13 (波1): 真 placement 留痕 (obligation_id → section); 现状曾硬编码 []。
                "stage_6_placement": [
                    {"obligation_id": oid, "section": section.value}
                    for oid, section in sorted((placements or {}).items())
                ],
            },
            "final_active_set": final_active_ids,
            "uncertainties": [],
            # §4.3 — cache_hit=true + cache_lookup_hash when stage 4 short-circuited (debt-b529da25797e);
            # resolver_runtime_ms is null on a cache hit (resolver not invoked, §9.1).
            "cache_hit": cache_hit,
            "cache_lookup_hash": cache_lookup_hash,
            "resolver_runtime_ms": resolver_runtime_ms,
            # §9.1 — the committed cutoff (bundle.as_of_seq) this decision judged at; same value as
            # its sibling CapsuleCompiled (§9.2) — both read against the one stage-1 fresh bundle.
            "as_of_projection_seq": as_of_projection_seq,
            # §5.2 — re-evaluation trail: reentry flag + why-re-eval context (null on normal calls).
            "reentry": reentry,
            "reentry_context": (
                {
                    "reason": reentry_reason or "re-evaluation",
                    "previous_verdict_event_id": previous_verdict_event_id,
                    "previous_run_id": previous_run_id,
                }
                if reentry
                else None
            ),
        },
        "confidence": None,
        "fallback_applied": fallback_applied,
    }
    intent = EventIntent(
        local_intent_id=f"rdm-{uuid.uuid4().hex[:12]}",
        event_type=EventType.RESOLVER_DECISION_MADE,
        event_category=EventCategory.SEMANTIC_JUDGMENT,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.CAPSULE_ASSEMBLER.value,
            actor_id="m03-capsule-assembler",
            task_id=task_id,
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CAPSULE,
                entity_id=correlation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


# ─── Stage 5b: emit ObligationScopeJudged per candidate ──────────────────────


def _emit_obligation_scope_judged(
    *,
    event_log: EventLog,
    task_id: str,
    task_type: SceneType,
    target_artifacts: list[str],
    filter_outcome: _MechanicalFilterOutcome,
    resolver_verdict: ResolverVerdict | None,
    resolver_decision_event_id: str,
    correlation_id: str,
) -> list[str]:
    """M-0.3 Patch G — one ObligationScopeJudged event per candidate (hit / miss / uncertain).

    M-0.6 §4.2.1: hit / miss / uncertain ALL get written — single-obligation
    audit trace requires miss events too.
    """
    event_ids: list[str] = []
    task_scope_payload = {
        "task_id": task_id,
        "task_type": _scene_to_task_type_str(task_type),
        "target_artifacts": target_artifacts,
    }

    def emit_one(
        cand: _CandidateRecord,
        judgment: ObligationScopeJudgment,
        confidence: float,
        reason: str,
    ) -> str:
        payload = {
            "judgment_type": JudgmentType.OBLIGATION_SCOPE.value,
            "after_state": {
                "obligation_id": cand.obligation_id,
                "task_scope": task_scope_payload,
                "judgment": judgment.value,
                "scope_rule_applied": cand.scope_rule,
            },
            "confidence": confidence,
            "evidence": [
                {
                    "source_type": "event",
                    "source_id": resolver_decision_event_id,
                    "relevance": reason,
                },
            ],
            "uncertainty": None,
        }
        intent = EventIntent(
            local_intent_id=f"osj-{uuid.uuid4().hex[:12]}",
            event_type=EventType.OBLIGATION_SCOPE_JUDGED,
            event_category=EventCategory.SEMANTIC_JUDGMENT,
            payload=payload,
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.CAPSULE_ASSEMBLER.value,
                actor_id="m03-capsule-assembler",
                task_id=task_id,
                correlation_id=correlation_id,
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.OBLIGATION,
                    entity_id=cand.obligation_id,
                    role=SubjectRole.PRIMARY,
                ),
                Subject(
                    entity_type=SubjectEntityType.CAPSULE,
                    entity_id=correlation_id,
                    role=SubjectRole.AFFECTED,
                ),
            ],
            schema_version="1.0.0",
        )
        return event_log.write_direct(intent).event_id

    for cand in filter_outcome.mechanical_hits:
        event_ids.append(
            emit_one(
                cand,
                ObligationScopeJudgment.IN_SCOPE,
                1.0,
                "mechanical_filter hit",
            ),
        )
    for cand in filter_outcome.mechanical_misses:
        event_ids.append(
            emit_one(
                cand,
                ObligationScopeJudgment.OUT_OF_SCOPE,
                1.0,
                "mechanical_filter miss",
            ),
        )
    if resolver_verdict is not None:
        verdict_by_id = {j.obligation_id: j for j in resolver_verdict.judgments}
        for cand in filter_outcome.needs_resolver:
            j = verdict_by_id.get(cand.obligation_id)
            if j is None:
                continue
            event_ids.append(
                emit_one(
                    cand,
                    (
                        ObligationScopeJudgment.IN_SCOPE
                        if j.hit
                        else ObligationScopeJudgment.OUT_OF_SCOPE
                    ),
                    j.confidence,
                    j.reason,
                ),
            )
    return event_ids


def _scene_to_task_type_str(scene_type: SceneType) -> str:
    """Map M-0.3 scene_type → M-0.1 §3.2 TaskScopeRef.task_type (TaskType enum value).

    TaskScopeRef uses TaskType (6 values); scene_type uses SceneType (10 values, +Patch K).
    E.1.5: conservative mapping — execution/fix/review/maintain straight, others → code_change.
    """
    mapping: dict[SceneType, str] = {
        SceneType.EXECUTION: "code_change",
        SceneType.FIX: "fix",
        SceneType.REVIEW: "review",
        SceneType.MAINTENANCE: "maintain",
        SceneType.INTERVIEW: "doc_change",
        SceneType.ENGINEERING_CONSENSUS: "design_change",
        SceneType.PLANNING: "design_change",
        SceneType.MISMATCH: "review",
        SceneType.AUDIT: "review",
        # M-0.7 §10.3 Patch K — consolidation run is a maintenance-class operation (produces
        # digest + snapshot + archive); task_type granularity folds it into "maintain".
        SceneType.CONSOLIDATION: "maintain",
    }
    return mapping[scene_type]


# ─── Stage 6: placement ─────────────────────────────────────────────────────


_LOW_CONFIDENCE_THRESHOLD = 0.7  # T-L0-13 M-0.3 §2.6 — 低于此 resolver 置信视为 low_confidence


def _placement_map(
    final_active: list[_CandidateRecord],
    confidence_map: dict[str, float] | None = None,
) -> dict[str, CapsuleSection]:
    """M-0.3 §2.6 — obligation_id → CapsuleSection by severity / nature / confidence.

    T-L0-13 (波1): low_confidence obligation (resolver 置信 < 阈值) → HIDDEN_REMINDER 软提醒区
    (不强断言, 仅提示, 避免低置信判断当确定约束误导)。red_line 优先 (永远显著, 不因低置信降级);
    mechanical_hits 不在 confidence_map (机械命中=高置信)。
    """
    conf = confidence_map or {}
    placements: dict[str, CapsuleSection] = {}
    for cand in final_active:
        c = conf.get(cand.obligation_id)
        if cand.severity is ObligationSeverity.RED_LINE:
            placements[cand.obligation_id] = CapsuleSection.RED_LINE_OBLIGATIONS
        elif c is not None and c < _LOW_CONFIDENCE_THRESHOLD:
            placements[cand.obligation_id] = CapsuleSection.HIDDEN_REMINDER
        elif cand.nature is ObligationNature.TASK_CONSTRAINT:
            placements[cand.obligation_id] = CapsuleSection.TASK_BRIEFING
        elif cand.nature is ObligationNature.TOOL_SPECIFIC:
            placements[cand.obligation_id] = CapsuleSection.TOOL_CONTEXT
        else:
            placements[cand.obligation_id] = CapsuleSection.MUST_NOT_DO
    return placements


def _red_line_floor_applies(
    cand: _CandidateRecord,
    judgment: ObligationJudgment | None,
) -> bool:
    """f-r12-4: defense-in-depth floor for the resolver SUCCESS path.

    Return True when a red_line-severity OR invariant-nature candidate was dropped by the
    resolver (hit=false) at LOW confidence (< _LOW_CONFIDENCE_THRESHOLD) and so must be
    force-included into the active set. A high-confidence drop
    (confidence >= _LOW_CONFIDENCE_THRESHOLD) IS respected — the resolver is trusted when it
    is sure. This makes the F1 fail-closed guarantee not depend on the resolver fork never
    being buggy or prompt-injected: a low-confidence / garbage hit=false can no longer
    silently drop a red_line obligation. The conservative_fallback path already force-includes
    every candidate when the resolver is unreachable; this gives the success path a matching
    (narrower: red_line/invariant + low-confidence only) floor, so the two paths are no longer
    asymmetric about red_line obligations.
    """
    if judgment is None or judgment.hit:
        return False
    is_red_line = cand.severity is ObligationSeverity.RED_LINE
    is_invariant = cand.nature is ObligationNature.INVARIANT
    if not (is_red_line or is_invariant):
        return False
    return judgment.confidence < _LOW_CONFIDENCE_THRESHOLD


# ─── Stage 7: assemble CapsuleResult ────────────────────────────────────────


_TASK_BRIEFING_NOTE = (
    "Capsule produced by M-0.3 pipeline E.1.5; resolver fallback may apply"
)


def _assemble_capsule_object(
    *,
    input_hash: str,
    scene_type: SceneType,
    target_fork_id: str,
    task_text: str,
    why_this_fork: str,
    must_return: str,
    final_active: list[_CandidateRecord],
    placements: dict[str, CapsuleSection],
    neighborhood: Neighborhood,
    files_to_read: list[str],
    max_capsule_tokens: int = 80000,  # T-L0-17: 由 caller model_tier 分级 (现状曾硬编码 8000)
    concept_graph: dict[str, object] | None = None,  # T-L0-18: hops re-render source
    touched_nodes: list[str] | None = None,  # T-L0-18: neighborhood seeds for hops re-render
    caller_known_facts: list[str] | None = None,  # 件 B: caller 注入的已知事实 → KNOWN_FACTS
    caller_must_not_do: list[str] | None = None,  # 件 B: caller 注入的硬约束 → MUST_NOT_DO
    conformance_card_text: str = "",  # T-LS-3: 命中模块对账卡渲染文本 → KNOWN_FACTS (空=没命中)
) -> CapsuleResult:
    """M-0.3 §2.7 — assemble 8 sections (priority order in CapsuleSection enum).

    T-L0-18/19 (波2): after assembly, if estimated_tokens > max_tokens the capsule is
    truncated in priority order (§7.2) — RED_LINE/TASK/MUST_RETURN never cut; KNOWN_FACTS
    reduces neighborhood hops; TASK_BRIEFING/MUST_NOT_DO summarize; FILES_TO_READ/TOOL_CONTEXT
    reduce items. truncation_applied + truncation_log留痕 the cuts. Still over-limit after every
    truncatable section is exhausted → PipelineError("capsule_too_large_after_truncation").
    """
    # Group obligations by placement.
    by_section: dict[CapsuleSection, list[_CandidateRecord]] = {}
    for cand in final_active:
        section = placements[cand.obligation_id]
        by_section.setdefault(section, []).append(cand)

    sections: list[CapsuleSectionContent] = []

    def add_section(section: CapsuleSection, content: str) -> None:
        if not content.strip():
            return
        sections.append(
            CapsuleSectionContent(
                section=section,
                content=content,
                estimated_tokens=max(1, len(content) // 4),
            ),
        )

    add_section(
        CapsuleSection.RED_LINE_OBLIGATIONS,
        _render_obligations(by_section.get(CapsuleSection.RED_LINE_OBLIGATIONS, [])),
    )
    add_section(
        CapsuleSection.TASK,
        f"{task_text}\n\nwhy this fork: {why_this_fork}".strip(),
    )
    add_section(CapsuleSection.MUST_RETURN, must_return)
    add_section(
        CapsuleSection.MUST_NOT_DO,
        # 件 B: caller 注入的硬约束 (must_not_do) 与 obligation 渲染合并进 MUST_NOT_DO section。
        _join_nonempty(
            _render_obligations(by_section.get(CapsuleSection.MUST_NOT_DO, [])),
            _render_caller_lines(caller_must_not_do),
        ),
    )
    add_section(
        CapsuleSection.TASK_BRIEFING,
        (
            _render_obligations(by_section.get(CapsuleSection.TASK_BRIEFING, []))
            or _TASK_BRIEFING_NOTE
        ),
    )
    add_section(
        CapsuleSection.TOOL_CONTEXT,
        _render_obligations(by_section.get(CapsuleSection.TOOL_CONTEXT, [])),
    )

    # M-0.3 §2.7 — KNOWN_FACTS = concept-graph neighborhood slice (concept definitions);
    # FILES_TO_READ = scene_template.files_to_read(task_scope) (block-2 minimal source =
    # task_scope.target_artifacts; full @-reference expansion lands with at_reference_graph).
    # 件 B: caller 注入的已知事实 (known_facts) 与 neighborhood 渲染合并进 KNOWN_FACTS section。
    # T-LS-3: 命中模块的对账卡文本也并进 KNOWN_FACTS (放最前, 让 agent 第一眼看到模块标准/红点);
    # 空串 (没命中模块) 时 _join_nonempty 自动丢掉, KNOWN_FACTS 行为与改前完全一致 (additive)。
    add_section(
        CapsuleSection.KNOWN_FACTS,
        _join_nonempty(
            conformance_card_text or "",
            _render_neighborhood(neighborhood),
            _render_caller_lines(caller_known_facts),
        ),
    )
    add_section(CapsuleSection.FILES_TO_READ, _render_files_to_read(files_to_read))

    if not sections:
        # Capsule must have at least 1 section per schema (sections min_length=1).
        sections.append(
            CapsuleSectionContent(
                section=CapsuleSection.TASK,
                content=task_text or "(no task body supplied)",
                estimated_tokens=8,
            ),
        )

    assembled = CapsuleResult(
        input_hash=input_hash,
        scene_type=scene_type,
        target_fork_id=target_fork_id,
        sections=sections,
        estimated_tokens=sum(s.estimated_tokens for s in sections),
        max_tokens=max_capsule_tokens,  # T-L0-17: 分级 token 上限 (替换硬编码 8000)
        truncation_applied=False,
        truncation_log=[],
    )

    # T-L0-18/19 (M-0.3 §7.2): priority-based truncation when over the token limit.
    # KNOWN_FACTS reduce_neighborhood_hops re-renders from the concept_graph + seeds at
    # decreasing hops; other sections summarize / reduce items. No-op when already fits.
    def _render_at_hops(_nb: Neighborhood, hops: int) -> str:
        reduced = expand_neighborhood(concept_graph, touched_nodes or [], hops=hops)
        return _render_neighborhood(reduced)

    try:
        return truncate_capsule(
            assembled,
            neighborhood=neighborhood,
            render_neighborhood=_render_at_hops,
        )
    except _TruncationAbort as exc:
        raise PipelineError("capsule_too_large_after_truncation") from exc


def _render_obligations(cands: list[_CandidateRecord]) -> str:
    if not cands:
        return ""
    lines = [f"- [{c.obligation_id}] {c.definition}" for c in cands]
    return "\n".join(lines)


def _render_caller_lines(items: list[str] | None) -> str:
    """件 B (debt-e623ea238ded) — render caller-injected known_facts / must_not_do as bullets."""
    if not items:
        return ""
    return "\n".join(f"- {item}" for item in items if item and item.strip())


def _join_nonempty(*chunks: str) -> str:
    """Join non-empty rendered chunks with a blank line (drops empties so add_section's
    strip() floor is honored — an all-empty join yields '' and the section is skipped)."""
    return "\n".join(c for c in chunks if c and c.strip())


def _render_neighborhood(neighborhood: Neighborhood) -> str:
    """M-0.3 §2.7 KNOWN_FACTS — render concept-graph neighborhood nodes as facts."""
    lines: list[str] = []
    for node in neighborhood.nodes:
        cid = node.get("concept_id")
        if not isinstance(cid, str) or not cid:
            continue
        name = node.get("name")
        definition = node.get("definition")
        label = f"{name} — " if isinstance(name, str) and name else ""
        body = definition if isinstance(definition, str) and definition else ""
        lines.append(f"- [{cid}] {label}{body}".rstrip(" —"))
    return "\n".join(lines)


def _files_to_read(target_artifacts: list[str]) -> list[str]:
    """M-0.3 §2.7 scene_template.files_to_read(task_scope) — block-2 minimal real source.

    The caller-declared target_artifacts are the floor; full neighborhood @-reference
    expansion is deferred to the at_reference_graph reducer (tracked with the other 6
    projection reducers). Dedup + order-stable.
    """
    seen: set[str] = set()
    files: list[str] = []
    for art in target_artifacts:
        if art and art not in seen:
            seen.add(art)
            files.append(art)
    return files


def _render_files_to_read(files: list[str]) -> str:
    if not files:
        return ""
    return "\n".join(f"- {f}" for f in files)


# ─── Stage 7 additional_event_reads (M-0.3 §6 / Patch 2) ─────────────────────


@dataclass(frozen=True)
class _SourceEventRef:
    """One additional_event_read consumed at assembly (M-0.3 §6 / Patch 4 source_event_refs).

    Records the event_id + event_type + seq the capsule材料 came from, so the CapsuleCompiled
    audit指纹 can prove "这条 capsule 引用了哪些 event"。
    """

    event_id: str
    event_type: str
    seq: int


def _read_additional_events(
    event_log: EventLog,
    template_event_types: list[EventType],
    as_of_seq: int,
) -> list[_SourceEventRef]:
    """M-0.3 §6 + Patch 2 (L852..L868) — consume the scene template's additional_event_reads,
    bounded to the bundle's committed cutoff.

    Patch 2 硬协议: capsule 的所有素材必须来自同一个 committed cutoff (= bundle.as_of_seq)。
    additional_event_reads 必须 `read_events_by_type(type, until_seq=bundle.as_of_seq)` — 任何
    seq > as_of_seq 的 event (在 bundle 读取之后才写入) 不得被本次组装使用 (否则造成时间裂缝)。
    M-0.1 的 get_events_by_type 无 until_seq 参数, 故在此按 sequence_number <= as_of_seq 过滤。

    去重 (按 event_id) + 按 seq 升序排, 让 source_event_refs 确定性 (同一 bundle 截点重组 capsule
    引用列表稳定 → 指纹稳定)。空模板 / 无匹配事件 → 空列表 (正常, 不是失败)。
    """
    seen: set[str] = set()
    refs: list[_SourceEventRef] = []
    for event_type in template_event_types:
        for rec in event_log.get_events_by_type(event_type):
            # Patch 2 — 绑定 as_of_seq: 截点之后写入的 event 看不到 (一致性, 非 bug)。
            if rec.sequence_number > as_of_seq:
                continue
            if rec.event_id in seen:
                continue
            seen.add(rec.event_id)
            refs.append(
                _SourceEventRef(
                    event_id=rec.event_id,
                    event_type=rec.event_type.value,
                    seq=rec.sequence_number,
                ),
            )
    refs.sort(key=lambda r: r.seq)
    return refs


# ─── Stage 8: emit CapsuleCompiled ──────────────────────────────────────────


def _emit_capsule_compiled(
    *,
    event_log: EventLog,
    capsule: CapsuleResult,
    task_id: str,
    correlation_id: str,
    session_id: str | None = None,  # F-CIR-capsule-fork-not-session — owning session unit (or None)
    as_of_projection_seq: int = 0,
    neighborhood_concept_ids: list[str] | None = None,
    source_event_refs: list[_SourceEventRef] | None = None,
    template: SceneTemplate | None = None,  # §9.2/Patch4 — template_id / template_version
    bundle_hash: str = "",  # §9.2/Patch4 — projection_bundle_hash
    resolver_input_hash: str = "",  # §9.2/Patch3 — resolver_input_hash → capsule_assembly_hash
    caller_params: dict[str, str] | None = None,  # §9.2/Patch4 — caller_params_hash
    touched_nodes: list[str] | None = None,  # §9.2/Patch4 — touched_nodes
) -> str:
    """M-0.3 §2.8 + §9.2 — CapsuleCompiled event (path B)."""
    section_kinds = {s.section: s for s in capsule.sections}
    # §9.2/Patch4 — subjects[]: task primary + capsule primary (spec §9.2). covered_subjects mirrors
    # this so audit reads one unified field instead of re-deriving from subjects.
    subjects = [
        Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY),
        Subject(entity_type=SubjectEntityType.CAPSULE, entity_id=correlation_id, role=SubjectRole.PRIMARY),
    ]
    fingerprint = _capsule_audit_fingerprint(
        capsule=capsule,
        template=template,
        bundle_hash=bundle_hash,
        resolver_input_hash=resolver_input_hash or capsule.input_hash,
        caller_params=caller_params or {},
        touched_nodes=touched_nodes or [],
        subjects=subjects,
    )
    red_line_count = (
        len(
            [
                line
                for line in (section_kinds[CapsuleSection.RED_LINE_OBLIGATIONS].content.splitlines())
                if line.strip()
            ],
        )
        if CapsuleSection.RED_LINE_OBLIGATIONS in section_kinds
        else 0
    )
    obligation_count = (
        red_line_count
        + (
            len(section_kinds[CapsuleSection.MUST_NOT_DO].content.splitlines())
            if CapsuleSection.MUST_NOT_DO in section_kinds
            else 0
        )
    )
    payload = {
        "input_hash": capsule.input_hash,
        "scene_type": capsule.scene_type.value,
        "target_fork_id": capsule.target_fork_id,
        # M-0.3 §2.8/§9.2 — bundle.as_of_seq baseline (Cap3); M-0.4 derives the envelope's
        # as_of_projection_seq from this (WriteConflict / FreshnessDrift baseline).
        "as_of_projection_seq": as_of_projection_seq,
        # M-0.3 §2.2 — the neighborhood concept ids the capsule was assembled over (read_set #3
        # upper bound, Cap5); M-0.4 derive_read_set uses these when no real read trace exists.
        "neighborhood_concept_ids": neighborhood_concept_ids or [],
        # M-0.3 §6/Patch2 — additional_event_reads consumed at assembly (≤ as_of_seq), deduped +
        # seq-sorted; the §9.2/Patch4 audit指纹 "capsule 引用了哪些 event"。
        "source_event_refs": [
            {"event_id": r.event_id, "event_type": r.event_type, "seq": r.seq}
            for r in (source_event_refs or [])
        ],
        "capsule_sections_summary": {
            "obligation_count": obligation_count,
            "red_line_count": red_line_count,
            "known_facts_count": _section_line_count(section_kinds, CapsuleSection.KNOWN_FACTS),
            "files_to_read_count": _section_line_count(section_kinds, CapsuleSection.FILES_TO_READ),
            # T-L0-19 (M-0.3 §7.2): truncation留痕 — CapsuleCompiled records whether/what was cut.
            "truncation_applied": capsule.truncation_applied,
            "truncation_log": [
                {
                    "section": entry.section.value,
                    "method": entry.method,
                    "tokens_before": entry.tokens_before,
                    "tokens_after": entry.tokens_after,
                }
                for entry in capsule.truncation_log
            ],
        },
        # §9.2/Patch4 审计指纹 (content_hash / section_hashes / template / *_hash / touched / subjects)。
        **fingerprint,
    }
    intent = EventIntent(
        local_intent_id=f"cap-{uuid.uuid4().hex[:12]}",
        event_type=EventType.CAPSULE_COMPILED,
        event_category=EventCategory.CAPSULE,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.CAPSULE_ASSEMBLER.value,
            actor_id="m03-capsule-assembler",
            # F-CIR-capsule-fork-not-session: stamp the owning session unit so a CapsuleCompiled can
            # be attributed to a specific session (not just its fork). actor_type stays
            # capsule_assembler (the true assembler) — the provenance boundary validator only REQUIRES
            # session_id for actor_type=agent_session, so an optional session_id here is permitted and
            # carries the "assembled for which session" attribution. None (audit fork / debug CLI) =
            # genuinely fork-keyed, unchanged.
            session_id=session_id,
            task_id=task_id,
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )
    return event_log.write_direct(intent).event_id


def _capsule_audit_fingerprint(
    *,
    capsule: CapsuleResult,
    template: SceneTemplate | None,
    bundle_hash: str,
    resolver_input_hash: str,
    caller_params: dict[str, str],
    touched_nodes: list[str],
    subjects: list[Subject],
) -> dict[str, object]:
    """M-0.3 §9.2 / Patch 4 (L914..L960) — compute the CapsuleCompiled audit fingerprint.

    不存 capsule 全文 (撑爆 event log), 但可证明"当时给 fork 的东西是什么":
      capsule_content_hash   — 所有 section content 拼接的 hash (内容一变即变);
      section_hashes         — 每个 section → 该 section content hash (定位 drift 在哪个 section);
      template_id / version  — 场景模板标识 + 版本 (哪版模板装的);
      projection_bundle_hash — 读取的 bundle hash;
      task_scope_hash        — task / scene_type / target_fork 维度 hash;
      caller_params_hash     — 调用方 task/why_this_fork/must_return 的 hash;
      capsule_assembly_hash  — Patch3 L903: hash(resolver_input_hash + scene_type + template_version
                               + caller_params_hash + truncation_config) — 整个 assembly 层面指纹;
      touched_nodes          — [{entity_type, entity_id}];
      covered_subjects       — [{entity_type, entity_id, role}] (从 subjects[])。
    """
    section_hashes = {
        s.section.value: _component_hash(s.content) for s in capsule.sections
    }
    # 内容 hash: 按 section 优先级顺序 (capsule.sections 已是该顺序) 拼接 — 顺序敏感 (重排即变)。
    content_blob = "\n\x00\n".join(f"{s.section.value}:{s.content}" for s in capsule.sections)
    capsule_content_hash = "sha256:" + _component_hash(content_blob)
    caller_blob = json.dumps(caller_params, sort_keys=True, ensure_ascii=False)
    caller_params_hash = "sha256:" + _component_hash(caller_blob)
    task_scope_blob = json.dumps(
        {
            "scene_type": capsule.scene_type.value,
            "target_fork_id": capsule.target_fork_id,
            "touched_nodes": sorted(touched_nodes),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    task_scope_hash = "sha256:" + _component_hash(task_scope_blob)
    template_version = template.template_version if template is not None else "1.0.0"
    template_id = template.scene_type.value if template is not None else capsule.scene_type.value
    truncation_config = f"max={capsule.max_tokens};applied={capsule.truncation_applied}"
    assembly_blob = "|".join(
        [resolver_input_hash, capsule.scene_type.value, template_version, caller_params_hash, truncation_config],
    )
    capsule_assembly_hash = "sha256:" + _component_hash(assembly_blob)
    return {
        "capsule_content_hash": capsule_content_hash,
        "section_hashes": section_hashes,
        "template_id": template_id,
        "template_version": template_version,
        "projection_bundle_hash": bundle_hash,
        "task_scope_hash": task_scope_hash,
        "caller_params_hash": caller_params_hash,
        "capsule_assembly_hash": capsule_assembly_hash,
        "touched_nodes": [
            {"entity_type": SubjectEntityType.CONCEPT.value, "entity_id": n} for n in touched_nodes
        ],
        "covered_subjects": [
            {"entity_type": s.entity_type.value, "entity_id": s.entity_id, "role": s.role.value}
            for s in subjects
        ],
    }


def _abort_capsule_assembly(
    event_log: EventLog,
    *,
    task_id: str,
    correlation_id: str,
    input_hash: str | None,
    failed_stage: str,
    partial_event_ids: list[str],
    final_error: str,
) -> None:
    """T-L0-16 (波1, M-0.3 §2.8/§2.10) — emit CapsuleAssemblyFailed 留痕 (best-effort)。

    M-0.1 写入失败时 capsule 装配中止; 本助手写一条留痕事件 (失败 stage + 原因 + 失败前已写
    partial event ids — 这些被本 FAILED 标记作废)。留痕本身 best-effort: 存储全死时连本事件都
    写不进 → 吞掉 (abort 的硬保证是上层抛 PipelineError 不交付半截 capsule, 不是留痕成功)。
    """
    intent = EventIntent(
        local_intent_id=f"caf-{uuid.uuid4().hex[:12]}",
        event_type=EventType.CAPSULE_ASSEMBLY_FAILED,
        event_category=EventCategory.CAPSULE,
        payload={
            "task_id": task_id,
            "correlation_id": correlation_id,
            "failed_stage": failed_stage,
            "final_error": final_error,
            "partial_event_ids": list(partial_event_ids),
            "input_hash": input_hash,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.CAPSULE_ASSEMBLER.value,
            actor_id="m03-capsule-assembler",
            task_id=task_id,
            correlation_id=correlation_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CAPSULE,
                entity_id=correlation_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    try:
        event_log.write_direct(intent)
    except (OSError, RuntimeError, ValueError):
        return  # 存储全死: 无处留痕; abort 的硬保证由上层 PipelineError 兜底


def _section_line_count(
    section_kinds: dict[CapsuleSection, CapsuleSectionContent],
    section: CapsuleSection,
) -> int:
    """Count non-blank rendered lines in a capsule section (0 if section absent)."""
    content = section_kinds[section].content if section in section_kinds else ""
    return len([line for line in content.splitlines() if line.strip()])


# ─── Patch G after-stage-8: emit ObligationActivated ────────────────────────


def _emit_obligation_activated(
    *,
    event_log: EventLog,
    task_id: str,
    capsule_compiled_event_id: str,
    resolver_decision_event_id: str,
    final_active: list[_CandidateRecord],
    resolver_hit_ids: set[str],
    floored_ids: set[str] | None = None,
    correlation_id: str,
) -> list[str]:
    """M-0.6 §8.3 / Patch G — emit ObligationActivated for each final_active member.

    activation_source = mechanical_filter (if not via resolver) / resolver_judgment
    (if from resolver verdict) / fallback_conservative (if resolver fallback used, OR
    f-r12-4 red_line/invariant floor force-included it despite a low-confidence hit=false —
    the floor is a conservative include, so it shares the fallback_conservative source +
    links the ResolverDecisionMade where the floor is recorded).
    """
    floored = floored_ids or set()
    event_ids: list[str] = []
    for cand in final_active:
        if cand.obligation_id in resolver_hit_ids:
            source = ObligationActivationSource.RESOLVER_JUDGMENT
            decision_event_id: str | None = resolver_decision_event_id
        elif cand.obligation_id in floored:
            source = ObligationActivationSource.FALLBACK_CONSERVATIVE
            decision_event_id = resolver_decision_event_id
        else:
            source = ObligationActivationSource.MECHANICAL_FILTER
            decision_event_id = None
        payload = {
            "obligation_id": cand.obligation_id,
            "obligation_lifecycle_state": ObligationFieldsLifecycleState.ACTIVATED.value,
            "activated_for_task_id": task_id,
            "activation_source": source.value,
            "resolver_decision_event_id": decision_event_id,
        }
        intent = EventIntent(
            local_intent_id=f"oa-{uuid.uuid4().hex[:12]}",
            event_type=EventType.OBLIGATION_ACTIVATED,
            event_category=EventCategory.OBLIGATION,
            payload=payload,
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.CAPSULE_ASSEMBLER.value,
                actor_id="m03-capsule-assembler",
                task_id=task_id,
                correlation_id=correlation_id,
                causation_event_id=capsule_compiled_event_id,
            ),
            base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
            supersede=Supersede(is_supersede=False),
            subjects=[
                Subject(
                    entity_type=SubjectEntityType.OBLIGATION,
                    entity_id=cand.obligation_id,
                    role=SubjectRole.PRIMARY,
                ),
                Subject(
                    entity_type=SubjectEntityType.CAPSULE,
                    entity_id=correlation_id,
                    role=SubjectRole.AFFECTED,
                ),
            ],
            schema_version="1.0.0",
        )
        event_ids.append(event_log.write_direct(intent).event_id)
    return event_ids


# ─── Cache key hash (M-0.3 §4.1) ─────────────────────────────────────────────


# obligation_lifecycle node fields the assembler itself churns per task (its OWN activation
# bookkeeping output). M-0.3 §4.2/§11.1 — the cache exists so a retry / re-eval of the SAME task
# reuses the verdict ("下次重试 cache 命中"). But the pipeline emits ObligationActivated /
# ObligationScopeJudged on every run, which flips these fields in the projection. If they were in
# the bundle hash, the very first assemble would invalidate its own cache, making a retry always a
# miss (the debt-b529da25797e symptom). They are NOT resolver-judgment inputs (the resolver judges
# scope, not activation lifecycle), so they are excluded from the cache-key bundle hash. A real
# semantic change — new concept / new obligation / new edge / changed definition / changed
# scope_rule / canonical_state transition (superseded / retired) — still flips the hash (§6.6).
_OBLIGATION_ACTIVATION_CHURN_FIELDS: frozenset[str] = frozenset(
    {
        "lifecycle_state",
        "last_observed_lifecycle_event_type",
        "last_state_change_event_id",
        "last_activation_event_id",
        "last_check_event_id",
        "last_violation_event_id",
    },
)


def _normalize_bundle_for_hash(
    bundle: dict[str, dict[str, object] | None],
) -> dict[str, dict[str, object] | None]:
    """Drop the assembler's own per-task activation churn from obligation_lifecycle_state.

    Definitional / structural fields (obligation_id / canonical_state / definition / scope_rule /
    scope_type / severity / nature / scope_hint / attached_to / superseded_by) are kept — those are
    the "projection 一变 cache miss" surface §6.6 means. The churn fields (see the constant above)
    are the pipeline's own ObligationActivated output and would spuriously break its own cache.
    """
    obl = bundle.get("obligation_lifecycle_state")
    if not isinstance(obl, dict):
        return bundle
    nodes = obl.get("obligations")
    if not isinstance(nodes, list):
        return bundle
    normalized_nodes = [
        {k: v for k, v in node.items() if k not in _OBLIGATION_ACTIVATION_CHURN_FIELDS}
        if isinstance(node, dict)
        else node
        for node in nodes
    ]
    normalized = dict(bundle)
    normalized["obligation_lifecycle_state"] = {**obl, "obligations": normalized_nodes}
    return normalized


def _compute_bundle_hash(bundle: dict[str, dict[str, object] | None]) -> str:
    """M-0.2a Patch 5 / M-0.6 §6.6 bundle_hash — a deterministic content hash over the projection
    bundle the pipeline read for this assembly.

    The spec's invariant (§6.6): "projection 一变 cache miss" — a real change to any projection in the
    bundle (new concept / new obligation / new edge / changed definition / scope_rule / canonical
    state) must change the hash so the cache key changes and the resolver is re-invoked. Hashing the
    canonical JSON of the (normalized) bundle gives exactly that: identical structural content →
    identical hash; any structural delta → different hash. None projections (a required projection
    with no materialized state yet) are included as null so the "had no state → now has state"
    transition also flips the hash.

    Normalization (_normalize_bundle_for_hash) strips the assembler's OWN per-task activation churn
    (lifecycle_state / last_*_event_id) so the pipeline does not invalidate its own cache on every
    run — that churn is the pipeline's output, not a resolver-judgment input (debt-b529da25797e).
    """
    canonical = json.dumps(
        _normalize_bundle_for_hash(bundle),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_input_hash(
    *,
    task_id: str,
    task_type: SceneType,
    description: str,
    target_artifacts: list[str],
    touched_nodes: list[str],
    candidates: list[_CandidateKey],
    bundle_hash: str,
    contract_version: str,
    scene_type: SceneType,
    reentry: bool,
    reentry_context_hash: str | None = None,
) -> str:
    """M-0.3 §4.1 / Patch3 cache key — hash of the full canonical_resolver_input.

    Patch3 (附录 B, L872..L911) replaces the hand-stitched key with a hash of the WHOLE canonical
    resolver input so two tasks that touch the same file but mean different things never collide:

      task_scope:  task_id / task_type / description / target_artifacts / touched_nodes / reentry
      candidates:  [{obligation_id, definition_hash, scope_rule_hash}]   (sorted by obligation_id)
      projection_bundle_hash
      scene_type
      resolver_contract_version
      reentry_context_hash?        (only when reentry=true — §5.2 forces a fresh judgment anyway)

    M-0.6 §6.6 sub-contract (kept): bundle_hash makes a projection change a cache miss; contract_version
    makes a resolver prompt / schema / decision-principle bump a cache miss. Patch3 adds: description
    (task semantics affect the resolver judgment), reentry, scene_type (different template ⇒ different
    projections / hops ⇒ possibly different judgment), and per-candidate definition_hash + scope_rule_hash
    (an obligation whose definition or scope rule changed must not reuse the old verdict). Any single
    component delta ⇒ different key (negative controls in test_run038_cache_key_*).

    Candidates are sorted by obligation_id so recall-order jitter does not cause spurious misses.
    """
    candidate_entries = sorted(
        (
            {
                "obligation_id": c.obligation_id,
                "definition_hash": _component_hash(c.definition),
                "scope_rule_hash": _component_hash(c.scope_rule),
            }
            for c in candidates
        ),
        key=lambda e: e["obligation_id"],
    )
    payload: dict[str, object] = {
        "task_scope": {
            "task_id": task_id,
            "task_type": task_type.value,
            "description": description,
            "target_artifacts": sorted(target_artifacts),
            "touched_nodes": sorted(touched_nodes),
            "reentry": reentry,
        },
        "candidates": candidate_entries,
        "projection_bundle_hash": bundle_hash,
        "scene_type": scene_type.value,
        "resolver_contract_version": contract_version,
    }
    if reentry and reentry_context_hash is not None:
        payload["reentry_context_hash"] = reentry_context_hash
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _component_hash(text: str) -> str:
    """Stable sha256 of a key component (Patch3 definition_hash / scope_rule_hash)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CapsulePipelineResult",
    "PipelineError",
    "assemble_capsule",
]
