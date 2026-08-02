"""RUN-080 漏洞1 — SessionLockRegistry: 并行会话隔离 + 崩溃锁自愈 (AC1 决定性).

# spec source:
#   harness/docs/SPEC-PATCH-CONCURRENCY-RUN080.md §1
#   src/towow/l1/session_lock.py

覆盖 AC1 两个决定性能力:
  - 并行隔离: N 个会话各持自己锁文件, resolve(explicit) 各自返回自己, 绝不认到别会话(血缘不串);
  - 崩溃自愈: 进程死(pid 不存活) / 锁超时 → reap_stale 自动回收, 不再 stale 卡死(无需手动 rm)。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from towow.cli.main import (
    _ORPHAN_META_GRACE_S,
    _SESSION_KINDS,
    _read_session_meta,
    _session_meta_path,
    _sweep_orphan_meta,
    _write_session_meta,
    acquire_session,
    resolve_session,
)
from towow.l1.session_lock import (
    AmbiguousActiveSessionError,
    DuplicateSessionError,
    SessionLockRegistry,
    UnknownSessionError,
)


@pytest.fixture()
def towow(tmp_path: Path) -> Path:
    d = tmp_path / ".towow"
    (d / "locks").mkdir(parents=True)
    return d


# ── 并行隔离 (血缘不串) ───────────────────────────────────────────────────────


def test_two_parallel_sessions_each_own_lock_file_no_cross(towow: Path) -> None:
    """两并行会话各持独立锁文件; resolve(A)→A, resolve(B)→B, 绝不串。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="actor-a", skill_id="M-1.1", pid=1)
    reg.acquire("sess-B", actor_id="actor-b", skill_id="M-1.1", pid=1)

    # 物理隔离: 两个独立文件
    a_file = towow / "locks" / "sessions" / "interview" / "sess-A.json"
    b_file = towow / "locks" / "sessions" / "interview" / "sess-B.json"
    assert a_file.exists() and b_file.exists()

    # resolve 各自只认自己 — A 的产出绝不被认成 B
    info_a = reg.resolve("sess-A")
    info_b = reg.resolve("sess-B")
    assert info_a is not None and info_a.session_id == "sess-A" and info_a.actor_id == "actor-a"
    assert info_b is not None and info_b.session_id == "sess-B" and info_b.actor_id == "actor-b"


def test_acquire_B_does_not_touch_A_lock(towow: Path) -> None:
    """acquire B 绝不覆盖 A 的锁文件内容 (单全局锁覆盖 bug 的根因消除)。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="actor-a", skill_id="M-1.1", pid=1)
    a_before = json.loads((towow / "locks/sessions/interview/sess-A.json").read_text())
    reg.acquire("sess-B", actor_id="actor-b", skill_id="M-1.1", pid=1)
    a_after = json.loads((towow / "locks/sessions/interview/sess-A.json").read_text())
    assert a_before == a_after
    assert a_after["session_id"] == "sess-A" and a_after["actor_id"] == "actor-a"


def test_resolve_ambiguous_when_multiple_live_no_explicit(towow: Path) -> None:
    """多并行会话同时 live 且未给 --session-id → 拒绝隐式默认(防认错), raise Ambiguous。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)
    reg.acquire("sess-B", actor_id="b", skill_id="M-1.1", pid=1)
    with pytest.raises(AmbiguousActiveSessionError):
        reg.resolve(None)


def test_resolve_single_live_backward_compat(towow: Path) -> None:
    """只有一个 live 会话且未给 explicit → 返回它(单串行向后兼容)。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-solo", actor_id="a", skill_id="M-1.1", pid=1)
    info = reg.resolve(None)
    assert info is not None and info.session_id == "sess-solo"


def test_resolve_unknown_explicit_raises(towow: Path) -> None:
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)
    with pytest.raises(UnknownSessionError):
        reg.resolve("sess-does-not-exist")


def test_acquire_duplicate_live_session_raises(towow: Path) -> None:
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)
    with pytest.raises(DuplicateSessionError):
        reg.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)


def test_different_kinds_isolated(towow: Path) -> None:
    """interview 与 consensus 命名空间隔离 — 同 .towow 下不同 phase 并行不互串。"""
    iv = SessionLockRegistry(towow, "interview")
    cs = SessionLockRegistry(towow, "consensus")
    iv.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)
    cs.acquire("sess-A", actor_id="a", skill_id="M-1.2", pid=1)  # 同 id 不同 kind → 共存
    assert iv.resolve("sess-A").skill_id == "M-1.1"
    assert cs.resolve("sess-A").skill_id == "M-1.2"


# ── 崩溃自愈 (复用 commit 锁协议: 超时即死 + 启动自动扫 + 自动释放) ──────────────


def test_dead_pid_lock_is_reaped(towow: Path) -> None:
    """会话崩溃/弃用(长时间无活动) → 空闲超时 reap_stale 自动回收, 不再 stale 卡死。

    去 pid 探活后(RUN-080 回归/Voyager finding-01: 单发 CLI 下 start 进程必先死, pid 探活把
    正常退出误判成崩溃、误杀真会话), 崩溃自愈判据 = 锁空闲(最后活动距今)超过 stale_after_s。
    """
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-crashed", actor_id="a", skill_id="M-1.1", pid=1, started_at_unix=1000)
    reaped = reg.reap_stale(now=1000 + 100_000, stale_after_s=3600)
    assert "sess-crashed" in reaped
    assert not (towow / "locks/sessions/interview/sess-crashed.json").exists()


def test_timed_out_lock_is_reaped(towow: Path) -> None:
    """锁超过 stale_after_s 年龄 → 超时回收(即便 pid 还活)。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-old", actor_id="a", skill_id="M-1.1", pid=1, started_at_unix=1000)
    # now 远晚于 started_at; pid 探针恒活 → 仅靠 age-timeout 触发回收
    reaped = reg.reap_stale(now=1000 + 10_000, stale_after_s=3600, pid_alive=lambda _p: True)
    assert "sess-old" in reaped


def test_live_session_not_reaped(towow: Path) -> None:
    """pid 活 + 未超时 → 不回收(不误杀真会话)。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-live", actor_id="a", skill_id="M-1.1", pid=1, started_at_unix=1000)
    reaped = reg.reap_stale(now=1000 + 60, stale_after_s=3600, pid_alive=lambda _p: True)
    assert reaped == []
    assert (towow / "locks/sessions/interview/sess-live.json").exists()


def test_crash_self_heal_unblocks_next_acquire(towow: Path) -> None:
    """决定性: 崩溃的会话锁不再永久阻塞 — acquire 时启动自动扫回收死锁, 新会话照常起。

    这是'社媒 stale 锁没自愈'反例的根治: 旧单锁崩溃后要手动 rm 才能再 start; 现在
    下一个 acquire 自带 reap, 死会话自动让位。
    """
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-dead", actor_id="a", skill_id="M-1.1", pid=1, started_at_unix=1000)
    NOW = 1000 + 100_000  # 远晚于 sess-dead 最后活动 → 视为崩溃/弃用
    info = reg.acquire(
        "sess-new", actor_id="b", skill_id="M-1.1", pid=1,
        started_at_unix=NOW, now=NOW, stale_after_s=3600,
    )
    assert info.session_id == "sess-new"
    assert not (towow / "locks/sessions/interview/sess-dead.json").exists()
    # 自愈后 sess-new 是唯一 live → resolve(None) 返回它(不 Ambiguous)
    assert reg.resolve(None, now=NOW, stale_after_s=3600).session_id == "sess-new"


def test_reaped_session_resolve_raises_unknown(towow: Path) -> None:
    """崩溃回收后再 resolve 那个 id → UnknownSessionError(不会拿到 stale 数据认错)。"""
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-x", actor_id="a", skill_id="M-1.1", pid=1, started_at_unix=1000)
    with pytest.raises(UnknownSessionError):
        reg.resolve("sess-x", now=1000 + 100_000, stale_after_s=3600)


def test_malformed_lock_reaped(towow: Path) -> None:
    """坏锁文件(读不出 session_id) → 也回收, 不让坏状态卡死 namespace。"""
    reg = SessionLockRegistry(towow, "interview")
    d = towow / "locks/sessions/interview"
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("{not json", encoding="utf-8")
    reaped = reg.reap_stale()
    assert "garbage" in reaped
    assert not (d / "garbage.json").exists()


def test_release_removes_lock(towow: Path) -> None:
    reg = SessionLockRegistry(towow, "interview")
    reg.acquire("sess-A", actor_id="a", skill_id="M-1.1", pid=1)
    assert reg.release("sess-A") is True
    assert reg.release("sess-A") is False
    # 已 release → 不在 live 列表; resolve 那个 id 应 UnknownSessionError(不拿到 stale 数据认错)
    assert reg.live_sessions() == []
    with pytest.raises(UnknownSessionError):
        reg.resolve("sess-A")


# ════════════════════════════════════════════════════════════════════════════════
# T-SL-A1 (plan-session-lock-registry-unification 阶段A基建) — CLI 统一 registry 双
# helper + meta 旁文件。设计: 01-reconciliation/SESSION-LOCK-FIX-DESIGN.md §1 +
# 第二轮复核 R1-R8。覆盖 done_criteria a1-tests:
#   崩溃序列不变量 / 600s 宽限孤儿清扫 / .meta 不被 reap 吞 / 12hex sid /
#   acquire 撞活跃 sid 友好拒(exit 1) / resolve 三态 / R3 全 kind reap。
# ════════════════════════════════════════════════════════════════════════════════

def _meta_file(towow: Path, kind: str, sid: str) -> Path:
    return towow / "locks" / "sessions" / kind / f"{sid}.meta"


# ── meta 命名铁律 (INV-SL-01: `.meta` 绝非 `.json`) + .meta 不被 reap 吞 ──────────


def test_session_kinds_is_six_phases() -> None:
    """_SESSION_KINDS 钉死 6 phase 全集 — guard/清扫/reap 的遍历域。"""
    assert _SESSION_KINDS == ("interview", "consensus", "plan", "execution", "review", "fix")


def test_meta_path_ends_with_meta_not_json(towow: Path) -> None:
    """命名铁律: meta 旁文件必须 `.meta` 结尾, 绝不可 `.json`(否则被 registry reap 当 malformed 锁吃掉)。"""
    p = _session_meta_path(towow, "execution", "sess-work-abc123def456")
    assert p.name.endswith(".meta")
    assert not p.name.endswith(".json")
    assert p.parent == towow / "locks" / "sessions" / "execution"


def test_meta_file_not_eaten_by_registry_reap(towow: Path) -> None:
    """.meta 文件跑 reap_stale 不被吞 — registry 的 glob `*.json` 天然不匹配 `.meta`。

    这是定稿 §0"新发现"的硬约束回归: 若 meta 命名 .json 结尾, 任何一次 reap 会把活会话
    的 meta 读成 malformed 锁当场删掉。
    """
    reg = SessionLockRegistry(towow, "execution")
    reg.acquire("sess-work-aaaabbbbcccc", actor_id="a", skill_id="M-1.4", pid=1)
    _write_session_meta(towow, "execution", "sess-work-aaaabbbbcccc", {"task_id": "T-X"})
    # 孤儿 meta(无锁本体)也一样不被 reap 碰 — reap 只管 *.json
    _write_session_meta(towow, "execution", "sess-work-orphan000000", {"task_id": "T-Y"})

    reaped = reg.reap_stale()

    assert reaped == []
    assert _meta_file(towow, "execution", "sess-work-aaaabbbbcccc").exists()
    assert _meta_file(towow, "execution", "sess-work-orphan000000").exists()
    assert _read_session_meta(towow, "execution", "sess-work-aaaabbbbcccc") == {"task_id": "T-X"}


# ── M1 崩溃序列不变量: 先写 meta 再 acquire (锁存在 ⟹ meta 必存在) ────────────────


def test_acquire_session_lock_implies_meta(towow: Path) -> None:
    """acquire_session 成功后锁与 meta 同在 — M1 不变量的正向面。"""
    info = acquire_session(
        towow, "consensus", None, actor_id="m12", skill_id="M-1.2", meta={"brief_event_id": "evt-b"},
    )
    lock_file = towow / "locks" / "sessions" / "consensus" / f"{info.session_id}.json"
    assert lock_file.exists()
    assert _read_session_meta(towow, "consensus", info.session_id) == {"brief_event_id": "evt-b"}


def test_acquire_session_crash_sequence_meta_written_before_lock(towow: Path, monkeypatch) -> None:
    """崩溃序列不变量: acquire 在锁写入前崩溃 → meta 已落盘(证明顺序=先 meta 后锁),
    锁不存在 → 不违反"锁存在 ⟹ meta 存在"; 孤儿 meta 在 600s 宽限内不被清扫误删。
    """
    def _boom(self: SessionLockRegistry, session_id: str, **kwargs: object) -> None:
        raise RuntimeError("simulated crash between meta write and lock acquire")

    monkeypatch.setattr(SessionLockRegistry, "acquire", _boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        acquire_session(
            towow, "plan", "sess-plan-crash0000dead",
            actor_id="m13", skill_id="M-1.3", meta={"plan_id": "p-1"},
        )

    meta = _meta_file(towow, "plan", "sess-plan-crash0000dead")
    lock = towow / "locks" / "sessions" / "plan" / "sess-plan-crash0000dead.json"
    assert meta.exists(), "meta 必须先于锁落盘 — 锁存在 ⟹ meta 存在的顺序保证"
    assert not lock.exists()
    # 崩溃留下的在途孤儿 meta: mtime 是刚才 → 宽限期内, 清扫不误删 (R2)
    _sweep_orphan_meta(towow, "plan")
    assert meta.exists()


# ── R2 孤儿 meta 清扫: 600s 宽限 (在途不误删 / 真孤儿清掉 / 有锁本体不碰) ──────────


def test_sweep_keeps_inflight_orphan_meta_within_grace(towow: Path) -> None:
    """并发在途 meta (无锁本体, mtime<600s) 不被误删 — 兄弟会话"写 meta→acquire"窗口保护。"""
    _write_session_meta(towow, "execution", "sess-work-inflight0000", {"task_id": "T-Z"})
    _sweep_orphan_meta(towow, "execution")
    assert _meta_file(towow, "execution", "sess-work-inflight0000").exists()


def test_sweep_removes_true_orphan_meta_past_grace(towow: Path) -> None:
    """真孤儿 meta (无锁本体, mtime>600s) 被清扫 — 锁已 reap/会话崩溃的残留。"""
    _write_session_meta(towow, "execution", "sess-work-orphan111111", {"task_id": "T-old"})
    p = _meta_file(towow, "execution", "sess-work-orphan111111")
    old = p.stat().st_mtime - (_ORPHAN_META_GRACE_S + 100)
    os.utime(p, (old, old))
    _sweep_orphan_meta(towow, "execution")
    assert not p.exists()


def test_sweep_never_touches_meta_with_live_lock_even_if_old(towow: Path) -> None:
    """锁本体在 → meta 是活会话的, 哪怕 mtime 远超宽限期也不碰 (孤儿判据=无锁本体, 非年龄)。"""
    reg = SessionLockRegistry(towow, "execution")
    reg.acquire("sess-work-longlived000", actor_id="a", skill_id="M-1.4", pid=1)
    _write_session_meta(towow, "execution", "sess-work-longlived000", {"task_id": "T-L"})
    p = _meta_file(towow, "execution", "sess-work-longlived000")
    old = p.stat().st_mtime - (_ORPHAN_META_GRACE_S * 10)
    os.utime(p, (old, old))
    _sweep_orphan_meta(towow, "execution")
    assert p.exists()


# ── M2 12hex sid + R5 execution 前缀映射 ─────────────────────────────────────────


def test_acquire_session_generates_12hex_sid(towow: Path) -> None:
    """缺省 sid = sess-<kind>-<12hex> (8 hex=32bit 有生日碰撞面, M2 加宽到 12)。"""
    info = acquire_session(towow, "plan", None, actor_id="m13", skill_id="M-1.3", meta={})
    assert re.fullmatch(r"sess-plan-[0-9a-f]{12}", info.session_id), info.session_id


def test_acquire_session_execution_keeps_sess_work_prefix(towow: Path) -> None:
    """R5: execution 的 sid 保留历史前缀 sess-work-, 不机械拼 sess-execution-。"""
    info = acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={})
    assert re.fullmatch(r"sess-work-[0-9a-f]{12}", info.session_id), info.session_id


def test_acquire_session_explicit_sid_used_as_new_sid(towow: Path) -> None:
    """B2: --session-id 给定即用它当 **新** sid (不是 attach 已有会话)。"""
    info = acquire_session(
        towow, "review", "sess-review-explicit0001", actor_id="m15", skill_id="M-1.5", meta={},
    )
    assert info.session_id == "sess-review-explicit0001"


# ── acquire 撞已活跃 sid → 友好 exit 1 (且不覆盖邻居 meta) ───────────────────────


def test_acquire_session_collision_with_live_sid_friendly_exit1(towow: Path) -> None:
    """撞已活跃 sid → exit 1; 预检在写 meta 之前 → 那个活会话的 meta 不被覆盖。"""
    acquire_session(
        towow, "fix", "sess-fix-takenalready", actor_id="m16", skill_id="M-1.6",
        meta={"finding_id": "f-orig"},
    )
    with pytest.raises(SystemExit) as exc_info:
        acquire_session(
            towow, "fix", "sess-fix-takenalready", actor_id="m16b", skill_id="M-1.6",
            meta={"finding_id": "f-intruder"},
        )
    assert exc_info.value.code == 1
    # 邻居会话的 meta 未被入侵者覆盖 (预检先于 meta 写)
    assert _read_session_meta(towow, "fix", "sess-fix-takenalready") == {"finding_id": "f-orig"}


# ── resolve 三态 (0 → exit 1 / 1 → 用它 / N → exit 1 要求 --session-id) ──────────


def test_resolve_session_zero_live_exit1(towow: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        resolve_session(towow, "consensus", None)
    assert exc_info.value.code == 1


def test_resolve_session_zero_live_message_uses_cli_group_name(towow: Path, capsys) -> None:
    """execution 的报错文案指 `work start`(CLI group 名), 不是 execution start。

    措辞 2026-07-13 随 T-TWU-A 回流从 `towow work start` 收敛为 `tw work start`
    (./tw 统一入口); 本测试守的是 group 名不串, 不钉具体入口拼写。"""
    with pytest.raises(SystemExit):
        resolve_session(towow, "execution", None)
    err = capsys.readouterr().err
    assert "work start" in err
    assert "execution start" not in err


def test_resolve_session_single_live_returns_it(towow: Path) -> None:
    info = acquire_session(towow, "plan", None, actor_id="m13", skill_id="M-1.3", meta={})
    resolved = resolve_session(towow, "plan", None)
    assert resolved.session_id == info.session_id


def test_resolve_session_multiple_live_no_explicit_exit1(towow: Path) -> None:
    """N 个 live 且未给 --session-id → fail-closed exit 1 (不给隐式默认避免认错)。"""
    acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={})
    acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={})
    with pytest.raises(SystemExit) as exc_info:
        resolve_session(towow, "execution", None)
    assert exc_info.value.code == 1


def test_resolve_session_explicit_hits_its_own_session_among_many(towow: Path) -> None:
    """并行 N 会话下 explicit --session-id 只认自己 — 血缘不串。"""
    a = acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={"task_id": "T-A"})
    acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={"task_id": "T-B"})
    resolved = resolve_session(towow, "execution", a.session_id)
    assert resolved.session_id == a.session_id
    assert _read_session_meta(towow, "execution", resolved.session_id) == {"task_id": "T-A"}


def test_resolve_session_unknown_explicit_exit1(towow: Path) -> None:
    """指向不存在/已回收的 session → exit 1 (绝不认到别的会话)。"""
    acquire_session(towow, "execution", None, actor_id="m14", skill_id="M-1.4", meta={})
    with pytest.raises(SystemExit) as exc_info:
        resolve_session(towow, "execution", "sess-work-doesnotexist")
    assert exc_info.value.code == 1


# ── R3 入口对全 6 kind reap — stale 锁回收窗口收敛到"下一条任意 phase 命令" ────────


def test_helper_entry_reaps_stale_locks_of_all_kinds(towow: Path) -> None:
    """空闲 kind 的 stale 锁被任意 phase 的 helper 入口回收 — 不再无上界躺尸。

    磁盘实证 (复核 R3): interview registry 锁躺 19-27h 没人收, 因 reap 只在"下一条同 kind
    命令"触发。修正后: 对 plan kind 跑 acquire_session, consensus kind 的 stale 锁也被回收。
    """
    stale_reg = SessionLockRegistry(towow, "consensus")
    stale_reg.acquire(
        "sess-consensus-stale0000000", actor_id="a", skill_id="M-1.2", pid=1, started_at_unix=1000,
    )
    acquire_session(towow, "plan", None, actor_id="m13", skill_id="M-1.3", meta={})
    stale_lock = towow / "locks" / "sessions" / "consensus" / "sess-consensus-stale0000000.json"
    assert not stale_lock.exists(), "R3: 任意 phase 命令应回收全部 kind 的 stale 锁"


# ── meta 读取健壮性 (缺失/损坏/非 dict → 空 dict, 不抛) ──────────────────────────


def test_read_session_meta_missing_corrupt_nondict_returns_empty(towow: Path) -> None:
    assert _read_session_meta(towow, "interview", "sess-interview-nothere00000") == {}
    p = _meta_file(towow, "interview", "sess-interview-corrupt00000")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert _read_session_meta(towow, "interview", "sess-interview-corrupt00000") == {}
    p2 = _meta_file(towow, "interview", "sess-interview-nondict00000")
    p2.write_text("[1, 2, 3]", encoding="utf-8")
    assert _read_session_meta(towow, "interview", "sess-interview-nondict00000") == {}
