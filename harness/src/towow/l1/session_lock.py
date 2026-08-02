"""Per-session lock registry — RUN-080 漏洞1 (大规模并行会话 session 隔离 + 崩溃自愈).

# spec source:
#   harness/docs/SPEC-PATCH-CONCURRENCY-RUN080.md §1 (session tree / per-session 隔离)
#   harness/docs/SPEC-GAP-CONCURRENCY-FINDINGS.md 漏洞1
#   M-3.1 §4.2.3 commit 锁协议 (崩溃自愈复用对象)

为什么存在:
  现行单全局锁 `.towow/locks/interview_session.lock` 只存一个 session_id —— owner 要的
  "大规模并行"会话各自干活时, 第二个会话要么被拒(强制串行)、要么覆盖单锁 → A 会话的
  产出被下游子命令认成 B 的(provenance 血缘串)。且会话崩溃后 stale 锁永久阻塞后续 start
  (错误提示让人手动 rm = 社媒 stale 锁没自愈的反例)。

  SessionLockRegistry 让 N 个会话各持自己 per-session 锁文件(session_id 命名空间), 物理上
  写 A 的锁绝不碰 B 的锁文件 → 血缘不串。崩溃自愈复用 commit 锁那套已验证协议(超时即死 +
  启动自动扫 + 自动释放); 因 session 锁是跨进程 state 文件(start 一个进程写、answer 另一个
  进程读), 不能用 fcntl.flock(进程退出即被 OS 释放, 无法持久), 故改等价语义的
  pid-liveness(进程死=锁可回收) + age-timeout(超时=锁可回收), 在每次 acquire/resolve 时
  启动自动扫(reap_stale)。

锁布局:
  .towow/locks/sessions/<kind>/<session_id>.json
    { session_id, actor_id, skill_id, pid, started_at_unix, parent_session_id?, fork_id? }
  per-session 一文件 = 隔离的物理保证。<kind> 命名空间区分 interview / consensus / plan,
  使同一 .towow 下不同 phase 的并行会话也不互串。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

# 崩溃自愈默认超时 (秒)。一个 session 锁超过这个年龄即视为 stale(进程可能已崩或被孤儿化)。
# 12h: 远大于任何正常采访/共识/计划会话时长, 又不至于让真崩溃的锁卡到下一个工作日。
DEFAULT_STALE_AFTER_S = 12 * 60 * 60

# lock-reap-typed-policy@v1 (T-LRF-04): 通道B 心跳超时按 kind 分型, 取代单一全局 12h。
# 交互式 5 phase (interview/consensus/plan/review/fix) 是单发 CLI, 一次活动间隔可达小时级
# (人在打字/思考), 阈值不变 (= DEFAULT_STALE_AFTER_S = 12h)。
INTERACTIVE_STALE_AFTER_S = DEFAULT_STALE_AFTER_S
# execution 是 bg 持续进程会话, 卡死/崩溃该分钟级被收重派 (06-12 活体: execution 卡死等 12h
# 是死链)。1800s = 30min: 远大于正常 task 间心跳间隔, 又不让 stuck execution 卡到小时级。
# "execution 精确超时分钟数是工程可调常量, 非 owner 级" (lock-reap-typed-policy@v1 边界⑤)。
EXECUTION_STALE_AFTER_S = 30 * 60

# T-FIX-B2-06 (INV-B2-6 / esc-532866a8): serial-reject kind —— 这个 skill 的 `start` 物理单例
# (现仅 plan; review/fix 已退役, 见下), 同一时刻只允许 ≤1 个 live 会话。单飞纪律落在 orchestrator
# 派发层 (T-FIX-B2-01: 派发前查 live, 不盲 spawn 让第二个内部 exit 1 静默死); 这里把"单飞门下
# live ≤1"焊成可验证的不变量 (assert_single_flight)。必须与 orchestrator._SERIAL_REJECT_KIND_BY_
# DISPATCH 的 *值* 集合一致 —— 多了会误约束 execution/review/fix 的并发隔离, 少了会漏守 plan 的
# cohort 写竞争。execution 故意不在内: 它要的是 N 并发隔离 (B1 承重墙), 永不受单飞约束。
# R11 owner-directed (2026-06-14): consensus 移出串行集 —— 改"跨 brief 并发、同 brief 单飞"
# (per-brief 去重在 consensus start), 并发改同一概念的正确性由 commit gate 兜 (O-08 §3.1 子项4)。
# T-RCL-01 (plan-review-concurrency-lease / 2026-06-17): review 移出串行集 —— 退役 kind 级单飞,
# 改 review-target 级完全并发。review 的 provenance 不再靠并发排斥守, 委托既有 review-unit
# (session_id) 血缘 (T-LND-02/T-SL-A4 已确立; review_verdict 折叠按 review_unit_id 精确过滤,
# 与并发无关) —— 同 review-target 第二视角放行, 不同 review-target 并行不拒。
# fix 退役 (finding-fix-serial-lock-redundant-bottlenecks-parallel-autopilot-repair / 2026-06-22):
# 串行曾守 finding 错挂会话的 provenance 腐蚀 (esc-532866a8)。已单独验 finding 归属在并发下安全 ——
# finding_lifecycle 折叠按 finding_id 内容寻址 (非持锁会话, _reduce_finding_lifecycle) +
# FixProposed/FixCompleted 按 fix_id→related_finding_id 血缘 + provenance 按显式 session_id 盖戳
# (resolve_session fail-loud) + 隔离工位 T-NB-3 就位; 并发安全测试
# test_symptom1b_fix_provenance_safe_under_concurrency 证。故 fix 移出串行集, 同 T-RCL-01 给 review
# 的退役 (provenance 锚定 > 并发排斥)。
# plan 退役 (R11 / 2026-06-23): plan_id 跨会话共享 (继承自 EngineeringConsensusFreezed), kind 级单飞
# 过粗 —— 把【不同 plan_id】的合法并行计划也拒掉。R11 (owner 2026-06-22 拍, 同 owner 2026-06-14 为
# consensus 拍的模型) = "同 plan_id 单飞、不同 plan_id 并行": 同 plan_id 防 cohort 污染由 plan_start
# 的 per-plan_id 自 reject 守 (镜像 consensus_start, 见 cli/main.py plan_start), 不同 plan_id 放行。
# 派发侧去重由 is_already_dispatched 复合键 <evt>__planning + T-FND-01 plan-product-exists 兜 (与
# consensus 对称, 退 serial 门无去重缺口)。plan 是 SERIAL_REJECT_KINDS 仅剩最后一个 kind (review
# T-RCL-01 / fix 2026-06-22 已先退) → 移出后集合空。机制本体 (registry/assert_single_flight/运行时
# 哨兵 _check_single_flight_invariants) 保留不删, 未来某 kind 真需 kind 级单飞可 re-populate (非一次性
# 单向门)。两侧对齐由 orchestrator._SERIAL_REJECT_KIND_BY_DISPATCH (现亦空) + test_single_flight_
# runtime_sentinel 钉死。
SERIAL_REJECT_KINDS: frozenset[str] = frozenset()


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写锁文件: 写临时文件 + os.replace(POSIX 原子 rename), 读者永不见写一半的半截内容。

    F-SESSION-REAP-RACE 根治: 旧 path.write_text 非原子(先截断再写); reap_stale 并发读到
    写一半的锁文件 → _read_lock 解析失败返回 None → reap 把活会话当 malformed 删除。原子写后
    任一读者只会看到旧完整文件或新完整文件, 不存在半截窗口。临时名带 pid 避免跨进程撞名;
    后缀 .tmp 不匹配 reap_stale 的 '*.json' glob, 故扫不到临时文件。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class SessionLockError(RuntimeError):
    """Base for session-lock registry errors."""


class DuplicateSessionError(SessionLockError):
    """acquire 一个已存在 LIVE 锁的 session_id(重复 acquire 同 id)。"""


class UnknownSessionError(SessionLockError):
    """resolve(explicit_session_id) 指向一个 registry 里不存在(或已 stale 被回收)的 session。"""


class AmbiguousActiveSessionError(SessionLockError):
    """resolve() 未给 explicit_session_id 但有 >1 个 live 会话 —— 必须显式 --session-id 消歧。

    这是"血缘不串"的关键守门: 多并行会话同时活跃时, 不给一个隐式默认避免认错。
    """


class InvalidSessionIdError(SessionLockError):
    """session_id 含非法字符 —— 它会被拼进锁/meta 文件路径, 必须挡目录穿越 (T-SL critical)。"""


class SingleFlightViolationError(SessionLockError):
    """T-FIX-B2-06 (INV-B2-6): serial-reject kind 在同一时刻有 >1 个 live 会话 —— 单飞门被绕过。

    这是 esc-532866a8『锁被抢』根症状的可检测违例: 两个 review/fix 会话同时活 → 后产的 finding
    可能 resolve 折叠认错邻居会话 (provenance 错挂)。守卫把这个静默腐蚀窗口变成立即可抓的违例。
    """


# session_id 白名单 (覆盖现存所有 sid 形态: sess-<kind>-<12hex> / sess-work-... / 12hex)。
# 用户可控的 --session-id 会被 _lock_path / _session_meta_path 直接拼进写路径; 不挡 '../' 能
# 目录穿越覆盖 canonical 投影 (../../../graph/task_graph.json) 或伪造跨 kind 治理锁。
_SID_ALLOWED_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
)


def validate_session_id(session_id: str) -> None:
    """Fail-closed 校验 session_id 字符集 (T-SL critical path-traversal guard)。

    非法 → InvalidSessionIdError。'.' 与 '/' 都不在白名单, 故 '..' / 绝对路径 / 任意穿越串
    自然被拒, 空串也拒。registry.acquire (写锁底座) + CLI acquire_session (start 入口) 两层都调,
    纵深防御: 即便未来有调用方绕过 CLI helper, 底座仍拦得住。
    """
    if not session_id or any(c not in _SID_ALLOWED_CHARS for c in session_id):
        raise InvalidSessionIdError(
            f"invalid session_id {session_id!r}: only [A-Za-z0-9_-] allowed "
            "(no '/', '\\\\', '..', or empty — path-traversal guard)",
        )


def _pid_alive(pid: int) -> bool:
    """进程是否存活 (os.kill(pid, 0) 探针)。pid<=0 视为'不可判定→保守当活'(不误回收)。"""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但属别的用户 → 仍活着 (EPERM 即 alive)。
        return True
    except OSError:
        return True
    return True


@dataclass(frozen=True)
class SessionLockInfo:
    """一个 per-session 锁文件的内容。"""

    session_id: str
    actor_id: str
    skill_id: str
    pid: int
    started_at_unix: int
    parent_session_id: str | None = None
    fork_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "skill_id": self.skill_id,
            "pid": self.pid,
            "started_at_unix": self.started_at_unix,
        }
        if self.parent_session_id is not None:
            d["parent_session_id"] = self.parent_session_id
        if self.fork_id is not None:
            d["fork_id"] = self.fork_id
        return d


PidAliveFn = Callable[[int], bool]


class SessionLockRegistry:
    """Per-(project, kind) 会话锁登记 —— N 并发会话隔离 + 崩溃自愈。

    所有读路径(resolve / live_sessions)和写路径(acquire)入口都先 reap_stale, 即"启动自动扫" —
    任何一个新命令进来都会顺手把崩溃/超时的旧锁回收掉, 无需后台 daemon、无需手动 rm。
    """

    def __init__(self, towow_dir: Path, kind: str) -> None:
        self._towow = towow_dir
        self._kind = kind

    def _sessions_dir(self) -> Path:
        return self._towow / "locks" / "sessions" / self._kind

    def _lock_path(self, session_id: str) -> Path:
        return self._sessions_dir() / f"{session_id}.json"

    def _read_lock(self, path: Path) -> SessionLockInfo | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or "session_id" not in data:
            return None
        try:
            return SessionLockInfo(
                session_id=str(data["session_id"]),
                actor_id=str(data.get("actor_id", "")),
                skill_id=str(data.get("skill_id", "")),
                pid=int(data.get("pid", 0) or 0),
                started_at_unix=int(data.get("started_at_unix", 0) or 0),
                parent_session_id=(
                    str(data["parent_session_id"]) if data.get("parent_session_id") else None
                ),
                fork_id=str(data["fork_id"]) if data.get("fork_id") else None,
            )
        except (TypeError, ValueError):
            return None

    def _default_stale_after_s(self) -> float:
        """本 kind 的通道B 默认心跳超时 (lock-reap-typed-policy@v1 分型)。

        execution (bg 持续进程会话) → 分钟级快收 stuck/崩溃; 交互式 5 phase (单发 CLI) → 12h
        不变。显式传 stale_after_s 仍覆盖此默认 (测试 / 特殊调用)。
        """
        return EXECUTION_STALE_AFTER_S if self._kind == "execution" else INTERACTIVE_STALE_AFTER_S

    def _is_stale(
        self,
        info: SessionLockInfo,
        *,
        now: float,
        stale_after_s: float,
        pid_alive: PidAliveFn,
    ) -> bool:
        """lock-reap-typed-policy@v1 (T-LRF-04): 两通道 staleness 判据 — 二者 OR, 任一成立即 stale。

        通道A (pid 死亡快通道): 锁 pid>0 (= 有一个全生命周期持续存活进程 backing 这个会话, 如
          execution bg 会话) 且 os.kill 探针确认进程已死 (ProcessLookupError) → 立即可回收,
          **不设 age 门**。pid==0 (单发 CLI: start 一进程写锁后即正常退出, 无持续进程) 绝不走通道A
          —— 不因"写锁那个 CLI 进程已退出"把活会话误收 (RUN-080 病根, Voyager finding-01: 跨命令
          session 必丢)。pid 字段语义 = 是否有持续进程 backing, 由 acquire 侧按会话类型决定盖不盖
          (单发 CLI 盖 0, bg/持续进程会话盖真 live pid); 盖错 = 回归 RUN-080。

        通道B (心跳超时): age = now - started_at_unix(最后活动心跳, resolve 成功即 _touch 刷新)
          > stale_after_s (已按 kind 分型: execution 分钟级 / 交互式 12h)。'pid 活着但心跳停' ≠ 死
          (那是 stuck), 归通道B 管 —— stuck execution 照样在 EXECUTION_STALE_AFTER_S 后被收重派。

        红线: 通道A 只**单向加速**回收死进程; pid 活**绝不**阻止通道B 该收的收 (永不回到纯 pid 探活)。
        """
        # 通道A — pid 死亡快通道 (仅当 pid>0 = 有持续进程 backing; pid<=0 单发 CLI 天然跳过)。
        if info.pid > 0 and not pid_alive(info.pid):
            return True
        # 通道B — 心跳超时 (stale_after_s 已按 kind 分型解析)。
        age = now - info.started_at_unix
        return age > stale_after_s

    def session_signal(
        self,
        session_id: str,
        *,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> tuple[bool | None, bool]:
        """供层① vitality 信号采集: 返回 (process_alive, holds_live_lock)。

        holds_live_lock = 有锁且**非 stale** (_is_stale=False)。关键 (守 F-R11 / T-LRF-04 不回退):
          锁因 pid 死(通道A) 或 心跳超时(通道B) 变 stale → holds_live_lock=False → 废 run/死会话的
          锁不再保护它 → 在 classify_vitality 里落到 DEAD 规则被回收。绝不让"持锁恒不死"变成
          "废 run 永远占位"。
        process_alive (per-form 正向死亡判据):
          - pid>0 (持续进程会话, 如 bg): pid_alive(pid)(os.kill 不唤醒)。pid 活但 stale(心跳超时)
            → True → 不据此判死 (= 误杀红线: 慢但活的持续进程绝不因租约过期被收, R11)。
          - pid<=0 (单发 CLI, 写锁后即退、无持续进程): stale → **False**(租约过期 = CLI 早退、无进程
            可"慢", 是确凿已亡, 正向死亡证据); fresh → None(租约内, 靠 holds_live_lock=True 走 PARKED 保护)。
          - 无锁 → None(不可判, 绝不据此判死)。
        """
        info = self._read_lock(self._lock_path(session_id))
        if info is None:
            return None, False
        now = time.time() if now is None else now
        sa = self._default_stale_after_s() if stale_after_s is None else stale_after_s
        stale = self._is_stale(info, now=now, stale_after_s=sa, pid_alive=pid_alive)
        if info.pid > 0:
            proc: bool | None = pid_alive(info.pid)
        else:
            proc = False if stale else None  # 单发CLI: 租约过期=已亡(正向证据); 租约内=不可判(靠活锁保护)
        return proc, (not stale)

    def reap_stale(
        self,
        *,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> list[str]:
        """扫所有 per-session 锁, 回收 stale 的(pid死/超时), 返回被回收的 session_id 列表。

        崩溃自愈核心。无 stale 时无副作用。malformed 锁文件(读不出 session_id)也直接回收
        (它本身就是坏状态, 不能让坏锁卡死 namespace)。

        stale_after_s 缺省 (None) → 按 kind 分型取默认 (lock-reap-typed-policy@v1:
        execution 分钟级 / 交互式 12h); 显式传值覆盖 (测试 / 特殊调用)。
        """
        sessions_dir = self._sessions_dir()
        if not sessions_dir.exists():
            return []
        now = time.time() if now is None else now
        stale_after_s = (
            self._default_stale_after_s() if stale_after_s is None else stale_after_s
        )
        reaped: list[str] = []
        for path in sorted(sessions_dir.glob("*.json")):
            info = self._read_lock(path)
            if info is None:
                # F-SESSION-REAP-RACE 防御: 单次读到 None 可能是并发写窗口里读到半截(原子写后
                # 理论上不再发生, 留一层纵深); 重读一次确认, 仍 None 才判 malformed 回收 — 不误杀活会话。
                info = self._read_lock(path)
                if info is None:
                    # malformed → 回收 (文件名 stem 当 session_id 记账)。
                    path.unlink(missing_ok=True)
                    reaped.append(path.stem)
                    continue
            if self._is_stale(
                info, now=now, stale_after_s=stale_after_s, pid_alive=pid_alive,
            ):
                path.unlink(missing_ok=True)
                reaped.append(info.session_id)
        return reaped

    def acquire(
        self,
        session_id: str,
        *,
        actor_id: str,
        skill_id: str,
        pid: int | None = None,
        started_at_unix: int | None = None,
        parent_session_id: str | None = None,
        fork_id: str | None = None,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> SessionLockInfo:
        """登记一个新会话锁。先 reap_stale(启动自动扫), 再写 per-session 文件。

        pid 语义 (lock-reap-typed-policy@v1 承重钉死) = 是否有一个全生命周期持续存活进程
        backing 这个会话。**默认 0** (单发 CLI 会话: start 一进程写锁后即正常退出, 无持续进程
        —— 绝不让 _is_stale 通道A 因"写锁那个 CLI 进程已退出"误收活会话 = RUN-080 病根)。
        bg/持续进程会话 (如 execution) 必须**显式传真 live pid**, 才让通道A 死亡快通道生效。
        盖错 (单发 CLI 盖了瞬时 CLI pid) = 回归 RUN-080。

        Raises:
            DuplicateSessionError: 同 session_id 已有 LIVE 锁(非 stale)。不同 session_id
              永远共存(各自一文件), 这是隔离的根。
        """
        validate_session_id(session_id)  # T-SL critical: 底座纵深防御 path-traversal
        self.reap_stale(now=now, stale_after_s=stale_after_s, pid_alive=pid_alive)
        existing = self._read_lock(self._lock_path(session_id))
        if existing is not None:
            raise DuplicateSessionError(
                f"session {session_id} (kind={self._kind}) already has a live lock",
            )
        info = SessionLockInfo(
            session_id=session_id,
            actor_id=actor_id,
            skill_id=skill_id,
            pid=0 if pid is None else pid,
            started_at_unix=int(time.time()) if started_at_unix is None else started_at_unix,
            parent_session_id=parent_session_id,
            fork_id=fork_id,
        )
        path = self._lock_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, json.dumps(info.to_dict(), ensure_ascii=False))
        return info

    def live_sessions(
        self,
        *,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> list[SessionLockInfo]:
        """reap_stale 后返回所有存活会话锁(按 session_id 排序, 确定性)。"""
        self.reap_stale(now=now, stale_after_s=stale_after_s, pid_alive=pid_alive)
        sessions_dir = self._sessions_dir()
        if not sessions_dir.exists():
            return []
        out: list[SessionLockInfo] = []
        for path in sorted(sessions_dir.glob("*.json")):
            info = self._read_lock(path)
            if info is not None:
                out.append(info)
        return sorted(out, key=lambda i: i.session_id)

    def read_meta(self, session_id: str) -> dict[str, object]:
        """读 per-session meta 旁文件 (业务字段: plan_id / task_id / run_id / ...)。缺/坏 → {}。

        meta 与锁文件同目录 (locks/sessions/<kind>/<sid>.meta), 由 CLI 的 _write/_read_session_meta
        写读 (单一约定路径)。这里给 **非 CLI 层 (l2 orchestrator 运行时哨兵)** 一个不跨层 import cli 的
        只读入口 —— T-RMD-S3-DOUBLEDRIVE per-plan_id 哨兵要按 live 会话的 meta.plan_id 分组判双驱动。
        """
        meta_path = self._sessions_dir() / f"{session_id}.meta"
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def assert_single_flight(
        self,
        *,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> list[SessionLockInfo]:
        """T-FIX-B2-06 (INV-B2-6): 断言本 kind 满足单飞不变量 —— serial-reject kind 同一时刻
        live 会话数恒 ≤1。

        这是把 T-FIX-B2-01 单飞门的 provenance 后果焊成可验证守卫: 不阻止 acquire (registry 物理上
        仍允许 N 个不同 session_id 共存 —— 那是 execution N 并发隔离的同一底座, 不能破坏), 只在被调用
        时检查 live_sessions() 长度。

        - kind 不在 SERIAL_REJECT_KINDS (如 execution): 无单飞约束, no-op, 返回当前 live 列表。
        - serial-reject kind 且 live ≤1: 返回 live 列表 (≤1 个)。
        - serial-reject kind 且 live >1: 单飞门被绕过 (esc-532866a8『锁被抢』) → raise
          SingleFlightViolationError, 把"两 review/fix 会话同时活→finding 错挂邻居"的窗口从静默腐蚀
          变成立即可抓的违例。供 orchestrator 运行时自检 / CI 不变量测试调用; 不引入中心锁/中心调度。

        Raises:
            SingleFlightViolationError: serial-reject kind 同一时刻有 >1 个 live 会话。
        """
        live = self.live_sessions(now=now, stale_after_s=stale_after_s, pid_alive=pid_alive)
        if self._kind not in SERIAL_REJECT_KINDS:
            return live
        if len(live) > 1:
            raise SingleFlightViolationError(
                f"single-flight invariant violated: kind={self._kind} has {len(live)} live "
                f"sessions ({', '.join(i.session_id for i in live)}) — 单飞门被绕过, review/fix "
                f"provenance 有错挂风险 (esc-532866a8 锁被抢)",
            )
        return live

    def resolve(
        self,
        explicit_session_id: str | None = None,
        *,
        now: float | None = None,
        stale_after_s: float | None = None,
        pid_alive: PidAliveFn = _pid_alive,
    ) -> SessionLockInfo | None:
        """解析"这个命令属于哪个会话"。

        - explicit_session_id 给定 → 返回那个会话(必须存在且 live, 否则 UnknownSessionError)。
          这是并行隔离的关键: 子命令显式绑定自己 session, 绝不认到别的会话上。
        - 未给定 → live 会话: 0 个返回 None; 恰好 1 个返回它(单串行向后兼容);
          >1 个 raise AmbiguousActiveSessionError(必须 --session-id 消歧, 不给隐式默认避免认错)。
        """
        live = self.live_sessions(now=now, stale_after_s=stale_after_s, pid_alive=pid_alive)
        touch_now = time.time() if now is None else now
        if explicit_session_id is not None:
            for info in live:
                if info.session_id == explicit_session_id:
                    self._touch(info.session_id, touch_now)
                    return info
            raise UnknownSessionError(
                f"session {explicit_session_id} (kind={self._kind}) not found among "
                f"{len(live)} live session(s) — 可能已 publish/崩溃回收, 或 id 写错",
            )
        if not live:
            return None
        if len(live) == 1:
            self._touch(live[0].session_id, touch_now)
            return live[0]
        raise AmbiguousActiveSessionError(
            f"{len(live)} live {self._kind} sessions active "
            f"({', '.join(i.session_id for i in live)}) — 必须 --session-id 指定哪一个",
        )

    def _touch(self, session_id: str, now: float) -> None:
        """刷新会话锁"最后活动"时间戳(心跳)。resolve 成功即调 → 活跃会话永不超时回收。"""
        path = self._lock_path(session_id)
        info = self._read_lock(path)
        if info is None:
            return
        d = info.to_dict()
        d["started_at_unix"] = int(now)
        # best-effort 心跳刷新: 写失败 (盘满/竞态) 不致命, 锁顶多稍后被 reap (SIM105: suppress)。
        with contextlib.suppress(OSError):
            _atomic_write_text(path, json.dumps(d, ensure_ascii=False))

    def release(self, session_id: str) -> bool:
        """移除一个会话锁(publish/terminate 时调)。返回 True iff 锁曾存在。"""
        path = self._lock_path(session_id)
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True


def _start_cs_lock_path(towow_dir: Path, kind: str, key: str) -> Path:
    """plan/work start 原子临界区的 per-(kind,key) flock 文件路径。

    key (plan_id / task_id) 含 '@' '.' 等普通字符, 不可控字符(' / ' '\\' '..')归一成 '_' —— 它会拼进
    写路径, 同 validate_session_id 的 path-traversal 防御精神(这里 key 来自 inherited_plan_id /
    task_id, 非纯用户输入, 但仍 fail-safe 归一)。
    """
    safe = "".join(c if (c in _SID_ALLOWED_CHARS or c in "@.") else "_" for c in key) or "_empty_"
    return towow_dir / "locks" / "start_cs" / kind / f"{safe}.lock"


@contextlib.contextmanager
def start_critical_section(towow_dir: Path, kind: str, key: str) -> Iterator[None]:
    """Per-(kind, key) 进程内原子临界区 —— T-RMD-S3-DOUBLEDRIVE 同 plan_id / 同 task_id 双驱动互斥。

    病: plan_start / work_start 的 "scan(live_sessions 查同 key)+acquire" 是 check-then-act, 两个
    并发进程交错 → 都通过 scan(谁都没 acquire)→各自 acquire(registry 只按 session_id 去重, 不去重
    plan_id/task_id)→ 双驱动(同 plan_id 两 cohort / 同 task_id 双跑)。kind 级单飞 R11 退役后, 这道
    TOCTOU 真空无人补。

    解: 把 scan+acquire 串成 per-key 互斥临界区。同 key 同一时刻只一个进程在区内; 后到者阻塞, 进区
    后 scan 看见先到者已落的 session 锁 → 经【既有 scan reject 路径】被拒(顺序拒语义/文案不变, 仅多
    了并发安全)。

    用 fcntl.flock(LOCK_EX) 而非 claim.py 的 O_EXCL 认领文件:
      - 临界区整体在【单进程内】(plan_start / work_start 一次 CLI 调用), flock 在持有进程退出/崩溃时
        由 OS **自动释放** → 天然 crash-safe, 无需 stale-reaper。区别于 claim.py 的 exec spawn 认领:
        那个认领要跨【spawn 出的独立子进程】、长持有, flock 会随父进程退出而释放故不适用, 才用
        O_EXCL + 显式 release(+ 同批 T-RMD-S3-REAPER 的 age reaper)。两者是两类锁、两类生命周期。
      - flock 在本仓库有先例(event_log / daemon_run_once / validation_runner)。session_lock 的
        per-session **state** 文件不用 flock(注释见本文件顶), 是因其跨进程读写(start 写/answer 读,
        flock 进程退出即失) —— 与此【进程内 CS】正交, 不矛盾。
    """
    lock_path = _start_cs_lock_path(towow_dir, kind, key)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = lock_path.open("w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "EXECUTION_STALE_AFTER_S",
    "INTERACTIVE_STALE_AFTER_S",
    "SERIAL_REJECT_KINDS",
    "AmbiguousActiveSessionError",
    "DuplicateSessionError",
    "SessionLockError",
    "SessionLockInfo",
    "SessionLockRegistry",
    "SingleFlightViolationError",
    "UnknownSessionError",
    "start_critical_section",
]
