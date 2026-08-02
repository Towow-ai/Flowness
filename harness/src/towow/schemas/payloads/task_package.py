"""M-1.3 §3.6 + §11.1 — TaskPackage self-contained content schema + publication gate.

spec source:
  04-l1-intelligence/M-1.3-planner-skill-detailed-design.md
    §3.6 TaskPackagePublished.payload.package_content (11-field self-contained package)
    §11.1 发布门槛 (11 publish thresholds — M-1.3a aligned to task-package-policy)
    §13.5 task-package-assemble (planner assembles package; publish gate validates)
  src/towow/skills/planning/knowledge/task-package-policy.md (canonical schema)

T-L1-28 fix (0-E-2 / RUN-033): the prior plan_package_publish emitted only
task_id / package_hash / is_self_contained, where is_self_contained was a caller-supplied
bool flag with NO validation — `--self-contained=false` published just as happily as true
(fake-done: 尺子只验 flag 不验内容, TIER0-PROGRESS §7 裁定3 同型病).

This module makes is_self_contained a DERIVED verdict of a real validation gate over the
embedded package_content: each of the 11 §11.1 thresholds is checked, the package_hash is
RECOMPUTED from the canonical content and must match (tamper-evident), and is_self_contained
is true iff every blocking threshold passes. A publish whose content fails any blocking
threshold is rejected — the event never lands.

Honesty boundaries (advisor verdict — surfaced as debt, not hidden):
  - HARD (structural / reference-existence — fail-closed): done_criteria non-empty + each
    has verification_type + evidence_ref; read_set/write_set non-empty + each has derived_from;
    pin_to_snapshot concept_ref must carry lock_event_id; active_obligations each from_event_id
    must reference a real event; review_required ⇒ a review task exists in the plan;
    package_hash recompute-match.
  - SOFT (semantic — honest-degraded + debt): concept_refs "no stale reference" (needs
    T-L1-14 detect_stale_references — we verify the concept_id resolves structurally, not
    that it is semantically fresh); model_tier_rationale quality (we verify non-empty only);
    "zero-context fork can really do it" (we verify the structural necessary conditions, not
    the semantic sufficiency). Degraded checks NEVER masquerade as hard gates.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    GATE_RECOMPUTABLE_VERIFICATION_METHODS,
    TaskModelTier,
    TaskType,
)
from towow.schemas.payloads.finding import (
    ClosureCriterion,
    closure_criterion_contract_defects,
)
from towow.schemas.payloads.plan_freezed import closed_task_ids

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ─── sub-models (task-package-policy.md canonical schema) ────────────────────────


class EvidenceRef(BaseModel):
    """M-1.3a 硬化 — every done_criterion / constraint traces to its source."""

    model_config = _STRICT

    source_type: Literal["brief.completion_condition", "concept_definition", "nature_judgment"]
    source_id: str = Field(min_length=1)
    quoted_claim: str = Field(min_length=1)


class DoneCriterion(BaseModel):
    """M-1.3 done_criterion. RUN-044 LB1/LB2: 加可选 machine_check — 复用 M-1.5 ClosureCriterion
    作结构化机器复算 spec (verification_method ∈ ClosureVerificationMethod 同词表, 自带
    verification_pattern/expected_occurrences/test_selector/occurrence_comparator — grep/git_diff
    的存在性意图 [如"至少引用了 X"] 须显式填 occurrence_comparator=gte, 不要用只能表达精确相等的
    expected_occurrences 硬撑存在性, 见 f-ohr3-done-criteria-exact-match-vs-existence-intent)。

    LB1: execution 自检 (l1/execution_done_recompute) 对带 gate-recomputable machine_check 的
    criterion 真起 subprocess 复算 (grep/test), 机器结果 != agent 自报 → fail-closed。
    LB2 (finding-r05 收紧): planner publish gate (validate_publication_gate) 拒"声称机械却不可复算"
    —— verification_type ∈ {manual_verify, test_passes, file_exists} 必须并带 gate-recomputable
    machine_check (grep/test/git_diff) 才放过 (见 §11.1 门 1 + _done_criterion_machine_recomputable);
    唯 concept_state_reached 放过 (= M-1.5 projection_check, 投影即真值, 不可门 subprocess 复算)。
    machine_check 默认 None 保留在 schema 层 (发布门只在新发布跑、strict=False replay 不重门 → 历史
    package 重放安全)。
    """

    model_config = _STRICT

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    verification_type: Literal["test_passes", "file_exists", "concept_state_reached", "manual_verify"]
    verification_params: dict[str, str] = Field(default_factory=dict)
    evidence_ref: EvidenceRef
    # RUN-044: 结构化机器复算 spec (复用 M-1.5 ClosureCriterion; 门用它自己 grep/pytest, 不信自报)。
    machine_check: ClosureCriterion | None = None


class ConceptRefEntry(BaseModel):
    model_config = _STRICT

    concept_id: str = Field(min_length=1)
    at_reference: str = Field(min_length=1)
    locking_policy: Literal["pin_to_snapshot", "auto_accept_latest", "explicit_decision_required"]
    why_locked: str = Field(min_length=1)
    # pin_to_snapshot ⇒ lock_event_id required (enforced at the publish gate, not the schema,
    # so historical/auto_accept_latest refs without it still validate on replay).
    lock_event_id: str | None = None


class FileRef(BaseModel):
    model_config = _STRICT

    path: str = Field(min_length=1)
    purpose: Literal["read_only", "will_write", "reference_doc"]


class EntityRefDerived(BaseModel):
    """read_set / write_set entry — M-1.3a 硬化: every entry carries derived_from provenance."""

    model_config = _STRICT

    entity_type: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    derived_from: str = Field(min_length=1)


class ObligationRef(BaseModel):
    model_config = _STRICT

    obligation_id: str = Field(min_length=1)
    from_event_id: str = Field(min_length=1)
    why_active_for_this_task: str = Field(min_length=1)


class ConstraintEntry(BaseModel):
    model_config = _STRICT

    constraint_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_ref: EvidenceRef


class CompletionCheck(BaseModel):
    model_config = _STRICT

    check_id: str = Field(min_length=1)
    check_type: Literal["test_passes", "file_exists", "concept_state_reached", "manual_verify"]
    check_params: dict[str, str] = Field(default_factory=dict)
    maps_to_done_criterion: str = Field(min_length=1)


class TaskPackageContent(BaseModel):
    """M-1.3 §3.6 package_content — the zero-context self-contained TaskPackage body.

    11 load-bearing fields (task_type / description / done_criteria / concept_refs /
    file_refs / read_set / write_set / active_obligations / constraints / model_tier /
    review_required) + completion_checks (fork self-check) + the two rationale/reason and
    estimated_token_budget context fields. All present at the schema level; the publish gate
    decides which are *blocking* (see validate_publication_gate).
    """

    model_config = _STRICT

    task_type: TaskType
    description: str = Field(min_length=1)
    done_criteria: list[DoneCriterion] = Field(default_factory=list)
    concept_refs: list[ConceptRefEntry] = Field(default_factory=list)
    file_refs: list[FileRef] = Field(default_factory=list)
    read_set: list[EntityRefDerived] = Field(default_factory=list)
    write_set: list[EntityRefDerived] = Field(default_factory=list)
    active_obligations: list[ObligationRef] = Field(default_factory=list)
    constraints: list[ConstraintEntry] = Field(default_factory=list)
    model_tier: TaskModelTier
    model_tier_rationale: str = Field(min_length=1)
    review_required: bool
    review_required_reason: str | None = None
    estimated_token_budget: int | None = None
    completion_checks: list[CompletionCheck] = Field(default_factory=list)
    # ─── fnd-r01-9 (assembler 第二面 — owner-gate machine_check 豁免) ──────────────────────
    # True = 本 task 是 owner-gated 红线任务 (撞 owner 的 5 类不可逆动作: 上线/删生产数据/动钱/对外发布/
    # 改公开承诺; 与 TaskNodeCreated.requires_owner_gate 同名同义)。它的"完成"判据本质是 **owner 显式批准**,
    # 不是 grep/test/git_diff 能机器复算的东西。组装器据此**豁免** manual_verify done_criterion 免于
    # machine_check 强制 (self_contained_done_criteria_defects) —— 否则会逼 planner 给一个无机器可判的
    # 红线任务**伪造**一个假 test_selector/git_diff (假 machine_check 在 work complete 复算时必然 fail-closed,
    # 把红线任务变成永远做不完的埋雷)。default False 向后兼容 (历史 package 无此字段 = 普通任务, machine_check
    # 照常强制)。**自报不算数**: 发布门 validate_publication_gate 把它与 canonical TaskNodeCreated 的权威
    # requires_owner_gate 交叉核对 —— 不一致则拒, 防止"谁都能塞 requires_owner_gate=True 跳过 machine_check"
    # 的普遍洞 (见 validate_publication_gate 门 1.5)。
    requires_owner_gate: bool = False


# ─── package hash (canonical, tamper-evident) ────────────────────────────────────


def compute_package_hash(content: TaskPackageContent) -> str:
    """sha256 over canonical JSON of the package content. Stable key order ⇒ reproducible.

    The publish gate recomputes this and requires it to equal the declared package_hash —
    so a package whose declared hash does not match its content (stale / tampered) is rejected.
    """
    canonical = json.dumps(content.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


# ─── publication gate (§11.1 — 11 thresholds) ───────────────────────────────────


class PublicationGateResult(BaseModel):
    """Outcome of the §11.1 publish gate. is_self_contained is DERIVED, never self-reported."""

    model_config = ConfigDict(strict=True, extra="forbid")

    is_self_contained: bool
    blockers: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)


def _event_type(e: dict[str, object]) -> str:
    return str(e.get("event_type", ""))


def _after_state(e: dict[str, object]) -> dict[str, object]:
    p = e.get("payload", {})
    if isinstance(p, dict):
        ast = p.get("after_state", {})
        if isinstance(ast, dict):
            return ast
    return {}


def _authoritative_owner_gate(events: list[dict[str, object]], task_id: str) -> bool | None:
    """fnd-r01-9 — canonical TaskNodeCreated.requires_owner_gate (权威源) for task_id。

    返回 True/False 找到对应 TaskNodeCreated 时, None 表示 event log 里根本没有该 task 的
    TaskNodeCreated (无法核对 —— 由 caller 决定如何处理)。这是 owner-gate 的**唯一权威源**:
    它在 plan-task-create 打标时被门强制带 owner_gate_reason, 编排器派发层 (_task_owner_gate) 也只读
    这里。package_content.requires_owner_gate 必须与它一致, 不能自报跳门 (validate_publication_gate 门 1.5)。
    """
    for e in events:
        if _event_type(e) == "TaskNodeCreated" and str(_after_state(e).get("task_id", "")) == task_id:
            return _after_state(e).get("requires_owner_gate") is True
    return None


def check_dependencies_completed_for_publish(
    publishing_task_id: str,
    events: list[dict[str, object]],
    plan_id: str | None,
) -> list[str]:
    """T-L1-33 (M-1.3 §11.2) — progressive publication: a downstream task may only publish once its
    hard prerequisites have completed (success) OR been honestly closed (done_elsewhere).

    Edge orientation source→target = source must precede target (same orientation
    compute_critical_path / check_no_circular_dependency use). So the prerequisites of
    publishing_task_id are every S with an edge S→publishing_task_id. A prerequisite counts as
    satisfied iff EITHER (a) it has a TaskRunCompleted with outcome=success, OR (b) it has an
    accepted TaskNodeClosed (readyset-closure-exclusion-contract@v1 / T-DEC-3) — reused verbatim via
    closed_task_ids() from plan_freezed, the same helper execution_dispatch.compute_ready_tasks
    folds into its "satisfied" set. Only strength=hard edges are enforced (medium = 可先做但
    re-check, does not block — §3.2). When plan_id is given, edges are scoped to it (defensive;
    edges typically don't carry plan_id, so an edge whose endpoints reference plan tasks is treated
    as in-plan).

    D-1 bootstrap fix (SERVER-TAKEOVER-EXECUTION-PLAN-2026-07-13.md §2 / T-LKC chain investigation):
    before this fix, this gate only recognized (a) — a prerequisite that honestly aborted and was
    later closed via `plan task-close --reason done_elsewhere` (closure-evidence-verification-gate
    already verified the deliverable exists elsewhere) still permanently blocked every downstream
    publish, because outcome=success was the only accepted terminal state here. The dispatch layer
    (execution_dispatch.compute_ready_tasks) already treated success and closed as equivalent
    (T-DEC-3) — this gate now matches that same contract instead of maintaining a second, narrower
    definition of "done" for the publish path.

    Returns the list of blocker strings (empty ⇒ all hard prerequisites satisfied → publish OK).
    """
    # task_ids belonging to this plan (for scoping; edges reference task_ids, not plan_id)
    plan_task_ids = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskNodeCreated"
        and (plan_id is None or _after_state(e).get("plan_id") == plan_id)
    }

    # tasks that completed success
    completed_success = {
        str(_after_state(e).get("task_id", ""))
        for e in events
        if _event_type(e) == "TaskRunCompleted" and _after_state(e).get("outcome") == "success"
    }
    # D-1 bootstrap fix (T-DEC-3 alignment): fold in gate-verified TaskNodeClosed(done_elsewhere) as
    # an equally-honest terminal state, same union execution_dispatch.compute_ready_tasks already
    # performs via this SAME closed_task_ids() helper — one exclusion, not two parallel definitions
    # of "done" (see readyset-closure-exclusion-contract@v1 in plan_freezed.closed_task_ids).
    satisfied_prereqs = completed_success | closed_task_ids(events)

    # T-L1-33 fix (dogfood R07 2026-06-14): respect edge removals. Earlier this loop counted every
    # TaskDependencyEdgeAdded and never looked at TaskDependencyEdgeRemoved, so correcting a
    # mis-directed dependency (dep-remove wrong edge + dep-add the reverse) left the removed edge
    # phantom-counted as a prerequisite → both directions blocked → plan unfreezable. Now we compute
    # the NET-ACTIVE edge set (last Add/Remove wins per edge identity), then enforce prerequisites
    # only over net-active hard edges. Red-team: a removal can NOT bypass a genuinely-uncompleted
    # prerequisite — a still-active S→target hard edge still blocks; only a truly-retracted edge
    # (dep-remove carries an audited removal_reason) stops blocking. Added.after_state and
    # Removed.before_state both carry (source_task_id, target_task_id, dependency_type).
    def _edge_key(d: dict[str, object]) -> tuple[str, str, str]:
        return (
            str(d.get("source_task_id", "")),
            str(d.get("target_task_id", "")),
            str(d.get("dependency_type") or d.get("edge_type") or ""),
        )

    active_edges: dict[tuple[str, str, str], dict[str, object]] = {}
    for e in events:
        et = _event_type(e)
        if et == "TaskDependencyEdgeAdded":
            es = _after_state(e)
            active_edges[_edge_key(es)] = es
        elif et == "TaskDependencyEdgeRemoved":
            p = e.get("payload", {})
            before = p.get("before_state", {}) if isinstance(p, dict) else {}
            if isinstance(before, dict):
                active_edges.pop(_edge_key(before), None)

    blockers: list[str] = []
    for es in active_edges.values():
        if str(es.get("target_task_id", "")) != publishing_task_id:
            continue
        # only hard dependencies block progressive publication
        if str(es.get("strength", "hard")) != "hard":
            continue
        prereq = str(es.get("source_task_id", ""))
        # scope: if we know the plan's tasks, ignore edges from a task outside it
        if plan_task_ids and prereq not in plan_task_ids:
            continue
        if prereq and prereq not in satisfied_prereqs:
            blockers.append(
                f"progressive-publish blocked: prerequisite task {prereq} has not completed "
                "(no TaskRunCompleted outcome=success, and no accepted TaskNodeClosed "
                f"done_elsewhere) for downstream {publishing_task_id} (§11.2)",
            )
    return sorted(set(blockers))


# verification_type whose claim is a genuine named method the gate canNOT subprocess-recompute:
# concept_state_reached → 投影即真值 (= M-1.5 projection_check, 本质不可门 subprocess 复算)。它放过
# 不带 gate-recomputable machine_check —— 与 M-1.5 把 projection_check/schema_check/replay 当具名方法
# (非 manual_reasoning 纯语义) 同构。test_passes/file_exists 不在此集: "测试过了/文件存在"是 directly-
# subprocess-recomputable 的声称, 见 _done_criterion_machine_recomputable。
_NAMED_NON_RECOMPUTABLE_VERIFICATION_TYPES: frozenset[str] = frozenset({"concept_state_reached"})


def _done_criterion_machine_recomputable(c: DoneCriterion, *, owner_gated: bool = False) -> bool:
    """一条 done_criterion 是否满足发布门"可机器复算"要求 (非空头支票式机械声称)。

    判定: 带 gate-recomputable machine_check (verification_method ∈ GATE_RECOMPUTABLE_VERIFICATION_METHODS
    = grep/test/git_diff, 门能自跑 subprocess 复算、自报伪造不了) → True。否则只放过
    concept_state_reached —— 它是真·具名方法 (= M-1.5 projection_check, 投影即真值, 本质不可门
    subprocess 复算)。

    finding-r05-planner-test-passes-not-machine-recomputed 收紧 (RUN-044 LB2 的延伸): 旧实现把
    test_passes/file_exists 也当"具名机械方法"放过 —— 但二者跟 M-1.5 的具名方法不同: "测试过了/文件
    存在"是 directly-subprocess-recomputable 的声称 (跑测试/查文件), 而执行侧 recompute
    (l1/execution_done_recompute) keys off machine_check.verification_method, **不** off verification_type。
    于是 verification_type=test_passes 但无 machine_check 的判据: 发布门放过 (名义机械)、执行门回退到只验
    --done-evidence 字符串非空 (不复算) —— 声称机械却既不要求可复算也不被真复算 (O-03 §7 红线"防止完成了
    但没人验证")。现在 test_passes/file_exists/manual_verify 都必须并带 gate-recomputable machine_check 门
    方放过; 一旦带, 执行侧 recompute_done_criteria_check 自动对它真起 subprocess 复算 (一处收紧封住两端)。

    fnd-r01-9 (assembler 第二面) 豁免: owner_gated=True 时, manual_verify done_criterion 放过免于
    machine_check —— owner-gated 红线任务的"完成"判据本质是 **owner 显式批准** (上线/删生产数据/动钱/对外
    发布/改公开承诺), 不是任何 grep/test/git_diff 能机器复算的工件。逼它带 machine_check = 逼 planner
    伪造一个假判据 (假 machine_check 在 work complete 复算时永久 fail-closed, 把红线任务变成做不完的埋雷)。
    豁免**只对 manual_verify 开**: 同一个 owner-gated 任务里若还混了 test_passes/file_exists 判据, 那些仍是
    directly-subprocess-recomputable 的声称, 仍须带 machine_check (豁免口尽量窄, 不是 owner-gated 任务整个
    跳过验证)。豁免的**防滥用**靠 validate_publication_gate 把 content.requires_owner_gate 与 canonical
    TaskNode 权威标记交叉核对 (自报跳门不算数, 见门 1.5)。
    """
    if c.verification_type in _NAMED_NON_RECOMPUTABLE_VERIFICATION_TYPES:
        return True
    if owner_gated and c.verification_type == "manual_verify":
        return True
    mc = c.machine_check
    return mc is not None and mc.verification_method in GATE_RECOMPUTABLE_VERIFICATION_METHODS


def self_contained_done_criteria_defects(content: TaskPackageContent) -> list[str]:
    """§11.1 门1 的 **context-free** 结构地板: 一个自包含 (is_self_contained=true) package 的
    done_criteria 必须非空 + 每条可机器复算 + 每条 machine_check 形态合法。

    抽成独立函数是因为它不需要 event-log / plan_id 上下文 (纯 content 的函数) —— 所以同一套判据
    既被 validate_publication_gate (CLI 发布门 §11.1 门1) 用, 也被 TaskPackagePublishedAfter 的
    写边界 schema validator 用 (finding-r05-planner-package-content-schema-optional)。写边界对
    "自包含 package 必带可机器复算的验收合约" 这条不变量不能比 CLI 发布门更弱 —— 否则任何绕过
    `plan package-publish`、直接 EventLog.append 的 caller 都能落一个 done_criteria 空/纯语义的
    误导性自包含包 (O-03 §7 红线 + CLAUDE.md '不绕 gate 直 emit')。空 list = 这条结构地板通过。
    """
    defects: list[str] = []
    if not content.done_criteria:
        defects.append("done_criteria is empty (§11.1: 非空且每条有 verification_type)")
        return defects
    # fnd-r01-9 (assembler 第二面): owner-gated 红线任务的 manual_verify ("owner 批准"型) 判据豁免
    # machine_check 强制 —— 见 _done_criterion_machine_recomputable docstring。注意 owner_gated 这个
    # content 字段是"自报", 在 context-free 这一层只是放行豁免; 防滥用的交叉核对 (与 canonical TaskNode
    # 权威标记一致) 由 validate_publication_gate 门 1.5 担。写边界 (TaskPackagePublishedAfter) 也复用本
    # 函数, 那里无 event 上下文做交叉核对 —— 但自报 owner-gate 是"自缚手脚": 任务一旦标 owner-gate,
    # 编排器派发层 (fnd-r01-9 第一面) 物理拒绝自动 spawn + 升级 owner, 跳 machine_check 换来的是"永不自动
    # 执行、永远等 owner", 严格更强的限制, 不能用来把未验证任务偷渡进自动执行。
    owner_gated = content.requires_owner_gate
    for c in content.done_criteria:
        if not _done_criterion_machine_recomputable(c, owner_gated=owner_gated):
            # 两种失败模式分开措辞 (都不可机器复算, 但成因不同):
            #  - manual_verify: 纯语义 (本就不声称机械) —— RUN-044 LB2 不再放过纯语义兜底;
            #  - test_passes/file_exists: 声称机械 ("测试过了/文件存在") 却没给可复算判据 —— finding-r05。
            if c.verification_type == "manual_verify":
                defects.append(
                    f"done_criterion {c.criterion_id} (verification_type=manual_verify) 纯语义不可机器复算 "
                    "(无 gate-recomputable machine_check grep/test/git_diff) "
                    "— LB2: planner done_criteria 必须可机器复算, manual_verify 须并带可复算判据 (RUN-044)。"
                    "若本 task 是 owner-gated 红线任务 (完成判据=owner 批准, 本无机器可判)，标 "
                    "package_content.requires_owner_gate=True 走豁免, 别伪造假 machine_check (fnd-r01-9)",
                )
            else:
                defects.append(
                    f"done_criterion {c.criterion_id} (verification_type={c.verification_type}) "
                    "声称机械却不可机器复算 — 须并带 gate-recomputable machine_check "
                    "(verification_method ∈ grep/test/git_diff)。finding-r05: test_passes/file_exists "
                    "是 directly-subprocess-recomputable 的声称 (跑测试/查文件), 不给可复算判据 → 执行侧退化成只验 "
                    "evidence 字符串 (唯 concept_state_reached = projection 真值放过)",
                )
        # finding-machine-check-encoding-gap-1780563112 缺口 (b): machine_check 形态校验 —
        # 拒"发布门照过、work complete 复算才 fail-closed 永久拒"的埋雷 (T-SL-A1 实撞:
        # search_scope 被塞进 'git diff --name-only <本任务commit>^..<本任务commit>' 命令模板
        # 文本, 占位符永远无人绑定, 按此包施工的 executor 无论实现多正确都无法 success)。
        if c.machine_check is not None:
            for defect in closure_criterion_contract_defects(c.machine_check):
                defects.append(f"done_criterion {c.criterion_id} machine_check 形态缺陷: {defect}")
    return defects


def validate_publication_gate(
    content: TaskPackageContent,
    declared_package_hash: str,
    *,
    events: list[dict[str, object]],
    plan_id: str | None,
    publishing_task_id: str | None = None,
    uncovered_mandated_test_files: list[str] | None = None,
    doc_only_test_selector_defects: list[str] | None = None,
) -> PublicationGateResult:
    """Run the §11.1 11 publish thresholds over a package. is_self_contained = no blockers.

    `events` is the full event log (list of records) — used for reference-existence checks
    (active_obligations from_event_id real; review_prep task exists in the plan). `plan_id`
    scopes the review-task lookup; None ⇒ that single check is degraded to a soft warning.
    `publishing_task_id` (T-L1-33): the task whose package is being published — enables the
    §11.2 progressive-publication gate (hard prerequisites must have completed). None ⇒ that
    gate is skipped (legacy callers / standalone schema tests).
    `uncovered_mandated_test_files` (f-tdec-plan-writeset-omits-mandated-tests): pre-computed by
    the caller (needs `_plan_claims` + repo existence + `normalize_guard_target` — l2/cli layer,
    not derivable in this pure schema fn) — the list of done_criteria.test_selector test files that
    MUST be newly created but are absent from the task's CLAIMS write_set (.owner/V-01 write
    boundary). Each becomes a fail-closed blocker so is_self_contained reflects it. None ⇒ caller
    did not compute (legacy/standalone) ⇒ check skipped (degraded, no false-positive).
    `doc_only_test_selector_defects` (fnd-r01-9-assembler-fake-test-selector): pre-computed by the
    caller (execution_dispatch.doc_only_test_machine_check_defects — needs events/plan_id to run the
    new-capability-task-classifier@v1 mutual-exclusion, not derivable in this pure schema fn) — a
    doc-only/no-.py-write-product task carrying a verification_method=test machine_check is a
    source-less fake gate (pytest with nothing under test ⇒ fail, or forces a fabricated shell test =
    fake-done). Each becomes a fail-closed blocker. None ⇒ caller did not compute (legacy/standalone)
    ⇒ check skipped (degraded, no false-positive).

    BLOCKERS (fail-closed) make is_self_contained=false ⇒ caller rejects the publish.
    soft_warnings are honest-degraded semantic checks (do not block) — surfaced for audit.
    """
    blockers: list[str] = []
    soft: list[str] = []

    # 1. done_criteria 非空 + 每条可机器复算 (RUN-044 LB2 — debt-832a233907a4 + finding-r05 收紧):
    #    "声称机械却不可复算"的判据**不再放过**。M-1.5 已把 verification_method 钉死成可机器复算
    #    (closure_verification 门自跑 grep/test); planner 侧对齐: verification_type ∈ {manual_verify,
    #    test_passes, file_exists} 必须并带一条 gate-recomputable machine_check (grep/test/git_diff),
    #    否则 fail-closed。这堵住 M-1.3 型漏网 (cross-plan 冲突检测仅文档/SKILL 有、代码 0 enforce 却拿
    #    自然语言糊弄过 planner) + finding-r05 漏网 (test_passes 声称机械但执行侧 recompute keys off
    #    machine_check 非 verification_type → 无 machine_check 时退化成只验 evidence 字符串)。唯
    #    concept_state_reached 放过 (= M-1.5 projection_check, 投影即真值, 本质不可门 subprocess 复算)。
    #    抽到 self_contained_done_criteria_defects (context-free): 同一结构地板也由 TaskPackagePublished
    #    写边界 schema 强制 (finding-r05-planner-package-content-schema-optional), 写边界不弱于此门。
    blockers.extend(self_contained_done_criteria_defects(content))

    # 1.5 fnd-r01-9 anti-hole 交叉核对: package 自报 requires_owner_gate=True (= 用 manual_verify 豁免
    #     machine_check) 必须与 canonical TaskNodeCreated 的权威 requires_owner_gate 一致。否则任何 caller
    #     都能在 package 里塞 requires_owner_gate=True 跳过 machine_check = "谁都能跳过验证"的普遍洞。
    #     权威源在 plan-task-create 打标时被门强制带 owner_gate_reason, 派发层也只信它 (_task_owner_gate) ——
    #     这里把 package 自报钉回权威源。publishing_task_id is None (legacy/standalone) 时跳过 (无 task 可查);
    #     找不到对应 TaskNodeCreated 也拒 (自报 owner-gate 却无权威背书 = 不可信)。content 不标 owner-gate 时
    #     无须核对 (任务可以 owner-gated 但 done_criteria 全机器可判 → 不用豁免; 或反之 —— 都不开洞)。
    if content.requires_owner_gate and publishing_task_id is not None:
        authoritative = _authoritative_owner_gate(events, publishing_task_id)
        if authoritative is not True:
            detail = (
                "no TaskNodeCreated found" if authoritative is None
                else "TaskNode.requires_owner_gate=False"
            )
            blockers.append(
                f"package_content.requires_owner_gate=True for task {publishing_task_id} but canonical "
                f"TaskNode disagrees ({detail}) — cannot self-grant machine_check exemption "
                "(fnd-r01-9: owner-gate 豁免须与权威 TaskNodeCreated 一致, 防自报跳门)",
            )

    # 2. done_criteria 每条都有 evidence_ref (schema guarantees presence; gate verifies
    #    structural completeness — non-empty source_id + quoted_claim). Quote-grounding
    #    against the brief is enforced at the freeze coverage gate (T-L1-30), where the brief
    #    is the independent anchor.
    for c in content.done_criteria:
        if not c.evidence_ref.source_id.strip() or not c.evidence_ref.quoted_claim.strip():
            blockers.append(f"done_criterion {c.criterion_id} evidence_ref has empty source_id/quoted_claim")

    # 3. read_set / write_set 非空.
    if not content.read_set:
        blockers.append("read_set is empty (§11.1)")
    if not content.write_set:
        blockers.append("write_set is empty (§11.1)")

    # 4. read_set 每条都有 derived_from (schema min_length guarantees non-empty; belt+braces).
    for r in content.read_set:
        if not r.derived_from.strip():
            blockers.append(f"read_set entry {r.entity_type}:{r.entity_id} missing derived_from")
    for w in content.write_set:
        if not w.derived_from.strip():
            blockers.append(f"write_set entry {w.entity_type}:{w.entity_id} missing derived_from")

    # 4.5 f-tdec-plan-writeset-omits-mandated-tests-no-owner-expand-port (M-1.6 fix, 预防层 #1):
    #     done_criteria.test_selector 钉死的"必须新建"测试文件必须落在本 task 的 CLAIMS write_set
    #     (.owner/V-01 物理写边界) 内 —— 否则 executor 无法在隔离工位创建它 = 自相矛盾的自包含包
    #     (跨 T-DEC-1..4 系统性复发)。计算需 _plan_claims + repo 存在性 + normalize_guard_target
    #     (l2/cli 层, 非纯 schema fn 可得), 故由 caller 预算后传入; 这里只并入 blockers 让
    #     is_self_contained 反映。None = caller 未算 (legacy/standalone schema test) → 跳过 (degraded)。
    if uncovered_mandated_test_files:
        blockers.extend(uncovered_mandated_test_files)

    # 4.6 fnd-r01-9-assembler-fake-test-selector (M-1.6 fix): doc-only/无 .py 写产出的 task 套
    #     verification_method=test 的 machine_check 是无源伪门 (success 门跑 pytest 无被测代码 → fail
    #     或逼 executor 造空壳测试过门 = fake-done, 与 canonical 交付物存在类 done_criteria 直接矛盾)。
    #     判定需 write_set/file_refs .py 检 + 与 new-capability-task-classifier@v1 第10道门**互斥**
    #     (被判新增能力的 task 必须 test 型, 不能同时拒, 否则冻结死锁), 需 events/plan_id 上下文, 故由
    #     caller (execution_dispatch.doc_only_test_machine_check_defects) 预算传入; 这里只并入 blockers。
    #     None = caller 未算 (legacy/standalone schema test) → 跳过 (degraded, 不误报)。
    if doc_only_test_selector_defects:
        blockers.extend(doc_only_test_selector_defects)

    # 5. concept_refs 中无 stale reference — SOFT (depends on T-L1-14 detect_stale_references,
    #    not done). HARD half: pin_to_snapshot ⇒ lock_event_id present (structurable).
    for cr in content.concept_refs:
        if cr.locking_policy == "pin_to_snapshot" and not (cr.lock_event_id or "").strip():
            blockers.append(f"concept_ref {cr.concept_id} pin_to_snapshot without lock_event_id (§9 锁定策略)")
    if content.concept_refs:
        soft.append(
            "concept_refs semantic-staleness not verified (needs T-L1-14 detect_stale_references); "
            "only structural lock policy checked",
        )

    # 6. model_tier 已分配且有 rationale — model_tier is a strict enum (always assigned);
    #    rationale non-empty is schema-guaranteed. Rationale QUALITY is a soft warning.
    if not content.model_tier_rationale.strip():
        blockers.append("model_tier_rationale empty (§11.1)")

    # 7. review_required=true ⇒ a review task exists in the plan (structural, fail-closed).
    if content.review_required:
        if plan_id is None:
            soft.append("review_required=true but plan_id unknown — review-task existence not checked")
        else:
            has_review_task = any(
                _event_type(e) == "TaskNodeCreated"
                and _after_state(e).get("plan_id") == plan_id
                and _after_state(e).get("task_type") == TaskType.REVIEW.value
                for e in events
            )
            if not has_review_task:
                blockers.append(
                    f"review_required=true but no review task in plan {plan_id} (§11.1: review_prep task)",
                )

    # 8. active_obligations 每条 from_event_id 指向真实 event (不凭空声明, fail-closed).
    known_event_ids = {str(e.get("event_id", "")) for e in events}
    for ob in content.active_obligations:
        if ob.from_event_id not in known_event_ids:
            blockers.append(
                f"active_obligation {ob.obligation_id} from_event_id={ob.from_event_id} not found in event log",
            )

    # 9. package_hash 已计算 + 重算匹配 (tamper-evident, fail-closed).
    recomputed = compute_package_hash(content)
    if not declared_package_hash.strip():
        blockers.append("package_hash empty (§11.1: 已计算)")
    elif declared_package_hash != recomputed:
        blockers.append(
            f"package_hash mismatch — declared {declared_package_hash} != recomputed {recomputed} "
            "(stale or tampered content)",
        )

    # 10. 零上下文 fork 自包含验证 — composite of the structural necessary conditions above.
    #     NOT defined as is_self_contained itself (that would be circular — advisor Q1 (中危)).
    #     Semantic "can a zero-context fork really do it" is a soft warning.
    soft.append(
        "zero-context executability verified at structural level only "
        "(refs resolve / obligations exist / criteria typed); semantic sufficiency not machine-judged",
    )

    # 11. T-L1-33 (§11.2) progressive publication — a downstream task only publishes once its hard
    #     prerequisites have completed (success). Skipped when publishing_task_id is unknown.
    if publishing_task_id is not None:
        blockers.extend(
            check_dependencies_completed_for_publish(publishing_task_id, events, plan_id),
        )

    # 12. is_self_contained = DERIVED (no blocker), never the caller's say-so.
    return PublicationGateResult(
        is_self_contained=not blockers,
        blockers=blockers,
        soft_warnings=soft,
    )


__all__ = [
    "CompletionCheck",
    "ConceptRefEntry",
    "ConstraintEntry",
    "DoneCriterion",
    "EntityRefDerived",
    "EvidenceRef",
    "FileRef",
    "ObligationRef",
    "PublicationGateResult",
    "TaskPackageContent",
    "check_dependencies_completed_for_publish",
    "compute_package_hash",
    "self_contained_done_criteria_defects",
    "validate_publication_gate",
]
