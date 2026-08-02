"""finding category event payload schemas.

# spec source:
#   03-l0-truth-source/M-0.1-event-log-detailed-design.md
#     §2.3.8 FindingFields (L215..L226)
#     §3.8 (L848..L895) — 4 base finding payloads
#   04-l1-intelligence/M-1.5-review-skill-detailed-design.md
#     §3.2 FindingCreated 扩展 payload (RUN-035 T-L1-51)
#     §3.3 FindingVerified 三态 (RUN-035 T-L1-52)
#     §3.5 FindingResolved + closure_verification (RUN-035 T-L1-53)
#   05-l2-maintenance/* finding_kind extensions (累积 9 — enum already in enums.py)
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from towow.schemas.enums import (
    GATE_RECOMPUTABLE_VERIFICATION_METHODS,
    ClosureResidualCheckMethod,
    ClosureRippleSyncStatus,
    ClosureVerificationMethod,
    FalsificationResult,
    FindingDetectionMethod,
    FindingKind,
    FindingLifecycleState,
    FindingResolution,
    FindingReviewDimension,
    FindingSeverity,
    FindingSourceType,
    FindingVerificationState,
    LedgerEventScope,
    OccurrenceComparator,
    ReviewClosureState,
    SuggestedFixLayer,
)

# RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation. audience 是 leaf 模块 → finding 单向无环。
from towow.schemas.payloads.audience import CheckpointAudienceSeparation

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


# ── M-1.5 §3.2 FindingCreated 扩展子模型 (RUN-035 T-L1-51) ──────────────────────


class FindingTarget(BaseModel):
    """M-1.5 §3.2 target — finding 可定位 (artifact + 文件:行/函数/entity ID)."""

    model_config = _STRICT

    artifact: str = Field(min_length=1)
    location: str = Field(min_length=1)


class SuggestedFixAlternativeLayer(BaseModel):
    model_config = _STRICT

    layer: SuggestedFixLayer
    condition: str = Field(min_length=1)


class FindingSuggestedFixLayer(BaseModel):
    """M-1.5 §3.2 suggested_fix_layer — 告诉 author 改哪一层 + 备选层."""

    model_config = _STRICT

    primary: SuggestedFixLayer
    rationale: str = Field(min_length=1)
    alternative_layers: list[SuggestedFixAlternativeLayer] = Field(default_factory=list)


class FindingFalsificationEvidence(BaseModel):
    """M-1.5 §3.2 falsification_evidence — 尝试 disprove patch 的过程 + 结果三态."""

    model_config = _STRICT

    attempt: str = Field(min_length=1)
    result: FalsificationResult


class ClosureCriterion(BaseModel):
    """M-1.5 §3.2 closure_contract.closure_criteria[] — 一个可验证的 closure 条件.

    RUN-037 P6 修补 (debt-d54af071ce07): 加结构化复算 spec — **由签合约时 (reviewer 产
    FindingCreated) 定死, 非 fixer/closer resolve 时自选**。closure 验证内核 (l1/closure_verification)
    用合约自带的 verification_pattern/test_selector 自己 grep/pytest, 不信被审者给的 recompute 参数。
    这堵住 P6: fixer 把 grep 指向与 condition 无关、恰好 0 命中的 pattern (自洽 count 对) → 门放过
    而 condition 实际未满足。pattern 绑在合约里, 被审者改不了。

    字段全 Optional (历史 closure_criteria 无这些字段 → 重放安全, 无 model_validator 强制)。
    门侧硬约束 (l1/closure_verification): verification_method=grep 但缺 verification_pattern/
    expected_occurrences, 或 =test 但缺 test_selector → **合约自相矛盾** (声明可机器验却没给参数) →
    门 fail-closed 拒 (非降级)。本质不可机器验的方法 (manual_reasoning/schema_check/projection_check/
    replay) 才走 not_recomputable 降级。

    occurrence_comparator (finding f-ohr3-done-criteria-exact-match-vs-existence-intent 修复):
    grep/git_diff 复算比较语义, 缺省 EQ (向后兼容 — 存量判据不带此字段, 语义与修复前完全一致)。
    条件本职是"存在性"(expected_result 写"至少 N 处出现/引用了...") 时签合约者应显式填 GTE, 让
    expected_occurrences 当下限而非精确目标 — 否则用只能表达"精确相等"的比较承载"至少存在"意图,
    真 occurrence 数随文档措辞自然漂移即物理无法通过 (T-OHR3 dc-1 实证: 决策分类词在把它当分类枚举
    的 rubric 真身里天然多次出现, 8≠1 在不摧毁文档本职前提下无解)。
    """

    model_config = _STRICT

    condition: str = Field(min_length=1)
    verification_method: ClosureVerificationMethod
    expected_result: str = Field(min_length=1)
    # ── RUN-037 P6: 合约自带结构化复算 spec (签合约时定死, 门用它复算, 被审者改不了) ──
    verification_pattern: str | None = None  # grep/git_diff: 门用这个 pattern 复算 (非 fixer 给的)
    # grep/git_diff: 门复算真 count 比较基准 (比较语义见 occurrence_comparator)
    expected_occurrences: int | None = Field(default=None, ge=0)
    # grep/git_diff: 复算比较语义, None → EQ (向后兼容); GTE = 存在性下限 (见类 docstring)。
    occurrence_comparator: OccurrenceComparator | None = None
    search_scope: str | None = None  # grep: 相对 repo_dir 搜索范围 / git_diff: 改动清单路径前缀过滤; None → 全域
    test_selector: str | None = None  # test: pytest nodeid (门自己跑, exit 0 才 pass)
    # ── live-target-execution-evidence@v1: LEDGER_EVENT 判据自带 spec (门读 committed 账本计数) ──
    event_kind: str | None = None  # ledger_event: 计数的真实 domain 事件 kind (如 "StrandedBatchManifestFrozen")
    payload_predicate: dict[str, Any] | None = None  # ledger_event: 对 after_state/payload 字段的等值谓词 (可选)
    min_occurrences: int | None = Field(default=None, ge=1)  # ledger_event: 门真计数须 ≥ 此值 (≥1)
    scope_binding: LedgerEventScope | None = None  # ledger_event: 计数范围锚定 (this_run/this_task/this_goal/global)


# ── search_scope / machine_check 形态校验 (finding-machine-check-encoding-gap-1780563112 缺口 b) ──
# T-SL-A1 实撞: assemble 把命令描述文本 ('git diff --name-only <本任务commit>^..<本任务commit>',
# 含永远无人绑定的占位符) 当 search_scope 发布, 发布门照过 → work complete 复算 repo_dir/<该文本>
# 路径不存在 → fail-closed 永久拒, 按此包施工的 executor 无论实现多正确都无法 success。
# 这里是单一真值源: M-1.3 publish gate (发布时拒) + l1/closure_verification (复算时明拒) 共用。

_SEARCH_SCOPE_PATH_RE = re.compile(r"[\w.\-/]+", re.UNICODE)


def search_scope_shape_defect(scope: str) -> str | None:
    """search_scope 必须是相对 repo_dir 的路径形态 — 返回缺陷描述, 合法返回 None.

    拒绝: 占位符 (<...>), 空白 (命令文本特征), 绝对路径 (逃出 repo_dir — `repo_dir / "/etc"`
    在 pathlib 语义下= `/etc`, 会把门的复算范围静默扩到仓库外), `..` 段 (同理越界),
    非路径字符 (shell 元字符等)。
    """
    if "<" in scope or ">" in scope:
        return f"search_scope 含占位符 '<...>' (命令模板文本, 非路径): {scope!r}"
    if any(ch.isspace() for ch in scope):
        return f"search_scope 含空白 (像命令文本, 非路径): {scope!r}"
    if scope.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", scope):
        return f"search_scope 是绝对路径 (须相对 repo_dir, 防复算范围逃出仓库): {scope!r}"
    if ".." in scope.split("/"):
        return f"search_scope 含 '..' 段 (越界逃出 repo_dir): {scope!r}"
    if not _SEARCH_SCOPE_PATH_RE.fullmatch(scope):
        return f"search_scope 含非路径字符 (合法字符: 字母数字._-/): {scope!r}"
    return None


def closure_criterion_contract_defects(mc: ClosureCriterion) -> list[str]:
    """machine_check / closure_criterion 发布前形态校验 — 返回缺陷清单 (空 = 形态合法).

    抓"发布门照过、复算时才 fail-closed 永久拒"的埋雷形态 (缺口 b):
    - grep/git_diff 声明可机器验却没绑 verification_pattern/expected_occurrences (合约自相矛盾,
      复算时必 fail-closed — 该在发布门就退回让 assemble 补);
    - test 没绑 test_selector (同上);
    - occurrence_comparator=gte 但 expected_occurrences<1 (`occ >= 0` 恒真, 声称机器验却什么都不验
      的空洞真验 — f-ohr3-done-criteria-exact-match-vs-existence-intent 修复引入 GTE 语义时须堵死
      的对称洞, 与"缺 pattern"同类但伪装成合法取值而非缺失字段);
    - search_scope 非路径形态 (占位符/命令文本/绝对路径/越界);
    - git_diff 的 verification_pattern 须能被 Python re 编译 (复算引擎用 re.search 对改动清单计数,
      编译不过复算必 fail-closed)。grep 的 pattern 交给 grep ERE 引擎, 不在此用 re 误判。
    """
    defects: list[str] = []
    m = mc.verification_method
    if m in (ClosureVerificationMethod.GREP, ClosureVerificationMethod.GIT_DIFF) and (
        not mc.verification_pattern or mc.expected_occurrences is None
    ):
        defects.append(
            f"verification_method={m.value} 未绑 verification_pattern/expected_occurrences — "
            "合约自相矛盾 (声明可机器验却没给复算参数), 复算时必 fail-closed 永久拒",
        )
    # f-ohr3-done-criteria-exact-match-vs-existence-intent 修复引入的新洞: GTE + expected_occurrences=0
    # 是 `occ >= 0` — grep 结果永远满足这条不等式, 判据退化成"声称机器验却什么都不验"的空洞真验
    # (与"声称 grep 验却没给 pattern"同一类合约缺陷, 只是伪装成合法值而非缺失值)。GTE 的存在性语义
    # 本职是"至少 N 处", N 须 >=1 才有意义; N=0 该用 EQ (或干脆不设 criterion)。
    if (
        m in (ClosureVerificationMethod.GREP, ClosureVerificationMethod.GIT_DIFF)
        and mc.occurrence_comparator is OccurrenceComparator.GTE
        and mc.expected_occurrences is not None
        and mc.expected_occurrences < 1
    ):
        defects.append(
            f"verification_method={m.value} occurrence_comparator=gte 但 expected_occurrences="
            f"{mc.expected_occurrences} (<1) — `occ >= 0` 恒真, 声称机器验却什么都不验 "
            "(空洞真验, 合约缺陷); GTE 存在性语义须 expected_occurrences>=1, =0 该用缺省 EQ",
        )
    if m is ClosureVerificationMethod.TEST and not mc.test_selector:
        defects.append(
            "verification_method=test 未绑 test_selector — 合约自相矛盾, 复算时必 fail-closed 永久拒",
        )
    if m is ClosureVerificationMethod.GIT_DIFF and mc.verification_pattern:
        try:
            re.compile(mc.verification_pattern)
        except re.error as exc:
            defects.append(
                f"git_diff verification_pattern 不是合法 Python 正则 ({exc}) — 复算时必 fail-closed",
            )
    # live-target-execution-evidence@v1: LEDGER_EVENT 缺 event_kind/min_occurrences → 合约自相矛盾
    # (声明读账本计数却不给"数哪种事件/数到几条") → 复算时必 fail-closed 永久拒, 发布门即拦。
    if m is ClosureVerificationMethod.LEDGER_EVENT:
        if not mc.event_kind or mc.min_occurrences is None:
            defects.append(
                "verification_method=ledger_event 未绑 event_kind/min_occurrences — 合约自相矛盾 "
                "(声明读账本计数却没给数哪种事件/数到几条), 复算时必 fail-closed 永久拒",
            )
        if mc.scope_binding is None:
            defects.append(
                "verification_method=ledger_event 未绑 scope_binding — 计数范围不定 "
                "(this_run/this_task/this_goal/global), 复算时无法锚定, fail-closed",
            )
    if mc.search_scope is not None:
        defect = search_scope_shape_defect(mc.search_scope)
        if defect:
            defects.append(defect)
    return defects


class ClosureRippleTarget(BaseModel):
    """M-1.5 §3.2 closure_contract.ripple_targets[] — 改一处必须同步的位置."""

    model_config = _STRICT

    artifact: str = Field(min_length=1)
    location_hint: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ClosureForbiddenResidual(BaseModel):
    """M-1.5 §3.2 closure_contract.forbidden_residuals[] — 没修干净的残留模式."""

    model_config = _STRICT

    pattern: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    check_method: ClosureResidualCheckMethod
    search_scope: str | None = None  # grep: reviewer 在合约设的搜索范围 (相对 repo_dir); None → 全域


def closure_forbidden_residual_contract_defects(fr: ClosureForbiddenResidual) -> list[str]:
    """forbidden_residual 发布前形态校验 — 返回缺陷清单 (空 = 形态合法).

    忠实镜像 closure_criterion_contract_defects 给 criterion 的 search_scope 待遇
    (f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①):
    scope **存在但非路径形态** (占位符/命令文本/绝对路径/越界) → 发布即拒 (dead-on-arrival:
    复算门必 fail-closed, 该在发布门就退回), 用同一 search_scope_shape_defect 单一真值源。
    scope **缺席** 不是硬缺陷 —— 与 criterion 对齐 (criterion 的 search_scope=None 合法, 落 repo_dir
    全域); 且 ②③ (glob 排除账本家族 + 超时降级 not_recomputable) 落地后, 无 scope 的全域 grep
    不再产假阳性 (不误命中备份, 超时不冒充残留), 无 scope residual 能正常闭合。缺 scope 只是
    "扫描面偏大、复算偏慢", 不是"注定不可闭合"—— 硬拒它会把这个共享发布门的语义从"拒不能闭合的
    合约"扩成"拒 scope 次优的合约", 是超出本 finding 的共识变更 (该另起 finding 给门 owner)。
    故缺 scope 走 CLI 层**警告** (see _warn_unscoped_grep_residuals), 不在此硬拒。
    非 grep (schema_check / manual_reasoning) 不强制 — 门侧本就走 not_recomputable 降级。
    """
    defects: list[str] = []
    if fr.check_method is ClosureResidualCheckMethod.GREP and fr.search_scope is not None:
        defect = search_scope_shape_defect(fr.search_scope)
        if defect:
            defects.append(defect)
    return defects


def unscoped_grep_residual_patterns(contract: ClosureContract) -> list[str]:
    """合约里缺 search_scope 的 grep forbidden_residual 的 pattern 列表 (CLI 层警告用, 非硬门)。

    f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①: 无 scope grep
    residual 让复算门退化 repo_dir 全域 grep (扫描面大、复算慢, ②③ 后不再假阳性但仍非最优)。
    CLI finding-create 用它对作者显式提示"该圈定扫描面", 满足"不无声进合约"而不硬拒 (避免把共享
    发布门语义扩成拒 scope 次优合约)。
    """
    return [
        fr.pattern
        for fr in contract.forbidden_residuals
        if fr.check_method is ClosureResidualCheckMethod.GREP and fr.search_scope is None
    ]


class ClosureContract(BaseModel):
    """M-1.5 §3.2 Patch g closure_contract — Finding 是可执行的修复合约 (非只描述).

    让 fix-after mode 能 bounded verify (不漫游); 第二轮 review 是 closure verification 而非
    free re-review。closure_criteria 必填 (min 1); ripple_targets / forbidden_residuals 可空。
    """

    model_config = _STRICT

    closure_criteria: list[ClosureCriterion] = Field(min_length=1)
    ripple_targets: list[ClosureRippleTarget] = Field(default_factory=list)
    forbidden_residuals: list[ClosureForbiddenResidual] = Field(default_factory=list)


class FindingSource(BaseModel):
    """M-1.5 §3.2 source — finding 来源 (model_review 是 review 语境判别器)."""

    model_config = _STRICT

    type: FindingSourceType
    agent_id: str | None = None
    hook_id: str | None = None
    rule_id: str | None = None


class FindingCreatedPayload(BaseModel):
    """M-0.1 §3.8 base + M-1.5 §3.2 扩展 (RUN-035 T-L1-51) + finding_kind extensions.

    扩展字段 payload 层 Optional (FindingCreated 被系统/maintenance 路径复用, 如 projection
    自查 — 不能强制系统 finding 带 review 的 VoI/falsification)。**review 语境 (source.type=
    model_review) 由 model_validator 门侧条件强制** voi_rationale/target/falsification_evidence/
    closure_contract 必须 present — 这是 enforced 写边界真重算 (非 CLI 自报 flag, 非 flag-only 假牙齿)。
    入 SPEC-CONFLICT-LEDGER: §3.2 "必填" = review 语境必填, payload 层 Optional + 条件强制。
    """

    model_config = _STRICT

    # ── 基础字段 (M-0.1 已定义) ──
    finding_id: str = Field(min_length=1)
    severity: FindingSeverity  # M-1.5 §3.2 + purple
    risk_surface: str = Field(min_length=1)
    # T-LND-02 (review-unit@v1): 本 finding 归属哪一次 review (verdict 折叠的分组边界 = 一个
    # review-unit 的全部 finding)。= review session_id (一次 review run 即一个 review-unit)。
    # Optional: 系统/maintenance/execution 侧 finding 不属任何 review-unit (None); review 语境由
    # review CLI 盖 sid。reducer 兜底从 provenance.session_id (model_review) 派生 (poka-yoke)。
    review_unit_id: str | None = None
    lifecycle_state: Literal[FindingLifecycleState.CREATED] = FindingLifecycleState.CREATED
    description: str
    related_patch_event_id: str | None = None
    related_obligation_ids: list[str] | None = None
    detection_method: FindingDetectionMethod
    finding_kind: FindingKind | None = None  # M-1.6 Patch D — optional in v1.0, mandatory in v2.x
    rule_id: str | None = None  # M-1.5 Patch — detection rule that produced this finding

    # ── M-1.5 §3.2 扩展字段 (RUN-035 T-L1-51, payload Optional + review 语境条件强制) ──
    voi_rationale: str | None = None
    target: FindingTarget | None = None
    suggested_fix_layer: FindingSuggestedFixLayer | None = None
    review_dimension: FindingReviewDimension | None = None
    is_preexisting: bool | None = None
    falsification_evidence: FindingFalsificationEvidence | None = None
    review_plan_dimension_ref: str | None = None
    voi_criterion_ref: str | None = None
    closure_contract: ClosureContract | None = None
    source: FindingSource | None = None
    # RUN-070 AC4 — 跨切 L1-checkpoint-audience-separation (M-1.5 finding-lifecycle checkpoint)。
    # Optional 向后兼容 (系统/maintenance 路径产的 finding 不必带; FindingCreated 跨语境复用)。
    # 一旦提供 (finding 要呈现给 Nature 时), schema 层 enforce 受众分离: review trace / finding
    # lifecycle 不会当 owner-facing finding 直接砸给 Nature (LEDGER 表列的 M-1.5 同类风险根治)。
    audience: CheckpointAudienceSeparation | None = None

    @model_validator(mode="after")
    def enforce_review_context_fields(self) -> Self:
        """source.type=model_review → review finding 必带 §3.2 review 必填字段 (门侧重算).

        review fork 产的 finding (source.type=model_review) 缺 voi_rationale/target/
        falsification_evidence/closure_contract → reject。系统/maintenance finding (source=None
        或别的 type) 不受约束 (FindingCreated 跨语境复用)。这是 enforced 写边界真校验,
        不是 CLI 自报 passed 的 flag (advisor 钦点: 防 flag-only 假牙齿)。

        RUN-093 (审计 v2 R3 / 1.5-a/b): §3.2 字段**存在性**之上再焊**实质**机械下限 —
        VoI 必须锚定真目标 (非"任何改进都算"), finding.target.location 必须是真定位符
        (非散文)。before: 泛泛 VoI / 无锚 location 全过门; after: 写边界拒 (run093-baseline.json)。
        语义级 "改善 > 0" 仍归 review fork / verify-step (LLM 层); 这里只拦零锚定的最弱形态。
        """
        if self.source is not None and self.source.type is FindingSourceType.MODEL_REVIEW:
            missing = [
                name
                for name, val in (
                    ("voi_rationale", self.voi_rationale),
                    ("target", self.target),
                    ("falsification_evidence", self.falsification_evidence),
                    ("closure_contract", self.closure_contract),
                )
                if val is None or (isinstance(val, str) and not val.strip())
            ]
            if missing:
                raise ValueError(
                    f"review finding (source.type=model_review) requires non-empty {missing} "
                    "(M-1.5 §3.2)",
                )
            # ── RUN-093 件A: VoI 实质锚定 (spec 自检 #1/#4 机械下限) ──
            from towow.schemas.payloads.review_substance import (
                finding_location_anchored,
                voi_rationale_substantive,
            )

            artifact = self.target.artifact if self.target is not None else ""
            location = self.target.location if self.target is not None else ""
            if not voi_rationale_substantive(
                self.voi_rationale or "",
                risk_surface=self.risk_surface,
                target_artifact=artifact,
                target_location=location,
            ):
                raise ValueError(
                    f"review finding voi_rationale 未锚定真目标 (泛泛'任何改进'类无效) — 须含结构锚 "
                    f"(文件:行/函数/entity-id/spec-ref) / 领域锚词 (done_criteria/obligation/契约/"
                    f"不变量) / 或指向 risk_surface·target (M-1.5 §自检 #1/#4): {self.voi_rationale!r}",
                )
            # ── RUN-093 件B: finding 可定位 (无锚 location 丢弃, spec §3.2 / 自检 #2) ──
            if not finding_location_anchored(location):
                raise ValueError(
                    f"review finding target.location 非真定位符 (散文无锚, 丢弃) — 须是 文件:行 / "
                    f"函数 / entity id / 行号 (M-1.5 §3.2 / §自检 #2): {location!r}",
                )
            # ── finding-finding-contract-form-publication-gap-1780566273: closure_contract
            # machine_check 形态校验 — 与 task_package.validate_publication_gate done_criteria
            # 面对称 (同用 closure_criterion_contract_defects 单一真值源)。review finding 签的
            # 合约含占位符 search_scope / grep·git_diff 未绑 pattern+occurrences / test 未绑
            # selector 时**发布即拒**, 不再发布后到 fix-after 复算 (l1/closure_verification)
            # 才 fail-closed 永久拒 — 那时 finding 已无法走 confirmed_and_fixed 机器闭合。
            # 只在 model_review 写边界强制; 历史事件读/重放路径不过此 validator (重放安全),
            # 读侧由复算门 fail-closed 兜底。
            if self.closure_contract is not None:  # narrowed (missing-check above 已保证 present)
                contract_defects = [
                    f"closure_criteria[{i}]: {defect}"
                    for i, crit in enumerate(self.closure_contract.closure_criteria)
                    for defect in closure_criterion_contract_defects(crit)
                ] + [
                    # f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①:
                    # grep residual 的 search_scope **非路径形态** → 发布即拒 (忠实镜像 criterion 的
                    # scope 待遇; scope 缺席不硬拒, 走 CLI 层警告 — 见函数 docstring)。
                    f"forbidden_residuals[{i}]: {defect}"
                    for i, fr in enumerate(self.closure_contract.forbidden_residuals)
                    for defect in closure_forbidden_residual_contract_defects(fr)
                ]
                if contract_defects:
                    raise ValueError(
                        "review finding closure_contract 形态缺陷 (发布即拒 — 否则 fix-after "
                        "复算必 fail-closed 永久拒, finding 无法机器闭合; "
                        "finding-finding-contract-form-publication-gap-1780566273): "
                        + "; ".join(contract_defects),
                    )
        return self


class FindingVerifiedPayload(BaseModel):
    """M-0.1 §3.8 FindingVerified + M-1.5 §3.3 三态 (RUN-035 T-L1-52).

    现 base 版只有 false_positive_eliminated: bool 二态; M-1.5 加 verification_state 三态
    (verified / rejected_false_positive / unverified_inconclusive) + verify_falsification_attempt
    (verify-step 独立尝试 disprove finding 的过程)。三态 payload Optional 保历史二态事件可重放;
    independent verify fork 产的事件应带三态。
    """

    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    lifecycle_state: Literal[FindingLifecycleState.VERIFIED] = FindingLifecycleState.VERIFIED
    verification_method: str = Field(min_length=1)
    false_positive_eliminated: bool
    # ── M-1.5 §3.3 扩展 (RUN-035 T-L1-52) ──
    verification_state: FindingVerificationState | None = None
    verify_falsification_attempt: str | None = None


class FindingDisputedPayload(BaseModel):
    """M-0.1 §3.8 FindingDisputed — dispute is not supersede; author fork rebuts."""

    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    lifecycle_state: Literal[FindingLifecycleState.DISPUTED] = FindingLifecycleState.DISPUTED
    dispute_reason: str = Field(min_length=1)


# ── f-stale-closure-contract-permanently-unclosable: closure_contract 修订通道 ──────────


class StaleClosureCriterion(BaseModel):
    """被合法架构演进架空的一条旧判据 — amend 必须逐条点名, 不许"整份换掉"含糊过去.

    三个字段各挡一种洗合约手法: original_condition 钉住"你退役的到底是哪一条"(不许事后含糊);
    became_unsatisfiable_because 钉住"为什么在当前架构下不可满足"(不许把"我懒得满足"写成"不可满足");
    superseding_change_evidence 钉住"哪一次**合法**变更架空了它"(commit/路径级指针 —— 没有变更就
    没有"陈旧", 只有"没做到")。
    """

    model_config = _STRICT

    original_condition: str = Field(min_length=1)
    became_unsatisfiable_because: str = Field(min_length=1)
    superseding_change_evidence: str = Field(min_length=1)


class FindingClosureContractAmendedPayload(BaseModel):
    """FindingClosureContractAmended — 有权座位 (M-1.5 review) 修订陈旧 closure_contract 的唯一通道.

    为什么需要它 (finding f-stale-closure-contract-permanently-unclosable): closure_contract 此前
    只有一个写入点 (FindingCreated), 且闭合门恒取**首条** FindingCreated —— 合约事实上写一次即
    不可变。判据锚定的位置被后续**合法**重构架空时 (例: 判据要求 `app/import/page.tsx` 内出现某调用,
    而该文件已被服务端化迁移搬空), 照字面满足需要把架构改回去 = 让合约字面凌驾于它本要保护的不变量;
    不满足则该 finding 在任何 resolution 口径下都被硬拦 = **永久不可闭合**, fix→review 每轮空转。

    **不是 lifecycle transition**: 无 lifecycle_state 字段。修订合约不改 finding 状态 (一条 verified
    finding 修订合约后仍是 verified) —— reducer 只换生效合约, 不动 lifecycle_state。

    反"洗合约"的物理约束 (amend 的风险恰是把判据改成"我已经满足的样子"):
    ① stale_criteria 非空且逐条带证据 (见 StaleClosureCriterion) —— 不点名旧判据不许 amend;
    ② 新合约至少一条判据 gate-recomputable (grep/test/git_diff/ledger_event) —— 物理阻断"amend 成
       一堆 manual_reasoning 再走 confirmed_and_accepted"这条退化路径;
    ③ preserved_risk_surface_evidence 非空 —— 必须说明新判据仍钉住**原风险面** (amend 换的是锚点,
       不是要保护的东西);
    ④ retired_forbidden_residuals 显式列出被退役的残留 pattern —— CLI 侧核对"旧合约有、新合约没有"
       的集差必须与本字段一致, 悄悄丢掉一条 forbidden_residual 被拒 (T-L1-53 省略探针同源)。
    """

    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    # 新的**生效**合约 (全量替换语义, 非 patch —— 闭合门读末条 amend 的这份)。
    closure_contract: ClosureContract
    amend_rationale: str = Field(min_length=1)
    stale_criteria: list[StaleClosureCriterion] = Field(min_length=1)
    retired_forbidden_residuals: list[str] = Field(default_factory=list)
    preserved_risk_surface_evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def _amended_contract_keeps_machine_teeth(self) -> Self:
        """约束②: 新合约至少一条 gate-recomputable 判据 (防 amend 成全 manual_reasoning 的空合约)."""
        if not any(
            c.verification_method in GATE_RECOMPUTABLE_VERIFICATION_METHODS
            for c in self.closure_contract.closure_criteria
        ):
            methods = sorted({c.verification_method.value for c in self.closure_contract.closure_criteria})
            raise ValueError(
                "amend 后的 closure_contract 必须至少含一条 gate-recomputable 判据 "
                f"(grep/test/git_diff/ledger_event); 当前全是 {methods} —— 全不可复算的合约让闭合退化成"
                "自报, 正是 amend 通道最该防的洗合约形态",
            )
        return self


# ── M-1.5 §3.5 FindingResolved closure_verification 子模型 (RUN-035 T-L1-53) ─────


class ClosureCriterionResult(BaseModel):
    model_config = _STRICT

    criterion: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)


class ClosureRippleResult(BaseModel):
    model_config = _STRICT

    target_artifact: str = Field(min_length=1)
    target_location: str = Field(min_length=1)
    sync_status: ClosureRippleSyncStatus


class ClosureResidualResult(BaseModel):
    model_config = _STRICT

    pattern: str = Field(min_length=1)
    found_occurrences: int = Field(ge=0)  # 应 = 0 if closure
    locations: list[str] = Field(default_factory=list)


class ClosureVerification(BaseModel):
    """M-1.5 §3.5 closure_verification — Review Closure Cycle 状态机的逐条验证结果."""

    model_config = _STRICT

    closure_state: ReviewClosureState
    criteria_results: list[ClosureCriterionResult] = Field(default_factory=list)
    ripple_results: list[ClosureRippleResult] = Field(default_factory=list)
    residual_check_results: list[ClosureResidualResult] = Field(default_factory=list)
    unrelated_findings_logged: list[str] | None = None
    # RUN-059 件B / finding-fixafter-degraded-no-independent-fork-1: 独立 verify-step falsifier fork
    # 的 verdict (在岗证据, 跟随 verify-step 输出进 FindingResolved 持久化, 可审计)。fork 形态
    # (spawned/passed/verification_state/...) 与 inline 降级明账 (mode=inline_degraded) 字段集不同,
    # 用宽松 dict 容纳两种形态 (此处不 _STRICT 递归校验)。
    independent_verify_fork: dict[str, Any] | None = None
    # degraded=true → 无 closure_contract 的系统 finding 走自洽底线 + 独立 fork (非合约 grep 复算)。
    degraded: bool = False


class FindingResolvedPayload(BaseModel):
    """M-0.1 §3.8 FindingResolved + M-1.5 §3.5 closure_verification (RUN-035 T-L1-53).

    resolution 补 unresolved_risk 第 5 态 (留作 known risk)。closure_verification 在 **payload 层**
    Optional (系统 finding / retracted / escalated / unresolved_risk 这类不主张闭合的 resolve 可不带);
    closure_state=closed 是 cycle 终态判据。

    ⚠ payload 层 Optional ≠ CLI 门允许省略 (f-stale-closure-contract-permanently-unclosable):
    对**带 closure_contract 的 review finding**, `review finding-resolve` 在 resolution 主张闭合
    (confirmed_and_fixed / **confirmed_and_accepted**) 时**必须**带 --closure-file —— 省掉它会让
    authoritative 闭合门的合约复算整段跳过, 账本上落一条无证据、未复算的闭合 (曾实际发生过一次)。
    confirmed_and_accepted 这条路径本身没被关: 判据本质不可机器复算 (manual_reasoning 等) 时照样写进
    closure file, 门判 not_recomputable → 拦 confirmed_and_fixed、放行 confirmed_and_accepted。
    换言之门要的是"接受风险也要把接受的依据写下来", 不是"必须机器验证".
    合约本身已被架构演进架空 → 先走 `review finding-contract-amend` 修订, 不是绕过复算。

    supersede.is_supersede=true required at EventIntent level only when resolution=retracted.
    """

    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    lifecycle_state: Literal[FindingLifecycleState.RESOLVED] = FindingLifecycleState.RESOLVED
    resolution: FindingResolution
    # T-FIX-B2-03 (REVIEW-verdict#2): 显式携带原 FindingCreated 的 review_unit_id (= 原 review
    # session_id)。跨会话 fix_after resolve 时 provenance.session_id 是 fix 会话 B ≠ 原 review-unit A;
    # 缺此字段则 derived-review-verdict 折叠回退到 prov.session_id=B, 把 resolve 折进错 unit → 原
    # unit A 永远 failed, REVIEW task 永不 verdict-passed (grade-3 断点)。显式锚定 → 折叠按字段归对组。
    # Optional: 系统/execution finding 无分组键 (None); review resolve 由 CLI 从 finding_lifecycle 节点盖回。
    review_unit_id: str | None = None
    # ── M-1.5 §3.5 扩展 (RUN-035 T-L1-53) ──
    resolution_evidence: str | None = None
    closure_verification: ClosureVerification | None = None


class FindingAcceptedPayload(BaseModel):
    """f-escalation-task-oriented-not-reversibility-framework — finding 接受为已知基线 (ACCEPTED).

    与 RESOLVED 区别: RESOLVED = 修好了 (closure verified); ACCEPTED = 改不了/不可变 (如账本 553
    历史重号, 史料不可变), owner 看过一次后接受为基线 → 哨兵永不再报。这是 owner 的基线决定
    (不是被审计的域 patch), 走 path-B 直写即可 (像 GuardAdminBypass / DebtRegistered 那类
    owner/治理自观测事件), owner 一条 write_direct 就能 accept 一个 finding。

    accepted_signature: 可选, 哨兵签名 (finding_signature)。哨兵产的 finding 其 finding_id =
    f-sentinel-<signature>, 但显式带 signature 让消费方不必反解析 id 即可建 accepted 集。
    """

    model_config = _STRICT

    finding_id: str = Field(min_length=1)
    lifecycle_state: Literal[FindingLifecycleState.ACCEPTED] = FindingLifecycleState.ACCEPTED
    accepted_reason: str = Field(min_length=1)  # 为何接受为基线 (如 "账本 553 历史重号, 史料不可变")
    accepted_by: str | None = None              # owner / actor id (谁 accept 的)
    immutable_baseline: bool = True             # 改不了/不可变 (True) vs 暂接受 (False)
    accepted_signature: str | None = None       # 哨兵 finding_signature (建 accepted 集免反解析 id)
    review_unit_id: str | None = None


__all__ = [
    "ClosureContract",
    "ClosureCriterion",
    "ClosureCriterionResult",
    "ClosureForbiddenResidual",
    "ClosureResidualResult",
    "ClosureRippleResult",
    "ClosureRippleTarget",
    "ClosureVerification",
    "FindingAcceptedPayload",
    "FindingClosureContractAmendedPayload",
    "FindingCreatedPayload",
    "FindingDisputedPayload",
    "FindingFalsificationEvidence",
    "FindingResolvedPayload",
    "FindingSource",
    "FindingSuggestedFixLayer",
    "FindingTarget",
    "FindingVerifiedPayload",
    "StaleClosureCriterion",
    "SuggestedFixAlternativeLayer",
    "closure_criterion_contract_defects",
    "closure_forbidden_residual_contract_defects",
    "search_scope_shape_defect",
    "unscoped_grep_residual_patterns",
]
