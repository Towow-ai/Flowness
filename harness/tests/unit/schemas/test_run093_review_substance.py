"""RUN-093 审计 v2 R3 — review 实质判据纯函数单测 (1.5-a VoI 锚定 / 1.5-b finding 可定位).

钉住 spec 自检 #1/#4 (VoI 必须锚定真目标, 非"任何改进都算") + §3.2 (location = 文件:行/函数/
entity id, 非散文) 的机械下限。before: 泛泛 VoI / 无锚 location 全过门 (run093-baseline.json)。
"""

from __future__ import annotations

import pytest

from towow.schemas.payloads.review_substance import (
    finding_location_anchored,
    has_structural_anchor,
    voi_rationale_substantive,
)

# ── 件A · VoI 实质 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "voi",
    [
        "修复 gate.py:838 off-by-one 让 commit_seq 单调性约束成立",  # 结构锚 file:line
        "patch 的 loop bound 改动是 done_criteria 第 1 条覆盖范围",   # 领域锚词 done_criteria
        "并发写 shared write_set 未进 freeze 门 — done_criteria 第 2 条覆盖",  # write_set + done_criteria
        "防止越界写绕过 owner-guard 红线",                            # 领域锚词 红线/越界
        "fix 后须验闭合, 没修好不能当修好",                          # 领域锚词 闭合
        "L42 的并发竞争影响 commit gate 单调性",                      # L42 + 单调
    ],
)
def test_voi_substantive_anchored_passes(voi: str) -> None:
    assert voi_rationale_substantive(
        voi, risk_surface="commit_gate_monotonicity",
        target_artifact="src/towow/l0/commit_gate/gate.py", target_location="gate.py:838",
    ) is True


@pytest.mark.parametrize(
    "voi",
    [
        "",                       # 空
        "   ",                    # 纯空白
        "任何改进",                # 审计探针
        "任何改进都算, 提升代码质量",  # 审计探针变体
        "可读性提升",              # 现 fixture 式泛泛
        "代码更好更清晰",          # 纯价值断言
        "improves quality",       # ASCII 泛泛 (stopword)
        "any improvement counts",
    ],
)
def test_voi_vacuous_rejected_when_no_anchor(voi: str) -> None:
    # risk_surface/target 与 voi 无共享实义 → 三条路径都不命中 → 拒。
    assert voi_rationale_substantive(
        voi, risk_surface="commit_gate_monotonicity",
        target_artifact="src/x.py", target_location="x.py:42",
    ) is False


def test_voi_shares_token_with_risk_surface_passes() -> None:
    """泛泛措辞但与 risk_surface 共享实义 token → 视为指向了具体主题 (路径 c)。"""
    assert voi_rationale_substantive(
        "并发安全很重要", risk_surface="并发写竞争", target_artifact="src/x.py",
        target_location="L42",
    ) is True
    # 同 ASCII 形态
    assert voi_rationale_substantive(
        "concurrency matters here", risk_surface="concurrency race on write_set",
        target_artifact="src/x.py", target_location="L42",
    ) is True


# ── 件B · finding 可定位 (无锚丢弃) ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "loc",
    ["x.py:42", "gate.py:838", "x.py:1", "L42", "L1", "file.py:5",
     "towow.l1.review_aggregation", "func_name()", "#L99", "evt-abc123"],
)
def test_location_anchored_passes(loc: str) -> None:
    assert finding_location_anchored(loc) is True


@pytest.mark.parametrize(
    "loc",
    ["", "   ", "somewhere", "代码里某处", "general", "the code", "整个模块", "various places"],
)
def test_location_prose_rejected(loc: str) -> None:
    assert finding_location_anchored(loc) is False


def test_structural_anchor_smoke() -> None:
    assert has_structural_anchor("see gate.py:838") is True
    assert has_structural_anchor("纯散文没有任何定位") is False
