"""T-FIX-B2-06 (INV-B2-6 / esc-532866a8): review/fix/plan provenance 不被错挂的单飞守卫.

# spec source:
#   01-reconciliation/LANDING-FIX-PLAN.md [T-FIX-B2-06]
#   src/towow/l1/session_lock.py (SessionLockRegistry.assert_single_flight)

把 INV-B2-6 焊成可验证守卫(非新机制, 是对 T-FIX-B2-01 单飞门的 provenance 后果做断言固化):
  (1) per-kind 单飞门下同一时刻 live serial-reject 会话数恒 ≤1
      —— SessionLockRegistry(kind).live_sessions() 长度 ≤1 (serial-reject kind);
  (2) execution(非 serial-reject kind)不受单飞约束, 保持 N 并发隔离能力;
  (3) 守卫只断言不阻止 acquire —— 不引入中心锁/中心调度, 只把不变量做成可调用的运行时断言/测试。

★ R11 (2026-06-23): plan 退役 kind 级单飞 —— review(T-RCL-01)/fix(2026-06-22)/plan 现【全部】退役,
  SERIAL_REJECT_KINDS = frozenset() (空)。机制本体 (assert_single_flight) 保留可 re-populate, 故本
  文件仍用 monkeypatch 把一个合成 serial-reject kind 塞回集合, 保留『机制本身正确 (0/1 live 通过、
  >1 live 抛违例)』的覆盖 (advisor: vestigial 机制别静默丢覆盖)。各真实 kind (review/fix/plan) 的
  退役由各自 test_*_retired_from_serial_reject_allows_concurrency 钉死。

esc-532866a8 4 症状(锁被抢/错挂/重复审/饿等)对照:
  本守卫覆盖『锁被抢→并发 serial-reject 会话同时 live』这条根: 守卫断言 live ≤1, 一旦 >1 立即抛
  SingleFlightViolationError(可在运行时/CI 抓)。错挂/重复审/饿等由本批 B2-01/B2-03/B2-05 覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import towow.l1.session_lock as session_lock
from towow.l1.session_lock import (
    SERIAL_REJECT_KINDS,
    SessionLockRegistry,
    SingleFlightViolationError,
)

# 合成 serial-reject kind: SERIAL_REJECT_KINDS 现为空 (review/fix/plan 全退役), 但机制保留可
# re-populate。下面的 fixture monkeypatch 把它塞回集合, 单测『机制本身』对 serial-reject kind 的
# 0/1/>1 live 行为 (不代表 plan 仍 serial-reject —— plan 退役由 test_plan_retired_... 钉死)。
_SYNTHETIC_SERIAL_KIND = "plan"


@pytest.fixture()
def towow(tmp_path: Path) -> Path:
    d = tmp_path / ".towow"
    (d / "locks").mkdir(parents=True)
    return d


@pytest.fixture()
def serial_kind(monkeypatch: pytest.MonkeyPatch) -> str:
    """把一个合成 serial-reject kind 塞回 SERIAL_REJECT_KINDS, 保留机制覆盖 (集合现真为空)。"""
    monkeypatch.setattr(
        session_lock, "SERIAL_REJECT_KINDS", frozenset({_SYNTHETIC_SERIAL_KIND})
    )
    return _SYNTHETIC_SERIAL_KIND


# ── (1) serial-reject kind 单飞不变量: live ≤1 时守卫通过 ──────────────────────


def test_assert_single_flight_passes_with_zero_live(towow: Path, serial_kind: str) -> None:
    """无 live 会话 → 守卫静默通过 (0 ≤ 1)。"""
    reg = SessionLockRegistry(towow, serial_kind)
    reg.assert_single_flight()  # 不抛


def test_assert_single_flight_passes_with_one_live(towow: Path, serial_kind: str) -> None:
    """恰好一个 live serial-reject 会话 → 守卫通过 (单飞门正常态)。"""
    reg = SessionLockRegistry(towow, serial_kind)
    reg.acquire(f"sess-{serial_kind}-A", actor_id="a", skill_id="s", pid=1)
    assert len(reg.live_sessions()) == 1
    reg.assert_single_flight()  # 不抛


# ── (1) 违例: 单飞门被绕过(两 live)→ 守卫必须抛, 把 provenance 错挂窗口做成可检测违例 ──


def test_assert_single_flight_raises_when_two_live(towow: Path, serial_kind: str) -> None:
    """两个 live serial-reject 会话同时存在(单飞门被绕过)→ 守卫抛 SingleFlightViolationError。

    这正是 esc-532866a8『锁被抢』根症状: 两 serial-reject 会话同时活 → 共享聚合体写竞争。
    守卫把这个窗口从"静默腐蚀"变成"立即可检测的违例"。

    R11 (2026-06-23): SERIAL_REJECT_KINDS 现真为空 (review/fix/plan 全退役), 故本测试经 fixture
    monkeypatch 一个合成 serial-reject kind 来验【机制本身】仍正确; 各真实 kind 的退役 (2 live 不抛)
    由各自 test_*_retired_from_serial_reject_allows_concurrency 钉死。
    """
    reg = SessionLockRegistry(towow, serial_kind)
    reg.acquire(f"sess-{serial_kind}-A", actor_id="a", skill_id="s", pid=1)
    reg.acquire(f"sess-{serial_kind}-B", actor_id="b", skill_id="s", pid=1)
    assert len(reg.live_sessions()) == 2  # 单飞门被绕过 → registry 物理允许两文件
    with pytest.raises(SingleFlightViolationError) as ei:
        reg.assert_single_flight()
    # 违例信息要含 kind + 两个 session_id (可观测, 供运行时定位是谁抢了谁)
    msg = str(ei.value)
    assert serial_kind in msg
    assert f"sess-{serial_kind}-A" in msg and f"sess-{serial_kind}-B" in msg


# ── (2) execution 不是 serial-reject kind: N 并发不受单飞约束 ──────────────────


def test_execution_kind_not_serial_reject_allows_n_concurrent(towow: Path) -> None:
    """execution 不在 SERIAL_REJECT_KINDS: 守卫不应把 execution 的 N 并发隔离误判违例。

    招牌纪律: 单飞门 (历史上) 只约束 start 物理单例 skill, 绝不收紧 execution 的并行隔离能力
    (那是 B1 线的承重墙)。review/fix/plan 现均已退出单飞门 (完全并发)。
    """
    assert "execution" not in SERIAL_REJECT_KINDS
    reg = SessionLockRegistry(towow, "execution")
    reg.acquire("sess-exec-A", actor_id="a", skill_id="s", pid=1)
    reg.acquire("sess-exec-B", actor_id="b", skill_id="s", pid=1)
    assert len(reg.live_sessions()) == 2
    # execution kind 调 assert_single_flight 不抛 —— 非 serial-reject kind 无单飞约束。
    reg.assert_single_flight()  # 不抛 (no-op for non-serial-reject kind)


def test_serial_reject_kinds_exact_set() -> None:
    """SERIAL_REJECT_KINDS 与 orchestrator 单飞门映射(_SERIAL_REJECT_KIND_BY_DISPATCH 值)对齐。

    退役谱系: consensus (R11 2026-06-14, 跨 brief 并发同 brief 单飞) → review (T-RCL-01 2026-06-17,
    review-target 级完全并发) → fix (2026-06-22, finding 内容寻址) → plan (R11 2026-06-23, 同 plan_id
    单飞、不同 plan_id 并行, 由 plan_start per-plan_id 自 reject 守)。四者全退役 → 集合空。
    机制本体 (registry/assert_single_flight/orchestrator._check_single_flight_invariants) 保留可
    re-populate; 两侧 (此处 + orchestrator._SERIAL_REJECT_KIND_BY_DISPATCH) 须始终对齐, 现均空。
    """
    assert SERIAL_REJECT_KINDS == frozenset()


# ── (3) 守卫只断言不阻止 acquire: 不引入中心锁 ────────────────────────────────


def test_guard_does_not_block_acquire_no_central_lock(towow: Path, serial_kind: str) -> None:
    """守卫是只读断言 —— acquire 本身不被守卫拦(不引入中心锁/中心调度)。

    单飞纪律落在 orchestrator 派发层(B2-01: 派发前查 live 不盲 spawn), registry 层只提供
    可验证的不变量断言。acquire 仍按原 per-session 隔离语义工作, 守卫不改 acquire 行为。
    (用合成 serial-reject kind 演示守卫不阻 acquire。)
    """
    reg = SessionLockRegistry(towow, serial_kind)
    # acquire 两次(模拟派发纪律被绕过)不抛 —— registry 不强制单例(那会破坏 execution N 并发的同一底座)。
    reg.acquire(f"sess-{serial_kind}-A", actor_id="a", skill_id="s", pid=1)
    reg.acquire(f"sess-{serial_kind}-B", actor_id="b", skill_id="s", pid=1)
    # 守卫独立检测违例, 但 acquire 路径不被它阻断。
    assert len(reg.live_sessions()) == 2


# ── (4) T-RCL-01: review 退役 kind 级单飞 → 守卫对 review 不再约束 (review-target 完全并发) ──


def test_review_retired_from_serial_reject_allows_concurrency(towow: Path) -> None:
    """T-RCL-01 (INV-MULTI d, 防 scope creep): review 已移出 SERIAL_REJECT_KINDS —— 两个 live
    review 会话同时存在 (review-target 级并发) 时, assert_single_flight 是 no-op, **绝不**抛违例。

    静态断言:
      (a) review 不在 SERIAL_REJECT_KINDS (无基于 session_id 的并发排斥键残留);
      (b) 两 live review → assert_single_flight 不抛 (与 execution 同语义: 非 serial-reject kind 不受约束)。
    provenance 由既有 review-unit (session_id) 血缘守 (review_verdict 折叠), 不靠并发排斥。
    """
    assert "review" not in SERIAL_REJECT_KINDS  # (a) 静态: review 无并发排斥键
    reg = SessionLockRegistry(towow, "review")
    reg.acquire("sess-review-A", actor_id="a", skill_id="M-1.5", pid=1)
    reg.acquire("sess-review-B", actor_id="b", skill_id="M-1.5", pid=1)
    assert len(reg.live_sessions()) == 2  # 同 review-target 第二视角 / 不同 target 并行都允许共存
    # (b) review 非 serial-reject → 守卫 no-op, 绝不抛 (review-target 完全并行, 不被误判违例)
    reg.assert_single_flight()  # 不抛


# ── (5) fix 退役 kind 级单飞 → 守卫对 fix 不再约束 (finding-fix-serial-lock-redundant... 2026-06-22) ──


def test_fix_retired_from_serial_reject_allows_concurrency(towow: Path) -> None:
    """fix 退役 (2026-06-22, 同 T-RCL-01 套路): fix 已移出 SERIAL_REJECT_KINDS —— 两个 live fix
    会话同时存在时 assert_single_flight 是 no-op, **绝不**抛违例。

    安全前提 (= review 同款): fix 的 finding 归属按 finding_id/fix_id 内容寻址折叠 (非持锁会话) +
    provenance 显式 session_id 盖戳 + 隔离工位 T-NB-3, 并发归属不串 (见 esc-532866a8 closure 的
    test_symptom1b_fix_provenance_safe_under_concurrency)。provenance 锚定 > 并发排斥。

    静态断言:
      (a) fix 不在 SERIAL_REJECT_KINDS;
      (b) 两 live fix → assert_single_flight 不抛 (非 serial-reject kind 不受约束)。
    """
    assert "fix" not in SERIAL_REJECT_KINDS  # (a) 静态: fix 无并发排斥键
    reg = SessionLockRegistry(towow, "fix")
    reg.acquire("sess-fix-A", actor_id="a", skill_id="M-1.6", pid=1)
    reg.acquire("sess-fix-B", actor_id="b", skill_id="M-1.6", pid=1)
    assert len(reg.live_sessions()) == 2  # 两 fix 会话并发共存被允许
    # (b) fix 非 serial-reject → 守卫 no-op, 绝不抛
    reg.assert_single_flight()  # 不抛


# ── (6) R11: plan 退役 kind 级单飞 → 守卫对 plan 不再约束 (同 plan_id 单飞改 plan_start per-plan_id 自 reject) ──


def test_plan_retired_from_serial_reject_allows_concurrency(towow: Path) -> None:
    """R11 (2026-06-23, owner 2026-06-22 拍, 同 owner 2026-06-14 为 consensus 拍的模型): plan 已移出
    SERIAL_REJECT_KINDS —— 两个 live plan 会话 (不同 plan_id) 同时存在时 assert_single_flight 是
    no-op, **绝不**抛违例。

    安全前提: 同 plan_id 防 cohort 污染由 plan_start 的 per-plan_id 自 reject 守 (镜像 consensus_start,
    见 cli/main.py plan_start), 不同 plan_id 放行; 派发侧去重由 is_already_dispatched 复合键
    <evt>__planning + T-FND-01 plan-product-exists 兜 (与 consensus 对称)。kind 级单飞退役不丢去重。

    静态断言:
      (a) plan 不在 SERIAL_REJECT_KINDS;
      (b) 两 live plan → assert_single_flight 不抛 (非 serial-reject kind 不受约束)。
    """
    assert "plan" not in SERIAL_REJECT_KINDS  # (a) 静态: plan 无并发排斥键
    reg = SessionLockRegistry(towow, "plan")
    reg.acquire("sess-plan-A", actor_id="a", skill_id="M-1.3", pid=1)
    reg.acquire("sess-plan-B", actor_id="b", skill_id="M-1.3", pid=1)
    assert len(reg.live_sessions()) == 2  # 不同 plan_id 的两 plan 会话并发共存被允许
    # (b) plan 非 serial-reject → 守卫 no-op, 绝不抛
    reg.assert_single_flight()  # 不抛
