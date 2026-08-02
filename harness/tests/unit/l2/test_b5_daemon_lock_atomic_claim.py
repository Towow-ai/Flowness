"""②D (2026-07-02 崩机根治): B5 单例守卫的原子声明 —— 收窄 check-then-write TOCTOU。

2026-07-01 夜实证: OOM 杀掉 daemon 留 stale pid + 反复前台重启的场景下, 旧版"读 pid 判活 →
(不活)清 → 写自己 pid"check-then-write 在两个并发 start 之间留了 TOCTOU 窗口, 都读到"不活"
就都往下走, 其中一个成了脱管的第二个 daemon。改用 O_CREAT|O_EXCL 把判断+登记收成一次原子
系统调用。

本文件钉住:
  (1) test_claim_succeeds_when_no_pid_file — 干净态声明成功, 拿到自己的 pid。
  (2) test_claim_raises_when_live_pid_present — 已有活 pid (真实当前进程) → RuntimeError,
      不清不抢。
  (3) test_claim_recovers_stale_pid_and_retries — 死 pid 残留 (确认不存在的 pid) → 清理后
      单次重试成功声明。
  (4) test_concurrent_claims_only_one_wins — 并发多进程从"干净态"同时声明, 恰好一个赢,
      其余 alive 后拒绝 (结构性证明: 不是"极小概率不出事", 是内核原子性保证)。
  (5) test_concurrent_stale_pid_recovery_only_one_wins — 并发多进程从"已有死 pid 残留"
      同时抢救, 恰好一个赢 (钉住 (4) 覆盖不到的窗口: "判活→清理→重抢"这段临界区本身的
      互斥, 见 orchestrator.py `_daemon_pid_recovery_lock`)。
  (6) test_concurrent_cli_start_sequence_only_one_wins — 复刻 `orchestrator start` CLI
      真实调用顺序 (先 detect_and_recover_stale_daemon 再 claim_daemon_singleton_or_raise)
      并发跑, 恰好一个赢。这道交错窗口比 (5) 窄得多 (单个 if 分支级别, 不是跨系统调用),
      多轮多进程 (n=4/8) 均未能自然复现——留作真实调用顺序的 smoke test, 不是这条不变量
      的判别性回归钉子 (锁被拆掉这条测试大概率照样绿, advisor 复核指出过, 如实记录)。
  (7) test_detect_and_recover_stale_daemon_uses_recovery_lock — (6) 覆盖不到之处的确定性
      钉子: 不靠并发时序赌运气, 直接 spy 断言 detect_and_recover_stale_daemon 真的进了
      `_daemon_pid_recovery_lock` 临界区——这才是"两个入口共享同一把锁"这条不变量真正的
      判别性回归测试, 锁被拆掉必挂, 不看时序脸色。
  (8) test_run_polling_loop_uses_atomic_claim_and_raises_on_live_daemon — 端到端:
      run_polling_loop 在已有活声明时拒绝启动第二个循环。
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
from typing import TYPE_CHECKING

import pytest

from towow.l2 import orchestrator as _orchestrator_module
from towow.l2.orchestrator import (
    claim_daemon_singleton_or_raise,
    clear_daemon_pid,
    detect_and_recover_stale_daemon,
    ensure_orchestrator_layout,
    is_orchestrator_process_alive,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _make_towow(tmp_path: Path) -> Path:
    towow = tmp_path / ".towow"
    ensure_orchestrator_layout(towow)
    return towow


def test_claim_succeeds_when_no_pid_file(tmp_path: Path) -> None:
    towow = _make_towow(tmp_path)
    pid = claim_daemon_singleton_or_raise(towow)
    assert pid == os.getpid()
    read_pid, alive = is_orchestrator_process_alive(towow)
    assert read_pid == pid
    assert alive is True


def test_claim_raises_when_live_pid_present(tmp_path: Path) -> None:
    towow = _make_towow(tmp_path)
    claim_daemon_singleton_or_raise(towow)  # 自己先声明一次 (自己的 pid 天然是活的)
    with pytest.raises(RuntimeError, match="already running"):
        claim_daemon_singleton_or_raise(towow)  # 再声明一次: 撞见"活"的自己 → 拒


def test_claim_recovers_stale_pid_and_retries(tmp_path: Path) -> None:
    towow = _make_towow(tmp_path)
    pid_path = towow / "orchestrator" / "daemon.pid"
    # 找一个确认不存在的 pid (真实进程 pid 上界远小于此, 不会真撞上活进程)。
    dead_pid = 2**30
    pid_path.write_text(str(dead_pid), encoding="utf-8")

    claimed_pid = claim_daemon_singleton_or_raise(towow)
    assert claimed_pid == os.getpid(), "死 pid 残留应被清理后重新原子声明成自己"
    read_pid, alive = is_orchestrator_process_alive(towow)
    assert read_pid == os.getpid()
    assert alive is True


def _race_worker(
    towow_dir: str,
    out_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    start_barrier: object,
    release_event: object,
) -> None:
    from pathlib import Path as _Path

    from towow.l2.orchestrator import claim_daemon_singleton_or_raise as _claim

    start_barrier.wait(timeout=30)  # type: ignore[attr-defined]
    try:
        pid = _claim(_Path(towow_dir))
        out_queue.put(("claimed", pid))
    except RuntimeError as e:
        out_queue.put(("refused", str(e)))
    # 撑住不立刻退出: 全负载下 spawn 4 个解释器时序会错开, 赢家若在输家还没检查活性前就退出,
    # 输家会读到"pid 存在但进程已死"从而合法地清理重抢——这不是原子性被破坏, 是测试自己让
    # "存活窗口"重叠不住导致的假阳性。等主进程收全 4 份结果再放行, 保证判活期间 4 个进程都还在。
    release_event.wait(timeout=30)  # type: ignore[attr-defined]


def _run_concurrent_claim_race(towow: Path, *, n: int) -> None:
    """spawn n 个真实进程同时争抢 claim_daemon_singleton_or_raise, 断言恰好一个赢。

    barrier 同步发起时机 + release_event 让赢家在全部结果收齐前不退出 (避免"赢家提前退出 →
    输家合法地把它当死残留清理重抢"这种测试自身的假阳性源, 跟真实竞态区分开)。
    """
    ctx = multiprocessing.get_context("spawn")
    q: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
    start_barrier = ctx.Barrier(n)
    release_event = ctx.Event()
    procs = [
        ctx.Process(target=_race_worker, args=(str(towow), q, start_barrier, release_event))
        for _ in range(n)
    ]
    for p in procs:
        p.start()

    results = [q.get(timeout=60) for _ in range(n)]
    release_event.set()  # 收全 n 份结果后才放行退出, 判活窗口内 n 个进程全程都活着

    for p in procs:
        p.join(timeout=60)
    exitcodes = [p.exitcode for p in procs]
    assert all(c == 0 for c in exitcodes), (
        f"{n} 个子进程必须都干净退出 (exitcode 0) 结果才可信, 实得 {exitcodes} —— "
        "非 0/None 说明子进程本身没跑完/崩了, 是基础设施问题不是原子性被破坏"
    )

    claimed = [r for r in results if r[0] == "claimed"]
    refused = [r for r in results if r[0] == "refused"]
    assert len(claimed) == 1, f"必须恰好一个进程声明成功, 实得 {results}"
    assert len(refused) == n - 1, f"其余 {n - 1} 个必须看见'活'的赢家而拒绝, 实得 {results}"
    for _, msg in refused:
        assert "already running" in msg


def test_concurrent_claims_only_one_wins(tmp_path: Path) -> None:
    """结构性证明 (不是猜性能): 4 个真实进程并发声明同一 daemon.pid, 恰好一个 claimed。

    O_CREAT|O_EXCL 是 POSIX 硬保证 (两个进程不可能都对同一路径 exclusive-create 成功), 所以
    这条钉子的假设前提本身不该 flaky。但 spawn 4 个全新解释器 + 在系统重负载下的调度抖动会让
    join/get 的时序不稳——超时给足余量, 并显式核验每个子进程都干净退出 (exitcode==0), 把
    "基础设施计时抖动"和"真的破坏了原子性"分开, 后者才是这条测试真正要守的不变量。

    用 barrier 同步 4 个进程一起发起声明、用 release_event 让赢家在输家判完活性前不退出——
    否则赢家提前退出会让输家合法地把它当"死残留"清理重抢, 制造出跟真实 bug 长得一样但成因
    完全不同的假阳性 (这条测试曾经因为这个原因误报过一次, 详见 orchestrator.py
    `_claim_daemon_pid_atomically` 的 os.link 写法注释)。
    """
    towow = _make_towow(tmp_path)
    _run_concurrent_claim_race(towow, n=4)


def test_concurrent_stale_pid_recovery_only_one_wins(tmp_path: Path) -> None:
    """并发多进程从"已有死 pid 残留"状态同时抢救声明, 恰好一个赢。

    钉住 test_concurrent_claims_only_one_wins 覆盖不到的窗口: 那条测试从空目录起步, 只
    验证了"创建"这一步的互斥 (os.link)。真实崩机场景是"OOM 杀掉 daemon 留 stale pid + 反复
    前台重启"——起步状态本来就有一个死 pid, 多个并发启动者都要走"判活(死)→清理→重抢"这条
    恢复路径。这条路径若不整体互斥 (_daemon_pid_recovery_lock), 两个都判定"死"的并发者会
    各自 unlink+重抢, 后一个 unlink 连带清掉前一个刚重抢成功的活声明——制造出两个都自认
    声明成功的赢家, 这才是"崩机夜孤儿 daemon"实际发生的那条路径, 不是干净态创建竞态。
    """
    towow = _make_towow(tmp_path)
    pid_path = towow / "orchestrator" / "daemon.pid"
    dead_pid = 2**30  # 确认不存在的 pid, 不会真撞上活进程
    pid_path.write_text(str(dead_pid), encoding="utf-8")

    _run_concurrent_claim_race(towow, n=4)


def _cli_start_sequence_worker(
    towow_dir: str,
    out_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    start_barrier: object,
    release_event: object,
) -> None:
    """复刻 `orchestrator start` CLI 的真实两步顺序 (cli/main.py ~18173-18186): 先
    `detect_and_recover_stale_daemon` (CLI 入口的快速拒绝检查), 没活 daemon 才继续
    `claim_daemon_singleton_or_raise` (daemonize/run_polling_loop 内部的真正声明)。
    """
    from pathlib import Path as _Path

    from towow.l2.orchestrator import claim_daemon_singleton_or_raise as _claim
    from towow.l2.orchestrator import detect_and_recover_stale_daemon as _detect

    start_barrier.wait(timeout=30)  # type: ignore[attr-defined]
    towow = _Path(towow_dir)
    recovery = _detect(towow)
    if recovery.running_pid is not None:
        out_queue.put(("refused", f"orchestrator daemon already running (pid={recovery.running_pid})"))
    else:
        try:
            pid = _claim(towow)
            out_queue.put(("claimed", pid))
        except RuntimeError as e:
            out_queue.put(("refused", str(e)))
    release_event.wait(timeout=30)  # type: ignore[attr-defined]


def test_concurrent_cli_start_sequence_only_one_wins(tmp_path: Path) -> None:
    """复刻 `orchestrator start` CLI 的真实两步调用顺序并发跑, 恰好一个赢。

    advisor 复核发现的独立漏洞: `detect_and_recover_stale_daemon` 是 CLI `orchestrator
    start` 和通用 PollingFrame 的独立入口, 跟 `claim_daemon_singleton_or_raise` 读写同一个
    daemon.pid, 但补 `_daemon_pid_recovery_lock` 时若只锁了 claim_daemon_singleton_or_raise
    没锁 detect_and_recover_stale_daemon, 两个入口各判各的、不共享互斥, 同样的"判活→清理"
    竞态会从这条侧门重新进来——补一个入口漏一个等于没补。这条测试直接复刻 CLI 的真实两步
    顺序 (而不是只测单一函数), 确保两个入口在同一把锁下真正互斥。
    """
    towow = _make_towow(tmp_path)
    pid_path = towow / "orchestrator" / "daemon.pid"
    dead_pid = 2**30  # 确认不存在的 pid, 不会真撞上活进程
    pid_path.write_text(str(dead_pid), encoding="utf-8")

    n = 4
    ctx = multiprocessing.get_context("spawn")
    q: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
    start_barrier = ctx.Barrier(n)
    release_event = ctx.Event()
    procs = [
        ctx.Process(
            target=_cli_start_sequence_worker,
            args=(str(towow), q, start_barrier, release_event),
        )
        for _ in range(n)
    ]
    for p in procs:
        p.start()

    results = [q.get(timeout=60) for _ in range(n)]
    release_event.set()

    for p in procs:
        p.join(timeout=60)
    exitcodes = [p.exitcode for p in procs]
    assert all(c == 0 for c in exitcodes), (
        f"{n} 个子进程必须都干净退出 (exitcode 0) 结果才可信, 实得 {exitcodes}"
    )

    claimed = [r for r in results if r[0] == "claimed"]
    assert len(claimed) == 1, f"必须恰好一个进程声明成功, 实得 {results}"


def test_detect_and_recover_stale_daemon_uses_recovery_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确定性钉住 (不靠并发时序赌运气): detect_and_recover_stale_daemon 必须经过
    `_daemon_pid_recovery_lock` 临界区, 不能退化成"判活→清理"两步各自为政。

    test_concurrent_cli_start_sequence_only_one_wins 试图用真实并发复现这条竞态, 但那道
    交错窗口太窄 (单个 if 分支级别), 多轮多进程都没能自然复现——对"两个入口共享同一把锁"这条
    不变量不具备判别力 (锁被拆掉那条测试大概率照样绿)。这条测试才是真正的回归钉子: spy 在
    `_daemon_pid_recovery_lock` 上, 直接断言 detect 函数进了这把锁的临界区——锁被拆掉必挂,
    不依赖任何时序运气。
    """
    towow = _make_towow(tmp_path)
    entered: list[bool] = []
    real_lock = _orchestrator_module._daemon_pid_recovery_lock

    @contextlib.contextmanager
    def _spy_lock(towow_dir: Path) -> Iterator[None]:
        entered.append(True)
        with real_lock(towow_dir):
            yield

    monkeypatch.setattr(_orchestrator_module, "_daemon_pid_recovery_lock", _spy_lock)
    detect_and_recover_stale_daemon(towow)
    assert entered, "detect_and_recover_stale_daemon 必须经过 _daemon_pid_recovery_lock 临界区"


def test_run_polling_loop_uses_atomic_claim_and_raises_on_live_daemon(tmp_path: Path) -> None:
    from towow.l0.event_log import EventLog
    from towow.l2.orchestrator import run_polling_loop

    towow = _make_towow(tmp_path)
    event_log = EventLog(towow / "events.log")
    claim_daemon_singleton_or_raise(towow)  # 模拟"已有一个活 daemon 声明了"

    with pytest.raises(RuntimeError, match="B5 singleton"):
        run_polling_loop(towow, event_log, poll_interval_s=0.01, mock_spawn=True, max_iterations=1)

    clear_daemon_pid(towow)  # 清理, 不留残留影响其它测试 (虽然 tmp_path 天然隔离, 显式收尾更清楚)
