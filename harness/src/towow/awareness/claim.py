"""K5 · in-flight 权威: 原子认领 (O_EXCL) + 单调 fencing token (层②)。

设计依据: solution-3 + rc-build-execution-isolated-ledger 邻域 / 20-layer23 §T2.3 / Kleppmann
《How to do distributed locking》。

## 本质
把"谁在干这件事"做成**抢占式原子事实**, reaper(判死重派) 与 dispatch(派活) 共用同一真相 →
误杀与双 driver 孪生从此共用同一真相, 不再分叉。

## 两个正交脑裂面, 各一个机制 (可证伪)
- **O_EXCL 原子认领** 堵"两进程同时认领同一 task"(互斥): os.open(O_CREAT|O_EXCL) 创建即认领,
  已存在即 FileExistsError → 转盯场不 spawn。对因为: O_EXCL 在同一本地 fs 是 POSIX 原子的。
  塌于 Y: 戳目录跨 NFS 不保证原子 → 不变量"戳目录必须本地 fs"。
- **单调 fencing token** 堵"被抢锁的旧会话复活后迟到写"(时序脑裂): 每次认领发一个递增 token, 写资源侧
  (commit gate) 拒收低于当前的 token。对因为: 仅 O_EXCL 堵不住"A被判死→token给B→A复活仍写"
  (Kleppmann 反复论证锁+GC/复活必须 fencing)。塌于 Y: 若资源侧不验 token(只认领侧带), fencing
  形同虚设 → token 必须在写入侧验(verify_fencing_token), 不只生成侧。

## 不变量
戳目录本地 fs; .fence(最高已发 token) 在 release 后保留, 保证 token 跨认领易主单调递增; 只有
O_EXCL 认领赢家碰 .fence(被 .claim 存在性串行化), RMW 安全。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# B-2 reaper (T-RMD-S3-REAPER, 根治 f-sub-atomic-claim-no-reaper): spawn 窗口阈值。
# .claim 只在 spawn 窗口内活 (claim → spawn → stamp → release, 都在 try/finally 里释放),
# 正常持有秒~分钟级。崩溃 (SIGKILL 卡在 claim 与 finally 之间) 会 strand 一个 .claim —— 无 reaper
# 时它永久存在 → 该 task 永远被认为已认领、永不重派 = 永久饿死 (finding live 实证)。心跳超时阈值取得
# 显著高于最坏 spawn 窗口 (工位创建 + try_spawn 有界重试 backoff, 秒级), 又远低于"永久", 让泄漏的
# claim 在一个 reaper 周期后被回收。心跳续约 (renew_claim) 刷新 ts 让活/慢 spawn 不被误回收。与
# session_lock EXECUTION_STALE_AFTER_S(30min) 同量级偏小 (claim 比 session 更短命)。工程可调常量。
EXEC_CLAIM_STALE_AFTER_S = 10 * 60


@dataclass(frozen=True)
class ClaimInfo:
    claimant: str
    fencing_token: int
    ts: float
    # B-2 reaper: 认领的 task_id 写进 .claim → reaper 扫文件回收时能溯源真 task_id (文件名是 slug,
    # task_id 含 / \ 时有损)。旧 .claim 文件无此字段 → 空串, read_claim/reaper 回退文件名 slug。
    task_id: str = ""


@dataclass(frozen=True)
class ReapedClaim:
    """reaper 回收掉的一个泄漏/过期 .claim (caller 据此 emit 回收事件 + ready-set 重派)。"""

    task_id: str
    claimant: str
    fencing_token: int
    age_s: float  # 回收时 claim 的心跳年龄 (now - ts); malformed → -1.0
    reason: str  # "stale" (心跳超时) | "malformed" (读不出, 坏状态不能卡死 task)


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    fencing_token: int | None
    detail: str


def _claim_path(claim_dir: Path, task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace("\\", "_")
    return Path(claim_dir) / f"{safe}.claim"


def _fence_path(claim_dir: Path, task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace("\\", "_")
    return Path(claim_dir) / f"{safe}.fence"


def _next_token(claim_dir: Path, task_id: str) -> int:
    """读 .fence(最高已发)→+1→持久化。只被 O_EXCL 认领赢家调(被 .claim 串行化)→RMW 安全。"""
    fp = _fence_path(claim_dir, task_id)
    try:
        cur = int(fp.read_text())
    except (OSError, ValueError):
        cur = 0
    nxt = cur + 1
    fp.write_text(str(nxt))
    return nxt


def claim_task(claim_dir: str | Path, task_id: str, claimant: str) -> ClaimResult:
    """原子认领。成功→(True, token); 已被别人认领→(False, None) 转盯场不 spawn。"""
    claim_dir = Path(claim_dir)
    claim_dir.mkdir(parents=True, exist_ok=True)
    p = _claim_path(claim_dir, task_id)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return ClaimResult(False, None, "already_claimed")
    try:
        token = _next_token(claim_dir, task_id)
        os.write(fd, json.dumps({"claimant": claimant, "fencing_token": token,
                                 "ts": time.time(), "task_id": task_id}).encode())
    finally:
        os.close(fd)
    return ClaimResult(True, token, "claimed")


def read_claim(claim_dir: str | Path, task_id: str) -> ClaimInfo | None:
    p = _claim_path(Path(claim_dir), task_id)
    try:
        d = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    # task_id: 新 .claim 自带; 旧文件无 → 回退 caller 已知的 task_id 参数 (read_claim 本就带它)。
    return ClaimInfo(d["claimant"], int(d["fencing_token"]), float(d["ts"]),
                     str(d.get("task_id") or task_id))


def renew_claim(claim_dir: str | Path, task_id: str, claimant: str) -> bool:
    """心跳续约: 只有当前 claimant 能续(更新 ts)。别人持有→False。

    B-2 reaper 接口: 续约刷新 ts → 让活/慢 spawn 的 claim 不被 reaper 当 stale 误回收
    (reaper 不误杀活会话, done_criteria②)。token 不变 (续约不易主)。
    """
    info = read_claim(claim_dir, task_id)
    if info is None or info.claimant != claimant:
        return False
    _claim_path(Path(claim_dir), task_id).write_text(json.dumps(
        {"claimant": claimant, "fencing_token": info.fencing_token, "ts": time.time(),
         "task_id": task_id}))
    return True


def release_claim(claim_dir: str | Path, task_id: str, claimant: str) -> bool:
    """释放认领(只有当前 claimant 能释放)。.fence 保留→下次认领 token 仍递增。"""
    info = read_claim(claim_dir, task_id)
    if info is None or info.claimant != claimant:
        return False
    _claim_path(Path(claim_dir), task_id).unlink(missing_ok=True)
    return True


def verify_fencing_token(claim_dir: str | Path, task_id: str, token: int) -> bool:
    """资源写入侧(commit gate)调: token 是否当前最高(=未被更晚认领作废)。

    被抢锁的旧会话复活后 token < 当前 .fence → 拒(堵时序脑裂)。无 .fence(从没认领)→拒(保守)。
    """
    fp = _fence_path(Path(claim_dir), task_id)
    try:
        current = int(fp.read_text())
    except (OSError, ValueError):
        return False
    return token == current


def _parse_claim_file(p: Path) -> tuple[str, str, int, float] | None:
    """读一个 .claim 文件 → (task_id, claimant, fencing_token, ts), 读不出/字段坏 → None (malformed)。

    task_id 优先取文件内字段 (新 claim 自带); 缺则回退文件名 stem (slug, task_id 无 / \\ 时无损)。
    """
    try:
        d = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    try:
        ts = float(d["ts"])
        token = int(d["fencing_token"])
    except (KeyError, TypeError, ValueError):
        return None
    task_id = str(d.get("task_id") or p.stem)
    claimant = str(d.get("claimant") or "")
    return task_id, claimant, token, ts


def reap_stale_claims(
    claim_dir: str | Path,
    *,
    now: float | None = None,
    stale_after_s: float = EXEC_CLAIM_STALE_AFTER_S,
    is_live: Callable[[str], bool] | None = None,
) -> list[ReapedClaim]:
    """回收泄漏/过期的 .claim (T-RMD-S3-REAPER, 根治 f-sub-atomic-claim-no-reaper)。

    崩溃 (SIGKILL 卡在 claim 与 finally:release 之间) 泄漏的 .claim 永久存在 → 该 task 永远被认为
    已认领、永不重派 = 永久饿死。本 reaper 与 session_lock.reap_stale 同构, 补上 exec claim 这条
    缺失的回收路径:

    - **心跳超时通道**: age = now - ts > stale_after_s → stale (claim 持有超 spawn 窗口阈值)。
      心跳续约 (renew_claim) 刷新 ts → 活/慢 spawn 不被误回收 (done_criteria②)。
    - **malformed 通道**: 读不出 / 字段坏的 .claim 也回收 (它本身就是坏状态, 不能让坏文件卡死 task,
      同 session_lock.reap_stale 对 malformed 锁的处理)。
    - **活会话护栏** (is_live, 可注入): is_live(task_id)=True 的 task 绝不回收 —— 即便 claim ts 过期,
      只要有对应 live execution 会话 (caller 据 session_lock fork_id 匹配判定) 就不误杀 (finding
      点名的"+无对应live execution会话"判据)。is_live=None → 仅按 ts/malformed (单元测试 / 无会话上下文)。

    回收 = unlink 该 .claim (.fence 保留 → 下次认领 token 仍单调递增, 不破坏 B-3 fencing)。被饿死
    task 的戳此前未写 (spawn 没跑完) → claim 一去, ready-set 重算即可重派。返回被回收的 ReapedClaim
    列表 (按 task_id 排序, 确定性), 让 caller emit 回收事件留痕。无 stale/malformed → 空列表无副作用。
    """
    cdir = Path(claim_dir)
    if not cdir.exists():
        return []
    now = time.time() if now is None else now
    reaped: list[ReapedClaim] = []
    for p in sorted(cdir.glob("*.claim")):
        parsed = _parse_claim_file(p)
        if parsed is None:
            # malformed → 回收 (坏状态不能卡死 task), task_id 回退文件名 slug。
            p.unlink(missing_ok=True)
            reaped.append(ReapedClaim(
                task_id=p.stem, claimant="", fencing_token=-1, age_s=-1.0, reason="malformed",
            ))
            continue
        task_id, claimant, token, ts = parsed
        age = now - ts
        if age <= stale_after_s:
            continue  # spawn 窗口内 / 续约刷新过 — 活, 不碰。
        if is_live is not None and is_live(task_id):
            continue  # 有对应 live execution 会话 — reaper 不误杀活会话。
        p.unlink(missing_ok=True)
        reaped.append(ReapedClaim(
            task_id=task_id, claimant=claimant, fencing_token=token, age_s=age, reason="stale",
        ))
    return sorted(reaped, key=lambda r: r.task_id)
