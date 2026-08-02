"""M-1.6 §7.2 / M-3.4 §5 场景 2.1 — 多轮 fix novelty gate (T-L1-77).

# spec source:
#   04-l1-intelligence/M-1.6-fix-skill-detailed-design.md §7.2
#     "如果连续 N 轮无 novelty → M-1.6 主动产 EscalationRaised"
#     "Novelty 强制 (M-0.5 NoveltyCheck cross-cutting): 每轮 fix supersede 之前轮 FixCompleted
#      时必须带 novelty"
#   06-l3-engineering-shell/M-3.4-validation-matrix-detailed-design.md §5 场景 2.1
#     "4+ rounds 自动 EscalationCandidate" / "NoveltyCheck 拦'换说法改同一处'的振荡"

修复死循环防护: 一个 finding 反复 reopen → fix → fix_insufficient → reopen 形成死循环。
若 fix 每轮都换说法改同一处 (无真 novelty), 系统该主动 EscalationRaised 给 Nature 决策,
而不是无限重试。这把 gate 从 FixCompleted 历史 (fix_attempt_no + fix_attempt_novelty 字段,
T-L1-68 落地) 抽轮次, 判断连续无 novelty 轮数是否达阈值。

依赖 (波2 done): T-L1-70 EscalationRaised event + fix escalate CLI; T-L1-68 fix_attempt_no/novelty
payload 字段。本模块是纯判定逻辑 (无副作用) —— fix skill / orchestrator 拿 verdict.should_escalate
决定走 `towow fix escalate` (产 EscalationRaised technical_blocker) 而非开下一轮。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# M-3.4 §5 场景 2.1 反退路: "4+ rounds 自动 EscalationCandidate" —— 连续 N 轮无 novelty 触发。
DEFAULT_NOVELTY_GATE_THRESHOLD = 4


@dataclass(frozen=True)
class FixRound:
    """一轮 fix 的 novelty 相关字段 (从 FixCompleted.after_state 抽)。

    fix_attempt_novelty 容三种形态 (向后兼容 T-L1-68 schema FixAttemptNovelty | str | None):
      - None / 空 → 本轮未声明 novelty (无进展信号);
      - dict (结构化 {semantic_delta, new_evidence?, changed_strategy?}) → 结构化 novelty;
      - 非空 str (旧自由字符串) → 自由文本 novelty。
    """

    fix_attempt_no: int
    fix_attempt_novelty: Any = None


@dataclass(frozen=True)
class NoveltyGateVerdict:
    """novelty gate 判定结果 — 结构化, 非裸 bool (带 consecutive 计数 + reason 便于审计/escalation)."""

    should_escalate: bool
    consecutive_no_novelty: int
    threshold: int
    reason: str


def _novelty_signature(novelty: Any) -> str | None:
    """返回本轮 novelty 的归一化签名; None 表示"无 novelty"(空/缺)。

    "无 novelty" 判据:
      - None → 无;
      - str: strip 后为空 → 无, 否则签名 = 归一化文本;
      - dict: semantic_delta strip 后为空/缺 → 无 (semantic_delta 是 §3.2 必填核心字段),
        否则签名 = 归一化的 semantic_delta (+ changed_strategy 若有) —— 换说法改同一处时
        semantic_delta 重复 → 同签名 → 被判振荡 (M-3.4 §5 "NoveltyCheck 拦换说法改同一处")。
    """
    if novelty is None:
        return None
    if isinstance(novelty, str):
        norm = novelty.strip().lower()
        return norm or None
    if isinstance(novelty, dict):
        delta = str(novelty.get("semantic_delta", "") or "").strip().lower()
        if not delta:
            return None
        strategy = str(novelty.get("changed_strategy", "") or "").strip().lower()
        return f"{delta}||{strategy}" if strategy else delta
    # 未知形态: 保守当"有 novelty"(不误触发死循环防护), 但用 repr 作签名以便重复检测。
    return repr(novelty).strip().lower() or None


def evaluate_novelty_gate(
    rounds: list[FixRound],
    *,
    threshold: int = DEFAULT_NOVELTY_GATE_THRESHOLD,
) -> NoveltyGateVerdict:
    """评 multi-round fix novelty gate (M-1.6 §7.2)。

    遍历按 fix_attempt_no 升序的轮次, 累计"连续无 novelty"计数:
      - 本轮无 novelty (签名 None) OR 签名与本连续段内某前轮重复 (振荡) → 计数 +1;
      - 本轮有**新** novelty → 计数重置为 0 (有真进展)。
    连续无 novelty 计数达 threshold → should_escalate=True。

    raise ValueError on empty rounds (调用方应保证 finding 至少有 1 轮 FixCompleted)。
    """
    if not rounds:
        raise ValueError("evaluate_novelty_gate: rounds is empty (need ≥1 FixCompleted round)")
    if threshold < 1:
        raise ValueError(f"threshold must be ≥ 1, got {threshold}")

    ordered = sorted(rounds, key=lambda r: r.fix_attempt_no)
    consecutive = 0
    max_consecutive = 0
    # 跨整个 lineage 记已见签名 (不在重置时清空) —— 一个 delta 首现算 novelty (区分真换策略),
    # 再次出现 = 振荡 (换说法改同一处, M-3.4 §5 "NoveltyCheck 拦换说法改同一处")。
    seen_signatures: set[str] = set()
    for rnd in ordered:
        sig = _novelty_signature(rnd.fix_attempt_novelty)
        # 无 novelty: 签名缺 (None/空) 或签名此前已出现过 (重复 = 振荡)。
        is_no_novelty = sig is None or sig in seen_signatures
        if is_no_novelty:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            # 真新 novelty (首现签名) → 重置连续无-novelty 段, 但保留 seen 以便后续重复检测。
            consecutive = 0
        if sig is not None:
            seen_signatures.add(sig)
    should = max_consecutive >= threshold
    if should:
        reason = (
            f"连续 {max_consecutive} 轮 fix 无 novelty (达阈值 {threshold}) — "
            "novelty 耗尽 / 振荡 (换说法改同一处), 应产 EscalationRaised(technical_blocker) "
            "给 Nature 决策, 不再开下一轮 (M-1.6 §7.2 / M-3.4 §5 场景 2.1)"
        )
    else:
        reason = (
            f"最长连续无 novelty {max_consecutive} 轮 < 阈值 {threshold} — 继续多轮 fix, 不触发 escalation"
        )
    return NoveltyGateVerdict(
        should_escalate=should,
        consecutive_no_novelty=max_consecutive,
        threshold=threshold,
        reason=reason,
    )


def extract_fix_rounds(events: list[dict[str, Any]]) -> list[FixRound]:
    """从 event 序列抽 FixCompleted 轮次 (fix_attempt_no + fix_attempt_novelty), 按 attempt_no 升序。

    调用方通常先按 finding/fix lineage 过滤好同一 finding 的 FixCompleted, 再传进来。
    非 FixCompleted event 忽略。缺 fix_attempt_no 的退化 event 默认 attempt_no=1。
    """
    rounds: list[FixRound] = []
    for ev in events:
        if ev.get("event_type") != "FixCompleted":
            continue
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else payload
        attempt_no = after.get("fix_attempt_no", 1)
        try:
            attempt_no = int(attempt_no)
        except (TypeError, ValueError):
            attempt_no = 1
        rounds.append(
            FixRound(
                fix_attempt_no=attempt_no,
                fix_attempt_novelty=after.get("fix_attempt_novelty"),
            ),
        )
    return sorted(rounds, key=lambda r: r.fix_attempt_no)


__all__ = [
    "DEFAULT_NOVELTY_GATE_THRESHOLD",
    "FixRound",
    "NoveltyGateVerdict",
    "evaluate_novelty_gate",
    "extract_fix_rounds",
]
