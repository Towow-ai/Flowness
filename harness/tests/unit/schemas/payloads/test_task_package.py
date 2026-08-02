"""T-L1-28 (0-E-2 RUN-033): TaskPackage §3.6 schema + §11.1 publication gate.

Adversarial intent: prove is_self_contained is a DERIVED verdict of real content validation,
not a flag — a non-self-contained package must fail the gate, and a tampered package_hash must
be caught. Negative controls confirm the gate is content-sensitive, not always-true.
"""

from __future__ import annotations

from towow.schemas.enums import ClosureVerificationMethod, TaskModelTier, TaskType
from towow.schemas.payloads.finding import ClosureCriterion
from towow.schemas.payloads.task_package import (
    ConceptRefEntry,
    DoneCriterion,
    EntityRefDerived,
    EvidenceRef,
    ObligationRef,
    TaskPackageContent,
    compute_package_hash,
    validate_publication_gate,
)


def _good_content(*, review_required: bool = False, with_obligation_event: str | None = None) -> TaskPackageContent:
    """A structurally self-contained package that should pass every blocking threshold."""
    return TaskPackageContent(
        task_type=TaskType.CODE_CHANGE,
        description="用户能通过 API 创建 batch",
        done_criteria=[
            DoneCriterion(
                criterion_id="dc-1",
                description="POST /batch 返回 201",
                verification_type="test_passes",
                evidence_ref=EvidenceRef(
                    source_type="brief.completion_condition",
                    source_id="obs-1",
                    quoted_claim="用户能创建 batch",
                ),
                # finding-r05 收紧: test_passes 必须并带 gate-recomputable machine_check, 否则发布门拒。
                machine_check=ClosureCriterion(
                    condition="POST /batch 创建测试通过",
                    verification_method=ClosureVerificationMethod.TEST,
                    expected_result="pytest exit 0",
                    test_selector="tests/integration/test_batch_api.py::test_create_batch_201",
                ),
            ),
        ],
        concept_refs=[
            ConceptRefEntry(
                concept_id="concept-batch",
                at_reference="@concept:batch",
                locking_policy="pin_to_snapshot",
                why_locked="代码基于确定的概念定义",
                lock_event_id="evt-lock-1",
            ),
        ],
        file_refs=[],
        read_set=[EntityRefDerived(entity_type="concept", entity_id="concept-batch", derived_from="cr-1")],
        write_set=[EntityRefDerived(entity_type="file", entity_id="api/batch.py", derived_from="dc-1")],
        active_obligations=(
            [ObligationRef(obligation_id="ob-1", from_event_id=with_obligation_event, why_active_for_this_task="x")]
            if with_obligation_event
            else []
        ),
        constraints=[],
        model_tier=TaskModelTier.SONNET,
        model_tier_rationale="标准 implementation，单一模块",
        review_required=review_required,
        completion_checks=[],
    )


def test_hash_recompute_is_stable() -> None:
    c = _good_content()
    assert compute_package_hash(c) == compute_package_hash(c)
    assert compute_package_hash(c).startswith("sha256:")


def test_gate_passes_self_contained_package() -> None:
    c = _good_content()
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True, result.blockers
    assert result.blockers == []


def test_gate_rejects_empty_done_criteria() -> None:
    c = _good_content().model_copy(update={"done_criteria": []})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("done_criteria is empty" in b for b in result.blockers)


def test_gate_rejects_empty_write_set() -> None:
    c = _good_content().model_copy(update={"write_set": []})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("write_set is empty" in b for b in result.blockers)


def test_gate_rejects_pin_without_lock_event_id() -> None:
    bad_ref = ConceptRefEntry(
        concept_id="c", at_reference="@concept:c", locking_policy="pin_to_snapshot",
        why_locked="w", lock_event_id=None,
    )
    c = _good_content().model_copy(update={"concept_refs": [bad_ref]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("pin_to_snapshot without lock_event_id" in b for b in result.blockers)


def test_gate_rejects_tampered_hash() -> None:
    """Negative control for the tamper-evident hash: a hash that does not match content fails."""
    c = _good_content()
    result = validate_publication_gate(c, "sha256:deadbeef", events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("package_hash mismatch" in b for b in result.blockers)


def test_gate_rejects_phantom_obligation() -> None:
    """active_obligation.from_event_id must reference a real event."""
    c = _good_content(with_obligation_event="evt-does-not-exist")
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("not found in event log" in b for b in result.blockers)


def test_gate_accepts_real_obligation_event() -> None:
    c = _good_content(with_obligation_event="evt-real-1")
    events: list[dict[str, object]] = [{"event_id": "evt-real-1", "event_type": "ObligationCaptured"}]
    result = validate_publication_gate(c, compute_package_hash(c), events=events, plan_id="p1")
    assert result.is_self_contained is True, result.blockers


def test_gate_rejects_review_required_without_review_task() -> None:
    c = _good_content(review_required=True)
    # plan has only a non-review task
    events: list[dict[str, object]] = [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"plan_id": "p1", "task_type": "code_change"}}},
    ]
    result = validate_publication_gate(c, compute_package_hash(c), events=events, plan_id="p1")
    assert result.is_self_contained is False
    assert any("no review task in plan" in b for b in result.blockers)


def test_gate_accepts_review_required_with_review_task() -> None:
    c = _good_content(review_required=True)
    events: list[dict[str, object]] = [
        {"event_type": "TaskNodeCreated", "payload": {"after_state": {"plan_id": "p1", "task_type": "review"}}},
    ]
    result = validate_publication_gate(c, compute_package_hash(c), events=events, plan_id="p1")
    assert result.is_self_contained is True, result.blockers


def test_soft_warnings_do_not_block() -> None:
    """test_passes done_criterion + concept_refs produce soft warnings but still self-contained."""
    c = _good_content()
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True
    assert result.soft_warnings  # at least the concept-staleness + zero-context structural notes


# ─── RUN-044 LB2: planner done_criteria 必须可机器复算 (纯 manual_verify 不再放过) ──────────


def _evref() -> EvidenceRef:
    return EvidenceRef(
        source_type="brief.completion_condition", source_id="obs-1", quoted_claim="x",
    )


def test_gate_rejects_pure_manual_verify_done_criterion() -> None:
    """LB2 红测核心: verification_type=manual_verify 且无 gate-recomputable machine_check → blocker.

    旧实现只发 soft warning 放过 (纯语义兜底); RUN-044 钉死: M-1.3 那种"代码 0 enforce 但文档吹全"
    在 planner 关就该被拒, 因为 done_criteria 给不出门能复算的判据。
    """
    pure_manual = DoneCriterion(
        criterion_id="dc-manual",
        description="人工确认 cross-plan 冲突检测做了",
        verification_type="manual_verify",
        evidence_ref=_evref(),
    )
    c = _good_content().model_copy(update={"done_criteria": [pure_manual]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("机器复算" in b for b in result.blockers), result.blockers


def test_gate_accepts_manual_verify_with_grep_machine_check() -> None:
    """带可机器复算判据 (grep machine_check) 的 manual_verify criterion → 通过 (不纯语义兜底)。"""
    backed = DoneCriterion(
        criterion_id="dc-manual-backed",
        description="cross-plan 冲突检测有 enforce 代码",
        verification_type="manual_verify",
        evidence_ref=_evref(),
        machine_check=ClosureCriterion(
            condition="cross-plan 冲突检测 enforce 代码存在",
            verification_method=ClosureVerificationMethod.GREP,
            expected_result="≥1 命中",
            verification_pattern=r"def detect_cross_plan_write_set_conflict",
            expected_occurrences=1,
            search_scope="src",
        ),
    )
    c = _good_content().model_copy(update={"done_criteria": [backed]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True, result.blockers


def test_gate_rejects_manual_verify_with_only_manual_reasoning_machine_check() -> None:
    """machine_check 也是 manual_reasoning (不可门复算) → 仍纯语义兜底 → blocker (防退化攻击)。"""
    fake_backed = DoneCriterion(
        criterion_id="dc-fake",
        description="x",
        verification_type="manual_verify",
        evidence_ref=_evref(),
        machine_check=ClosureCriterion(
            condition="x",
            verification_method=ClosureVerificationMethod.MANUAL_REASONING,
            expected_result="人工判断",
        ),
    )
    c = _good_content().model_copy(update={"done_criteria": [fake_backed]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("机器复算" in b for b in result.blockers), result.blockers


# ─── finding-r05-planner-test-passes-not-machine-recomputed: test_passes/file_exists 收紧 ──────


def test_gate_rejects_test_passes_without_machine_check() -> None:
    """finding-r05 核心红测: verification_type=test_passes 但无 gate-recomputable machine_check → blocker.

    旧实现把 test_passes 当"具名机械方法"放过, 但执行侧 recompute keys off machine_check.verification_method
    不 off verification_type —— 无 machine_check 时退化成只验 --done-evidence 字符串非空 (不机器复核
    "test 真过了")。"测试过了"是 directly-subprocess-recomputable 的声称, 现在必须并带可复算判据。
    """
    hollow_test_passes = DoneCriterion(
        criterion_id="dc-hollow",
        description="测试通过 (但没给可复算的 test selector)",
        verification_type="test_passes",
        evidence_ref=_evref(),
    )
    c = _good_content().model_copy(update={"done_criteria": [hollow_test_passes]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("不可机器复算" in b and "test_passes" in b for b in result.blockers), result.blockers


def test_gate_accepts_test_passes_with_test_machine_check() -> None:
    """带 gate-recomputable test machine_check (test_selector) 的 test_passes criterion → 通过。

    一旦带可复算判据, 执行侧 recompute_done_criteria_check 自动对它真起 subprocess pytest 复算。
    """
    backed = DoneCriterion(
        criterion_id="dc-test-backed",
        description="POST /batch 返回 201",
        verification_type="test_passes",
        evidence_ref=_evref(),
        machine_check=ClosureCriterion(
            condition="创建 batch 测试通过",
            verification_method=ClosureVerificationMethod.TEST,
            expected_result="pytest exit 0",
            test_selector="tests/integration/test_batch_api.py::test_create_batch_201",
        ),
    )
    c = _good_content().model_copy(update={"done_criteria": [backed]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True, result.blockers


def test_gate_rejects_file_exists_without_machine_check() -> None:
    """finding-r05: file_exists 同 test_passes —— "文件存在"是可复算声称, 无 machine_check → blocker。"""
    hollow_file_exists = DoneCriterion(
        criterion_id="dc-file-hollow",
        description="生成的 config 文件存在",
        verification_type="file_exists",
        evidence_ref=_evref(),
    )
    c = _good_content().model_copy(update={"done_criteria": [hollow_file_exists]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is False
    assert any("不可机器复算" in b and "file_exists" in b for b in result.blockers), result.blockers


def test_gate_accepts_file_exists_with_grep_machine_check() -> None:
    """带 gate-recomputable grep machine_check 的 file_exists criterion → 通过 (门可对文件内容真复算)。"""
    backed = DoneCriterion(
        criterion_id="dc-file-backed",
        description="config 文件含必填字段",
        verification_type="file_exists",
        evidence_ref=_evref(),
        machine_check=ClosureCriterion(
            condition="config.yaml 含 version 字段",
            verification_method=ClosureVerificationMethod.GREP,
            expected_result="≥1 命中",
            verification_pattern=r"^version:",
            expected_occurrences=1,
            search_scope="config.yaml",
        ),
    )
    c = _good_content().model_copy(update={"done_criteria": [backed]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True, result.blockers


def test_gate_still_accepts_concept_state_reached_without_machine_check() -> None:
    """回归守: concept_state_reached 仍放过 (= M-1.5 projection_check, 投影即真值, 本质不可门 subprocess 复算)。

    finding-r05 只收紧 directly-subprocess-recomputable 的 test_passes/file_exists; concept_state_reached
    是真·具名方法, 不被误伤 (强制它带 gate-recomputable machine_check 不可能 — projection_check ∉ grep/test/git_diff)。
    """
    concept_state = DoneCriterion(
        criterion_id="dc-concept",
        description="concept-batch 状态到达 frozen",
        verification_type="concept_state_reached",
        evidence_ref=_evref(),
    )
    c = _good_content().model_copy(update={"done_criteria": [concept_state]})
    result = validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")
    assert result.is_self_contained is True, result.blockers


# ─── finding-machine-check-encoding-gap-1780563112 缺口 (b): machine_check 形态校验 ──────────
# T-SL-A1 实撞回归: 命令模板文本 search_scope (含永远无人绑定的占位符) 发布门照过 →
# work complete 复算 fail-closed 永久拒, 按此包施工的 executor 无论实现多正确都无法 success。
# 发布门现在把这类"必砸在复算"的形态在发布时就拒掉。

_TSLA1_COMMAND_TEXT_SCOPE = "git diff --name-only <本任务commit>^..<本任务commit>"


def _mc_criterion(mc: ClosureCriterion, cid: str = "dc-mc") -> DoneCriterion:
    return DoneCriterion(
        criterion_id=cid,
        description="x",
        verification_type="manual_verify",
        evidence_ref=_evref(),
        machine_check=mc,
    )


def _gate(dc: DoneCriterion):
    c = _good_content().model_copy(update={"done_criteria": [dc]})
    return validate_publication_gate(c, compute_package_hash(c), events=[], plan_id="p1")


def test_gate_rejects_tsla1_command_text_search_scope() -> None:
    """逐字回归: T-SL-A1 实际发布的反模式 machine_check → 发布门 blocker (不再放到复算才砸)。"""
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="session_lock.py 与 goal 锁族在本任务 commit 中零改动",
        verification_method=ClosureVerificationMethod.GREP,
        expected_result="0 命中(白名单文件不在改动清单)",
        verification_pattern=r"session_lock\.py|l1/goal/",
        expected_occurrences=0,
        search_scope=_TSLA1_COMMAND_TEXT_SCOPE,
    )))
    assert result.is_self_contained is False
    assert any("形态缺陷" in b and "占位符" in b for b in result.blockers), result.blockers


def test_gate_rejects_grep_machine_check_missing_pattern() -> None:
    """grep machine_check 没绑 pattern/expected → 合约自相矛盾, 发布时即拒 (复算必 fail-closed)。"""
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.GREP, expected_result="0",
    )))
    assert result.is_self_contained is False
    assert any("自相矛盾" in b for b in result.blockers), result.blockers


def test_gate_rejects_test_machine_check_missing_selector() -> None:
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.TEST, expected_result="exit 0",
    )))
    assert result.is_self_contained is False
    assert any("test_selector" in b for b in result.blockers), result.blockers


def test_gate_accepts_git_diff_machine_check() -> None:
    """缺口 (a) 正控: INV 类不变量 ('文件 X 零改动') 现在有合法编码 — git_diff machine_check
    过发布门 + 计入 LB2 可机器复算 (manual_verify 不再被拒)。"""
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="session_lock.py 与 goal 锁族在本任务 commit 中零改动",
        verification_method=ClosureVerificationMethod.GIT_DIFF,
        expected_result="0 命中 (白名单文件不在改动清单)",
        verification_pattern=r"session_lock\.py|l1/goal/",
        expected_occurrences=0,
    )))
    assert result.is_self_contained is True, result.blockers


def test_gate_rejects_git_diff_missing_pattern() -> None:
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.GIT_DIFF, expected_result="0",
    )))
    assert result.is_self_contained is False
    assert any("自相矛盾" in b for b in result.blockers), result.blockers


def test_gate_rejects_git_diff_invalid_regex() -> None:
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.GIT_DIFF,
        expected_result="0", verification_pattern=r"([unclosed", expected_occurrences=0,
    )))
    assert result.is_self_contained is False
    assert any("正则" in b for b in result.blockers), result.blockers


def test_gate_rejects_absolute_search_scope() -> None:
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.GREP,
        expected_result="0", verification_pattern="p", expected_occurrences=0,
        search_scope="/etc",
    )))
    assert result.is_self_contained is False
    assert any("绝对路径" in b for b in result.blockers), result.blockers


def test_gate_accepts_legal_relative_search_scope() -> None:
    """负对照 (防误伤): 合法相对路径 search_scope 照常通过 (既有 RUN-044 用法不回归)。"""
    result = _gate(_mc_criterion(ClosureCriterion(
        condition="x", verification_method=ClosureVerificationMethod.GREP,
        expected_result="0", verification_pattern="p", expected_occurrences=0,
        search_scope="src/towow",
    )))
    assert result.is_self_contained is True, result.blockers


# ─── fnd-r01-9 (assembler 第二面): owner-gated 任务 machine_check 豁免 ──────────────────────────
#
# 安全洞: 组装器对 owner-gated 红线任务无豁免口 → 它的"完成"判据本质是 owner 批准 (manual_verify,
# 无机器可判), 却被逼带 machine_check → planner 要么伪造一个假 test_selector/git_diff (work complete
# 复算时永久 fail-closed = 红线任务做不完), 要么发不出包。修: content.requires_owner_gate=True 时豁免
# manual_verify 免 machine_check; 豁免**只对 manual_verify 开**且与 canonical TaskNode 权威标记交叉核对
# (防"谁都能塞 requires_owner_gate=True 跳验证"的普遍洞)。


def _owner_gate_node(task_id: str, *, gated: bool) -> dict[str, object]:
    """canonical TaskNodeCreated event carrying the authoritative requires_owner_gate flag."""
    return {
        "event_type": "TaskNodeCreated",
        "event_id": f"evt-node-{task_id}",
        "payload": {"after_state": {"task_id": task_id, "requires_owner_gate": gated}},
    }


def _owner_approval_criterion() -> DoneCriterion:
    """owner-gated 任务的"完成"判据 = owner 显式批准 (manual_verify, 本无机器可判, 无 machine_check)。"""
    return DoneCriterion(
        criterion_id="dc-owner-approval",
        description="owner 显式批准上线",
        verification_type="manual_verify",
        evidence_ref=EvidenceRef(
            source_type="nature_judgment", source_id="nj-deploy", quoted_claim="上线到生产需我批准",
        ),
    )


def test_owner_gated_manual_verify_exempt_when_authoritative_agrees() -> None:
    """核心修: owner-gated 任务 (content flag + 权威 TaskNode 一致) 的 owner-批准型判据通过发布门 —
    不被逼伪造 machine_check。"""
    c = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": True},
    )
    result = validate_publication_gate(
        c, compute_package_hash(c),
        events=[_owner_gate_node("t-deploy", gated=True)], plan_id="p1", publishing_task_id="t-deploy",
    )
    assert result.is_self_contained is True, result.blockers


def test_normal_task_manual_verify_still_blocked_without_flag() -> None:
    """豁免窄: 普通任务 (requires_owner_gate=False) 的 manual_verify 仍被强制要 machine_check — 不回归。"""
    c = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": False},
    )
    result = validate_publication_gate(
        c, compute_package_hash(c),
        events=[_owner_gate_node("t-normal", gated=False)], plan_id="p1", publishing_task_id="t-normal",
    )
    assert result.is_self_contained is False
    assert any("机器复算" in b for b in result.blockers), result.blockers


def test_anti_hole_self_granted_owner_gate_rejected_when_node_disagrees() -> None:
    """anti-hole: package 自报 requires_owner_gate=True 但权威 TaskNode 说 False → 拒 (防自报跳门)。"""
    c = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": True},
    )
    result = validate_publication_gate(
        c, compute_package_hash(c),
        events=[_owner_gate_node("t-fake", gated=False)], plan_id="p1", publishing_task_id="t-fake",
    )
    assert result.is_self_contained is False
    assert any("cannot self-grant machine_check exemption" in b for b in result.blockers), result.blockers


def test_anti_hole_self_granted_owner_gate_rejected_when_no_node() -> None:
    """anti-hole: package 自报 owner-gate 但 event log 里根本没有该 task 的 TaskNodeCreated → 拒。"""
    c = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": True},
    )
    result = validate_publication_gate(
        c, compute_package_hash(c), events=[], plan_id="p1", publishing_task_id="t-missing",
    )
    assert result.is_self_contained is False
    assert any("no TaskNodeCreated found" in b for b in result.blockers), result.blockers


def test_owner_gate_exemption_narrow_test_passes_still_needs_machine_check() -> None:
    """豁免只对 manual_verify 开: owner-gated 任务里若混 test_passes 判据 (声称机械) 仍须带 machine_check。"""
    test_passes_no_mc = DoneCriterion(
        criterion_id="dc-tp",
        description="POST /deploy 返回 200",
        verification_type="test_passes",
        evidence_ref=_evref(),
    )
    c = _good_content().model_copy(
        update={"done_criteria": [test_passes_no_mc], "requires_owner_gate": True},
    )
    result = validate_publication_gate(
        c, compute_package_hash(c),
        events=[_owner_gate_node("t-mixed", gated=True)], plan_id="p1", publishing_task_id="t-mixed",
    )
    assert result.is_self_contained is False
    assert any("dc-tp" in b for b in result.blockers), result.blockers


def test_owner_gate_write_boundary_consistent_with_cli_gate() -> None:
    """写边界 (TaskPackagePublishedAfter) 与 CLI 发布门一致: owner-gated 自包含 package 落账不被拒;
    普通 manual_verify 无 machine_check 的自包含 package 仍被写边界拒 (machine_check 地板不被洞穿)。"""
    from towow.schemas.payloads.state_transition import TaskPackagePublishedAfter

    gated = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": True},
    )
    # owner-gated → ACCEPTED (一致)
    TaskPackagePublishedAfter(
        task_id="t-deploy", package_hash=compute_package_hash(gated),
        is_self_contained=True, package_content=gated,
    )

    normal = _good_content().model_copy(
        update={"done_criteria": [_owner_approval_criterion()], "requires_owner_gate": False},
    )
    # 普通任务同样的 manual_verify → 写边界仍拒 is_self_contained=True
    import pytest

    with pytest.raises(ValueError, match="机器复算"):
        TaskPackagePublishedAfter(
            task_id="t-normal", package_hash=compute_package_hash(normal),
            is_self_contained=True, package_content=normal,
        )
