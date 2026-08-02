"""f-interview-start-commitlock-silent-block-zombie-session: global_commit_mutex 诊断层。

覆盖:
  - 排他语义不变(拿到锁的一方在 with 块内持锁, 外部非阻塞探测必失败)。
  - 锁被占时, on_wait 回调恰好触发一次, 且拿到持有者 pid/acquired_at_unix。
  - 拿到锁后, 锁文件里写回了自己的 pid, 供下一个等待者诊断。
"""

from __future__ import annotations

import fcntl
import json
import os
from typing import TYPE_CHECKING

from towow.l0.commit_gate.global_lock import global_commit_mutex

if TYPE_CHECKING:
    from pathlib import Path


def test_global_commit_mutex_still_exclusive(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    lock_path = towow / "locks" / "commit.lock"

    with global_commit_mutex(towow), lock_path.open("a+") as probe:
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            contended = False
        except BlockingIOError:
            contended = True
    assert contended, "锁被持有期间, 外部非阻塞探测应当失败(排他语义必须保持不变)"

    with lock_path.open("a+") as probe2:
        fcntl.flock(probe2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # 释放后必须能拿到, 不抛才对
        fcntl.flock(probe2.fileno(), fcntl.LOCK_UN)


def test_global_commit_mutex_calls_on_wait_when_contended(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    lock_path = towow / "locks" / "commit.lock"
    lock_path.write_text(
        json.dumps({"pid": 999999, "acquired_at_unix": 1}), encoding="utf-8",
    )

    calls: list[dict | None] = []

    # 模拟外部已经持锁(另一个进程/操作正在提交), 再进 global_commit_mutex 应该走等待分支。
    with lock_path.open("a+") as external_holder:
        fcntl.flock(external_holder.fileno(), fcntl.LOCK_EX)
        import threading

        released = threading.Event()

        def _acquire_after_notify() -> None:
            with global_commit_mutex(towow, on_wait=calls.append):
                pass
            released.set()

        t = threading.Thread(target=_acquire_after_notify)
        t.start()
        # 给等待方足够时间探测到锁被占、调用 on_wait 并进入阻塞等待。
        import time

        time.sleep(0.3)
        assert calls, "锁被占用时应当触发一次 on_wait 回调, 而不是静默等待"
        assert calls[0] is not None
        assert calls[0]["pid"] == 999999
        fcntl.flock(external_holder.fileno(), fcntl.LOCK_UN)
        t.join(timeout=5)
    assert released.is_set(), "外部释放锁后, 等待方应当能拿到锁并正常退出"


def test_global_commit_mutex_writes_own_pid_after_acquire(tmp_path: Path) -> None:
    towow = tmp_path / ".towow"
    (towow / "locks").mkdir(parents=True)
    lock_path = towow / "locks" / "commit.lock"

    with global_commit_mutex(towow, on_wait=None):
        pass

    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert isinstance(data["acquired_at_unix"], int)
