"""FindingCreated 写边界 closure_contract 形态校验 (发布门).

# finding source:
#   finding-finding-contract-form-publication-gap-1780566273 (fix_after review 独立证伪
#   fork scope 外发现, 审 finding-machine-check-encoding-gap-1780563112 修复 1d9a53c 时):
#   形态校验双门只覆盖了 task-package 面 (validate_publication_gate done_criteria),
#   FindingCreated 面不对称 — review finding 自己签的 closure_contract 在写边界只验
#   存在性+VoI+location, 不验 machine_check 形态 → 含占位符 search_scope / grep 未绑
#   pattern 的合约可发布成功, 到 fix-after 复算 (l1/closure_verification) 才 fail-closed,
#   该 finding 从此无法走 confirmed_and_fixed 机器闭合。
#
# 钉住行为: source.type=model_review 的 FindingCreatedPayload, closure_contract.
# closure_criteria 逐条过 closure_criterion_contract_defects (与 task_package.py 发布门
# 同一纯函数) — 形态缺陷发布即拒 (raise), 使知识文档 finding-as-first-class-object.md
# 宣称的"发布门 + 复算门双重拒"对 closure_contract 真成立。
# 非 model_review 语境 (系统/maintenance finding) 不受约束 — 跨语境复用 + 重放安全。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from towow.schemas.payloads.finding import FindingCreatedPayload


def _mv(data: dict[str, Any]) -> FindingCreatedPayload:
    """对齐真实写边界路径: registry.validate_event_payload 用 strict=False
    (payload 是 JSON-derived dict, enum 以字符串到达 — registry.py L36)。"""
    return FindingCreatedPayload.model_validate(data, strict=False)


# ── 合法 review finding 基底 (过存在性 + RUN-093 VoI/location 实质门) ──────────────

_BASE: dict[str, Any] = {
    "finding_id": "finding-contract-form-case",
    "severity": "minor",
    "risk_surface": "closure_contract 发布面形态缺陷 → fix-after 复算 fail-closed 永久拒",
    "description": "形态校验发布门测试用例",
    "detection_method": "manual_review",
    "voi_rationale": "closure_contract 形态缺陷在发布门拦下, 省一轮注定无法机器闭合的 fix 循环",
    "target": {"artifact": "src/towow/schemas/payloads/finding.py", "location": "finding.py:139"},
    "falsification_evidence": {"attempt": "构造形态缺陷合约尝试发布", "result": "confirmed"},
    "source": {"type": "model_review", "agent_id": "m15.review"},
}


def _payload(criteria: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    data = dict(_BASE)
    data["closure_contract"] = {
        "closure_criteria": criteria,
        "ripple_targets": [],
        "forbidden_residuals": [],
    }
    data.update(overrides)
    return data


_VALID_GREP = {
    "condition": "旧模式清零 (forbidden residual 同义钉法)",
    "verification_method": "grep",
    "expected_result": "0 occurrences",
    "verification_pattern": "OLD_PATTERN_MARK",
    "expected_occurrences": 0,
}

_VALID_TEST = {
    "condition": "专门单测钉住发布门拒收行为",
    "verification_method": "test",
    "expected_result": "pytest exit 0",
    "test_selector": "tests/unit/schemas/payloads/test_finding_closure_contract_form.py",
}


# ── 正例: 形态合法合约发布成功 ────────────────────────────────────────────────────


def test_well_formed_grep_and_test_contract_publishes() -> None:
    p = _mv(_payload([_VALID_GREP, _VALID_TEST]))
    assert p.closure_contract is not None
    assert len(p.closure_contract.closure_criteria) == 2


def test_well_formed_contract_with_path_search_scope_publishes() -> None:
    crit = {**_VALID_GREP, "search_scope": "src/towow/schemas/payloads"}
    p = _mv(_payload([crit]))
    assert p.closure_contract is not None
    assert p.closure_contract.closure_criteria[0].search_scope == "src/towow/schemas/payloads"


# ── 负例: 合约自相矛盾 (声明可机器验却没给复算参数) → 发布即拒 ────────────────────


def test_grep_without_pattern_rejected() -> None:
    crit = {k: v for k, v in _VALID_GREP.items() if k not in ("verification_pattern", "expected_occurrences")}
    with pytest.raises(ValidationError, match="closure_contract 形态缺陷"):
        _mv(_payload([crit]))


def test_grep_without_expected_occurrences_rejected() -> None:
    crit = {k: v for k, v in _VALID_GREP.items() if k != "expected_occurrences"}
    with pytest.raises(ValidationError, match="未绑 verification_pattern/expected_occurrences"):
        _mv(_payload([crit]))


def test_git_diff_without_pattern_rejected() -> None:
    crit = {
        "condition": "本任务 commit 集不得触碰文件 X",
        "verification_method": "git_diff",
        "expected_result": "0 命中",
    }
    with pytest.raises(ValidationError, match="未绑 verification_pattern/expected_occurrences"):
        _mv(_payload([crit]))


def test_test_without_selector_rejected() -> None:
    crit = {k: v for k, v in _VALID_TEST.items() if k != "test_selector"}
    with pytest.raises(ValidationError, match="未绑 test_selector"):
        _mv(_payload([crit]))


# ── 负例 (f-ohr3-done-criteria-exact-match-vs-existence-intent 修复引入的对称洞):
#    occurrence_comparator=gte 但 expected_occurrences<1 → `occ >= 0` 恒真, 空洞真验 ─────


def test_grep_gte_with_zero_expected_occurrences_rejected() -> None:
    crit = {**_VALID_GREP, "expected_occurrences": 0, "occurrence_comparator": "gte"}
    with pytest.raises(ValidationError, match="恒真, 声称机器验却什么都不验"):
        _mv(_payload([crit]))


def test_grep_gte_with_positive_expected_occurrences_publishes() -> None:
    """gte 语义须 expected_occurrences>=1 才有意义 (真存在性下限) — 合法值照常放行。"""
    crit = {**_VALID_GREP, "expected_occurrences": 1, "occurrence_comparator": "gte"}
    p = _mv(_payload([crit]))
    assert p.closure_contract is not None
    assert p.closure_contract.closure_criteria[0].occurrence_comparator.value == "gte"


def test_grep_eq_default_with_zero_expected_occurrences_still_publishes() -> None:
    """occurrence_comparator 缺省 (None=eq) + expected_occurrences=0 是合法的"必须清零"判据

    (residual/old-path-removal 存量惯用形态), 不受新增 gte 校验影响 — 回归锚。
    """
    crit = {**_VALID_GREP, "expected_occurrences": 0}
    p = _mv(_payload([crit]))
    assert p.closure_contract is not None
    assert p.closure_contract.closure_criteria[0].occurrence_comparator is None


def test_git_diff_uncompilable_pattern_rejected() -> None:
    crit = {
        "condition": "改动清单 pattern 须 re 可编译",
        "verification_method": "git_diff",
        "expected_result": "0 命中",
        "verification_pattern": "[unclosed",
        "expected_occurrences": 0,
    }
    with pytest.raises(ValidationError, match="不是合法 Python 正则"):
        _mv(_payload([crit]))


# ── 负例: search_scope 非路径形态 (T-SL-A1 反模式家族) → 发布即拒 ────────────────


def test_tsla1_placeholder_command_text_search_scope_rejected() -> None:
    """T-SL-A1 占位符反模式逐字用例 — 命令模板文本被当 search_scope 发布.

    实撞原文: assemble 把 'git diff --name-only <本任务commit>^..<本任务commit>'
    (含永远无人绑定的占位符) 当 search_scope 发布, 发布门照过 → 复算 repo_dir/<该文本>
    路径不存在 → fail-closed 永久拒。本用例钉住: FindingCreated 面同形态发布即拒。
    """
    crit = {**_VALID_GREP, "search_scope": "git diff --name-only <本任务commit>^..<本任务commit>"}
    with pytest.raises(ValidationError, match="closure_contract 形态缺陷"):
        _mv(_payload([crit]))


def test_whitespace_command_text_search_scope_rejected() -> None:
    crit = {**_VALID_GREP, "search_scope": "grep -r pattern src/"}
    with pytest.raises(ValidationError, match="含空白"):
        _mv(_payload([crit]))


def test_absolute_path_search_scope_rejected() -> None:
    crit = {**_VALID_GREP, "search_scope": "/etc"}
    with pytest.raises(ValidationError, match="绝对路径"):
        _mv(_payload([crit]))


def test_dotdot_escape_search_scope_rejected() -> None:
    crit = {**_VALID_GREP, "search_scope": "../outside"}
    with pytest.raises(ValidationError, match="越界"):
        _mv(_payload([crit]))


# ── 多 criteria: 缺陷逐条列出 (一次发布失败看全所有缺陷, 不挤牙膏) ────────────────


def test_multiple_defective_criteria_all_reported() -> None:
    bad_grep = {k: v for k, v in _VALID_GREP.items() if k != "verification_pattern"}
    bad_test = {k: v for k, v in _VALID_TEST.items() if k != "test_selector"}
    with pytest.raises(ValidationError) as exc_info:
        _mv(_payload([bad_grep, bad_test]))
    msg = str(exc_info.value)
    assert "closure_criteria[0]" in msg
    assert "closure_criteria[1]" in msg


# ── forbidden_residuals: grep 类 search_scope 形态门 (忠实镜像 criterion 待遇) ────
# f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ①:
# scope **非路径形态** (占位符/命令文本/绝对路径/越界) → 发布即拒 (dead-on-arrival, 复算门必
# fail-closed)。scope **缺席** 不硬拒 —— 与 criterion 对齐 (criterion 的 None 合法), 且 ②③ 落地后
# 无 scope 全域 grep 不再假阳性; 缺 scope 走 CLI 层警告 (test_review_cli 覆盖), 非 schema 硬门。


def _residual_payload(residuals: list[dict[str, Any]]) -> dict[str, Any]:
    data = dict(_BASE)
    data["closure_contract"] = {
        "closure_criteria": [_VALID_TEST],
        "ripple_targets": [],
        "forbidden_residuals": residuals,
    }
    return data


_VALID_RESIDUAL = {
    "pattern": "OLD_PATTERN_MARK",
    "rationale": "旧表述残留 = 没修干净",
    "check_method": "grep",
    "search_scope": "src/towow",
}


def test_grep_residual_with_path_search_scope_publishes() -> None:
    p = _mv(_residual_payload([_VALID_RESIDUAL]))
    assert p.closure_contract is not None
    assert p.closure_contract.forbidden_residuals[0].search_scope == "src/towow"


def test_grep_residual_without_search_scope_publishes_not_hard_rejected() -> None:
    """缺 scope 不是发布门硬缺陷 (镜像 criterion 的 None 合法): 发布通过, 软提示在 CLI 层。

    硬拒缺 scope 会把共享发布门语义从"拒不能闭合的合约"扩成"拒 scope 次优的合约" — 超出本
    finding 的共识变更 (advisor 判)。②③ 落地后无 scope 全域 grep 能正常闭合, 只是扫描面偏大。
    """
    residual = {k: v for k, v in _VALID_RESIDUAL.items() if k != "search_scope"}
    p = _mv(_residual_payload([residual]))
    assert p.closure_contract is not None
    assert p.closure_contract.forbidden_residuals[0].search_scope is None


def test_grep_residual_malformed_command_text_search_scope_rejected() -> None:
    """scope 存在但非路径形态 (命令文本) → 发布即拒 (忠实镜像 criterion 的 scope 形态门)。"""
    residual = {**_VALID_RESIDUAL, "search_scope": "grep -r OLD src/"}
    with pytest.raises(ValidationError, match="含空白"):
        _mv(_residual_payload([residual]))


def test_grep_residual_absolute_search_scope_rejected() -> None:
    residual = {**_VALID_RESIDUAL, "search_scope": "/etc"}
    with pytest.raises(ValidationError, match="绝对路径"):
        _mv(_residual_payload([residual]))


def test_grep_residual_explicit_repo_root_scope_publishes() -> None:
    residual = {**_VALID_RESIDUAL, "search_scope": "."}
    p = _mv(_residual_payload([residual]))
    assert p.closure_contract is not None


# ── 语境边界: 非 model_review 不受约束 (跨语境复用 + 历史重放安全) ────────────────


def test_non_review_source_defective_contract_not_blocked() -> None:
    """系统/maintenance finding (source.type≠model_review) 带形态缺陷合约不拦.

    FindingCreated 跨语境复用 (projection 自查等), §3.2 review 必填族只对 model_review
    强制 — 与 enforce_review_context_fields 既有语境边界一致 (重放安全同理:
    读/重放路径不过写边界 validator, 此处钉住的是写边界语境豁免)。
    """
    bad_test = {k: v for k, v in _VALID_TEST.items() if k != "test_selector"}
    data = _payload([bad_test], source={"type": "physical_hook", "hook_id": "h-1"})
    p = _mv(data)
    assert p.closure_contract is not None


def test_no_source_defective_contract_not_blocked() -> None:
    bad_test = {k: v for k, v in _VALID_TEST.items() if k != "test_selector"}
    data = _payload([bad_test])
    del data["source"]
    p = _mv(data)
    assert p.source is None
