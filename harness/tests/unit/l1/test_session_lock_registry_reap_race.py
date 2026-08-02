"""F-SESSION-REAP-RACE — SessionLockRegistry 非原子写竞态根治 (决定性复现).

病根: `_touch`/`acquire` 用 `path.write_text` 非原子写(先截断再写); `reap_stale` 读到
写一半的锁文件 → `_read_lock` 解析失败返回 None → reap 把活会话当 malformed `unlink` 删除。
并发读写下活会话被"读到半截"误杀。实证: 落地 crisis-5 修复时 plan 会话反复 '0 live sessions',
12 任务只建成 1 个(F-SESSION-REAP-RACE, verified)。

修法: 锁写原子(临时文件 + os.replace, POSIX 原子 rename) + reap 防御性重读(单次读失败不立即判 malformed)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from towow.l1.session_lock import SessionLockRegistry


@pytest.fixture()
def towow(tmp_path: Path) -> Path:
    d = tmp_path / ".towow"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_reap_does_not_kill_live_session_on_transient_partial_read(
    towow: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """活会话锁被并发写到一半时, reap_stale 单次读到 None 不得把它当 malformed 删除。

    红(修前): reap 见 None 立即 unlink → 活会话被误回收。
    绿(修后): 防御性重读拿到真值 → 活会话存活。
    """
    reg = SessionLockRegistry(towow, "plan")
    reg.acquire("sess-plan-test01", actor_id="a", skill_id="m13")

    real_read = reg._read_lock
    state = {"flaked": False}

    def flaky_read(path: Path):
        # 只对目标会话锁制造一次瞬态读失败(模拟读到写一半的文件)。
        if path.name == "sess-plan-test01.json" and not state["flaked"]:
            state["flaked"] = True
            return None
        return real_read(path)

    monkeypatch.setattr(reg, "_read_lock", flaky_read)

    reaped = reg.reap_stale()

    assert "sess-plan-test01" not in reaped, (
        "活会话被瞬态半截读误回收 — F-SESSION-REAP-RACE 未根治"
    )


def test_lock_write_is_atomic_no_partial_file(towow: Path) -> None:
    """锁写必须原子(临时文件 + os.replace): 写后无 .tmp 残留, 文件始终完整合法 JSON。"""
    reg = SessionLockRegistry(towow, "plan")
    reg.acquire("sess-plan-atom", actor_id="a", skill_id="m13")
    # resolve 触发 _touch 的写路径
    reg.resolve("sess-plan-atom")

    lock_dir = towow / "locks" / "sessions" / "plan"
    leftover_tmps = list(lock_dir.glob("*.tmp"))
    assert not leftover_tmps, f"残留临时文件(写不原子): {leftover_tmps}"

    data = json.loads((lock_dir / "sess-plan-atom.json").read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-plan-atom"
