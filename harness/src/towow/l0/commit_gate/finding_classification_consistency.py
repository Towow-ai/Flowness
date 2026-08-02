"""finding-classification-consistency-birth-gate@v1 (W1-R2, LEDGER Conflict 32) — commit gate 对
FindingCreated 准入加一道分类一致性检查，抓"finding_kind 打错"（不是 finding_birth_gate.py 已经在管
的"可路由性"，是"分类跟内容自相矛盾"）。

## 背景 (Conflict 32)

finding_kind=concept_issue 把一条语义上是 premise_false（task 因前提为假该退役）的 finding 路由到
engineering-consensus 座位——consensus 写不了代码、处理不了"task 该不该退休"这种 task-graph 终态判定，
是第 6 次同构"路由税"事故。前 5 次 (Conflict 22-26) 是 suggested_fix_layer=code 类的座位矛盾，已有
_fix_layer_contradicts_consensus_seat 纠偏 (orchestrator.py) 覆盖；本次是新变体：suggested_fix_layer=
null，现有纠偏机制不覆盖。真实案例 (finding f-prw-testdebug-premise-false，evt-ea2a9989a3674356b8b8df
2ccabb3c0b) closure_contract 明写 "T-PRW-TEST-DEBUG 经 plan task-close --reason retired 关闭"，
description 写"premise-false，应退休"，但 finding_kind 打成了 concept_issue。

## R2 — 退役语义误分类检测

finding_kind != premise_false 且 description/closure_contract 文本命中退役信号正则 → 疑似误分类。
命中分两类 (总控裁决)：
  - 机械模式 (--reason retired / reason=retired / TaskClosureReason.RETIRED / task-close...retired 等
    代码化字面 token，误伤风险低)；
  - 自然语言模式 (前提为假/应退休/应退役/premise-false 等中文或散文措辞，误伤风险高——同样的词组可能
    出现在与退役无关的讨论里)。
只有 finding_kind=concept_issue 且命中机械模式，才有资格硬拒 (总控裁决收窄的范围)；其余情况 (非
concept_issue 的 kind、或自然语言模式命中) 永远只告警，不拒绝。

## R3 — premise_false 结构完整性前置检查

finding_kind=premise_false 但 subjects 里没有 entity_type=task 的锚定项 → 疑似缺锚定。把纠错点从
TaskNodeClosed 时 (closure_evidence_check.py _retired_premise_false_verified，太晚) 前移到
FindingCreated 时。

## R4 — 故意不做 (记录、不留代码)

dashboard-only 的 5 个 finding_kind (cross_projection_inconsistency / routing_stuck / anomaly /
efficiency_regression / system_governance_defect) 不派 worker，不存在"座位能力被违反"，不检查
suggested_fix_layer 矛盾。写在这里防将来有人"顺手"给它们加规则。

## warn-only 观察期 (总控裁决 1)

R2、R3 各自独立一个开关 (读 .towow/maintenance/config.json 两个 bool key)，默认都是 False = warn-only
(fail-safe-closed，镜像 orchestrator.py governance_auto_repair_unfrozen 的 INF-003 开关先例)。两条规则
风险画像不同、校准曲线独立——共用一个开关会把"R2 30 天重放数据干净了可以切硬拒"跟"R3 还没校准"耦合在一起，
被迫同升同降。默认关闭时两条规则都只在 notices 里留非阻断提示，供 30 天账本重放统计命中率、人工抽查确认
无误伤后，owner 显式把对应 key 写 true 才升级为硬拒。

L0→L2 无 import (不依赖 orchestrator)；本文件只读 domain_intents/event_log，不喂给 _route_event。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from towow.l0.commit_gate.finding_birth_gate import FindingRoutabilityResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from towow.l0.event_log import EventLog
    from towow.schemas.event_intent import EventIntent, Subject

_FINDING_CREATED = "FindingCreated"
_CONCEPT_ISSUE_KIND = "concept_issue"
_PREMISE_FALSE_KIND = "premise_false"
_TASK_SUBJECT_ENTITY_TYPE = "task"

_R2_ENFORCE_KEY = "finding_classification_r2_enforce"
_R3_ENFORCE_KEY = "finding_classification_r3_enforce"

# 机械模式：代码化字面 token (CLI flag / enum 全名 / 明确的 task-close 动作短语)。命中且
# finding_kind=concept_issue 才有资格在 enforce=True 时硬拒。
#
# `task[-_]close.{0,120}retired` 用有界量词代替原来的贪婪 `.*` (W1-R2 返修轮, VERIFY-REPORT §2.f
# 致命问题)：无界 `.*` 在文本里有多个 "task-close" 但没有 "retired" 时会退化为 O(n²) 回溯 (实测
# 100KB 对抗文本 3.3 秒)，且该检查在 enforce=False (默认) 时也无条件跑在全局串行 commit 锁内
# (cli/main.py:192)。120 字符上限留了充分余量——Conflict 32 真实案例里 "task-close" 到 "retired"
# 之间只隔 "--reason " 共 9 个字符 (且实际是被前一个 alternative `--reason\s+retired` 先命中)。
MECHANICAL_RETIREMENT_PATTERN = re.compile(
    r"--reason\s+retired"
    r"|reason\s*[=:]\s*retired"
    r"|TaskClosureReason\.RETIRED"
    r"|task[-_]close.{0,120}retired",
    re.IGNORECASE,
)
# 自然语言模式：中文/散文措辞，误伤风险高——永远只告警，不参与硬拒判定。
NATURAL_LANGUAGE_RETIREMENT_PATTERN = re.compile(
    r"前提为假|应退休|应退役|premise[-_]false",
    re.IGNORECASE,
)

# 喂给上面两条正则的检索文本长度上限——双保险的第二层：即使量词本身未来被误改回无界，被搜索
# 文本本身也有硬上限，不会退化成 O(n²)。退役信号短语设计上出现在文本靠前位置 (当事人描述当前
# 事件用的措辞，不会藏在几千字之后)，截断不影响真实检出率。同时保护本文件里另一条无量词上限
# 隐患的正则 (NATURAL_LANGUAGE_RETIREMENT_PATTERN 本身是纯 alternation、无回溯风险，但输入长度
# 上限对它同样无害)。
_RETIREMENT_SCAN_TEXT_LIMIT = 4096


def truncate_for_retirement_scan(text: str) -> str:
    """对将喂给 MECHANICAL_RETIREMENT_PATTERN / NATURAL_LANGUAGE_RETIREMENT_PATTERN 的文本做长度
    截断 (前 4096 字符)。orchestrator.py 的短期止血分支与本模块的 R2 检查共用本函数，确保两处
    退役信号正则搜索都受益于同一条长度上限。"""
    return text[:_RETIREMENT_SCAN_TEXT_LIMIT]


def _config_flag(towow_dir: Path, key: str) -> bool:
    """读 .towow/maintenance/config.json 的一个 bool key；缺失/坏文件 → False (fail-safe-closed)。

    镜像 orchestrator.py governance_auto_repair_unfrozen 的开关先例：默认绝不硬拒，owner 显式把
    对应 key 写 true 后才生效。
    """
    import json

    path = towow_dir / "maintenance" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get(key) is True


def _towow_root_from_event_log(event_log: EventLog) -> Path:
    """从 event_log 的 log 路径上溯找 .towow 根 (含 graph/ 的那层); 找不到退 log 所在目录。

    小工具函数, 逻辑镜像 orchestrator.py 同名私有函数 (两层各自独立维护, 逻辑本身长期稳定,
    不值得为省 6 行代码在 L0 里跨模块 import L2 的私有符号)。
    """
    log_path = Path(event_log._log_path)
    for p in (log_path.parent, *log_path.parents):
        if (p / "graph").is_dir():
            return p
    return log_path.parent


def enforced_flags(event_log: EventLog | None) -> tuple[bool, bool]:
    """返回 (r2_enforce, r3_enforce)；event_log 缺省 → 两者皆 False (fail-safe-closed)。"""
    if event_log is None:
        return False, False
    towow_dir = _towow_root_from_event_log(event_log)
    return _config_flag(towow_dir, _R2_ENFORCE_KEY), _config_flag(towow_dir, _R3_ENFORCE_KEY)


def _closure_contract_text(payload: dict[str, object]) -> str:
    """description + closure_contract.closure_criteria[].condition/expected_result 拼一份检索文本。

    返回值在正则搜索前已做长度截断 (见 truncate_for_retirement_scan / _RETIREMENT_SCAN_TEXT_LIMIT)：
    退役信号短语设计上出现在文本靠前位置，截断不影响真实检出率，但防止无上限的 finding
    description/closure_contract 文本喂给正则时退化成 O(n²) 回溯 (W1-R2 返修轮，VERIFY-REPORT §2.f)。
    """
    parts = [str(payload.get("description") or "")]
    closure_contract = payload.get("closure_contract")
    if isinstance(closure_contract, dict):
        for criterion in closure_contract.get("closure_criteria") or []:
            if isinstance(criterion, dict):
                parts.append(str(criterion.get("condition") or ""))
                parts.append(str(criterion.get("expected_result") or ""))
    return truncate_for_retirement_scan("\n".join(parts))


def _has_task_anchor(subjects: Sequence[Subject]) -> bool:
    """subjects 里是否有 entity_type=task 的项 (镜像 closure_evidence_check._verdict_subjects_task
    的判据, 但本检查只问"有没有任一 task 锚定", 不比对具体 task_id)。"""
    for subject in subjects:
        entity_type = getattr(subject, "entity_type", None)
        value = getattr(entity_type, "value", entity_type)
        if value == _TASK_SUBJECT_ENTITY_TYPE:
            return True
    return False


@dataclass(frozen=True)
class _RuleOutcome:
    reject: bool
    failure_reason: str | None = None
    notice: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)


def _evaluate_r2(payload: dict[str, object], *, enforce: bool) -> _RuleOutcome:
    finding_kind = payload.get("finding_kind")
    if finding_kind == _PREMISE_FALSE_KIND:
        return _RuleOutcome(reject=False)
    text = _closure_contract_text(payload)
    mechanical = MECHANICAL_RETIREMENT_PATTERN.search(text)
    natural = NATURAL_LANGUAGE_RETIREMENT_PATTERN.search(text)
    matched = mechanical or natural
    if matched is None:
        return _RuleOutcome(reject=False)
    matched_pattern = matched.group(0)
    evidence = {
        "rule": "R2",
        "finding_id": str(payload.get("finding_id", "")),
        "finding_kind": str(finding_kind),
        "matched_pattern": matched_pattern,
        "match_type": "mechanical" if mechanical else "natural_language",
    }
    message = (
        f"closure_contract/description 内容像是在描述 task 退役闭合 (检出模式: {matched_pattern!r})，"
        f"但 finding_kind={finding_kind} 不是 premise_false。若确是退役场景，请改 "
        f"finding_kind=premise_false 并带 task 锚定 subject (entity_type=task)；若不是，请去掉退役类"
        f"措辞或改用别的 closure 描述。(finding-classification-consistency R2 / LEDGER Conflict 32)"
    )
    hard_reject_eligible = finding_kind == _CONCEPT_ISSUE_KIND and mechanical is not None
    if hard_reject_eligible and enforce:
        return _RuleOutcome(
            reject=True,
            failure_reason="finding_classification_retirement_language_misrouted",
            evidence=evidence,
        )
    return _RuleOutcome(reject=False, notice=f"[warn-only][R2] {message}", evidence=evidence)


def _evaluate_r3(payload: dict[str, object], subjects: Sequence[Subject], *, enforce: bool) -> _RuleOutcome:
    if payload.get("finding_kind") != _PREMISE_FALSE_KIND:
        return _RuleOutcome(reject=False)
    if _has_task_anchor(subjects):
        return _RuleOutcome(reject=False)
    evidence = {
        "rule": "R3",
        "finding_id": str(payload.get("finding_id", "")),
        "finding_kind": _PREMISE_FALSE_KIND,
    }
    message = (
        "finding_kind=premise_false 必须锚定被退役的 task (subjects 含 entity_type=task)，否则 "
        "closure-evidence-verification-gate@v1 的 retired 分支永远拒绝复用——现在补，好过 "
        "TaskNodeClosed 时才发现。(finding-classification-consistency R3)"
    )
    if enforce:
        return _RuleOutcome(
            reject=True,
            failure_reason="finding_classification_premise_false_missing_task_anchor",
            evidence=evidence,
        )
    return _RuleOutcome(reject=False, notice=f"[warn-only][R3] {message}", evidence=evidence)


def check_finding_classification_consistency(
    domain_intents: list[EventIntent],
    event_log: EventLog | None = None,
) -> FindingRoutabilityResult:
    """对 envelope batch 里每个 FindingCreated 校验 R2 (退役语义误分类) + R3 (premise_false 结构完整
    性)。warn-only 观察期 (默认): 命中只留 notices, passed=True。owner 显式解冻对应 enforce key 后,
    命中范围内的规则才 fail-closed 拒整批。

    非 FindingCreated intent → 跳过。单条 finding 分类检查内部异常 (畸形 payload/subjects) 绝不让
    整条 commit gate 失败——这是尽力而为的辅助闸, 不是 finding 合法性的唯一裁判 (那是
    check_finding_routability 的职责), 内部异常直接跳过该条, 不影响其余 intent。
    """
    r2_enforce, r3_enforce = enforced_flags(event_log)
    notices: list[str] = []

    for intent in domain_intents:
        if intent.event_type.value != _FINDING_CREATED:
            continue
        try:
            payload = intent.payload if isinstance(intent.payload, dict) else {}
            subjects = list(intent.subjects)
            for outcome in (
                _evaluate_r2(payload, enforce=r2_enforce),
                _evaluate_r3(payload, subjects, enforce=r3_enforce),
            ):
                if outcome.reject:
                    return FindingRoutabilityResult(
                        passed=False,
                        failure_reason=outcome.failure_reason,
                        failure_evidence=outcome.evidence,
                    )
                if outcome.notice:
                    notices.append(outcome.notice)
        except Exception:  # noqa: S112 — 防御性: 分类一致性检查不能让畸形 finding 拖垮整个 commit gate
            continue

    return FindingRoutabilityResult(passed=True, notices=notices)


__all__ = [
    "MECHANICAL_RETIREMENT_PATTERN",
    "NATURAL_LANGUAGE_RETIREMENT_PATTERN",
    "check_finding_classification_consistency",
    "enforced_flags",
    "truncate_for_retirement_scan",
]
