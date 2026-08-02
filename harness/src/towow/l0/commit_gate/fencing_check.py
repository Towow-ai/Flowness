"""B-3 (substrate 4, K5 fencing / Kleppmann《How to do distributed locking》): commit gate 资源侧
fencing token 校验 —— 拒"被抢锁的旧会话复活后迟到写"(时序脑裂)。

## 本质 (Kleppmann 核心批判的落地)
仅 O_EXCL 认领 (B-2) 堵不住"A 被判死→token 给 B→A 复活仍写"。fencing token 给每次认领一个单调
递增世代号; 资源侧 (commit gate) 拒收 token 低于当前最高的写。enforcement 必须在资源侧 (gate),
不在认领侧 (Kleppmann: "storage server takes an active role in checking tokens")。

## 🔴 三个边界 case (advisor 钉死 — 缺 (c) 是灾难)
(a) **no-token (never-claimed) → PASS untouched**: 绝大多数 producer (review-conclude/fix/legacy/
    非 execution/未认领 task) 的 TaskRunCompleted 无 fencing_token。None ≠ stale。**必须在 verify 之前
    就 return pass** —— verify_fencing_token 对无 .fence 返 False, 把 no-token 路进 verify = 拒所有合法写。
(b) **stale (token < 当前最高 .fence) → REJECT**: 旧会话复活的迟到写, 物理拒 (堵时序脑裂)。
(c) **current/valid (token == 当前最高) → PASS**: happy path。若 (c) 误拒 = 每个合法 execution 完工
    被拒 = autopilot 死 (比 governor 回归更惨)。三 case 全过才算门对, 不能靠"拒所有带 token"骗过 (b)。

## 🔴 enforcement 默认 ON (owner 2026-06-30 在场受控点亮)
本门的真 REJECT 默认开 (TOWOW_FENCING_ENFORCE 默认 on)。设计标 B-3 原要求"独立审计 + owner turn-on"
后才开 (不凭自己绿测开 commit-gate enforcement = 运动员当裁判): 三 case 逻辑已落 + code-path-real
集成测试验过 + observe 期零误伤撞门, owner 据此在场授权翻 on (T-RMD-S6-REDLINE / 闭合
debt-s3fencing-flagon-ownergated)。stale token 真拒 (堵时序脑裂)。回滚: 显式 TOWOW_FENCING_ENFORCE=off
(或 0/false) 退回只观测留痕不拒。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from towow.schemas.event_intent import EventIntent

_TASK_RUN_COMPLETED = "TaskRunCompleted"


def fencing_enforce_enabled() -> bool:
    """🔴 fencing enforcement —— 默认 ON (owner 2026-06-30 在场受控点亮)。

    设计标 B-3 原要求独立审计后 owner 才开 (不凭绿测自开 = 运动员当裁判)。三 case 逻辑已落 +
    code-path-real 集成测试验过 + observe 期零误伤撞门, owner 据此在场授权翻 on。默认 ON: env 未设即
    enforce (stale token 真拒)。回滚口: 显式 TOWOW_FENCING_ENFORCE=off (或 0/false) 退回只观测不拒。
    """
    return os.environ.get("TOWOW_FENCING_ENFORCE", "on").strip().lower() in {"1", "true", "on"}


@dataclass(frozen=True)
class FencingCheckResult:
    """gate fencing 检查结果 (镜像 ReviewVerdictResult 形态)。"""

    passed: bool
    failure_reason: str | None = None
    failure_evidence: dict[str, str] = field(default_factory=dict)
    # 观测 (flag-off 时若逻辑判定该拒, 记 would_reject=True 留痕, 但 passed 仍 True 不真拒)。
    would_reject: bool = False
    # 🔴 f-rp-autopilot-fencing-enforce-silently-off-no-detection: 本次 gate 进程运行时 enforce 真实态
    # (fencing_enforce_enabled() 的求值结果, 与 check 同进程单次读 env, 无 TOCTOU)。gate 把它记进
    # CommitAccepted.checks_passed → 每次门运行都把该进程真实 enforce 态写进 canonical 账本 = enforce=off
    # 可观测 (此前 off 静默不可见, 跨进程不一致无从察觉)。默认 True 对齐 fencing_enforce_enabled() 默认 on。
    enforce_enabled: bool = True


def _claim_dir_from_log(log_path: Path) -> Path:
    """从 event_log 的 _log_path (.towow/events.log) 推出认领目录 .towow/orchestrator/exec_claims/。

    与 orchestrator._exec_claim_dir 同一路径约定 (单一真源在 orchestrator, 此处镜像; 二者必须一致,
    不一致 = 门查错 .fence)。
    """
    return log_path.parent / "orchestrator" / "exec_claims"


def check_fencing_token(
    domain_intents: list[EventIntent],
    log_path: Path,
) -> FencingCheckResult:
    """TaskRunCompleted 携带 fencing_token 时, 校验它 ≥ 该 task 当前最高 .fence (资源侧 fencing)。

    三 case (见模块 docstring):
      (a) no-token → PASS (return 在 verify 之前, 绝不把 None 路进 verify)。
      (b) stale (token < 当前最高) → enforce-on 时 REJECT / enforce-off 时 would_reject 留痕不拒。
      (c) current (token == 当前最高) → PASS。

    非 TaskRunCompleted / 无 token → 不适用, PASS。
    """
    from towow.awareness.claim import _fence_path  # 单一真源: 直接读 claim.py 的 .fence

    # enforce 真实态: 本进程单次求值 (与下面 :121 的判定同一次读 env, 无 TOCTOU)。挂到每条返回结果上
    # 供 gate 记进 canonical 账本 → enforce=off 可观测 (f-rp-autopilot-fencing-enforce-silently-off)。
    enforce_on = fencing_enforce_enabled()
    claim_dir = _claim_dir_from_log(log_path)
    for intent in domain_intents:
        if intent.event_type.value != _TASK_RUN_COMPLETED:
            continue
        payload = intent.payload if isinstance(intent.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        token = after.get("fencing_token")
        # ── case (a): no-token (never-claimed) → PASS untouched, 在任何 verify 之前 ──
        if token is None:
            continue
        if not isinstance(token, int):
            continue  # 非法 token 形态 → 不当 fencing 处理 (schema 已 typed; 防御性跳过)
        task_id = after.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        # 读该 task 当前最高 .fence (claim.py 写它; release 后保留 → 跨认领单调)。
        fp = _fence_path(claim_dir, task_id)
        try:
            current = int(fp.read_text())
        except (OSError, ValueError):
            # 无 .fence: 该 task 从没经认领 (legacy / 未走 B-2 派发路径)。token 却带了 → 不拒
            # (无基线可比, 保守 PASS; 不是 stale)。这与 (a) 同精神: 拿不准不拒合法写。
            continue
        # ── case (c): current/valid (token == 当前最高) → PASS (happy path) ──
        if token == current:
            continue
        # ── case (b): stale (token != 当前最高, 即 < 当前; 旧会话复活迟到写) → 该拒 ──
        evidence = {
            "task_id": task_id,
            "carried_token": str(token),
            "current_highest_fence": str(current),
            "reason": (
                "fencing token 落后于当前最高世代 (被抢锁的旧会话复活后迟到写; Kleppmann 时序脑裂) — "
                "资源侧拒过期写"
            ),
        }
        if enforce_on:
            return FencingCheckResult(
                passed=False,
                failure_reason="fencing_token_stale",
                failure_evidence=evidence,
                would_reject=True,
                enforce_enabled=True,
            )
        # 🔴 enforce-off (owner 受控回滚): 不真拒, 只留痕 would_reject (诚实: 链通+逻辑对, 真拒等审计+owner)。
        return FencingCheckResult(
            passed=True, would_reject=True, failure_evidence=evidence, enforce_enabled=False,
        )
    return FencingCheckResult(passed=True, enforce_enabled=enforce_on)


__all__ = ["FencingCheckResult", "check_fencing_token", "fencing_enforce_enabled"]
