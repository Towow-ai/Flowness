"""系统内存 admission 共享原语 — 口子4 (orchestrator 派发门) 与 fork spawn 门共用的【单一可信来源】。

崩溃根 (per-session-fork-uncounted-by-cap-causes-oom@v1, 2026-06-30): cap 只数【会话】, 真内存
杀手是每会话同步 spawn 的 `claude -p` fork 子进程 (execution-self-check / audit / method fork /
fix-self-check), 这些 fork 不被任何 cap/runaway 看见 → 真实进程 = 会话 × fork倍数 → 55G OOM 整机重启。

口子4 (l2.orchestrator) 在【派发轮询】层采系统真内存 (含所有 fork 的 RSS) 做 admission-control:
内存紧 → pause + 拒新【会话】派发。但那道门管不到【已在跑的会话内部 spawn 的 fork】—— 那条 fork
spawn 走 l1.verification_fork._default_runner (`claude -p` 真咽喉), 不经过 orchestrator 派发轮询。

本模块把内存采样原语抽成两方共享 (l1 fork 门 + l2 派发门), 避免"两处该一致却各看各的"漂移
(正是本崩溃根的同型失败)。l1 不能 import l2, 故采样原语下沉到 l1 这里, orchestrator (l2) 反过来
import 本模块。

⚠ 本层是【per-process 快照】admission, 不是【跨会话原子 slot】: N 个独立会话进程各自在 ~21% free
时都通过本门、然后各 spawn 2-3G fork, 仍可能合起来过冲 (无跨进程共享计数)。这对【补防线】足够
(口子4 派发门 + 20% 余量兜主要 case), 但【硬保证】是跨进程 fork-slot —— 那是另一条留债的工作,
不要把本门读成密不透风的总帽。
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time
from collections.abc import Callable

# ── 阈值 (口子4 与 fork 门同源, 单一 env / 默认值, 不重定义) ──────────────────────────
_MEMORY_PAUSE_FRACTION_ENV = "TOWOW_MEMORY_PAUSE_FREE_FRACTION"
DEFAULT_MEMORY_PAUSE_FRACTION = 0.20  # 系统可用内存 < 20% → 暂停 (55G崩溃约在~14%free, 留余量提前拦)

# ── fork spawn 门: 内存紧时先【延迟】(有界等内存回落) 再【拒绝】, 而非立刻硬拒 ────────────
_FORK_ADMISSION_WAIT_ENV = "TOWOW_FORK_MEMORY_ADMISSION_WAIT_S"
DEFAULT_FORK_ADMISSION_WAIT_S = 6.0  # 内存紧时最多有界等多久 (给瞬态 spike 回落机会; 超时仍紧 → 拒)
_FORK_ADMISSION_POLL_S = 1.5  # 等待期内重采间隔


def memory_pause_fraction() -> float:
    """口子4/fork 门阈值: 系统可用内存占比低于此则暂停/拒 fork。

    env TOWOW_MEMORY_PAUSE_FREE_FRACTION (0~1), 默认 0.20。无效值 → 默认。
    """
    raw = os.environ.get(_MEMORY_PAUSE_FRACTION_ENV, "").strip()
    try:
        v = float(raw)
        if 0.0 < v < 1.0:
            return v
    except ValueError:
        pass
    return DEFAULT_MEMORY_PAUSE_FRACTION


def available_memory_fraction() -> float | None:
    """采系统可用内存占比 (0.0~1.0)。无 psutil 依赖 → subprocess (Linux /proc/meminfo, macOS
    memory_pressure)。采不到返 None → 调用方 fail-open 不拦 (口子2 会话数闸兜底, 不因采样失败冻死)。"""
    # Linux: /proc/meminfo (MemAvailable / MemTotal)
    with contextlib.suppress(Exception):
        mi: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    mi[k.strip()] = int(parts[0])  # kB
        total = mi.get("MemTotal")
        avail = mi.get("MemAvailable")
        if total and avail:
            return avail / total
    # macOS: memory_pressure "System-wide memory free percentage: N%"
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(r"free percentage:\s*(\d+)%", out)
        if m:
            return int(m.group(1)) / 100.0
    return None


def _fork_admission_wait_s() -> float:
    """fork 门内存紧时的有界等待上限 (秒)。env TOWOW_FORK_MEMORY_ADMISSION_WAIT_S, 默认 6.0。

    钳到 ≥0 (0 = 不等, 紧则立刻拒); 无效值 → 默认。
    """
    raw = os.environ.get(_FORK_ADMISSION_WAIT_ENV, "").strip()
    try:
        v = float(raw)
        if v >= 0.0:
            return v
    except ValueError:
        pass
    return DEFAULT_FORK_ADMISSION_WAIT_S


def fork_memory_admission(
    *,
    sample: Callable[[], float | None] | None = None,
    threshold: float | None = None,
    max_wait_s: float | None = None,
    poll_s: float = _FORK_ADMISSION_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> str | None:
    """fork spawn 内存 admission 门 (补防线): 真起 `claude -p` fork 子进程前调。

    返回:
      - None  → 放行 (内存够 / 采不到样 fail-open / 等待期内回落)
      - str   → 拒绝 (有界等待后系统可用内存仍 < 阈值); 字符串是给 caller 拼进异常的原因。

    语义 = 先【延迟】再【拒绝】 (闭合判据 "内存门触发时新 fork 被拒/延迟"):
      内存紧 → 有界等 max_wait_s (给其他 fork 退出腾内存的瞬态 spike 回落机会), 期间每 poll_s 重采;
      回落到阈值上 → 放行; 等满仍紧 → 拒绝 (caller fail-closed: 门 fork 把拒绝当不放行, 安全侧)。

    fail-open: sample 返 None (采不到内存) → 放行 —— 与口子4 同口径, 不因采样失败冻死整链
    (口子4 派发门 + 口子2 会话数闸在更外层兜底)。

    注入缝 (sample/sleep/now/threshold/max_wait_s) 便于单测, 默认全接真实采样 + 真 sleep。
    """
    _sample = sample or available_memory_fraction
    thr = threshold if threshold is not None else memory_pause_fraction()
    wait = max_wait_s if max_wait_s is not None else _fork_admission_wait_s()

    frac = _sample()
    if frac is None:
        return None  # 采不到 → fail-open (口子4 派发门 / 口子2 会话数闸兜底)
    if frac >= thr:
        return None  # 内存够 → 放行

    # 内存紧: 有界等内存回落 (延迟), 期间重采; 等满仍紧 → 拒绝。
    deadline = now() + wait
    last = frac
    while now() < deadline:
        sleep(min(poll_s, max(0.0, deadline - now())))
        cur = _sample()
        if cur is None:
            return None  # 等待中采样失败 → fail-open (不冻死)
        last = cur
        if cur >= thr:
            return None  # 回落 → 放行
    return (
        f"系统可用内存 {last * 100:.0f}% < 阈值 {thr * 100:.0f}% (有界等 {wait:.0f}s 未回落) — "
        f"瞬态内存 admission 拒绝本次 fork spawn, 内存回落后重试 (防 per-session fork 顶到 OOM, "
        f"口子4 补防线)。可调阈值 {_MEMORY_PAUSE_FRACTION_ENV} / 等待 {_FORK_ADMISSION_WAIT_ENV}"
    )


__all__ = [
    "DEFAULT_FORK_ADMISSION_WAIT_S",
    "DEFAULT_MEMORY_PAUSE_FRACTION",
    "available_memory_fraction",
    "fork_memory_admission",
    "memory_pause_fraction",
]
