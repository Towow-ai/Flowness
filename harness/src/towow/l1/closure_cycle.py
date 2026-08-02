"""M-1.5 §3.5a Review Closure Cycle 状态机 — cycle 显式终止条件 (T-L1-57).

Finding lifecycle 不只是 created → verified → disputed → resolved。每个 finding 的修复闭合走
显式状态机 (§3.5a): FixCompleted → M-1.5 fix-after closure_verification 产 closure_state →
按 closure_state 路由。

```
accept → M-1.6 fix → FixCompleted → ★ closure_verification ★
                                       │
  closed                              ├→ resolved(confirmed_and_fixed) [终态]
  fix_insufficient                    ├→ reopen FindingCreated (新轮 fix) [非终态]
  ripple_incomplete                   ├→ reopen FindingCreated (scope bounded 到 ripple_targets) [非终态]
  new_unrelated_finding_logged        └→ resolved + 新 finding_id 进 backlog [终态, 不阻塞]
```

关键收束条件 (§3.5a):
- **closed = 终态**: 所有 closure_criteria 满足 + ripple_targets 同步 + forbidden_residuals 0。
- **fix_insufficient 不进终态**: reopen → M-1.6 重新 fix (整 finding, scope 不 bounded)。
- **ripple_incomplete 不进终态**: reopen 但 **scope bounded 到 ripple_targets** — 不扩到全文
  re-review (防漫游)。
- **new_unrelated_finding_logged 进终态**: 当前 finding 闭合 (confirmed_and_fixed), 新 finding
  独立 id 进 backlog — 不阻塞当前 closure (除非 red-line / data loss / correctness-critical)。

**这是反复 review 的工程化终止判据** — bounded regression test 模式, 不是 free re-review。

本模块是纯函数状态机 (无副作用) — 供 finding-resolve CLI / fix-after verify-step 路由用:
非终态 closure_state 配 confirmed_and_fixed = 非法转移 (没修好却声称修好) → 拒。
"""

from __future__ import annotations

from dataclasses import dataclass

from towow.schemas.enums import FindingResolution, ReviewClosureState


class ClosureCycleError(ValueError):
    """非法 closure cycle 转移 (如非终态 closure_state 声称 confirmed_and_fixed)。"""


# 终态 closure_state → 对应合法 resolution。非此集合的 closure_state 不进终态 (须 reopen)。
_TERMINAL_STATES: frozenset[ReviewClosureState] = frozenset(
    {
        ReviewClosureState.CLOSED,
        ReviewClosureState.NEW_UNRELATED_FINDING_LOGGED,
    },
)

# reopen (非终态) closure_state — fix 不足, 走新一轮 fix。
_REOPEN_STATES: frozenset[ReviewClosureState] = frozenset(
    {
        ReviewClosureState.FIX_INSUFFICIENT,
        ReviewClosureState.RIPPLE_INCOMPLETE,
    },
)


@dataclass(frozen=True)
class ClosureRouting:
    """closure_state 路由结果 — 终态 resolve 还是 reopen, 及边界规则。"""

    closure_state: ReviewClosureState
    is_terminal: bool
    reopen: bool
    # 终态时的合法 resolution (closed / new_unrelated 都 → confirmed_and_fixed); reopen 时 None。
    resolution: FindingResolution | None
    # new_unrelated_finding_logged: 当前 finding 闭合, 但新 finding 进 backlog (不阻塞)。
    backlog_new_finding: bool
    # ripple_incomplete reopen 时 scope 严格 bounded 到 ripple_targets (防全文 re-review 漫游)。
    reopen_scope_bounded_to_ripple: bool

    def assert_resolution_legal(self, resolution: FindingResolution) -> None:
        """校验 (closure_state, resolution) 转移合法 — 非法抛 ClosureCycleError。

        - reopen 态 (fix_insufficient/ripple_incomplete): 不进终态, 任何 resolution 都非法
          (没修好却想 resolve = §3.5a 漏洞)。
        - 终态 (closed/new_unrelated): resolution 必须 == 路由钦定的 confirmed_and_fixed —
          closed 是 cycle 真闭合, 不能 retracted/escalated 等 (那是别的分支, 不经 closure_verification)。
        """
        if self.reopen:
            raise ClosureCycleError(
                f"closure_state={self.closure_state.value} 是非终态 (须 reopen 新一轮 fix) — "
                f"不能 resolve 为 {resolution.value} (没修好却声称已 resolve, M-1.5 §3.5a)",
            )
        if resolution is not self.resolution:
            raise ClosureCycleError(
                f"closure_state={self.closure_state.value} 终态对应 resolution="
                f"{self.resolution.value if self.resolution else None} — 不能配 {resolution.value} "
                "(closure-verified 闭合只能 confirmed_and_fixed; retracted/escalated 走非 closure 分支)",
            )


def route_closure_state(closure_state: ReviewClosureState) -> ClosureRouting:
    """§3.5a 状态机路由 — closure_state → 终态 resolve / reopen + 边界规则。"""
    if closure_state in _TERMINAL_STATES:
        return ClosureRouting(
            closure_state=closure_state,
            is_terminal=True,
            reopen=False,
            resolution=FindingResolution.CONFIRMED_AND_FIXED,
            backlog_new_finding=(
                closure_state is ReviewClosureState.NEW_UNRELATED_FINDING_LOGGED
            ),
            reopen_scope_bounded_to_ripple=False,
        )
    if closure_state in _REOPEN_STATES:
        return ClosureRouting(
            closure_state=closure_state,
            is_terminal=False,
            reopen=True,
            resolution=None,
            backlog_new_finding=False,
            reopen_scope_bounded_to_ripple=(
                closure_state is ReviewClosureState.RIPPLE_INCOMPLETE
            ),
        )
    # enum 全集已覆盖; 防御性 (新增 closure_state 未路由 → fail-closed)。
    msg = f"未路由的 closure_state: {closure_state!r} (M-1.5 §3.5a 状态机未覆盖)"
    raise ClosureCycleError(msg)


__all__ = [
    "ClosureCycleError",
    "ClosureRouting",
    "route_closure_state",
]
