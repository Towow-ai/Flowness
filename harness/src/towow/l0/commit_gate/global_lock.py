"""M-3.1 §4.2.3 全局 commit mutex 的共享实现。

f-interview-start-commitlock-silent-block-zombie-session: cli/main.py、l2/daemon_run_once.py、
l3/migration/engine.py 原各自逐字重复一份裸 `fcntl.flock(LOCK_EX)` 阻塞获取——无重试退避、获取
前后均无用户可见反馈、锁文件从不记持有者 pid。调用方撞上任何一个正在提交的并发操作(哪怕完全
不相关)就静默罚站数分钟，无法判断是在排队还是已经卡死。三处收敛到这里，只加等待期间的可观测性
(非阻塞探测失败才打一次提示 + 把自己的 pid/起始时间写回锁文件供下一个等待者读)，不改变锁的排他
语义与覆盖范围——那属于 tier-2(锁粒度/审计子进程移出锁范围)，另走工程共识决定怎么改。

commit-lock-hardening@v1 阶段A(诚实 holder 探测，T-LKC-05)：上面这段等待提示只读锁文件、从不对
holder pid 验活——死进程(fcntl 随进程死亡自动释放)仍被原样呈现成"当前持有者"，等待中的调用方
被过期文案误导成"锁被活进程占着"(采访证据 #2：等锁 8 分钟被死 pid 提示误导)。本次只加一层诊断
探测(`_probe_pid_alive`)，锁的获取/等待/排他语义(`global_commit_mutex` 内 fcntl 调用序列)
逐字不变——零行为变更，只改 default_on_wait 打印的文案依据。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any


def _read_holder(f: Any) -> dict[str, Any] | None:
    """best-effort 读锁文件里记的持有者信息(非阻塞探测失败后，读不到/读坏不致命，返回 None)。"""
    try:
        f.seek(0)
        raw = f.read()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_holder(f: Any) -> None:
    """拿到锁之后把自己的 pid/起始时间写回锁文件，供下一个等待者诊断用。best-effort，写失败不致命。"""
    payload = json.dumps({"pid": os.getpid(), "acquired_at_unix": int(time.time())})
    try:
        f.seek(0)
        f.truncate()
        f.write(payload)
        f.flush()
    except OSError:
        pass


def _probe_pid_alive(pid: object) -> bool | None:
    """对 holder pid 做一次非破坏性验活探测(`os.kill(pid, 0)` —— 不发信号，只测能否投递)。

    三态返回，probe-never-raises(探测本身任何异常都被这里接住，绝不向上冒泡打断等待/获取主路径)：
    - True  = 确认存活(信号能投递给该 pid)
    - False = 确认已死 —— 唯一判据是 `ProcessLookupError`(errno ESRCH，内核层面"这个 pid 不存在")。
      fcntl 物理事实：持有进程死亡时 flock 随之自动释放，死 pid 冒充 holder 纯属提示层陈旧。
    - None  = 无法确认("状态未知")——覆盖两类情况：① pid 字段本身不是合法正整数(类型不对——
      如浮点/字符串/None，或 pid<=0 —— `os.kill(0, 0)` 探测整个进程组、`os.kill(-1, 0)` 探测
      所有可发信号进程，都不是"探测这一个 pid"该有的语义)——一律不当真、不做隐式类型强转
      (`int(3.14)` 悄悄截成 3 再探测是错的 pid，宁可保守拒绝也不猜)；② `os.kill` 抛出 ESRCH
      以外的其它 OSError(最常见 PermissionError/EPERM —— 该错误其实意味着进程存在只是无权限
      发信号，即"活着"的证据，但为了绝不误报死亡这边保守降级为"未知"而非直接判活)。
      降级为未知，绝不因探测失败就误判为"死"或悍然认定"活"。
    """
    # bool 是 int 的子类(True==1/False==0)——holder 记录里出现 bool 类型的 pid 本身就是数据
    # 损坏的信号，不当真；显式排除，不靠 isinstance(pid, int) 误收。
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    else:
        return True


def default_on_wait(holder: dict[str, Any] | None) -> None:
    """默认等待提示：打到 stderr，不依赖 click(l0 层不引入 CLI 框架依赖)。

    诚实 holder 探测(commit-lock-hardening@v1 阶段A)：读到 holder 记录后先对其 pid 验活，
    死进程诚实呈现"持有者已死……非活跃占用"，不再冒充"当前持有者"；探测拿不准(状态未知)时也
    如实说未知，不假装知道。
    """
    if holder and holder.get("pid"):
        age_s = max(0, int(time.time()) - int(holder.get("acquired_at_unix", 0) or 0))
        pid = holder["pid"]
        alive = _probe_pid_alive(pid)
        if alive is False:
            sys.stderr.write(
                f"… 等待 commit.lock（持有者已死：pid={pid}，记录占用约 {age_s}s——"
                "fcntl 已随进程死亡自动释放或即将可得，非活跃占用）—— 请稍候"
                "（若持续未释放，检查 .towow/locks/）\n",
            )
        elif alive is True:
            sys.stderr.write(
                f"… 等待 commit.lock（当前被 pid={pid} 持有，已占用约 {age_s}s）——"
                " 这是全局提交锁，一次只允许一个操作落账，请稍候\n",
            )
        else:
            sys.stderr.write(
                f"… 等待 commit.lock（记录持有者 pid={pid}，已占用约 {age_s}s，"
                "但无法验证是否仍存活——状态未知）—— 请稍候\n",
            )
    else:
        sys.stderr.write("… 等待 commit.lock（被另一个进程持有，具体信息暂读不到）—— 请稍候\n")
    sys.stderr.flush()


@contextlib.contextmanager
def global_commit_mutex(
    towow: Path,
    *,
    on_wait: Callable[[dict[str, Any] | None], None] | None = default_on_wait,
) -> Iterator[None]:
    """fcntl-based global commit mutex on .towow/locks/commit.lock.

    Blocking acquire — submit wrapper serializes commit attempts under this mutex.
    等待时最多打印一次诊断提示(on_wait=None 可静默，测试用)。
    """
    lock_path = towow / "locks" / "commit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if on_wait is not None:
                on_wait(_read_holder(f))
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # blocking fallback —— 排他语义不变
        _write_holder(f)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
