"""T-REFLOW-EVTIER — functional-equivalence-evidence-tier@v1: 功能等价证据分级共享内核.

# concept source:
#   concept functional-equivalence-evidence-tier@v1 (evt-3187b7fcf3e740c4b952d8b28c8e5000)
#   concept closure-evidence-verification-gate@v1 (evt-cc332ad53f194338951ca89d5f0c32ea) — 下游门, 本模块位于其上游
#   plan plan-reflow-systemic-repair / task T-REFLOW-EVTIER

判断"某改动是否已功能等价落地到 committed main HEAD"时, 对支撑该主张的证据强度做统一分级 ——
被 functional-equivalence-closure-criterion (T-REFLOW-CLOSURE-CODE) 与
stranded-worktree-batch-disposition (T-REFLOW-BATCH-MECH) 两下游消费方共享同一版本 import,
不得各自内联复制标准导致漂移。

两步分级 (classify()):
  (a) per-item 剔除 non_signal 项 (NS1) —— 它们不是证据, 既不计入分级也不当反证。
      NS1 = "fix/工位提交不是 committed main HEAD 的祖先(merge-base 祖先关系为假)": 回流是
      apply-diff 再提交, 落进 main 的是内容相同但哈希全新的提交, 原工位提交必然不成为祖先 ——
      这是机制的必然产物, 非信号。
  (b) 剩余(admissible)证据满足 ≥1 条 hard 判据(H1/H2/H3, 析取) → hard; 否则 → not_hard
      (N1-N4, 均"有信号但不达 hard", 必须派 agent 实查)。
      剔除 non_signal 后 admissible 证据为空 → 不属任一 tier, 是"未落地"(classify() 返回
      None), 由消费方按未落地处置, 不派 agent。

本模块纯分级逻辑: 不 emit 事件、不做 gate 判定、不触发 agent 实查 dispatch —— 那些是消费方
(closure criterion / batch disposition)按分级结果各自决定的下一步。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum, unique

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid")


@unique
class EvidenceReasonCode(StrEnum):
    """单条证据的具体判据码 (H1-H3 hard / N1-N4 not_hard / NS1 non_signal, 析取关系)."""

    # ── hard: 满足任一即可直接判等价, 免 agent 实查 ──
    H1 = "h1_regression_test_green_on_committed_main"  # 回归测试在 committed main HEAD 真跑绿 + 引测试名/run log
    H2 = "h2_equivalent_commit_with_independent_verdict"  # main 有语义等价提交 + 独立 verdict 确认覆盖原 done_criteria
    H3 = "h3_ledger_reflow_event"  # canonical 账本存在对应回流事件(reflow/backflow)

    # ── not_hard: 有信号但不达 hard, 必须派 agent 实查 ──
    N1 = "n1_patch_identity_only"  # 仅 patch 文本相似/patch-identity 匹配
    N2 = "n2_partial_hunk_coverage"  # 只有部分 hunk 出现在 main/merge-base, 覆盖不完整
    N3 = "n3_uncertain"  # patch-identity 或"是否已落地"本身拿不准
    N4 = "n4_measurement_disagreement"  # 同一度量面两次度量结果不一致(度量分歧本身即证据不足信号)

    # ── non_signal: per-item 剔除器, 出现即从证据集删除 ──
    NS1 = "ns1_fix_commit_not_ancestor_of_committed_main_head"  # merge-base 祖先关系为假(回流机制必然产物, 非信号)


@unique
class EvidenceTier(StrEnum):
    """证据分级 (concept 定义的 tier 枚举 = {hard, not_hard, non_signal})."""

    HARD = "hard"
    NOT_HARD = "not_hard"
    NON_SIGNAL = "non_signal"


_HARD_CODES = frozenset({EvidenceReasonCode.H1, EvidenceReasonCode.H2, EvidenceReasonCode.H3})
_NON_SIGNAL_CODES = frozenset({EvidenceReasonCode.NS1})
# N1-N4 落 not_hard 靠上面两个显式集合的补集 (default-to-not_hard): 任何不在 _HARD_CODES /
# _NON_SIGNAL_CODES 里的 reason_code 保守判 not_hard, 强制派 agent 实查而非误判 hard 直接放行。


class EvidenceItem(BaseModel):
    """一条支撑"改动已功能等价落地"主张的证据 (reason_code 决定其 tier, detail 供 not_hard 实查时读)."""

    model_config = _STRICT

    reason_code: EvidenceReasonCode
    detail: str = Field(min_length=1)


def reason_tier(code: EvidenceReasonCode) -> EvidenceTier:
    """单条证据 reason_code → tier 的映射 (纯函数)."""
    if code in _HARD_CODES:
        return EvidenceTier.HARD
    if code in _NON_SIGNAL_CODES:
        return EvidenceTier.NON_SIGNAL
    return EvidenceTier.NOT_HARD


def classify(evidence_set: Sequence[EvidenceItem]) -> EvidenceTier | None:
    """对一组证据分级, 返回 EvidenceTier(hard/not_hard) 或 None(未落地 — 无 admissible 证据).

    None 与 EvidenceTier.NON_SIGNAL 是两回事: 全体证据均为 non_signal (或 evidence_set 为空)时,
    admissible 证据集为空 → 返回 None, 由消费方按"未落地"处置, 不派 agent —— non_signal 本身
    从不作为 classify() 的整体返回值 (它只是 reason_tier() 的单条判定, 用于第一步剔除)。
    """
    admissible = [item for item in evidence_set if reason_tier(item.reason_code) is not EvidenceTier.NON_SIGNAL]
    if not admissible:
        return None
    if any(reason_tier(item.reason_code) is EvidenceTier.HARD for item in admissible):
        return EvidenceTier.HARD
    return EvidenceTier.NOT_HARD


__all__ = [
    "EvidenceItem",
    "EvidenceReasonCode",
    "EvidenceTier",
    "classify",
    "reason_tier",
]
